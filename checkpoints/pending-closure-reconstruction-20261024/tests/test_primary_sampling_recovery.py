"""Bounded authenticated sampler exhaustion and atomic recovery; no models.

Process census rows, resource values and original handles are controlled fakes.
The existing genuine sampler budget branches and unchanged Worker handoff run.
No observation or recovery grants process cleanup or historical exit proof.
"""
import copy
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import native_execution_permit as mod
from audio_backend.worker import Worker
import test_current_census_resample as census
import test_native_execution_permit as fixture


def make():
    p=mod.NativeExecutionPermit(fixture.settings());now=[0.]
    p._sample_clock=lambda:now[0]
    return p,now


def exhaust(p,kind='wall'):
    assert p._sampler is mod.service_sample
    name='SAMPLE_SECONDS' if kind=='wall' else 'MAX_ROWS'
    with census.censuses([census.caller()],[census.caller()]),patch.object(mod,name,0):
        p.observe_primary()


def good(p):p._sampler=lambda _:fixture.sample()


def view(p):
    return (dict(p._observed),{key:dict(row['children']) for key,row in p._handles.items()},
      p._confirmed_sample_exits,p._completed_sample_retries,p._sample_exit_confirmation)


def handle(p,pid=901,created=2.,children=None):
    h=fixture.Handle(pid);p._handles[id(h)]={'proc':h,'pid':pid,'created':created,
      'resident':None,'children':dict(children or {}),'idle':False}
    return h


@pytest.mark.parametrize('kind',['wall','rows'])
def test_exact_sampler_exhaustion_is_pending_then_one_complete_recovery(kind):
    p,now=make();p._observed={os.getpid():1.};prior=view(p)
    with patch.object(mod._LOG,'warning',side_effect=AssertionError('no callback for transient')):exhaust(p,kind)
    status=p.status();assert status['primary_sampling_pending'] and p.sampling_recovery_pending
    assert status['optional_admission_unavailable']=='primary_sampling_unavailable'
    diagnostic=status['primary_sampling_failure']
    assert {k:v for k,v in diagnostic.items() if k not in ('last_elapsed_ms','rows_observed')}==dict(operation='observe_primary',phase='process_enumeration',reason='process_sample_budget',budget=kind)
    assert type(diagnostic['last_elapsed_ms']) is int and 0<=diagnostic['last_elapsed_ms']<=2**31-1
    assert diagnostic['rows_observed']==0
    assert status['primary_observation_failure'] is None and status['primary_sampling_exhaustions']==1
    assert view(p)==prior and p._sample_flight is None
    good(p);now[0]=1.;assert p.recover_primary_sampling()
    status=p.status();assert not status['primary_sampling_pending'] and status['optional_admission_unavailable'] is None
    assert status['completed_primary_sampling_recoveries']==1 and status['primary_sampling_exhaustions']==1
    assert p._observed=={os.getpid():1.} and p._sample_flight is None
    token=object();p.reserve_optional(token,threading.Event(),lambda:None);p.release_optional(token,cleaned=True)


@pytest.mark.parametrize('mode',['unmarked','wrong_token','wrong_kind','list','subclass','custom_sampler'])
def test_generic_or_forged_budget_errors_stay_sticky(mode):
    p,now=make();error=mod.AdmissionError('process_sample_budget')
    if mode=='wrong_token':error._sample_budget=(object(),'wall',0,0)
    elif mode=='wrong_kind':error._sample_budget=(mod._SAMPLE_BUDGET,'private path',0,0)
    elif mode=='list':error._sample_budget=[mod._SAMPLE_BUDGET,'wall',0,0]
    elif mode=='subclass':
        class Derived(mod.AdmissionError):pass
        error=Derived('process_sample_budget');error._sample_budget=(mod._SAMPLE_BUDGET,'wall',0,0)
    elif mode=='custom_sampler':error=mod._sample_exhausted('wall')
    assert (mod._authenticated_exhaustion(error) is not None)==(mode=='custom_sampler')
    p._sampler=lambda _:(_ for _ in ()).throw(error);p.observe_primary()
    first=p.status()['primary_observation_failure'];assert first is not None
    assert p._unavailable=='primary_ownership_uncertain' and not p.sampling_recovery_pending
    assert not p.status()['primary_sampling_pending'] and p.status()['primary_sampling_exhaustions']==0
    good(p);now[0]=100;p.observe_primary();assert not p.recover_primary_sampling()
    assert p.status()['primary_observation_failure']==first and p._unavailable=='primary_ownership_uncertain'
    assert 'private' not in json.dumps(p.status())


@pytest.mark.parametrize('kind',['root','access','reuse','identity_cap','orphan'])
def test_real_sampler_identity_failures_never_become_transient(kind):
    p,now=make();root=census.caller();child=census.Process(901,census.ROOT)
    overrides={};first=[root,child];known={};cap=128
    if kind=='root':root.parent_error=mod.psutil.NoSuchProcess(root.pid)
    elif kind=='access':child.parent_error=mod.psutil.AccessDenied(child.pid)
    elif kind=='reuse':known={901:99.}
    elif kind=='identity_cap':cap=1
    else:first=[root];known={901:2.};overrides[(0,901)]=child
    p._observed=known
    with census.censuses(first,first,lookup_overrides=overrides),patch.object(mod,'MAX_IDENTITIES',cap):p.observe_primary()
    assert p._unavailable=='primary_ownership_uncertain' and not p.sampling_recovery_pending
    assert p.status()['primary_sampling_exhaustions']==0 and p.status()['primary_observation_failure']


def test_cooldown_skip_does_not_sample_recount_or_relatch():
    p,now=make();exhaust(p);calls=[];p._sampler=lambda _:(calls.append(1),fixture.sample())[1]
    first=p.status()['primary_sampling_failure'];first['budget']='corrupt returned copy'
    for stamp in (0.,.25,.999):
        now[0]=stamp;p.observe_primary();assert not p.recover_primary_sampling()
    assert calls==[] and p.status()['primary_sampling_exhaustions']==1
    assert p.status()['primary_observation_failure'] is None and p._sample_flight is None
    now[0]=1.;assert p.recover_primary_sampling() and calls==[1]
    assert p.status()['primary_sampling_failure']['budget']=='wall'
    assert not p.recover_primary_sampling() and calls==[1]


def test_repeated_exhaustion_has_one_second_floor_and_fixed_first_diagnostic():
    p,now=make();exhaust(p,'rows');first=p.status()['primary_sampling_failure']
    now[0]=.999;exhaust(p,'wall');assert p.status()['primary_sampling_exhaustions']==1
    now[0]=1.;exhaust(p,'wall');assert p.status()['primary_sampling_exhaustions']==2
    now[0]=1.999;good(p);assert not p.recover_primary_sampling()
    now[0]=2.;assert p.recover_primary_sampling()
    assert p.status()['primary_sampling_failure']==first and p._sample_flight is None


def test_optional_reservation_cannot_clear_due_pending_sampling():
    p,now=make();exhaust(p);now[0]=100.;calls=[]
    p._sampler=lambda _:(calls.append(1),fixture.sample())[1]
    with pytest.raises(mod.AdmissionError,match='primary_sampling_unavailable'):
        p.reserve_optional(object(),threading.Event(),lambda:None)
    assert calls==[] and p._optional is None and p.status()['primary_sampling_pending']
    assert p.recover_primary_sampling() and calls==[1]


def test_active_optional_resource_failure_still_cancels_and_retains_owner():
    p,now=make();good(p);token=object();cancelled=threading.Event();p.reserve_optional(token,cancelled,lambda:None)
    p._sampler=mod.service_sample
    with census.censuses([census.caller()],[census.caller()]),patch.object(mod,'MAX_ROWS',0):
        with pytest.raises(mod.AdmissionError,match='process_sample_budget'):p.check_optional(token)
    assert cancelled.is_set() and p._optional is token and p.sampling_recovery_pending
    p.release_optional(token,cleaned=False);assert p._optional is token and p._state=='OPTIONAL_BLOCKED'
    p.release_optional(token,cleaned=True);assert p._optional is None and p.sampling_recovery_pending


@pytest.mark.parametrize('pending',[False,True])
def test_single_flight_skips_parallel_observers_without_partial_admission(pending):
    p,now=make()
    if pending:exhaust(p);now[0]=1.
    entered=threading.Event();release=threading.Event();calls=[];prior=view(p)
    def sampler(_):calls.append(1);entered.set();assert release.wait(2);return fixture.sample()
    p._sampler=sampler;worker=threading.Thread(target=p.observe_primary);worker.start()
    try:
        assert entered.wait(1)
        for _ in range(8):p.observe_primary();assert not p.recover_primary_sampling()
        with pytest.raises(mod.AdmissionError) as refused:p.reserve_optional(object(),threading.Event(),lambda:None)
        assert type(refused.value) is mod.AdmissionError
        assert refused.value.reason==('primary_sampling_unavailable' if pending else 'process_sample_unavailable')
        assert calls==[1] and view(p)==prior and p._optional is None
        assert p.status()['primary_observation_failure'] is None
    finally:release.set();worker.join(2);assert not worker.is_alive()
    assert p._sample_flight is None and not p.status()['primary_sampling_pending']
    assert p.status()['completed_primary_sampling_recoveries']==int(pending)


@pytest.mark.parametrize('change',['sticky','shutdown','bind','retire'])
def test_late_sample_cannot_clear_newer_lifecycle_generation(change):
    p,now=make();exhaust(p);now[0]=1.;h=handle(p) if change=='retire' else None
    entered=threading.Event();release=threading.Event();results=[];prior=view(p)
    def sampler(_):entered.set();assert release.wait(2);return fixture.sample(extra={901:(2.,os.getpid(),20)})
    p._sampler=sampler;t=threading.Thread(target=lambda:results.append(p.recover_primary_sampling()));t.start()
    try:
        assert entered.wait(1)
        if change=='sticky':p._record_primary_failure('finish_primary','original_wait',mod.AdmissionError('primary_wait_unconfirmed'))
        elif change=='shutdown':p.close_admission()
        elif change=='bind':
            h=fixture.Handle()
            with patch.object(mod.psutil,'Process',return_value=SimpleNamespace(create_time=lambda:2.)):p.bind_primary(h)
        else:
            h.returncode=0
            with patch.object(mod.psutil,'Process',side_effect=mod.psutil.NoSuchProcess(h.pid)):p.finish_primary(h)
            assert h.waits==1 and id(h) not in p._handles
    finally:release.set();t.join(2);assert not t.is_alive()
    assert results==[False] and p._sample_flight is None and p.status()['primary_sampling_pending']
    assert p._observed==prior[0] and p.status()['completed_primary_sampling_recoveries']==0
    if change=='sticky':assert p._unavailable=='primary_ownership_uncertain' and p.status()['primary_observation_failure']
    if change=='shutdown':assert p.status()['admission_closed'] and not p.sampling_recovery_pending
    if change=='bind':assert id(h) in p._handles and p._handles[id(h)]['children']=={}


def test_family_capacity_failure_publishes_no_accounting_or_partial_children():
    p,now=make();p._observed={os.getpid():1.};first=handle(p,901);second=handle(p,902,3.)
    rows={os.getpid():(1.,0,10),901:(2.,os.getpid(),20),902:(3.,901,30),903:(4.,902,40)}
    p._sampler=lambda _:mod.ProcessSample(rows,100,10,10*1024**3,10*1024**3,1,True)
    prior=view(p)
    with patch.object(mod,'MAX_IDENTITIES',4):p.observe_primary()
    assert view(p)==prior and p._unavailable=='primary_ownership_uncertain'
    assert p.status()['primary_observation_failure']['phase']=='family_tracking'
    assert p._sample_flight is None and first.waits==second.waits==0


def test_success_commits_recovered_families_and_all_counters_together():
    p,now=make();h=handle(p,children={902:3.});exhaust(p);now[0]=1.;prior=view(p)
    rows={os.getpid():(1.,0,10),901:(2.,os.getpid(),20),902:(3.,901,30),903:(4.,902,40)}
    def sampler(_):
        assert view(p)==prior and p.status()['primary_sampling_pending']
        return mod.ProcessSample(rows,100,10,10*1024**3,10*1024**3,1,True)
    p._sampler=sampler;assert p.recover_primary_sampling()
    assert p._handles[id(h)]['children']=={902:3.,903:4.} and h.waits==0
    assert p._observed=={pid:row[0] for pid,row in rows.items()}
    assert p._confirmed_sample_exits==p._completed_sample_retries==p._sampling_recoveries==1
    assert not p._sampling_pending and p._sample_flight is None


def test_latest_handle_identity_conflict_cannot_publish_or_clear():
    p,now=make();h=handle(p);exhaust(p);now[0]=1.;prior=view(p)
    def sampler(_):
        p._handles[id(h)]['created']=99.
        return fixture.sample(extra={901:(2.,os.getpid(),10)})
    p._sampler=sampler;assert not p.recover_primary_sampling()
    assert p._observed==prior[0] and p._confirmed_sample_exits==p._completed_sample_retries==0
    assert p._unavailable=='primary_ownership_uncertain' and p._sample_flight is None


def test_positive_cleanup_and_empty_handles_do_not_clear_pending():
    p,now=make();h=handle(p);exhaust(p);h.returncode=0
    with patch.object(mod.psutil,'Process',side_effect=mod.psutil.NoSuchProcess(h.pid)):p.finish_primary(h)
    assert h.waits==1 and not p._handles and p.sampling_recovery_pending
    assert p.status()['completed_primary_sampling_recoveries']==0
    good(p);now[0]=1.;assert p.recover_primary_sampling()


def test_unconfirmed_cleanup_promotes_sticky_and_cannot_be_recovered():
    p,now=make();h=handle(p);exhaust(p);p.finish_primary(h)
    assert p._unavailable=='primary_ownership_uncertain' and id(h) in p._handles
    good(p);now[0]=10.;assert not p.recover_primary_sampling()
    assert p.status()['primary_observation_failure']['reason']=='primary_wait_unconfirmed'


@pytest.mark.parametrize('mode',['empty','primary_arrivals','stop','sticky'])
def test_worker_handoff_has_bounded_recovery_without_model_readiness(mode):
    p,now=make();exhaust(p);now[0]=1.;samples=[];steps=[];hints=[]
    p._sampler=lambda _:(samples.append(p._condition._is_owned()),fixture.sample())[1]
    worker=object.__new__(Worker);worker.settings=SimpleNamespace(_native_execution_permit=p)
    worker.stop_event=threading.Event()
    worker.moss_automatic=SimpleNamespace(jobs=None,maintain_text_storage=lambda **_:False,primary_yield_due=lambda:(hints.append(1),False)[1],step=lambda **kw:steps.append(kw))
    if mode=='stop':worker.stop_event.set()
    if mode=='sticky':p.mark_unavailable()
    if mode=='primary_arrivals':
        for _ in range(3):
            with p.primary_span('worker'):worker._automatic_handoff(request=True)
    else:worker._automatic_handoff(request=False)
    assert samples==([False] if mode in ('empty','primary_arrivals') else [])
    assert steps==[] and p._optional is None
    assert p.status()['primary_sampling_pending']==(mode in ('stop','sticky'))
    assert not p._primary


def test_persistent_budget_failure_keeps_primary_progress_and_recovery_backoff():
    p,now=make();exhaust(p);worker=object.__new__(Worker)
    worker.settings=SimpleNamespace(_native_execution_permit=p);worker.stop_event=threading.Event()
    worker.moss_automatic=SimpleNamespace(jobs=None,maintain_text_storage=lambda **_:False,primary_yield_due=lambda:False,step=lambda **kw:pytest.fail('no optional work'))
    for index,stamp in enumerate((.2,1.,1.5,2.)):
        now[0]=stamp
        with p.primary_span('worker'),census.censuses([census.caller()],[census.caller()]),patch.object(mod,'MAX_ROWS',0):
            worker._automatic_handoff(request=True)
    assert p._sampling_exhaustions==3 and p._sampling_recoveries==0 and p._sample_flight is None
    assert p._optional is None and not p._primary and p.sampling_recovery_pending


def test_accounting_recovery_does_not_bypass_ordinary_memory_reserves():
    p,now=make();exhaust(p);now[0]=1.;p._sampler=lambda _:fixture.sample(rss=6*1024**3)
    assert p.recover_primary_sampling()
    with pytest.raises(mod.AdmissionError,match='service_memory_budget'):
        p.reserve_optional(object(),threading.Event(),lambda:None)
    assert p._optional is None


@pytest.mark.parametrize('kind',['wall','rows'])
def test_budget_diagnostic_reuses_last_clock_and_enumerated_row_count(kind):
    from unittest.mock import Mock
    values=[0.,.250] if kind=='wall' else [0.,.050]
    clock=Mock(side_effect=values)
    with census.censuses([census.caller(),census.Process(901,0)],[census.caller()]), \
         patch.object(mod,'MAX_ROWS',4096 if kind=='wall' else 1):
        with pytest.raises(mod.AdmissionError) as caught:mod.service_sample(fixture.settings(),clock=clock)
        assert mod._authenticated_exhaustion(caught.value)==(kind,250 if kind=='wall' else 50,0 if kind=='wall' else 1)
    assert clock.call_count==2


def test_budget_counter_saturates_and_diagnostic_status_is_copied():
    p,now=make();p._sampling_exhaustions=2**31-2
    exhaust(p);now[0]=1.;exhaust(p,'rows')
    assert p._sampling_exhaustions==2**31-1
    first=p.status()['primary_sampling_failure'];first['phase']='private mutation'
    assert p.status()['primary_sampling_failure']['phase']=='process_enumeration'
    assert len(json.dumps(p.status()['primary_sampling_failure']))<512
    good(p);now[0]=2.;p._sampling_recoveries=2**31-1;assert p.recover_primary_sampling()
    assert p._sampling_recoveries==2**31-1 and p._sampling_exhaustions==2**31-1


@pytest.mark.parametrize('elapsed,expected',[(float('nan'),None),(float('inf'),None),(-1.,None),(10**1000,None),(1e100,2**31-1)])
def test_diagnostic_numeric_conversion_cannot_replace_budget_error(elapsed,expected):
    error=mod._sample_exhausted('wall',elapsed,0)
    assert type(error) is mod.AdmissionError and error.reason=='process_sample_budget'
    assert mod._authenticated_exhaustion(error)==('wall',expected,0)
