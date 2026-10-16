"""Fresh SQLite claim contention; no model, native process, or saved database."""
from contextlib import contextmanager
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from audio_backend import storage
from audio_backend.native_execution_permit import NativeExecutionPermit
from audio_backend.settings import Settings
from audio_backend.worker import Worker


@pytest.fixture
def queued(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    # Enable only the claim-policy flag after constructing model-free settings.
    settings.moss_automatic_enabled=True
    store=storage.Store(tmp_path/'jobs.sqlite3',settings)
    sid=store.create_session('synthetic contention')['id']
    cid,new=store.add_chunk(sid,'one','a'*64,tmp_path/'unused.wav',1.)
    assert new
    return store,settings,cid


def lock(store):
    db=sqlite3.connect(store.path,timeout=0)
    db.execute('BEGIN IMMEDIATE')
    return db


def close_lock(db):
    db.rollback();db.close()


def test_real_busy_before_begin_preserves_queue_and_cursor(queued):
    store,settings,cid=queued
    cursor=(0.,'');store._automatic_claim_after=cursor
    blocker=lock(store)
    try:
        with pytest.raises(storage._ClaimDeferred) as caught:store.claim()
        assert caught.value.__context__.sqlite_errorcode & 0xff == sqlite3.SQLITE_BUSY
        assert store._automatic_claim_after==cursor
        assert store.chunk(cid)['status']=='queued'
    finally:close_lock(blocker)
    assert store.claim()['id']==cid
    assert store.chunk(cid)['status']=='running'
    assert store.claim() is None


def test_real_shared_cache_locked_before_begin_is_classified(queued,monkeypatch):
    store,settings,cid=queued
    connect=sqlite3.connect
    uri='file:'+str(store.path)+'?cache=shared'
    blocker=connect(uri,uri=True,timeout=0)
    blocker.execute('BEGIN IMMEDIATE')
    def shared(*args,**kwargs):
        return connect(uri,uri=True,**kwargs)
    monkeypatch.setattr(storage.sqlite3,'connect',shared)
    try:
        with pytest.raises(storage._ClaimDeferred) as caught:store.claim()
        assert caught.value.__context__.sqlite_errorcode==sqlite3.SQLITE_LOCKED_SHAREDCACHE
    finally:close_lock(blocker)
    assert store.claim()['id']==cid
    assert store.claim() is None


class TrackedStop(threading.Event):
    def __init__(self,permit,optional):
        super().__init__();self.permit=permit;self.optional=optional
        self.waits=[];self.second=threading.Event()
    def wait(self,timeout=None):
        self.waits.append((timeout,time.monotonic()))
        assert self.optional==[]
        if self.permit is not None:
            assert not self.permit._probe and not self.permit._primary
        if len(self.waits)>=2:self.second.set()
        return super().wait(timeout)


@pytest.mark.parametrize('with_permit',[False,True])
@pytest.mark.parametrize('cancel',[False,True])
def test_worker_survives_contention_recovers_once_or_cancels(queued,with_permit,cancel):
    store,settings,cid=queued
    permit=NativeExecutionPermit(settings) if with_permit else None
    if permit is not None:settings._native_execution_permit=permit
    worker=Worker(store,settings)
    optional=[];processed=[];claims=[]
    worker.moss_automatic=SimpleNamespace(step=lambda:optional.append('dispatch'))
    worker.stop_event=TrackedStop(permit,optional)
    worker.wake.set()  # A pending input wake cannot defeat the contention backoff.
    claim=store.claim
    def counted_claim():
        claims.append(time.monotonic())
        return claim()
    store.claim=counted_claim
    def process(chunk):
        processed.append(chunk['id'])
        with store.db() as db:db.execute("UPDATE chunks SET status='completed' WHERE id=?",(cid,))
        worker.stop_event.set()
    worker.process=process
    blocker=lock(store)
    worker.start()
    try:
        assert worker.stop_event.second.wait(2), 'two bounded waits must be observed'
        assert worker.thread.is_alive() and worker.current_chunk is None
        assert len(claims)>=2 and claims[1]-claims[0]>=.075
        assert all(timeout==.1 for timeout,_ in worker.stop_event.waits)
        assert optional==[] and processed==[]
        assert store.chunk(cid)['status']=='queued'
        if cancel:
            began=time.monotonic();assert worker.stop()
            assert time.monotonic()-began<1
            assert processed==[] and store.chunk(cid)['status']=='queued'
        else:
            close_lock(blocker);blocker=None
            worker.thread.join(2)
            assert not worker.thread.is_alive()
            assert processed==[cid] and store.chunk(cid)['status']=='completed'
            assert claim() is None
        assert optional==[] and worker.current_chunk is None
        if permit is not None:assert not permit._probe and not permit._primary
    finally:
        assert worker.stop()
        if blocker is not None:close_lock(blocker)


def test_genuine_nonbusy_begin_error_is_not_deferred(queued,monkeypatch):
    store,settings,cid=queued
    connect=sqlite3.connect
    monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**k:connect('file:'+str(store.path)+'?mode=ro',uri=True,**k))
    with pytest.raises(sqlite3.OperationalError) as caught:store.claim()
    assert caught.value.sqlite_errorcode==sqlite3.SQLITE_READONLY


@contextmanager
def injected_connection(monkeypatch,*,phase,code=sqlite3.SQLITE_BUSY):
    """A real transaction, with one explicitly injected boundary/ack failure."""
    connect=sqlite3.connect;counts={'connect':0,'begin':0,'commit':0};statements=[]
    error=sqlite3.OperationalError('database is locked')
    if code is not None:error.sqlite_errorcode=code
    class Fault(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            statements.append(sql)
            if sql=='BEGIN IMMEDIATE':
                counts['begin']+=1
                if phase=='before_begin':raise error
                result=super().execute(sql,*args,**kwargs)
                if phase=='acquired_begin':raise error
                return result
            if phase=='after_begin' and sql.startswith('SELECT c.id'):raise error
            return super().execute(sql,*args,**kwargs)
        def commit(self):
            counts['commit']+=1
            super().commit()
            if phase=='commit_ack':raise error
    def observed(*args,**kwargs):
        counts['connect']+=1
        return connect(*args,**kwargs,factory=Fault)
    with monkeypatch.context() as patch:
        patch.setattr(storage.sqlite3,'connect',observed)
        yield error,counts,statements


@pytest.mark.parametrize('phase',['acquired_begin','after_begin','commit_ack'])
def test_busy_after_acquisition_and_uncertain_commit_are_never_retried(queued,monkeypatch,phase):
    store,settings,cid=queued
    worker=Worker(store,settings);optional=[];processed=[]
    worker.moss_automatic=SimpleNamespace(step=lambda:optional.append(True))
    worker.process=lambda chunk:processed.append(chunk['id'])
    with injected_connection(monkeypatch,phase=phase) as (error,counts,statements):
        with pytest.raises(sqlite3.OperationalError) as caught:worker.loop()
        assert caught.value is error
        assert counts['connect']==counts['begin']==1
        assert counts['commit']==int(phase=='commit_ack')
    assert optional==[] and processed==[]
    assert store.chunk(cid)['status']==('running' if phase=='commit_ack' else 'queued')
    # Lost commit acknowledgement must not result in a second claim of the row.
    if phase=='commit_ack':assert store.claim() is None


@pytest.mark.parametrize('code',[None,sqlite3.SQLITE_IOERR,True,'5'])
def test_message_or_unclassified_code_never_authorizes_deferral(queued,monkeypatch,code):
    store,settings,cid=queued
    with injected_connection(monkeypatch,phase='before_begin',code=code) as (error,counts,statements):
        with pytest.raises(sqlite3.OperationalError) as caught:store.claim()
        assert caught.value is error and counts['begin']==1
    assert store.chunk(cid)['status']=='queued'


def test_ordinary_mode_behavior_is_unchanged(queued,monkeypatch):
    store,settings,cid=queued;settings.moss_automatic_enabled=False
    with injected_connection(monkeypatch,phase='before_begin') as (error,counts,statements):
        with pytest.raises(sqlite3.OperationalError) as caught:store.claim()
        assert caught.value is error
        assert 'PRAGMA busy_timeout=0' not in statements
    assert store.chunk(cid)['status']=='queued'


def test_real_empty_claim_still_allows_optional_step(queued):
    store,settings,cid=queued
    with store.db() as db:db.execute("UPDATE chunks SET status='completed' WHERE id=?",(cid,))
    worker=Worker(store,settings);optional=[]
    def step():optional.append(True);worker.stop_event.set();worker.wake.set()
    worker.moss_automatic=SimpleNamespace(step=step)
    worker.loop()
    assert optional==[True]
