"""Frozen p1 stress plus unchanged q1 matrix; retain trust-limit diagnostics."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import tempfile

import pytest

from audio_backend.fragment_core import BoundedPCM16Reader, SourcePlan, build_pcm16_plan
from audio_backend.fragment_matcher import Continuity
from audio_backend.fragment_matcher_p1 import POLICY, native_alias_correlation

HERE = Path(__file__).parent
DATA = HERE / 'fragment_core_data' / 'p1'
MANIFEST = json.loads((DATA / 'freeze.json').read_text())
HASHES = {row['id']: row for row in json.loads((DATA / 'frozen_hashes.json').read_text())}
Q1_HASHES = json.loads((DATA / 'q1_plan_hashes.json').read_text())


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recipe = load(DATA / 'fixture_recipe.py', 'p1_frozen_recipe')
old = load(HERE / 'test_fragment_matcher_v2.py', 'p1_reuses_q1_frozen_matrix')
old.POLICY = POLICY


@pytest.mark.parametrize('case', old.MANIFEST['cases'], ids=lambda c: c['id'])
def test_old40_under_p1(case, tmp_path, monkeypatch):
    root = Path(os.environ['FRAGMENT_RESULTS_DIR']).parent
    namespace = tmp_path / 'isolated-results'
    namespace.mkdir()
    with monkeypatch.context() as context:
        context.setenv('FRAGMENT_RESULTS_DIR', str(namespace / 'matrix'))
        try:
            old.test_frozen_v2_matrix(case, tmp_path)
        finally:
            source = namespace / 'v2' / 'synthetic-matrix' / (case['id'] + '.json')
            if source.exists():
                destination = root / 'p1' / 'old40-matrix'
                destination.mkdir(parents=True, exist_ok=True)
                (destination / source.name).write_bytes(source.read_bytes())


@pytest.mark.parametrize('case', MANIFEST['cases'], ids=lambda c: c['id'])
def test_new_alias_and_trust_stress(case, tmp_path):
    parts, oracle, reference = recipe.make_fixture(case)
    frozen = HASHES[case['id']]
    assert [hashlib.sha256(x).hexdigest() for x in parts] == frozen['pieces_sha256']
    assert oracle == frozen['oracle_overlap_frames']
    with tempfile.TemporaryDirectory(prefix='p1-pcm-', dir=tmp_path) as directory:
        sources, paths, reader = old.source_setup(parts, directory, case['rate'], case['channels'])
        declaration = Continuity(case['relation'], case['same_capture'], case['replay_possible'])
        plan = build_pcm16_plan(sources, [declaration], reader, final=True, policy=POLICY)
        evidence = plan.seams[0].evidence
        trim = evidence.trim_frames
        native = native_alias_correlation(parts[1][:(case['rate'] + 1) // 2 * case['channels'] * 2], case['rate'], case['channels'])
        false_assertion = case['expected'] == 'indistinguishable_trim_under_false_assertion'
        expected_trim = (case['rate'] + 1) // 2 if case['expected'] == 'trim' or false_assertion else 0
        result = {'id': case['id'], 'policy_id': POLICY, 'physical_truth': case['physical_truth'],
                  'expected_policy_or_contract_outcome': case['expected'], 'actual_trim_frames': trim,
                  'oracle_actual_shared_frames': oracle,
                  'actual_new_observation_frames_deleted': trim if trim and oracle == 0 else 0,
                  'trusted_provenance_contract_satisfied': not false_assertion,
                  'scored_as_policy_safety_guarantee': not false_assertion,
                  'contract_limitation': case.get('trust_contract'), 'evidence': evidence.to_dict(),
                  'native_alias_correlation': native, 'plan_sha256': plan.sha256,
                  'source_sha256': [s.sha256 for s in sources], 'expected_action_pass': trim == expected_trim,
                  'no_models': True}
        output = Path(os.environ['FRAGMENT_RESULTS_DIR']).parent / 'p1' / 'new-stress'
        output.mkdir(parents=True, exist_ok=True)
        (output / (case['id'] + '.json')).write_text(json.dumps(result, indent=2) + '\n')
        assert trim == expected_trim
        assert SourcePlan.from_json(plan.to_json()).sha256 == plan.sha256
        if case['kind'] == 'locked_tone_jitter':
            assert native['peak'] >= .95
        if reference is not None and trim == oracle:
            view = BoundedPCM16Reader(sources, paths, plan)
            assert view.read(0, plan.end_frame) == reference
        if false_assertion:
            assert trim > 0 and oracle == 0  # Visible trust limitation, never a safe-origin claim.


@pytest.mark.parametrize('case', old.MANIFEST['cases'], ids=lambda c: c['id'])
def test_q1_historical_plan_hash_unchanged(case, tmp_path):
    parts, _, _ = old.recipe.make_fixture(case)
    with tempfile.TemporaryDirectory(prefix='q1-identity-', dir=tmp_path) as directory:
        sources, _, reader = old.source_setup(parts, directory, case['rate'], case['channels'])
        declaration = Continuity(case['relation'], case['same_capture'], case['replay_possible'], case.get('gap_frames'))
        plan = build_pcm16_plan(sources, [declaration] * (len(sources) - 1), reader,
                               final=True, policy='exact-pcm16-v2q1')
        assert plan.sha256 == Q1_HASHES[case['id']]


def test_native_centering_zero_variance_overflow_and_channel_isolation():
    zero = native_alias_correlation(recipe.pcm([16000] * 8000), 16000, 1)
    assert zero['peak'] == 1.0
    assert zero['channels'][0]['status'] == 'zero_variance_conservative_reject'
    for values in ([32750 + (i % 2) for i in range(8000)],
                   [-32768 if i % 2 else 32767 for i in range(8000)]):
        result = native_alias_correlation(recipe.pcm(values), 16000, 1)
        assert result['peak'] == 1.0
        assert result['channels'][0]['centered_variance_x_n'] > 0
    rng = random.Random(777881)
    values = [value for i in range(8000) for value in (16000, rng.randrange(-30, 31))]
    stereo = native_alias_correlation(recipe.pcm(values), 16000, 2)
    assert stereo['channels'][0]['centered_variance_x_n'] == 0
    assert stereo['channels'][1]['centered_variance_x_n'] > 0
    assert stereo['channels'][1]['correlation'] < .95


@pytest.mark.parametrize('rate', [8000, 8001, 16000, 44100, 48000])
def test_native_lag_geometry_and_pair_bounds(rate):
    result = native_alias_correlation(recipe.pcm([1, -1] * ((rate + 1) // 2)), rate, 1)
    assert result['lag_frames'] % (rate // 400) == 0
    assert result['lag_frames'] >= (rate * 20 + 999) // 1000
    assert result['pair_count_per_channel'] <= 30 * rate


def test_maximum_native_stereo_check_is_bounded():
    #5.76MB input bytes; no WAV writes/full-file reads/per-frame float arrays.
    pcm = b'\x01\x00\x02\x00' * (30 * 48000)
    result = native_alias_correlation(pcm, 48000, 2)
    assert result['pair_count_per_channel'] == 30 * 48000 - 960
    assert len(result['channels']) == 2 and result['peak'] == 1.0
    with pytest.raises(ValueError, match='30s'):
        native_alias_correlation(pcm + b'\0\0\0\0', 48000, 2)


def test_replay_truth_controls_have_identical_pcm_inputs():
    assert HASHES['honest-replay']['pieces_sha256'] == HASHES['false-replay-exclusion']['pieces_sha256']
