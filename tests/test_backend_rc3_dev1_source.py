"""Immutable rc3.dev1 source proof against published e6824ea4; stdlib only.

The current_snapshot adapter reads that exact Git commit, verifies its tree and
sole parent, and derives the historical seal solely from those immutable bytes.
Original verifier and mutation bodies are preserved. This is a focused source
proof, not an execution of the old full suite or renewed acoustic qualification.
The separate rc3.dev2 gate owns current worktree/index/external-seal checks.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
from types import MappingProxyType
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = 'd72ccfc3b606931c8d0ec689cdb880effbf8f7eb'
BASE_TREE = 'cb24084ac612dca93cea348743afe8e17c5c353d'
VERSION = '0.2.0rc3.dev1'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
MANIFEST = 'docs/BACKEND_RC3_DEV1_SOURCE.json'
GATE = 'tests/test_backend_rc3_dev1_source.py'
HISTORICAL = 'tests/test_backend_dev13_source.py'
GUARD = 'scripts/source_staging_guard.py'
MODULE = 'audio_backend/native_lexical_alternatives.py'
ADDED = frozenset((MODULE, 'tests/test_native_lexical_alternatives.py',
                   'docs/NATIVE_LEXICAL_ALTERNATIVES.md', GATE, MANIFEST))
CHANGED = frozenset(('pyproject.toml', 'audio_backend/__init__.py', POLICY, HISTORICAL))
DELTA = ADDED | CHANGED
MAX_FILES, MAX_TOTAL_BYTES, MAX_FILE_BYTES = 298, 3_500_000, 250_000
PURPOSE = ('Fresh rc3.dev1 pure untimed native lexical alternatives contract; '
           'focused stdlib-only checks; no production integration or restored qualification')
SCOPE = ('Fresh pure untimed whole-support Qwen/MOSS alternatives contract; '
         'no production import, provider, API, Worker, storage or model changes')
QUALIFICATION = 'Focused stdlib-only source/contract checks; no historical-suite or acoustic qualification'


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
    files, modes = read_git_snapshot(BASE)
    require(len(files) == 293 and sum(map(len, files.values())) == 3_412_765, 'Published base size differs')
    return MappingProxyType(files), MappingProxyType(modes)


def historical_adapter(original):
    start = original.index(b"@pytest.fixture(scope='module')\ndef current():")
    end = original.index(b'\n\ndef test_dev13_exact_source(current):', start)
    fixture_before = original[start:end]
    fixture_after = b"""@pytest.fixture(scope='module')
def current():
    # Preserve published dev13 proof; rc3.dev1 has a separate current-source gate.
    revision = 'd72ccfc3b606931c8d0ec689cdb880effbf8f7eb'
    files = guard.read_git_tree(revision)
    modes = tree_modes(revision)
    return files, dict(files), modes, dict(modes)
"""
    head_before = b"    head = git('rev-parse', 'HEAD').decode().strip()"
    head_after = (b"    head = 'd72ccfc3b606931c8d0ec689cdb880effbf8f7eb'\n"
                  b"    git('merge-base', '--is-ancestor', head, 'HEAD')")
    replacements = ((fixture_before, fixture_after), (head_before, head_after))
    candidate = original
    for before, after in replacements:
        require(candidate.count(before) == 1 and after not in candidate, 'Historical adapter target not unique')
        candidate = candidate.replace(before, after)
    restored = candidate
    for before, after in reversed(replacements):
        require(restored.count(after) == 1, 'Historical adapter reversal not unique')
        restored = restored.replace(after, before)
    require(restored == original, 'Historical proof bytes not preserved')
    return candidate


def expected_policy(base):
    old = json.loads(base[POLICY])
    return dict(old, purpose=PURPOSE, maximum_files=MAX_FILES, maximum_total_bytes=MAX_TOTAL_BYTES,
                allowed_files=sorted(set(old['allowed_files']) | ADDED))


def expected_manifest(files, base, base_modes):
    base_inventory = [{'path': name, **pin(base[name]), 'mode': base_modes[name]} for name in sorted(base)]
    return {
        'schema': 'reviewed-backend-rc3-dev1-source-v1', 'version': VERSION,
        'base_commit': BASE, 'base_tree': BASE_TREE,
        'base_files': 293, 'base_bytes': 3_412_765,
        'base_source_inventory_sha256': hashlib.sha256(canonical(base_inventory)).hexdigest(),
        'scope': SCOPE, 'qualification': QUALIFICATION,
        'source_budget': {'maximum_files': MAX_FILES, 'maximum_total_bytes': MAX_TOTAL_BYTES,
                          'maximum_file_bytes': MAX_FILE_BYTES},
        'historical_proof': 'Immutable published dev13 fixture and lineage adapter only; original proof bodies preserved',
        'external_seal': 'Manifest excludes itself; independent external seal pins both this manifest and the source gate',
        'files': [{'path': name, **pin(files[name]), 'mode': '100644',
                   **({'supersedes': pin(base[name])} if name in base else {'base_absent': True})}
                  for name in sorted(DELTA - {MANIFEST})],
    }


def expected_seal(files):
    return {'schema': 'rc3-dev1-external-source-seal-v1', 'base_commit': BASE, 'base_tree': BASE_TREE,
            'gate': {'path': GATE, **pin(files[GATE])},
            'manifest': {'path': MANIFEST, **pin(files[MANIFEST])}}


@lru_cache(maxsize=1)
def privacy_guard():
    base, _ = baseline()
    require((ROOT / GUARD).read_bytes() == base[GUARD], 'Privacy guard differs from published source')
    spec = importlib.util.spec_from_file_location('_rc3_dev1_unchanged_privacy_guard', ROOT / GUARD)
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
        require(base[name].count(b'0.2.0rc2.dev13') == 1, 'Base version target not unique')
        require(files[name] == base[name].replace(b'0.2.0rc2.dev13', VERSION.encode()), 'Version-only replacement differs')
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
    revision = 'e6824ea44f591b22be767a3a1794e8fb2b9d04b1'
    headers = git('cat-file', 'commit', revision).split(b'\n\n', 1)[0].splitlines()
    require([line[5:].decode() for line in headers if line.startswith(b'tree ')] ==
            ['6441a3d1c5242b20122f66a593f15cf0263e1a68'], 'Historical rc3.dev1 tree differs')
    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] ==
            [BASE], 'Historical rc3.dev1 sole parent differs')
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

    def test_historical_adapter_is_exact_and_reversible(self):
        self.assertEqual(self.current[0][HISTORICAL], historical_adapter(self.base[HISTORICAL]))
        original = self.base[HISTORICAL]
        adapted = self.current[0][HISTORICAL]
        for start, end in ((b'REVIEWED = ', b'\n\n\ndef expected_manifest'),
                           (b'def expected_manifest', b"@pytest.fixture(scope='module')"),
                           (b"@pytest.mark.parametrize('mutation'", None)):
            before = original[original.index(start):original.index(end) if end else None]
            after = adapted[adapted.index(start):adapted.index(end) if end else None]
            self.assertEqual(before, after)

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
        elif mutation in ('module', 'contract_test', 'contract_docs', 'gate', 'runtime', 'history', 'guard'):
            name = {'module': MODULE, 'contract_test': 'tests/test_native_lexical_alternatives.py',
                    'contract_docs': 'docs/NATIVE_LEXICAL_ALTERNATIVES.md', 'gate': GATE,
                    'runtime': 'audio_backend/worker.py', 'history': 'docs/PUBLIC_HISTORY_MAP.json', 'guard': GUARD}[mutation]
            files[name] += b'\n'
        elif mutation.startswith('version_'):
            name = 'pyproject.toml' if mutation == 'version_project' else 'audio_backend/__init__.py'
            files[name] = files[name].replace(VERSION.encode(), b'0.2.0rc3.dev2')
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
                files[HISTORICAL] = files[HISTORICAL].replace(b'assert files == index', b'assert files == files')
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
            elif kind == 'version': value['version'] = '0.2.0rc3.dev2'
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


for _name in ('missing', 'extra', 'module', 'contract_test', 'contract_docs', 'gate',
              'runtime', 'history', 'guard', 'version_project', 'version_module',
              'policy_count', 'policy_total', 'policy_privacy', 'policy_allowlist', 'policy_purpose',
              'historical_verifier', 'historical_fixture', 'privacy_secret', 'privacy_path',
              'privacy_binary', 'privacy_nul', 'manifest_schema', 'manifest_base', 'manifest_tree',
              'manifest_version', 'manifest_scope', 'manifest_duplicate', 'manifest_self',
              'manifest_order', 'manifest_pin', 'manifest_format', 'mode', 'index_mode', 'index', 'seal', 'overlarge'):
    setattr(SourceClosureTests, 'test_rejects_' + _name, _mutation_case(_name))


class SourceDiscoveryTests(unittest.TestCase):
    def test_git_discovery_includes_tracked_and_unignored_paths(self):
        with tempfile.TemporaryDirectory(prefix='rc3-source-fixture-') as directory:
            root = Path(directory)
            git('init', '--quiet', root=root)
            (root / '.gitignore').write_bytes(b'ignored\n')
            (root / 'tracked').write_bytes(b'synthetic')
            (root / 'unignored').write_bytes(b'synthetic')
            (root / 'ignored').write_bytes(b'synthetic')
            git('add', '--', '.gitignore', 'tracked', root=root)
            rows = git('ls-files', '--cached', '--others', '--exclude-standard', '-z', root=root)
            self.assertEqual(set(rows.split(b'\0')) - {b''}, {b'.gitignore', b'tracked', b'unignored'})

    def test_rejects_symlink_file(self):
        with tempfile.TemporaryDirectory(prefix='rc3-source-fixture-') as directory:
            root = Path(directory)
            (root / 'regular').write_bytes(b'synthetic')
            (root / 'link').symlink_to('regular')
            with self.assertRaises(ValueError): read_paths(root, ['link'])

    def test_rejects_symlink_directory(self):
        with tempfile.TemporaryDirectory(prefix='rc3-source-fixture-') as directory:
            root = Path(directory)
            (root / 'real').mkdir()
            (root / 'real' / 'source').write_bytes(b'synthetic')
            (root / 'link').symlink_to('real', target_is_directory=True)
            with self.assertRaises(ValueError): read_paths(root, ['link/source'])

    def test_rejects_hardlink_file(self):
        with tempfile.TemporaryDirectory(prefix='rc3-source-fixture-') as directory:
            root = Path(directory)
            (root / 'regular').write_bytes(b'synthetic')
            os.link(root / 'regular', root / 'linked')
            with self.assertRaises(ValueError): read_paths(root, ['linked'])

    def test_rejects_missing_file(self):
        with tempfile.TemporaryDirectory(prefix='rc3-source-fixture-') as directory:
            with self.assertRaises(FileNotFoundError): read_paths(Path(directory), ['missing'])

    def test_rejects_directory_file(self):
        with tempfile.TemporaryDirectory(prefix='rc3-source-fixture-') as directory:
            root = Path(directory)
            (root / 'directory').mkdir()
            with self.assertRaises(ValueError): read_paths(root, ['directory'])

    def test_rejects_duplicate_path(self):
        with self.assertRaises(ValueError): read_paths(ROOT, ['README.md', 'README.md'])

    def test_rejects_unsafe_path(self):
        for path in ('../source', '/source', './source', 'a//source', '.git/config', 'a\\source'):
            with self.subTest(path=path), self.assertRaises(ValueError): safe_name(path)

    def test_rejects_oversize_before_read(self):
        with tempfile.TemporaryDirectory(prefix='rc3-source-fixture-') as directory:
            root = Path(directory)
            (root / 'large').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
            with self.assertRaises(ValueError): read_paths(root, ['large'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
