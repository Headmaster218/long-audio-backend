"""Fresh read contention at the real pre-window barrier; no saved DB or models.

Acoustic extraction/capture/plans in the Worker test are explicitly synthetic;
Store, before_window, readonly SQLite, commit, loop/permit and cleanup are real.
"""
from contextlib import contextmanager
import logging
import sqlite3
import threading
import time
from types import SimpleNamespace
import wave

import pytest

from audio_backend import moss_automatic as auto, moss_result_store as history
from audio_backend import native_owned_core_seams as core
from audio_backend.native_execution_permit import NativeExecutionPermit
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker
from test_native_execution_permit import sample
from test_moss_automatic_integration import seed_intent


@pytest.fixture
def fixture(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0,overlap_backend='none')
    (tmp_path/'tmp').mkdir()
    store=Store(tmp_path/'fresh.sqlite3',settings)
    sid=store.create_session('synthetic preflight')['id']
    cid,_=store.add_chunk(sid,'one','a'*64,tmp_path/'synthetic.wav',10.5,offset=94.5)
    with store.db() as db:
        core.init_schema(db)
        db.execute("INSERT INTO recording_batches(id,session_id,capture_id,state,created) VALUES('b',?,'synthetic','queued',0)",(sid,))
        db.execute("INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,capture_start_frame,native_receipt_status) VALUES(?,'b',0,168000,336044,1512000,'synthetic')",(cid,))
        db.execute("UPDATE chunks SET next_offset=9.1 WHERE id=?",(cid,))
    settings.moss_automatic_enabled=True
    archive=history.MossResultStore(store)
    a=auto.Automatic(store,archive,capture=object(),jobs=SimpleNamespace(_enabled=lambda:True))
    return store,archive,a,cid


def exclusive(store):
    db=sqlite3.connect(store.path,timeout=0)
    assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0]=='delete'
    db.execute('BEGIN EXCLUSIVE')
    return db


def release(db):db.rollback();db.close()


def markers(caplog):
    rows=[r.automatic_preflight_deferred for r in caplog.records if hasattr(r,'automatic_preflight_deferred')]
    for row in rows:assert set(row)=={'operation','callsite','phase','reason','sqlite_code'}
    return rows


def test_genuine_busy_at_before_window_is_closed_read_with_direct_callsite_marker(fixture,caplog):
    store,archive,a,cid=fixture;chunk=store.chunk(cid)
    blocker=exclusive(store)
    try:
        assert a.before_window(chunk) is auto._WINDOW_READ_DEFERRED
    finally:release(blocker)
    row,=markers(caplog)
    assert row=={'operation':'read','callsite':'before_window','phase':'body','reason':'result_storage_unavailable','sqlite_code':sqlite3.SQLITE_BUSY}
    assert a.last_reason=='automatic_preflight_read_deferred'
    assert store.chunk(cid)['next_offset']==9.1 and store.chunk(cid)['status']=='queued'
    assert a.before_window(chunk) is True


def test_genuine_shared_cache_read_locked_is_classified(fixture,monkeypatch):
    store,archive,a,cid=fixture;chunk=store.chunk(cid)
    connect=sqlite3.connect;uri='file:'+str(store.path)+'?cache=shared'
    blocker=connect(uri,uri=True,timeout=0)
    blocker.execute('BEGIN IMMEDIATE');blocker.execute('UPDATE recording_work SET position=position')
    def shared(*args,**kw):return connect(args[0]+'&cache=shared',*args[1:],**kw)
    monkeypatch.setattr(history.sqlite3,'connect',shared)
    try:assert a.before_window(chunk) is auto._WINDOW_READ_DEFERRED
    finally:release(blocker)
    assert a.before_window(chunk) is True


@contextmanager
def injected(monkeypatch,*,code=261,write=False,close_failure=False,phase='body'):
    """Real connection, explicitly injected numeric failure/ack at one boundary."""
    connect=sqlite3.connect;closed=[];calls=[]
    error=sqlite3.OperationalError('PRIVATE_EXCEPTION_PAYLOAD')
    if code is not None:error.sqlite_errorcode=code
    class Fault(sqlite3.Connection):
        def execute(self,sql,*args,**kw):
            calls.append(sql)
            if phase=='begin' and sql in ('BEGIN','BEGIN IMMEDIATE'):raise error
            if phase=='body' and sql.startswith('SELECT'):raise error
            return super().execute(sql,*args,**kw)
        def commit(self):
            super().commit()
            if phase=='commit':raise error
        def close(self):
            super().close();closed.append(True)
            if close_failure:raise OSError('PRIVATE_CLOSE_FAILURE')
    with monkeypatch.context() as patch:
        patch.setattr(history.sqlite3,'connect',lambda *a,**k:connect(*a,**k,factory=Fault))
        yield error,closed,calls


@pytest.mark.parametrize('code',[sqlite3.SQLITE_BUSY,261,517,sqlite3.SQLITE_LOCKED,262])
@pytest.mark.parametrize('phase',['begin','body'])
def test_private_signal_preserves_public_mapping_only_after_close(fixture,monkeypatch,code,phase):
    _,archive,_,_=fixture
    with injected(monkeypatch,code=code,phase=phase) as (_,closed,calls):
        with pytest.raises(history._ReadDeferred) as caught:
            with archive._database() as db:db.execute('SELECT 1')
        assert closed==[True] and isinstance(caught.value,ReadError)
        assert (caught.value.status_code,caught.value.reason,caught.value.sqlite_code,caught.value.phase)==(503,'result_storage_unavailable',code,phase)
        assert calls.count('BEGIN')==1 and 'BEGIN IMMEDIATE' not in calls


@pytest.mark.parametrize('code',[None,'5',True,-1,2**31,sqlite3.SQLITE_IOERR,sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_INTERRUPT,sqlite3.SQLITE_READONLY])
def test_nonbusy_or_malformed_numeric_errors_keep_original_mapping(fixture,monkeypatch,code):
    _,archive,a,cid=fixture
    with injected(monkeypatch,code=code):
        with pytest.raises(ReadError) as caught:a.before_window({'id':cid,'session_id':'irrelevant'})
        assert type(caught.value) is ReadError
        assert caught.value.reason=='result_storage_unavailable'


@pytest.mark.parametrize('phase',['begin','body','commit'])
def test_write_and_lost_commit_ack_are_never_safe_read_deferrals(fixture,monkeypatch,phase):
    store,archive,_,_=fixture
    with injected(monkeypatch,phase=phase):
        with pytest.raises(ReadError) as caught:
            with archive._database(write=True) as db:
                if phase=='body':db.execute('SELECT 1')
                else:db.execute("UPDATE sessions SET name='committed fixture' WHERE name='synthetic preflight'")
        assert type(caught.value) is ReadError
    with store.db() as db:
        name=db.execute('SELECT name FROM sessions').fetchone()[0]
    assert (name=='committed fixture')==(phase=='commit')


def test_failed_close_cannot_be_reported_as_safe_deferral(fixture,monkeypatch,caplog):
    _,_,a,cid=fixture
    with injected(monkeypatch,close_failure=True):
        with pytest.raises(OSError,match='PRIVATE_CLOSE_FAILURE'):a.before_window({'id':cid})
    assert not markers(caplog)


@pytest.mark.parametrize('failure',['clock','vm','missing','changed','schema'])
def test_noncontention_barrier_failure_does_not_defer(fixture,monkeypatch,failure):
    store,archive,a,cid=fixture;chunk=store.chunk(cid)
    if failure=='clock':
        times=iter([0.,1.]);archive.clock=lambda:next(times)
    elif failure=='vm':
        original=archive._database
        @contextmanager
        def limited():
            with original(budget=[time.monotonic()+.25,50000]) as db:yield db
        monkeypatch.setattr(archive,'_database',limited)
    elif failure=='missing':archive.path.unlink()
    elif failure=='changed':archive.database_identity=(-1,-1)
    else:
        with store.db() as db:db.execute('DROP TABLE recording_work')
    with pytest.raises(ReadError) as caught:a.before_window(chunk)
    assert type(caught.value) is ReadError and caught.value.reason=='result_storage_unavailable'


def test_logging_sink_failure_cannot_change_preflight_deferral(fixture,monkeypatch):
    store,_,a,cid=fixture;chunk=store.chunk(cid);blocker=exclusive(store)
    def broken(*args,**kw):raise RuntimeError('PRIVATE_SINK')
    monkeypatch.setattr(logging.Logger,'warning',broken)
    try:assert a.before_window(chunk) is auto._WINDOW_READ_DEFERRED
    finally:release(blocker)


def test_capacity_and_configuration_remain_explicit_backpressure(fixture):
    store,_,a,cid=fixture;chunk=store.chunk(cid)
    with store.db() as db:db.execute("UPDATE moss_automatic_usage_v1 SET rows=256,charge=4194304,pending=256 WHERE scope='' ")
    assert a.before_window(chunk) is False and a.last_reason=='automatic_pending_capacity'
    with store.db() as db:db.execute("UPDATE moss_automatic_usage_v1 SET rows=0,charge=0,pending=0 WHERE scope='' ")
    a.closed=True
    assert a.before_window(chunk) is False and a.last_reason=='automatic_configuration_unavailable'


@pytest.mark.parametrize('with_permit',[False,True])
@pytest.mark.parametrize('cancel',[False,True])
def test_real_worker_preflight_contention_cleanup_backoff_and_exact_once(fixture,monkeypatch,with_permit,cancel):
    store,archive,a,cid=fixture
    settings=store.settings;p=NativeExecutionPermit(settings,sampler=lambda _:sample()) if with_permit else None
    if p is not None:settings._native_execution_permit=p
    with store.db() as db:
        seed_intent(db,store.chunk(cid)['session_id'],21)
        before=[tuple(r) for r in db.execute('SELECT * FROM moss_automatic_intents_v1')]
    optional=[];analyzed=[];prepared=[];commits=[];cleaned=[];native_closed=[]
    class Analyzer:
        def analyze(self,*args,**kw):analyzed.append(True);return {'segments':[],'events':[],'speaker_turns':[],'metadata':{}}
        def close(self):cleaned.append('analyzer')
    w=Worker(store,settings,lambda _:Analyzer());w.moss_automatic=a
    w.native=SimpleNamespace(close=lambda:native_closed.append(True))
    w.native_owned_cells=SimpleNamespace(prepare=lambda *args,**kw:prepared.append(True) or (),prepare_core_seam=lambda *args,**kw:None)
    a.primary_yield_due=lambda:False;a.step=lambda **kw:optional.append(True)
    # Ledger-only no-new-candidate plan; existing pending proof is checked intact.
    monkeypatch.setattr(auto,'plans',lambda *args:[])
    def extract(chunk,start,end,path,tmp,stop,**kw):
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*round((end-start)*16000))
    w.extract_pcm=extract
    original_commit=store.commit_window
    def commit(chunk,offset,end,result):
        commits.append((offset,end));value=original_commit(chunk,offset,end,result);w.stop_event.set();return value
    monkeypatch.setattr(store,'commit_window',commit)
    original=a.before_window;attempts=[]
    def contend(chunk):
        attempts.append(chunk['next_offset'])
        if len(attempts)==1:
            blocker=exclusive(store)
            try:return original(chunk)
            finally:release(blocker)
        return original(chunk)
    a.before_window=contend
    original_process=w.process
    def process(chunk):
        try:return original_process(chunk)
        finally:cleaned.append('process')
    w.process=process
    class Stop(threading.Event):
        def __init__(self):super().__init__();self.waits=[]
        def wait(self,timeout=None):
            self.waits.append(timeout)
            assert timeout==.1 and cleaned==['process']
            assert not optional and not analyzed and not prepared and not commits and not native_closed
            row=store.chunk(cid);assert row['status']=='queued' and row['next_offset']==9.1
            if p is not None:assert not p._primary and not p._probe and p._optional is None
            if cancel:self.set()
            return super().wait(timeout)
    w.stop_event=Stop();w.wake.set()
    w.start()
    try:
        w.thread.join(3);assert not w.thread.is_alive()
        assert w.stop_event.waits==[.1] and optional==[] and not native_closed
        assert attempts==([9.1] if cancel else [9.1,9.1])
        assert commits==([] if cancel else [(9.1,10.5)])
        assert len(analyzed)==len(prepared)==int(not cancel)
        row=store.chunk(cid);assert (row['status'],row['next_offset'])==(('queued',9.1) if cancel else ('completed',10.5))
        with store.db() as db:assert [tuple(r) for r in db.execute('SELECT * FROM moss_automatic_intents_v1')]==before
        assert w.current_chunk is None
        if p is not None:assert not p._primary and not p._probe
    finally:assert w.stop()
