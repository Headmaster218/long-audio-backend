"""Real fresh result SQLite and Worker/permit, with controlled fake native driver.

No model/provider process or saved database is opened. These cases qualify
storage-only scheduling and exact acknowledgement, not acoustic inference.
"""
from contextlib import closing
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_automatic as auto
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.worker import Worker
from test_moss_automatic_integration import automatic_cell_jobs, manager_intent, result_rows, candidate
from test_moss_result_store import observed_connections


def worker(f):
    value=Worker.__new__(Worker)
    value.settings=f.store.settings;value.stop_event=threading.Event()
    value.moss_automatic=f.automatic
    value.settings._native_execution_permit=f.permit
    return value


def pending(f,kind,*,lost_ack=False,automatic=True):
    cell,body=f.ready_cell(frames=16)
    if kind=='outcome':
        factory=f.owner._driver_factory
        def failed(*a):
            d=factory(*a);d.wait_error=RuntimeError('synthetic native failure');return d
        f.owner._driver_factory=failed
    if automatic:f.owner.submit_automatic(manager_intent(f,cell))
    else:f.submit_cell(cell,body)
    record=f.owner._live
    method='no_result' if kind=='outcome' else 'commit'
    context=(observed_connections(lose_commit_ack=True,commit_match="SET state='no_result'" if kind=='outcome' else "SET state='committed'")
             if lost_ack else patch.object(f.owner.results,method,side_effect=ReadError(503,'result_storage_unavailable')))
    with context:f.finish()
    assert record['cleaned'] and record['process_cleanup_verified'] and record['lease'].released
    assert record['storage_pending'] and not record['thread'].is_alive()
    if automatic or kind=='outcome':assert f.permit._optional is record
    assert not f.owner.automatic_ready_hint()
    frozen=record['terminal_metadata'] if kind=='outcome' else record['history_snapshot']
    assert frozen is not None
    return record,frozen,worker(f)


def assert_exact(f,record,frozen,kind):
    row=result_rows(f.store)[0]
    assert row['state']==('no_result' if kind=='outcome' else 'committed')
    assert row['metadata_json']==(frozen if kind=='outcome' else frozen.metadata_json)
    if kind=='result':assert row['result_json']==frozen.result_json
    else:assert row['result_json'] is None
    assert not record['storage_pending'] and f.permit._optional is None
    assert sum(d.starts for d in f.drivers)==1 and sum(d.waits for d in f.drivers)==1


@pytest.mark.parametrize('kind',['result','outcome'])
@pytest.mark.parametrize('lost_ack',[False,True])
@pytest.mark.parametrize('mode',['queued_primary','empty_eos'])
def test_exact_ack_reachable_before_turn_with_or_without_candidates(automatic_cell_jobs,kind,lost_ack,mode):
    f=automatic_cell_jobs;record,frozen,w=pending(f,kind,lost_ack=lost_ack)
    before=result_rows(f.store)[0]
    assert before['state']==('committed' if kind=='result' else 'no_result') if lost_ack else before['state']=='accepted'
    assert not f.automatic._snapshot() and not f.automatic.primary_yield_due()
    current=f.permit.primary_intent('worker') if mode=='queued_primary' else None
    if current is not None:assert not current.granted
    with patch.object(f.automatic,'_snapshot',side_effect=AssertionError('no model queue read during storage turn')):
        if current is None:w._automatic_handoff(request=True)
        else:
            # Actual PrimaryIntent.wait invokes the Worker pump outside its lock.
            current.wait(lambda:False,handoff=w._automatic_handoff)
    assert_exact(f,record,frozen,kind)
    if lost_ack:assert result_rows(f.store)[0]==before
    assert not f.permit.status()['automatic_turn_pending']
    if current is not None:assert current.granted;current.release()


@pytest.mark.parametrize('kind',['result','outcome'])
def test_genuine_sqlite_busy_preserves_owner_and_payload_then_continues_once(automatic_cell_jobs,kind):
    f=automatic_cell_jobs;record,frozen,w=pending(f,kind)
    queued=f.permit.primary_intent('worker');peer=f.permit.primary_intent('probe')
    with closing(sqlite3.connect(f.store.path,timeout=0)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        try:
            statements=[]
            with observed_connections(statements=statements):w._automatic_handoff()
            assert record['storage_pending'] and f.permit._optional is record
            assert not queued.granted and not peer.granted
            assert record['terminal_metadata'] is frozen if kind=='outcome' else record['history_snapshot'] is frozen
            assert result_rows(f.store)[0]['state']=='accepted'
            assert any(sql=='BEGIN IMMEDIATE' for sql in statements)
            # Repeated pumps inside the existing floor do no further SQL work.
            with patch.object(f.owner,'maintain_automatic_text_storage',side_effect=AssertionError('cooldown bypass')):
                for _ in range(20):w._automatic_handoff(request=True)
        finally:blocker.rollback()
    f.automatic.next_attempt=0.;w._automatic_handoff()
    assert_exact(f,record,frozen,kind)
    queued.wait();peer.wait();queued.release();peer.release()


@pytest.mark.parametrize('kind',['result','outcome'])
def test_persistent_storage_failure_keeps_exact_owner_and_one_second_floor(automatic_cell_jobs,kind):
    f=automatic_cell_jobs;record,frozen,w=pending(f,kind);calls=[]
    original=f.owner._persist_locked
    def attempt(r):calls.append(r);return original(r)
    method='no_result' if kind=='outcome' else 'commit'
    with patch.object(f.owner.results,method,side_effect=ReadError(503,'result_storage_unavailable')),patch.object(f.owner,'_persist_locked',side_effect=attempt):
        for instant in (100.,100.5,101.,101.5,102.):
            with patch.object(auto.time,'monotonic',return_value=instant):w._automatic_handoff(request=True)
            assert record['storage_pending'] and f.permit._optional is record
            assert record['terminal_metadata'] is frozen if kind=='outcome' else record['history_snapshot'] is frozen
        assert calls==[record]*3 and f.automatic.next_attempt==103.
    f.automatic.next_attempt=0.;w._automatic_handoff();assert_exact(f,record,frozen,kind)


@pytest.mark.parametrize('condition',['live_thread','cleanup','process','lease','probe','probe_storage',
                                     'payload','owner','owner_kind','pending_acquisition','purge','scheduler'])
def test_uncertain_or_mismatched_owner_cannot_enter_storage_maintenance(automatic_cell_jobs,condition):
    f=automatic_cell_jobs;record,frozen,w=pending(f,'outcome')
    with patch.dict(record),patch.object(f.owner,'_pending_record',f.owner._pending_record),patch.object(f.permit,'_optional',f.permit._optional),patch.object(f.permit,'_optional_kind',f.permit._optional_kind):
        if condition=='live_thread':record['thread']=SimpleNamespace(is_alive=lambda:True)
        elif condition=='cleanup':record['cleaned']=False
        elif condition=='process':record['process_cleanup_verified']=False
        elif condition=='lease':record['lease']=SimpleNamespace(released=False)
        elif condition=='probe':record['speaker_probe']={'operation':SimpleNamespace(cleaned=False)}
        elif condition=='probe_storage':record['speaker_probe']={'commit_pending':True}
        elif condition=='payload':record['terminal_metadata']=None
        elif condition=='owner':f.permit._optional=object()
        elif condition=='owner_kind':f.permit._optional_kind='preemptible'
        elif condition=='pending_acquisition':f.owner._pending_record=object()
        elif condition=='purge':record['purge_pending']=True
        scheduler=object() if condition=='scheduler' else f.automatic
        before=f.automatic.next_attempt
        with patch.object(f.owner,'_persist_locked',side_effect=AssertionError('ineligible write')):
            assert not f.owner.maintain_automatic_text_storage(scheduler,stop=lambda:False)
        assert f.automatic.next_attempt==before
    w._automatic_handoff();assert_exact(f,record,frozen,'outcome')


@pytest.mark.parametrize('kind',['result','outcome'])
def test_lost_ack_then_cancel_releases_only_after_exact_readback(automatic_cell_jobs,kind):
    f=automatic_cell_jobs;record,frozen,w=pending(f,kind,lost_ack=True)
    f.owner.cancel('session',record['job_id'],f.owner.jobs_epoch)
    assert f.permit._optional is record and record['storage_pending']
    before=result_rows(f.store)[0]
    w._automatic_handoff();assert_exact(f,record,frozen,kind)
    assert result_rows(f.store)[0]==before


def test_cancellation_alone_cannot_discard_unknown_result_commit(automatic_cell_jobs):
    f=automatic_cell_jobs;record,frozen,w=pending(f,'result')
    f.owner.cancel('session',record['job_id'],f.owner.jobs_epoch)
    w._automatic_handoff()
    assert record['storage_pending'] and f.permit._optional is record
    assert record['history_snapshot'] is frozen and result_rows(f.store)[0]['state']=='accepted'
    f.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");f.db.commit()
    f.owner.invalidate_session('session');assert f.permit._optional is None


@pytest.mark.parametrize('kind',['result','outcome'])
def test_explicit_deleted_session_uses_existing_veto_and_never_replays(automatic_cell_jobs,kind):
    f=automatic_cell_jobs;record,frozen,w=pending(f,kind)
    f.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");f.db.commit()
    w._automatic_handoff()
    assert not record['storage_pending'] and f.permit._optional is None
    assert sum(d.starts for d in f.drivers)==1


@pytest.mark.parametrize('kind',['result','outcome'])
def test_explicit_source_veto_preserves_existing_archive_semantics(automatic_cell_jobs,kind):
    f=automatic_cell_jobs;record,frozen,w=pending(f,kind)
    if kind=='result':f.cell_owner._publication_revoke_pending=True
    else:
        with closing(sqlite3.connect(f.store.path)) as db:
            db.row_factory=sqlite3.Row
            with db:auto.cancel_session(db,'session')
    w._automatic_handoff()
    assert not record['storage_pending'] and f.permit._optional is None
    assert result_rows(f.store)[0]['state']=='accepted' and sum(d.starts for d in f.drivers)==1


def test_stop_preserves_pending_owner_and_does_not_consume_throttle(automatic_cell_jobs):
    f=automatic_cell_jobs;record,frozen,w=pending(f,'outcome');w.stop_event.set()
    before=f.automatic.next_attempt;w._automatic_handoff(request=True)
    assert f.automatic.next_attempt==before and f.permit._optional is record
    assert record['terminal_metadata'] is frozen
    w.stop_event.clear();w._automatic_handoff();assert_exact(f,record,frozen,'outcome')


def test_empty_hint_never_postpones_normal_step(automatic_cell_jobs):
    f=automatic_cell_jobs;w=worker(f);before=f.automatic.next_attempt
    assert not f.automatic.maintain_text_storage() and f.automatic.next_attempt==before
    cell,_=f.ready_cell(frames=16);intent=manager_intent(f,cell)
    w._automatic_handoff(request=True)
    assert f.owner._live['automatic_key']==intent['candidate_key']
    f.finish();assert sum(d.starts for d in f.drivers)==1


def test_manual_outcome_policy_remains_explicit_storage_retry(automatic_cell_jobs):
    f=automatic_cell_jobs;record,frozen,w=pending(f,'outcome',automatic=False)
    before=f.automatic.next_attempt;w._automatic_handoff()
    assert record['storage_pending'] and f.permit._optional is record and f.automatic.next_attempt==before
    f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
    assert_exact(f,record,frozen,'outcome')


@pytest.mark.parametrize('kind',['result','outcome'])
def test_next_job_admission_follows_ack_and_existing_floor(automatic_cell_jobs,kind):
    f=automatic_cell_jobs;record,frozen,w=pending(f,kind)
    cell,_=f.ready_cell(index=1,frames=16);intent=manager_intent(f,cell,2)
    method='no_result' if kind=='outcome' else 'commit'
    with patch.object(f.owner.results,method,side_effect=ReadError(503,'result_storage_unavailable')):
        w._automatic_handoff(request=True)
    assert f.owner._live is record and candidate(f.store,intent['candidate_key'])['state']=='published'
    assert sum(d.starts for d in f.drivers)==1
    f.automatic.next_attempt=0.;w._automatic_handoff(request=True)
    assert_exact(f,record,frozen,kind)
    assert f.owner._live is record # Successful storage does not bypass the floor.
    f.automatic.next_attempt=0.;w._automatic_handoff(request=True)
    assert f.owner._live is not record and f.owner._live['automatic_key']==intent['candidate_key']
    assert sum(d.starts for d in f.drivers)==1
    f.finish();assert sum(d.starts for d in f.drivers)==2
