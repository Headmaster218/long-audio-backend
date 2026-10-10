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
    assert policy['maximum_files'] == 270
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
        verify_import_bytes(effective,lambda path:(ROOT/path).read_bytes())


def test_candidate_layer_preserves_all_historical_import_facts():
    historical = json.loads(subprocess.check_output(
        ['git','show','44f1019533200261fe687d6814a433a05c59bd27:docs/FRAGMENT_STAGING_LAYERS.json'],cwd=ROOT))
    _, current = import_records()
    assert current['base_manifest_sha256'] == historical['base_manifest_sha256']
    assert current['updates'][:-1] == historical['updates']
    assert current['reviewed_layer_order'][:-1] == historical['reviewed_layer_order']


def test_reviewed_import_index_bytes_are_exact():
    base, layers = import_records()
    verify_import_bytes(reviewed_imports(base,layers),
                        lambda path:subprocess.check_output(['git','show',':'+path],cwd=ROOT))


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
    assert current['target_version'] == '0.2.0rc2.dev8'
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
HISTORICAL_DEV8 = '5ec6c9d5bd3febf2c30695120b969e6b70a1c0d2'
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


def dev8_bytes(path):
    return subprocess.check_output(['git', 'show', HISTORICAL_DEV8 + ':' + path], cwd=ROOT)


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


def test_historical_dev8_composition_exact_parent_inputs():
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


def test_historical_dev8_exact_union_preserves_all_historical_provenance():
    assert (ROOT / LAYER_PATH).read_bytes() == subprocess.check_output(['git', 'show', ':' + LAYER_PATH], cwd=ROOT)
    verify_dev8_inherited_bytes(guard.read_git_tree(HISTORICAL_DEV8))
    _, current = import_records()
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
    files = guard.read_git_tree(HISTORICAL_DEV8); files[path] += b'\n'
    with pytest.raises(AssertionError):
        verify_dev8_inherited_bytes(files)


NUMERIC_MANIFEST = 'docs/EVENT_CANDIDATE_NUMERIC_SOURCE.json'
NUMERIC_ADDITIONS = {NUMERIC_MANIFEST, 'docs/EVENT_CANDIDATE_NUMERIC_OWNERSHIP.md',
                     'tests/test_event_candidate_numeric_projection.py'}
NUMERIC_PRODUCTION = {
    'audio_backend/event_candidate_store.py': '0e8224ae8ecc7a8f56b4bd461e11e65816f04ee19370c3873dbbc2f20d7cf968',
    'audio_backend/storage.py': 'c684d14b65e12d39ce6919323467bcc5ca8bd8c568c750520c5dd8fa24bfc401',
    'audio_backend/worker.py': '51e56dbe04cb9bf85e1ee265744856a0257689a7f5b734299da74dbb132050af',
}
NUMERIC_CHANGED = set(NUMERIC_PRODUCTION) | {
    'docs/FRAGMENT_STAGING_POLICY.json', 'tests/test_fragment_staging_sources.py'}
NUMERIC_SCOPE = {
    'status': 'standalone-dev8-derived-source-fix-not-a-release',
    'production': 'event candidate numeric ownership and bounded diagnostic persistence only',
    'unchanged': 'ASR, detector, speaker models, thresholds, schema and historical provenance',
    'validation': 'synthetic model-free numeric and transactional checks; no quality claim',
}


def verify_numeric_source_followup(files, baseline):
    """Pin only the reviewed delta while retaining every historical byte outside it."""
    assert set(baseline) == set(json.loads(baseline['docs/FRAGMENT_STAGING_POLICY.json'])['allowed_files'])
    assert len(baseline) == 267
    assert set(files) == set(baseline) | NUMERIC_ADDITIONS
    changed = {path for path in baseline if files[path] != baseline[path]}
    assert changed == NUMERIC_CHANGED, 'Unreviewed or missing inherited-source delta'
    manifest = json.loads(files[NUMERIC_MANIFEST])
    assert set(manifest) == {'schema', 'base_commit', 'public_base_commit', 'base_tree',
                            'version', 'scope', 'manifest_self_hash', 'files'}
    assert manifest['schema'] == 'reviewed-event-numeric-source-v1'
    assert manifest['base_commit'] == HISTORICAL_DEV8
    assert manifest['public_base_commit'] == '4bde1e8d2fd050a10a1168e4cb46f80a5db6d28c'
    assert manifest['base_tree'] == 'd6729a14d0981a58d191f21b788af62772155085'
    assert manifest['version'] == '0.2.0rc2.dev8'
    assert manifest['scope'] == NUMERIC_SCOPE
    assert manifest['manifest_self_hash'] == 'excluded-to-avoid-recursion; structure-validated'
    rows = manifest['files']
    expected = NUMERIC_CHANGED | (NUMERIC_ADDITIONS - {NUMERIC_MANIFEST})
    assert len(rows) == len(expected) and {row['path'] for row in rows} == expected
    for row in rows:
        path = row['path']; data = files[path]
        fields = {'path', 'bytes', 'sha256', 'source_blob'}
        if path in baseline:
            assert set(row) == fields | {'supersedes'}
            old = baseline[path]
            assert row['supersedes'] == {
                'sha256': hashlib.sha256(old).hexdigest(),
                'source_blob': hashlib.sha1(b'blob ' + str(len(old)).encode() + b'\0' + old).hexdigest()}
        else:
            assert set(row) == fields | {'base_absent'} and row['base_absent'] is True
        assert row['bytes'] == len(data) and row['sha256'] == hashlib.sha256(data).hexdigest()
        assert row['source_blob'] == hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    for path, digest in NUMERIC_PRODUCTION.items():
        assert hashlib.sha256(files[path]).hexdigest() == digest, 'Reviewed production bytes changed'
    previous_policy = json.loads(baseline['docs/FRAGMENT_STAGING_POLICY.json'])
    policy = json.loads(files['docs/FRAGMENT_STAGING_POLICY.json'])
    expected_policy = dict(previous_policy, maximum_files=270,
                           allowed_files=sorted(set(previous_policy['allowed_files']) | NUMERIC_ADDITIONS))
    assert policy == expected_policy, 'Policy broadened beyond the three reviewed additions'
    assert guard.MAX_BYTES == 250_000
    assert guard.validate(files, policy, require_manifest=False)['findings'] == 0


def test_numeric_source_exact_worktree_index_and_historical_tree():
    baseline = guard.read_git_tree(HISTORICAL_DEV8)
    assert subprocess.check_output(['git', 'rev-parse', HISTORICAL_DEV8 + '^{tree}'], cwd=ROOT).decode().strip() == 'd6729a14d0981a58d191f21b788af62772155085'
    staged = guard.read_git_tree(':')
    worktree = {path: (ROOT / path).read_bytes() for path in staged}
    assert worktree == staged, 'Numeric follow-up worktree/index mismatch'
    verify_numeric_source_followup(staged, baseline)
    verify_numeric_source_followup(worktree, baseline)


@pytest.mark.parametrize('mutation', [
    'missing_file', 'extra_file', 'production', 'inherited', 'manifest_key', 'manifest_self',
    'base', 'public_base', 'base_tree', 'version', 'scope', 'missing_row', 'duplicate_row',
    'extra_row', 'bytes', 'hash', 'blob', 'supersedes', 'absence', 'policy_budget',
    'policy_paths', 'policy_exclusions', 'document', 'test', 'layer_history',
])
def test_numeric_source_rejects_unreviewed_delta(mutation):
    baseline = guard.read_git_tree(HISTORICAL_DEV8)
    files = guard.read_git_tree(':')
    # A passing baseline prevents a pre-existing failure from masking a weak case.
    verify_numeric_source_followup(files, baseline)
    manifest = json.loads(files[NUMERIC_MANIFEST])
    policy_path = 'docs/FRAGMENT_STAGING_POLICY.json'
    if mutation == 'missing_file': files.pop('tests/test_event_candidate_numeric_projection.py')
    elif mutation == 'extra_file': files['docs/unreviewed.md'] = b'synthetic'
    elif mutation == 'production': files['audio_backend/worker.py'] += b'\n'
    elif mutation == 'inherited': files['audio_backend/settings.py'] += b'\n'
    elif mutation == 'manifest_key': manifest['unreviewed'] = True
    elif mutation == 'manifest_self': manifest['manifest_self_hash'] = 'not checked'
    elif mutation == 'base': manifest['base_commit'] = '0' * 40
    elif mutation == 'public_base': manifest['public_base_commit'] = '0' * 40
    elif mutation == 'base_tree': manifest['base_tree'] = '0' * 40
    elif mutation == 'version': manifest['version'] = 'release'
    elif mutation == 'scope': manifest['scope']['status'] = 'production-ready'
    elif mutation == 'missing_row': manifest['files'].pop()
    elif mutation == 'duplicate_row': manifest['files'].append(manifest['files'][0])
    elif mutation == 'extra_row': manifest['files'][0]['path'] = NUMERIC_MANIFEST
    elif mutation == 'bytes': manifest['files'][0]['bytes'] += 1
    elif mutation == 'hash': manifest['files'][0]['sha256'] = '0' * 64
    elif mutation == 'blob': manifest['files'][0]['source_blob'] = '0' * 40
    elif mutation == 'supersedes': next(r for r in manifest['files'] if 'supersedes' in r)['supersedes']['sha256'] = '0' * 64
    elif mutation == 'absence': next(r for r in manifest['files'] if 'base_absent' in r)['base_absent'] = False
    elif mutation.startswith('policy_'):
        policy = json.loads(files[policy_path])
        if mutation == 'policy_budget': policy['maximum_total_bytes'] += 1
        elif mutation == 'policy_paths': policy['allowed_files'].append('docs/unreviewed.md')
        else: policy['excluded_categories'].pop()
        files[policy_path] = json.dumps(policy).encode()
        # Refresh this row so the policy-specific restriction must reject it.
        row = next(r for r in manifest['files'] if r['path'] == policy_path)
        data = files[policy_path]
        row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                   source_blob=hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest())
    elif mutation == 'document': files['docs/EVENT_CANDIDATE_NUMERIC_OWNERSHIP.md'] += b'\n'
    elif mutation == 'test': files['tests/test_event_candidate_numeric_projection.py'] += b'\n'
    elif mutation == 'layer_history': files[LAYER_PATH] += b'\n'
    files[NUMERIC_MANIFEST] = json.dumps(manifest).encode()
    with pytest.raises((AssertionError, ValueError)):
        verify_numeric_source_followup(files, baseline)
