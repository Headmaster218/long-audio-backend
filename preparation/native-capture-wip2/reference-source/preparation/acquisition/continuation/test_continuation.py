#!/usr/bin/env python3
"""Tiny pure continuation fixtures; no downloads or real asset verification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE=Path(__file__).absolute().parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result

c=module('continuation_under_test',HERE/'continue_models.py')
launcher=module('continuation_launch_under_test',HERE/'launch.py')

class Clock:
    def __call__(self):return 1.

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a=c.load_acquire()
        cls.outer=cls.a.context_from_env(str(c.PREP))
        cls.manifest=cls.a.small_json(HERE.parent/'ASSET_MANIFEST.json',c.MANIFEST_SHA)
        cls.base=cls.outer.temp_root/'synthetic-continuation';cls.base.mkdir()
    def setUp(self):
        self.outer.check();self.temp=tempfile.TemporaryDirectory(dir=self.base)
        self.workspace=Path(self.temp.name);self.root=self.workspace/'prep';self.root.mkdir()
        self.ctx=self.a.Context(self.root,1000.,self.root/'STOP',Clock(),lambda n:None)
        self.ctx.run_directory=self.root/'new-run';self.ctx.run_directory.mkdir()
        self.ctx.temp_root=self.ctx.run_directory/'tmp'
        self.terminal={'pins':[]}
        paths=['models/speaker.onnx','old-run/archive.part']+['old-run/evidence-'+str(i) for i in range(7)]
        for index,name in enumerate(paths):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
            data=('tiny-fixture-'+str(index)).encode();path.write_bytes(data)
            self.terminal['pins'].append({'path':str(path.relative_to(self.workspace)),
                'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    def tearDown(self):
        self.temp.cleanup();self.outer.check()
    def receipt(self):return json.loads((self.ctx.run_directory/'continuation-result.json').read_text())
    def valid_spec(self):
        return {'stage':launcher.STAGE,'absolute_seconds':2400,'work_seconds':2340,
                'guard_sha256':launcher.GUARD_SHA,
                'argv':['python','-I','-B','-S',str(HERE/'continue_models.py')],
                'external_files':{str(HERE/'launch.py'):hashlib.sha256((HERE/'launch.py').read_bytes()).hexdigest()}}
    def test_exact_five_artifact_filter_preserves_full_verify(self):
        before=json.dumps(self.manifest,sort_keys=True)
        filtered=c.exact_filtered_manifest(self.a,self.manifest)
        self.assertEqual(tuple(x['id'] for x in filtered['artifacts']),c.REMAINING_IDS)
        self.assertEqual(sum(x['bytes'] for x in filtered['artifacts']),c.NEW_BYTES)
        self.assertEqual(filtered['mandatory_runtime_files'],self.manifest['mandatory_runtime_files'])
        self.assertTrue(any(x.get('path')=='models/speaker.onnx' for x in filtered['mandatory_runtime_files']))
        self.assertEqual(json.dumps(self.manifest,sort_keys=True),before)
    def test_wrong_reused_hash_rejected(self):
        (self.root/'models/speaker.onnx').write_bytes(b'wrong-content!')
        with self.assertRaises(self.a.Refused):c.prior_pins(self.a,self.terminal,self.workspace,self.ctx)
    def test_changed_old_part_rejected(self):
        path=self.root/'old-run/archive.part';path.write_bytes(b'x'*path.stat().st_size)
        with self.assertRaises(self.a.Refused):c.prior_pins(self.a,self.terminal,self.workspace,self.ctx)
    def test_all_nine_prior_files_verified(self):
        result=c.prior_pins(self.a,self.terminal,self.workspace,self.ctx)
        self.assertEqual(len(result),9)
        self.assertEqual([x['sha256'] for x in result],[x['sha256'] for x in self.terminal['pins']])
    def test_new_destination_collision_blocks_runner(self):
        filtered=c.exact_filtered_manifest(self.a,self.manifest)
        target=self.root/filtered['artifacts'][0]['destination'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'keep')
        calls=[]
        with self.assertRaises(self.a.Refused):c.perform(self.a,self.ctx,self.manifest,self.terminal,self.workspace,
            runner=lambda *args:calls.append(args))
        self.assertEqual(calls,[]);self.assertEqual(target.read_bytes(),b'keep')
        self.assertTrue(self.receipt()['prior_after_verified'])
    def test_extracted_tree_collision_rejected(self):
        filtered=c.exact_filtered_manifest(self.a,self.manifest)
        spec=filtered['archives']['zipformer-small-audioset']
        (self.root/spec['output']/spec['root']).mkdir(parents=True)
        with self.assertRaises(self.a.Refused):c.missing_destinations(self.a,filtered,self.ctx)
    def test_final_prior_pin_change_fails_and_records(self):
        calls=[]
        def fake_runner(stage,filtered,ctx):
            calls.append(stage)
            path=self.root/'old-run/archive.part';path.write_bytes(b'x'*path.stat().st_size)
        with self.assertRaises(self.a.Refused):c.perform(self.a,self.ctx,self.manifest,self.terminal,self.workspace,fake_runner)
        report=self.receipt()
        self.assertEqual(calls,['model_runtime']);self.assertEqual(report['status'],'FAILED')
        self.assertTrue(report['prior_before_verified']);self.assertFalse(report['prior_after_verified'])
        self.assertIn('prior_after_failure',report)
    def test_success_receipt_new_and_reused_scope(self):
        calls=[]
        original={x['path']:(self.workspace/x['path']).read_bytes() for x in self.terminal['pins']}
        report=c.perform(self.a,self.ctx,self.manifest,self.terminal,self.workspace,
                         runner=lambda *args:calls.append(args))
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],'model_runtime')
        self.assertEqual(len(calls[0][1]['artifacts']),5)
        self.assertEqual(report['new_expected_bytes'],387737866)
        self.assertEqual(report['reused_expected_bytes'],39593761)
        self.assertEqual(report['old_incomplete_part_retained_bytes'],96041420)
        self.assertEqual(report['conservative_headroom_bytes'],390534058)
        self.assertTrue(report['prior_before_verified'] and report['prior_after_verified'])
        self.assertEqual(self.ctx.deadline,1000.)
        self.assertEqual(original,{x['path']:(self.workspace/x['path']).read_bytes() for x in self.terminal['pins']})
    def test_duplicate_prior_pin_rejected(self):
        self.terminal['pins'][-1]=self.terminal['pins'][0]
        with self.assertRaises(self.a.Refused):c.prior_pins(self.a,self.terminal,self.workspace,self.ctx)
    def test_launcher_load_and_closed_stage(self):
        guard=launcher.load_guard();self.assertEqual(guard.STAGES,launcher.OLD_STAGES)
        launcher.extend(guard);self.assertEqual(guard.STAGES[launcher.STAGE],2400)
        launcher.validate_spec(guard,self.valid_spec())
    def test_launcher_wrong_prior_stage_map(self):
        guard=launcher.load_guard();guard.STAGES={'synthetic':1}
        with self.assertRaises(guard.Refused):launcher.extend(guard)
    def test_launcher_wrong_absolute_budget(self):
        guard=launcher.load_guard();launcher.extend(guard);spec=self.valid_spec();spec['absolute_seconds']=2401
        with self.assertRaises(guard.Refused):launcher.validate_spec(guard,spec)
    def test_launcher_wrong_work_budget(self):
        guard=launcher.load_guard();launcher.extend(guard);spec=self.valid_spec();spec['work_seconds']=2341
        with self.assertRaises(guard.Refused):launcher.validate_spec(guard,spec)
    def test_launcher_wrong_entrypoint(self):
        guard=launcher.load_guard();launcher.extend(guard);spec=self.valid_spec();spec['argv'][4]='/other/entry.py'
        with self.assertRaises(guard.Refused):launcher.validate_spec(guard,spec)
    def test_launcher_wrong_stage_or_adapter_pin(self):
        guard=launcher.load_guard();launcher.extend(guard)
        spec=self.valid_spec();spec['stage']='model_runtime'
        with self.assertRaises(guard.Refused):launcher.validate_spec(guard,spec)
        spec=self.valid_spec();spec['external_files'][str(HERE/'launch.py')]='0'*64
        with self.assertRaises(guard.Refused):launcher.validate_spec(guard,spec)

class RecordedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.cases=[];self.current={}
    def startTest(self,test):
        super().startTest(test);row={'id':test.id(),'status':'STARTED'}
        self.cases.append(row);self.current[test.id()]=row
    def addSuccess(self,test):
        super().addSuccess(test);self.current[test.id()]['status']='PASS'
    def addFailure(self,test,err):
        super().addFailure(test,err);self.current[test.id()].update(status='FAIL',failure=Tests.a.failure_summary(err[1]))
    def addError(self,test,err):
        super().addError(test,err)
        self.current.setdefault(test.id(),{'id':test.id()}).update(status='ERROR',failure=Tests.a.failure_summary(err[1]))


def main(acquire):
    acquire.require(__import__('sys').argv[1:]==[],'no mutable synthetic arguments')
    outer=acquire.context_from_env(str(c.PREP))
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
    expected=json.loads((HERE/'TEST_IDS.json').read_text())['test_ids']
    actual=[test.id() for test in suite]
    acquire.require(actual==expected,'continuation test IDs changed')
    result=unittest.TextTestRunner(verbosity=2,resultclass=RecordedResult).run(suite)
    exact=len(result.cases)==len(expected) and [x['id'] for x in result.cases]==expected and all(x['status']=='PASS' for x in result.cases)
    acquire.write_json(outer.run_directory/'continuation-synthetic-result.json',{
        'tests_run':result.testsRun,'expected_test_ids':expected,'collected_test_ids':actual,'cases':result.cases,
        'failures':len(result.failures),'errors':len(result.errors),'successful':result.wasSuccessful() and exact,
        'network_requests':0,'real_asset_reads':0,'model_calls':0},outer,cleanup=True)
    return 0 if result.wasSuccessful() and exact else 1

if __name__=='__main__':
    acquire=c.load_acquire()
    raise SystemExit(acquire.cli_boundary(lambda:main(acquire)))
