"""Finite legacy-primary/MOSS exclusion and conservative optional admission.

This observer never signals a primary process or replaces its cleanup. Uncertain
primary observations disable optional work; uncertain MOSS cleanup holds the
permit. The primary cohort remains concurrent (one Worker and one upload).
"""
from contextlib import contextmanager
from dataclasses import dataclass
import os
import shutil
import threading
import time

import psutil

MOSS_ENVELOPE = int(3.5 * 1024**3)
WRAPPER_RESERVE = 16 * 1024**2
HOST_RESERVE = 2 * 1024**3
DISK_RESERVE = int(5.5 * 1024**3)
MAX_ROWS, MAX_IDENTITIES, SAMPLE_SECONDS = 4096, 128, .250


class AdmissionError(RuntimeError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProcessSample:
    identities: dict
    rss: int
    caller_rss: int
    available: int
    disk_free: int


def service_sample(settings, *, clock=time.monotonic, known=()):
    """Bounded strict full-service observation; never confers kill authority."""
    deadline = clock() + SAMPLE_SECONDS
    rows = {}
    try:
        caller = psutil.Process(os.getpid())
        caller_created = caller.create_time()
        for index, process in enumerate(psutil.process_iter(['pid', 'ppid'])):
            if index >= MAX_ROWS or clock() >= deadline:
                raise AdmissionError('process_sample_budget')
            info = process.info
            pid, parent = info['pid'], info['ppid']
            if type(pid) is not int or type(parent) is not int or pid in rows:
                raise AdmissionError('process_identity_uncertain')
            rows[pid] = (parent, process)
        selected = {caller.pid}
        remembered = dict(known)
        if len(remembered) > MAX_IDENTITIES:
            raise AdmissionError('process_identity_budget')
        for pid, created in remembered.items():
            if clock() >= deadline:
                raise AdmissionError('process_sample_budget')
            if pid in rows:
                selected.add(pid)
            else:
                try:
                    psutil.Process(pid).create_time()
                except psutil.NoSuchProcess:
                    continue
                raise AdmissionError('remembered_identity_unobserved')
        if len(selected) > MAX_IDENTITIES:
            raise AdmissionError('process_identity_budget')
        for _ in range(MAX_IDENTITIES):
            added = {pid for pid, (parent, _) in rows.items() if parent in selected} - selected
            if not added:
                break
            selected.update(added)
            if len(selected) > MAX_IDENTITIES or clock() >= deadline:
                raise AdmissionError('process_identity_budget')
        else:
            raise AdmissionError('process_identity_budget')
        if caller.pid not in rows:
            raise AdmissionError('caller_identity_uncertain')
        identities = {}
        total = caller_rss = 0
        for pid in selected:
            if clock() >= deadline:
                raise AdmissionError('process_sample_budget')
            parent, process = rows[pid]
            created = process.create_time()
            rss = process.memory_info().rss
            if process.create_time() != created or process.ppid() != parent or type(rss) is not int or rss < 0:
                raise AdmissionError('process_identity_uncertain')
            if pid in remembered and remembered[pid] != created:
                raise AdmissionError('process_identity_uncertain')
            if pid == caller.pid and created != caller_created:
                raise AdmissionError('caller_identity_uncertain')
            identities[pid] = (created, parent, rss)
            total += rss
            if pid == caller.pid:
                caller_rss = rss
        available = psutil.virtual_memory().available
        free = shutil.disk_usage(settings.data_dir).free
        if clock() >= deadline:
            raise AdmissionError('process_sample_budget')
        return ProcessSample(identities, total, caller_rss, available, free)
    except (psutil.Error, OSError, KeyError, TypeError, ValueError):
        raise AdmissionError('process_sample_unavailable') from None


class PrimaryIntent:
    def __init__(self, owner, kind):
        self.owner, self.kind, self.entered, self.released = owner, kind, False, False

    def wait(self, stop=None):
        owner = self.owner
        with owner._condition:
            while owner._optional is not None:
                if stop is not None and stop():
                    self.release()
                    raise InterruptedError('Primary wait interrupted')
                owner._condition.wait(.05)
            if stop is not None and stop():
                self.release()
                raise InterruptedError('Primary wait interrupted')
            self.entered = True
            owner._local.intent = self
        return self

    def release(self):
        owner = self.owner
        with owner._condition:
            if not self.released:
                self.released = True
                owner._primary.pop(self.kind, None)
                if getattr(owner._local, 'intent', None) is self:
                    owner._local.intent = None
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
        self._cancel = self._cancel_callback = None
        self._state = 'IDLE'
        self._closed = False
        self._unavailable = None
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
            if self._closed or record['cancelled'].is_set() or self._primary:
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
            if self._optional is not None:
                self._cancel.set()  # Publication invalidation precedes termination.
                callback = self._cancel_callback
        if callback is not None:
            try:
                callback()
            except Exception:
                pass  # Optional ownership remains held, never silently released.
        return intent

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

    def _take_sample(self):
        with self._condition:
            known = tuple(self._observed.items())
        current = (service_sample(self.settings, known=known) if self._sampler is service_sample
                   else self._sampler(self.settings))
        if len(current.identities) > MAX_IDENTITIES:
            raise AdmissionError('process_identity_budget')
        with self._condition:
            for pid, row in current.identities.items():
                if pid in self._observed and self._observed[pid] != row[0]:
                    raise AdmissionError('process_identity_uncertain')
            self._observed = {pid:row[0] for pid,row in current.identities.items()}
        return current

    def mark_unavailable(self, reason='primary_ownership_uncertain'):
        with self._condition:
            self._unavailable = reason

    def bind_primary(self, process, *, resident=None):
        """Remember original handle identity; observation errors never escape."""
        try:
            proc = process.proc if hasattr(process, 'proc') else process
            with self._condition:
                if len(self._handles) >= MAX_IDENTITIES:
                    raise AdmissionError('primary_identity_budget')
                created = psutil.Process(proc.pid).create_time()
                self._handles[id(proc)] = {'proc': proc, 'pid': proc.pid, 'created': created,
                    'resident': resident, 'children': {}, 'idle': False}
            self.observe_primary()
        except Exception:
            self.mark_unavailable()

    def observe_primary(self):
        try:
            sample = self._take_sample()
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
        except Exception:
            self.mark_unavailable()

    def finish_primary(self, process, *, normal=False):
        """Positive original wait/reap or explicit resident-idle is required."""
        proc = process.proc if hasattr(process, 'proc') else process
        try:
            self.observe_primary()
            with self._condition:
                record = self._handles.get(id(proc))
                if record is None or record['proc'] is not proc:
                    raise AdmissionError('primary_handle_unknown')
                if normal and record['resident'] is not None and record['resident']():
                    if (proc.poll() is not None or psutil.Process(proc.pid).create_time() != record['created']):
                        raise AdmissionError('resident_identity_uncertain')
                    record['idle'] = True
                    return
                if proc.poll() is None or proc.wait(timeout=0) is None:
                    raise AdmissionError('primary_wait_unconfirmed')
                for pid, created in ((record['pid'], record['created']), *record['children'].items()):
                    try:
                        live = psutil.Process(pid)
                        if live.create_time() != created or live.is_running():
                            raise AdmissionError('primary_descendant_unresolved')
                    except psutil.NoSuchProcess:
                        pass
                del self._handles[id(proc)]
        except Exception:
            self.mark_unavailable()

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

    def reserve_optional(self, token, cancelled, cancel_callback):
        with self._condition:
            if self._closed or not self._configuration_current():
                raise AdmissionError('jobs_configuration_unavailable')
            if self._optional is not None or self._primary or self._probe:
                raise AdmissionError('primary_or_optional_busy')
            if self._unavailable:
                raise AdmissionError(self._unavailable)
            self._resource_check(reserve=True)
            self._optional, self._cancel, self._cancel_callback = token, cancelled, cancel_callback
            self._state = 'OPTIONAL_RESERVED'

    def check_optional(self, token, phase='running'):
        with self._condition:
            if self._optional is not token:
                raise AdmissionError('optional_ownership_changed')
            if (self._closed or self._primary or self._cancel.is_set()
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
            self._state = 'IDLE'
            self._condition.notify_all()

    def close_admission(self):
        with self._condition:
            self._closed = True
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
                'optional_admission_unavailable': self._unavailable,
                'admission_closed': self._closed}
