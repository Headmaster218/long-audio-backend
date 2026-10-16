"""Synthetic controller receipts and genuine fresh SQLite, never model inference.

Manager tests use real permit/cell owner/result storage with a fake native driver
and simulated automatic intent authority. They do not qualify native binaries.
"""
from dataclasses import replace
import json
import sqlite3
from unittest.mock import patch

import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import moss_result_store as history
from audio_backend import moss_automatic as auto
from audio_backend.native_execution_permit import AdmissionError
from audio_backend.native_owned_support_cache import ReadError
import test_moss_handy_controller as controller_fixture
from test_moss_handy_controller import clean
from test_moss_result_store import archive_fixture, observed_connections, snapshot, usage
from test_moss_automatic_integration import (automatic_cell_jobs, manager_intent,
    result_rows, candidate, durable_snapshot, seed_intent)


def outcome(status):
    value = controller.terminal_outcome(status)
    assert len(history.encoded(value)) <= 1024
    return value


@pytest.mark.parametrize('phase,basis,counts', [
    ('prepared', 'not_invoked', [0,0,0]),
    ('before_input', 'not_invoked', [0,0,0]),
    ('start', 'unknown', [None,None,None]),
    ('wait', 'unknown', [None,None,None]),
    ('invalid', 'unknown', [None,None,None]),
    ('native', 'validated_receipt', [1,1,1]),
    ('late_cancel', 'validated_receipt', [1,1,1]),
    ('projection', 'validated_receipt', [1,1,1]),
])
def test_controller_preserves_count_basis_at_exact_boundary(phase,basis,counts):
    def configure(d):
        if phase == 'start':d.start_error=OSError(5, '/secret/argument')
        if phase == 'wait':d.wait_error=RuntimeError('stdout secret')
        if phase == 'invalid':d.modify=lambda r:r.update(run_attempts=2)
        if phase == 'native':d.modify=lambda r:r.update(error_type='RuntimeError')
    c,jid,drivers=controller_fixture.ControllerTests().make(configure)
    try:
        if phase == 'prepared':c.cancel(jid);status=c.status(jid)
        else:
            if phase == 'late_cancel':
                factory=c._factory
                def wrapped(*a):
                    d=factory(*a);d.callback=lambda:c.cancel(jid);return d
                c._factory=wrapped
            with patch.object(controller,'project',side_effect=ValueError('private transcript')) if phase=='projection' else patch.object(controller,'project',wraps=controller.project):
                with patch.object(c,'_claim_matches',side_effect=AdmissionError('optional_cancelled')) if phase=='before_input' else patch.object(c,'_claim_matches',wraps=c._claim_matches):
                    status=c.run(jid)
            if phase=='before_input':
                # The injected claim observation also blocks cleanup; retry the
                # normal cleanup only after that diagnostic seam is removed.
                status=c.retry_cleanup(jid)
        value=outcome(status)
        assert value['count_basis']==basis
        assert [value[k] for k in ('construct_attempts','session_attempts','run_attempts')]==counts
        assert value['receipt_validated']==(basis=='validated_receipt')
        assert value['invocation_possible']==(phase not in ('prepared','before_input'))
        if phase not in ('prepared',):assert value['failure']['phase']=={'invalid':'validate_return','native':'native_return','late_cancel':'native_return','projection':'projection'}.get(phase,phase)
        assert not any(word in json.dumps(value) for word in ('secret','stdout','transcript','/argument'))
        if phase=='start':assert value['failure']['errno']==5
        assert sum(d.starts for d in drivers)<=1
    finally:
        if c.status(jid)['state']=='cleanup_blocked':c.retry_cleanup(jid)
        c.discard(jid)


def test_valid_failed_receipt_retains_exact_partial_attempt_counts_and_sanitizes_close_errors():
    def configure(d):
        d.raw=None
        d.modify=lambda r:r.update(construct_attempts=1,session_attempts=0,run_attempts=0,
            error_type='private-native-message',model_closed=False,session_closed=False,
            close_errors=[{'object':'model','type':'OSError','message':'private text'},
                          {'object':'/secret/path','type':'private class','args':['audio']}])
    c,jid,drivers=controller_fixture.ControllerTests().make(configure)
    try:
        value=outcome(c.run(jid))
        assert [value[k] for k in ('construct_attempts','session_attempts','run_attempts')]==[1,0,0]
        assert value['native_error_type']=='other'
        assert value['close_errors']==[{'object':'model','type':'OSError'},{'object':'other','type':'other'}]
        assert all(word not in json.dumps(value) for word in ('private','secret','audio','message','args'))
    finally:c.discard(jid)


def test_validated_receipt_is_copied_before_projection_mutation():
    held=[]
    def configure(d):d.modify=lambda r:held.append(r)
    c,jid,_=controller_fixture.ControllerTests().make(configure)
    def fail(*a,**k):held[0]['run_attempts']=0;raise ValueError('projection')
    try:
        with patch.object(controller,'project',side_effect=fail):status=c.run(jid)
        assert outcome(status)['run_attempts']==1
    finally:c.discard(jid)


@pytest.mark.parametrize('automatic',[False,True])
def test_no_result_commit_and_lost_ack_are_exact_and_atomic(archive_fixture,automatic):
    store,a,sid=archive_fixture
    value=snapshot(sid,metadata=history.encoded({'context':{'admitted':'unchanged'},'outcome':{}}))
    key=None
    if automatic:
        with store.db() as db:key=seed_intent(db,sid)['candidate_key']
    a.reserve(value.identity,value.metadata_json,automatic_key=key)
    frozen=history.encoded({'context':{'admitted':'unchanged'},'outcome':{'count_basis':'unknown','run_attempts':None}})
    with observed_connections(lose_commit_ack=True,commit_match="SET state='no_result'"):
        with pytest.raises(ReadError):a.no_result(value.identity,automatic_key=key,metadata_json=frozen)
    before=durable_snapshot(store)
    assert result_rows(store)[0]['metadata_json']==frozen
    assert result_rows(store)[0]['state']=='no_result'
    assert result_rows(store)[0]['charge_bytes']==len(frozen)+history.ACCOUNTING_BYTES
    a.no_result(value.identity,automatic_key=key,metadata_json=frozen)
    assert durable_snapshot(store)==before
    if automatic:
        assert candidate(store,key)['state']=='no_result'
        with pytest.raises(ReadError,match='automatic_result_binding'):
            a.no_result(value.identity,automatic_key=key,interrupted=True,metadata_json=frozen)
    changed=json.loads(frozen);changed['outcome']['run_attempts']=0
    with pytest.raises(ReadError,match='result_snapshot_conflict'):
        a.no_result(value.identity,automatic_key=key,metadata_json=history.encoded(changed))
    assert durable_snapshot(store)==before


@pytest.mark.parametrize('change',['context','oversize','committed','old_no_result','old_interrupted','identity'])
def test_store_rejects_retrofit_conflict_and_non_outcome_changes(archive_fixture,change):
    store,a,sid=archive_fixture
    value=snapshot(sid,metadata=history.encoded({'context':{'same':True},'outcome':{}}))
    a.reserve(value.identity,value.metadata_json)
    if change=='committed':a.commit(value)
    if change=='old_no_result':a.no_result(value.identity)
    if change=='old_interrupted':
        reopened=history.MossResultStore(store)
        reopened.bind_recovery(store._test_history_lock)
        assert reopened.reconcile()['reconciled']==1
    frozen={'context':{'same':True},'outcome':{'count_basis':'unknown','run_attempts':None}}
    if change=='context':frozen['context']['same']=False
    if change=='oversize':frozen['outcome']['x']='x'*1024
    identity=replace(value.identity,original_jobs_epoch='b'*32) if change=='identity' else value.identity
    before=durable_snapshot(store)
    with pytest.raises(ReadError):a.no_result(identity,metadata_json=history.encoded(frozen))
    assert durable_snapshot(store)==before


def test_failure_rolls_back_metadata_accounting_and_automatic_transition(archive_fixture):
    store,a,sid=archive_fixture
    value=snapshot(sid,metadata=history.encoded({'outcome':{}}))
    with store.db() as db:key=seed_intent(db,sid)['candidate_key']
    a.reserve(value.identity,value.metadata_json,automatic_key=key)
    before=durable_snapshot(store)
    with store.db() as db:db.execute(f"CREATE TRIGGER fail_terminal BEFORE UPDATE ON {history.TABLE} BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(ReadError):a.no_result(value.identity,automatic_key=key,metadata_json=history.encoded({'outcome':{'run_attempts':None}}))
    assert durable_snapshot(store)==before


def submit_failed(f,automatic,*,cleanup=None):
    cell,body=f.ready_cell(frames=16)
    factory=f.owner._driver_factory
    def wrapped(*args):
        d=factory(*args);d.wait_error=RuntimeError('private failed native wait')
        if cleanup is not None:d.cleanup_value=cleanup
        return d
    f.owner._driver_factory=wrapped
    if automatic:f.owner.submit_automatic(manager_intent(f,cell))
    else:f.submit_cell(cell,body)
    return f.owner._live


@pytest.mark.parametrize('automatic',[False,True])
@pytest.mark.parametrize('lost_ack',[False,True])
def test_manager_keeps_frozen_owner_through_storage_failure_cancel_and_exact_retry(automatic_cell_jobs,automatic,lost_ack):
    f=automatic_cell_jobs;record=submit_failed(f,automatic)
    context=observed_connections(lose_commit_ack=True,commit_match="SET state='no_result'") if lost_ack else patch.object(f.owner.results,'no_result',side_effect=ReadError(503,'result_storage_unavailable'))
    with context:f.finish()
    frozen=record['terminal_metadata'];assert type(frozen) is bytes
    assert record['storage_pending'] and record['terminal_pending']
    assert record['cleaned'] and record['lease'].released and f.permit._optional is record
    with pytest.raises(ReadError,match='result_storage_pending'):f.owner._retire(record)
    before=json.loads(frozen);admission=json.loads(record['admission_metadata'])
    assert {**before,'outcome':{}}==admission
    assert before['outcome']['count_basis']=='unknown' and before['outcome']['run_attempts'] is None
    assert before['outcome']['cleanup_verified'] is True
    assert before['outcome']['copied_pin_release_acknowledged'] is True
    assert len(history.encoded(before['outcome']))<=1024
    f.owner.cancel('session',record['job_id'],f.owner.jobs_epoch)
    assert record['terminal_metadata'] is frozen and f.permit._optional is record
    code,_=f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
    assert code==200 and not record['storage_pending'] and not record['terminal_pending']
    assert f.permit._optional is None
    assert result_rows(f.store)[0]['metadata_json']==frozen
    assert sum(d.starts for d in f.drivers)==1 and sum(d.waits for d in f.drivers)==1
    if automatic:assert candidate(f.store,record['automatic_key'])['state']=='no_result'
    f.owner._retire(record)
    assert f.owner._live is None


@pytest.mark.parametrize('automatic',[False,True])
def test_cleanup_retry_precedes_terminal_seal(automatic_cell_jobs,automatic):
    f=automatic_cell_jobs
    record=submit_failed(f,automatic,cleanup={'cleanup_verified':False,'original_handle_waited_and_reaped':False,'remaining_live_owned':[{'pid':99}]})
    f.finish()
    assert record['terminal_metadata'] is None and not record['cleaned']
    assert result_rows(f.store)[0]['state']=='accepted' and f.permit._optional is record
    f.drivers[0].cleanup_value=clean()
    f.owner.cancel('session',record['job_id'],f.owner.jobs_epoch)
    record['thread'].run()
    row=result_rows(f.store)[0];value=json.loads(row['metadata_json'])['outcome']
    assert row['state']=='no_result' and value['cleanup_verified'] is True
    assert value['original_handle_waited_and_reaped'] is True and value['remaining_owned_count']==0
    assert value['count_basis']=='unknown' and value['run_attempts'] is None
    assert sum(d.starts for d in f.drivers)==1 and f.permit._optional is None


@pytest.mark.parametrize('automatic',[False,True])
@pytest.mark.parametrize('uncertain',[False,True])
def test_actual_session_deletion_only_releases_exact_cleaned_owner(automatic_cell_jobs,automatic,uncertain):
    f=automatic_cell_jobs;record=submit_failed(f,automatic)
    with patch.object(f.owner.results,'no_result',side_effect=ReadError(503,'result_storage_unavailable')):f.finish()
    assert f.permit._optional is record
    if uncertain:record['cleaned']=False
    f.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");f.db.commit()
    f.owner.invalidate_session('session')
    assert (f.permit._optional is record)==uncertain
    if uncertain:
        record['cleaned']=True
        f.owner.invalidate_session('session')
        assert f.permit._optional is None


def test_automatic_explicit_source_veto_does_not_publish_frozen_outcome(automatic_cell_jobs):
    f=automatic_cell_jobs;record=submit_failed(f,True)
    with patch.object(f.owner.results,'no_result',side_effect=ReadError(503,'result_storage_unavailable')):f.finish()
    with sqlite3.connect(f.store.path) as db:
        db.row_factory=sqlite3.Row
        auto.cancel_session(db,'session')
    code,_=f.owner.retry_storage('session',record['job_id'],f.owner.jobs_epoch)
    assert code==200 and not record['storage_pending'] and f.permit._optional is None
    row=result_rows(f.store)[0]
    assert row['state']=='accepted' and json.loads(row['metadata_json'])['outcome']=={}
    assert sum(d.starts for d in f.drivers)==1


@pytest.mark.parametrize('automatic',[False,True])
@pytest.mark.parametrize('phase',['preparation','native','late_cancel','projection','raw_unavailable'])
def test_manager_durable_outcome_preserves_validated_or_zero_counts(automatic_cell_jobs,automatic,phase):
    f=automatic_cell_jobs;cell,body=f.ready_cell(frames=16)
    factory=f.owner._driver_factory
    def wrapped(*args):
        if phase=='preparation':raise ValueError('private factory configuration')
        d=factory(*args)
        if phase=='native':d.modify=lambda r:r.update(error_type='RuntimeError')
        if phase=='raw_unavailable':d.raw=None
        if phase=='late_cancel':d.callback=lambda:f.owner._cancel_record(f.owner._live)
        return d
    f.owner._driver_factory=wrapped
    if automatic:f.owner.submit_automatic(manager_intent(f,cell))
    else:f.submit_cell(cell,body)
    with patch.object(controller,'project',side_effect=ValueError('private projection')) if phase=='projection' else patch.object(controller,'project',wraps=controller.project):
        f.finish()
    row=result_rows(f.store)[0];value=json.loads(row['metadata_json'])['outcome']
    assert row['state']=='no_result' and value['cleanup_verified']
    assert value['count_basis']==('not_invoked' if phase=='preparation' else 'validated_receipt')
    assert value['run_attempts']==(0 if phase=='preparation' else 1)
    assert f.permit._optional is None
    if phase=='raw_unavailable':assert value['raw_unavailable_reason']=='raw_text_oversize'
    assert sum(d.starts for d in f.drivers)==(0 if phase=='preparation' else 1)


@pytest.mark.parametrize('reason',['service_envelope_budget','service_host_envelope',
    'jobs_configuration_unavailable','optional_ownership_changed','publication_fence_busy'])
def test_fixed_admission_reasons_remain_distinct(reason):
    value=controller.failure_observation('before_spawn',AdmissionError(reason))
    assert value=={'phase':'before_spawn','type':'AdmissionError','reason':reason}
    assert controller.failure_observation('before_spawn',AdmissionError('/private/path'))['reason']=='other'


@pytest.mark.parametrize('reason',['no_native_result','raw_text_unavailable',
    'raw_text_oversize','raw_text_invalid_unicode'])
def test_validated_raw_unavailable_reason_survives_no_result(reason):
    def configure(d):
        d.raw=None;d.modify=lambda r:r.update(raw_unavailable_reason=reason)
    c,jid,_=controller_fixture.ControllerTests().make(configure)
    try:
        value=outcome(c.run(jid))
        assert value['raw_unavailable_reason']==reason and value['run_attempts']==1
    finally:c.discard(jid)


@pytest.mark.parametrize('phase,category',[
    ('eligibility','eligibility_refused'),('source_validation','source_validation_refused'),
    ('projection','result_capacity'),('retained','retained_capacity'),
    ('publication_before_release','publication_refused'),('publication_after_release','publication_refused'),
    ('snapshot_validation','snapshot_refused'),('configuration','configuration_unavailable'),('seal','seal_refused')])
def test_manager_first_rejection_is_durable_without_discarding_native_counts(automatic_cell_jobs,phase,category):
    from contextlib import ExitStack
    from audio_backend import moss_handy_jobs as jobs
    f=automatic_cell_jobs;cell,body=f.ready_cell(frames=16);f.submit_cell(cell,body)
    finish=f.owner._finish
    def wrapped(record,*args,**kwargs):
        with ExitStack() as stack:
            if phase=='eligibility':record['cancelled'].set()
            elif phase=='source_validation':stack.enter_context(patch.object(f.owner,'_validate',side_effect=ReadError(409,'private source detail')))
            elif phase=='projection':stack.enter_context(patch.object(jobs,'MAX_RESULT_BYTES',1))
            elif phase=='retained':
                charge=f.owner._retained_charge;calls=[]
                def observe(r):
                    calls.append(True);return jobs.MAX_RETAINED_BYTES+1 if len(calls)==1 else charge(r)
                stack.enter_context(patch.object(f.owner,'_retained_charge',side_effect=observe))
            elif phase.startswith('publication_'):
                current=f.source.publication_current;calls=[]
                def observe(token):
                    calls.append(True)
                    if len(calls)==(1 if phase=='publication_before_release' else 2):raise ReadError(409,'private source detail')
                    return current(token)
                stack.enter_context(patch.object(f.source,'publication_current',side_effect=observe))
            elif phase=='snapshot_validation':stack.enter_context(patch.object(f.owner.results,'validate_snapshot',side_effect=ValueError('private output')))
            elif phase=='configuration':
                enabled=f.owner._enabled;calls=[]
                def observe():
                    calls.append(True)
                    if len(calls)==3:raise ValueError('private configuration')
                    return enabled()
                stack.enter_context(patch.object(f.owner,'_enabled',side_effect=observe))
            elif phase=='seal':
                seal=f.owner._seal_history_locked
                def observe(r,c):r['cancelled'].set();return seal(r,c)
                stack.enter_context(patch.object(f.owner,'_seal_history_locked',side_effect=observe))
            return finish(record,*args,**kwargs)
    with patch.object(f.owner,'_finish',side_effect=wrapped):f.finish()
    row=result_rows(f.store)[0];value=json.loads(row['metadata_json'])['outcome']
    assert row['state']=='no_result' and value['run_attempts']==1 and value['receipt_validated']
    assert value['manager_rejection']=={'phase':'projection' if phase=='retained' else phase,'category':category}
    assert 'private' not in json.dumps(value) and len(history.encoded(value))<=1024
    assert f.permit._optional is None and sum(d.starts for d in f.drivers)==1
