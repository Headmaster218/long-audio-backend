"""Fresh SQLite prelaunch contention; no native/model/provider or child process.

Real ServiceGate/MossJobs/Automatic/PrimaryAuthority paths and budgets are used.
A tiny fixture reader replaces only the full acoustic proof projection; source
and resource preconditions and native return are explicitly synthetic seams.
"""
from contextlib import contextmanager, closing
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import moss_automatic as auto
from audio_backend import native_owned_cell_capture as capture
from audio_backend import native_owned_cell_store as cells
from audio_backend.native_owned_cells import Unavailable
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.native_execution_permit import AdmissionError
from test_moss_gate_failure_diagnostics import gate_fixture
import test_moss_handy_controller as controller_fixture


def busy(code=sqlite3.SQLITE_BUSY):
    error=sqlite3.OperationalError('private SQL/path must not be retained')
    error.sqlite_errorcode=code
    error.sqlite_errorname={5:'SQLITE_BUSY',6:'SQLITE_LOCKED',9:'SQLITE_INTERRUPT',10:'SQLITE_IOERR',
                           261:'SQLITE_BUSY_RECOVERY',262:'SQLITE_LOCKED_SHAREDCACHE'}.get(code,'SQLITE_ERROR')
    return error


def marked(error):
    return getattr(error,'_closed_automatic_read_busy',None) is capture._CLOSED_AUTOMATIC_READ_BUSY


@contextmanager
def traced_connections(*, fault=None, shared=False):
    original=sqlite3.connect;opened=[];closed=[]
    class Tracked(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            if fault=='pragma' and sql.startswith('PRAGMA'):raise busy(6)
            if fault=='begin' and sql=='BEGIN':raise busy(6)
            return super().execute(sql,*args,**kwargs)
        def close(self):
            super().close();closed.append(self)
            if fault=='close':raise busy(6)
    def connect(path,*args,**kwargs):
        assert str(path).endswith('?mode=ro') and kwargs['timeout']==0 and kwargs['uri'] is True
        if fault=='connect':raise busy(6)
        if shared:path=str(path)+'&cache=shared'
        db=original(path,*args,**kwargs,factory=Tracked);opened.append(db);return db
    with patch.object(capture.sqlite3,'connect',side_effect=connect):yield opened,closed


def fixture_reader(authority,key,doc,budget):
    # Metadata-only accepted-row fixture. The genuine read-only authority and
    # automatic_intent phase remain the exact production contention boundary.
    row=authority._read(budget,'automatic_intent',lambda db:
        db.execute('SELECT state FROM fixture_authority WHERE id=?',(key,)).fetchone())
    return row is not None and row[0]=='accepted'


def setup_gate(tmp_path):
    gate,authority,record,permit,trace=gate_fixture(tmp_path)
    with closing(sqlite3.connect(authority.path)) as db,db:
        db.execute('CREATE TABLE fixture_authority(id TEXT PRIMARY KEY,state TEXT NOT NULL)')
        db.execute('INSERT INTO fixture_authority VALUES(?,?)',(record['automatic_key'],'accepted'))
    record.update(cleanup_deadline=None,state='running')
    record['lease'].release_pcm=lambda:trace.append('release_pcm')
    return gate,authority,record,permit,trace


@pytest.fixture
def gate_case(tmp_path):
    value=setup_gate(tmp_path)
    try:yield value
    finally:value[3].release_optional(value[2],cleaned=True)


def controller_case(gate,configure=lambda d:None):
    def prepare(driver):
        driver.guard=SimpleNamespace(deadline=time.monotonic()+5.,check=lambda:None)
        configure(driver)
    owner,jid,drivers=controller_fixture.ControllerTests().make(prepare)
    owner._service_gate=gate
    return owner,jid,drivers


def finish(owner,jid,permit,record):
    status=owner.status(jid)
    if status['state']=='cleanup_blocked':owner.retry_cleanup(jid)
    owner.discard(jid)
    permit.release_optional(record,cleaned=True)


def test_real_busy_release_revalidates_same_owner_and_launches_exactly_once(gate_case):
    gate,authority,record,permit,trace=gate_case
    holder=sqlite3.connect(authority.path,timeout=0,isolation_level=None)
    def configure(driver):holder.execute('BEGIN EXCLUSIVE')
    owner,jid,drivers=controller_case(gate,configure)
    waits=[];budgets=[];original_budget=cells.Budget
    def budget():
        value=original_budget();budgets.append(value);return value
    lease,token,deadline=record['lease'],record['token'],record['deadline']
    try:
        with traced_connections() as (opened,closed), patch.object(auto,'authority_valid',side_effect=fixture_reader), \
             patch.object(cells,'Budget',side_effect=budget):
            def wait(seconds):
                waits.append(seconds)
                assert 0<seconds<=.1 and len(opened)==len(closed)
                assert not permit._condition._is_owned()
                assert permit._optional is record and record['lease'] is lease and record['token'] is token
                assert record['deadline']==deadline and len(drivers)==1 and drivers[0].starts==0
                assert owner.status(jid)['invocation_possible'] is False
                holder.rollback()
                return False
            with patch.object(owner._cancel,'wait',side_effect=wait):status=owner.run(jid)
        assert status['state']=='returned_diagnostic' and status['receipt_validated']
        assert len(waits)==1 and len(drivers)==1 and drivers[0].starts==drivers[0].waits==1
        assert trace.count('release_pcm')==1 and trace.count('publication_current')==3
        assert len(budgets)==3 and len({id(b) for b in budgets})==3
        assert all(b.seconds==cells.OP_SECONDS and b._began is None for b in budgets)
        assert budgets[1].elapsed>0 and record['deadline']==deadline
        assert len(opened)==len(closed)==5  # before_input, three BUSY reads, next gate
        with pytest.raises(controller.Busy):owner.run(jid)
    finally:
        holder.close();finish(owner,jid,permit,record)


def test_persistent_real_lock_reaches_original_guard_deadline_without_invocation(gate_case):
    gate,authority,record,permit,_=gate_case
    holder=sqlite3.connect(authority.path,timeout=0,isolation_level=None)
    original_deadline=[]
    def configure(driver):
        driver.guard.deadline=time.monotonic()+.045
        original_deadline.append(driver.guard.deadline)
        holder.execute('BEGIN EXCLUSIVE')
    owner,jid,drivers=controller_case(gate,configure)
    try:
        with traced_connections() as (opened,closed), patch.object(auto,'authority_valid',side_effect=fixture_reader), \
             patch.object(owner._cancel,'wait',wraps=owner._cancel.wait) as wait:
            status=owner.run(jid)
        assert status['failure']=={'phase':'before_spawn','type':'AdmissionError','reason':'optional_prelaunch_deadline'}
        assert not status['invocation_possible'] and drivers[0].starts==0 and len(drivers)==1
        assert drivers[0].guard.deadline==original_deadline[0] and wait.call_count==1
        assert len(opened)==len(closed)==4 and holder.in_transaction
        result=controller.terminal_outcome(status)
        assert result['count_basis']=='not_invoked' and result['run_attempts']==0
    finally:
        holder.close();finish(owner,jid,permit,record)


@pytest.mark.parametrize('veto',['cancel','revoke','job_deadline','lease_cancel','permit_cancel'])
def test_wait_rechecks_cancellation_source_lease_permit_and_original_job_deadline(gate_case,veto):
    gate,authority,record,permit,_=gate_case
    holder=sqlite3.connect(authority.path,timeout=0,isolation_level=None)
    owner,jid,drivers=controller_case(gate,lambda d:holder.execute('BEGIN EXCLUSIVE'))
    original_deadline=record['deadline']
    def wait(seconds):
        holder.rollback()
        if veto=='cancel':owner.cancel(jid);return True
        if veto=='revoke':
            holder.execute("UPDATE fixture_authority SET state='revoked'")
        if veto=='job_deadline':gate.owner.clock=lambda:original_deadline
        if veto=='lease_cancel':
            record['lease'].released=False;record['lease'].cancelled=threading.Event();record['lease'].cancelled.set()
        if veto=='permit_cancel':record['cancelled'].set()
        return False
    try:
        with patch.object(auto,'authority_valid',side_effect=fixture_reader), patch.object(owner._cancel,'wait',side_effect=wait):
            status=owner.run(jid)
        assert not status['invocation_possible'] and drivers[0].starts==0 and len(drivers)==1
        assert record['deadline']==original_deadline
        assert status['failure']['phase']=='before_spawn'
        assert status['failure']['type']=={'cancel':'Cancelled','revoke':'ReadError'}.get(veto,'AdmissionError')
        assert controller.terminal_outcome(status)['run_attempts']==0
    finally:
        holder.close();finish(owner,jid,permit,record)


@pytest.mark.parametrize('phase',['before_input','inner_before_spawn','before_admission'])
def test_other_controller_boundaries_are_single_shot_even_for_marked_closed_read(gate_case,phase):
    gate,authority,record,permit,_=gate_case
    holder=sqlite3.connect(authority.path,timeout=0,isolation_level=None)
    if phase=='before_input':holder.execute('BEGIN EXCLUSIVE')
    def configure(driver):
        original=driver.start
        def start(cancelled):
            holder.execute('BEGIN EXCLUSIVE')
            original(cancelled)
            driver._service_gate.check('before_spawn' if phase=='inner_before_spawn' else 'before_admission')
        driver.start=start
    owner,jid,drivers=controller_case(gate,configure)
    try:
        with patch.object(auto,'authority_valid',side_effect=fixture_reader), patch.object(owner._cancel,'wait',side_effect=AssertionError('no wait')):
            status=owner.run(jid)
        assert status['failure']['type']=='OperationalError'
        assert status['failure']['phase']==('before_input' if phase=='before_input' else 'start')
        assert status['invocation_possible']==(phase!='before_input')
        assert len(drivers)==int(phase!='before_input')
        if drivers:assert drivers[0].starts==1
        assert controller.terminal_outcome(status)['run_attempts']==(0 if phase=='before_input' else None)
    finally:
        holder.close();finish(owner,jid,permit,record)


@pytest.mark.parametrize('phase',['session','cell_commit','core_commit','mapping'])
def test_shared_manual_authority_phases_never_get_prelaunch_marker(gate_case,phase):
    _,authority,_,permit,_=gate_case
    with pytest.raises(sqlite3.OperationalError) as error:
        authority._read(cells.Budget(),phase,lambda db:(_ for _ in ()).throw(busy(6)))
    assert not marked(error.value)
    assert permit._optional is not None


@pytest.mark.parametrize('fault',['connect','pragma','close'])
def test_connect_setup_or_close_uncertainty_has_no_safe_marker(gate_case,fault):
    _,authority,_,_,_=gate_case
    with traced_connections(fault=fault), pytest.raises(sqlite3.OperationalError) as error:
        authority._read(cells.Budget(),'automatic_intent',lambda db:(_ for _ in ()).throw(busy(6)))
    assert not marked(error.value)


@pytest.mark.parametrize('where',['begin','reader'])
def test_exact_closed_read_body_busy_or_locked_gets_marker_only_after_close(gate_case,where):
    _,authority,_,_,_=gate_case
    with traced_connections(fault='begin' if where=='begin' else None) as (opened,closed), \
         pytest.raises(sqlite3.OperationalError) as error:
        authority._read(cells.Budget(),'automatic_intent',lambda db:(_ for _ in ()).throw(busy(6)))
    assert marked(error.value) and type(error.value) is sqlite3.OperationalError
    assert len(opened)==len(closed)==1


def test_real_shared_cache_locked_read_can_be_marked_after_closed_connection(gate_case):
    _,authority,_,_,_=gate_case
    holder=sqlite3.connect(authority.path.as_uri()+'?cache=shared',uri=True,timeout=0,isolation_level=None)
    holder.execute('BEGIN IMMEDIATE');holder.execute("UPDATE fixture_authority SET state='held'")
    try:
        with traced_connections(shared=True) as (opened,closed), pytest.raises(sqlite3.OperationalError) as error:
            fixture_reader(authority,'a'*64,{},cells.Budget())
        assert error.value.sqlite_errorcode==sqlite3.SQLITE_LOCKED_SHAREDCACHE and marked(error.value)
        assert len(opened)==len(closed)==1 and holder.in_transaction
    finally:holder.close()


@pytest.mark.parametrize('code',[1,7,8,9,10,11,13,14,17,21,26,999999,True,None])
def test_other_sqlite_errors_are_unchanged_single_read_and_unmarked(gate_case,code):
    _,authority,_,_,_=gate_case
    original=busy(code)
    with traced_connections() as (opened,closed), pytest.raises(sqlite3.OperationalError) as error:
        authority._read(cells.Budget(),'automatic_intent',lambda db:(_ for _ in ()).throw(original))
    assert error.value is original and not marked(error.value) and len(opened)==len(closed)==1


@pytest.mark.parametrize('fault',['time','steps','identity','write'])
def test_budget_source_identity_or_readonly_write_veto_precedes_marker(gate_case,fault):
    _,authority,_,_,_=gate_case
    budget=cells.Budget()
    def read(db):
        if fault=='time':budget.deadline=0.
        if fault=='steps':budget.steps=cells.SQL_STEPS+1
        if fault=='identity':authority.identity='changed-fixture-identity'
        if fault=='write':return db.execute("UPDATE fixture_authority SET state='mutation'")
        raise busy(6)
    with traced_connections() as (opened,closed), pytest.raises((Unavailable,sqlite3.OperationalError)) as error:
        authority._read(budget,'automatic_intent',read)
    assert not marked(error.value) and len(opened)==len(closed)==1
    if fault=='write':assert error.value.sqlite_errorcode==sqlite3.SQLITE_READONLY
    elif fault=='identity':assert str(error.value)=='primary_database_replaced'
    else:assert str(error.value) in ('cooperative_deadline','operation_work_capacity')


def test_unmarked_controller_busy_is_not_retried(gate_case):
    gate,_,record,permit,_=gate_case
    owner,jid,drivers=controller_case(gate)
    original=gate.check
    def check(phase):
        if phase=='before_spawn':raise busy()
        return original(phase)
    try:
        with patch.object(auto,'authority_valid',side_effect=fixture_reader), patch.object(gate,'check',side_effect=check), \
             patch.object(owner._cancel,'wait',side_effect=AssertionError('no wait')):
            status=owner.run(jid)
        assert status['failure']['type']=='OperationalError' and drivers[0].starts==0
        assert not status['invocation_possible']
    finally:finish(owner,jid,permit,record)


def test_identity_lookup_consuming_original_read_deadline_cannot_mark_or_defer(gate_case):
    _,authority,_,_,_=gate_case
    now=[0.];lookups=[];original_identity=capture._identity
    def identity(path):
        value=original_identity(path);lookups.append(path)
        if len(lookups)==2:now[0]=cells.OP_SECONDS+.001
        return value
    original=busy(6);budget=cells.Budget()
    with patch.object(capture,'_identity',side_effect=identity), \
         patch.object(cells.time,'monotonic',side_effect=lambda:now[0]), \
         traced_connections() as (opened,closed), pytest.raises(Unavailable) as error:
        authority._read(budget,'automatic_intent',lambda db:(_ for _ in ()).throw(original))
    assert str(error.value)=='cooperative_deadline' and not marked(original)
    assert len(opened)==len(closed)==1 and len(lookups)==2
    assert budget.elapsed>budget.seconds
