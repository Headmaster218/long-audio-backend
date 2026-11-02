"""Same-owner one-shot delivery after real claim COMMIT; generated data only."""
import sqlite3
import hashlib
import threading
from contextlib import contextmanager

import pytest
from audio_backend import storage, native_owned_cell_store as cells
from audio_backend.native_owned_cells import Unavailable
from audio_backend.native_execution_permit import NativeExecutionPermit
from audio_backend.worker import Worker
from test_claim_postcommit_notice_nov02 import queued,exhaust_after_positive_commit
from test_strict_archive_v2 import positive_close


@contextmanager
def owner(store):
    permit=NativeExecutionPermit(store.settings)
    store.settings._native_execution_permit=permit
    worker=Worker(store,store.settings)
    worker.thread=threading.current_thread()  # Exact current synthetic caller owner.
    with permit.claim_probe():yield worker,permit


def exhaust_after_clock_boundary(store,monkeypatch,boundary):
    connect=storage.sqlite3.connect;original_time=cells.time;offset=[0.]
    class Clock:
        def __getattr__(self,name):return getattr(original_time,name)
        def monotonic(self):return original_time.monotonic()+offset[0]
    monkeypatch.setattr(cells,'time',Clock())
    trace={'commit_count':0,'close_count':0}
    class Connection(sqlite3.Connection):
        def commit(self):
            slot=next((i for i,c in enumerate(store._strict_connections) if c is self),None)
            budget=store._strict_connection_budgets[slot] if slot is not None else None
            claiming=budget is not None and getattr(budget,'claim_running_write',False) and not trace['commit_count']
            super().commit()
            if claiming:
                assert not self.in_transaction
                trace.update(budget=budget,connection=self,commit_count=1)
                if boundary=='commit_clock':offset[0]=.533518
        def close(self):
            super().close()
            if trace.get('connection') is self:
                trace['close_count']+=1
                if boundary=='phase_exit':offset[0]=.533518
    monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**kw:connect(*a,**kw,factory=Connection))
    return trace


def issue(store,value,worker,monkeypatch,boundary='elapsed'):
    receipt=(exhaust_after_positive_commit(store,value.cid,monkeypatch) if boundary=='elapsed'
        else exhaust_after_clock_boundary(store,monkeypatch,boundary))
    with pytest.raises(storage.CommittedClaimLate) as caught:store.claim_for_worker(worker)
    error=caught.value
    assert type(error.original) is Unavailable and str(error.original)=='bounded_planning_capacity'
    assert error.__cause__ is error.original
    assert receipt['budget'].sql_outcome=='committed'
    assert receipt['commit_count']==receipt['close_count']==1
    return error,receipt


@pytest.mark.parametrize('boundary',['elapsed','commit_clock','phase_exit'])
def test_one_authenticated_receipt_consumes_original_row_without_any_sql(tmp_path,monkeypatch,boundary):
    store,value=queued(tmp_path)
    try:
        with owner(store) as (worker,permit):
            error,trace=issue(store,value,worker,monkeypatch,boundary)
            with store.db() as db:
                nonce=db.execute('SELECT nonce FROM strict_recording_control WHERE cid=?',(value.cid,)).fetchone()[0]
            with monkeypatch.context() as patch:
                patch.setattr(storage.sqlite3,'connect',lambda *a,**k:pytest.fail('receipt consumption issued SQL'))
                row=store.consume_committed_claim(error.receipt,worker)
                assert row['id']==value.cid and row['digest']==hashlib.sha256(value.encoded).hexdigest()
                assert row['next_offset']==0 and row['_strict_attempt_nonce']==nonce
                with pytest.raises(RuntimeError,match='committed_claim_receipt'):store.consume_committed_claim(error.receipt,worker)
            assert permit._probe and trace['budget'].elapsed>=.533518
            assert store._claim_late_pending is None and store._claim_late_proof is None
    finally:positive_close(store)


def test_unconsumed_receipt_blocks_another_claim_and_rejects_forged_identity(tmp_path,monkeypatch):
    store,value=queued(tmp_path)
    try:
        with owner(store) as (worker,_):
            error,_=issue(store,value,worker,monkeypatch)
            with pytest.raises(RuntimeError,match='committed_claim_pending'):store.claim()
            forged=type(error.receipt)()
            with pytest.raises(RuntimeError,match='committed_claim_receipt'):store.consume_committed_claim(forged,worker)
            assert store.consume_committed_claim(error.receipt,worker)['id']==value.cid
    finally:positive_close(store)


def test_wrong_worker_and_thread_cannot_consume(tmp_path,monkeypatch):
    store,value=queued(tmp_path)
    try:
        with owner(store) as (worker,_):
            error,_=issue(store,value,worker,monkeypatch)
            other=Worker(store,store.settings);other.thread=threading.current_thread()
            with pytest.raises(RuntimeError,match='committed_claim_receipt'):store.consume_committed_claim(error.receipt,other)
            results=[]
            def cross_thread():
                try:store.consume_committed_claim(error.receipt,worker)
                except RuntimeError as exc:results.append(str(exc))
            thread=threading.Thread(target=cross_thread);thread.start();thread.join(1)
            assert not thread.is_alive() and results==['committed_claim_owner']
            assert store.consume_committed_claim(error.receipt,worker)['id']==value.cid
    finally:positive_close(store)


def test_original_probe_and_permit_must_still_own_delivery(tmp_path,monkeypatch):
    store,value=queued(tmp_path)
    try:
        with owner(store) as (worker,permit):error,_=issue(store,value,worker,monkeypatch)
        with pytest.raises(RuntimeError,match='committed_claim_probe'):store.consume_committed_claim(error.receipt,worker)
        replacement=NativeExecutionPermit(store.settings);store.settings._native_execution_permit=replacement
        with replacement.claim_probe():
            with pytest.raises(RuntimeError,match='committed_claim_receipt'):store.consume_committed_claim(error.receipt,worker)
        store.settings._native_execution_permit=permit
        with permit.claim_probe():
            with pytest.raises(RuntimeError,match='committed_claim_receipt'):store.consume_committed_claim(error.receipt,worker)
        assert store._claim_late_pending is not None  # original epoch is gone; manual review, no adoption.
    finally:positive_close(store)


@pytest.mark.parametrize('fault',['body','commit_ack','close_ack','work_cap'])
def test_only_positive_closed_time_late_claim_can_issue_receipt(tmp_path,monkeypatch,fault):
    store,value=queued(tmp_path);connect=storage.sqlite3.connect;seen={'done':False}
    class Connection(sqlite3.Connection):
        def execute(self,sql,*a,**kw):
            if fault=='body' and sql.startswith("UPDATE chunks SET status='running'"):
                raise Unavailable('bounded_planning_capacity')
            return super().execute(sql,*a,**kw)
        def commit(self):
            slot=next((i for i,c in enumerate(store._strict_connections) if c is self),None)
            budget=store._strict_connection_budgets[slot] if slot is not None else None
            claiming=budget is not None and getattr(budget,'claim_running_write',False) and not seen['done']
            super().commit()
            if claiming:
                seen.update(done=True,connection=self,budget=budget)
                if fault=='commit_ack':raise Unavailable('bounded_planning_capacity')
                if fault in ('close_ack','work_cap'):budget.elapsed+=.533518
                if fault=='work_cap':budget.steps=150001
        def close(self):
            super().close()
            if fault=='close_ack' and seen.get('connection') is self:raise OSError('synthetic close acknowledgement unknown')
    monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**kw:connect(*a,**kw,factory=Connection))
    with owner(store) as (worker,_):
        with pytest.raises(Unavailable,match='bounded_planning_capacity'):store.claim_for_worker(worker)
    assert store._claim_late_pending is None and store._claim_late_proof is None
    if fault=='close_ack':
        # Positive underlying close with a lost acknowledgement is still unknown;
        # never reclose/reset the original retained owner to make this test pass.
        assert store.strict_cleanup_uncertain()
    else:positive_close(store)


def test_fabricated_budget_flags_without_store_commit_proof_are_not_authority(tmp_path):
    store,value=queued(tmp_path)
    budget=cells.Budget();budget.elapsed=1.;budget._claim_commit_returned=budget._claim_close_returned=True
    budget.sql_outcome='committed';budget.claim_running_write=True;budget._refusal_diagnosed=True
    try:
        with owner(store) as (worker,_):
            assert store._late_claim_receipt(worker,budget,Unavailable('bounded_planning_capacity')) is None
            assert store._claim_late_pending is None
    finally:positive_close(store)


def test_generic_and_wrong_thread_owner_keep_original_fail_closed_exception(tmp_path,monkeypatch):
    store,value=queued(tmp_path);exhaust_after_positive_commit(store,value.cid,monkeypatch)
    worker=Worker(store,store.settings)  # Not the current execution thread.
    permit=NativeExecutionPermit(store.settings);store.settings._native_execution_permit=permit
    try:
        with permit.claim_probe():
            with pytest.raises(Unavailable,match='bounded_planning_capacity'):store.claim_for_worker(worker)
        assert store._claim_late_pending is None
    finally:positive_close(store)


@pytest.mark.parametrize('kind',['empty','probe'])
def test_nonrunning_postcommit_lateness_is_not_relabelled_as_rollback_or_defer(tmp_path,monkeypatch,kind):
    from test_strict_payload_v2 import component_store
    store,value=queued(tmp_path) if kind=='probe' else (component_store(tmp_path),None)
    connect=storage.sqlite3.connect;seen={}
    class Connection(sqlite3.Connection):
        def commit(self):
            slot=next((i for i,c in enumerate(store._strict_connections) if c is self),None)
            budget=store._strict_connection_budgets[slot] if slot is not None else None
            super().commit()
            if budget is not None and not seen:
                assert not self.in_transaction and budget.claim_running_write is False
                seen['budget']=budget;budget.elapsed+=.533518
    monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**kw:connect(*a,**kw,factory=Connection))
    try:
        with owner(store) as (worker,_):
            with pytest.raises(Unavailable,match='bounded_planning_capacity') as raised:
                store.claim_for_worker(worker,probe=kind=='probe')
        assert not isinstance(raised.value,storage._ClaimDeferred)
        assert seen['budget'].sql_outcome=='committed' and store._claim_late_pending is None
        if value is not None:
            with store.db() as db:assert db.execute('SELECT status FROM chunks WHERE id=?',(value.cid,)).fetchone()[0]=='queued'
    finally:positive_close(store)


def test_other_store_cannot_adopt_receipt(tmp_path,monkeypatch):
    first=tmp_path/'first';other=tmp_path/'other';first.mkdir();other.mkdir()
    store,value=queued(first);second,_=queued(other)
    try:
        with owner(store) as (worker,_):
            error,_=issue(store,value,worker,monkeypatch)
            with owner(second) as (other_worker,_):
                with pytest.raises(RuntimeError,match='committed_claim_receipt'):
                    second.consume_committed_claim(error.receipt,other_worker)
            assert store.consume_committed_claim(error.receipt,worker)['id']==value.cid
    finally:positive_close(store);positive_close(second)


def test_reentrant_claim_cannot_overwrite_real_inflight_commit_proof(tmp_path,monkeypatch):
    store,value=queued(tmp_path);connect=storage.sqlite3.connect;seen={'reentry':0}
    class Connection(sqlite3.Connection):
        def commit(self):
            slot=next((i for i,c in enumerate(store._strict_connections) if c is self),None)
            budget=store._strict_connection_budgets[slot] if slot is not None else None
            claiming=budget is not None and getattr(budget,'claim_running_write',False) and not seen['reentry']
            super().commit()
            if claiming:
                proof=store._claim_late_proof
                with pytest.raises(RuntimeError,match='committed_claim_inflight'):store.claim_for_worker(worker)
                seen['reentry']+=1
                assert store._claim_late_proof is proof
                budget.elapsed+=.533518
    monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**kw:connect(*a,**kw,factory=Connection))
    try:
        with owner(store) as (worker,_):
            with pytest.raises(storage.CommittedClaimLate) as caught:store.claim_for_worker(worker)
            assert store.consume_committed_claim(caught.value.receipt,worker)['id']==value.cid
        assert seen['reentry']==1 and store._claim_late_proof is None
    finally:positive_close(store)
