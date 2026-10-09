"""Opt-in, local-only Qwen window runner. Importing never imports a model.

The supervisor owns the original child handle. Every audio call waits for a
durable reservation and invocation ACK; an interrupted call is never replayed.
"""
import array
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import time

from .fragment_core import SourcePlan
from .fragment_runtime import LogicalPCMReader
from .fragment_runtime_contracts import canonical, digest
from .fragment_model_guard import Identity, ResourceGuard
from .qwen_candidate_store import CandidateStore, COMPLETE

ROOT = Path(__file__).resolve().parents[1]
MAX_JSON = 2 * 1024**2
LIMITS = frozenset({'max_calls', 'max_frames', 'tree_rss_bytes', 'host_reserve_bytes',
                    'disk_reserve_bytes', 'artifact_bytes', 'timeout_seconds'})
PINNED_MODEL_SHA256 = '062a7bcb18675c2fe5ffd0e8b354eac6a1ceada9b2af9ef7168746ae16b54358'
PINNED_NATIVE_COMMIT = '63a44d9239d610b3908e8a66b384924cd4a77217'


class ChildFailure(RuntimeError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__('Candidate child failed: ' + str(evidence.get('type', 'unknown')))


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError('Oversized Qwen manifest')
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def file_receipt(path):
    path = Path(path).absolute()
    before = path.stat()
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('File changed while hashing')
    return {'path': str(path), 'resolved_path': str(path.resolve()), 'bytes': after.st_size, 'sha256': sha}


def verify_file(entry):
    if (not isinstance(entry, dict) or set(entry) != {'path', 'resolved_path', 'bytes', 'sha256'}
        or type(entry['bytes']) is not int or entry['bytes'] < 0
        or not Path(entry['path']).is_absolute()):
        raise ValueError('Exact local file receipt required')
    if file_receipt(entry['path']) != entry:
        raise ValueError('Frozen source/model/runtime hash changed')


def verify_runtime(runtime):
    fields = {'schema', 'model', 'python', 'native_library', 'files', 'package_version',
              'native_commit', 'model_id', 'model_revision', 'license'}
    if not isinstance(runtime, dict) or set(runtime) != fields or runtime['schema'] != 'qwen-local-runtime-v1':
        raise ValueError('Explicit local Qwen runtime inventory required')
    if not isinstance(runtime['files'], list) or not 1 <= len(runtime['files']) <= 512:
        raise ValueError('Bounded complete runtime file inventory required')
    for name in ('package_version', 'native_commit', 'model_id', 'model_revision', 'license'):
        if not isinstance(runtime[name], str) or not 0 < len(runtime[name]) <= 512:
            raise ValueError('Missing bounded runtime provenance')
    entries = [runtime[name] for name in ('model', 'python', 'native_library')] + runtime['files']
    for entry in entries:
        verify_file(entry)
    paths = [e['path'] for e in runtime['files']]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate runtime file inventory')


def runtime_stats(runtime):
    result = {}
    for entry in [runtime[k] for k in ('model', 'python', 'native_library')] + runtime['files']:
        path = Path(entry['path'])
        stat = path.stat()
        result[str(path)] = (str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size,
                            stat.st_mtime_ns, stat.st_ctime_ns)
    return result


def validate_limits(limits):
    if not isinstance(limits, dict) or set(limits) != LIMITS or any(type(v) is not int or v < 1 for v in limits.values()):
        raise ValueError('Every finite candidate resource/call limit is required')
    if limits['disk_reserve_bytes'] < 5 * 1024**3 or limits['host_reserve_bytes'] < 2 * 1024**3:
        raise ValueError('Disk/host safety floors cannot be lowered')
    if limits['max_calls'] > 1024 or limits['max_frames'] > 1024 * 320000:
        raise ValueError('Offline run inventory exceeds v1 bound')
    return limits


def freeze(plan, paths, runtime, limits, *, source_scope, source_generation=0):
    from .qwen_candidates import prepare_candidate_requests, DEFAULT_CONFIG
    verify_runtime(runtime)
    validate_limits(limits)
    paths = {key: str(Path(path).absolute()) for key, path in paths.items()}
    if set(paths) != {s.fragment_id for s in plan.sources}:
        raise ValueError('Exact source binding inventory required')
    requests = prepare_candidate_requests(plan, paths, source_scope=source_scope,
        source_generation=source_generation, runtime_manifest_sha256=digest(runtime),
        model_sha256=runtime['model']['sha256'], config=DEFAULT_CONFIG)
    # Metadata, not source copies. Pin the package closure used by the runner.
    implementation = [file_receipt(p) for p in sorted((ROOT / 'audio_backend').rglob('*.py'))]
    implementation.append(file_receipt(ROOT / 'scripts/benchmarks/run_qwen_candidates.py'))
    value = {'schema': 'qwen-frozen-candidates-v1', 'source_plan': plan.to_json(),
             'source_bindings': paths, 'runtime': runtime, 'limits': limits,
             'requests': list(requests), 'implementation': implementation}
    if len(canonical(value).encode()) > MAX_JSON:
        raise ValueError('Frozen run exceeds metadata bound')
    return value


def verify_frozen(frozen, expected_sha256):
    from .qwen_candidates import prepare_candidate_requests
    if (not isinstance(frozen, dict) or set(frozen) != {'schema', 'source_plan', 'source_bindings',
        'runtime', 'limits', 'requests', 'implementation'} or frozen['schema'] != 'qwen-frozen-candidates-v1'
        or digest(frozen) != expected_sha256):
        raise ValueError('Frozen candidate contract/hash mismatch')
    validate_limits(frozen['limits'])
    verify_runtime(frozen['runtime'])
    expected_paths = {str(p.absolute()) for p in (ROOT / 'audio_backend').rglob('*.py')}
    expected_paths.add(str(ROOT / 'scripts/benchmarks/run_qwen_candidates.py'))
    if (not isinstance(frozen['implementation'], list) or
        {e.get('path') for e in frozen['implementation'] if isinstance(e, dict)} != expected_paths
        or len(frozen['implementation']) != len(expected_paths)):
        raise ValueError('Frozen implementation inventory is incomplete')
    for entry in frozen['implementation']:
        verify_file(entry)
    if not frozen['requests']:
        raise ValueError('Empty candidate inventory')
    first = frozen['requests'][0]
    plan = SourcePlan.from_json(frozen['source_plan'])
    expected = prepare_candidate_requests(plan, frozen['source_bindings'],
        source_scope=first['source_scope'], source_generation=first['source_generation'],
        runtime_manifest_sha256=digest(frozen['runtime']), model_sha256=frozen['runtime']['model']['sha256'],
        config=first['config'])
    if canonical(expected) != canonical(frozen['requests']):
        raise ValueError('Source-window/input receipt differs from frozen inventory')
    return plan


def atomic_json(path, value):
    path = Path(path)
    encoded = canonical(value).encode()
    if len(encoded) > MAX_JSON:
        raise ValueError('Bounded JSON exceeded')
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError('Refusing to overwrite an existing different manifest')
        return
    temporary = path.with_name(path.name + '.pending')
    with temporary.open('xb') as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    try:
        os.link(temporary, path)  # no clobber under a concurrent writer
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink()


@contextmanager
def runner_lease(root):
    path = Path(root) / '.qwen-runner-lease'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def wire(sock, value=None, *, check=lambda: None):
    def transfer(data=None, count=0):
        result = bytearray()
        while data or len(result) < count:
            check()
            ready = select.select([] if data else [sock], [sock] if data else [], [], .05)
            if not ready[0] and not ready[1]:
                continue
            if data:
                sent = sock.send(data)
                if sent == 0:
                    raise EOFError('Candidate wire closed during send')
                data = data[sent:]
                if not data:
                    return
            else:
                block = sock.recv(min(65536, count - len(result)))
                if not block:
                    raise EOFError('Candidate child exited without a receipt')
                result.extend(block)
        return bytes(result)
    if value is not None:
        encoded = canonical(value).encode()
        if not 0 < len(encoded) <= MAX_JSON:
            raise ValueError('Candidate wire envelope overflow')
        transfer(struct.pack('!I', len(encoded)) + encoded)
        return
    size = struct.unpack('!I', transfer(count=4))[0]
    if not 0 < size <= MAX_JSON:
        raise ValueError('Candidate wire length overflow')
    return json.loads(transfer(count=size))


def receive(sock, check):
    message = wire(sock, check=check)
    if isinstance(message, dict) and message.get('kind') == 'failure':
        raise ChildFailure(message['failure'])
    return message


def decode_window(model, api, samples):
    """The sole native audio-call adapter. Tests inject model/API doubles."""
    from .qwen_candidates import normalize_native_result
    raw = dict(native_status=None, native_status_name=None, result_available=False,
               text=None, raw_text=None, language=None, was_truncated=None,
               truncation_available=False, exception_type=None, exception_status=None,
               partial_result_available=False, native_token_count=None,
               native_timestamp_kind=None, execution_returned=False)
    with model.session(n_threads=2, kv_type='f16', n_ctx=2048) as session:
        if session.limits.effective_n_ctx != 2048 or session.limits.effective_max_audio_ms < 20000:
            raise ValueError('Effective Qwen session limits differ from frozen profile')
        if not hasattr(api._lib, 'transcribe_was_truncated') or not hasattr(session, '_h'):
            raise ValueError('Pinned native truncation proof is unavailable before invocation')
        result = None
        try:
            result = session.run(samples, task='transcribe', language=None, timestamps='none')
            raw.update(native_status=0, native_status_name='OK')
        except Exception as exc:
            status = getattr(exc, 'status', None)
            if type(status) is not int:
                status = None
            raw.update(native_status=status, native_status_name=None,
                       exception_type=type(exc).__name__, exception_status=status)
            result = getattr(exc, 'partial_result', None)
            raw['partial_result_available'] = result is not None
        raw['execution_returned'] = True
        if result is not None:
            raw.update(result_available=True, text=result.text, raw_text=result.raw_text,
                       language=result.language, native_token_count=len(result.tokens),
                       native_timestamp_kind=str(result.timestamp_kind))
        try:
            raw['was_truncated'] = bool(api._lib.transcribe_was_truncated(session._h))
            raw['truncation_available'] = True
        except Exception:
            # Retain the native status/text, but never call unknown truncation
            # complete. Normalizer returns an explicit unverified schema error.
            raw['was_truncated'] = None
            raw['truncation_available'] = False
    return normalize_native_result(raw)


def _construct(runtime):
    # No import occurs until a real, explicitly admitted run enters its child.
    if any(os.environ.get(k) for k in ('LD_PRELOAD', 'LD_LIBRARY_PATH')):
        raise ValueError('Native loader overrides are not admitted')
    verify_runtime(runtime)
    if (runtime['model']['sha256'] != PINNED_MODEL_SHA256 or runtime['model']['bytes'] != 645356192
        or runtime['package_version'] != '0.2.3' or runtime['native_commit'] != PINNED_NATIVE_COMMIT):
        raise ValueError('Only the reviewed local Qwen Q5/runtime profile is admitted')
    if str(Path(sys.executable).resolve()) != runtime['python']['resolved_path']:
        raise ValueError('Actual Python interpreter differs from frozen runtime')
    import transcribe_cpp as api
    allowed = {e['resolved_path']: e for e in runtime['files'] + [runtime['native_library'], runtime['python']]}
    if (api.__version__ != runtime['package_version'] or
        not runtime['native_commit'].startswith(api.native_commit()) or
        str(Path(api.library_path()).resolve()) != runtime['native_library']['resolved_path']):
        raise ValueError('Actual Qwen runtime differs from frozen provenance')
    for name, module in list(sys.modules.items()):
        if name.startswith('transcribe_cpp') and getattr(module, '__file__', None):
            path = str(Path(module.__file__).resolve())
            if path not in allowed:
                raise ValueError('Actual imported Qwen wrapper is unfrozen')
            verify_file(allowed[path])
    native_root = Path(runtime['native_library']['resolved_path']).parent
    # Freeze all bundled dynamic companions, not just the top-level library.
    for path in native_root.rglob('*'):
        if path.is_file() and ('.so' in path.name or path.suffix in {'.dll', '.dylib'}):
            if str(path.resolve()) not in allowed:
                raise ValueError('Unfrozen native runtime companion')
            verify_file(allowed[str(path.resolve())])
    model = api.Model(runtime['model']['path'], backend='cpu')
    try:
        if (model.backend.casefold() != 'cpu' or model.device.kind != 'cpu' or model.device.device_type != 'cpu'
            or model.arch != 'qwen3_asr' or model.variant != 'qwen3-asr-0.6b'):
            raise ValueError('Qwen actual backend/device is not CPU')
        device = {k: v for k, v in asdict(model.device).items() if k != '_handle'}
        receipt = {'package_version': api.__version__, 'native_version': api.native_version(),
                   'native_commit': api.native_commit(), 'actual_backend': model.backend,
                   'device': device, 'library_sha256': runtime['native_library']['sha256'],
                   'model_sha256': runtime['model']['sha256'], 'runtime_manifest_sha256': digest(runtime)}
        return model, api, receipt
    except BaseException:
        model.close()
        raise


def child_main(fd, frozen_path, expected):
    from .qwen_candidates import validate_candidate_input
    control = socket.socket(fileno=fd)
    model = None
    try:
        frozen = read_json(frozen_path)
        plan = verify_frozen(frozen, expected)
        reader = LogicalPCMReader(plan, frozen['source_bindings'])
        model, api, runtime = _construct(frozen['runtime'])
        identities = runtime_stats(frozen['runtime'])
        wire(control, {'kind': 'ready', 'pid': os.getpid(), 'runtime': runtime})
        indexed = {r['request_id']: r for r in frozen['requests']}
        while True:
            message = wire(control)
            if message == {'kind': 'close'}:
                break
            rid = message.get('request_id')
            if message.get('kind') != 'prepare' or rid not in indexed:
                raise ValueError('Unknown candidate invocation')
            request = indexed[rid]
            if runtime_stats(frozen['runtime']) != identities:
                raise ValueError('Frozen runtime changed before native invocation')
            pcm = reader.read(request['read']['start_frame'], request['read']['end_frame'])
            data = validate_candidate_input(request, pcm)
            samples = array.array('f'); samples.frombytes(data)
            if sys.byteorder != 'little':
                samples.byteswap()
            wire(control, {'kind': 'prepared', 'request_id': rid})
            if wire(control) != {'kind': 'invoke', 'request_id': rid}:
                raise ValueError('Native invocation lacks exact durable ACK')
            native = decode_window(model, api, samples)
            validation = None
            try:
                reader.verify()
            except Exception as exc:
                validation = {'status': 'source_changed', 'type': type(exc).__name__}
            try:
                if runtime_stats(frozen['runtime']) != identities:
                    validation = {'status': 'provenance_mismatch', 'type': 'RuntimeChanged'}
            except Exception as exc:
                validation = {'status': 'provenance_mismatch', 'type': type(exc).__name__}
            wire(control, {'kind': 'returned', 'request_id': rid, 'native': native,
                           'validation_failure': validation})
    except Exception as exc:
        status = getattr(exc, 'status', None)
        wire(control, {'kind': 'failure', 'failure': {'type': type(exc).__name__,
             'native_status': status if type(status) is int else None, 'message': str(exc)[:512]}})
        raise
    finally:
        if model is not None:
            model.close()
        control.close()


def run(frozen_path, expected, approval, output, *, child_command=None):
    """One serial owned child. child_command is a model-free testing seam only."""
    from .qwen_candidates import validate_native_result
    frozen = read_json(frozen_path)
    verify_frozen(frozen, expected)
    if (not isinstance(approval, dict) or set(approval) != {'frozen_sha256', 'authorization_reference'}
        or approval['frozen_sha256'] != expected or not isinstance(approval['authorization_reference'], str)
        or not 0 < len(approval['authorization_reference']) <= 1024):
        raise ValueError('Matching explicit run authorization receipt required')
    output = Path(output).absolute(); output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.resolve() != output:
        raise ValueError('Unsafe candidate output directory')
    limits = frozen['limits']
    guard = ResourceGuard(output, (Identity.bind(os.getpid()),), own_cap=limits['tree_rss_bytes'],
        aggregate_cap=limits['tree_rss_bytes'], artifact_cap=limits['artifact_bytes'],
        host_reserve=limits['host_reserve_bytes'], disk_reserve=limits['disk_reserve_bytes'],
        deadline=limits['timeout_seconds'])
    guard.check()
    with runner_lease(output):
        store = CandidateStore(output, reserve=guard.check)
        store.initialize(expected, frozen['requests'], {k: limits[k] for k in ('max_calls', 'max_frames')})
        store.acquire({'pid': os.getpid(), 'created': Identity.bind(os.getpid()).created})
        pending = [r for r in frozen['requests'] if store.request(r['request_id'])['status'] == 'planned']
        if not pending:
            return store.events()
        parent, child = socket.socketpair()
        proc = None; aid = None; failure = None; runtime = None; receipt = {}
        def check():
            guard.check(); store.check_owner()
        try:
            env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                       CUDA_VISIBLE_DEVICES='', TRANSCRIBE_BACKEND='cpu', OMP_NUM_THREADS='2',
                       OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2', QWEN_CONTROL_FD=str(child.fileno()))
            command = child_command or [frozen['runtime']['python']['path'], '-B', '-m',
                       'audio_backend.qwen_candidate_runner', '--child', str(child.fileno()), str(frozen_path), expected]
            proc = subprocess.Popen(command, pass_fds=(child.fileno(),), cwd=ROOT, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            child.close()
            identity = Identity.bind(proc.pid)
            receipt.update(pid=proc.pid, created=identity.created)
            # Positive process visibility before sending any audio invocation.
            sampled = guard.check()
            if not any(p['pid'] == proc.pid and p['created'] == identity.created for p in sampled['processes']):
                raise RuntimeError('Owned child is absent from psutil tree; no inference admitted')
            receipt['positive_child_rss_sample'] = next(p for p in sampled['processes'] if p['pid'] == proc.pid)
            hello = receive(parent, check)
            if hello.get('kind') != 'ready' or hello.get('pid') != proc.pid:
                raise ValueError('Wrong candidate child readiness')
            runtime = hello['runtime']
            sampled = guard.check()
            receipt['ready_child_rss_sample'] = next(
                p for p in sampled['processes'] if p['pid'] == proc.pid and p['created'] == identity.created)
            if child_command is None and (not isinstance(runtime, dict)
                or runtime.get('runtime_manifest_sha256') != digest(frozen['runtime'])
                or runtime.get('model_sha256') != frozen['runtime']['model']['sha256']
                or runtime.get('library_sha256') != frozen['runtime']['native_library']['sha256']
                or str(runtime.get('actual_backend', '')).casefold() != 'cpu'):
                raise ValueError('Child runtime attestation disagrees with frozen request')
            for request in pending:
                check()
                aid = store.reserve(request['request_id'])
                wire(parent, {'kind': 'prepare', 'request_id': request['request_id']}, check=check)
                if receive(parent, check) != {'kind': 'prepared', 'request_id': request['request_id']}:
                    raise ValueError('Child did not confirm exact candidate input')
                store.started(aid)
                wire(parent, {'kind': 'invoke', 'request_id': request['request_id']}, check=check)
                result = receive(parent, check)
                if result.get('kind') != 'returned' or result.get('request_id') != request['request_id']:
                    raise ValueError('Wrong native result request binding')
                native = validate_native_result(result['native'])
                status = native['status']
                evidence = {'native': native, 'runtime': runtime}
                validation = result.get('validation_failure')
                if validation is not None:
                    if (not isinstance(validation, dict) or set(validation) != {'status', 'type'}
                        or validation['status'] not in {'source_changed', 'provenance_mismatch'}
                        or not isinstance(validation['type'], str) or len(validation['type']) > 128):
                        raise ValueError('Malformed post-inference validation failure')
                    status = validation['status']; evidence['validation_failure'] = validation
                store.finish(aid, status, evidence, returned=True)
                aid = None
                if status not in COMPLETE:
                    store.stop_remaining(status)
                    break
            wire(parent, {'kind': 'close'}, check=check)
        except BaseException as exc:
            failure = {'type': type(exc).__name__, 'message': str(exc)[:512]}
            if isinstance(exc, ChildFailure):
                failure['child'] = exc.evidence
        finally:
            parent.close(); child.close()
            if proc is not None:
                try:
                    if failure:
                        guard.terminate_owned(exclude=(os.getpid(),))
                    until = time.monotonic() + 5
                    while True:
                        try:
                            code = proc.wait(timeout=.05)
                            break
                        except subprocess.TimeoutExpired:
                            if time.monotonic() >= until:
                                raise
                            try:
                                guard.check()
                            except Exception:
                                guard.terminate_owned(exclude=(os.getpid(),))
                except subprocess.TimeoutExpired:
                    guard.terminate_owned(exclude=(os.getpid(),))
                    proc.kill(); code = proc.wait(timeout=5)
                receipt.update(wait_returncode=code, original_handle_waited_and_reaped=True)
                if code != 0 and failure is None:
                    failure = {'type': 'ChildExitFailure', 'message': 'Owned child exited with status ' + str(code)}
            receipt.update(failure=failure, peak_owned_rss=guard.peak_own,
                           minimum_host_available=guard.minimum_host, minimum_disk_free=guard.minimum_disk)
            # Child is reaped before durable failure writes; resource pressure
            # must not cause a false success or erase an earlier reservation.
            if aid is not None:
                store.finish(aid, 'interrupted_outcome_unknown', {'failure': failure}, returned=False)
            if failure:
                store.stop_remaining(failure['type'])
            store.receipt(receipt)
        if failure:
            raise RuntimeError('Candidate run stopped: ' + failure['type'])
        return store.events()


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--child':
        child_main(int(sys.argv[2]), sys.argv[3], sys.argv[4])
    else:
        raise SystemExit('Use scripts/benchmarks/run_qwen_candidates.py')
