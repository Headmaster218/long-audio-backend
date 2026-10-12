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
        env={'GUARD_WORK_END':'1540','GUARD_ABSOLUTE_END':'1600','REAL_CAPTURE_POSTHASH_END':'1540',
             'GUARD_STOP_PATH':'/deliberately-absent'}
        deadline=Deadlines(env,clock=lambda:100.)
        self.assertEqual(deadline.call_end(120),220.)
        bad=dict(env,REAL_CAPTURE_POSTHASH_END='1539')
        with self.assertRaises(Refused):Deadlines(bad,clock=lambda:100.)
        with self.assertRaises(Refused):Deadlines(env,clock=lambda:1420.)

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
        with retained_root() as td:
            p=Path(td)/'raw';p.write_bytes(b'{malformed fake result\x00')
            receipt=retain_raw(p,Path(td)/'kept')
            self.assertTrue(receipt['complete_raw'])
            self.assertEqual((Path(td)/'kept').read_bytes(),p.read_bytes())
            self.assertEqual(receipt['full_sha256'],sha(p.read_bytes()))

    def test_oversize_retains_prefix_with_honest_incomplete_hash(self):
        with retained_root() as td:
            p=Path(td)/'raw';p.write_bytes(b'x'*100)
            receipt=retain_raw(p,Path(td)/'kept',maximum=32,prefix=8)
            self.assertFalse(receipt['complete_raw']);self.assertIsNone(receipt['full_sha256'])
            self.assertEqual(receipt['observed_size'],100);self.assertEqual(receipt['retained_bytes'],8)
            self.assertEqual((Path(td)/'kept').read_bytes(),b'x'*8)

    def test_absent_raw_diagnostic_is_not_claimed_complete(self):
        with retained_root() as td:
            receipt=retain_raw(Path(td)/'absent',Path(td)/'kept')
            self.assertEqual(receipt['state'],'not_present');self.assertFalse(receipt['complete_raw'])

    def test_symlink_and_growth_ceiling_fail_closed(self):
        with retained_root() as td:
            import common
            p=Path(td)/'real';p.write_bytes(b'xx');q=Path(td)/'mocked-alias'
            observed=[]
            def nofollow_open(path,flags):
                self.assertEqual(Path(path),q);self.assertTrue(flags & os.O_NOFOLLOW)
                observed.append((Path(path),flags))
                raise OSError(errno.ELOOP,'explicit mocked no-follow symbolic-link rejection')
            proxy=types.SimpleNamespace(**{name:getattr(os,name) for name in
                ('O_RDONLY','O_NOFOLLOW','O_NONBLOCK','O_CLOEXEC','fstat','read','close')},open=nofollow_open)
            with patch.object(common,'os',proxy):
                with self.assertRaises(OSError) as rejected:bounded_read(q,10)
            self.assertEqual(rejected.exception.errno,errno.ELOOP);self.assertEqual(len(observed),1)
            self.assertFalse(q.exists())
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
        with retained_root() as td:
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
        with retained_root() as td:
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



class ApiSmokeFixtures(unittest.TestCase):
    def deadline(self,clock=None):
        clock=clock or FakeClock()
        value=Deadlines({'GUARD_WORK_END':'1540','GUARD_ABSOLUTE_END':'1600',
            'REAL_CAPTURE_POSTHASH_END':'1540','GUARD_STOP_PATH':'/explicitly-absent-fixture-stop'},clock=clock)
        return clock,value

    def test_dev5_runtime_closed_pins_reject_old_source(self):
        import launch
        freeze={'product_source_files':{'a.py':'a'*64},'product_source_count':331,'product_source_bytes':4098577}
        spec={'source_root':str(launch.SOURCE),'source_files':freeze['product_source_files']}
        launch.validate_source_binding(spec,freeze)
        with self.assertRaises(ValueError):launch.validate_source_binding(dict(spec,source_root=str(launch.WORKSPACE/'reconstruction-rc3-dev3')),freeze)
        with self.assertRaises(ValueError):launch.validate_source_binding(dict(spec,source_files={}),freeze)
        self.assertEqual(launch.PYTHON,launch.RUNTIME/'bin/python3.12')
        self.assertEqual(len(launch.source_names()),8)
        self.admission_integration()

    def admission_integration(self):
        """Real strict pin readers with fixture-local metadata; no product/resource launch."""
        import launch,capture
        original_profile=launch.profile_data()
        imported_raw=(HERE.parent/'readiness/COMBINED_IMPORT_TERMINAL.json').read_bytes()
        storage_raw=(HERE.parent/'readiness/STORAGE_INITIAL_ADMISSION.json').read_bytes()
        runtime_raw=Path(original_profile['runtime_inventory']['path']).read_bytes()
        tree=ast.parse(Path(launch.GUARD).read_text())
        readers=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('read_regular','read_pin_json')]
        self.assertEqual(len(readers),2)
        with retained_root() as td:
            base=Path(td);root=base/'smoke';root.mkdir();(root/'controller').mkdir()
            source=base/'reconstruction-rc3-dev5';source.mkdir()
            run=root/'runs/run-PRIVATE_TEMPLATE';out=run/'tmp/smoke';out.mkdir(parents=True)
            def put(path,value):
                path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
                raw=value if type(value) is bytes else launch.canonical(value)
                path.write_bytes(raw);return {'path':str(path),'sha256':sha(raw)}
            def check(end):
                if time.monotonic()>=end:raise ValueError('fixture_reader_deadline')
            ns=dict(Path=Path,os=os,stat=stat,hashlib=hashlib,json=json,
                    require=launch.require,clock_check=check,CHECKS=root)
            exec(compile(ast.Module(body=readers,type_ignores=[]),str(launch.GUARD),'exec'),ns)
            end=time.monotonic()+30
            class Guard:
                def verify_capture_posthash(self,end):raise AssertionError('adapter missing')
                def ownership_unchanged(self):return 'sentinel'
            original_method=Guard.ownership_unchanged
            g=types.SimpleNamespace(WORKSPACE=launch.WORKSPACE,MAX_DISK=12*2**30,ACTUAL_RSS=4*2**30,
                ACTUAL_VISIBLE_RSS=6*2**30,ACTUAL_AS=8*2**30,CLEANUP_RESERVE=60,OLD_UNKNOWN_DISK=320*2**20,
                ORDINARY_FREE_FLOOR=11*2**29,POLL=.05,RECEIPT_RESERVE=16384,Guard=Guard,
                scratch_bytes=lambda *a,**k:0,clock_check=check,read_pin_json=ns['read_pin_json'],
                read_regular=ns['read_regular'])
            product_rows=[]
            for i in range(331):
                path=source/f'explicit-fixture-{i:03}.txt';pin=put(path,b'model-free fixture byte\n')
                product_rows.append(dict(pin,bytes=path.stat().st_size))
            source_map={Path(row['path']).name:row['sha256'] for row in product_rows}
            freeze={'product_source_files':source_map,'harness_files':[],'metadata_files':[]}
            frozen=put(root/'SOURCE_FREEZE.json',freeze)['sha256']
            storage=put(root/'readiness/storage.json',storage_raw)
            imported=put(root/'readiness/import.json',imported_raw)
            runtime_pin=put(root/'runtime-metadata.json',runtime_raw)
            chosen=dict(original_profile,immutable_assets=[product_rows[0]],immutable_host_files=[],evidence_pins=[],
                model_settings={},storage_admission=storage,runtime_import_local=imported,runtime_inventory=runtime_pin)
            runtime=json.loads(runtime_raw)
            rows=product_rows+[dict(path=str(launch.RUNTIME/row['destination']),bytes=row['bytes'],sha256=row['sha256'])
                               for row in runtime['files']]
            inv={'schema':'dev5-api-smoke-immutable-inventory-v1','source_freeze_sha256':frozen,
                'hash_inventory':rows,'inventory_sha256':sha(launch.canonical(sorted(rows,key=lambda row:row['path'])))}
            invpin=put(root/'HASH_INVENTORY.json',inv)
            fixtures={'schema':'dev5-api-smoke-fixtures-terminal-v1','status':'PASS','cleanup':'CONFIRMED',
                'tests_run':41,'failures':0,'errors':0,'actual_acoustic_calls':0,'original_tool_wait_reaped':True,
                'source_freeze_sha256':frozen,'fixture_metadata_only':True}
            fixturepin=put(root/'readiness/fixtures.json',fixtures)
            hashed={'schema':'dev5-api-smoke-hash-terminal-v1','status':'PASS_HASH_ONLY','cleanup':'CONFIRMED',
                'all_matched':True,'original_tool_wait_reaped':True,'source_freeze_sha256':frozen,
                'inventory_manifest_sha256':invpin['sha256'],'inventory_sha256':inv['inventory_sha256'],
                'files':len(rows),'bytes':sum(row['bytes'] for row in rows),'elapsed_seconds':.01,
                'product_imports':0,'audio_decodes':0,'services':0,'model_calls':0,'fixture_metadata_only':True}
            hashpin=put(root/'readiness/hash.json',hashed)
            authority={'schema':'dev5-api-smoke-authority-v1','actual_capture_approved':True,
                'scope':'one-fresh-primary-and-one-moss-job','source_head':'e6791e6d1b78a14c113529d809b8486349be1146',
                'source_freeze_sha256':frozen,'authorization_reference':'Explicit fake fixture; no executable authority'}
            authoritypin=put(root/'ACTUAL_AUTHORITY.json',authority)
            controller={'schema':'dev5-api-smoke-controller-v1','actual_run_authorized':True,
                'source_root':str(source),'source_file_count':331,'source_head':authority['source_head'],
                'source_tree':'0aa2ac344d4513d9e9cfd68897957303f92e550d','source_freeze_sha256':frozen,
                'approval_receipt':authoritypin,'output_dir':str(out),'data_dir':str(out/'data'),
                'python':str(launch.PYTHON),'outer_seconds':1500,'primary_seconds':600,'posthash_seconds':120,
                'cleanup_seconds':60,'identical_key_replay':False,'input_wav':product_rows[0]['path'],'settings':{},
                'hash_inventory':rows,'source_files':{row['path']:row['sha256'] for row in product_rows},
                'inventory_manifest':invpin,'storage_admission':{'receipt':storage},
                'installed_runtime':{'receipt':imported},'posthash_probe':{'receipt':hashpin},
                'model_free_tests':{'receipt':fixturepin},'positive_owned_probe':{'receipt':fixturepin}}
            spec={'stage':'api_smoke_actual','argv':[str(launch.PYTHON),'-I','-B','-S',str(root/'controller/bootstrap.py'),
                '--mode','controller','--spec',str(root/'controller.actual.json')]}
            def bind():
                pin=put(root/'controller.actual.json',controller)
                spec.update(controller_spec=pin,final_manifest=pin)
            with patch.multiple(launch,ROOT=root,HERE=root/'controller',SOURCE=source,RUN=run),\
                    patch.object(launch,'profile_data',return_value=chosen):
                launch.configure(g);self.assertIs(Guard.ownership_unchanged,original_method)
                spec['external_files']=dict.fromkeys(launch.common_names()|{str(root/'controller.actual.json'),
                    str(root/'ACTUAL_AUTHORITY.json'),invpin['path'],fixturepin['path'],hashpin['path'],
                    storage['path'],imported['path']},'fixture')
                bind();accepted=g.actual_admission(spec,end)
                self.assertEqual(accepted[1],controller);self.assertEqual(accepted[3],run)
                with self.assertRaisesRegex(ValueError,'json_pin_shape'):
                    g.read_pin_json(dict(imported,bytes=len(imported_raw)),end)
                with self.assertRaisesRegex(ValueError,'json_pin_outside_private_prep'):
                    g.read_pin_json({'path':str(HERE.parent/'readiness/COMBINED_IMPORT_TERMINAL.json'),
                        'sha256':imported['sha256']},end)
                controller['approval_receipt']=dict(authoritypin,path=str(root/'absent-authority.json'));bind()
                with self.assertRaises(FileNotFoundError):g.actual_admission(spec,end)
                controller['approval_receipt']=authoritypin;bind()
                put(authoritypin['path'],dict(authority,actual_capture_approved=False))
                with self.assertRaisesRegex(ValueError,'json_pin_mismatch'):g.actual_admission(spec,end)
                controller['approval_receipt']=put(authoritypin['path'],dict(authority,actual_capture_approved=False));bind()
                with self.assertRaisesRegex(ValueError,'separate_exact_root_GO'):g.actual_admission(spec,end)
                controller['approval_receipt']=put(authoritypin['path'],authority);bind()
                controller['posthash_probe']['receipt']=dict(hashpin,path=str(root/'absent-hash.json'));bind()
                with self.assertRaises(FileNotFoundError):g.actual_admission(spec,end)
                controller['posthash_probe']['receipt']=hashpin;bind()
                put(hashpin['path'],dict(hashed,inventory_sha256='0'*64))
                with self.assertRaisesRegex(ValueError,'json_pin_mismatch'):g.actual_admission(spec,end)
                controller['posthash_probe']['receipt']=put(hashpin['path'],dict(hashed,inventory_sha256='0'*64));bind()
                with self.assertRaisesRegex(ValueError,'exact_full_inventory_hash_terminal_required'):g.actual_admission(spec,end)
                controller['posthash_probe']['receipt']=put(hashpin['path'],hashed);bind()
                self.assertEqual(g.actual_admission(spec,end)[1],controller)
                # Same receipt bytes stay prospective; a generic PASS wrapper is rejected.
                bad=put(root/'readiness/generic-storage.json',dict(json.loads(storage_raw),status='PASS'))
                with patch.object(launch,'profile_data',return_value=dict(chosen,storage_admission=bad)):
                    controller['storage_admission']['receipt']=bad;bind()
                    with self.assertRaisesRegex(ValueError,'prospective_storage_schema'):g.actual_admission(spec,end)
                controller['storage_admission']['receipt']=storage;bind()
                post={'all_matched':True,'source_files':331,'files':len(rows),
                      'bytes':sum(row['bytes'] for row in rows),'elapsed_seconds':.01}
                value={'status':'COMPLETE','posthash':post,'actual_native_selected':True,'one_moss_job':True}
                put(out/'posthash.json',post);put(out/'capture-result.json',value)
                owner=Guard();owner.controller=controller;owner.receipt={}
                owner.verify_capture_posthash(end);self.assertTrue(owner.receipt['controller_full_posthash_verified'])
                put(out/'posthash.json',dict(post,files=len(rows)-1))
                with self.assertRaisesRegex(ValueError,'mandatory_complete_full_posthash'):owner.verify_capture_posthash(end)
                owner.controller=dict(controller,output_dir=str(root/'missing-output'))
                with self.assertRaises(FileNotFoundError):owner.verify_capture_posthash(end)
            # Exercise unchanged real no-follow hash reader on 331 tiny ordinary fixtures.
            tiny=dict(inv,hash_inventory=product_rows,source_root=str(source),source_files=controller['source_files'],
                installed_files={},runtime_files={},harness_files={},input_wav=product_rows[0]['path'])
            tiny['inventory_sha256']=sha(launch.canonical(sorted(product_rows,key=lambda row:row['path'])))
            tinypin=put(root/'HASH_INVENTORY.json',tiny)
            hash_run=root/'runs/hash-fixture';(hash_run/'tmp').mkdir(parents=True)
            now=time.monotonic();env={'GUARD_ABSOLUTE_END':str(now+300),'GUARD_WORK_END':str(now+240),
                'GUARD_STOP_PATH':str(hash_run/'STOP'),'GUARD_RUN_DIRECTORY':str(hash_run)}
            argv=['capture.py','--hash-inventory',tinypin['path'],'--hash-inventory-sha256',tinypin['sha256']]
            with patch.object(capture,'__file__',str(root/'controller/capture.py')),patch.object(sys,'argv',argv),\
                    patch.dict(os.environ,env):
                capture.hash_only_main()
                result=json.loads((hash_run/'tmp/hash-only/hash-only-result.json').read_text())
                self.assertEqual(result['files'],331);self.assertEqual(result['status'],'PASS_HASH_ONLY')
                self.assertEqual(result['model_calls'],0)
                with patch.object(sys,'argv',argv[:-1]+['0'*64]):
                    with self.assertRaisesRegex(Refused,'hash_inventory_manifest_pin'):capture.hash_only_main()
            put(product_rows[0]['path'],b'changed fixture bytes')
            with self.assertRaises(Refused):capture.verify_inventory(tiny,types.SimpleNamespace(stop=hash_run/'STOP'),end=end)

    def test_phase_maxima_never_borrow_fixed_endpoints(self):
        clock,d=self.deadline();clock.now=270.
        row=d.enter('preflight',spec_sha256='x')
        self.assertEqual(row['deadline'],280.)
        clock.now=279.
        with self.assertRaises(Refused):d.enter('preflight',spec_sha256='x')
        clock.now=800.;row=d.enter('support',spec_sha256='x')
        self.assertEqual(row['deadline'],830.)
        clock.now=905.;row=d.enter('moss-compute',spec_sha256='x')
        self.assertEqual(row['deadline'],1205.)
        clock.now=1206.
        with self.assertRaises(Refused):d.check()
        row=d.enter('moss-cleanup',spec_sha256='x')
        self.assertEqual(row['deadline'],1266.)
        with self.assertRaises(Refused):d.enter('primary',spec_sha256='x')

    def test_first_claim_preserves_source_once_and_refuses_late(self):
        from server import observe_first_claim
        from common import WAV_SHA
        clock,d=self.deadline();d.enter('preflight',spec_sha256='x')
        with retained_root() as td:
            hooks=types.SimpleNamespace(capture_allowed=True,claimed=0,deadlines=d,spec={'_sha256':'x'},
                out=Path(td),journal=FakeJournal(),claim_source=None)
            value={'status':'queued','duration':FRAMES/16000,'timeline_start':0,'next_offset':0,
                'digest':WAV_SHA,'id':'chunk','session_id':'session'}
            observe_first_claim(hooks,value)
            self.assertEqual(hooks.claim_source,value);self.assertEqual(value['status'],'queued')
            self.assertEqual(d.primary,700.)
            with self.assertRaises(Refused):observe_first_claim(hooks,value)
            clock,late=self.deadline();clock.now=281.
            hooks.deadlines=late;hooks.claimed=0
            with self.assertRaises(Refused):observe_first_claim(hooks,value)

    def test_ordinary_idle_worker_start_is_one_without_provider_calls(self):
        start={'entered':1,'returned':1,'accepted':1,'rejected':0,'exceptions':0}
        value={'counts':{'jobs':{'worker_start':start,'analyzer':dict(start,entered=0,returned=0,accepted=0)},
                        'rpc':{'reset':dict(start,entered=0,returned=0,accepted=0)}},'launches':{'moss-direct':0}}
        no_new_calls(value)
        value['counts']['jobs']['worker_start']=dict(start,entered=0)
        with self.assertRaises(Refused):no_new_calls(value)
        value['counts']['jobs']['worker_start']=start;value['launches']['moss-direct']=1
        with self.assertRaises(Refused):no_new_calls(value)

    def test_direct_moss_argv_environment_stdin_unchanged_and_one_launch(self):
        import bootstrap
        clock,d=self.deadline();d.enter('support',spec_sha256='x')
        calls=[]
        with retained_root() as td:
            root=Path(td)
            spec={'_server_phase':'capture','_sha256':'x','python':sys.executable,
                'source_root':str(root/'source'),'data_dir':str(root/'data'),'output_dir':str(root)}
            argv=[sys.executable,'-I','-B','-S',str(root/'source/audio_backend/moss_handy_runner.py'),
                  str(root/'data/moss-handy-jobs/slot'),'a'*64]
            def spawn(command,**kwargs):
                calls.append((command,kwargs));return types.SimpleNamespace(args=command,pid=123)
            ledger=types.SimpleNamespace(originals=[],spawn=spawn,stop_original=lambda p:None)
            modules={'audio_backend':types.ModuleType('audio_backend'),
                'audio_backend.resources':types.ModuleType('audio_backend.resources'),
                'audio_backend.native_worker':types.ModuleType('audio_backend.native_worker')}
            modules['audio_backend'].resources=modules['audio_backend.resources']
            modules['audio_backend'].native_worker=modules['audio_backend.native_worker']
            env=dict(os.environ)
            kwargs={'cwd':root/'source','env':env,'stdin':subprocess.PIPE,
                    'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
            original=subprocess.Popen
            try:
                with patch.dict(sys.modules,modules):
                    counts=bootstrap.install_launch_hook(spec,d,FakeJournal(),ledger,'server')
                    subprocess.Popen(argv,**kwargs)
                    with self.assertRaises(Refused):subprocess.Popen(argv,**kwargs)
            finally:subprocess.Popen=original
            self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],argv)
            self.assertIs(calls[0][1]['env'],env);self.assertEqual(calls[0][1],kwargs)
            self.assertEqual(counts['moss-direct'],1)

    def test_direct_child_requires_product_cleanup_without_fake_bootstrap(self):
        import bootstrap
        good={'cleanup':{'cleanup_verified':True,'original_handle_waited_and_reaped':True,
                         'remaining_live_owned':[]}}
        self.assertIs(bootstrap.moss_cleanup_evidence(good),good['cleanup'])
        for field,value in (('cleanup_verified',False),('original_handle_waited_and_reaped',False),
                            ('remaining_live_owned',[{'pid':1}])):
            with self.assertRaises(Refused):bootstrap.moss_cleanup_evidence({'cleanup':dict(good['cleanup'],**{field:value})})
        with self.assertRaises(Refused):bootstrap.moss_cleanup_evidence(None)

    def test_idempotency_header_and_identical_replay_are_bounded(self):
        import capture
        now=time.monotonic()
        d=Deadlines({'GUARD_WORK_END':str(now+1440),'GUARD_ABSOLUTE_END':str(now+1500),
                     'GUARD_STOP_PATH':'/explicitly-absent-fixture-stop'})
        d.enter('support',spec_sha256='x')
        calls=[]
        class Timer:
            def __init__(self,*a,**k):pass
            def start(self):pass
            def cancel(self):pass
        class Response:
            length=16;status=200
            def read(self,n):
                value=getattr(self,'sent',False);self.sent=True
                return b'' if value else b'{"job_id":"one"}'
            def getheaders(self):return [('Cache-Control','private, no-store')]
        class Connection:
            sock=None
            def __init__(self,*a,**k):pass
            def request(self,*a,**k):calls.append((a,k))
            def getresponse(self):return Response()
            def close(self):pass
        client=capture.Client(1,'fixture-token',d,FakeJournal(),maximum=2)
        with patch.object(capture.http.client,'HTTPConnection',Connection),patch.object(capture.threading,'Timer',Timer):
            for _ in range(2):
                self.assertEqual(client.json('POST','/sessions/s/native-owned-supports/c/moss-jobs',
                    json_body={'fixture':True},idempotency_key='same-key',private=True)['job_id'],'one')
            with self.assertRaises(Refused):client.request('GET','/health')
        self.assertEqual(calls[0],calls[1]);self.assertEqual(calls[0][1]['headers']['Idempotency-Key'],'same-key')

    def test_private_observer_does_not_drain_expire_or_release(self):
        import threading
        from server import read_only_state
        event=threading.Event()
        def forbidden(*a,**k):raise AssertionError('private observer mutated product')
        dispatch=types.SimpleNamespace(released=False,
            token=types.SimpleNamespace(target=types.SimpleNamespace(binding='b'*64)))
        cache=types.SimpleNamespace(_lock=threading.Lock(),_lease_lock=threading.Lock(),epoch='e'*32,
            _entries={('s','c'):object()},_retained=64,_marker_bytes=0,_pending=None,_cleanup_pending=event,_blocked=event,
            _revision=1,_dispatch=dispatch,_dispatch_cleanup_pending=event,_lease_cleanup_pending=event,
            _lease_count=1,_lease_bytes=128,drain=forbidden,_expire=forbidden,revalidate_dispatch=forbidden)
        native={'construct_attempts':1,'session_attempts':1,'run_attempts':1}
        cleanup={'cleanup_verified':True,'original_handle_waited_and_reaped':True,'remaining_live_owned':[]}
        controller=types.SimpleNamespace(_lock=threading.RLock(),_job={'job_id':'internal',
            'state':'returned_diagnostic','native_receipt':native,'cleanup':cleanup},status=forbidden)
        record={'job_id':'public','support_id':'s'*64,'capture_epoch':'e'*32,'revision':1,
            'state':'returned_diagnostic','cleaned':True,'purge_pending':False,'invocation_possible':True,
            'cancelled':event,'controller':controller}
        permit=types.SimpleNamespace(_condition=threading.Condition(threading.RLock()),_state='IDLE',
            _optional=None,_primary={},_probe=False,_unavailable=None,_closed=False,_handles={1:object()},
            _take_sample=forbidden,release_optional=forbidden,status=forbidden)
        manager=types.SimpleNamespace(_lock=threading.RLock(),_live=record,_records=[record],
            jobs_epoch='j'*32,stopped=False,_closed=False,permit=permit,_expire=forbidden,_retire=forbidden)
        worker=types.SimpleNamespace(current_chunk=None,native_owned_support_cache=cache)
        app=types.SimpleNamespace(state=types.SimpleNamespace(worker=worker,moss_jobs=manager))
        spec={};before=dict(record);native_before=dict(native);cleanup_before=dict(cleanup)
        first=read_only_state(app,spec);second=read_only_state(app,spec)
        self.assertEqual(first,second);self.assertEqual(record,before)
        self.assertEqual(native,native_before);self.assertEqual(cleanup,cleanup_before)
        self.assertIs(cache._dispatch,dispatch);self.assertFalse(dispatch.released)
        self.assertEqual((cache._lease_count,cache._lease_bytes),(1,128));self.assertFalse(event.is_set())
        self.assertEqual(first['cache']['source_binding'],'b'*64)
        self.assertEqual(first['jobs']['accepted_jobs'],1);self.assertEqual(first['jobs']['job_id'],'public')
        self.assertEqual(first['moss_terminal']['job_id'],'internal')
        self.assertEqual(first['moss_terminal']['native_receipt'],native)
        self.assertEqual(first['permit']['resident_handles'],1);self.assertEqual(cache._revision,1)

    def test_response_and_dispatch_leases_must_be_zero(self):
        from capture import zero_leases
        value={'cache':{'lease_count':0,'lease_bytes':0,'response_cleanup_pending':False,
               'dispatch_present':False,'dispatch_cleanup_pending':False},'permit':None}
        zero_leases(value)
        for key in value['cache']:
            changed={'cache':dict(value['cache'],**{key:1}),'permit':None}
            with self.assertRaises(Refused):zero_leases(changed)

    def test_native_counts_are_reported_separately_from_launch(self):
        from capture import native_moss_receipt
        native=dict(construct_attempts=1,session_attempts=1,run_attempts=1,model_closed=True,
                    session_closed=True,close_errors=[],error_type=None,eos=None,generated_token_count=None)
        value={'native_receipt':native,'cleanup':{'cleanup_verified':True,
               'original_handle_waited_and_reaped':True,'remaining_live_owned':[]}}
        self.assertIs(native_moss_receipt(value),native)
        with self.assertRaises(Refused):native_moss_receipt(dict(value,native_receipt=None))
        with self.assertRaises(Refused):native_moss_receipt(dict(value,native_receipt=dict(native,run_attempts=2)))

    def test_failed_controller_still_attempts_full_posthash_after_cleanup(self):
        import bootstrap,capture
        order=[]
        now=time.monotonic()
        d=Deadlines({'GUARD_WORK_END':str(now+1440),'GUARD_ABSOLUTE_END':str(now+1500),
                     'GUARD_STOP_PATH':'/explicitly-absent-fixture-stop'})
        with retained_root() as td:
            spec={'output_dir':td,'_sha256':'x'}
            def cleanup(path):order.append('cleanup');return {'cleanup':'CONFIRMED','originals':[]}
            ledger=types.SimpleNamespace(originals=[],cleanup=cleanup)
            def post(*a,**k):order.append('posthash');return {'all_matched':True}
            with patch.object(bootstrap,'terminal_observation',return_value={'status':'UNOBSERVED'}),patch.object(capture,'verify_inventory',side_effect=post):
                bootstrap.finalize(spec,d,FakeJournal(),ledger,'controller',None,RuntimeError('explicit fixture failure'))
            self.assertEqual(order,['cleanup','posthash'])
            value=json.loads((Path(td)/'capture-result.json').read_text())
            self.assertEqual(value['status'],'INCOMPLETE');self.assertTrue(value['posthash']['all_matched'])

    def test_physical_db_wal_partial_and_directory_reservation(self):
        import launch
        success=retained_root()
        with success as td:
            saved=Path(td)/'success.txt';saved.write_bytes(b'retained-success')
        self.assertTrue(success.closed);self.assertEqual(saved.read_bytes(),b'retained-success')
        failed=retained_root()
        with self.assertRaisesRegex(RuntimeError,'explicit retained failure'):
            with failed as td:
                error_file=Path(td)/'error.txt';error_file.write_bytes(b'retained-error')
                raise RuntimeError('explicit retained failure')
        self.assertTrue(failed.closed);self.assertEqual(error_file.read_bytes(),b'retained-error')
        manual=retained_root();manual_file=manual.path/'manual.txt';manual_file.write_bytes(b'retained-cleanup')
        manual.cleanup();manual.cleanup()
        self.assertTrue(manual.closed);self.assertEqual(manual_file.read_bytes(),b'retained-cleanup')
        mib=2**20
        self.assertEqual(launch.budget_projection(16*mib,16*mib,16*mib,448*mib,16*mib),512*mib)
        for index in range(5):
            values=[16*mib,16*mib,16*mib,448*mib,16*mib];values[index]+=1
            with self.assertRaises(ValueError):launch.budget_projection(*values)
        # The reused qualified guard counts max(logical,allocated), including sparse DB/WAL and directories.
        tree=ast.parse(Path(launch.GUARD).read_text())
        charge=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='scratch_bytes')
        text=ast.unparse(charge)
        self.assertIn('st_blocks',text);self.assertIn('st_size',text);self.assertIn('max(',text)


if __name__=='__main__':
    # A reviewed exact list is mandatory. No discovery or production test imports.
    require_env=('GUARD_RUN_DIRECTORY','GUARD_STOP_PATH','GUARD_WORK_END','GUARD_ABSOLUTE_END')
    if not all(key in os.environ for key in require_env):raise SystemExit('Reviewed outer guard required')
    suite=unittest.TestSuite()
    classes=(AdmissionFixtures,RawAndControlFixtures,SnapshotFixtures,OwnershipFixtures,FakeNativeABI,FakeSDKFixtures,ApiSmokeFixtures)
    expected=json.loads((HERE.parent/'FIXTURE_IDS.json').read_text())['ids']
    observed=[]
    for cls in classes:
        for name in unittest.defaultTestLoader.getTestCaseNames(cls):
            observed.append(cls.__name__+'.'+name)
    if sorted(observed)!=expected or len(observed)!=41:raise SystemExit('Exact 41 fixture IDs required')
    for cls in classes:
        for name in unittest.defaultTestLoader.getTestCaseNames(cls):suite.addTest(cls(name))
    result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
    print(json.dumps({'label':'MODEL_FREE_EXPLICIT_FAKE_PRODUCERS','tests_run':result.testsRun,
                      'failures':len(result.failures),'errors':len(result.errors),
                      'skips':len(result.skipped),'actual_acoustic_calls':0}),flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
