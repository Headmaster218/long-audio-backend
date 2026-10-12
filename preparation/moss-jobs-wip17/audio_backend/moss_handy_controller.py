"""Volatile one-job MOSS owner with private optional service gates.

No backlog, replay, retry after a possible invocation, or automatic abandoned
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

    def start(self, cancelled):
        from .fragment_model_guard import Identity
        if self._service_gate is not None:self._service_gate.check('before_spawn')
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
        if self._service_gate is not None:self._service_gate.check('before_admission')
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
                'raw': None, 'result': None, 'error_type': None}
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

    def run(self, job_id):
        with self._lock:
            job = self._require(job_id)
            if job['state'] != 'prepared':
                raise Busy('A started or terminal job is never replayed')
            job['state'] = 'running'
        try:
            if self._service_gate is not None:self._service_gate.check('before_input')
            if self._cancel.is_set():
                raise Cancelled('Publication cancelled before start')
            self._claim_matches()
            if profile(self.profile.name) != self.profile:
                raise ValueError('Frozen profile changed')
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
            self._driver = self._factory(self.directory, hashlib.sha256(request).hexdigest())
            if self._service_gate is not None:
                self._driver._service_gate = self._service_gate
                self._service_gate.check('before_spawn')
            # Conservative: start can send admission and then fail. No retry.
            job['invocation_possible'] = True
            self._driver.start(self._cancel)
            receipt, raw = self._driver.wait(self._cancel)
            self._validate_return(receipt, raw)
            with self._lock:
                # Raw retention precedes projection, even if later parsing fails.
                job['raw'] = raw
                if self._cancel.is_set() or not job['publication_valid']:
                    raise Cancelled('Late result invalidated by cancellation')
                job['native_receipt'] = receipt
                if receipt['error_type'] or receipt['close_errors']:
                    raise RuntimeError('Native call or close failed; exact raw remains diagnostic')
                if raw is not None:
                    job['result'] = project(raw, support=job['support'], profile_name=self.profile.name,
                        outcome='failed' if receipt['aborted'] or receipt['error_type'] or receipt['close_errors'] else 'returned',
                        truncated=receipt['truncated'], aborted=receipt['aborted'])
        except Exception as exc:
            with self._lock:
                job['error_type'] = type(exc).__name__
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
                'native_receipt', 'result')}
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
