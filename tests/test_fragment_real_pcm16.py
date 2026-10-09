"""Opt-in frozen REAL PCM mechanics matrix; no models, codecs or ASR scoring.

Prepared before execution. Default test collection skips all arms. Explicitly
set FRAGMENT_REAL_MANIFEST and FRAGMENT_REAL_SOURCE_ROOT only after review.
Original source ranges/oracle labels stay in this fixture/scoring module; core
matcher receives only anonymous source PCM, order and independent declarations.
"""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import tempfile
import wave

import pytest

from audio_backend.fragment_core import (
    BoundedPCM16Reader, MissingAudio, PlanCheckpoint, SourcePlan,
    build_pcm16_plan, core_windows, inspect_pcm16_source,
)
from audio_backend.fragment_matcher import Continuity

MANIFEST_SHA256 = 'b1440539eadc526966440b14b1dee74d548fd35b4b359f6e4d271a9ab6afe702'
ARM_IDS = (
    'reference:A_en_midword', 'reference:B_zh_turn_repeat_overlap', 'reference:F_event_interval',
    'split:A_en_midword', 'split:B_zh_turn_repeat_overlap', 'split:F_event_interval',
    'duplicate:1280', 'duplicate:8000', 'duplicate:32000',
    'unknown_duplicate:1280', 'unknown_duplicate:8000', 'unknown_duplicate:32000',
    'distinct_word:gap', 'distinct_word:unknown',
)
pytestmark = pytest.mark.skipif(
    not (os.environ.get('FRAGMENT_REAL_MANIFEST') and os.environ.get('FRAGMENT_REAL_SOURCE_ROOT')),
    reason='Frozen real PCM matrix requires explicit reviewed opt-in; no models',
)


@lru_cache(maxsize=1)
def frozen_manifest():
    path = Path(os.environ['FRAGMENT_REAL_MANIFEST'])
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == MANIFEST_SHA256, 'Frozen real manifest changed'
    return json.loads(data)


def streaming_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=2)
def original_reader(asset_id):
    """Bind only already-local PCM16 WAV; FLAC is not a new core feature."""
    manifest = frozen_manifest()
    root = Path(os.environ['FRAGMENT_REAL_SOURCE_ROOT'])
    asset = next(a for a in manifest['assets'] if a['id'] == asset_id)
    original_path = root / asset['path']
    assert streaming_sha256(original_path) == asset['sha256']
    offset = 0
    if asset['format'] == 'FLAC':
        alias = manifest['optional_existing_alias']
        fixture = next(f for f in manifest['fixtures'] if f['id'] == alias['fixture_id'])
        assert fixture['asset_id'] == asset_id
        path = root / alias['path']
        expected_file_sha256 = alias['sha256']
        offset = fixture['source_frames'][0]
    else:
        path = original_path
        expected_file_sha256 = asset['sha256']
    # Two original bindings only, cached throughout the run; no repeated whole
    # source hashing per arm. Inspection streams native PCM in bounded blocks.
    source = inspect_pcm16_source('original', path)
    assert source.sha256 == expected_file_sha256
    assert source.sample_rate == 16000 and source.channels == 1
    reader = BoundedPCM16Reader((source,), {'original': path})
    return reader, offset


def source_view(view):
    reader, offset = original_reader(view['asset_id'])
    start, end = view['source_frames']
    # Every selected reference is <=60s; subfragments are <=30s. No full-record
    # read and no repeated resampler initialization are involved.
    assert 0 < end - start <= 60 * 16000
    return reader.read_source('original', start - offset, end - offset)


@lru_cache(maxsize=3)
def reference_pcm(fixture_id):
    fixture = next(f for f in frozen_manifest()['fixtures'] if f['id'] == fixture_id)
    pcm = source_view(fixture['whole_view'])
    assert hashlib.sha256(pcm).hexdigest() == fixture['canonical_pcm_sha256']
    assert len(pcm) // 2 == fixture['logical_frames'][1]
    return pcm


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def owned_interval(interval, windows):
    start, end = interval
    return union((max(start, w.core_start), min(end, w.core_end)) for w in windows)


def fake_annotation_ownership(fixture, windows):
    """Given gold intervals, check ownership only. No recognition is claimed."""
    reference = fixture['reference']
    if fixture['id'] == 'A_en_midword':
        word = reference['critical_cross_seam_word']
        anchor = reference['fake_unit_anchor_frame']
        owners = [w for w in windows if w.core_start <= anchor < w.core_end]
        assert len(owners) == 1
        assert word['logical_frames'][0] < fixture['cuts_logical_frames'][0] < word['logical_frames'][1]
        # Keep complete interval when its anchor is published once.
        return {'kind': 'fake_gold_unit_ownership', 'retained_interval': word['logical_frames'],
                'owner_count': 1, 'asr_or_word_accuracy_measured': False}
    if fixture['id'] == 'B_zh_turn_repeat_overlap':
        by_speaker = {}
        for turn in reference['turns']:
            by_speaker.setdefault(turn['speaker'], []).extend(owned_interval(turn['logical_frames'], windows))
        durations = {key: sum(b - a for a, b in union(items)) for key, items in by_speaker.items()}
        assert durations == reference['speaker_union_frames']
        overlap = union(part for interval in reference['overlap_intervals']
                        for part in owned_interval(interval['logical_frames'], windows))
        assert sum(b - a for a, b in overlap) == reference['overlap_union_frames']
        for repeat in reference['repeat_text_cases']:
            turn = next(t for t in reference['turns'] if t['gold_array_index'] == repeat['gold_array_index'])
            assert repeat['substring'] in turn['text']
        return {'kind': 'fake_gold_turn_ownership', 'speaker_union_frames': durations,
                'overlap_union_frames': sum(b - a for a, b in overlap),
                'repeated_gold_substrings_retained': True, 'character_timestamps_created': False,
                'speaker_or_asr_accuracy_measured': False}
    event = reference['event']['logical_frames']
    owned = owned_interval(event, windows)
    assert owned == [event]
    assert sum(b - a for a, b in owned) == reference['event_union_frames']
    return {'kind': 'fake_gold_event_ownership', 'event_union_frames': reference['event_union_frames'],
            'acoustic_continuity_or_event_accuracy_measured': False}


@pytest.mark.parametrize('arm_id', ARM_IDS)
def test_frozen_real_pcm_matrix(arm_id, tmp_path):
    manifest = frozen_manifest()
    mode, key = arm_id.split(':')
    continuous = Continuity('continuous', same_capture=True, replay_possible=False)
    fixture = None
    oracle_trims = []
    if mode in ('reference', 'split'):
        fixture = next(f for f in manifest['fixtures'] if f['id'] == key)
        views = [fixture['whole_view']] if mode == 'reference' else fixture['split_views']
        declarations = [continuous] * (len(views) - 1)
        oracle_trims = [0] * (len(views) - 1)
        expected_reference = reference_pcm(fixture['id'])
    elif mode in ('duplicate', 'unknown_duplicate'):
        variant = next(v for v in manifest['duplicate_head_variants'] if v['duplicate_frames'] == int(key))
        fixture = next(f for f in manifest['fixtures'] if f['id'] == variant['base_fixture_id'])
        views = variant['input_views']
        declarations = [continuous if mode == 'duplicate' else Continuity()] * (len(views) - 1)
        oracle_trims = [variant['duplicate_frames'], 0]
        expected_reference = reference_pcm(fixture['id'])
    else:
        negative = manifest['distinct_source_word_negative']
        views = negative['views']
        declarations = [Continuity('gap', gap_frames=negative['known_gap_frames']) if key == 'gap' else Continuity()]
        oracle_trims = [0]
        expected_reference = None
    raw_parts = [source_view(v) for v in views]
    if mode == 'distinct_word':
        for raw, view in zip(raw_parts, views):
            assert hashlib.sha256(raw).hexdigest() == view['canonical_pcm_sha256']
    # Scorer-only provenance reconstruction. No SourcePlan decision is forged
    # from oracle_trims; in particular the80ms oracle is NOT a core capability.
    oracle_pcm = raw_parts[0] + b''.join(p[n * 2:] for p, n in zip(raw_parts[1:], oracle_trims))
    if expected_reference is not None:
        assert oracle_pcm == expected_reference
    with tempfile.TemporaryDirectory(prefix='real-pcm-', dir=tmp_path) as directory:
        paths = []
        for i, pcm in enumerate(raw_parts):
            path = Path(directory) / f'fragment-{i}.wav'
            with wave.open(str(path), 'wb') as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(16000)
                target.writeframes(pcm)
            paths.append(path)
        temp_bytes = sum(p.stat().st_size for p in paths)
        assert temp_bytes < 5 * 1024 ** 2
        # IDs are anonymous sequence numbers, never cut positions or offsets.
        sources = tuple(inspect_pcm16_source(str(i), p) for i, p in enumerate(paths))
        bindings = {s.fragment_id: p for s, p in zip(sources, paths)}
        reader = BoundedPCM16Reader(sources, bindings)
        plan = build_pcm16_plan(sources, declarations, reader, final=True)
        inferred = [s.evidence.trim_frames for s in plan.seams]
        for inferred_trim, oracle_trim in zip(inferred, oracle_trims):
            assert inferred_trim in (0, oracle_trim), 'False source-frame deletion or wrong inferred offset'
        if mode in ('reference', 'split', 'distinct_word', 'unknown_duplicate') or mode == 'duplicate' and key == '1280':
            assert not any(inferred), 'Frozen negative/below-minimum/provenance-unknown must not trim'
        view_reader = BoundedPCM16Reader(sources, bindings, plan)
        actual = b''.join(view_reader.read(s.logical_start, s.logical_end) for s in plan.spans)
        expected_decision_pcm = raw_parts[0] + b''.join(p[n * 2:] for p, n in zip(raw_parts[1:], inferred))
        assert actual == expected_decision_pcm
        assert plan.observed_frames == sum(len(p) // 2 for p in raw_parts) - sum(inferred)
        complete_oracle_match = inferred == oracle_trims
        if complete_oracle_match:
            assert actual == oracle_pcm
        if mode == 'distinct_word' and key == 'gap':
            gap_start, gap_end = negative['known_gap_logical_frames']
            assert plan.end_frame == negative['observed_frames'] + negative['known_gap_frames']
            with pytest.raises(MissingAudio):
                view_reader.read(gap_start, gap_end)
        if mode in ('unknown_duplicate', 'distinct_word') and (mode != 'distinct_word' or key == 'unknown'):
            assert all(seam.status == 'unknown_continuity' for seam in plan.seams)
            assert len({s.island for s in plan.spans}) == len(sources)
        windows = list(core_windows(plan, core_frames=30 * 16000, context_frames=2 * 16000))
        assert sum(w.core_end - w.core_start for w in windows) == plan.observed_frames
        for window in windows:
            block = view_reader.read(window.read_start, window.read_end)
            assert len(block) // 2 == window.read_end - window.read_start <= 60 * 16000
        annotation_check = None
        if fixture and complete_oracle_match:
            annotation_check = fake_annotation_ownership(fixture, windows)
        restored = SourcePlan.from_json(plan.to_json())
        assert restored.sha256 == plan.sha256
        assert build_pcm16_plan(sources, declarations, view_reader, final=True).sha256 == plan.sha256
        checkpoint = PlanCheckpoint(plan, plan.end_frame)
        assert checkpoint.eos_control() is not None
        assert PlanCheckpoint.from_json(checkpoint.acknowledge_eos().to_json()).eos_control() is None
        assert [streaming_sha256(p) for p in paths] == [s.sha256 for s in sources]
        expected_grid = None
        if fixture and complete_oracle_match:
            expected_grid = fixture['model_partition']['logical_core_frames']
        result = {
            'arm_id': arm_id, 'frozen_manifest_sha256': MANIFEST_SHA256, 'source_plan_sha256': plan.sha256,
            'matcher_input_excludes_source_ranges_and_oracle': True, 'models_executed': [],
            'inferred_trim_frames': inferred, 'hidden_oracle_trim_frames': oracle_trims,
            'accepted_offsets_equal_oracle': all(not a or a == b for a, b in zip(inferred, oracle_trims)),
            'complete_oracle_reconstruction': complete_oracle_match,
            'unresolved_positive': bool(mode == 'duplicate' and key != '1280' and not complete_oracle_match),
            'seams': [dict(declaration=s.declaration.relation, status=s.status, evidence=s.evidence.to_dict()) for s in plan.seams],
            'naive_concat_frames': sum(len(p) // 2 for p in raw_parts), 'observed_frames': plan.observed_frames,
            'logical_end_frame': plan.end_frame, 'observed_pcm_sha256': hashlib.sha256(actual).hexdigest(),
            'oracle_pcm_sha256': hashlib.sha256(oracle_pcm).hexdigest(),
            'owned_cores': [[w.core_start, w.core_end] for w in windows],
            'expected_canonical_cores_when_normalized': expected_grid,
            'canonical_grid_equal_when_normalized': [[w.core_start, w.core_end] for w in windows] == expected_grid if expected_grid else None,
            'annotation_ownership_only': annotation_check, 'native_resampling_or_flac_decode_used': False,
            'peak_short_wav_bytes': temp_bytes, 'source_files_unchanged': True,
            'reader_max_frames': view_reader.max_read_frames, 'aliases': [a.__dict__ for a in plan.aliases],
            'limits': 'Original active source files read only. No acoustic purity, ASR, speaker or event accuracy evidence.',
        }
        destination = Path(os.environ['FRAGMENT_RESULTS_DIR']).parent / 'real-matrix'
        destination.mkdir(exist_ok=True)
        (destination / (arm_id.replace(':', '-') + '.json')).write_text(json.dumps(result, indent=2) + '\n')
    assert not Path(directory).exists()
