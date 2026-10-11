#!/usr/bin/env python3
"""Finite model-free readiness fixtures. ALL acoustic producers below are fake.

Prepared only. The parent may run this exact list only under a separately reviewed
model-free outer guard. One positive subprocess fixture launches isolated Python
with no product/model imports; no real acoustic input or SDK is invoked.
"""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ctypes
import hashlib
import json
import os
import subprocess
import tempfile
import time
import types
import unittest
from unittest.mock import patch
from common import Admission, Deadlines, Refused, FRAMES, MIB, bounded_read, retain_raw, sha
from ownership import Ledger
from capture import assert_primary_equal, no_new_calls
from instrumentation import Instrumentation


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


class AdmissionFixtures(unittest.TestCase):
    def test_held_admission_lock_refuses_without_original_dispatch(self):
        calls=[];checks=[]
        gate=Admission({'call':1},lambda:checks.append('deadline'))
        class DeterministicallyHeldLock:
            held=True
            def acquire(self,timeout):
                self.timeout=timeout
                return not self.held
            def release(self):raise AssertionError('an unacquired held lock cannot be released')
        gate.lock=DeterministicallyHeldLock()
        with self.assertRaisesRegex(Refused,'admission_lock_deadline'):
            gate.invoke('call',lambda:calls.append('must never dispatch'))
        self.assertEqual(gate.lock.timeout,.05)
        self.assertEqual(calls,[])
        self.assertEqual(gate.counts['call']['entered'],0)
        self.assertEqual(gate.counts['call']['returned'],0)
        self.assertEqual(gate.counts['call']['accepted'],0)

    def test_delayed_entry_journal_refuses_before_original_dispatch(self):
        clock=FakeClock();calls=[]
        def check():
            if clock.now>=101:raise Refused('expired_before_delegation')
        def emit(event,**kwargs):
            if event=='entered':clock.now=101
        gate=Admission({'call':1},check,emit=emit)
        with self.assertRaisesRegex(Refused,'expired_before_delegation'):
            gate.invoke('call',lambda:calls.append('must never dispatch'))
        self.assertEqual(calls,[])
        self.assertEqual(gate.counts['call'],{'entered':1,'returned':0,'accepted':0,'rejected':1,'exceptions':0})

    def test_exact_four_rpc_original_arguments_and_identity(self):
        calls=[]
        original_result=object()
        def original(*a, **kw):
            calls.append((a,kw));return original_result
        names=['reset','push','finish','turns']
        gate=Admission(dict.fromkeys(names,1),lambda:None,sequence=names)
        for name in names:
            self.assertIs(gate.invoke(name,original,name,key='unchanged'),original_result)
        self.assertEqual(calls,[((n,),{'key':'unchanged'}) for n in names])
        self.assertTrue(all(v['accepted']==1 for v in gate.counts.values()))

    def test_fifth_rpc_never_invokes_original(self):
        calls=[]
        gate=Admission({'reset':1},lambda:None)
        gate.invoke('reset',lambda:calls.append('original'))
        with self.assertRaises(Refused):gate.invoke('reset',lambda:calls.append('extra'))
        self.assertEqual(calls,['original']);self.assertEqual(gate.counts['reset']['rejected'],1)

    def test_wrong_sequence_never_invokes_original(self):
        calls=[]
        gate=Admission({'reset':1,'push':1},lambda:None,sequence=['reset','push'])
        with self.assertRaises(Refused):gate.invoke('push',lambda:calls.append('wrong'))
        self.assertEqual(calls,[]);self.assertEqual(gate.counts['push']['entered'],0)
        self.assertEqual(gate.counts['push']['rejected'],1)

    def test_all_sdk_ceilings_are_pre_entry(self):
        for name,cap in [('event_compute',7),('embedding_compute',8),('sensevoice_decode',32)]:
            calls=[];gate=Admission({name:cap},lambda:None)
            for _ in range(cap):gate.invoke(name,lambda:calls.append('fake SDK'))
            with self.assertRaises(Refused):gate.invoke(name,lambda:calls.append('extra'))
            self.assertEqual(len(calls),cap);self.assertEqual(gate.counts[name]['rejected'],1)

    def test_native_ABI_sequence_and_extra_read_rejection(self):
        names=['create','open']+['push']*10+['finish','segments','segments']
        gate=Admission({'create':1,'open':1,'push':10,'finish':1,'segments':2},lambda:None,sequence=names)
        calls=[]
        for name in names:gate.invoke(name,lambda name=name:calls.append(name))
        with self.assertRaises(Refused):gate.invoke('segments',lambda:calls.append('extra'))
        self.assertEqual(calls,names)

    def test_original_exception_object_preserved(self):
        error=RuntimeError('explicit fake original exception')
        gate=Admission({'call':1},lambda:None)
        def original():raise error
        with self.assertRaises(RuntimeError) as got:gate.invoke('call',original)
        self.assertIs(got.exception,error)
        self.assertEqual(gate.counts['call'],{'entered':1,'returned':0,'accepted':0,'rejected':0,'exceptions':1})

    def test_late_return_keeps_entered_returned_but_not_accepted(self):
        clock=FakeClock()
        def check():
            if clock.now>=101:raise Refused('late')
        gate=Admission({'call':1},check)
        calls=[]
        def original():calls.append(True);clock.now=101;return {'actual':'fake return'}
        with self.assertRaises(Refused):gate.invoke('call',original)
        self.assertEqual(calls,[True]);self.assertEqual(gate.counts['call']['returned'],1)
        self.assertEqual(gate.counts['call']['accepted'],0)

    def test_missing_work_authority_and_nonfinite_deadline_refused(self):
        with self.assertRaises(Refused):Deadlines({})
        env={'GUARD_WORK_END':'nan','GUARD_ABSOLUTE_END':'900','REAL_CAPTURE_POSTHASH_END':'800',
             'GUARD_STOP_PATH':'/deliberately-absent'}
        with self.assertRaises(Refused):Deadlines(env,clock=lambda:100.)

    def test_deadline_reserves_exact_and_preexpired(self):
        env={'GUARD_WORK_END':'840','GUARD_ABSOLUTE_END':'900','REAL_CAPTURE_POSTHASH_END':'840',
             'GUARD_STOP_PATH':'/deliberately-absent'}
        deadline=Deadlines(env,clock=lambda:100.)
        self.assertEqual(deadline.call_end(120),220.)
        bad=dict(env,REAL_CAPTURE_POSTHASH_END='839')
        with self.assertRaises(Refused):Deadlines(bad,clock=lambda:100.)
        with self.assertRaises(Refused):Deadlines(env,clock=lambda:720.)

    def test_distinct_return_deadline_applies_before_acceptance(self):
        calls=[];gate=Admission({'call':1},lambda:None)
        def expired():raise Refused('RPC120 expired though outer still alive')
        with self.assertRaises(Refused):gate.invoke('call',lambda:calls.append(True),return_check=expired)
        self.assertEqual(calls,[True]);self.assertEqual(gate.counts['call']['accepted'],0)

    def test_admission_shape_failure_rejects_without_original(self):
        calls=[];gate=Admission({'call':1},lambda:None)
        def invalid():raise Refused('shape')
        with self.assertRaises(Refused):gate.invoke('call',lambda:calls.append(True),before_entry=invalid)
        self.assertEqual(calls,[]);self.assertEqual(gate.counts['call']['rejected'],1)


class RawAndControlFixtures(unittest.TestCase):
    def test_malformed_original_bytes_retained_without_parsing(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'raw';p.write_bytes(b'{malformed fake result\x00')
            receipt=retain_raw(p,Path(td)/'kept')
            self.assertTrue(receipt['complete_raw'])
            self.assertEqual((Path(td)/'kept').read_bytes(),p.read_bytes())
            self.assertEqual(receipt['full_sha256'],sha(p.read_bytes()))

    def test_oversize_retains_prefix_with_honest_incomplete_hash(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'raw';p.write_bytes(b'x'*100)
            receipt=retain_raw(p,Path(td)/'kept',maximum=32,prefix=8)
            self.assertFalse(receipt['complete_raw']);self.assertIsNone(receipt['full_sha256'])
            self.assertEqual(receipt['observed_size'],100);self.assertEqual(receipt['retained_bytes'],8)
            self.assertEqual((Path(td)/'kept').read_bytes(),b'x'*8)

    def test_absent_raw_diagnostic_is_not_claimed_complete(self):
        with tempfile.TemporaryDirectory() as td:
            receipt=retain_raw(Path(td)/'absent',Path(td)/'kept')
            self.assertEqual(receipt['state'],'not_present');self.assertFalse(receipt['complete_raw'])

    def test_symlink_and_growth_ceiling_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'real';p.write_bytes(b'xx');q=Path(td)/'alias';q.symlink_to(p)
            with self.assertRaises(OSError):bounded_read(q,10)
            with self.assertRaises(Refused):bounded_read(p,1)

    def test_primary_comparison_includes_whole_snapshot_hash(self):
        first={'snapshot':{'bytes':10,'sha256':'a'}}
        assert_primary_equal(first,first)
        with self.assertRaises(Refused):assert_primary_equal(first,{'snapshot':{'bytes':10,'sha256':'b'}})

    def test_restart_call_and_launch_counters_must_stay_zero(self):
        good={'counts':{'rpc':{'reset':{'entered':0,'rejected':0}}},'launches':{'native':0}}
        no_new_calls(good)
        bad={'counts':{'rpc':{'reset':{'entered':1,'rejected':0}}},'launches':{'native':0}}
        with self.assertRaises(Refused):no_new_calls(bad)
        with self.assertRaises(Refused):no_new_calls(dict(good,launches={'native':1}))


class SnapshotFixtures(unittest.TestCase):
    def test_first_whole_snapshot_only_and_all_table_comparison(self):
        import sqlite3
        from server import snapshot
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dbpath=root/'fake.sqlite3'
            with sqlite3.connect(dbpath) as db:
                db.executescript('CREATE TABLE a(id INTEGER PRIMARY KEY,text TEXT); CREATE TABLE b(x BLOB,y REAL);')
                db.execute('INSERT INTO a VALUES(?,?)',(1,'explicit fake UTF8 中文'))
                db.execute('INSERT INTO b VALUES(?,?)',(bytes(range(16)),1.25))
            store=types.SimpleNamespace(path=dbpath);path=root/'first.snapshot'
            first=snapshot(store,path,lambda:None);original=path.read_bytes()
            same=snapshot(store,path,lambda:None)
            self.assertTrue(first['retained']);self.assertFalse(same['retained'])
            self.assertEqual(first['sha256'],same['sha256']);self.assertEqual(path.read_bytes(),original)
            with sqlite3.connect(dbpath) as db:db.execute('UPDATE b SET y=2.5')
            changed=snapshot(store,path,lambda:None)
            self.assertNotEqual(first['sha256'],changed['sha256'])
            self.assertEqual(list(root.glob('*.snapshot')),[path])

    def test_SQL_row_byte_gate_rejects_before_export(self):
        import sqlite3
        from server import snapshot
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dbpath=root/'fake.sqlite3'
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE a(x TEXT)');db.execute('INSERT INTO a VALUES(?)',('x'*1025,))
            with patch('server.MIB',128):
                with self.assertRaises(Refused):snapshot(types.SimpleNamespace(path=dbpath),root/'partial',lambda:None)


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
        # This fixture is dormant until the parent launches the reviewed model-free suite.
        with tempfile.TemporaryDirectory() as td:
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
        with tempfile.TemporaryDirectory() as td:
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
        with tempfile.TemporaryDirectory() as td:
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
        with tempfile.TemporaryDirectory() as td:
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


class FakeNativeABI(unittest.TestCase):
    def test_wrapped_fake_ABI_original_calls_and_pre_entry_overcap(self):
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
        with patch.dict(sys.modules,fake_modules),patch('instrumentation.FLOAT_SHA',zero_sha):
            instrument.install_native();obj=FakeCAPI('explicit-fake-library')
            obj.lib.nemo_speech_diar_create(None,None);obj.lib.nemo_speech_diar_stream_open(None,None)
            block=(ctypes.c_float*32000)()
            ptr=ctypes.cast(block,ctypes.POINTER(ctypes.c_float))
            for _ in range(9):obj.lib.nemo_speech_diar_stream_push_f32(None,ptr,32000,16000)
            obj.lib.nemo_speech_diar_stream_push_f32(None,ptr,19040,16000)
            obj.lib.nemo_speech_diar_stream_finish(None)
            obj.lib.nemo_speech_diar_segments(None,None,None,0,None)
            obj.lib.nemo_speech_diar_segments(None,None,None,0,None)
            with self.assertRaises(Refused):obj.lib.nemo_speech_diar_segments(None,None,None,0,None)
        self.assertEqual(len(calls),15)
        self.assertEqual(instrument.abi.counts['native_segments']['rejected'],1)
        self.assertEqual(instrument.native_frames,FRAMES)


class FakeSDKFixtures(unittest.TestCase):
    def test_actual_wrappers_preserve_fake_sdk_objects_and_block_excess(self):
        calls=[]
        class DType:
            byteorder='<'
            def __str__(self):return 'float32'
        class Samples:
            ndim=1;dtype=DType()
            def __init__(self,frames):self.frames=frames
            def __len__(self):return self.frames
            def tobytes(self,order='C'):return bytes(self.frames*4)
        class Stream:
            def accept_waveform(self,*a,**kw):calls.append(('accept',self,a,kw))
        class Tagger:
            def __init__(self,*a,**kw):calls.append(('event_constructor',a,kw))
            def create_stream(self):return Stream()
            def compute(self,stream):calls.append(('event_compute',stream));return self
        class Extractor:
            def __init__(self,*a,**kw):calls.append(('embedding_constructor',a,kw))
            def create_stream(self):return Stream()
            def compute(self,stream):calls.append(('embedding_compute',stream));return self
            def is_ready(self,stream):return True
        class Recognizer:
            @classmethod
            def from_sense_voice(cls,*a,**kw):calls.append(('sensevoice_constructor',a,kw));return cls()
            def create_stream(self):return Stream()
            def decode_stream(self,stream):calls.append(('sensevoice_decode',stream));return self
        class Analyzer:
            def __init__(self,*a,**kw):calls.append(('analyzer_constructor',a,kw))
            def _events(self,*a,**kw):return None
            def _embed_turns(self,*a,**kw):return None
            def _transcribe_sensevoice(self,*a,**kw):return None
            def _recover_missing_speech(self,*a,**kw):raise AssertionError('must never enter fake recovery')
            def _diarize(self,*a,**kw):raise AssertionError('must never enter fake alternate diarizer')
        clean_audio=Samples(32000)
        def clean(*a,**kw):return clean_audio,{'explicit_fake_clean_frames':[0,32000]}
        for cls in (Stream,Tagger,Extractor,Recognizer,Analyzer):
            for name,value in vars(cls).items():
                if isinstance(value,classmethod):value=value.__func__
                if callable(value):value.__module__='explicit_fake_producer'
        clean.__module__='explicit_fake_producer'
        sdk=types.ModuleType('sherpa_onnx');sdk.AudioTagging=Tagger
        sdk.SpeakerEmbeddingExtractor=Extractor;sdk.OfflineRecognizer=Recognizer
        evidence=types.ModuleType('audio_backend.speaker_evidence');evidence.clean_speaker_audio=clean
        analyzer=types.ModuleType('audio_backend.inference.analyzer');analyzer.AudioAnalyzer=Analyzer
        modules={'sherpa_onnx':sdk,'audio_backend':types.ModuleType('audio_backend'),
                 'audio_backend.speaker_evidence':evidence,'audio_backend.inference':types.ModuleType('audio_backend.inference'),
                 'audio_backend.inference.analyzer':analyzer,'explicit_fake_producer':types.ModuleType('explicit_fake_producer')}
        spec={'output_dir':'/unused-model-free','site_packages':str(HERE),'installed_files':{str(Path(__file__).resolve()):{}}}
        modules['explicit_fake_producer'].__file__=__file__
        instrument=Instrumentation(spec,FakeDeadline(),FakeJournal(),'analyzer')
        instrument.check=lambda:None;instrument.sdk.check=lambda:None
        with patch.dict(sys.modules,modules):
            instrument.install_sdk()
            Analyzer({'explicit':'fake'})
            tagger=sdk.AudioTagging('same-config')
            audio=Samples(80000)
            for _ in range(7):
                stream=tagger.create_stream();stream.accept_waveform(sample_rate=16000,waveform=audio)
                self.assertIs(tagger.compute(stream),tagger)
            with self.assertRaises(Refused):tagger.compute(stream)
            evidence.clean_speaker_audio(Samples(FRAMES),[],'fake-label')
            extractor=sdk.SpeakerEmbeddingExtractor('same-config')
            for _ in range(8):
                clean_stream=extractor.create_stream()
                clean_stream.accept_waveform(sample_rate=16000,waveform=clean_audio)
                self.assertIs(extractor.compute(clean_stream),extractor)
            with self.assertRaises(Refused):extractor.compute(clean_stream)
            recognizer=sdk.OfflineRecognizer.from_sense_voice(model='fake-model',provider='cpu')
            for _ in range(32):
                speech=recognizer.create_stream();speech.accept_waveform(16000,Samples(100))
                self.assertIs(recognizer.decode_stream(speech),recognizer)
            with self.assertRaises(Refused):recognizer.decode_stream(speech)
        self.assertEqual(sum(x[0]=='event_compute' for x in calls),7)
        self.assertEqual(sum(x[0]=='embedding_compute' for x in calls),8)
        self.assertEqual(sum(x[0]=='sensevoice_decode' for x in calls),32)
        self.assertIs(next(x for x in calls if x[0]=='accept')[3]['waveform'],audio)
        self.assertEqual(instrument.sdk.counts['sensevoice_decode']['rejected'],1)
        self.assertEqual(instrument.streams,{})


if __name__=='__main__':
    # Exact finite list; do not discover/import production tests or packages.
    suite=unittest.TestSuite()
    for cls in (AdmissionFixtures,RawAndControlFixtures,SnapshotFixtures,OwnershipFixtures,FakeNativeABI,FakeSDKFixtures):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
    print(json.dumps({'label':'MODEL_FREE_EXPLICIT_FAKE_PRODUCERS','tests_run':result.testsRun,
                      'failures':len(result.failures),'errors':len(result.errors),
                      'skips':len(result.skipped),'actual_acoustic_calls':0}),flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
