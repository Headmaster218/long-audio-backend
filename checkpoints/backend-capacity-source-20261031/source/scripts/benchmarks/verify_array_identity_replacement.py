#!/usr/bin/env python3
"""Read-only numerical/source audit of the frozen research control.

Uses only the standard library. It neither downloads nor loads an inference model.
Run without -O: assertions deliberately fail closed for this fixed audit artifact.
The execution runner itself is Linux-only; this verifier has no resource dependency.
"""
import hashlib
import importlib.metadata
import json
import math
import pathlib
import struct
import sys
import wave

ROOT = pathlib.Path(__file__).resolve().parents[2]
P = ROOT / 'reports/identity_array_replacement_v1'
C = ROOT / 'benchmarks/private_array_identity_replacement_v1'


def read(path):
    return json.loads(pathlib.Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def unit(vector):
    if not vector or not all(type(x) in (int, float) and math.isfinite(x) for x in vector):
        raise ValueError('Expected a finite nonempty numeric vector')
    scale = max(abs(x) for x in vector)
    if not scale:
        raise ValueError('Zero vector')
    scaled = [x / scale for x in vector]
    norm = math.sqrt(math.fsum(x * x for x in scaled))
    return [x / norm for x in scaled]


def privacy(value):
    if isinstance(value, dict):
        assert not {'embedding', 'vector', 'evidence'}.intersection(value)
        for child in value.values():
            privacy(child)
    elif isinstance(value, list):
        assert not (len(value) >= 128 and all(type(x) in (int, float) for x in value))
        for child in value:
            privacy(child)


def public_json_paths(directory):
    # Never read our own redirected stdout, which may be empty/partial mid-run.
    return sorted(path for path in directory.glob('*.json') if path.name != 'verification.json')


def main():
    if not __debug__:
        raise RuntimeError('Audit assertions require Python without -O')
    plan = read(P / 'execution_plan.json')
    seal = read(P / 'execution-seal.json')
    report = read(P / 'replay.json')
    assert sha(P / 'execution_plan.json') == seal['plan_sha256'] == report['plan_sha256']
    assert sha(ROOT / 'scripts/benchmarks/run_array_identity_replacement.py') == seal['runner_sha256']
    assert sha(P / 'execution-seal.json') == report['execution_seal_sha256']
    frontend = plan['model_and_frontend']
    dependencies = plan['dependency_sha256'] | frontend['runtime_file_sha256']
    dependencies[plan['frozen_interval_plan']] = plan['frozen_interval_plan_sha256']
    dependencies[frontend['model_path']] = frontend['model_sha256']
    dependencies['audio_backend/profiles/eres2net-ami20-v1.json'] = frontend['profile_sha256']
    dependencies[frontend['ffmpeg']['path']] = frontend['ffmpeg']['sha256']
    for path, expected in dependencies.items():
        assert sha(ROOT / path) == expected, path
    for package, expected in frontend['packages'].items():
        assert importlib.metadata.version(package) == expected
    for name, key in [('acquisition-summary.json', 'acquisition_summary_sha256'),
                      ('extraction-summary.json', 'extraction_summary_sha256')]:
        assert sha(P / name) == report['source_and_embedding_provenance'][key]
    parity = read(P / 'parity.json')
    assert parity['status'] == 'passed' and len(parity['rows']) == 3
    for row, frozen in zip(parity['rows'], frontend['parity_crops_existing_calibration_only']):
        assert row['id'] == frozen['cache_id'] and row['passed']
        assert row['cosine'] >= .999999 and row['max_abs_error'] <= .00001
        assert sha(ROOT / frozen['local_audio']) == frozen['audio_sha256']
        assert sha(ROOT / 'benchmarks/private_speaker_embeddings' / (row['id'] + '.json')) == frozen['cache_sha256']

    acquired = read(C / 'acquisition.json')
    vectors = read(C / 'embeddings.json')
    summary = read(P / 'acquisition-summary.json')
    extract = read(P / 'extraction-summary.json')
    assert sha(C / 'acquisition.json') == summary['private_acquisition_sha256']
    assert sha(C / 'embeddings.json') == extract['private_embedding_cache_sha256']
    assert acquired['complete'] and vectors['complete']
    assert len(acquired['records']) == len(vectors['records']) == 24
    attempts = read(C / 'acquisition-attempts.json')
    assert len(attempts) <= 64 and all(x['status'] == 'complete' and x['http_status'] == 206 for x in attempts)
    assert sum(x['received_bytes'] for x in attempts) == summary['received_bytes_including_failed_attempts'] <= 10 * 1024**2
    assert sum(x['wall_seconds'] for x in attempts) <= 600
    for row in attempts:
        assert row['received_bytes'] == row['end'] - row['start'] + 1
        assert row['response_headers']['Content-Range'] == f"bytes {row['start']}-{row['end']}/{row['source_total_bytes']}"
        assert int(row['response_headers']['Content-Length']) == row['received_bytes']
        assert sha(C / row['path']) == row['sha256']
    stages = read(C / 'stage-attempts.json')
    resources = read(P / 'resource-summary.json')
    assert stages == resources['attempts'] and all(x['status'] == 'complete' for x in stages)
    outputs = {'parity': 'parity.json', 'acquire': 'acquisition-summary.json',
               'extract': 'extraction-summary.json', 'score': 'replay.json'}
    assert [x['stage'] for x in stages] == list(outputs)
    for row in stages:
        assert sha(P / outputs[row['stage']]) == row['output_sha256']
        assert row['guard_samples'] > 0
    assert sum(x['wall_seconds'] for x in stages if x['stage'] in ('parity', 'extract')) <= 300
    assert resources['peak_tree_rss_bytes'] <= 1024**3 and resources['peak_aggregate_rss_bytes'] < 6 * 1024**3
    assert resources['foreign_process_signals_sent'] is False
    manifest = read(ROOT / plan['frozen_interval_plan'])
    lookup = {r['existing_headset_cache_id']: r for r in vectors['records']}
    assert len(lookup) == 24
    for source_id, source in acquired['sources'].items():
        assert source['official_url'] == plan['source_strategy']['official_sources'][source_id]['file_url']
        headers = [r for r in attempts if r['request_key'].startswith(source_id + '-header-')]
        raw = b''.join((C / r['path']).read_bytes() for r in headers)
        assert len(raw) == source['header_bytes_received'] <= 256 * 1024
        assert hashlib.sha256(raw).hexdigest() == source['header_received_sha256']
        assert raw[:4] == b'RIFF' and raw[8:12] == b'WAVE'
        assert struct.unpack_from('<I', raw, 4)[0] + 8 == source['source_total_bytes']
        offset = 12
        fmt = None
        while offset + 8 <= len(raw):
            kind, size = raw[offset:offset + 4], struct.unpack_from('<I', raw, offset + 4)[0]
            if kind == b'fmt ':
                fmt = struct.unpack_from('<HHIIHH', raw, offset + 8)
            if kind == b'data':
                assert fmt == (1, 1, 16000, 32000, 2, 16)
                assert offset + 8 == source['data_offset'] and size == source['data_bytes']
                break
            offset += 8 + size + (size % 2)
        else:
            raise AssertionError('Missing data chunk in acquired header')
    for row, frozen in zip(acquired['records'], manifest['reviewed_intervals']):
        assert all(row[key] == value for key, value in frozen.items())
        assert sha(ROOT / row['local_gold']) == row['local_gold_sha256']
        assert sha(ROOT / row['clip_path']) == row['clip_sha256']
        source = acquired['sources'][row['recording_id']]
        attempt = next(r for r in attempts if r['request_key'] == row['existing_headset_cache_id'])
        assert attempt['url'] == source['official_url']
        assert attempt['start'] == row['range_start'] == source['data_offset'] + row['expected_16khz_start_sample'] * 2
        assert attempt['end'] == row['range_end_inclusive'] == source['data_offset'] + row['expected_16khz_end_sample_exclusive'] * 2 - 1
        assert attempt['source_total_bytes'] == source['source_total_bytes']
        for field, header in [('etag', 'ETag'), ('last_modified', 'Last-Modified')]:
            assert attempt['response_headers'][header] == source[field]
        with wave.open(str(ROOT / row['clip_path']), 'rb') as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
            pcm = wav.readframes(wav.getnframes())
        assert pcm == (C / attempt['path']).read_bytes()
        assert hashlib.sha256(pcm).hexdigest() == row['pcm_sha256']
        vector = lookup[row['existing_headset_cache_id']]
        assert vector['clip_sha256'] == row['clip_sha256'] and vector['paired_interval_id'] == row['paired_interval_id']
        floats = b''.join(struct.pack('<f', x[0] / 32768) for x in struct.iter_unpack('<h', pcm))
        assert hashlib.sha256(floats).hexdigest() == vector['pcm_f32_sha256']
        assert len(vector['embedding']) == 512 and vector['model_sha256'] == frontend['model_sha256']
        assert all(math.isfinite(x) for x in vector['embedding'])
        assert abs(sum(x * x for x in vector['embedding']) - 1) < 1e-8
        assert not row['annotation_audit']['annotation_contradiction']
        assert row['annotation_audit']['actual_array_identity_or_crosstalk_certified'] is False

    old = read(ROOT / 'reports/identity_learning_v1/replay.json')
    digest, all_old, count = hashlib.sha256(), {}, 0
    for path in sorted((ROOT / 'benchmarks/private_speaker_embeddings').glob('*.json')):
        row = read(path)
        if row['model_sha256'] == frontend['model_sha256']:
            digest.update(path.name.encode())
            digest.update(sha(path).encode())
            all_old[row['id']] = row
            count += 1
    assert digest.hexdigest() == old['selected_cache_aggregate_sha256']
    checked = 0
    for arm in report['results']:
        refs = {}
        selected = manifest['reviewed_intervals'][:arm['simulated_reviewed_clips']]
        for row in selected:
            if row['retained_in_existing_frozen_schedule']:
                refs.setdefault(row['person'], []).append(unit(lookup[row['existing_headset_cache_id']]['embedding']))
        assert len(refs) == 5 and max(map(len, refs.values())) <= 8
        assert {p: len(v) for p, v in refs.items()} == arm['references_by_person']
        assert abs(sum(x['reviewed_audio_seconds'] for x in selected) - arm['simulated_reviewed_audio_seconds']) < 1e-8
        means = {p: unit([math.fsum(x[i] for x in vs) for i in range(512)]) for p, vs in refs.items()}
        ending = '' if arm['simulated_reviewed_clips'] == 18 else '_early_corrections'
        for name, result in arm['slices'].items():
            baseline_name = 'original_farfield' if name == 'same51suffix' else name
            baseline = next(c for c in old['cases'] if c['case'] == baseline_name + ending
                            and c['strategy'] == 'centroid' and c['retained_initial_anchors_per_person_cap'] == 3)
            baseline_rows = {r['id']: r for r in baseline['rows']}
            new_ids = {r['id'] for r in result['rows']}
            assert new_ids <= set(baseline_rows)
            if name != 'same51suffix':
                assert new_ids == set(baseline_rows)
            transitions = {key: [] for key in result['paired']}
            for row in result['rows']:
                previous = baseline_rows[row['id']]
                assert row['gold_person'] == previous['gold_person'] and row['known'] == previous['known']
                if row['assigned'] == row['gold_person'] and previous['assigned'] != row['gold_person']:
                    transitions['rescued_correct_ids'].append(row['id'])
                if previous['assigned'] == row['gold_person'] and row['assigned'] != row['gold_person']:
                    transitions['lost_correct_ids'].append(row['id'])
                if row['assigned'] is not None and row['assigned'] != row['gold_person'] and row['assigned'] != previous['assigned']:
                    transitions['newly_wrong_ids'].append(row['id'])
                if not row['known'] and row['assigned'] is not None and previous['assigned'] is None:
                    transitions['new_unknown_accept_ids'].append(row['id'])
            assert transitions == result['paired']
            expected = 51 if name == 'same51suffix' else 60 if name == 'original_farfield' else 116
            known = [r for r in result['rows'] if r['known']]
            assert len(known) == result['known']['known_queries'] == expected
            assert len({r['id'] for r in result['rows']}) == len(result['rows'])
            for row in result['rows']:
                query = unit(all_old[row['id']]['embedding'])
                scores = {p: math.fsum(x * y for x, y in zip(query, mean)) for p, mean in means.items()}
                ranked = sorted(scores, key=lambda p: (-scores[p], p))
                best, gap = ranked[0], scores[ranked[0]] - scores[ranked[1]]
                threshold = .55 if len(refs[best]) == 1 else .56
                predicted = best if scores[best] >= threshold and gap >= .10 else None
                assert predicted == row['assigned']
                assert abs(scores[best] - row['score']) < 1e-12 and abs(gap - row['margin']) < 1e-12
                checked += 1
            assert sum(r['assigned'] == r['gold_person'] for r in known) == result['known']['correct_known']
    for path in public_json_paths(P):
        privacy(read(path))
    print(json.dumps({
        'status': 'passed',
        'checks': 'Independent standard-library centroid math for every reported row; frozen dependencies; exact source ranges, waveform and cache hashes; complete denominators; recorded resource budgets; public vector exclusion',
        'reported_row_decisions_recomputed': checked,
        'row_count_note': 'Includes repeated suffix and repeated presentations of the same unknown clips; not independent trials',
        'old_cache_files_unchanged': count,
        'new_clips_verified': 24,
        'source_range_requests': len(attempts),
        'public_vectors_found': False,
        'actual_human_or_acoustic_purity_certification': False,
        'verifier_sha256': sha(__file__),
    }, indent=2))


if __name__ == '__main__':
    main()
