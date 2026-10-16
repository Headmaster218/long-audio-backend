"""Outer automatic no-dispatch gate; real owner/SQLite and stdlib child only.

Acoustic proof/model receipts are synthetic. Real owner-slot refusals, accepted
read distinctions, original child cleanup and durable manager paths are retained.
The saved historical zero-invocation outcome is not replayed by these fixtures.
"""
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import moss_handy_jobs as jobs
from audio_backend import native_owned_cell_store as cells
from audio_backend import native_owned_cell_capture as capture
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.native_execution_permit import AdmissionError
from audio_backend.fragment_model_guard import ResourceLimit
import test_moss_owner_admission_defer as inner_fixture
import test_moss_handy_controller as child_fixture
from test_moss_automatic_integration import automatic_cell_jobs, result_rows


@pytest.mark.parametrize('mode',['release','persistent','cancel','revoked','job_deadline','resource','manual'])
def test_real_outer_slot_keeps_one_identity_before_any_child(automatic_cell_jobs,mode):
    f=automatic_cell_jobs;cell,record=inner_fixture.admit(f,automatic=mode!='manual')
    accepted=(record['job_id'],record['lease'],record['token'],record['deadline'])
    c=record['controller'];jid=record['controller_id'];returned=[];drivers=[]
    real_run=c.run;real_factory=c._factory;real_outer=c._check_before_spawn
    real_read=f.cell_owner._read;real_submit=f.cell_owner.submit;real_check=jobs._ServiceGate.check
    entered=threading.Event();release=threading.Event();fault=threading.Event()
    holding=[];refusals=[];calls=[];request_hashes=[]
    def factory(*args):
        d=real_factory(*args);drivers.append(d);request_hashes.append(args[1]);return d
    def run(job_id):
        result=real_run(job_id);returned.append(result);return result
    def read(op,budget):
        if op.kind=='jobs_detail':
            holding.append(op);entered.set();assert release.wait(3)
        return real_read(op,budget)
    def submit(kind,payload):
        before=f.cell_owner.health()['accepted'];answer=real_submit(kind,payload)
        if holding and not release.is_set() and kind=='validate_dispatch':
            assert answer=={'status':'busy','reason':'one_total_handoff_slot'}
            assert before==f.cell_owner.health()['accepted'];refusals.append(answer)
            assert record['lease'] is accepted[1] and record['token'] is accepted[2]
            assert f.permit._optional is record and children==[] and drivers[0].proc is None
            assert c.status(jid)['invocation_possible'] is False
            if mode=='release':release.set();assert holding[0].done.wait(3)
            elif mode=='cancel':f.owner._cancel_record(record)
            elif mode=='revoked':record['lease'].cancelled.set()
            elif mode=='job_deadline':record['deadline']=f.clock()-1
            elif mode=='resource':fault.set()
        return answer
    def check(gate,where):
        calls.append(where)
        if where=='before_spawn' and not holding:
            assert calls.count('before_spawn')==1 and not c.status(jid)['invocation_possible']
            op=real_submit('jobs_detail',(cell.session_id,cell.cell_id))
            assert isinstance(op,cells.Operation) and entered.wait(3)
            if mode=='persistent':drivers[0].guard.deadline=time.monotonic()+.23
            if mode=='resource':
                original=drivers[0].guard.check
                def sample(*args,**kwargs):
                    if fault.is_set():raise ResourceLimit('private resource detail')
                    return original(*args,**kwargs)
                drivers[0].guard.check=sample
        return real_check(gate,where)
    def outer():
        try:return real_outer()
        finally:
            if holding:release.set();assert holding[0].done.wait(3) and holding[0].slot_released.is_set()
    began=time.monotonic()
    try:
        with inner_fixture.stdlib_child_only() as children, patch.object(c,'_factory',side_effect=factory), \
             patch.object(c,'run',side_effect=run),patch.object(c,'_check_before_spawn',side_effect=outer), \
             patch.object(f.cell_owner,'_read',side_effect=read),patch.object(f.cell_owner,'submit',side_effect=submit), \
             patch.object(jobs._ServiceGate,'check',check):
            record['thread'].run();assert len(returned)==1;status=returned[0]
            assert len(drivers)==len(request_hashes)==1 and refusals and time.monotonic()-began<3
            assert record['job_id']==accepted[0] and (mode=='job_deadline' or record['deadline']==accepted[3])
            assert status['cleanup']['cleanup_verified'] and status['cleanup']['original_handle_waited_and_reaped']
            assert all(p.poll() is not None for p in children)
            archived=result_rows(f.store);assert len(archived)==1
            if mode=='release':
                assert len(refusals)==1 and len(children)==1 and children[0].stdin.writes==[b'1']
                assert status['receipt_validated'] and status['native_receipt']['run_attempts']==1
                assert record['durable_result_available'] and archived[0]['state']=='committed'
                assert calls.count('before_spawn')==3 and calls.count('before_admission')==1
            else:
                assert children==[] and not status['invocation_possible'] and not status['receipt_validated']
                assert status['failure']['phase']=='before_spawn'
                value=json.loads(archived[0]['metadata_json'])['outcome']
                assert archived[0]['state']=='no_result' and archived[0]['result_json'] is None
                assert value['count_basis']=='not_invoked' and value['run_attempts']==0
                assert value['failure']==status['failure'] and not value['failure']['owner_read']['dispatched']
                if mode=='persistent':
                    assert 2<=len(refusals)<=4 and value['failure']['reason']=='optional_prelaunch_deadline'
                if mode=='manual':assert len(refusals)==1 and value['failure']['type']=='ReadError'
                if mode=='resource':assert value['failure']['type']=='ResourceLimit'
            assert record['cleaned'] and accepted[1].released and not record['storage_pending']
            assert f.permit._optional is None and not f.cell_owner._pins
            with pytest.raises((KeyError,controller.Busy)):real_run(jid)
            assert len(drivers)==1
    finally:
        release.set()
        for op in holding:assert op.done.wait(3) and op.slot_released.is_set()


@pytest.mark.parametrize('mode',['pending','completed_failure'])
def test_accepted_outer_read_is_never_deferred(automatic_cell_jobs,mode):
    f=automatic_cell_jobs;cell,record=inner_fixture.admit(f,owned=False)
    c=record['controller'];release=threading.Event();operations=[]
    real_read=f.cell_owner._read;real_submit=f.cell_owner.submit
    def read(op,budget):
        if op.kind=='validate_dispatch':
            if mode=='pending':assert release.wait(3)
            else:raise sqlite3.OperationalError('private SQL message')
        return real_read(op,budget)
    def submit(kind,payload):
        result=real_submit(kind,payload)
        if kind=='validate_dispatch':operations.append(result)
        return result
    c._driver=SimpleNamespace(guard=SimpleNamespace(deadline=time.monotonic()+1,check=lambda:None))
    try:
        with patch.object(f.cell_owner,'_read',side_effect=read),patch.object(f.cell_owner,'submit',side_effect=submit):
            with pytest.raises(ReadError) as caught:c._check_before_spawn()
            assert len(operations)==1 and isinstance(operations[0],cells.Operation)
            assert not hasattr(caught.value,'_owner_read_not_dispatched')
            observation=controller.failure_observation('before_spawn',caught.value)
            assert observation['owner_read']['dispatched'] is True
            assert observation['owner_read']['stage']==('wait' if mode=='pending' else 'done')
            assert f.drivers==[] and not c.status(record['controller_id'])['invocation_possible']
    finally:
        release.set()
        for op in operations:assert op.done.wait(3) and op.slot_released.is_set()
        c._driver=None;record['cancelled'].set();record['thread'].run()


def pure_outer(check,*,automatic=True):
    c=object.__new__(controller.MossController);c._cancel=threading.Event()
    record=dict(automatic_key='synthetic' if automatic else None,deadline=time.monotonic()+2,
      cancelled=threading.Event(),lease=SimpleNamespace(cancelled=threading.Event()))
    gate=jobs._ServiceGate(SimpleNamespace(clock=time.monotonic),record);gate.check=check;c._service_gate=gate
    c._driver=SimpleNamespace(guard=SimpleNamespace(deadline=time.monotonic()+2,check=lambda:None))
    c._claim_matches=lambda:None;c.profile=controller.profile(child_fixture.NAME)
    return c,record


@pytest.mark.parametrize('mode',['public_reason','wrong_marker','source_changed','sqlite','subclass','manual','probe'])
def test_nonmarker_or_nonautomatic_remains_single_shot(mode):
    error=inner_fixture.busy_error()
    if mode=='public_reason':del error._owner_read_not_dispatched
    elif mode=='wrong_marker':error._owner_read_not_dispatched=object()
    elif mode=='source_changed':error=ReadError(409,'cell_binding_changed')
    elif mode=='sqlite':error=sqlite3.OperationalError('private SQL')
    elif mode=='subclass':
        class Derived(ReadError):pass
        derived=Derived(503,'cell_owner_unavailable');derived.__dict__.update(error.__dict__);error=derived
    calls=[]
    def check(phase):calls.append(phase);raise error
    c,record=pure_outer(check,automatic=mode not in ('manual','probe'))
    if mode=='probe':c._service_gate=SimpleNamespace(check=check)
    with pytest.raises(type(error)) as caught:c._check_before_spawn()
    assert caught.value is error and calls==['before_spawn']
    assert not hasattr(jobs._SpeakerProbeGate(SimpleNamespace(),{}),'_automatic_text')


@pytest.mark.parametrize('mode',['cancel','driver_expiry','job_expiry','manager_cancel','lease_revoked','source_changed','resource'])
def test_after_busy_final_sample_preserves_later_veto(mode):
    calls=[];samples=[];starts=[]
    def check(phase):
        calls.append(phase)
        if len(calls)==1:raise inner_fixture.busy_error()
        if mode=='source_changed':raise ReadError(409,'cell_binding_changed')
    c,record=pure_outer(check)
    def sample():
        samples.append(1)
        if len(samples)==3:
            if mode=='cancel':c._cancel.set()
            elif mode=='driver_expiry':c._driver.guard.deadline=time.monotonic()-1
            elif mode=='job_expiry':record['deadline']=time.monotonic()-1
            elif mode=='manager_cancel':record['cancelled'].set()
            elif mode=='lease_revoked':record['lease'].cancelled.set()
            elif mode=='resource':raise ResourceLimit('private detail')
    c._driver.guard.check=sample
    expected=(controller.Cancelled if mode in ('cancel','manager_cancel','lease_revoked') else
      ReadError if mode=='source_changed' else ResourceLimit if mode=='resource' else AdmissionError)
    with pytest.raises(expected) as caught:c._check_before_spawn();starts.append(1)
    assert calls==['before_spawn']*2 and starts==[]
    assert len(samples)==(2 if mode=='source_changed' else 3)
    observation=controller.failure_observation('before_spawn',caught.value)
    assert observation['owner_read']==dict(stage='submit',state='busy',reason='owner_lock_busy',dispatched=False)
    if mode=='source_changed':assert observation['reason']=='cell_binding_changed'
    if mode=='resource':assert observation['type']=='ResourceLimit'
    assert 'private' not in json.dumps(observation)


def test_real_immediate_owner_lock_busy_retries_without_dispatch(automatic_cell_jobs):
    f=automatic_cell_jobs;cell,record=inner_fixture.admit(f,owned=False)
    c=record['controller'];c._driver=SimpleNamespace(guard=SimpleNamespace(deadline=time.monotonic()+2,check=lambda:None))
    original=f.cell_owner.submit;answers=[];accepted=[]
    def submit(kind,payload):
        if kind=='validate_dispatch' and not answers:
            before=f.cell_owner.health()['accepted'];assert f.cell_owner._lock.acquire(timeout=1)
            try:answer=original(kind,payload)
            finally:f.cell_owner._lock.release()
            assert answer=={'status':'busy','reason':'owner_lock_busy'}
            assert f.cell_owner.health()['accepted']==before;answers.append(answer);return answer
        result=original(kind,payload)
        if kind=='validate_dispatch':accepted.append(result)
        return result
    try:
        with patch.object(f.cell_owner,'submit',side_effect=submit):c._check_before_spawn()
        assert len(answers)==1 and len(accepted)==1 and isinstance(accepted[0],cells.Operation)
        assert accepted[0].done.is_set() and accepted[0].slot_released.is_set()
        assert not c.status(record['controller_id'])['invocation_possible'] and f.drivers==[]
    finally:c._driver=None;record['cancelled'].set();record['thread'].run()


def test_existing_closed_sqlite_marker_still_has_its_separate_loop():
    calls=[]
    def check(phase):
        calls.append(phase)
        if len(calls)==1:
            error=sqlite3.OperationalError('private SQL')
            error._closed_automatic_read_busy=capture._CLOSED_AUTOMATIC_READ_BUSY;raise error
    c,record=pure_outer(check);c._check_before_spawn();assert calls==['before_spawn']*2


def test_before_input_stays_single_shot_before_driver_construction():
    c,jid,drivers=child_fixture.ControllerTests().make();calls=[]
    class Gate:
        _automatic_text=True
        def check(self,phase):calls.append(phase);raise inner_fixture.busy_error()
        def cleaning(self):pass
        def cleanup_sample(self):pass
    c._service_gate=Gate()
    try:
        status=c.run(jid);assert calls==['before_input'] and drivers==[]
        outcome=controller.terminal_outcome(status)
        assert not status['invocation_possible'] and outcome['count_basis']=='not_invoked' and outcome['run_attempts']==0
    finally:c.discard(jid)
