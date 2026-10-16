"""Volatile one-job MOSS owner with private optional service gates.

No backlog, replay, native re-invocation, or automatic abandoned
claim removal. Cleanup reuses the reviewed PID/create-time/original-handle
mechanism; its scope is observed owned descendants, not a kernel containment
or exhaustive process-discovery guarantee.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import wave

from .moss_handy_adapter import encoded, project
from .moss_handy_profile import canonical, check_loader_context, profile
from .moss_handy_runner import (CAPS, SCRATCH_BYTES, check_directory, directory_identity,
    float_bytes, process_identity, read_owned, samples_from_pcm, write_owned)
from .native_lexical_alternatives import SourceSupport

ROOT = Path(__file__).resolve().parents[1]


class Busy(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


_ERROR_TYPES = frozenset(('Cancelled', 'Busy', 'AdmissionError', 'RuntimeError',
    'ValueError', 'TypeError', 'KeyError', 'OSError', 'PermissionError',
    'FileNotFoundError', 'ProcessLookupError', 'TimeoutError', 'TimeoutExpired',
    'MemoryError', 'UnicodeError', 'UnicodeDecodeError', 'JSONDecodeError',
    'AssertionError', 'NoSuchProcess', 'AccessDenied', 'ZombieProcess'))
_FAILURE_PHASES = frozenset(('prepared', 'before_input', 'input', 'driver',
    'before_spawn', 'start', 'wait', 'validate_return', 'native_return', 'projection'))
_ADMISSION_REASONS = frozenset(('optional_cancelled', 'primary_or_optional_busy',
    'primary_ownership_uncertain', 'service_memory_budget', 'service_disk_reserve',
    'service_host_reserve', 'process_sample_budget', 'process_identity_uncertain',
    'process_identity_budget', 'remembered_identity_unobserved', 'caller_identity_uncertain',
    'process_sample_unavailable', 'resident_identity_uncertain', 'unclassified_service_child',
    'service_envelope_budget', 'service_host_envelope', 'jobs_configuration_unavailable',
    'optional_ownership_changed', 'publication_fence_busy', 'optional_prelaunch_deadline'))


# Existing gate exceptions have fixed public reasons; never retain their messages.
_GATE_UNAVAILABLE_REASONS = frozenset(('cooperative_deadline', 'operation_work_capacity',
    'primary_database_replaced', 'primary_read_snapshot_not_ended',
    'primary_list_read_scope_mismatch', 'nested_primary_list_read', 'primary_read_phase',
    'primary_source_projection_capacity', 'primary_source_unavailable',
    'primary_commit_or_source_unavailable'))
_GATE_READ_REASONS = frozenset(('feature_disabled', 'cell_revoked', 'cell_owner_unavailable',
    'cell_binding_changed', 'automatic_configuration_unavailable', 'automatic_source_revoked',
    'session_not_found', 'cell_unavailable', 'cell_pcm_not_retained', 'response_closed'))
_GATE_SQLITE_CODES = {1:'SQLITE_ERROR', 5:'SQLITE_BUSY', 261:'SQLITE_BUSY_RECOVERY',
    517:'SQLITE_BUSY_SNAPSHOT', 773:'SQLITE_BUSY_TIMEOUT', 6:'SQLITE_LOCKED',
    262:'SQLITE_LOCKED_SHAREDCACHE', 518:'SQLITE_LOCKED_VTAB', 7:'SQLITE_NOMEM',
    8:'SQLITE_READONLY', 9:'SQLITE_INTERRUPT', 10:'SQLITE_IOERR', 11:'SQLITE_CORRUPT',
    13:'SQLITE_FULL', 14:'SQLITE_CANTOPEN', 17:'SQLITE_SCHEMA', 21:'SQLITE_MISUSE',
    26:'SQLITE_NOTADB'}


def _error_type(value):
    return value if type(value) is str and value in _ERROR_TYPES else 'other'


def failure_observation(phase, error):
    """Fixed scalars only; never exception messages or arbitrary attributes."""
    from .native_execution_permit import AdmissionError
    from .native_owned_cells import Unavailable
    from .native_owned_support_cache import ReadError
    from .fragment_model_guard import ResourceLimit
    gate_types = {Unavailable:'Unavailable', ReadError:'ReadError',
                  ResourceLimit:'ResourceLimit', sqlite3.OperationalError:'OperationalError'}
    value = {'phase':phase if phase in _FAILURE_PHASES else 'other',
             'type':gate_types.get(type(error), _error_type(type(error).__name__))}
    if type(error) is Unavailable:
        args = BaseException.__getattribute__(error, 'args')
        reason = args[0] if len(args) == 1 else None
        value['reason'] = reason if type(reason) is str and reason in _GATE_UNAVAILABLE_REASONS else 'other'
    if type(error) is ReadError:
        reason = error.reason
        value['reason'] = reason if type(reason) is str and reason in _GATE_READ_REASONS else 'other'
    if type(error) is sqlite3.OperationalError:
        code = getattr(error, 'sqlite_errorcode', None)
        name = getattr(error, 'sqlite_errorname', None)
        code = code if type(code) is int and code in _GATE_SQLITE_CODES else None
        value['sqlite_code'] = code
        value['sqlite_name'] = name if type(name) is str and name == _GATE_SQLITE_CODES.get(code) else 'other'
    if type(error) is AdmissionError:
        reason = error.reason
        value['reason'] = reason if type(reason) is str and reason in _ADMISSION_REASONS else 'other'
    if type(error) in (OSError, PermissionError, FileNotFoundError, ProcessLookupError, TimeoutError):
        number = error.errno
        if type(number) is int and 0 <= number <= 65535:value['errno'] = number
    gate = getattr(error, '_moss_start_gate', None)
    if gate in ('before_spawn', 'before_admission'):value['gate'] = gate
    observed = getattr(error, '_cell_owner_read', None)
    if type(observed) is tuple and len(observed) == 4:
        stage, state, reason, dispatched = observed
        if (stage in ('submit', 'wait', 'done') and type(dispatched) is bool
                and state in ('busy', 'pending', 'unavailable', 'unknown')):
            value['owner_read'] = {'stage':stage, 'state':state, 'dispatched':dispatched,
                'reason':reason if type(reason) is str and reason in (
                    'owner_lock_busy', 'one_total_handoff_slot', 'optional_sql_or_io_failure',
                    'cooperative_deadline', 'operation_work_capacity', 'cell_revoked',
                    'cell_binding_changed', 'source_changed_after_read', 'revoked_generation') else 'other'}
    return value


def terminal_outcome(status):
    """Sanitized evidence, sealed by the manager only after positive cleanup.

    Possible invocation is not an independent native-start acknowledgement.
    Counts without a validated return stay unknown, including nonzero child exit.
    """
    possible = status['invocation_possible']
    receipt = status.get('native_receipt') if status.get('receipt_validated') is True else None
    validated = type(receipt) is dict
    outcome = {'schema':'moss-no-result-v1', 'invocation_possible':possible,
        'receipt_validated':validated,
        'count_basis':'validated_receipt' if validated else 'unknown' if possible else 'not_invoked'}
    for name in ('construct_attempts', 'session_attempts', 'run_attempts'):
        outcome[name] = receipt[name] if validated else None if possible else 0
    failure = status.get('failure')
    if failure is not None:outcome['failure'] = dict(failure)
    if validated:
        for name in ('truncated', 'aborted', 'model_closed', 'session_closed'):
            outcome[name] = receipt[name]
        outcome['raw_unavailable_reason'] = receipt['raw_unavailable_reason']
        outcome['native_error_type'] = None if receipt['error_type'] is None else _error_type(receipt['error_type'])
        outcome['close_errors'] = [{'object':item.get('object') if item.get('object') in ('model','session') else 'other',
            'type':_error_type(item.get('type'))} if type(item) is dict else {'object':'other','type':'other'}
            for item in receipt['close_errors']]
    cleanup = status.get('cleanup') or {}
    for name in ('cleanup_verified', 'original_handle_waited_and_reaped'):
        outcome[name] = cleanup.get(name) is True
    code = cleanup.get('wait_returncode')
    outcome['wait_returncode'] = code if type(code) is int and -(2**31) <= code < 2**31 else None
    remaining = cleanup.get('remaining_live_owned')
    outcome['remaining_owned_count'] = len(remaining) if type(remaining) is list and len(remaining) <= 128 else None
    error = cleanup.get('cleanup_failure')
    outcome['cleanup_failure_type'] = _error_type(error.get('type')) if type(error) is dict else 'other' if error else None
    outcome['cleanup_scope'] = 'observed_owned_only'
    if len(encoded(outcome)) > 1024:raise ValueError('Terminal outcome capacity')
    return outcome


class OwnedDriver:
    """Original Popen owner; shared cleanup mechanics do not share Qwen jobs."""
    def __init__(self, directory, request_sha):
        from .fragment_model_guard import Identity
        from .qwen_candidate_runner import CandidateGuard
        import psutil
        if psutil.__version__ != '7.2.2':
            raise ValueError('The experimental owner requires psutil==7.2.2')
        self.directory, self.request_sha, self.proc = directory, request_sha, None
        self._service_gate = None
        self.identity = directory_identity(directory)
        self.limits = dict(own_cap=int(3.5 * 1024**3), aggregate_cap=int(3.5 * 1024**3),
            artifact_cap=SCRATCH_BYTES, host_reserve=2 * 1024**3, disk_reserve=int(5.5 * 1024**3))
        self.guard = CandidateGuard(directory, caller=Identity.bind(os.getpid()), deadline=300, **self.limits)

    def _check_start_gate(self, phase, cancelled):
        from .native_owned_cell_jobs import _OWNER_READ_NOT_DISPATCHED
        from .native_owned_support_cache import ReadError
        from .native_execution_permit import AdmissionError
        service = self._service_gate
        if service is None:return
        automatic = getattr(service, '_automatic_text', False) is True
        last_read = None
        try:
            while True:
                if automatic:
                    if cancelled.is_set() or service._automatic_cancelled:
                        raise Cancelled('Cancelled before invocation admission')
                    if time.monotonic() >= self.guard.deadline or service._automatic_wait_remaining <= 0:
                        raise AdmissionError('optional_prelaunch_deadline')
                    check_loader_context();check_directory(self.directory, self.identity)
                    self.guard.check()
                    if phase == 'before_admission' and self.proc.poll() is not None:
                        raise RuntimeError('Owned waiting child exited')
                try:
                    service.check(phase)
                    break
                except ReadError as error:
                    if (not automatic or type(error) is not ReadError or
                            getattr(error, '_owner_read_not_dispatched', None) is not _OWNER_READ_NOT_DISPATCHED):
                        raise
                    last_read = error._cell_owner_read
                # No owner operation/transaction/permit lock is held here. Each
                # full validation keeps its own unchanged read budget; all waits
                # share the original driver and accepted-job deadlines.
                remaining = min(self.guard.deadline - time.monotonic(), service._automatic_wait_remaining)
                if remaining <= 0:raise AdmissionError('optional_prelaunch_deadline')
                if cancelled.wait(min(.1, remaining)):
                    raise Cancelled('Cancelled before invocation admission')
            if automatic:
                if cancelled.is_set() or service._automatic_cancelled:
                    raise Cancelled('Cancelled before invocation admission')
                if time.monotonic() >= self.guard.deadline or service._automatic_wait_remaining <= 0:
                    raise AdmissionError('optional_prelaunch_deadline')
                self.guard.check()
                if phase == 'before_admission' and self.proc.poll() is not None:
                    raise RuntimeError('Owned waiting child exited')
                if cancelled.is_set() or service._automatic_cancelled:
                    raise Cancelled('Cancelled before invocation admission')
                if time.monotonic() >= self.guard.deadline or service._automatic_wait_remaining <= 0:
                    raise AdmissionError('optional_prelaunch_deadline')
        except Exception as error:
            # Fixed diagnostic scalars cannot replace the original failure.
            try:
                error._moss_start_gate = phase
                if last_read is not None and not hasattr(error, '_cell_owner_read'):
                    error._cell_owner_read = last_read
            except Exception:pass
            raise

    def start(self, cancelled):
        from .fragment_model_guard import Identity
        self._check_start_gate('before_spawn', cancelled)
        check_loader_context()
        check_directory(self.directory, self.identity)
        self.guard.check()
        if cancelled.is_set():
            raise Cancelled('Cancelled before child creation')
        command = [sys.executable, '-I', '-B', '-S', str(ROOT / 'audio_backend/moss_handy_runner.py'),
                   str(self.directory), self.request_sha]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
        self.proc = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        identity = Identity.bind(self.proc.pid)
        self.guard.bind_worker(identity)
        observed = self.guard.check()['processes']
        if not any(row['pid'] == identity.pid and row['created'] == identity.created for row in observed):
            raise RuntimeError('Owned child visibility unconfirmed')
        if cancelled.is_set():
            raise Cancelled('Cancelled before invocation admission')
        self._check_start_gate('before_admission', cancelled)
        self.proc.stdin.write(b'1')
        self.proc.stdin.close()

    def wait(self, cancelled):
        while self.proc.poll() is None:
            if self._service_gate is not None:self._service_gate.check('running')
            if cancelled.is_set():
                raise Cancelled('Publication cancelled')
            self.guard.check()
            check_directory(self.directory, self.identity)
            time.sleep(.05)
        if self.proc.returncode != 0:
            raise RuntimeError('Owned child failed')
        result = json.loads(read_owned(self.directory, 'result.json', identity=self.identity))
        raw = read_owned(self.directory, 'raw.bin', identity=self.identity) if result.get('raw_retained') is True else None
        return result, raw

    def cleanup(self, stop):
        from .qwen_candidate_runner import CandidateGuard, process_cleanup
        service = self._service_gate
        class CleanupGuard(CandidateGuard):
            def check(inner, *args, **kwargs):
                if service is not None:
                    # Aggregate evidence cannot abort positive owned cleanup.
                    try:service.cleanup_sample()
                    except Exception:pass
                return super().check(*args, **kwargs)
        cleanup = CleanupGuard(self.directory, caller=self.guard.caller, deadline=60, **self.limits)
        cleanup.own_roots = self.guard.own_roots
        cleanup.known_own.update(self.guard.known_own)
        receipt = {}
        if self.proc is not None and self.proc.stdin is not None and not self.proc.stdin.closed:
            self.proc.stdin.close()
        process_cleanup(self.proc, cleanup, receipt, stop)
        # Do not turn remembered PIDs into an assertion of exhaustive discovery.
        receipt['discovery_complete'] = False
        receipt['containment'] = 'observed-owned-identities-only'
        self.guard.known_own.update(cleanup.known_own)
        return receipt


class MossController:
    """Explicit local developer entry. A new instance never adopts an old claim.

    _driver_factory is an injected model-free fixture seam, not a public route
    or an alternate command mechanism. Production callers use OwnedDriver.
    """
    def __init__(self, directory, *, profile_name, model_path, _driver_factory=OwnedDriver,
                 _service_gate=None, _service_context=None):
        self.directory = Path(directory)
        if (not self.directory.is_absolute() or self.directory.resolve() != self.directory
                or any(p.is_symlink() for p in (self.directory, *self.directory.parents))):
            raise ValueError('Private absolute no-symlink directory required')
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Caller must supply a private mode-0700 directory')
        if any(self.directory.iterdir()):
            raise Busy('Existing scratch or abandoned owner claim requires explicit investigation')
        self._directory_identity = directory_identity(self.directory)
        self.profile = profile(profile_name)
        if type(model_path) is not str or len(model_path.encode('utf-8')) > 4096 or not Path(model_path).is_absolute():
            raise ValueError('Explicit bounded absolute model path required')
        self.model_path, self._factory = model_path, _driver_factory
        self._lock, self._cancel = threading.RLock(), threading.Event()
        self._job = self._driver = self._claim_identity = None
        self._service_gate = _service_gate
        if _service_context is not None:
            if (type(_service_context) is not dict or set(_service_context) != {'jobs_epoch', 'capture_epoch', 'source_binding'}
                    or any(type(_service_context[k]) is not str or len(_service_context[k]) != width
                           or any(c not in '0123456789abcdef' for c in _service_context[k])
                           for k, width in (('jobs_epoch', 32), ('capture_epoch', 32), ('source_binding', 64)))):
                raise ValueError('Invalid service claim context')
        self._service_context = dict(_service_context) if _service_context is not None else None

    def submit(self, support, pcm16le):
        with self._lock:
            check_directory(self.directory, self._directory_identity)
            if self._job is not None:
                raise Busy('Single slot occupied; explicitly discard a cleaned terminal result first')
            if type(support) is not SourceSupport or type(pcm16le) is not bytes:
                raise ValueError('Immutable validated support and PCM16 bytes required')
            if len(pcm16le) > 640000 or not 0 < support.end_frame - support.start_frame <= 320000:
                raise ValueError('Support exceeds the one-job frame/byte cap')
            if len(pcm16le) != 2 * (support.end_frame - support.start_frame):
                raise ValueError('Support PCM16 frame count differs')
            if (hashlib.sha256(pcm16le).hexdigest() != support.pcm16le_sha256 or
                    hashlib.sha256(float_bytes(samples_from_pcm(pcm16le))).hexdigest() != support.float32le_sha256):
                raise ValueError('Support PCM16/float32 hashes differ')
            if profile(self.profile.name) != self.profile:
                raise ValueError('Frozen profile changed')
            job_id = uuid.uuid4().hex
            claim = canonical({'schema': 'moss-owned-claim-v1', 'owner': uuid.uuid4().hex,
                'job_id': job_id, 'support_id': support.support_id, 'profile_sha256': self.profile.sha256,
                'source_generation': support.source_generation, 'parent_identity': process_identity(os.getpid()),
                'directory_identity': self._directory_identity,
                **({'service_context': self._service_context} if self._service_context is not None else {})})
            write_owned(self.directory, 'owner.json', claim, identity=self._directory_identity)
            info = (self.directory / 'owner.json').lstat()
            self._claim_identity = (info.st_dev, info.st_ino, claim)
            self._cancel.clear()
            self._job = {'job_id': job_id, 'support': support, 'pcm': pcm16le, 'state': 'prepared',
                'invocation_possible': False, 'publication_valid': True, 'cleanup': None,
                'raw': None, 'result': None, 'error_type': None, 'receipt_validated': False, 'failure': None}
            return job_id

    def _claim_matches(self):
        check_directory(self.directory, self._directory_identity)
        device, inode, raw = self._claim_identity
        info = (self.directory / 'owner.json').lstat()
        if (info.st_dev, info.st_ino) != (device, inode) or read_owned(self.directory, 'owner.json', identity=self._directory_identity) != raw:
            raise RuntimeError('Owned claim identity differs')

    def _cleanup(self, stop):
        if self._driver is None:
            receipt = {'cleanup_verified': True, 'original_handle_waited_and_reaped': True,
                'remaining_live_owned': [], 'cleanup_scope': 'no-child-was-created', 'discovery_complete': True}
        else:
            receipt = self._driver.cleanup(stop)
        if type(receipt) is not dict or len(encoded(receipt)) > 16384:
            raise RuntimeError('Invalid cleanup receipt')
        self._job['cleanup'] = receipt
        confirmed = (receipt.get('cleanup_verified') is True and
            receipt.get('original_handle_waited_and_reaped') is True and
            receipt.get('remaining_live_owned') == [] and not receipt.get('cleanup_failure'))
        if confirmed:
            self._claim_matches()
            (self.directory / 'owner.json').unlink()
            self._claim_identity = None
        return confirmed

    def _check_before_spawn(self):
        from .native_owned_cell_capture import _CLOSED_AUTOMATIC_READ_BUSY
        from .native_owned_cell_jobs import _OWNER_READ_NOT_DISPATCHED
        from .native_owned_support_cache import ReadError
        from .native_execution_permit import AdmissionError
        # Before input stays single-shot. This outer gate shares the already
        # constructed driver and accepted job clocks; no child exists yet.
        # Each complete validation retains its independent ordinary read Budget.
        service = self._service_gate
        automatic = getattr(service, '_automatic_text', False) is True
        deferred, last_owner_read = False, None

        def owner_veto():
            if self._cancel.is_set() or service._automatic_cancelled:
                raise Cancelled('Cancelled before child creation')
            if (time.monotonic() >= self._driver.guard.deadline
                    or service._automatic_wait_remaining <= 0):
                raise AdmissionError('optional_prelaunch_deadline')

        try:
            while True:
                if self._cancel.is_set():raise Cancelled('Cancelled before child creation')
                if last_owner_read is not None:owner_veto()
                try:
                    service.check('before_spawn')
                    if deferred:
                        if time.monotonic() >= self._driver.guard.deadline:
                            raise AdmissionError('optional_prelaunch_deadline')
                        self._driver.guard.check()
                    if last_owner_read is not None:owner_veto()
                    return
                except sqlite3.OperationalError as error:
                    if (type(error) is not sqlite3.OperationalError or
                            getattr(error, '_closed_automatic_read_busy', None) is not _CLOSED_AUTOMATIC_READ_BUSY):
                        raise
                except ReadError as error:
                    if (not automatic or type(error) is not ReadError or
                            getattr(error, '_owner_read_not_dispatched', None) is not _OWNER_READ_NOT_DISPATCHED):
                        raise
                    last_owner_read = error._cell_owner_read
                deferred = True
                guard = self._driver.guard
                remaining = guard.deadline - time.monotonic()
                if last_owner_read is not None:
                    owner_veto()
                    remaining = min(remaining, service._automatic_wait_remaining)
                if remaining <= 0:raise AdmissionError('optional_prelaunch_deadline')
                guard.check()
                if last_owner_read is not None:
                    owner_veto()
                    remaining = min(guard.deadline - time.monotonic(), service._automatic_wait_remaining)
                if self._cancel.wait(min(.1, remaining)):
                    raise Cancelled('Cancelled before child creation')
                if time.monotonic() >= guard.deadline:
                    raise AdmissionError('optional_prelaunch_deadline')
                guard.check()
                if last_owner_read is not None:owner_veto()
                self._claim_matches()
                if profile(self.profile.name) != self.profile:raise ValueError('Frozen profile changed')
        except Exception as error:
            # Preserve only the last no-operation observation, not a new cause.
            if last_owner_read is not None and not hasattr(error, '_cell_owner_read'):
                try:error._cell_owner_read = last_owner_read
                except Exception:pass
            raise

    def run(self, job_id):
        with self._lock:
            job = self._require(job_id)
            if job['state'] != 'prepared':
                raise Busy('A started or terminal job is never replayed')
            job['state'] = 'running'
        phase = 'before_input'
        try:
            if self._service_gate is not None:self._service_gate.check('before_input')
            if self._cancel.is_set():
                raise Cancelled('Publication cancelled before start')
            self._claim_matches()
            if profile(self.profile.name) != self.profile:
                raise ValueError('Frozen profile changed')
            phase = 'input'
            request = canonical({'job_id': job_id, 'support': job['support'].to_dict(),
                'profile_name': self.profile.name, 'profile_sha256': self.profile.sha256,
                'model_path': self.model_path})
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as wav:
                wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                wav.writeframes(job['pcm'])
            write_owned(self.directory, 'support.wav', buffer.getvalue(), identity=self._directory_identity)
            write_owned(self.directory, 'request.json', request, identity=self._directory_identity)
            job['pcm'] = None
            if self._service_gate is not None:self._service_gate.inputs_written()
            phase = 'driver'
            self._driver = self._factory(self.directory, hashlib.sha256(request).hexdigest())
            phase = 'before_spawn'
            if self._service_gate is not None:
                self._driver._service_gate = self._service_gate
                self._check_before_spawn()
            # Conservative: start can send admission and then fail. No retry.
            job['invocation_possible'] = True
            phase = 'start'
            self._driver.start(self._cancel)
            phase = 'wait'
            receipt, raw = self._driver.wait(self._cancel)
            phase = 'validate_return'
            self._validate_return(receipt, raw)
            with self._lock:
                # Raw retention precedes projection, even if later parsing fails.
                job['raw'] = raw
                # Freeze validated evidence before cancellation or projection can discard output.
                job['native_receipt'] = json.loads(encoded(receipt))
                job['receipt_validated'] = True
                phase = 'native_return'
                if self._cancel.is_set() or not job['publication_valid']:
                    raise Cancelled('Late result invalidated by cancellation')
                if receipt['error_type'] or receipt['close_errors']:
                    raise RuntimeError('Native call or close failed; exact raw remains diagnostic')
                if raw is not None:
                    phase = 'projection'
                    job['result'] = project(raw, support=job['support'], profile_name=self.profile.name,
                        outcome='failed' if receipt['aborted'] or receipt['error_type'] or receipt['close_errors'] else 'returned',
                        truncated=receipt['truncated'], aborted=receipt['aborted'])
        except Exception as exc:
            with self._lock:
                job['error_type'] = type(exc).__name__
                job['failure'] = failure_observation(phase, exc)
                job['publication_valid'] = False
                job['result'] = None
        finally:
            job['pcm'] = None
            if self._service_gate is not None:
                try:self._service_gate.cleaning()
                except Exception:pass
            try:
                confirmed = self._cleanup(bool(job['error_type']) or self._cancel.is_set())
            except Exception as exc:
                confirmed = False
                job['cleanup'] = {'cleanup_verified': False, 'cleanup_failure': {'type': type(exc).__name__}}
            with self._lock:
                if not confirmed:
                    job['publication_valid'] = False
                    job['result'] = None
                    job['state'] = 'cleanup_blocked'
                elif self._cancel.is_set():
                    job['state'] = 'cancelled'
                elif job['error_type']:
                    job['state'] = 'failed'
                else:
                    job['state'] = 'returned_diagnostic'
        return self.status(job_id)

    def _validate_return(self, receipt, raw):
        job = self._job
        if type(receipt) is not dict or len(encoded(receipt)) > 8192:
            raise ValueError('Invalid bounded native receipt')
        required = {'construct_attempts', 'session_attempts', 'run_attempts', 'raw_retained',
            'raw_unavailable_reason', 'truncated', 'aborted', 'eos', 'generated_token_count',
            'model_closed', 'session_closed', 'error_type', 'close_errors',
            'job_id', 'support_id', 'profile_sha256', 'raw_kind'}
        if set(receipt) != required | ({'raw_bytes', 'raw_sha256'} if receipt.get('raw_retained') is True else set()):
            raise ValueError('Native receipt fields differ')
        if (receipt['job_id'] != job['job_id'] or receipt['support_id'] != job['support'].support_id
                or receipt['profile_sha256'] != self.profile.sha256 or receipt['eos'] is not None
                or receipt['generated_token_count'] is not None or receipt['raw_kind'] != 'exact-native-raw-text-utf8'):
            raise ValueError('Native result binding or unknown EOS differs')
        for key in ('construct_attempts', 'session_attempts', 'run_attempts'):
            if type(receipt[key]) is not int or receipt[key] not in (0, 1):
                raise ValueError('Native attempt count differs')
        for key in ('raw_retained', 'truncated', 'aborted', 'model_closed', 'session_closed'):
            if type(receipt[key]) is not bool:
                raise ValueError('Native receipt boolean differs')
        if not (receipt['run_attempts'] <= receipt['session_attempts'] <= receipt['construct_attempts']):
            raise ValueError('Native attempt order differs')
        if (receipt['error_type'] is not None and (type(receipt['error_type']) is not str or len(receipt['error_type']) > 128)
                or type(receipt['close_errors']) is not list or len(receipt['close_errors']) > 2):
            raise ValueError('Native failure fields differ')
        if receipt['raw_retained'] and receipt['run_attempts'] != 1:
            raise ValueError('Raw without invocation is invalid')
        if receipt['raw_retained'] and receipt['raw_unavailable_reason'] is not None:
            raise ValueError('Exact raw cannot also be unavailable')
        if not receipt['raw_retained'] and receipt['raw_unavailable_reason'] not in (
                'no_native_result', 'raw_text_unavailable', 'raw_text_oversize', 'raw_text_invalid_unicode'):
            raise ValueError('Raw unavailability reason differs')
        if (receipt['error_type'] is None and not receipt['close_errors'] and
                (receipt['construct_attempts'] != 1 or receipt['session_attempts'] != 1 or
                 receipt['run_attempts'] != 1 or not receipt['model_closed'] or not receipt['session_closed'])):
            raise ValueError('Unqualified successful native return')
        if receipt['raw_retained']:
            if (type(raw) is not bytes or len(raw) > 131072 or type(receipt['raw_bytes']) is not int
                    or len(raw) != receipt['raw_bytes'] or hashlib.sha256(raw).hexdigest() != receipt['raw_sha256']):
                raise ValueError('Exact raw binding differs')
        elif raw is not None:
            raise ValueError('Unexpected raw output')

    def _require(self, job_id):
        if self._job is None or type(job_id) is not str or self._job['job_id'] != job_id:
            raise KeyError('Unknown volatile MOSS job')
        return self._job

    def cancel(self, job_id):
        with self._lock:
            job = self._require(job_id)
            if job['state'] in ('returned_diagnostic', 'failed', 'cancelled'):
                return False
            job['publication_valid'] = False  # Invalidate before any termination.
            job['result'] = None
            self._cancel.set()
            if job['state'] == 'prepared':
                job['failure'] = failure_observation('prepared', Cancelled())
                try:
                    confirmed = self._cleanup(False)
                except Exception as exc:
                    confirmed = False
                    job['cleanup'] = {'cleanup_verified': False, 'cleanup_failure': {'type': type(exc).__name__}}
                job['pcm'] = None
                job['state'] = 'cancelled' if confirmed else 'cleanup_blocked'
            return True

    def retry_cleanup(self, job_id):
        with self._lock:
            job = self._require(job_id)
            if job['state'] != 'cleanup_blocked':
                raise Busy('Only blocked cleanup can be retried')
            if self._cleanup(True):
                job['state'] = 'cancelled' if self._cancel.is_set() else 'failed'
            return self.status(job_id)

    def status(self, job_id):
        with self._lock:
            job = self._require(job_id)
            status = {key: job.get(key) for key in (
                'job_id', 'state', 'invocation_possible', 'publication_valid', 'cleanup', 'error_type',
                'native_receipt', 'receipt_validated', 'failure', 'result')}
            if job['state'] != 'returned_diagnostic' or not job['publication_valid']:
                status['result'] = None
            data = encoded(status)
            if len(data) > 262144:
                raise RuntimeError('Terminal projection budget exceeded')
            return json.loads(data)

    def exact_raw(self, job_id):
        with self._lock:
            return self._require(job_id)['raw']

    def discard(self, job_id):
        with self._lock:
            job = self._require(job_id)
            if job['state'] not in ('returned_diagnostic', 'failed', 'cancelled') or self._claim_identity is not None:
                raise Busy('Live or uncertain ownership cannot be discarded')
            check_directory(self.directory, self._directory_identity)
            for path in self.directory.iterdir():
                if path.name == 'owner.json':
                    raise Busy('Unexpected live claim cannot be discarded')
                path.unlink()
            self._job = self._driver = None
