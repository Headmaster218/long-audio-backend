"""Exact reviewed source imports and unchanged privacy gates for isolated staging."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('staging_guard', ROOT / 'scripts/source_staging_guard.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
guard.ROOT = ROOT


def test_reviewed_import_bytes_are_exact():
    provenance = json.loads((ROOT / 'docs/FRAGMENT_STAGING_IMPORTS.json').read_text())
    for component in provenance['imports']:
        for row in component['files']:
            data = (ROOT / row['path']).read_bytes()
            assert len(data) == row['bytes'], row['path']
            assert hashlib.sha256(data).hexdigest() == row['sha256'], row['path']


def test_complete_source_tree_passes_exact_allowlist():
    policy = json.loads((ROOT / 'docs/FRAGMENT_STAGING_POLICY.json').read_text())
    files = guard.read_git_tree(':')
    assert set(files) == set(policy['allowed_files'])
    result = guard.validate(files, policy, require_manifest=False)
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
    policy = json.loads((ROOT / 'docs/FRAGMENT_STAGING_POLICY.json').read_text())
    files = guard.read_git_tree(':')
    files[path] = data
    with pytest.raises(ValueError):
        guard.validate(files, policy, require_manifest=False)


def test_normalizer_is_not_runtime_or_http_integrated():
    for path in ('app.py', 'native_runner.py', 'native_worker.py',
                 'fragment_runtime.py', 'fragment_model_bridges.py'):
        assert 'fragment_normalizer' not in (ROOT / 'audio_backend' / path).read_text()
