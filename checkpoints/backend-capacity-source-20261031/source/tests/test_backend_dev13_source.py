"""Exact dev13 candidate source delta and unchanged historical/runtime scope."""
import json
from pathlib import Path
import tomllib
import pytest
from scripts import source_staging_guard as guard
from test_backend_dev11_integration import canonical, pin, git, tree_modes

ROOT = Path(__file__).resolve().parents[1]
BASE = 'b291776174e0a8d4f74d8ca988931c7a7cba1956'
BASE_TREE = '091a197f2da2edf3f14b75d4100f6b7bc624c275'
MANIFEST = 'docs/BACKEND_DEV13_QWEN_RECOVERY.json'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
GATE = 'tests/test_backend_dev13_source.py'
ADDED = {MANIFEST, GATE, 'audio_backend/qwen_recovery.py',
         'tests/test_qwen_recovery.py', 'docs/QWEN_BOUNDED_RECOVERY.md'}
CHANGED = {'README.md', 'pyproject.toml', 'audio_backend/__init__.py', POLICY,
           'tests/test_backend_dev12_source.py'}
PURPOSE = 'Isolated dev13 experimental pure Qwen bounded recovery; native integration absent; historical and privacy proofs retained'
REVIEWED = {'README.md': {'bytes': 13823,
               'sha256': '9be42e7e2df9b537347af4af21f826c9fe289a932ff578e02cbb9959fbca7cd4',
               'source_blob': '7c530a7104395dde4944504f4c7dc6d59ac09ed5'},
 'audio_backend/__init__.py': {'bytes': 108,
                               'sha256': 'd5ba020afc8a4e51fc1882da38d8ae0bf6d8b6db137f194d145e498bcf601cc1',
                               'source_blob': '4ee100103f7c68bc3ce37f3aab8ea930f3f0b29c'},
 'audio_backend/qwen_recovery.py': {'bytes': 11177,
                                    'sha256': '6689b3222690bd5d1247dfe9b2165270ded6495472d8b6b67ebc784ca62e933f',
                                    'source_blob': '4cc94dbb5e7718620f560bba3b574f36594d5432'},
 'docs/FRAGMENT_STAGING_POLICY.json': {'bytes': 13165,
                                       'sha256': 'caf9c697b60772884e5a3734fc7eb92cc9588151139cb51b3bd27135fce567ad',
                                       'source_blob': 'b471ea6d59cc65405fb76cd6b11df5a66ead390f'},
 'docs/QWEN_BOUNDED_RECOVERY.md': {'bytes': 2771,
                                   'sha256': 'c8ec60111cbc598dc16c658ef197776a1a73625712d92ea14baaa50e60ca6b22',
                                   'source_blob': '2682d9e1b1606c802964e8bd05125c5dd737475d'},
 'pyproject.toml': {'bytes': 829,
                    'sha256': '1b905b59b5ee34ca0530b5024e8f9748cea5b60878eb8cb747aeed8aebf64965',
                    'source_blob': '5c94337237e2462b693fb5a05b85d428c5a73501'},
 'tests/test_backend_dev12_source.py': {'bytes': 13204,
                                        'sha256': 'b8c2b36b2d558e12a12c326f65db6157a2a8f1c48a19c4f41d4ce682e12eaca0',
                                        'source_blob': 'f3bd89f27e0e02547ed7fdae713082b2697687f5'},
 'tests/test_qwen_recovery.py': {'bytes': 10254,
                                 'sha256': '38a3e86106fae95467e5cd67fde220ae8de2e6afec087d7f24bcfceab650f8a2',
                                 'source_blob': '123098276272375a3dbd3c9ba6d183d4c24a2ff2'}}


def expected_manifest(files):
    base = guard.read_git_tree(BASE)
    return {'schema': 'reviewed-backend-dev13-qwen-recovery-v1',
        'version': '0.2.0rc2.dev13', 'base_commit': BASE, 'base_tree': BASE_TREE,
        'scope': 'Model-free opt-in full20s status18 two10s atomic recovery; no native CLI or primary integration',
        'source_budget': {'maximum_files': 293, 'maximum_total_bytes': 3420000,
                          'maximum_file_bytes': 250000},
        'files': [{'path': p, **pin(files[p]), **({'supersedes': pin(base[p])}
                  if p in base else {'base_absent': True})}
                  for p in sorted((ADDED | CHANGED) - {MANIFEST})]}


def verify(files, index, modes, index_modes):
    base = guard.read_git_tree(BASE)
    assert git('rev-parse', BASE+'^{tree}').decode().strip() == BASE_TREE
    assert len(base) == 288 and sum(map(len, base.values())) == 3377055
    assert files == index
    assert set(files) == set(base) | ADDED and len(files) == 293
    assert {p for p in files if files[p] != base.get(p)} == ADDED | CHANGED
    assert set(REVIEWED) == (ADDED | CHANGED) - {MANIFEST, GATE}
    for p, expected in REVIEWED.items(): assert pin(files[p]) == expected, p
    for p in set(base)-CHANGED: assert files[p] == base[p], p
    assert files[MANIFEST] == canonical(expected_manifest(files))
    old = json.loads(base[POLICY])
    policy = dict(old, purpose=PURPOSE, maximum_files=293, maximum_total_bytes=3420000,
                  allowed_files=sorted(set(old['allowed_files']) | ADDED))
    assert files[POLICY] == canonical(policy)
    assert guard.MAX_BYTES == 250000
    assert guard.validate(files, policy, require_manifest=False)['findings'] == 0
    for p in ['pyproject.toml', 'audio_backend/__init__.py']:
        assert files[p] == base[p].replace(b'0.2.0rc2.dev12', b'0.2.0rc2.dev13')
    assert tomllib.loads(files['pyproject.toml'].decode())['project']['version'] == '0.2.0rc2.dev13'
    assert modes == index_modes == {**tree_modes(BASE), **{p: '100644' for p in ADDED}}


@pytest.fixture(scope='module')
def current():
    # Preserve published dev13 proof; rc3.dev1 has a separate current-source gate.
    revision = 'd72ccfc3b606931c8d0ec689cdb880effbf8f7eb'
    files = guard.read_git_tree(revision)
    modes = tree_modes(revision)
    return files, dict(files), modes, dict(modes)


def test_dev13_exact_source(current):
    verify(*current)


def test_dev13_lineage(current):
    git('merge-base', '--is-ancestor', BASE, 'HEAD')
    merge = Path(git('rev-parse', '--git-path', 'MERGE_HEAD').decode().strip())
    assert not (merge if merge.is_absolute() else ROOT/merge).exists()
    head = 'd72ccfc3b606931c8d0ec689cdb880effbf8f7eb'
    git('merge-base', '--is-ancestor', head, 'HEAD')
    if head == BASE: return  # Exact staged precommit candidate.
    headers = git('cat-file', 'commit', head).split(b'\n\n', 1)[0].splitlines()
    assert [r[7:].decode() for r in headers if r.startswith(b'parent ')] == [BASE]
    assert guard.read_git_tree(head) == current[1]
    assert tree_modes(head) == current[3]


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'module', 'runtime', 'history',
    'version', 'budget', 'privacy', 'allowlist', 'manifest', 'mode', 'index'])
def test_dev13_rejects_unreviewed(mutation, current):
    verify(*current)
    files, index, modes, index_modes = map(dict, current)
    if mutation == 'missing': files.pop('README.md')
    elif mutation == 'extra': files['docs/unreviewed.md'] = b'unreviewed'
    elif mutation in ('module', 'runtime', 'history', 'version'):
        p = {'module': 'audio_backend/qwen_recovery.py',
             'runtime': 'audio_backend/qwen_candidate_runner.py',
             'history': 'docs/PUBLIC_HISTORY_MAP.json', 'version': 'pyproject.toml'}[mutation]
        files[p] += b'\n'
    elif mutation in ('budget', 'privacy', 'allowlist'):
        value = json.loads(files[POLICY])
        if mutation == 'budget': value['maximum_total_bytes'] += 1
        elif mutation == 'privacy': value['excluded_categories'].pop()
        else: value['allowed_files'].append('docs/unreviewed.md')
        files[POLICY] = canonical(value)
    elif mutation == 'manifest': files[MANIFEST] += b'\n'
    elif mutation == 'mode': modes['audio_backend/qwen_recovery.py'] = '100755'
    else: index['README.md'] += b'\n'
    if mutation not in ('missing', 'manifest', 'index'):
        files[MANIFEST] = canonical(expected_manifest(files))
    if mutation != 'index': index = dict(files)
    with pytest.raises((AssertionError, ValueError)):
        verify(files, index, modes, index_modes)
