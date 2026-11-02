"""Generated real SQLite contention at one irreversible publication boundary.

No models/Worker thread. Existing component frontier seam only; original Store,
archive link, receipt SQL, SQLite native busy wait and cleanup remain real.
"""
import hashlib
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from audio_backend import storage, strict_payload_v2 as payload, speaker_support as support
from audio_backend.worker import Worker
from test_strict_payload_v2 import PlacementConnection, component_store, completed_component, component_frontier
from test_strict_archive_v2 import positive_close


def worker_for(value,monkeypatch):
    worker=object.__new__(Worker);worker.store=value.store;worker.settings=value.store.settings
    worker._payload_maintenance_error=None;worker._strict_cleanup_failed=False
    worker._strict_first_error=None;worker._payload_cleanup_observer_error=None
    worker._payload_frontier_owner=None
    def frontier(actual,chunk):
        assert actual is worker and chunk['id']==value.cid
        witness,_=component_frontier(value,monkeypatch)
        witness.worker=worker;witness.capture=worker.native_owned_cells
        witness._closed=False;witness._guarding=False;witness._held_locks=[]
        worker._payload_frontier_owner=witness;worker.native_owned_cells._payload_frontier=witness
        return witness
    worker.native_owned_cells=SimpleNamespace(payload_frontier=frontier,_payload_frontier=None)
    return worker


class Contention:
    def __init__(self,value,monkeypatch,release_after):
        self.value=value;self.release_after=release_after;self.thread=None
        self.ready=threading.Event();self.begin=threading.Event();self.release=threading.Event()
        self.errors=[];self.closed=False;self.fired=False;self.links=0
        self.receipt_begins=self.receipt_bodies=self.receipt_commits=0
        self.original_connect=storage.sqlite3.connect;control=self
        class OS:
            def __getattr__(self,name):return getattr(original_os,name)
            def link(self,*args,**kwargs):
                result=original_os.link(*args,**kwargs);control.links+=1;return result
        original_os=payload.os;monkeypatch.setattr(payload,'os',OS())
        class Connection(PlacementConnection):
            def execute(self,sql,args=()):
                if sql=='BEGIN IMMEDIATE' and not control.fired:
                    units=list((value.store.settings.data_dir/'strict-payload-v2').rglob('*.unit'))
                    if units:
                        assert len(units)==1 and not self._db.in_transaction
                        assert not list((value.store.settings.data_dir/'strict-payload-v2').rglob('*.stage'))
                        row=self._db.execute('SELECT state,file_sha256,signature FROM strict_payload_unit').fetchone()
                        assert tuple(row)==('reserved',None,None)
                        control.fired=True;control.receipt_connection=self
                        control.budget=value.store._strict_archive_owner.budget
                        control.deadline=control.budget.deadline
                        control.charges={k:getattr(control.budget,k) for k in ('read_bytes','metadata_bytes','payload_bytes','sql_steps')}
                        control.timeout_ms=self._db.execute('PRAGMA busy_timeout').fetchone()[0]
                        assert 0<=control.timeout_ms<=250
                        assert control.timeout_ms/1000 <= control.budget.remaining_seconds()+.002
                        control.unit_path=units[0];control.unit_hash=hashlib.sha256(units[0].read_bytes()).hexdigest()
                        control.thread=threading.Thread(target=control.compete,name='generated-receipt-lock')
                        control.thread.start();assert control.ready.wait(1.) and not control.errors
                        control.receipt_begins+=1;control.begin_at=time.monotonic();control.begin.set()
                        try:return super().execute(sql,args)
                        except sqlite3.OperationalError as error:
                            control.original_error=error
                            assert error.sqlite_errorcode&0xff==sqlite3.SQLITE_BUSY
                            assert not self._db.in_transaction
                            raise
                        finally:control.returned_at=time.monotonic()
                if sql.startswith("UPDATE strict_payload_unit SET state='published'"):
                    assert self is control.receipt_connection
                    control.receipt_bodies+=1;self._receipt=True
                return super().execute(sql,args)
            def commit(self):
                result=super().commit()
                if getattr(self,'_receipt',False):control.receipt_commits+=1
                return result
        monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**kw:Connection(control.original_connect(*a,**kw),None,None))

    def compete(self):
        db=None
        try:
            db=self.original_connect(str(self.value.store.path),timeout=0,isolation_level=None)
            db.execute('BEGIN IMMEDIATE');assert db.in_transaction
            self.ready.set();assert self.begin.wait(2.)
            if self.release_after is None:
                assert self.release.wait(2.),'test did not release owned writer'
            else:time.sleep(self.release_after)
            db.rollback();assert not db.in_transaction
            self.released_at=time.monotonic()
        except BaseException as error:self.errors.append(error)
        finally:
            self.ready.set()
            if db is not None:
                try:db.close();self.closed=True
                except BaseException as error:self.errors.append(error)

    def cleanup(self):
        self.begin.set();self.release.set()
        if self.thread is not None:
            self.thread.join(3.)
            assert not self.thread.is_alive(),'owned SQLite writer exit unknown'
            assert self.closed and not self.errors,self.errors


def assert_budget_and_file(control):
    assert control.budget.deadline==control.deadline
    assert all(getattr(control.budget,k)>=n for k,n in control.charges.items())
    assert control.budget.read_bytes>0 and control.budget.sql_steps>0
    assert control.links==control.receipt_begins==1
    assert hashlib.sha256(control.unit_path.read_bytes()).hexdigest()==control.unit_hash


def test_real_50ms_writer_releases_inside_single_native_receipt_begin(tmp_path,monkeypatch):
    value=completed_component(component_store(tmp_path));witness,_=component_frontier(value,monkeypatch)
    control=Contention(value,monkeypatch,.05);budget=payload.PayloadBudget();deadline=budget.deadline
    try:
        result=value.store.maintain_payloads(witness,budget)
        control.cleanup()
        assert result['state']=='committed'
        assert control.budget is budget and budget.deadline==deadline
        assert control.begin_at<control.released_at<=control.returned_at
        assert control.returned_at-control.begin_at>=.04
        assert control.receipt_bodies==control.receipt_commits==1
        assert budget.first_error is None and budget.sql_outcome=='committed'
        assert_budget_and_file(control)
        with value.store.db() as db:
            assert db.execute('SELECT state FROM strict_payload_unit').fetchone()[0]=='committed'
            assert db.execute('SELECT count(*) FROM strict_payload_object').fetchone()[0]==2
            assert db.execute('SELECT pcm FROM speaker_support WHERE id=?',(value.support['id'],)).fetchone()[0] is None
        assert support.audio(value.store,value.sid,value.support['id'])==value.audio
    finally:control.cleanup();positive_close(value.store)


def test_real_persistent_writer_is_original_terminal_worker_error(tmp_path,monkeypatch):
    value=completed_component(component_store(tmp_path));worker=worker_for(value,monkeypatch)
    control=Contention(value,monkeypatch,None)
    try:
        with pytest.raises(sqlite3.OperationalError) as raised:
            Worker._maintain_payloads(worker,{'id':value.cid})
        assert raised.value is control.original_error
        assert getattr(raised.value,'_payload_nondeferrable',False) is True
        assert worker._payload_maintenance_error is raised.value
        assert control.receipt_bodies==control.receipt_commits==0
        assert control.budget.sql_outcome=='rolled_back'
        assert not worker._strict_cleanup_failed and not value.store.strict_cleanup_uncertain()
        control.cleanup();assert_budget_and_file(control)
        with value.store.db() as db:
            assert tuple(db.execute('SELECT state,file_sha256,signature FROM strict_payload_unit').fetchone())==('reserved',None,None)
            assert db.execute('SELECT count(*) FROM strict_payload_object').fetchone()[0]==0
            assert db.execute('SELECT pcm FROM speaker_support WHERE id=?',(value.support['id'],)).fetchone()[0]==value.support['pcm']
        with pytest.raises(sqlite3.OperationalError) as repeated:
            Worker._maintain_payloads(worker,{'id':value.cid})
        assert repeated.value is raised.value and control.receipt_begins==1
    finally:control.cleanup();positive_close(value.store)


def test_receipt_native_wait_clipped_to_existing_caller_allowance(tmp_path,monkeypatch):
    value=completed_component(component_store(tmp_path));witness,_=component_frontier(value,monkeypatch)
    class Caller:
        def check(self):pass
        def progress(self):return 0
        def remaining_seconds(self):return .020
    budget=payload.PayloadBudget(caller=Caller());deadline=budget.deadline
    control=Contention(value,monkeypatch,None)
    try:
        with pytest.raises(sqlite3.OperationalError) as raised:value.store.maintain_payloads(witness,budget)
        assert raised.value is control.original_error and getattr(raised.value,'_payload_nondeferrable',False) is True
        assert 0<=control.timeout_ms<=20
        assert budget.deadline==deadline
        assert control.receipt_bodies==control.receipt_commits==0
        control.cleanup();assert_budget_and_file(control)
    finally:control.cleanup();positive_close(value.store)
