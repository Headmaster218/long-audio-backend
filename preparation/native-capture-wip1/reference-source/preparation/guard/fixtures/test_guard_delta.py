"""Finite guard-only fixtures. Runs solely under an accepted outer spec."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import stat
from types import SimpleNamespace
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('acquisition_guard', HERE / 'acquisition_guard.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
RUN = Path(os.environ['GUARD_RUN_DIRECTORY'])


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class GuardDelta(unittest.TestCase):
    def test_stage_deadlines_exact(self):
        for stage, seconds in g.STAGES.items():
            self.assertEqual(g.stage_seconds({'stage':stage,'absolute_seconds':seconds}),
                             (seconds, seconds-60))

    def test_stage_invalid_or_widened(self):
        for value in [None, 'inference', 'corpus ', []]:
            with self.assertRaises((g.Refused, TypeError)):
                g.stage_seconds({'stage':value,'absolute_seconds':1800})
        for seconds in [True, 1800.0, 1801, 1799]:
            with self.assertRaises(g.Refused):
                g.stage_seconds({'stage':'corpus','absolute_seconds':seconds})

    def test_work_includes_cleanup_reserve(self):
        for work in [0, -1, 541, float('inf'), float('nan'), '540', True]:
            with self.assertRaises(g.Refused):
                g.stage_seconds({'stage':'model_runtime','absolute_seconds':600,'work_seconds':work})
        self.assertEqual(g.stage_seconds({'stage':'corpus','absolute_seconds':1800,'work_seconds':1}),
                         (1800, 1))

    def test_free_floor_full_remaining_scope(self):
        self.assertEqual(g.ORDINARY_FREE_FLOOR, 5905580032)
        self.assertEqual(g.required_free(0), 13757317120)
        self.assertEqual(g.required_free(g.MAX_DISK), 6241124352)
        self.assertEqual(g.required_free(12345), 13757317120-12345)

    def test_free_floor_bad_charge(self):
        for charge in [-1, g.MAX_DISK+1, 1.0, True]:
            with self.assertRaises(g.Refused):
                g.required_free(charge)

    def test_read_regular_nofollow_and_cap(self):
        with tempfile.TemporaryDirectory(dir=RUN/'tmp') as td:
            root=Path(td); p=root/'data';p.write_bytes(b'1234');q=root/'alias';q.symlink_to('data')
            self.assertEqual(g.read_regular(p,4,time.monotonic()+2),b'1234')
            for path,cap in [(q,4),(p,3)]:
                with self.assertRaises(g.Refused):g.read_regular(path,cap,time.monotonic()+2)

    def make_scope(self, root):
        for name in g.REQUIRED_ROOT_NAMES:(root/name).mkdir()
        (root/'RECOVERY_KNOWLEDGE_20261011.md').write_text('x')

    def test_scope_new_future_root_charged(self):
        with tempfile.TemporaryDirectory(dir=RUN/'tmp') as td:
            root=Path(td);self.make_scope(root)
            with patch.object(g,'WORKSPACE',root):
                before=g.scratch_bytes(time.monotonic()+2)
                (root/'reconstruction-new-assets').mkdir()
                (root/'reconstruction-new-assets'/'data').write_bytes(b'x'*8192)
                after=g.scratch_bytes(time.monotonic()+2)
                self.assertGreaterEqual(after-before,8192)

    def test_scope_missing_required_fails(self):
        with tempfile.TemporaryDirectory(dir=RUN/'tmp') as td:
            root=Path(td);self.make_scope(root);(root/g.REQUIRED_ROOT_NAMES[0]).rmdir()
            with patch.object(g,'WORKSPACE',root),self.assertRaises(g.Refused):
                g.charged_roots(time.monotonic()+2)

    def test_scope_symlink_root_fails(self):
        with tempfile.TemporaryDirectory(dir=RUN/'tmp') as td:
            root=Path(td);self.make_scope(root);candidate=root/'reconstruction-alias';candidate.mkdir()
            original=Path.lstat
            def fake(path,*args,**kwargs):
                if path==candidate:return SimpleNamespace(st_mode=stat.S_IFLNK)
                return original(path,*args,**kwargs)
            with patch.object(g,'WORKSPACE',root),patch.object(Path,'lstat',fake),self.assertRaises(g.Refused):
                g.charged_roots(time.monotonic()+2)

    def test_walk_failure_is_not_empty_scope(self):
        with patch.object(g,'charged_roots',return_value=[RUN]),patch.object(g.os,'walk',side_effect=PermissionError('fixture')):
            with self.assertRaises(PermissionError):g.scratch_bytes(time.monotonic()+2)

    def test_scope_alias_escape_and_fifo_fail(self):
        # Invalid filesystem states are injected, never materialized where the
        # outer supervisor's honest concurrent scan could encounter them.
        with tempfile.TemporaryDirectory(dir=RUN/'tmp') as td:
            root=Path(td);self.make_scope(root);inside=root/g.REQUIRED_ROOT_NAMES[0]
            candidate=inside/'candidate';candidate.write_bytes(b'x')
            original=Path.lstat
            for mode in [stat.S_IFLNK,stat.S_IFIFO]:
                def fake(path,*args,**kwargs):
                    if path==candidate:return SimpleNamespace(st_mode=mode,st_size=1,st_blocks=1)
                    return original(path,*args,**kwargs)
                with patch.object(g,'WORKSPACE',root),patch.object(Path,'lstat',fake),patch.object(g.os,'readlink',return_value='/escape'),self.assertRaises(g.Refused):
                    g.scratch_bytes(time.monotonic()+2)

    def test_sample_exact_free_boundary(self):
        s=self.make_spec('success');obj=g.Guard(s,'fixture');disk=1024
        available=g.MIN_AVAILABLE;free=g.required_free(disk)
        with patch.object(obj,'census',return_value=[]),patch.object(g,'proc',return_value={'rss':1024}),patch.object(g,'scratch_bytes',return_value=disk),patch.object(g,'host',return_value=(available,free)):
            obj.sample(time.monotonic()+2)
        with patch.object(obj,'census',return_value=[]),patch.object(g,'proc',return_value={'rss':1024}),patch.object(g,'scratch_bytes',return_value=disk),patch.object(g,'host',return_value=(available,free-1)),self.assertRaises(g.Refused):
            obj.sample(time.monotonic()+2)

    def test_sample_exact_aggregate_boundary(self):
        s=self.make_spec('success');obj=g.Guard(s,'fixture')
        for offset in [0,1]:
            disk=g.MAX_DISK-g.RECEIPT_RESERVE+offset
            with patch.object(obj,'census',return_value=[]),patch.object(g,'proc',return_value={'rss':1024}),patch.object(g,'scratch_bytes',return_value=disk),patch.object(g,'host',return_value=(g.MIN_AVAILABLE,g.required_free(disk))):
                if offset:
                    with self.assertRaises(g.Refused):obj.sample(time.monotonic()+2)
                else:obj.sample(time.monotonic()+2)

    def test_owned_helper_ast_parity(self):
        old=ast.parse((HERE/'qualified_guard.snapshot.py').read_text());new=ast.parse((HERE/'acquisition_guard.py').read_text())
        def functions(tree):
            out={}
            for n in tree.body:
                if isinstance(n,ast.FunctionDef):out[n.name]=ast.dump(n,include_attributes=False)
                if isinstance(n,ast.ClassDef):
                    for f in n.body:
                        if isinstance(f,ast.FunctionDef):out[n.name+'.'+f.name]=ast.dump(f,include_attributes=False)
            return out
        a,b=functions(old),functions(new)
        for name in ['read_regular','proc','namespace_snapshot','children','identity','Guard.remember',
                     'Guard.census','Guard.signal_owned','Guard.reap','Guard.cleanup','Guard.run_command']:
            self.assertEqual(a[name],b[name],name)

    def make_spec(self, mode):
        files={str(p):sha(p) for p in [HERE/'fixtures'/'owned_probe.py',HERE/'qualified_guard.snapshot.py']}
        return {'schema':2,'stage':'synthetic','absolute_seconds':300,'work_seconds':15,
                'address_space_cap_bytes':g.MAX_AS,'guard_sha256':sha(HERE/'acquisition_guard.py'),
                'source_root':str(HERE/'fixtures'),'source_files':g.inventory(HERE/'fixtures',time.monotonic()+2),
                'cwd':str(HERE.parent),'external_files':files,
                'runtime_files':{str(Path(sys.executable).resolve()):sha(Path(sys.executable).resolve())},
                'argv':[str(Path(sys.executable).resolve()),'-I','-B','-S',str(HERE/'fixtures'/'owned_probe.py'),mode]}

    def test_spec_rejects_unpinned_entrypoint(self):
        s=self.make_spec('success');s['argv'][4]=str(HERE/'missing.py')
        obj=g.Guard(s,'fixture')
        with self.assertRaises(g.Refused):obj.verify_pin(time.monotonic()+3)

    def test_spec_rejects_cwd_and_runtime(self):
        for key,value in [('cwd',str(HERE)),('runtime_files',{})]:
            s=self.make_spec('success');s[key]=value;obj=g.Guard(s,'fixture')
            with self.assertRaises(g.Refused):obj.verify_pin(time.monotonic()+3)

    def nested(self,mode,expected):
        s=self.make_spec(mode);p=RUN/('nested-'+mode+'.json');p.write_text(json.dumps(s,sort_keys=True)+'\n')
        out=RUN/('nested-'+mode+'.log')
        with out.open('xb') as f:
            child=subprocess.Popen([str(Path(sys.executable).resolve()),'-I','-B','-S',str(HERE/'acquisition_guard.py'),
                                    '--spec',str(p),'--spec-sha256',sha(p)],stdout=f,stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)
            retained={'handle':child,'mode':mode,'original_waited':False}
            HANDLES.append(retained)  # Preserve even if the following wait raises.
            code=child.wait(timeout=25)
            retained['original_waited']=True
        lines=out.read_text().splitlines();terminal=json.loads(lines[-1]);receipt=json.loads(Path(terminal['receipt']).read_text())
        self.assertEqual(receipt['status'],expected)
        self.assertEqual(receipt['cleanup'],'CONFIRMED')
        self.assertEqual(receipt['errors'],[])
        self.assertTrue(receipt['final_source_pin_verified'])
        self.assertLessEqual(receipt['final_child_highwater_plus_supervisor_rss'],g.MAX_RSS)
        self.assertTrue(receipt['caps']['remaining_aggregate_free_reservation'])
        self.assertGreaterEqual(receipt['latest_required_free'],g.MIN_FREE)
        self.assertTrue(receipt['original_handle_waited'])
        self.assertEqual(receipt['unresolved_owned'],[])
        self.assertEqual(receipt['final_direct_child_pids'],[])
        self.assertEqual(code,0 if expected=='PASS' else 1)
        return {'mode':mode,'subprocess_original_waited':True,'returncode':code,'receipt':terminal['receipt'],
                'receipt_sha256':sha(terminal['receipt'])}

    def test_owned_positive_quick_child(self):
        NESTED.append(self.nested('success','PASS'))

    def test_owned_negative_quick_nonzero(self):
        NESTED.append(self.nested('nonzero','FAIL_CLOSED'))


NESTED=[]
HANDLES=[]
if __name__=='__main__':
    expected=json.loads((HERE/'guard-test-ids.json').read_text())
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(GuardDelta)
    actual=[test.id() for test in suite]
    if actual!=expected:raise SystemExit('fixture_inventory_mismatch')
    result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
    report={'expected':expected,'collected':actual,'tests_run':result.testsRun,'failures':len(result.failures),
            'errors':len(result.errors),'skipped':len(result.skipped),'nested_original_waits':NESTED,
            'retained_handle_waits':[{'mode':h['mode'],'original_waited':h['original_waited'],
                                      'returncode':h['handle'].returncode} for h in HANDLES],
            'passed':result.wasSuccessful() and result.testsRun==len(expected) and not result.skipped and
                     len(HANDLES)==2 and all(h['original_waited'] for h in HANDLES)}
    (RUN/'guard-delta-results.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    raise SystemExit(0 if report['passed'] else 1)
