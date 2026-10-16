"""Separate single-call child. No batch, recovery, service registration or downloads."""
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))

from audio_backend.moss_handy_adapter import retain_raw
from audio_backend.moss_handy_profile import NAME, canonical, profile, runtime_site_paths, verify_model, verify_runtime
from audio_backend.native_lexical_alternatives import SourceSupport

CAPS = {'owner.json': 1024, 'request.json': 16384, 'support.wav': 640044,
        'raw.bin': 131072, 'result.json': 8192, 'probe-result.json': 65536,
        **{'probe-%d.pcm' % index: 64000 for index in range(4)}}
SCRATCH_BYTES = 2 * 1024**2


def directory_identity(directory):
    if (not directory.is_absolute() or directory.resolve() != directory or
            any(path.is_symlink() for path in (directory, *directory.parents))):
        raise ValueError('Owned directory path changed or contains an alias')
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()):
        raise ValueError('Owned directory kind, mode or owner differs')
    return info.st_dev, info.st_ino, info.st_uid


def process_identity(pid):
    """Linux process identity without importing third-party code in the child."""
    with open('/proc/' + str(pid) + '/stat', 'rb') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError('Process identity record exceeds bound')
    return pid, int(raw.rsplit(b')', 1)[1].split()[19])


def check_directory(directory, identity=None):
    """Fixed files, logical and allocated byte bounds, no links or subdirectories."""
    actual = directory_identity(directory)
    if identity is not None and actual != identity:
        raise ValueError('Owned directory identity changed')
    total = allocated = 0
    for path in directory.iterdir():
        info = path.lstat()
        if (path.name not in CAPS or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > CAPS[path.name]):
            raise ValueError('Unexpected or oversized owned scratch file')
        total += info.st_size
        allocated += info.st_blocks * 512
    if max(total, allocated) > SCRATCH_BYTES:
        raise ValueError('Owned scratch byte budget exceeded')
    return max(total, allocated)


def write_owned(directory, name, raw, *, identity=None):
    if type(raw) is not bytes or name not in CAPS or len(raw) > CAPS[name]:
        raise ValueError('Owned write exceeds fixed bound')
    if check_directory(directory, identity) + len(raw) + 4096 > SCRATCH_BYTES:
        raise ValueError('Owned scratch projected budget exceeded')
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    check_directory(directory, identity)


def read_owned(directory, name, *, identity=None):
    check_directory(directory, identity)
    fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > CAPS[name]:
            raise ValueError('Invalid owned file')
        data = stream.read(CAPS[name] + 1)
        after = os.fstat(stream.fileno())
    if len(data) != before.st_size or (before.st_ino, before.st_mtime_ns, before.st_size) != (
            after.st_ino, after.st_mtime_ns, after.st_size):
        raise ValueError('Owned file changed during read')
    return data


def admit_parent(directory, stream):
    claim = json.loads(read_owned(directory, 'owner.json'))
    identity = tuple(claim['directory_identity'])
    check_directory(directory, identity)
    expected = tuple(claim['parent_identity'])
    if process_identity(os.getppid()) != expected:
        raise ValueError('Original parent identity lost before admission')
    if stream.read(1) != b'1':
        raise ValueError('Missing owned-parent invocation admission')
    if process_identity(os.getppid()) != expected:
        raise ValueError('Original parent identity lost during admission')
    return expected, identity


def samples_from_pcm(pcm):
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    return array('f', (sample / 32768.0 for sample in samples))


def float_bytes(samples):
    result = array('f', samples)
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


def context_frames(frames):
    if type(frames) is not int or not 0 < frames <= 320000:
        raise ValueError('Bounded support required')
    tokens = (frames - 1) // 1280 + 1
    markers = sum(len(str(second)) for second in range(5, (tokens * 2) // 25 + 1, 5))
    return 86 + tokens + markers + 512


def one_call(api, model_path, samples, *, retain, check):
    """Injected model-free seam. The possibly-started call is never retried."""
    model = session = None
    receipt = {'construct_attempts': 0, 'session_attempts': 0, 'run_attempts': 0,
        'raw_retained': False, 'raw_unavailable_reason': 'no_native_result',
        'raw_kind': 'exact-native-raw-text-utf8',
        'truncated': False, 'aborted': False, 'eos': None, 'generated_token_count': None,
        'model_closed': False, 'session_closed': False, 'error_type': None, 'close_errors': []}
    try:
        check()
        n_ctx = context_frames(len(samples))
        receipt['construct_attempts'] = 1
        model = api.Model(model_path, backend='cpu')
        if (model.device.kind.lower() != 'cpu' or not model.backend.lower().startswith('cpu')
                or model.arch != 'moss' or model.variant != 'moss-transcribe-diarize'):
            raise ValueError('Loaded model family/device differs')
        check()
        receipt['session_attempts'] = 1
        session = model.session(n_threads=2, n_ctx=n_ctx, kv_type='f16')
        if (session.limits.effective_n_ctx != n_ctx or
                session.limits.effective_max_audio_ms not in (0,) and
                session.limits.effective_max_audio_ms < math.ceil(len(samples) / 16)):
            raise ValueError('Native session limits differ')
        check()
        receipt['run_attempts'] = 1
        try:
            native = session.run(samples, diarize='on', timestamps='segment')
        except (api.OutputTruncated, api.Aborted) as exc:
            receipt['truncated'] = isinstance(exc, api.OutputTruncated)
            receipt['aborted'] = isinstance(exc, api.Aborted)
            native = exc.partial_result
        raw, reason = retain_raw(getattr(native, 'raw_text', None))
        receipt['raw_unavailable_reason'] = reason
        if raw is not None:
            retain(raw)  # Exact bytes are retained before any parsing or close.
            receipt.update(raw_retained=True, raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
    except Exception as exc:
        receipt['error_type'] = type(exc).__name__
    finally:
        for name, instance in (('session', session), ('model', model)):
            if instance is not None:
                try:
                    instance.close()
                    receipt[name + '_closed'] = True
                except Exception as exc:
                    receipt['close_errors'].append({'object': name, 'type': type(exc).__name__})
    return receipt


def main(directory, request_sha):
    directory = Path(directory)
    original_parent, scratch_identity = admit_parent(directory, sys.stdin.buffer)
    deadline = time.monotonic() + 300
    done = threading.Event()
    def check():
        if time.monotonic() >= deadline or process_identity(os.getppid()) != original_parent:
            raise RuntimeError('Owned child deadline or parent loss')
        check_directory(directory, scratch_identity)
    def watchdog():
        while not done.wait(.1):
            try:
                check()
            except Exception:
                os._exit(75)
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        os.environ.update(OMP_NUM_THREADS='2', OMP_THREAD_LIMIT='2', OPENBLAS_NUM_THREADS='1',
            MKL_NUM_THREADS='2', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
            HF_HUB_DISABLE_TELEMETRY='1', CUDA_VISIBLE_DEVICES='')
        if not hasattr(os, 'sched_getaffinity'):
            raise ValueError('CPU affinity unavailable')
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
        def deny(event, args):
            if event.startswith(('subprocess.', 'socket.')) or event in {
                    'os.system', 'os.fork', 'os.forkpty', 'os.exec', 'os.posix_spawn', 'os.spawn'}:
                raise ValueError('Child process/network creation denied')
        sys.addaudithook(deny)
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (640044, 640044))
        raw_request = read_owned(directory, 'request.json', identity=scratch_identity)
        if hashlib.sha256(raw_request).hexdigest() != request_sha:
            raise ValueError('Request pin differs')
        request = json.loads(raw_request)
        if request.get('kind') == 'recording-speaker-probes-v1':
            # This is a fresh admitted child after MOSS model/session closure
            # and positive reap; it can only run the fixed optional extractor.
            deadline = time.monotonic() + 10.
            from audio_backend.recording_speaker_probe_runner import child
            child(directory, request, identity=scratch_identity, check=check)
            return
        if set(request) != {'job_id', 'support', 'profile_name', 'profile_sha256', 'model_path'}:
            raise ValueError('Unexpected child request fields')
        support = SourceSupport.from_dict(request['support'])
        chosen = profile(request['profile_name'])
        if chosen.sha256 != request['profile_sha256']:
            raise ValueError('Profile pin differs')
        with wave.open(str(directory / 'support.wav'), 'rb') as stream:
            frames = stream.getnframes()
            if ((stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000)
                    or frames != support.end_frame - support.start_frame):
                raise ValueError('Support WAV geometry differs')
            pcm = stream.readframes(frames)
        samples = samples_from_pcm(pcm)
        if (len(samples) != frames or hashlib.sha256(pcm).hexdigest() != support.pcm16le_sha256
                or hashlib.sha256(float_bytes(samples)).hexdigest() != support.float32le_sha256):
            raise ValueError('Immutable support hashes differ')
        model_path = verify_model(request['model_path'], check=check)
        # -S avoids sitecustomize/.pth execution; expose package metadata only
        # after the stdlib request/support checks. Runtime sources load through
        # the unchanged hash-verifying finder, never ambient bytecode.
        sites = runtime_site_paths()
        for location in sites:
            if str(location) not in sys.path:
                sys.path.append(str(location))
        _, paths, hashes = verify_runtime(NAME, sites=sites)
        from audio_backend import moss_td_cpp_assets as assets
        api = assets.load_verified_runtime(paths, hashes)
        spec = chosen.payload()
        commit = api.native_commit()
        if (api.__version__ != spec['runtime_version'] or api.native_version() != spec['runtime_version']
                or type(commit) is not str or len(commit) < 7 or not spec['runtime_source_revision'].startswith(commit)
                or api.native_provider() != 'transcribe-cpp-native'
                or Path(api.library_path()).resolve() != paths[assets.native_library_name()]
                or not api.backend_available('cpu')):
            raise ValueError('Loaded native identity differs')
        result = one_call(api, model_path, samples,
            retain=lambda raw: write_owned(directory, 'raw.bin', raw, identity=scratch_identity), check=check)
        check()
        # A second full hash is verification, never a second model call.
        verify_model(model_path, check=check)
        result.update(job_id=request['job_id'], support_id=support.support_id, profile_sha256=chosen.sha256)
        write_owned(directory, 'result.json', canonical(result), identity=scratch_identity)
    finally:
        done.set()


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('The owned controller supplies an isolated request and its hash')
    main(sys.argv[1], sys.argv[2])
