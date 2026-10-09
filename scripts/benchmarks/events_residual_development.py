#!/usr/bin/env python3
"""One immutable development-only DFN3 residual/context experiment.

No production imports load a model until timing passes. Assets and media remain
private; this helper never downloads, modifies original data, or reads holdouts.
The resource guard is sampled, not an OS-enforced memory guarantee.
"""
from __future__ import annotations

import argparse
import configparser
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PROTOCOL = ROOT / 'docs/research/EVENT_RESIDUAL_DEVELOPMENT_PROTOCOL_v1.json'
PROTOCOL_SHA256 = 'f234afed2f7738ef79cc84befc7526bd885d7cc6ab28bd425137c9c7661559fa'
SELF = Path(__file__).resolve()
TEST = ROOT / 'tests/test_events_residual_development.py'
GIB = 1024 ** 3
THREAD_ENV = {k: '1' for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'RAYON_NUM_THREADS')}
FILTER = 'aresample=resampler=swr:filter_size=32:phase_shift=10:linear_interp=1:exact_rational=1:cutoff=0.97:dither_method=none'
TARGETS = {'driving', 'subway'}


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def dump(path, data):
    path = Path(path)
    if path.exists():
        raise RuntimeError('Refusing to overwrite evidence: ' + path.name)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def samples_for(row):
    import numpy as np
    path = ROOT / row['path']
    if digest(path) != row['file_sha256']:
        raise RuntimeError('Input file hash mismatch: ' + row['id'])
    if path.suffix == '.npy':
        a = np.load(path, allow_pickle=False)
    else:
        with wave.open(str(path)) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
                raise RuntimeError('Unexpected original WAV format')
            a = np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(np.float32) / 32768
    if a.dtype != np.float32 or a.ndim != 1 or len(a) != row['num_samples'] or not np.isfinite(a).all():
        raise RuntimeError('Malformed waveform: ' + row['id'])
    if hashlib.sha256(a.tobytes()).hexdigest() != row['waveform_sha256']:
        raise RuntimeError('Waveform hash mismatch: ' + row['id'])
    return a


def geometry(archive, source_dir):
    expected = {'tmp/export/enc.onnx', 'tmp/export/erb_dec.onnx', 'tmp/export/df_dec.onnx', 'tmp/export/config.ini'}
    if Path(archive).stat().st_size > 16 * 1024 ** 2:
        raise RuntimeError('Archive input size cap exceeded')
    content = {}
    expanded = 0
    # Streaming iteration rejects header sizes before reading or seeking payloads.
    with tarfile.open(archive, mode='r|gz') as tar:
        for member in tar:
            if len(content) >= 4 or member.name not in expected or member.name in content or not member.isfile():
                raise RuntimeError('Unexpected archive contents or model-specific terms')
            expanded += member.size
            member_cap = 64 * 1024 if member.name.endswith('.ini') else 16 * 1024 ** 2
            if member.size < 0 or member.size > member_cap or expanded > 32 * 1024 ** 2:
                raise RuntimeError('Archive expanded/member size cap exceeded')
            content[member.name] = tar.extractfile(member).read(member.size + 1)
            if len(content[member.name]) != member.size:
                raise RuntimeError('Archive member length mismatch')
    if set(content) != expected:
        raise RuntimeError('Unexpected archive contents or model-specific terms')
    config = configparser.ConfigParser()
    config.read_string(content['tmp/export/config.ini'].decode())
    if config['train']['model'] != 'deepfilternet3':
        raise RuntimeError('Wrong model type')
    sr, fft, hop = (config.getint('df', k) for k in ('sr', 'fft_size', 'hop_size'))
    lookahead = max(config.getint('deepfilternet', 'conv_lookahead'), config.getint('df', 'df_lookahead'))
    if sr != 48000 or not 0 < hop <= fft or lookahead < 0:
        raise RuntimeError('Invalid model geometry')
    source = (source_dir / 'enhance_wav.rs').read_text()
    tract = (source_dir / 'tract.rs').read_text()
    writer = (source_dir / 'wav_utils.rs').read_text()
    for exact, text in [('let mut delay = model.fft_size - model.hop_size;', source),
                        ('delay += model.lookahead * model.hop_size;', source),
                        ('"deepfilternet3" => conv_lookahead.max(df_lookahead)', tract),
                        ('(ch[t] * i16::MAX as f32) as i16', writer)]:
        if exact not in text:
            raise RuntimeError('Unknown upstream timing/quantization semantics')
    return dict(sample_rate=sr, fft_size=fft, hop_size=hop, lookahead=lookahead,
                delay_samples=fft-hop+lookahead*hop,
                members={k: dict(bytes=len(v), sha256=hashlib.sha256(v).hexdigest()) for k, v in content.items()})


def last_complete_metrics(data):
    lines = data.splitlines(keepends=True)
    complete = [line for line in lines if line.endswith(b'\n')]
    if not complete:
        raise RuntimeError('No complete owner telemetry row')
    return json.loads(complete[-1])


def check_limits(own, owner, available, free, trial_bytes, report_bytes, age, wall):
    failures = []
    if own > GIB: failures.append('own RSS exceeds stricter 1 GiB owner admission')
    if owner >= 5 * GIB: failures.append('owner RSS exceeds admission bound')
    if owner + own >= 6 * GIB: failures.append('aggregate RSS reaches 6 GiB')
    if available < 2 * GIB: failures.append('host available memory below 2 GiB')
    if free < 5 * GIB: failures.append('disk reserve below 5 GiB')
    if trial_bytes > 512 * 1024 ** 2: failures.append('trial disk exceeds 512 MiB')
    if report_bytes > 64 * 1024 ** 2: failures.append('persistent reports exceed 64 MiB')
    if not 0 <= age <= 10: failures.append('owner telemetry stale or future-dated')
    if wall > 1800: failures.append('active execution exceeds 1800 seconds')
    return failures


def size_tree(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.exists() else 0


class Guard:
    def __init__(self, private, out, owner_dir):
        import psutil
        self.psutil = psutil
        self.me = psutil.Process()
        self.identity = self.me.create_time()
        self.private, self.out, self.owner_dir = private, out, owner_dir
        self.started = time.monotonic()
        self.owner_identity = None
        self.stages = []
        self.peak_own = self.peak_aggregate = 0
        self.min_available = self.min_free = float('inf')
        self.sample('admission')

    def sample(self, stage):
        psutil = self.psutil
        now = time.time()
        metrics_path = self.owner_dir / 'metrics.jsonl'
        stat = metrics_path.stat()
        with metrics_path.open('rb') as f:
            f.seek(max(0, stat.st_size - 131072))
            owner = last_complete_metrics(f.read())
        progress = json.loads((self.owner_dir / 'progress.json').read_text())
        progress_time = dt.datetime.fromisoformat(progress['utc']).timestamp()
        # A partial append must not make an older complete row appear fresh.
        sample_time = progress_time - max(0., float(progress['wall_seconds_this_invocation']) - float(owner['wall_seconds']))
        age = max(now - stat.st_mtime, now - progress_time, now - sample_time)
        identities = sorted((int(p['pid']), float(p['created'])) for p in owner['processes'])
        # Namespace-local owner identities are compared only inside owner telemetry.
        # We never inspect or signal those PIDs from this executor.
        if self.owner_identity is None:
            self.owner_identity = identities[:3]
        if not all(item in identities for item in self.owner_identity):
            raise RuntimeError('Owner process identity changed')
        if self.me.create_time() != self.identity:
            raise RuntimeError('Own PID identity changed')
        own = 0
        processes = []
        for p in [self.me] + self.me.children(recursive=True):
            try:
                row = dict(pid=p.pid, created=p.create_time(), rss_bytes=p.memory_info().rss,
                           threads=p.num_threads(), affinity=p.cpu_affinity())
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            if row['affinity'] != [8]:
                raise RuntimeError('Trial-owned process escaped CPU 8')
            own += row['rss_bytes']
            processes.append(row)
        available = min(psutil.virtual_memory().available, int(owner['host_available_memory_bytes']))
        free = min(shutil.disk_usage(self.private).free, int(owner['disk_free_bytes']))
        report_bytes = size_tree(self.out)
        trial_bytes = size_tree(self.private) + report_bytes + SELF.stat().st_size + TEST.stat().st_size
        failures = check_limits(own, int(owner['rss_bytes']), available, free, trial_bytes,
                                report_bytes, age, time.monotonic() - self.started)
        row = dict(utc=utc(), stage=stage, own_rss_bytes=own, owner_rss_bytes=int(owner['rss_bytes']),
                   aggregate_rss_bytes=own+int(owner['rss_bytes']), owner_age_seconds=age,
                   owner_identities=identities, own_processes=processes, host_available_bytes=available,
                   disk_free_bytes=free, trial_disk_bytes=trial_bytes, persistent_results_bytes=report_bytes,
                   failures=failures)
        with (self.out / 'resources.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')
        self.peak_own = max(self.peak_own, own)
        self.peak_aggregate = max(self.peak_aggregate, own+int(owner['rss_bytes']))
        self.min_available = min(self.min_available, available)
        self.min_free = min(self.min_free, free)
        if failures:
            raise RuntimeError('resource_guard_failed: ' + '; '.join(failures))
        return row

    def run(self, command, stage, *, timeout=60, input_data=None):
        self.sample(stage + ':admission')
        start = time.monotonic()
        # Owned bounded disk files avoid PIPE deadlock while sampling every .5s.
        stdin_path = self.private / 'scratch' / 'stdin.bin'
        stdout_path = self.private / 'scratch' / 'stdout.bin'
        stderr_path = self.private / 'scratch' / 'stderr.txt'
        if input_data is not None:
            stdin_path.write_bytes(input_data)
        with (stdin_path.open('rb') if input_data is not None else open(os.devnull, 'rb')) as src, stdout_path.open('wb') as dst, stderr_path.open('wb') as err:
            process = subprocess.Popen(command, stdin=src, stdout=dst, stderr=err, cwd=ROOT,
                                       env={**os.environ, **THREAD_ENV}, start_new_session=True)
            owned = self.psutil.Process(process.pid)
            created = owned.create_time()
            try:
                while process.poll() is None:
                    self.sample(stage)
                    if time.monotonic() - start > timeout:
                        raise RuntimeError(stage + ': fixture timeout')
                    time.sleep(.5)
                self.sample(stage + ':complete')
                if process.returncode:
                    raise RuntimeError(stage + ': subprocess exit ' + str(process.returncode) + ': ' + stderr_path.read_text()[-2000:])
            except BaseException:
                # Only this subprocess's observed descendants, with create-time checks.
                try:
                    if owned.is_running() and owned.create_time() == created:
                        children = [(p, p.create_time()) for p in owned.children(recursive=True)]
                        for p, born in reversed(children + [(owned, created)]):
                            try:
                                if p.is_running() and p.create_time() == born: p.kill()
                            except self.psutil.NoSuchProcess: pass
                except self.psutil.NoSuchProcess:
                    pass
                process.wait(timeout=5)
                raise
        output = stdout_path.read_bytes()
        self.stages.append(dict(stage=stage, seconds=time.monotonic()-start, command=command,
                                stdout_sha256=hashlib.sha256(output).hexdigest(), stderr_sha256=digest(stderr_path)))
        return output

    def summary(self):
        return dict(peak_own_rss_bytes=self.peak_own, peak_aggregate_rss_bytes=self.peak_aggregate,
                    minimum_host_available_bytes=self.min_available, minimum_disk_free_bytes=self.min_free,
                    active_seconds=time.monotonic()-self.started, sampled_not_os_guarantee=True)


def resample(a, rate_in, rate_out, guard, stage):
    import numpy as np
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-threads', '1', '-f', 'f32le',
               '-ar', str(rate_in), '-ac', '1', '-i', 'pipe:0', '-af', FILTER,
               '-ar', str(rate_out), '-ac', '1', '-threads', '1', '-f', 'f32le', 'pipe:1']
    result = guard.run(command, stage, input_data=np.asarray(a, dtype='<f4').tobytes())
    out = np.frombuffer(result, '<f4').copy()
    if not np.isfinite(out).all(): raise RuntimeError('Nonfinite resampled samples')
    return out


def quantize(a):
    import numpy as np
    if np.any(np.abs(a) >= 1):
        raise RuntimeError('Matched PCM16 identity would saturate')
    return np.trunc(a * np.float32(32767)).astype(np.int16).astype(np.float32) / np.float32(32767)


def support(a, raw, geom):
    import numpy as np
    n, h, d = len(a), geom['hop_size'], geom['delay_samples']
    p = n // h * h
    v = p-d
    if len(raw) != n or v <= 0 or raw.dtype != np.int16 or np.any((raw == 32767) | (raw == -32768)):
        raise RuntimeError('Malformed length/support or enhanced PCM16 saturation')
    enhanced = raw[d:p].astype(np.float32) / np.float32(32767)
    matched = a[:v]
    residual = matched-enhanced
    error = float(np.max(np.abs((residual+enhanced)-matched)))
    if not np.isfinite(residual).all() or error > 2e-7:
        raise RuntimeError('Residual reconstruction failed')
    q = quantize(matched)
    return dict(R=residual, M=q, Q0=matched-q, unquantized=matched), dict(
        input_samples=n, processed_samples=p, delay_samples=d, valid_samples=v,
        valid_start_seconds=0, valid_end_seconds=v/48000, excluded_tail_seconds=(n-v)/48000,
        reconstruction_absolute_error=error)


def correlation(x, y):
    import numpy as np
    x, y = x.astype(np.float64), y.astype(np.float64)
    x, y = x-x.mean(), y-y.mean()
    denom = float(np.sqrt(np.dot(x, x)*np.dot(y, y)))
    if denom <= 1e-12: raise RuntimeError('Unidentifiable correlation interval')
    return float(np.dot(x, y)/denom)


def alignment_check(a, raw, geom, intervals):
    import numpy as np
    d = geom['delay_samples']
    y = raw.astype(np.float32)/np.float32(32767)
    output = []
    for left, right in intervals:
        lo, hi = round(left*48000), round(right*48000)
        if lo < 96 or hi+d+96 > len(raw) or hi <= lo:
            raise RuntimeError('Invalid predeclared correlation support')
        curve = [(offset, correlation(a[lo:hi], y[lo+d+offset:hi+d+offset])) for offset in range(-96,97)]
        maximum = max(c for _, c in curve)
        peaks = [off for off, c in curve if abs(c-maximum) <= 1e-12]
        zero = dict(curve)[0]
        passed = zero >= .50 and len(peaks) == 1 and abs(peaks[0]) <= 1
        output.append(dict(interval_seconds=[left,right], correlation_at_fixed_delay=zero,
                           diagnostic_peak_offsets=peaks, diagnostic_maximum=maximum,
                           diagnostic_correlation_curve=curve, passed=passed, applied_extra_shift=0))
    return output


def native(a, assets, geom, guard, label, compensate=False):
    import numpy as np
    import soundfile as sf
    scratch = guard.private / 'scratch'
    source = scratch / 'input.wav'
    output = scratch / 'native'
    output.mkdir(exist_ok=True)
    target = output / 'input.wav'
    if target.exists(): target.unlink()
    sf.write(source, a, 48000, subtype='FLOAT')
    command = [str(assets/'deep-filter-0.5.6-x86_64-unknown-linux-musl'),
               '--model', str(assets/'DeepFilterNet3_onnx.tar.gz'), '--atten-lim-db', '100',
               '--min-db-thresh=-15', '--max-db-erb-thresh', '35', '--max-db-df-thresh', '35',
               '--reduce-mask', '1', '--output-dir', str(output)]
    if compensate: command.append('-D')
    command.append(str(source))
    guard.run(command, label)
    with wave.open(str(target)) as w:
        if (w.getframerate(),w.getnchannels(),w.getsampwidth()) != (48000,1,2):
            raise RuntimeError('Native output format mismatch')
        aout = np.frombuffer(w.readframes(w.getnframes()), '<i2').copy()
    target.unlink()
    source.unlink()
    return aout


def timing_smoke(rows, assets, geom, guard):
    import numpy as np
    speech = next(r for r in rows if r['id']=='speech-control-development')
    case_rows = [speech, speech,
                 next(r for r in rows if r['id']=='generated-zeros-development'),
                 next(r for r in rows if r['id']=='generated-synthetic-impulses-development')]
    results = []
    for index, row in enumerate(case_rows):
        name = ['speech', 'speech_state_gap', 'exact_zeros', 'synthetic_impulses'][index]
        a = resample(samples_for(row),16000,48000,guard,'smoke:'+name+':resample')
        if index == 1: a[7*48000:int(7.5*48000)] = 0
        raw = native(a,assets,geom,guard,'smoke:'+name+':raw1')
        repeat = native(a,assets,geom,guard,'smoke:'+name+':raw2')
        compensated = native(a,assets,geom,guard,'smoke:'+name+':compensated',True)
        branches, info = support(a,raw,geom)
        d,p,v = info['delay_samples'],info['processed_samples'],info['valid_samples']
        checks = dict(repeated_integer_output_identical=np.array_equal(raw,repeat),
                      compensated_length=len(compensated)==len(a)-d,
                      compensated_exact_raw_alignment=np.array_equal(raw[d:p],compensated[:v]))
        intervals = [[1,min(13,v/48000-1)]] if index==0 else [[2,5],[9,12]] if index==1 else []
        align = alignment_check(a,raw,geom,intervals)
        checks['fixed_delay_correlations'] = all(c['passed'] for c in align)
        if index==2:
            checks['exact_zero_output_and_residual'] = bool(np.all(raw==0) and np.all(branches['R']==0))
        lengths = {key:len(resample(value,48000,16000,guard,'smoke:'+name+':'+key)) for key,value in branches.items()}
        checks['branch_lengths_equal'] = len(set(lengths.values()))==1
        result = dict(case=name, original_input_id=row['id'], waveform48_sha256=hashlib.sha256(a.tobytes()).hexdigest(),
                      raw_integer_sha256=hashlib.sha256(raw.tobytes()).hexdigest(), support=info,
                      correlation=align, checks=checks, output_lengths16=lengths,
                      impulse_output_nonzero_samples=int(np.count_nonzero(raw)) if index==3 else None,
                      passed=all(checks.values()))
        results.append(result)
        print('TIMING',name,result['passed'],flush=True)
        if not result['passed']:
            return dict(passed=False,cases=results,failed_case=name,unrun_cases=4-len(results))
    return dict(passed=True,cases=results)


def tag_file(input_path, output_path, window_seconds):
    import numpy as np
    from audio_backend.events.detector import category, window_starts, postprocess, LABELS
    import sherpa_onnx as s
    a = np.load(input_path,allow_pickle=False)
    model = ROOT/'models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15'
    tagger = s.AudioTagging(s.AudioTaggingConfig(model=s.AudioTaggingModelConfig(
        zipformer=s.OfflineZipformerAudioTaggingModelConfig(model=str(model/'model.onnx')),
        num_threads=1,provider='cpu'),labels=str(model/'class_labels_indices.csv'),top_k=527))
    window, hop = int(window_seconds*16000), int(window_seconds*8000)
    details=[]
    for st in window_starts(len(a),window,hop):
        chunk=a[st:st+window]
        peak=float(np.max(np.abs(chunk)))
        row=dict(start=st/16000,end=(st+len(chunk))/16000,category_scores={},
                 rms=float(np.sqrt(np.mean(chunk*chunk))),peak=peak,model_inference_executed=False,
                 classifier_padding_samples=max(0,window-len(chunk)))
        if peak<=1e-5:
            row['category_scores']['silence']=dict(score=1.,source_label='digital_or_near_digital_silence')
        else:
            padded=np.pad(chunk,(0,max(0,window-len(chunk))))
            stream=tagger.create_stream();stream.accept_waveform(sample_rate=16000,waveform=padded)
            for item in tagger.compute(stream):
                cat=category(str(item.name))
                if cat and float(item.prob)>row['category_scores'].get(cat,{}).get('score',-1):
                    row['category_scores'][cat]=dict(score=float(item.prob),source_label=str(item.name))
            row['model_inference_executed']=True
        details.append(row)
    events=postprocess(details,{k:.25 for k in LABELS if k!='silence'},release_ratio=1.)
    dump(output_path,dict(labels=sorted({e['label'] for e in events}),details=details,
                          window_seconds=window_seconds,hop_seconds=window_seconds/2,
                          rms=float(np.sqrt(np.mean(a*a))),peak=float(np.max(np.abs(a)))))


def score(a, guard, label, seconds=5):
    import numpy as np
    src=guard.private/'scratch'/'score-input.npy'
    dst=guard.private/'scratch'/'score-output.json'
    if dst.exists():dst.unlink()
    np.save(src,a,allow_pickle=False)
    guard.run([sys.executable,str(SELF),'tag','--input',str(src),'--output',str(dst),
               '--window-seconds',str(seconds)],label,timeout=60)
    result=json.loads(dst.read_text());src.unlink();dst.unlink()
    return result


def evaluate(rows, cases):
    """Pure frozen gates; baseline union cannot hide raw-view retention losses."""
    decisions={}
    for arm in ['R','C']:
        errors=[]; targets={}; annotations=[]
        for row in rows:
            case=cases[row['id']];base=set(row['baseline_labels']);m=set(case['M']['labels'])
            view=set(case[arm]['labels']);union=base|(view&TARGETS);known=set(row['labels'])
            q0=set(case['Q0']['labels'])
            if q0&TARGETS:errors.append(dict(id=row['id'],gate='Q0_target_nomination',labels=sorted(q0&TARGETS)))
            if row['kind'] in ['speech_control','generated_control']:
                for branch in ['R','C','M','Q0']:
                    nominated=set(case[branch]['labels'])&TARGETS
                    if nominated:errors.append(dict(id=row['id'],gate='control_target_nomination',branch=branch,labels=sorted(nominated)))
            if row['kind']=='source':
                needed=base&known&TARGETS
                if needed-view:errors.append(dict(id=row['id'],gate='clean_transport_view_loss',labels=sorted(needed-view)))
                if needed-m:errors.append(dict(id=row['id'],gate='preprocessing_failed',labels=sorted(needed-m)))
            added=union-base
            if row['source_type'] in ['ordinary_rail','road','speech'] and 'subway'in added:
                errors.append(dict(id=row['id'],gate='curated_source_context_negative',labels=['subway']))
            if row['source_group'].startswith('esc:') and (added-known):
                errors.append(dict(id=row['id'],gate='weak_label_unverified_addition',labels=sorted(added-known)))
            annotations.append(dict(id=row['id'],baseline_labels=sorted(base),policy_labels=sorted(union),
                added_labels=sorted(added),view_lost_labels=sorted(base-view),
                annotation=row['annotation'],added_known_positive=sorted(added&known),
                added_unverified=sorted(added-known)))
        for target, label, minimum in [('subway','subway',6),('train','driving',6)]:
            subset=[r for r in rows if r['kind']=='mixture' and r['source_type']==target]
            if len(subset)!=(9 if target=='subway' else 12):raise RuntimeError('Wrong target denominator')
            hits=[];gains=[];artifact_gains=[]
            for r in subset:
                case=cases[r['id']];b=set(r['baseline_labels']);v=set(case[arm]['labels']);m=set(case['M']['labels'])
                if label in b|v:hits.append(r['id'])
                if label in v and label not in b:
                    (artifact_gains if label in m else gains).append(r)
            groups=sorted({r['source_group'] for r in gains})
            passed=len(hits)>=minimum and len(groups)>=2 and (target!='subway' or any(r['snr_db']==-6 for r in gains))
            targets[target]=dict(passed=passed,total=len(subset),hits=len(hits),hit_ids=hits,
                new_recovery_ids=[r['id'] for r in gains],new_recovery_source_groups=groups,
                preprocessing_only_gains=[r['id'] for r in artifact_gains],
                per_snr={str(s):dict(total=sum(r['snr_db']==s for r in subset),
                    hits=sum(r['snr_db']==s and r['id'] in hits for r in subset)) for s in [-6,0,6]},
                per_source_group={g:dict(total=sum(r['source_group']==g for r in subset),
                    hits=sum(r['source_group']==g and r['id']in hits for r in subset)) for g in sorted({r['source_group'] for r in subset})})
        decisions[arm]=dict(passed=not errors and all(v['passed'] for v in targets.values()),targets=targets,
                            failed_gates=errors,annotations=annotations,quality_promoted=False)
    return decisions


def historical_parity(rows):
    from audio_backend.events.detector import postprocess
    caches={};checks=[]
    for r in rows:
        path=r['baseline_cache']
        if path not in caches:caches[path]=json.loads((ROOT/path).read_text())
        cache=caches[path]
        record=cache.get('cases',cache)[r['baseline_cache_id']]
        detail=record.get('baseline_details',record.get('details'))
        labels=sorted({x['label'] for x in postprocess(detail)})
        if labels!=sorted(r['baseline_labels']):raise RuntimeError('Cached decision reproduction mismatch: '+r['id'])
        checks.append(dict(id=r['id'],labels=labels,cache=path,score_windows=len(detail)))
    return checks


def preflight(assets, protocol):
    acquisition=json.loads((assets/'acquisition.json').read_text())
    if acquisition['download_bytes']>80*1024**2:raise RuntimeError('Acquisition cap exceeded')
    for row in acquisition['files']:
        file=assets/row['name']
        if digest(file)!=row['sha256'] or file.stat().st_size!=row['actual_bytes']:raise RuntimeError('Asset changed')
    release=json.loads((assets/'release-metadata.json').read_text())
    if release['id']!=119426317 or release['tag_name']!='v0.5.6':raise RuntimeError('Wrong release')
    for row in protocol['upstream']['assets']:
        data=(assets/row['url'].split('/')[-1]).read_bytes()
        if len(data)!=row['bytes']:raise RuntimeError('Wrong asset length')
        if 'git_blob_sha1'in row:
            actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            if actual!=row['git_blob_sha1']:raise RuntimeError('Wrong model Git blob')
        else:
            found=next(x for x in release['assets'] if x['id']==row['github_asset_id'])
            if found['size']!=len(data) or found['browser_download_url']!=row['url'] or found['digest'] is not None:
                raise RuntimeError('Binary release metadata mismatch')
    if 'MIT license'not in (assets/'LICENSE').read_text() or 'Apache License'not in (assets/'LICENSE').read_text():
        raise RuntimeError('License declaration mismatch')
    for row in protocol['inputs']['frozen_source_files']:
        if digest(ROOT/row['path'])!=row['sha256']:raise RuntimeError('Frozen source changed: '+row['path'])
    for k in ['model','labels']:
        if digest(ROOT/protocol['baseline'][k+'_path'])!=protocol['baseline'][k+'_sha256']:
            raise RuntimeError('Tagger identity mismatch')
    rows=protocol['inputs']['rows']
    if len(rows)!=138 or len({r['id']for r in rows})!=138:raise RuntimeError('Wrong inputs')
    for r in rows:
        if r['experiment_split']!='development':raise RuntimeError('Non-development input forbidden')
        samples_for(r)
    return {k:acquisition[k]for k in ['download_bytes']}, [dict(role=r['role'],url=r['url'],bytes=r['actual_bytes'],sha256=r['sha256'])for r in acquisition['files']]


def score_deltas(row, current):
    cache=json.loads((ROOT/row['baseline_cache']).read_text())
    previous=cache.get('cases',cache)[row['baseline_cache_id']]
    old=previous.get('baseline_details',previous.get('details'))
    fresh=current['details']
    if len(old)!=len(fresh):raise RuntimeError('Original score window-count parity failed')
    deltas=[]
    for before,after in zip(old,fresh):
        if (before['start'],before['end'])!=(after['start'],after['end']):
            raise RuntimeError('Original score support parity failed')
        for label in sorted(set(before['category_scores'])|set(after['category_scores'])):
            a=float(before['category_scores'].get(label,{}).get('score',0))
            b=float(after['category_scores'].get(label,{}).get('score',0))
            deltas.append(dict(start=after['start'],end=after['end'],label=label,historical=a,current=b,delta=b-a))
    return dict(maximum_absolute_delta=max((abs(v['delta'])for v in deltas),default=0),windows=deltas)


def privacy_audit(out):
    forbidden={'embedding','embeddings','speaker_embedding','speaker_embeddings','identity_vector','password','api_key','access_token','authorization','cookie','audio_samples','waveform_samples'}
    findings=[]
    def inspect(value,path):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.lower()in forbidden:findings.append(path+':'+key)
                inspect(item,path)
        elif isinstance(value,list):
            for item in value:inspect(item,path)
        elif isinstance(value,str) and ('X-Amz-Signature='in value or 'sig='in value and 'https://'in value):
            findings.append(path+':signed_url')
    files=[]
    for path in sorted(out.iterdir()):
        if not path.is_file():continue
        if path.suffix not in ['.json','.jsonl']:
            findings.append(path.name+':unexpected_file_type');continue
        if path.suffix=='.jsonl':
            for line in path.read_text().splitlines():inspect(json.loads(line),path.name)
        else:inspect(json.loads(path.read_text()),path.name)
        files.append(dict(name=path.name,sha256=digest(path),bytes=path.stat().st_size))
    return dict(passed=not findings,findings=findings,files=files,
                scope='Metadata, mapped category scores, hashes and timing/resources only; no audio, identity vectors, credentials or signed acquisition URLs.')


def run(args):
    os.sched_setaffinity(0,{8})
    os.environ.update(THREAD_ENV)
    if digest(PROTOCOL)!=PROTOCOL_SHA256:raise RuntimeError('Protocol hash mismatch')
    protocol=json.loads(PROTOCOL.read_text());rows=protocol['inputs']['rows']
    private=Path(args.private).resolve();assets=private/'assets';scratch=private/'scratch'
    out=ROOT/'reports/event-residual-development-v1'
    if out.exists():raise RuntimeError('Output root already exists; no repeated experiment allowed')
    out.mkdir();scratch.mkdir(exist_ok=False)
    guard=None
    try:
        guard=Guard(private,out,ROOT/'reports/endurance_v03/moss_cpp_q8_native_overlap_v03')
        admission,assets_safe=preflight(assets,protocol)
        geom=geometry(assets/'DeepFilterNet3_onnx.tar.gz',assets)
        historical=historical_parity(rows)
        dump(out/'cached-decision-reproduction.json',dict(passed=True,count=len(historical),cases=historical))
        source_paths=[str(p.relative_to(ROOT))for p in [SELF,TEST]]
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        for path in source_paths:
            if subprocess.run(['git','diff','--quiet','HEAD','--',path],cwd=ROOT).returncode:
                raise RuntimeError('Harness differs from committed source')
            subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
        version=subprocess.check_output(['ffmpeg','-version'],text=True)
        if not version.startswith('ffmpeg version 7.1.5-0+deb13u1 '):raise RuntimeError('Wrong FFmpeg')
        binary=assets/'deep-filter-0.5.6-x86_64-unknown-linux-musl'
        native_version=subprocess.check_output([str(binary),'--version'],text=True).strip()
        if native_version!='deep_filter 0.5.6':raise RuntimeError('Wrong native binary version')
        freeze=dict(utc=utc(),protocol_sha256=PROTOCOL_SHA256,harness_git_commit=commit,
            source_hashes={p:digest(ROOT/p)for p in source_paths},geometry=geom,assets=assets_safe,
            licensing='Pinned root MIT OR Apache-2.0 notices preserved; exact four-member model archive has no overriding terms. No redistribution.',
            binary_digest_caveat='Recorded local digest; upstream release provided no publisher digest.',
            acquisition=admission,ffmpeg_version=version,ffmpeg_sha256=digest(shutil.which('ffmpeg')),
            native_version=native_version,native_help=subprocess.check_output([str(binary),'--help'],text=True),
            fixed_cli_flags=protocol['upstream']['fixed_cli_flags'],resampler_filter=FILTER,
            runtime_versions={name:__import__('importlib.metadata',fromlist=['version']).version(name) for name in ['numpy','soundfile','sherpa-onnx','psutil']},
            source_sha256={r['path']:r['sha256']for r in protocol['inputs']['frozen_source_files']},
            input_file_hashes={r['id']:r['file_sha256']for r in rows},
            execution_argv=sys.argv,cpu_affinity=[8],thread_environment=THREAD_ENV,own_rss_cap_bytes=GIB,
            no_heldout_inference=True,no_production_changes=True)
        from scripts.benchmarks.endurance_runtime import capture
        runtime_manifest = capture(sys.executable, ['numpy','soundfile','sherpa-onnx','psutil'])
        dump(out/'runtime-files.json',runtime_manifest)
        freeze['runtime_files_sha256'] = digest(out/'runtime-files.json')
        freeze['runtime_capture_helper_sha256'] = digest(ROOT/'scripts/benchmarks/endurance_runtime.py')
        freeze['metrics_helper_sha256'] = digest(ROOT/'audio_backend/events/metrics.py')
        dump(out/'execution-freeze.json',freeze)
        smoke=timing_smoke(rows,assets,geom,guard)
        dump(out/('timing-passed.json'if smoke['passed']else'alignment_failed.json'),smoke)
        if not smoke['passed']:
            preflight(assets,protocol)
            historical_parity(rows)
            dump(out/'summary.json',dict(status='alignment_failed',event_scores_executed=False,
                candidate_arms_rejected_without_quality_claim=True,quality_promoted=False,
                geometry=geom,resources=guard.summary(),input_cases_scored=0,
                cached_decision_reproduction_passed=True,failed_case=smoke['failed_case']))
            return
        # All untouched original parity labels must pass before candidate scoring.
        parity={}
        for r in rows:
            value=score(samples_for(r),guard,'original-parity:'+r['id'])
            value['historical_score_deltas']=score_deltas(r,value)
            parity[r['id']]=value
            if value['labels']!=sorted(r['baseline_labels']):
                dump(out/'original-parity-partial.json',parity)
                raise RuntimeError('Original baseline label parity failed: '+r['id'])
        dump(out/'original-parity.json',parity)
        cases={}
        for index,r in enumerate(rows):
            a=resample(samples_for(r),16000,48000,guard,'fixture:'+r['id']+':upsample')
            raw=native(a,assets,geom,guard,'fixture:'+r['id']+':native')
            branches,info=support(a,raw,geom)
            transformed={k:resample(v,48000,16000,guard,'fixture:'+r['id']+':'+k+':downsample')for k,v in branches.items()}
            if len({len(v)for v in transformed.values()})!=1:raise RuntimeError('Branch support lengths differ')
            case=dict(support=info,historical_baseline_labels=r['baseline_labels'],
                context_interpretation='actual_more_than_5s_observed_support' if info['valid_end_seconds']>5 else 'at_most_5s_observed_support_padding_changes_pooling_only')
            for arm in ['M','R','Q0','C']:
                data=transformed['M'if arm=='C'else arm]
                case[arm]=score(data,guard,'fixture:'+r['id']+':score:'+arm,10 if arm=='C'else 5)
                case[arm]['waveform_sha256']=hashlib.sha256(data.tobytes()).hexdigest()
            cases[r['id']]=case
            dump(out/('case-'+str(index).zfill(3)+'.json'),dict(id=r['id'],**case))
            print('SCORED',index+1,len(rows),r['id'],flush=True)
        decisions=evaluate(rows,cases)
        from audio_backend.events.metrics import presence_metrics, group_presence
        metrics = {}
        sources = [r for r in rows if r['kind']=='source']
        for arm in ['baseline','M','R','C','Q0']:
            predictions = {r['id']:set(r['baseline_labels'] if arm=='baseline' else cases[r['id']][arm]['labels']) for r in rows}
            metrics[arm] = dict(annotation_aware=presence_metrics(rows,predictions,sorted(TARGETS)),
                source_group_annotation_aware=group_presence(sources,predictions,sorted(TARGETS)),
                source_group_closed_set_weak_label_proxy=group_presence(sources,predictions,sorted(TARGETS),True),
                independent_source_groups=len({r['source_group']for r in sources}),
                confidence_interval_warning='SNR repetitions are correlated; mixture intervals are descriptive, not independent-recording confidence.')
        engines = [r for r in rows if r['kind']=='mixture' and r['source_type']=='engine']
        metrics['engine_diagnostic'] = {arm:dict(total=len(engines), hits=sum('driving' in (r['baseline_labels'] if arm=='baseline' else cases[r['id']][arm]['labels']) for r in engines)) for arm in ['baseline','M','R','C']}
        dump(out/'metrics.json',metrics)
        dump(out/'decisions.json',decisions)
        reread={json.loads(p.read_text())['id']:json.loads(p.read_text())for p in sorted(out.glob('case-*.json'))}
        if evaluate(rows,reread)!=decisions:raise RuntimeError('Cached candidate decision reproduction mismatch')
        # Recheck immutable original hashes/caches after the entire experiment.
        preflight(assets,protocol)
        dump(out/'summary.json',dict(status='completed_development_pass',arms=decisions,quality_promoted=False,
            independent_validation_requires_new_approval=True,resources=guard.summary(),input_cases_scored=len(cases),
            correlated_media_seconds=1265,original_source_seconds=330,
            cached_decision_reproduction_passed=True,limitations=protocol['limitations']))
    except BaseException as exc:
        if not (out/'failure.json').exists():
            dump(out/'failure.json',dict(utc=utc(),error=str(exc),type=type(exc).__name__,
                quality_promoted=False,resources=guard.summary()if guard else None))
        raise
    finally:
        if guard:
            dump(out/'stage-costs.json',guard.stages)
            dump(out/'resource-summary.json',guard.summary())
            audit=privacy_audit(out)
            dump(out/'privacy-audit.json',audit)
            if not audit['passed']:raise RuntimeError('Report privacy audit failed')
        # Only explicitly owned scratch outputs; originals/assets/evidence are untouched.
        if scratch.exists():shutil.rmtree(scratch)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('run');p.add_argument('--private',required=True)
    p=sub.add_parser('tag');p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--window-seconds',type=int,choices=[5,10],required=True)
    args=parser.parse_args()
    if args.command=='run':run(args)
    else:tag_file(args.input,args.output,args.window_seconds)


if __name__=='__main__':main()
