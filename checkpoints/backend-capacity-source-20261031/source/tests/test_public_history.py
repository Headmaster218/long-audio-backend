"""Public-only resolution and exact dev10 recovery delta, including rejection cases."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from scripts.public_history import BASELINE, MAP_PATH, HistoryError, PublicHistory
from scripts import source_staging_guard as guard

ROOT = Path(__file__).resolve().parents[1]
FOLLOWUP = 'docs/PUBLIC_RECOVERY_FOLLOWUP.json'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
ADDED = {MAP_PATH, FOLLOWUP, 'docs/PUBLIC_REPRODUCIBILITY.md',
         'scripts/public_history.py', 'tests/test_public_history.py'}
CHANGED = {'README.md', POLICY, 'tests/test_fragment_staging_sources.py'}
SCOPE = 'Public Git-only dev10 provenance tooling and documentation; no runtime or acceptance changes'
RECOVERY = '366797b5c0bba70975507b71db8b4596b6d9344f'
RECOVERY_TREE = 'dc6c9bd493238b4b6c41b087eb668a60f1726895'


def recovery_revision():
    history = PublicHistory(ROOT)
    headers = history._git('cat-file', 'commit', RECOVERY).split(b'\n\n', 1)[0].splitlines()
    assert [line[5:].decode() for line in headers if line.startswith(b'tree ')] == [RECOVERY_TREE]
    assert [line[7:].decode() for line in headers if line.startswith(b'parent ')] == [BASELINE]
    return RECOVERY


def canonical(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def pin(data):
    blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\x00' + data).hexdigest()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'source_blob': blob}


@pytest.fixture(scope='module')
def baseline():
    return guard.read_git_tree(PublicHistory(ROOT).resolve(BASELINE))


@pytest.fixture(scope='module')
def recovery_sources():
    historical = guard.read_git_tree(recovery_revision())
    return historical, historical


def verify_followup(files, index, base):
    assert files == index, 'Recovery worktree/index mismatch'
    assert set(files) == set(base) | ADDED
    assert {path for path in files if files[path] != base.get(path)} == ADDED | CHANGED
    value = json.loads(files[FOLLOWUP])
    assert set(value) == {'schema', 'base_commit', 'scope', 'files'}
    assert value['schema'] == 'reviewed-public-recovery-v1'
    assert value['base_commit'] == BASELINE and value['scope'] == SCOPE
    assert files[FOLLOWUP] == canonical(value), 'Noncanonical recovery manifest'
    expected = []
    for path in sorted((ADDED | CHANGED) - {FOLLOWUP}):
        row = {'path': path, **pin(files[path])}
        row.update({'supersedes': pin(base[path])} if path in base else {'base_absent': True})
        expected.append(row)
    assert value['files'] == expected, 'Recovery delta differs from exact reviewed pins'
    assert files[FOLLOWUP] == canonical(dict(value, files=expected)), 'Recovery pin types differ'
    policy, original = json.loads(files[POLICY]), json.loads(base[POLICY])
    expected_policy = dict(original, maximum_files=277,
                           allowed_files=sorted(set(original['allowed_files']) | ADDED))
    assert policy == expected_policy, 'Source/privacy policy changed'
    assert files[POLICY] == canonical(expected_policy)
    assert guard.MAX_BYTES == 250_000
    assert guard.validate(files, policy, require_manifest=False)['findings'] == 0


def test_historical_exact_reviewed_recovery_followup(recovery_sources, baseline):
    verify_followup(*recovery_sources, baseline)


@pytest.mark.parametrize('mutation', [
    'unknown_file', 'missing_file', 'runtime', 'feature', 'old_manifest', 'version',
    'unpinned_bytes', 'worktree', 'index', 'policy_cap', 'policy_privacy', 'policy_allowlist',
    'manifest_schema', 'manifest_base', 'manifest_scope', 'manifest_extra',
    'manifest_missing', 'manifest_duplicate', 'manifest_self_pin', 'manifest_supersedes',
    'manifest_absence', 'manifest_absence_type', 'manifest_order', 'manifest_format',
])
def test_unreviewed_recovery_is_rejected(mutation, recovery_sources, baseline):
    verify_followup(*recovery_sources, baseline)
    files, index = map(dict, recovery_sources)
    value = json.loads(files[FOLLOWUP])
    path = 'README.md'
    if mutation == 'unknown_file': files['docs/unreviewed.md'] = b'unreviewed'
    elif mutation == 'missing_file': files.pop('docs/API.md')
    elif mutation in {'runtime', 'feature', 'old_manifest', 'version', 'unpinned_bytes', 'worktree', 'index'}:
        path = {'runtime': 'audio_backend/app.py', 'feature': 'audio_backend/overlap_store.py',
                'old_manifest': 'docs/FRAGMENT_STAGING_LAYERS.json', 'version': 'pyproject.toml'}.get(mutation, path)
        (index if mutation == 'index' else files)[path] += b'\n'
    elif mutation.startswith('policy_'):
        policy = json.loads(files[POLICY])
        if mutation == 'policy_cap': policy['maximum_total_bytes'] += 1
        elif mutation == 'policy_privacy': policy['excluded_categories'].pop()
        else: policy['allowed_files'].append('docs/unreviewed.md')
        files[POLICY] = canonical(policy)
    elif mutation == 'manifest_schema': value['schema'] = 'unreviewed'
    elif mutation == 'manifest_base': value['base_commit'] = '0' * 40
    elif mutation == 'manifest_scope': value['scope'] = 'model quality accepted'
    elif mutation == 'manifest_extra': value['accepted'] = True
    elif mutation == 'manifest_missing': value['files'].pop()
    elif mutation == 'manifest_duplicate': value['files'].append(value['files'][0])
    elif mutation == 'manifest_self_pin': value['files'].append({'path': FOLLOWUP, **pin(files[FOLLOWUP])})
    elif mutation == 'manifest_supersedes': value['files'][0]['supersedes']['source_blob'] = '0' * 40
    elif mutation in {'manifest_absence', 'manifest_absence_type'}:
        next(row for row in value['files'] if 'base_absent' in row)['base_absent'] = (
            False if mutation == 'manifest_absence' else 1)
    elif mutation == 'manifest_order': value['files'].reverse()
    if mutation.startswith('manifest_'):
        files[FOLLOWUP] = canonical(value) + (b'\n' if mutation == 'manifest_format' else b'')
    if mutation not in {'worktree', 'index'}:
        index = dict(files)
    with pytest.raises(AssertionError):
        verify_followup(files, index, baseline)


def test_public_object_store_has_no_original_objects_or_alternates():
    history = PublicHistory(ROOT)
    common = Path(history._git('rev-parse', '--git-common-dir').decode().strip())
    if not common.is_absolute(): common = ROOT / common
    assert not any(os.environ.get(key) for key in ('GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_DIR', 'GIT_WORK_TREE'))
    assert not (common / 'objects/info/alternates').exists()
    assert not (common / 'info/grafts').exists()
    assert not history._git('for-each-ref', 'refs/replace').strip()
    assert history._git('rev-parse', '--is-shallow-repository').strip() == b'false'
    for original in history.pins:
        history.resolve(original)
        if original != BASELINE:
            with pytest.raises(HistoryError):
                history._git('cat-file', '-t', original)
    assert len(history.pins) == 16


@pytest.mark.parametrize('ref', ['HEAD', '--help', '0a27197', '0' * 40,
    BASELINE + '^', BASELINE + ':README.md', '74b51d282c53ff0354fde52e491627a5804aa633'])
def test_unknown_or_option_revision_is_rejected(ref):
    history = PublicHistory(ROOT)
    with pytest.raises(HistoryError): history.resolve(ref)
    with pytest.raises(HistoryError): history.file_oid(ref, 'missing.txt')


@pytest.mark.parametrize('path', ['../README.md', '/README.md', '--help', 'docs//API.md', 'README.md\n', 'HEAD:README.md'])
def test_nonliteral_paths_are_rejected(path):
    with pytest.raises(HistoryError): PublicHistory(ROOT).file_oid(BASELINE, path)


def test_absence_requires_verified_existing_tree():
    history = PublicHistory(ROOT)
    assert history.file_oid(BASELINE, 'docs/definitely-absent.txt') is None
    with pytest.raises(FileNotFoundError): history.read_file(BASELINE, 'docs/definitely-absent.txt')
    assert history.read_file(BASELINE, 'README.md')


@pytest.mark.parametrize('mutation', ['public', 'tree', 'parent_order', 'parent_missing'])
def test_exact_public_structure_is_required(mutation):
    history = PublicHistory(ROOT)
    original = '117e8414c4f524477db83886ab3731a5c3afc0b4'
    row = history.pins[original]
    if mutation == 'public': row['public'] = BASELINE
    elif mutation == 'tree': row['tree'] = '0' * 40
    elif mutation == 'parent_order': row['parents'].reverse()
    else: row['parents'].pop()
    with pytest.raises(HistoryError): history.resolve(original)


def test_changed_map_digest_is_rejected(tmp_path):
    path = tmp_path / MAP_PATH
    path.parent.mkdir()
    path.write_bytes((ROOT / MAP_PATH).read_bytes() + b'\n')
    with pytest.raises(HistoryError): PublicHistory(tmp_path)


@pytest.mark.parametrize('missing', ['commit', 'tree', 'parent', 'blob', 'subtree'])
def test_missing_public_objects_never_masquerade_as_absence(tmp_path, missing):
    path = tmp_path / MAP_PATH
    path.parent.mkdir()
    path.write_bytes((ROOT / MAP_PATH).read_bytes())
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    source, sparse = PublicHistory(ROOT), PublicHistory(tmp_path)
    row = source.pins[BASELINE]
    objects = [] if missing == 'commit' else [(BASELINE, 'commit')]
    if missing not in {'commit', 'tree'}: objects.append((row['tree'], 'tree'))
    if missing not in {'commit', 'parent'}: objects.extend((oid, 'commit') for oid in row['parents'])
    for oid, kind in objects:
        result = subprocess.check_output(['git', 'hash-object', '-w', '-t', kind, '--stdin'],
                    cwd=tmp_path, input=source._git('cat-file', kind, oid)).decode().strip()
        assert result == oid
    target = 'docs/definitely-absent.txt' if missing == 'subtree' else 'README.md'
    with pytest.raises(HistoryError): sparse.file_oid(BASELINE, target)


def test_unsupported_graph_operations_fail_closed():
    history = PublicHistory(ROOT)
    with pytest.raises(HistoryError): history.merge_base(BASELINE)
    with pytest.raises(HistoryError): history.parents('0de25be7b0e8983aceb1e5591f41f6cf667851fc')


def test_historical_readme_relative_links_exist(recovery_sources):
    files, _ = recovery_sources
    for target in re.findall(r'\]\(([^)]+)\)', files['README.md'].decode()):
        if '://' not in target and not target.startswith('#'):
            assert target.split('#', 1)[0] in files, target


def verify_modes(base, index, worktree):
    expected = {**base, **{path: '100644' for path in ADDED}}
    assert index == worktree == expected, 'Unreviewed Git executable mode change'


def test_exact_recovery_file_modes():
    history = PublicHistory(ROOT)
    base = {}
    for entry in history._git('ls-tree', '-rz', history.resolve(BASELINE)).split(b'\x00'):
        if entry:
            header, name = entry.decode().split('\t')
            base[name] = header.split()[0]
    recovered = {}
    for entry in history._git('ls-tree', '-rz', recovery_revision()).split(b'\x00'):
        if entry:
            header, name = entry.decode().split('\t')
            recovered[name] = header.split()[0]
    verify_modes(base, recovered, recovered)


@pytest.mark.parametrize('mutation', ['inherited', 'added', 'worktree'])
def test_unreviewed_modes_are_rejected(mutation):
    base = {'audio_backend/app.py': '100644'}
    index = {**base, **{path: '100644' for path in ADDED}}
    worktree = dict(index)
    path = MAP_PATH if mutation == 'added' else 'audio_backend/app.py'
    worktree[path] = '100755'
    if mutation != 'worktree': index[path] = '100755'
    with pytest.raises(AssertionError): verify_modes(base, index, worktree)
