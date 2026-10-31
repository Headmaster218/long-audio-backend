"""Bounded real condition handoff and controlled accounting; no model/provider.

Sampler and process values are fixtures. Genuine service_sample error/budget
branches run with controlled process census objects. No native child is run.
"""
import copy
import json
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import native_execution_permit as mod
from audio_backend import moss_handy_controller as controller
from audio_backend import moss_handy_jobs as jobs
from audio_backend.fragment_model_guard import ResourceLimit
import test_native_execution_permit as fixture
import test_current_census_resample as census


def make(kind='automatic_text'):
    p=mod.NativeExecutionPermit(fixture.settings(),sampler=lambda _:fixture.sample())
    token=object();cancelled=threading.Event()
    p.reserve_optional(token,cancelled,lambda:None,owner_kind=kind)
    return p,token,cancelled


def run(p,token,remaining=lambda:3.,cancelled=lambda:False,phase='running'):
    return p.check_optional(token,phase,running_remaining=remaining,running_cancelled=cancelled)


def fake_wait(p,action):
    def wait(seconds):
        assert 0 < seconds <= .05
        action(seconds)
    return patch.object(p._condition,'wait',side_effect=wait)


def test_real_competing_census_notifies_then_exact_owner_takes_fresh_sample():
    p,token,cancelled=make();entered=threading.Event();release=threading.Event();calls=[]
    original=fixture.Handle(901);p._handles[id(original)]={'proc':original,'pid':901,'created':2.,'children':{},'resident':None,'idle':False}
    def sample(_):
        calls.append(threading.current_thread().name)
        if len(calls)==1:
            entered.set();assert release.wait(1.)
        return fixture.sample(extra={901:(2.,os.getpid(),1024)})
    p._sampler=sample
    observer=threading.Thread(target=p.observe_primary,name='original-observation');observer.start()
    errors=[];done=threading.Event()
    def checking():
        try:run(p,token)
        except BaseException as e:errors.append(e)
        finally:done.set()
    checking_thread=threading.Thread(target=checking,name='fresh-running-sample')
    try:
        assert entered.wait(1.);checking_thread.start()
        assert not done.wait(.03);release.set()
        assert done.wait(1.);observer.join(1.);checking_thread.join(1.)
        assert not errors and calls==['original-observation','fresh-running-sample']
        assert p._optional is token and not cancelled.is_set() and p._sample_flight is None
        assert p._handles[id(original)]['proc'] is original and original.waits==0
        assert p._observed[901]==2. and p.status()['primary_sampling_exhaustions']==0
    finally:
        release.set();observer.join(1.)
        if checking_thread.ident is not None:checking_thread.join(1.)
        assert not observer.is_alive() and not checking_thread.is_alive()
        p.release_optional(token,cleaned=True)


def test_notifications_cannot_reset_absolute_grace_or_reuse_stale_rss():
    p,token,event=make();p._sample_flight=object();now=[10.];waits=[];calls=[]
    p._sample_clock=lambda:now[0];p._last_sample_at=9.;before=dict(p._observed)
    p._sampler=lambda _:(calls.append(1),fixture.sample())[1]
    def wake(seconds):waits.append(seconds);now[0]+=min(seconds,.01)
    with patch.object(mod.time,'monotonic',side_effect=lambda:now[0]),fake_wait(p,wake):
        with pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert caught.value.reason=='process_sample_unavailable' and not calls and event.is_set()
    assert .25 <= now[0]-10. < .261 and 25 <= len(waits) <= 27
    detail=mod.optional_sample_failure(caught.value)
    assert detail['subtype']=='busy_flight' and detail['grace_used'] and detail['reservation_held']
    assert detail['wait_ms']==250 and detail['sample_age_ms']>=1250
    assert p._observed==before and p._optional is token
    p._sample_flight=None;p.release_optional(token,cleaned=True)


@pytest.mark.parametrize('change',['record_cancel','controller_cancel','config','sticky','owner','kind'])
def test_wake_revalidates_exact_owner_and_guards_without_new_sample(change):
    p,token,event=make();p._sample_flight=object();other=threading.Event();calls=[]
    p._sampler=lambda _:(calls.append(1),fixture.sample())[1]
    def wake(_):
        p._sample_flight=None
        if change=='record_cancel':event.set()
        elif change=='controller_cancel':other.set()
        elif change=='config':p._configuration_current=lambda:False
        elif change=='sticky':p.mark_unavailable()
        elif change=='owner':p._optional=object()
        elif change=='kind':p._optional_kind='preemptible'
    with fake_wait(p,wake),pytest.raises(mod.AdmissionError) as caught:run(p,token,cancelled=other.is_set)
    assert not calls and caught.value.reason==('optional_ownership_changed' if change=='owner' else 'optional_cancelled')
    assert p._optional is not None
    detail=mod.optional_sample_failure(caught.value)
    assert detail['grace_used'] and detail['subtype']==('ownership_changed' if change=='owner' else 'sticky' if change=='sticky' else 'cancelled')


@pytest.mark.parametrize('kind,phase',[('preemptible','running'),('automatic_text','before_input'),
    ('automatic_text','before_spawn'),('automatic_text','before_admission'),('automatic_text','wait')])
def test_manual_probe_and_admission_checks_remain_single_shot(kind,phase):
    p,token,event=make(kind);p._sample_flight=object()
    with patch.object(p._condition,'wait',side_effect=AssertionError('unapproved wait')):
        with pytest.raises(mod.AdmissionError) as caught:run(p,token,phase=phase)
    assert caught.value.reason=='process_sample_unavailable' and event.is_set()
    assert not mod.optional_sample_failure(caught.value)['grace_used']


def test_optional_reservation_itself_never_waits_for_census():
    p=mod.NativeExecutionPermit(fixture.settings(),sampler=lambda _:fixture.sample());p._sample_flight=object()
    with patch.object(p._condition,'wait',side_effect=AssertionError('admission wait')):
        with pytest.raises(mod.AdmissionError,match='process_sample_unavailable'):
            p.reserve_optional(object(),threading.Event(),lambda:None,owner_kind='automatic_text')
    assert p._optional is None


@pytest.mark.parametrize('skip',['cooldown','stale_generation','recovery_not_due','skipped'])
def test_other_skips_never_gain_busy_grace(skip):
    p,token,event=make()
    with patch.object(p,'_take_sample',side_effect=mod._SampleSkipped(skip)),patch.object(p._condition,'wait',side_effect=AssertionError('nonbusy wait')):
        with pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert type(caught.value) is mod.AdmissionError and caught.value.reason=='process_sample_unavailable'
    assert event.is_set() and mod.optional_sample_failure(caught.value)['subtype']==skip


@pytest.mark.parametrize('marker',[None,('wrong','busy_flight'),('wrong','stale_generation')])
def test_generic_or_forged_unavailable_cannot_retry(marker):
    p,token,event=make();error=mod.AdmissionError('process_sample_unavailable')
    if marker:error._optional_sample_skip=marker
    with patch.object(p,'_resource_check',side_effect=error),patch.object(p._condition,'wait',side_effect=AssertionError('forged wait')):
        with pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert caught.value is error and event.is_set()


@pytest.mark.parametrize('failure',['identity','memory','disk','host','stale'])
def test_one_fresh_failed_sample_is_never_retried(failure):
    p,token,event=make();p._sample_flight=object();calls=[]
    def fresh(_):
        calls.append(1)
        if failure=='identity':raise mod._sample_observation(mod.AdmissionError('process_identity_uncertain'),'selected_recheck',mod.psutil.NoSuchProcess(901))
        if failure=='stale':p._sample_generation=object()
        return fixture.sample(rss=7*1024**3 if failure=='memory' else 1024,
            free=1 if failure=='disk' else 10*1024**3,available=1 if failure=='host' else 10*1024**3)
    p._sampler=fresh
    with fake_wait(p,lambda _:setattr(p,'_sample_flight',None)):
        with pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert calls==[1] and event.is_set() and p._optional is token
    expected={'identity':'process_identity_uncertain','memory':'service_memory_budget','disk':'service_disk_reserve','host':'service_host_reserve','stale':'process_sample_unavailable'}
    assert caught.value.reason==expected[failure]
    detail=mod.optional_sample_failure(caught.value)
    if failure=='identity':assert detail['phase']=='selected_recheck' and detail['cause']=='NoSuchProcess'
    if failure=='stale':assert detail['subtype']=='stale_generation'


@pytest.mark.parametrize('cause',['access','root'])
def test_genuine_census_failure_after_grace_preserves_failure_and_ownership(cause):
    p,token,event=make();p._sampler=mod.service_sample;p._sample_flight=object()
    root=census.caller();child=census.Process(901,census.ROOT)
    if cause=='access':child.parent_error=mod.psutil.AccessDenied(child.pid)
    else:root.parent_error=mod.psutil.NoSuchProcess(root.pid)
    with census.censuses([root,child],[root,child]),fake_wait(p,lambda _:setattr(p,'_sample_flight',None)):
        with pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert event.is_set() and p._optional is token
    detail=mod.optional_sample_failure(caught.value)
    assert detail['subtype']=='census_failure' and detail['cause']==('AccessDenied' if cause=='access' else 'NoSuchProcess')
    assert detail['phase']=='selected_identity' and p._sample_flight is None


def gate_fixture():
    p,token,event=make();lease=SimpleNamespace(released=False,cancelled=threading.Event());job_now=[10.]
    record={'automatic_key':'fixed','deadline':13.,'cancelled':event,'lease':lease,'origin':'durable_cell','token':object()}
    p._optional=record
    owner=SimpleNamespace(clock=lambda:job_now[0],permit=p,_source_expired=lambda r:False,
        cell_source=SimpleNamespace(publication_current=lambda t:None))
    return p,record,event,jobs._ServiceGate(owner,record),job_now


@pytest.mark.parametrize('when',['wait','fresh'])
@pytest.mark.parametrize('deadline',['job','driver'])
def test_original_deadline_clips_wait_and_is_rechecked_after_fresh_sample(when,deadline):
    p,record,event,gate,job_now=gate_fixture();p._sample_flight=object();now=[10.];calls=[]
    def expire():
        if deadline=='job':job_now[0]=13.
        else:now[0]=13.
    def wake(_):
        p._sample_flight=None
        if when=='wait':expire()
    def fresh(_):calls.append(1);expire();return fixture.sample()
    p._sampler=fresh
    with patch.object(mod.time,'monotonic',side_effect=lambda:now[0]),fake_wait(p,wake):
        with pytest.raises(mod.AdmissionError if deadline=='job' else ResourceLimit) as caught:
            gate.check('running',_driver_deadline=13.,_driver_cancelled=threading.Event())
    assert calls==([] if when=='wait' else [1]) and event.is_set()
    assert mod.optional_sample_failure(caught.value)['subtype']=='deadline'
    assert p._optional is record


@pytest.mark.parametrize('cancel',['lease','controller'])
def test_service_gate_rechecks_distinct_cancel_events_after_final_sample(cancel):
    p,record,event,gate,job_now=gate_fixture();p._sample_flight=object();controller_event=threading.Event()
    def fresh(_):
        (record['lease'].cancelled if cancel=='lease' else controller_event).set();return fixture.sample()
    p._sampler=fresh
    with fake_wait(p,lambda _:setattr(p,'_sample_flight',None)),pytest.raises(mod.AdmissionError,match='optional_cancelled'):
        gate.check('running',_driver_deadline=time.monotonic()+3.,_driver_cancelled=controller_event)
    assert event.is_set() and p._optional is record


def test_real_sampler_deadline_is_clipped_without_increasing_ordinary_budget():
    settings=fixture.settings();now=[0.]
    def clock():now[0]+=.01;return now[0]
    with census.censuses([census.caller()],[census.caller()]),pytest.raises(mod.AdmissionError) as caught:
        mod.service_sample(settings,clock=clock,deadline=.015)
    assert caught.value.reason=='process_sample_budget'
    assert mod._authenticated_exhaustion(caught.value)[0]=='wall' and now[0]<.25


def test_owned_driver_passes_its_original_deadline_and_event_without_replacing_handle():
    calls=[];event=threading.Event();proc=SimpleNamespace(returncode=0)
    polls=iter([None,0]);proc.poll=lambda:next(polls)
    service=SimpleNamespace(_automatic_text=True,check=lambda phase,**kw:calls.append((phase,kw)))
    driver=controller.OwnedDriver.__new__(controller.OwnedDriver)
    driver.proc=proc;driver._service_gate=service;driver.directory='fixture';driver.identity=object()
    driver.guard=SimpleNamespace(deadline=123.,check=lambda:None)
    with patch.object(controller,'check_directory'),patch.object(controller.time,'sleep'),patch.object(controller,'read_owned',return_value=b'{"raw_retained":false}'):
        assert driver.wait(event)==({'raw_retained':False},None)
    assert calls==[('running',{'_driver_deadline':123.,'_driver_cancelled':event})]
    assert driver.proc is proc and driver.guard.deadline==123.


def test_private_diagnostics_preserve_actual_reason_redact_and_survive_durable_projection():
    p,token,event=make();p._sample_flight=object();now=[1.];p._sample_clock=lambda:now[0];p._last_sample_at=0.
    def wake(seconds):now[0]+=seconds
    with patch.object(mod.time,'monotonic',side_effect=lambda:now[0]),fake_wait(p,wake),pytest.raises(mod.AdmissionError) as caught:run(p,token)
    failure=controller.failure_observation('wait',caught.value)
    assert failure['reason']=='process_sample_unavailable' and failure['sampling']['subtype']=='busy_flight'
    status={'invocation_possible':True,'receipt_validated':False,'failure':failure,
        'cleanup':{'cleanup_verified':True,'original_handle_waited_and_reaped':True,'remaining_live_owned':[],'wait_returncode':-9}}
    value=controller.terminal_outcome(status)
    assert value['failure']['sampling']==failure['sampling'] and value['run_attempts'] is None
    assert len(controller.encoded({**value,'state':'cancelled','manager_rejection':{'phase':'publication_before_release','category':'publication_refused'},'copied_pin_release_acknowledged':True}))<=1024
    altered=mod.optional_sample_failure(caught.value);altered['subtype']='private token'
    assert 'private' not in json.dumps(controller.failure_observation('wait',caught.value))
    assert 'private' not in json.dumps(controller.failure_observation('wait',mod.AdmissionError('/private/token')))


def test_additive_diagnostic_omission_keeps_full_old_outcome_and_status_unchanged():
    diag={'subtype':'busy_flight','reservation_held':True,'sample_age_ms':2**31-1,
        'wait_ms':250,'grace_used':True,'phase':'selected_identity','cause':'NoSuchProcess'}
    status={'invocation_possible':True,'receipt_validated':False,
        'failure':{'phase':'wait','type':'AdmissionError','reason':'process_sample_unavailable','padding':'x'*280},
        'cleanup':{'cleanup_verified':True,'original_handle_waited_and_reaped':True,'remaining_live_owned':[]}}
    plain=controller.terminal_outcome(status);status['failure']['sampling']=diag;before=copy.deepcopy(status)
    value=controller.terminal_outcome(status)
    assert value==plain and status==before and len(controller.encoded(value))<=1024


@pytest.mark.parametrize('value',[None,(object(),'busy_flight',True,0,0,None,None,True),
    (mod._OPTIONAL_SAMPLE,'/private/path',True,0,0,None,None,True),
    (mod._OPTIONAL_SAMPLE,'busy_flight',True,float('nan'),0,None,None,True)])
def test_unknown_or_malformed_private_diagnostic_is_omitted(value):
    error=mod.AdmissionError('process_sample_unavailable');error._optional_sample_failure=value
    assert mod.optional_sample_failure(error) is None
    assert 'sampling' not in controller.failure_observation('wait',error)


@pytest.mark.parametrize('change',['owner','config','sticky'])
def test_final_fresh_sample_rechecks_exact_owner_configuration_and_sticky_state(change):
    p,token,event=make();p._sample_flight=object()
    def fresh(_):
        if change=='owner':p._optional=object()
        elif change=='config':p._configuration_current=lambda:False
        else:p._unavailable='primary_ownership_uncertain'
        return fixture.sample()
    p._sampler=fresh
    with fake_wait(p,lambda _:setattr(p,'_sample_flight',None)),pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert caught.value.reason==('optional_ownership_changed' if change=='owner' else 'optional_cancelled')
    assert event.is_set() is (change!='owner')


def test_existing_pending_sampling_diagnostic_is_not_described_as_sticky():
    p,token,event=make();p._sampling_pending=True;p._unavailable='primary_sampling_unavailable'
    with patch.object(p._condition,'wait',side_effect=AssertionError('pending wait')):
        with pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert caught.value.reason=='optional_cancelled'
    assert mod.optional_sample_failure(caught.value)['subtype']=='sampling_pending'


def test_diagnostic_clock_failure_cannot_replace_original_refusal():
    p,token,event=make();error=mod.AdmissionError('process_identity_uncertain')
    p._sample_clock=lambda:(_ for _ in ()).throw(ValueError('private diagnostic clock'))
    with patch.object(p,'_resource_check',side_effect=error),pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert caught.value is error and event.is_set() and mod.optional_sample_failure(error) is None


def test_fresh_real_budget_failure_keeps_its_actual_reason_without_second_attempt():
    p,token,event=make();p._sampler=mod.service_sample;p._sample_flight=object();rows=[census.caller()]
    with census.censuses(rows,rows) as (calls,_),patch.object(mod,'MAX_ROWS',0),fake_wait(p,lambda _:setattr(p,'_sample_flight',None)):
        with pytest.raises(mod.AdmissionError) as caught:run(p,token)
    assert caught.value.reason=='process_sample_budget' and calls==[0]
    assert mod.optional_sample_failure(caught.value)['subtype']=='rows_budget'
    assert event.is_set() and p._sampling_pending and p._optional is token


def test_maximum_validated_receipt_outcome_omits_only_additive_sampling_if_needed():
    status={'invocation_possible':True,'receipt_validated':True,
        'failure':{'phase':'before_spawn','type':'AdmissionError','reason':'process_sample_unavailable'},
        'native_receipt':{'construct_attempts':1,'session_attempts':1,'run_attempts':1,
            'truncated':False,'aborted':True,'model_closed':True,'session_closed':True,
            'raw_unavailable_reason':'raw_output_capacity','error_type':'RuntimeError',
            'close_errors':[{'object':'session','type':'RuntimeError'},{'object':'model','type':'RuntimeError'}]},
        'cleanup':{'cleanup_verified':True,'original_handle_waited_and_reaped':True,
            'remaining_live_owned':[],'wait_returncode':-2147483648,'cleanup_failure':{'type':'RuntimeError'}}}
    plain=controller.terminal_outcome(status)
    status['failure']['sampling']={'subtype':'stale_generation','reservation_held':True,
        'sample_age_ms':2147483647,'wait_ms':250,'grace_used':True,'phase':'remembered_identity','cause':'FileNotFoundError'}
    value=controller.terminal_outcome(status)
    assert value==plain
    value.update(state='returned_diagnostic',copied_pin_release_acknowledged=True,
        manager_rejection={'phase':'publication_before_release','category':'publication_refused'})
    assert len(controller.encoded(value))<=1024


@pytest.mark.parametrize('when',['wait','fresh'])
@pytest.mark.parametrize('change',['generation','revocation_pending','readiness'])
def test_post_wait_existing_source_guard_detects_change_before_pin_events(when,change):
    from audio_backend.native_owned_cell_jobs import CellJobsSource
    from audio_backend.native_owned_support_cache import ReadError
    p,record,event,gate,job_now=gate_fixture();p._sample_flight=object()
    source=CellJobsSource.__new__(CellJobsSource);source._closed=False
    source._configuration_allowed=lambda:True;source.epoch='adapter';source._identity=object()
    source.store=SimpleNamespace(settings=SimpleNamespace(native_owned_cells_enabled=True,moss_jobs_enabled=True))
    owner=SimpleNamespace(_epoch=object(),qualification=object(),_publication_revoke_pending=False,ready=True)
    owner.authority=SimpleNamespace(owner=owner,identity=source._identity)
    owner.health=lambda:{'ready':owner.ready and not owner._publication_revoke_pending}
    source.owner=owner;gate.owner.cell_source=source
    record['token']=SimpleNamespace(owner=owner,adapter_epoch=source.epoch,generation=owner._epoch)
    observed=[];original=source.publication_current
    def current(token):
        assert not p._condition._is_owned()
        observed.append(1);return original(token)
    source.publication_current=current
    def mutate():
        if change=='generation':owner._epoch=object()
        elif change=='revocation_pending':owner._publication_revoke_pending=True
        else:owner.ready=False
    def wake(_):
        p._sample_flight=None
        if when=='wait':mutate()
    def fresh(_):
        if when=='fresh':mutate()
        return fixture.sample()
    p._sampler=fresh
    with fake_wait(p,wake),pytest.raises(ReadError) as caught:
        gate.check('running',_driver_deadline=time.monotonic()+3.,_driver_cancelled=threading.Event())
    assert observed==[1,1] and not event.is_set() and not record['lease'].cancelled.is_set()
    assert caught.value.reason==('cell_revoked' if change=='generation' else 'cell_owner_unavailable')
    assert p._optional is record and not hasattr(caught.value,'_owner_read_not_dispatched')


@pytest.mark.parametrize('change',['lease','controller','job','driver'])
def test_final_source_guard_is_followed_by_cancel_and_both_original_deadline_checks(change):
    p,record,event,gate,job_now=gate_fixture();controller_event=threading.Event();now=[10.];calls=[]
    def current(token):
        assert not p._condition._is_owned();calls.append(1)
        if len(calls)==2:
            if change=='lease':record['lease'].cancelled.set()
            elif change=='controller':controller_event.set()
            elif change=='job':job_now[0]=13.
            else:now[0]=13.
    gate.owner.cell_source.publication_current=current
    with patch.object(mod.time,'monotonic',side_effect=lambda:now[0]):
        with pytest.raises(ResourceLimit if change=='driver' else mod.AdmissionError):
            gate.check('running',_driver_deadline=13.,_driver_cancelled=controller_event)
    assert calls==[1,1] and p._optional is record
