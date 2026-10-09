"""Same frozen14 real arms once under v2q1p1; never retune on these results."""
from functools import partial
import importlib.util
import json
import os
from pathlib import Path

import pytest

from audio_backend.fragment_core import build_pcm16_plan
from audio_backend.fragment_matcher_p1 import POLICY

path = Path(__file__).with_name('test_fragment_real_pcm16.py')
spec = importlib.util.spec_from_file_location('frozen_real_p1_harness', path)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.build_pcm16_plan = partial(build_pcm16_plan, policy=POLICY)
pytestmark = base.pytestmark


@pytest.mark.parametrize('arm_id', base.ARM_IDS)
def test_frozen_real_pcm_matrix_p1(arm_id, tmp_path, monkeypatch):
    original = Path(os.environ['FRAGMENT_RESULTS_DIR'])
    destination = original.parent / 'p1' / 'matrix'
    destination.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('FRAGMENT_RESULTS_DIR', str(destination))
    base.test_frozen_real_pcm_matrix(arm_id, tmp_path)
    output = destination.parent / 'real-matrix' / (arm_id.replace(':', '-') + '.json')
    data = json.loads(output.read_text())
    data['tested_policy_id'] = POLICY
    data['reference_v1_manifest_role'] = 'Original input/oracle/declaration truth preserved; v2q1 information gate plus separate p1 native periodic veto'
    data['v1_harness_source_unchanged'] = True
    output.write_text(json.dumps(data, indent=2) + '\n')
