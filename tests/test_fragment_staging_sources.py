"""Exact reviewed imports and unchanged privacy gates for the public projection."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('staging_guard', ROOT / 'scripts/check_public_tree.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
guard.ROOT = ROOT


def public_source_files(policy):
    # Exported archives have no Git metadata. Public staging/history gates
    # separately reject unlisted paths; here verify every promised source byte.
    files = {}
    for name in policy['allowed_files']:
        path = ROOT / name
        assert path.is_file() and not path.is_symlink(), name
        files[name] = path.read_bytes()
    return files


def test_reviewed_import_bytes_are_exact():
    provenance = json.loads((ROOT / 'docs/FRAGMENT_STAGING_IMPORTS.json').read_text())
    for component in provenance['imports']:
        for row in component['files']:
            data = (ROOT / row['path']).read_bytes()
            assert len(data) == row['bytes'], row['path']
            assert hashlib.sha256(data).hexdigest() == row['sha256'], row['path']


def test_complete_source_tree_passes_exact_allowlist():
    policy = json.loads((ROOT / 'PUBLIC_POLICY.json').read_text())
    files = public_source_files(policy)
    assert set(files) == set(policy['allowed_files'])
    result = guard.validate(files, policy, require_manifest=True)
    assert result['findings'] == 0
    assert policy['maximum_files'] == 250
    assert policy['maximum_total_bytes'] == 2_500_000
    assert guard.MAX_BYTES == 250_000


@pytest.mark.parametrize('path,data', [
    ('reports/private/state.json', b'{}'),
    ('models/model.onnx', b'weight'),
    ('data/state.db', b'state'),
    ('docs/unreviewed.md', b'unreviewed'),
    ('docs/vector.json', b'{"embedding": [0.1, 0.2]}'),
])
def test_private_and_unreviewed_source_is_rejected(path, data):
    policy = json.loads((ROOT / 'PUBLIC_POLICY.json').read_text())
    files = public_source_files(policy)
    files[path] = data
    with pytest.raises(ValueError):
        guard.validate(files, policy, require_manifest=True)


def test_normalizer_is_not_runtime_or_http_integrated():
    for path in ('app.py', 'native_runner.py', 'native_worker.py',
                 'fragment_runtime.py', 'fragment_model_bridges.py'):
        assert 'fragment_normalizer' not in (ROOT / 'audio_backend' / path).read_text()


def test_private_output_admission_and_public_gate_exclusion():
    from audio_backend.fragment_model_compare import require_private_output
    with pytest.raises(ValueError, match='private_'):
        require_private_output(ROOT / 'reports' / 'fragment-model-bridge')
    for folder in ('audio_backend', 'docs'):
        with pytest.raises(ValueError, match='reports/private_'):
            require_private_output(ROOT / folder / 'private_run')
    assert require_private_output(ROOT / 'reports/private_fragment_models').name == 'private_fragment_models'
    policy = json.loads((ROOT / 'PUBLIC_POLICY.json').read_text())
    files = public_source_files(policy)
    files['reports/private_fragment_models/evidence/fake.json'] = b'{"synthetic": true}'
    with pytest.raises(ValueError, match='forbidden path'):
        guard.validate(files, policy, require_manifest=True)
