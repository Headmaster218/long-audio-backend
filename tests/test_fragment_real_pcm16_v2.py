"""Same frozen14 real arms once under v2q1; never retune on these results."""
from functools import partial
import importlib.util
import json
import os
from pathlib import Path

import pytest

from audio_backend.fragment_core import build_pcm16_plan
from audio_backend.fragment_matcher_v2 import POLICY

path = Path(__file__).with_name('test_fragment_real_pcm16.py')
spec = importlib.util.spec_from_file_location('frozen_real_v1_harness_reused', path)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.build_pcm16_plan = partial(build_pcm16_plan, policy=POLICY)
pytestmark = base.pytestmark


@pytest.mark.parametrize('arm_id', base.ARM_IDS)
def test_frozen_real_pcm_matrix_v2(arm_id, tmp_path, monkeypatch):
    original = Path(os.environ['FRAGMENT_RESULTS_DIR'])
    destination = original.parent / 'v2' / 'matrix'
    destination.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('FRAGMENT_RESULTS_DIR', str(destination))
    base.test_frozen_real_pcm_matrix(arm_id, tmp_path)
    output = destination.parent / 'real-matrix' / (arm_id.replace(':', '-') + '.json')
    data = json.loads(output.read_text())
    data['tested_policy_id'] = POLICY
    data['reference_v1_manifest_role'] = 'Original input/oracle/declaration truth preserved; only information gate is v2q1'
    data['v1_harness_source_unchanged'] = True
    output.write_text(json.dumps(data, indent=2) + '\n')
