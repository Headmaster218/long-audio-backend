"""Fresh strict reconstruction: actual hooks, synthetic native/model/Store edges.

No model, service, primary SQL or original audio is exercised. Generated WAV
controls cover positive descriptor flow; unknown-close controls are doubles.
"""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from types import SimpleNamespace
import errno
import hashlib
import wave

import pytest

from audio_backend import worker as wm, native_owned_cell_capture as cap
from audio_backend import recording_primary_provenance as pp, native_owned_core_seams as core
from audio_backend import strict_recording as strict
from audio_backend.lexical_evidence import PRIMARY_TIMESTAMP
from audio_backend.native_owned_cells import SourceSpan, Unavailable, RATE
from audio_backend.native_owned_cell_store import Budget
from test_native_owned_cells import observed_window

PENDING_PUBLICATION = None


class StoreDouble:
    def __init__(self, settings):
        self.settings=settings; self.unknown=[]; self.pauses=[]; self.commits=[]; self.waits=[]
    def strict_cleanup_unknown(self, owner, **kw):self.unknown.append((owner,kw))
    def strict_cleanup_confirmed(self):return not self.unknown
    def strict_cleanup_uncertain(self):return bool(self.unknown)
    def _strict_refuse(self):
        raise self.unknown[0][1]['error']
    def strict_pause(self, chunk, reason, *, cleanup_confirmed):
        self.pauses.append((chunk['id'],cleanup_confirmed,reason))
    def strict_wait_for_input(self, chunk, end, final):self.waits.append((end,final))
    def chunk(self, cid):return {'status':'running'}
    def db(self):return nullcontext(SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchone=lambda:None)))
    def commit_window(self, chunk, offset, end, result):
        value=strict.require_prepared(chunk,offset,end,result)
        self.commits.append(value);return True


def case(tmp_path, *, mode='empty', strict_mode=True):
    settings=SimpleNamespace(strict_primary_enabled=True, data_dir=tmp_path,
        diarization_backend='nemotron', window_seconds=2., context_seconds=0., overlap_backend='none',
        speaker_support_enabled=False, speaker_support_max_records=0, lexical_evidence_enabled=False,
        native_owned_support_enabled=False, recording_speaker_links_enabled=True,
        moss_automatic_enabled=True, native_owned_bounded_core_support_enabled=True,
        native_owned_bounded_core_support_seconds=.75, asr_backend='sensevoice',
        inference_config=lambda:{})
    (tmp_path/'tmp').mkdir(exist_ok=True)
    store=StoreDouble(settings)
    chunk={'session_id':'session','id':'chunk','digest':'c'*64,'timeline_start':0.,
        'duration':2.,'next_offset':0.,'metadata':{},'_strict_primary':strict_mode,
        '_strict_attempt_nonce':'1'*32}
    witness,native,pcm,_=observed_window(2*RATE)
    native['end']=2.
    if mode=='retained':
        native['turns']=native['turns'][:1]
        witness.observe({'turns':deepcopy(native['turns'])})
    part=SourceSpan('session','chunk','b'*64,'c'*64,2*RATE,0,2*RATE,0,2*RATE)
    owner=SimpleNamespace(enabled=True,_epoch=object(),health=lambda:{'ready':True},
        bounded_core_enabled=True,retention_store_key='d'*64)
    authority=SimpleNamespace(store=store,uploads=tmp_path,
        list_read_scope=lambda *a,**kw:nullcontext(),mapping=lambda *a:(part,),original_pcm=lambda *a:pcm)
    capture=cap.Capture(owner,authority);capture.primary_only=True
    capture.core_seams=core.CoreSeams(capture)
    capture.begin_prepare(chunk,None)
    capture.witness=witness;capture.observed_reset_generation=1
    capture.analyzer=pcm;capture.analyzer_start=0
    capture.end_prepare()
    label=native['turns'][0]['speaker_local_id']
    segments=[] if mode=='empty' else [{'start':.1,'end':.3,'text':'speech','raw_text':'speech',
        'speaker_local_id':None if mode=='unknown_only' else label,'asr_backend':'sensevoice',
        'timestamp_kind':PRIMARY_TIMESTAMP, 'confidence':0,
        '_units':[{'start':.1,'end':.3,'ownership_anchor':.1,'text':'speech','raw_text':'speech',
            'anchor_kind':'ctc_emission'}]}]
    raw={'segments':segments,'events':[],'speaker_turns':deepcopy(native['turns']),
        'metadata':{'diarization_turns':deepcopy(native['turns']),'asr_backend':'sensevoice',
            'timestamp_semantics':PRIMARY_TIMESTAMP}}
    # Use the actual provenance observer/normalization/ownership chain.
    result=deepcopy(raw);observed=pp.observe(result,native)
    wm.validate_result(result,2.);pp.validate_observation(result,observed)
    wm.own_core(result,0.,2.)
    result['metadata']['core_cut']={'nominal':2.,'actual':2.,'reason':'fixed_or_final'}
    return SimpleNamespace(settings=settings,store=store,chunk=chunk,capture=capture,native=native,
        pcm=pcm,result=result,raw=raw,part=part)


@pytest.mark.parametrize('mode',['empty','unknown_only','retained'])
def test_capture_success_requires_real_phase_and_exact_rows(tmp_path,mode):
    c=case(tmp_path,mode=mode)
    c.capture.prepare(c.chunk,c.native,0.,2.,c.result,required=True)
    value=c.result['_strict_capture'];assert type(value) is strict.CaptureOutcome
    assert value.status==mode
    c.capture.prepare_core_seam(c.chunk,c.native,0.,2.,c.result,None,lambda:False,required=True)
    assert strict.require_prepared(c.chunk,0.,2.,c.result) is value
    changed=dict(c.chunk,_strict_attempt_nonce='2'*32)
    with pytest.raises(ValueError):strict.require_prepared(changed,0.,2.,c.result)


def test_empty_status_without_observation_cannot_authorize(tmp_path):
    c=case(tmp_path);c.result.pop('_primary_observation',None);c.result['_primary_status']='empty'
    with pytest.raises(Unavailable):c.capture.prepare(c.chunk,c.native,0.,2.,c.result,required=True)
    assert '_strict_capture' not in c.result
    with pytest.raises(Unavailable):c.capture.prepare_core_seam(c.chunk,c.native,0.,2.,c.result,None,lambda:False,required=True)
    assert '_native_core_seam' not in c.result


def test_phase_exit_refusal_cannot_publish_and_next_begin_is_fresh(tmp_path):
    c=case(tmp_path)
    class ExitFailure:
        @contextmanager
        def phase(self):
            yield
            raise Unavailable('deadline')
    c.capture.budget=ExitFailure()
    with pytest.raises(Unavailable):c.capture.prepare(c.chunk,c.native,0.,2.,c.result,required=True)
    assert '_strict_capture' not in c.result and c.capture._strict_failed
    c.capture.begin_prepare(c.chunk,None)
    assert type(c.capture.budget) is Budget and not c.capture._strict_failed
    c.capture._strict_cleanup_uncertain=True; old=c.capture.budget
    with pytest.raises(Unavailable):c.capture.begin_prepare(c.chunk,None)
    assert c.capture.budget is old


def test_strict_empty_still_validates_original_native_pcm(tmp_path):
    c=case(tmp_path);c.capture.authority.original_pcm=lambda *a:b'\x00\0'*32000
    with pytest.raises(Unavailable):c.capture.prepare(c.chunk,c.native,0.,2.,c.result,required=True)
    assert '_strict_capture' not in c.result


def test_actual_worker_missing_then_manual_new_attempt_only_second_compact(tmp_path,monkeypatch):
    c=case(tmp_path);attempts=[]
    class Coordinator:
        def __init__(self,*a,**kw):self.process=None;attempts.append(self)
        def prepare(self,*a):
            c.capture.witness,c.native,c.pcm,_=observed_window(2*RATE)
            c.capture.observed_reset_generation+=1
            return c.native
        def close(self):self.process=None
    class Analyzer:
        def __init__(self,*a):pass
        def analyze(self,*a,**kw):
            value=deepcopy(c.raw)
            if len(attempts)==1:value['metadata'].pop('diarization_turns')
            return value
        def close(self):pass
    worker=wm.Worker(c.store,c.settings,analyzer_factory=Analyzer);worker.native_owned_cells=c.capture
    monkeypatch.setattr(wm,'NativeCoordinator',Coordinator)
    monkeypatch.setattr(wm,'stream_info',lambda *a:None)
    monkeypatch.setattr(wm,'check_resources',lambda *a:None)
    def extract(chunk,start,end,wav,*a,**kw):
        with wave.open(str(wav),'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(RATE);out.writeframes(c.pcm)
    monkeypatch.setattr(worker,'extract_pcm',extract)
    worker._run_strict_chunk(c.chunk,None,None,False,None)
    assert not c.store.commits and len(c.store.pauses)==1 and c.store.pauses[0][1]
    assert worker.native is None and not c.capture._seam_provisional
    # This is explicit fake Store manual authorization, not an HTTP/SQL claim.
    retry=dict(c.chunk,_strict_attempt_nonce='2'*32)
    worker._run_strict_chunk(retry,None,None,False,None)
    assert len(attempts)==2 and len(c.store.commits)==1
    assert (c.store.commits[0].start,c.store.commits[0].end)==(0,32000)
    assert c.store.commits[0].nonce=='2'*32
    assert worker._strict_retire()


def test_worker_preopportunity_input_wait_is_not_manual_failure(tmp_path,monkeypatch):
    c=case(tmp_path);worker=wm.Worker(c.store,c.settings,analyzer_factory=lambda *_:None)
    worker.native_owned_cells=c.capture
    monkeypatch.setattr(wm,'stream_info',lambda *a:{'end':0.,'start':0.,'final':False,'previous':None})
    monkeypatch.setattr(wm,'check_resources',lambda *a:None)
    c.store.strict_cleanup_confirmed=lambda:False  # ordinary concurrent DB owner, not sticky
    worker._run_strict_chunk(c.chunk,None,None,False,None)
    assert c.store.waits==[(0.,False)] and not c.store.pauses and worker.native is None
    witness=c.capture.witness
    state={key:witness.state[key] for key in ('epoch_id','fingerprint','local_capacity','finished')}
    state.update({key+'_seconds':witness.state[key]['raw'] for key in ('input','processed','stable')})
    coordinator=SimpleNamespace(key=witness.epoch,provenance=witness.provenance,state=state)
    assert c.capture.require_strict_wait(coordinator)
    c.capture.witness=cap.RollingWitness()
    with pytest.raises(Unavailable):c.capture.require_strict_wait(coordinator)
    # The existing normal-wait path must not publish eligibility if the final
    # original intent release is uncertain. Restart will still see active work.
    import threading
    c2=case(tmp_path);worker2=wm.Worker(c2.store,c2.settings,analyzer_factory=lambda *_:None)
    worker2.native_owned_cells=c2.capture
    release_error=OSError('release uncertain');order=[]
    permit=SimpleNamespace(_condition=threading.RLock(),_handles={},_unavailable=None,
        _primary_observation_failure=None,finish_cohort=lambda **kw:order.append('cohort'))
    intent=SimpleNamespace(entered=True,release=lambda:(_ for _ in ()).throw(release_error))
    with pytest.raises(OSError) as raised:worker2._run_strict_chunk(c2.chunk,intent,permit,False,None)
    assert raised.value is release_error and order==['cohort'] and not c2.store.waits
    assert c2.store.pauses[0][1] is False and worker2._strict_wait_pending==(0.,False,0.)
    # Fair positive progress is also deferred; its final write error stays in
    # the original error/manual path rather than escaping an unprotected tail.
    c3=case(tmp_path);worker3=wm.Worker(c3.store,c3.settings,analyzer_factory=lambda *_:None)
    transition_error=ValueError('requeue write refused');order=[]
    monkeypatch.setattr(worker3,'process',lambda *a:True)
    monkeypatch.setattr(worker3,'_finish_native_support',lambda:order.append('support'))
    permit=SimpleNamespace(_condition=threading.RLock(),_handles={},_unavailable=None,
        _primary_observation_failure=None,finish_cohort=lambda **kw:order.append('cohort'))
    intent=SimpleNamespace(entered=True,release=lambda:order.append('release'))
    def requeue(chunk):order.append('requeue');raise transition_error
    c3.store.requeue_core=requeue
    worker3._run_strict_chunk(c3.chunk,intent,permit,True,None)
    assert order==['support','cohort','release','requeue'] and c3.store.pauses[0][1] is True
    assert 'requeue write refused' in c3.store.pauses[0][2]


def test_failure_and_cleanup_keep_original_and_one_shot_owner(tmp_path,monkeypatch):
    c=case(tmp_path);worker=wm.Worker(c.store,c.settings,analyzer_factory=lambda *_:None)
    original=ValueError('missing proof');calls=[];cohorts=[]
    close_error=OSError('close uncertain')
    c.settings._native_execution_permit=SimpleNamespace(finish_cohort=lambda **kw:cohorts.append(kw))
    def body(*a):raise original
    def close():calls.append('close');raise close_error
    worker.native=SimpleNamespace(process=None,close=close)
    monkeypatch.setattr(worker,'process',body)
    with pytest.raises(ValueError) as raised:worker._run_strict_chunk(c.chunk,None,None,False,None)
    assert raised.value is original and calls==['close'] and c.store.unknown[0][0] is worker
    assert c.store.pauses[0][1] is False and not cohorts and worker._strict_first_error is close_error
    assert worker._strict_retire() is False and calls==['close']
    # A body inspection OSError is already sticky before finally: the exact
    # intent remains held, no subsequent close or manual grant is fabricated.
    import threading
    c2=case(tmp_path);worker2=wm.Worker(c2.store,c2.settings,analyzer_factory=lambda *_:None)
    io=OSError('inspection close uncertain');released=[];cohorts=[]
    intent=SimpleNamespace(entered=True,release=lambda:released.append(True))
    permit=SimpleNamespace(_condition=threading.RLock(),_handles={},_unavailable=None,
        _primary_observation_failure=None,finish_cohort=lambda **kw:cohorts.append(kw))
    monkeypatch.setattr(worker2,'process',lambda *a:(_ for _ in ()).throw(io))
    with pytest.raises(OSError) as raised:worker2._run_strict_chunk(c2.chunk,intent,permit,False,None)
    assert raised.value is io and worker2._strict_intent_owner is intent and not released and not cohorts
    assert c2.store.pauses[0][1] is False and worker2._strict_first_error is io


def test_terminal_stop_requires_permit_flags_and_empty_handles(tmp_path):
    import threading
    c=case(tmp_path);worker=wm.Worker(c.store,c.settings)
    permit=SimpleNamespace(_condition=threading.RLock(),_handles={},_unavailable='uncertain',
        _primary_observation_failure=None,finish_cohort=lambda **kw:None)
    c.settings._native_execution_permit=permit
    assert worker.stop() is False and c.store.unknown


def test_native_constructor_and_binding_failure_keep_fixed_original_witness(tmp_path,monkeypatch):
    from audio_backend import native_worker
    c=case(tmp_path);worker=wm.Worker(c.store,c.settings);worker._strict_active=True
    process=SimpleNamespace(proc=object(),busy=False)
    bind_error=RuntimeError('bind uncertain')
    monkeypatch.setattr(native_worker,'NativeProcess',lambda settings:process)
    c.settings._native_execution_permit=SimpleNamespace(bind_primary=lambda *a,**kw:(_ for _ in ()).throw(bind_error))
    with pytest.raises(RuntimeError) as raised:worker._native_process_factory(c.settings)
    assert raised.value is bind_error and worker._strict_native_owner is process
    assert worker._strict_first_error is bind_error and c.store.unknown[0][0] is worker
    c2=case(tmp_path);worker2=wm.Worker(c2.store,c2.settings);worker2._strict_active=True
    constructor_error=RuntimeError('constructor uncertain')
    class Partial:
        def __init__(self,settings):self.marker=object();raise constructor_error
    monkeypatch.setattr(native_worker,'NativeProcess',Partial)
    with pytest.raises(RuntimeError) as raised:worker2._native_process_factory(c2.settings)
    assert raised.value is constructor_error and worker2._strict_first_error is constructor_error
    trace=constructor_error.__traceback__;found=[]
    while trace is not None:
        value=trace.tb_frame.f_locals.get('self')
        if type(value) is Partial:found.append(value)
        trace=trace.tb_next
    assert len(found)==1 and worker2._strict_native_owner is None
    assert worker2._strict_retire() is False


def test_exact_intentional_cancellation_not_os_eintr():
    for exc in (InterruptedError(),InterruptedError('Worker stopping or chunk paused/cancelled'),
            InterruptedError('Native diarization interrupted'),InterruptedError('Primary wait interrupted')):
        assert wm.Worker._strict_cancellation(exc)
    class Child(InterruptedError):pass
    for exc in (InterruptedError(errno.EINTR,'interrupted'),Child(),InterruptedError('other'),OSError()):
        assert not wm.Worker._strict_cancellation(exc)


class PublicationOwner:
    def __init__(self):self.source=None;self.tokens=[];self.errors=[]
    def acquired(self,source):assert self.source is None;self.source=source
    def token(self,token):assert self.source is token.fd;self.tokens.append(token);self.source=None
    def released(self,source):
        if not self.errors:assert self.source is source;self.source=None
    def failed(self,error):self.errors.append(error)


def test_strict_publication_positive_generated_descriptor(tmp_path):
    global PENDING_PUBLICATION
    assert PENDING_PUBLICATION is None
    c=case(tmp_path);path=tmp_path/'tiny.part';owner=PublicationOwner()
    with wave.open(str(path),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(RATE);out.writeframes(b'\x01\0'*16)
    data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
    with path.open('rb') as stream:identity=c.capture.streamed_descriptor(stream.fileno(),digest,len(data))
    token=None;original=None;PENDING_PUBLICATION=owner
    try:
        token=c.capture.prepare_upload(path,digest,len(data),identity,strict_owner=owner)
        assert owner.tokens==[token] and owner.source is None and not owner.errors
        final=path.with_suffix('.audio');path.rename(final)
        receipt=c.capture.observe_recording_receipt(token,'session','chunk',final,0.,'stream',close_token=False)
        assert receipt is not None and not token.closed
    except BaseException as exc:
        original=exc;raise
    finally:
        if token is not None:
            try:token.close()
            except BaseException as cleanup:
                owner.failed(cleanup)
                if original is None:raise
        if not owner.errors and owner.source is None:PENDING_PUBLICATION=None


def test_strict_publication_unknown_source_close_is_retained(tmp_path,monkeypatch):
    c=case(tmp_path);owner=PublicationOwner();calls=[]
    original=ValueError('shape')
    class Source:
        def close(self):calls.append('close');raise OSError('unknown close')
    source=Source()
    def opening(*a):owner.acquired(source);return source,(99,)
    monkeypatch.setattr(cap,'_open_strict',opening)
    identity=cap.StreamIdentity('c'*64,90,(1,),Budget())
    # Failure is before token transfer; exact acquired source remains owned.
    with pytest.raises(Exception):c.capture.prepare_upload(tmp_path/'tiny.part','c'*64,90,identity,strict_owner=owner)
    assert owner.source is source and owner.errors and calls==['close']


def test_readonly_close_unknown_blocks_retry_but_positive_busy_keeps_original_budget(tmp_path,monkeypatch):
    import sqlite3
    import threading
    c=case(tmp_path);owner=object();connections=[];closes=[]
    authority=cap.PrimaryAuthority.__new__(cap.PrimaryAuthority)
    authority.store=c.store;authority.path=tmp_path/'unused.sqlite';authority.identity=(1,)
    authority._list_reads=threading.local();authority._strict_failure_owner=owner
    monkeypatch.setattr(cap,'_identity',lambda path:(1,))
    error=sqlite3.OperationalError('synthetic close busy');error.sqlite_errorcode=sqlite3.SQLITE_BUSY
    class DB:
        in_transaction=False
        def set_progress_handler(self,*a):pass
        def execute(self,sql,*a):return self
        def close(self):closes.append(self);raise error
    def connect(*a,**kw):value=DB();connections.append(value);return value
    monkeypatch.setattr(cap.sqlite3,'connect',connect)
    budget=Budget()
    with pytest.raises(sqlite3.OperationalError) as raised:authority._read(budget,'source_row',lambda db:'value')
    assert raised.value is error and len(connections)==len(closes)==1
    assert c.store.unknown[0][0] is owner and c.store.unknown[0][1]['error'] is error
    with pytest.raises(sqlite3.OperationalError):authority._read(budget,'source_row',lambda db:'unreachable')
    assert len(connections)==len(closes)==1
    # The list-scope's distinct original close preserves an earlier body error.
    c2=case(tmp_path);authority.store=c2.store;original=ValueError('original body')
    with pytest.raises(ValueError) as raised:
        with authority.list_read_scope(Budget()):
            authority._list_reads.current['db']=DB()  # a distinct original synthetic connection
            raise original
    assert raised.value is original and c2.store.unknown[0][1]['error'] is error
    # A known positive close is not sticky: the existing finite BUSY retry and
    # same Budget object remain, without a new SQL transaction/model workload.
    c3=case(tmp_path);authority.store=c3.store;connections.clear();closes.clear();reads=[]
    class PositiveDB(DB):
        def close(self):closes.append(self)
    def positive(*a,**kw):value=PositiveDB();connections.append(value);return value
    monkeypatch.setattr(cap.sqlite3,'connect',positive)
    busy=sqlite3.OperationalError('synthetic read busy');busy.sqlite_errorcode=sqlite3.SQLITE_BUSY
    def read(db):
        reads.append(db)
        if len(reads)==1:raise busy
        return 'accepted'
    budget=Budget()
    assert authority._read(budget,'source_row',read)=='accepted'
    assert len(connections)==len(closes)==len(reads)==2 and not c3.store.unknown
