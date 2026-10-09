"""Offline synthetic mechanics only: no neural models, ASR, resampling or API."""
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import struct
import wave

import pytest

from audio_backend.fragment_core import (
    Alias, BoundedPCM16Reader, CoreWindow, MissingAudio, PlanCheckpoint, Seam, Source,
    SourceChanged, SourcePlan, Span, UnsupportedAudio, build_pcm16_plan, core_windows, inspect_pcm16_source,
)
from audio_backend.fragment_matcher import Continuity, BoundaryEvidence, match_boundary

DATA = Path(__file__).parent / 'fragment_core_data'
FREEZE_SHA = '6c1f58fc0f3cd7b8e35ca02a007bfe0dd9be294788164bc11d8d81c9192838fe'
RECIPE_SHA = '49c16ea769346cce823cdf97dfafa80b76add009287c0e070550725d03a534b2'
MANIFEST = json.loads((DATA / 'freeze.json').read_text())
HASHES = json.loads((DATA / 'frozen_hashes.json').read_text())
spec = importlib.util.spec_from_file_location('fixture_recipe', DATA / 'fixture_recipe.py')
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)
CONTINUOUS = Continuity('continuous', same_capture=True, replay_possible=False)


def write_wav(path, data, *, rate=16000, channels=1, width=2):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data)
    return path


def setup_sources(paths):
    sources = tuple(inspect_pcm16_source(str(i), p) for i, p in enumerate(paths))
    path_map = {s.fragment_id: p for s, p in zip(sources, paths)}
    return sources, path_map, BoundedPCM16Reader(sources, path_map)


def synthetic_metadata_plan(lengths, *, rate=16000, final=True, gaps=None):
    """Scheduler-only fake metadata, deliberately no PCM/no model evidence."""
    sources = tuple(Source(str(i), '0' * 64, rate, 1, length) for i, length in enumerate(lengths))
    cursor, island = lengths[0], 0
    spans = [Span('0', 0, lengths[0], 0, cursor, island)]
    seams = []
    for i, length in enumerate(lengths[1:], 1):
        gap = (gaps or {}).get(i)
        declaration = Continuity('gap', gap_frames=gap) if gap else CONTINUOUS
        status = 'declared_gap' if gap else 'declared_continuity_only'
        seams.append(Seam(str(i - 1), str(i), declaration,
                          BoundaryEvidence('abstain', 'no_exact_overlap_evidence'), cursor, status))
        if gap:
            cursor += gap
            island += 1
        spans.append(Span(str(i), 0, length, cursor, cursor + length, island))
        cursor += length
    return SourcePlan(sources, tuple(spans), (), tuple(seams), final).validate()


def test_freeze_precedes_implementation():
    assert hashlib.sha256((DATA / 'freeze.json').read_bytes()).hexdigest() == FREEZE_SHA
    assert hashlib.sha256((DATA / 'fixture_recipe.py').read_bytes()).hexdigest() == RECIPE_SHA
    import audio_backend.fragment_matcher as matcher
    assert matcher.MAX_MATCH_SECONDS == MANIFEST['thresholds']['matcher_seconds']
    assert matcher.MIN_OVERLAP_MS == MANIFEST['thresholds']['minimum_overlap_ms']
    assert matcher.ANCHOR_MS == MANIFEST['thresholds']['informative_anchor_ms']
    assert matcher.MIN_RMS_DBFS == MANIFEST['thresholds']['anchor_rms_dbfs']
    assert matcher.MIN_ANCHOR_SPACING_MS == MANIFEST['thresholds']['anchor_center_spacing_ms']
    assert matcher.ANCHOR_COUNT == MANIFEST['thresholds']['informative_anchor_count']
    assert matcher.PERIODIC_HZ == MANIFEST['thresholds']['periodic_downsample_hz']
    assert matcher.PERIODIC_REJECT == MANIFEST['thresholds']['periodic_correlation_reject']
    assert matcher.MAX_CANDIDATES == MANIFEST['thresholds']['candidate_limit']


@pytest.mark.parametrize('case', MANIFEST['fixtures'], ids=lambda case: case['id'])
def test_frozen_matrix(case, tmp_path):
    paths, reference, pieces = recipe.make_fixture(case, tmp_path)
    hashes_before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    assert hashes_before == HASHES[case['id']]['wav_sha256']
    assert hashlib.sha256(reference).hexdigest() == HASHES[case['id']]['reference_pcm_sha256']
    declaration = Continuity(case.get('relation', 'continuous'), case.get('same_capture', True),
                            case.get('replay_possible', False), case.get('gap_frames'))
    sources, path_map, reader = setup_sources(paths)
    # build_pcm16_plan sees only original sources, order and provenance declarations.
    # case['cut'], case['overlap'] and expected labels never enter matcher calls.
    plan = build_pcm16_plan(sources, [declaration], reader, final=True)
    evidence = plan.seams[0].evidence
    assert evidence.outcome == case['expected']
    assert evidence.trim_frames == case['expected_trim']
    oracle = case['overlap']
    if evidence.outcome == 'trim':
        assert evidence.candidate_frames == (oracle,)  # inferred vs hidden oracle
        assert evidence.trim_frames == oracle
        assert len(plan.aliases) == 1
        alias = plan.aliases[0]
        assert alias.source_start == 0 and alias.source_end == oracle
        assert alias.logical_start == case['cut'] - oracle
    else:
        assert not plan.aliases
    logical_reader = BoundedPCM16Reader(sources, path_map, plan)
    # Read every owner exactly once; ambiguity islands never imply continuity.
    actual = b''.join(logical_reader.read(s.logical_start, s.logical_end) for s in plan.spans)
    expected = pieces[0] + pieces[1][evidence.trim_frames * 2 * case['channels']:]
    assert actual == expected
    if evidence.trim_frames or (oracle == 0 and case.get('kind') not in ('channel_mismatch', 'quantized')):
        assert actual == reference
    assert plan.observed_frames == sum(s.frames for s in sources) - evidence.trim_frames
    assert plan.end_frame == plan.observed_frames + (case.get('gap_frames') or 0)
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths] == hashes_before
    assert SourcePlan.from_json(plan.to_json()).to_json() == plan.to_json()
    assert SourcePlan.from_json(plan.to_json()).sha256 == plan.sha256
    assert max(evidence.searched_left_frames, evidence.searched_right_frames) <= case['rate'] * 30
    assert logical_reader.max_read_frames <= case['rate'] * 60
    out = os.environ.get('FRAGMENT_RESULTS_DIR')
    if out:
        result = dict(id=case['id'], expected=case['expected'], actual=evidence.to_dict(),
                      hidden_oracle_overlap_frames=oracle, inferred_trim_error_frames=evidence.trim_frames - oracle
                      if evidence.trim_frames else None,
                      naive_concat_frames=sum(s.frames for s in sources), normalized_observed_frames=plan.observed_frames,
                      plan_sha256=plan.sha256, source_sha256=hashes_before, seam_status=plan.seams[0].status)
        (Path(out) / (case['id'] + '.json')).write_text(json.dumps(result, indent=2) + '\n')


def test_default_declaration_cannot_trim():
    rng = random.Random(990)
    pcm = struct.pack('<16000h', *(rng.randrange(-12000, 12001) for _ in range(16000)))
    assert match_boundary(pcm, pcm, sample_rate=16000, channels=1, declaration=Continuity()).trim_frames == 0
    assert match_boundary(pcm, pcm, sample_rate=16000, channels=1,
                          declaration=Continuity('continuous', same_capture=True)).reason == 'real_replay_not_excluded'


def test_native_reader_crosses_non_grid_files(tmp_path):
    case = MANIFEST['fixtures'][0]
    paths, reference, pieces = recipe.make_fixture(case, tmp_path)
    # Add a third physical cut on neither a second nor model-window grid.
    paths[1].unlink()
    split = 7123 * 2
    paths[1] = write_wav(tmp_path / 'b.wav', pieces[1][:split])
    paths.append(write_wav(tmp_path / 'c.wav', pieces[1][split:]))
    sources, path_map, reader = setup_sources(paths)
    plan = build_pcm16_plan(sources, [CONTINUOUS, CONTINUOUS], reader, final=True)
    assert all(s.status == 'declared_continuity_only' for s in plan.seams)
    assert all(s.evidence.reason == 'no_exact_overlap_evidence' for s in plan.seams)
    reader = BoundedPCM16Reader(sources, path_map, plan)
    assert reader.read(0, plan.end_frame) == reference
    windows = list(core_windows(plan, core_frames=16000, context_frames=3200))
    assert all(w.core_start % 16000 == 0 for w in windows)
    assert any(w.core_start < case['cut'] < w.core_end for w in windows)
    assert any(len(reader.mapping(w.read_start, w.read_end)) > 1 for w in windows)
    assert b''.join(reader.read(w.core_start, w.core_end) for w in windows) == reference
    for window in windows:
        assert reader.read(window.read_start, window.read_end) == reference[window.read_start * 2:window.read_end * 2]


@pytest.mark.parametrize('relation', ['gap', 'unknown'])
def test_missing_is_not_silence_or_adjacency(tmp_path, relation):
    case = MANIFEST['fixtures'][0]
    paths, _, _ = recipe.make_fixture(case, tmp_path)
    sources, path_map, reader = setup_sources(paths)
    declaration = Continuity(relation, gap_frames=137 if relation == 'gap' else None)
    plan = build_pcm16_plan(sources, [declaration], reader, final=True)
    reader = BoundedPCM16Reader(sources, path_map, plan)
    cut = sources[0].frames
    with pytest.raises(MissingAudio):
        reader.read(cut - 1, cut + (137 if relation == 'gap' else 0) + 1)
    if relation == 'gap':
        with pytest.raises(MissingAudio):
            reader.read(cut, cut + 137)
    windows = list(core_windows(plan, core_frames=16000, context_frames=3200))
    assert not any(w.read_start < cut < w.read_end for w in windows)
    assert sum(w.core_end - w.core_start for w in windows) == sum(s.frames for s in sources)
    assert [w.finish_native for w in windows].count(True) == 1
    assert any(w.right_context == 'discontinuity' and not w.finish_native for w in windows)
    assert plan.seams[0].evidence.reason == 'no_exact_overlap_evidence'
    assert plan.seams[0].status != 'adjacent'


def test_ambiguous_exact_short_overlap_splits_context(tmp_path):
    case = MANIFEST['fixtures'][1]
    paths, _, _ = recipe.make_fixture(case, tmp_path)
    sources, path_map, reader = setup_sources(paths)
    plan = build_pcm16_plan(sources, [CONTINUOUS], reader, final=True)
    assert plan.seams[0].evidence.candidate_frames == (1280,)
    assert plan.seams[0].status == 'ambiguous_overlap'
    reader = BoundedPCM16Reader(sources, path_map, plan)
    with pytest.raises(MissingAudio):
        reader.read(case['cut'] - 1, case['cut'] + 1)


def test_multi_fragment_aliases_preserve_earlier_owner(tmp_path):
    rng = random.Random(912)
    raw = struct.pack('<80000h', *(rng.randrange(-12000, 12001) for _ in range(80000)))
    # Third fragment overlaps both retained owners; fourth is entirely duplicate.
    pieces = [raw[:32000 * 2], raw[24000 * 2:40000 * 2], raw[28000 * 2:60000 * 2],
              raw[28000 * 2:60000 * 2], raw[52000 * 2:]]
    paths = [write_wav(tmp_path / f'{i}.wav', p) for i, p in enumerate(pieces)]
    sources, path_map, reader = setup_sources(paths)
    plan = build_pcm16_plan(sources, [CONTINUOUS] * 4, reader, final=True)
    assert [s.evidence.trim_frames for s in plan.seams] == [8000, 12000, 32000, 8000]
    assert len(plan.spans) == 4 and len(plan.aliases) == 4
    assert plan.spans[0].fragment_id == '0'
    assert plan.aliases[1].logical_start == 28000
    assert plan.aliases[1].logical_end == 40000
    reader = BoundedPCM16Reader(sources, path_map, plan)
    assert reader.read(0, 80000) == raw
    assert SourcePlan.from_json(plan.to_json()) == plan


def test_logical_hour_and_epoch_ownership_without_hour_pcm():
    rate = 16000
    plan = synthetic_metadata_plan([3600 * rate, 300 * rate + 1])
    windows = list(core_windows(plan, core_frames=17 * rate, context_frames=2 * rate))
    assert all(a.core_end == b.core_start for a, b in zip(windows, windows[1:]))
    assert sum(w.core_end - w.core_start for w in windows) == 3900 * rate + 1
    boundary = next(w for w in windows if w.core_start == 3600 * rate)
    assert boundary.epoch_index == 12 and boundary.epoch_start == 3600 * rate
    assert boundary.read_start == (3600 - 2) * rate
    assert any(w.core_start < (3600 - .5) * rate < w.core_end for w in windows)
    assert windows[-1].core_end - windows[-1].core_start == 1
    assert windows[-1].epoch_index == 13 and windows[-1].finish_native
    assert len(plan.spans) == 2
    assert len(plan.to_json()) < 3000
    assert all(w.read_end - w.read_start <= 60 * rate for w in windows)
    # Changing only physical segmentation leaves global window ownership intact.
    unsplit = synthetic_metadata_plan([3900 * rate + 1])
    assert windows == list(core_windows(unsplit, core_frames=17 * rate, context_frames=2 * rate))


def test_live_tail_waits_late_fragment_extends_and_eos_finishes_once(tmp_path):
    rate = 16000
    live = synthetic_metadata_plan([3600 * rate], final=False)
    windows = list(core_windows(live, core_frames=30 * rate, context_frames=4 * rate))
    assert windows[-1].core_end == 3570 * rate
    assert not any(w.finish_native for w in windows)
    checkpoint = PlanCheckpoint(live, windows[-1].core_end)
    checkpoint.save(tmp_path / 'checkpoint.json')
    resumed = PlanCheckpoint.from_json((tmp_path / 'checkpoint.json').read_text())
    assert resumed == checkpoint
    extended = synthetic_metadata_plan([3600 * rate, 300 * rate + 1], final=True)
    resumed = resumed.extend(extended)
    tail = list(core_windows(resumed.plan, core_frames=30 * rate, context_frames=4 * rate,
                             start_frame=resumed.next_frame))
    complete = list(core_windows(extended, core_frames=30 * rate, context_frames=4 * rate))
    assert windows + tail == complete
    assert len([w for w in windows + tail if w.finish_native]) == 1
    assert tail[-1].core_end - tail[-1].core_start == 1
    assert not list(core_windows(extended, core_frames=30 * rate, context_frames=4 * rate,
                                 start_frame=extended.end_frame))
    with pytest.raises(ValueError, match='EOS'):
        resumed.extend(extended)
    with pytest.raises(ValueError, match='prefix'):
        checkpoint.extend(synthetic_metadata_plan([3599 * rate, 301 * rate + 1], final=True))


def test_one_frame_final_fragment_never_lost(tmp_path):
    case = next(c for c in MANIFEST['fixtures'] if c['id'] == 'final_one_frame')
    paths, reference, _ = recipe.make_fixture(case, tmp_path)
    sources, path_map, reader = setup_sources(paths)
    plan = build_pcm16_plan(sources, [CONTINUOUS], reader, final=True)
    windows = list(core_windows(plan, core_frames=16000, context_frames=1600))
    assert windows[-1].core_end - windows[-1].core_start == 1
    assert windows[-1].finish_native
    assert windows[-1].right_context == 'eos'
    logical = BoundedPCM16Reader(sources, path_map, plan)
    assert b''.join(logical.read(w.core_start, w.core_end) for w in windows) == reference


@pytest.mark.parametrize('rate', [44100, 48000])
def test_thousands_of_non_grid_fragments_no_cumulative_native_drift(rate):
    lengths = [1001 + i % 37 for i in range(2000)]
    plan = synthetic_metadata_plan(lengths, rate=rate)
    assert plan.end_frame == sum(lengths)
    assert plan.observed_frames == sum(lengths)
    assert len(plan.spans) == len(lengths)
    assert len(plan.to_json()) < 2 * 1024 * 1024
    cursor = 0
    for span, length in zip(plan.spans, lengths):
        assert (span.logical_start, span.logical_end) == (cursor, cursor + length)
        cursor += length
    assert sum(w.core_end - w.core_start for w in core_windows(
        plan, core_frames=rate * 30, context_frames=rate * 4)) == sum(lengths)


def test_strict_formats_reject_resampling_downmix_and_codec(tmp_path):
    paths = [write_wav(tmp_path / 'a.wav', b'\x01\x00' * 10000, rate=44100),
             write_wav(tmp_path / 'b.wav', b'\x01\x00' * 10000, rate=48000)]
    sources, _, reader = setup_sources(paths)
    with pytest.raises(UnsupportedAudio, match='Mixed'):
        build_pcm16_plan(sources, [CONTINUOUS], reader)
    stereo = write_wav(tmp_path / 'stereo.wav', b'\x01\x00' * 20000, rate=44100, channels=2)
    mixed, _, reader = setup_sources([paths[0], stereo])
    with pytest.raises(UnsupportedAudio, match='Mixed'):
        build_pcm16_plan(mixed, [CONTINUOUS], reader)
    non16 = write_wav(tmp_path / '24.wav', b'\x01\x00\x00' * 10000, width=3)
    with pytest.raises(UnsupportedAudio):
        inspect_pcm16_source('24', non16)
    invalid = tmp_path / 'compressed.wav'
    invalid.write_bytes(b'OggS' + b'\0' * 100)
    with pytest.raises(UnsupportedAudio):
        inspect_pcm16_source('codec', invalid)
    float_wav = tmp_path / 'float.wav'
    fmt = struct.pack('<HHIIHH', 3, 1, 16000, 64000, 4, 32)
    float_wav.write_bytes(b'RIFF' + struct.pack('<I', 36 + 16) + b'WAVEfmt ' + struct.pack('<I', 16) + fmt + b'data' + struct.pack('<I', 16) + b'\0' * 16)
    with pytest.raises(UnsupportedAudio):
        inspect_pcm16_source('float', float_wav)


def test_read_matcher_bounds_and_no_hidden_silent_padding(tmp_path):
    rate = 8000
    pcm = b'\0\0' * (61 * rate)
    path = write_wav(tmp_path / 'bounded.wav', pcm, rate=rate)
    sources, path_map, reader = setup_sources([path])
    plan = build_pcm16_plan(sources, [], reader, final=True)
    reader = BoundedPCM16Reader(sources, path_map, plan)
    assert reader.read(0, 60 * rate) == pcm[:60 * rate * 2]
    with pytest.raises(ValueError, match='60'):
        reader.read(0, 60 * rate + 1)
    with pytest.raises(ValueError, match='60'):
        reader.read_source('0', 0, 60 * rate + 1)
    with pytest.raises(ValueError, match='30'):
        match_boundary(pcm, pcm, sample_rate=rate, channels=1, declaration=CONTINUOUS)
    assert plan.end_frame == 61 * rate  # silence is actual data, never codec padding
    with pytest.raises(ValueError, match='60'):
        list(core_windows(plan, core_frames=rate * 59, context_frames=rate))


def test_matcher_edge_reads_are_30_seconds_even_on_long_sources(tmp_path):
    rate = 8000
    # 61s files remain ~1 MiB each; the matching search still reads only 30s.
    paths = [write_wav(tmp_path / f'{i}.wav', b'\0\0' * (61 * rate), rate=rate) for i in range(2)]
    sources, _, reader = setup_sources(paths)
    plan = build_pcm16_plan(sources, [CONTINUOUS], reader, final=True)
    evidence = plan.seams[0].evidence
    assert evidence.searched_left_frames == evidence.searched_right_frames == 30 * rate
    assert reader.max_read_frames == 30 * rate
    assert evidence.trim_frames == 0


def test_source_mutation_and_truncation_fail_closed(tmp_path):
    path = write_wav(tmp_path / 'a.wav', b'\1\0' * 1000)
    sources, path_map, reader = setup_sources([path])
    with path.open('r+b') as f:
        f.seek(45)
        f.write(b'\3')
    with pytest.raises(SourceChanged):
        reader.read_source('0', 0, 1)
    with pytest.raises(SourceChanged):
        BoundedPCM16Reader(sources, path_map)
    bad = tmp_path / 'truncated.wav'
    bad.write_bytes(path.read_bytes()[:-2])
    with pytest.raises(UnsupportedAudio, match='Truncated'):
        inspect_pcm16_source('bad', bad)


def test_serialization_tamper_and_invalid_plan_fail(tmp_path):
    plan = synthetic_metadata_plan([16003, 16007])
    checkpoint = PlanCheckpoint(plan, 16000)
    data = json.loads(checkpoint.to_json())
    data['plan']['final'] = False
    with pytest.raises(ValueError, match='hash'):
        PlanCheckpoint.from_json(json.dumps(data))
    data = json.loads(plan.to_json())
    data['spans'][1]['source_start'] = 1
    with pytest.raises(ValueError):
        SourcePlan.from_json(json.dumps(data))
    with pytest.raises(ValueError):
        replace(plan, spans=(replace(plan.spans[0], logical_start=1), *plan.spans[1:])).validate()
    with pytest.raises(ValueError):
        PlanCheckpoint(plan, plan.end_frame + 1).to_json()
    a, b = tmp_path / 'a.json', tmp_path / 'b.json'
    checkpoint.save(a)
    PlanCheckpoint.from_json(a.read_text()).save(b)
    assert a.read_bytes() == b.read_bytes()
    assert not list(tmp_path.glob('*.tmp'))


def test_fake_unit_and_event_ownership_cross_files_and_epoch():
    rate = 16000
    plan = synthetic_metadata_plan([3600 * rate, 300 * rate])
    windows = list(core_windows(plan, core_frames=30 * rate, context_frames=4 * rate))
    # Complete source intervals retained; lexical anchors publish once, even
    # repeated text and intervals crossing a physical file/epoch seam.
    units = [(3599 * rate, 3601 * rate, 3600 * rate, '是'),
             (3601 * rate, 3602 * rate, 3601 * rate, '是')]
    emitted = [u for w in windows for u in units if w.core_start <= u[2] < w.core_end]
    assert emitted == units
    # Simultaneous speakers and continuous event coverage stay independently
    # owned, with no arbitrary merge on same words or anonymous speaker slot.
    events = [('event', 3590 * rate, 3610 * rate),
              ('speaker_A', 3595 * rate, 3605 * rate),
              ('speaker_B', 3598 * rate, 3602 * rate)]
    for _, a, b in events:
        owned = [(max(a, w.core_start), min(b, w.core_end)) for w in windows
                 if max(a, w.core_start) < min(b, w.core_end)]
        assert sum(y - x for x, y in owned) == b - a
        assert all(x[1] == y[0] for x, y in zip(owned, owned[1:]))


def test_rebuild_restart_never_trims_an_alias_twice(tmp_path):
    case = next(c for c in MANIFEST['fixtures'] if c['id'] == 'overlap_2s')
    paths, reference, _ = recipe.make_fixture(case, tmp_path)
    sources, path_map, raw_reader = setup_sources(paths)
    first = build_pcm16_plan(sources, [CONTINUOUS], raw_reader, final=True)
    restored = SourcePlan.from_json(first.to_json())
    view_reader = BoundedPCM16Reader(sources, path_map, restored)
    # Replanning uses the original source method, never the already-trimmed view.
    second = build_pcm16_plan(sources, [CONTINUOUS], view_reader, final=True)
    assert second.sha256 == first.sha256
    assert view_reader.read(0, restored.end_frame) == reference
    assert sum(a.source_end - a.source_start for a in second.aliases) == case['overlap']


def test_reader_descriptor_mismatch_cannot_enter_plan(tmp_path):
    path = write_wav(tmp_path / 'a.wav', b'\1\0' * 1000)
    sources, _, reader = setup_sources([path])
    with pytest.raises(SourceChanged, match='descriptors'):
        build_pcm16_plan((replace(sources[0], sha256='a' * 64),), [], reader)


def test_json_duplicate_unknown_and_nonfinite_fields_fail_closed():
    plan = synthetic_metadata_plan([16000])
    with pytest.raises(ValueError, match='Duplicate'):
        SourcePlan.from_json(plan.to_json()[:-1] + ',"final":true}')
    with pytest.raises(ValueError, match='Unexpected'):
        SourcePlan.from_json(plan.to_json()[:-1] + ',"unexpected":1}')
    with pytest.raises(ValueError, match='Non-finite'):
        SourcePlan.from_json(plan.to_json()[:-1] + ',"unexpected":NaN}')
    checkpoint = PlanCheckpoint(plan, 0).to_json()
    with pytest.raises(ValueError, match='Unexpected'):
        PlanCheckpoint.from_json(checkpoint[:-1] + ',"unexpected":1}')


def test_exact_threshold_rounds_up_at_odd_native_sample_rate():
    rng = random.Random(4081)
    raw = struct.pack('<4001h', *(rng.randrange(-12000, 12001) for _ in range(4001)))
    short = match_boundary(raw[:-2], raw[:-2], sample_rate=8001, channels=1, declaration=CONTINUOUS)
    assert short.reason == 'overlap_below_minimum' and not short.trim_frames
    complete = match_boundary(raw, raw, sample_rate=8001, channels=1, declaration=CONTINUOUS)
    assert complete.trim_frames == 4001


def test_worst_case_30_second_informative_match_has_bounded_working_set():
    rng = random.Random(223141)
    # This is a synthetic maximum-buffer/complexity structural check, not a new
    # threshold-tuning or real-audio validation case. Less than0.5MiB PCM.
    pcm = struct.pack('<240000h', *(rng.randrange(-12000, 12001) for _ in range(240000)))
    evidence = match_boundary(pcm, pcm, sample_rate=8000, channels=1, declaration=CONTINUOUS)
    assert evidence.trim_frames == 240000
    assert evidence.searched_left_frames == evidence.searched_right_frames == 240000


def test_resource_and_integer_validation():
    with pytest.raises(ValueError):
        Source('bad', '0' * 64, 16000, 1, True)
    with pytest.raises(ValueError):
        Continuity('gap', gap_frames=True)
    with pytest.raises(ValueError):
        match_boundary(b'\0\0', b'\0\0', sample_rate=16000, channels=True, declaration=CONTINUOUS)
    with pytest.raises(ValueError):
        match_boundary(b'\0', b'\0', sample_rate=16000, channels=1, declaration=CONTINUOUS)


def test_failed_anchor_search_is_bounded_and_never_trims_after_exhaustion():
    from audio_backend.fragment_matcher import MAX_ANCHOR_HITS
    hardening = json.loads((DATA / 'hardening_freeze.json').read_text())
    assert MAX_ANCHOR_HITS == hardening['additional_budget']['max_anchor_hits']
    frames = 30 * 48000
    right = b'\0\0' * frames
    left = right[:-2] + b'\1\0'
    evidence = match_boundary(left, right, sample_rate=48000, channels=1, declaration=CONTINUOUS)
    assert evidence.reason == 'anchor_search_limit_ambiguous'
    assert evidence.outcome == 'abstain' and evidence.trim_frames == 0
    assert evidence.search_exhausted
    assert evidence.anchor_hits == MAX_ANCHOR_HITS + 1  # one existence sentinel, not a full comparison
    assert not evidence.candidate_frames


def test_symlink_target_swap_during_descriptor_open_is_rejected(tmp_path, monkeypatch):
    a = write_wav(tmp_path / 'a.wav', b'\1\0' * 8000, rate=8000)
    b = write_wav(tmp_path / 'b.wav', b'\2\0' * 8000, rate=8000)
    binding = tmp_path / 'binding.wav'
    binding.symlink_to(a)
    sources, _, reader = setup_sources([binding])
    original_open = Path.open

    def swap_only_during_open(path, *args, **kwargs):
        if path == binding:
            binding.unlink()
            binding.symlink_to(b)
            opened = original_open(path, *args, **kwargs)
            binding.unlink()
            binding.symlink_to(a)
            return opened
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', swap_only_during_open)
    with pytest.raises(SourceChanged, match='descriptor'):
        reader.read_source('0', 0, 16)
    assert binding.resolve() == a
    assert hashlib.sha256(a.read_bytes()).hexdigest() == sources[0].sha256


def test_path_switch_during_wave_open_cannot_redirect_bound_descriptor(tmp_path, monkeypatch):
    a = write_wav(tmp_path / 'a.wav', b'\1\0' * 8000, rate=8000)
    b = write_wav(tmp_path / 'b.wav', b'\2\0' * 8000, rate=8000)
    binding = tmp_path / 'binding.wav'
    binding.symlink_to(a)
    sources, _, reader = setup_sources([binding])
    original_open = wave.open

    def swap_only_during_wave_open(file, mode):
        binding.unlink()
        binding.symlink_to(b)
        opened = original_open(file, mode)
        binding.unlink()
        binding.symlink_to(a)
        return opened

    monkeypatch.setattr(wave, 'open', swap_only_during_wave_open)
    assert reader.read_source('0', 0, 16) == b'\1\0' * 16
    # Inspection also hashes/decodes the bound A descriptor, not a mix of A/B.
    assert inspect_pcm16_source('0', binding) == sources[0]


def test_live_exact_grid_zero_context_eos_is_explicit_control(tmp_path):
    live = synthetic_metadata_plan([8000], rate=8000, final=False)
    windows = list(core_windows(live, core_frames=8000, context_frames=0))
    assert len(windows) == 1 and not windows[0].finish_native
    checkpoint = PlanCheckpoint(live, windows[0].core_end)
    assert checkpoint.eos_control() is None
    final = replace(live, final=True)
    checkpoint = checkpoint.extend(final)
    assert not list(core_windows(final, core_frames=8000, context_frames=0,
                                 start_frame=checkpoint.next_frame))
    # Zero new PCM is not zero need to finish. This is a control message only.
    control = checkpoint.eos_control()
    assert control.final_frame == 8000 and control.epoch_index == 0
    assert control.reason == 'true_eos'
    checkpoint.save(tmp_path / 'pending.json')
    assert PlanCheckpoint.from_json((tmp_path / 'pending.json').read_text()).eos_control() == control
    acknowledged = checkpoint.acknowledge_eos()
    acknowledged.save(tmp_path / 'finished.json')
    assert PlanCheckpoint.from_json((tmp_path / 'finished.json').read_text()).eos_control() is None
    with pytest.raises(ValueError):
        acknowledged.acknowledge_eos()
    with pytest.raises(ValueError):
        PlanCheckpoint(live, 8000, eos_finished=True).validate()


def test_checkpoint_integrity_covers_cursor_and_eos_acknowledgement():
    checkpoint = PlanCheckpoint(synthetic_metadata_plan([8000], rate=8000), 0)
    original = json.loads(checkpoint.to_json())
    for field, value in [('next_frame', 8000), ('eos_finished', True)]:
        data = dict(original, **{field: value})
        with pytest.raises(ValueError, match='envelope hash'):
            PlanCheckpoint.from_json(json.dumps(data))


def test_core_and_eos_share_fixed_native_epoch_contract():
    plan = synthetic_metadata_plan([8000], rate=8000)
    with pytest.raises(ValueError, match='fixed to300 seconds'):
        list(core_windows(plan, core_frames=8000, context_frames=0, epoch_frames=4000))
    windows = list(core_windows(plan, core_frames=8000, context_frames=0, epoch_frames=300 * 8000))
    control = PlanCheckpoint(plan, 8000).eos_control()
    assert windows[-1].epoch_index == control.epoch_index == 0
