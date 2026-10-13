"""Finite rc3.dev8 current source closure; stdlib only, no product/native imports.

The new gate owns the 351-file current worktree/index/external-seal closure.
Historical gates retain immutable published snapshots and every prior test ID.
No passing source gate is an actual, acoustic or platform qualification.
"""
from functools import lru_cache
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
from types import MappingProxyType
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = 'ebc7a02fb4420c0089f2a146ec6bf9dcc23137b4'
BASE_TREE = '41a091d97438238c88491a4c543002b47e647c72'
BASE_PARENT = '9e0a135bda2c37641fb0a641655835a0c6a92feb'
VERSION = '0.2.0rc3.dev8'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
MANIFEST = 'docs/BACKEND_RC3_DEV8_SOURCE.json'
GATE = 'tests/test_backend_rc3_dev8_source.py'
HISTORICAL = 'tests/test_backend_rc3_dev7_source.py'
GUARD = 'scripts/source_staging_guard.py'
MAX_FILES, MAX_TOTAL_BYTES, MAX_FILE_BYTES = 351, 5 * 1024**2, 250000
MAX_SOURCE_GIT_BYTES = 12 * 1024**2
PURPOSE = 'Fresh rc3.dev8 opt-in two-commit seam alternatives; source-only UNQUALIFIED pending independent frozen model-free and finite SQLite-policy acceptance; no actual, acoustic, long-recording or broader-platform qualification'
DESIGN_PINS = {
    'plan': '5603a38c8b80d7c4877380606ac47ec38b24b05599f73aa20e4a20b453ad8b8b',
    'inventory': '876863d909d8d8f2439b2194c63aae0a58eca7090ae4c3cfa65deb6ffb389579',
    'clarification': '745f6f8cb9261841e1719c8eb724f71a75b6db6bcc31217580ec32a5bd9b803b',
    'identity_correction': 'b1e24da3e5fdaba63367f110550cb5dcea3ccfa8112d84649a374fea5ceba3f2',
}
ADDED = frozenset((
    'audio_backend/native_owned_seam_cells.py',
    'docs/BACKEND_RC3_DEV8_SOURCE.json',
    'docs/NATIVE_OWNED_SEAM_CELLS.md',
    'tests/test_backend_rc3_dev8_source.py',
    'tests/test_native_owned_seam_cells.py',
))
CHANGED = frozenset((
    'audio_backend/__init__.py', 'audio_backend/__main__.py',
    'audio_backend/moss_handy_http.py', 'audio_backend/native_owned_cell_capture.py',
    'audio_backend/native_owned_cell_jobs.py', 'audio_backend/native_owned_cell_store.py',
    'audio_backend/native_owned_cells.py', 'audio_backend/settings.py',
    'docs/FRAGMENT_STAGING_POLICY.json', 'pyproject.toml',
    'tests/test_backend_rc3_dev7_source.py', 'tests/test_moss_cell_jobs_api.py',
    'tests/test_native_owned_cell_capture.py', 'tests/test_native_owned_cell_jobs.py',
    'tests/test_native_owned_cell_store.py',
))
DELTA = ADDED | CHANGED
CONTRACT_PATHS = frozenset(path for path in DELTA if path.endswith('.py') and path not in (
    GATE, HISTORICAL, 'audio_backend/__init__.py'))
# Exact source-definition pins for independent review. A pin is not a passing
# test or an acceptance record; external review and execution remain required.
CONTRACT_PINS = {'audio_backend/__main__.py': 'c74ddb7667618196c43dc39710e08669eae0e50367f1a5e8f8be988e323eceb8', 'audio_backend/moss_handy_http.py': '9297b7da9e0a173e843d061c3f689ccee78cd796e017497e816af1441ef99c53', 'audio_backend/native_owned_cell_capture.py': '17ef79047982a9b766cf06a5c54e53844ea3b939573aae9647474a8193ff89c9', 'audio_backend/native_owned_cell_jobs.py': 'ee6bc73a8bb98a069914f9ca03aebe8ecf60aeddbba4b185f670b2c79ba67a41', 'audio_backend/native_owned_cell_store.py': '4e3751f162b1c6290367b206be4dd2e309da4c80367c0f67a23ff94b79fa06bb', 'audio_backend/native_owned_cells.py': '6a9f0d31360d0ab8938b96a4ed20baa856f84865b0d24b0c0a612d32fdca6c82', 'audio_backend/native_owned_seam_cells.py': '7a398affebccb6d7668a6102af2cbf1c7e27c7f0ade44e7113e885548023c0cf', 'audio_backend/settings.py': '3e320bc9d23e195694202fc0c9ccbb4da1f89bbbe7917a724c70211b0942813f', 'tests/test_moss_cell_jobs_api.py': '998ebd55b1eff4a51fc95944b882a98e406a6a66912d1b330802e1fdd75407af', 'tests/test_native_owned_cell_capture.py': 'df3ffc2ba578aec53f39488591fe7ca7e62d403cfc12b3c6c6cf7a72e95434f6', 'tests/test_native_owned_cell_jobs.py': '6b791f436a5262432cf4e9062501efc0d682ec7ddcb41d872c86fa89396b0595', 'tests/test_native_owned_cell_store.py': '9736442cf0e8af4e9b027a4158c7f4234bdf92460793cca9a22037c117167e0d', 'tests/test_native_owned_seam_cells.py': '4b6f32b5bd8202ce3102dd934a056c9f255e27ddd3c3d371df26dc3b58a6913b'}
P1 = '8385e071e9700dced5d766c80f7c41a676481a796b74a06848204f561d1ea0b5'
P2 = 'b8480ec44a75c456a6e036cfa8d28d2b03829e2769f438178f4b29b14f2425b1'
S2_POLICY = 'fece6df7a046b4ac0b94e608e002f580f5df8a42c2f90ccd17f6a950b1184e64'


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


def shape(node):return ast.dump(node, annotate_fields=True, include_attributes=False)


def definition(data, *names):
    node = ast.parse(data)
    for name in names:
        found = [child for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == name]
        require(len(found) == 1, 'Missing or duplicate source definition')
        node = found[0]
    return node


@lru_cache(maxsize=1)
def baseline():
    headers = git('cat-file', 'commit', BASE).split(b'\n\n', 1)[0].splitlines()
    require([x[5:].decode() for x in headers if x.startswith(b'tree ')] == [BASE_TREE],
            'Published dev7 tree differs')
    require([x[7:].decode() for x in headers if x.startswith(b'parent ')] == [BASE_PARENT],
            'Published dev7 sole parent differs')
    files, modes = read_git_snapshot(BASE)
    require(len(files) == 346 and sum(map(len, files.values())) == 4487300,
            'Published dev7 inventory differs')
    return MappingProxyType(files), MappingProxyType(modes)


def historical_adapter(original):
    doc_end = original.index(b'"""', 3) + 3
    start = original.index(b'\ndef current_snapshot():') + 1
    end = original.index(b'\n\nclass SourceClosureTests', start)
    doc = b'"""Immutable rc3.dev7 source proof against published ebc7a02f; stdlib only.\n\nOnly the current_snapshot adapter and this docstring changed. Original verifier\nand mutation bodies preserve their original 346-file historical denominator.\nThe separate rc3.dev8 gate owns current worktree/index/external-seal closure.\n"""'
    fn = b"def current_snapshot():\n    check_isolation()\n    revision = 'ebc7a02fb4420c0089f2a146ec6bf9dcc23137b4'\n    headers = git('cat-file', 'commit', revision).split(b'\\n\\n', 1)[0].splitlines()\n    require([line[5:].decode() for line in headers if line.startswith(b'tree ')] ==\n            ['41a091d97438238c88491a4c543002b47e647c72'], 'Historical rc3.dev7 tree differs')\n    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] ==\n            [BASE], 'Historical rc3.dev7 sole parent differs')\n    git('merge-base', '--is-ancestor', revision, 'HEAD')\n    files, modes = read_git_snapshot(revision)\n    require(len(files) == 346 and sum(map(len, files.values())) == 4487300,\n            'Historical rc3.dev7 inventory differs')\n    return files, dict(files), modes, dict(modes), canonical(expected_seal(files))\n"
    return doc + original[doc_end:start] + fn + original[end:]


def expected_policy(base):
    old = json.loads(base[POLICY])
    return dict(old, purpose=PURPOSE, maximum_files=MAX_FILES,
                maximum_total_bytes=MAX_TOTAL_BYTES,
                allowed_files=sorted(set(old['allowed_files']) | ADDED))


def expected_manifest(files, base, base_modes):
    inventory = [{'path': name, **pin(base[name]), 'mode': base_modes[name]}
                 for name in sorted(base)]
    return {
        'schema': 'reviewed-backend-rc3-dev8-source-v1', 'version': VERSION,
        'base_commit': BASE, 'base_tree': BASE_TREE, 'base_sole_parent': BASE_PARENT,
        'base_files': 346, 'base_bytes': 4487300,
        'base_source_inventory_sha256': hashlib.sha256(canonical(inventory)).hexdigest(),
        'design_package': DESIGN_PINS,
        'composed_inventory': {'added': sorted(ADDED), 'changed': sorted(CHANGED),
                               'total': 20, 'deleted': []},
        'scope': 'Default-OFF two-commit same-epoch seam alternatives using existing capture and owner hooks',
        'qualification': 'SOURCE-ONLY UNQUALIFIED: independent source/model-free and finite exact-runtime SQLite-policy acceptance pending; no actual run, acoustic, genuine overlap, long-recording, in-flight, power-loss, hard-RSS or broader-platform claim',
        'source_budget': {'maximum_files': MAX_FILES, 'maximum_total_bytes': MAX_TOTAL_BYTES,
                          'maximum_file_bytes': MAX_FILE_BYTES,
                          'maximum_source_plus_git_bytes': MAX_SOURCE_GIT_BYTES},
        'preparation_budget': {'maximum_metadata_bytes': 16 * 1024**2,
                               'maximum_test_artifact_bytes': 256 * 1024**2,
                               'tests_executed_by_this_manifest': False},
        'runtime_bounds': {'new_threads': 0, 'new_processes': 0, 'new_queues': 0,
                           'new_tables': 0, 'new_persistent_files': 0,
                           'pending_tail_slots': 1, 'tail_payload_bytes': 786432,
                           'assembler_working_reservation_bytes': 4194304,
                           'working_reservation_measured': False,
                           'source_parts': 2, 'original_commits': 2, 'native_witnesses': 2,
                           'max_turn_records': 256, 'max_side_frames': 160000,
                           'max_core_envelope_frames': 960000,
                           'proof_bytes': 65536, 'cell_pcm_bytes': 640000,
                           'batch_cells': 3, 'handoff_bytes': 2359296,
                           'total_cell_pins': 2, 'pin_reservation_bytes': 786432},
        'policy_hashes': {'v1': P1, 'seam_v2': P2, 'composite_store': S2_POLICY},
        'coverage': {'kind': 'seam_overlap_alternative',
                     'source_support_core_semantics': 'two_committed_core_envelope',
                     'publication_anchor': 'original right-hand B commit',
                     'coverage_intent': 'overlapping_diagnostic',
                     'adds_primary_audio': False, 'automatic_dispatch': False},
        'ownership': 'Ordered A/B chunk-local coordinates, session/stream/epoch, geometry and policy; excludes evidence hashes; conflicting evidence keeps the same ownership and must reject',
        'storage_exception': 'Only known legacy/composite policy binding and strict read validation extend _open; unchanged schema, write function bytes, write SQL, pragmas, qualified runtime tuple and allocation caps',
        'preserved': 'All 346 old paths, prior test IDs including the 458-case historical selection, v1 identities/wire bytes/meaning, primary Worker/SQL/native/provider/manager/permit paths and existing routes',
        'historical_proof': 'Published dev7 docstring/current_snapshot-only adapter; original verifier/mutation bytes and 346-file denominator preserved; dev6 and earlier gates/manifests unchanged',
        'acceptance_records': 'Independent preparation records must freeze source/index/test IDs, review storage parity and resource admission before any authorized execution; no prior success is adopted',
        'external_seal': 'Manifest excludes itself; independently held external seal pins the manifest and gate',
        'files': [{'path': name, **pin(files[name]), 'mode': '100644',
                   **({'supersedes': pin(base[name])} if name in base else {'base_absent': True})}
                  for name in sorted(DELTA - {MANIFEST})],
    }


def expected_seal(files):
    return {'schema': 'rc3-dev8-external-source-seal-v1',
            'base_commit': BASE, 'base_tree': BASE_TREE,
            'gate': {'path': GATE, **pin(files[GATE])},
            'manifest': {'path': MANIFEST, **pin(files[MANIFEST])}}


def definition_bytes(data, *names):
    node = definition(data, *names)
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return b''.join(data.splitlines(keepends=True)[start - 1:node.end_lineno])


def storage_contract(files, base):
    path = 'audio_backend/native_owned_cell_store.py'
    for names in (('Budget',), ('RuntimeQualification',), ('Receipt',),
                  ('_runtime_hash',), ('runtime_fingerprints',), ('qualified_runtime',)):
        require(shape(definition(files[path], *names)) == shape(definition(base[path], *names)),
                'Frozen storage/runtime verification changed: ' + '.'.join(names))
    for name in ('start', '_schema', '_transaction', '_receipt', '_publish', '_insert_cell',
                 '_expire', '_sweep', '_scan', '_check_parents', '_generation'):
        require(definition_bytes(files[path], 'CellStore', name) ==
                definition_bytes(base[path], 'CellStore', name),
                'Frozen storage allocation/write bytes changed: ' + name)
    # _open is deliberately NOT blanket AST-preserved: the exact known-policy
    # admission extension is independently reviewed and covered by CONTRACT_PINS.
    def constants(data):
        result = {}
        for node in ast.parse(data).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):result[target.id] = shape(node)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                result[node.target.id] = shape(node)
        return result
    old_constants, new_constants = constants(base[path]), constants(files[path])
    require(all(new_constants.get(name) == value for name, value in old_constants.items()),
            'Inherited storage caps or qualified runtime tuple changed')
    def sql(data):
        return Counter(node.value for node in ast.walk(ast.parse(data))
                       if isinstance(node, ast.Constant) and isinstance(node.value, str)
                       and node.value.strip().upper().startswith((
                           'INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ', 'PRAGMA ',
                           'BEGIN', 'COMMIT', 'ROLLBACK', 'ALTER ', 'DROP ', 'REPLACE ')))
    require(sql(files[path]) == sql(base[path]), 'Write SQL/pragma inventory changed')
    def connections(data):
        return Counter(shape(node.func) for node in ast.walk(ast.parse(data))
                       if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr in ('connect', 'Thread', 'Queue'))
    require(connections(files[path]) == connections(base[path]),
            'Storage owner/connection count changed')


def static_test_ids(files):
    found = set()
    def collect(body, prefix):
        for node in body:
            if isinstance(node, ast.ClassDef):collect(node.body, prefix + (node.name,))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                found.add(prefix + (node.name,))
    for name, data in files.items():
        if name.startswith('tests/') and name.endswith('.py'):
            collect(ast.parse(data).body, (name,))
    return found


def settings_contract(files, base):
    path = 'audio_backend/settings.py'
    expected = base[path].replace(
        b'    native_owned_cells_enabled: bool = False\n',
        b'    native_owned_cells_enabled: bool = False\n    native_owned_seam_cells_enabled: bool = False\n', 1)
    expected = expected.replace(
        b'        if type(self.native_owned_support_enabled) is not bool:\n',
        b"        if type(self.native_owned_seam_cells_enabled) is not bool:\n            raise ValueError('native_owned_seam_cells_enabled must be boolean')\n        if self.native_owned_seam_cells_enabled and not self.native_owned_cells_enabled:\n            raise ValueError('native_owned_seam_cells_enabled requires native_owned_cells_enabled')\n        if type(self.native_owned_support_enabled) is not bool:\n", 1)
    require(files[path] == expected, 'Settings must add only the default-OFF boolean and cells dependency')
    tree = ast.parse(files['audio_backend/__main__.py'])
    options = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == 'add_argument'
               and n.args and isinstance(n.args[0], ast.Constant)
               and n.args[0].value == '--native-owned-seam-cells']
    require(len(options) == 1 and any(k.arg == 'action' and isinstance(k.value, ast.Constant)
            and k.value.value == 'store_true' for k in options[0].keywords), 'CLI opt-in differs')
    require(any(isinstance(n, ast.keyword) and n.arg == 'native_owned_seam_cells_enabled'
                and isinstance(n.value, ast.Attribute) and n.value.attr == 'native_owned_seam_cells'
                for n in ast.walk(tree)), 'CLI setting propagation missing')


def verify_contracts(files, base):
    require(len(ADDED) == 5 and len(CHANGED) == 15 and len(DELTA) == 20,
            'Exact twenty-path contract changed')
    require(type(CONTRACT_PINS) is dict and set(CONTRACT_PINS) == CONTRACT_PATHS,
            'Implementation AST pins are pending independent final-source review')
    for path, expected in CONTRACT_PINS.items():
        require(type(expected) is str and len(expected) == 64 and
                hashlib.sha256(shape(ast.parse(files[path])).encode()).hexdigest() == expected,
                'Reviewed implementation/test AST changed: ' + path)
    storage_contract(files, base)
    settings_contract(files, base)
    require(static_test_ids(base).issubset(static_test_ids(files)), 'Prior test method ID removed or renamed')
    def routes(data):
        return Counter(n.args[0].value for n in ast.walk(ast.parse(data))
                       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr in ('get', 'post', 'put', 'delete', 'patch')
                       and n.args and isinstance(n.args[0], ast.Constant)
                       and isinstance(n.args[0].value, str) and n.args[0].value.startswith('/'))
    path = 'audio_backend/moss_handy_http.py'
    require(routes(files[path]) == routes(base[path]), 'Seam feature must not add or remove routes')
    permitted_imports = {'functools', 'ast', 'collections', 'hashlib', 'importlib', 'json',
                         'os', 'pathlib', 'stat', 'subprocess', 'types', 'unittest'}
    for node in ast.walk(ast.parse(files[GATE])):
        if isinstance(node, ast.Import):
            require(all(n.name.split('.')[0] in permitted_imports for n in node.names),
                    'Non-stdlib import in source gate')
        elif isinstance(node, ast.ImportFrom):
            require(node.level == 0 and (node.module or '').split('.')[0] in permitted_imports,
                    'Product/native import in source gate')
    documentation = files['docs/NATIVE_OWNED_SEAM_CELLS.md']
    for phrase in (b'two_committed_core_envelope', b'overlapping_diagnostic',
                   b'publication_anchor', b'evidence_sha256', b'UNQUALIFIED'):
        require(phrase in documentation, 'Required seam coverage/qualification documentation missing')


@lru_cache(maxsize=1)
def privacy_guard():
    base, _ = baseline()
    require((ROOT / GUARD).read_bytes() == base[GUARD], 'Immutable privacy scanner changed')
    spec = importlib.util.spec_from_file_location('_dev8_source_guard', ROOT / GUARD)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    require(module.MAX_BYTES == MAX_FILE_BYTES, 'Per-file cap changed')
    return module


def verify(files, index, modes, index_modes, seal, base=None, base_modes=None):
    if base is None or base_modes is None:base, base_modes = baseline()
    require(files == index, 'Working source/index differ')
    require(set(files) == set(base) | ADDED and len(files) == MAX_FILES, 'Source inventory differs')
    require({name for name in files if files[name] != base.get(name)} == DELTA, 'Exact source delta differs')
    require(all(files[name] == base[name] for name in set(base) - CHANGED), 'Immutable source changed')
    require(modes == index_modes == {**base_modes, **{name: '100644' for name in ADDED}}, 'Source modes differ')
    require(files[HISTORICAL] == historical_adapter(base[HISTORICAL]), 'Historical dev7 verifier bytes changed')
    for name in ('audio_backend/__init__.py', 'pyproject.toml'):
        require(files[name] == base[name].replace(b'0.2.0rc3.dev7', VERSION.encode()), 'Version-only metadata differs')
    verify_contracts(files, base)
    policy = expected_policy(base)
    require(files[POLICY] == canonical(policy), 'Exact source allowlist/privacy policy changed')
    require(files[MANIFEST] == canonical(expected_manifest(files, base, base_modes)), 'Manifest stale/noncanonical')
    require(seal == canonical(expected_seal(files)), 'Independent external seal differs')
    require(all(len(data) <= MAX_FILE_BYTES for data in files.values()) and
            sum(map(len, files.values())) <= MAX_TOTAL_BYTES, 'Source byte ceiling exceeded')
    require(privacy_guard().validate(files, policy, require_manifest=False)['findings'] == 0, 'Privacy findings')


def current_snapshot():
    check_isolation()
    index_path = external_file('RC3_DEV8_INDEX')
    seal_path = external_file('RC3_DEV8_SOURCE_SEAL')
    require(index_path != seal_path, 'Separate external index and seal required')
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z', index=index_path)
    files, modes = read_paths(ROOT, [name.decode() for name in names.split(b'\0') if name])
    index, index_modes = read_git_snapshot(':', index_path)
    head = git('rev-parse', 'HEAD').decode().strip()
    if head != BASE:
        headers = git('cat-file', 'commit', head).split(b'\n\n', 1)[0].splitlines()
        require([x[7:].decode() for x in headers if x.startswith(b'parent ')] == [BASE],
                'Candidate sole dev7 parent differs')
        require(read_git_snapshot(head) == (files, modes), 'Committed candidate differs')
    git_bytes = 0
    for path in (ROOT / '.git').rglob('*'):
        require(not path.is_symlink(), 'Git metadata symlink prohibited')
        if path.is_file():
            info = path.stat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'Git metadata alias prohibited')
            git_bytes += info.st_size
            require(git_bytes + sum(map(len, files.values())) <= MAX_SOURCE_GIT_BYTES,
                    'Source plus Git ceiling exceeded')
    return files, index, modes, index_modes, seal_path.read_bytes()


class SourceClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = current_snapshot(); cls.base, cls.base_modes = baseline()
        verify(*cls.current, cls.base, cls.base_modes)

    def reject(self, path, old, new):
        files, index, modes, index_modes = map(dict, self.current[:4])
        self.assertIn(old, files[path]); files[path] = files[path].replace(old, new, 1); index[path] = files[path]
        files[MANIFEST] = canonical(expected_manifest(files, self.base, self.base_modes)); index[MANIFEST] = files[MANIFEST]
        with self.assertRaises((ValueError, SyntaxError)):
            verify(files, index, modes, index_modes, canonical(expected_seal(files)), self.base, self.base_modes)

    def test_exact_twenty_path_delta_and_source_limits(self):
        verify(*self.current, self.base, self.base_modes)
        self.assertEqual((len(ADDED), len(CHANGED), len(DELTA)), (5, 15, 20))
        files, index, modes, index_modes = map(dict, self.current[:4])
        files['unlisted.py'] = index['unlisted.py'] = b'x=1\n'
        modes['unlisted.py'] = index_modes['unlisted.py'] = '100644'
        with self.assertRaises(ValueError):
            verify(files, index, modes, index_modes, self.current[4], self.base, self.base_modes)

    def test_exact_published_dev7_tree_sole_parent_and_inventory(self):
        self.assertEqual(len(self.base), 346)
        self.assertEqual(sum(map(len, self.base.values())), 4487300)
        self.assertEqual(git('rev-parse', BASE + '^{tree}').decode().strip(), BASE_TREE)

    def test_manifest_external_index_and_independent_seal_match(self):
        self.assertEqual(self.current[0][MANIFEST], canonical(expected_manifest(self.current[0], self.base, self.base_modes)))
        self.assertEqual(self.current[4], canonical(expected_seal(self.current[0])))
        with self.assertRaises(ValueError):
            verify(*self.current[:4], self.current[4] + b' ', self.base, self.base_modes)

    def test_dev7_historical_adapter_only_and_earlier_gates_unchanged(self):
        original = self.base[HISTORICAL]; adapted = self.current[0][HISTORICAL]
        self.assertEqual(adapted, historical_adapter(original))
        anchor = b'\n\nclass SourceClosureTests'
        self.assertEqual(original[original.index(anchor):], adapted[adapted.index(anchor):])
        for name in self.base:
            if name.startswith(('tests/test_backend_', 'docs/BACKEND_')) and name != HISTORICAL:
                self.assertEqual(self.current[0][name], self.base[name])

    def test_all_prior_test_ids_preserved_without_denominator_substitution(self):
        self.assertTrue(static_test_ids(self.base).issubset(static_test_ids(self.current[0])))
        self.reject('tests/test_native_owned_cell_capture.py',
                    b'def test_no_new_routes_or_model_dispatch_and_conditional_health(',
                    b'def removed_legacy_test(')

    def test_version_only_metadata_and_dependencies_unchanged(self):
        self.assertEqual(self.current[0]['pyproject.toml'],
                         self.base['pyproject.toml'].replace(b'0.2.0rc3.dev7', b'0.2.0rc3.dev8'))
        self.reject('audio_backend/__init__.py', b'dev8', b'dev9')

    def test_default_off_strict_boolean_dependency_and_cli_wiring(self):
        settings_contract(self.current[0], self.base)
        self.reject('audio_backend/settings.py', b'native_owned_seam_cells_enabled: bool = False',
                    b'native_owned_seam_cells_enabled: bool = True')
        self.reject('audio_backend/__main__.py', b'--native-owned-seam-cells', b'--automatic-seam-cells')

    def test_primary_worker_native_provider_manager_permit_closure_unchanged(self):
        for name in set(self.base) - CHANGED:self.assertEqual(self.current[0][name], self.base[name])
        for path in ('audio_backend/storage.py', 'audio_backend/worker.py',
                     'audio_backend/native_worker.py', 'audio_backend/moss_handy_jobs.py',
                     'audio_backend/native_execution_permit.py'):
            self.reject(path, b'import ', b'# changed\nimport ')

    def test_storage_write_bytes_sql_pragmas_caps_runtime_unchanged(self):
        storage_contract(self.current[0], self.base)
        self.reject('audio_backend/native_owned_cell_store.py',
                    b'TOTAL_BYTES = 64 * 1024**2', b'TOTAL_BYTES = 128 * 1024**2')
        self.reject('audio_backend/native_owned_cell_store.py',
                    b'PRAGMA synchronous=FULL', b'PRAGMA synchronous=OFF')
        self.reject('audio_backend/native_owned_cell_store.py',
                    b'filesystem_device=27', b'filesystem_device=28')

    def test_open_policy_exception_is_explicit_and_final_ast_pinned(self):
        path = 'audio_backend/native_owned_cell_store.py'
        self.assertIn(path, CONTRACT_PINS)
        self.assertNotEqual(shape(definition(self.current[0][path], 'CellStore', '_open')),
                            shape(definition(self.base[path], 'CellStore', '_open')))
        self.assertIn('Only known legacy/composite policy binding',
                      json.loads(self.current[0][MANIFEST])['storage_exception'])

    def test_routes_and_existing_provider_dispatch_remain_unchanged(self):
        self.reject('audio_backend/moss_handy_http.py',
                    b'/sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs',
                    b'/sessions/{sid}/native-owned-cells/{cell_id}/audio')
        self.reject('audio_backend/moss_handy_jobs.py',
                    b'    def _run(self, record):',
                    b'    def _run(self, record):\n        self.submit_cell("session", "target", {}, "auto")')

    def test_source_policy_preserves_privacy_and_rejects_unlisted_artifacts(self):
        files = self.current[0]; policy = json.loads(files[POLICY]); guard = privacy_guard()
        self.assertEqual(policy['excluded_categories'], json.loads(self.base[POLICY])['excluded_categories'])
        for path in ('runtime/python', 'cells.sqlite3', 'source.wav', 'secret.env', 'server.log'):
            candidate = dict(files); candidate[path] = b'synthetic'
            with self.assertRaises((ValueError, RuntimeError)):
                guard.validate(candidate, policy, require_manifest=False)

    def test_source_gate_has_no_product_or_native_import(self):
        for node in ast.walk(ast.parse(self.current[0][GATE])):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or '').startswith('audio_backend'))
        self.reject(GATE, b'import hashlib', b'import hashlib\nimport audio_backend')
