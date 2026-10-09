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


def lineage_pin(row):
    if row.get('source_kind') == 'reviewed_local_override':
        return {'source_blob': row['source_blob'], 'sha256': row['sha256']}
    return {'source_commit': row['source_commit'], 'sha256': row['sha256']}


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
        if layer['component'] == 'finalized-pcm16-http':
            assert layer is layers['updates'][-1] and len(layer['files']) == 1
            assert layer['files'][0]['path'] == 'audio_backend/fragment_runtime_store.py'
            assert layer['files'][0].get('source_kind') == 'reviewed_local_override'
        else:
            assert all('source_kind' not in row for row in layer['files'])
        seen = set()
        for row in layer['files']:
            path = row['path']
            assert path not in seen, 'Duplicate override in reviewed layer'
            seen.add(path)
            previous = effective.get(path)
            if previous is not None:
                assert row['supersedes'] == lineage_pin(previous), 'Unreviewed or stale override'
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
        if row.get('source_kind') == 'reviewed_local_override':
            assert path == 'audio_backend/fragment_runtime_store.py'
            assert set(row) == {'path','bytes','sha256','source_kind','reviewed_base_commit','source_blob','supersedes'}
            assert row['reviewed_base_commit'] == '867a215a0578bb23885d175623a36ee3ed04d238'
            blob = subprocess.check_output(['git','hash-object','--stdin'],input=data,cwd=ROOT).decode().strip()
            assert blob == row['source_blob'], 'Stale reviewed Git blob pin'
            kind = subprocess.check_output(['git','cat-file','-t',blob],cwd=ROOT).decode().strip()
            assert kind == 'blob'
            assert subprocess.check_output(['git','cat-file','blob',blob],cwd=ROOT) == data
            before = subprocess.check_output(['git','show',row['reviewed_base_commit']+':'+path],cwd=ROOT)
            assert hashlib.sha256(before).hexdigest() == row['supersedes']['sha256'], 'Override base differs from prior imported bytes'


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
    policy = json.loads((ROOT / 'docs/FRAGMENT_STAGING_POLICY.json').read_text())
    files = guard.read_git_tree(':')
    assert set(files) == set(policy['allowed_files'])
    result = guard.validate(files, policy, require_manifest=False)
    assert result['findings'] == 0
    assert policy['maximum_files'] == 250
    assert policy['maximum_total_bytes'] == 2_550_000
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


@pytest.mark.parametrize('mutation', ['blob','base','missing_override'])
def test_candidate_override_requires_exact_reviewed_provenance(mutation):
    base, layers = import_records()
    row = layers['updates'][-1]['files'][0]
    assert layers['updates'][-1]['component'] == 'finalized-pcm16-http'
    if mutation == 'blob':
        row['source_blob'] = '0'*40
    elif mutation == 'base':
        row['reviewed_base_commit'] = '0'*40
    else:
        layers['updates'][-1]['files'].clear()
    with pytest.raises(AssertionError):
        effective = reviewed_imports(base,layers)
        verify_import_bytes(effective,lambda path:(ROOT/path).read_bytes())


def test_candidate_layer_preserves_all_historical_import_facts():
    historical = json.loads(subprocess.check_output(
        ['git','show','867a215a0578bb23885d175623a36ee3ed04d238:docs/FRAGMENT_STAGING_LAYERS.json'],cwd=ROOT))
    _, current = import_records()
    assert current['base_manifest_sha256'] == historical['base_manifest_sha256']
    assert current['updates'][:-1] == historical['updates']
    assert current['reviewed_layer_order'][:-1] == historical['reviewed_layer_order']


def test_reviewed_import_index_bytes_are_exact():
    base, layers = import_records()
    verify_import_bytes(reviewed_imports(base,layers),
                        lambda path:subprocess.check_output(['git','show',':'+path],cwd=ROOT))
