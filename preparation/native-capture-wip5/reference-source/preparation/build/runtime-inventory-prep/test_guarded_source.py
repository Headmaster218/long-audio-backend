"""Prepared AST/metadata-only cases; never imports worker or launches a process."""
import ast
import hashlib
import json
from pathlib import Path
import unittest
HERE=Path(__file__).resolve().parent
PREP=HERE.parent.parent

class StaticContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=(HERE/'inspect_runtime_guarded.py').read_text()
        cls.tree=ast.parse(cls.source)
        cls.funcs={n.name:n for n in cls.tree.body if isinstance(n,ast.FunctionDef)}
    def test_allowlist_unchanged(self):
        raw=(HERE/'allowlist.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'0203fc9da45c45ee4cd7abc44289411518c41b9a71781b2bed72141c1889c7aa')
        d=json.loads(raw)
        self.assertEqual((len(d['candidates']),len(d['aliases']),sum(p['bytes'] for p in d['candidates'])),(63,17,183119537))
    def test_original_wait_cleanup_AST(self):
        original=ast.parse((PREP/'installation/complete_offline.v8.py').read_text())
        old=next(n for n in original.body if isinstance(n,ast.FunctionDef) and n.name=='child')
        new=self.funcs['child']
        get=lambda f:next(n for n in ast.walk(f) if isinstance(n,ast.Try) and n.handlers and isinstance(n.handlers[0].type,ast.Name) and n.handlers[0].type.id=='BaseException')
        a,b=get(old),get(new)
        self.assertEqual(ast.dump(ast.Module(body=a.handlers,type_ignores=[])),ast.dump(ast.Module(body=b.handlers,type_ignores=[])))
        self.assertEqual(ast.dump(ast.Module(body=a.finalbody,type_ignores=[])),ast.dump(ast.Module(body=b.finalbody,type_ignores=[])))
        assignments=[(i,n) for i,n in enumerate(b.body) if isinstance(n,ast.Assign)]
        i=next(i for i,n in assignments if any(isinstance(t,ast.Name) and t.id=='p' for t in n.targets))
        self.assertEqual(ast.unparse(b.body[i+1]),'ORIGINALS.append(p)')
        self.assertEqual(ast.unparse(b.body[i+2]),'pidfd = os.pidfd_open(p.pid, 0)')
    def test_no_new_supervisor(self):
        calls=[ast.unparse(n.func) for n in ast.walk(self.tree) if isinstance(n,ast.Call)]
        self.assertEqual(calls.count('subprocess.Popen'),1)
        for forbidden in ['os._exit','signal.setitimer','os.kill','os.killpg','subprocess.run','subprocess.check_output']:
            self.assertNotIn(forbidden,calls)
    def test_deadline_and_output_contract(self):
        self.assertIn('INSPECTION_END=min(START+180.0,float(os.environ[\'GUARD_WORK_END\']))',self.source)
        self.assertIn("env['GUARD_WORK_END']=str(INSPECTION_END)",self.source)
        self.assertIn('LOG_CAP=8192',self.source)
        self.assertIn('REPORT_CAP=384*1024',self.source)
        self.assertLessEqual(63*8192+384*1024+4096,1048576)
        self.assertEqual(458752+1048576,1507328)
        self.assertEqual(458752+913408,1372160)
    def test_category_route(self):
        self.assertIn('return g.category(checker=check)',self.source)
        self.assertIn("BUILD=c.safe_path(PREP,'build/runtime-inventory-'+run.name)",self.source)
        self.assertIn('ca523151480bdab022df21a42c492f0c6ca7c54a58a1f92d6bf4cd4201f70102',self.source)
        self.assertIn('len(pins)==9',self.source)
        self.assertIn("qualification=bind_geometry_qualification()",self.source)
    def test_spec_guard_rules(self):
        d=json.loads((HERE/'guarded.synthetic.spec.json').read_text())
        self.assertEqual((d['schema'],d['stage'],d['absolute_seconds'],d['work_seconds']),(2,'synthetic',300,240))
        self.assertEqual(d['argv'][1:4],['-I','-B','-S'])
        self.assertEqual(set(d['runtime_files']),{d['argv'][0]})
        self.assertIn(d['argv'][4],d['external_files'])
        self.assertEqual(d['source_root'],str(PREP/'guard/fixtures'))

if __name__=='__main__':unittest.main()
