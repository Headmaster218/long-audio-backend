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


def fixture_pins():
    """Only explicit synthetic metadata; source identity inherited, never audio-read."""
    import common
    result={role:{'frames':frames,'wav_sha256':str(i+1)*64,'pcm16_sha256':'b'*64,'float32_sha256':'c'*64}
            for i,(role,frames) in enumerate(common.ROLE_FRAMES.items())}
    result['source']={'frames':336000,'wav_sha256':common.SOURCE_WAV_SHA,
                      'pcm16_sha256':common.SOURCE_PCM_SHA,'float32_sha256':common.SOURCE_FLOAT_SHA}
    return result


def fixture_material():
    import common
    return {'schema':'__PRIVATE_CONTRACT_ID_018__','status':'MATERIALIZED_HASHED',
        'inherited_source':{'path':str(common.SOURCE_WAV),'bytes':common.SOURCE_WAV_BYTES,'sha256':common.SOURCE_WAV_SHA},
        'source_frames':[0,336000],'split_frames':[[0,168000],[168000,336000]],
        'input_pins':fixture_pins(),'source_descriptor_before':[1,2,672044,3,4],
        'source_descriptor_after':[1,2,672044,3,4],'output_readback':True,
        'concatenated_pcm_equal':True,'split_wav_digests_distinct':True,'fixture_metadata_only':True}


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
        for name,cap in [('event_compute',8),('embedding_compute',8),('sensevoice_decode',32)]:
            calls=[];gate=Admission({name:cap},lambda:None)
            for _ in range(cap):gate.invoke(name,lambda:calls.append('fake SDK'))
            with self.assertRaises(Refused):gate.invoke(name,lambda:calls.append('extra'))
            self.assertEqual(len(calls),cap);self.assertEqual(gate.counts[name]['rejected'],1)

    def test_native_ABI_sequence_and_extra_read_rejection(self):
        names=['create','open']+['push']*11+['finish','segments','segments']
        gate=Admission({'create':1,'open':1,'push':11,'finish':1,'segments':2},lambda:None,sequence=names)
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
        env={'GUARD_WORK_END':'1840','GUARD_ABSOLUTE_END':'1900','REAL_CAPTURE_POSTHASH_END':'1840',
             'GUARD_STOP_PATH':'/deliberately-absent'}
        deadline=Deadlines(env,clock=lambda:100.)
        self.assertEqual(deadline.call_end(120),220.)
        bad=dict(env,REAL_CAPTURE_POSTHASH_END='1839')
        with self.assertRaises(Refused):Deadlines(bad,clock=lambda:100.)
        with self.assertRaises(Refused):Deadlines(env,clock=lambda:1720.)

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
            for _ in range(4):
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
        self.assertEqual(sum(x[0]=='event_compute' for x in calls),4)
        self.assertEqual(sum(x[0]=='embedding_compute' for x in calls),8)
        self.assertEqual(sum(x[0]=='sensevoice_decode' for x in calls),32)
        self.assertIs(next(x for x in calls if x[0]=='accept')[3]['waveform'],audio)
        self.assertEqual(instrument.sdk.counts['sensevoice_decode']['rejected'],1)
        self.assertEqual(instrument.streams,{})



class ApiSmokeFixtures(unittest.TestCase):
    def deadline(self,clock=None):
        clock=clock or FakeClock()
        value=Deadlines({'GUARD_WORK_END':'1840','GUARD_ABSOLUTE_END':'1900',
            'REAL_CAPTURE_POSTHASH_END':'1840','GUARD_STOP_PATH':'/explicitly-absent-fixture-stop'},clock=clock)
        return clock,value

    def test_dev8_runtime_closed_pins_reject_old_source(self):
        import launch
        freeze={'product_source_files':{'a.py':'a'*64},'product_source_count':__PRIVATE_PRODUCT_SOURCE_COUNT__,'product_source_bytes':__PRIVATE_PRODUCT_SOURCE_BYTES__}
        spec={'source_root':str(launch.SOURCE),'source_files':freeze['product_source_files']}
        launch.validate_source_binding(spec,freeze)
        for changed in (dict(spec,source_root=str(launch.WORKSPACE/'__PRIVATE_DIRECTORY_002__')),
                        dict(spec,source_files={})): 
            with self.assertRaisesRegex(ValueError,'exact_product_source_closure'):
                launch.validate_source_binding(changed,freeze)
        self.assertEqual(launch.PYTHON,launch.RUNTIME/'bin/python3.12')
        self.assertEqual(len(launch.source_names()),8)
        self.assertEqual(launch.WORKSPACE,launch.ROOT.parent)
        self.assertEqual(launch.COMPONENT,launch.ROOT)
        self.assertEqual(launch.RUN,launch.ROOT/'runs/__PRIVATE_RUN_ID_001__')
        self.source_symbol_origins()
        self.admission_integration()

    def source_symbol_origins(self):
        """Real-shaped list membership and agreeing source/installed hash rows; fake symbols only."""
        with retained_root() as td:
            base=Path(td);source=base/'source';site=base/'site';site.mkdir()
            modules={'audio_backend.inference.native_diarization':'audio_backend/inference/native_diarization.py',
                'audio_backend.inference.analyzer':'audio_backend/inference/analyzer.py',
                'audio_backend.speaker_evidence':'audio_backend/speaker_evidence.py'}
            paths={};pins={}
            for module,relative in modules.items():
                path=source/relative;path.parent.mkdir(parents=True,exist_ok=True)
                raw=('explicit fake symbol origin '+module+'\n').encode();path.write_bytes(raw)
                paths[module]=path;pins[str(path)]={'bytes':len(raw),'sha256':sha(raw)}
            sdk_path=site/'explicit_fake_sdk.py';sdk_path.write_bytes(b'explicit fake SDK origin\n')
            pins[str(sdk_path)]={'bytes':sdk_path.stat().st_size,'sha256':sha(sdk_path.read_bytes())}
            spec={'output_dir':str(base),'source_root':str(source),'site_packages':str(site),
                'source_files':[str(p) for p in paths.values()],
                'installed_files':pins,'hash_inventory':[dict(path=k,**v) for k,v in pins.items()]}
            instrument=Instrumentation(spec,FakeDeadline(),FakeJournal(),'native');calls=[]
            def attempt(module,path,accepted):
                original=lambda *a,**k:calls.append('unexpected provider entry')
                original.__module__=module
                owner=types.SimpleNamespace(__name__='ExplicitFakeOwner',method=original)
                replacement=lambda *a,**k:None
                fake=types.ModuleType(module);fake.__file__=str(path)
                before=len(instrument.symbols)
                with patch.dict(sys.modules,{module:fake}):
                    if accepted:
                        self.assertIs(instrument.bind(owner,'method',replacement),original)
                        self.assertIs(owner.method,replacement)
                        self.assertEqual(len(instrument.symbols),before+1)
                    else:
                        with self.assertRaises(Refused):instrument.bind(owner,'method',replacement)
                        self.assertIs(owner.method,original);self.assertEqual(len(instrument.symbols),before)
                self.assertEqual(calls,[])
            for module,path in paths.items():attempt(module,path,True)
            attempt('explicit_fake_sdk',sdk_path,True)
            native='audio_backend.inference.native_diarization';path=paths[native]
            attempt('audio_backend.inference.unlisted',path,False)
            attempt(native,paths['audio_backend.inference.analyzer'],False)
            attempt(native,base/'outside-source.py',False)
            attempt(native,path.parent/'../inference'/path.name,False)
            attempt('explicit_fake_sdk',path,False)
            with patch.dict(spec,source_root=str(base/'wrong-source')):attempt(native,path,False)
            with patch.dict(spec,source_files=[]):attempt(native,path,False)
            with patch.dict(spec,source_files={str(path):pins[str(path)]['sha256']}):attempt(native,path,False)
            missing=dict(pins);missing.pop(str(path))
            with patch.dict(spec,installed_files=missing):attempt(native,path,False)
            changed=dict(pins);changed[str(path)]=dict(pins[str(path)],sha256='0'*64)
            with patch.dict(spec,installed_files=changed):attempt(native,path,False)
            rows=[dict(row,sha256='0'*64) if row['path']==str(path) else row for row in spec['hash_inventory']]
            with patch.dict(spec,installed_files=changed,hash_inventory=rows):attempt(native,path,False)
            path.write_bytes(b'changed ordinary fixture source bytes')
            attempt(native,path,False)

    def admission_integration(self):
        """Original strict readers and exact prerequisite shapes; all local data explicitly fake."""
        import launch,capture,common
        original_profile=launch.profile_data()
        imported_raw=(HERE.parent/'readiness/COMBINED_IMPORT_TERMINAL.json').read_bytes()
        runtime_raw=Path(original_profile['runtime_inventory']['path']).read_bytes()
        tree=ast.parse(Path(launch.GUARD).read_text())
        readers=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('read_regular','read_pin_json')]
        self.assertEqual(len(readers),2)
        with retained_root() as td:
            base=Path(td);root=base/'component';root.mkdir();(root/'controller').mkdir()
            source=base/'__PRIVATE_DIRECTORY_006__';source.mkdir()
            run=root/'runs/__PRIVATE_RUN_ID_001__';out=run/'tmp/smoke';out.mkdir(parents=True)
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
            scanned=[];charges={}
            def scratch(end,roots=None):
                key=None if roots is None else tuple(roots);scanned.append(key);return charges.get(key,0)
            g=types.SimpleNamespace(WORKSPACE=launch.WORKSPACE,MAX_DISK=12*2**30,ACTUAL_RSS=4*2**30,
                ACTUAL_VISIBLE_RSS=6*2**30,ACTUAL_AS=8*2**30,CLEANUP_RESERVE=60,OLD_UNKNOWN_DISK=320*2**20,
                ORDINARY_FREE_FLOOR=11*2**29,POLL=.05,RECEIPT_RESERVE=16384,Guard=Guard,
                scratch_bytes=scratch,clock_check=check,read_pin_json=ns['read_pin_json'],
                read_regular=ns['read_regular'])
            product_rows=[]
            for i in range(__PRIVATE_PRODUCT_SOURCE_COUNT__):
                size=__PRIVATE_PRODUCT_SOURCE_BYTES__//__PRIVATE_PRODUCT_SOURCE_COUNT__+int(i<__PRIVATE_PRODUCT_SOURCE_BYTES__%__PRIVATE_PRODUCT_SOURCE_COUNT__)
                payload=(b'explicit non-audio source fixture\n'*((size//34)+2))[:size]
                path=source/f'explicit-fixture-{i:03}.txt';pin=put(path,payload)
                product_rows.append(dict(pin,bytes=path.stat().st_size))
            source_map={Path(row['path']).name:row['sha256'] for row in product_rows}
            freeze={'product_source_files':source_map,'harness_files':[],'metadata_files':[]}
            frozen=put(root/'SOURCE_FREEZE.json',freeze)['sha256']
            imported=put(root/'readiness/COMBINED_IMPORT_TERMINAL.json',imported_raw)
            runtime_pin=put(root/'runtime-metadata.json',runtime_raw)
            partitions=original_profile['component_partitions']
            storage_value={'schema':'__PRIVATE_CONTRACT_ID_014__','status':'PROSPECTIVE_CAPACITY_RESERVED',
                'fresh_guard_readmission_required':True,'does_not_authorize_actual_execution':True,
                'does_not_qualify_physical_store_behavior':True,'global_cap_bytes':12*2**30,
                'ordinary_free_floor_bytes':11*2**29,'old_unknown_disk_bytes':320*2**20,
                'old_unknown_rss_bytes':512*2**20,'component_path':str(root),'component_cap_bytes':512*2**20,
                'partitions':partitions,'global_prospective_bytes':2**30,'observed_free_bytes':30*2**30,
                'required_free_bytes':11*2**29+320*2**20+11*2**30,'fixture_metadata_only':True}
            storage=put(root/'readiness/RESOURCE_ADMISSION.json',storage_value)
            pins=fixture_pins();material_value=fixture_material()
            material=put(root/'input/MATERIALIZATION.json',material_value)
            chosen=dict(original_profile,immutable_assets=[product_rows[0]],immutable_host_files=[],evidence_pins=[],
                model_settings={},runtime_import_local=imported,runtime_inventory=runtime_pin)
            runtime=json.loads(runtime_raw)
            rows=product_rows+[dict(path=str(launch.RUNTIME/row['destination']),bytes=row['bytes'],sha256=row['sha256'])
                               for row in runtime['files']]
            # Intentionally opaque fake input metadata. No audio is created or decoded.
            rows += [dict(path=str(common.SOURCE_WAV),bytes=672044,sha256=pins['source']['wav_sha256']),
                     dict(material,bytes=Path(material['path']).stat().st_size)]
            rows += [dict(path=str(root/('input/upload-'+key+'-10p5s.wav')),bytes=336044,
                          sha256=pins['upload_'+key]['wav_sha256']) for key in ('a','b')]
            inv={'schema':'__PRIVATE_CONTRACT_ID_008__','preparation_status':'FINALIZED_INPUT_BOUND','source_freeze_sha256':frozen,
                'hash_inventory':rows,'inventory_sha256':sha(launch.canonical(sorted(rows,key=lambda row:row['path'])))}
            invpin=put(root/'HASH_INVENTORY.json',inv)
            fixtures={'schema':'__PRIVATE_CONTRACT_ID_006__','status':'PASS','cleanup':'CONFIRMED',
                'tests_run':chosen['fixture_count'],'fixture_ids_sha256':chosen['fixture_ids_sha256'],
                'failures':0,'errors':0,'actual_acoustic_calls':0,'original_tool_wait_reaped':True,
                'source_freeze_sha256':frozen,'fixture_metadata_only':True}
            fixturepin=put(root/'readiness/FIXTURES_TERMINAL.json',fixtures)
            hashed={'schema':'__PRIVATE_CONTRACT_ID_007__','status':'PASS_HASH_ONLY','cleanup':'CONFIRMED',
                'all_matched':True,'original_tool_wait_reaped':True,'source_freeze_sha256':frozen,
                'inventory_manifest_sha256':invpin['sha256'],'inventory_sha256':inv['inventory_sha256'],
                'files':len(rows),'bytes':sum(row['bytes'] for row in rows),'elapsed_seconds':.01,
                'product_imports':0,'audio_decodes':0,'services':0,'model_calls':0,'fixture_metadata_only':True,
                'origin':'SEPARATE_AUTHORIZED_HASH_GATE','origin_receipt':{'path':str(root/'runs/__PRIVATE_RUN_ID_002__/tmp/hash-only/hash-only-result.json'),'sha256':'e'*64}}
            hashed['origin_receipt']=put(root/'runs/__PRIVATE_RUN_ID_002__/tmp/hash-only/hash-only-result.json',
                {key:value for key,value in hashed.items() if key not in ('origin','origin_receipt')})
            hashpin=put(root/'readiness/HASH_TERMINAL.json',hashed)
            entry_value={'schema':'__PRIVATE_CONTRACT_ID_010__','status':'PASS_ISOLATED_ENTRY_REGRESSION',
                'cases':12,'case_ids':chosen['entry_case_ids'],'original_handles_waited':12,'cleanup':'CONFIRMED',
                'source_freeze_sha256':frozen,'entry_sha256':launch.sha(HERE.parent/'entry/isolated_entry.py'),
                'regression_sha256':launch.sha(HERE.parent/'entry/test_isolated_entry.py'),
                'actual_execution_authorized':False,'service_model_provider_calls':0,'sampled_tree_RSS_claimed':False}
            for entry_name in ('isolated_entry.py','test_isolated_entry.py'):
                put(root/'entry'/entry_name,(HERE.parent/'entry'/entry_name).read_bytes())
            entrypin=put(root/'readiness/ISOLATED_ENTRY_TERMINAL.json',entry_value)
            material_terminal={'schema':'__PRIVATE_CONTRACT_ID_011__','status':'PASS_MATERIALIZATION_ONLY',
                'cleanup':'CONFIRMED','original_tool_wait_reaped':True,'source_freeze_sha256':frozen,
                'materialization_receipt':material,'output_wav_count':2,'output_wav_bytes':672088,
                'output_readback':True,'concatenated_pcm_equal':True,'split_wav_digests_distinct':True,
                'actual_execution_authorized':False,'product_imports':0,'original_corpus_decodes':0,'services':0,'model_calls':0}
            materialpin=put(root/'readiness/MATERIALIZATION_TERMINAL.json',material_terminal)
            review=put(root/'readiness/INDEPENDENT_PREPARATION_REVIEW.json',{
                'schema':'__PRIVATE_CONTRACT_ID_009__','status':'ACCEPTED_SOURCE_AND_MODEL_FREE',
                'source_freeze_sha256':frozen,'fixtures_terminal_sha256':fixturepin['sha256'],
                'hash_terminal_sha256':hashpin['sha256'],'actual_execution_authorized':False,'fixture_metadata_only':True})
            authority={'schema':'__PRIVATE_CONTRACT_ID_004__','actual_capture_approved':True,
                'scope':'one-genuine-two-upload-seam-and-one-composite-moss-job','source_head':common.SOURCE_HEAD,
                'source_freeze_sha256':frozen,'authorization_reference':'Explicit fake fixture; no executable authority'}
            authoritypin=put(root/'ACTUAL_AUTHORITY.json',authority)
            controller={'schema':'__PRIVATE_CONTRACT_ID_005__','actual_run_authorized':True,
                'source_root':str(source),'source_file_count':__PRIVATE_PRODUCT_SOURCE_COUNT__,'source_head':authority['source_head'],
                'source_tree':common.SOURCE_TREE,'source_index_sha256':common.SOURCE_INDEX_SHA,
                'source_freeze_sha256':frozen,'approval_receipt':authoritypin,'output_dir':str(out),'data_dir':str(out/'data'),
                'python':str(launch.PYTHON),'outer_seconds':1800,'primary_seconds':900,'posthash_seconds':120,
                'cleanup_seconds':60,'identical_key_replay':True,'input_wav':str(common.SOURCE_WAV),
                'input_uploads':{key:str(root/('input/upload-'+key+'-10p5s.wav')) for key in ('a','b')},
                'input_pins':pins,'materialization_receipt':material,'settings':{},'hash_inventory':rows,
                'source_files':[row['path'] for row in product_rows],'inventory_manifest':invpin,
                'storage_admission':{'receipt':storage},'installed_runtime':{'receipt':imported},
                'posthash_probe':{'receipt':hashpin},'model_free_tests':{'receipt':fixturepin},
                'positive_owned_probe':{'receipt':fixturepin},'preparation_review':review,
                'entry_regression':{'receipt':entrypin},'materialization_probe':{'receipt':materialpin}}
            spec={'stage':'api_smoke_actual','argv':[str(launch.PYTHON),'-I','-B','-S',str(root/'controller/bootstrap.py'),
                '--mode','controller','--spec',str(root/'controller.actual.json')]}
            def bind():
                pin=put(root/'controller.actual.json',controller)
                spec.update(controller_spec=pin,final_manifest=pin)
            with patch.multiple(launch,ROOT=root,COMPONENT=root,HERE=root/'controller',SOURCE=source,RUN=run),\
                    patch.object(launch,'profile_data',return_value=chosen),\
                    patch.object(common,'__file__',str(root/'controller/common.py')):
                launch.configure(g);self.assertIs(Guard.ownership_unchanged,original_method)
                spec['external_files']=dict.fromkeys(launch.common_names()|{str(root/'controller.actual.json'),
                    str(root/'ACTUAL_AUTHORITY.json'),invpin['path'],fixturepin['path'],hashpin['path'],
                    storage['path'],imported['path'],material['path'],review['path'],entrypin['path'],materialpin['path']},'fixture')
                bind();accepted=g.actual_admission(spec,end)
                self.assertEqual(accepted[1],controller);self.assertEqual(accepted[3],run)
                self.assertEqual(accepted[2],[root])
                for exact in (root,root/'runs',run):self.assertIn((exact,),scanned)
                mib=2**20
                charges.update({(root,):20*mib,(root/'runs',):5*mib,(run,):2*mib})
                self.assertEqual(g.scratch_bytes(end,[root]),20*mib)
                charges[(root,)]=26*mib
                with self.assertRaisesRegex(ValueError,'smoke_component_reservation'):g.scratch_bytes(end,[root])
                charges.update({(root,):30*mib,(root/'runs',):20*mib,(run,):2*mib})
                with self.assertRaisesRegex(ValueError,'smoke_component_reservation'):g.scratch_bytes(end,[root])
                charges.update({(root,):450*mib,(root/'runs',):449*mib,(run,):449*mib})
                with self.assertRaisesRegex(ValueError,'smoke_component_reservation'):g.scratch_bytes(end,[root])
                charges.clear()
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
                bad=put(storage['path'],dict(storage_value,status='PASS'))
                controller['storage_admission']['receipt']=bad;bind()
                with self.assertRaisesRegex(ValueError,'prospective_storage_schema'):g.actual_admission(spec,end)
                controller['storage_admission']['receipt']=put(storage['path'],storage_value);bind()
                post={'all_matched':True,'source_files':__PRIVATE_PRODUCT_SOURCE_COUNT__,'files':len(rows),
                      'bytes':sum(row['bytes'] for row in rows),'elapsed_seconds':.01}
                value={'status':'COMPLETE','posthash':post,'actual_native_selected':True,'one_moss_job':True,
                    'genuine_durable_cell_gate':True,'committed_core_frames':[[0,168000],[168000,336000]],
                    'selected_cell_frames':[8000,328000],'retained_restart_verified':True,
                    'real_commit_returns':2,'same_response_overlap':True,'current_authorities':2,
                    'session_revocation':True,'actual_independent_per_upload_revocation':False,
                    'original_completed_job_postdelete_raw_result_actual':False}
                put(out/'posthash.json',post);put(out/'capture-result.json',value)
                owner=Guard();owner.controller=controller;owner.receipt={}
                owner.verify_capture_posthash(end);self.assertTrue(owner.receipt['controller_full_posthash_verified'])
                for field,changed in [('source_files',331),('files',len(rows)-1)]:
                    put(out/'posthash.json',dict(post,**{field:changed}))
                    with self.assertRaisesRegex(ValueError,'mandatory_complete_full_posthash'):owner.verify_capture_posthash(end)
                put(out/'posthash.json',post)
                put(out/'capture-result.json',dict(value,retained_restart_verified=False))
                with self.assertRaisesRegex(ValueError,'mandatory_complete_full_posthash'):owner.verify_capture_posthash(end)
                owner.controller=dict(controller,output_dir=str(root/'missing-output'))
                with self.assertRaises(FileNotFoundError):owner.verify_capture_posthash(end)
            # The real complete source/pair positive is the separately admitted validation gate.
            # This negative uses the exact shared inventory predicate before any audio path.
            tiny=dict(inv,hash_inventory=product_rows,source_root=str(source),source_files=controller['source_files'],
                installed_files={'/explicit-absent-execution-file':{'bytes':0,'sha256':'0'*64}},
                runtime_files={},harness_files={},input_wav=product_rows[0]['path'])
            with self.assertRaisesRegex(Refused,'full_hash_missing_execution_files'):
                capture.verify_inventory(tiny,types.SimpleNamespace(stop=root/'STOP'),end=end)
            put(product_rows[0]['path'],b'changed fixture bytes')
            with self.assertRaisesRegex(Refused,'full_hash_file_shape'):
                capture.verify_inventory(dict(tiny,installed_files={}),types.SimpleNamespace(stop=root/'STOP'),end=end)

    def test_phase_maxima_never_borrow_fixed_endpoints(self):
        import common
        clock,d=self.deadline();clock.now=270.
        self.assertEqual(d.enter('preflight',spec_sha256='x')['deadline'],280.)
        clock.now=279.
        with self.assertRaises(Refused):d.enter('preflight',spec_sha256='x')
        for name,(maximum,endpoint) in list(common.PHASES.items())[2:-1]:
            clock.now=d.origin+endpoint-5
            row=d.enter(name,spec_sha256='x');self.assertEqual(row['deadline'],d.origin+endpoint)
            clock.now=row['deadline']
            with self.assertRaises(Refused):d.check()
        with self.assertRaises(Refused):d.enter('primary',spec_sha256='x')

    def test_three_claims_preserve_two_sources_and_refuse_fourth(self):
        from server import observe_first_claim
        clock,d=self.deadline();d.enter('preflight',spec_sha256='x');pins=fixture_pins()
        with retained_root() as td:
            hooks=types.SimpleNamespace(capture_allowed=True,claimed=0,deadlines=d,
                spec={'_sha256':'x','input_pins':pins},out=Path(td),journal=FakeJournal(),claim_sources=[])
            a={'status':'queued','duration':10.5,'timeline_start':64.,'next_offset':0,
                'digest':pins['upload_a']['wav_sha256'],'id':'a','session_id':'s'}
            b=dict(a,id='b',timeline_start=74.5,digest=pins['upload_b']['wav_sha256'])
            for value in (a,a,b):observe_first_claim(hooks,value)
            self.assertEqual(hooks.claim_sources,[a,a,b]);self.assertEqual(hooks.claimed,3)
            self.assertEqual(d.primary,1000.);self.assertEqual(a['status'],'queued')
            with self.assertRaises(Refused):observe_first_claim(hooks,b)
            clock,late=self.deadline();clock.now=281.;hooks.deadlines=late;hooks.claimed=0;hooks.claim_sources=[]
            with self.assertRaises(Refused):observe_first_claim(hooks,a)

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
        d=Deadlines({'GUARD_WORK_END':str(now+1740),'GUARD_ABSOLUTE_END':str(now+1800),
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
        manager=types.SimpleNamespace(_lock=threading.RLock(),_live=record,_records=[record],_keys={'key':record},
            jobs_epoch='j'*32,stopped=False,_closed=False,permit=permit,_expire=forbidden,_retire=forbidden)
        worker=types.SimpleNamespace(current_chunk=None,native_owned_support_cache=cache,native_owned_cells=None)
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
               'dispatch_present':False,'dispatch_cleanup_pending':False},'permit':None,
               'cells':{'pins':0,'handoffs_active_or_pending':0,'cleanup_pending':False}}
        zero_leases(value)
        for key in value['cache']:
            changed=dict(value,cache=dict(value['cache'],**{key:1}))
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
        d=Deadlines({'GUARD_WORK_END':str(now+1740),'GUARD_ABSOLUTE_END':str(now+1800),
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


class DurableCellFixtures(unittest.TestCase):
    def test_exact_source_split_role_pins_refuse_unknown_or_extra(self):
        import common,copy
        pins=fixture_pins();self.assertEqual(common.validate_input_pins(pins),pins)
        for changed in (None,dict(pins,extra={}),{key:value for key,value in pins.items() if key!='upload_b'}):
            with self.assertRaises(Refused):common.validate_input_pins(changed)
        for role in common.ROLE_FRAMES:
            for key,bad in [('frames',True),('frames',1),('wav_sha256',None),('pcm16_sha256','g'*64)]:
                changed=copy.deepcopy(pins);changed[role][key]=bad
                with self.assertRaises(Refused):common.validate_input_pins(changed)
        equal=copy.deepcopy(pins);equal['upload_b']['wav_sha256']=equal['upload_a']['wav_sha256']
        with self.assertRaisesRegex(Refused,'split_wav_digests_must_differ'):common.validate_input_pins(equal)
        self.assertEqual(common.ROLE_FRAMES,{'source':336000,'upload_a':168000,'upload_b':168000,
            'analyzer_a':200000,'analyzer_b':200000,'composite':320000})
        self.assertIsNone(common.PCM_SHA);self.assertIsNone(common.FLOAT_SHA);self.assertIsNone(common.WAV_SHA)

    def test_materialization_receipt_missing_origin_interval_status_refused(self):
        import common
        with retained_root() as td:
            root=Path(td);(root/'controller').mkdir();(root/'input').mkdir();path=root/'input/MATERIALIZATION.json'
            value=fixture_material()
            with patch.object(common,'__file__',str(root/'controller/common.py')):
                with self.assertRaises(FileNotFoundError):common.input_pins()
                path.write_bytes(common.canonical(value));self.assertEqual(common.input_pins(),value['input_pins'])
                for key,bad in [('inherited_source',{}),('source_frames',[0,335999]),
                    ('split_frames',[[0,168001],[168001,336000]]),('status','PLANNED'),('output_readback',False),
                    ('concatenated_pcm_equal',False),('split_wav_digests_distinct',False),('source_descriptor_after',[1])]:
                    path.write_bytes(common.canonical(dict(value,**{key:bad})))
                    with self.assertRaisesRegex(Refused,'input_materialization_binding'):common.input_pins()

    def test_finite_primary_caps_and_phase_endpoints_are_exact(self):
        import common
        spec={'output_dir':'/explicit-unused-fixture'}
        instrument=Instrumentation(spec,FakeDeadline(),FakeJournal(),'native')
        self.assertEqual(instrument.abi.caps,{'native_model_create':1,'native_stream_open':1,
            'native_push':12,'native_finish':1,'native_segments':4})
        self.assertEqual(instrument.abi.sequence,tuple(['native_model_create','native_stream_open']+
            ['native_push']*12+['native_finish']+['native_segments']*4))
        self.assertEqual((instrument.sdk.caps['event_compute'],instrument.sdk.caps['embedding_compute'],
                          instrument.sdk.caps['sensevoice_decode']),(4,8,32))
        self.assertEqual([v[1] for v in common.PHASES.values()],[180.,1080.,1140.,1440.,1500.,1620.,1740.,1800.])

    def test_guard_stage_profile_rejects_clock_cpu_rss_and_actual_leak(self):
        import launch
        def spec(actual=False):
            return {'stage':'api_smoke_actual' if actual else 'api_smoke_fixtures',
                'absolute_seconds':1800 if actual else 300,'work_seconds':1740 if actual else 240,
                'address_space_cap_bytes':8*2**30 if actual else 2**30,
                'rss_cap_bytes':4*2**30 if actual else 256*2**20,'cpus':[0,1] if actual else [3],
                'scratch_cap_bytes':12*2**30}
        self.assertEqual(launch.stage_profile(spec()),(False,{3},2**30,256*2**20))
        self.assertEqual(launch.stage_profile(spec(True)),(True,{0,1},8*2**30,4*2**30))
        for key,bad in [('stage','unknown'),('absolute_seconds',301),('work_seconds',241),
                        ('address_space_cap_bytes',2**31),('rss_cap_bytes',512*2**20),
                        ('cpus',[0,1]),('scratch_cap_bytes',13*2**30)]:
            with self.assertRaises(ValueError):launch.stage_profile(dict(spec(),**{key:bad}))
        for key in ('controller_spec','final_manifest'):
            with self.assertRaisesRegex(ValueError,'fixtures_cannot_authorize_actual'):
                launch.stage_profile(dict(spec(),**{key:{'path':'fake','sha256':'a'*64}}))

    def test_original_context_formula_requires855_and_rejects21sec_whole(self):
        import launch
        path=launch.SOURCE/'audio_backend/moss_handy_runner.py'
        frozen=json.loads((HERE.parent/'SOURCE_FREEZE.json').read_text())
        self.assertEqual(sha(path.read_bytes()),frozen['product_source_files']['audio_backend/moss_handy_runner.py'])
        node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='context_frames')
        ns={};exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
        self.assertEqual(ns['context_frames'](320000),855)
        for frames in (336000,0,True,320001):
            with self.assertRaises(ValueError):ns['context_frames'](frames)

    def test_original_ownership_bytes_and_guard_methods_remain_unchanged(self):
        import launch
        old=launch.WORKSPACE/'__PRIVATE_DIRECTORY_004__/v2/controller/ownership.py'
        self.assertEqual((HERE/'ownership.py').read_bytes(),old.read_bytes())
        self.assertEqual(sha((HERE/'ownership.py').read_bytes()),
                         '__PRIVATE_INTEGRITY_PIN_012__')
        self.assertEqual(launch.sha(launch.GUARD),launch.GUARD_SHA)
        actual=launch.ownership_ast()
        tree=ast.parse(launch.read(launch.GUARD))
        owner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Guard')
        expected={n.name:sha(ast.dump(n,include_attributes=False).encode()) for n in owner.body
                  if isinstance(n,ast.FunctionDef) and n.name!='verify_capture_posthash'}
        self.assertEqual(actual,expected)
        self.assertNotIn('Guard.verify_pin',launch.OVERRIDES)

    def test_cell_pagination_bounds_epoch_sequence_and_count(self):
        from capture import cell_page
        def client(pages):
            iterator=iter(pages)
            return types.SimpleNamespace(json=lambda *a,**k:next(iterator))
        a={'sequence':1,'cell_id':'a'};b={'sequence':2,'cell_id':'b'}
        first={'jobs_epoch':'e','cells':[a],'has_more':True,'next_cursor':1}
        last={'jobs_epoch':'e','cells':[b],'has_more':False,'next_cursor':2}
        self.assertEqual(cell_page(client([first,last]),'/cells')['cells'],[a,b])
        for pages in ([dict(first,has_more=False,next_cursor=0)],
                      [first,dict(last,jobs_epoch='other')],
                      [first,dict(last,cells=[a],next_cursor=1)],
                      [dict(first,cells=[a,b,{'sequence':3},{'sequence':4}],next_cursor=4)],
                      [dict(first,cells=[],next_cursor=0)]):
            with self.assertRaises(Refused):cell_page(client(pages),'/cells')

    def test_delayed_publication_observes_original_completion_without_retry(self):
        from capture import await_cell_publication
        import copy
        pub={'kind':'Operation','operation_kind':'publish','done':False,'drained':False,'slot_released':False,'result':None}
        records=[{'prepared':True,'commit_returned_true':True,'committed_called':True,
                  'core_frames':core,'publication':copy.deepcopy(pub),'seam_reason':'observed'}
                 for core in ([0,168000],[168000,336000])]
        one={'cell_witness':{'errors':[],'records':records},'cells':{'handoffs_active_or_pending':1}}
        two=copy.deepcopy(one)
        for row in two['cell_witness']['records']:row['publication'].update(done=True,result={'status':'durable'})
        three=copy.deepcopy(two)
        for row in three['cell_witness']['records']:row['publication'].update(drained=True,slot_released=True)
        three['cells']['handoffs_active_or_pending']=0
        commands=[];sleeps=[];states=iter([one,two,three])
        def command(action):commands.append(action);return next(states)
        d=types.SimpleNamespace(check=lambda:None,phase_end=time.monotonic()+30)
        self.assertIs(await_cell_publication(types.SimpleNamespace(command=command),d,sleep=sleeps.append),three)
        self.assertEqual(commands,['state']*3);self.assertEqual(len(sleeps),2);self.assertFalse(pub['done'])
        for field,value in [('prepared',False),('commit_returned_true',False),('core_frames',[0,320000])]:
            bad=copy.deepcopy(three);bad['cell_witness']['records'][1][field]=value
            with self.assertRaises(Refused):await_cell_publication(types.SimpleNamespace(command=lambda action:bad),d,sleep=lambda x:None)
        bad=copy.deepcopy(three);bad['cell_witness']['records'][1]['publication']['result']['status']='failed'
        with self.assertRaises(Refused):await_cell_publication(types.SimpleNamespace(command=lambda action:bad),d,sleep=lambda x:None)
        with self.assertRaisesRegex(Refused,'bounded_publication_observation_exhausted'):
            await_cell_publication(types.SimpleNamespace(command=lambda action:one),d,sleep=lambda x:None,maximum=2)

    def test_passive_failed_pin_observation_preserves_original_permit_release(self):
        import server,threading,functools
        from capture import cell_cleanup
        hooks=server.ServerHooks.__new__(server.ServerHooks)
        hooks._observation_lock=threading.RLock();hooks.cell_witness={'errors':[]};hooks.journal=FakeJournal()
        hooks._copied_lease=types.SimpleNamespace(released=True)
        hooks._pin_receipt_done=types.SimpleNamespace(wait=lambda timeout:False)
        hooks.cell_pin_cleanup={'registry_removal_acknowledged':0,'exact_registry_absent':False,'permit_release_checked':0}
        token={'origin':'durable_cell','lease':hooks._copied_lease,'process_cleanup_verified':True}
        calls=[];sentinel=object()
        def original(obj,value,*,cleaned):calls.append((obj,value,cleaned));return sentinel
        tree=ast.parse(Path(server.__file__).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ServerHooks')
        install=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='install')
        wrapper=next(n for n in install.body if isinstance(n,ast.FunctionDef) and n.name=='release_optional')
        ns={'self':hooks,'functools':functools,'original_permit_release':original}
        exec(compile(ast.Module(body=[wrapper],type_ignores=[]),str(server.__file__),'exec'),ns)
        owner=object();self.assertIs(ns['release_optional'](owner,token,cleaned=True),sentinel)
        self.assertEqual(calls,[(owner,token,True)]);self.assertEqual(len(hooks.cell_witness['errors']),1)
        with self.assertRaisesRegex(Refused,'cell_observer_error'):
            cell_cleanup({'cell_witness':hooks.cell_witness,'cell_pin_cleanup':{}},'cell','proof')
        error=RuntimeError('original cleanup exception')
        def failed(*a,**k):raise error
        ns['original_permit_release']=failed
        with self.assertRaises(RuntimeError) as got:ns['release_optional'](owner,token,cleaned=True)
        self.assertIs(got.exception,error)

    def test_exact_copied_pin_acknowledgement_and_cell_cleanup_required(self):
        import server,threading
        from capture import cell_cleanup
        hooks=server.ServerHooks.__new__(server.ServerHooks);hooks.cell_pin_cleanup={}
        hooks._pin_receipt_done=threading.Event();hooks.journal=FakeJournal()
        pin=types.SimpleNamespace(release_done=threading.Event(),_released=True,_pcm=b'',cell_id='cell')
        pin.release_done.set();owner=types.SimpleNamespace(_lock=threading.Lock(),_pins={})
        hooks.released_cell(owner,pin,True,True)
        self.assertTrue(hooks._pin_receipt_done.is_set())
        self.assertEqual(hooks.cell_pin_cleanup['registry_removal_acknowledged'],1)
        for registered,result,pcm in [(False,True,b''),(True,True,b'not-dropped')]:
            pin._pcm=pcm
            with self.assertRaises(Refused):hooks.released_cell(owner,pin,registered,result)
        pin._pcm=b'';owner._pins[id(pin)]=pin
        with self.assertRaises(Refused):hooks.released_cell(owner,pin,True,True)
        receipt=dict(acquire_entered=2,rejected_acquisitions=1,rejected_pins_acquired=1,rejected_pins_released=1,
            accepted_entered=1,acquired=1,accepted=1,registry_removal_acknowledged=1,release_confirmed=1,
            release_requested=1,permit_release_checked=1,cell_id='cell',proof_sha256='proof',
            exact_registry_absent=True,original_release_returned_true=True)
        state={'cell_witness':{'errors':[]},'cell_pin_cleanup':receipt,'permit':None,
            'cache':dict(lease_count=0,lease_bytes=0,response_cleanup_pending=False,dispatch_present=False,dispatch_cleanup_pending=False),
            'cells':dict(pins=0,handoffs_active_or_pending=0,cleanup_pending=False)}
        self.assertIs(cell_cleanup(state,'cell','proof'),receipt)
        for name in ('registry_removal_acknowledged','release_confirmed','permit_release_checked','exact_registry_absent'):
            with self.assertRaises(Refused):cell_cleanup(dict(state,cell_pin_cleanup=dict(receipt,**{name:0})),'cell','proof')
        for name in ('pins','handoffs_active_or_pending','cleanup_pending'):
            with self.assertRaises(Refused):cell_cleanup(dict(state,cells=dict(state['cells'],**{name:1})),'cell','proof')

    def test_two_upload_composite_selected_without_fabrication(self):
        from capture import validate_seam_geometry
        import copy
        doc=SeamActualFixtures.document();self.assertTrue(validate_seam_geometry(doc))
        for key,value in [('schema','native-owned-cell-v1'),('commits',doc['commits'][:1]),
                          ('dependencies',doc['dependencies'][:1]),('publication_anchor',{})]:
            bad=copy.deepcopy(doc);bad[key]=value
            with self.assertRaises((Refused,KeyError)):validate_seam_geometry(bad)
        bad=copy.deepcopy(doc);bad['geometry']['start_frame']=0
        with self.assertRaises(Refused):validate_seam_geometry(bad)

    def test_materialization_stage_path_argv_and_no_actual_authority(self):
        import launch,common
        value={'stage':'api_smoke_materialize','absolute_seconds':300,'work_seconds':240,
            'address_space_cap_bytes':2**30,'rss_cap_bytes':256*2**20,'cpus':[3],
            'scratch_cap_bytes':12*2**30,'argv':[str(launch.PYTHON),'-I','-B','-S',str(HERE/'capture.py'),'--materialize-only'],
            'input_uploads':{key:str(HERE.parent/('input/upload-'+key+'-10p5s.wav')) for key in ('a','b')},
            'inherited_source':{'path':str(common.SOURCE_WAV),'bytes':672044,'sha256':common.SOURCE_WAV_SHA},
            'materialization_target':str(HERE.parent/'input/MATERIALIZATION.json'),
            'source_frames':[0,336000],'split_frames':[[0,168000],[168000,336000]],'actual_run_authorized':False,
            'fixture_terminal':{'path':str(HERE.parent/'readiness/FIXTURES_TERMINAL.json'),'sha256':'a'*64},
            'entry_terminal':{'path':str(HERE.parent/'readiness/ISOLATED_ENTRY_TERMINAL.json'),'sha256':'b'*64}}
        value['external_files']=dict.fromkeys(launch.common_names()|{value[k]['path'] for k in ('fixture_terminal','entry_terminal')},'fake')
        launch.materialize_contract(value)
        for key,bad in [('stage','api_smoke_hash'),('argv',value['argv'][:-1]+['--hash-inventory']),
            ('input_uploads',{}),('materialization_target','/outside'),('source_frames',[0,1]),('inherited_source',{}),
            ('actual_run_authorized',True),('external_files',{}),('entry_terminal',{'path':'/outside','sha256':'a'*64}),
            ('controller_spec',{})]:
            with self.assertRaises(ValueError):launch.materialize_contract(dict(value,**{key:bad}))

    def test_original_popen_wait_wrapper_preserves_handle_arguments_return_and_exception(self):
        import server,threading,functools
        original_handle=object();outsider=object();calls=[];observations=[]
        hooks=server.ServerHooks.__new__(server.ServerHooks)
        hooks.ledger=types.SimpleNamespace(originals=[original_handle])
        hooks._observation_lock=threading.RLock();hooks.cell_witness={'errors':[]};hooks.journal=FakeJournal()
        hooks.original_wait_observation=lambda handle,value:observations.append((handle,value))
        def original(handle,*args,**kwargs):calls.append((handle,args,kwargs));return 17
        tree=ast.parse(Path(server.__file__).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ServerHooks')
        install=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='install')
        wrapper=next(n for n in ast.walk(install) if isinstance(n,ast.FunctionDef) and n.name=='wait')
        ns={'self':hooks,'functools':functools,'original_wait':original}
        exec(compile(ast.Module(body=[wrapper],type_ignores=[]),str(server.__file__),'exec'),ns)
        self.assertEqual(ns['wait'](original_handle,3,timeout=.1),17)
        self.assertEqual(calls,[(original_handle,(3,),{'timeout':.1})])
        self.assertEqual(observations,[(original_handle,17)])
        self.assertEqual(ns['wait'](outsider,timeout=0),17);self.assertEqual(len(observations),1)
        error=RuntimeError('exact original wait error')
        def failed(*a,**k):raise error
        ns['original_wait']=failed
        with self.assertRaises(RuntimeError) as got:ns['wait'](original_handle,timeout=2)
        self.assertIs(got.exception,error);self.assertEqual(len(observations),1)

    def test_cell_publication_observer_never_waits_claims_or_drains(self):
        import server,threading
        hooks=server.ServerHooks.__new__(server.ServerHooks)
        hooks._observation_lock=threading.RLock();hooks.cell_witness={'errors':[]};hooks.cell_pin_cleanup={}
        def forbidden(*a,**k):raise AssertionError('observer started owner work')
        operation=types.SimpleNamespace(kind='publish',done=threading.Event(),drained=threading.Event(),
            slot_released=threading.Event(),result={'status':'durable'},wait=forbidden,claim=forbidden,abandon=forbidden)
        witness={'role':'a','completion_saved':True};hooks._publications=[(witness,operation)];hooks._witnesses=[witness]
        first=hooks.cell_state();self.assertFalse(first['cell_witness']['records'][0]['publication']['done'])
        self.assertIsNone(first['cell_witness']['records'][0]['publication']['result'])
        operation.done.set();operation.slot_released.set()
        second=hooks.cell_state();self.assertEqual(second['cell_witness']['records'][0]['publication']['result'],{'status':'durable'})
        self.assertFalse(operation.drained.is_set());self.assertFalse(first['cell_witness']['records'][0]['publication']['done'])

    def test_native_two_query_admission_preserves_inherited_predicates(self):
        from server import admit_native_turns
        from common import require,canonical
        helper=types.SimpleNamespace(require=require,encoded=canonical,MAX_TURNS=32,MAX_LABELS=8)
        turns=[{'start':0.,'end':21.,'speaker_local_id':'native:one'},
               {'start':19.,'end':20.,'speaker_local_id':'native:two'}]
        self.assertEqual(admit_native_turns(helper,turns),frozenset(('native:one','native:two')))
        for frames in (307040,320000,336001,True):
            with self.assertRaisesRegex(Refused,'fixed native frame count'):
                admit_native_turns(helper,turns,frames=frames)
        for bad in ([dict(turns[0],end=21.001)], [dict(turns[0],start=-.1)],
                    [dict(turns[0],end=0)], [dict(turns[0],speaker_local_id='')],turns*17):
            with self.assertRaises(Refused):admit_native_turns(helper,bad)

    def test_materialization_generated_guard_root_admits_without_audio_read(self):
        import capture,ownership
        from common import canonical
        class BeforeAudio(Exception):pass
        with retained_root() as td:
            root=Path(td)/'component';run=root/'runs/__PRIVATE_RUN_ID_003__';run.mkdir(parents=True)
            (root/'controller').mkdir();script=root/'controller/capture.py'
            python=root.parent/'__PRIVATE_DIRECTORY_003__/runtime/bin/python3.12'
            argv=[str(python),'-I','-B','-S',str(script),'--materialize-only']
            spec={'stage':'api_smoke_materialize','argv':argv,'cwd':str(root),
                'input_uploads':{key:str(root/('input/upload-'+key+'-10p5s.wav')) for key in ('a','b')},
                'inherited_source':{'path':str(root.parent/'__PRIVATE_DIRECTORY_005__/input/primary-21s.wav'),
                    'bytes':672044,'sha256':'__PRIVATE_INTEGRITY_PIN_002__'},
                'materialization_target':str(root/'input/MATERIALIZATION.json'),
                'source_frames':[0,336000],'split_frames':[[0,168000],[168000,336000]],
                'actual_run_authorized':False}
            raw=canonical(spec);(root/'materialize.spec.json').write_bytes(raw)
            index={'status':'GATED','run_directory':str(run),'stop_path':str(run/'STOP'),
                'spec_sha256':sha(raw),'argv':argv,'original':{'pid':123,'start_ticks':456},
                'supervisor':{'pid':122},'caps':{'work_seconds':240,'absolute_seconds':300}}
            (run/'index.json').write_bytes(canonical(index))
            now=time.monotonic();env={'GUARD_ABSOLUTE_END':str(now+300),'GUARD_WORK_END':str(now+240),
                'GUARD_RUN_DIRECTORY':str(run),'GUARD_STOP_PATH':str(run/'STOP')}
            original=root.parent/'__PRIVATE_DIRECTORY_005__/input/primary-21s.wav'
            attempted=[];real_open=os.open
            def fence(path,*args,**kwargs):
                if Path(path)==original:attempted.append(str(path));raise BeforeAudio('no audio file is opened')
                return real_open(path,*args,**kwargs)
            with patch.object(capture,'__file__',str(script)),patch.object(sys,'argv',[str(script),'--materialize-only']),\
                    patch.dict(os.environ,env),patch.object(capture.os,'getcwd',return_value=str(root)),\
                    patch.object(ownership,'proc',return_value={'pid':123,'ppid':122,'start_ticks':456}),\
                    patch.object(capture.os,'open',side_effect=fence):
                with self.assertRaises(BeforeAudio):capture.materialize_main()
                self.assertEqual(attempted,[str(original)])
                for key,bad in [('spec_sha256','0'*64),('original',{'pid':999,'start_ticks':456}),
                                ('supervisor',{'pid':999}),('status','PREPARING')]:
                    (run/'index.json').write_bytes(canonical(dict(index,**{key:bad})))
                    with self.assertRaisesRegex(Refused,'materialization_guard_original_invocation_binding'):
                        capture.materialize_main()
                    self.assertEqual(len(attempted),1)
                with patch.dict(os.environ,GUARD_RUN_DIRECTORY=str(root/'runs/__PRIVATE_RUN_ID_005__')):
                    with self.assertRaisesRegex(Refused,'materialization_exact_run_scope'):capture.materialize_main()
                self.assertFalse((root/'input').exists())

    def test_closed_OFF_logical_snapshot_requires_reaped_owner_and_exact_lineage(self):
        import sqlite3
        from capture import closed_cell_snapshot
        from common import canonical
        with retained_root() as td:
            root=Path(td);directory=root/'data/native-owned-cells';directory.mkdir(parents=True)
            path=directory/'cells.sqlite3';cid='fake-chunk';sid='fake-session';cell='c'*64;digest='a'*64
            pcm=bytes(640000);receipt=canonical({'fixture_only':'source receipt'})
            part={'session_id':sid,'chunk_id':cid,'source_sha256':digest,'receipt_sha256':sha(receipt),
                'source_start':0,'source_end':320000,'logical_start':0,'logical_end':320000}
            doc={'dependencies':[part],'commit':{'chunk_id':cid,'start':0,'end':336000},'support':{'pcm16le_sha256':sha(pcm)}}
            proof=canonical(doc);ownership=sha(canonical({'session':sid,'chunk':cid,'core':[0,336000],'cell':[0,320000]}))
            db=sqlite3.connect(path)
            try:
                db.executescript("""CREATE TABLE cells(seq INTEGER PRIMARY KEY,cell_id TEXT,ownership TEXT,sid TEXT,cid TEXT,proof BLOB,pcm BLOB,state TEXT,revision INTEGER);
                    CREATE TABLE sources(cid TEXT,sid TEXT,digest TEXT,receipt BLOB);
                    CREATE TABLE dependencies(cell_id TEXT,position INTEGER,cid TEXT,digest TEXT,receipt_id TEXT,source_start INTEGER,source_end INTEGER,logical_start INTEGER,logical_end INTEGER);
                    CREATE TABLE store_meta(id INTEGER,schema TEXT,policy TEXT,primary_identity TEXT,qualification TEXT,namespace_identity TEXT,database_identity TEXT);""")
                db.execute('INSERT INTO cells VALUES(?,?,?,?,?,?,?,?,?)',(1,cell,ownership,sid,cid,proof,pcm,'retained',1))
                db.execute('INSERT INTO sources VALUES(?,?,?,?)',(cid,sid,digest,receipt))
                db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',(cell,0,cid,digest,sha(receipt),0,320000,0,320000))
                db.execute('INSERT INTO store_meta VALUES(1,?,?,?,?,?,?)',('fixture-schema','fixture-policy','p','q','n','d'))
                db.commit()
            finally:db.close()
            server=types.SimpleNamespace(stopped_and_reaped=True,proc=types.SimpleNamespace(returncode=0,pid=123),
                ledger=types.SimpleNamespace(census=lambda:[]))
            stopped={'cells':{'present':True,'status':'closed','owner_alive':False,'ready':False,
                'cleanup_pending':False,'pins':0,'handoffs_active_or_pending':0},'cell_witness':{'errors':[]}}
            spec={'data_dir':str(root/'data'),'input_pins':{'wav_sha256':digest}}
            deadline=types.SimpleNamespace(call_end=lambda seconds:time.monotonic()+seconds,check=lambda end=None:None)
            page={'cells':[{'sequence':1,'revision':1,'cell_id':cell,'start_frame':0,'end_frame':320000}]}
            details=[{'document':doc,'proof':proof}];lineage={'source_receipt_sha256':sha(receipt)}
            first=closed_cell_snapshot(server,stopped,spec,deadline,page,details,lineage)
            second=closed_cell_snapshot(server,stopped,spec,deadline,page,details,lineage)
            self.assertEqual(first['logical_sha256'],second['logical_sha256'])
            self.assertEqual(first['cells'][0]['pcm_sha256'],sha(pcm));self.assertTrue(first['query_only'])
            server.stopped_and_reaped=False
            with self.assertRaisesRegex(Refused,'closed_cell_snapshot_original_server_not_reaped'):
                closed_cell_snapshot(server,stopped,spec,deadline,page,details,lineage)
            server.stopped_and_reaped=True
            with self.assertRaisesRegex(Refused,'closed_cell_owner_cleanup_unconfirmed'):
                closed_cell_snapshot(server,dict(stopped,cells=dict(stopped['cells'],owner_alive=True)),spec,deadline,page,details,lineage)
            with self.assertRaisesRegex(Refused,'closed_source_receipt_binding'):
                closed_cell_snapshot(server,stopped,spec,deadline,page,details,{'source_receipt_sha256':'0'*64})
            changed=[dict(details[0],proof=b'explicit wrong proof')]
            with self.assertRaisesRegex(Refused,'closed_cell_row_binding'):
                closed_cell_snapshot(server,stopped,spec,deadline,page,changed,lineage)
            # Retain an ordinary fake journal-name file; never create a link/FIFO or delete fixture evidence.
            (directory/'cells.sqlite3-journal').write_bytes(b'explicit fake leftover journal')
            with self.assertRaisesRegex(Refused,'closed_cell_journal_or_unexpected_family_member'):
                closed_cell_snapshot(server,stopped,spec,deadline,page,details,lineage)

    def test_dormant_actual_targets_absent_and_templates_unapproved(self):
        import common
        root=HERE.parent
        for name in ('ACTUAL_AUTHORITY.json','controller.actual.json','actual.spec.json'):
            self.assertFalse((root/name).exists())
        authority=json.loads((root/'authority.template.json').read_text())
        controller=json.loads((root/'controller.template.json').read_text())
        outer=json.loads((root/'outer.template.json').read_text())
        self.assertFalse(authority['actual_capture_approved']);self.assertFalse(controller['actual_run_authorized'])
        self.assertEqual(controller['source_head'],common.SOURCE_HEAD)
        self.assertEqual(controller['source_tree'],common.SOURCE_TREE)
        self.assertEqual(outer['preparation_status'],'INERT_TEMPLATE_NO_EXECUTION_AUTHORITY')
        self.assertIsNone(controller['input_pins'])


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
            root=Path(td)/'component';run=root/'runs/__PRIVATE_RUN_ID_003__';run.mkdir(parents=True)
            (root/'input').mkdir();partial=root/'input/upload-a-10p5s.wav';partial.write_bytes(b'failed-partial')
            now=time.monotonic();env={'GUARD_ABSOLUTE_END':str(now+300),'GUARD_WORK_END':str(now+240),
                'GUARD_RUN_DIRECTORY':str(run),'GUARD_STOP_PATH':str(run/'STOP')}
            with patch.object(capture,'__file__',str(root/'controller/capture.py')),patch.dict(os.environ,env),\
                    patch.object(sys,'argv',[str(root/'controller/capture.py'),'--materialize-only']):
                with self.assertRaisesRegex(Refused,'fresh_materialization_only'):capture.materialize_main()
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
            clock=FakeClock();_,deadline=ApiSmokeFixtures().deadline(clock);pins=fixture_pins()
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

    def test_twelve_abi_blocks_and_exact_frame_sum(self):
        import instrumentation
        value=Instrumentation({'output_dir':'/unused-explicit-fixture'},FakeDeadline(),FakeJournal(),'native')
        self.assertEqual(value.abi.caps['native_push'],12);self.assertEqual(value.abi.caps['native_segments'],4)
        source=Path(instrumentation.__file__).read_text()
        self.assertIn('8000',source);self.assertEqual(sum(([32000]*5+[8000])*2),336000)
        FakeNativeABI().test_wrapped_fake_ABI_original_calls_and_pre_entry_overcap()

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
        profile=json.loads((HERE.parent/'PROFILE.json').read_text())
        self.assertEqual(profile['counters']['SDK_max'],{'SenseVoice':64,'each_model_constructor':2,'embedding':16,'event':8,'streams':88})

    def test_seven_upload_requests_keep_two_receipts_and_three_claims(self):
        from capture import validate_primary_counts
        counts=self.counts();self.assertTrue(validate_primary_counts(counts,self.launches()))
        counts['nonempty_claims']=4
        with self.assertRaises(Refused):validate_primary_counts(counts,self.launches())
        profile=json.loads((HERE.parent/'PROFILE.json').read_text())
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
        profile=json.loads((HERE.parent/'PROFILE.json').read_text())
        self.assertFalse(profile['revocation']['old_completed_job_postdelete_raw_result_actual'])
        self.assertEqual(profile['revocation']['actual_final_public_session_DELETE'],1)


if __name__=='__main__':
    # A reviewed exact list is mandatory. No discovery or production test imports.
    require_env=('GUARD_RUN_DIRECTORY','GUARD_STOP_PATH','GUARD_WORK_END','GUARD_ABSOLUTE_END')
    if not all(key in os.environ for key in require_env):raise SystemExit('Reviewed outer guard required')
    suite=unittest.TestSuite()
    classes=(AdmissionFixtures,RawAndControlFixtures,SnapshotFixtures,OwnershipFixtures,FakeNativeABI,FakeSDKFixtures,ApiSmokeFixtures,DurableCellFixtures,SeamActualFixtures)
    expected=json.loads((HERE.parent/'FIXTURE_IDS.json').read_text())['ids']
    observed=[]
    for cls in classes:
        for name in unittest.defaultTestLoader.getTestCaseNames(cls):
            observed.append(cls.__name__+'.'+name)
    if sorted(observed)!=expected or len(observed)!=len(expected):raise SystemExit('Exact frozen fixture IDs required')
    for cls in classes:
        for name in unittest.defaultTestLoader.getTestCaseNames(cls):suite.addTest(cls(name))
    result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
    print(json.dumps({'label':'MODEL_FREE_EXPLICIT_FAKE_PRODUCERS','tests_run':result.testsRun,
                      'failures':len(result.failures),'errors':len(result.errors),
                      'skips':len(result.skipped),'actual_acoustic_calls':0}),flush=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)
