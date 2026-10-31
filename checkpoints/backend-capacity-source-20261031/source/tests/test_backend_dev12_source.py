"""Exact local dev12 source delta; immutable dev11 proof remains separate."""
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import tomllib

import pytest

from scripts import source_staging_guard as guard
from scripts.public_history import BASELINE, HistoryError, PublicHistory
from test_backend_dev11_integration import canonical, pin, git, tree_modes, dev11_revision

ROOT = Path(__file__).resolve().parents[1]
BASE = '5b71122f17e4a72cac6ad4120d1c707d9b2133fb'
BASE_TREE = '7e7547e1b49473dd24c69887e772b4580014c236'
MANIFEST = 'docs/BACKEND_DEV12_PARTIAL_RECOVERY.json'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
GATE = 'tests/test_backend_dev12_source.py'
ADDED = {MANIFEST, 'docs/BACKEND_DEV12_PARTIAL_RECOVERY.md', GATE,
         'audio_backend/moss_td_recovery.py', 'tests/test_moss_td_partial_recovery.py'}
CHANGED = {'README.md', 'pyproject.toml', 'audio_backend/__init__.py', POLICY,
           'tests/test_backend_dev11_integration.py', 'tests/test_fragment_staging_sources.py',
           'audio_backend/moss_td_candidates.py', 'audio_backend/overlap_store.py',
           'audio_backend/overlap_candidates.py', 'audio_backend/overlap_ownership.py'}
PURPOSE = ('Isolated dev12 partial schema-valid phrase recovery; strict parser, '
           'immutable historical pins and privacy exclusions unchanged')
SCOPE = {
    'feature': 'partial_schema_valid_phrases_v1',
    'normalization': 'none; no timestamp repair or clipping',
    'history': 'Exact dev11 baseline and all earlier public-only historical proofs retained',
    'acceptance': 'Synthetic model-free source candidate; no release or real-model quality acceptance',
}
# Independently reviewed pins, separate from the mutable manifest rows.
REVIEWED = {'README.md': {'bytes': 13664,
               'sha256': '3fe390538823f03aced61914a88c46a3a650ec41478707a1cb02adeb42994d4d',
               'source_blob': '7c303467853e840a875fbf8de0bff4a1894c8038'},
 'audio_backend/__init__.py': {'bytes': 108,
                               'sha256': '9fe54da9a34fb1e591f08583a29df0cbd63fd527b49ed36182f180cd65256ca1',
                               'source_blob': 'fc0b98d72dcfa9be8dfea37c1178a2416296428b'},
 'audio_backend/moss_td_candidates.py': {'bytes': 16598,
                                         'sha256': '396b020d784d98b88e8195e0df563ac3ef4559bc0ced92e1853fa113f62c4423',
                                         'source_blob': '424923571d6c0b167b6f4ef9e580dd82ac76bd65'},
 'audio_backend/moss_td_recovery.py': {'bytes': 5083,
                                       'sha256': 'cb630d475fa8d6cfcb7aa17ac2a23e79ba4bb1553015c1cf8012408376ad0b72',
                                       'source_blob': '6b871f2bf896d983fb9532539dcd4b8728226e9b'},
 'audio_backend/overlap_candidates.py': {'bytes': 21709,
                                         'sha256': 'a539c4adaa1b9b5616ff7fd5b6a6ea543f7c664040498aef3b78e5be82cbbb31',
                                         'source_blob': '188960373ce8587f3058252060bede82072d9418'},
 'audio_backend/overlap_ownership.py': {'bytes': 12916,
                                        'sha256': '5d579adebec2bf7d7f153131c8f06e772af1d516efba8ec43946778c5b900e75',
                                        'source_blob': 'cf5b8bf2ef80c170d45a6d893028d90ece6013c1'},
 'audio_backend/overlap_store.py': {'bytes': 34123,
                                    'sha256': 'f84b27db0e0f1a2b35df320f26ead6d404b29161f4587403fdedc77eb3ed1843',
                                    'source_blob': '703d0a002e23d1573d66ae8a2d4a6304b9e3c807'},
 'docs/BACKEND_DEV12_PARTIAL_RECOVERY.md': {'bytes': 4592,
                                            'sha256': 'b20b67ca3c3fa0efafdc7fb61c433f45e4a5f1a4760427f59345cda951014e9c',
                                            'source_blob': '51a814c09b7b0802f61473403d837a4ad9205b84'},
 'docs/FRAGMENT_STAGING_POLICY.json': {'bytes': 12973,
                                       'sha256': '08bf3b8bd3d6be4ae775c8dfe6b38561bf6dd59771584ca28748cc69549c6738',
                                       'source_blob': '15668cbd5f938fa8bd537ebf5a9e76e841795d54'},
 'pyproject.toml': {'bytes': 829,
                    'sha256': '17d309939b13eaa5f6292f434be2dde6b7d64d9218d0920e69c5b9e7adf3fa74',
                    'source_blob': '1fdc4bbd03b1909bac729475949448155a218a0c'},
 'tests/test_backend_dev11_integration.py': {'bytes': 45783,
                                             'sha256': '3c59adf5254b6722becb1c3ee6ebdbb362caf0fa414f21ddbab3087573a16768',
                                             'source_blob': '243f7a1a0511e76ebbd73ead6666e61e47e36333'},
 'tests/test_fragment_staging_sources.py': {'bytes': 50920,
                                            'sha256': '6ff58045702ef0d1b6630a8de6230b44ec907edf24d9395928cd30fd0a8f78b9',
                                            'source_blob': '88a8486d460f69449f0a7a2a2f35d9b7682e94d3'},
 'tests/test_moss_td_partial_recovery.py': {'bytes': 22049,
                                            'sha256': 'd550dc7f02f97039182fa7147b756da4f4f4470e0861c5ee4479257999b9af62',
                                            'source_blob': '5021771f1fa63ea62ab2bf0401746e6a14242952'}}


@lru_cache(maxsize=1)
def baseline():
    assert dev11_revision() == BASE
    files = guard.read_git_tree(BASE)
    assert len(files) == 283 and sum(map(len, files.values())) == 3317327
    return files


def expected_manifest(files):
    base = baseline()
    rows = []
    for path in sorted((ADDED | CHANGED) - {MANIFEST}):
        row = {'path': path, **pin(files[path])}
        row.update({'supersedes': pin(base[path])} if path in base else {'base_absent': True})
        rows.append(row)
    return {'schema': 'reviewed-backend-dev12-partial-recovery-v1',
            'version': '0.2.0rc2.dev12', 'base_commit': BASE, 'base_tree': BASE_TREE,
            'scope': SCOPE, 'source_budget': {'maximum_files': 288,
                'maximum_total_bytes': 3400000, 'maximum_file_bytes': 250000},
            'manifest_self_hash': 'excluded-to-avoid-recursion; exact schema and canonical serialization validated',
            'files': rows}


def verify_source(files, index, modes, index_modes):
    base = baseline()
    assert files == index, 'Dev12 worktree/index mismatch'
    assert set(files) == set(base) | ADDED and len(files) == 288
    assert {path for path in files if files[path] != base.get(path)} == ADDED | CHANGED
    assert files[MANIFEST] == canonical(expected_manifest(files))
    assert set(REVIEWED) == (ADDED | CHANGED) - {MANIFEST, GATE}
    for path, expected in REVIEWED.items():
        assert pin(files[path]) == expected, 'Reviewed dev12 source changed: ' + path
    for path in set(base) - CHANGED:
        assert files[path] == base[path], 'Inherited historical or runtime source changed: ' + path
    for path in ['audio_backend/__init__.py', 'pyproject.toml']:
        assert files[path] == base[path].replace(b'0.2.0rc2.dev11', b'0.2.0rc2.dev12')
    assert tomllib.loads(files['pyproject.toml'].decode())['project']['version'] == '0.2.0rc2.dev12'
    old_policy = json.loads(base[POLICY])
    policy = dict(old_policy, purpose=PURPOSE, maximum_files=288,
                  allowed_files=sorted(set(old_policy['allowed_files']) | ADDED))
    assert files[POLICY] == canonical(policy)
    assert set(policy['allowed_files']) == set(files)
    assert guard.MAX_BYTES == 250000 and policy['maximum_total_bytes'] == 3400000
    assert guard.validate(files, policy, require_manifest=False)['findings'] == 0
    expected_modes = {**tree_modes(BASE), **{path: '100644' for path in ADDED}}
    assert modes == index_modes == expected_modes
    for target in re.findall(r'\]\(([^)]+)\)', files['README.md'].decode()):
        if '://' not in target and not target.startswith('#'):
            assert target.split('#', 1)[0] in files


@pytest.fixture(scope='module')
def current_sources():
    # Preserve the immutable public dev12 proof; dev13 has its own current gate.
    revision = 'b291776174e0a8d4f74d8ca988931c7a7cba1956'
    files = guard.read_git_tree(revision)
    modes = tree_modes(revision)
    return files, dict(files), modes, dict(modes)


def test_dev12_exact_current_source(current_sources):
    verify_source(*current_sources)


def test_dev12_lineage_and_committed_tree(current_sources):
    dev11_revision()
    merge_path = Path(git('rev-parse', '--git-path', 'MERGE_HEAD').decode().strip())
    if not merge_path.is_absolute(): merge_path = ROOT / merge_path
    assert not merge_path.exists(), 'Dev12 must retain exactly one baseline parent'
    head = 'b291776174e0a8d4f74d8ca988931c7a7cba1956'
    git('merge-base', '--is-ancestor', head, 'HEAD')
    if head == BASE:  # Exact staged candidate before its one-parent commit.
        return
    headers = git('cat-file', 'commit', head).split(b'\n\n', 1)[0].splitlines()
    assert [line[7:].decode() for line in headers if line.startswith(b'parent ')] == [BASE]
    assert guard.read_git_tree(head) == current_sources[1]
    assert tree_modes(head) == current_sources[3]


def test_dev12_public_only_history_unchanged():
    history = PublicHistory(ROOT)
    common = Path(git('rev-parse', '--git-common-dir').decode().strip())
    if not common.is_absolute(): common = ROOT / common
    assert not any(os.environ.get(key) for key in (
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_DIR', 'GIT_WORK_TREE'))
    assert not (common / 'objects/info/alternates').exists()
    assert not (common / 'info/grafts').exists()
    assert not git('for-each-ref', 'refs/replace').strip()
    assert git('rev-parse', '--is-shallow-repository').strip() == b'false'
    assert len(history.pins) == 16
    for original in history.pins:
        history.resolve(original)
        if original != BASELINE:
            with pytest.raises(HistoryError): history._git('cat-file', '-t', original)


@pytest.mark.parametrize('mutation', [
    'missing', 'extra', 'runtime', 'strict_parser', 'historical_gate', 'history_map',
    'historical_manifest', 'version', 'policy_cap', 'policy_count', 'policy_privacy',
    'policy_paths', 'manifest_schema', 'manifest_base', 'manifest_tree', 'manifest_scope',
    'manifest_version', 'manifest_type', 'manifest_duplicate', 'manifest_self',
    'manifest_order', 'manifest_format', 'worktree', 'index', 'mode', 'index_mode',
])
def test_dev12_rejects_unreviewed_source(mutation, current_sources):
    verify_source(*current_sources)
    files, index, modes, index_modes = map(dict, current_sources)
    if mutation == 'missing': files.pop('docs/API.md')
    elif mutation == 'extra': files['docs/unreviewed.md'] = b'synthetic'
    elif mutation in {'runtime', 'strict_parser', 'historical_gate', 'history_map',
                      'historical_manifest', 'version', 'worktree', 'index'}:
        path = {'runtime': 'audio_backend/moss_td_recovery.py',
                'strict_parser': 'audio_backend/inference/moss_td_cpp_asr.py',
                'historical_gate': 'tests/test_backend_dev11_integration.py',
                'history_map': 'docs/PUBLIC_HISTORY_MAP.json',
                'historical_manifest': 'docs/BACKEND_DEV11_INTEGRATION.json',
                'version': 'pyproject.toml'}.get(mutation, 'README.md')
        (index if mutation == 'index' else files)[path] += b'\n'
    elif mutation.startswith('policy_'):
        value = json.loads(files[POLICY])
        if mutation == 'policy_cap': value['maximum_total_bytes'] += 1
        elif mutation == 'policy_count': value['maximum_files'] += 1
        elif mutation == 'policy_paths': value['allowed_files'].append('docs/unreviewed.md')
        else: value['excluded_categories'].pop()
        files[POLICY] = canonical(value)
    elif mutation in {'mode', 'index_mode'}:
        (index_modes if mutation == 'index_mode' else modes)['audio_backend/moss_td_recovery.py'] = '100755'
    else:
        value = json.loads(files[MANIFEST])
        if mutation == 'manifest_schema': value['schema'] = 'unreviewed'
        elif mutation == 'manifest_base': value['base_commit'] = '0' * 40
        elif mutation == 'manifest_tree': value['base_tree'] = '0' * 40
        elif mutation == 'manifest_scope': value['scope']['normalization'] = 'repair timestamps'
        elif mutation == 'manifest_version': value['version'] = 'release'
        elif mutation == 'manifest_type': value['source_budget']['maximum_files'] = 288.0
        elif mutation == 'manifest_duplicate': value['files'].append(value['files'][0])
        elif mutation == 'manifest_self': value['files'].append({'path': MANIFEST, **pin(files[MANIFEST])})
        elif mutation == 'manifest_order': value['files'].reverse()
        files[MANIFEST] = canonical(value) + (b'\n' if mutation == 'manifest_format' else b'')
    # Refreshed manifest cannot legitimize unreviewed runtime/history/policy bytes.
    if mutation not in {'missing', 'worktree', 'index'} and not mutation.startswith('manifest_'):
        files[MANIFEST] = canonical(expected_manifest(files))
    if mutation not in {'worktree', 'index'}: index = dict(files)
    with pytest.raises((AssertionError, ValueError)):
        verify_source(files, index, modes, index_modes)
