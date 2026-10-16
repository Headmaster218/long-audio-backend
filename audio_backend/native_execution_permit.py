"""Finite legacy-primary/MOSS exclusion and conservative optional admission.

This observer never signals a primary process or replaces its cleanup. Uncertain
primary observations disable optional work; uncertain MOSS cleanup holds the
permit. The primary cohort remains concurrent (one Worker and one upload).
"""
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
import shutil
import subprocess
import threading
import time

import psutil

MOSS_ENVELOPE = int(3.5 * 1024**3)
WRAPPER_RESERVE = 16 * 1024**2
HOST_RESERVE = 2 * 1024**3
DISK_RESERVE = int(5.5 * 1024**3)
MAX_ROWS, MAX_IDENTITIES, SAMPLE_SECONDS = 4096, 128, .250
_LOG = logging.getLogger(__name__)
_OBSERVATION_PHASES = frozenset(('caller_identity', 'process_enumeration',
    'remembered_identity', 'family_selection', 'selected_identity', 'selected_rss',
    'selected_recheck', 'host_memory', 'disk_free', 'bind_identity', 'sample',
    'family_tracking', 'resident_check', 'original_wait', 'descendant_check'))
_OBSERVATION_REASONS = frozenset(('process_sample_budget', 'process_identity_uncertain',
    'process_identity_budget', 'remembered_identity_unobserved', 'caller_identity_uncertain',
    'process_sample_unavailable', 'primary_identity_budget', 'primary_identity_uncertain',
    'primary_handle_unknown', 'resident_identity_uncertain', 'primary_wait_unconfirmed',
    'primary_descendant_unresolved'))


class AdmissionError(RuntimeError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _observation_class(error):
    classes = ((AdmissionError, 'AdmissionError'), (psutil.NoSuchProcess, 'NoSuchProcess'),
        (psutil.ZombieProcess, 'ZombieProcess'), (psutil.AccessDenied, 'AccessDenied'),
        (psutil.Error, 'Error'), (PermissionError, 'PermissionError'),
        (FileNotFoundError, 'FileNotFoundError'), (ProcessLookupError, 'ProcessLookupError'),
        (OSError, 'OSError'), (KeyError, 'KeyError'), (TypeError, 'TypeError'),
        (ValueError, 'ValueError'), (RuntimeError, 'RuntimeError'),
        (subprocess.TimeoutExpired, 'TimeoutExpired'))
    if error is None:return None
    return next((name for kind, name in classes if type(error) is kind), 'other')


def _observation_errno(error):
    if type(error) not in (OSError, PermissionError, FileNotFoundError, ProcessLookupError):return None
    value = BaseException.__getattribute__(error, 'errno')
    return value if type(value) is int and 0 <= value <= 65535 else None


def _sample_observation(error, phase, cause=None):
    # Attach sanitized scalars only; retain the original public exception reason.
    error._sample_observation = {'phase': phase if phase in _OBSERVATION_PHASES else 'other',
        'cause_class': _observation_class(cause), 'errno': _observation_errno(cause)}
    return error


@dataclass(frozen=True)
class ProcessSample:
    identities: dict
    rss: int
    caller_rss: int
    available: int
    disk_free: int
    confirmed_exits: int = 0
    resampled: bool = False


def service_sample(settings, *, clock=time.monotonic, known=()):
    """Bounded current accounting; a discarded census never proves an exit.

    One fresh census may replace an indeterminate non-caller identity read.
    Both passes share the time/row/identity caps. This is asynchronous observed
    accounting, not proof about unseen historical descendants or PID reuse.
    """
    deadline = clock() + SAMPLE_SECONDS
    phase = 'caller_identity'
    try:
        caller = psutil.Process(os.getpid())
        caller_created = caller.create_time()
        remembered = dict(known)
        carried_parents, carried_created = {}, {}
        enumerated = 0
        for attempt in range(2):
            rows, obtained = {}, {}
            selected = {caller.pid}
            pid = None
            try:
                phase = 'caller_identity'
                if attempt and psutil.Process(caller.pid).create_time() != caller_created:
                    raise AdmissionError('caller_identity_uncertain')
                phase = 'process_enumeration'
                for process in psutil.process_iter(['pid', 'ppid']):
                    if enumerated >= MAX_ROWS or clock() >= deadline:
                        raise AdmissionError('process_sample_budget')
                    enumerated += 1
                    info = process.info
                    row_pid, parent = info['pid'], info['ppid']
                    if type(row_pid) is not int or type(parent) is not int or row_pid in rows:
                        raise AdmissionError('process_identity_uncertain')
                    rows[row_pid] = (parent, process)
                phase = 'remembered_identity'
                tracked = dict(remembered)
                for old_pid, created in carried_created.items():
                    if old_pid in tracked and tracked[old_pid] != created:
                        raise AdmissionError('process_identity_uncertain')
                    tracked[old_pid] = created
                required = set(tracked) | set(carried_parents) | {caller.pid}
                if len(required) > MAX_IDENTITIES:
                    raise AdmissionError('process_identity_budget')
                for old_pid in required - {caller.pid}:
                    if clock() >= deadline:
                        raise AdmissionError('process_sample_budget')
                    if old_pid in rows:
                        selected.add(old_pid)
                    else:
                        # This absent row grants only current-accounting
                        # completion. It never reaps/removes an owned handle.
                        try:
                            psutil.Process(old_pid)
                        except psutil.NoSuchProcess as error:
                            if type(error) is not psutil.NoSuchProcess:raise
                            continue
                        raise AdmissionError('remembered_identity_unobserved')
                phase = 'family_selection'
                for _ in range(MAX_IDENTITIES):
                    added = {p for p, (parent, _) in rows.items() if parent in selected} - selected
                    if not added:break
                    selected.update(added)
                    if len(selected | required) > MAX_IDENTITIES or clock() >= deadline:
                        raise AdmissionError('process_identity_budget')
                else:
                    raise AdmissionError('process_identity_budget')
                if caller.pid not in rows:
                    raise AdmissionError('caller_identity_uncertain')
                identities = {}
                total = caller_rss = confirmed_exits = 0
                for pid in selected:
                    if clock() >= deadline:
                        raise AdmissionError('process_sample_budget')
                    parent, process = rows[pid]
                    phase = 'selected_identity'
                    # Retry uses independent public objects, not iterator cache.
                    if attempt:process = psutil.Process(pid)
                    created = process.create_time()
                    if pid in tracked and tracked[pid] != created:
                        raise AdmissionError('process_identity_uncertain')
                    if pid == caller.pid and created != caller_created:
                        raise AdmissionError('caller_identity_uncertain')
                    obtained[pid] = created
                    if (process.ppid() != parent or
                            pid in carried_parents and carried_parents[pid] != parent):
                        raise AdmissionError('process_identity_uncertain')
                    phase = 'selected_rss'
                    try:
                        rss = process.memory_info().rss
                    except psutil.NoSuchProcess as error:
                        if type(error) is not psutil.NoSuchProcess or pid == caller.pid:raise
                        ancestor, seen = parent, {pid}
                        while ancestor != caller.pid:
                            if ancestor not in selected or ancestor in seen:raise error
                            seen.add(ancestor); ancestor = rows[ancestor][0]
                        try:
                            psutil.Process(pid)
                        except psutil.NoSuchProcess as confirmation:
                            if type(confirmation) is not psutil.NoSuchProcess:raise
                            confirmed_exits += 1
                            continue
                        raise
                    phase = 'selected_recheck'
                    if process.create_time() != created or process.ppid() != parent or type(rss) is not int or rss < 0:
                        raise AdmissionError('process_identity_uncertain')
                    identities[pid] = (created, parent, rss)
                    total += rss
                    if pid == caller.pid:caller_rss = rss
                phase = 'host_memory'
                available = psutil.virtual_memory().available
                phase = 'disk_free'
                free = shutil.disk_usage(settings.data_dir).free
                if clock() >= deadline:raise AdmissionError('process_sample_budget')
                return ProcessSample(identities, total, caller_rss, available, free,
                                     confirmed_exits, bool(attempt))
            except psutil.NoSuchProcess as error:
                if (attempt or type(error) is not psutil.NoSuchProcess
                        or phase != 'selected_identity' or pid == caller.pid):raise
                # Carry ALL selected candidates, even ones not yet queried.
                # No first-pass RSS, identities or confirmation counts escape.
                carried_parents = {p:rows[p][0] for p in selected}
                carried_created = obtained
                if len(set(remembered) | set(carried_parents)) > MAX_IDENTITIES:
                    raise AdmissionError('process_identity_budget')
    except AdmissionError as error:
        _sample_observation(error, phase)
        raise
    except (psutil.Error, OSError, KeyError, TypeError, ValueError) as error:
        raise _sample_observation(AdmissionError('process_sample_unavailable'), phase, error) from None


class PrimaryIntent:
    def __init__(self, owner, kind):
        self.owner, self.kind = owner, kind
        self.granted = self.entered = self.released = False

    def wait(self, stop=None, *, handoff=None):
        owner = self.owner
        while True:
            with owner._condition:
                if self.released or stop is not None and stop():
                    self.release()
                    raise InterruptedError('Primary wait interrupted')
                if self.granted and owner._optional is None:
                    self.entered = True
                    owner._local.intent = self
                    return self
            # Only the existing Worker supplies this bounded scheduling pump.
            # Never run it under the permit condition or a SQLite transaction.
            if handoff is not None:handoff()
            with owner._condition:
                if not self.granted or owner._optional is not None:
                    owner._condition.wait(.05)

    def release(self):
        owner = self.owner
        with owner._condition:
            if not self.released:
                self.released = True
                if owner._primary.get(self.kind) is self:owner._primary.pop(self.kind)
                if getattr(owner._local, 'intent', None) is self:
                    owner._local.intent = None
                owner._grant_primary_locked()
                owner._condition.notify_all()


class NativeExecutionPermit:
    def __init__(self, settings, *, sampler=service_sample):
        self.settings, self._sampler = settings, sampler
        self._condition = threading.Condition(threading.RLock())
        self._local = threading.local()
        self._primary, self._handles = {}, {}
        self._observed = {}
        self._probe = False
        self._optional = None
        self._optional_kind = None
        self._handoff = self._turn = None
        self._cancel = self._cancel_callback = None
        self._state = 'IDLE'
        self._closed = False
        self._unavailable = None
        self._primary_observation_failure = None
        self._confirmed_sample_exits = 0
        self._completed_sample_retries = 0
        self._sample_exit_confirmation = None
        self._configuration_current = lambda: True

    @contextmanager
    def claim_probe(self):
        with self._condition:
            if self._probe:
                raise RuntimeError('Only the legacy Worker may claim')
            self._probe = True
        try:
            yield
        finally:
            with self._condition:
                self._probe = False
                self._condition.notify_all()

    @contextmanager
    def automatic_publication_guard(self,record):
        # Lock order: manager -> source generation guard -> this condition ->
        # primary/result DB. No native/owner callback or join under these locks.
        if not self._condition.acquire(timeout=.005):raise AdmissionError('publication_fence_busy')
        try:
            if (self._closed or record['cancelled'].is_set()
                    or self._optional is not record or self._optional_kind != 'automatic_text'
                    or any(x.granted or x.entered for x in self._primary.values())):
                raise AdmissionError('optional_cancelled')
            yield
        finally:self._condition.release()

    def primary_intent(self, kind):
        if kind not in ('worker', 'probe'):
            raise ValueError('Unknown primary participant')
        callback = None
        with self._condition:
            if kind in self._primary:
                raise RuntimeError('Primary participant already active')
            intent = PrimaryIntent(self, kind)
            self._primary[kind] = intent
            self._grant_primary_locked()
            if self._optional is not None and self._optional_kind != 'automatic_text':
                self._cancel.set()  # Publication invalidation precedes termination.
                callback = self._cancel_callback
        if callback is not None:
            try:
                callback()
            except Exception:
                pass  # Optional ownership remains held, never silently released.
        return intent

    def _grant_primary_locked(self):
        # A due handoff freezes the existing granted identities. Arrivals stay
        # queued until its one opportunity closes (or its exact owner releases).
        if self._optional is None and self._handoff is None:
            for intent in self._primary.values():intent.granted = True

    def request_automatic_turn(self):
        with self._condition:
            if self._closed or self._unavailable or self._optional is not None:return False
            if self._handoff is None:self._handoff = object()
            self._condition.notify_all()
            return True

    @contextmanager
    def automatic_turn(self):
        token = None
        with self._condition:
            if (self._handoff is not None and self._turn is None and self._optional is None
                    and not self._probe and not any(x.granted for x in self._primary.values())):
                token = self._turn = self._handoff
                self._local.turn = token
        try:yield token is not None
        finally:
            if token is not None:
                with self._condition:
                    if self._turn is token:
                        self._turn = self._handoff = None
                        self._local.turn = None
                        self._grant_primary_locked()
                        self._condition.notify_all()

    def abandon_automatic_turn(self):
        # Shutdown can discard only a non-owned scheduling hint, never a live
        # optional token or a turn currently executing its bounded callback.
        with self._condition:
            if self._turn is None:
                self._handoff = None
                self._grant_primary_locked()
                self._condition.notify_all()

    @contextmanager
    def primary_span(self, kind='probe', stop=None):
        nested = getattr(self._local, 'intent', None)
        if nested is not None and nested.entered and not nested.released:
            yield nested
            return
        intent = self.primary_intent(kind)
        try:
            intent.wait(stop)
            yield intent
        finally:
            intent.release()

    def _known_identities_locked(self):
        known = dict(self._observed)
        for record in self._handles.values():
            for pid, created in ((record['pid'], record['created']), *record['children'].items()):
                if pid in known and known[pid] != created:
                    raise AdmissionError('primary_identity_uncertain')
                known[pid] = created
        if len(known) > MAX_IDENTITIES:
            raise AdmissionError('primary_identity_budget')
        return known

    def _take_sample(self):
        with self._condition:
            known = tuple(self._known_identities_locked().items())
        current = (service_sample(self.settings, known=known) if self._sampler is service_sample
                   else self._sampler(self.settings))
        if len(current.identities) > MAX_IDENTITIES:
            raise AdmissionError('process_identity_budget')
        if (type(current.confirmed_exits) is not int or not 0 <= current.confirmed_exits <= MAX_IDENTITIES
                or type(current.resampled) is not bool):
            raise AdmissionError('process_identity_uncertain')
        with self._condition:
            latest = self._known_identities_locked()
            if len(set(latest) | set(current.identities)) > MAX_IDENTITIES:
                raise AdmissionError('primary_identity_budget')
            prior = dict(known)
            if any(latest[pid] != prior[pid] for pid in set(latest) & set(prior)):
                raise AdmissionError('primary_identity_uncertain')
            if any(pid not in prior and pid not in current.identities for pid in latest):
                raise AdmissionError('primary_identity_uncertain')
            for pid, row in current.identities.items():
                if pid in latest and latest[pid] != row[0]:
                    raise AdmissionError('process_identity_uncertain')
            self._observed = {pid:row[0] for pid,row in current.identities.items()}
            if current.resampled:
                self._completed_sample_retries = min(2**31 - 1, self._completed_sample_retries + 1)
            if current.confirmed_exits:
                if self._sample_exit_confirmation is None:
                    self._sample_exit_confirmation = {'phase': 'selected_rss',
                        'reason': 'independent_absence_confirmed',
                        'confirmed_exits': current.confirmed_exits}
                self._confirmed_sample_exits = min(2**31 - 1,
                    self._confirmed_sample_exits + current.confirmed_exits)
        return current

    def mark_unavailable(self, reason='primary_ownership_uncertain'):
        with self._condition:
            self._unavailable = reason

    def _record_primary_failure(self, operation, phase, error):
        record = None
        with self._condition:
            self.mark_unavailable()
            if self._primary_observation_failure is not None:return
            try:
                reason = error.reason if type(error) is AdmissionError else None
                if reason is not None and (type(reason) is not str or reason not in _OBSERVATION_REASONS):reason = 'other'
                cause = BaseException.__getattribute__(error, '__context__')
                cause_class, errno = _observation_class(cause), _observation_errno(error)
                if type(error) is AdmissionError:
                    observed = getattr(error, '_sample_observation', None)
                    if type(observed) is dict:
                        phase = observed.get('phase', phase)
                        cause_class, errno = observed.get('cause_class'), observed.get('errno')
                handles = len(self._handles)
                descendants = sum(len(row['children']) for row in self._handles.values())
                record = {'operation': operation if operation in ('bind_primary', 'observe_primary', 'finish_primary') else 'other',
                    'phase': phase if type(phase) is str and phase in _OBSERVATION_PHASES else 'other',
                    'exception_class': _observation_class(error),
                    'cause_class': cause_class if cause_class is None or type(cause_class) is str and cause_class in ('AdmissionError', 'NoSuchProcess',
                        'ZombieProcess', 'AccessDenied', 'Error', 'PermissionError', 'FileNotFoundError',
                        'ProcessLookupError', 'OSError', 'KeyError', 'TypeError', 'ValueError',
                        'RuntimeError', 'TimeoutExpired', 'other') else 'other',
                    'reason': reason, 'errno': errno if type(errno) is int and 0 <= errno <= 65535 else None,
                    'handle_count': handles if 0 <= handles <= MAX_IDENTITIES else None,
                    'descendant_count': descendants if 0 <= descendants <= MAX_IDENTITIES else None}
                self._primary_observation_failure = record
            except Exception:
                return  # A diagnostic failure cannot undo the existing latch.
        try:_LOG.warning('primary_ownership_observation %s', dict(record))
        except Exception:pass

    def bind_primary(self, process, *, resident=None):
        """Remember original handle identity; observation errors never escape."""
        phase = 'bind_identity'
        try:
            proc = process.proc if hasattr(process, 'proc') else process
            with self._condition:
                if len(self._handles) >= MAX_IDENTITIES:
                    raise AdmissionError('primary_identity_budget')
                created = psutil.Process(proc.pid).create_time()
                self._handles[id(proc)] = {'proc': proc, 'pid': proc.pid, 'created': created,
                    'resident': resident, 'children': {}, 'idle': False}
            self.observe_primary()
        except Exception as error:
            self._record_primary_failure('bind_primary', phase, error)

    def observe_primary(self):
        phase = 'sample'
        try:
            sample = self._take_sample()
            phase = 'family_tracking'
            with self._condition:
                for record in self._handles.values():
                    family = {record['pid']}
                    for _ in range(MAX_IDENTITIES):
                        added = {pid for pid, row in sample.identities.items() if row[1] in family} - family
                        if not added:
                            break
                        family.update(added)
                    for pid in family - {record['pid']}:
                        identity = sample.identities[pid][0]
                        prior = record['children'].get(pid)
                        if prior is not None and prior != identity:
                            raise AdmissionError('primary_identity_uncertain')
                        record['children'][pid] = identity
                    if sum(1 + len(x['children']) for x in self._handles.values()) > MAX_IDENTITIES:
                        raise AdmissionError('primary_identity_budget')
        except Exception as error:
            self._record_primary_failure('observe_primary', phase, error)

    def finish_primary(self, process, *, normal=False):
        """Positive original wait/reap or explicit resident-idle is required."""
        proc = process.proc if hasattr(process, 'proc') else process
        phase = 'sample'
        try:
            self.observe_primary()
            with self._condition:
                phase = 'original_wait'
                record = self._handles.get(id(proc))
                if record is None or record['proc'] is not proc:
                    raise AdmissionError('primary_handle_unknown')
                phase = 'resident_check'
                if normal and record['resident'] is not None and record['resident']():
                    if (proc.poll() is not None or psutil.Process(proc.pid).create_time() != record['created']):
                        raise AdmissionError('resident_identity_uncertain')
                    record['idle'] = True
                    return
                phase = 'original_wait'
                if proc.poll() is None or proc.wait(timeout=0) is None:
                    raise AdmissionError('primary_wait_unconfirmed')
                phase = 'descendant_check'
                for pid, created in ((record['pid'], record['created']), *record['children'].items()):
                    try:
                        live = psutil.Process(pid)
                        if live.create_time() != created or live.is_running():
                            raise AdmissionError('primary_descendant_unresolved')
                    except psutil.NoSuchProcess:
                        pass
                del self._handles[id(proc)]
        except Exception as error:
            self._record_primary_failure('finish_primary', phase, error)

    def finish_cohort(self, *, normal):
        with self._condition:
            processes = [x['proc'] for x in self._handles.values() if x['resident'] is not None]
        for proc in processes:
            self.finish_primary(proc, normal=normal)

    def _resource_check(self, *, reserve):
        sample = self._take_sample()
        if sample.rss >= self.settings.process_memory_limit_bytes:
            raise AdmissionError('service_memory_budget')
        if sample.disk_free < max(DISK_RESERVE, self.settings.disk_reserve_bytes):
            raise AdmissionError('service_disk_reserve')
        if sample.available < HOST_RESERVE:
            raise AdmissionError('service_host_reserve')
        if reserve:
            allowed = {os.getpid()}
            for record in self._handles.values():
                if (not record['idle'] or record['resident'] is None or not record['resident']()
                        or record['proc'].poll() is not None
                        or sample.identities.get(record['pid'], (None,))[0] != record['created']):
                    raise AdmissionError('resident_identity_uncertain')
                allowed.add(record['pid'])
                for pid, created in record['children'].items():
                    if pid in sample.identities:
                        if sample.identities[pid][0] != created:
                            raise AdmissionError('resident_identity_uncertain')
                        allowed.add(pid)
            if set(sample.identities) - allowed:
                raise AdmissionError('unclassified_service_child')
            resident_rss = sample.rss - sample.caller_rss
            if resident_rss + MOSS_ENVELOPE + WRAPPER_RESERVE >= self.settings.process_memory_limit_bytes:
                raise AdmissionError('service_envelope_budget')
            if sample.available < HOST_RESERVE + max(0, MOSS_ENVELOPE - sample.caller_rss) + WRAPPER_RESERVE:
                raise AdmissionError('service_host_envelope')
        return sample

    def reserve_optional(self, token, cancelled, cancel_callback, *, owner_kind='preemptible'):
        if owner_kind not in ('preemptible', 'automatic_text'):raise ValueError('Unknown optional owner')
        with self._condition:
            if self._closed or not self._configuration_current():
                raise AdmissionError('jobs_configuration_unavailable')
            turn = (owner_kind == 'automatic_text' and self._turn is not None
                    and getattr(self._local, 'turn', None) is self._turn)
            primary = any(x.granted or x.entered for x in self._primary.values()) if turn else bool(self._primary)
            if self._optional is not None or primary or self._probe or self._handoff is not None and not turn:
                raise AdmissionError('primary_or_optional_busy')
            if self._unavailable:
                raise AdmissionError(self._unavailable)
            self._resource_check(reserve=True)
            self._optional, self._cancel, self._cancel_callback = token, cancelled, cancel_callback
            self._optional_kind = owner_kind
            self._state = 'OPTIONAL_RESERVED'

    def check_optional(self, token, phase='running'):
        with self._condition:
            if self._optional is not token:
                raise AdmissionError('optional_ownership_changed')
            primary = (any(x.granted or x.entered for x in self._primary.values())
                if self._optional_kind == 'automatic_text' else bool(self._primary))
            if (self._closed or primary or self._cancel.is_set()
                    or not self._configuration_current() or self._unavailable):
                self._cancel.set()
                raise AdmissionError('optional_cancelled')
            try:
                self._resource_check(reserve=phase in ('reserved', 'before_spawn'))
            except Exception:
                self._cancel.set()
                raise
            self._state = 'OPTIONAL_RUNNING'

    def cleaning(self, token):
        with self._condition:
            if self._optional is token:
                self._state = 'OPTIONAL_CLEANING'

    def release_optional(self, token, *, cleaned):
        with self._condition:
            if self._optional is not token:
                return
            if not cleaned:
                self._state = 'OPTIONAL_BLOCKED'
                return
            self._optional = self._cancel = self._cancel_callback = None
            self._optional_kind = None
            self._state = 'IDLE'
            self._grant_primary_locked()
            self._condition.notify_all()

    def close_admission(self):
        with self._condition:
            self._closed = True
            if self._turn is None:self._handoff = None
            self._grant_primary_locked()
            if self._cancel is not None:
                self._cancel.set()
            callback = self._cancel_callback
        if callback is not None:
            try:
                callback()
            except Exception:
                pass

    def status(self):
        with self._condition:
            return {'state': self._state if self._optional is not None else ('PRIMARY' if self._primary else 'IDLE'),
                'primary_waiting': any(not x.entered for x in self._primary.values()),
                'primary_queued': sum(not x.granted for x in self._primary.values()),
                'primary_granted_waiting': sum(x.granted and not x.entered for x in self._primary.values()),
                'primary_executing': sum(x.entered for x in self._primary.values()),
                'automatic_turn_pending': self._handoff is not None,
                'optional_owner_kind': self._optional_kind,
                'optional_admission_unavailable': self._unavailable,
                # Observation only; never substitutes for original wait/cleanup.
                'confirmed_sample_exits': self._confirmed_sample_exits,
                'completed_current_sample_retries': self._completed_sample_retries,
                'sample_exit_confirmation': (dict(self._sample_exit_confirmation)
                    if self._sample_exit_confirmation is not None else None),
                'primary_observation_failure': (dict(self._primary_observation_failure)
                    if self._primary_observation_failure is not None else None),
                'admission_closed': self._closed}
