"""One bounded, truth-free native Sherpa encoding stage per C or T partition.

Preparation/import/tests never construct a real model. Only ``run`` launches a
worker. No threshold, labels, feature matrices, C bridge, downloads, or production
state are read or written. T is released separately after a calibration seal.

make_plan_template(C_or_T, ordered_samples, two_repeat_ids) leaves protocol and
release fields null; it cannot run. After the protocol has been sealed,
make_manifest(C_or_T, protocol_sha256, ordered_samples, two_repeat_ids,
              release_pin=None) creates the in-memory manifest. Each sample:
  {id, wav: {path, bytes, sha256}, pcm_sha256, float32_sha256}
The caller seals the returned JSON and passes its SHA256 to run. T requires the
file pin of the separately sealed calibration choice; its bytes are checked before and after encoding. This module verifies neither
that choice's semantics nor corpus independence, which belong to orchestration.

Reused immutable source: ../backend-frontend-comparison-20261104/
frontend_experiment.py, SHA256 4c324be6150b02d513cbbd867acfd1910e3126586ab719e7ec8e5a277e4a7ed7.
Only its pin/PCM/normalization/repeatability/resource/cleanup helpers are called.
The native contract retains the same 2,048 package/interpreter/model/numerical
pins from EXPERIMENT_FREEZE.json. This is same-host CP312 evidence, not an OS or
reproducible-build proof. RSS is sampled, not a hard address-space limit.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import shutil
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parent
FROZEN_ROOT = ROOT.parent / 'backend-frontend-comparison-20261104'
HELPER_PATH = FROZEN_ROOT / 'frontend_experiment.py'
HELPER_SHA = '4c324be6150b02d513cbbd867acfd1910e3126586ab719e7ec8e5a277e4a7ed7'
EVIDENCE_SHA = 'fbe31b4d31c8bb43af745618f93bda75ccda9e6e11e7de6254471e3a23b5bc3f'
CONTRACT_SHA = '39cf81d7e72507ee7867b87bfb71d680c530b9582d4d586383ed6f7351bd8b2d'


def load_helpers():
    # Compile the exact bytes checked here; do not import a possibly stale pyc.
    source = HELPER_PATH.read_bytes()
    if hashlib.sha256(source).hexdigest() != HELPER_SHA:
        raise ValueError('frozen helper source pin mismatch')
    module = types.ModuleType('_pinned_frontend_helpers')
    module.__file__ = str(HELPER_PATH)
    exec(compile(source, str(HELPER_PATH), 'exec'), module.__dict__)
    return module


h = load_helpers()
LIMITS = {k: h.LIMITS[k] for k in ('wall_seconds', 'cleanup_seconds', 'cpu_seconds',
          'rss_bytes', 'output_bytes', 'memory_reserve_bytes', 'disk_reserve_bytes')}
LIMITS.update(max_planned_clips=256, repeat_calls=4, sessions_per_stage=1)
MANIFEST_KEYS = {'schema', 'partition', 'protocol_sha256', 'release_pin',
                 'source', 'helper_source', 'runtime_contract', 'affinity_cpus',
                 'limits', 'samples', 'repeat_ids'}
SAMPLE_KEYS = {'id', 'wav', 'pcm_sha256', 'float32_sha256'}


def sha_ok(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def validate_pin_shape(pin):
    if (not isinstance(pin, dict) or set(pin) != {'path', 'bytes', 'sha256'}
            or not isinstance(pin['path'], str) or not Path(pin['path']).is_absolute()
            or type(pin['bytes']) is not int or pin['bytes'] < 1 or not sha_ok(pin['sha256'])):
        raise ValueError('invalid artifact pin')


def native_contract():
    """Prepare native-only metadata from the already-frozen comparison evidence.

    This function does not import Sherpa, construct a model, or open any WAV.
    Only whitelisted runtime fields are returned; no old sample metadata escapes.
    The worker receives this contract and never opens the older manifest.
    """
    path = FROZEN_ROOT / 'EXPERIMENT_FREEZE.json'
    if h.digest_file(path) != EVIDENCE_SHA:
        raise ValueError('frozen runtime evidence changed')
    frozen = h.read_json(path)
    runtime = Path(frozen['runtime'])
    keep = {frozen['model']['path'], str(Path(frozen['python']).resolve()),
            *[p['path'] for p in frozen['numerical_library_pins']]}
    pins = {p['path']: p for p in frozen['artifact_pins']
            if Path(p['path']).is_relative_to(runtime) or p['path'] in keep}
    contract = {k: frozen[k] for k in ('model', 'library', 'runtime', 'python',
                                      'numerical_library_pins')}
    contract['artifact_pins'] = [pins[k] for k in sorted(pins)]
    if hashlib.sha256(h.canonical(contract)).hexdigest() != CONTRACT_SHA:
        raise ValueError('native runtime contract mismatch')
    return contract


def make_plan_template(partition, samples, repeat_ids):
    """Build a non-executable plan without inventing protocol or release pins."""
    value = {'schema': 1, 'partition': partition, 'protocol_sha256': None,
             'release_pin': None, 'source': h.pinned(__file__),
             'helper_source': h.pinned(HELPER_PATH), 'runtime_contract': native_contract(),
             'affinity_cpus': sorted(os.sched_getaffinity(0))[:2], 'limits': dict(LIMITS),
             'samples': samples, 'repeat_ids': repeat_ids}
    return validate_plan_template(value)


def make_manifest(partition, protocol_sha256, samples, repeat_ids, release_pin=None):
    value = make_plan_template(partition, samples, repeat_ids)
    value.update(protocol_sha256=protocol_sha256, release_pin=release_pin)
    return validate_manifest(value)


def validate_plan_template(value):
    return _validate_manifest(value, executable=False)


def validate_manifest(value):
    return _validate_manifest(value, executable=True)


def _validate_manifest(value, executable):
    # Closed schemas reject accidental labels, query truth, roles, or extra knobs.
    if not isinstance(value, dict) or set(value) != MANIFEST_KEYS or type(value['schema']) is not int or value['schema'] != 1:
        raise ValueError('unexpected manifest schema or fields')
    if value['partition'] not in ('C', 'T'):
        raise ValueError('invalid partition')
    release = value['release_pin']
    if executable:
        if not sha_ok(value['protocol_sha256']):
            raise ValueError('executable manifest requires protocol seal')
        if value['partition'] == 'C' and release is not None:
            raise ValueError('C must not have a calibration-choice release pin')
        if value['partition'] == 'T':
            validate_pin_shape(release)
    elif value['protocol_sha256'] is not None or release is not None:
        raise ValueError('plan templates must have null protocol and release pins')
    if value['limits'] != LIMITS:
        raise ValueError('unreviewed resource limits')
    for key in ('source', 'helper_source'):
        validate_pin_shape(value[key])
    if value['source']['path'] != str(Path(__file__).resolve()):
        raise ValueError('runner source path mismatch')
    if (value['helper_source']['path'] != str(HELPER_PATH)
            or value['helper_source']['sha256'] != HELPER_SHA):
        raise ValueError('helper source contract mismatch')
    if hashlib.sha256(h.canonical(value['runtime_contract'])).hexdigest() != CONTRACT_SHA:
        raise ValueError('native runtime contract mismatch')
    cpus = value['affinity_cpus']
    if (not isinstance(cpus, list) or len(cpus) != 2 or len(set(cpus)) != 2
            or any(type(cpu) is not int or cpu < 0 for cpu in cpus)
            or cpus != sorted(cpus)):
        raise ValueError('exactly two ordered affinity CPUs required')
    samples = value['samples']
    if not isinstance(samples, list) or not 2 <= len(samples) <= LIMITS['max_planned_clips']:
        raise ValueError('planned clip count outside bound')
    ids = []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != SAMPLE_KEYS:
            raise ValueError('unexpected clip fields, including possible truth')
        identity = sample['id']
        if not isinstance(identity, str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identity) is None:
            raise ValueError('invalid opaque clip ID')
        validate_pin_shape(sample['wav'])
        if sample['wav']['bytes'] > 128 * 1024:
            raise ValueError('oversized fixed-length WAV')
        if not all(sha_ok(sample[k]) for k in ('pcm_sha256', 'float32_sha256')):
            raise ValueError('missing exact PCM/float32 input hashes')
        ids.append(identity)
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate opaque clip IDs')
    repeats = value['repeat_ids']
    if (not isinstance(repeats, list) or len(repeats) != 2
            or any(not isinstance(i, str) for i in repeats)
            or len(set(repeats)) != 2 or not set(repeats) <= set(ids)):
        raise ValueError('exactly two distinct planned repeat anchors required')
    return value


def load_manifest(path, sha256):
    if not sha_ok(sha256) or h.digest_file(path) != sha256:
        raise ValueError('manifest SHA256 mismatch')
    return validate_manifest(h.read_json(path))


def verify_environment(manifest):
    contract = manifest['runtime_contract']
    if Path(sys.prefix).resolve() != Path(contract['runtime']).resolve():
        raise ValueError('wrong isolated interpreter prefix')
    if not set(manifest['affinity_cpus']) <= os.sched_getaffinity(0):
        raise ValueError('selected affinity CPUs unavailable')
    h.verify_pin(manifest['source'])
    h.verify_pin(manifest['helper_source'])
    if manifest['release_pin'] is not None:
        h.verify_pin(manifest['release_pin'])
    for pin in contract['artifact_pins']:
        h.verify_pin(pin)
    packages = Path(contract['runtime']) / 'lib/python3.12/site-packages'
    expected = {p['path'] for p in contract['artifact_pins']
                if Path(p['path']).is_relative_to(packages)}
    actual = {str(p.resolve(strict=True)) for p in packages.rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('runtime package file inventory changed')


def waveform(sample):
    import numpy as np
    pcm = h.pcm_bytes(sample)
    value = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / np.float32(32768)
    if (value.shape != (32000,) or not np.isfinite(value).all()
            or hashlib.sha256(value.astype('<f4', copy=False).tobytes()).hexdigest()
            != sample['float32_sha256']):
        raise ValueError('exact PCM-to-float32 input bridge mismatch')
    return value


def new_extractor(contract):
    import sherpa_onnx
    return sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=contract['model']['path'], num_threads=2, provider='cpu', debug=False))


def error_record(exc):
    return {'type': type(exc).__name__, 'message': str(exc)}


def worker(args):
    manifest = load_manifest(args.manifest, args.manifest_sha)
    output = Path(args.output)
    output.mkdir(exist_ok=False)
    ids = [s['id'] for s in manifest['samples']]
    result = {'schema': 1, 'partition': manifest['partition'], 'manifest_sha256': args.manifest_sha,
              'status': 'failed', 'planned_ids': ids, 'completed_ids': [], 'unrun_ids': list(ids),
              'calls_attempted': 0, 'calls_completed': 0, 'sessions_attempted': 0,
              'planned_calls': len(ids) + 4, 'repeat_metrics': [], 'error': None,
              'clips': [dict(id=s['id'], status='unrun', wav_sha256=s['wav']['sha256'],
                             pcm_sha256=s['pcm_sha256'], float32_sha256=s['float32_sha256'])
                        for s in manifest['samples']]}
    journal = (output / 'journal.jsonl').open('x', buffering=1)
    def event(**value):
        journal.write(h.canonical(value).decode() + '\n')
        journal.flush()
        os.fsync(journal.fileno())
    model = None
    raw = normalized = valid = repeat_raw = repeat_normalized = None
    repeat_ids = [identity for identity in manifest['repeat_ids'] for _ in (1, 2)]
    try:
        verify_environment(manifest)
        verify_worker_bounds(manifest)
        import numpy as np
        raw = np.full((len(ids), 512), np.nan, dtype=np.float32)
        normalized = raw.copy()
        valid = np.zeros(len(ids), dtype=np.bool_)
        repeat_raw = np.full((4, 512), np.nan, dtype=np.float32)
        repeat_normalized = repeat_raw.copy()
        waveforms = []
        for sample, row in zip(manifest['samples'], result['clips']):
            try:
                waveforms.append(waveform(sample))
                row['status'] = 'verified_unrun'
            except Exception as exc:
                waveforms.append(None)
                row.update(status='missing_or_invalid_input', error=error_record(exc))
                event(event='input_rejected', id=sample['id'], error=error_record(exc))
        if any(w is None for w in waveforms):
            raise ValueError('one or more planned inputs unavailable; no model construction')
        event(event='model_construction_attempt')
        result['sessions_attempted'] = 1
        model = new_extractor(manifest['runtime_contract'])
        if model.dim != 512:
            raise ValueError('native dimension mismatch')
        result['loaded_ort_paths'] = h.loaded_ort(manifest['runtime_contract'])
        event(event='model_construction_complete', dimension=512)
        def encode(index, repeat):
            if result['calls_attempted'] >= result['planned_calls']:
                raise RuntimeError('fixed native call cap reached')
            result['calls_attempted'] += 1
            event(event='encode_attempt', id=ids[index], repeat=repeat,
                  call=result['calls_attempted'], float32_sha256=manifest['samples'][index]['float32_sha256'])
            stream = model.create_stream()
            stream.accept_waveform(16000, waveforms[index])
            stream.input_finished()
            if not model.is_ready(stream):
                raise ValueError('native stream not ready')
            value = np.ascontiguousarray(model.compute(stream), dtype=np.float32)
            norm = h.producer_normalize(value)
            result['calls_completed'] += 1
            event(event='encode_complete', id=ids[index], repeat=repeat,
                  call=result['calls_completed'], raw_sha256=hashlib.sha256(value.tobytes()).hexdigest(),
                  normalized_sha256=hashlib.sha256(norm.tobytes()).hexdigest())
            return value, norm
        for index, row in enumerate(result['clips']):
            row['status'] = 'encoding'
            raw[index], normalized[index] = encode(index, 0)
            valid[index] = True
            row.update(status='encoded', raw_sha256=hashlib.sha256(raw[index].tobytes()).hexdigest(),
                       normalized_sha256=hashlib.sha256(normalized[index].tobytes()).hexdigest())
            result['completed_ids'].append(ids[index])
            result['unrun_ids'].remove(ids[index])
        for index, identity in enumerate(repeat_ids):
            base = ids.index(identity)
            number = index % 2 + 1
            repeat_raw[index], repeat_normalized[index] = encode(base, number)
            metric = h.vector_delta(normalized[base], repeat_normalized[index])
            metric.update(id=identity, repeat=number)
            result['repeat_metrics'].append(metric)
        if len(result['repeat_metrics']) != 4 or not all(r['passed'] for r in result['repeat_metrics']):
            raise RuntimeError('fixed-anchor repeatability gate failed')
        result['status'] = 'passed'
    except BaseException as exc:
        result['error'] = error_record(exc)
        event(event='stage_error', error=result['error'])
    finally:
        model = None
        gc.collect()
        try:
            if raw is not None:
                np.savez(output / 'vectors.npz', ids=np.asarray(ids), raw=raw, normalized=normalized,
                         valid=valid, repeat_raw=repeat_raw, repeat_normalized=repeat_normalized,
                         repeat_ids=np.asarray(repeat_ids))
                result['vectors'] = h.pinned(output / 'vectors.npz')
            verify_environment(manifest)
            for sample in manifest['samples']:
                waveform(sample)
            result['terminal_integrity_passed'] = True
        except BaseException as exc:
            result['terminal_integrity_passed'] = False
            result['status'] = 'failed'
            result['terminal_integrity_error'] = error_record(exc)
        for row in result['clips']:
            if row['status'] == 'encoding':
                row['status'] = 'encoding_failed'
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result.update(cpu_seconds=usage.ru_utime + usage.ru_stime, maxrss_kib=usage.ru_maxrss)
        event(event='terminal', status=result['status'])
        journal.close()
        h.write_json(output / 'result.json', result)
    return 0 if result['status'] == 'passed' else 1


def verify_worker_bounds(manifest):
    if (os.sched_getaffinity(0) != set(manifest['affinity_cpus'])
            or os.getpriority(os.PRIO_PROCESS, 0) != 19
            or os.getpgrp() != os.getpid() or os.getsid(0) != os.getpid()):
        raise ValueError('worker affinity, priority or owned-session boundary missing')
    for limit, cap in ((resource.RLIMIT_CPU, LIMITS['cpu_seconds']),
                       (resource.RLIMIT_FSIZE, LIMITS['output_bytes'])):
        soft, hard = resource.getrlimit(limit)
        if not 0 < soft <= hard <= cap:
            raise ValueError('worker hard resource limit missing')
    if resource.getrlimit(resource.RLIMIT_CORE) != (0, 0):
        raise ValueError('worker core-dump prohibition missing')


def cleanup_child(child, absolute_deadline):
    # Share one 30-second budget with the frozen helper and every fallback.
    deadline = min(absolute_deadline, time.monotonic() + LIMITS['cleanup_seconds'])
    try:
        return h.cleanup_owned(child, absolute_deadline, {'deadline': deadline})
    except BaseException as exc:
        # Group state remains uncertain, but the original Popen handle still
        # owns the leader. Stop/reap that handle without renewing any budget.
        waited = False
        code = None
        error = 'owned cleanup raised: ' + str(exc)
        try:
            if child.poll() is None:
                child.terminate()
        except BaseException as stop_error:
            error += '; original handle terminate failed: ' + str(stop_error)
        try:
            code = child.wait(timeout=max(0, min(5, deadline - time.monotonic())))
            waited = True
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except BaseException as kill_error:
                error += '; original handle kill failed: ' + str(kill_error)
            try:
                code = child.wait(timeout=max(0, deadline - time.monotonic()))
                waited = True
            except BaseException as wait_error:
                error += '; original handle wait failed: ' + str(wait_error)
        except BaseException as wait_error:
            error += '; original handle wait failed: ' + str(wait_error)
        return {'returncode': code, 'positive_original_wait': waited,
                'owned_process_group_absent': False, 'termination_needed': True,
                'cleanup_error': error}


def prepare_child(manifest, cpu_remaining, inherited_signal_mask=()):
    os.sched_setaffinity(0, manifest['affinity_cpus'])
    os.setpriority(os.PRIO_PROCESS, 0, 19)
    cap = max(1, math.floor(cpu_remaining))
    resource.setrlimit(resource.RLIMIT_CPU, (cap, cap))
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMITS['output_bytes'], LIMITS['output_bytes']))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.pthread_sigmask(signal.SIG_SETMASK, inherited_signal_mask)


def supervise_child(manifest, manifest_path, manifest_sha, output, started, cpu_start):
    """One original Popen handle, one owned process group, no retry or next stage."""
    import psutil
    h.admission(output, started, 0, cpu_start, True)
    own = resource.getrusage(resource.RUSAGE_SELF)
    remaining = LIMITS['cpu_seconds'] - (own.ru_utime + own.ru_stime - cpu_start)
    argv = [manifest['runtime_contract']['python'], '-B', '-I', manifest['source']['path'], 'worker',
            '--manifest', str(Path(manifest_path).resolve()), '--manifest-sha', manifest_sha,
            '--output', str(output / 'native')]
    # No inherited loader or Python search overrides reach the worker.
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONNOUSERSITE': '1', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
           'MKL_NUM_THREADS': '1'}
    previous = resource.getrusage(resource.RUSAGE_CHILDREN)
    last_cpu = 0.0
    peak_rss = 0
    stop_reason = None
    with (output / 'native.stdout').open('xb') as stdout, (output / 'native.stderr').open('xb') as stderr:
        # Defer cancellation across fork/handle assignment so that every started
        # child is owned before a signal can unwind to the cleanup path.
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            child = subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=env,
                                     start_new_session=True,
                                     preexec_fn=lambda: prepare_child(manifest, remaining, old_mask))
        except BaseException:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
            raise
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
            monitor = psutil.Process(child.pid)
            while child.poll() is None:
                members = [monitor] + monitor.children(recursive=True)
                if len(members) != 1:
                    raise RuntimeError('unexpected descendant process')
                cpu = 0.0
                rss = psutil.Process().memory_info().rss
                for process in members:
                    try:
                        times = process.cpu_times()
                        cpu += times.user + times.system
                        rss += process.memory_info().rss
                    except psutil.NoSuchProcess:
                        continue
                last_cpu = max(last_cpu, cpu)
                peak_rss = max(peak_rss, rss)
                own = resource.getrusage(resource.RUSAGE_SELF)
                if time.monotonic() - started >= 870:
                    raise RuntimeError('work deadline before cleanup reserve')
                if cpu + own.ru_utime + own.ru_stime - cpu_start >= LIMITS['cpu_seconds']:
                    raise RuntimeError('aggregate CPU budget exhausted')
                if rss > LIMITS['rss_bytes']:
                    raise RuntimeError('sampled RSS budget exceeded')
                if h.output_bytes(output) > LIMITS['output_bytes']:
                    raise RuntimeError('output budget exceeded')
                if h.available_memory() < LIMITS['memory_reserve_bytes']:
                    raise RuntimeError('memory reserve lost')
                if shutil.disk_usage(output).free < LIMITS['disk_reserve_bytes']:
                    raise RuntimeError('disk reserve lost')
                time.sleep(.1)
        except BaseException as exc:
            stop_reason = error_record(exc)
        finally:
            cleanup = cleanup_child(child, started + LIMITS['wall_seconds'])
    waited = resource.getrusage(resource.RUSAGE_CHILDREN)
    waited_cpu = waited.ru_utime + waited.ru_stime - previous.ru_utime - previous.ru_stime
    return dict(pid=child.pid, argv=argv, **cleanup, stop_reason=stop_reason,
                sampled_cpu_seconds=last_cpu, waited_cpu_seconds=waited_cpu,
                peak_sampled_owned_rss=peak_rss)


def validate_success(result, manifest, output):
    import numpy as np
    ids = [s['id'] for s in manifest['samples']]
    calls = len(ids) + 4
    if (result.get('status') != 'passed' or not result.get('terminal_integrity_passed')
            or result.get('planned_ids') != ids or result.get('completed_ids') != ids
            or result.get('unrun_ids') != [] or result.get('partition') != manifest['partition']
            or (result.get('calls_attempted'), result.get('calls_completed'), result.get('sessions_attempted'))
            != (calls, calls, 1)):
        raise ValueError('stage status, population or call/session accounting mismatch')
    expected_repeat_ids = [identity for identity in manifest['repeat_ids'] for _ in (1, 2)]
    if (len(result.get('repeat_metrics', [])) != 4
            or [r['id'] for r in result['repeat_metrics']] != expected_repeat_ids
            or [r['repeat'] for r in result['repeat_metrics']] != [1, 2, 1, 2]
            or not all(r['passed'] for r in result['repeat_metrics'])):
        raise ValueError('repeatability accounting mismatch')
    if (len(result.get('clips', [])) != len(ids)
            or [row.get('id') for row in result['clips']] != ids):
        raise ValueError('saved clip accounting mismatch')
    for row, sample in zip(result['clips'], manifest['samples']):
        if (row.get('status') != 'encoded' or row.get('wav_sha256') != sample['wav']['sha256']
                or row.get('pcm_sha256') != sample['pcm_sha256']
                or row.get('float32_sha256') != sample['float32_sha256']):
            raise ValueError('saved exact input hash binding mismatch')
    journal_path = output / 'native/journal.jsonl'
    if journal_path.stat().st_size > 2 * 1024**2:
        raise ValueError('oversized native call journal')
    events = [json.loads(line) for line in journal_path.read_text().splitlines()]
    expected_calls = [(identity, 0) for identity in ids] + [
        (identity, number) for identity in manifest['repeat_ids'] for number in (1, 2)]
    if ([e.get('event') for e in events]
            != ['model_construction_attempt', 'model_construction_complete']
               + [name for _ in expected_calls for name in ('encode_attempt', 'encode_complete')]
               + ['terminal'] or events[-1].get('status') != 'passed'):
        raise ValueError('incomplete or reordered native call journal')
    samples_by_id = {sample['id']: sample for sample in manifest['samples']}
    for number, (identity, repeat) in enumerate(expected_calls, 1):
        attempt, complete = events[2 * number:2 * number + 2]
        if (any((event.get('id'), event.get('repeat'), event.get('call')) != (identity, repeat, number)
                for event in (attempt, complete))
                or attempt.get('float32_sha256') != samples_by_id[identity]['float32_sha256']):
            raise ValueError('native journal call or exact input binding mismatch')
    h.verify_pin(result['vectors'])
    if result['vectors']['path'] != str((output / 'native/vectors.npz').resolve()):
        raise ValueError('unexpected vector artifact')
    with np.load(result['vectors']['path'], allow_pickle=False) as saved:
        if set(saved.files) != {'ids', 'raw', 'normalized', 'valid', 'repeat_raw', 'repeat_normalized', 'repeat_ids'}:
            raise ValueError('unexpected saved vector fields')
        if saved['ids'].tolist() != ids or saved['repeat_ids'].tolist() != expected_repeat_ids:
            raise ValueError('saved vector ID order mismatch')
        if saved['valid'].dtype != np.bool_ or saved['valid'].shape != (len(ids),) or not saved['valid'].all():
            raise ValueError('missing planned vectors')
        for name, count in [('raw', len(ids)), ('normalized', len(ids)), ('repeat_raw', 4), ('repeat_normalized', 4)]:
            h.require_array(saved[name], (count, 512))
        for index, (raw, norm) in enumerate(zip(saved['raw'], saved['normalized'])):
            if not h.bitwise_equal(h.producer_normalize(raw), norm):
                raise ValueError('saved producer normalization mismatch')
            row = result['clips'][index]
            if (row.get('raw_sha256') != hashlib.sha256(raw.tobytes()).hexdigest()
                    or row.get('normalized_sha256') != hashlib.sha256(norm.tobytes()).hexdigest()):
                raise ValueError('saved vector hash binding mismatch')
        for index, identity in enumerate(expected_repeat_ids):
            raw, norm = saved['repeat_raw'][index], saved['repeat_normalized'][index]
            if not h.bitwise_equal(h.producer_normalize(raw), norm):
                raise ValueError('saved repeat normalization mismatch')
            metric = h.vector_delta(saved['normalized'][ids.index(identity)], norm)
            metric.update(id=identity, repeat=index % 2 + 1)
            if metric != result['repeat_metrics'][index] or not metric['passed']:
                raise ValueError('saved repeatability evidence mismatch')


def run(args):
    started = time.monotonic()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu_start = usage.ru_utime + usage.ru_stime
    manifest = load_manifest(args.manifest, args.manifest_sha)
    output = Path(args.output).resolve()
    output.mkdir(exist_ok=False)  # Existing stage output is never reused or retried.
    ids = [s['id'] for s in manifest['samples']]
    final = {'schema': 1, 'partition': manifest['partition'], 'manifest_sha256': args.manifest_sha,
             'protocol_sha256': manifest['protocol_sha256'], 'release_pin': manifest['release_pin'],
             'status': 'failed', 'planned_ids': ids, 'planned_calls': len(ids) + 4,
             'completed_ids': [], 'unrun_ids': list(ids), 'stage': None, 'reason': None}
    cpu_done = 0.0
    def interrupted(signum, frame):
        raise InterruptedError('supervisor received signal ' + str(signum))
    previous_handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        verify_environment(manifest)
        stage = supervise_child(manifest, args.manifest, args.manifest_sha, output, started, cpu_start)
        final['stage'] = stage
        cpu_done = stage['waited_cpu_seconds']
        path = output / 'native/result.json'
        result = h.read_json(path) if path.exists() else None
        if result is not None:
            final['completed_ids'] = result.get('completed_ids', [])
            final['unrun_ids'] = [i for i in ids if i not in final['completed_ids']]
        if (stage['stop_reason'] or stage['returncode'] != 0 or stage['cleanup_error']
                or not stage['positive_original_wait'] or not stage['owned_process_group_absent']
                or stage['termination_needed'] or result is None):
            raise RuntimeError('native stage failed or cleanup uncertain: ' + str(stage['stop_reason'] or (result or {}).get('error')))
        if result.get('manifest_sha256') != args.manifest_sha:
            raise ValueError('worker manifest binding mismatch')
        validate_success(result, manifest, output)
        verify_environment(manifest)
        h.admission(output, started, cpu_done, cpu_start, False)
        final['status'] = 'completed_bounded_native_stage'
        final['artifacts'] = [h.pinned(output / 'native' / name)
                              for name in ('vectors.npz', 'result.json', 'journal.jsonl')]
    except BaseException as exc:
        final['reason'] = error_record(exc)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        final.update(elapsed_seconds=time.monotonic() - started, child_cpu_seconds=cpu_done,
                     aggregate_cpu_seconds=cpu_done + usage.ru_utime + usage.ru_stime - cpu_start,
                     allocated_output_bytes=h.output_bytes(output))
        if (final['elapsed_seconds'] > LIMITS['wall_seconds']
                or final['aggregate_cpu_seconds'] > LIMITS['cpu_seconds']
                or final['allocated_output_bytes'] + 65536 > LIMITS['output_bytes']):
            final['status'] = 'failed'
            final['reason'] = {'type': 'RuntimeError', 'message': 'terminal resource bound failed'}
        h.write_json(output / 'TERMINAL.json', final)
    return 0 if final['status'] == 'completed_bounded_native_stage' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('run', 'worker'):
        sub = commands.add_parser(command)
        for field in ('manifest', 'manifest-sha', 'output'):
            sub.add_argument('--' + field, required=True)
    args = parser.parse_args()
    return {'run': run, 'worker': worker}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
