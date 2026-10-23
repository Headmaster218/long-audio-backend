"""Finite frozen-policy binding controls, no product/model/SQL/service import."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from ab_profile import PROFILE, validate_inputs
from verified_policy_binding import POLICY, POLICY_RELATIVE, POLICY_SHA, verify_policy, validate_effective_policy


@pytest.fixture
def bound(tmp_path):
    original = Path(__file__).parent.parent/'audio-recovered-commit-diagnostics-rev01-20261023'/POLICY_RELATIVE
    data = original.read_bytes()  # Static frozen source JSON only.
    assert hashlib.sha256(data).hexdigest() == POLICY_SHA
    source = tmp_path/'product'
    path = source/POLICY_RELATIVE
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    value = dict(qualification='AB-two-session', product_source=str(source),
        files=[dict(path=str(path), bytes=len(data), sha256=POLICY_SHA)],
        settings=dict(verified_identity_policy_file=str(path)))
    return value, path


def test_exact_existing_policy_and_effective_settings_are_accepted(bound):
    value, path = bound
    verify_policy(value, time.monotonic()+5)
    validate_effective_policy(SimpleNamespace(verified_identity_policy_file=path,
        verified_identity_policy=dict(POLICY)), value)
    assert POLICY == dict(schema_version=1, experimental=True, single_threshold=.75,
        centroid_threshold=.75, margin=.10, minimum_query_seconds=2., minimum_enrollment_seconds=2.)
    assert PROFILE['identity_mode'] == 'verified_review_v1'
    assert PROFILE['identity_group'] == 'AB-verified-anonymous'
    assert PROFILE['verified_policy_sha256'] == POLICY_SHA
    assert PROFILE['reviewed_primary_persons_enabled'] is False


@pytest.mark.parametrize('fault', ['absent_path','external_path','missing_pin','duplicate_pin',
    'wrong_pin','size','threshold','schema2','oversize','fifo','symlink','deadline'])
def test_policy_cannot_be_relaxed_relocated_or_unpinned(bound, fault):
    value, path = bound
    deadline = time.monotonic()+5
    if fault == 'absent_path': value['settings'].pop('verified_identity_policy_file')
    if fault == 'external_path': value['settings']['verified_identity_policy_file'] = str(path.parent/'other.json')
    if fault == 'missing_pin': value['files'] = []
    if fault == 'duplicate_pin': value['files'].append(dict(value['files'][0]))
    if fault == 'wrong_pin': value['files'][0]['sha256'] = '0'*64
    if fault == 'size': value['files'][0]['bytes'] += 1
    if fault in ('threshold','schema2'):
        policy = dict(POLICY)
        policy['single_threshold' if fault == 'threshold' else 'schema_version'] = .5 if fault == 'threshold' else 2
        path.write_text(json.dumps(policy))
        value['files'][0]['bytes'] = path.stat().st_size
    if fault == 'oversize': path.write_bytes(b'x'*8193)
    if fault == 'fifo': path.unlink(); os.mkfifo(path)
    if fault == 'symlink':
        target = path.parent/'target'; path.rename(target); path.symlink_to(target)
    if fault == 'deadline': deadline = 0
    with pytest.raises((ValueError, OSError, TimeoutError)):
        verify_policy(value, deadline)


@pytest.mark.parametrize('fault', ['path', 'policy', 'legacy_mode', 'old_group'])
def test_effective_policy_or_profile_cannot_silently_downgrade(bound, fault):
    value, path = bound
    if fault in ('path','policy'):
        settings = SimpleNamespace(verified_identity_policy_file=path,
                                   verified_identity_policy=dict(POLICY))
        if fault == 'path': settings.verified_identity_policy_file = path.parent/'other'
        if fault == 'policy': settings.verified_identity_policy['margin'] = 0
        with pytest.raises(ValueError): validate_effective_policy(settings, value)
    else:
        value['integration_profile'] = copy.deepcopy(PROFILE)
        value['integration_profile']['identity_mode' if fault == 'legacy_mode' else 'identity_group'] = (
            'legacy' if fault == 'legacy_mode' else 'AB-anonymous')
        with pytest.raises(ValueError, match='profile'):
            validate_inputs(value, set(), time.monotonic()+5)


@pytest.mark.parametrize('kind', ['lifecycle-only','short-operational','A-operational','B-operational'])
def test_single_session_profiles_cannot_gain_policy_override(bound, kind):
    value, path = bound
    value['qualification'] = kind
    with pytest.raises(ValueError): verify_policy(value, time.monotonic()+5)
    value['settings']['verified_identity_policy_file'] = None
    verify_policy(value, time.monotonic()+5)
    validate_effective_policy(SimpleNamespace(verified_identity_policy=None), value)


def test_actual_prepare_checks_policy_before_Settings_and_effective_policy_after():
    tree = ast.parse((Path(__file__).parent/'launch_binding.py').read_text())
    calls = {node.func.id: node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id in ('verify_manifest','verify_policy','Settings','validate_effective_policy')}
    assert calls['verify_manifest'] < calls['verify_policy'] < calls['Settings'] < calls['validate_effective_policy']


def test_policy_response_after_deadline_is_not_accepted(bound, monkeypatch):
    import verified_policy_binding
    readings = iter((0., 2.))
    monkeypatch.setattr(verified_policy_binding.time, 'monotonic', lambda: next(readings))
    with pytest.raises(TimeoutError): verify_policy(bound[0], 1.)


def test_new_Settings_delta_is_only_path_policy_and_capacity():
    root = Path(__file__).parent
    new = json.loads((root/'settings.AB.json').read_bytes())
    old = json.loads((root.parent/'audio-recovery-runner-rev08-20261023/settings.AB.json').read_bytes())
    assert new.pop('data_dir') != old.pop('data_dir')
    assert new.pop('verified_identity_policy_file').endswith('/'+POLICY_RELATIVE)
    assert new == old
    assert new['max_sessions'] == 2 and new['reviewed_primary_persons_enabled'] is False
