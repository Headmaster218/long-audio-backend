"""Finite, entirely fake-environment tests; no network request or model import."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time
import unittest
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('network_launcher',HERE/'network_launch.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
guard=launcher.load_guard()
class NetworkEnvironment(unittest.TestCase):
    def test_exact_allowlist(self):
        self.assertEqual(guard.PLATFORM_NETWORK_KEYS,('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE'))
    def test_only_present_keys(self):
        self.assertEqual(guard.platform_network({'HTTPS_PROXY':'https://synthetic.invalid:443'},'model_runtime'),{'HTTPS_PROXY':'https://synthetic.invalid:443'})
    def test_unrelated_variables_excluded(self):
        data={'HTTPS_PROXY':'https://synthetic.invalid','PATH':'untrusted','PYTHONPATH':'untrusted','TOKEN':'synthetic-secret','REQUESTS_CA_BUNDLE':'untrusted'}
        self.assertEqual(guard.platform_network(data,'corpus'),{'HTTPS_PROXY':'https://synthetic.invalid'})
        clean=launcher.environment(data,guard)
        self.assertEqual(set(clean),{'PATH','LANG','LC_ALL','PYTHONDONTWRITEBYTECODE','PYTHONNOUSERSITE','HTTPS_PROXY'})
        self.assertEqual(clean['PATH'],'/usr/bin:/bin')
    def test_all_authorized_keys_keep_existing_value(self):
        data={key:'synthetic-value-'+key for key in guard.PLATFORM_NETWORK_KEYS}
        self.assertEqual(guard.platform_network(data,'wheels_env'),data)
    def test_synthetic_stage_has_no_network_environment(self):
        self.assertEqual(guard.platform_network({key:'synthetic' for key in guard.PLATFORM_NETWORK_KEYS},'synthetic'),{})
    def test_strict_value_bounds(self):
        for bad in (None,True,123,'a'*16385,'a\nb','a\rb','a\0b'):
            with self.assertRaisesRegex(guard.Refused,'invalid_platform_network_value'):
                guard.platform_network({'HTTP_PROXY':bad},'model_runtime')
    def test_unknown_stage_rejected(self):
        with self.assertRaisesRegex(guard.Refused,'invalid_network_stage'):
            guard.platform_network({},'other')
    def test_resource_constants_unchanged(self):
        self.assertEqual((guard.MAX_RSS,guard.MAX_AS,guard.MAX_DISK,guard.ORDINARY_FREE_FLOOR,guard.OLD_UNKNOWN_DISK),(256*2**20,2**30,7*2**30,11*2**29,320*2**20))
        self.assertEqual(guard.STAGES,{'synthetic':300,'model_runtime':600,'corpus':1800,'wheels_env':600})
    def test_ownership_helpers_unchanged(self):
        old=ast.parse((HERE/'history/network-v1/acquisition_guard.py').read_text())
        new=ast.parse((HERE/'acquisition_guard.py').read_text())
        olddefs={n.name:n for n in old.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        newdefs={n.name:n for n in new.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        for name,node in olddefs.items():
            if name=='Guard':
                before={n.name:n for n in node.body if isinstance(n,ast.FunctionDef)}
                after={n.name:n for n in newdefs[name].body if isinstance(n,ast.FunctionDef)}
                self.assertEqual(set(before),set(after))
                for method,value in before.items():
                    if method!='dispatch':self.assertEqual(ast.dump(value,include_attributes=False),ast.dump(after[method],include_attributes=False))
            else:self.assertEqual(ast.dump(node,include_attributes=False),ast.dump(newdefs[name],include_attributes=False))
    def test_recording_does_not_include_values(self):
        source=(HERE/'acquisition_guard.py').read_text()
        self.assertIn("self.receipt['platform_network_keys_present'] = sorted(network)",source)
        self.assertIn("self.receipt['platform_network_value_recording'] = 'NONE'",source)
        self.assertNotIn("self.receipt['platform_network_values']",source)
class Results(unittest.TextTestResult):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.rows=[]
    def startTest(self,test):super().startTest(test);self.rows.append({'id':test.id(),'status':'STARTED'})
    def addSuccess(self,test):super().addSuccess(test);self.rows[-1]['status']='PASS'
    def addFailure(self,test,err):super().addFailure(test,err);self.rows[-1]['status']='FAIL'
    def addError(self,test,err):super().addError(test,err);self.rows[-1]['status']='ERROR'
if __name__=='__main__':
    assert time.monotonic()<float(os.environ['GUARD_WORK_END'])
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(NetworkEnvironment)
    fixed=json.loads((HERE/'NETWORK_TEST_IDS.json').read_text())
    ids=[t.id() for t in suite];assert ids==fixed
    result=unittest.TextTestRunner(verbosity=2,resultclass=Results).run(suite)
    success=result.wasSuccessful() and len(result.rows)==len(fixed) and all(x['status']=='PASS' for x in result.rows)
    with (Path(os.environ['GUARD_RUN_DIRECTORY'])/'network-results.json').open('x') as stream:
        json.dump({'test_ids':ids,'cases':result.rows,'successful':success,'network_requests':0,'model_calls':0},stream,indent=2);stream.write('\n')
    raise SystemExit(0 if success else 1)
