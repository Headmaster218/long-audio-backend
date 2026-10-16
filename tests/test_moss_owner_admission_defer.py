"""Real SQLite owner contention and stdin-gated stdlib children; no native models.

The historical start-phase failures remain unidentified. Automatic intent proof
qualification is the existing synthetic fixture; production owner/lease/permit,
controller/driver and durable terminal paths are exercised unchanged otherwise.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import moss_handy_jobs as jobs
from audio_backend import native_owned_cell_jobs as source
from audio_backend import native_owned_cell_store as cells
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.native_execution_permit import AdmissionError
from audio_backend.fragment_model_guard import ResourceLimit
import test_moss_handy_controller as child_fixture
from test_moss_automatic_integration import automatic_cell_jobs, manager_intent, result_rows


class TrackedInput:
    def __init__(self,stream):self.stream=stream;self.writes=[]
    def write(self,data):self.writes.append(data);return self.stream.write(data)
    def __getattr__(self,name):return getattr(self.stream,name)


@contextmanager
def stdlib_child_only():
    original=subprocess.Popen;children=[]
    def launch(command,**kwargs):
        assert command[:4]==[sys.executable,'-I','-B','-S']
        assert command[4]==str(controller.ROOT/'audio_backend/moss_handy_runner.py') and len(command)==7
        actual=command[:4]+[str(Path(child_fixture.__file__).resolve()),'--fixture',command[5],command[6],'fixed-v1']
        proc=original(actual,**kwargs);proc.stdin=TrackedInput(proc.stdin);children.append(proc);return proc
    try:
        with patch.object(controller.subprocess,'Popen',side_effect=launch):yield children
    finally:
        for proc in children:
            if proc.poll() is None:
                if proc.stdin is not None and not proc.stdin.closed:proc.stdin.close()
                proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)


def admit(f,automatic=True,owned=True):
    cell,body=f.ready_cell()
    if owned:f.owner._driver_factory=controller.OwnedDriver
    if automatic:
        intent=manager_intent(f,cell);assert f.owner.submit_automatic(intent)[0]==202
    else:assert f.submit_cell(cell,body)[0]==202
    return cell,f.owner._live


@pytest.mark.parametrize('phase,mode',[(p,m) for p in ('before_spawn','before_admission')
  for m in ('release','persistent','cancel','job_deadline','revoked','resource','child_exit','manual')
  if not (p=='before_spawn' and m=='child_exit')])
def test_real_slot_same_driver_child_and_accepted_identity(automatic_cell_jobs,phase,mode):
    f=automatic_cell_jobs;cell,record=admit(f,automatic=mode!='manual')
    original_identity=(record['job_id'],record['lease'],record['token'],record['deadline'])
    original_controller=record['controller'];jid=record['controller_id'];returned=[]
    original_run=original_controller.run
    def observe_run(job_id):
        status=original_run(job_id);returned.append(status);return status
    entered=threading.Event();release=threading.Event();holding=[];refusals=[];calls=[];drivers=[]
    real_read=f.cell_owner._read;real_submit=f.cell_owner.submit;real_check=jobs._ServiceGate.check
    real_inner=controller.OwnedDriver._check_start_gate
    real_factory=f.owner._driver_factory
    def factory(*args):
        d=real_factory(*args);drivers.append(d);return d
    record['controller']._factory=factory
    fault=threading.Event()
    def read(op,budget):
        if op.kind=='jobs_detail':
            holding.append(op);entered.set();assert release.wait(3),'owner barrier not released'
        return real_read(op,budget)
    def submit(kind,payload):
        before=f.cell_owner.health()['accepted'];answer=real_submit(kind,payload)
        if holding and not release.is_set() and kind=='validate_dispatch':
            assert answer=={'status':'busy','reason':'one_total_handoff_slot'}
            assert before==f.cell_owner.health()['accepted'];refusals.append(answer)
            assert record['lease'] is original_identity[1] and f.permit._optional is record
            if phase=='before_admission':
                assert len(children)==1 and drivers[0].proc is children[0]
                assert children[0].poll() is None and children[0].stdin.writes==[]
                assert not (f.owner.directory/'result.json').exists()
            if mode=='release':release.set();assert holding[0].done.wait(3)
            elif mode=='cancel':f.owner._cancel_record(record)
            elif mode=='job_deadline':record['deadline']=f.clock()-1
            elif mode=='revoked':record['lease'].cancelled.set()
            elif mode=='resource':fault.set()
            elif mode=='child_exit':children[0].terminate();children[0].wait(timeout=3)
        return answer
    def check(gate,where):
        calls.append(where)
        chosen=(where==phase and not holding and (phase=='before_admission' or calls.count('before_spawn')==2))
        if chosen:
            assert record['controller'].status(record['controller_id'])['invocation_possible'] is True
            op=real_submit('jobs_detail',(cell.session_id,cell.cell_id))
            assert isinstance(op,cells.Operation) and entered.wait(3)
            if mode=='persistent':drivers[0].guard.deadline=time.monotonic()+.23
            if mode=='resource':
                original_check=drivers[0].guard.check
                def resource_check(*a,**k):
                    if fault.is_set():raise ResourceLimit('private resource detail')
                    return original_check(*a,**k)
                drivers[0].guard.check=resource_check
        return real_check(gate,where)
    def inner(driver,where,cancelled):
        try:return real_inner(driver,where,cancelled)
        finally:
            if where==phase and holding:
                release.set();assert holding[0].done.wait(3) and holding[0].slot_released.is_set()
    began=time.monotonic()
    try:
        with stdlib_child_only() as children, patch.object(f.cell_owner,'_read',side_effect=read), \
             patch.object(f.cell_owner,'submit',side_effect=submit),patch.object(jobs._ServiceGate,'check',check), \
             patch.object(controller.OwnedDriver,'_check_start_gate',inner), \
             patch.object(original_controller,'run',side_effect=observe_run):
            record['thread'].run();assert len(returned)==1;status=returned[0]
            assert len(drivers)==1 and refusals and time.monotonic()-began<3
            assert record['job_id']==original_identity[0]
            if mode=='job_deadline':
                assert record['state']=='expired' and record['controller'] is None and record['token'] is None
                assert f.owner._live is None
                archived=result_rows(f.store)[0];assert archived['state']=='no_result' and archived['result_json'] is None
                persisted=json.loads(archived['metadata_json'])['outcome']
                assert persisted['failure']==status['failure'] and persisted['count_basis']=='unknown'
                assert persisted['run_attempts'] is None
            else:assert record['token'] is original_identity[2]
            if mode!='job_deadline':assert record['deadline']==original_identity[3]
            assert len(children)==(1 if mode=='release' or phase=='before_admission' else 0)
            assert all(p.poll() is not None for p in children)
            assert status['cleanup']['cleanup_verified'] and status['cleanup']['original_handle_waited_and_reaped']
            if mode=='release':
                assert len(refusals)==1 and children[0].stdin.writes==[b'1']
                assert status['receipt_validated'] and status['native_receipt']['run_attempts']==1
                assert record['durable_result_available']
            else:
                assert all(p.stdin.writes==[] for p in children)
                assert status['failure']['gate']==phase and not status['receipt_validated']
                outcome=controller.terminal_outcome(status)
                assert outcome['count_basis']=='unknown' and outcome['run_attempts'] is None
                assert outcome['failure']['owner_read']['dispatched'] is False
                if mode=='persistent':
                    assert 2<=len(refusals)<=4 and status['failure']['reason']=='optional_prelaunch_deadline'
                if mode=='manual':assert len(refusals)==1 and status['failure']['type']=='ReadError'
                if mode=='resource':assert status['failure']['type']=='ResourceLimit'
            assert record['cleaned'] and original_identity[1].released and not record['storage_pending']
            assert f.permit._optional is None and not f.cell_owner._pins
            assert len(result_rows(f.store))==1
            with pytest.raises(KeyError if mode=='job_deadline' else controller.Busy):original_run(jid)
            assert len(drivers)==1
    finally:
        release.set()
        for op in holding:assert op.done.wait(3) and op.slot_released.is_set()


@pytest.mark.parametrize('mode',['pending','completed_failure'])
def test_accepted_read_never_retried_at_automatic_inner_gate(automatic_cell_jobs,mode):
    f=automatic_cell_jobs;cell,record=admit(f,owned=False)
    release=threading.Event();operations=[];real_read=f.cell_owner._read;real_submit=f.cell_owner.submit
    def read(op,budget):
        if op.kind=='validate_dispatch':
            if mode=='pending':assert release.wait(3)
            else:raise sqlite3.OperationalError('private SQL message')
        return real_read(op,budget)
    def submit(kind,payload):
        answer=real_submit(kind,payload)
        if kind=='validate_dispatch':operations.append(answer)
        return answer
    d=controller.OwnedDriver(f.owner.directory,'f'*64);d._service_gate=jobs._ServiceGate(f.owner,record)
    try:
        with patch.object(f.cell_owner,'_read',side_effect=read),patch.object(f.cell_owner,'submit',side_effect=submit):
            with pytest.raises(ReadError) as caught:d._check_start_gate('before_spawn',record['cancelled'])
            assert len(operations)==1 and isinstance(operations[0],cells.Operation)
            assert not hasattr(caught.value,'_owner_read_not_dispatched')
            observation=controller.failure_observation('start',caught.value)
            assert observation['owner_read']['dispatched'] is True
            assert observation['owner_read']['stage']==('wait' if mode=='pending' else 'done')
            assert d.proc is None and f.drivers==[]
    finally:
        release.set()
        for op in operations:assert op.done.wait(3) and op.slot_released.is_set()
        record['cancelled'].set();record['thread'].run()


def test_actual_owner_lock_busy_is_marked_only_before_dispatch(automatic_cell_jobs):
    f=automatic_cell_jobs;cell,record=admit(f,owned=False);real_submit=f.cell_owner.submit;observed=[]
    def submit(kind,payload):
        if kind=='validate_dispatch':
            assert f.cell_owner._lock.acquire(timeout=1)
            try:answer=real_submit(kind,payload)
            finally:f.cell_owner._lock.release()
            observed.append(answer);return answer
        return real_submit(kind,payload)
    before=f.cell_owner.health()['accepted']
    with patch.object(f.cell_owner,'submit',side_effect=submit):
        with pytest.raises(ReadError) as caught:f.source.revalidate_dispatch(record['token'])
    assert observed==[{'status':'busy','reason':'owner_lock_busy'}]
    assert caught.value._owner_read_not_dispatched is source._OWNER_READ_NOT_DISPATCHED
    assert f.cell_owner.health()['accepted']==before
    record['cancelled'].set();record['thread'].run()


def busy_error():
    error=ReadError(503,'cell_owner_unavailable')
    error._owner_read_not_dispatched=source._OWNER_READ_NOT_DISPATCHED
    error._cell_owner_read=('submit','busy','owner_lock_busy',False)
    return error


@pytest.mark.parametrize('mode',['public_reason','wrong_marker','source_changed','sqlite','manual','probe'])
def test_nonretryable_gate_failures_remain_single_shot(tmp_path,mode):
    error=busy_error()
    if mode=='public_reason':del error._owner_read_not_dispatched
    if mode=='wrong_marker':error._owner_read_not_dispatched=object()
    if mode=='source_changed':error=ReadError(409,'cell_binding_changed')
    if mode=='sqlite':error=sqlite3.OperationalError('private SQL')
    calls=[]
    gate=SimpleNamespace(_automatic_text=mode not in ('manual','probe'),_automatic_wait_remaining=1.,_automatic_cancelled=False,check=lambda phase:(calls.append(phase),(_ for _ in ()).throw(error)))
    d=object.__new__(controller.OwnedDriver);d._service_gate=gate;d.directory=tmp_path;d.identity=object();d.proc=None
    d.guard=SimpleNamespace(deadline=time.monotonic()+1,check=lambda:None)
    with patch.object(controller,'check_directory'),patch.object(controller,'check_loader_context'):
        with pytest.raises(type(error)) as caught:d._check_start_gate('before_spawn',threading.Event())
    assert caught.value is error and calls==['before_spawn']
    assert not hasattr(jobs._SpeakerProbeGate(SimpleNamespace(),{}),'_automatic_text')
    assert jobs._ServiceGate(None,{'automatic_key':None})._automatic_text is False


def test_before_input_does_not_defer_and_constructs_no_driver():
    c,jid,drivers=child_fixture.ControllerTests().make();calls=[]
    class Gate:
        _automatic_text=True
        def check(self,phase):calls.append(phase);raise busy_error()
        def cleaning(self):pass
        def cleanup_sample(self):pass
    c._service_gate=Gate()
    try:
        status=c.run(jid);assert calls==['before_input'] and drivers==[]
        assert not status['invocation_possible'] and controller.terminal_outcome(status)['run_attempts']==0
    finally:c.discard(jid)


@pytest.mark.parametrize('value',[('/private','busy','owner_lock_busy',False),('submit','private','owner_lock_busy',False),
    ('submit','busy','private / SQL',False),('submit','busy','owner_lock_busy','private'),['private'],None])
def test_fixed_observation_redacts_unknown_attributes(value):
    e=ReadError(503,'cell_owner_unavailable');e._moss_start_gate='/private';e._cell_owner_read=value
    observed=controller.failure_observation('start',e)
    assert 'private' not in json.dumps(observed) and 'gate' not in observed


def test_worst_reachable_terminal_outcomes_fit_existing_cap():
    e=busy_error();e._moss_start_gate='before_admission'
    e._cell_owner_read=('submit','busy','one_total_handoff_slot',False)
    status=dict(invocation_possible=True,failure=controller.failure_observation('start',e),
      cleanup=dict(cleanup_verified=True,original_handle_waited_and_reaped=True,wait_returncode=-2147483648,
      remaining_live_owned=[{}]*128,cleanup_failure={'type':'ProcessLookupError'}))
    value=controller.terminal_outcome(status)
    value.update(manager_rejection={'phase':'snapshot_validation','category':'snapshot_refused'},state='failed',copied_pin_release_acknowledged=True)
    assert len(controller.encoded(value))<=1024
    # A validated native return cannot coexist with an inner-start refusal.
    status['failure']=controller.failure_observation('projection',ValueError())
    status.update(receipt_validated=True,native_receipt=dict(construct_attempts=1,session_attempts=1,run_attempts=1,
      truncated=True,aborted=True,model_closed=False,session_closed=False,raw_unavailable_reason='raw_text_invalid_utf8',
      error_type='UnicodeDecodeError',close_errors=[{'object':x,'type':'ProcessLookupError'} for x in ('model','session')]))
    value=controller.terminal_outcome(status);value.update(manager_rejection={'phase':'snapshot_validation','category':'snapshot_refused'},state='failed',copied_pin_release_acknowledged=True)
    assert len(controller.encoded(value))<=1024


@pytest.mark.parametrize('mode',['cancel','driver_expiry','job_expiry','manager_cancel','lease_revoked'])
def test_final_resource_sample_cannot_admit_after_cancellation_or_deadline(tmp_path,mode):
    cancelled=threading.Event();samples=[];calls=[]
    record=dict(automatic_key='synthetic',deadline=time.monotonic()+1,cancelled=threading.Event(),
      lease=SimpleNamespace(cancelled=threading.Event()))
    gate=jobs._ServiceGate(SimpleNamespace(clock=time.monotonic),record)
    gate.check=lambda phase:calls.append(phase)
    d=object.__new__(controller.OwnedDriver);d._service_gate=gate;d.directory=tmp_path;d.identity=object()
    writes=[];d.proc=SimpleNamespace(poll=lambda:None,stdin=SimpleNamespace(write=writes.append))
    def sample():
        samples.append(1)
        if len(samples)==2:
            if mode=='cancel':cancelled.set()
            elif mode=='driver_expiry':d.guard.deadline=time.monotonic()-1
            elif mode=='job_expiry':record['deadline']=time.monotonic()-1
            elif mode=='manager_cancel':record['cancelled'].set()
            else:record['lease'].cancelled.set()
    d.guard=SimpleNamespace(deadline=time.monotonic()+1,check=sample)
    with patch.object(controller,'check_directory'),patch.object(controller,'check_loader_context'):
        with pytest.raises(controller.Cancelled if mode in ('cancel','manager_cancel','lease_revoked') else AdmissionError):
            d._check_start_gate('before_admission',cancelled)
            d.proc.stdin.write(b'1')
    assert samples==[1,1] and calls==['before_admission'] and writes==[]
