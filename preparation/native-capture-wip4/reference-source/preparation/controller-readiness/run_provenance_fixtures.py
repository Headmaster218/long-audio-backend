"""Exact historical 48 plus frozen provenance delta; no providers or assets."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
HERE=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module

def main():
    if sys.argv[1:]:raise RuntimeError('no fixture arguments')
    prior=json.loads((HERE/'TEST_IDS.json').read_bytes())
    new=json.loads((HERE/'PROVENANCE_TEST_IDS.json').read_bytes())
    expected=prior+new
    if len(prior)!=48 or len(new)!=43 or len(expected)!=91 or len(set(expected))!=91:
        raise RuntimeError('fixed 48 plus 43 fixture inventory')
    old=load('historical_fixture_runner',HERE/'run_fixtures.py')
    sys.addaudithook(old.audit);old.check()
    modules={name:load(name,path) for name,path in (
        ('controller_fixture',HERE.parent/'controller/test_controller.py'),
        ('admission_fixture',HERE.parent/'storage-proof/test_admission.py'),
        ('provenance_fixture',HERE/'test_provenance.py'))}
    suite=unittest.TestSuite(getattr(modules[name],cls)(method) for name,cls,method in
                             (ident.split('.') for ident in expected))
    result=unittest.TextTestRunner(verbosity=2,resultclass=old.Result).run(suite)
    old.check()
    ok=result.wasSuccessful() and result.testsRun==91 and [x['id'] for x in result.rows]==expected and all(
        x['status']=='PASS' for x in result.rows)
    output={'status':'PASS' if ok else 'FAIL','expected_tests':91,'historical_tests':48,'new_tests':43,
            'tests_run':result.testsRun,'cases':result.rows,'actual_models':0,'actual_audio':0,'network_requests':0}
    path=Path(os.environ['GUARD_RUN_DIRECTORY'])/'controller-provenance-fixtures.json'
    with path.open('x') as stream:
        json.dump(output,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    return 0 if ok else 1
if __name__=='__main__':raise SystemExit(main())
