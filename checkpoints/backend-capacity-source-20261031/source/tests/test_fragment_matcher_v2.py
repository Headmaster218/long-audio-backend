"""Frozen v2 development/stress; report every result before any policy assertion."""
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import tempfile
import wave

import pytest

from audio_backend.fragment_core import BoundedPCM16Reader, PlanCheckpoint, SourcePlan, build_pcm16_plan, inspect_pcm16_source
from audio_backend.fragment_matcher import Continuity
from audio_backend.fragment_matcher_v2 import POLICY, anchor_statistics, quantized_statistics

DATA = Path(__file__).parent / 'fragment_core_data'
V2 = DATA / 'v2'
MANIFEST = json.loads((V2 / 'freeze.json').read_text())
HASHES = {row['id']: row for row in json.loads((V2 / 'frozen_hashes.json').read_text())}
V1_HASHES = json.loads((V2 / 'v1_plan_hashes.json').read_text())


def load_recipe(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recipe = load_recipe(V2 / 'fixture_recipe.py', 'v2_frozen_recipe')
v1_recipe = load_recipe(DATA / 'fixture_recipe.py', 'v1_frozen_recipe')


def source_setup(pieces, directory, rate, channels):
    paths = []
    for i, pcm in enumerate(pieces):
        path = Path(directory) / f'{i}.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(channels)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(pcm)
        paths.append(path)
    assert sum(p.stat().st_size for p in paths) < 5 * 1024 ** 2
    sources = tuple(inspect_pcm16_source(str(i), p) for i, p in enumerate(paths))
    bindings = {s.fragment_id: p for s, p in zip(sources, paths)}
    return sources, bindings, BoundedPCM16Reader(sources, bindings)


@pytest.mark.parametrize('case', MANIFEST['cases'], ids=lambda c: c['partition'] + '-' + c['id'])
def test_frozen_v2_matrix(case, tmp_path):
    pieces, oracle, reference = recipe.make_fixture(case)
    frozen = HASHES[case['id']]
    assert [hashlib.sha256(p).hexdigest() for p in pieces] == frozen['pieces_sha256']
    assert [len(p) for p in pieces] == frozen['pieces_bytes']
    assert oracle == frozen['oracle_overlap_frames']
    if reference is not None:
        assert hashlib.sha256(reference).hexdigest() == frozen['reference_sha256']
    with tempfile.TemporaryDirectory(prefix='v2-pcm-', dir=tmp_path) as directory:
        sources, paths, reader = source_setup(pieces, directory, case['rate'], case['channels'])
        declaration = Continuity(case['relation'], case['same_capture'], case['replay_possible'], case.get('gap_frames'))
        declarations = [declaration] * (len(sources) - 1)
        plan = build_pcm16_plan(sources, declarations, reader, final=True, policy=POLICY)
        inferred = [s.evidence.trim_frames for s in plan.seams]
        actual = 'trim' if inferred and all(n > 0 for n in inferred) else 'abstain'
        policy_pass = actual == case['expected']
        wrong_source_deletion = sum(n for n, truth in zip(inferred, oracle) if n and n != truth)
        view = BoundedPCM16Reader(sources, paths, plan)
        observed = b''.join(view.read(s.logical_start, s.logical_end) for s in plan.spans)
        expected_observed = pieces[0] + b''.join(p[n * case['channels'] * 2:] for p, n in zip(pieces[1:], inferred))
        anchors = []
        for seam, part in zip(plan.seams, pieces[1:]):
            candidates = seam.evidence.candidate_frames
            anchors.append(anchor_statistics(part[:candidates[0] * case['channels'] * 2], case['rate'], case['channels'])
                           if len(candidates) == 1 and candidates[0] >= (case['rate'] + 1) // 2 else [])
        result = {'id': case['id'], 'partition': case['partition'], 'policy_id': POLICY,
                  'physical_truth': case['physical_truth'], 'expected_policy_action': case['expected'],
                  'actual_policy_action': actual, 'policy_expectation_pass': policy_pass,
                  'oracle_overlap_frames': oracle, 'inferred_trim_frames': inferred,
                  'wrong_source_deletion_frames': wrong_source_deletion,
                  'policy_abstention_control_accepted': case['expected'] == 'abstain' and actual == 'trim',
                  'true_duplicate_missed': case['expected'] == 'trim' and actual == 'abstain',
                  'seams': [s.evidence.to_dict() for s in plan.seams], 'anchor_statistics': anchors,
                  'plan_sha256': plan.sha256, 'original_source_sha256': [s.sha256 for s in sources],
                  'observed_frames': plan.observed_frames, 'end_frame': plan.end_frame,
                  'reference_equal': observed == reference if reference is not None and inferred == oracle else None,
                  'no_models': True, 'thresholds_changed_after_freeze': False}
        destination = Path(os.environ['FRAGMENT_RESULTS_DIR']).parent / 'v2' / 'synthetic-matrix'
        destination.mkdir(parents=True, exist_ok=True)
        (destination / (case['id'] + '.json')).write_text(json.dumps(result, indent=2) + '\n')
        # Results must survive a failed predeclared expectation. A failure blocks
        # acceptance, never silently changes physical truth or policy labels.
        assert observed == expected_observed
        assert wrong_source_deletion == 0
        assert plan.version == POLICY
        assert SourcePlan.from_json(plan.to_json()).sha256 == plan.sha256
        assert build_pcm16_plan(sources, declarations, view, final=True, policy=POLICY).sha256 == plan.sha256
        if reference is not None and inferred == oracle:
            assert observed == reference
        assert policy_pass, f"Frozen {case['partition']} expectation failed: {case['id']}; results retained"


@pytest.mark.parametrize('levels', [15, 16])
def test_distinct_level_boundary(levels):
    stats = quantized_statistics([i % levels for i in range(1280)])
    assert stats['distinct_sample_levels'] == levels
    assert stats['checks']['sample_levels'] == (levels >= 16)


@pytest.mark.parametrize('count', [319, 320, 321])
def test_modal_fraction_boundary(count):
    values = [0] * count + [1 + i % 31 for i in range(1280 - count)]
    stats = quantized_statistics(values)
    assert stats['modal_count'] == count
    assert stats['checks']['modal_fraction'] == (count <= 320)


@pytest.mark.parametrize('count', [319, 320, 321])
def test_transition_fraction_boundary(count):
    changing = [i % 17 for i in range(count + 1)]
    values = changing + [changing[-1]] * (1281 - len(changing))
    stats = quantized_statistics(values)
    assert stats['changed_transitions'] == count
    assert stats['transition_denominator'] == 1280
    assert stats['checks']['transition_fraction'] == (count >= 320)


@pytest.mark.parametrize('count', [7, 8])
def test_signed_nonzero_difference_boundary(count):
    values = [0]
    for i in range(1279):
        values.append(values[-1] + i % count + 1)
    stats = quantized_statistics(values)
    assert stats['distinct_signed_nonzero_deltas'] == count
    assert stats['checks']['signed_nonzero_delta_types'] == (count >= 8)


def test_widened_difference_and_rounding_invariance_limits():
    stats = quantized_statistics([-32768, 32767, -32768])
    assert (stats['minimum_delta'], stats['maximum_delta']) == (-65535, 65535)
    assert quantized_statistics([0, 2, 4])['distinct_signed_nonzero_deltas'] == 1
    assert quantized_statistics([0, 2, 3])['distinct_signed_nonzero_deltas'] == 2


def test_channelwise_information_never_uses_interleaving():
    rng = random.Random(7790)
    values = [value for _ in range(8000) for value in (rng.randrange(-20, 21), 10000)]
    rows = anchor_statistics(recipe.encode(values), 16000, 2)
    assert len(rows) == 3
    for row in rows:
        assert row['channels'][0]['qualifies']
        assert row['channels'][1]['distinct_sample_levels'] == 1
        assert row['channels'][1]['changed_transitions'] == 0
        assert not row['qualifies']


@pytest.mark.parametrize('case', json.loads((DATA / 'freeze.json').read_text())['fixtures'], ids=lambda c: c['id'])
def test_original_v1_plan_identity_unchanged(case, tmp_path):
    paths, _, _ = v1_recipe.make_fixture(case, tmp_path)
    sources = tuple(inspect_pcm16_source(str(i), p) for i, p in enumerate(paths))
    bindings = {s.fragment_id: p for s, p in zip(sources, paths)}
    reader = BoundedPCM16Reader(sources, bindings)
    declaration = Continuity(case.get('relation', 'continuous'), case.get('same_capture', True),
                            case.get('replay_possible', False), case.get('gap_frames'))
    plan = build_pcm16_plan(sources, [declaration], reader, final=True)
    assert plan.sha256 == V1_HASHES[case['id']]
    assert plan.seams[0].evidence.outcome == case['expected']
    assert plan.seams[0].evidence.trim_frames == case['expected_trim']


def test_policy_version_is_bound_to_plan_and_extension(tmp_path):
    case = MANIFEST['cases'][0]
    pieces, _, _ = recipe.make_fixture(case)
    sources, paths, reader = source_setup(pieces[:1], tmp_path, case['rate'], case['channels'])
    v1 = build_pcm16_plan(sources, [], reader)
    v2 = build_pcm16_plan(sources, [], reader, policy=POLICY)
    assert v1.version != v2.version and v1.sha256 != v2.sha256
    with pytest.raises(ValueError, match='policy'):
        PlanCheckpoint(v1, 0).extend(v2)
    with pytest.raises(ValueError, match='Unsupported'):
        build_pcm16_plan(sources, [], reader, policy='unrecognized')
