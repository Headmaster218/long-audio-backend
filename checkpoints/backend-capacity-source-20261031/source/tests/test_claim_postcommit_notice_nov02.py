"""Generated strict claim: real COMMIT, then injected accounting exhaustion.

This reproduces only the known post-COMMIT boundary. It is not a timing or SQL
planner benchmark, and never opens a prior run's database or starts a model.
"""
import sqlite3
import time
from types import SimpleNamespace

import pytest
from audio_backend import storage
from audio_backend.native_owned_cells import Unavailable
from audio_backend.worker import Worker
from test_strict_payload_v2 import component_store
from test_strict_archive_v2 import seed_source, positive_close


def queued(tmp_path):
    store=component_store(tmp_path)
    value=seed_source(store,tmp_path/'uploads',name='generated-claim')
    assert store._strict_enabled and store._strict_policy=='strict-primary-payload-v2'
    assert store.moss_automatic.primary_only is True
    return store,value


def exhaust_after_positive_commit(store,cid,monkeypatch):
    connect=storage.sqlite3.connect
    receipt={'fired':False,'commit_count':0,'rollback_count':0,'close_count':0}
    class Connection(sqlite3.Connection):
        def commit(self):
            slot=next((i for i,c in enumerate(store._strict_connections) if c is self),None)
            budget=store._strict_connection_budgets[slot] if slot is not None else None
            claiming=budget is not None and getattr(budget,'claim_running_write',False) and not receipt['fired']
            if claiming:
                assert self.in_transaction and budget.sql_outcome=='commit_attempted'
                assert budget._planning_limits==(.5,150000,256*1024)
                receipt['before_steps']=budget.steps
                receipt['before_elapsed']=budget.elapsed+(time.monotonic()-budget._began)
                assert receipt['before_elapsed']<.5
            super().commit()  # Real original transaction durability/ack first.
            if claiming:
                assert not self.in_transaction
                receipt.update(fired=True,budget=budget,connection=self,commit_count=1)
                # Independent read-only connection sees durable running status
                # before the synthetic clock/accounting event is introduced.
                observer=connect(store.path.resolve().as_uri()+'?mode=ro',uri=True)
                try:
                    receipt['observer_status']=observer.execute('SELECT status FROM chunks WHERE id=?',(cid,)).fetchone()[0]
                    assert receipt['observer_status']=='running'
                finally:observer.close()
                receipt['committed_positive']=True
                # Advance this existing budget's elapsed accounting ONLY after
                # positive commit. No cap change, fake SQL, wall sleep or retry.
                budget.elapsed+=.533518
        def rollback(self):
            if receipt.get('connection') is self:
                receipt['rollback_count']+=1
                receipt['rollback_had_transaction']=self.in_transaction
                receipt['outcome_before_rollback']=receipt['budget'].sql_outcome
            return super().rollback()
        def close(self):
            if receipt.get('connection') is self:
                receipt['outcome_at_close']=receipt['budget'].sql_outcome
                result=super().close();receipt['close_count']+=1;return result
            return super().close()
    monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**kw:connect(*a,**kw,factory=Connection))
    return receipt


def verify_committed_but_lost_return(store,value,receipt):
    budget=receipt['budget']
    assert receipt['committed_positive'] and receipt['observer_status']=='running'
    assert receipt['commit_count']==receipt['rollback_count']==receipt['close_count']==1
    assert receipt['rollback_had_transaction'] is False
    assert receipt['outcome_before_rollback']=='committed'
    # Fixed accounting retains known COMMIT despite no-op rollback cleanup.
    # The original defect remains frozen in repro commit 164a180.
    assert receipt['outcome_at_close']==budget.sql_outcome=='committed'
    assert budget.claim_running_write is True
    assert budget.steps>=receipt['before_steps'] and budget.steps<150000
    assert budget.elapsed>=.533518 and budget._began is None
    assert budget._planning_limits is None and budget._refusal_diagnosed
    assert not store.strict_cleanup_uncertain()
    with store.db() as db:
        assert db.execute('SELECT status FROM chunks WHERE id=?',(value.cid,)).fetchone()[0]=='running'
        assert db.execute('SELECT count(*) FROM chunks').fetchone()[0]==1
        assert db.execute('SELECT state FROM strict_recording_control WHERE cid=?',(value.cid,)).fetchone()[0]=='active'
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
    positive_close(store)


def test_strict_claim_positive_commit_then_notice_expiry_loses_return(tmp_path,monkeypatch):
    store,value=queued(tmp_path);receipt=exhaust_after_positive_commit(store,value.cid,monkeypatch)
    returned=[]
    try:
        with pytest.raises(Unavailable,match='^bounded_planning_capacity$') as caught:
            returned.append(store.claim())
        assert not isinstance(caught.value,storage._ClaimDeferred) and returned==[]
        verify_committed_but_lost_return(store,value,receipt)
    finally:positive_close(store)


def test_real_worker_loop_exits_before_process_on_postcommit_notice_expiry(tmp_path,monkeypatch):
    store,value=queued(tmp_path);receipt=exhaust_after_positive_commit(store,value.cid,monkeypatch)
    worker=Worker(store,store.settings);processed=[]
    worker.process=lambda chunk:processed.append(chunk['id'])
    try:
        with pytest.raises(Unavailable,match='^bounded_planning_capacity$'):worker.loop()
        assert processed==[] and worker.current_chunk is None
        assert worker._terminal_error_class=='Unavailable'
        assert not worker.thread.is_alive() and worker.native is None
        verify_committed_but_lost_return(store,value,receipt)
    finally:positive_close(store)


def test_same_generated_strict_claim_without_injection_returns_once(tmp_path):
    store,value=queued(tmp_path)
    try:
        result=store.claim()
        assert result['id']==value.cid
        with store.db() as db:assert db.execute('SELECT status FROM chunks WHERE id=?',(value.cid,)).fetchone()[0]=='running'
        positive_close(store)
    finally:positive_close(store)
