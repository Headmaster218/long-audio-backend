#!/usr/bin/env python3
"""Closed-config, admission and actual owner-lifecycle fixtures. No model or network."""
import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock
HERE=Path(__file__).resolve().parent

def module(name,file):
    s=importlib.util.spec_from_file_location(name,HERE/file);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
b=module('portable_bootstrap','bootstrap.py')
g=module('portable_guard','acquisition_guard.py')
CONFIG=None
ARGS=None
RUN=None
HANDLES=[]

def setup_guard():
    g.CONFIG=CONFIG;g.BOOTSTRAP=b;g.WORKSPACE=Path(CONFIG['roots']['workspace']);g.CHECKS=Path(CONFIG['roots']['work'])/'evidence'

class Tests(unittest.TestCase):
    def test_01_packet_exact_members(self):self.assertEqual(set(b.verify_packet()['files']),set(b.FILE_CEILINGS)-{'PACKET_MANIFEST.json'})
    def test_02_manifest_totals(self):
        a=b.assets();self.assertEqual(len(a['artifacts']),38);self.assertEqual(sum(x['bytes'] for x in a['artifacts']),5150756491)
    def test_03_no_qwen_weight(self):self.assertFalse(any('qwen' in x['destination'].lower() for x in b.assets()['artifacts']))
    def test_04_deadlines(self):
        self.assertEqual(g.stage_seconds({'stage':'download','absolute_seconds':10800}),(10800,10740))
        self.assertEqual(g.stage_seconds({'stage':'synthetic','absolute_seconds':300}),(300,240))
    def test_05_bad_deadlines(self):
        for v in (True,10800.0,10801,10799):
            with self.assertRaises(g.Refused):g.stage_seconds({'stage':'download','absolute_seconds':v})
    def test_06_work_reserve(self):
        for v in (0,10741,float('nan'),float('inf'),True):
            with self.assertRaises(g.Refused):g.stage_seconds({'stage':'download','absolute_seconds':10800,'work_seconds':v})
    def test_07_unknown_config_key(self):
        c=copy.deepcopy(CONFIG);c['extra']=1
        with self.assertRaises(b.Refused):b.validate_config(c)
    def test_08_boolean_integer_rejected(self):
        c=copy.deepcopy(CONFIG);c['limits']['attempts_per_artifact']=True
        with self.assertRaises(b.Refused):b.validate_config(c)
    def test_09_global_and_category_caps(self):
        self.assertEqual(sum(b.CAPS.values()),9294577664);self.assertEqual(g.MAX_DISK,12884901888)
        self.assertEqual((g.MAX_RSS,g.MAX_AS),(268435456,1073741824))
    def test_10_free_floor_remaining_global(self):
        self.assertEqual(g.required_free(0),18790481920);self.assertEqual(g.required_free(g.MAX_DISK),5905580032)
        with self.assertRaises(g.Refused):g.required_free(True)
    def test_11_platform_network_allowlist(self):
        self.assertEqual(g.platform_network({'HTTPS_PROXY':'https://example.invalid','SECRET':'secret'},'download'),{'HTTPS_PROXY':'https://example.invalid'})
        self.assertEqual(g.platform_network({'HTTPS_PROXY':'https://example.invalid'},'synthetic'),{})
    def test_12_platform_network_invalid_values(self):
        for bad in ('a\nb','a\0b','x'*16385,None,True):
            with self.assertRaises(g.Refused):g.platform_network({'HTTPS_PROXY':bad},'download')
    def test_13_owned_function_ast_parity(self):
        tree=ast.parse((HERE/'acquisition_guard.py').read_text());found={}
        for n in tree.body:
            if isinstance(n,ast.FunctionDef):found[n.name]=n
            if isinstance(n,ast.ClassDef):
                for f in n.body:
                    if isinstance(f,ast.FunctionDef):found[n.name+'.'+f.name]=f
        for name,digest in b.verify_packet()['preserved_ast_sha256']['guard'].items():self.assertEqual(hashlib.sha256(ast.dump(found[name],include_attributes=False).encode()).hexdigest(),digest,name)
    def test_14_no_arbitrary_command(self):
        spec=b.make_spec(CONFIG,ARGS.config,ARGS.config_sha256,'download');obj=g.Guard(spec,'fixture')
        spec['argv'][-1]='changed'
        with self.assertRaises(g.Refused):obj.verify_pin(time.monotonic()+30)
    def test_15_original_scope_counts_future_file(self):
        before=b.resource_usage(CONFIG,time.monotonic()+30)
        p=RUN/'tmp/new-allocation';p.write_bytes(b'x'*8192)
        after=b.resource_usage(CONFIG,time.monotonic()+30)
        self.assertGreaterEqual(after['evidence']-before['evidence'],8192)
    def test_16_stop_is_checked(self):
        a=module('guard_fixture_acquire','acquire.py');root=RUN/'tmp/stop-case';root.mkdir()
        ctx=a.Context(root,time.monotonic()+120,root/'STOP');(root/'STOP').touch()
        with self.assertRaises(a.Refused):ctx.check()
    def test_17_short_source_read_cap(self):
        p=RUN/'tmp/pin-cap';p.write_bytes(b'1234')
        with self.assertRaises(g.Refused):g.read_regular(p,3,time.monotonic()+2)
        self.assertEqual(g.read_regular(p,4,time.monotonic()+2),b'1234')
    def test_18_host_floors_observed(self):
        row=b.admission(CONFIG,time.monotonic()+30)
        self.assertGreaterEqual(row['free_bytes'],row['required_free_bytes'])
        self.assertGreaterEqual(row['available_bytes'],b.LIMITS['minimum_available_bytes'])
    def test_21_late_admission_cannot_restart_clock(self):
        old=b.launch_binding(time.monotonic()-10801,'download')
        with self.assertRaises(b.Refused):b.validate_launch(old,'download')
        start=time.monotonic()-10;bound=b.launch_binding(start,'download')
        self.assertEqual(b.validate_launch(bound,'download')['absolute_end_monotonic'],start+10800)
        self.assertLess(bound['absolute_end_monotonic'],time.monotonic()+10800)
    def test_22_runtime_ancestors_and_optional_proof_are_charged(self):
        self.assertEqual(Path(CONFIG['roots']['runtime_container']).parent,Path(CONFIG['roots']['workspace']))
        self.assertTrue(Path(CONFIG['roots']['runtime']).is_relative_to(Path(CONFIG['roots']['runtime_container'])))
        tree=(HERE/'bootstrap.py').read_text()
        self.assertIn("('source_recovery','packet','work','runtime_container','proof_source')",tree)
        self.assertIn("Path(roots['proof_source'])",tree)
    def test_23_receipts_follow_real_configured_context(self):
        a=module('routing_acquire','acquire.py');fixtures=module('routing_pacing','test_pacing.py')
        work=RUN/'tmp/routing-work';run=work/'evidence/runs/run-fixture';run.mkdir(parents=True)
        end=time.monotonic()+120;ctx,root=a.configured_context({'roots':{'work':str(work)}},run,end,end-60,run/'STOP')
        data=b'fixture-body';item={'id':'routing','url':'https://files.pythonhosted.org/fixture','destination':'assets/wheels/body.whl','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        target=a.download(item,ctx,fixtures.Opener(fixtures.Response(data,[len(data)])))
        a.write_json(run/'download-result.json',{'status':'FIXTURE'},ctx)
        self.assertEqual(target,root/'wheels/body.whl')
        self.assertTrue((run/'receipts/download-routing.json').is_file())
        self.assertTrue((run/'download-result.json').is_file())
        self.assertFalse((root/'receipts').exists())
    def test_24_promotion_race_rescans_complete_category(self):
        c=copy.deepcopy(CONFIG);work=RUN/'tmp/promotion-work';tmp=work/'assets/tmp';tmp.mkdir(parents=True)
        c['roots']['work']=str(work);part=tmp/('a'*64+'.part');target=work/'assets/promoted.bin';part.write_bytes(b'x'*8192)
        original=Path.lstat;triggered=[]
        def raced(path,*args,**kwargs):
            if path==part and not triggered:
                triggered.append(True);part.rename(target);raise FileNotFoundError(str(part))
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'lstat',raced):row=b.resource_usage(c,time.monotonic()+30)
        self.assertEqual(triggered,[True]);self.assertGreaterEqual(row['acquisition'],8192)
        self.assertTrue(target.is_file());self.assertFalse(part.exists())
    def nested(self,mode,wanted):
        log=RUN/('nested-'+mode+'.log')
        with log.open('xb') as f:
            child=subprocess.Popen([CONFIG['host']['python'],'-I','-B','-S',str(HERE/'guard_driver.py'),
              '--config',ARGS.config,'--config-sha256',ARGS.config_sha256,'--phase','owned-'+mode],
              stdout=f,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
            row={'original':child,'mode':mode,'waited':False};HANDLES.append(row)
            code=child.wait(timeout=60);row['waited']=True
        lines=log.read_text().splitlines();self.assertTrue(lines)
        result=json.loads(lines[-1]);receipt=b.json_file(result['receipt'])
        self.assertEqual(receipt['status'],wanted);self.assertEqual(receipt['cleanup'],'CONFIRMED')
        self.assertTrue(receipt['original_handle_waited']);self.assertTrue(receipt['final_source_pin_verified'])
        self.assertEqual(receipt['errors'],[]);self.assertEqual(receipt['unresolved_owned'],[])
        self.assertEqual(receipt['final_direct_child_pids'],[]);self.assertEqual(code,0 if wanted=='PASS' else 1)
    def test_19_owned_success_original_waited(self):self.nested('success','PASS')
    def test_20_owned_nonzero_original_waited(self):self.nested('nonzero','FAIL_CLOSED')

def main():
    global CONFIG,ARGS,RUN
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--config-sha256',required=True)
    p.add_argument('--probe-mode',choices=('success','nonzero'));ARGS=p.parse_args()
    b.verify_packet();CONFIG=b.load_config(ARGS.config,ARGS.config_sha256);RUN=Path(os.environ['GUARD_RUN_DIRECTORY']);setup_guard()
    if ARGS.probe_mode=='nonzero':return 3
    if ARGS.probe_mode=='success':
        child=subprocess.Popen([sys.executable,'-I','-B','-S','-c','raise SystemExit(0)'])
        return child.wait(timeout=5)
    # HTTP is never needed by these model-free fixtures.
    import urllib.request
    with mock.patch.object(urllib.request.OpenerDirector,'open',side_effect=AssertionError('network_forbidden')):
        result=unittest.TestResult();unittest.defaultTestLoader.loadTestsFromTestCase(Tests).run(result)
    good=result.wasSuccessful() and result.testsRun==24 and not result.skipped and len(HANDLES)==2 and all(x['waited'] for x in HANDLES)
    value={'schema':'portable-guard-fixtures-v1','status':'PASS' if good else 'FAIL','tests_run':result.testsRun,
      'failures':[t._testMethodName for t,e in result.failures],'errors':[t._testMethodName for t,e in result.errors],
      'skipped':len(result.skipped),'original_child_waits':[{'mode':r['mode'],'waited':r['waited'],'returncode':r['original'].returncode} for r in HANDLES],
      'network_requests':0,'model_loads':0,'model_calls':0}
    b.write_new(RUN/'guard-fixtures.json',value,32768)
    return 0 if good else 1

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as e:print(json.dumps({'status':'FIXTURE_ERROR','error_type':type(e).__name__}));raise SystemExit(2)
