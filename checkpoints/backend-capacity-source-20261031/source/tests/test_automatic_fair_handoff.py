"""Finite scheduling contracts with real SQLite and fake acoustic/owner providers."""
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_automatic as auto
from audio_backend.native_execution_permit import NativeExecutionPermit, AdmissionError
from audio_backend.native_owned_cell_store import Operation
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.worker import Worker
from test_native_execution_permit import settings, sample
from test_moss_result_store import archive_fixture, observed_connections
from test_moss_automatic_integration import (seed_intent, automatic_cell_jobs,
    manager_intent, candidate, result_rows)
from test_worker_claim_busy import queued


def permit():return NativeExecutionPermit(settings(),sampler=lambda _:sample())


def reserve(p):
    record={'cancelled':threading.Event()};callbacks=[]
    p.reserve_optional(record,record['cancelled'],lambda:callbacks.append(True),owner_kind='automatic_text')
    return record,callbacks


def test_exact_automatic_owner_survives_queued_primary_until_publication_release():
    p=permit();record,callbacks=reserve(p)
    worker=p.primary_intent('worker');probe=p.primary_intent('probe')
    assert p.status()['primary_queued']==2
    assert not worker.granted and not probe.granted
    assert not record['cancelled'].is_set() and not callbacks
    p.check_optional(record)
    with p.automatic_publication_guard(record):pass
    with pytest.raises(AdmissionError):p.reserve_optional(object(),threading.Event(),lambda:None)
    p.release_optional(record,cleaned=False)
    assert not worker.granted and p.status()['state']=='OPTIONAL_BLOCKED'
    p.release_optional(object(),cleaned=True)
    assert p._optional is record
    p.release_optional(record,cleaned=True)
    worker.wait();probe.wait()
    assert p.status()['primary_executing']==2
    worker.release();probe.release()


def test_continuous_arrivals_cannot_extend_frozen_cohort_or_skip_primary():
    p=permit();visits=[]
    current=[p.primary_intent(kind).wait() for kind in ('worker','probe')]
    for index in range(24):
        assert p.request_automatic_turn()
        current[0].release()
        new_worker=p.primary_intent('worker')
        assert not new_worker.granted
        with p.automatic_turn() as granted:assert not granted
        current[1].release()
        new_probe=p.primary_intent('probe')
        assert not new_probe.granted
        with p.automatic_turn() as granted:
            assert granted
            record,_=reserve(p);visits.append(index)
            with p.automatic_turn() as again:assert not again
        assert not new_worker.granted and not new_probe.granted
        p.release_optional(record,cleaned=True)
        new_worker.wait();new_probe.wait()
        assert p.status()['primary_executing']==2
        current=[new_worker,new_probe]
    for intent in current:intent.release()
    assert visits==list(range(24)) and not p._primary


@pytest.mark.parametrize('remove',['cancel','stop','empty'])
def test_nonowned_gate_withdrawal_and_noeligible_turn_restore_primary(remove):
    p=permit();first=p.primary_intent('probe').wait();p.request_automatic_turn()
    waiting=p.primary_intent('worker');first.release()
    if remove=='cancel':waiting.release()
    elif remove=='stop':p.abandon_automatic_turn()
    else:
        with p.automatic_turn() as granted:assert granted
    if remove=='cancel':
        with p.automatic_turn() as granted:assert granted
    else:waiting.wait();waiting.release()
    assert not p._primary and p._optional is None and not p.status()['automatic_turn_pending']


def test_worker_wait_pumps_due_turn_outside_condition_then_enters():
    p=permit();active=p.primary_intent('probe').wait();p.request_automatic_turn()
    worker=p.primary_intent('worker');called=[]
    def pump():
        assert not p._condition._is_owned()
        with p.automatic_turn() as granted:
            if granted:called.append(True)
    entered=threading.Event()
    t=threading.Thread(target=lambda:(worker.wait(handoff=pump),entered.set()))
    t.start()
    try:
        assert not entered.wait(.03);active.release()
        assert entered.wait(1);assert called==[True]
    finally:worker.release();active.release();t.join(1)
    assert not t.is_alive()


def test_wait_stop_does_not_release_protected_owner():
    p=permit();record,_=reserve(p);worker=p.primary_intent('worker')
    with pytest.raises(InterruptedError):worker.wait(lambda:True,handoff=lambda:pytest.fail('pump after stop'))
    p.abandon_automatic_turn();assert p._optional is record
    p.release_optional(record,cleaned=True)


def test_entered_primary_fences_every_automatic_boundary():
    p=permit();intent=p.primary_intent('worker').wait()
    with pytest.raises(AdmissionError):reserve(p)
    intent.release();record,_=reserve(p)
    # Deliberately inject an impossible entered primary to test the fail-closed check.
    intent=p.primary_intent('probe');intent.entered=True
    with pytest.raises(AdmissionError):p.check_optional(record)
    with pytest.raises(AdmissionError):
        with p.automatic_publication_guard(record):pass
    p.release_optional(record,cleaned=True);intent.release()


def selector(archive_fixture):
    store,results,sid=archive_fixture
    return store,auto.Automatic(store,results),sid


def test_complete_snapshot_rotates_sessions_exhausts_keys_and_defers_arrivals(archive_fixture):
    store,a,sid=selector(archive_fixture)
    other=store.create_session('other')['id']
    with store.db() as db:
        old=[seed_intent(db,sid,i)['candidate_key'] for i in range(1,5)]
        peer=[seed_intent(db,other,i)['candidate_key'] for i in range(1,3)]
    assert a._snapshot();snapshot=set(old+peer)
    visits=[]
    for index in range(6):
        key=a._select_key();visits.append(key)
        with store.db() as db:seed_intent(db,sid,100+index)
    assert set(visits)==snapshot and len(set(visits))==6
    assert (visits[:4:2]==old[:2] and visits[1:4:2]==peer or
            visits[:4:2]==peer and visits[1:4:2]==old[:2])
    assert not a._round and not a._sessions
    next_visits=[a._select_key() for _ in range(12)]
    assert len(set(next_visits))==12
    assert set(visits)<=set(next_visits)


def test_old_lower_key_cannot_starve_under_continuous_higher_arrivals(archive_fixture):
    store,a,sid=selector(archive_fixture)
    with store.db() as db:old=seed_intent(db,sid,1)['candidate_key']
    for index in range(2,22):
        with store.db() as db:new=seed_intent(db,sid,index)['candidate_key']
        captured=[]
        assert a._snapshot()
        count=sum(map(len,a._round.values()))
        for _ in range(count):captured.append(a._select_key())
        assert old in captured and new in captured and len(set(captured))==count
        with store.db() as db:
            row=db.execute(f'SELECT {auto.COLUMNS} FROM {auto.TABLE} WHERE candidate_key=?',(new,)).fetchone()
            auto._terminal(db,row,'cancelled','synthetic_removed')


def test_snapshot_stale_deletion_and_state_change_consume_only_captured_visit(archive_fixture):
    store,a,sid=selector(archive_fixture)
    with store.db() as db:keys=[seed_intent(db,sid,i)['candidate_key'] for i in range(3)]
    a._snapshot()
    with store.db() as db:
        db.execute(f'DELETE FROM {auto.TABLE} WHERE candidate_key=?',(keys[0],))
        row=db.execute(f'SELECT {auto.COLUMNS} FROM {auto.TABLE} WHERE candidate_key=?',(keys[1],)).fetchone()
        auto._terminal(db,row,'cancelled','synthetic_removed')
    assert [a._select_key() for _ in range(3)]==keys
    assert a.read_candidate(keys[0]) is None
    assert a.read_candidate(keys[1])['state']=='cancelled'
    assert a._select_key()==keys[2]


def test_maximum_snapshot_is_indexed_metadata_only_and_finite(archive_fixture):
    store,a,sid=selector(archive_fixture)
    sessions=[sid]+[store.create_session('other')['id'] for _ in range(3)]
    with store.db() as db:
        for session in sessions:
            for i in range(64):seed_intent(db,session,i)
        plan=db.execute(f'EXPLAIN QUERY PLAN SELECT seq,sid,candidate_key,state FROM {auto.TABLE} INDEXED BY auto_state_seq WHERE state=? ORDER BY seq LIMIT ?',('queued',257)).fetchall()
    assert any('auto_state_seq' in row[3] for row in plan)
    statements=[]
    with observed_connections(statements=statements):assert a._snapshot()
    selections=[sql for sql in statements if sql.startswith('SELECT seq,')]
    assert len(selections)==2 and all('proof' not in sql and 'binding' not in sql and 'LIMIT 257' in sql for sql in selections)
    assert len({a._select_key() for _ in range(256)})==256
    assert not a._round and not a._sessions


@pytest.mark.parametrize('action',['process','pause','cancel','delete','stop'])
def test_real_worker_keeps_queued_row_and_no_transaction_during_optional_wait(queued,action):
    store,config,cid=queued;p=permit();config._native_execution_permit=p
    worker=Worker(store,config);processed=[]
    worker.moss_automatic=SimpleNamespace(jobs=None,primary_yield_due=lambda:False,step=lambda **_:None,maintain_text_storage=lambda **_:False)
    def process(chunk):
        processed.append(chunk['id']);worker.stop_event.set()
    worker.process=process;record,_=reserve(p)
    worker.start()
    try:
        deadline=time.monotonic()+2
        while p.status()['primary_queued']!=1 and time.monotonic()<deadline:time.sleep(.005)
        assert p.status()['primary_queued']==1
        assert worker.current_chunk is None and store.chunk(cid)['status']=='queued'
        db=sqlite3.connect(store.path,timeout=0)
        db.execute('BEGIN IMMEDIATE');db.rollback();db.close()
        if action in ('pause','cancel'):store.set_state(cid,action)
        if action=='delete':
            with store.db() as db:db.execute('DELETE FROM chunks WHERE id=?',(cid,))
        if action=='stop':assert worker.stop()
        p.release_optional(record,cleaned=True)
        if action=='process':worker.thread.join(2);assert processed==[cid]
        else:
            time.sleep(.03);assert processed==[]
    finally:
        p.release_optional(record,cleaned=True);assert worker.stop()
    assert not worker.thread.is_alive() and not p._primary and not p._probe


@pytest.mark.parametrize('cancel',[False,True])
def test_lost_real_commit_ack_keeps_snapshot_and_owner_until_exact_readback(automatic_cell_jobs,cancel):
    f=automatic_cell_jobs;cell,_=f.ready_cell(frames=16)
    intent=manager_intent(f,cell);f.owner.submit_automatic(intent)
    def cancellation():
        if cancel:f.owner._live['cancelled'].set()
    with observed_connections(lose_commit_ack=True,commit_match="SET state='committed'",on_commit=cancellation):
        record=f.finish()
    snapshot=record['history_snapshot']
    assert snapshot is not None and record['storage_pending']
    assert f.permit._optional is record and f.permit.status()['state']=='OPTIONAL_BLOCKED'
    assert candidate(f.store,intent['candidate_key'])['state']=='committed'
    waiting=f.permit.primary_intent('worker');assert not waiting.granted
    try:
        code,reply=f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
        assert code==200 and reply['durable_result_available']
        assert f.owner.results.result('session',record['job_id'])==snapshot.result_json
        assert f.permit._optional is None and waiting.granted
        assert sum(d.starts for d in f.drivers)==1
    finally:waiting.release()


def test_generic_storage_read_failure_plus_cancel_keeps_exact_snapshot(automatic_cell_jobs):
    f=automatic_cell_jobs;cell,_=f.ready_cell(frames=16)
    f.owner.submit_automatic(manager_intent(f,cell))
    with patch.object(f.owner.results,'commit',side_effect=ReadError(503,'result_storage_unavailable')):
        record=f.finish()
    snapshot=record['history_snapshot'];record['cancelled'].set()
    with patch.object(f.owner.results,'match_committed',side_effect=ReadError(503,'result_storage_unavailable')):
        f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
    assert record['history_snapshot'] is snapshot and record['storage_pending']
    assert f.permit._optional is record
    # Explicit source veto is distinct from cancellation plus an unknown read.
    with patch.object(f.owner.results,'match_committed',return_value=False), \
         patch.object(f.permit,'automatic_publication_guard',side_effect=ReadError(409,'automatic_publication_veto')):
        f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
    assert not record['storage_pending'] and f.permit._optional is None


def test_explicit_deleted_session_releases_cleaned_pending_owner(automatic_cell_jobs):
    f=automatic_cell_jobs;cell,_=f.ready_cell(frames=16)
    f.owner.submit_automatic(manager_intent(f,cell))
    with patch.object(f.owner.results,'commit',side_effect=ReadError(503,'result_storage_unavailable')):record=f.finish()
    with patch.object(f.owner.results,'is_deleted',return_value=True),pytest.raises(ReadError,match='session_not_found'):
        f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
    assert f.permit._optional is None and not record['storage_pending']


@pytest.mark.parametrize('complete',[False,True])
def test_same_key_flight_uses_original_deadline_and_never_resubmits(archive_fixture,complete):
    store,a,sid=selector(archive_fixture)
    with store.db() as db:row=seed_intent(db,sid,1,state='queued')
    op=Operation('publish_auto',None,object());op.deadline=time.monotonic()-.001
    admitted=[]
    a.jobs=SimpleNamespace(submit_automatic=lambda selected:admitted.append(selected['candidate_key']))
    a.flight=(row['candidate_key'],op,row['cell_id'])
    if complete:
        op.result={'status':'durable','cells':[{'cell_id':row['cell_id'],'status':'retained'}]};op.slot_released.set()
    began=time.monotonic();a._finish_flight(lambda:False)
    assert time.monotonic()-began<.1
    if complete:assert admitted==[row['candidate_key']] and a.flight is None
    else:assert not admitted and a.flight[1] is op


def test_flight_acknowledgement_and_admission_stay_same_key_without_budget_refresh(archive_fixture):
    store,a,sid=selector(archive_fixture)
    with store.db() as db:row=seed_intent(db,sid,1,state='queued')
    op=Operation('publish_auto',None,object());deadline=op.deadline
    admitted=[];a.jobs=SimpleNamespace(submit_automatic=lambda selected:admitted.append(selected['candidate_key']))
    a.flight=(row['candidate_key'],op,row['cell_id'])
    def acknowledge():
        time.sleep(.02);op.result={'status':'durable','cells':[{'cell_id':row['cell_id'],'status':'retained'}]};op.slot_released.set()
    t=threading.Thread(target=acknowledge);t.start()
    try:a._finish_flight(lambda:False)
    finally:t.join(1)
    assert op.deadline==deadline and admitted==[row['candidate_key']] and a.flight is None

from test_same_epoch_core_seams import factory


@pytest.mark.parametrize('seconds',[63.,315.])
def test_core_yield_reclaims_exact_offset_and_preserves_native_primary_geometry(factory,seconds,monkeypatch):
    """Actual Worker.process/Store paths; scheduler owner callbacks are synthetic."""
    import test_same_epoch_core_seams as harness
    original=harness.analyzer_result;edge=480000
    def with_boundary_event(native,a,b,markers):
        value=original(native,a,b,markers)
        if a<=edge-1<edge+1<=b:
            value['events']=[{'start':(edge-1-a)/16000,'end':(edge+1-a)/16000,'label':'Door','score':.25}]
        return value
    monkeypatch.setattr(harness,'analyzer_result',with_boundary_event)
    outputs=[];calls=[]
    for due in (False,True):
        h=factory((30.,60.,300.));h.settings.moss_automatic_enabled=True
        h.markers.extend((edge+d,1,name) for d,name in ((-1,'before'),(0,'at'),(1,'after')))
        cid=h.add(seconds)
        h.worker.moss_automatic=SimpleNamespace(primary_yield_due=lambda:due)
        grants=[]
        h.settings._native_execution_permit=SimpleNamespace(
            bind_primary=lambda *a,**k:None,request_automatic_turn=lambda:grants.append(True) or True)
        h.worker._fair_core_handoff=True
        starts=[];previous=0.
        for _ in range(50):
            chunk=h.store.claim()
            if chunk is None:break
            starts.append(chunk['next_offset']);assert chunk['next_offset']==previous
            yielded=h.worker.process(chunk)
            row=h.store.chunk(cid);assert row['next_offset']>previous
            previous=row['next_offset']
            if yielded:h.store.requeue_core(chunk)
        assert h.store.chunk(cid)['status']=='completed' and previous==seconds
        assert starts[0]==0 and len(starts)==(len(grants) if due else 1)
        with h.store.db() as db:
            primary={table:[tuple(r) for r in db.execute(sql)] for table,sql in (
                ('segments','SELECT start,end,text FROM segments ORDER BY start,end,text'),
                ('events','SELECT start,end,label FROM events ORDER BY start,end,label'))}
        h.settle()
        rows=h.rows();parts=[json.loads(r['body'])['part'] for r in rows]
        primary['compacts']=[(r['logical_start'],r['logical_end']) for r in parts]
        from test_multicore_seam_coverage import assert_disjoint_cover
        assert len(parts)==len(h.commits)>1
        assert_disjoint_cover(primary['compacts'],0,round(seconds*16000))
        assert primary['compacts']==[tuple(r['frames']) for r in h.commits]
        reasons=[r['reason'] for r in h.packets]
        assert reasons[0]=='core_first_or_unavailable_left'
        assert all(r in ('core_pending','no_same_response_native_overlap','core_reset_short_side') for r in reasons[1:]),reasons
        assert all(r['state'] in ('none','gap','published') for r in rows)
        cells=h.cells()
        primary['seams']=sorted((doc['schema'],doc['geometry']['seam_frame'],
            doc['geometry']['start_frame'],doc['geometry']['end_frame'],
            doc['support']['pcm16le_sha256']) for _,doc in cells)
        assert {30*16000,60*16000}<={r[1] for r in primary['seams']}
        assert len(cells)>=2
        primary['core_outcomes']=[(r['logical_end'],r['state'],r['reason']) for r in rows]
        primary['prepare_reasons']=reasons
        if seconds>300:
            assert {json.loads(r['body'])['scope']['index'] for r in rows}=={0,1}
        primary['analyzer_reads']=h.analyzer_reads
        outputs.append(primary);calls.append(len(starts))
    assert outputs[0]==outputs[1]
    assert outputs[0]['events'] and {'before','at','after'}<={r[2] for r in outputs[0]['segments']}
    assert calls[0]==1 and calls[1]>1
    report=Path(os.environ['RESET_REPLAY_REPORT_DIR']);report.mkdir(parents=True,exist_ok=True)
    (report/f'fair-core-{int(seconds)}.json').write_text(json.dumps({'seconds':seconds,'process_calls':calls,'equal_outputs':True,'projection':outputs[0]},indent=2))


def test_publication_hint_does_not_read_proofs_or_retry_storage(automatic_cell_jobs):
    f=automatic_cell_jobs;cell,_=f.ready_cell(frames=16)
    manager_intent(f,cell)
    statements=[]
    with patch.object(f.owner,'automatic_idle',side_effect=AssertionError('storage retry in hint')), \
         patch.object(f.automatic,'read_candidate',side_effect=AssertionError('payload in hint')), \
         observed_connections(statements=statements):
        assert f.automatic.primary_yield_due()
    assert all('SELECT proof' not in sql for sql in statements)
    f.permit.mark_unavailable()
    assert not f.automatic.primary_yield_due()


def test_failed_worker_drops_only_nonowned_gate_and_preserves_original_error(queued):
    store,config,cid=queued;p=permit();config._native_execution_permit=p
    worker=Worker(store,config);p.request_automatic_turn()
    waiting=p.primary_intent('probe');error=ValueError('synthetic claim failure')
    try:
        with patch.object(store,'claim',side_effect=error),pytest.raises(ValueError) as caught:worker.loop()
        assert caught.value is error
        assert waiting.granted and not p.status()['automatic_turn_pending']
    finally:waiting.release()
    record,_=reserve(p)
    with patch.object(store,'claim',side_effect=error),pytest.raises(ValueError):worker.loop()
    assert p._optional is record
    p.release_optional(record,cleaned=True)


@pytest.mark.parametrize('uncertain',[None,'process','probe'])
def test_actual_deleted_session_invalidation_does_not_orphan_retained_text_owner(automatic_cell_jobs,uncertain):
    f=automatic_cell_jobs;cell,_=f.ready_cell(frames=16)
    f.owner.submit_automatic(manager_intent(f,cell))
    with observed_connections(lose_commit_ack=True,commit_match="SET state='committed'"):
        record=f.finish()
    assert record['history_snapshot'] is not None and f.permit._optional is record
    if uncertain=='process':record['cleaned']=False
    if uncertain=='probe':
        record['speaker_probe']={'cancelled':threading.Event(),'operation':SimpleNamespace(cleaned=False),'commit_pending':False}
    f.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");f.db.commit()
    f.owner.invalidate_session('session')
    if uncertain is None:
        assert f.owner._live is None and f.permit._optional is None
    else:
        assert f.owner._live is record and f.permit._optional is record
        record['cleaned']=True;record.pop('speaker_probe',None)
        f.owner.invalidate_session('session')
        assert f.owner._live is None and f.permit._optional is None


def test_known_source_revocation_is_not_masked_by_concurrent_cancellation(automatic_cell_jobs):
    f=automatic_cell_jobs;cell,_=f.ready_cell(frames=16)
    f.owner.submit_automatic(manager_intent(f,cell))
    with patch.object(f.owner.results,'commit',side_effect=ReadError(503,'result_storage_unavailable')):record=f.finish()
    snapshot=record['history_snapshot'];record['cancelled'].set();f.cell_owner.revoked()
    with patch.object(f.owner.results,'match_committed',side_effect=ReadError(503,'result_storage_unavailable')):
        f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
    assert record['history_snapshot'] is snapshot and f.permit._optional is record
    with patch.object(f.owner.results,'commit',side_effect=AssertionError('source veto forbids commit')):
        f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
    assert record['invalidated'] and not record['storage_pending']
    assert f.permit._optional is None
