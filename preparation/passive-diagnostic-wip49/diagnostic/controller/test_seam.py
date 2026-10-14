# Unexecuted portable template; generate every configuration placeholder before use.
#!/usr/bin/env python3
"""Retained 22 seam and six ownership groups for the fresh model-free gate.
Acoustic producers are explicit fakes; selected pure product helpers are pinned
AST extracts. Real ownership children are isolated Python with no model imports.
Prepared source only; no group in this file has been executed by drafting.
"""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ctypes
import ast
import hashlib
import json
import os
import subprocess
import stat
import errno
from contextlib import contextmanager
import time
import types
import unittest
from unittest.mock import patch
from common import Admission, Deadlines, Refused, FRAMES, MIB, bounded_read, retain_raw, sha
from ownership import Ledger
from capture import assert_primary_equal, no_new_calls
from instrumentation import Instrumentation


_RETAINED_COUNT = 0


class RetainedFixtureRoot:
    """Fixture-local ordinary allocation; context exit/cleanup never removes evidence."""
    def __init__(self):
        global _RETAINED_COUNT
        run=Path(os.environ['GUARD_RUN_DIRECTORY'])
        if not run.is_absolute() or run.resolve()!=run:raise Refused('fixture_run_scope')
        base=run/'tmp/fixture-artifacts';base.mkdir(mode=0o700,exist_ok=True)
        if base.resolve()!=base or base.is_symlink():raise Refused('fixture_root_alias')
        if _RETAINED_COUNT>=48:raise Refused('fixture_root_count')
        self.path=base/f'case-{_RETAINED_COUNT:03}'
        _RETAINED_COUNT+=1;self.path.mkdir(mode=0o700,exist_ok=False)
        self.closed=False
    def __enter__(self):return str(self.path)
    def cleanup(self):
        base=self.path.parent;total=0;entries=0
        for path in (base,*base.rglob('*')):
            info=path.lstat();entries+=1
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise Refused('fixture_nonordinary_artifact')
            total+=max(info.st_size,info.st_blocks*512)
            if total>12*2**20 or entries>4096:raise Refused('fixture_retained_output_cap')
        self.closed=True
    def __exit__(self,*exc):self.cleanup();return False


def retained_root():return RetainedFixtureRoot()


class FakeClock:
    def __init__(self): self.now = 100.
    def __call__(self): return self.now

class FakeDeadline:
    def __init__(self):
        self.work = time.monotonic()+30
        self.absolute = self.work+30
        self.stop = Path('/this-is-a-deliberately-absent-test-stop')
    def check(self, end=None):
        if time.monotonic() >= min(self.work, end or self.work): raise Refused('fake_deadline')
    def cleanup_check(self):
        if time.monotonic() >= self.absolute: raise Refused('fake_cleanup_deadline')

class FakeJournal:
    def __init__(self): self.events=[]
    def emit(self, *args, **kwargs): self.events.append((args, kwargs))

class FakePopen:
    pid = 912345
    returncode = 0
    def wait(self, timeout=None): return 0


def fixture_pins():
    import common
    result = {role:{'frames':frames,'wav_sha256':str(i+1)*64,
        'pcm16_sha256':'b'*64,'float32_sha256':'c'*64}
        for i,(role,frames) in enumerate(common.ROLE_FRAMES.items())}
    result['source']['wav_sha256'] = common.SOURCE_WAV_SHA
    return result


def fixture_deadline(clock):
    value = Deadlines({'GUARD_WORK_END':'1840','GUARD_ABSOLUTE_END':'1900',
        'REAL_CAPTURE_POSTHASH_END':'1840','GUARD_STOP_PATH':'/explicitly-absent-fixture-stop'},clock=clock)
    return clock,value

class OwnershipFixtures(unittest.TestCase):
    def test_original_Popen_retained_before_identity_failure(self):
        ledger=Ledger.__new__(Ledger)
        ledger.deadline=FakeDeadline();ledger.journal=FakeJournal();ledger.originals=[]
        import threading
        ledger.lock=threading.RLock();ledger.errors=[]
        original=FakePopen();calls=[]
        def factory(*a,**kw):calls.append(True);return original
        ledger.real_popen=factory
        with patch('ownership.proc',side_effect=OSError('explicit fake identity observation error')):
            with self.assertRaises(OSError):ledger.spawn(['explicit-fake-producer'])
        self.assertEqual(calls,[True]);self.assertEqual(len(ledger.originals),1)
        self.assertIs(ledger.originals[0],original)
        self.assertTrue(ledger.errors)

    def test_one_positive_owned_child_original_wait_and_absence(self):
        # This synthetic fixture requires the bounded model-free test configuration.
        with retained_root() as td:
            deadline=FakeDeadline();journal=FakeJournal();ledger=Ledger(deadline,journal)
            try:
                original=ledger.spawn([sys.executable,'-I','-B','-S','-c','pass'],
                                      stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                code=original.wait(timeout=5)
                self.assertEqual(code,0)
            finally:
                receipt=ledger.cleanup(Path(td)/'cleanup.json')
            self.assertEqual(receipt['cleanup'],'CONFIRMED')
            self.assertEqual(len(receipt['originals']),1)
            self.assertTrue(receipt['originals'][0]['original_waited'])
            self.assertEqual(receipt['remaining_owned'],[])

    def test_owned_grandchild_adoption_before_restart_barrier(self):
        with retained_root() as td:
            ledger=Ledger(FakeDeadline(),FakeJournal())
            code='import os,time; child=os.fork(); time.sleep(.2) if child==0 else None; os._exit(0)'
            try:
                original=ledger.spawn([sys.executable,'-I','-B','-S','-c',code],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                self.assertEqual(original.wait(timeout=5),0)
            finally:
                receipt=ledger.cleanup(Path(td)/'cleanup.json')
            self.assertEqual(receipt['cleanup'],'CONFIRMED')
            self.assertEqual(receipt['remaining_owned'],[])
            self.assertGreaterEqual(len(receipt['owned_identities']),2)

    def test_real_original_wait_after_identity_failure_remains_unknown(self):
        with retained_root() as td:
            ledger=Ledger(FakeDeadline(),FakeJournal())
            with patch('ownership.proc',side_effect=OSError('explicit injected identity failure')):
                with self.assertRaises(OSError):
                    ledger.spawn([sys.executable,'-I','-B','-S','-c','pass'],
                        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            receipt=ledger.cleanup(Path(td)/'cleanup.json')
            self.assertEqual(receipt['cleanup'],'UNKNOWN')
            self.assertEqual(len(receipt['originals']),1)
            self.assertTrue(receipt['originals'][0]['original_waited'])
            self.assertEqual(receipt['remaining_owned'],[])

    def test_provider_close_raises_still_waits_original(self):
        with retained_root() as td:
            ledger=Ledger(FakeDeadline(),FakeJournal())
            original=ledger.spawn([sys.executable,'-I','-B','-S','-c','pass'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            error=RuntimeError('explicit fake provider close error')
            def fake_close():raise error
            with self.assertRaises(RuntimeError) as got:fake_close()
            self.assertIs(got.exception,error)
            receipt=ledger.cleanup(Path(td)/'cleanup.json')
            self.assertEqual(receipt['cleanup'],'CONFIRMED')
            self.assertTrue(receipt['originals'][0]['original_waited'])

    def test_ownership_lock_refuses_instead_of_waiting_indefinitely(self):
        ledger=Ledger.__new__(Ledger);ledger.deadline=FakeDeadline()
        class FakeBusyLock:
            def acquire(self,timeout):self.timeout=timeout;return False
        ledger.lock=FakeBusyLock()
        with self.assertRaises(Refused):
            with ledger._locked():raise AssertionError('never enter')
        self.assertLessEqual(ledger.lock.timeout,.2)


class NativeABIHelper:
    def check_wrapped_fake_ABI_original_calls_and_pre_entry_overcap(self):
        calls=[]
        def fake(*args):calls.append(args);return 0
        fake.__module__='explicit_fake_producer'
        names=('nemo_speech_diar_create','nemo_speech_diar_stream_open',
               'nemo_speech_diar_stream_push_f32','nemo_speech_diar_stream_finish','nemo_speech_diar_segments')
        class FakeCAPI:
            def __init__(obj,*args,**kwargs):obj.lib=types.SimpleNamespace(**dict.fromkeys(names,fake))
        FakeCAPI.__init__.__module__='explicit_fake_producer'
        producer=types.ModuleType('audio_backend.inference.native_diarization');producer._CAPI=FakeCAPI
        fake_module=types.ModuleType('explicit_fake_producer');fake_module.__file__=__file__
        spec={'output_dir':'/unused-model-free','settings':{'native_diarization_library':'explicit-fake-library'},
              'site_packages':str(HERE),'installed_files':{str(Path(__file__).resolve()):{}}}
        instrument=Instrumentation(spec,FakeDeadline(),FakeJournal(),'native')
        instrument.check=lambda:None
        instrument.abi.check=lambda:None
        fake_modules={'audio_backend':types.ModuleType('audio_backend'),
                      'audio_backend.inference':types.ModuleType('audio_backend.inference'),
                      'audio_backend.inference.native_diarization':producer,
                      'explicit_fake_producer':fake_module}
        zero_sha=hashlib.sha256(bytes(FRAMES*4)).hexdigest()
        spec['input_pins']={'source':{'float32_sha256':zero_sha}}
        with patch.dict(sys.modules,fake_modules):
            instrument.install_native();obj=FakeCAPI('explicit-fake-library')
            obj.lib.nemo_speech_diar_create(None,None);obj.lib.nemo_speech_diar_stream_open(None,None)
            block=(ctypes.c_float*32000)()
            ptr=ctypes.cast(block,ctypes.POINTER(ctypes.c_float))
            for frames in [32000]*5+[8000]+[32000]*5+[8000]:
                obj.lib.nemo_speech_diar_stream_push_f32(None,ptr,frames,16000)
            obj.lib.nemo_speech_diar_stream_finish(None)
            for _ in range(4):obj.lib.nemo_speech_diar_segments(None,None,None,0,None)
            with self.assertRaises(Refused):obj.lib.nemo_speech_diar_segments(None,None,None,0,None)
        self.assertEqual(len(calls),19)
        self.assertEqual(instrument.abi.counts['native_segments']['rejected'],1)
        self.assertEqual(instrument.native_frames,FRAMES)


class SeamActualFixtures(unittest.TestCase):
    """Explicit synthetic source/receipt/producer data; no product or native import."""
    @staticmethod
    def document():
        commits=[{'chunk_id':role,'source_origin_frame':i*168000,'local_start_frame':0,
            'local_end_frame':168000,'logical_start_frame':i*168000,'logical_end_frame':(i+1)*168000,
            'evidence_sha256':str(i+1)*64} for i,role in enumerate(('a','b'))]
        parts=[{'chunk_id':'a','receipt_sha256':'a'*64,'source_frames':168000,'source_start':8000,
            'source_end':168000,'logical_start':8000,'logical_end':168000},
            {'chunk_id':'b','receipt_sha256':'b'*64,'source_frames':168000,'source_start':0,
            'source_end':160000,'logical_start':168000,'logical_end':328000}]
        return {'schema':'native-owned-seam-composite-v2','scope':{'stream_id':'seam-a',
            'origin_seconds_hex':float(64).hex(),'epoch_index':0,'reset_generation':1},
            'geometry':{'seam_frame':168000,'left_frames':160000,'right_frames':160000,
                        'start_frame':8000,'end_frame':328000},
            'support':{'source_frames':336000,'core_start_frame':0,'core_end_frame':336000,
                       'start_frame':8000,'end_frame':328000},'commits':commits,'dependencies':parts,
            'native_witnesses':[{'explicit_fake':'a'},{'explicit_fake':'b'}],
            'publication_anchor':{'chunk_id':'b','start':0,'end':168000,'sha256':'2'*64},
            'coverage':{'kind':'seam_overlap_alternative','frame_range':[8000,328000],
                'coverage_envelope_frames':[0,336000],'source_support_core_semantics':'two_committed_core_envelope',
                'adds_primary_audio':False,'automatic_dispatch':False}}

    @staticmethod
    def counts():
        def row(n):return {'entered':n,'returned':n,'accepted':n,'rejected':0,'exceptions':0}
        return {'rpc':{k:row(v) for k,v in {'reset':1,'push':2,'finish':1,'turns':2}.items()},
            'jobs':{k:row(v) for k,v in {'analyzer':2,'commit':2,'worker_start':1,'cell_prepare':2,
                'cell_committed':2,'analyzer_process_constructor':3}.items()},'nonempty_claims':3}

    @staticmethod
    def launches():return {'audio_backend.native_runner':1,'audio_backend.runner':3,'audio_backend.guard':9}

    @staticmethod
    def authority():
        return {'session_valid':True,'sources':[{'receipt_valid':True,'receipt':{'fake':role},
            'row':{'id':role},'receipt_id':role*64} for role in ('a','b')],
            'composite':{'commit_current':[True,True],'adjacent':True,'composed_current':True}}

    @staticmethod
    def controller_source():
        import capture
        tree=ast.parse(Path(capture.__file__).read_text())
        return ast.unparse(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_controller'))

    def test_split_rejoins_exact_pcm_and_float_hashes(self):
        import capture,struct
        intervals=capture.split_role_intervals();pcm=bytes(range(256))*2625
        self.assertEqual(len(pcm),672000)
        buffers={role:bytearray() for role in intervals}
        for at in range(0,336000,16384):
            for role,part in capture.split_block_contributions(at,pcm[at*2:min(at+16384,336000)*2]).items():
                buffers[role].extend(part)
        self.assertEqual(bytes(buffers['upload_a'])+bytes(buffers['upload_b']),pcm)
        for role,(lo,hi) in intervals.items():
            expected=pcm[lo*2:hi*2];self.assertEqual(bytes(buffers[role]),expected)
            a=b''.join(struct.pack('<f',x[0]/32768.) for x in struct.iter_unpack('<h',buffers[role]))
            b=b''.join(struct.pack('<f',x[0]/32768.) for x in struct.iter_unpack('<h',expected))
            self.assertEqual(sha(a),sha(b));self.assertEqual(len(capture.canonical_pcm16_header(hi-lo)),44)

    def test_split_wrong_source_geometry_or_pin_refused(self):
        import capture,common,copy
        for at,block in [(-1,b'00'),(True,b'00'),(336000,b'00'),(0,b'0'),(0,b'0'*32770)]:
            with self.assertRaises(Refused):capture.split_block_contributions(at,block)
        for frames in (True,1,307040,336001):
            with self.assertRaises(Refused):capture.canonical_pcm16_header(frames)
        bad=fixture_pins();bad['source']['wav_sha256']='0'*64
        with self.assertRaises(Refused):common.validate_input_pins(bad)

    def test_split_exclusive_output_partial_retained(self):
        import capture
        with retained_root() as td:
            root=Path(td)/'component';run=root/'runs/run-fixture0';run.mkdir(parents=True)
            (root/'input').mkdir();partial=root/'input/upload-a-10p5s.wav';partial.write_bytes(b'failed-partial')
            now=time.monotonic();env={'GUARD_ABSOLUTE_END':str(now+300),'GUARD_WORK_END':str(now+240),
                'GUARD_RUN_DIRECTORY':str(run),'GUARD_STOP_PATH':str(run/'STOP')}
            targets={'a':partial,'b':root/'input/upload-b-10p5s.wav'}
            with self.assertRaisesRegex(Refused,'fresh_materialization_only'):
                capture.check_split_targets(targets,root/'input/MATERIALIZATION.json')
            self.assertEqual(partial.read_bytes(),b'failed-partial');self.assertFalse((root/'input/MATERIALIZATION.json').exists())

    def test_A_waiting_requires_zero_analyze_and_no_commit(self):
        from server import ServerHooks
        hooks=ServerHooks.__new__(ServerHooks);hooks.rpc=types.SimpleNamespace(counts={'push':{'entered':1}})
        hooks.native_states=[];state={'input_seconds':10.5,'processed_seconds':0.,'stable_seconds':0.,'finished':False}
        # Extract only this original branch; fake product admission utilities never load providers.
        fake=types.ModuleType('audio_backend.native_owned_support');fake._turns=lambda *a:None
        fake.validate_observed_provenance=lambda *a:None
        with patch.dict(sys.modules,{'audio_backend':types.ModuleType('audio_backend'),'audio_backend.native_owned_support':fake}):
            hooks.native_result('push',state)
            for key,value in [('processed_seconds',1.),('stable_seconds',1.),('finished',True)]:
                with self.assertRaises(Refused):hooks.native_result('push',dict(state,**{key:value}))
        text=self.controller_source();self.assertIn('waiting_input',text);self.assertIn('exact_waiting_A_work',text)

    def test_second_upload_resumes_A_before_B(self):
        from server import observe_first_claim
        with retained_root() as td:
            clock=FakeClock();_,deadline=fixture_deadline(clock);pins=fixture_pins()
            hooks=types.SimpleNamespace(capture_allowed=True,claimed=0,claim_sources=[],deadlines=deadline,
                spec={'_sha256':'x','input_pins':pins},out=Path(td),journal=FakeJournal())
            a={'status':'queued','duration':10.5,'timeline_start':64.,'next_offset':0,'digest':pins['upload_a']['wav_sha256'],'id':'a','session_id':'s'}
            b=dict(a,id='b',timeline_start=74.5,digest=pins['upload_b']['wav_sha256'])
            observe_first_claim(hooks,a)
            with self.assertRaises(Refused):observe_first_claim(hooks,b)
            observe_first_claim(hooks,a);observe_first_claim(hooks,b);self.assertEqual([r['id'] for r in hooks.claim_sources],['a','a','b'])

    def test_six_rpc_order_and_seventh_refused(self):
        from server import ServerHooks
        calls=[]
        with patch('server.load_admission',return_value=object()):
            hooks=ServerHooks({'output_dir':'/unused-explicit-fixture'},FakeDeadline(),FakeJournal(),True)
        for action in ('reset','push','push','finish','turns','turns'):
            sentinel=object();self.assertIs(hooks.rpc.invoke(action,lambda value:(calls.append(action),value)[1],sentinel),sentinel)
        with self.assertRaises(Refused):hooks.rpc.invoke('turns',lambda:calls.append('unexpected'))
        self.assertEqual(calls,['reset','push','push','finish','turns','turns'])

        # The original wrapper must forward the product observer through diagnostic faults.
        import server,functools
        tree=ast.parse(Path(server.__file__).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ServerHooks')
        install=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='install')
        wrapper=next(n for n in install.body if isinstance(n,ast.FunctionDef) and n.name=='request')
        for failure in (None,'retention','export','prior'):
            original_error=RuntimeError('original prior observer exception')
            returned=object();producer_result={'explicit':'fake native result'};forwarded=[];callback_returns=[]
            def prior(value):
                forwarded.append(value)
                if failure=='prior':raise original_error
                return returned
            def original_request(obj,action,stop,**kwargs):
                self.assertEqual(action,'reset');self.assertFalse(stop())
                callback_returns.append(obj.response_observer(producer_result))
                return producer_result
            d=FakeDeadline();d.call_end=lambda maximum:time.monotonic()+maximum
            with patch.object(server,'load_admission',return_value=object()):
                hooks=ServerHooks({'output_dir':'/explicit-unused-native-fixture'},d,FakeJournal(),True)
            hooks.active=lambda *args:None;hooks.native_result=lambda *args:None
            def retain(*args,**kwargs):
                if failure in ('retention','prior'):raise RuntimeError('injected diagnostic retention failure')
            def export(*args,**kwargs):
                if failure=='export':raise RuntimeError('injected diagnostic export failure')
            hooks.raw=retain
            obj=types.SimpleNamespace(counter=0,root=Path('/explicit-unused-native-root'),response_observer=prior)
            ns={'self':hooks,'functools':functools,'original_request':original_request,'time':time,
                'require':server.require,'MIB':MIB,'atomic_json':export,'sys':sys}
            exec(compile(ast.Module(body=[wrapper],type_ignores=[]),str(server.__file__),'exec'),ns)
            if failure=='prior':
                with self.assertRaises(RuntimeError) as got:ns['request'](obj,'reset',lambda:False)
                self.assertIs(got.exception,original_error);self.assertEqual(callback_returns,[])
            else:
                self.assertIs(ns['request'](obj,'reset',lambda:False),producer_result)
                self.assertEqual(callback_returns,[returned]);self.assertEqual(hooks.rpc.counts['reset']['accepted'],1)
            self.assertEqual(forwarded,[producer_result]);self.assertIs(obj.response_observer,prior)
            self.assertEqual(bool(hooks.cell_witness['errors']),failure is not None)


    def test_twelve_abi_blocks_and_exact_frame_sum(self):
        import instrumentation
        value=Instrumentation({'output_dir':'/unused-explicit-fixture'},FakeDeadline(),FakeJournal(),'native')
        self.assertEqual(value.abi.caps['native_push'],12);self.assertEqual(value.abi.caps['native_segments'],4)
        source=Path(instrumentation.__file__).read_text()
        self.assertIn('8000',source);self.assertEqual(sum(([32000]*5+[8000])*2),336000)
        NativeABIHelper.check_wrapped_fake_ABI_original_calls_and_pre_entry_overcap(self)

    def test_two_contexts_have_64000_frame_intersection(self):
        import capture
        roles=capture.split_role_intervals();a,b=roles['analyzer_a'],roles['analyzer_b']
        self.assertEqual((a,b),((0,200000),(136000,336000)))
        self.assertEqual(min(a[1],b[1])-max(a[0],b[0]),64000)
        self.assertEqual(sum(hi-lo for lo,hi in (a,b)),400000)

    def test_three_children_have_two_analyzes_and_aggregate_sdk_caps(self):
        from capture import validate_primary_counts
        import copy
        counts=self.counts();self.assertTrue(validate_primary_counts(counts,self.launches()))
        for name in ('analyzer','analyzer_process_constructor'):
            changed=copy.deepcopy(counts);changed['jobs'][name]['entered']+=1
            with self.assertRaises(Refused):validate_primary_counts(changed,self.launches())
        profile=__import__('binding').profile_contract()
        self.assertEqual(profile['counters']['SDK_max'],{'SenseVoice':64,'each_model_constructor':2,'embedding':16,'event':8,'streams':88})

    def test_seven_upload_requests_keep_two_receipts_and_three_claims(self):
        from capture import validate_primary_counts
        counts=self.counts();self.assertTrue(validate_primary_counts(counts,self.launches()))
        counts['nonempty_claims']=4
        with self.assertRaises(Refused):validate_primary_counts(counts,self.launches())
        profile=__import__('binding').profile_contract()
        self.assertEqual([profile['counters'][k] for k in ('PUT_requests','fresh_receipts','nonempty_claims')],[7,2,3])
        text=self.controller_source();self.assertIn('upload-overlap',text);self.assertIn('upload-a',text);self.assertIn('upload-b',text)

    def test_replay_and_conflicts_add_no_hidden_primary_work(self):
        from capture import validate_primary_counts
        import copy
        counts=self.counts();launches=self.launches()
        for area,name in [('rpc','push'),('jobs','commit'),('jobs','analyzer')]:
            changed=copy.deepcopy(counts);changed[area][name].update(entered=99,returned=99,accepted=99)
            with self.assertRaises(Refused):validate_primary_counts(changed,launches)
        with self.assertRaises(Refused):validate_primary_counts(counts,dict(launches,**{'audio_backend.guard':10}))

    def test_two_original_true_commit_returns_precede_composite(self):
        from capture import await_cell_publication
        d=types.SimpleNamespace(check=lambda:None,phase_end=time.monotonic()+30)
        records=[{'prepared':True,'commit_returned_true':True,'committed_called':True,'core_frames':core,
            'publication':{'kind':'Operation','operation_kind':'publish','done':True,'drained':True,
                'slot_released':True,'result':{'status':'durable'}}} for core in ([0,168000],[168000,336000])]
        state={'cell_witness':{'records':records,'errors':[]},'cells':{'handoffs_active_or_pending':0}}
        self.assertIs(await_cell_publication(types.SimpleNamespace(command=lambda action:state),d),state)
        records[1]['commit_returned_true']=False
        with self.assertRaises(Refused):await_cell_publication(types.SimpleNamespace(command=lambda action:state),d)

        # Extract the real post-return wrapper; diagnostic failure cannot replace True.
        import server,functools,threading
        tree=ast.parse(Path(server.__file__).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ServerHooks')
        install=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='install')
        wrapper=next(n for n in install.body if isinstance(n,ast.FunctionDef) and n.name=='commit')
        for failure in (None,'witness','journal','original'):
            calls=[];committed=[];original_error=RuntimeError('original commit exception')
            def original_commit(*args):
                calls.append(args)
                if failure=='original':raise original_error
                return True
            hooks=server.ServerHooks.__new__(server.ServerHooks)
            hooks._observation_lock=threading.RLock();hooks.cell_witness={'errors':[],'commit_returned_true':False}
            witness={'role':'a','commit_returned_true':False};hooks._witnesses=[witness]
            hooks._witness=None if failure=='witness' else witness
            hooks.jobs=Admission({'commit':2},lambda:None,FakeJournal().emit)
            hooks.admission=types.SimpleNamespace(admit=lambda *args,**kwargs:None)
            hooks.labels=frozenset(('fake',));hooks.spec={'speaker_model_sha256':'a'*64}
            hooks.turns=[{'speaker_local_id':'fake','start':0.,'end':21.}]
            hooks.out=Path('/explicit-unused-commit-fixture')
            def emit(event,**fields):
                if failure=='journal' and event=='production_commit_return':raise RuntimeError('injected diagnostic journal failure')
            hooks.journal=types.SimpleNamespace(emit=emit)
            ns={'self':hooks,'functools':functools,'original_commit':original_commit,'require':server.require,
                'atomic_json':lambda *args,**kwargs:None,'MIB':MIB}
            exec(compile(ast.Module(body=[wrapper],type_ignores=[]),str(server.__file__),'exec'),ns)
            producer=object();chunk={'timeline_start':64.};result={'explicit':'fake result'}
            if failure=='original':
                with self.assertRaises(RuntimeError) as got:ns['commit'](producer,chunk,0.,10.5,result)
                self.assertIs(got.exception,original_error)
            else:
                value=ns['commit'](producer,chunk,0.,10.5,result)
                self.assertIs(value,True)
                if value is True:committed.append('normal subsequent Worker committed hook remains reachable')
                self.assertEqual(len(committed),1);self.assertEqual(hooks.jobs.counts['commit']['accepted'],1)
            self.assertEqual(calls,[(producer,chunk,0.,10.5,result)])
            self.assertEqual(bool(hooks.cell_witness['errors']),failure in ('witness','journal'))


        # Exercise pinned product own_core and its real pure clipping dependencies.
        # AST selection imports neither the application nor acoustic providers.
        import copy,math,unicodedata
        from binding import fixture_source
        source, frozen = fixture_source()
        def pure(relative,names,namespace,constants=False):
            path=source/relative;raw=path.read_bytes()
            self.assertEqual(sha(raw),frozen[relative])
            parsed=ast.parse(raw)
            selected=[n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name in names
                or constants and isinstance(n,ast.Assign)]
            self.assertEqual({n.name for n in selected if isinstance(n,ast.FunctionDef)},set(names))
            exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),namespace)
            return namespace
        boundaries=pure('audio_backend/boundaries.py',
            ('lexical_core','normalized','committed_punctuation','overlap_matches'),
            {'math':math,'unicodedata':unicodedata})
        events=pure('audio_backend/event_candidate_store.py',
            ('own_candidates','_number','_text','_json','_payload','_sample_boundary','_diagnostic_evidence'),
            {'math':math,'json':json,'hashlib':hashlib},constants=True)
        worker=pure('audio_backend/worker.py',('own_core',),
            {'lexical_core':boundaries['lexical_core'],'event_candidate_store':types.SimpleNamespace(**events)})
        admission=server.load_admission({'storage_admission':{'helper':
            {'path':str(HERE/'admission.py'),'sha256':sha((HERE/'admission.py').read_bytes())}}},FakeDeadline())
        epoch_turns=[{'speaker_local_id':label,'start':lo,'end':hi} for label,lo,hi in
            [('native:one',.1,1.),('native:two',8.5,10.5),('native:three',10.,11.),
             ('native:four',11.241,12.319),('native:one',19.,20.)]]
        epoch_before=copy.deepcopy(epoch_turns)
        contexts=((0.,12.5),(8.5,21.))
        expected=(frozenset(('native:one','native:two','native:three')),
                  frozenset(('native:one','native:three','native:four')))
        for index,role in enumerate(('a','b')):
            lo,hi=contexts[index];core_lo,core_hi=((0.,10.5),(10.5,21.))[index]
            turns=[dict(t,start=max(lo,t['start'])-lo,end=min(hi,t['end'])-lo)
                for t in epoch_turns if min(hi,t['end'])>max(lo,t['start'])]
            segments=[dict(t,text='synthetic',asr_backend=admission.BACKEND,timestamp_kind=admission.TIMING,
                _units=[{'text':'synthetic','start':t['start'],'end':t['end'],
                    'ownership_anchor':(t['start']+t['end'])/2}]) for t in turns]
            full={'speaker_turns':turns,'segments':segments,
                'events':[{'start':0.,'end':12.5,'label':'explicit synthetic event','score':.5}],
                'metadata':{'asr_backend':admission.BACKEND,'timestamp_semantics':admission.TIMING,
                    'speaker_embedding_sha256':'a'*64,'event_candidates':[
                        {'start':0.,'end':12.5,'top_labels':[],'category_scores':{}}]}}
            context_labels=frozenset(t['speaker_local_id'] for t in turns)
            self.assertEqual(len(context_labels),4)
            admission.admit_raw(json.dumps(full).encode(),native_labels=context_labels,
                expected_speaker_sha256='a'*64)
            clipped=worker['own_core'](full,core_lo-lo,core_hi-lo)
            self.assertIs(clipped,full)
            self.assertEqual(frozenset(t['speaker_local_id'] for t in clipped['speaker_turns']),expected[index])
            self.assertTrue(all(0<=t['start']<t['end']<=10.5 for t in clipped['speaker_turns']))
            self.assertTrue(clipped['segments'])
            self.assertTrue(all(row['speaker_local_id'] in expected[index] for row in clipped['segments']))
            self.assertEqual([(e['start'],e['end']) for e in clipped['events']],[(0.,10.5)])
            self.assertEqual([(e['start'],e['end']) for e in clipped['event_candidates']],[(0.,10.5)])
            self.assertEqual(clipped['metadata']['boundary_policy'],
                'whole lexical units; time-constrained overlap sequence alignment; native timing retained')
            # The unchanged context validator correctly rejects this already-clipped core.
            with self.assertRaises(admission.Rejected):
                admission.admit(clipped,native_labels=context_labels,expected_speaker_sha256='a'*64)
            chunk={'timeline_start':(64.,74.5)[index]};producer=object()
            def invoke(value,returned=True,error=None,refused=False):
                calls=[];admitted=[];before=copy.deepcopy(value)
                hooks=server.ServerHooks.__new__(server.ServerHooks)
                hooks._observation_lock=threading.RLock();hooks.cell_witness={'errors':[],'commit_returned_true':False}
                witness={'role':role,'commit_returned_true':False}
                hooks._witness=witness;hooks._witnesses=[witness];hooks.journal=FakeJournal()
                hooks.jobs=Admission({'commit':2},lambda:None,FakeJournal().emit)
                hooks.jobs.counts['commit'].update(entered=index,returned=index,accepted=index)
                hooks.turns=copy.deepcopy(epoch_turns);hooks.labels=context_labels
                hooks.spec={'speaker_model_sha256':'a'*64};hooks.out=Path('/explicit-unused-core-fixture')
                def admit(result,**kwargs):
                    admitted.append((result,kwargs['native_labels']))
                    return admission.admit(result,**kwargs)
                hooks.admission=types.SimpleNamespace(admit=admit)
                def original(obj,got_chunk,offset,end,result):
                    calls.append(result)
                    self.assertIs(obj,producer);self.assertIs(got_chunk,chunk);self.assertIs(result,value)
                    self.assertEqual((offset,end),(0.,10.5))
                    if error is not None:raise error
                    return returned
                ns={'self':hooks,'functools':functools,'original_commit':original,'require':server.require,
                    'atomic_json':lambda *args,**kwargs:None,'MIB':MIB}
                exec(compile(ast.Module(body=[wrapper],type_ignores=[]),str(server.__file__),'exec'),ns)
                try:
                    result=ns['commit'](producer,chunk,0.,10.5,value)
                    self.assertIs(result,returned)
                finally:
                    self.assertEqual(value,before);self.assertEqual(hooks.turns,epoch_before)
                    self.assertEqual(len(admitted),1);self.assertIs(admitted[0][0],value)
                    self.assertEqual(admitted[0][1],expected[index])
                    if refused:
                        self.assertEqual(calls,[]);self.assertEqual(hooks.jobs.counts['commit']['entered'],index)
                        self.assertEqual(hooks.jobs.counts['commit']['rejected'],1)
                    else:self.assertEqual(len(calls),1)
                return calls,hooks
            for returned in (True,False,None,object()):
                calls,hooks=invoke(clipped,returned)
                self.assertEqual(len(calls),1);self.assertIs(calls[0],clipped)
                self.assertEqual(hooks.jobs.counts['commit']['accepted'],index+1)
                self.assertEqual(hooks._witness['commit_returned_true'],returned is True)
            original_error=RuntimeError('same original clipped-core commit error')
            with self.assertRaises(RuntimeError) as got:invoke(clipped,error=original_error)
            self.assertIs(got.exception,original_error)
            out_of_core=('native:four','native:two')[index]
            for label in ('foreign:label',out_of_core):
                bad=copy.deepcopy(clipped)
                bad['speaker_turns'].append({'speaker_local_id':label,'start':.1,'end':.2})
                with self.assertRaisesRegex(admission.Rejected,'turn label not native admitted'):
                    invoke(bad,refused=True)
            for label in expected[index]:
                bad=copy.deepcopy(clipped)
                bad['speaker_turns']=[t for t in bad['speaker_turns'] if t['speaker_local_id']!=label]
                with self.assertRaisesRegex(admission.Rejected,'analyzer/native label set differs'):
                    invoke(bad,refused=True)
        self.assertEqual(epoch_turns,epoch_before)


    def test_original_process_epoch_and_reset_serial_required(self):
        from capture import validate_seam_geometry
        import copy
        doc=self.document();self.assertTrue(validate_seam_geometry(doc))
        for key,value in [('epoch_index',1),('reset_generation',2),('origin_seconds_hex',float(0).hex()),('stream_id','other')]:
            bad=copy.deepcopy(doc);bad['scope'][key]=value
            with self.assertRaises(Refused):validate_seam_geometry(bad)
        import server
        self.assertIn('native_process_replaced',Path(server.__file__).read_text())

    def test_same_response_crossing_overlap_required(self):
        from capture import same_response_seam_overlap
        a={'start':10.,'end':11.,'speaker_local_id':'one'};b=dict(a,speaker_local_id='two')
        result=same_response_seam_overlap([{'turns':[a,b]},{'turns':[]}])
        self.assertTrue(result['same_original_response']);self.assertEqual(result['overlap_frames'],[160000,176000])
        for turns in ([a,dict(b,end=10.5)],[a,dict(b,start=10.5)],[a,a]):
            with self.assertRaises(Refused):same_response_seam_overlap([{'turns':turns},{'turns':[]}])

    def test_union_of_responses_cannot_select_gate(self):
        from capture import same_response_seam_overlap
        turns=[{'start':10.,'end':11.,'speaker_local_id':role} for role in ('one','two')]
        with self.assertRaisesRegex(Refused,'missing_same_response_overlap_crossing_seam'):
            same_response_seam_overlap([{'turns':[turns[0]]},{'turns':[turns[1]]}])

    def test_v2_geometry_dependencies_anchor_and_pcm_exact(self):
        from capture import validate_seam_geometry
        import copy
        doc=self.document();self.assertTrue(validate_seam_geometry(doc))
        for mutate in (lambda d:d['dependencies'][0].update(source_start=0),
                       lambda d:d['dependencies'][1].update(source_end=159999),
                       lambda d:d['publication_anchor'].update(chunk_id='a'),
                       lambda d:d['commits'][1].update(logical_start_frame=0)):
            bad=copy.deepcopy(doc);mutate(bad)
            with self.assertRaises(Refused):validate_seam_geometry(bad)
        import capture
        pcm=b'\x01\x00'*336000;parts={}
        for at in range(0,336000,16384):
            for role,data in capture.split_block_contributions(at,pcm[at*2:min(at+16384,336000)*2]).items():parts.setdefault(role,bytearray()).extend(data)
        self.assertEqual(parts['composite'],pcm[16000:656000]);self.assertEqual(len(parts['composite']),640000)

    def test_both_real_positive_authority_results_required(self):
        from capture import validate_dual_authority
        import copy
        value=self.authority();self.assertTrue(validate_dual_authority(value))
        for i in (0,1):
            bad=copy.deepcopy(value);bad['sources'][i]['receipt_valid']=False
            with self.assertRaises(Refused):validate_dual_authority(bad)
            bad=copy.deepcopy(value);bad['composite']['commit_current'][i]=False
            with self.assertRaises(Refused):validate_dual_authority(bad)
        bad=copy.deepcopy(value);bad['composite']['composed_current']=False
        with self.assertRaises(Refused):validate_dual_authority(bad)

    def test_missing_or_wrong_intent_adds_no_job_and_drains_pin(self):
        text=self.controller_source()
        for exact in ('coverage_intent_required','invalid_body','seam-moss-missing','seam-moss-wrong','overlapping_diagnostic'):
            self.assertIn(exact,text)
        import server
        source=Path(server.__file__).read_text()
        for key in ('rejected_pins_acquired','rejected_pins_released','rejected_acquisitions'):
            self.assertIn(key,source)
        self.assertIn('idempotency_conflict',text)

    def test_one_diagnostic_exact_key_replay_no_redispatch(self):
        from capture import validate_job_replay
        import copy
        pin={key:1 for key in ('acquire_entered','accepted_entered','acquired','accepted','rejected_acquisitions')}
        state={'jobs':{'accepted_jobs':1},'launches':{'moss-direct':1},'cell_pin_cleanup':pin}
        binding={'explicit':'fake'};accepted={'job_id':'one'};replay={'job_id':'one','cell_binding':binding}
        self.assertTrue(validate_job_replay(state,copy.deepcopy(state),accepted,replay,binding))
        bad=copy.deepcopy(state);bad['launches']['moss-direct']=2
        with self.assertRaises(Refused):validate_job_replay(state,bad,accepted,replay,binding)
        bad=copy.deepcopy(state);bad['cell_pin_cleanup']['acquire_entered']=2
        with self.assertRaises(Refused):validate_job_replay(state,bad,accepted,replay,binding)

    def test_OFF_then_ON_preserves_extant_logical_lineage(self):
        text=self.controller_source()
        self.assertLess(text.index('restart-off'),text.index('restart-on'))
        self.assertIn('closed_cell_snapshot',text);self.assertIn('assert_primary_equal',text)
        self.assertLess(text.index('restart-on'),text.index("'DELETE'"))

    def test_final_session_delete_does_not_claim_per_source_revocation(self):
        from capture import validate_delete_observation
        import copy
        value={'session_valid':False,'session_tombstoned':True,'cleanup_pending':0,'chunk_count':0,
            'stream_receipt_rows':0,'sources':[{'row':None,'receipt':None,'receipt_id':None,'receipt_valid':False} for _ in range(2)]}
        self.assertTrue(validate_delete_observation(value,[True,True]))
        for key,bad in [('cleanup_pending',1),('chunk_count',1),('stream_receipt_rows',1),('session_tombstoned',False)]:
            with self.assertRaises(Refused):validate_delete_observation(dict(value,**{key:bad}),[True,True])
        with self.assertRaises(Refused):validate_delete_observation(value,[True,False])
        self.assertIn("'actual_independent_per_upload_revocation': False",self.controller_source())

    def test_restart_old_job_404_is_not_postdelete_raw_revocation(self):
        text=self.controller_source();self.assertIn('non-adoption',text)
        self.assertIn("'original_completed_job_postdelete_raw_result_actual': False",text)
        profile=__import__('binding').profile_contract()
        self.assertFalse(profile['revocation']['old_completed_job_postdelete_raw_result_actual'])
        self.assertEqual(profile['revocation']['actual_final_public_session_DELETE'],1)



if __name__ == '__main__':
    raise SystemExit('Use the bounded 42-group test entry point in test_binding.py')


class ReceiptReaderHelper:
    """In-memory synthetic dictionaries with authentic completed receipt SHAPES.

    These are negative/positive parser fixtures, never emitted acceptance evidence.
    Payloads are synthetic. Generated configuration placeholders provide the exact
    identities required by the parser contracts; these templates are unexecuted.
    """
    @staticmethod
    def check_readers(test, prepared):
        import copy
        import binding
        import launch
        from common import canonical
        cfg=copy.deepcopy(prepared)
        cfg['input_pins']={'source':{'pcm16_sha256':'c'*64}}
        cfg['inventory']={'hash_inventory':[], 'native_runtime_hashes':{}}
        cfg['paths']['native_library']=str(Path(cfg['workspace_root'])/
            '@GENERATED_ASSETS_ROOT_REL@/vendor/native-diarization/linux-x86_64-cpu/runtime/lib/libnemo_speech_asr_c.so')
        store={};sources={};counter=[0]
        def ref(label, value=None, *, digest=None, size=1):
            counter[0]+=1
            row={'path':'/explicit-synthetic-receipts/'+label,'bytes':size,
                 'sha256':digest or sha(label.encode())}
            if value is not None:store[row['path']]={'pin':dict(row),'value':copy.deepcopy(value)}
            return row
        def source(label,digest,size):
            row=ref(label,digest=digest,size=size);sources[row['path']]=dict(row);return row
        def save(pin,value):store[pin['path']]={'pin':dict(pin),'value':copy.deepcopy(value)}
        def fake_read(pin,check=lambda:None,maximum=4*MIB):
            check();expected=store[pin['path']]
            binding.require(pin==expected['pin'],'synthetic_metadata_pin_mismatch')
            return copy.deepcopy(expected['value'])
        def fake_source(pin,check):
            check();binding.require(pin==sources[pin['path']],'synthetic_source_pin_mismatch')
        def command(label):
            return {'label':label,'original_handle_retained':True,'positive_wait':True,
                'returncode':0,'cleanup':'DIRECT_ORIGINAL_REAPED'}
        limits={'work_seconds':540,'cleanup_seconds':60,'total_seconds':600,'cpu':3,
            'nice':19,'sampled_rss_bytes':512*MIB,'address_space_bytes':1024*MIB}
        physical_source=source('repair.py','@GENERATED_STORAGE_REPAIR_SOURCE_SHA256@',int('@GENERATED_STORAGE_REPAIR_SOURCE_BYTES@'))
        decoder_source=source('decoder.py','@GENERATED_DECODER_SOURCE_SHA256@',int('@GENERATED_DECODER_SOURCE_BYTES@'))
        proof_config=ref('proof-config.json',{'schema':'@GENERATED_STORAGE_PROOF_ROOT_REL@-config-v1',
            'product_commit':binding.SOURCE_HEAD,'product_root':cfg['paths']['source_root'],
            'interpreter':cfg['paths']['python']})
        termination={'kill_sent':True,'returncode':-9,'reaped':True}
        baseline={'database':{'bytes':123,'sha256':'d'*64},'oracle':{'counts':{'cells':43},'sha256':'e'*64}}
        driver=ref('raw-physical.json',{'status':'CORROBORATED_MATCH','fresh_evidence':True,
            'production_qualification_changed':False,'runtime_matches_shipped':True,'termination':termination,
            'config_sha256':proof_config['sha256'],'claims':{'power_loss':False},
            'capture':{'observed_changed_page_one':True},'journal_audit':{'all_checksums_valid':True,
                'all_pages_match_baseline':True},'baseline':copy.deepcopy(baseline),'recovery':copy.deepcopy(baseline)},
            digest='@GENERATED_STORAGE_PHYSICAL_RESULT_SHA256@')
        regression_pin={'bytes':1,'sha256':'f'*64}
        outcomes={'setup':{'helper_cases_rerun':0,'prior_helper_cases_passed':48,
                'config':{'sha256':proof_config['sha256']},
                'new_driver':{'bytes':int('@GENERATED_STORAGE_DRIVER_SOURCE_BYTES@'),'sha256':'@GENERATED_STORAGE_DRIVER_SOURCE_SHA256@'}},
            'regression':{'cap_bytes':64*MIB,'receipt':regression_pin,
                'result':{'status':'REGRESSION_PASSED','cleanup_confirmed':True,
                    'physical_qualification':False,'receipt_sha256':regression_pin['sha256']}},
            'physical':{'driver_status':'CORROBORATED_MATCH','original_writer_termination':termination,
                'physical_cap_bytes':193*MIB,'writer_count_owned_by_driver':1,'power_loss_claim':False,
                'driver_receipt_path':driver['path'],'driver_receipt':{k:driver[k] for k in ('bytes','sha256')}}}
        physical_entries=[];phase_refs={}
        for name,count in (('setup',2),('regression',1),('physical',1)):
            value={'schema':'cellstore-repair-owner-phase-v1','phase':name,
                'status':'ACCEPTED_PHASE_PENDING_OUTER_TOOL_COMPLETION','outer_original_tool_terminal_required':True,
                'original_import_outer_guard_status':'FAIL_CLOSED','original_import_final_source_pin_verified':False,
                'source':{k:physical_source[k] for k in ('bytes','sha256')},'prior_failed_physical_preserved':True,
                'helper_cases_rerun':0,'production_qualification_changed':False,
                'prior_original_completion':{'session_id':int('@GENERATED_PRIOR_PROCESS_SESSION_ID@'),'exit_code':2},'limits':limits,
                'commands':[command(name+str(i)) for i in range(count)],'outcome':outcomes[name]}
            phase_refs[name]=ref(name+'.json',value)
        completion=ref('physical-completion.json',{'schema':'fresh-seam-original-tool-completion-v1',
            'terminal':True,'exit_code':0,'original_session_id':int('@GENERATED_STORAGE_PROCESS_SESSION_ID@'),'original_handle_waited':True,
            'cleanup':'CONFIRMED','owner_source_sha256':physical_source['sha256'],
            'phase_receipts':list(phase_refs.values())})
        for name in ('setup','regression','physical'):
            physical_entries.append({'phase':name,'receipt':phase_refs[name],
                'owner_source':physical_source,'completion':completion})
        prior=ref('prior-helper.json',{'schema':'cellstore-decoder-owner-phase-v1','phase':'setup',
            'status':'ACCEPTED_PHASE_PENDING_OUTER_TOOL_COMPLETION','outcome':{'helper_cases_passed':48},
            'commands':[command('prior'+str(i)) for i in range(3)]},
            digest='@GENERATED_STORAGE_HELPER_RESULT_SHA256@')
        versions={}
        for name,version in [('ffmpeg','@GENERATED_FFMPEG_VERSION_OUTPUT_SHA256@'),
                ('ffprobe','@GENERATED_FFPROBE_VERSION_OUTPUT_SHA256@')]:
            binary={'bytes':123,'sha256':sha(name.encode())}
            versions[name]={'binary':binary,'version_stdout':{'sha256':version}}
            cfg['inventory']['hash_inventory'].append({'path':cfg['paths'][name],**binary})
        decoder=ref('decoder-phase.json',{'schema':'cellstore-decoder-owner-phase-v1','phase':'decoder_only',
            'status':'ACCEPTED_PHASE_PENDING_OUTER_TOOL_COMPLETION','outer_original_tool_terminal_required':True,
            'original_import_outer_guard_status':'FAIL_CLOSED','original_import_final_source_pin_verified':False,
            'source':{k:decoder_source[k] for k in ('bytes','sha256')},'no_setup_or_physical_rerun':True,
            'physical_status_preserved':'FAILED','frozen_owner_source':{'bytes':int('@GENERATED_PRIOR_STORAGE_SOURCE_BYTES@'),
                'sha256':'@GENERATED_PRIOR_STORAGE_SOURCE_SHA256@'},
            'limits':limits,'commands':[command('decoder'+str(i)) for i in range(4)],
            'outcome':{'frames':336000,'pcm_bytes':672000,'pcm_sha256':'c'*64,
                'source_wave':{'bytes':672044,'sha256':binding.SOURCE_WAV_SHA},'provider_calls':0,
                'one_extra_frame_requested_eof_check':True,'full_static_dynamic_library_closure_claimed':False,
                'versions':versions}},digest='@GENERATED_DECODER_RESULT_SHA256@')
        decoder_completion=ref('decoder-completion.json',{'schema':'owner-decoder-acceptance-v1',
            'status':'ACCEPTED_DECODER_FUNCTIONAL_ONLY','all_original_commands_exit_zero_and_reaped':True,
            'model_execution_authorized':False,'original_tool':{'positive_terminal':True,'exit_code':0,'session_id':int('@GENERATED_DECODER_PROCESS_SESSION_ID@')},
            'receipt':decoder,'source':decoder_source},
            digest='@GENERATED_DECODER_COMPLETION_SHA256@')
        decoder_entries=[{'phase':'decoder_only','receipt':decoder,'owner_source':decoder_source,
            'completion':decoder_completion}]
        for kind,entries in [('cell_store',physical_entries),('decoder',decoder_entries)]:
            review=ref(kind+'-review.json',{'schema':'fresh-seam-prerequisite-review-v1','status':'ACCEPTED',
                'subject_sha256':binding.subject_digest(cfg),'kind':kind,'entries_sha256':sha(canonical(entries)),
                'import_guard_relabelled':False})
            accepted={'schema':'fresh-seam-prerequisite-acceptance-v1','kind':kind,'status':'ACCEPTED',
                'subject_sha256':binding.subject_digest(cfg),'entries':entries,
                'proof_config':proof_config if kind=='cell_store' else None,'review':review}
            if kind=='cell_store':accepted['prior_helper_setup']=prior
            cfg['prerequisites'][kind]=ref(kind+'-accepted.json',accepted)
        # Complete import/model-only metadata graph. No payload or runtime object exists.
        save(cfg['runtime_import'],{'schema':'independent-runtime-import-review-v1',
            'status':'ACCEPTED_IMPORT_ONLY_WITH_SEPARATE_FAILED_OUTER_GUARD',
            'raw_guard':{'status':'FAIL_CLOSED'},'execution_authorized':False})
        model_results={}
        assets=Path(cfg['workspace_root'])/'@GENERATED_ASSETS_ROOT_REL@'
        for name in ('audioset','sensevoice','native'):
            if name=='native':
                relative_library=Path(cfg['paths']['native_library']).relative_to(assets).as_posix()
                relative_install=Path(cfg['paths']['native_installation']).relative_to(assets).as_posix()
                names=[relative_library]+['vendor/native-diarization/explicit-synthetic-'+str(i) for i in range(88)]+[relative_install]
            else:names=['models/'+name+'/explicit-synthetic-'+str(i) for i in range(2)]
            rows=[{'path':relative,'bytes':i+1,'sha256':sha(relative.encode())} for i,relative in enumerate(names)]
            for row in rows:cfg['inventory']['hash_inventory'].append({'path':str(assets/row['path']),
                **{k:row[k] for k in ('bytes','sha256')}})
            result=ref(name+'-result.json',{'schema':'fixed-selected-model-result-v1','artifact':name,
                'status':'MATERIALIZED_PENDING_OWNER_ACCEPTANCE','model_loads':0,'model_calls':0,
                'native_runtime_qualified':False,'outputs':rows});model_results[name]=result
            save(cfg['model_acceptance'][name],{'schema':'owner-selected-model-acceptance-v1',
                'status':'ACCEPTED_MATERIALIZATION_ONLY','artifact':name,'cleanup':'CONFIRMED',
                'inference_authorized':False,'result':result})
            if name=='native':
                install={'path':cfg['paths']['native_installation'],**{k:rows[-1][k] for k in ('bytes','sha256')}}
                save(install,{'settings':{'native_diarization_model':cfg['paths']['native_model'],
                    'native_diarization_library':cfg['paths']['native_library'],'native_diarization_threads':2},
                    'materialization':'DATA_ONLY_NO_MODEL_LOAD_OR_NATIVE_QUALIFICATION'})
                cfg['inventory']['native_runtime_hashes']={Path(relative_library).name:rows[0]['sha256']}
        outer_source=source('outer.py','a'*64,123)
        cfg['prerequisites']['outer_guard']=ref('outer-contract.json',{'schema':'fresh-seam-outer-contract-v1',
            'status':'REVIEWED_DORMANT','subject_sha256':binding.subject_digest(cfg),'owner_source':outer_source,
            'limits':binding.LIMITS,'phases':cfg['phases'],'original_handle_retention':True,
            'pidfd_identity_checks':True,'cleanup_required':'CONFIRMED'})
        gate_calls=[];live_calls=[]
        with patch.object(launch,'read_pin',side_effect=fake_read),patch.object(binding,'read_pin',side_effect=fake_read),\
                patch.object(launch,'read_source_pin',side_effect=fake_source),patch.object(binding,'read_source_pin',side_effect=fake_source),\
                patch.object(launch,'verify_gate_receipt',side_effect=lambda c,s,ch:gate_calls.append(s)),\
                patch.object(launch,'verify_live_owner',side_effect=lambda *a:live_calls.append(True)):
            launch.verify_physical_prerequisite(cfg,'cell_store',lambda:None)
            launch.verify_physical_prerequisite(cfg,'decoder',lambda:None)
            binding.verify_prerequisites(cfg,canonical(cfg),lambda:None)
            test.assertEqual(gate_calls,['split','fixtures','hash_inventory']);test.assertEqual(live_calls,[True])
            mutations=[
                (decoder['path'],lambda d:d.update(physical_status_preserved='CORROBORATED_MATCH'),'decoder'),
                (decoder['path'],lambda d:d['commands'][0].update(positive_wait=False),'decoder'),
                (decoder['path'],lambda d:d['source'].update(sha256='0'*64),'decoder'),
                (decoder['path'],lambda d:d['outcome']['versions']['ffprobe']['version_stdout'].update(sha256='0'*64),'decoder'),
                (decoder_completion['path'],lambda d:d['original_tool'].update(exit_code=2),'decoder'),
                (driver['path'],lambda d:d.update(status='FAILED'),'cell_store'),
                (driver['path'],lambda d:d['termination'].update(reaped=False),'cell_store'),
                (driver['path'],lambda d:d['recovery']['database'].update(sha256='0'*64),'cell_store'),
                (phase_refs['setup']['path'],lambda d:d['prior_original_completion'].update(exit_code=0),'cell_store'),
                (completion['path'],lambda d:d.update(exit_code=2),'cell_store')]
            for path,mutate,kind in mutations:
                before=copy.deepcopy(store[path]['value']);mutate(store[path]['value'])
                with test.assertRaises(Refused):launch.verify_physical_prerequisite(cfg,kind,lambda:None)
                store[path]['value']=before
            # Exact record, source and digest identities must match the independent validation.
            accepted_path=cfg['prerequisites']['decoder']['path']
            accepted=store[accepted_path]['value'];entry=accepted['entries'][0]
            for field in ('receipt','owner_source','completion'):
                before=copy.deepcopy(entry[field]);entry[field]['sha256']='0'*64
                review_path=accepted['review']['path'];old_review=copy.deepcopy(store[review_path]['value'])
                store[review_path]['value']['entries_sha256']=sha(canonical(accepted['entries']))
                with test.assertRaises(Refused):launch.verify_physical_prerequisite(cfg,'decoder',lambda:None)
                entry[field]=before;store[review_path]['value']=old_review
            for path,mutate in [
                    (cfg['runtime_import']['path'],lambda d:d['raw_guard'].update(status='COMPLETE')),
                    (cfg['runtime_import']['path'],lambda d:d.update(execution_authorized=True)),
                    (cfg['model_acceptance']['audioset']['path'],lambda d:d.update(inference_authorized=True)),
                    (model_results['sensevoice']['path'],lambda d:d['outputs'][0].update(sha256='0'*64)),
                    (model_results['native']['path'],lambda d:d['outputs'].append(copy.deepcopy(d['outputs'][0]))),
                    (store[accepted_path]['value']['review']['path'],lambda d:d.update(import_guard_relabelled=True))]:
                before=copy.deepcopy(store[path]['value']);mutate(store[path]['value'])
                with test.assertRaises(Refused):binding.verify_prerequisites(cfg,canonical(cfg),lambda:None)
                store[path]['value']=before
        # Independently cover real bounded metadata pin checking, rather than only its mock.
        raw=canonical({'explicit_synthetic_metadata':True})
        pin={'path':'/explicit-synthetic-metadata.json','bytes':len(raw),'sha256':sha(raw)}
        with patch.object(binding,'ordinary_path',side_effect=lambda p,*a:Path(p)),\
                patch.object(binding,'bounded_read',return_value=raw):
            test.assertEqual(binding.read_pin(pin),{'explicit_synthetic_metadata':True})
            for bad in (dict(pin,sha256='0'*64),dict(pin,bytes=len(raw)+1)):
                with test.assertRaisesRegex(Refused,'metadata_pin_mismatch'):binding.read_pin(bad)
