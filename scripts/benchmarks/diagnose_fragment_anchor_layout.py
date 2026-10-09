#!/usr/bin/env python3
"""Authorized model-free diagnostic only; never changes matcher decisions.

Read the two already-rejected exact candidates. Measure existing first/middle/
last80ms anchors, then enumerate80ms windows on a20ms grid. No matcher rerun,
threshold change, alternative trim, model, source write or runtime integration.
"""
from array import array
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from audio_backend.fragment_core import BoundedPCM16Reader, inspect_pcm16_source
from audio_backend.fragment_matcher import ANCHOR_MS, MIN_ANCHOR_SPACING_MS, MIN_RMS_DBFS

MANIFEST_SHA256 = 'b1440539eadc526966440b14b1dee74d548fd35b4b359f6e4d271a9ab6afe702'
MAX_GRID_WINDOWS = 150


def tree_bytes():
    return sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file() and not p.is_symlink())


def metrics(values, start, width, channels):
    rows = []
    minimum_power = (32768 * 10 ** (MIN_RMS_DBFS / 20)) ** 2
    for channel in range(channels):
        samples = values[start * channels + channel:(start + width) * channels:channels]
        assert len(samples) == width
        power = sum(x * x for x in samples) / width
        mean = sum(samples) / width
        variance = power - mean * mean
        rows.append({'channel': channel, 'rms_dbfs': 10 * math.log10(power / 32768 ** 2) if power else None,
                     'mean_pcm_units': mean, 'demeaned_variance_pcm_units_squared': variance,
                     'rms_above_frozen_threshold': power > minimum_power,
                     'nonzero_demeaned_energy': variance > 0})
    return {'start_frame': start, 'end_frame': start + width, 'channels': rows,
            'informative_under_existing_gate': all(r['rms_above_frozen_threshold'] and r['nonzero_demeaned_energy'] for r in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    process = psutil.Process()
    if 6 not in process.cpu_affinity():
        raise RuntimeError('CPU6 unavailable')
    process.cpu_affinity([6])
    assert process.num_threads() == 1
    cap = 480 * 1024 ** 2
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    before_bytes = tree_bytes()
    disk = shutil.disk_usage(ROOT).free
    ram = psutil.virtual_memory().available
    if disk < int(5.5 * 1024 ** 3) + 1024 ** 2 or ram < 2 * 1024 ** 3 or before_bytes + 1024 ** 2 > 96 * 1024 ** 2:
        raise RuntimeError('Diagnostic resource guard refused execution')
    started = time.monotonic()
    data = args.manifest.read_bytes()
    assert hashlib.sha256(data).hexdigest() == MANIFEST_SHA256
    manifest = json.loads(data)
    asset = next(a for a in manifest['assets'] if a['id'] == 'ami_es2004a')
    source_path = args.source_root / asset['path']
    source = inspect_pcm16_source('diagnostic-source', source_path)
    assert source.sha256 == asset['sha256']
    reader = BoundedPCM16Reader((source,), {source.fragment_id: source_path})
    width = (source.sample_rate * ANCHOR_MS + 999) // 1000
    step = source.sample_rate * 20 // 1000
    separation = (source.sample_rate * MIN_ANCHOR_SPACING_MS + 999) // 1000
    results = []
    for duration in (8000, 32000):
        report = json.loads((ROOT / 'reports/audio-fragment-core/real-matrix' / f'duplicate-{duration}.json').read_text())
        evidence = report['seams'][0]['evidence']
        assert evidence['outcome'] == 'abstain' and evidence['reason'] == 'weak_or_uninformative_anchors'
        assert evidence['candidate_frames'] == [duration]
        variant = next(v for v in manifest['duplicate_head_variants'] if v['duplicate_frames'] == duration)
        first_frame = variant['input_views'][1]['source_frames'][0]
        # Candidate size comes from the recorded independent matcher inference.
        # Source range here only locates those original bytes for diagnosis.
        count = evidence['candidate_frames'][0]
        raw = reader.read_source(source.fragment_id, first_frame, first_frame + count)
        values = array('h')
        values.frombytes(raw)
        if sys.byteorder != 'little':
            values.byteswap()
        fixed = [metrics(values, start, width, source.channels) for start in (0, (count - width) // 2, count - width)]
        starts = range(0, count - width + 1, step)
        assert len(starts) <= MAX_GRID_WINDOWS
        grid = [metrics(values, start, width, source.channels) for start in starts]
        informative = [row['start_frame'] for row in grid if row['informative_under_existing_gate']]
        chosen = []
        for start in informative:
            if not chosen or start - chosen[-1] >= separation:
                chosen.append(start)
                if len(chosen) == 3:
                    break
        regions = []
        for start in informative:
            if regions and start == regions[-1]['last_start_frame'] + step:
                regions[-1]['last_start_frame'] = start
                regions[-1]['window_count'] += 1
            else:
                regions.append({'first_start_frame': start, 'last_start_frame': start, 'window_count': 1})
        results.append({'candidate_frames': count, 'candidate_pcm_sha256': hashlib.sha256(raw).hexdigest(),
                        'source_frames_for_diagnostic_only': [first_frame, first_frame + count],
                        'unchanged_decision': evidence, 'fixed_anchors': fixed,
                        'grid_step_frames': step, 'grid_window_count': len(grid),
                        'informative_grid_window_count': len(informative), 'informative_start_regions': regions,
                        'three_separated_informative_windows_exist': len(chosen) == 3,
                        'diagnostic_earliest_separated_starts': chosen,
                        'grid': grid})
    assert inspect_pcm16_source(source.fragment_id, source_path) == source
    own_peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    assert own_peak_rss <= 512 * 1024 ** 2
    payload = {'scope': 'diagnostic only; no matcher decisions or thresholds changed',
               'manifest_sha256': MANIFEST_SHA256, 'source_sha256': source.sha256,
               'thresholds_unchanged': {'rms_dbfs_exclusive': MIN_RMS_DBFS, 'anchor_ms': ANCHOR_MS,
                                        'minimum_center_spacing_ms': MIN_ANCHOR_SPACING_MS,
                                        'requires_nonzero_demeaned_energy': True},
               'diagnostic_search': {'step_ms': 20, 'maximum_windows_per_candidate': MAX_GRID_WINDOWS,
                                     'maximum_candidates': 2, 'selection_is_not_a_trim_decision': True},
               'results': results,
               'resources': {'cpu_affinity': process.cpu_affinity(), 'threads': process.num_threads(),
                             'child_processes_spawned': 0, 'explicit_self_pid': process.pid,
                             'address_space_limit_bytes': cap, 'own_peak_rss_bytes': own_peak_rss,
                             'disk_free_before_bytes': disk, 'memory_available_before_bytes': ram,
                             'workspace_before_bytes': before_bytes, 'elapsed_seconds': time.monotonic() - started,
                             'maximum_source_read_frames': reader.max_read_frames},
               'models_executed': [], 'new_audio_files': 0, 'source_unchanged': True}
    encoded = json.dumps(payload, indent=2, allow_nan=False) + '\n'
    assert len(encoded.encode()) < 128 * 1024
    args.output.write_text(encoded)
    assert tree_bytes() < 96 * 1024 ** 2
    print(json.dumps({k: v for k, v in payload.items() if k != 'results'}, indent=2))
    for result in results:
        print(json.dumps({k: v for k, v in result.items() if k != 'grid'}, indent=2))


if __name__ == '__main__':
    main()
