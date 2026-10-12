"""Fixed model-free qualification. No provider import or child dispatch."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import types
import unittest
from unittest import mock
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE/'controller'))
import bootstrap as b
import continuation as c
import server as s


class Cases(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.root = Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp/shm-fixture'
    cls.output = cls.root/'tmp/continuation'
    (cls.output/'data').mkdir(parents=True, mode=0o700)
    for p in (cls.root, cls.root/'tmp', cls.output):
      p.chmod(0o700)
    cls.path = cls.output/'data/jobs.sqlite3-shm'
    cls.spec = {'output_dir': str(cls.output), 'data_dir': str(cls.output/'data'),
          'hash_inventory': [], '_server_phase': 'restart-on'}

  def setUp(self):
    if not self.path.exists():
      with self.path.open('xb') as out:
        out.write(bytes(32768))
    self.env = mock.patch.dict(os.environ, {'GUARD_RUN_DIRECTORY': str(self.root)})
    self.env.start()
    self.addCleanup(self.env.stop)

  def raw(self, name=None, permissions='rw-s', offset=0, inode=None, length=32768, start=4096):
    st = self.path.stat()
    return (f'{start:x}-{start+length:x} {permissions} {offset:x} '
        f'{os.major(st.st_dev):x}:{os.minor(st.st_dev):x} '
        f'{st.st_ino if inode is None else inode} {name or self.path}\n').encode()

  def observed(self, raw=None, spec=None, check=lambda: None):
    raw = self.raw() if raw is None else raw
    b.mapped_files(raw, check)
    return b.owned_sqlite_shm(self.spec if spec is None else spec, raw, check)

  def test_01_exact_owned_mapping(self):
    result = self.observed()
    self.assertEqual(result[0]['bytes'], 32768)
    self.assertFalse(result[0]['content_hash_claim'])

  def test_02_vma_count_and_overlap(self):
    raw = b''.join(self.raw(start=4096+i*32768) for i in range(16))
    self.assertEqual(len(self.observed(raw)[0]['vmas']), 16)
    for bad in (raw+self.raw(start=4096+16*32768), self.raw()*2):
      with self.assertRaises(Exception): self.observed(bad)

  def test_03_permissions_offsets_ranges(self):
    for perms in ('r--s', 'rw-p', 'rwxp', 'rwxs', '--xs'):
      with self.assertRaises(Exception): self.observed(self.raw(permissions=perms))
    for changes in ({'offset': 4096}, {'length': 16384}, {'length': 65536}, {'start': 4097}):
      with self.assertRaises(Exception): self.observed(self.raw(**changes))

  def test_04_role_scope_and_runtime_collision(self):
    for change in ({'_server_phase': 'capture'}, {'_server_phase': None},
           {'output_dir': str(self.root/'other')},
           {'hash_inventory': [{'path': str(self.path)}]}):
      with self.assertRaises(Exception): self.observed(spec=dict(self.spec, **change))

  def test_05_kernel_identity_and_deleted(self):
    with self.assertRaises(Exception): self.observed(self.raw(inode=self.path.stat().st_ino+1))
    with self.assertRaises(Exception): self.observed(self.raw(name=str(self.path)+' (deleted)'))

  def test_06_unknown_mapping_still_refused(self):
    deadline = types.SimpleNamespace(clock=lambda: 1., guard_work=240., posthash=240., absolute=300.,
                    stop=types.SimpleNamespace(exists=lambda: False))
    raw = self.raw(name='/not-a-runtime-pin/libunknown.so')
    with mock.patch.object(b, 'bounded_read', return_value=raw):
      with self.assertRaisesRegex(Exception, 'unknown_terminal_mapping'):
        b.terminal_observation(self.spec, deadline)

  def test_07_file_ownership_type_size_links(self):
    original = b.os.fstat
    for attr, value in (('st_uid', os.geteuid()+1), ('st_nlink', 2), ('st_size', 65536),
              ('st_mode', stat.S_IFIFO|0o600), ('st_mode', stat.S_IFREG|0o700)):
      def changed(fd, attr=attr, value=value):
        st = original(fd)
        if not stat.S_ISREG(st.st_mode): return st
        values = {k: getattr(st, k) for k in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_nlink', 'st_size')}
        values[attr] = value
        return types.SimpleNamespace(**values)
      with mock.patch.object(b.os, 'fstat', side_effect=changed):
        with self.assertRaises(Exception): self.observed()

  def test_08_no_follow_ancestor_and_file(self):
    original = b.os.open
    for target in ('tmp', 'continuation', 'data', 'jobs.sqlite3-shm'):
      def denied(path, flags, *args, target=target, **kwargs):
        self.assertTrue(flags & os.O_NOFOLLOW)
        if path == target: raise OSError('symlink refused')
        return original(path, flags, *args, **kwargs)
      with mock.patch.object(b.os, 'open', side_effect=denied):
        with self.assertRaises(OSError): self.observed()

  def test_09_deadline_closes_fds(self):
    opened, closed = [], []
    original_open, original_close = b.os.open, b.os.close
    def tracked(*args, **kwargs):
      fd = original_open(*args, **kwargs); opened.append(fd); return fd
    def close(fd): closed.append(fd); original_close(fd)
    def check():
      if len(opened) == 5: raise RuntimeError('deadline')
    with mock.patch.object(b.os, 'open', side_effect=tracked), mock.patch.object(b.os, 'close', side_effect=close):
      with self.assertRaisesRegex(RuntimeError, 'deadline'): self.observed(check=check)
    self.assertEqual(set(opened), set(closed))

  def test_10_stop_and_accounting_cutoff(self):
    d = types.SimpleNamespace(clock=lambda: 239., guard_work=240., posthash=240., absolute=300.,
                 stop=types.SimpleNamespace(exists=lambda: True))
    with self.assertRaisesRegex(Exception, 'outer_STOP'): b.accounting_check(d)
    d.stop.exists = lambda: False
    d.clock = lambda: 240.
    with self.assertRaisesRegex(Exception, 'mapping_deadline'): b.accounting_check(d)

  def test_11_snapshot_closes_success_and_error(self):
    for failure in (False, True):
      db = mock.Mock()
      marker = ValueError('original SQL error')
      db.execute.side_effect = marker if failure else lambda *a: []
      destination = self.root/('snapshot-error' if failure else 'snapshot-success')
      with mock.patch.object(s.sqlite3, 'connect', return_value=db):
        if failure:
          with self.assertRaises(ValueError) as caught:
            s.snapshot(types.SimpleNamespace(path='unused'), destination, lambda: None)
          self.assertIs(caught.exception, marker)
        else:
          result = s.snapshot(types.SimpleNamespace(path='unused'), destination, lambda: None)
          self.assertLessEqual(result['bytes'], 512*1024)
      db.close.assert_called_once()

  def test_12_inventory_union_conflicts_and_caps(self):
    base = [{'path': '/fixture/'+str(i), 'bytes': 0, 'sha256': '0'*64} for i in range(3791)]
    self.assertEqual(len(b.merge_inventory(base, [base[0]])), 3791)
    with self.assertRaises(Exception): b.merge_inventory(base, [dict(base[0], sha256='1'*64)])
    with self.assertRaises(Exception): b.merge_inventory(base, [base[0]]*129)

  def test_13_constructor_import_and_dispatch_denials(self):
    hooks = []
    spec = {'python': '/pinned/python', '_path': '/pinned/spec', 'output_dir': '/owned/output',
        'runtime_directory_aliases': []}
    with mock.patch.object(b.sys, 'addaudithook', side_effect=hooks.append):
      b.install_zero_inference(spec, types.SimpleNamespace(check=lambda: None), mock.Mock(), 'controller')
    audit = hooks[0]
    for event, args in [('import', ('audio_backend.inference',)), ('import', ('sherpa_onnx',)),
              ('ctypes.dlopen', ('/native.so',)), ('os.fork', ()),
              ('subprocess.Popen', ('python', ['python', '-m', 'audio_backend.runner']))]:
      with self.assertRaises(Exception): audit(event, args)
    with mock.patch.object(b, 'verify_directory_aliases', return_value=[]):
      for phase in ('restart-on', 'restart-off'):
        argv = [spec['python'], '-I', '-B', '-S', str(b.HERE/'bootstrap.py'), '--mode',
            'server', '--spec', spec['_path'], '--', phase, spec['output_dir']+'/'+phase]
        audit('subprocess.Popen', (argv[0], argv))
      with self.assertRaises(Exception): audit('subprocess.Popen', (argv[0], argv))
    fake = types.ModuleType('audio_backend')
    native, resources = types.ModuleType('audio_backend.native_worker'), types.ModuleType('audio_backend.resources')
    native.NativeProcess = type('NativeProcess', (), {})
    resources.AnalyzerProcess = type('AnalyzerProcess', (), {})
    with mock.patch.dict(sys.modules, {'audio_backend': fake, 'audio_backend.native_worker': native,
                     'audio_backend.resources': resources}), \
      mock.patch.object(b.sys, 'addaudithook'):
      b.install_zero_inference(spec, mock.Mock(), mock.Mock(), 'server')
    for cls in (native.NativeProcess, resources.AnalyzerProcess):
      with self.assertRaisesRegex(RuntimeError, 'zero_inference_constructor'): cls()

  def test_14_original_exception_and_cleanup_parity(self):
    old = ast.parse((HERE.parents[1]/'controller/bootstrap.py').read_text())
    new = ast.parse((HERE/'controller/bootstrap.py').read_text())
    for name in ('finalize', 'mapped_files', 'observed_mappings', 'accounting_check', 'LiveProvenance', 'PinnedFinder'):
      a = next(n for n in old.body if getattr(n, 'name', None) == name)
      z = next(n for n in new.body if getattr(n, 'name', None) == name)
      self.assertEqual(ast.dump(a, include_attributes=False), ast.dump(z, include_attributes=False))
    for name in ('common.py', 'ownership.py'):
      self.assertEqual((HERE/'controller'/name).read_bytes(), (HERE.parents[1]/'controller'/name).read_bytes())

  def test_15_profile_and_unapproved_contract_refused(self):
    path = HERE/'continuation.template.json'
    raw = path.read_bytes()
    with mock.patch.dict(os.environ, {'REAL_CAPTURE_SPEC_SHA256': b.sha(raw)}):
      with self.assertRaisesRegex(Exception, 'separate_continuation_GO_required'):
        b.load_spec(path, b.Deadlines())
    contract = json.loads(raw)
    contract['seconds'] = {'outer': 900}
    raw = b.canonical(contract)
    with mock.patch.dict(os.environ, {'REAL_CAPTURE_SPEC_SHA256': b.sha(raw)}), \
         mock.patch.object(b, 'bounded_read', return_value=raw):
      with self.assertRaisesRegex(Exception, 'continuation_resource_profile'):
        b.load_spec(path, b.Deadlines())

  def test_16_clone_shape_and_existing_target_refused(self):
    with self.assertRaises(Exception): c.clone_database({'clone_files': []}, self.root/'new-data', lambda: None)
    rows = [{'path': '/sealed/'+n, 'bytes': z, 'sha256': '0'*64} for n, z in
        [('jobs.sqlite3', 737280), ('jobs.sqlite3-wal', 164832), ('jobs.sqlite3-shm', 32768)]]
    with self.assertRaises(Exception): c.clone_database({'clone_files': rows}, self.output/'data', lambda: None)

  def test_17_fixed_loader_helper_ast(self):
    old = ast.parse((HERE.parents[1]/'actual-guard/actual_guard.py').read_text())
    new = ast.parse((HERE/'controller/bootstrap.py').read_text())
    select = lambda t: next(n for n in t.body if getattr(n, 'name', None) == 'verify_directory_aliases')
    self.assertEqual(ast.dump(select(old), include_attributes=False), ast.dump(select(new), include_attributes=False))

  def test_18_real_sqlite_wal_maps(self):
    db = sqlite3.connect(self.output/'data/jobs.sqlite3')
    try:
      db.execute('PRAGMA journal_mode=WAL')
      db.execute('CREATE TABLE real_fixture (v INTEGER)')
      db.execute('INSERT INTO real_fixture VALUES (1)')
      db.commit()
      raw = b.bounded_read('/proc/self/maps', b.MAP_BYTES)
      b.mapped_files(raw, lambda: None)
      row = b.owned_sqlite_shm(self.spec, raw, lambda: None)
      self.assertEqual(len(row), 1)
      b.atomic_json(self.root/'real-sqlite-mapping.json', row, 4096)
    finally:
      db.close()


  def test_19_required_new_server_mapping_observation(self):
    spec = dict(self.spec, _sha256='1'*64)
    good = {'status': 'COMPLETE', 'pid': 123, 'mode': 'server',
        'spec_sha256': spec['_sha256'], 'cleanup': 'CONFIRMED', 'failures': [],
        'mappings': {'status': 'PATHS_OBSERVED_NOT_REHASHED'}}
    with mock.patch.object(c, 'bounded_read', return_value=b.canonical(good)):
      self.assertEqual(c.require_server_observation(spec, 123, lambda: None)['bytes'], len(b.canonical(good)))
    with mock.patch.object(c, 'bounded_read', side_effect=FileNotFoundError):
      with self.assertRaises(FileNotFoundError): c.require_server_observation(spec, 123, lambda: None)
    for changes in ({'mappings': {'status': 'UNOBSERVED'}}, {'mappings': {}},
            {'pid': 124}, {'spec_sha256': '2'*64}, {'cleanup': 'UNKNOWN'}, {'status': 'INCOMPLETE'}):
      with mock.patch.object(c, 'bounded_read', return_value=b.canonical(dict(good, **changes))):
        with self.assertRaisesRegex(Exception, 'required_new_server_mapping_observation'):
          c.require_server_observation(spec, 123, lambda: None)


if __name__ == '__main__':
  ids = [f'__main__.Cases.{name}' for name in sorted(n for n in dir(Cases) if n.startswith('test_'))]
  expected = json.loads((HERE/'TEST_IDS.json').read_text())['ids']
  if ids != expected: raise RuntimeError('exact fixture denominator')
  result = unittest.TestResult()
  unittest.defaultTestLoader.loadTestsFromTestCase(Cases).run(result)
  summary = {'tests_run': result.testsRun, 'expected': len(ids), 'failures': len(result.failures),
       'errors': len(result.errors), 'passed': result.wasSuccessful(), 'model_calls': 0,
       'details': [str(case)+': '+text[-400:] for case, text in result.failures+result.errors]}
  raw = b.canonical(summary)
  if len(raw) > 8192: raise RuntimeError('fixture result cap')
  with (Path(os.environ['GUARD_RUN_DIRECTORY'])/'continuation-fixtures.json').open('xb') as out:
    out.write(raw)
  print(json.dumps({k: v for k, v in summary.items() if k != 'details'}, sort_keys=True))
  raise SystemExit(0 if result.wasSuccessful() else 1)
