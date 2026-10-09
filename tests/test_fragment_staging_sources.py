"""Exact layered source lineage and unchanged public privacy gates."""
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


def reviewed_imports(base, layers):
    """Resolve explicit reviewed updates without rewriting historical import facts."""
    assert layers['schema_version'] == 1
    effective = {}
    for component in base['imports']:
        for row in component['files']:
            assert row['path'] not in effective
            effective[row['path']] = row
    assert [layer['component'] for layer in layers['updates']] == layers['reviewed_layer_order']
    updated = []
    for layer in layers['updates']:
        seen = set()
        for row in layer['files']:
            path = row['path']
            assert path not in seen, 'Duplicate override in reviewed layer'
            seen.add(path)
            previous = effective.get(path)
            if previous is not None:
                assert row['supersedes'] == {
                    'source_commit': previous['source_commit'], 'sha256': previous['sha256'],
                }, 'Unreviewed or stale override'
                assert row['sha256'] != previous['sha256'], 'Duplicate unchanged override'
            else:
                assert 'supersedes' not in row
            effective[path] = row
            updated.append(path)
    assert sorted(updated) == layers['expected_updated_paths'], 'Missing or unexpected import path'
    return effective


def import_records():
    base_bytes = (ROOT / 'docs/FRAGMENT_STAGING_IMPORTS.json').read_bytes()
    layers = json.loads((ROOT / 'docs/FRAGMENT_STAGING_LAYERS.json').read_text())
    assert hashlib.sha256(base_bytes).hexdigest() == layers['base_manifest_sha256']
    return json.loads(base_bytes), layers


def verify_import_bytes(records, read_bytes):
    for path, row in records.items():
        data = read_bytes(path)
        assert len(data) == row['bytes'], path
        assert hashlib.sha256(data).hexdigest() == row['sha256'], path


def test_reviewed_import_bytes_are_exact():
    base, layers = import_records()
    verify_import_bytes(reviewed_imports(base, layers), lambda path: (ROOT / path).read_bytes())


def verify_target_version(layers, module_version, project_version):
    assert layers['target_version'] == module_version == project_version


def test_reviewed_target_version_is_current():
    import tomllib
    from audio_backend import __version__
    _, layers = import_records()
    verify_target_version(layers, __version__,
                          tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version'])


@pytest.mark.parametrize('stale', ['target', 'module', 'project'])
def test_stale_version_is_rejected(stale):
    _, layers = import_records()
    current = layers['target_version']
    if stale == 'target':
        layers['target_version'] = 'stale'
    with pytest.raises(AssertionError):
        verify_target_version(layers, 'stale' if stale == 'module' else current,
                              'stale' if stale == 'project' else current)


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'stale_override'])
def test_invalid_layered_lineage_is_rejected(mutation):
    base, layers = import_records()
    if mutation == 'missing':
        layers['updates'][0]['files'].pop()
    elif mutation == 'duplicate':
        layers['updates'][0]['files'].append(layers['updates'][0]['files'][0])
    else:
        row = next(row for row in layers['updates'][0]['files'] if 'supersedes' in row)
        row['supersedes']['sha256'] = '0' * 64
    with pytest.raises(AssertionError):
        reviewed_imports(base, layers)


def test_unreviewed_import_bytes_are_rejected():
    base, layers = import_records()
    effective = reviewed_imports(base, layers)
    with pytest.raises(AssertionError):
        verify_import_bytes(effective, lambda path: (ROOT / path).read_bytes() + b'\n')


def test_missing_import_file_is_not_silently_skipped():
    base, layers = import_records()
    def missing(path):
        raise FileNotFoundError(path)
    with pytest.raises(FileNotFoundError):
        verify_import_bytes(reviewed_imports(base, layers), missing)


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


def test_new_experiments_are_not_http_or_production_wired():
    for path in ('app.py', 'native_runner.py', 'native_worker.py'):
        source = (ROOT / 'audio_backend' / path).read_text()
        for name in ('fragment_normalized_runtime', 'fragment_decoded_source', 'fragment_rolling_decode'):
            assert name not in source


def test_codec_reader_is_not_sourceplan_or_runtime_integrated():
    for path in ('fragment_core.py', 'fragment_runtime.py',
                 'fragment_normalized_runtime.py', 'fragment_model_bridges.py'):
        source = (ROOT / 'audio_backend' / path).read_text()
        assert 'fragment_decoded_source' not in source
        assert 'fragment_rolling_decode' not in source

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
