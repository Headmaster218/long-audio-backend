"""Four mocked boundaries; no assets, installation, network or child process."""
import importlib.util
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('category_common',HERE/'common.py')
c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
import os
import unittest
from unittest.mock import patch

class Boundaries(unittest.TestCase):
    def case(self,group,amount,accepted):
        with patch.dict(c._EXCLUSION_PROOFS,{},clear=True),patch.object(c,'scan',return_value=({group+'/fixture':amount},amount,1)):
            if accepted:
                result=c.category(checker=lambda:None)
                self.assertEqual(result['logical_groups'][group],amount)
                self.assertEqual(result['cap_bytes'],512*2**20)
            else:
                with self.assertRaises(c.Refused):c.category(checker=lambda:None)
    def test_bookkeeping_exact(self):self.case('bookkeeping',7340032,True)
    def test_bookkeeping_one_over(self):self.case('bookkeeping',7340033,False)
    def test_build_exact(self):self.case('build',15728640,True)
    def test_build_one_over(self):self.case('build',15728641,False)

def main():
    c.check();suite=unittest.defaultTestLoader.loadTestsFromTestCase(Boundaries)
    ids=[t.id() for t in suite]
    expected=['__main__.Boundaries.'+x for x in ('test_bookkeeping_exact','test_bookkeeping_one_over','test_build_exact','test_build_one_over')]
    c.require(ids==expected,'fixed boundary case IDs')
    result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
    ok=result.wasSuccessful() and result.testsRun==4 and not result.skipped
    c.write_json(Path(os.environ['GUARD_RUN_DIRECTORY'])/'category-delta.v10.result.json',
        {'status':'PASS' if ok else 'FAIL','tests':result.testsRun,'ids':ids,
         'network':0,'processes':0,'models':0,'category_cap_bytes':536870912})
    return 0 if ok else 1
if __name__=='__main__':raise SystemExit(c.cli_boundary(main))
