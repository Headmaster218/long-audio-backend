"""Bounded research harness. No model is constructed on import/freeze/self-test.

Only an explicitly invoked run command starts inference. All paths are passed in
one frozen manifest. Production databases, certificates and gates are not used.
"""
from __future__ import annotations
import argparse
import ctypes
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import stat
import struct
import subprocess
import sys
import time
import traceback
import wave

MODEL_SHA = '1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b'
ORT_SHA = '4b3607aebd1784b26b6f9b20e4bd974c7ab8287043e4d095cb7d2cb40b5e566e'
BOUND_SHA = '3afb278301172b658db455a402b796a0df5c3d6f334ff93bde28fccbc649f53f'
PROFILES = {'N': (False, -400.0), 'H': (False, 0.0),
            'S': (True, -400.0), 'C': (True, 0.0)}
STAGES = ('native', 'bridge', 'candidate')
LIMITS = {'wall_seconds': 900, 'cleanup_seconds': 30, 'cpu_seconds': 600,
          'rss_bytes': 3 * 1024**3, 'output_bytes': 32 * 1024**2,
          'memory_reserve_bytes': 2 * 1024**3, 'disk_reserve_bytes': 5 * 1024**3,
          'calls_per_stage': 40, 'calls_total': 120, 'sessions_total': 3}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    data = canonical(value) + b'\n'
    with path.open('xb') as f:
        f.write(data)


def read_json(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 4 * 1024**2:
        raise ValueError('bounded JSON input unavailable')
    return json.loads(path.read_text())


def pinned(path):
    path = Path(path).resolve(strict=True)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('nonregular input')
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': digest_file(path)}


def verify_pin(pin):
    path = Path(pin['path'])
    if (path.resolve(strict=True) != path or not path.is_file()
            or path.stat().st_size != pin['bytes'] or digest_file(path) != pin['sha256']):
        raise ValueError('artifact pin mismatch: ' + str(path))


def pcm_bytes(sample):
    verify_pin(sample['wav'])
    with wave.open(sample['wav']['path'], 'rb') as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate(), f.getnframes(),
                f.getcomptype()) != (1, 2, 16000, 32000, 'NONE'):
            raise ValueError('fixed PCM geometry mismatch')
        data = f.readframes(32001)
    if len(data) != 64000 or hashlib.sha256(data).hexdigest() != sample['pcm_sha256']:
        raise ValueError('fixed PCM hash mismatch')
    return data


def verify_inputs(frozen, include_audio=True):
    for pin in frozen['artifact_pins']:
        verify_pin(pin)
    if include_audio:
        for sample in frozen['samples']:
            pcm_bytes(sample)


def freeze(args):
    import numpy
    import sherpa_onnx
    root, runtime = Path(args.preparation).resolve(), Path(args.runtime).resolve()
    if Path(sys.prefix).resolve()!=runtime:
        raise ValueError('freeze requires exact isolated interpreter')
    bound = pinned(args.samples)
    if bound['sha256'] != BOUND_SHA:
        raise ValueError('unreviewed sample binding')
    data = read_json(bound['path'])
    crops = data['crops']
    if len(crops) != 36 or [c['ordinal'] for c in crops] != list(range(1, 37)):
        raise ValueError('fixed population mismatch')
    sample_root = Path(bound['path']).parent
    samples, gallery, diagnostic_gallery, queries = [], [], [], []
    for c in crops:
        wav = pinned(sample_root / c['crop_relative_path'])
        if not Path(wav['path']).is_relative_to(sample_root) or wav['sha256'] != c['crop_wav_sha256']:
            raise ValueError('crop path or hash mismatch')
        sample = {'ordinal': c['ordinal'], 'wav': wav, 'pcm_sha256': c['crop_pcm_sha256']}
        pcm = pcm_bytes(sample)
        floats = [v[0] / 32768.0 for v in struct.iter_unpack('<h', pcm)]
        sample['float32_sha256'] = hashlib.sha256(struct.pack('<32000f', *floats)).hexdigest()
        samples.append(sample)
        if c['role'] == 'reference':
            diagnostic_gallery.append({'label': c['expected_gallery_label'], 'ordinal': c['ordinal']})
            if c['role_slot'] == 1:
                gallery.append({'label': c['expected_gallery_label'], 'ordinal': c['ordinal']})
        else:
            queries.append(c['ordinal'])
    if len(gallery) != 6 or len({g['label'] for g in gallery}) != 6 or len(queries) != 24:
        raise ValueError('gallery/query population mismatch')
    model = pinned(args.model)
    library = pinned(runtime / 'lib/python3.12/site-packages/sherpa_onnx/lib/libonnxruntime.so')
    if model['sha256'] != MODEL_SHA or library['sha256'] != ORT_SHA:
        raise ValueError('unreviewed encoder or backend')
    packages = runtime / 'lib/python3.12/site-packages'
    artifacts = [pinned(p) for p in sorted(packages.rglob('*')) if p.is_file()]
    artifacts += [model, pinned(__file__), pinned(root / 'ort_bridge.c'),
                  pinned(root / 'libort_bridge.so'), pinned(root / 'EXPERIMENT_PLAN.md'),
                  pinned(root / 'provenance/ort-c-api-v1.22.0.h'),
                  pinned(sys.executable),pinned(runtime/'pyvenv.cfg')]
    artifacts += [pinned(p) for p in sorted((root/'provenance').iterdir()) if p.is_file()]
    numerical_pins=[pinned(p) for p in numerical_library_paths()]
    artifacts += numerical_pins
    base_options = read_json(root / 'KNF_DEFAULT_OPTIONS.json')
    base_options['frame_opts']['dither'] = 0.0
    base_options['mel_opts']['num_bins'] = 80
    # Query truth is deliberately absent from samples/predictor input. The full
    # annotation file is opened only by the later labeled summary function.
    value = {'schema': 1, 'status': 'frozen_no_inference', 'model': model,
             'runtime': str(runtime), 'python': str(runtime / 'bin/python'),
             'library': library, 'bridge': pinned(root / 'libort_bridge.so'),
             'source': pinned(__file__), 'plan': pinned(root / 'EXPERIMENT_PLAN.md'),
             'evaluation_truth': bound, 'samples': samples,
             'predictor': {'gallery': gallery, 'queries': queries},
             'diagnostic_gallery': diagnostic_gallery, 'repeat_ordinals': [1, 31],
             'feature_options': base_options, 'artifact_pins': artifacts,
             'numerical_library_pins':numerical_pins,
             'environment_boundary':'Same-host CP312 experiment. Interpreter, venv, runtime packages, mapped numerical libraries, helper/model/source evidence are pinned; not a complete OS/standard-library or reproducible-build proof.',
             'limits': LIMITS, 'profiles': PROFILES,
             'affinity_cpus': sorted(os.sched_getaffinity(0))[:2]}
    if len(value['affinity_cpus']) != 2:
        raise ValueError('two affinity CPUs unavailable')
    write_json(args.output, value)
    print(json.dumps({'freeze': str(Path(args.output).resolve()),
                      'sha256': digest_file(args.output), 'model_calls': 0}))


def normal64(raw):
    if len(raw) != 512 or any(not math.isfinite(v) for v in raw):
        raise ValueError('invalid vector')
    scale = max(abs(v) for v in raw)
    if scale <= 1e-30:
        raise ValueError('zero vector')
    norm = math.sqrt(math.fsum((v / scale) ** 2 for v in raw))
    return [(v / scale) / norm for v in raw]


def match_score(reference, query):
    # Exact single-reference specialization of normalized_vector/centroid_score:
    # stored reference/query normalization, then normalized one-item centroid.
    reference, query = normal64(reference), normal64(query)
    centroid = normal64([math.fsum([v]) for v in reference])
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(centroid, query))))


def predict(predictor, vectors):
    if set(predictor) != {'gallery', 'queries'}:
        raise ValueError('unexpected predictor input, including query labels')
    if len(predictor['gallery']) != 6 or len(predictor['queries']) != 24:
        raise ValueError('fixed predictor population mismatch')
    rows = []
    for ordinal in predictor['queries']:
        scores = [(match_score(vectors[g['ordinal'] - 1].tolist(), vectors[ordinal - 1].tolist()),
                   g['label']) for g in predictor['gallery']]
        scores.sort(key=lambda x: (-x[0], x[1]))
        best, winner = scores[0]
        margin = best - scores[1][0]
        reason = ('unknown_below_threshold' if best < 0.75 else
                  'unknown_ambiguous' if margin < 0.10 else 'experimental_identity_match')
        rows.append({'ordinal': ordinal, 'winner': winner, 'score': best, 'margin': margin,
                     'status': reason, 'emitted': winner if reason == 'experimental_identity_match' else None,
                     'scores': [{'label': label, 'score': score} for score, label in scores]})
    return rows


def bitwise_equal(a,b):
    import numpy as np
    x,y=np.asarray(a),np.asarray(b)
    return x.shape==y.shape and x.dtype==y.dtype and x.tobytes(order='C')==y.tobytes(order='C')


def vector_delta(a, b):
    import numpy as np
    x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if x.shape != (512,) or y.shape != (512,) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('invalid vector comparison')
    d = x - y
    nx = math.sqrt(math.fsum(float(v) ** 2 for v in x))
    ny = math.sqrt(math.fsum(float(v) ** 2 for v in y))
    if nx == 0 or ny == 0:
        raise ValueError('zero comparison norm')
    cosine = max(-1.0, min(1.0, math.fsum(float(u) * float(v) for u, v in zip(x, y)) / (nx * ny)))
    out = {'bitwise_equal': bitwise_equal(a, b), 'max_absolute': float(np.max(np.abs(d))),
           'l2': math.sqrt(math.fsum(float(v) ** 2 for v in d)), 'cosine': cosine,
           'cosine_gap': 1.0 - cosine, 'a_norm': nx, 'b_norm': ny}
    out['passed'] = out['max_absolute'] <= 1e-5 and out['l2'] <= 1e-4 and out['cosine_gap'] <= 1e-7
    return out


def feature_delta(a, b, gate=False):
    import numpy as np
    if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('unaligned or nonfinite feature comparison')
    x, y = a.astype(np.float64), b.astype(np.float64)
    delta = y - x
    out = {'shape': list(a.shape), 'bitwise_equal': bitwise_equal(a, b),
           'max_absolute': float(np.max(np.abs(delta))), 'rmse': float(np.sqrt(np.mean(delta * delta))),
           'a_bin_mean': x.mean(axis=0).tolist(), 'b_bin_mean': y.mean(axis=0).tolist(),
           'a_bin_std': x.std(axis=0).tolist(), 'b_bin_std': y.std(axis=0).tolist()}
    if gate:
        out['passed'] = bool(np.all(np.abs(delta) <= 2e-5 + 1e-6 * np.abs(x)))
    return out


def producer_normalize(raw):
    import numpy as np
    a = np.asarray(raw, dtype=np.float32)
    if a.shape != (512,) or not np.isfinite(a).all():
        raise ValueError('invalid producer output')
    norm = np.linalg.norm(a)
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError('invalid producer norm')
    return np.ascontiguousarray(a / norm, dtype=np.float32)


def require_array(value, shape):
    import numpy as np
    if not isinstance(value,np.ndarray) or value.shape != shape or value.dtype != np.float32 or not np.isfinite(value).all():
        raise ValueError('incomplete or invalid fixed array: expected '+repr(shape))
    return value


class CBridge:
    def __init__(self, frozen):
        verify_pin(frozen['bridge']); verify_pin(frozen['library']); verify_pin(frozen['model'])
        self.handle = None
        self.lib = ctypes.CDLL(frozen['bridge']['path'])
        fp = ctypes.POINTER(ctypes.c_float)
        self.lib.bridge_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
        self.lib.bridge_open.restype = ctypes.c_void_p
        self.lib.bridge_run.argtypes = [ctypes.c_void_p, fp, ctypes.c_int64, fp, ctypes.c_char_p, ctypes.c_size_t]
        self.lib.bridge_run.restype = ctypes.c_int
        self.lib.bridge_close.argtypes = [ctypes.c_void_p]
        self.lib.bridge_close.restype = None
        self.error = ctypes.create_string_buffer(2048)
        self.handle = self.lib.bridge_open(frozen['library']['path'].encode(), frozen['model']['path'].encode(),
                                           self.error, len(self.error))
        if not self.handle:
            raise RuntimeError(self.error.value.decode(errors='replace'))

    def run(self, features):
        import numpy as np
        if (not isinstance(features, np.ndarray) or features.dtype != np.float32
                or features.shape not in ((198, 80), (200, 80))
                or not features.flags.c_contiguous or not features.flags.writeable
                or not np.isfinite(features).all() or not self.handle):
            raise ValueError('invalid bridge buffer')
        output = np.empty(512, dtype=np.float32)
        fp = ctypes.POINTER(ctypes.c_float)
        rc = self.lib.bridge_run(self.handle, features.ctypes.data_as(fp), features.shape[0],
                                 output.ctypes.data_as(fp), self.error, len(self.error))
        if rc:
            raise RuntimeError(self.error.value.decode(errors='replace'))
        if not np.isfinite(output).all():
            raise ValueError('invalid bridge output')
        return output

    def close(self):
        if self.handle:
            self.lib.bridge_close(self.handle)
            self.handle = None


def explicit_features(waveform, profile, options):
    import numpy as np
    import kaldi_native_fbank as knf
    opts = knf.FbankOptions()
    for key, value in options.items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                setattr(getattr(opts, key), subkey, subvalue)
        else:
            setattr(opts, key, value)
    opts.frame_opts.snip_edges, opts.mel_opts.high_freq = PROFILES[profile]
    fbank = knf.OnlineFbank(opts)
    fbank.accept_waveform(16000, waveform)
    fbank.input_finished()
    expected = 198 if PROFILES[profile][0] else 200
    if fbank.num_frames_ready != expected:
        raise ValueError('unexpected feature geometry')
    features = np.stack([fbank.get_frame(i) for i in range(expected)]).astype(np.float32)
    if features.shape != (expected, 80) or not np.isfinite(features).all():
        raise ValueError('invalid explicit features')
    return features, opts.as_dict()


def loaded_ort(frozen):
    maps = Path('/proc/self/maps').read_text()
    paths = sorted(set(line.split()[-1] for line in maps.splitlines() if 'libonnxruntime.so' in line))
    if paths != [frozen['library']['path']]:
        raise ValueError('loaded ORT path mismatch: ' + repr(paths))
    verify_pin(frozen['library'])
    if numerical_library_paths() != sorted(p['path'] for p in frozen['numerical_library_pins']):
        raise ValueError('loaded numerical library path set mismatch')
    return paths


def numerical_library_paths():
    needles=('libm.so','libstdc++','libgcc','libc.so','ld-linux','libgfortran',
             'libquadmath','openblas','libgomp','libonnxruntime')
    paths=set()
    for line in Path('/proc/self/maps').read_text().splitlines():
        path=line.split()[-1]
        if path.startswith('/') and any(n in Path(path).name for n in needles):
            paths.add(str(Path(path).resolve(strict=True)))
    return sorted(paths)


def worker(args):
    import numpy as np
    if digest_file(args.freeze) != args.freeze_sha:
        raise ValueError('freeze hash mismatch')
    frozen = read_json(args.freeze)
    if Path(sys.prefix).resolve() != Path(frozen['runtime']).resolve():
        raise ValueError('wrong isolated interpreter prefix')
    verify_inputs(frozen)
    output = Path(args.output); output.mkdir(exist_ok=False)
    result = {'stage': args.stage, 'status': 'failed', 'planned_slots': 36,
              'calls_attempted': 0, 'calls_completed': 0, 'sessions_attempted': 0,
              'completed_ordinals': [], 'unrun_ordinals': list(range(1, 37)),
              'repeat_metrics': [], 'error': None}
    journal = (output / 'journal.jsonl').open('x', buffering=1)
    def event(value):
        journal.write(canonical(value).decode() + '\n'); journal.flush()
    raw = np.full((36, 512), np.nan, dtype=np.float32)
    normalized = raw.copy(); repeats = []; model = None; features = {}
    try:
        waveforms = [np.frombuffer(pcm_bytes(s), dtype='<i2').astype(np.float32) / np.float32(32768)
                     for s in frozen['samples']]
        result['float32_input_hashes'] = [hashlib.sha256(w.tobytes()).hexdigest() for w in waveforms]
        if result['float32_input_hashes'] != [s['float32_sha256'] for s in frozen['samples']]:
            raise ValueError('PCM-to-float32 input bridge mismatch')
        if args.stage == 'bridge':
            with np.load(output.parent / 'native/vectors.npz', allow_pickle=False) as saved:
                native_features = saved['features'].copy()
                native_vectors = saved['normalized'].copy()
            require_array(native_features,(36,200,80))
            require_array(native_vectors,(36,512))
            for name in PROFILES:
                rows = []
                for waveform in waveforms:
                    x, opts = explicit_features(waveform, name, frozen['feature_options']); rows.append(x)
                features[name] = np.stack(rows)
                result.setdefault('effective_options', {})[name] = opts
            np.savez(output / 'features.npz', **features)
            result['feature_parity'] = [feature_delta(a, b, True)
                                        for a, b in zip(native_features, features['N'])]
            result['frequency_factor_N_H'] = [feature_delta(a, b) for a, b in zip(features['N'], features['H'])]
            result['frequency_factor_S_C'] = [feature_delta(a, b) for a, b in zip(features['S'], features['C'])]
            if len(result['feature_parity']) != 36 or not all(row['passed'] for row in result['feature_parity']):
                raise RuntimeError('native feature bridge gate failed before explicit-model construction')
        elif args.stage == 'candidate':
            gate = read_json(output.parent / 'BRIDGE_GATE.json')
            if not gate['passed'] or gate['freeze_sha256'] != args.freeze_sha:
                raise ValueError('candidate bridge gate absent')
            for pin in gate['pins']:
                verify_pin(pin)
            with np.load(output.parent / 'bridge/features.npz', allow_pickle=False) as saved:
                features['C'] = saved['C'].copy()
            require_array(features['C'],(36,198,80))
        event({'event': 'model_construction_attempt', 'stage': args.stage})
        result['sessions_attempted'] = 1
        if args.stage == 'native':
            import sherpa_onnx
            model = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=frozen['model']['path'], num_threads=2, provider='cpu', debug=False))
            if model.dim != 512:
                raise ValueError('native dimension mismatch')
            native_rows = []
        else:
            model = CBridge(frozen)
        result['loaded_ort_paths'] = loaded_ort(frozen)
        def encode(ordinal, repeat_index=0):
            if result['calls_attempted'] >= 40:
                raise RuntimeError('fixed call cap')
            result['calls_attempted'] += 1
            event({'event': 'encode_attempt', 'ordinal': ordinal, 'repeat': repeat_index,
                   'call': result['calls_attempted']})
            if args.stage == 'native':
                stream = model.create_stream()
                stream.accept_waveform(16000, waveforms[ordinal - 1])
                stream.input_finished()
                if not model.is_ready(stream):
                    raise ValueError('native stream not ready')
                native_feature = np.asarray(stream.get_frames(0, 200), dtype=np.float32).reshape(200, 80)
                if not np.isfinite(native_feature).all():
                    raise ValueError('invalid native feature')
                if not repeat_index:
                    native_rows.append(native_feature)
                value = np.asarray(model.compute(stream), dtype=np.float32)
            else:
                name = 'N' if args.stage == 'bridge' else 'C'
                centered = np.array(features[name][ordinal - 1], dtype=np.float32, order='C', copy=True)
                centered -= centered.mean(axis=0, keepdims=True)
                value = model.run(centered)
            norm = producer_normalize(value)
            result['calls_completed'] += 1
            event({'event': 'encode_complete', 'ordinal': ordinal, 'repeat': repeat_index})
            return value, norm
        for ordinal in range(1, 37):
            raw[ordinal - 1], normalized[ordinal - 1] = encode(ordinal)
            result['completed_ordinals'].append(ordinal)
            result['unrun_ordinals'].remove(ordinal)
        for ordinal in frozen['repeat_ordinals']:
            for number in (1, 2):
                r, n = encode(ordinal, number); repeats.append(n)
                metrics = vector_delta(normalized[ordinal - 1], n)
                metrics.update(ordinal=ordinal, repeat=number)
                result['repeat_metrics'].append(metrics)
        if not all(row['passed'] for row in result['repeat_metrics']):
            raise RuntimeError('repeatability gate failed')
        if args.stage == 'bridge':
            result['vector_parity'] = [vector_delta(a, b) for a, b in zip(native_vectors, normalized)]
            if len(result['vector_parity']) != 36 or not all(row['passed'] for row in result['vector_parity']):
                raise RuntimeError('native embedding bridge gate failed')
        write_json(output / 'predictions.json', predict(frozen['predictor'], normalized))
        result['per_crop_vectors'] = [{'ordinal': i+1,
            'raw_sha256': hashlib.sha256(raw[i].tobytes()).hexdigest(),
            'normalized_sha256': hashlib.sha256(normalized[i].tobytes()).hexdigest(),
            'raw_norm64': math.sqrt(math.fsum(float(v)**2 for v in raw[i])),
            'normalized_norm64': math.sqrt(math.fsum(float(v)**2 for v in normalized[i]))}
            for i in range(36)]
        result['status'] = 'passed'
    except BaseException as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
    finally:
        if isinstance(model, CBridge):
            model.close()
        model = None; gc.collect()
        kwargs = {'raw': raw, 'normalized': normalized, 'repeats': np.asarray(repeats, dtype=np.float32)}
        if args.stage == 'native':
            kwargs['features'] = np.asarray(locals().get('native_rows', []), dtype=np.float32)
        np.savez(output / 'vectors.npz', **kwargs)
        try:
            verify_inputs(frozen)
            result['terminal_integrity_passed'] = True
        except BaseException as exc:
            result['terminal_integrity_passed'] = False; result['status'] = 'failed'
            result['terminal_integrity_error'] = str(exc)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result['cpu_seconds'] = usage.ru_utime + usage.ru_stime
        result['maxrss_kib'] = usage.ru_maxrss
        event({'event': 'terminal', 'status': result['status']}); journal.close()
        write_json(output / 'result.json', result)
    return 0 if result['status'] == 'passed' else 1


def output_bytes(root):
    total = 0
    for path in Path(root).rglob('*'):
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise ValueError('output symlink refused')
        if stat.S_ISREG(st.st_mode):
            total += st.st_blocks * 512
    return total


def available_memory():
    import psutil
    available = psutil.virtual_memory().available
    root = Path('/sys/fs/cgroup')
    try:
        cap = (root / 'memory.max').read_text().strip()
        if cap != 'max':
            available = min(available, int(cap) - int((root / 'memory.current').read_text()))
    except (OSError, ValueError):
        pass
    return available


def group_present(pid):
    try:
        os.killpg(pid,0)
        return True
    except ProcessLookupError:
        return False


def cleanup_owned(child, absolute_deadline, cleanup_state):
    """Reap the original handle and clear its owned group, including an exited leader."""
    code = child.poll()
    needed = code is None or group_present(child.pid)
    error = None; waited = False
    if needed:
        if cleanup_state.get('deadline') is None:
            cleanup_state['deadline'] = min(absolute_deadline,time.monotonic()+30)
        deadline = cleanup_state['deadline']
        try:
            if group_present(child.pid):
                os.killpg(child.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        except OSError as exc: error='owned-group SIGTERM failed: '+str(exc)
        grace = min(deadline,time.monotonic()+5)
        while time.monotonic() < grace:
            child.poll()
            if not group_present(child.pid): break
            time.sleep(.05)
        if group_present(child.pid):
            try: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            except OSError as exc: error='owned-group SIGKILL failed: '+str(exc)
    else:
        deadline = absolute_deadline
    try:
        code = child.wait(timeout=max(.01,deadline-time.monotonic()))
        waited = True
    except subprocess.TimeoutExpired:
        error = 'original child wait remained unavailable at cleanup deadline'
    while group_present(child.pid) and time.monotonic() < deadline:
        time.sleep(.05)
    absent = not group_present(child.pid)
    if not absent:
        error = 'owned process group remains at shared cleanup deadline'
    return {'returncode':code,'positive_original_wait':waited,
            'owned_process_group_absent':absent,'termination_needed':needed,'cleanup_error':error}


def admission(output,started,cpu_done,supervisor_cpu_start,starting):
    import shutil
    own=resource.getrusage(resource.RUSAGE_SELF)
    if time.monotonic()-started >= 870: raise RuntimeError('work deadline before cleanup reserve')
    if cpu_done+own.ru_utime+own.ru_stime-supervisor_cpu_start >= 600: raise RuntimeError('aggregate CPU budget exhausted')
    allocated=output_bytes(output)
    if allocated > LIMITS['output_bytes']: raise RuntimeError('output budget exceeded')
    reserve=LIMITS['memory_reserve_bytes']+(LIMITS['rss_bytes'] if starting else 0)
    if available_memory() < reserve: raise RuntimeError('memory reserve admission failed')
    disk=LIMITS['disk_reserve_bytes']+(LIMITS['output_bytes']-allocated if starting else 0)
    if shutil.disk_usage(output).free < disk: raise RuntimeError('disk reserve admission failed')


def bridge_decisions(native, bridge):
    if len(native) != 24 or len(bridge) != 24:
        return False
    return all((a['ordinal'], a['winner'], a['status'], a['emitted']) ==
               (b['ordinal'], b['winner'], b['status'], b['emitted'])
               for a, b in zip(native, bridge))


def validate_predictions(rows, predictor):
    expected = predictor['queries']
    labels = {g['label'] for g in predictor['gallery']}
    if (len(expected) != 24 or len(set(expected)) != 24 or len(labels) != 6
            or len(rows) != 24 or [row['ordinal'] for row in rows] != expected):
        raise ValueError('incomplete, duplicate, or reordered prediction population')
    for row in rows:
        scores = row['scores']
        if (len(scores) != 6 or {s['label'] for s in scores} != labels
                or any(type(s['score']) not in (float,int) or not math.isfinite(s['score'])
                       or not -1 <= s['score'] <= 1 for s in scores)):
            raise ValueError('missing, duplicated, or invalid rival score')
        ranked = sorted(scores, key=lambda s: (-s['score'],s['label']))
        best, winner = ranked[0]['score'],ranked[0]['label']
        margin = best-ranked[1]['score']
        status = ('unknown_below_threshold' if best < .75 else
                  'unknown_ambiguous' if margin < .10 else 'experimental_identity_match')
        if (scores != ranked or row['winner'] != winner or row['score'] != best
                or row['margin'] != margin or row['status'] != status
                or row['emitted'] != (winner if status=='experimental_identity_match' else None)):
            raise ValueError('prediction differs from fixed decision rule')


def summarize(frozen, output):
    import numpy as np
    # This function is called only after PREDICTION_SEAL.json exists and verifies.
    seal = read_json(output / 'PREDICTION_SEAL.json')
    for pin in seal['pins']:
        verify_pin(pin)
    verify_pin(frozen['evaluation_truth'])
    truth = {c['ordinal']: c for c in read_json(frozen['evaluation_truth']['path'])['crops']}
    expected_labels = {i:(None if truth[i]['expected_gallery_label']=='unknown'
                         else truth[i]['expected_gallery_label']) for i in frozen['predictor']['queries']}
    gallery_labels={g['label'] for g in frozen['predictor']['gallery']}
    if (sum(v is None for v in expected_labels.values()) != 6
            or sum(v in gallery_labels for v in expected_labels.values()) != 18):
        raise ValueError('fixed known/unknown truth population mismatch')
    summary = {'status': 'bounded_technical_result_not_production_qualification',
               'counts_are_dependent': True, 'human_blinding_claim': False, 'arms': {}}
    for stage in ('native', 'candidate'):
        predictions = read_json(output / stage / 'predictions.json')
        validate_predictions(predictions, frozen['predictor'])
        counts = {'known_correct': 0, 'known_wrong': 0, 'known_abstained': 0,
                  'unknown_false_accept': 0, 'unknown_abstained': 0, 'emissions': 0,
                  'known_unavailable': 0, 'unknown_unavailable': 0,
                  'known_queries': 18, 'unknown_queries': 6, 'planned_queries': 24}
        rows = []
        for row in predictions:
            expected = expected_labels[row['ordinal']]
            if row['emitted'] is not None:
                counts['emissions'] += 1
            if expected is None:
                counts['unknown_false_accept' if row['emitted'] else 'unknown_abstained'] += 1
            elif row['emitted'] is None:
                counts['known_abstained'] += 1
            elif row['emitted'] == expected:
                counts['known_correct'] += 1
            else:
                counts['known_wrong'] += 1
            out = dict(row, expected=expected)
            if expected is not None:
                true_score = next(s['score'] for s in row['scores'] if s['label'] == expected)
                rival = max(s['score'] for s in row['scores'] if s['label'] != expected)
                out.update(true_score=true_score, best_rival=rival, true_margin=true_score-rival,
                           true_rank=next(i+1 for i,s in enumerate(row['scores']) if s['label'] == expected))
            rows.append(out)
        counts['emission_precision'] = (counts['known_correct']/counts['emissions'] if counts['emissions'] else None)
        counts['coverage'] = counts['emissions']/24
        pairs = []
        with np.load(output / stage / 'vectors.npz', allow_pickle=False) as saved:
            vectors = saved['normalized']
            if vectors.shape != (36,512) or vectors.dtype != np.float32 or not np.isfinite(vectors).all():
                raise ValueError('complete summary requires every fixed vector')
            for ordinal in frozen['predictor']['queries']:
                for g in frozen['diagnostic_gallery']:
                    pairs.append({'query_ordinal': ordinal, 'reference_ordinal': g['ordinal'],
                                  'same': truth[ordinal]['speaker_id'] == truth[g['ordinal']]['speaker_id'],
                                  'primary': truth[g['ordinal']]['role_slot'] == 1,
                                  'score': match_score(vectors[g['ordinal']-1].tolist(), vectors[ordinal-1].tolist())})
        summary['arms'][stage] = {'counts': counts, 'rows': rows, 'diagnostic_pairs': pairs}
    summary['paired_transitions'] = [{'ordinal': a['ordinal'], 'native': a['status'],
        'candidate': b['status'], 'native_emitted': a['emitted'], 'candidate_emitted': b['emitted']}
        for a,b in zip(summary['arms']['native']['rows'], summary['arms']['candidate']['rows'])]
    write_json(output / 'SUMMARY.json', summary)


def run(args):
    import psutil
    import shutil
    started = time.monotonic()
    supervisor_usage = resource.getrusage(resource.RUSAGE_SELF)
    supervisor_cpu_start = supervisor_usage.ru_utime + supervisor_usage.ru_stime
    if digest_file(args.freeze) != args.freeze_sha:
        raise ValueError('freeze hash mismatch')
    frozen = read_json(args.freeze)
    if Path(sys.prefix).resolve() != Path(frozen['runtime']).resolve():
        raise ValueError('wrong isolated supervisor interpreter prefix')
    verify_inputs(frozen)
    if frozen['limits'] != LIMITS:
        raise ValueError('unreviewed resource limits')
    output = Path(args.output).resolve(); output.mkdir(exist_ok=False)
    cpu_done = 0.0; records = []; peak_rss = 0
    cleanup_state = {'deadline':None}
    final = {'status': 'failed', 'freeze_sha256': args.freeze_sha, 'stages': records,
             'planned_queries': 24, 'planned_slots_per_stage': 36, 'reason': None}
    try:
        for stage in STAGES:
            admission(output,started,cpu_done,supervisor_cpu_start,True)
            child = None
            previous_children = resource.getrusage(resource.RUSAGE_CHILDREN)
            env = dict(os.environ)
            for key in ('PYTHONPATH','PYTHONHOME','LD_PRELOAD','LD_LIBRARY_PATH'):
                env.pop(key, None)
            env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                       OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
            def prepare_child():
                os.sched_setaffinity(0, frozen['affinity_cpus']); os.nice(19)
                resource.setrlimit(resource.RLIMIT_CPU, (max(1, math.ceil(600-cpu_done)), max(1, math.ceil(600-cpu_done))))
                resource.setrlimit(resource.RLIMIT_FSIZE, (LIMITS['output_bytes'], LIMITS['output_bytes']))
                resource.setrlimit(resource.RLIMIT_CORE, (0,0))
            argv = [frozen['python'], '-B', '-I', frozen['source']['path'], 'worker',
                    '--stage', stage, '--freeze', str(Path(args.freeze).resolve()),
                    '--freeze-sha', args.freeze_sha, '--output', str(output/stage)]
            with (output/(stage+'.stdout')).open('xb') as stdout, (output/(stage+'.stderr')).open('xb') as stderr:
                child = subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=env,
                                         start_new_session=True, preexec_fn=prepare_child)
                last_cpu = 0.0; stop_reason = None
                try:
                    monitor = psutil.Process(child.pid)
                    while child.poll() is None:
                        members = [monitor] + monitor.children(recursive=True)
                        if len(members) != 1:
                            raise RuntimeError('unexpected descendant process')
                        cpu = 0.0; rss = psutil.Process().memory_info().rss
                        for process in members:
                            try:
                                times = process.cpu_times(); cpu += times.user+times.system
                                rss += process.memory_info().rss
                            except psutil.NoSuchProcess:
                                continue
                        last_cpu = max(last_cpu,cpu); peak_rss = max(peak_rss,rss)
                        own = resource.getrusage(resource.RUSAGE_SELF)
                        total_cpu = cpu_done+cpu+own.ru_utime+own.ru_stime-supervisor_cpu_start
                        elapsed = time.monotonic()-started
                        if elapsed > 870: raise RuntimeError('work deadline before cleanup reserve')
                        if total_cpu > 600: raise RuntimeError('aggregate CPU budget exceeded')
                        if rss > LIMITS['rss_bytes']: raise RuntimeError('sampled RSS budget exceeded')
                        if output_bytes(output) > LIMITS['output_bytes']: raise RuntimeError('output budget exceeded')
                        if available_memory() < LIMITS['memory_reserve_bytes']: raise RuntimeError('memory reserve lost')
                        if shutil.disk_usage(output).free < LIMITS['disk_reserve_bytes']: raise RuntimeError('disk reserve lost')
                        time.sleep(0.1)
                except BaseException as exc:
                    stop_reason = str(exc)
                finally:
                    cleanup = cleanup_owned(child,started+900,cleanup_state)
                    code = cleanup['returncode']
                current_children = resource.getrusage(resource.RUSAGE_CHILDREN)
                waited_cpu = (current_children.ru_utime + current_children.ru_stime
                              - previous_children.ru_utime - previous_children.ru_stime)
                record = {'stage': stage, 'pid': child.pid, 'argv': argv, 'returncode': code,
                          **cleanup, 'sampled_cpu_seconds': last_cpu,
                          'waited_cpu_seconds': waited_cpu,
                          'stop_reason': stop_reason}
                records.append(record)
                path = output/stage/'result.json'
                result = read_json(path) if path.exists() else None
                cpu_done += waited_cpu
                if (stop_reason or code != 0 or not cleanup['owned_process_group_absent']
                        or not cleanup['positive_original_wait'] or cleanup['termination_needed']
                        or result is None or result['status'] != 'passed'):
                    raise RuntimeError('stage failed: '+stage+': '+str(stop_reason or (result or {}).get('error')))
                if (result['calls_attempted'],result['calls_completed'],result['sessions_attempted']) != (40,40,1):
                    raise RuntimeError('stage call/session accounting mismatch')
                admission(output,started,cpu_done,supervisor_cpu_start,False)
            if stage == 'bridge':
                a = read_json(output/'native/predictions.json'); b = read_json(output/'bridge/predictions.json')
                passed = bridge_decisions(a,b)
                write_json(output/'BRIDGE_GATE.json', {'passed': passed, 'freeze_sha256': args.freeze_sha,
                    'pins': [pinned(output/name/file) for name in ('native','bridge')
                             for file in ('vectors.npz','predictions.json','result.json')] + [pinned(output/'bridge/features.npz')]})
                if not passed:
                    raise RuntimeError('native versus explicit decision bridge failed')
        verify_inputs(frozen)
        write_json(output/'PREDICTION_SEAL.json', {'freeze_sha256': args.freeze_sha,
            'pins': [pinned(output/stage/file) for stage in STAGES
                     for file in ('vectors.npz','predictions.json','result.json')]})
        summarize(frozen,output)
        own = resource.getrusage(resource.RUSAGE_SELF)
        if (time.monotonic()-started > 900
                or cpu_done+own.ru_utime+own.ru_stime-supervisor_cpu_start > 600
                or output_bytes(output) > LIMITS['output_bytes']):
            raise RuntimeError('terminal resource bound failed')
        final['status'] = 'completed_bounded_technical_experiment'
    except BaseException as exc:
        final['reason'] = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
    finally:
        final.update(elapsed_seconds=time.monotonic()-started, child_cpu_seconds=cpu_done,
                     peak_sampled_owned_rss=peak_rss, allocated_output_bytes=output_bytes(output))
        write_json(output/'TERMINAL.json', final)
    return 0 if final['status'] == 'completed_bounded_technical_experiment' else 1


def main():
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('freeze')
    for name in ('preparation','runtime','samples','model','output'): p.add_argument('--'+name, required=True)
    for name in ('run','worker'):
        p = commands.add_parser(name)
        for field in ('freeze','freeze-sha','output'): p.add_argument('--'+field, required=True)
        if name == 'worker': p.add_argument('--stage', choices=STAGES, required=True)
    args = parser.parse_args()
    return {'freeze':freeze,'worker':worker,'run':run}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
