"""Exact gate/authority reads on fresh SQLite; no controller, provider or model.

Source-token and resource preconditions are metadata-only seams. The actual
ServiceGate -> MossJobs._validate -> Automatic.validate_record -> authority_valid
-> PrimaryAuthority._read chain, read budgets and SQLite connections are used.
This reproduces a possible refusal, never the historical exception's identity.
"""
from contextlib import closing
import json
import os
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import moss_handy_jobs as jobs
from audio_backend import moss_automatic as auto
from audio_backend import native_owned_cell_capture as capture
from audio_backend import native_owned_cell_store as cells
from audio_backend.fragment_model_guard import ResourceLimit
from audio_backend.native_owned_cells import Unavailable
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.native_execution_permit import NativeExecutionPermit, ProcessSample, AdmissionError


def gate_fixture(tmp_path):
    path = tmp_path / 'jobs.sqlite3'
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
        db.execute(auto.SCHEMA[auto.TABLE])
    settings = SimpleNamespace(data_dir=tmp_path, process_memory_limit_bytes=6*1024**3,
                               disk_reserve_bytes=0)
    store = SimpleNamespace(path=path, settings=settings)
    authority = capture.PrimaryAuthority(store)
    automatic = auto.Automatic(store, None, capture=SimpleNamespace(authority=authority))
    trace = []
    source = SimpleNamespace(publication_current=lambda _:trace.append('publication_current'),
                             revalidate_dispatch=lambda _:trace.append('revalidate_dispatch'))
    sample = ProcessSample({os.getpid():(1.,0,1024)},1024,1024,10*1024**3,10*1024**3)
    permit = NativeExecutionPermit(settings, sampler=lambda _:sample)
    record = {'origin':'durable_cell', 'deadline':time.monotonic()+10.,
              'cancelled':threading.Event(), 'lease':SimpleNamespace(released=True),
              'token':SimpleNamespace(proof=b'{}'), 'automatic_key':'a'*64}
    owner = SimpleNamespace(clock=time.monotonic, cell_source=source, automatic=automatic,
                            permit=permit)
    owner._source_expired = lambda row:jobs.MossJobs._source_expired(owner,row)
    owner._validate = lambda row:jobs.MossJobs._validate(owner,row)
    permit.reserve_optional(record,record['cancelled'],lambda:None)
    return jobs._ServiceGate(owner,record),authority,record,permit,trace


@pytest.mark.parametrize('gate_phase,controller_phase', [
    ('before_spawn','before_spawn'), ('before_admission','start')])
def test_real_sqlite_contention_at_exact_gate_chain_keeps_existing_budget_and_flow(tmp_path,gate_phase,controller_phase):
    gate,authority,record,permit,trace = gate_fixture(tmp_path)
    original_connect = sqlite3.connect
    holder = original_connect(authority.path,timeout=0,isolation_level=None)
    holder.execute('BEGIN EXCLUSIVE')
    opened=[];closed=[];budgets=[]
    class Tracked(sqlite3.Connection):
        def close(self):
            closed.append(self)
            return super().close()
    def connect(database,*args,**kwargs):
        assert str(database).endswith('?mode=ro')
        assert kwargs['uri'] is True and kwargs['timeout']==0. and kwargs['isolation_level'] is None
        db=original_connect(database,*args,**kwargs,factory=Tracked);opened.append(db);return db
    original_budget=cells.Budget
    def make_budget():
        value=original_budget();budgets.append(value);return value
    try:
        with patch.object(capture.sqlite3,'connect',side_effect=connect), patch.object(cells,'Budget',side_effect=make_budget):
            with pytest.raises(sqlite3.OperationalError) as caught:
                gate.check(gate_phase)
        assert caught.value.sqlite_errorcode==sqlite3.SQLITE_BUSY
        assert len(opened)==len(closed)==3 and len({id(x) for x in opened})==3
        assert len(budgets)==1 and budgets[0].seconds==cells.OP_SECONDS
        assert 0<budgets[0].elapsed<cells.OP_SECONDS and budgets[0]._began is None
        assert trace==['publication_current','revalidate_dispatch']
        assert not record['cancelled'].is_set() and permit._optional is record
        assert controller.failure_observation(controller_phase,caught.value)=={
            'phase':controller_phase,'type':'OperationalError','sqlite_code':5,'sqlite_name':'SQLITE_BUSY'}
        # The original transaction and metadata are unchanged. Releasing this
        # test-only lock exposes the existing missing-intent veto, not success.
        assert holder.in_transaction
        holder.rollback()
        with pytest.raises(ReadError) as source_veto:gate.check(gate_phase)
        assert source_veto.value.reason=='automatic_source_revoked'
        assert controller.failure_observation(controller_phase,source_veto.value)['reason']=='automatic_source_revoked'
        with closing(original_connect(authority.path)) as db:
            assert db.execute(f'SELECT count(*) FROM {auto.TABLE}').fetchone()[0]==0
        assert permit._optional is record and not record['cancelled'].is_set()
    finally:
        holder.close();permit.release_optional(record,cleaned=True)
    assert permit._optional is None


@pytest.mark.parametrize('reason',sorted(controller._GATE_UNAVAILABLE_REASONS))
def test_existing_unavailable_reason_is_literal_only(reason):
    assert controller.failure_observation('before_spawn',Unavailable(reason))=={
        'phase':'before_spawn','type':'Unavailable','reason':reason}


@pytest.mark.parametrize('reason',sorted(controller._GATE_READ_REASONS))
def test_existing_read_reason_is_literal_only(reason):
    assert controller.failure_observation('start',ReadError(503,reason))=={
        'phase':'start','type':'ReadError','reason':reason}


@pytest.mark.parametrize('error',[
    Unavailable('/private/audio and SQL SELECT'),Unavailable('cooperative_deadline','secret'),
    Unavailable(['cooperative_deadline']),ReadError(503,'/private/audio and SQL SELECT'),
    ReadError(503,['cell_revoked']),ResourceLimit('/private/audio and SQL SELECT'),
])
def test_new_gate_categories_never_retain_messages_or_arbitrary_reasons(error):
    value=controller.failure_observation('before_spawn',error)
    assert value['type']==type(error).__name__
    if type(error) is not ResourceLimit:assert value['reason']=='other'
    else:assert set(value)=={'phase','type'}
    assert all(text not in json.dumps(value) for text in ('private','audio','SELECT','secret'))


@pytest.mark.parametrize('code,name,expected_code,expected_name',[
    (5,'SQLITE_BUSY',5,'SQLITE_BUSY'),(261,'SQLITE_BUSY_RECOVERY',261,'SQLITE_BUSY_RECOVERY'),
    (262,'SQLITE_LOCKED_SHAREDCACHE',262,'SQLITE_LOCKED_SHAREDCACHE'),
    (999999,'SQLITE_PRIVATE_PATH',None,'other'),(-1,'SQLITE_BUSY',None,'other'),
    (True,'SQLITE_ERROR',None,'other'),(5,'/private/audio',5,'other'),
    (5,['SQLITE_BUSY'],5,'other'),(5,'SQLITE_LOCKED',5,'other'),
])
def test_sqlite_code_and_name_use_finite_matching_allowlist(code,name,expected_code,expected_name):
    error=sqlite3.OperationalError('secret SQL /private/audio')
    error.sqlite_errorcode=code;error.sqlite_errorname=name
    assert controller.failure_observation('start',error)=={'phase':'start',
        'type':'OperationalError','sqlite_code':expected_code,'sqlite_name':expected_name}


def test_only_exact_new_types_are_categorized_and_no_message_stringification_occurs():
    for base in (Unavailable,ReadError,ResourceLimit,sqlite3.OperationalError):
        class Unknown(base):
            def __str__(self):raise AssertionError('No message inspection')
        error=Unknown(503,'private') if base is ReadError else Unknown('private')
        assert controller.failure_observation('start',error)=={'phase':'start','type':'other'}
    class Hostile:
        def __str__(self):raise AssertionError('No argument conversion')
    assert controller.failure_observation('start',Unavailable(Hostile()))['reason']=='other'


@pytest.mark.parametrize('possible',[False,True])
def test_gate_detail_fits_terminal_cap_and_does_not_change_count_basis(possible):
    failure=controller.failure_observation('start' if possible else 'before_spawn',
                                         Unavailable('primary_read_snapshot_not_ended'))
    value=controller.terminal_outcome({'invocation_possible':possible,'failure':failure,
        'receipt_validated':False,'native_receipt':None,'cleanup':{'cleanup_verified':True,
        'original_handle_waited_and_reaped':True,'wait_returncode':-9 if possible else None,
        'remaining_live_owned':[]}})
    value.update(state='failed',copied_pin_release_acknowledged=True)
    assert len(json.dumps(value,separators=(',',':')).encode())<=1024
    assert value['count_basis']==('unknown' if possible else 'not_invoked')
    assert value['run_attempts']==(None if possible else 0)
    assert value['receipt_validated'] is False


def test_existing_admission_reason_is_already_preserved():
    assert controller.failure_observation('before_spawn',AdmissionError('service_host_reserve'))=={
        'phase':'before_spawn','type':'AdmissionError','reason':'service_host_reserve'}
