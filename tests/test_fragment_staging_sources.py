"""Exact reviewed source imports and unchanged privacy gates for isolated staging."""
from functools import lru_cache
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
            assert len(layer['files']) == 1
            assert layer['files'][0]['path'] == 'audio_backend/fragment_runtime_store.py'
            assert layer['files'][0].get('source_kind') == 'reviewed_local_override'
        elif layer['component'] == 'finalized-cpu-provider':
            assert layer is layers['updates'][-1]
            assert layer['projection_reference_commit'] == '74b51d282c53ff0354fde52e491627a5804aa633'
            assert sorted(r['path'] for r in layer['files']) == [
                'audio_backend/fragment_model_bridges.py','audio_backend/native_runner.py','audio_backend/native_worker.py']
            assert all(r.get('source_kind') == 'reviewed_local_override' for r in layer['files'])
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
            original = path == 'audio_backend/fragment_runtime_store.py'
            assert original or path in {'audio_backend/fragment_model_bridges.py','audio_backend/native_runner.py','audio_backend/native_worker.py'}
            assert set(row) == {'path','bytes','sha256','source_kind','reviewed_base_commit','source_blob','supersedes'}
            assert row['reviewed_base_commit'] == ('867a215a0578bb23885d175623a36ee3ed04d238' if original else '44f1019533200261fe687d6814a433a05c59bd27')
            blob = subprocess.check_output(['git','hash-object','--stdin'],input=data,cwd=ROOT).decode().strip()
            assert blob == row['source_blob'], 'Stale reviewed Git blob pin'
            kind = subprocess.check_output(['git','cat-file','-t',blob],cwd=ROOT).decode().strip()
            assert kind == 'blob'
            assert subprocess.check_output(['git','cat-file','blob',blob],cwd=ROOT) == data
            before = subprocess.check_output(['git','show',row['reviewed_base_commit']+':'+path],cwd=ROOT)
            assert hashlib.sha256(before).hexdigest() == row['supersedes']['sha256'], 'Override base differs from prior imported bytes'


def test_historical_reviewed_import_bytes_are_exact():
    base, layers = import_records()
    verify_import_bytes(reviewed_imports(base, layers), dev8_bytes)


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
        verify_import_bytes(effective, lambda path: dev8_bytes(path) + b'\n')


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
    assert policy['maximum_files'] == 272
    assert policy['maximum_total_bytes'] == 3_200_000
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
    assert layers['updates'][-1]['component'] == 'finalized-cpu-provider'
    if mutation == 'blob':
        row['source_blob'] = '0'*40
    elif mutation == 'base':
        row['reviewed_base_commit'] = '0'*40
    else:
        layers['updates'][-1]['files'].clear()
    with pytest.raises(AssertionError):
        effective = reviewed_imports(base,layers)
        verify_import_bytes(effective, dev8_bytes)


def test_candidate_layer_preserves_all_historical_import_facts():
    historical = json.loads(subprocess.check_output(
        ['git','show','44f1019533200261fe687d6814a433a05c59bd27:docs/FRAGMENT_STAGING_LAYERS.json'],cwd=ROOT))
    _, current = import_records()
    assert current['base_manifest_sha256'] == historical['base_manifest_sha256']
    assert current['updates'][:-1] == historical['updates']
    assert current['reviewed_layer_order'][:-1] == historical['reviewed_layer_order']


def test_historical_reviewed_import_tree_bytes_are_exact():
    base, layers = import_records()
    verify_import_bytes(reviewed_imports(base,layers),
                        lambda path: immutable_tree(DEV9_BASE)[path])


BUILDER_BASE = '0de25be7b0e8983aceb1e5591f41f6cf667851fc'
BUILDER_PATHS = {'audio_backend/fragment_cpu_provider.py',
                 'audio_backend/inference/native_diarization.py',
                 'scripts/build_fragment_cpu_manifest.py'}


def verify_local_builder_followup(layers, worktree_bytes, index_bytes):
    followups = layers['local_followups']
    assert len(followups) == 2
    verify_coverage_followup(layers, worktree_bytes, index_bytes)
    followup = followups[0]
    assert set(followup) == {'component', 'reviewed_base_commit', 'files'}
    assert followup['component'] == 'offline-cpu-manifest-builder'
    assert followup['reviewed_base_commit'] == BUILDER_BASE
    rows = followup['files']
    assert len(rows) == len(BUILDER_PATHS) and {row['path'] for row in rows} == BUILDER_PATHS
    for row in rows:
        path = row['path']
        if path in COVERAGE_PATHS:
            # Verify the actual immutable intermediate blob. Later layers must
            # explicitly supersede it; never rewrite a historical builder pin.
            assert row['source_blob'] == subprocess.check_output(
                ['git', 'rev-parse', COVERAGE_BASE + ':' + path], cwd=ROOT).decode().strip()
            data = subprocess.check_output(['git', 'cat-file', 'blob', row['source_blob']], cwd=ROOT)
        else:
            data = worktree_bytes(path)
            assert data == index_bytes(path), 'Worktree/index source differs'
        assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256']
        blob = subprocess.check_output(['git', 'hash-object', '--stdin'], input=data, cwd=ROOT).decode().strip()
        assert row['source_blob'] == blob
        assert subprocess.check_output(['git', 'cat-file', '-t', blob], cwd=ROOT).strip() == b'blob'
        assert subprocess.check_output(['git', 'cat-file', 'blob', blob], cwd=ROOT) == data
        key = BUILDER_BASE + ':' + path
        before = subprocess.run(['git', 'cat-file', '-e', key], cwd=ROOT, capture_output=True)
        if path == 'scripts/build_fragment_cpu_manifest.py':
            assert before.returncode != 0 and row['base_absent'] is True
            assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'base_absent'}
        else:
            assert before.returncode == 0
            previous = subprocess.check_output(['git', 'show', key], cwd=ROOT)
            previous_blob = subprocess.check_output(['git', 'rev-parse', key], cwd=ROOT).decode().strip()
            assert row['supersedes'] == {'source_blob': previous_blob, 'sha256': hashlib.sha256(previous).hexdigest()}
            assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'supersedes'}
            assert data != previous


def test_local_builder_followup_exact_worktree_and_index():
    _, layers = import_records()
    verify_local_builder_followup(layers, lambda path: (ROOT / path).read_bytes(),
        lambda path: subprocess.check_output(['git', 'show', ':' + path], cwd=ROOT))


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'base', 'blob', 'extra', 'supersedes', 'absence', 'worktree', 'index'])
def test_local_builder_followup_rejects_unreviewed_delta(mutation):
    _, layers = import_records()
    followup = layers['local_followups'][0]
    if mutation == 'missing': followup['files'].pop()
    elif mutation == 'duplicate': followup['files'].append(followup['files'][0])
    elif mutation == 'base': followup['reviewed_base_commit'] = '0' * 40
    elif mutation == 'blob': followup['files'][0]['source_blob'] = '0' * 40
    elif mutation == 'extra': followup['files'][0]['path'] = 'audio_backend/app.py'
    elif mutation == 'supersedes': followup['files'][0]['supersedes']['sha256'] = '0' * 64
    elif mutation == 'absence': followup['files'][-1]['base_absent'] = False
    with pytest.raises(AssertionError):
        verify_local_builder_followup(layers,
            lambda path: (ROOT / path).read_bytes() + (b'\n' if mutation == 'worktree' else b''),
            lambda path: subprocess.check_output(['git', 'show', ':' + path], cwd=ROOT) + (b'\n' if mutation == 'index' else b''))


def test_local_builder_followup_preserves_every_historical_layer():
    historical = json.loads(subprocess.check_output(['git', 'show', BUILDER_BASE + ':docs/FRAGMENT_STAGING_LAYERS.json'], cwd=ROOT))
    _, current = import_records()
    current.pop('local_followups')
    current.pop('composition')
    current.pop('coverage_composition')
    current.pop('candidate_evaluation_composition')
    current.pop('canonical_duration_eos_followup')
    current.pop('overlap_diagnostic_retention_composition')
    assert current['target_version'] == '0.2.0rc2.dev10'
    current['target_version'] = historical['target_version']
    assert current == historical


COMPOSITION_PARENTS = ['4ab61e7d54b2365195c0e5c2cc3ad3e0ba841efb',
                       '429de02166fd18e77088489763abc233b33426f0']
COMPOSITION_BASE = '867a215a0578bb23885d175623a36ee3ed04d238'
COMPOSED_PATHS = {'audio_backend/__init__.py', 'audio_backend/app.py', 'audio_backend/change_feed.py',
    'audio_backend/storage.py', 'docs/API.md', 'docs/FRAGMENT_EXTENSIONS_STAGING.md',
    'docs/FRAGMENT_STAGING_POLICY.json', 'pyproject.toml', 'tests/test_fragment_http.py',
    'tests/test_fragment_staging_sources.py'}
CANONICAL_PATHS = {'audio_backend/identity_canonical.py', 'audio_backend/verified_identity.py',
                   'docs/VERIFIED_IDENTITY.md', 'tests/test_verified_identity_canonical.py'}


def parent_pin(commit, path):
    key = commit + ':' + path
    if subprocess.run(['git', 'cat-file', '-e', key], cwd=ROOT, capture_output=True).returncode:
        return {'absent': True}
    data = subprocess.check_output(['git', 'show', key], cwd=ROOT)
    return {'source_blob': subprocess.check_output(['git', 'rev-parse', key], cwd=ROOT).decode().strip(),
            'sha256': hashlib.sha256(data).hexdigest()}


def verify_composition(layers, worktree_bytes, index_bytes):
    value = layers['composition']
    assert set(value) == {'schema', 'parents', 'merge_base', 'composed', 'canonical_unchanged'}
    assert value['schema'] == 'reviewed-branch-composition-v1'
    assert value['parents'] == COMPOSITION_PARENTS and value['merge_base'] == COMPOSITION_BASE
    assert subprocess.check_output(['git', 'merge-base', *COMPOSITION_PARENTS], cwd=ROOT).decode().strip() == COMPOSITION_BASE
    for name, expected in [('composed', COMPOSED_PATHS), ('canonical_unchanged', CANONICAL_PATHS)]:
        rows = value[name]
        assert len(rows) == len(expected) and {row['path'] for row in rows} == expected
        for row in rows:
            path = row['path']; data = worktree_bytes(path)
            assert data == index_bytes(path), 'Composition worktree/index mismatch'
            assert row['bytes'] == len(data) and row['sha256'] == hashlib.sha256(data).hexdigest()
            blob = subprocess.check_output(['git', 'hash-object', '--stdin'], input=data, cwd=ROOT).decode().strip()
            assert row['source_blob'] == blob
            assert subprocess.check_output(['git', 'cat-file', '-t', blob], cwd=ROOT).strip() == b'blob'
            assert subprocess.check_output(['git', 'cat-file', 'blob', blob], cwd=ROOT) == data
            if name == 'composed':
                assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'parents'}
                assert row['parents'] == {commit: parent_pin(commit, path) for commit in COMPOSITION_PARENTS}
                assert all(pin.get('sha256') != row['sha256'] for pin in row['parents'].values())
            else:
                assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'source_commit'}
                assert row['source_commit'] == COMPOSITION_PARENTS[1]
                assert parent_pin(row['source_commit'], path) == {'source_blob': blob, 'sha256': row['sha256']}


def test_historical_dev6_composition_exact_parent_inputs():
    _, layers = import_records()
    verify_composition(layers, dev6_bytes, dev6_bytes)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'parent', 'base', 'input', 'blob', 'canonical', 'worktree', 'index'])
def test_historical_dev6_composition_rejects_unreviewed_sources(mutation):
    _, layers = import_records(); value = layers['composition']
    if mutation == 'missing': value['composed'].pop()
    elif mutation == 'extra': value['composed'][0]['path'] = 'README.md'
    elif mutation == 'duplicate': value['composed'].append(value['composed'][0])
    elif mutation == 'parent': value['parents'][0] = '0' * 40
    elif mutation == 'base': value['merge_base'] = '0' * 40
    elif mutation == 'input': value['composed'][0]['parents'][COMPOSITION_PARENTS[0]]['sha256'] = '0' * 64
    elif mutation == 'blob': value['composed'][0]['source_blob'] = '0' * 40
    elif mutation == 'canonical': value['canonical_unchanged'][0]['source_commit'] = COMPOSITION_PARENTS[0]
    with pytest.raises(AssertionError):
        verify_composition(layers,
            lambda path: dev6_bytes(path) + (b'\n' if mutation == 'worktree' else b''),
            lambda path: dev6_bytes(path) + (b'\n' if mutation == 'index' else b''))


def test_historical_dev6_composition_preserves_history():
    union = set()
    for commit in COMPOSITION_PARENTS:
        union.update(subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', commit], cwd=ROOT).decode().splitlines())
    assert set(guard.read_git_tree(DEV7_PARENTS[0])) == union and len(union) == 256
    historical = json.loads(subprocess.check_output(['git', 'show', COMPOSITION_PARENTS[0] + ':docs/FRAGMENT_STAGING_LAYERS.json'], cwd=ROOT))
    current = json.loads(dev6_bytes('docs/FRAGMENT_STAGING_LAYERS.json'))
    current.pop('composition')
    assert current['target_version'] == '0.2.0rc2.dev6'
    current['target_version'] = historical['target_version']
    assert current == historical


COVERAGE_BASE = '4ab61e7d54b2365195c0e5c2cc3ad3e0ba841efb'
COVERAGE_PATHS = {'audio_backend/fragment_asr_coverage.py', 'audio_backend/fragment_cpu_meter.py',
                  'audio_backend/fragment_cpu_provider.py', 'tests/test_fragment_asr_coverage.py',
                  'tests/test_fragment_cpu_provider.py', 'docs/ASR_INPUT_COVERAGE.md'}


def verify_coverage_followup(layers, worktree_bytes, index_bytes):
    layer = layers['local_followups'][1]
    assert set(layer) == {'component', 'reviewed_base_commit', 'files'}
    assert layer['component'] == 'asr-input-coverage'
    assert layer['reviewed_base_commit'] == COVERAGE_BASE
    assert len(layer['files']) == len(COVERAGE_PATHS)
    assert {row['path'] for row in layer['files']} == COVERAGE_PATHS
    for row in layer['files']:
        data = worktree_bytes(row['path'])
        assert data == index_bytes(row['path']), 'Worktree/index source differs'
        assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256']
        blob = subprocess.check_output(['git', 'hash-object', '--stdin'], input=data, cwd=ROOT).decode().strip()
        assert row['source_blob'] == blob
        assert subprocess.check_output(['git', 'cat-file', 'blob', blob], cwd=ROOT) == data
        key = COVERAGE_BASE + ':' + row['path']
        before = subprocess.run(['git', 'cat-file', '-e', key], cwd=ROOT, capture_output=True)
        if before.returncode:
            assert row['base_absent'] is True
            assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'base_absent'}
        else:
            previous = subprocess.check_output(['git', 'show', key], cwd=ROOT)
            previous_blob = subprocess.check_output(['git', 'rev-parse', key], cwd=ROOT).decode().strip()
            assert row['supersedes'] == {'source_blob': previous_blob, 'sha256': hashlib.sha256(previous).hexdigest()}
            assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'supersedes'}
            assert data != previous


def test_coverage_preserves_all_prior_provenance():
    historical = json.loads(subprocess.check_output(['git', 'show', COVERAGE_BASE + ':docs/FRAGMENT_STAGING_LAYERS.json'], cwd=ROOT))
    coverage = json.loads(subprocess.check_output(['git', 'show', DEV7_PARENTS[1] + ':docs/FRAGMENT_STAGING_LAYERS.json'], cwd=ROOT))
    _, current = import_records()
    assert current['local_followups'] == coverage['local_followups']
    coverage['local_followups'].pop()
    assert coverage == historical


@pytest.mark.parametrize('mutation', ['missing', 'base', 'blob', 'supersedes', 'worktree', 'index'])
def test_coverage_provenance_rejects_stale_delta(mutation):
    _, layers = import_records()
    layer = layers['local_followups'][1]
    if mutation == 'missing': layer['files'].pop()
    elif mutation == 'base': layer['reviewed_base_commit'] = '0' * 40
    elif mutation == 'blob': layer['files'][0]['source_blob'] = '0' * 40
    elif mutation == 'supersedes':
        next(row for row in layer['files'] if 'supersedes' in row)['supersedes']['sha256'] = '0' * 64
    with pytest.raises(AssertionError):
        verify_coverage_followup(layers,
            lambda path: (ROOT / path).read_bytes() + (b'\n' if mutation == 'worktree' else b''),
            lambda path: subprocess.check_output(['git', 'show', ':' + path], cwd=ROOT) + (b'\n' if mutation == 'index' else b''))


DEV7_PARENTS = ['7e2ce6cea70c622f6979fe5b9c517ff87dfa11ff',
                '9b44a401143ed339ff2e21e3063201e5d044d232']
DEV7_COMPOSED_PATHS = {'README.md', 'audio_backend/__init__.py', 'docs/API.md',
    'docs/FRAGMENT_EXTENSIONS_STAGING.md', 'docs/FRAGMENT_STAGING_POLICY.json',
    'pyproject.toml', 'tests/test_fragment_staging_sources.py'}
LAYER_PATH = 'docs/FRAGMENT_STAGING_LAYERS.json'


def dev6_bytes(path):
    return subprocess.check_output(['git', 'show', DEV7_PARENTS[0] + ':' + path], cwd=ROOT)


def verify_coverage_composition(layers, worktree_bytes, index_bytes):
    value = layers['coverage_composition']
    assert set(value) == {'schema', 'parents', 'merge_base', 'composed', 'coverage_unchanged'}
    assert value['schema'] == 'reviewed-branch-composition-v1'
    assert value['parents'] == DEV7_PARENTS and value['merge_base'] == COVERAGE_BASE
    assert subprocess.check_output(['git', 'merge-base', *DEV7_PARENTS], cwd=ROOT).decode().strip() == COVERAGE_BASE
    for name, expected in [('composed', DEV7_COMPOSED_PATHS), ('coverage_unchanged', COVERAGE_PATHS)]:
        rows = value[name]
        assert len(rows) == len(expected) and {row['path'] for row in rows} == expected
        for row in rows:
            path = row['path']; data = worktree_bytes(path)
            assert data == index_bytes(path), 'Composition worktree/index mismatch'
            assert row['bytes'] == len(data) and row['sha256'] == hashlib.sha256(data).hexdigest()
            blob = subprocess.check_output(['git', 'hash-object', '--stdin'], input=data, cwd=ROOT).decode().strip()
            assert row['source_blob'] == blob
            assert subprocess.check_output(['git', 'cat-file', '-t', blob], cwd=ROOT).strip() == b'blob'
            assert subprocess.check_output(['git', 'cat-file', 'blob', blob], cwd=ROOT) == data
            if name == 'composed':
                assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'parents'}
                assert row['parents'] == {commit: parent_pin(commit, path) for commit in DEV7_PARENTS}
                assert all(pin.get('sha256') != row['sha256'] for pin in row['parents'].values())
            else:
                assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'source_commit'}
                assert row['source_commit'] == DEV7_PARENTS[1]
                assert parent_pin(row['source_commit'], path) == {'source_blob': blob, 'sha256': row['sha256']}


def test_historical_dev7_coverage_composition_exact_parent_inputs():
    _, layers = import_records()
    verify_coverage_composition(layers, dev7_bytes, dev7_bytes)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'parent', 'base',
                                     'input', 'blob', 'coverage', 'worktree', 'index'])
def test_coverage_composition_rejects_unreviewed_sources(mutation):
    _, layers = import_records(); value = layers['coverage_composition']
    if mutation == 'missing': value['composed'].pop()
    elif mutation == 'extra': value['composed'][0]['path'] = 'audio_backend/app.py'
    elif mutation == 'duplicate': value['composed'].append(value['composed'][0])
    elif mutation == 'parent': value['parents'][0] = '0' * 40
    elif mutation == 'base': value['merge_base'] = '0' * 40
    elif mutation == 'input': value['composed'][0]['parents'][DEV7_PARENTS[0]]['sha256'] = '0' * 64
    elif mutation == 'blob': value['composed'][0]['source_blob'] = '0' * 40
    elif mutation == 'coverage': value['coverage_unchanged'][0]['source_commit'] = DEV7_PARENTS[0]
    with pytest.raises(AssertionError):
        verify_coverage_composition(layers,
            lambda path: dev7_bytes(path) + (b'\n' if mutation == 'worktree' else b''),
            lambda path: dev7_bytes(path) + (b'\n' if mutation == 'index' else b''))


def verify_dev7_inherited_bytes(files):
    union = set()
    for commit in DEV7_PARENTS:
        union.update(guard.read_git_tree(commit))
    assert set(files) == union and len(union) == 259
    for path in union - DEV7_COMPOSED_PATHS - {LAYER_PATH}:
        parent = DEV7_PARENTS[1] if path in COVERAGE_PATHS else DEV7_PARENTS[0]
        assert files[path] == subprocess.check_output(['git', 'show', parent + ':' + path], cwd=ROOT), path


def test_historical_dev7_is_exact_reviewed_union_with_unchanged_inherited_bytes():
    verify_dev7_inherited_bytes(guard.read_git_tree(DEV8_BASE))
    historical = json.loads(dev6_bytes(LAYER_PATH))
    current = json.loads(dev7_bytes(LAYER_PATH))
    current.pop('coverage_composition')
    current['local_followups'].pop()
    assert current['target_version'] == '0.2.0rc2.dev7'
    current['target_version'] = historical['target_version']
    assert current == historical


@pytest.mark.parametrize('path', ['audio_backend/app.py', 'audio_backend/identity_canonical.py',
    'audio_backend/fragment_model_bridges.py', 'audio_backend/fragment_cpu_provider.py'])
def test_dev7_rejects_mutation_of_inherited_identity_and_asr_sources(path):
    files = guard.read_git_tree(DEV8_BASE); files[path] += b'\n'
    with pytest.raises(AssertionError):
        verify_dev7_inherited_bytes(files)


DEV8_BASE = '1cbd6e62da69b7abc0ecac6425b46401f76fc294'
DEV8_PARENTS = ['440701a24bbef0e7053b2aa90cd2e3b03259938c',
                '9585cd5d9afbb512e7e70f8c43c97987c066000b']
DEV8_COMPOSED_PATHS = {'audio_backend/__init__.py', 'pyproject.toml',
    'docs/FRAGMENT_EXTENSIONS_STAGING.md', 'docs/FRAGMENT_STAGING_POLICY.json',
    'tests/test_fragment_staging_sources.py'}
EVENT_COVERAGE_PATHS = {'scripts/benchmarks/score_predictions.py',
    'tests/test_event_evaluation_coverage.py', 'docs/EVENT_EVALUATION_COVERAGE.md'}
QWEN_CANDIDATE_PATHS = {'audio_backend/qwen_candidate_runner.py',
    'audio_backend/qwen_candidate_store.py', 'audio_backend/qwen_candidates.py',
    'scripts/benchmarks/run_qwen_candidates.py', 'tests/test_qwen_candidate_store.py',
    'tests/test_qwen_candidates.py'}
DEV8_SCOPE = {
    'qwen': 'explicit opt-in offline candidate CLI; not default ASR, HTTP or long-run acceptance',
    'events': 'evaluation coverage only; no model, taxonomy, threshold or default change',
    'quality': 'not established by model-free integration; no production rail candidate',
}


def dev7_bytes(path):
    return subprocess.check_output(['git', 'show', DEV8_BASE + ':' + path], cwd=ROOT)


def verify_candidate_evaluation_composition(layers, worktree_bytes, index_bytes):
    value = layers['candidate_evaluation_composition']
    assert set(value) == {'schema', 'parents', 'merge_base', 'composed',
                          'event_coverage_unchanged', 'qwen_unchanged', 'scope'}
    assert value['schema'] == 'reviewed-branch-composition-v1'
    assert value['parents'] == DEV8_PARENTS and value['merge_base'] == DEV8_BASE
    assert value['scope'] == DEV8_SCOPE
    assert subprocess.check_output(['git', 'merge-base', *DEV8_PARENTS], cwd=ROOT).decode().strip() == DEV8_BASE
    for name, expected, parent in [
        ('composed', DEV8_COMPOSED_PATHS, None),
        ('event_coverage_unchanged', EVENT_COVERAGE_PATHS, DEV8_PARENTS[0]),
        ('qwen_unchanged', QWEN_CANDIDATE_PATHS, DEV8_PARENTS[1]),
    ]:
        rows = value[name]
        assert len(rows) == len(expected) and {row['path'] for row in rows} == expected
        for row in rows:
            path = row['path']; data = worktree_bytes(path)
            assert data == index_bytes(path), 'Dev8 worktree/index mismatch'
            blob = subprocess.check_output(['git', 'hash-object', '--stdin'], input=data, cwd=ROOT).decode().strip()
            assert row['bytes'] == len(data) and row['sha256'] == hashlib.sha256(data).hexdigest()
            assert row['source_blob'] == blob
            assert subprocess.check_output(['git', 'cat-file', 'blob', blob], cwd=ROOT) == data
            if parent is None:
                assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'parents'}
                assert row['parents'] == {commit: parent_pin(commit, path) for commit in DEV8_PARENTS}
                assert all(pin['sha256'] != row['sha256'] for pin in row['parents'].values())
            else:
                assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'source_commit'}
                assert row['source_commit'] == parent
                assert parent_pin(parent, path) == {'source_blob': blob, 'sha256': row['sha256']}


def test_historical_dev8_composition_exact_sources():
    _, layers = import_records()
    verify_candidate_evaluation_composition(layers, dev8_bytes, dev8_bytes)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'parent', 'base',
                                     'input', 'blob', 'qwen', 'event', 'scope', 'worktree', 'index'])
def test_dev8_rejects_unreviewed_or_mislabelled_composition(mutation):
    _, layers = import_records(); value = layers['candidate_evaluation_composition']
    if mutation == 'missing': value['composed'].pop()
    elif mutation == 'extra': value['composed'][0]['path'] = 'audio_backend/app.py'
    elif mutation == 'duplicate': value['composed'].append(value['composed'][0])
    elif mutation == 'parent': value['parents'][0] = '0' * 40
    elif mutation == 'base': value['merge_base'] = '0' * 40
    elif mutation == 'input': value['composed'][0]['parents'][DEV8_PARENTS[0]]['sha256'] = '0' * 64
    elif mutation == 'blob': value['composed'][0]['source_blob'] = '0' * 40
    elif mutation == 'qwen': value['qwen_unchanged'][0]['source_commit'] = DEV8_PARENTS[0]
    elif mutation == 'event': value['event_coverage_unchanged'][0]['source_commit'] = DEV8_PARENTS[1]
    elif mutation == 'scope': value['scope']['qwen'] = 'default ASR'
    with pytest.raises(AssertionError):
        verify_candidate_evaluation_composition(layers,
            lambda path: dev8_bytes(path) + (b'\n' if mutation == 'worktree' else b''),
            lambda path: dev8_bytes(path) + (b'\n' if mutation == 'index' else b''))


def verify_dev8_inherited_bytes(files):
    union = set()
    for commit in DEV8_PARENTS:
        union.update(guard.read_git_tree(commit))
    assert set(files) == union and len(union) == 267
    for path in union - DEV8_COMPOSED_PATHS - {LAYER_PATH}:
        parent = (DEV8_PARENTS[0] if path in EVENT_COVERAGE_PATHS else
                  DEV8_PARENTS[1] if path in QWEN_CANDIDATE_PATHS else DEV8_BASE)
        assert files[path] == subprocess.check_output(['git', 'show', parent + ':' + path], cwd=ROOT), path


def test_dev8_exact_union_preserves_all_historical_provenance():
    verify_dev8_inherited_bytes(immutable_tree(DEV9_BASE))
    current = json.loads(dev8_bytes(LAYER_PATH))
    current.pop('candidate_evaluation_composition')
    assert current['target_version'] == '0.2.0rc2.dev8'
    historical = json.loads(dev7_bytes(LAYER_PATH))
    current['target_version'] = historical['target_version']
    assert current == historical
    assert (ROOT / 'docs/FRAGMENT_STAGING_IMPORTS.json').read_bytes() == dev7_bytes('docs/FRAGMENT_STAGING_IMPORTS.json')


@pytest.mark.parametrize('path', ['audio_backend/app.py', 'audio_backend/worker.py',
    'audio_backend/settings.py', 'audio_backend/inference/analyzer.py',
    'audio_backend/qwen_candidate_runner.py', 'scripts/benchmarks/score_predictions.py'])
def test_dev8_rejects_unreviewed_runtime_or_candidate_changes(path):
    files = dict(immutable_tree(DEV9_BASE)); files[path] += b'\n'
    with pytest.raises(AssertionError):
        verify_dev8_inherited_bytes(files)


DEV9_BASE = '5ec6c9d5bd3febf2c30695120b969e6b70a1c0d2'
DEV9_FEATURE = 'bbf46874885423cf998426d4c7cb3f451939e595'
DEV9_FEATURE_PATHS = {'audio_backend/app.py', 'audio_backend/native_worker.py',
    'audio_backend/worker.py', 'docs/API.md', 'tests/test_canonical_duration_eos.py'}
DEV9_INTEGRATION_PATHS = {'audio_backend/__init__.py', 'pyproject.toml',
    'docs/FRAGMENT_EXTENSIONS_STAGING.md', 'docs/FRAGMENT_STAGING_POLICY.json',
    'tests/test_fragment_staging_sources.py', LAYER_PATH}
DEV9_SCOPE = {
    'duration': 'exact same-rate PCM/FLAC admission and source-relative native frame grid',
    'eos': 'native duration mismatches fail closed; generic compressed/resampled reconciliation remains open',
    'compatibility': 'legacy measured resampled ranges retained; decoder errors fail on both backends',
    'acceptance': 'model-free source correctness only; no new long-run, ASR or speaker-quality acceptance',
}


@lru_cache(maxsize=None)
def immutable_tree(commit):
    return guard.read_git_tree(commit)


def dev8_bytes(path):
    return immutable_tree(DEV9_BASE)[path]


def verify_dev9_followup(layers, files, index_files):
    value = layers['canonical_duration_eos_followup']
    assert set(value) == {'schema', 'reviewed_base_commit', 'reviewed_feature_commit',
                          'feature_files', 'integration_files', 'scope'}
    assert value['schema'] == 'reviewed-feature-integration-v1'
    assert value['reviewed_base_commit'] == DEV9_BASE
    assert value['reviewed_feature_commit'] == DEV9_FEATURE
    assert value['scope'] == DEV9_SCOPE
    historical = json.loads(dev8_bytes(LAYER_PATH))
    prior = {key: data for key, data in layers.items() if key != 'canonical_duration_eos_followup'}
    assert prior['target_version'] == '0.2.0rc2.dev9'
    prior['target_version'] = historical['target_version']
    assert prior == historical, 'Historical provenance changed'
    base, feature = immutable_tree(DEV9_BASE), immutable_tree(DEV9_FEATURE)
    assert subprocess.check_output(['git', 'merge-base', DEV9_BASE, DEV9_FEATURE], cwd=ROOT).decode().strip() == DEV9_BASE
    assert set(feature) == set(base) | {'tests/test_canonical_duration_eos.py'}
    assert {path for path in feature if feature[path] != base.get(path)} == DEV9_FEATURE_PATHS
    assert set(files) == set(index_files) == set(feature) and len(files) == 268
    assert files == index_files, 'Dev9 worktree/index mismatch'
    assert json.loads(files[LAYER_PATH]) == layers
    for name, expected in [('feature_files', DEV9_FEATURE_PATHS),
                           ('integration_files', DEV9_INTEGRATION_PATHS - {LAYER_PATH})]:
        rows = value[name]
        assert len(rows) == len(expected) and {row['path'] for row in rows} == expected
        for row in rows:
            path = row['path']; data = files[path]
            if name == 'feature_files':
                assert data == feature[path], 'Feature differs from reviewed corrective commit'
            else:
                assert data != feature[path], 'Undeclared unchanged integration file'
            assert row['bytes'] == len(data) and row['sha256'] == hashlib.sha256(data).hexdigest()
            blob = subprocess.check_output(['git', 'hash-object', '--stdin'], input=data, cwd=ROOT).decode().strip()
            assert row['source_blob'] == blob
            assert subprocess.check_output(['git', 'cat-file', 'blob', blob], cwd=ROOT) == data
            if path in base:
                assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'supersedes'}
                assert row['supersedes'] == parent_pin(DEV9_BASE, path)
            else:
                assert set(row) == {'path', 'bytes', 'sha256', 'source_blob', 'base_absent'}
                assert row['base_absent'] is True
    for path in set(files) - DEV9_FEATURE_PATHS - DEV9_INTEGRATION_PATHS:
        assert files[path] == base[path], path
    policy = json.loads(files['docs/FRAGMENT_STAGING_POLICY.json'])
    original_policy = json.loads(base['docs/FRAGMENT_STAGING_POLICY.json'])
    assert policy['purpose'] == 'Isolated dev9 canonical-duration/EOS feature integration; historical pins and privacy exclusions unchanged'
    assert policy['maximum_files'] == 268
    assert policy['allowed_files'] == sorted(original_policy['allowed_files'] + ['tests/test_canonical_duration_eos.py'])
    for key in ('purpose', 'maximum_files', 'allowed_files'):
        policy[key] = original_policy[key]
    assert policy == original_policy, 'Byte/privacy policy changed'
    assert files['scripts/source_staging_guard.py'] == base['scripts/source_staging_guard.py']
    assert guard.MAX_BYTES == 250_000


@pytest.fixture(scope='module')
def dev9_sources():
    historical = immutable_tree(DEV10_PARENTS[0])
    return historical, historical


def test_historical_dev9_exact_feature_integration(dev9_sources):
    layers = json.loads(immutable_tree(DEV10_PARENTS[0])[LAYER_PATH])
    verify_dev9_followup(layers, *dev9_sources)


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'base', 'feature', 'blob',
    'supersedes', 'absence', 'scope', 'history', 'extra_path', 'inherited', 'feature_bytes',
    'worktree', 'index', 'policy'])
def test_dev9_rejects_unreviewed_feature_or_integration_delta(mutation, dev9_sources):
    layers = json.loads(immutable_tree(DEV10_PARENTS[0])[LAYER_PATH])
    verify_dev9_followup(layers, *dev9_sources)
    files, index = (dict(values) for values in dev9_sources)
    value = layers['canonical_duration_eos_followup']
    if mutation == 'missing': value['feature_files'].pop()
    elif mutation == 'duplicate': value['feature_files'].append(value['feature_files'][0])
    elif mutation == 'base': value['reviewed_base_commit'] = '0' * 40
    elif mutation == 'feature': value['reviewed_feature_commit'] = DEV9_BASE
    elif mutation == 'blob': value['integration_files'][0]['source_blob'] = '0' * 40
    elif mutation == 'supersedes': value['feature_files'][0]['supersedes']['sha256'] = '0' * 64
    elif mutation == 'absence': next(row for row in value['feature_files'] if 'base_absent' in row)['base_absent'] = False
    elif mutation == 'scope': value['scope']['acceptance'] = '12-hour native quality accepted'
    elif mutation == 'history': layers['updates'][-1]['files'][0]['sha256'] = '0' * 64
    elif mutation == 'extra_path': files['docs/unreviewed.md'] = index['docs/unreviewed.md'] = b'unreviewed'
    elif mutation == 'inherited': files['audio_backend/settings.py'] = index['audio_backend/settings.py'] + b'\n'; index.update(files)
    elif mutation == 'feature_bytes': files['audio_backend/native_worker.py'] += b'\n'; index.update(files)
    elif mutation == 'worktree': files['audio_backend/app.py'] += b'\n'
    elif mutation == 'index': index['audio_backend/app.py'] += b'\n'
    elif mutation == 'policy':
        policy = json.loads(files['docs/FRAGMENT_STAGING_POLICY.json']); policy['maximum_total_bytes'] += 1
        files['docs/FRAGMENT_STAGING_POLICY.json'] = index['docs/FRAGMENT_STAGING_POLICY.json'] = json.dumps(policy).encode()
    if mutation not in {'worktree', 'index'}:
        files[LAYER_PATH] = index[LAYER_PATH] = json.dumps(layers).encode()
    with pytest.raises(AssertionError):
        verify_dev9_followup(layers, files, index)


DEV10_PARENTS = ['3b7f5a9dcee0d626348d5354531d1a8bad6bed7b',
                 '9ba755a87f599c1b3743388594da6ae431e82a64']
DEV10_BASE = '5ec6c9d5bd3febf2c30695120b969e6b70a1c0d2'
DEV10_MERGE = '117e8414c4f524477db83886ab3731a5c3afc0b4'
DEV10_FEATURE_PATHS = {'audio_backend/moss_td_candidates.py', 'audio_backend/overlap_candidates.py',
    'audio_backend/overlap_diagnostics.py', 'audio_backend/overlap_ownership.py',
    'audio_backend/overlap_store.py', 'audio_backend/storage.py',
    'docs/OVERLAP_DIAGNOSTIC_RETENTION.md', 'tests/fixtures/overlap_schema_dev8.sql',
    'tests/test_moss_td_candidates.py', 'tests/test_overlap_diagnostics.py',
    'tests/test_overlap_public_contract.py'}
DEV10_NEW_PATHS = {'audio_backend/overlap_diagnostics.py', 'docs/OVERLAP_DIAGNOSTIC_RETENTION.md',
    'tests/fixtures/overlap_schema_dev8.sql', 'tests/test_overlap_diagnostics.py'}
DEV10_INTEGRATION_PATHS = {'audio_backend/__init__.py', 'pyproject.toml',
    'docs/FRAGMENT_EXTENSIONS_STAGING.md', 'docs/FRAGMENT_STAGING_POLICY.json',
    'tests/test_fragment_staging_sources.py', LAYER_PATH}
DEV10_SCOPE = {
    'retention': 'prospective bounded private overlap diagnostics; historical missing output is not recovered',
    'durability': 'atomic candidate evidence and cursor persistence; shared-support deduplication and deletion cascade',
    'privacy': 'private local export only; unchanged public API, transcript and identity semantics',
    'compatibility': 'reviewed dev9 duration/EOS and retention feature blobs preserved exactly',
    'acceptance': 'focused model-free source validation only; no inherited full-suite, model-quality or long-run acceptance',
}
DEV10_PURPOSE = 'Isolated dev10 private overlap diagnostic-retention composition; immutable historical pins and privacy exclusions unchanged'


def verify_dev10_composition(layers, files, index_files):
    import tomllib
    key = 'overlap_diagnostic_retention_composition'
    value = layers[key]
    assert set(value) == {'schema', 'parents', 'merge_base', 'merge_commit',
                          'feature_files', 'integration_files', 'scope'}
    assert value['schema'] == 'reviewed-branch-feature-integration-v1'
    assert value['parents'] == DEV10_PARENTS and value['merge_base'] == DEV10_BASE
    assert value['merge_commit'] == DEV10_MERGE and value['scope'] == DEV10_SCOPE
    base, first, feature, merged = (immutable_tree(ref) for ref in
        (DEV10_BASE, *DEV10_PARENTS, DEV10_MERGE))
    assert subprocess.check_output(['git', 'merge-base', *DEV10_PARENTS], cwd=ROOT).decode().strip() == DEV10_BASE
    assert subprocess.check_output(['git', 'show', '-s', '--format=%P', DEV10_MERGE], cwd=ROOT).decode().split() == DEV10_PARENTS
    assert set(feature) == set(base) | DEV10_NEW_PATHS
    assert {path for path in feature if feature[path] != base.get(path)} == DEV10_FEATURE_PATHS
    assert not ({path for path in first if first[path] != base.get(path)} & DEV10_FEATURE_PATHS)
    expected = dict(first)
    expected.update({path: feature[path] for path in DEV10_FEATURE_PATHS})
    assert merged == expected, 'Merge was not the exact disjoint reviewed union'
    assert len(merged) == 272 and sum(map(len, merged.values())) == 3_149_704
    assert set(files) == set(index_files) == set(merged)
    assert files == index_files, 'Dev10 worktree/index mismatch'
    assert json.loads(files[LAYER_PATH]) == layers
    historical = json.loads(first[LAYER_PATH])
    prior = {name: data for name, data in layers.items() if name != key}
    assert prior['target_version'] == '0.2.0rc2.dev10'
    prior['target_version'] = historical['target_version']
    assert prior == historical, 'Dev9 or older historical provenance changed'
    assert tomllib.loads(files['pyproject.toml'].decode())['project']['version'] == '0.2.0rc2.dev10'
    assert "__version__ = '0.2.0rc2.dev10'" in files['audio_backend/__init__.py'].decode()
    assert {path for path in files if files[path] != merged[path]} == DEV10_INTEGRATION_PATHS
    for path in DEV10_FEATURE_PATHS:
        assert files[path] == feature[path], 'Retention feature blob changed'
    for path in DEV9_FEATURE_PATHS:
        assert files[path] == first[path], 'Duration/EOS feature blob changed'
    for name, expected_paths in [('feature_files', DEV10_FEATURE_PATHS),
                                 ('integration_files', DEV10_INTEGRATION_PATHS - {LAYER_PATH})]:
        rows = value[name]
        assert len(rows) == len(expected_paths) and {row['path'] for row in rows} == expected_paths
        for row in rows:
            path = row['path']; data = files[path]
            fields = {'path', 'bytes', 'sha256', 'source_blob'} | ({'parents'} if name == 'feature_files' else {'supersedes'})
            assert set(row) == fields
            assert row['bytes'] == len(data) and row['sha256'] == hashlib.sha256(data).hexdigest()
            blob = subprocess.check_output(['git', 'hash-object', '--stdin'], input=data, cwd=ROOT).decode().strip()
            assert row['source_blob'] == blob
            assert subprocess.check_output(['git', 'cat-file', 'blob', blob], cwd=ROOT) == data
            if name == 'feature_files':
                assert row['parents'] == {ref: parent_pin(ref, path) for ref in (DEV10_BASE, DEV10_PARENTS[0])}
                assert parent_pin(DEV10_PARENTS[1], path) == {'source_blob': blob, 'sha256': row['sha256']}
            else:
                assert row['supersedes'] == parent_pin(DEV10_MERGE, path)
    policy = json.loads(files['docs/FRAGMENT_STAGING_POLICY.json'])
    original_policy = json.loads(first['docs/FRAGMENT_STAGING_POLICY.json'])
    assert policy['purpose'] == DEV10_PURPOSE and policy['maximum_files'] == 272
    assert policy['allowed_files'] == sorted(set(original_policy['allowed_files']) | DEV10_NEW_PATHS)
    for field in ('purpose', 'maximum_files', 'allowed_files'):
        policy[field] = original_policy[field]
    assert policy == original_policy, 'Byte/privacy policy changed'
    assert files['scripts/source_staging_guard.py'] == first['scripts/source_staging_guard.py']
    assert guard.MAX_BYTES == 250_000
    assert 0 < sum(map(len, files.values())) - sum(map(len, merged.values())) <= 30_000
    assert guard.validate(files, json.loads(files['docs/FRAGMENT_STAGING_POLICY.json']), require_manifest=False)['findings'] == 0


@pytest.fixture(scope='module')
def dev10_sources():
    index = guard.read_git_tree(':')
    return {path: (ROOT / path).read_bytes() for path in index}, index


def test_dev10_exact_reviewed_union_and_current_integration(dev10_sources):
    _, layers = import_records()
    verify_dev10_composition(layers, *dev10_sources)


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'parent_order', 'base', 'merge',
    'blob', 'prior_pin', 'absence', 'scope', 'extra_claim', 'historical_dev9', 'historical_dev8',
    'self_pin', 'extra_path', 'missing_path', 'inherited', 'retention_bytes', 'duration_bytes',
    'worktree', 'index', 'policy_bytes', 'policy_exclusions', 'stale_version'])
def test_dev10_rejects_unreviewed_composition_or_acceptance(mutation, dev10_sources):
    _, layers = import_records()
    verify_dev10_composition(layers, *dev10_sources)
    files, index = (dict(values) for values in dev10_sources)
    value = layers['overlap_diagnostic_retention_composition']
    if mutation == 'missing': value['feature_files'].pop()
    elif mutation == 'duplicate': value['feature_files'].append(value['feature_files'][0])
    elif mutation == 'parent_order': value['parents'].reverse()
    elif mutation == 'base': value['merge_base'] = '0' * 40
    elif mutation == 'merge': value['merge_commit'] = DEV10_PARENTS[0]
    elif mutation == 'blob': value['integration_files'][0]['source_blob'] = '0' * 40
    elif mutation == 'prior_pin': value['feature_files'][0]['parents'][DEV10_BASE]['sha256'] = '0' * 64
    elif mutation == 'absence':
        next(row for row in value['feature_files'] if row['path'] in DEV10_NEW_PATHS)['parents'][DEV10_BASE] = {'absent': False}
    elif mutation == 'scope': value['scope']['acceptance'] = 'historical endurance accepted'
    elif mutation == 'extra_claim': value['quality_accepted'] = True
    elif mutation == 'historical_dev9': layers['canonical_duration_eos_followup']['feature_files'][0]['sha256'] = '0' * 64
    elif mutation == 'historical_dev8': layers['candidate_evaluation_composition']['merge_base'] = '0' * 40
    elif mutation == 'self_pin': value['integration_files'].append({'path': LAYER_PATH})
    elif mutation == 'extra_path': files['docs/unreviewed.md'] = index['docs/unreviewed.md'] = b'unreviewed'
    elif mutation == 'missing_path':
        files.pop('audio_backend/overlap_diagnostics.py'); index.pop('audio_backend/overlap_diagnostics.py')
    elif mutation in {'inherited', 'retention_bytes', 'duration_bytes', 'worktree', 'index'}:
        path = {'inherited': 'audio_backend/settings.py', 'retention_bytes': 'audio_backend/overlap_store.py',
                'duration_bytes': 'audio_backend/native_worker.py', 'worktree': 'audio_backend/storage.py',
                'index': 'audio_backend/storage.py'}[mutation]
        if mutation != 'index': files[path] += b'\n'
        if mutation != 'worktree': index[path] += b'\n'
    elif mutation in {'policy_bytes', 'policy_exclusions'}:
        policy = json.loads(files['docs/FRAGMENT_STAGING_POLICY.json'])
        if mutation == 'policy_bytes': policy['maximum_total_bytes'] += 1
        else: policy['excluded_categories'].pop()
        files['docs/FRAGMENT_STAGING_POLICY.json'] = index['docs/FRAGMENT_STAGING_POLICY.json'] = json.dumps(policy).encode()
    elif mutation == 'stale_version':
        files['pyproject.toml'] = index['pyproject.toml'] = files['pyproject.toml'].replace(b'0.2.0rc2.dev10', b'0.2.0rc2.dev9')
    if mutation not in {'worktree', 'index'}:
        files[LAYER_PATH] = index[LAYER_PATH] = json.dumps(layers).encode()
    with pytest.raises(AssertionError):
        verify_dev10_composition(layers, files, index)
