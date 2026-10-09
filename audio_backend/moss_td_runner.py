"""One isolated MOSS-TD load, up to four sequential bounded local supports."""
import json
import math
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_JSON_BYTES = 1024**2
MAX_SUPPORTS = 4
MAX_TOTAL_SECONDS = 80.0
HOST_RESERVE_BYTES = int(1.5 * 1024**3)
DISK_RESERVE_BYTES = 5 * 1024**3
ERROR_PATH = None
STAGE = 'request_validation'


def atomic_json(path, value):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError('MOSS-TD response exceeds 1 MiB')
    path = Path(path)
    pending = path.with_name(path.name + '.pending')
    pending.write_bytes(encoded)
    pending.replace(path)


def fail_report(reason, message):
    if ERROR_PATH is not None:
        try:
            atomic_json(ERROR_PATH, {'reason_code': reason, 'message': str(message)[:2000]})
        except (OSError, ValueError):
            pass


def positive_limit(config, key, default, maximum):
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f'Invalid MOSS-TD limit: {key}')
    value = float(value)
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise ValueError(f'Invalid MOSS-TD limit: {key}')
    return value


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {'config', 'supports', 'output'}:
        raise ValueError('Expected config, supports and output only')
    config, supports, output = request['config'], request['supports'], request['output']
    if not isinstance(config, dict) or not isinstance(output, str) or not output or '://' in output:
        raise ValueError('Invalid MOSS-TD config or local output path')
    if not isinstance(supports, list) or not 0 < len(supports) <= MAX_SUPPORTS:
        raise ValueError('MOSS-TD batch requires 1 to 4 bounded supports')
    seen = set()
    for support in supports:
        if not isinstance(support, dict) or set(support) != {'support_id', 'path'}:
            raise ValueError('MOSS-TD support accepts only support_id and path; no conditioning inputs')
        sid, path = support['support_id'], support['path']
        if (not isinstance(sid, str) or not sid or len(sid) > 256 or sid in seen or
                not isinstance(path, str) or not path or len(path) > 8192 or '://' in path):
            raise ValueError('Invalid or duplicate support identifier/local path')
        seen.add(sid)
    positive_limit(config, 'moss_td_max_support_seconds', 20, 20)
    positive_limit(config, 'moss_td_max_seconds', 40, MAX_TOTAL_SECONDS)
    positive_limit(config, 'moss_td_memory_limit_bytes', int(3.5 * 1024**3), int(4.5 * 1024**3))
    positive_limit(config, 'moss_td_timeout_seconds', 300, 3600)
    if config.get('moss_td_runtime', 'transformers') not in {'transformers', 'cpp'}:
        raise ValueError('Unsupported MOSS-TD runtime')
    if config.get('moss_td_cpp_device', 'cpu') not in {'cpu', 'vulkan'}:
        raise ValueError('Unsupported MOSS-TD C++ device')
    if config.get('moss_td_runtime', 'transformers') != 'cpp' and config.get('moss_td_cpp_device', 'cpu') != 'cpu':
        raise ValueError('Vulkan requires the explicit MOSS-TD C++ runtime')
    return config, supports, Path(output)


def process_tree_rss(process, psutil):
    total = 0
    for item in [process, *process.children(recursive=True)]:
        try:
            total += item.memory_info().rss
        except psutil.NoSuchProcess:
            continue
    return total


def resource_failure(process, config, output, stats, began, psutil):
    rss = process_tree_rss(process, psutil)
    available = psutil.virtual_memory().available
    disk_free = shutil.disk_usage(output.parent).free
    stats['peak_rss_bytes'] = max(stats.get('peak_rss_bytes', 0), rss)
    stats['minimum_host_available_bytes'] = min(stats.get('minimum_host_available_bytes', available), available)
    stats['minimum_disk_free_bytes'] = min(stats.get('minimum_disk_free_bytes', disk_free), disk_free)
    cap = int(config.get('moss_td_memory_limit_bytes', int(3.5 * 1024**3)))
    if rss > cap:
        return 'memory_guard', f'MOSS-TD process-tree RSS exceeded {cap} bytes'
    if available < HOST_RESERVE_BYTES:
        return 'host_memory_reserve', 'Host available memory below 1.5 GiB reserve'
    if disk_free < DISK_RESERVE_BYTES:
        return 'disk_reserve', 'Disk free space below 5 GiB reserve'
    if time.monotonic() - began > float(config.get('moss_td_timeout_seconds', 300)):
        return 'timeout', 'MOSS-TD worker exceeded its configured wall-clock limit'
    return None


def terminate_owned(process, psutil, reason, message):
    fail_report(reason, message)
    for child in process.children(recursive=True):
        try:
            child.kill()
        except psutil.Error:
            pass
    os._exit(75)


def main(argv=None):
    global ERROR_PATH, STAGE
    STAGE = 'request_validation'
    path = Path((sys.argv[1:] if argv is None else argv)[0])
    ERROR_PATH = path.with_suffix('.error.json')
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError('Oversized MOSS-TD request')
    request = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(request, dict) and isinstance(request.get('output'), str) and request['output']:
        ERROR_PATH = Path(request['output']).with_suffix('.error.json')
    config, supports, output = validate_request(request)
    os.environ.update(OMP_NUM_THREADS='2', OMP_THREAD_LIMIT='2', OPENBLAS_NUM_THREADS='1',
                      MKL_NUM_THREADS='2', NUMBA_NUM_THREADS='2', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
    import psutil
    from audio_backend.runner import watch_parent
    threading.Thread(target=watch_parent, daemon=True).start()
    process = psutil.Process()
    stats = {'peak_rss_bytes': 0, 'cpu_threads': 2, 'resource_scope': 'worker_process_tree'}
    try:
        if hasattr(os, 'sched_getaffinity'):
            os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
        else:
            process.cpu_affinity(process.cpu_affinity()[:2])
        stats['cpu_affinity_enforced'] = True
    except (AttributeError, NotImplementedError, OSError, psutil.Error):
        stats['cpu_affinity_enforced'] = False
    began = time.monotonic()
    stop = threading.Event()
    def check():
        failure = resource_failure(process, config, output, stats, began, psutil)
        if failure:
            terminate_owned(process, psutil, *failure)
    check()
    def guard():
        while not stop.wait(.1):
            try:
                check()
            except Exception as exc:
                terminate_owned(process, psutil, 'resource_guard_error', str(exc))
    threading.Thread(target=guard, daemon=True).start()
    try:
        if config.get('moss_td_runtime', 'transformers') == 'cpp':
            # The service inference package imports numpy/soundfile. This
            # isolated worker only needs the stdlib C++ provider and parser.
            name = 'audio_backend.inference'
            if name not in sys.modules:
                package = types.ModuleType(name)
                package.__path__ = [str(ROOT / 'audio_backend/inference')]
                sys.modules[name] = package
            from audio_backend.inference.moss_td_cpp_asr import MossTDCppRecognizer as MossTDRecognizer, unresolved, wav_info
            audio_info = wav_info
        else:
            import soundfile as sf
            from audio_backend.inference.moss_td_asr import MossTDRecognizer, unresolved
            audio_info = sf.info
        invalid, durations = {}, {}
        support_limit = float(config.get('moss_td_max_support_seconds', 20))
        for support in supports:
            sid = support['support_id']
            try:
                info = audio_info(support['path'])
                if info.channels != 1 or info.samplerate != 16000 or info.frames <= 0:
                    invalid[sid] = unresolved('invalid_audio', metadata={'inference_run': False})
                elif info.frames / 16000 > support_limit:
                    invalid[sid] = unresolved('support_duration_limit', metadata={'inference_run': False})
                else:
                    durations[sid] = info.frames / 16000
            except (OSError, RuntimeError, ValueError) as exc:
                invalid[sid] = unresolved('invalid_audio', metadata={'inference_run': False, 'error': str(exc)[:1000]})
        seconds = sum(durations.values())
        if seconds > float(config.get('moss_td_max_seconds', 40)):
            raise ValueError('MOSS-TD total audio budget exceeded')
        recognizer = None
        if durations:
            STAGE = 'model_load'
            recognizer = MossTDRecognizer(config)
        STAGE = 'inference'
        rows = []
        for support in supports:
            sid = support['support_id']
            if sid in invalid:
                result = invalid[sid]
            else:
                try:
                    result = recognizer.transcribe(support)
                except Exception as exc:
                    result = unresolved('support_inference_error', metadata={'error': f'{type(exc).__name__}: {exc}'[:2000], 'inference_run': None})
            if recognizer is not None:
                result['metadata'] = {**recognizer.metadata, **result.get('metadata', {})}
            if sid in durations:
                result['metadata'].setdefault('duration_seconds', durations[sid])
            rows.append({'support_id': sid, **result})
            check()
        check()
        atomic_json(output, {'supports': rows, 'metadata': {**stats, 'seconds': time.monotonic() - began,
                    'audio_seconds': seconds, 'model_loads': int(recognizer is not None),
                    'model': recognizer.metadata if recognizer is not None else {}}})
    finally:
        try:
            if 'recognizer' in locals() and recognizer is not None and hasattr(recognizer, 'close'):
                recognizer.close()
        finally:
            stop.set()


if __name__ == '__main__':
    try:
        main()
    except (ModuleNotFoundError, ImportError) as exc:
        fail_report('dependency_error', f'{type(exc).__name__}: {exc}')
        raise
    except Exception as exc:
        reason = {'request_validation': 'request_validation_error', 'model_load': 'model_load_error'}.get(STAGE, 'inference_error')
        fail_report(reason, f'{type(exc).__name__}: {exc}')
        raise
