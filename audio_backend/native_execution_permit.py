"""Finite legacy-primary/MOSS exclusion and conservative optional admission.

This observer never signals a primary process or replaces its cleanup. Uncertain
primary observations disable optional work; uncertain MOSS cleanup holds the
permit. The primary cohort remains concurrent (one Worker and one upload).
"""
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import math
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
SERVICE_MEMORY_CEILING = 6 * 1024**3
COMPARISON_LOGICAL_BYTES = 128 * 1024**2
COMPARISON_BASELINE_BYTES = 32 * 1024**2
COMPARISON_CHILD_RSS_BYTES = 160 * 1024**2
COMPARISON_PARENT_BYTES = 32 * 1024**2
COMPARISON_RESERVE_BYTES = 192 * 1024**2
MAX_ROWS, MAX_IDENTITIES, SAMPLE_SECONDS = 4096, 128, .250
_LOG = logging.getLogger(__name__)
_SAMPLE_BUDGET = object()
_COMPARISON_SAMPLE_DEADLINE = object()
SAMPLING_RETRY_SECONDS = 1.
_OPTIONAL_SAMPLE = object()
_OPTIONAL_ADMISSION_PENDING = object()
_COMPARISON_START_PENDING = object()
OPTIONAL_BUSY_GRACE = .250
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


class _SampleSkipped(AdmissionError):
    """No observation was made; never a new ownership failure."""
    def __init__(self, subtype='skipped'):
        super().__init__('process_sample_unavailable')
        self.subtype = subtype


class _SampleBusy(_SampleSkipped):
    def __init__(self):super().__init__('busy_flight')


def optional_sample_failure(error):
    """Private fixed scalars; no messages, process identities or stale RSS."""
    value = getattr(error, '_optional_sample_failure', None)
    if type(value) is not tuple or len(value) != 8 or value[0] is not _OPTIONAL_SAMPLE:return None
    _, subtype, held, age, waited, phase, cause, grace = value
    if (type(subtype) is not str or subtype not in ('busy_flight','stale_generation','cooldown',
            'recovery_not_due','skipped','wall_budget','rows_budget','census_failure',
            'resource_limit','cancelled','deadline','ownership_changed','sampling_pending','sticky','other')
            or type(held) is not bool
            or age is not None and (type(age) is not int or not 0 <= age <= 2**31 - 1)
            or type(waited) is not int or not 0 <= waited <= 250
            or type(grace) is not bool):return None
    return {'subtype':subtype,'reservation_held':held,'sample_age_ms':age,
        'wait_ms':waited,'grace_used':grace,
        'phase':phase if type(phase) is str and phase in _OBSERVATION_PHASES else None,
        'cause':cause if type(cause) is str and cause in ('AdmissionError','NoSuchProcess',
            'ZombieProcess','AccessDenied','Error','PermissionError','FileNotFoundError',
            'ProcessLookupError','OSError','KeyError','TypeError','ValueError','RuntimeError',
            'TimeoutExpired','other') else None}



def _sample_exhausted(kind, elapsed=None, rows=0):
    # Last budget-clock observation, not an extra clock read at refusal.
    elapsed_ms = None
    try:
        if type(elapsed) in (float, int) and math.isfinite(elapsed) and elapsed >= 0:
            elapsed_ms = min(2**31 - 1, int(min(elapsed, 2**31) * 1000))
    except (OverflowError, TypeError, ValueError):pass
    error = AdmissionError('process_sample_budget')
    error._sample_budget = (_SAMPLE_BUDGET, kind, elapsed_ms, min(MAX_ROWS, rows))
    return error


def _authenticated_exhaustion(error):
    if type(error) is not AdmissionError or error.reason != 'process_sample_budget':return None
    marker = getattr(error, '_sample_budget', None)
    if (type(marker) is tuple and len(marker) == 4 and marker[0] is _SAMPLE_BUDGET
            and type(marker[1]) is str and marker[1] in ('wall', 'rows')
            and (marker[2] is None or type(marker[2]) is int and 0 <= marker[2] <= 2**31 - 1)
            and type(marker[3]) is int and 0 <= marker[3] <= MAX_ROWS):return marker[1:]
    return None


def _authenticated_probe_deadline(error):
    marker = getattr(error, '_optional_sample_deadline', None)
    return (type(error) is AdmissionError and error.reason == 'optional_cancelled'
        and type(marker) is tuple and len(marker) == 2 and marker[0] is _OPTIONAL_SAMPLE
        and type(marker[1]) is str and marker[1] == 'automatic_primary_probe')


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


def service_sample(settings, *, clock=time.monotonic, known=(), deadline=None, comparison_deadline=False,
                   primary_probe_deadline=False):
    """Bounded current accounting; a discarded census never proves an exit.

    One fresh census may replace an indeterminate non-caller identity read.
    Both passes share the time/row/identity caps. This is asynchronous observed
    accounting, not proof about unseen historical descendants or PID reuse.
    """
    started = last_clock = clock()
    budget_deadline = started + SAMPLE_SECONDS
    deadline = min(budget_deadline, deadline) if deadline is not None else budget_deadline
    clipped_comparison = comparison_deadline and deadline < budget_deadline
    clipped_probe = primary_probe_deadline and deadline < budget_deadline
    enumerated = 0
    def time_exhausted():
        nonlocal last_clock
        last_clock = clock()
        return last_clock >= deadline
    def exhausted(kind):
        # A comparison's shorter work/cleanup deadline is not evidence that the
        # shared observer exhausted its original 250 ms budget. Real wall/row
        # exhaustion retains the existing authenticated recovery behavior.
        if kind == 'wall' and clipped_comparison and last_clock < budget_deadline:
            error = AdmissionError('comparison_deadline')
            error._comparison_sample_deadline = _COMPARISON_SAMPLE_DEADLINE
            return error
        if kind == 'wall' and clipped_probe and last_clock < budget_deadline:
            error = AdmissionError('optional_cancelled')
            error._optional_sample_deadline = (_OPTIONAL_SAMPLE, 'automatic_primary_probe')
            error._optional_execution_deadline = True
            return error
        return _sample_exhausted(kind, last_clock - started, enumerated)
    phase = 'caller_identity'
    try:
        caller = psutil.Process(os.getpid())
        caller_created = caller.create_time()
        remembered = dict(known)
        carried_parents, carried_created = {}, {}
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
                    if enumerated >= MAX_ROWS:raise exhausted('rows')
                    if time_exhausted():raise exhausted('wall')
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
                    if time_exhausted():
                        raise exhausted('wall')
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
                    if len(selected | required) > MAX_IDENTITIES:
                        raise AdmissionError('process_identity_budget')
                    if time_exhausted():raise exhausted('wall')
                else:
                    raise AdmissionError('process_identity_budget')
                if caller.pid not in rows:
                    raise AdmissionError('caller_identity_uncertain')
                identities = {}
                total = caller_rss = confirmed_exits = 0
                for pid in selected:
                    if time_exhausted():
                        raise exhausted('wall')
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
                if time_exhausted():raise exhausted('wall')
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
        self._optional_work_deadline = None
        self._primary_probe_admission = None
        self._comparison = None
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
        self._sample_clock = time.monotonic
        self._sample_flight = None
        self._sample_generation = object()
        self._sampling_pending = False
        self._sampling_retry_after = 0.
        self._sampling_failure = None
        self._sampling_exhaustions = self._sampling_recoveries = 0
        self._last_sample_at = None

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
            if self._optional is not None and self._optional_kind not in ('automatic_text','automatic_primary_probe'):
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
        comparison = self._comparison
        child = comparison['child'] if comparison is not None else None
        if child is not None:
            identities = dict(child['children'])
            if child['created'] is not None:identities[child['pid']] = child['created']
            for pid, created in identities.items():
                if pid in known and known[pid] != created:
                    raise AdmissionError('process_identity_uncertain')
                known[pid] = created
        if len(known) > MAX_IDENTITIES:
            raise AdmissionError('primary_identity_budget')
        return known

    def _memory_limit(self):
        return min(SERVICE_MEMORY_CEILING, self.settings.process_memory_limit_bytes)

    @staticmethod
    def _comparison_family(sample, child):
        # Remember every observed descendant even after reparenting. A missing
        # row is accounting absence only, never permission to release an owner.
        children = dict(child['children'])
        if not child['verified']:return children
        family = {child['pid']} | set(children)
        for _ in range(MAX_IDENTITIES):
            added = {pid for pid, row in sample.identities.items() if row[1] in family} - family
            if not added:break
            family.update(added)
            if len(family) > MAX_IDENTITIES:raise AdmissionError('process_identity_budget')
        else:raise AdmissionError('process_identity_budget')
        for pid in family - {child['pid']}:
            row = sample.identities.get(pid)
            if row is None:continue
            if pid in children and children[pid] != row[0]:
                raise AdmissionError('process_identity_uncertain')
            children[pid] = row[0]
        return children

    @staticmethod
    def _revoke_comparison(record, reason):
        record['cancelled'].set()
        record['running'] = False
        if record['failure'] is None:record['failure'] = reason

    @property
    def sampling_recovery_pending(self):
        with self._condition:
            return (self._sampling_pending and not self._closed
                    and self._unavailable == 'primary_sampling_unavailable')

    def recover_primary_sampling(self):
        # One due observation only. The caller holds no SQLite/permit lock.
        try:self._take_sample(observe=True, recovery_only=True)
        except Exception:return False
        with self._condition:return not self._sampling_pending and not self._unavailable

    def _take_sample(self, *, observe=False, recovery_only=False, sample_deadline=None, comparison_deadline=False,
                     primary_probe_deadline=False, sample_wait=None):
        token = None
        phase = 'sample'
        try:
            with self._condition:
                # Only an already-owned probe supplies this bounded waiter.
                # It returns with this same condition held, so the free check
                # and the fresh flight claim below have no reclaim gap.
                if sample_wait is not None:sample_wait()
                pending = self._sampling_pending
                if self._sample_flight is not None:raise _SampleBusy()
                if recovery_only and not self.sampling_recovery_pending:raise _SampleSkipped('recovery_not_due')
                if pending:
                    if (not observe or not self.sampling_recovery_pending
                            or self._sample_clock() < self._sampling_retry_after):raise _SampleSkipped('cooldown')
                    # Reserved before sampling so concurrent callers cannot
                    # bypass the floor. Every exit releases only this token.
                    self._sampling_retry_after = self._sample_clock() + SAMPLING_RETRY_SECONDS
                known = tuple(self._known_identities_locked().items())
                generation = self._sample_generation
                token = self._sample_flight = object()
            current = (service_sample(self.settings, known=known, deadline=sample_deadline,
                           comparison_deadline=comparison_deadline and not observe,
                           primary_probe_deadline=primary_probe_deadline and not observe) if self._sampler is service_sample
                       else self._sampler(self.settings))
            if len(current.identities) > MAX_IDENTITIES:
                raise AdmissionError('process_identity_budget')
            if (type(current.confirmed_exits) is not int or not 0 <= current.confirmed_exits <= MAX_IDENTITIES
                    or type(current.resampled) is not bool):
                raise AdmissionError('process_identity_uncertain')
            if any(type(x) not in (int,float) or not math.isfinite(x) or x < 0
                   for x in (current.rss,current.caller_rss,current.available,current.disk_free)):
                raise AdmissionError('process_sample_unavailable')
            with self._condition:
                # A newer bind/retire/failure/shutdown invalidates this sample.
                # Neither stale accounting nor a stale successful clear escapes.
                if (self._sample_flight is not token or generation is not self._sample_generation
                        or pending and self._closed):raise _SampleSkipped('stale_generation')
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
                phase = 'family_tracking'
                staged = {}
                for key, record in self._handles.items():
                    children = dict(record['children'])
                    family = {record['pid']}
                    for _ in range(MAX_IDENTITIES):
                        added = {pid for pid, row in current.identities.items() if row[1] in family} - family
                        if not added:break
                        family.update(added)
                    for pid in family - {record['pid']}:
                        identity = current.identities[pid][0]
                        if pid in children and children[pid] != identity:
                            raise AdmissionError('primary_identity_uncertain')
                        children[pid] = identity
                    staged[key] = children
                comparison = self._comparison
                child = comparison['child'] if comparison is not None else None
                comparison_children = self._comparison_family(current, child) if child is not None else {}
                if (sum(1 + len(children) for children in staged.values())
                        + (1 + len(comparison_children) if child is not None else 0) > MAX_IDENTITIES):
                    raise AdmissionError('primary_identity_budget')
                # All validation has completed. Publish accounting and families
                # together; retained identities never imply cleanup or reaping.
                observed = {pid:row[0] for pid,row in current.identities.items()}
                for key, children in staged.items():self._handles[key]['children'] = children
                self._observed = observed
                if child is not None:
                    child['children'] = comparison_children
                    family = {child['pid']} | set(comparison_children)
                    child['rss'] = sum(current.identities[pid][2] for pid in family if pid in current.identities)
                    child['rss_peak'] = max(child['rss_peak'], child['rss'])
                    # These failures affect comparison alone. Observing primary
                    # ownership must not signal or cancel primary/model owners.
                    if child['rss'] > COMPARISON_CHILD_RSS_BYTES:
                        self._revoke_comparison(comparison, 'comparison_child_memory_capacity')
                    if comparison_children:
                        self._revoke_comparison(comparison, 'comparison_unexpected_descendant')
                if comparison is not None and current.rss + comparison['bytes'] >= self._memory_limit():
                    self._revoke_comparison(comparison, 'comparison_memory_capacity')
                # Reporting only. A diagnostic clock failure cannot change an
                # otherwise completed sample or authorize cached accounting.
                try:
                    stamp = self._sample_clock()
                    self._last_sample_at = stamp if type(stamp) in (int,float) and math.isfinite(stamp) else None
                except Exception:self._last_sample_at = None
                if current.resampled:
                    self._completed_sample_retries = min(2**31 - 1, self._completed_sample_retries + 1)
                if current.confirmed_exits:
                    if self._sample_exit_confirmation is None:
                        self._sample_exit_confirmation = {'phase': 'selected_rss',
                            'reason': 'independent_absence_confirmed', 'confirmed_exits': current.confirmed_exits}
                    self._confirmed_sample_exits = min(2**31 - 1,
                        self._confirmed_sample_exits + current.confirmed_exits)
                if pending:
                    self._sampling_pending = False
                    self._unavailable = None
                    self._sampling_recoveries = min(2**31 - 1, self._sampling_recoveries + 1)
                self._condition.notify_all()
            return current
        except _SampleSkipped:raise
        except Exception as error:
            budget = _authenticated_exhaustion(error) if self._sampler is service_sample else None
            caller_expired = (comparison_deadline and not observe and self._sampler is service_sample
                and type(error) is AdmissionError and error.reason == 'comparison_deadline'
                and getattr(error, '_comparison_sample_deadline', None) is _COMPARISON_SAMPLE_DEADLINE)
            probe_expired = (primary_probe_deadline and not observe and self._sampler is service_sample
                and _authenticated_probe_deadline(error))
            if not caller_expired and not probe_expired and (observe or budget is not None):
                self._record_primary_failure('observe_primary' if observe else 'other', phase, error,
                                             sample_budget=budget)
            raise
        finally:
            if token is not None:
                with self._condition:
                    if self._sample_flight is token:self._sample_flight = None
                    self._condition.notify_all()

    def mark_unavailable(self, reason='primary_ownership_uncertain'):
        with self._condition:
            self._sample_generation = object()
            self._unavailable = reason

    def _record_primary_failure(self, operation, phase, error, *, sample_budget=None):
        record = None
        with self._condition:
            if sample_budget is not None:
                # Only _take_sample authenticates the actual sampler boundary.
                self._sample_generation = object()
                self._sampling_exhaustions = min(2**31 - 1, self._sampling_exhaustions + 1)
                if self._sampling_failure is None:
                    detail = getattr(error, '_sample_observation', {})
                    observed_phase = detail.get('phase') if type(detail) is dict else None
                    self._sampling_failure = {'operation': operation,
                        'phase': observed_phase if type(observed_phase) is str and observed_phase in _OBSERVATION_PHASES else 'other',
                        'reason': 'process_sample_budget', 'budget': sample_budget[0],
                        'last_elapsed_ms': sample_budget[1], 'rows_observed': sample_budget[2]}
                if self._unavailable in (None, 'primary_sampling_unavailable'):
                    self._sampling_pending = True
                    self._sampling_retry_after = self._sample_clock() + SAMPLING_RETRY_SECONDS
                    self._unavailable = 'primary_sampling_unavailable'
                return
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
                self._sample_generation = object()
                self._handles[id(proc)] = {'proc': proc, 'pid': proc.pid, 'created': created,
                    'resident': resident, 'children': {}, 'idle': False}
            self.observe_primary()
        except Exception as error:
            self._record_primary_failure('bind_primary', phase, error)

    def observe_primary(self):
        try:self._take_sample(observe=True)
        except Exception:pass  # Failure is classified before releasing its token.

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
                self._sample_generation = object()
        except Exception as error:
            self._record_primary_failure('finish_primary', phase, error)

    def finish_cohort(self, *, normal):
        with self._condition:
            processes = [x['proc'] for x in self._handles.values() if x['resident'] is not None]
        for proc in processes:
            self.finish_primary(proc, normal=normal)

    def _resource_check(self, *, reserve, sample_deadline=None, primary_probe_deadline=False, sample_wait=None):
        # Callers must not hold the condition across the bounded census or any
        # original-handle/resident predicate. Recheck before using accounting.
        with self._condition:generation = self._sample_generation
        try:sample = self._take_sample(sample_deadline=sample_deadline,
            primary_probe_deadline=primary_probe_deadline, sample_wait=sample_wait)
        except _SampleSkipped as skipped:
            error = AdmissionError('process_sample_unavailable')
            error._optional_sample_skip = (_OPTIONAL_SAMPLE,
                'busy_flight' if type(skipped) is _SampleBusy else skipped.subtype)
            raise error from None
        with self._condition:
            if generation is not self._sample_generation:raise AdmissionError('process_sample_unavailable')
            comparison = self._comparison
            comparison_bytes = comparison['bytes'] if comparison is not None else 0
            child = comparison['child'] if comparison is not None else None
            comparison_root = (child['proc'], child['pid']) if child is not None and child['verified'] else None
            comparison_identities = dict(child['children']) if child is not None and child['verified'] else {}
            if child is not None and child['verified'] and child['created'] is not None:
                comparison_identities[child['pid']] = child['created']
            residents = [(record['proc'], record['pid'], record['created'], record['idle'],
                record['resident'], dict(record['children'])) for record in self._handles.values()]
        if sample.rss + comparison_bytes >= self._memory_limit():
            with self._condition:
                if self._comparison is comparison and comparison is not None:
                    self._revoke_comparison(comparison, 'comparison_memory_capacity')
            raise AdmissionError('service_memory_budget')
        if sample.disk_free < max(DISK_RESERVE, self.settings.disk_reserve_bytes):
            raise AdmissionError('service_disk_reserve')
        if sample.available < HOST_RESERVE:
            raise AdmissionError('service_host_reserve')
        if reserve:
            allowed = {os.getpid()}
            for proc, pid, created, idle, resident, children in residents:
                if (not idle or resident is None or not resident() or proc.poll() is not None
                        or sample.identities.get(pid, (None,))[0] != created):
                    raise AdmissionError('resident_identity_uncertain')
                allowed.add(pid)
                for descendant, birth in children.items():
                    if descendant in sample.identities:
                        if sample.identities[descendant][0] != birth:
                            raise AdmissionError('resident_identity_uncertain')
                        allowed.add(descendant)
            # Only identities bound to the original comparison handle or its
            # bounded observed family qualify. An unrelated sibling still fails.
            if comparison_root is not None:
                proc, pid = comparison_root
                row = sample.identities.get(pid)
                if proc.pid != pid or row is not None and row[1] != os.getpid():
                    raise AdmissionError('comparison_identity_uncertain')
            for pid, created in comparison_identities.items():
                if pid in sample.identities:
                    if sample.identities[pid][0] != created:
                        raise AdmissionError('comparison_identity_uncertain')
                    allowed.add(pid)
            if set(sample.identities) - allowed:
                raise AdmissionError('unclassified_service_child')
            resident_rss = sample.rss - sample.caller_rss
            if resident_rss + MOSS_ENVELOPE + WRAPPER_RESERVE + comparison_bytes >= self._memory_limit():
                raise AdmissionError('service_envelope_budget')
            if sample.available < HOST_RESERVE + max(0, MOSS_ENVELOPE - sample.caller_rss) + WRAPPER_RESERVE + comparison_bytes:
                raise AdmissionError('service_host_envelope')
        with self._condition:
            if generation is not self._sample_generation or self._comparison is not comparison:
                raise AdmissionError('process_sample_unavailable')
        return sample

    def _optional_state_locked(self, *, allow_queued=False, primary_probe_running=False):
        # Keep object references, not reusable numeric ids or caller equality.
        # Once this primary probe owns its exact reservation, its four execution
        # gates ignore caller-turn and queue scheduling hints.
        # Reservation admission and every other owner/phase keep their full fingerprint.
        comparison = self._comparison
        identities = (self._sample_generation, self._optional, self._cancel,
            self._cancel_callback, None if primary_probe_running else self._turn,
            None if primary_probe_running else getattr(self._local, 'turn', None),
            None if primary_probe_running else self._handoff, self._configuration_current, comparison,
            comparison['revision'] if comparison is not None else None)
        values = (self._optional_kind, self._optional_work_deadline,
            None if self._cancel is None else self._cancel.is_set(),
            None if primary_probe_running else self._probe,
            self._closed, self._unavailable, bool(self._configuration_current()),
            tuple((kind, intent, intent.granted, intent.entered, intent.released)
                for kind, intent in sorted(self._primary.items())
                if not allow_queued or intent.granted or intent.entered))
        return identities, values

    def _optional_state_unchanged_locked(self, state, *, allow_queued=False, primary_probe_running=False):
        identities, values = self._optional_state_locked(allow_queued=allow_queued,
            primary_probe_running=primary_probe_running)
        return all(old is new for old, new in zip(state[0], identities)) and state[1] == values

    def _primary_probe_active_locked(self):
        return (self._primary_probe_admission is not None or
            self._optional is not None and self._optional_kind == 'automatic_primary_probe')

    def primary_probe_admission_pending(self, error, token, *, work_deadline):
        """Authenticate only a resource-free, unexecuted admission refusal."""
        if type(error) is not AdmissionError or error.reason != 'comparison_start_pending':return False
        marker = getattr(error, '_optional_admission_pending', None)
        with self._condition:
            return (type(marker) is tuple and len(marker) == 8
                and marker[0] is _OPTIONAL_ADMISSION_PENDING and marker[1] is self
                and marker[2] is token and marker[4] == work_deadline
                and self._optional is None and not marker[3].is_set()
                and (type(token) is not dict or token.get('cancelled') is marker[3])
                and self._comparison is not None and self._comparison['start_claim'] is marker[7]
                and self._optional_state_unchanged_locked(marker[5],allow_queued=marker[6]))


    def reserve_optional(self, token, cancelled, cancel_callback, *, owner_kind='preemptible', work_deadline=None):
        if owner_kind not in ('preemptible', 'automatic_text', 'automatic_primary_probe'):raise ValueError('Unknown optional owner')
        if work_deadline is not None and (owner_kind != 'automatic_primary_probe'
                or type(work_deadline) not in (int,float) or not math.isfinite(work_deadline)):
            raise ValueError('Invalid primary probe deadline')
        def current():
            if self._closed or not self._configuration_current():
                raise AdmissionError('jobs_configuration_unavailable')
            turn = (owner_kind in ('automatic_text','automatic_primary_probe') and self._turn is not None
                    and getattr(self._local, 'turn', None) is self._turn)
            primary = any(x.granted or x.entered for x in self._primary.values()) if turn else bool(self._primary)
            if self._optional is not None or primary or self._probe or self._handoff is not None and not turn:
                raise AdmissionError('primary_or_optional_busy')
            if self._unavailable:raise AdmissionError(self._unavailable)
            if cancelled.is_set():raise AdmissionError('optional_cancelled')
        admission = None
        try:
            with self._condition:
                current()
                if owner_kind == 'automatic_primary_probe':
                    comparison = self._comparison
                    if comparison is not None and comparison['start_claim'] is not None:
                        error = AdmissionError('comparison_start_pending')
                        allow_queued = self._turn is not None and getattr(self._local, 'turn', None) is self._turn
                        error._optional_admission_pending = (_OPTIONAL_ADMISSION_PENDING,
                            self, token, cancelled, work_deadline,
                            self._optional_state_locked(allow_queued=allow_queued),
                            allow_queued, comparison['start_claim'])
                        raise error
                    if self._primary_probe_admission is not None:raise AdmissionError('primary_or_optional_busy')
                    admission = self._primary_probe_admission = object()
                allow_queued = (owner_kind in ('automatic_text','automatic_primary_probe')
                    and self._turn is not None and getattr(self._local, 'turn', None) is self._turn)
                state = self._optional_state_locked(allow_queued=allow_queued)
            self._resource_check(reserve=True)
            with self._condition:
                current()
                if (admission is not None and self._primary_probe_admission is not admission
                        or not self._optional_state_unchanged_locked(state, allow_queued=allow_queued)):
                    raise AdmissionError('optional_ownership_changed')
                self._optional, self._cancel, self._cancel_callback = token, cancelled, cancel_callback
                self._optional_kind = owner_kind
                self._optional_work_deadline = work_deadline
                self._state = 'OPTIONAL_RESERVED'
        finally:
            if admission is not None:
                with self._condition:
                    if self._primary_probe_admission is admission:self._primary_probe_admission = None
                    self._condition.notify_all()

    def check_optional(self, token, phase='running', *, running_remaining=None, running_cancelled=None):
        # One absolute busy-flight grace, then one fresh census clipped to the
        # original deadline. No recursive condition hold spans either census.
        with self._condition:
            automatic = self._optional_kind == 'automatic_text' and phase == 'running'
            eligible = automatic and running_remaining is not None and running_cancelled is not None
            probe_deadline = self._optional_work_deadline
            probe_eligible = (self._optional is token and self._optional_kind == 'automatic_primary_probe'
                and phase in ('before_input', 'before_spawn', 'before_admission', 'running')
                and probe_deadline is not None)
            def current():
                if self._optional is not token:raise AdmissionError('optional_ownership_changed')
                primary = (any(x.granted or x.entered for x in self._primary.values())
                    if self._optional_kind in ('automatic_text','automatic_primary_probe') else bool(self._primary))
                if (self._closed or primary or self._cancel.is_set()
                        or not self._configuration_current() or self._unavailable
                        or eligible and (self._optional_kind != 'automatic_text' or running_cancelled())):
                    raise AdmissionError('optional_cancelled')
                if probe_eligible:
                    if (self._optional_work_deadline != probe_deadline
                            or type(token) is dict and token.get('work_deadline') != probe_deadline):
                        raise AdmissionError('optional_ownership_changed')
                    if time.monotonic() >= probe_deadline:
                        error = AdmissionError('optional_cancelled')
                        error._optional_execution_deadline = True
                        raise error
                return running_remaining() if eligible else None
            allow_queued = self._optional_kind in ('automatic_text','automatic_primary_probe')
            primary_probe_running = (self._optional is token
                and self._optional_kind == 'automatic_primary_probe'
                and phase in ('before_input', 'before_spawn', 'before_admission', 'running'))
            state = self._optional_state_locked(allow_queued=allow_queued,
                primary_probe_running=primary_probe_running)
        began = grace_ended = None
        try:
            with self._condition:remaining = current()
            try:
                self._resource_check(reserve=phase in ('reserved','before_spawn'),
                    sample_deadline=probe_deadline if probe_eligible else None,
                    primary_probe_deadline=probe_eligible)
            except AdmissionError as error:
                if (not (eligible or probe_eligible) or type(error) is not AdmissionError or
                        getattr(error,'_optional_sample_skip',None) != (_OPTIONAL_SAMPLE,'busy_flight')):raise
                began = time.monotonic()
                until = min(probe_deadline, began + OPTIONAL_BUSY_GRACE) if probe_eligible else began + OPTIONAL_BUSY_GRACE
                if probe_eligible:
                    def wait_for_probe_sample():
                        nonlocal grace_ended
                        while True:
                            current()
                            if not self._optional_state_unchanged_locked(state, allow_queued=allow_queued,
                                    primary_probe_running=True):raise AdmissionError('optional_ownership_changed')
                            now = time.monotonic()
                            if now >= until:raise error
                            if self._sample_flight is None:
                                grace_ended = now
                                return
                            self._condition.wait(min(.05, until-now))
                    # The waiter and _take_sample's claim run under one condition
                    # acquisition. Sampling runs only after that lock is released.
                    self._resource_check(reserve=phase in ('reserved','before_spawn'),
                        sample_deadline=probe_deadline, primary_probe_deadline=True,
                        sample_wait=wait_for_probe_sample)
                else:
                    while True:
                        with self._condition:
                            remaining = current()
                            if not self._optional_state_unchanged_locked(state, allow_queued=allow_queued):raise AdmissionError('optional_ownership_changed')
                            now = time.monotonic()
                            if now >= until:raise error
                            if self._sample_flight is None:
                                grace_ended = now
                                break
                            self._condition.wait(min(.05, until-now, remaining))
                    with self._condition:remaining = current()
                    try:self._resource_check(reserve=False, sample_deadline=time.monotonic()+remaining)
                    except Exception:
                        running_remaining()  # Preserve the original expired deadline cause.
                        raise
            with self._condition:
                current()
                if not self._optional_state_unchanged_locked(state, allow_queued=allow_queued,
                        primary_probe_running=primary_probe_running):raise AdmissionError('optional_ownership_changed')
                self._state = 'OPTIONAL_RUNNING'
        except Exception as error:
            with self._condition:
                if self._optional is token:self._cancel.set()
                try:
                    skip = getattr(error,'_optional_sample_skip',None)
                    budget = _authenticated_exhaustion(error)
                    detail = getattr(error,'_sample_observation',{})
                    reason = error.reason if type(error) is AdmissionError else None
                    subtype = (skip[1] if type(skip) is tuple and len(skip)==2 and skip[0] is _OPTIONAL_SAMPLE else
                        budget[0]+'_budget' if budget is not None else
                        'deadline' if getattr(error,'_optional_execution_deadline',False) is True else
                        'ownership_changed' if reason=='optional_ownership_changed' else
                        'sampling_pending' if self._sampling_pending and self._unavailable == 'primary_sampling_unavailable' else
                        'sticky' if self._unavailable else
                        'cancelled' if reason=='optional_cancelled' else
                        'resource_limit' if reason in ('service_memory_budget','service_disk_reserve','service_host_reserve',
                            'service_envelope_budget','service_host_envelope') else
                        'census_failure' if type(detail) is dict and detail else 'other')
                    now = self._sample_clock();age = None
                    if self._last_sample_at is not None and type(now) in (int,float) and math.isfinite(now):
                        age = min(2**31-1,max(0,int((now-self._last_sample_at)*1000)))
                    waited = 0 if began is None else min(250,max(0,int(((grace_ended if grace_ended is not None else time.monotonic())-began)*1000)))
                    error._optional_sample_failure = (_OPTIONAL_SAMPLE,subtype,self._optional is token,age,waited,
                        detail.get('phase') if type(detail) is dict else None,
                        detail.get('cause_class') if type(detail) is dict else None,began is not None)
                except Exception:pass
            raise

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
            self._optional_work_deadline = None
            self._state = 'IDLE'
            self._grant_primary_locked()
            self._condition.notify_all()

    def reserve_comparison(self, token, cancelled, *, memory_bytes=COMPARISON_RESERVE_BYTES):
        """One complete child/parent owner, separate from inference ownership.

        The unchanged logical build is 128 MiB. Its process/interpreter and both
        IPC ends require the full 192 MiB reservation even when the cache is idle.
        """
        if type(memory_bytes) is not int or memory_bytes != COMPARISON_RESERVE_BYTES:
            raise AdmissionError('comparison_memory_capacity')
        with self._condition:
            if (self._closed or self._unavailable or self._comparison is not None
                    or cancelled.is_set() or not self._configuration_current()):
                raise AdmissionError('comparison_pending')
            if self._primary_probe_active_locked():
                error = AdmissionError('comparison_pending')
                error._comparison_start_pending = (_COMPARISON_START_PENDING, self, token)
                raise error
            record = self._comparison = {'token':token, 'bytes':memory_bytes,
                'cancelled':cancelled, 'running':False, 'thread':None, 'completed':None,
                'sampling':None, 'revision':object(), 'child':None, 'owner':None,
                'failure':None, 'parent_bytes':0, 'parent_bytes_peak':0,
                'start_claim':None, 'start_owner':None}
            self._sample_generation = object()
        try:
            if not self._refresh_comparison(record):raise AdmissionError('comparison_pending')
        except BaseException:
            # No coordinator/child/buffer has been bound to this provisional owner.
            self.release_comparison(token)
            raise

    @staticmethod
    def _comparison_parent_usage(owner):
        if owner is None:return 0
        value = owner.comparison_parent_bytes()
        if type(value) is not int or not 0 <= value <= COMPARISON_PARENT_BYTES:
            raise AdmissionError('comparison_parent_memory_capacity')
        return value

    def _comparison_sample_check(self, record, sample, parent_bytes):
        # Under the condition; these are only already observed scalar checks.
        child = record['child']
        if child is not None:
            row = sample.identities.get(child['pid'])
            if (not child['verified'] or child['closed'] or row is None
                    or row[0] != child['created'] or row[1] != os.getpid()):
                raise AdmissionError('comparison_identity_uncertain')
            if child['children']:raise AdmissionError('comparison_unexpected_descendant')
            if child['rss'] > COMPARISON_CHILD_RSS_BYTES:
                raise AdmissionError('comparison_child_memory_capacity')
            if child['rss'] + parent_bytes > record['bytes']:
                raise AdmissionError('comparison_memory_capacity')
        # Full measured parent+child+model RSS plus the full owner reserve.
        service, available = sample.rss, HOST_RESERVE + record['bytes']
        if self._optional is not None:
            service = max(service, sample.rss-sample.caller_rss+MOSS_ENVELOPE+WRAPPER_RESERVE)
            available += max(0, MOSS_ENVELOPE-sample.caller_rss)+WRAPPER_RESERVE
        if service + record['bytes'] >= self._memory_limit() or sample.available < available:
            raise AdmissionError('comparison_memory_capacity')
        if sample.disk_free < max(DISK_RESERVE, self.settings.disk_reserve_bytes):
            raise AdmissionError('service_disk_reserve')
        record['parent_bytes'] = parent_bytes
        record['parent_bytes_peak'] = max(record['parent_bytes_peak'], parent_bytes)

    def _refresh_comparison(self, record):
        with self._condition:
            if (self._comparison is not record or record['cancelled'].is_set()
                    or self._closed or self._unavailable or not self._configuration_current()
                    or record['sampling'] is not None):return False
            flight = record['sampling'] = object()
            revision, generation, owner = record['revision'], self._sample_generation, record['owner']
        try:
            # Neither a manager gate nor this condition encloses OS observation
            # or the driver's bounded, read-only parent-buffer accounting.
            parent_bytes = self._comparison_parent_usage(owner)
            sample = self._take_sample()
            parent_bytes = max(parent_bytes, self._comparison_parent_usage(owner))
            with self._condition:
                if (self._comparison is not record or record['sampling'] is not flight
                        or record['revision'] is not revision or self._sample_generation is not generation
                        or record['cancelled'].is_set() or self._closed or self._unavailable
                        or not self._configuration_current()):return False
                self._comparison_sample_check(record, sample, parent_bytes)
                record['running'] = True
                return True
        finally:
            with self._condition:
                if record['sampling'] is flight:record['sampling'] = None
                self._condition.notify_all()

    def comparison_start_blocked(self, token):
        """Scalar scheduling hint only; begin_comparison_start is authoritative."""
        with self._condition:
            record = self._comparison
            return bool(record is not None and record['token'] is token
                and (self._primary_probe_active_locked() or record['start_claim'] is not None))

    def comparison_start_pending(self, error, token):
        marker = getattr(error, '_comparison_start_pending', None)
        return (type(error) is AdmissionError and error.reason == 'comparison_pending'
            and type(marker) is tuple and len(marker) == 3
            and marker[0] is _COMPARISON_START_PENDING and marker[1] is self and marker[2] is token)

    def begin_comparison_start(self, token):
        with self._condition:
            record = self._comparison
            if (record is None or record['token'] is not token or record['owner'] is not None
                    or record['cancelled'].is_set() or not record['running']
                    or self._closed or self._unavailable or not self._configuration_current()):
                raise AdmissionError('comparison_ownership_changed')
            if self._primary_probe_active_locked() or record['start_claim'] is not None:
                error = AdmissionError('comparison_pending')
                error._comparison_start_pending = (_COMPARISON_START_PENDING, self, token)
                raise error
            claim = record['start_claim'] = object()
            return claim

    def comparison_start_valid(self, token, claim, owner):
        with self._condition:
            record = self._comparison
            return bool(record is not None and record['token'] is token and claim is not None
                and record['start_claim'] is claim and record['owner'] is owner
                and record['start_owner'] is owner and not record['cancelled'].is_set()
                and record['running'] and not self._closed and not self._unavailable
                and self._configuration_current() and not self._primary_probe_active_locked())

    def end_comparison_start(self, token, claim, outcome, *, owner=None):
        """Settle the exact start, never infer resource closure from scheduling."""
        with self._condition:
            record = self._comparison
            if (record is None or record['token'] is not token or claim is None
                    or record['start_claim'] is not claim or record['owner'] is not owner
                    or record['start_owner'] is not owner):return False
            child = record['child']
            if outcome == 'resource_free':
                if owner is not None or child is not None:return False
            elif outcome == 'started':
                if (owner is None or child is None or child['owner'] is not owner
                        or not child['verified'] or child['closed'] or record['cancelled'].is_set()
                        or self._closed or self._unavailable or not self._configuration_current()):return False
            elif outcome == 'closed':
                if owner is None or child is not None and (not child['closed']
                        or type(child['original_returncode']) is not int):return False
            else:return False
        if outcome == 'closed':
            try:
                if owner.comparison_resources_closed() is not True or self._comparison_parent_usage(owner) != 0:return False
            except Exception:return False
        with self._condition:
            if (self._comparison is not record or record['token'] is not token
                    or record['start_claim'] is not claim or record['owner'] is not owner
                    or record['start_owner'] is not owner or record['child'] is not child):return False
            record['start_claim'] = record['start_owner'] = None
            self._condition.notify_all()
            return True

    def bind_comparison_owner(self, token, owner, *, claim=None):
        """Bind scalar driver before socket creation, including pre-spawn errors."""
        if (not callable(getattr(owner, 'comparison_parent_bytes', None))
                or not callable(getattr(owner, 'comparison_resources_closed', None))):
            raise AdmissionError('comparison_parent_ownership_uncertain')
        with self._condition:
            record = self._comparison
            if (record is None or record['token'] is not token
                    or record['owner'] is not None and record['owner'] is not owner):
                raise AdmissionError('comparison_ownership_changed')
            if record['owner'] is owner:
                if claim is not None and record['start_claim'] is not claim:
                    raise AdmissionError('comparison_ownership_changed')
                return
            if (claim is None or record['start_claim'] is not claim
                    or self._primary_probe_active_locked() or record['cancelled'].is_set()
                    or self._closed or self._unavailable or not self._configuration_current()):
                raise AdmissionError('comparison_ownership_changed')
            record['owner'] = record['start_owner'] = owner
            record['revision'] = object()
            self._sample_generation = object()

    def bind_comparison_process(self, token, process, *, owner, claim=None):
        """Retain the original handle before any fallible identity observation.

        Driver startup additionally authenticates argv, executable and UID. This
        permit owns its own PID/birth/parent observation and never signals a PID.
        Failed startup still leaves the exact original handle/reserve retained.
        """
        with self._condition:
            record = self._comparison
            if record is None or record['token'] is not token:
                raise AdmissionError('comparison_ownership_changed')
            if record['owner'] is not owner:
                raise AdmissionError('comparison_ownership_changed')
            old = record['child']
            if old is not None:
                if (old['proc'] is not process or old['owner'] is not owner
                        or claim is not None and record['start_claim'] is not claim):
                    raise AdmissionError('comparison_ownership_changed')
                return bool(old['verified'] and not old['closed'] and not record['cancelled'].is_set())
            child = record['child'] = {'proc':process, 'owner':owner, 'pid':process.pid,
                'created':None, 'children':{}, 'verified':False, 'closed':False,
                'closing':None, 'rss':0, 'rss_peak':0, 'baseline_rss':None, 'original_returncode':None}
            record['revision'] = object()
            self._sample_generation = object()
            generation = self._sample_generation
            conflicts = {value['pid'] for value in self._handles.values()}
            conflicts.update(pid for value in self._handles.values() for pid in value['children'])
        try:
            # Popen already happened. Missing/stale startup authority refuses
            # work only AFTER the exact original handle has been retained.
            if claim is None or record['start_claim'] is not claim or record['start_owner'] is not owner:
                raise AdmissionError('comparison_ownership_changed')
            if (type(child['pid']) is not int or child['pid'] <= 0 or child['pid'] == os.getpid()
                    or child['pid'] in conflicts or not callable(getattr(owner, 'comparison_parent_bytes', None))
                    or not callable(getattr(owner, 'comparison_resources_closed', None))):
                raise AdmissionError('comparison_identity_uncertain')
            deadline = time.monotonic() + SAMPLE_SECONDS
            observed = psutil.Process(child['pid'])
            birth = observed.create_time()
            if type(birth) not in (int, float) or not math.isfinite(birth) or birth < 0:
                raise AdmissionError('comparison_identity_uncertain')
            with self._condition:
                if self._comparison is not record or record['child'] is not child:
                    raise AdmissionError('comparison_ownership_changed')
                child['created'] = birth
                self._sample_generation = object()
                generation = self._sample_generation
            if (observed.ppid() != os.getpid() or observed.create_time() != birth
                    or process.pid != child['pid'] or process.poll() is not None
                    or time.monotonic() >= deadline):
                raise AdmissionError('comparison_identity_uncertain')
            parent_bytes = self._comparison_parent_usage(child['owner'])
            with self._condition:
                if (self._comparison is not record or record['child'] is not child
                        or self._sample_generation is not generation):
                    raise AdmissionError('comparison_ownership_changed')
                # OS ownership remains classified even if work was cancelled
                # while identity reads ran; cancellation cannot erase a family.
                child['verified'] = True
                if (record['start_claim'] is not claim or record['start_owner'] is not owner
                        or record['cancelled'].is_set() or self._closed or self._unavailable
                        or not self._configuration_current()):
                    raise AdmissionError('comparison_ownership_changed')
                record['parent_bytes'] = parent_bytes
                record['parent_bytes_peak'] = max(record['parent_bytes_peak'], parent_bytes)
            return True
        except Exception as error:
            with self._condition:
                if self._comparison is record:
                    self._revoke_comparison(record, error.reason if type(error) is AdmissionError
                        else 'comparison_identity_uncertain')
            return False

    def observe_comparison_process(self, token, process, *, startup=False, deadline=None):
        """A fresh bounded census around a single child quantum or IPC page."""
        with self._condition:
            record = self._comparison
            child = record['child'] if record is not None else None
            if (record is None or record['token'] is not token or child is None
                    or child['proc'] is not process or process.pid != child['pid']):
                raise AdmissionError('comparison_ownership_changed')
            if record['sampling'] is not None:raise AdmissionError('comparison_sampling_pending')
            flight = record['sampling'] = object()
            revision, generation = record['revision'], self._sample_generation
        try:
            parent_bytes = self._comparison_parent_usage(child['owner'])
            sample = self._take_sample(sample_deadline=deadline, comparison_deadline=True)
            parent_bytes = max(parent_bytes, self._comparison_parent_usage(child['owner']))
            with self._condition:
                if (self._comparison is not record or record['sampling'] is not flight
                        or record['revision'] is not revision or self._sample_generation is not generation
                        or record['cancelled'].is_set() or self._closed or self._unavailable
                        or not self._configuration_current()):
                    raise AdmissionError(record['failure'] or 'comparison_pending')
                self._comparison_sample_check(record, sample, parent_bytes)
                if startup and child['rss'] > COMPARISON_BASELINE_BYTES:
                    raise AdmissionError('comparison_startup_memory_capacity')
                if startup:child['baseline_rss'] = max(child['baseline_rss'] or 0, child['rss'])
                return {'rss':child['rss'], 'parent_bytes':parent_bytes,
                    'family_size':1+len(child['children'])}
        except Exception as error:
            with self._condition:
                if self._comparison is record:
                    self._revoke_comparison(record, error.reason if type(error) is AdmissionError
                        else 'comparison_resource_observation_unavailable')
            raise
        finally:
            with self._condition:
                if record['sampling'] is flight:record['sampling'] = None
                self._condition.notify_all()

    def finish_comparison_process(self, token, process, *, deadline=None):
        """Positive original wait and bounded observed-family/buffer closure.

        Protocol close acknowledgement is diagnostic, not OS exit authority.
        The coordinator may call this in its finally block; reservation release
        independently requires that the coordinator has actually stopped.
        """
        with self._condition:
            record = self._comparison
            child = record['child'] if record is not None else None
            if (record is None or record['token'] is not token or child is None
                    or child['proc'] is not process or process.pid != child['pid']):return False
            if child['closing'] is not None or record['sampling'] is not None:return False
            flight = child['closing'] = object()
            record['running'] = False
            record['revision'] = object()
            generation = self._sample_generation
        try:
            # No condition, manager lock, database transaction, or owner caller
            # boolean substitutes for this exact retained handle's wait.
            returncode = child['proc'].wait(timeout=0)
            if type(returncode) is not int:return False
            deadline = min(deadline, time.monotonic()+SAMPLE_SECONDS) if deadline is not None else time.monotonic()+SAMPLE_SECONDS
            if time.monotonic() >= deadline:return False
            self._take_sample(sample_deadline=deadline, comparison_deadline=True)
            with self._condition:
                if (self._comparison is not record or record['child'] is not child
                        or self._sample_generation is not generation):return False
                family = dict(child['children'])
                family[child['pid']] = child['created']
            for pid, created in family.items():
                if time.monotonic() >= deadline:return False
                try:
                    live = psutil.Process(pid)
                    # A reused PID belongs to somebody else; never signal it or
                    # accept it as the former owner's positive closure proof.
                    if created is None or live.create_time() != created or live.is_running():return False
                except psutil.NoSuchProcess as error:
                    if type(error) is not psutil.NoSuchProcess:return False
            if child['owner'].comparison_resources_closed() is not True:return False
            parent_bytes = self._comparison_parent_usage(child['owner'])
            if parent_bytes != 0:return False
            with self._condition:
                if (self._comparison is not record or record['child'] is not child
                        or child['closing'] is not flight or self._sample_generation is not generation
                        or record['sampling'] is not None):return False
                child['closed'] = True
                child['original_returncode'] = returncode
                record['parent_bytes'] = parent_bytes
                record['revision'] = object()
                self._sample_generation = object()
                return True
        except Exception:
            return False  # Unknown ownership remains retained; never poison primary.
        finally:
            with self._condition:
                if child['closing'] is flight:child['closing'] = None
                self._condition.notify_all()

    def bind_comparison_thread(self, token, thread, completed):
        with self._condition:
            record = self._comparison
            if record is None or record['token'] is not token:
                raise AdmissionError('comparison_ownership_changed')
            old_thread, old_completed = record['thread'], record['completed']
            if old_thread is not None and (old_completed is None or not old_completed.is_set() or old_thread.is_alive()):
                raise AdmissionError('comparison_ownership_changed')
            record.update(thread=thread, completed=completed)

    def comparison_grant_valid(self, token):
        with self._condition:
            record = self._comparison
            return bool(record is not None and record['token'] is token and record['running']
                and not record['cancelled'].is_set() and not self._closed and not self._unavailable
                and self._configuration_current())

    def pause_comparison(self, token):
        with self._condition:
            if self._comparison is not None and self._comparison['token'] is token:
                if (self._comparison['running'] is False
                        and self._comparison['sampling'] is None):return
                self._comparison['running'] = False
                self._comparison['revision'] = object()

    def resume_comparison(self, token):
        with self._condition:
            record = self._comparison
            if record is None or record['token'] is not token:return False
        return self._refresh_comparison(record)

    def release_comparison(self, token):
        with self._condition:
            record = self._comparison
            if record is None:return True
            if (record['token'] is not token or record['sampling'] is not None
                    or record['start_claim'] is not None):return False
            thread, completed, child = record['thread'], record['completed'], record['child']
            owner = record['owner']
        try:
            if owner is not None and (owner.comparison_resources_closed() is not True
                    or self._comparison_parent_usage(owner) != 0):return False
        except Exception:return False
        if thread is not None and (completed is None or not completed.is_set() or thread.is_alive()):return False
        if child is not None and not child['closed'] and not self.finish_comparison_process(token, child['proc']):return False
        with self._condition:
            if (self._comparison is not record or record['sampling'] is not None
                    or record['start_claim'] is not None or record['thread'] is not thread or record['completed'] is not completed
                    or record['owner'] is not owner or record['child'] is not child
                    or child is not None and (not child['closed']
                        or type(child['original_returncode']) is not int)):return False
            self._comparison = None
            self._sample_generation = object()
            self._condition.notify_all()
            return True

    def close_admission(self):
        with self._condition:
            self._closed = True
            if self._comparison is not None:
                self._comparison['running'] = False
                self._comparison['cancelled'].set()
            self._sample_generation = object()
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
                'comparison_reserved_bytes':self._comparison['bytes'] if self._comparison else 0,
                'comparison_reserved_cpu_lanes':int(self._comparison is not None),
                'comparison_cpu_lanes':int(bool(self._comparison and self._comparison['running'])),
                'comparison_preemption_tail':'50ms_child_compute_plus_bounded_transport_cleanup',
                'comparison_child_rss':self._comparison['child']['rss'] if self._comparison and self._comparison['child'] else 0,
                'comparison_child_rss_peak':self._comparison['child']['rss_peak'] if self._comparison and self._comparison['child'] else 0,
                'comparison_startup_rss':self._comparison['child']['baseline_rss'] if self._comparison and self._comparison['child'] else None,
                'comparison_parent_bytes':self._comparison['parent_bytes'] if self._comparison else 0,
                'comparison_parent_bytes_peak':self._comparison['parent_bytes_peak'] if self._comparison else 0,
                'comparison_cleanup_confirmed':bool(self._comparison and self._comparison['child'] and self._comparison['child']['closed']),
                'comparison_failure':self._comparison['failure'] if self._comparison else None,
                'optional_admission_unavailable': self._unavailable,
                'primary_sampling_pending': self._sampling_pending,
                'primary_sampling_exhaustions': self._sampling_exhaustions,
                'completed_primary_sampling_recoveries': self._sampling_recoveries,
                'primary_sampling_failure': (dict(self._sampling_failure)
                    if self._sampling_failure is not None else None),
                # Observation only; never substitutes for original wait/cleanup.
                'confirmed_sample_exits': self._confirmed_sample_exits,
                'completed_current_sample_retries': self._completed_sample_retries,
                'sample_exit_confirmation': (dict(self._sample_exit_confirmation)
                    if self._sample_exit_confirmation is not None else None),
                'primary_observation_failure': (dict(self._primary_observation_failure)
                    if self._primary_observation_failure is not None else None),
                'admission_closed': self._closed}
