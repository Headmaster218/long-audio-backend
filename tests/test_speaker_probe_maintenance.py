"""Finite metadata maintenance: real Worker turn/permit with fake acoustic providers.

Scheduler cases use the existing synthetic optional-link store. Separate fresh
SQLite cases use the real link store to qualify exact storage retry and vectors.
No native/model process, saved database, network or new retry authority is used.
"""
from copy import deepcopy
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_handy_jobs as jobs
from audio_backend import recording_speaker_link_store as ledger
from audio_backend import recording_speaker_links as links
from audio_backend import transcript_contract as contract
from audio_backend.worker import Worker
from audio_backend.native_owned_support_cache import ReadError
from test_moss_automatic_integration import automatic_cell_jobs, manager_intent, result_rows
from test_recording_speaker_probe_lifecycle import setup_probe
from test_recording_speaker_link_store import environment, prepared, reserve, rows, counters, SID, RID
from test_recording_speaker_links import vector
from test_moss_result_store import observed_connections


def worker_handoff(f):
    # Exercise the unchanged actual Worker handoff method. Primary processing is
    # represented by positively released permit intents, not an acoustic worker.
    worker=Worker.__new__(Worker)
    worker.settings=f.store.settings;worker.stop_event=threading.Event()
    worker.moss_automatic=f.automatic
    worker.settings._native_execution_permit=f.permit
    return worker


def pending(f,monkeypatch):
    sidecar,events,drivers,primary=setup_probe(f,monkeypatch,failure='commit')
    record=f.finish();probe=record['speaker_probe']
    assert probe['commit_pending'] and record['cleaned'] and record['lease'].released
    assert record['process_cleanup_verified'] and not record['thread'].is_alive()
    assert f.permit._optional is None and not f.owner.automatic_ready_hint()
    return sidecar,record,probe,drivers,worker_handoff(f)


def test_empty_candidate_snapshot_and_end_of_input_reach_storage_turn(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs;sidecar,record,probe,drivers,worker=pending(f,monkeypatch)
    assert not f.automatic._snapshot()
    with patch.object(f.automatic,'_snapshot',side_effect=AssertionError('maintenance must precede candidate reads')):
        assert f.owner.automatic_maintenance_hint() and f.automatic.primary_yield_due()
    frozen=contract.encoded(probe['batch']);sidecar.failure=None
    worker._automatic_handoff(request=True)
    assert not probe['commit_pending'] and f.owner.automatic_ready_hint()
    assert not f.owner.automatic_maintenance_hint() and f.owner._probe_staging_bytes==0
    assert len(drivers)==1 and len(sidecar.commits)==1
    assert contract.encoded(sidecar.commits[0][0])==frozen
    assert f.permit._optional is None and not f.permit.status()['automatic_turn_pending']
    assert len(result_rows(f.store))==1


def test_continuous_primary_gets_progress_between_failed_bounded_maintenance_turns(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs;sidecar,record,probe,drivers,worker=pending(f,monkeypatch)
    batch=probe['batch'];frozen=contract.encoded(batch);attempt=probe['attempt_key'];reservation=probe['reservation']
    commits=[];original=sidecar.commit
    def fail(*a,**k):commits.append(contract.encoded(a[1]));return original(*a,**k)
    sidecar.commit=fail
    for _ in range(4):
        f.automatic.next_attempt=0. # Simulate a new throttle interval, never a SQL budget reset.
        current=f.permit.primary_intent('probe');assert current.granted
        worker._automatic_handoff(request=True)
        following=f.permit.primary_intent('worker');assert not following.granted
        current.release();worker._automatic_handoff()
        assert following.granted and f.permit._optional is None
        assert not f.permit.status()['automatic_turn_pending']
        following.wait();following.release() # Next primary unit can execute.
        assert not f.automatic.primary_yield_due() # Existing one-second backoff.
        worker._automatic_handoff(request=True)
        assert probe['commit_pending'] and not f.owner.automatic_ready_hint()
        assert probe['batch'] is batch and probe['attempt_key'] is attempt and probe['reservation'] is reservation
        assert contract.encoded(batch)==frozen and f.owner._probe_staging_bytes==524288
    assert commits==[frozen]*4 and len(drivers)==1 and len(result_rows(f.store))==1
    first=deepcopy(probe['storage_failure'])
    assert first=={'phase':'publish','error_type':'ReadError','reason':'result_storage_unavailable','errno':None}
    health=f.owner.health();health['speaker_probe_storage_failure']['reason']='mutated'
    assert probe['storage_failure']==first
    detail=f.owner._summary(record);detail['recording_speaker_links']['storage_failure']['phase']='mutated'
    assert probe['storage_failure']==first
    sidecar.failure=None;f.automatic.next_attempt=0.;worker._automatic_handoff(request=True)
    assert not probe['commit_pending'] and probe['storage_failure']==first


@pytest.mark.parametrize('condition',['thread','process','lease','text_storage','probe','retry','acquisition','batch','attempt','cleaned'])
def test_uncertain_or_owned_work_cannot_request_maintenance(automatic_cell_jobs,monkeypatch,condition):
    f=automatic_cell_jobs;sidecar,record,probe,drivers,worker=pending(f,monkeypatch)
    with patch.dict(record),patch.dict(probe):
        if condition=='thread':record['thread']=SimpleNamespace(is_alive=lambda:True)
        elif condition=='process':record['process_cleanup_verified']=False
        elif condition=='lease':record['lease']=SimpleNamespace(released=False)
        elif condition=='text_storage':record['storage_pending']=True
        elif condition=='probe':probe['operation']=SimpleNamespace(cleaned=False)
        elif condition=='retry':probe['commit_retrying']=True
        elif condition=='batch':probe['batch']=None
        elif condition=='attempt':probe['attempt_key']=None
        elif condition=='cleaned':record['cleaned']=False
        with patch.object(f.owner,'_pending_record',object() if condition=='acquisition' else None):
            assert not f.owner.automatic_maintenance_hint()
            assert not f.automatic.primary_yield_due()
    sidecar.failure=None;f.owner.automatic_idle()


@pytest.mark.parametrize('phase',['reserve','commit'])
@pytest.mark.parametrize('cancel',[False,True])
def test_lost_ack_maintenance_retains_attempt_payload_without_probe_replay(automatic_cell_jobs,monkeypatch,phase,cancel):
    f=automatic_cell_jobs;sidecar,events,drivers,primary=setup_probe(f,monkeypatch)
    original=getattr(sidecar,phase);seen=[]
    def lose(*args,**kwargs):
        material=contract.encoded(args[1]) if phase=='commit' else contract.encoded(kwargs['batch'])
        seen.append(material)
        if len(seen)==1:
            original(*args,**kwargs);raise ReadError(503,'private acknowledgment detail')
        assert material==seen[0]
        return {'revision':2}
    setattr(sidecar,phase,lose)
    record=f.finish();probe=record['speaker_probe'];worker=worker_handoff(f)
    assert probe['commit_pending'] and f.owner.automatic_maintenance_hint()
    initial=deepcopy(probe['storage_failure'])
    assert initial['phase']==('reserve' if phase=='reserve' else 'publish') and initial['reason']=='other'
    if cancel:f.owner.cancel('session',record['job_id'],f.owner.jobs_epoch)
    worker._automatic_handoff(request=True)
    assert not probe['commit_pending'] and f.owner._probe_staging_bytes==0
    assert len(drivers)==int(phase=='commit')
    assert len(sidecar.reservations)==1 and len(sidecar.commits)==int(phase=='commit')
    assert probe['storage_failure']==initial and 'private' not in json.dumps(initial)
    if phase=='commit':assert len(seen)==2 and seen[0]==seen[1]
    else:assert len(sidecar.resolutions)==1 and len(seen)==1


def test_deleted_session_releases_staging_without_storage_or_model_retry(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs;sidecar,record,probe,drivers,worker=pending(f,monkeypatch)
    f.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");f.db.commit()
    f.owner.invalidate_session('session')
    assert not probe['commit_pending'] and probe['batch'] is None
    assert f.owner._probe_staging_bytes==0 and not f.owner.automatic_maintenance_hint()
    with patch.object(sidecar,'commit',side_effect=AssertionError('deleted pending work')):
        worker._automatic_handoff(request=True)
    assert len(drivers)==1 and f.permit._optional is None


def test_next_job_admission_waits_for_acknowledged_maintenance(automatic_cell_jobs,monkeypatch):
    f=automatic_cell_jobs;sidecar,record,probe,drivers,worker=pending(f,monkeypatch)
    cell,_=f.ready_cell(index=1,frames=16);intent=manager_intent(f,cell,2)
    worker._automatic_handoff(request=True)
    assert f.owner._live is record and len(result_rows(f.store))==1 and probe['commit_pending']
    sidecar.failure=None;f.automatic.next_attempt=0.;worker._automatic_handoff(request=True)
    assert not probe['commit_pending'] and f.owner._live is not record
    assert f.owner._live['automatic_key']==intent['candidate_key'] and len(result_rows(f.store))==2
    assert len(drivers)==1 # No prior probe replay; next text thread has not run.
    f.finish()
    assert sum(d.starts for d in f.drivers)==2


def real_pending_manager(store,value,batch,token,*,resolve):
    owner=jobs.MossJobs.__new__(jobs.MossJobs);owner._lock=threading.RLock()
    owner._probe_staging_bytes=524288;owner.speaker_links=value
    probe={'operation':None,'cancelled':threading.Event(),'batch':batch,'crops':(),
        'charge_bytes':524288,'reservation':token,'commit_pending':True,'commit_retrying':False,
        'resolve_only':resolve,'terminal_state':'source_scoped','state':'probe_unavailable',
        'attempt_key':(SID,RID,batch['job_id'],batch['result_sha256']),'storage_failure':None}
    return owner,{'thread':None,'speaker_probe':probe},probe


@pytest.mark.parametrize('phase',['reserve','commit'])
def test_genuine_sqlite_lost_ack_same_attempt_storage_only_resolution(environment,phase):
    store,value,_=environment;batch=prepared(store);token=reserve(value,batch) if phase=='commit' else None
    embedded=deepcopy(batch)
    for node in embedded['nodes']:node['vector']=vector(1.)
    with observed_connections(lose_commit_ack=True):
        with pytest.raises(ReadError):reserve(value,batch) if phase=='reserve' else value.commit(token,embedded)
    owner,record,probe=real_pending_manager(store,value,embedded if phase=='commit' else batch,token,resolve=phase=='reserve')
    before=rows(store,f'SELECT * FROM {ledger.BATCHES}')
    with patch.object(value,'recompute_scope',return_value={'saved':False,'state':'links_stale'}):owner._retry_probe_storage(record)
    assert not probe['commit_pending'] and owner._probe_staging_bytes==0
    assert rows(store,f'SELECT COUNT(*) FROM {ledger.BATCHES}')==[(1,)]
    if phase=='commit':assert rows(store,f'SELECT * FROM {ledger.BATCHES}')==before
    assert reserve(value,batch)['should_probe'] is False


def test_genuine_sqlite_busy_keeps_exact_vector_batch_then_recovers_once(environment):
    store,value,_=environment;batch=prepared(store);token=reserve(value,batch)
    embedded=deepcopy(batch)
    for node in embedded['nodes']:node['vector']=vector(1.)
    owner,record,probe=real_pending_manager(store,value,embedded,token,resolve=False)
    frozen=contract.encoded(embedded);before=counters(store)
    blocker=sqlite3.connect(store.path,timeout=0);blocker.execute('BEGIN IMMEDIATE')
    try:
        connections=[]
        with observed_connections(connections=connections):owner._retry_probe_storage(record)
        assert len(connections)==1 and connections[0]['timeout']==0
        assert probe['commit_pending'] and probe['batch'] is embedded and contract.encoded(embedded)==frozen
        assert counters(store)==before and owner._probe_staging_bytes==524288
    finally:blocker.rollback();blocker.close()
    with patch.object(value,'recompute_scope',return_value={'saved':False,'state':'links_stale'}):owner._retry_probe_storage(record)
    assert not probe['commit_pending'] and owner._probe_staging_bytes==0
    assert rows(store,f'SELECT COUNT(*) FROM {ledger.BATCHES}')==[(1,)]
    assert reserve(value,batch)['should_probe'] is False


@pytest.mark.parametrize('error',[ReadError(503,['private']),OSError(5,'/private/audio'),ValueError('private transcript')])
def test_fixed_storage_failure_redacts_and_keeps_first_observation(error):
    probe={};jobs.MossJobs._probe_storage_failure(probe,'retry_storage',error)
    first=deepcopy(probe['storage_failure'])
    jobs.MossJobs._probe_storage_failure(probe,'reserve',RuntimeError('replacement'))
    assert probe['storage_failure']==first and len(contract.encoded(first))<512
    assert 'private' not in json.dumps(first) and 'audio' not in json.dumps(first)
