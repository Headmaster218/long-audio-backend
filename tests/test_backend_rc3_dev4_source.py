"""Immutable rc3.dev4 source proof against published 762509bd; stdlib only.

The current_snapshot adapter checks the exact published tree and sole parent.
Original verifier/mutation bodies are preserved. This focused historical source
check does not restore an earlier full-suite or acoustic qualification.
The separate rc3.dev5 gate owns current worktree/index/external-seal checks.
"""
from __future__ import annotations

from functools import lru_cache
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASE = '2c841d84a9b3204e510628f4aef17fe90334e76c'
BASE_PARENT = 'fa8ed88c4691e0784cbb30baff66ca777c1de871'
BASE_TREE = 'bb31c31a9411ea4c0ec5e185cc9bae57ac31a1f0'
VERSION = '0.2.0rc3.dev4'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
MANIFEST = 'docs/BACKEND_RC3_DEV4_SOURCE.json'
GATE = 'tests/test_backend_rc3_dev4_source.py'
HISTORICAL = 'tests/test_backend_rc3_dev3_source.py'
GUARD = 'scripts/source_staging_guard.py'
MODULE = 'audio_backend/moss_handy_controller.py'
ADDED = frozenset(('audio_backend/moss_handy_adapter.py', 'audio_backend/moss_handy_controller.py', 'audio_backend/moss_handy_profile.py', 'audio_backend/moss_handy_runner.py', 'audio_backend/profiles/moss_handy_q8_0.json', 'docs/BACKEND_RC3_DEV4_SOURCE.json', 'docs/MOSS_HANDY_EXPERIMENTAL.md', 'tests/test_backend_rc3_dev4_source.py', 'tests/test_moss_handy_adapter.py', 'tests/test_moss_handy_controller.py', 'tests/test_moss_handy_profile.py'))
CHANGED = frozenset(('audio_backend/__init__.py', 'docs/FRAGMENT_STAGING_POLICY.json', 'pyproject.toml', 'tests/test_backend_rc3_dev3_source.py'))
DELTA = ADDED | CHANGED
MAX_FILES, MAX_TOTAL_BYTES, MAX_FILE_BYTES = 322, 4_000_000, 250_000
PURPOSE = 'Fresh rc3.dev4 experimental Handy MOSS one-job source; bounded model-free evidence; no restored qualification'
SCOPE = 'Explicit experimental Handy profile, raw-first unknown-EOS adapter and isolated MOSS-owned one-job controller; no app/Worker dispatch, API or scheduler changes'
QUALIFICATION = 'Model-free unit fixtures and tiny stdlib fake-worker wiring only when separately reported against frozen source; no new inference, acoustic, full-suite or long-recording qualification'


def require(value, reason):
    if not value:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def pin(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def git(*args, index=None, data=None, root=ROOT):
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1')
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    return subprocess.run(['git', *args], cwd=root, env=env, input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, timeout=20).stdout


def safe_name(name):
    parts = PurePosixPath(name).parts
    require(bool(parts) and name == str(PurePosixPath(name)) and
            not name.startswith('/') and '..' not in parts and
            '.git' not in parts and '\\' not in name, 'Unsafe source path')


def read_paths(root, names):
    require(len(names) == len(set(names)) <= MAX_FILES, 'Duplicate or excess source paths')
    files, modes, total = {}, {}, 0
    for name in sorted(names):
        safe_name(name)
        path = root
        for component in PurePosixPath(name).parts:
            path = path / component
            require(not path.is_symlink(), 'Source path contains a symlink')
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                'Source path must be a regular, unlinked file')
        require(info.st_size <= MAX_FILE_BYTES, 'Source file exceeds byte cap')
        total += info.st_size
        require(total <= MAX_TOTAL_BYTES, 'Source total exceeds byte cap')
        data = path.read_bytes()
        require(len(data) == info.st_size, 'Source changed during read')
        files[name] = data
        modes[name] = '100755' if info.st_mode & 0o111 else '100644'
    return files, modes


def read_git_snapshot(ref, index=None):
    rows = git('ls-files', '--stage', '-z', index=index) if ref == ':' else git('ls-tree', '-r', '-z', ref)
    entries = {}
    for row in rows.split(b'\0'):
        if not row:
            continue
        header, raw_name = row.split(b'\t', 1)
        name = raw_name.decode('utf-8')
        safe_name(name)
        fields = header.decode('ascii').split()
        if ref == ':':
            mode, oid, stage = fields
            require(stage == '0', 'Unmerged source index')
        else:
            mode, kind, oid = fields
            require(kind == 'blob', 'Non-blob source entry')
        require(mode in ('100644', '100755') and name not in entries, 'Nonregular or duplicate Git entry')
        entries[name] = (mode, oid)
    require(len(entries) <= MAX_FILES, 'Git snapshot exceeds file cap')
    oids = sorted({oid for _, oid in entries.values()})
    request = ''.join(oid + '\n' for oid in oids).encode('ascii')
    headers = git('cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)', data=request).splitlines()
    require(len(headers) == len(oids), 'Incomplete Git object size response')
    sizes = {}
    for oid, header in zip(oids, headers):
        got, kind, size = header.decode('ascii').split()
        require(got == oid and kind == 'blob', 'Unexpected Git object')
        size = int(size)
        require(0 <= size <= MAX_FILE_BYTES, 'Git object exceeds file cap')
        sizes[oid] = size
    require(sum(sizes[oid] for _, oid in entries.values()) <= MAX_TOTAL_BYTES, 'Git snapshot exceeds byte cap')
    output = git('cat-file', '--batch', data=request)
    blobs, offset = {}, 0
    for oid in oids:
        end = output.index(b'\n', offset)
        require(output[offset:end] == f'{oid} blob {sizes[oid]}'.encode(), 'Unexpected Git blob header')
        start, finish = end + 1, end + 1 + sizes[oid]
        data = output[start:finish]
        require(output[finish:finish + 1] == b'\n', 'Incomplete Git blob response')
        identity = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(identity == oid, 'Git blob content does not match object identity')
        blobs[oid] = data
        offset = finish + 1
    require(offset == len(output), 'Extra Git blob output')
    return ({name: blobs[oid] for name, (_, oid) in entries.items()},
            {name: mode for name, (mode, _) in entries.items()})


@lru_cache(maxsize=1)
def baseline():
    require(git('rev-parse', BASE + '^{tree}').decode().strip() == BASE_TREE, 'Published base tree differs')
    headers = git('cat-file', 'commit', BASE).split(b'\n\n', 1)[0].splitlines()
    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] == [BASE_PARENT],
            'Published base sole parent differs')
    files, modes = read_git_snapshot(BASE)
    require(len(files) == 311 and sum(map(len, files.values())) == 3_741_330, 'Published base size differs')
    return MappingProxyType(files), MappingProxyType(modes)


def historical_adapter(original):
    doc_end = original.index(b'"""', 3) + 3
    start = original.index(b'\ndef current_snapshot():') + 1
    end = original.index(b'\n\nclass SourceClosureTests', start)
    docstring = b'"""Immutable rc3.dev3 source proof against published 2c841d84; stdlib only.\n\nThe current_snapshot adapter checks the exact published tree and sole parent.\nOriginal verifier/mutation bodies are preserved. This focused historical source\ncheck does not restore an earlier full-suite or acoustic qualification.\nThe separate rc3.dev4 gate owns current worktree/index/external-seal checks.\n"""'
    snapshot = b"def current_snapshot():\n    check_isolation()\n    revision = '2c841d84a9b3204e510628f4aef17fe90334e76c'\n    headers = git('cat-file', 'commit', revision).split(b'\\n\\n', 1)[0].splitlines()\n    require([line[5:].decode() for line in headers if line.startswith(b'tree ')] ==\n            ['bb31c31a9411ea4c0ec5e185cc9bae57ac31a1f0'], 'Historical rc3.dev3 tree differs')\n    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] ==\n            [BASE], 'Historical rc3.dev3 sole parent differs')\n    git('merge-base', '--is-ancestor', revision, 'HEAD')\n    files, modes = read_git_snapshot(revision)\n    return files, dict(files), modes, dict(modes), canonical(expected_seal(files))\n"
    return docstring + original[doc_end:start] + snapshot + original[end:]


def expected_policy(base):
    old = json.loads(base[POLICY])
    return dict(old, purpose=PURPOSE, maximum_files=MAX_FILES, maximum_total_bytes=MAX_TOTAL_BYTES,
                allowed_files=sorted(set(old['allowed_files']) | ADDED))


def expected_manifest(files, base, base_modes):
    base_inventory = [{'path': name, **pin(base[name]), 'mode': base_modes[name]} for name in sorted(base)]
    return {
        'schema': 'reviewed-backend-rc3-dev4-source-v1', 'version': VERSION,
        'base_commit': BASE, 'base_tree': BASE_TREE,
        'base_files': 311, 'base_bytes': 3_741_330,
        'base_source_inventory_sha256': hashlib.sha256(canonical(base_inventory)).hexdigest(),
        'scope': SCOPE, 'qualification': QUALIFICATION,
        'source_budget': {'maximum_files': MAX_FILES, 'maximum_total_bytes': MAX_TOTAL_BYTES,
                          'maximum_file_bytes': MAX_FILE_BYTES},
        'historical_proof': 'Immutable published rc3.dev3 current_snapshot/docstring adapter only; original verifier and mutation bodies preserved',
        'external_seal': 'Manifest excludes itself; independent external seal pins both this manifest and the source gate',
        'files': [{'path': name, **pin(files[name]), 'mode': '100644',
                   **({'supersedes': pin(base[name])} if name in base else {'base_absent': True})}
                  for name in sorted(DELTA - {MANIFEST})],
    }


def expected_seal(files):
    return {'schema': 'rc3-dev4-external-source-seal-v1', 'base_commit': BASE, 'base_tree': BASE_TREE,
            'gate': {'path': GATE, **pin(files[GATE])},
            'manifest': {'path': MANIFEST, **pin(files[MANIFEST])}}


@lru_cache(maxsize=1)
def privacy_guard():
    base, _ = baseline()
    require((ROOT / GUARD).read_bytes() == base[GUARD], 'Privacy guard differs from published source')
    spec = importlib.util.spec_from_file_location('_rc3_dev4_unchanged_privacy_guard', ROOT / GUARD)
    require(spec is not None and spec.loader is not None, 'Cannot load unchanged privacy guard')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.MAX_BYTES == MAX_FILE_BYTES, 'Privacy per-file cap differs')
    return module


def verify(files, index, modes, index_modes, seal, base=None, base_modes=None):
    if base is None or base_modes is None:
        base, base_modes = baseline()
    require(files == index, 'Working source and explicit candidate index disagree')
    require(set(files) == set(base) | ADDED and len(files) == MAX_FILES, 'Source inventory differs')
    require({name for name in files if files[name] != base.get(name)} == DELTA, 'Source delta differs')
    require(all(files[name] == base[name] for name in set(base) - CHANGED), 'Unapproved base source changed')
    require(modes == index_modes == {**base_modes, **{name: '100644' for name in ADDED}}, 'Source modes differ')
    require(files[HISTORICAL] == historical_adapter(base[HISTORICAL]), 'Historical proof adapter differs')
    for name in ('pyproject.toml', 'audio_backend/__init__.py'):
        require(base[name].count(b'0.2.0rc3.dev3') == 1, 'Base version target not unique')
        require(files[name] == base[name].replace(b'0.2.0rc3.dev3', VERSION.encode()), 'Version-only replacement differs')
    policy = expected_policy(base)
    require(files[POLICY] == canonical(policy), 'Policy differs from exact budget/allowlist-only update')
    require(files[MANIFEST] == canonical(expected_manifest(files, base, base_modes)), 'Manifest is noncanonical or stale')
    require(seal == canonical(expected_seal(files)), 'External gate/manifest seal differs')
    require(all(len(data) <= MAX_FILE_BYTES for data in files.values()) and
            sum(map(len, files.values())) <= MAX_TOTAL_BYTES, 'Source byte budget exceeded')
    require(privacy_guard().validate(files, policy, require_manifest=False)['findings'] == 0, 'Privacy findings')


def external_file(variable):
    value = os.environ.get(variable)
    require(bool(value), 'Missing explicit external ' + variable)
    path = Path(value)
    require(path.is_absolute(), 'External proof paths must be absolute')
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'External proof path contains a symlink')
    path = path.resolve(strict=True)
    require(not path.is_relative_to(ROOT), 'External proof must be outside source checkout')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'External proof must be a regular unlinked file')
    require(info.st_size <= 100_000, 'External proof exceeds cap')
    return path


def check_isolation():
    require(not any(os.environ.get(key) for key in (
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
        'GIT_CONFIG', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS')), 'Ambient Git override prohibited')
    common = Path(git('rev-parse', '--git-common-dir').decode().strip())
    common = (ROOT / common).resolve() if not common.is_absolute() else common.resolve()
    require((ROOT / '.git').is_dir() and not (ROOT / '.git').is_symlink() and common == ROOT / '.git',
            'Checkout is not a self-contained ordinary clone')
    require(not (common / 'objects/info/alternates').exists() and not (common / 'info/grafts').exists(),
            'Git alternates or grafts prohibited')
    require(not git('for-each-ref', 'refs/replace').strip(), 'Git replacement refs prohibited')
    require(git('rev-parse', '--is-shallow-repository').strip() == b'false', 'Shallow source history prohibited')
    for path in (common / 'objects').rglob('*'):
        require(not path.is_symlink(), 'Git object symlink prohibited')
        if path.is_file():
            require(path.stat().st_nlink == 1, 'Git object hardlink prohibited')
    require(not (common / 'MERGE_HEAD').exists(), 'Merge in progress')
    git('merge-base', '--is-ancestor', BASE, 'HEAD')


def current_snapshot():
    check_isolation()
    revision = '762509bddf4c3f1604c446d485034be8882de6f7'
    headers = git('cat-file', 'commit', revision).split(b'\n\n', 1)[0].splitlines()
    require([line[5:].decode() for line in headers if line.startswith(b'tree ')] ==
            ['8ce9f6fc4a70cb018175c742e9ead04c136c4148'], 'Historical rc3.dev4 tree differs')
    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] ==
            [BASE], 'Historical rc3.dev4 sole parent differs')
    git('merge-base', '--is-ancestor', revision, 'HEAD')
    files, modes = read_git_snapshot(revision)
    return files, dict(files), modes, dict(modes), canonical(expected_seal(files))


class SourceClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = current_snapshot()
        cls.base, cls.base_modes = baseline()
        verify(*cls.current, cls.base, cls.base_modes)

    def test_exact_fresh_source(self):
        verify(*self.current, self.base, self.base_modes)

    def test_historical_adapter_preserves_verifier_and_mutation_bodies(self):
        original, adapted = self.base[HISTORICAL], self.current[0][HISTORICAL]
        self.assertEqual(adapted, historical_adapter(original))
        original_doc_end = original.index(b'"""', 3) + 3
        adapted_doc_end = adapted.index(b'"""', 3) + 3
        self.assertEqual(original[original_doc_end:original.index(b'def current_snapshot():')],
                         adapted[adapted_doc_end:adapted.index(b'def current_snapshot():')])
        anchor = b'\n\nclass SourceClosureTests'
        self.assertEqual(original[original.index(anchor):], adapted[adapted.index(anchor):])

    def test_privacy_exclusions_and_limit_are_unchanged(self):
        old, new = json.loads(self.base[POLICY]), json.loads(self.current[0][POLICY])
        self.assertEqual(old['excluded_categories'], new['excluded_categories'])
        self.assertEqual(self.base[GUARD], self.current[0][GUARD])
        self.assertEqual(privacy_guard().MAX_BYTES, MAX_FILE_BYTES)

    def reject_mutation(self, mutation):
        files, index, modes, index_modes = map(dict, self.current[:4])
        seal = self.current[4]
        regenerate = mutation.startswith(('version_', 'policy_', 'historical_', 'privacy_'))
        if mutation == 'missing':
            del files['README.md']
        elif mutation == 'extra':
            files['docs/unreviewed.md'] = b'synthetic\n'
        elif mutation in ('module', 'contract_test', 'contract_docs', 'gate', 'runner', 'cli', 'runtime', 'history', 'guard'):
            name = {'module': MODULE, 'contract_test': 'tests/test_moss_handy_controller.py',
                    'contract_docs': 'docs/MOSS_HANDY_EXPERIMENTAL.md', 'gate': GATE,
                    'runner': 'audio_backend/moss_handy_runner.py',
                    'cli': 'audio_backend/__main__.py',
                    'runtime': 'audio_backend/worker.py', 'history': 'docs/PUBLIC_HISTORY_MAP.json', 'guard': GUARD}[mutation]
            files[name] += b'\n'
        elif mutation.startswith('version_'):
            name = 'pyproject.toml' if mutation == 'version_project' else 'audio_backend/__init__.py'
            files[name] = files[name].replace(VERSION.encode(), b'0.2.0rc3.dev5')
        elif mutation.startswith('policy_'):
            value = json.loads(files[POLICY])
            kind = mutation.removeprefix('policy_')
            if kind == 'count': value['maximum_files'] += 1
            elif kind == 'total': value['maximum_total_bytes'] += 1
            elif kind == 'privacy': value['excluded_categories'].pop()
            elif kind == 'allowlist': value['allowed_files'].append('docs/unreviewed.md')
            elif kind == 'purpose': value['purpose'] += ' altered'
            files[POLICY] = canonical(value)
        elif mutation.startswith('historical_'):
            if mutation == 'historical_verifier':
                files[HISTORICAL] = files[HISTORICAL].replace(b'require(files == index', b'require(files == files')
            else:
                files[HISTORICAL] = files[HISTORICAL].replace(BASE.encode(), b'0' * 40)
        elif mutation.startswith('privacy_'):
            if mutation == 'privacy_secret':
                files[MODULE] += b'\n# ' + b'sk-' + b'synthetic' * 5 + b'\n'
            elif mutation == 'privacy_path':
                files[MODULE] += b'\n# ' + b'/' + b'workspace/' + b'synthetic\n'
            elif mutation == 'privacy_binary':
                files[MODULE] += b'\xff'
            else:
                files[MODULE] += b'\0'
        elif mutation.startswith('manifest_'):
            value = json.loads(files[MANIFEST])
            kind = mutation.removeprefix('manifest_')
            if kind == 'schema': value['schema'] = 'unreviewed'
            elif kind == 'base': value['base_commit'] = '0' * 40
            elif kind == 'tree': value['base_tree'] = '0' * 40
            elif kind == 'version': value['version'] = '0.2.0rc3.dev5'
            elif kind == 'scope': value['scope'] += ' altered'
            elif kind == 'duplicate': value['files'].append(value['files'][0])
            elif kind == 'self': value['files'].append({'path': MANIFEST, **pin(files[MANIFEST])})
            elif kind == 'order': value['files'].reverse()
            elif kind == 'pin': value['files'][0]['sha256'] = '0' * 64
            files[MANIFEST] = canonical(value) + (b'\n' if kind == 'format' else b'')
            seal = canonical(expected_seal(files))
        elif mutation == 'mode':
            modes[MODULE] = '100755'
            index_modes = dict(modes)
        elif mutation == 'index_mode': index_modes[MODULE] = '100755'
        elif mutation == 'index': index['README.md'] += b'\n'
        elif mutation == 'seal': seal += b'\n'
        elif mutation == 'overlarge': files[MODULE] += b'x' * MAX_FILE_BYTES
        else: self.fail('Unknown mutation')
        if regenerate:
            files[MANIFEST] = canonical(expected_manifest(files, self.base, self.base_modes))
            seal = canonical(expected_seal(files))
        if mutation != 'index': index = dict(files)
        with self.assertRaises(ValueError, msg=mutation):
            verify(files, index, modes, index_modes, seal, self.base, self.base_modes)


def _mutation_case(name):
    def test(self):
        self.reject_mutation(name)
    test.__name__ = 'test_rejects_' + name
    return test


for _name in ('missing', 'extra', 'module', 'contract_test', 'contract_docs', 'gate', 'runner', 'cli',
              'runtime', 'history', 'guard', 'version_project', 'version_module',
              'policy_count', 'policy_total', 'policy_privacy', 'policy_allowlist', 'policy_purpose',
              'historical_verifier', 'historical_fixture', 'privacy_secret', 'privacy_path',
              'privacy_binary', 'privacy_nul', 'manifest_schema', 'manifest_base', 'manifest_tree',
              'manifest_version', 'manifest_scope', 'manifest_duplicate', 'manifest_self',
              'manifest_order', 'manifest_pin', 'manifest_format', 'mode', 'index_mode', 'index', 'seal', 'overlarge'):
    setattr(SourceClosureTests, 'test_rejects_' + _name, _mutation_case(_name))


@contextmanager
def retained_fixture(*, prefix):
    """Keep successful, failed and interrupted fixture artifacts for the guard."""
    parent = Path(os.environ['GUARD_RUN_DIRECTORY']) / 'tmp' if os.environ.get('GUARD_RUN_DIRECTORY') else Path(tempfile.mkdtemp(prefix=prefix))
    directory = parent / (prefix + uuid.uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    yield str(directory)


class SourceDiscoveryTests(unittest.TestCase):
    def test_git_discovery_includes_tracked_and_unignored_paths(self):
        with retained_fixture(prefix='rc3-dev4-source-fixture-') as directory:
            root = Path(directory)
            git('init', '--quiet', '--template=', root=root)
            (root / '.gitignore').write_bytes(b'ignored\n')
            (root / 'tracked').write_bytes(b'synthetic')
            (root / 'unignored').write_bytes(b'synthetic')
            (root / 'ignored').write_bytes(b'synthetic')
            git('add', '--', '.gitignore', 'tracked', root=root)
            rows = git('ls-files', '--cached', '--others', '--exclude-standard', '-z', root=root)
            self.assertEqual(set(rows.split(b'\0')) - {b''}, {b'.gitignore', b'tracked', b'unignored'})

    def test_rejects_symlink_file(self):
        with retained_fixture(prefix='rc3-dev4-source-fixture-') as directory:
            root = Path(directory)
            (root / 'regular').write_bytes(b'synthetic')
            (root / 'link').write_bytes(b'synthetic')
            with patch.object(Path, 'is_symlink', lambda path: path.name == 'link'), self.assertRaises(ValueError):
                read_paths(root, ['link'])

    def test_rejects_symlink_directory(self):
        with retained_fixture(prefix='rc3-dev4-source-fixture-') as directory:
            root = Path(directory)
            (root / 'real').mkdir()
            (root / 'real' / 'source').write_bytes(b'synthetic')
            (root / 'link').mkdir()
            with patch.object(Path, 'is_symlink', lambda path: path.name == 'link'), self.assertRaises(ValueError):
                read_paths(root, ['link/source'])

    def test_rejects_hardlink_file(self):
        with retained_fixture(prefix='rc3-dev4-source-fixture-') as directory:
            root = Path(directory)
            (root / 'regular').write_bytes(b'synthetic')
            info = (root / 'regular').lstat()
            fake = SimpleNamespace(st_mode=info.st_mode, st_nlink=2, st_size=info.st_size)
            with patch.object(Path, 'lstat', return_value=fake), self.assertRaises(ValueError):
                read_paths(root, ['regular'])

    def test_rejects_missing_file(self):
        with retained_fixture(prefix='rc3-dev4-source-fixture-') as directory:
            with self.assertRaises(FileNotFoundError): read_paths(Path(directory), ['missing'])

    def test_rejects_directory_file(self):
        with retained_fixture(prefix='rc3-dev4-source-fixture-') as directory:
            root = Path(directory)
            (root / 'directory').mkdir()
            with self.assertRaises(ValueError): read_paths(root, ['directory'])

    def test_rejects_duplicate_path(self):
        with self.assertRaises(ValueError): read_paths(ROOT, ['README.md', 'README.md'])

    def test_rejects_unsafe_path(self):
        for path in ('../source', '/source', './source', 'a//source', '.git/config', 'a\\source'):
            with self.subTest(path=path), self.assertRaises(ValueError): safe_name(path)

    def test_rejects_oversize_before_read(self):
        with retained_fixture(prefix='rc3-dev4-source-fixture-') as directory:
            root = Path(directory)
            (root / 'large').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
            with self.assertRaises(ValueError): read_paths(root, ['large'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
