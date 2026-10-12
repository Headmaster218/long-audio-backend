"""Finite rc3.dev7 source closure; stdlib only, no product/native imports.

Current source and candidate Git index must match an independently held seal.
Historical dev6 checks retain their original verifier bodies and denominator.
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
BASE = '9e0a135bda2c37641fb0a641655835a0c6a92feb'
BASE_TREE = 'dbdcdcf033f8806b5774391bf6e094cd51a2988d'
BASE_PARENT = 'e6791e6d1b78a14c113529d809b8486349be1146'
BASE_INDEX_SHA256 = '8adce89550e78cad8f9712255eb356a3343170fe16da9d56d919b07a7945d12d'
VERSION = '0.2.0rc3.dev7'
POLICY = 'docs/FRAGMENT_STAGING_POLICY.json'
MANIFEST = 'docs/BACKEND_RC3_DEV7_SOURCE.json'
GATE = 'tests/test_backend_rc3_dev7_source.py'
HISTORICAL = 'tests/test_backend_rc3_dev6_source.py'
AMENDED_CAPTURE = 'tests/test_native_owned_cell_capture.py'
GUARD = 'scripts/source_staging_guard.py'
MAX_FILES, MAX_TOTAL_BYTES, MAX_FILE_BYTES = 346, 4600000, 250000
PURPOSE = 'Fresh rc3.dev7 explicit durable-cell MOSS jobs; source-only pending frozen model-free gates; unchanged qualified storage envelope and no long-recording, acoustic or broader-platform qualification'
DESIGN_PINS = {'inventory':'44562179397c7f79399706d999235f73d31697872ba52b93b5080888964f2dae',
 'contract':'c5025fb83c2ba5d0ee9daa746e99e91a4d2dfad6d3a94aebf20a5220fef31861',
 'validation':'4271bce0672b1e77ec45eb5ca010efe2547378c4d88681466059d0429b25359b'}
ADDED = frozenset(('audio_backend/native_owned_cell_jobs.py', 'docs/BACKEND_RC3_DEV7_SOURCE.json', 'docs/MOSS_CELL_JOBS.md', 'tests/test_backend_rc3_dev7_source.py', 'tests/test_moss_cell_jobs_api.py', 'tests/test_native_owned_cell_jobs.py'))
CHANGED = frozenset(('audio_backend/__init__.py', 'audio_backend/app.py', 'audio_backend/moss_handy_http.py', 'audio_backend/moss_handy_jobs.py', 'audio_backend/native_owned_cell_store.py', 'docs/FRAGMENT_STAGING_POLICY.json', 'pyproject.toml', 'tests/test_backend_rc3_dev6_source.py', AMENDED_CAPTURE))
DELTA = ADDED | CHANGED
CONTRACT_PINS = {'audio_backend/app.py': '8ae7e944be306f96822e1cc14b242ac7daa3050d99eb3eb0964115ec9b0300c2', 'audio_backend/moss_handy_http.py': '70aba9afe9fcfb8e347e3cce1564f2637cf2ed8cf6b111c52ccc0fe71e59848b', 'audio_backend/moss_handy_jobs.py': '4a728aba264ff0de4315524937ad1802bf3ae1e78e4faf8fcba0886bff7b6349', 'audio_backend/native_owned_cell_jobs.py': '5305f206b44ba8364310c0b5db4fba4ca9c55e5536cb3236a9563aef031cc0db', 'audio_backend/native_owned_cell_store.py': '139ccfa5e3e12480ee272292f7877bb37c476e40d529bf6f55bccfefb1c9a323', 'tests/test_moss_cell_jobs_api.py': '146170f13b3d848c52ee0ea7d54e00e9a2672d73c836a9f241cd5ea5605a1227', 'tests/test_native_owned_cell_jobs.py': '4371584b1f369a90b4dc9b6133b5b1c83d379cd57b16196bb055f461c8e2e045', 'tests/test_native_owned_cell_capture.py': 'd8bf70e9098547083f1630befbffebd61c0dc550341f5bdbbb9cf05b83be6213'}

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
    headers=git('cat-file','commit',BASE).split(b'\n\n',1)[0].splitlines()
    require([x[5:].decode() for x in headers if x.startswith(b'tree ')]==[BASE_TREE], 'Published dev6 tree differs')
    require([x[7:].decode() for x in headers if x.startswith(b'parent ')]==[BASE_PARENT], 'Published dev6 sole parent differs')
    files,modes=read_git_snapshot(BASE)
    require(len(files)==340 and sum(map(len,files.values()))==4321712,'Published dev6 inventory differs')
    return MappingProxyType(files),MappingProxyType(modes)


def historical_adapter(original):
    doc_end=original.index(b'"""',3)+3
    start=original.index(b'\ndef current_snapshot():')+1
    end=original.index(b'\n\nclass SourceClosureTests',start)
    doc=b'"""Immutable rc3.dev6 source proof against published 9e0a135b; stdlib only.\n\nOnly the current_snapshot adapter and this docstring changed. Original verifier\nand mutation bodies preserve their original 340-file historical denominator.\nThe separate rc3.dev7 gate owns current worktree/index/external-seal closure.\n"""'
    fn=b"def current_snapshot():\n    check_isolation()\n    revision = '9e0a135bda2c37641fb0a641655835a0c6a92feb'\n    headers = git('cat-file', 'commit', revision).split(b'\\n\\n', 1)[0].splitlines()\n    require([line[5:].decode() for line in headers if line.startswith(b'tree ')] ==\n            ['dbdcdcf033f8806b5774391bf6e094cd51a2988d'], 'Historical rc3.dev6 tree differs')\n    require([line[7:].decode() for line in headers if line.startswith(b'parent ')] ==\n            [BASE], 'Historical rc3.dev6 sole parent differs')\n    git('merge-base', '--is-ancestor', revision, 'HEAD')\n    files, modes = read_git_snapshot(revision)\n    return files, dict(files), modes, dict(modes), canonical(expected_seal(files))\n"
    return doc+original[doc_end:start]+fn+original[end:]


def capture_fixture_contract(files,base):
    # Explicit fifteenth-path amendment: preserve every byte outside this one
    # historical test body, and preserve its ID in the 458-case denominator.
    method='test_no_new_routes_or_model_dispatch_and_conditional_health'
    def split(data):
        node=definition(data,'CaptureFixture',method)
        lines=data.splitlines(keepends=True)
        return (b''.join(lines[:node.lineno-1]), b''.join(lines[node.lineno-1:node.end_lineno]),
                b''.join(lines[node.end_lineno:]))
    old=split(base[AMENDED_CAPTURE]);new=split(files[AMENDED_CAPTURE])
    require(new[0]+old[1]+new[2]==base[AMENDED_CAPTURE],
            'Historical capture fixture changed outside the admitted route assertion')


def expected_policy(base):
    old=json.loads(base[POLICY])
    return dict(old,purpose=PURPOSE,maximum_files=MAX_FILES,maximum_total_bytes=MAX_TOTAL_BYTES,
                allowed_files=sorted(set(old['allowed_files'])|ADDED))


def expected_manifest(files,base,base_modes):
    inventory=[{'path':name,**pin(base[name]),'mode':base_modes[name]} for name in sorted(base)]
    return {'schema':'reviewed-backend-rc3-dev7-source-v1','version':VERSION,
        'base_commit':BASE,'base_tree':BASE_TREE,'base_sole_parent':BASE_PARENT,
        'base_files':340,'base_bytes':4321712,'base_external_index_sha256':BASE_INDEX_SHA256,
        'base_source_inventory_sha256':hashlib.sha256(canonical(inventory)).hexdigest(),
        'design_package':DESIGN_PINS,
        'composed_inventory':{'added':sorted(ADDED),'changed':sorted(CHANGED),'total':15},
        'inventory_amendment':{'original_total':14,'added_test_only_path':AMENDED_CAPTURE,
            'scope':'Only the historical route/disabled-dispatch assertion; existing method ID preserved',
            'revised_total':15,'preserved_test_ids':458},
        'scope':'Three bounded cell routes and one explicit durable-cell origin in the existing volatile MOSS manager',
        'qualification':'Source-only UNQUALIFIED pending independent model-free and exact-A adapter acceptance; unchanged finite dev6 storage envelope; no model, acoustic, genuine larger-core, long-recording, actual in-flight, power-loss, hard-RSS or broader-platform qualification',
        'source_budget':{'maximum_files':MAX_FILES,'maximum_total_bytes':MAX_TOTAL_BYTES,'maximum_file_bytes':MAX_FILE_BYTES},
        'runtime_bounds':{'new_threads':0,'new_processes':0,'new_queues':0,'new_tables':0,'new_persistent_files':0,
            'owner_handoffs':1,'cell_dispatch_pins':1,'total_cell_pins':2,'pin_reservation_bytes':786432,
            'accepted_keys':64,'job_ttl_seconds':900,'response_bytes':1572864,'result_retained_bytes':1048576,
            'proof_bytes':65536,'cell_pcm_bytes':640000,'working_reservation_measured':False},
        'preserved':'Primary SQL, producer/native witness, schema/write transactions/pragmas/runtime/allocation identity, controller/runner/provider/permit and raw/local-speaker/EOS semantics',
        'excluded':'No scheduler, automatic replay, regeneration, source retirement, stitching, identity merge, native inference or broader qualification',
        'historical_proof':'Published dev6 docstring/current_snapshot-only adapter; original verifier/mutation bytes and 340-file denominator preserved',
        'external_seal':'Manifest excludes itself; independent external seal pins the manifest and gate',
        'files':[{'path':name,**pin(files[name]),'mode':'100644',
            **({'supersedes':pin(base[name])} if name in base else {'base_absent':True})}
            for name in sorted(DELTA-{MANIFEST})]}


def expected_seal(files):
    return {'schema':'rc3-dev7-external-source-seal-v1','base_commit':BASE,'base_tree':BASE_TREE,
            'gate':{'path':GATE,**pin(files[GATE])},'manifest':{'path':MANIFEST,**pin(files[MANIFEST])}}


def storage_contract(files,base):
    path='audio_backend/native_owned_cell_store.py'
    for names in (('Budget',),('RuntimeQualification',),('Receipt',),('_runtime_hash',),
                  ('runtime_fingerprints',),('qualified_runtime',)):
        require(shape(definition(files[path],*names))==shape(definition(base[path],*names)),
                'Frozen storage verification changed')
    for name in ('start','_schema','_transaction','_receipt','_publish','_insert_cell','_expire','_sweep',
                 '_open','_scan','_check_parents','_generation'):
        require(shape(definition(files[path],'CellStore',name))==shape(definition(base[path],'CellStore',name)),
                'Frozen storage allocation/write body changed: '+name)
    def constants(data):
        return [shape(n) for n in ast.parse(data).body if isinstance(n,(ast.Assign,ast.AnnAssign))]
    require(constants(files[path])==constants(base[path]),'Storage caps or qualified runtime tuple changed')
    def sql(data):
        return Counter(n.value for n in ast.walk(ast.parse(data)) if isinstance(n,ast.Constant)
            and isinstance(n.value,str) and n.value.strip().upper().startswith(
                ('SELECT ','INSERT ','UPDATE ','DELETE ','CREATE ','PRAGMA ','BEGIN','COMMIT','ROLLBACK')))
    require(sql(files[path])==sql(base[path]),'SQL/pragma inventory changed')
    def connection_threads(data):
        return Counter(shape(n.func) for n in ast.walk(ast.parse(data)) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Attribute) and n.func.attr in ('connect','Thread','Queue'))
    require(connection_threads(files[path])==connection_threads(base[path]),'Storage owner/connection count changed')


def verify_contracts(files,base):
    require(len(ADDED)==6 and len(CHANGED)==9 and len(DELTA)==15,'Exact amended fifteen-path contract changed')
    for path,expected in CONTRACT_PINS.items():
        require(hashlib.sha256(shape(ast.parse(files[path])).encode()).hexdigest()==expected,
                'Reviewed implementation/test AST changed: '+path)
    storage_contract(files,base)
    capture_fixture_contract(files,base)
    http=ast.parse(files['audio_backend/moss_handy_http.py'])
    old=ast.parse(base['audio_backend/moss_handy_http.py'])
    def routes(tree):
        return Counter(n.args[0].value for n in ast.walk(tree) if isinstance(n,ast.Call)
            and isinstance(n.func,ast.Attribute) and n.func.attr in ('get','post')
            and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)
            and n.args[0].value.startswith('/sessions/'))
    delta=routes(http)-routes(old)
    require(delta==Counter({'/sessions/{sid}/native-owned-cells':1,
        '/sessions/{sid}/native-owned-cells/{cell_id}':1,
        '/sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs':1}), 'Only three cell routes allowed')
    require(not routes(old)-routes(http),'Existing routes removed')
    for path in ('audio_backend/native_owned_cell_jobs.py','tests/test_backend_rc3_dev7_source.py'):
        for node in ast.walk(ast.parse(files[path])):
            if isinstance(node,ast.Import):
                require(all(x.name.split('.')[0] not in ('numpy','torch','requests','transcribe_cpp')
                            for x in node.names),'Native/model/network import in pure gate/adapter')
    require(b'No scheduler' in files['docs/MOSS_CELL_JOBS.md'],'Scope documentation missing')


@lru_cache(maxsize=1)
def privacy_guard():
    base,_=baseline()
    require((ROOT/GUARD).read_bytes()==base[GUARD],'Immutable privacy scanner changed')
    spec=importlib.util.spec_from_file_location('_dev7_source_guard',ROOT/GUARD)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    require(module.MAX_BYTES==MAX_FILE_BYTES,'Per-file cap changed')
    return module


def verify(files,index,modes,index_modes,seal,base=None,base_modes=None):
    if base is None or base_modes is None:base,base_modes=baseline()
    require(files==index,'Working source/index differ')
    require(set(files)==set(base)|ADDED and len(files)==MAX_FILES,'Source inventory differs')
    require({n for n in files if files[n]!=base.get(n)}==DELTA,'Exact source delta differs')
    require(all(files[n]==base[n] for n in set(base)-CHANGED),'Immutable source changed')
    require(modes==index_modes=={**base_modes,**{n:'100644' for n in ADDED}},'Source modes differ')
    require(files[HISTORICAL]==historical_adapter(base[HISTORICAL]),'Historical verifier bytes changed')
    for name in ('audio_backend/__init__.py','pyproject.toml'):
        require(files[name]==base[name].replace(b'0.2.0rc3.dev6',VERSION.encode()),'Version-only metadata differs')
    verify_contracts(files,base)
    policy=expected_policy(base)
    require(files[POLICY]==canonical(policy),'Source allowlist changed')
    require(files[MANIFEST]==canonical(expected_manifest(files,base,base_modes)),'Manifest stale/noncanonical')
    require(seal==canonical(expected_seal(files)),'Independent external seal differs')
    require(all(len(x)<=MAX_FILE_BYTES for x in files.values()) and sum(map(len,files.values()))<=MAX_TOTAL_BYTES,
            'Source byte ceiling exceeded')
    require(privacy_guard().validate(files,policy,require_manifest=False)['findings']==0,'Privacy findings')


def current_snapshot():
    check_isolation()
    index_path=external_file('RC3_DEV7_INDEX');seal_path=external_file('RC3_DEV7_SOURCE_SEAL')
    require(index_path!=seal_path,'Separate external index and seal required')
    names=git('ls-files','--cached','--others','--exclude-standard','-z',index=index_path)
    files,modes=read_paths(ROOT,[n.decode() for n in names.split(b'\0') if n])
    index,index_modes=read_git_snapshot(':',index_path)
    head=git('rev-parse','HEAD').decode().strip()
    if head!=BASE:
        headers=git('cat-file','commit',head).split(b'\n\n',1)[0].splitlines()
        require([x[7:].decode() for x in headers if x.startswith(b'parent ')]==[BASE], 'Candidate sole dev6 parent differs')
        require(read_git_snapshot(head)==(files,modes),'Committed candidate differs')
    return files,index,modes,index_modes,seal_path.read_bytes()


class SourceClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current=current_snapshot();cls.base,cls.base_modes=baseline()
        verify(*cls.current,cls.base,cls.base_modes)

    def reject(self,path,old,new):
        files,index,modes,index_modes=map(dict,self.current[:4])
        self.assertIn(old,files[path]);files[path]=files[path].replace(old,new,1);index[path]=files[path]
        files[MANIFEST]=canonical(expected_manifest(files,self.base,self.base_modes));index[MANIFEST]=files[MANIFEST]
        with self.assertRaises((ValueError,SyntaxError)):
            verify(files,index,modes,index_modes,canonical(expected_seal(files)),self.base,self.base_modes)

    def test_exact_fourteen_path_delta_and_source_limits(self):
        # Historical test ID retained for original fourteen paths plus the one
        # explicitly admitted capture-fixture correction; no denominator trim.
        verify(*self.current,self.base,self.base_modes)
        files,index,modes,index_modes=map(dict,self.current[:4])
        files['unlisted.py']=index['unlisted.py']=b'x=1\n';modes['unlisted.py']=index_modes['unlisted.py']='100644'
        with self.assertRaises(ValueError):verify(files,index,modes,index_modes,self.current[4],self.base,self.base_modes)

    def test_exact_published_dev6_tree_and_sole_parent(self):
        self.assertEqual(len(self.base),340);self.assertEqual(sum(map(len,self.base.values())),4321712)
        self.assertEqual(git('rev-parse',BASE+'^{tree}').decode().strip(),BASE_TREE)

    def test_manifest_external_index_and_independent_seal_match(self):
        self.assertEqual(self.current[0][MANIFEST],canonical(expected_manifest(self.current[0],self.base,self.base_modes)))
        self.assertEqual(self.current[4],canonical(expected_seal(self.current[0])))
        with self.assertRaises(ValueError):verify(*self.current[:4],self.current[4]+b' ',self.base,self.base_modes)

    def test_historical_dev6_verifier_and_mutation_bodies_unchanged(self):
        original=self.base[HISTORICAL];adapted=self.current[0][HISTORICAL]
        self.assertEqual(adapted,historical_adapter(original))
        anchor=b'\n\nclass SourceClosureTests'
        self.assertEqual(original[original.index(anchor):],adapted[adapted.index(anchor):])

    def test_version_only_metadata_and_unchanged_dependencies(self):
        self.assertEqual(self.current[0]['pyproject.toml'],self.base['pyproject.toml'].replace(b'dev6',b'dev7'))
        self.reject('audio_backend/__init__.py',b'dev7',b'dev8')

    def test_primary_sql_worker_lifecycle_capture_and_provider_files_unchanged(self):
        for path in set(self.base)-CHANGED:self.assertEqual(self.current[0][path],self.base[path])
        self.reject('audio_backend/storage.py',b'import ',b'# altered\nimport ')

    def test_cell_schema_write_transactions_pragmas_and_allocation_identity_unchanged(self):
        storage_contract(self.current[0],self.base)
        self.reject('audio_backend/native_owned_cell_store.py',b'TOTAL_BYTES = 64 * 1024**2',b'TOTAL_BYTES = 128 * 1024**2')
        self.reject('audio_backend/native_owned_cell_store.py',b'PRAGMA synchronous=FULL',b'PRAGMA synchronous=OFF')

    def test_added_cell_operations_are_read_only_and_single_owner(self):
        self.reject('audio_backend/native_owned_cell_store.py',b"elif kind == 'jobs_list':",
            b"elif kind == 'jobs_list':\n            self._db.execute('DELETE FROM cells')")
        self.reject('audio_backend/native_owned_cell_jobs.py',b'import hashlib',b'import hashlib\nimport sqlite3\nnew = sqlite3.connect(\':memory:\')')
        self.reject('audio_backend/native_owned_cell_jobs.py',b'import hashlib',b'import hashlib\nthreading.Thread(target=lambda:None).start()')

    def test_only_three_new_routes_and_existing_parser_response_pools(self):
        self.reject('audio_backend/moss_handy_http.py',b"/sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs",
            b"/sessions/{sid}/native-owned-cells/{cell_id}/audio")
        self.assertEqual(self.current[0]['audio_backend/native_owned_support_cache.py'],self.base['audio_backend/native_owned_support_cache.py'])

    def test_no_scheduler_supervisor_replay_identity_merge_or_source_retirement(self):
        self.reject('audio_backend/moss_handy_jobs.py',b'    def _run(self, record):',
            b'    def _run(self, record):\n        self.submit_cell("session", "target", {}, "auto")')

    def test_privacy_source_allowlist_rejects_runtime_database_audio_credentials_and_logs(self):
        files=self.current[0];policy=json.loads(files[POLICY]);guard=privacy_guard()
        self.assertEqual(policy['excluded_categories'],json.loads(self.base[POLICY])['excluded_categories'])
        for path in ('runtime/python','cells.sqlite3','source.wav','secret.env','server.log'):
            candidate=dict(files);candidate[path]=b'synthetic'
            with self.assertRaises((ValueError,RuntimeError)):
                guard.validate(candidate,policy,require_manifest=False)

    def test_exact_runtime_qualification_and_no_model_import_in_source_gate(self):
        self.reject('audio_backend/native_owned_cell_store.py',b"filesystem_device=27",b"filesystem_device=28")
        for node in ast.walk(ast.parse(self.current[0][GATE])):
            if isinstance(node,ast.ImportFrom):self.assertFalse((node.module or '').startswith('audio_backend'))
