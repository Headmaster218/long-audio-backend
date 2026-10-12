"""Fresh rc3.dev6 source closure, stdlib only and separately externally frozen.

RC3_DEV6_INDEX is an external Git candidate index; RC3_DEV6_SOURCE_SEAL is
an independent external gate/manifest seal. No production module is imported.
"""
from __future__ import annotations
from functools import lru_cache
from contextlib import contextmanager
import ast
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
BASE = 'e6791e6d1b78a14c113529d809b8486349be1146'
BASE_PARENT = '762509bddf4c3f1604c446d485034be8882de6f7'
BASE_TREE = '0aa2ac344d4513d9e9cfd68897957303f92e550d'
BASE_INDEX_SHA256 = '1c65871ed492120dc0931b0355ead419d8821c8352cc704623aeaa8f17b36d63'
VERSION = '0.2.0rc3.dev6'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
MANIFEST = 'docs/BACKEND_RC3_DEV6_SOURCE.json'
GATE = 'tests/test_backend_rc3_dev6_source.py'
HISTORICAL = 'tests/test_backend_rc3_dev5_source.py'
GUARD = 'scripts/source_staging_guard.py'
MAX_FILES, MAX_TOTAL_BYTES, MAX_FILE_BYTES = 340, 4_600_000, 250_000
ADDED = frozenset(('audio_backend/native_owned_cells.py', 'audio_backend/native_owned_cell_store.py',
    'audio_backend/native_owned_cell_capture.py', 'docs/NATIVE_OWNED_CELLS.md',
    'docs/BACKEND_RC3_DEV6_SOURCE.json', 'tests/test_native_owned_cells.py',
    'tests/test_native_owned_cell_store.py', 'tests/test_native_owned_cell_capture.py',
    'tests/test_backend_rc3_dev6_source.py'))
CHANGED = frozenset(('audio_backend/__init__.py', 'audio_backend/__main__.py',
    'audio_backend/settings.py', 'audio_backend/worker.py', 'audio_backend/app.py',
    'audio_backend/lifecycle.py', 'docs/FRAGMENT_STAGING_POLICY.json', 'pyproject.toml',
    'tests/test_backend_rc3_dev5_source.py'))
DELTA = ADDED | CHANGED
PURPOSE = 'Fresh rc3.dev6 default-OFF passive selected native-owned cells; UNQUALIFIED source checkpoint, production SQLite/VFS registry empty; no new model dispatch or restored qualification'
DESIGN_PINS = {'inventory': 'bb02ca78d54b262df4b1542cfd907235bace0efb319162a168e0b57deff49312',
              'design': '649cf6769b579ab1de5fcaf10d610c5827d19d7e22975a9068d89f08228e0ccb',
              'tests': '2183e695461a261416e4df9a23bc757842d5da2eebfbbfb0ea0bd0033677fe2e'}
BOUNDS = {'cell_frames': 320000, 'cell_pcm_bytes': 640000, 'core_frames': 960000,
    'ring_frames': 1536000, 'ring_pcm_bytes': 3072000, 'proof_dependency_bytes': 65536,
    'source_parts': 128, 'cells': 1024, 'source_receipts': 4096, 'receipt_bytes': 1024,
    'handoffs_active_or_pending': 1, 'handoff_cells': 3, 'handoff_material_bytes': 2359296,
    'pins': 2, 'pin_bytes': 786432, 'page_rows': 20, 'page_bytes': 65536,
    'total_bytes': 67108864, 'database_bytes': 25165824, 'journal_bytes': 33554432,
    'ancillary_bytes': 2097152, 'envelope_bytes': 60817408, 'page_size': 4096,
    'max_page_count': 6144, 'operation_seconds': 0.5, 'lock_seconds': 0.005,
    'sql_steps': 150000, 'sql_projection_bytes': 262144, 'source_read_bytes': 41943040,
    'transfer_bytes': 65536, 'working_reservation_bytes': 67108864, 'working_memory_measured': False}
# Filled by source-only AST parsing before freeze, never by importing product.
CONTRACT_PINS = {'audio_backend/__main__.py': '7b7068e9d0c9fa8c382d177ec90e8f1e40832c767132ceb9fda5583283d52e6d', 'audio_backend/app.py': '1ba79ba1a7d113bd24b751d909ca73b30d20c8fd4e3ff739440e428a507e706f', 'audio_backend/lifecycle.py': '101e84c55a07e7a5a2eadbac6ce18b13ed691965bc8e34f94ad9074394810669', 'audio_backend/native_owned_cell_capture.py': 'b11ed2b58f9cb6b141f74b27464525485ec4331a89f197ae0aa8bd8cfa292225', 'audio_backend/native_owned_cell_store.py': '65fb6ec93f4827317743fea7a9d44beda6a2d395deafd89fc02581fbb28897b0', 'audio_backend/native_owned_cells.py': '00ad8cd30ab8e3aeedeffd0e5f5f6e462bb3663ac9e70db4c3ad4c06c20a6e8d', 'audio_backend/settings.py': 'd349281961ae83aee1f350a73cfbf73ab2a49d81a9d8aa2ce91b3e5608ea198b', 'audio_backend/worker.py': '7e9792ba1fc3be13344955569c56c324fe7a0aa0b474e84d24f0b539d03b2c1f', 'tests/test_native_owned_cell_capture.py': '0b054fb5442eb605d270bbaa0e699c2df26fb54368774a68df8f66d88e62ff9d', 'tests/test_native_owned_cell_store.py': '64916185f55fc863a944c7801a0931dfb068b89dcb7fde1927772880711c9546', 'tests/test_native_owned_cells.py': '2bc17d77dadac905c13d17f03af0596189ba36148c9eee801ccb30900b198190'}


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

@contextmanager
def retained_fixture(*, prefix):
    """Keep successful, failed and interrupted fixture artifacts for the guard."""
    parent = Path(os.environ['GUARD_RUN_DIRECTORY']) / 'tmp' if os.environ.get('GUARD_RUN_DIRECTORY') else Path(tempfile.mkdtemp(prefix=prefix))
    directory = parent / (prefix + uuid.uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    yield str(directory)


@lru_cache(maxsize=1)
def baseline():
    require(git('rev-parse', BASE + '^{tree}').decode().strip() == BASE_TREE, 'Published dev5 tree differs')
    headers = git('cat-file', 'commit', BASE).split(b'\n\n', 1)[0].splitlines()
    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] == [BASE_PARENT],
            'Published dev5 sole parent differs')
    files, modes = read_git_snapshot(BASE)
    require(len(files) == 331 and sum(map(len, files.values())) == 4_098_577, 'Published dev5 inventory differs')
    return MappingProxyType(files), MappingProxyType(modes)


def historical_adapter(original):
    doc_end = original.index(b'"""', 3) + 3
    start = original.index(b'\ndef current_snapshot():') + 1
    end = original.index(b'\n\nclass SourceClosureTests', start)
    docstring = b'"""Immutable rc3.dev5 source proof against published e6791e6d; stdlib only.\n\nThe current_snapshot adapter checks the exact published tree and sole parent.\nOriginal verifier/mutation bodies are preserved. This focused historical source\ncheck does not restore an earlier full-suite or acoustic qualification.\nThe separate rc3.dev6 gate owns current worktree/index/external-seal checks.\n"""'
    snapshot = b"def current_snapshot():\n    check_isolation()\n    revision = 'e6791e6d1b78a14c113529d809b8486349be1146'\n    headers = git('cat-file', 'commit', revision).split(b'\\n\\n', 1)[0].splitlines()\n    require([line[5:].decode() for line in headers if line.startswith(b'tree ')] ==\n            ['0aa2ac344d4513d9e9cfd68897957303f92e550d'], 'Historical rc3.dev5 tree differs')\n    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] ==\n            [BASE], 'Historical rc3.dev5 sole parent differs')\n    git('merge-base', '--is-ancestor', revision, 'HEAD')\n    files, modes = read_git_snapshot(revision)\n    return files, dict(files), modes, dict(modes), canonical(expected_seal(files))\n"
    return docstring + original[doc_end:start] + snapshot + original[end:]


def shape(node):return ast.dump(node, annotate_fields=True, include_attributes=False)


def definition(data, *names):
    node = ast.parse(data)
    for name in names:
        found = [child for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == name]
        require(len(found) == 1, 'Missing or duplicate source definition')
        node = found[0]
    return node


def expected_policy(base):
    old = json.loads(base[POLICY])
    return dict(old, purpose=PURPOSE, maximum_files=MAX_FILES, maximum_total_bytes=MAX_TOTAL_BYTES,
                allowed_files=sorted(set(old['allowed_files']) | ADDED))


def expected_manifest(files, base, base_modes):
    base_inventory = [{'path': name, **pin(base[name]), 'mode': base_modes[name]} for name in sorted(base)]
    return {'schema': 'reviewed-backend-rc3-dev6-source-v1', 'version': VERSION,
        'base_commit': BASE, 'base_tree': BASE_TREE, 'base_sole_parent': BASE_PARENT,
        'base_files': 331, 'base_bytes': 4098577, 'base_external_index_sha256': BASE_INDEX_SHA256,
        'base_source_inventory_sha256': hashlib.sha256(canonical(base_inventory)).hexdigest(),
        'design_package': DESIGN_PINS, 'composed_inventory': {'added': sorted(ADDED), 'changed': sorted(CHANGED), 'total': 18},
        'scope': 'Default-OFF passive native cell witness, immutable fresh-upload receipts, optional dedicated store, publication and retention only',
        'qualification': 'UNQUALIFIED source checkpoint; production runtime registry empty; simulated fixture profiles are not enabled SQLite/VFS/allocation proof; no model, acoustic, service RSS, long-recording or full-suite qualification',
        'runtime_bounds': BOUNDS, 'source_budget': {'maximum_files': MAX_FILES, 'maximum_total_bytes': MAX_TOTAL_BYTES, 'maximum_file_bytes': MAX_FILE_BYTES},
        'preserved': 'Existing primary algorithms/windows/native epochs, SQL, original files/chunk rows, support and MOSS APIs/managers/controllers/providers/permits',
        'excluded': 'No public cell routes, jobs adapter, regeneration execution, source retirement, scheduler, provider construction or inference',
        'historical_proof': 'Published dev5 current_snapshot/docstring adapter only; all original verifier and mutation bodies preserved',
        'external_seal': 'Manifest excludes itself; independent external seal pins the manifest and source gate',
        'files': [{'path': name, **pin(files[name]), 'mode': '100644',
                  **({'supersedes': pin(base[name])} if name in base else {'base_absent': True})}
                  for name in sorted(DELTA - {MANIFEST})]}


def expected_seal(files):
    return {'schema': 'rc3-dev6-external-source-seal-v1', 'base_commit': BASE, 'base_tree': BASE_TREE,
            'gate': {'path': GATE, **pin(files[GATE])}, 'manifest': {'path': MANIFEST, **pin(files[MANIFEST])}}


@lru_cache(maxsize=1)
def privacy_guard():
    base, _ = baseline()
    require((ROOT / GUARD).read_bytes() == base[GUARD], 'Immutable privacy scanner changed')
    spec = importlib.util.spec_from_file_location('_rc3_dev6_privacy_guard', ROOT / GUARD)
    require(spec is not None and spec.loader is not None, 'Cannot load scanner')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    require(module.MAX_BYTES == MAX_FILE_BYTES, 'Privacy per-file ceiling changed')
    return module


def verify_contracts(files, base):
    require(len(ADDED) == len(CHANGED) == 9 and len(DELTA) == 18, 'Exact 18-path inventory changed')
    for path, expected in CONTRACT_PINS.items():
        require(hashlib.sha256(shape(ast.parse(files[path])).encode()).hexdigest() == expected,
                'Reviewed product/test source contract changed: ' + path)
    for name in ('validate_result', 'own_core'):
        require(shape(definition(files['audio_backend/worker.py'], name)) ==
                shape(definition(base['audio_backend/worker.py'], name)), 'Primary result algorithm changed')
    for name in ('loop', 'process', 'extract_pcm', '_begin_native_support', '_finish_native_support', '_publish_native_support', '_native_process_factory'):
        require(shape(definition(files['audio_backend/worker.py'], 'Worker', name)) ==
                shape(definition(base['audio_backend/worker.py'], 'Worker', name)), 'Primary/native-support algorithm changed')
    require(shape(definition(files['audio_backend/settings.py'], 'Settings', 'inference_config')) ==
            shape(definition(base['audio_backend/settings.py'], 'Settings', 'inference_config')), 'Cell setting leaked into inference')
    old = definition(base['audio_backend/app.py'], 'create_app'); new = definition(files['audio_backend/app.py'], 'create_app')
    def routes(node):
        return [shape(d) for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for d in n.decorator_list
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ('get', 'post', 'put', 'delete', 'patch')]
    require(routes(old) == routes(new), 'Public route inventory changed')
    old_defs = {n.name: n for n in old.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    new_defs = {n.name: n for n in new.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    require(set(old_defs) == set(new_defs), 'Unapproved application entry point')
    for name in set(old_defs) - {'lifespan', 'health', 'upload'}:
        require(shape(old_defs[name]) == shape(new_defs[name]), 'Existing route or authorization changed')
    old_delete = definition(base['audio_backend/lifecycle.py'], 'delete_session')
    new_delete = definition(files['audio_backend/lifecycle.py'], 'delete_session')
    require(shape(old_delete.body[1]) == shape(new_delete.body[1]), 'Primary erasure transaction changed')
    for path in ('audio_backend/native_owned_cells.py', 'audio_backend/native_owned_cell_capture.py', 'audio_backend/native_owned_cell_store.py'):
        tree = ast.parse(files[path])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                require(all(n.name.split('.')[0] not in {'subprocess', 'numpy', 'torch', 'requests'} for n in node.names), 'New native/model/network import')
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('execute', 'executescript') and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                sql = node.args[0].value.strip().upper()
                require(not sql.startswith(('ATTACH ', 'VACUUM', 'BACKUP', 'PRAGMA JOURNAL_MODE=WAL')), 'Unapproved SQL maintenance/migration')
    doc = files['docs/NATIVE_OWNED_CELLS.md']
    require(b'UNQUALIFIED' in doc and b'unmeasured' in doc and b'not a universal' in doc,
            'Qualification limits missing')


def verify(files, index, modes, index_modes, seal, base=None, base_modes=None):
    if base is None or base_modes is None:base, base_modes = baseline()
    require(files == index, 'Working source and candidate index differ')
    require(set(files) == set(base) | ADDED and len(files) == MAX_FILES, 'Source inventory differs')
    require({name for name in files if files[name] != base.get(name)} == DELTA, 'Source delta differs')
    require(all(files[name] == base[name] for name in set(base) - CHANGED), 'Unapproved primary/support/provider source change')
    require(modes == index_modes == {**base_modes, **{name: '100644' for name in ADDED}}, 'Source modes differ')
    require(files[HISTORICAL] == historical_adapter(base[HISTORICAL]), 'Historical dev5 verifier/mutation adapter changed')
    verify_contracts(files, base)
    for name in ('pyproject.toml', 'audio_backend/__init__.py'):
        require(files[name] == base[name].replace(b'0.2.0rc3.dev5', VERSION.encode()), 'Version-only replacement differs')
    policy = expected_policy(base)
    require(files[POLICY] == canonical(policy), 'Allowlist/budget policy differs')
    require(files[MANIFEST] == canonical(expected_manifest(files, base, base_modes)), 'Manifest is stale or noncanonical')
    require(seal == canonical(expected_seal(files)), 'External source seal differs')
    require(all(len(data) <= MAX_FILE_BYTES for data in files.values()) and sum(map(len, files.values())) <= MAX_TOTAL_BYTES,
            'Source byte ceiling exceeded')
    require(privacy_guard().validate(files, policy, require_manifest=False)['findings'] == 0, 'Privacy findings')


def current_snapshot():
    check_isolation()
    index_path = external_file('RC3_DEV6_INDEX'); seal_path = external_file('RC3_DEV6_SOURCE_SEAL')
    require(index_path != seal_path, 'External index and seal must be separate')
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z', index=index_path)
    files, modes = read_paths(ROOT, [n.decode() for n in names.split(b'\0') if n])
    index, index_modes = read_git_snapshot(':', index_path)
    head = git('rev-parse', 'HEAD').decode().strip()
    if head != BASE:
        headers = git('cat-file', 'commit', head).split(b'\n\n', 1)[0].splitlines()
        require([line[7:].decode() for line in headers if line.startswith(b'parent ')] == [BASE],
                'Candidate must have sole published dev5 parent')
        require(read_git_snapshot(head) == (files, modes), 'Committed candidate differs from source')
    return files, index, modes, index_modes, seal_path.read_bytes()


class SourceClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = current_snapshot(); cls.base, cls.base_modes = baseline()
        verify(*cls.current, cls.base, cls.base_modes)

    def test_exact_18_path_fresh_source(self):verify(*self.current, self.base, self.base_modes)

    def test_historical_all_verifier_and_mutation_bytes_unchanged(self):
        original = self.base[HISTORICAL]; adapted = self.current[0][HISTORICAL]
        self.assertEqual(adapted, historical_adapter(original))
        anchor = b'\n\nclass SourceClosureTests'
        self.assertEqual(original[original.index(anchor):], adapted[adapted.index(anchor):])
        a = original.index(b'"""', 3) + 3; b = adapted.index(b'"""', 3) + 3
        self.assertEqual(original[a:original.index(b'def current_snapshot():')], adapted[b:adapted.index(b'def current_snapshot():')])

    def test_unchanged_privacy_exclusions_and_source_guard(self):
        self.assertEqual(self.current[0][GUARD], self.base[GUARD])
        self.assertEqual(json.loads(self.current[0][POLICY])['excluded_categories'], json.loads(self.base[POLICY])['excluded_categories'])

    def reject_mutation(self, kind):
        files, index, modes, index_modes = map(dict, self.current[:4]); seal = self.current[4]
        store = 'audio_backend/native_owned_cell_store.py'; capture = 'audio_backend/native_owned_cell_capture.py'
        mutations = {
            'quota': (store, b'TOTAL_BYTES = 64 * 1024**2', b'TOTAL_BYTES = 128 * 1024**2'),
            'handoff': (store, b'if self._closed or self._slot is not None:', b'if self._closed:'),
            'cleanup': (store, b'self._epoch = object(); self._cleanup_pending = True;', b'self._epoch = self._epoch; self._cleanup_pending = True;'),
            'authority': (capture, b'if _identity(self.path) != self.identity:', b'if False:'),
            'source_receipt': (capture, b'if before != receipt.signature:', b'if False:'),
            'runtime_bypass': (store, b'if self.qualification is None:', b'if False:'),
            'allocation': (store, b'total = directory_charge;', b'total = 0;'),
            'dispatch': ('audio_backend/native_owned_cells.py', b'import math', b'import math\nimport subprocess'),
            'api': ('audio_backend/app.py', b"@app.get('/health')", b"@app.get('/cells')"),
            'historical': (HISTORICAL, b"'Source inventory differs'", b"'mutated historical test'"),
        }
        if kind in mutations:
            path, old, new = mutations[kind]; self.assertIn(old, files[path]); files[path] = files[path].replace(old, new, 1); index[path] = files[path]
        elif kind == 'extra':files['unapproved.py'] = index['unapproved.py'] = b'x=1\n'; modes['unapproved.py'] = index_modes['unapproved.py'] = '100644'
        elif kind == 'immutable':files['audio_backend/storage.py'] += b'\n# changed\n'; index['audio_backend/storage.py'] = files['audio_backend/storage.py']
        elif kind == 'index':index['README.md'] += b'\n'
        elif kind == 'mode':modes['README.md'] = '100755'
        elif kind == 'seal':seal += b'\n'
        elif kind == 'oversize':files['docs/NATIVE_OWNED_CELLS.md'] += b'x' * 250001; index.update(files)
        elif kind == 'private':files['docs/NATIVE_OWNED_CELLS.md'] += b'\n' + b'/work' + b'space/private/example\n'; index.update(files)
        elif kind == 'manifest':files[MANIFEST] += b' '; index[MANIFEST] = files[MANIFEST]
        if kind in mutations or kind in ('private', 'oversize'):
            files[MANIFEST] = canonical(expected_manifest(files, self.base, self.base_modes)); index[MANIFEST] = files[MANIFEST]
            seal = canonical(expected_seal(files))
        with self.assertRaises((ValueError, SyntaxError)):verify(files, index, modes, index_modes, seal, self.base, self.base_modes)




def _mutation_case(kind):
    def test(self):self.reject_mutation(kind)
    test.__name__ = 'test_rejects_' + kind
    return test

for _name in ('quota', 'handoff', 'cleanup', 'authority', 'source_receipt', 'runtime_bypass', 'allocation',
             'dispatch', 'api', 'historical', 'extra', 'immutable', 'index', 'mode', 'seal', 'oversize', 'private', 'manifest'):setattr(SourceClosureTests, 'test_rejects_' + _name, _mutation_case(_name))


class SourceDiscoveryTests(unittest.TestCase):
    def test_regular_path_read(self):
        with retained_fixture(prefix='rc3-dev6-source-fixture-') as directory:
            root = Path(directory); (root / 'regular').write_bytes(b'synthetic')
            self.assertEqual(read_paths(root, ['regular'])[0], {'regular': b'synthetic'})

    def test_real_symlink_rejection_branch_with_metadata_mock(self):
        with retained_fixture(prefix='rc3-dev6-source-fixture-') as directory:
            root = Path(directory); (root / 'regular').write_bytes(b'synthetic')
            with patch.object(Path, 'is_symlink', lambda p: p.name == 'regular'), self.assertRaises(ValueError):read_paths(root, ['regular'])

    def test_real_hardlink_rejection_branch_with_metadata_mock(self):
        with retained_fixture(prefix='rc3-dev6-source-fixture-') as directory:
            root = Path(directory); (root / 'regular').write_bytes(b'synthetic'); st = (root / 'regular').lstat()
            fake = SimpleNamespace(st_mode=st.st_mode, st_nlink=2, st_size=st.st_size)
            with patch.object(Path, 'lstat', return_value=fake), self.assertRaises(ValueError):read_paths(root, ['regular'])

    def test_fifo_rejection_branch_without_special_artifact(self):
        with retained_fixture(prefix='rc3-dev6-source-fixture-') as directory:
            root = Path(directory); (root / 'regular').write_bytes(b'synthetic')
            fake = SimpleNamespace(st_mode=stat.S_IFIFO | 0o600, st_nlink=1, st_size=0)
            with patch.object(Path, 'lstat', return_value=fake), self.assertRaises(ValueError):read_paths(root, ['regular'])

    def test_duplicate_unsafe_and_oversize_rejection(self):
        with retained_fixture(prefix='rc3-dev6-source-fixture-') as directory:
            root = Path(directory); (root / 'regular').write_bytes(b'synthetic')
            for names in (['regular', 'regular'], ['../escape'], ['/absolute']):
                with self.subTest(names=names), self.assertRaises(ValueError):read_paths(root, names)
            fake = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_size=250001)
            with patch.object(Path, 'lstat', return_value=fake), self.assertRaises(ValueError):read_paths(root, ['regular'])
