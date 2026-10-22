"""Bounded trial observer transport and partial hooks, NOT a completeness proof.

Stage 3 still has no reconciliation or COMPLETE seal. Its observations
cannot grant sampling/identity authority. A collector must stop on unhealthy,
then positively stop the product before inspecting the preserved sidecar.
"""
from __future__ import annotations

import contextvars
import functools
import itertools
try:
    import fcntl
except ImportError:  # Optional transport must not make default Worker imports platform-specific.
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading

MAX_BYTES = 16 * 1024 * 1024
RESERVE_BYTES = 1024 * 1024
MAX_EVENTS = 8192
MAX_WINDOWS = 2048
MAX_EVENT_BYTES = 1536
MAX_MANIFEST_BYTES = 65536
_HEX = re.compile(r'^[0-9a-f]{64}$')
_MANIFEST_HASHES = frozenset(('source_sha256', 'source_tree_sha256',
    'source_snapshot_sha256', 'runtime_sha256', 'model_manifest_sha256',
    'sampling_policy_sha256', 'observer_sha256'))
_MANIFEST_INTS = frozenset(('source_frames', 'origin_frame', 'start_frame',
    'end_frame', 'interval_frames'))
_PHASES = frozenset(('WINDOW_BEGIN', 'PREPARE_RESULT', 'DUE_PRECHECK',
    'COMMIT_INTENT', 'COMMIT_ACK', 'CALLBACK_RESULT', 'DECISION_INTENT',
    'DECISION_ACK', 'TRANSACTION_SNAPSHOT', 'TERMINAL_RESULT', 'RECOVERY_RESULT'))
_FIELDS = frozenset(('operation_id', 'operation', 'value_kind', 'scope_present',
    'scope_state', 'authority_revision', 'interval_frames', 'sampling_policy',
    'counter_name', 'counter_before', 'counter_after', 'result_state', 'found',
    'resolved', 'should_probe', 'terminal_sha256', 'source_version_sha256', 'component', 'session_id', 'chunk_id', 'recording_id',
    'origin_frame', 'commit_evidence_sha256', 'outcome', 'reason', 'due', 'transaction_due', 'frame',
    'local_start', 'local_end', 'logical_start', 'logical_end',
    'source_revision', 'history_revision', 'high_water', 'last_claim_frame',
    'bootstrap_count', 'last_bucket', 'examined_bucket', 'examined_count',
    'packet_sha256', 'node_sha256', 'mapping_sha256', 'reservation_token',
    'invocation_id', 'exception_class'))


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def capture_fields(fields):
    """Copy a bounded scalar observation in memory; no I/O or DB reads.

    The caller must invoke this on values from its ORIGINAL transaction and
    append only after that transaction returns. This helper does not itself
    enforce a transaction boundary or establish the values' provenance.
    """
    if type(fields) is not dict or len(fields) > len(_FIELDS):
        raise ValueError('fields')
    result = {}
    for key, value in fields.items():
        if type(key) is not str or key not in _FIELDS:
            raise ValueError('field_name')
        if value is None or type(value) is bool:
            pass
        elif type(value) is int:
            if not -(2**63) <= value < 2**63:raise ValueError('integer')
        elif type(value) is str:
            if len(value) > 96 or not value.isascii():raise ValueError('text')
        else:raise ValueError('scalar')
        result[key] = value
    if len(_encoded(result)) > 1024:raise ValueError('fields_bytes')
    return result


class CensusError(RuntimeError):
    pass


class TrialCensus:
    """Single-process/single-writer append sink in a NEW, exclusive directory.

    A failed write may leave a partial tail: never truncate/retry/reopen it.
    Restart append is deliberately unsupported in stage 1. `inspect` may read
    old evidence, but never authorizes resumption or completes missing events.
    """
    def __init__(self, directory, *, run_id, epoch, manifest,
                 event_limit=MAX_EVENTS, window_limit=MAX_WINDOWS):
        self._creator_pid = os.getpid()
        if (fcntl is None or not hasattr(os, 'posix_fallocate')
                or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')):
            raise CensusError('unsupported_observer_platform')
        if (type(run_id) is not str or not _HEX.fullmatch(run_id)
                or type(epoch) is not int or not 0 <= epoch < 2**31
                or type(event_limit) is not int or not 1 <= event_limit <= MAX_EVENTS
                or type(window_limit) is not int or not 1 <= window_limit <= MAX_WINDOWS
                or type(manifest) is not dict):
            raise ValueError('census_configuration')
        if set(manifest) != _MANIFEST_HASHES | _MANIFEST_INTS | {'sampling_policy'}:
            raise ValueError('manifest_fields')
        for key in _MANIFEST_HASHES:
            if type(manifest[key]) is not str or not _HEX.fullmatch(manifest[key]):
                raise ValueError('manifest_hash')
        for key in _MANIFEST_INTS:
            if type(manifest[key]) is not int or not 0 <= manifest[key] < 2**63:
                raise ValueError('manifest_integer')
        if (not manifest['interval_frames']
                or not 0 <= manifest['start_frame'] < manifest['end_frame'] <= manifest['source_frames']
                or type(manifest['sampling_policy']) is not str
                or not 1 <= len(manifest['sampling_policy']) <= 96
                or not manifest['sampling_policy'].isascii()):
            raise ValueError('manifest_geometry_policy')
        if manifest['observer_sha256'] != _sha(Path(__file__).read_bytes()):
            raise ValueError('observer_source_mismatch')
        envelope = {'schema_version': 1, 'stage': 'UNQUALIFIED_STAGE1',
                    'run_id': run_id, 'epoch': epoch, 'creator_pid': self._creator_pid, 'identity': manifest,
                    'caps': {'bytes': MAX_BYTES, 'reserve_bytes': RESERVE_BYTES,
                             'events': event_limit, 'windows': window_limit,
                             'event_bytes': MAX_EVENT_BYTES}}
        raw = _encoded(envelope)
        if len(raw) > MAX_MANIFEST_BYTES:raise ValueError('manifest_size')
        self.directory = Path(directory)
        self.run_id, self.epoch = run_id, epoch
        self.event_limit, self.window_limit = event_limit, window_limit
        self._mutex = threading.Lock()
        self._seq, self._prev = 0, '0' * 64
        self._windows = set()
        self._healthy, self._failure, self._closed = True, None, False
        self._fds, self._identities = {}, {}
        self._event_bytes = 0
        self._manifest_sha = _sha(raw)
        self.directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        self._dir_identity = self._identity(self.directory.lstat())
        try:
            for name in ('owner', 'manifest.json', 'events.jsonl', 'terminal.reserve'):
                fd = os.open(self.directory / name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                self._fds[name] = fd
                self._identities[name] = self._identity(os.fstat(fd))
            fcntl.flock(self._fds['owner'], fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._write_all(self._fds['manifest.json'], raw)
            os.fsync(self._fds['manifest.json'])
            # Real allocation, not a sparse promise of future terminal space.
            os.posix_fallocate(self._fds['terminal.reserve'], 0, RESERVE_BYTES)
            os.fsync(self._fds['terminal.reserve'])
            self._sync_directory()
            self._sync_parent_directory()
            self._check_files()
        except BaseException:
            self._healthy, self._failure = False, 'initialization_failed'
            self._close_fds()
            raise

    @staticmethod
    def _identity(st):return st.st_dev, st.st_ino

    @staticmethod
    def _write_all(fd, raw):
        offset = 0
        while offset < len(raw):
            wrote = os.write(fd, memoryview(raw)[offset:])
            if wrote <= 0:raise OSError('short_write')
            offset += wrote

    def _sync_directory(self):
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)

    def _sync_parent_directory(self):
        # fsync of the new directory alone does not durably link it into its
        # parent. Initialization cannot acknowledge before this entry is durable.
        fd = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)

    def _check_files(self):
        if self._identity(self.directory.lstat()) != self._dir_identity:
            raise CensusError('directory_replaced')
        if set(os.listdir(self.directory)) != set(self._fds):
            raise CensusError('unexpected_files')
        allocated = 0
        for name, fd in self._fds.items():
            st, path_st = os.fstat(fd), (self.directory / name).lstat()
            if (not stat.S_ISREG(st.st_mode) or not stat.S_ISREG(path_st.st_mode)
                    or st.st_nlink != 1 or self._identity(st) != self._identities[name]
                    or self._identity(path_st) != self._identities[name]):
                raise CensusError('file_replaced')
            allocated += max(st.st_size, st.st_blocks * 512)
        if allocated > MAX_BYTES:raise CensusError('allocation_cap')
        if os.fstat(self._fds['events.jsonl']).st_size != self._event_bytes:
            raise CensusError('event_size_changed')
        return allocated

    def _owned_process(self):
        # Check before any inherited mutex/FD operation: flock survives fork,
        # whereas Python locks/counters do not provide cross-process ownership.
        return os.getpid() == self._creator_pid

    def _reject_inherited(self):
        self._healthy = False
        self._failure = 'inherited_process'

    @property
    def healthy(self):return self._owned_process() and self._healthy and not self._closed

    @property
    def failure(self):return self._failure

    def invalidate(self, reason='observer_error'):
        # Fixed category only; never retain exception objects or private text.
        if not self._owned_process():
            self._reject_inherited()
            return
        with self._mutex:
            self._healthy = False
            if self._failure is None:self._failure = 'observer_error'

    def append(self, phase, opportunity, fields):
        """Return False and stick unhealthy on ANY observation failure.

        No exception escapes into the product caller. Caller must independently
        monitor health; a False return does not mean a product skip occurred.
        """
        if not self._owned_process():
            self._reject_inherited()
            return False
        with self._mutex:
            if not self.healthy:return False
            try:
                if phase not in _PHASES or type(opportunity) is not str or not _HEX.fullmatch(opportunity):
                    raise CensusError('event_identity')
                values = capture_fields(fields)
                is_new = opportunity not in self._windows
                if is_new and phase != 'WINDOW_BEGIN':raise CensusError('window_not_started')
                if not is_new and phase == 'WINDOW_BEGIN':raise CensusError('duplicate_window')
                if self._seq >= self.event_limit:raise CensusError('event_cap')
                if is_new and len(self._windows) >= self.window_limit:raise CensusError('window_cap')
                row = {'schema_version': 1, 'run_id': self.run_id, 'epoch': self.epoch,
                       'seq': self._seq + 1, 'previous_sha256': self._prev,
                       'manifest_sha256': self._manifest_sha, 'phase': phase,
                       'opportunity': opportunity, 'fields': values}
                digest = _sha(_encoded(row));row['event_sha256'] = digest
                raw = _encoded(row) + b'\n'
                if len(raw) > MAX_EVENT_BYTES:raise CensusError('event_bytes_cap')
                allocated = self._check_files()
                # Allow a whole allocation block for the next append.
                if allocated + len(raw) + 4096 > MAX_BYTES:raise CensusError('allocation_cap')
                self._write_all(self._fds['events.jsonl'], raw)
                os.fsync(self._fds['events.jsonl'])
                self._event_bytes += len(raw)
                self._seq += 1;self._prev = digest
                if is_new:self._windows.add(opportunity)
                return True
            except BaseException:
                self._healthy = False
                self._failure = 'append_failed'
                return False

    def close(self):
        """Write bounded diagnostic terminal metadata, NEVER a complete seal."""
        if not self._owned_process():
            self._reject_inherited()
            return
        with self._mutex:
            if self._closed:return
            terminal = {'schema_version': 1, 'status': 'UNQUALIFIED_STAGE1',
                        'observer_healthy_before_close': self._healthy,
                        'failure': self._failure, 'events_acknowledged': self._seq,
                        'windows_started': len(self._windows), 'last_sha256': self._prev,
                        'manifest_sha256': self._manifest_sha,
                        'complete_census_certified': False}
            raw = _encoded(terminal) + b'\n'
            try:
                if len(raw) >= RESERVE_BYTES:raise CensusError('terminal_cap')
                fd = self._fds['terminal.reserve']
                os.lseek(fd, 0, os.SEEK_SET)
                self._write_all(fd, raw)
                os.fsync(fd)
            except BaseException:
                self._healthy = False
                if self._failure is None:self._failure = 'terminal_failed'
            finally:
                self._closed = True
                self._close_fds()

    def _close_fds(self):
        if not self._owned_process():return
        for fd in self._fds.values():
            try:os.close(fd)
            except OSError:pass
        self._fds = {}


class ObserverPort:
    """Exception-isolated explicit interface; not auto-installed in the product."""
    def __init__(self, sink):
        self.sink = sink
        self.failed = False

    @property
    def healthy(self):
        try:return not self.failed and self.sink.healthy
        except BaseException:return False

    def observe(self, phase, opportunity, fields):
        try:
            result = self.sink.append(phase, opportunity, fields)
            if result is not True:self.failed = True
            return result is True
        except BaseException:
            self.failed = True
            try:self.sink.invalidate()
            except BaseException:pass
            return False


def inspect(directory):
    """Bounded read-only integrity inspection; never a completeness proof.

    Rejects links, extra files, oversized/torn records and unknown window keys.
    This does not prove original product execution or authorize restart append.
    """
    root = Path(directory)
    names = {'owner', 'manifest.json', 'events.jsonl', 'terminal.reserve'}
    if root.is_symlink() or set(os.listdir(root)) != names:raise CensusError('files')
    allocated = 0
    for name in names:
        st = (root / name).lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:raise CensusError('file')
        allocated += max(st.st_size, st.st_blocks * 512)
    if allocated > MAX_BYTES:raise CensusError('allocation_cap')
    manifest_path = root / 'manifest.json'
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:raise CensusError('manifest_size')
    manifest_raw = manifest_path.read_bytes()
    manifest_sha = _sha(manifest_raw)
    manifest = json.loads(manifest_raw)
    if manifest.get('schema_version') != 1 or manifest.get('stage') != 'UNQUALIFIED_STAGE1':
        raise CensusError('manifest_schema')
    seq, prev, windows, identity = 0, '0' * 64, set(), None
    with (root / 'events.jsonl').open('rb') as source:
        while True:
            raw = source.readline(MAX_EVENT_BYTES + 1)
            if not raw:break
            if len(raw) > MAX_EVENT_BYTES or not raw.endswith(b'\n'):raise CensusError('tail')
            row = json.loads(raw)
            if _encoded(row) + b'\n' != raw:raise CensusError('noncanonical_event')
            if set(row) != {'schema_version', 'run_id', 'epoch', 'seq', 'previous_sha256',
                            'manifest_sha256', 'phase', 'opportunity', 'fields', 'event_sha256'}:
                raise CensusError('event_schema')
            digest = row.pop('event_sha256')
            if _sha(_encoded(row)) != digest:raise CensusError('digest')
            if (row['schema_version'] != 1 or row['seq'] != seq + 1
                    or row['previous_sha256'] != prev or row['manifest_sha256'] != manifest_sha
                    or row['phase'] not in _PHASES):raise CensusError('chain')
            current = row['run_id'], row['epoch']
            if current != (manifest['run_id'], manifest['epoch']):raise CensusError('manifest_identity')
            if identity is not None and current != identity:raise CensusError('identity')
            identity = current
            key = row['opportunity']
            if type(key) is not str or not _HEX.fullmatch(key):raise CensusError('opportunity')
            if row['phase'] == 'WINDOW_BEGIN':
                if key in windows:raise CensusError('duplicate_window')
                windows.add(key)
            elif key not in windows:raise CensusError('window_not_started')
            capture_fields(row['fields'])
            seq += 1;prev = digest
            if (seq > min(MAX_EVENTS, manifest['caps']['events'])
                    or len(windows) > min(MAX_WINDOWS, manifest['caps']['windows'])):
                raise CensusError('count_cap')
    return {'status': 'INTEGRITY_ONLY_NOT_COMPLETE', 'events': seq,
            'windows': len(windows), 'last_sha256': prev,
            'complete_census_certified': False}


_ACTIVE = contextvars.ContextVar('trial_census_active_window', default=None)


def observe_active(phase, fields):
    """Only call outside DB contexts; never changes a product result/exception."""
    try:
        active = _ACTIVE.get()
        if active is not None:
            port, key = active
            return port.observe(phase, key, fields)
    except BaseException:
        try:
            if active is not None:active[0].failed = True
        except BaseException:pass
        return False
    return False


class window_observation:
    """Exception-contained synchronous worker scope. No thread propagation.

    Current identity binds chunk/session/exact core frames to the immutable
    manifest via its events. Source mapping/revision remains UNKNOWN until the
    later provenance/transaction integration, so no complete seal is possible.
    """
    def __init__(self, observer, chunk, start, end, capture):
        self.observer = observer
        self.args = chunk, start, end, capture
        self.token = None

    def __enter__(self):
        try:self.token = _ACTIVE.set(None)
        except BaseException:pass
        if self.observer is None:return self
        try:
            from .native_owned_cells import exact_frame
            chunk, start, end, capture = self.args
            local_start, local_end = exact_frame(start), exact_frame(end)
            origin = getattr(capture, 'origin_frame', None)
            if type(origin) is not int:origin = None
            fields = {'session_id': chunk.get('session_id'), 'chunk_id': chunk.get('id'),
                      'recording_id': None, 'local_start': local_start, 'local_end': local_end,
                      'origin_frame': origin,
                      'logical_start': origin + local_start if origin is not None else None,
                      'logical_end': origin + local_end if origin is not None else None,
                      'reason': 'source_mapping_revision_unknown'}
            fields = capture_fields(fields)
            key = _sha(_encoded(fields))
            _ACTIVE.set((self.observer, key))
            observe_active('WINDOW_BEGIN', fields)
        except BaseException:
            try:self.observer.failed = True
            except BaseException:pass
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is not None:
                observe_active('PREPARE_RESULT', {'component': 'worker_scope',
                               'outcome': 'exception', 'reason': 'unresolved_window_outcome'})
        except BaseException:pass
        finally:
            try:
                if self.token is not None:_ACTIVE.reset(self.token)
            except BaseException:pass
        return False


def observe_commit(call):
    """The original commit call is executed exactly once, unchanged return."""
    observe_active('COMMIT_INTENT', {})
    try:
        result = call()
    except BaseException:
        observe_active('COMMIT_ACK', {'outcome': 'unknown', 'reason': 'commit_exception'})
        raise
    observe_active('COMMIT_ACK', {'outcome': 'true' if result is True else 'not_true',
                                'reason': 'none' if result is True else 'commit_not_acknowledged'})
    return result


def observe_callback(call):
    """Preserve original callback return/exception; caller's catch is unchanged."""
    observe_active('CALLBACK_RESULT', {'outcome': 'invoked'})
    try:
        result = call()
    except BaseException:
        observe_active('CALLBACK_RESULT', {'outcome': 'exception', 'reason': 'callback_outcome_unknown'})
        raise
    observe_active('CALLBACK_RESULT', {'outcome': 'returned'})
    return result


def classify_window(phases):
    """Small synthetic coverage helper, not stopped-snapshot reconciliation."""
    committed = any(phase == 'COMMIT_ACK' and fields.get('outcome') == 'true'
                    for phase, fields in phases)
    callback = any(phase == 'CALLBACK_RESULT' and fields.get('outcome') in ('returned', 'absent')
                   for phase, fields in phases)
    return {'committed_acknowledged': committed, 'callback_observed': callback,
            'eligibility': 'UNKNOWN', 'complete_census_certified': False}


_TRANSACTION = contextvars.ContextVar('trial_census_transaction', default=None)
_OPERATION_IDS = itertools.count(1)
_MAX_TRANSACTION_RECORDS = 32


def current_binding():
    try:return _ACTIVE.get()
    except BaseException:return None


class bound_observation:
    """Restore only the original explicit binding; never infer from new windows."""
    def __init__(self, binding):self.binding, self.token = binding, None
    def __enter__(self):
        try:self.token = _ACTIVE.set(self.binding)
        except BaseException:pass
        return self
    def __exit__(self, *args):
        try:
            if self.token is not None:_ACTIVE.reset(self.token)
        except BaseException:pass
        return False


def capture_transaction(fields):
    """Evaluate lazy scalar reads only inside the isolated, active buffer.

    Producers must not perform I/O, encoding, hashing or queries. A callable is
    evaluated synchronously, never retained, so mutable pre-state is copied now.
    """
    trace = None
    try:
        trace = _TRANSACTION.get()
        if trace is None or trace['failed']:return
        if len(trace['records']) >= _MAX_TRANSACTION_RECORDS:raise ValueError('buffer_cap')
        if callable(fields):fields = fields()
        copied = {}
        if type(fields) is not dict or len(fields) > len(_FIELDS):raise ValueError('fields')
        for key, value in fields.items():
            if type(key) is not str or key not in _FIELDS:raise ValueError('field')
            if value is None or type(value) is bool:pass
            elif type(value) is int:
                if not -(2**63) <= value < 2**63:raise ValueError('integer')
            elif type(value) is str:
                if len(value) > 96 or not value.isascii():raise ValueError('text')
            else:raise ValueError('scalar')
            copied[key] = value
        trace['records'].append(copied)
    except BaseException:
        if trace is not None:trace['failed'] = True


def capture_scope(scope, extra=None):
    """Scope and extra mapping reads are skipped when no capture is active."""
    def fields_from_scope():
        fields = {'value_kind':'observed_pre', 'scope_present':scope is not None}
        if scope is not None:
            for key in ('high_water', 'last_claim_frame', 'bootstrap_count', 'last_bucket',
                        'examined_bucket', 'examined_count', 'source_revision',
                        'history_revision', 'authority_revision', 'interval_frames', 'sampling_policy'):
                try:fields[key] = scope[key]
                except (KeyError, IndexError):fields[key] = None
        if extra is not None:fields.update(extra() if callable(extra) else extra)
        return fields
    capture_transaction(fields_from_scope)


def capture_counters(counts, *, before):
    """Avoid observer-only counter iteration entirely without an active trace."""
    trace = None
    try:
        trace = _TRANSACTION.get()
        if trace is None or trace['failed']:return
        for key, value in counts.items():
            capture_transaction({'value_kind':'observed_pre' if before else 'intended_post',
                                 'counter_name':key,
                                 'counter_before' if before else 'counter_after':value})
            if trace['failed']:return
    except BaseException:
        try:
            if trace is not None:trace['failed'] = True
        except BaseException:pass


def _finish_operation(trace, result, returned):
    try:
        if trace['failed']:
            port, _ = trace['binding']
            port.failed = True
            try:port.sink.invalidate()
            except BaseException:pass
            return
        for fields in trace['records']:
            observe_active('TRANSACTION_SNAPSHOT', dict(fields, operation_id=trace['id'], operation=trace['name']))
        fields = {'operation_id':trace['id'], 'operation':trace['name'],
                  'outcome':'returned' if returned else 'unknown',
                  'reason':'none' if returned else 'method_exception'}
        if returned and type(result) is dict:
            for source, target in (('token','reservation_token'), ('state','result_state'),
                                   ('found','found'), ('resolved','resolved'),
                                   ('should_probe','should_probe'), ('due','due'),
                                   ('terminal_sha256','terminal_sha256')):
                if source in result:fields[target] = result[source]
        elif returned and type(result) is bool:fields['result_state'] = 'true' if result else 'false'
        observe_active('DECISION_ACK', fields)
    except BaseException:
        try:trace['binding'][0].failed = True
        except BaseException:pass


def history_operation(name):
    """ACK after original method and all original DB contexts have returned."""
    def decorate(original):
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            binding = current_binding()
            if binding is None:return original(*args, **kwargs)
            trace, token, returned, result = None, None, False, None
            try:
                trace = {'name':name, 'id':format(next(_OPERATION_IDS), '016x'),
                         'binding':binding, 'records':[], 'failed':False}
                observe_active('DECISION_INTENT', {'operation':name, 'operation_id':trace['id']})
                token = _TRANSACTION.set(trace)
            except BaseException:
                try:binding[0].failed = True
                except BaseException:pass
            try:
                result = original(*args, **kwargs)
                returned = True
                return result
            finally:
                try:
                    if token is not None:_TRANSACTION.reset(token)
                    if trace is not None:_finish_operation(trace, result, returned)
                except BaseException:
                    try:binding[0].failed = True
                    except BaseException:pass
        return wrapped
    return decorate



def primary_binding(argument=False):
    """Retain original opportunity across maintenance and model-thread boundaries."""
    def decorate(original):
        @functools.wraps(original)
        def wrapped(self, *args, **kwargs):
            probe, binding = None, None
            try:
                probe = (args[0] if args else kwargs.get('probe', kwargs.get('handle'))) if argument else getattr(self, '_primary_pending', None)
                if type(probe) is dict:binding = probe.get('_trial_census_binding')
            except BaseException:pass
            with bound_observation(binding):
                returned = False
                try:
                    answer = original(self, *args, **kwargs)
                    returned = True
                    return answer
                finally:
                    try:
                        if binding is not None:
                            observe_active('TERMINAL_RESULT', {'component':original.__name__,
                                'outcome':'returned' if returned else 'unknown',
                                'result_state':probe.get('state'),
                                'frame':probe.get('source_start_frame'),
                                'invocation_id':probe.get('invocation_id'),
                                'reason':'state_only_not_durable_terminal_proof'})
                    except BaseException:
                        try:binding[0].failed = True
                        except BaseException:pass
        return wrapped
    return decorate
