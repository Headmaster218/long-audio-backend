"""Exact stdlib fixture union; no actual acoustic providers or network."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import unittest
HERE=Path(__file__).resolve().parent
PREP=HERE.parent

def check():
    if time.monotonic()>=float(os.environ['GUARD_WORK_END']):raise RuntimeError('fixture deadline')
    if Path(os.environ['GUARD_STOP_PATH']).exists():raise RuntimeError('fixture STOP')

def audit(event,args):
    if event.startswith('socket.') or event in ('os.system',):raise RuntimeError('fixture network/shell forbidden')
    if event=='open' and args and isinstance(args[0],(str,bytes)):
        path=Path(os.fsdecode(args[0])).absolute()
        if any(path.is_relative_to(PREP/x) for x in ('models','vendor','corpus','input','environment')):
            raise RuntimeError('real provider/input access forbidden')

class Result(unittest.TextTestResult):
    def startTest(self,test):
        check();super().startTest(test)
    def addSuccess(self,test):
        super().addSuccess(test);self.rows.append({'id':test.id(),'status':'PASS'})
    def addFailure(self,test,err):
        super().addFailure(test,err);self.rows.append({'id':test.id(),'status':'FAIL'})
    def addError(self,test,err):
        super().addError(test,err);self.rows.append({'id':test.id(),'status':'ERROR'})
    def addSkip(self,test,reason):
        super().addSkip(test,reason);self.rows.append({'id':test.id(),'status':'SKIP'})
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.rows=[]

def main():
    if sys.argv[1:]:raise RuntimeError('no fixture arguments')
    expected=json.loads((HERE/'TEST_IDS.json').read_bytes())
    if len(expected)!=48 or len(set(expected))!=48:raise RuntimeError('fixed fixture inventory')
    sys.addaudithook(audit);check();modules={}
    for name,path in [('controller_fixture',PREP/'controller/test_controller.py'),('admission_fixture',PREP/'storage-proof/test_admission.py')]:
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);modules[name]=module
    suite=unittest.TestSuite()
    for ident in expected:
        name,cls,method=ident.split('.');suite.addTest(getattr(modules[name],cls)(method))
    result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(suite)
    check();qualified=result.wasSuccessful() and [x['id'] for x in result.rows]==expected and all(x['status']=='PASS' for x in result.rows)
    output={'status':'PASS' if qualified else 'FAIL','cases':result.rows,'expected_tests':48,'tests_run':result.testsRun,'actual_models':0,'actual_audio':0,'network_requests':0,'scope':'explicit fake controller producers and finite-shape admission only'}
    path=Path(os.environ['GUARD_RUN_DIRECTORY'])/'controller-admission-fixtures.json'
    with path.open('x') as f:json.dump(output,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    return 0 if qualified else 1
if __name__=='__main__':raise SystemExit(main())
