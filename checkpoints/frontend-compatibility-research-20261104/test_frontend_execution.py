"""Model-free wrapper and execution boundary tests. No real model is opened."""
import ctypes
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
import sys
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('frontend_experiment', ROOT/'frontend_experiment.py')
h = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(h)


class FakeFunction:
    def __init__(self, fn): self.fn = fn
    def __call__(self, *args): return self.fn(*args)


class FakeLibrary:
    def __init__(self, fail_open=False, fail_run=False):
        self.opens = 0; self.runs = 0; self.closes = 0
        def opened(*args):
            self.opens += 1
            return 0 if fail_open else 123
        def run(handle, features, frames, output, error, cap):
            self.runs += 1
            if fail_run: return -1
            for i in range(512): output[i] = 1.0 if i == 0 else 0.0
            return 0
        def closed(handle): self.closes += 1
        self.bridge_open = FakeFunction(opened)
        self.bridge_run = FakeFunction(run)
        self.bridge_close = FakeFunction(closed)


class ExecutionTests(unittest.TestCase):
    def fixture(self, directory):
        p = Path(directory)/'fake-artifact'; p.write_bytes(b'model-free fake, not a model')
        pin = h.pinned(p)
        return {'bridge': pin, 'library': pin, 'model': pin}

    def test_bridge_ctypes_contract_and_output_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = FakeLibrary()
            with patch.object(h.ctypes, 'CDLL', return_value=library):
                bridge = h.CBridge(self.fixture(tmp))
                self.assertEqual(library.bridge_run.restype, ctypes.c_int)
                self.assertEqual(len(library.bridge_run.argtypes), 6)
                self.assertEqual(library.bridge_open.restype, ctypes.c_void_p)
                for frames in (198, 200):
                    out = bridge.run(np.zeros((frames,80),dtype=np.float32))
                    self.assertEqual(out.shape,(512,)); self.assertEqual(out.dtype,np.float32)
                    self.assertEqual(out[0],1.0)
                bridge.close(); bridge.close()
            self.assertEqual((library.opens,library.runs,library.closes),(1,2,1))

    def test_bridge_rejects_bad_buffers_before_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = FakeLibrary()
            with patch.object(h.ctypes, 'CDLL', return_value=library):
                bridge = h.CBridge(self.fixture(tmp))
                readonly = np.zeros((200,80),dtype=np.float32); readonly.flags.writeable=False
                bad = [np.zeros((200,80),dtype=np.float64),np.zeros((199,80),dtype=np.float32),
                       np.zeros((200,79),dtype=np.float32),np.zeros((200,80),dtype=np.float32,order='F'),
                       np.full((200,80),np.nan,dtype=np.float32),readonly,[[0.0]]]
                for value in bad:
                    with self.assertRaises(ValueError): bridge.run(value)
                bridge.close()
                with self.assertRaises(ValueError): bridge.run(np.zeros((200,80),dtype=np.float32))
            self.assertEqual(library.runs,0)

    def test_open_and_run_errors_are_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            frozen=self.fixture(tmp); library=FakeLibrary(fail_open=True)
            with patch.object(h.ctypes,'CDLL',return_value=library):
                with self.assertRaises(RuntimeError): h.CBridge(frozen)
            self.assertEqual(library.opens,1)
            library=FakeLibrary(fail_run=True)
            with patch.object(h.ctypes,'CDLL',return_value=library):
                bridge=h.CBridge(frozen)
                try:
                    with self.assertRaises(RuntimeError): bridge.run(np.zeros((200,80),dtype=np.float32))
                finally: bridge.close()
            self.assertEqual((library.opens,library.runs,library.closes),(1,1,1))

    def test_artifact_mismatch_prevents_library_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            frozen=self.fixture(tmp); Path(frozen['model']['path']).write_bytes(b'changed')
            with patch.object(h.ctypes,'CDLL') as load:
                with self.assertRaises(ValueError): h.CBridge(frozen)
                load.assert_not_called()

    def test_numeric_feature_gate_and_grid_guard(self):
        a=np.ones((200,80),dtype=np.float32)
        self.assertTrue(h.feature_delta(a,a.copy(),True)['passed'])
        b=a.copy();b[0,0]+=np.float32(1e-4)
        self.assertFalse(h.feature_delta(a,b,True)['passed'])
        with self.assertRaises(ValueError):h.feature_delta(a,a[:198],True)

    def test_cosine_uses_both_norms_and_fixed_bounds(self):
        a=np.zeros(512,dtype=np.float32);a[0]=1.000001
        out=h.vector_delta(a,a.copy())
        self.assertEqual(out['cosine_gap'],0.0);self.assertTrue(out['passed'])
        b=a.copy();b[1]=1e-3
        self.assertFalse(h.vector_delta(a,b)['passed'])
        self.assertFalse(h.vector_delta(a,b)['bitwise_equal'])

    def test_signed_zero_is_not_reported_bitwise_equal(self):
        a=np.zeros(512,dtype=np.float32);a[0]=1.0
        b=a.copy();b[1]=-0.0
        self.assertFalse(h.vector_delta(a,b)['bitwise_equal'])
        self.assertTrue(h.vector_delta(a,b)['passed'])
        x=np.zeros((200,80),dtype=np.float32);y=x.copy();y[0,0]=-0.0
        self.assertFalse(h.feature_delta(x,y,True)['bitwise_equal'])
        self.assertTrue(h.feature_delta(x,y,True)['passed'])

    def test_output_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'link').symlink_to(ROOT/'ort_bridge.c')
            with self.assertRaises(ValueError):h.output_bytes(root)

    def test_decision_bridge_requires_complete_same_ordered_decisions(self):
        rows=[{'ordinal':i,'winner':'x','status':'unknown_below_threshold','emitted':None}
              for i in range(24)]
        self.assertTrue(h.bridge_decisions(rows,list(rows)))
        self.assertFalse(h.bridge_decisions(rows,rows[:-1]))
        other=[dict(x) for x in rows];other[-1]['winner']='y'
        self.assertFalse(h.bridge_decisions(rows,other))

    def test_native_worker_source_does_not_import_knf(self):
        import inspect
        worker=inspect.getsource(h.worker)
        self.assertNotIn('import kaldi_native_fbank',worker)
        self.assertIn("if args.stage == 'bridge':",worker)

    def test_supervisor_decision_failure_blocks_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);freeze=root/'freeze.json';out=root/'output'
            h.write_json(freeze,{'runtime':sys.prefix,'limits':h.LIMITS,'python':sys.executable,
                                'source':{'path':str(ROOT/'frontend_experiment.py')},'affinity_cpus':[0,1]})
            launched=[]
            class FakeChild:
                pid=987654321
                def __init__(self,argv,**kwargs):
                    stage=argv[argv.index('--stage')+1];launched.append(stage)
                    folder=Path(argv[argv.index('--output')+1]);folder.mkdir()
                    h.write_json(folder/'result.json',{'status':'passed','calls_attempted':40,
                                 'calls_completed':40,'sessions_attempted':1})
                    rows=[{'ordinal':i,'winner':'x','status':'unknown_below_threshold','emitted':None}
                          for i in range(24)]
                    if stage=='bridge':rows[-1]['winner']='y'
                    h.write_json(folder/'predictions.json',rows)
                    (folder/'vectors.npz').write_bytes(b'model-free fake vectors')
                    if stage=='bridge':(folder/'features.npz').write_bytes(b'model-free fake features')
                def poll(self):return 0
                def wait(self,**kwargs):return 0
            args=types.SimpleNamespace(freeze=str(freeze),freeze_sha=h.digest_file(freeze),output=str(out))
            with patch.object(h,'verify_inputs'),patch.object(h,'available_memory',return_value=10*1024**3),\
                 patch.object(h.subprocess,'Popen',FakeChild),patch('psutil.Process',return_value=object()),\
                 patch('shutil.disk_usage',return_value=types.SimpleNamespace(free=10*1024**3)):
                self.assertEqual(h.run(args),1)
            self.assertEqual(launched,['native','bridge'])
            self.assertFalse((out/'PREDICTION_SEAL.json').exists())
            self.assertFalse((out/'SUMMARY.json').exists())
            self.assertFalse(h.read_json(out/'BRIDGE_GATE.json')['passed'])
            terminal=h.read_json(out/'TERMINAL.json')
            self.assertTrue(all(s['positive_original_wait'] for s in terminal['stages']))

    def test_supervisor_cpu_budget_stops_and_waits_without_next_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);freeze=root/'freeze.json';out=root/'output'
            h.write_json(freeze,{'runtime':sys.prefix,'limits':h.LIMITS,'python':sys.executable,
                                'source':{'path':str(ROOT/'frontend_experiment.py')},'affinity_cpus':[0,1]})
            launched=[];killed=[];waited=[];state={'code':None}
            class FakeChild:
                pid=987654321
                def __init__(self,argv,**kwargs):launched.append(argv[argv.index('--stage')+1])
                def poll(self):return state['code']
                def wait(self,**kwargs):waited.append(True);return state['code']
            class FakeProcess:
                def children(self,**kwargs):return []
                def cpu_times(self):return types.SimpleNamespace(user=601.,system=0.)
                def memory_info(self):return types.SimpleNamespace(rss=1024)
            def kill(pid,sig):
                if sig==0:
                    if state['code'] is None:return None
                    raise ProcessLookupError()
                killed.append(sig);state['code']=-15
            args=types.SimpleNamespace(freeze=str(freeze),freeze_sha=h.digest_file(freeze),output=str(out))
            with patch.object(h,'verify_inputs'),patch.object(h,'available_memory',return_value=10*1024**3),\
                 patch.object(h.subprocess,'Popen',FakeChild),patch('psutil.Process',return_value=FakeProcess()),\
                 patch('shutil.disk_usage',return_value=types.SimpleNamespace(free=10*1024**3)),\
                 patch.object(h.os,'killpg',side_effect=kill):
                self.assertEqual(h.run(args),1)
            self.assertEqual(launched,['native']);self.assertTrue(killed);self.assertTrue(waited)
            self.assertIn('aggregate CPU budget exceeded',h.read_json(out/'TERMINAL.json')['reason']['message'])

    def test_exited_leader_still_cleans_owned_group_and_waits(self):
        state={'present':True,'waited':False};signals=[]
        class Child:
            pid=123456
            def poll(self):return 0
            def wait(self,**kwargs):state['waited']=True;return 0
        def kill(pid,sig):
            if sig==0:
                if not state['present']:raise ProcessLookupError()
                return
            signals.append(sig);state['present']=False
        with patch.object(h.os,'killpg',side_effect=kill):
            result=h.cleanup_owned(Child(),h.time.monotonic()+30,{'deadline':None})
        self.assertTrue(signals);self.assertTrue(state['waited'])
        self.assertTrue(result['owned_process_group_absent']);self.assertTrue(result['termination_needed'])

    def test_admission_rejects_exhausted_cpu_before_new_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError,'aggregate CPU budget exhausted'):
                h.admission(Path(tmp),h.time.monotonic(),600,0,True)

    def test_saved_array_completeness_is_not_zip_truncated(self):
        h.require_array(np.ones((36,200,80),dtype=np.float32),(36,200,80))
        for value in (np.ones((35,200,80),dtype=np.float32),np.ones((36,200,80),dtype=np.float64),
                      np.full((36,200,80),np.nan,dtype=np.float32)):
            with self.assertRaises(ValueError):h.require_array(value,(36,200,80))


if __name__=='__main__': unittest.main(verbosity=2)
