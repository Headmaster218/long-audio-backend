"""Candidate primary-probe census coordination, without real children.

These reconstruct possible scheduling overlaps, not historical attribution.
Fixtures follow the existing reservation/dispatch and compact Worker patterns.
Each case has one original five-second deadline, including release and join.
No model, audio, vector, transcript, database, network, or process is created.
"""
from contextlib import contextmanager, nullcontext
import json
import os
import socket
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import moss_handy_jobs as jobs
from audio_backend import native_execution_permit as mod
from audio_backend import recording_speaker_probe_runner as runner
from audio_backend.worker import Worker


MIB = 1024**2


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No child, database, socket, or input/result work')
    monkeypatch.setattr(controller.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(runner, 'check_directory', forbidden)
    monkeypatch.setattr(runner, 'read_owned', forbidden)
    monkeypatch.setattr(mod.psutil, 'process_iter', forbidden)
    monkeypatch.setattr(mod.psutil, 'Process', forbidden)
    monkeypatch.setattr(mod.psutil, 'virtual_memory', forbidden)
    monkeypatch.setattr(mod.shutil, 'disk_usage', forbidden)


def sample():
    rss = 10 * MIB
    return mod.ProcessSample({os.getpid(): (1., 0, rss)},
                             rss, rss, 10 * 1024**3, 10 * 1024**3)


@contextmanager
def reservation():
    deadline = time.monotonic() + 5.
    settings = SimpleNamespace(data_dir='.', process_memory_limit_bytes=6 * 1024**3,
                               disk_reserve_bytes=0)
    permit = mod.NativeExecutionPermit(settings, sampler=lambda _: sample())
    event, callbacks = threading.Event(), []
    token = {'cancelled': event, 'work_deadline': deadline}
    callback = lambda: callbacks.append(event.is_set())
    assert permit.request_automatic_turn()
    turn = permit.automatic_turn()
    assert turn.__enter__()
    try:
        permit.reserve_optional(token, event, callback, owner_kind='automatic_primary_probe',
                                work_deadline=deadline)
        yield SimpleNamespace(permit=permit, token=token, event=event,
            callback=callback, callbacks=callbacks, deadline=deadline,
            remaining=lambda: max(0., deadline - time.monotonic()))
        assert time.monotonic() < deadline
    finally:
        turn.__exit__(None, None, None)
        permit.release_optional(token, cleaned=True)
        assert permit._optional is None and permit._sample_flight is None


def manager_and_worker(case):
    manager = jobs.MossJobs.__new__(jobs.MossJobs)
    manager.permit = case.permit
    manager._lock = threading.RLock()
    manager._history_enabled = manager._links_enabled = lambda: True
    manager._history_comparison_enabled = lambda: True
    manager._history_maintenance = manager._history_comparison = None
    # Due through the ordinary timer, with no previous comparison owner.
    manager._history_comparison_pending = False
    manager._history_compare_after = 0.
    forbidden_calls, storage = [], []

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError('No comparison thread, DB, primary dispatch, or step')

    manager._thread_factory = forbidden
    manager.history = SimpleNamespace(dispatch_current=lambda *args,**kwargs: True, finish_comparison=forbidden)
    probe = case.token
    probe.update(owner=manager, state='running', admitted=True, crop=object(),
        reservation={'token': 'fixed'}, invocation_id='fixed', node={}, operation=None,
        failure_frozen=False, thread_start_attempted=True, read_defer_deadline=None)
    probe['dispatch_binding'] = (probe['reservation'], probe['crop'], probe['invocation_id'],
                                probe['node'], tuple(sorted(probe['reservation'].items())))
    manager._primary_pending = probe
    manager._primary_guard = lambda record: nullcontext()
    automatic = SimpleNamespace(jobs=manager,
        maintain_text_storage=lambda **kwargs: storage.append(kwargs['stop']()),
        primary_yield_due=forbidden, step=forbidden)
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(_native_execution_permit=case.permit)
    worker.stop_event = threading.Event()
    worker.moss_automatic = automatic
    return manager, worker, forbidden_calls, storage


def assert_exact_reservation(case, *, cancelled):
    permit = case.permit
    assert permit._optional is case.token
    assert permit._optional_kind == 'automatic_primary_probe'
    assert permit._cancel is case.event and permit._cancel_callback is case.callback
    assert case.event.is_set() is cancelled and not case.callbacks
    assert case.token['work_deadline'] == case.deadline
    assert permit._completed_sample_retries == 0
    assert permit._unavailable is None and not permit._sampling_pending


@pytest.mark.parametrize('phase', [
    'dispatch_before_input', 'operation_before_input', 'before_spawn', 'before_admission',
])
def test_worker_maintenance_cannot_mutate_held_primary_census(phase):
    with reservation() as case:
        permit = case.permit
        manager, worker, forbidden, storage = manager_and_worker(case)
        gate = jobs._SpeakerProbeGate(manager, case.token)
        driver = object.__new__(runner.ProbeDriver)
        driver._service_gate = gate
        entered, resume = threading.Event(), threading.Event()
        calls, outcomes, maintenance, admissions = [], [], [], []
        generation = permit._sample_generation
        def controlled(_):
            assert not permit._condition._is_owned() and not manager._lock._is_owned()
            calls.append(threading.get_ident()); entered.set()
            assert resume.wait(case.remaining())
            return sample()
        def checking():
            try:
                if phase == 'dispatch_before_input':manager._primary_dispatch_current(case.token,reserved=True)
                elif phase == 'operation_before_input':gate.check('before_input')
                else:driver._check_start_gate(phase,case.event)
            except BaseException as error:outcomes.append(error)
            else:outcomes.append(None)
        real_maintain, real_reserve = manager.maintain_history_comparison, permit.reserve_comparison
        def maintain():
            answer = real_maintain(); maintenance.append(answer); return answer
        def reserve(token,event,**kwargs):
            try:return real_reserve(token,event,**kwargs)
            except BaseException as error:
                admissions.append((token,error,permit._comparison,permit._sample_generation));raise
        permit._sampler = controlled
        thread = threading.Thread(target=checking)
        with patch.object(manager,'maintain_history_comparison',side_effect=maintain), \
                patch.object(permit,'reserve_comparison',side_effect=reserve):
            thread.start()
            try:
                assert entered.wait(case.remaining())
                now = time.monotonic()
                with patch.object(jobs,'time',SimpleNamespace(monotonic=lambda:now)):
                    worker._automatic_handoff(request=False)
                assert maintenance == [False,True] and storage == [False]
                assert len(admissions) == 1
                token,error,comparison,after = admissions[0]
                assert permit.comparison_start_pending(error,token)
                assert comparison is None and after is generation
                assert not forbidden and calls == [thread.ident]
                assert manager._history_comparison is None and manager._history_maintenance is None
            finally:
                resume.set();thread.join(case.remaining())
        assert not thread.is_alive() and outcomes == [None]
        assert permit._sample_generation is generation and permit._sample_flight is None
        assert_exact_reservation(case,cancelled=False)


@pytest.mark.parametrize('timing',['released_before','released_during','held_to_cutoff'])
def test_observer_overlap_uses_one_separate_fresh_census(timing):
    with reservation() as case:
        permit = case.permit
        entered,release,waiting = threading.Event(),threading.Event(),threading.Event()
        calls,observations,outcomes = [],[],[]
        original_wait = permit._condition.wait
        def wait(seconds):
            waiting.set();return original_wait(seconds)
        def sampler(_):
            assert not permit._condition._is_owned()
            current = sample();calls.append((threading.get_ident(),current))
            if len(calls)==1:
                entered.set();assert release.wait(case.remaining())
            else:assert len(calls)==2 and current is not calls[0][1]
            return current
        def observe():
            try:permit.observe_primary()
            except BaseException as error:observations.append(error)
            else:observations.append(None)
        def check():
            try:permit.check_optional(case.token,'running')
            except BaseException as error:outcomes.append(error)
            else:outcomes.append(None)
        permit._sampler = sampler
        observer = threading.Thread(target=observe)
        checker = threading.Thread(target=check)
        with patch.object(permit._condition,'wait',side_effect=wait):
            observer.start()
            try:
                assert entered.wait(case.remaining())
                if timing=='released_before':release.set();observer.join(case.remaining())
                checker.start()
                if timing=='released_during':
                    assert waiting.wait(case.remaining());release.set()
                checker.join(case.remaining())
                assert not checker.is_alive()
                if timing=='held_to_cutoff':
                    assert len(calls)==1 and len(outcomes)==1
                    error=outcomes[0];detail=mod.optional_sample_failure(error)
                    assert type(error) is mod.AdmissionError
                    assert detail['subtype']=='busy_flight' and detail['grace_used']
                    assert 0 <= detail['wait_ms'] <= 250
                else:
                    assert outcomes==[None] and len(calls)==2
                    assert calls[1][0]==checker.ident
            finally:
                release.set();observer.join(case.remaining())
                if checker.ident is not None:checker.join(case.remaining())
        assert not observer.is_alive() and observations==[None]
        assert_exact_reservation(case,cancelled=timing=='held_to_cutoff')
        assert permit._sample_flight is None


def comparison(permit):
    grant,event=object(),threading.Event()
    permit.reserve_comparison(grant,event)
    return grant,event,permit._comparison


def test_probe_admission_census_blocks_comparison_reserve_and_start():
    permit=mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,
        disk_reserve_bytes=0),sampler=lambda _:sample())
    grant,_,record=comparison(permit)
    generation,revision=permit._sample_generation,record['revision']
    token={'work_deadline':time.monotonic()+5.};event=token['cancelled']=threading.Event()
    calls=[]
    def census(_):
        assert not permit._condition._is_owned()
        assert permit._primary_probe_admission is not None and permit._optional is None
        with pytest.raises(mod.AdmissionError) as caught:permit.begin_comparison_start(grant)
        assert permit.comparison_start_pending(caught.value,grant)
        assert permit._sample_generation is generation and record['revision'] is revision
        calls.append(1);return sample()
    permit._sampler=census
    permit.reserve_optional(token,event,event.set,owner_kind='automatic_primary_probe',work_deadline=token['work_deadline'])
    assert calls==[1] and permit._primary_probe_admission is None
    assert permit.status()['comparison_reserved_bytes']==192*1024**2
    with pytest.raises(mod.AdmissionError):permit.begin_comparison_start(grant)
    permit.release_optional(token,cleaned=True)
    claim=permit.begin_comparison_start(grant)
    assert permit.end_comparison_start(grant,claim,'resource_free')
    assert permit.release_comparison(grant)


def test_new_comparison_loses_to_probe_admission_token_without_any_mutation():
    with reservation() as case:
        case.permit.release_optional(case.token,cleaned=True)
        generation=case.permit._sample_generation
        def census(_):
            grant=object()
            with pytest.raises(mod.AdmissionError) as caught:case.permit.reserve_comparison(grant,threading.Event())
            assert case.permit.comparison_start_pending(caught.value,grant)
            assert case.permit._comparison is None and case.permit._sample_generation is generation
            return sample()
        case.permit._sampler=census
        case.permit.reserve_optional(case.token,case.event,case.callback,
            owner_kind='automatic_primary_probe',work_deadline=case.deadline)
        assert case.permit._primary_probe_admission is None


def test_old_admission_finally_cannot_clear_replacement_token():
    with reservation() as case:
        permit=case.permit;permit.release_optional(case.token,cleaned=True);replacement=object()
        def census(_):permit._primary_probe_admission=replacement;return sample()
        permit._sampler=census
        with pytest.raises(mod.AdmissionError,match='optional_ownership_changed'):
            permit.reserve_optional(case.token,case.event,case.callback,
                owner_kind='automatic_primary_probe',work_deadline=case.deadline)
        assert permit._optional is None and permit._primary_probe_admission is replacement
        permit._primary_probe_admission=None


@pytest.mark.parametrize('change',['cancel','deadline','event','callback','config_identity','config_value',
    'generation','owner','shutdown','native_granted','sticky','sampling_pending','comparison_revision'])
def test_wait_revalidates_every_authority_without_second_census(change):
    with reservation() as case:
        permit=case.permit;calls=[];permit._sample_flight=object()
        old_event=case.event
        def wait(_):
            if change=='cancel':case.event.set()
            elif change=='deadline':case.token['work_deadline']=time.monotonic()-1.
            elif change=='event':permit._cancel=threading.Event()
            elif change=='callback':permit._cancel_callback=lambda:None
            elif change=='config_identity':permit._configuration_current=lambda:True
            elif change=='config_value':permit._configuration_current=lambda:False
            elif change=='generation':permit._sample_generation=object()
            elif change=='owner':permit._optional=object()
            elif change=='shutdown':permit.close_admission()
            elif change=='native_granted':permit.primary_intent('worker').granted=True
            elif change=='sticky':permit.mark_unavailable('sticky_fixture')
            elif change=='sampling_pending':
                permit._sampling_pending=True;permit._unavailable='primary_sampling_unavailable'
            else:permit._comparison={'revision':object(),'bytes':0,'child':None}
            permit._sample_flight=None
        permit._sampler=lambda _:calls.append(1) or sample()
        with patch.object(permit._condition,'wait',side_effect=wait),pytest.raises(mod.AdmissionError):
            permit.check_optional(case.token,'before_spawn')
        assert not calls and permit._sample_flight is None
        if change=='owner':
            assert not old_event.is_set();permit._optional=case.token
        else:assert permit._cancel.is_set()
        if change=='comparison_revision':permit._comparison=None
        for intent in list(permit._primary.values()):intent.release()


@pytest.mark.parametrize('failure',['memory','host','disk','envelope','service_envelope','unclassified','stale','identity'])
def test_fresh_after_wait_preserves_before_spawn_resource_and_generation_refusals(failure):
    with reservation() as case:
        permit=case.permit;permit._sample_flight=object();calls=[]
        def wait(_):permit._sample_flight=None
        def census(_):
            calls.append(1)
            if failure=='identity':raise mod.AdmissionError('process_identity_uncertain')
            if failure=='stale':permit._sample_generation=object()
            if failure=='service_envelope':permit.settings.process_memory_limit_bytes=3*1024**3
            rss=7*1024**3 if failure=='memory' else 10*MIB
            available=1 if failure=='host' else 3*1024**3 if failure=='envelope' else 10*1024**3
            identities={os.getpid():(1.,0,rss)}
            if failure=='unclassified':identities[12345]=(2.,os.getpid(),10)
            return mod.ProcessSample(identities,rss,rss,available,1 if failure=='disk' else 10*1024**3)
        permit._sampler=census
        with patch.object(permit._condition,'wait',side_effect=wait),pytest.raises(mod.AdmissionError) as caught:
            permit.check_optional(case.token,'before_spawn')
        assert calls==[1] and case.event.is_set()
        if failure=='stale':assert mod.optional_sample_failure(caught.value)['subtype']=='stale_generation'
        if failure=='unclassified':assert caught.value.reason=='unclassified_service_child'
        if failure=='envelope':assert caught.value.reason=='service_host_envelope'
        if failure=='service_envelope':assert caught.value.reason=='service_envelope_budget'
        assert case.token['work_deadline']==case.deadline


def test_wrong_owner_and_missing_explicit_deadline_never_wait():
    with reservation() as case:
        permit=case.permit;permit._sample_flight=object();calls=[]
        permit._sampler=lambda _:calls.append(1) or sample()
        with patch.object(permit._condition,'wait',side_effect=AssertionError('unexpected wait')):
            with pytest.raises(mod.AdmissionError):permit.check_optional(object())
            assert not case.event.is_set()
            permit._optional_work_deadline=None
            with pytest.raises(mod.AdmissionError):permit.check_optional(case.token)
        assert not calls and case.event.is_set()
        permit._sample_flight=None

@pytest.fixture
def pending_case(monkeypatch):
    from audio_backend import recording_primary_probe as source
    owner=object.__new__(jobs.MossJobs)
    owner._lock=threading.RLock();owner._live=None
    state=SimpleNamespace(configured=True,fences=[],fail_exit=False,starts=[],failures=[],maintenance=[])
    owner._history_enabled=lambda:state.configured
    owner.automatic_idle=lambda:True
    owner.cell_source=SimpleNamespace(owner=SimpleNamespace(_publication_guard=threading.RLock()))
    def fence(cell_source,saved):
        assert cell_source is owner.cell_source
        state.fences.append(saved)
        if state.fail_exit and len(state.fences)%2==0:
            from audio_backend.native_owned_support_cache import ReadError
            raise ReadError(409,'primary_probe_source_stale')
    monkeypatch.setattr(source,'_check_fence',fence)
    permit=owner.permit=mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',
        process_memory_limit_bytes=6*1024**3,disk_reserve_bytes=0),sampler=lambda _:sample())
    grant,_,_=comparison(permit);claim=permit.begin_comparison_start(grant)
    deadline=time.monotonic()+5.
    probe={'owner':owner,'state':'pending','admitted':True,'cancelled':threading.Event(),
        'crop':object(),'reservation':{'token':'fixed'},'invocation_id':'fixed','node':{},
        'thread':None,'operation':None,'thread_start_attempted':False,'completed':threading.Event(),
        'failure_json':None,'failure_frozen':False,'terminal_reason':None,'work_deadline':deadline,
        'read_defer_deadline':None,'fence':object()}
    probe['dispatch_binding']=(probe['reservation'],probe['crop'],probe['invocation_id'],probe['node'],
        tuple(sorted(probe['reservation'].items())))
    owner._primary_pending=probe
    owner._primary_dispatch_current=lambda record,**kwargs:True
    owner._observe_probe_failure=lambda *args:state.failures.append(args)
    owner._maintain_primary=lambda:state.maintenance.append(True)
    class Thread:
        def __init__(self,**kwargs):self.kwargs=kwargs;self.started=False
        def start(self):self.started=True;state.starts.append(self)
        def is_alive(self):return self.started
    owner._thread_factory=Thread
    state.owner,state.permit,state.probe,state.grant,state.claim=owner,permit,probe,grant,claim
    yield state
    permit.release_optional(probe,cleaned=True)
    if permit._comparison is not None and permit._comparison['start_claim'] is claim:
        assert permit.end_comparison_start(grant,claim,'resource_free')
    assert permit.release_comparison(grant)


def test_start_claim_defers_same_probe_then_admits_once_after_settlement(pending_case):
    case=pending_case;probe=case.probe;binding=probe['dispatch_binding'];deadline=probe['work_deadline']
    assert case.owner.submit_primary() is False
    assert probe['state']=='pending' and probe['dispatch_binding'] is binding
    assert probe['work_deadline']==deadline and probe['crop'] is binding[1]
    assert len(case.fences)==2 and not case.starts and not case.failures and not case.maintenance
    assert case.permit._optional is None and probe['thread'] is None and not probe['thread_start_attempted']
    assert case.permit.end_comparison_start(case.grant,case.claim,'resource_free')
    assert case.owner.submit_primary() is True
    assert len(case.starts)==1 and probe['state']=='running'
    assert probe['work_deadline']==deadline and probe['dispatch_binding'] is binding
    assert case.permit._optional is probe and case.permit._optional_work_deadline==deadline
    assert case.owner.submit_primary() is False and len(case.starts)==1


def test_pending_admission_must_pass_the_source_guard_exit(pending_case):
    case=pending_case;case.fail_exit=True
    assert case.owner.submit_primary() is False
    assert len(case.fences)==2 and case.probe['state']=='finish_pending'
    assert len(case.failures)==1 and case.maintenance==[True] and not case.starts
    assert case.permit._optional is None


@pytest.mark.parametrize('change',['event','configuration','cancel','deadline','binding','crop',
    'pending_owner','operation','thread','thread_start','claim'])
def test_pending_marker_cannot_survive_authority_or_dispatch_changes(pending_case,change):
    case=pending_case;probe=case.probe;original=case.permit.reserve_optional
    def reserve(*args,**kwargs):
        try:return original(*args,**kwargs)
        except mod.AdmissionError:
            if change=='event':probe['cancelled']=threading.Event()
            elif change=='configuration':case.permit._configuration_current=lambda:True
            elif change=='cancel':probe['cancelled'].set()
            elif change=='deadline':probe['work_deadline']+=1.
            elif change=='binding':probe['dispatch_binding']=tuple(list(probe['dispatch_binding']))
            elif change=='crop':probe['crop']=object()
            elif change=='pending_owner':case.owner._primary_pending=object()
            elif change=='operation':probe['operation']=object()
            elif change=='thread':probe['thread']=object()
            elif change=='thread_start':probe['thread_start_attempted']=True
            else:case.permit._comparison['start_claim']=object()
            raise
    with patch.object(case.permit,'reserve_optional',side_effect=reserve):
        assert case.owner.submit_primary() is False
    assert probe['state'] in ('finish_pending','thread_unknown') and not case.starts
    assert case.permit._optional is None
    if change=='claim':case.permit._comparison['start_claim']=case.claim


@pytest.mark.parametrize('error_kind',['plain','subclass','forged','storage'])
def test_unproven_admission_refusal_is_terminal(pending_case,error_kind):
    from audio_backend.native_owned_support_cache import ReadError
    case=pending_case
    class Subclass(mod.AdmissionError):pass
    error=(ReadError(503,'result_storage_unavailable') if error_kind=='storage'
        else Subclass('comparison_start_pending') if error_kind=='subclass'
        else mod.AdmissionError('comparison_start_pending'))
    if error_kind=='forged':error._optional_admission_pending=(object(),case.permit,case.probe)
    with patch.object(case.permit,'reserve_optional',side_effect=error):
        assert case.owner.submit_primary() is False
    assert case.probe['state']=='finish_pending' and len(case.failures)==1
    assert not case.starts and case.permit._optional is None


class SyntheticProcess:
    def __init__(self):self.pid=os.getpid()+100000;self.exited=False
    def poll(self):return 0 if self.exited else None
    def wait(self,timeout):
        assert timeout==0
        if not self.exited:raise AssertionError('synthetic original not positively exited')
        return 0


@pytest.fixture
def comparison_case(monkeypatch):
    from audio_backend import recording_speaker_history_process as process_module
    permit=mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',
        process_memory_limit_bytes=6*1024**3,disk_reserve_bytes=0),sampler=lambda _:sample())
    grant,event,permit_record=comparison(permit)
    manager=object.__new__(jobs.MossJobs);manager.permit=permit;manager._lock=threading.RLock()
    state=SimpleNamespace(configured=True,constructed=[],starts=[],begins=[],closes=[],before_bind=None,
        before_start=None,fail_close=False,raise_start=False,current_reads=[])
    manager._history_comparison_enabled=lambda:state.configured
    snapshot={'token':object()}
    manager.history=SimpleNamespace(comparison_current=lambda value:state.current_reads.append(value) or 'current')
    now=time.monotonic()
    record={'grant':grant,'cancelled':event,'driver':None,'driver_active':False,'budget_class':'cold',
        'deadline':now+5.,'admission_start':now,'task_sequence':0,'budget_cache_binding':None,
        'snapshot':snapshot,'state':'starting','reason':None}
    def lookup(pid):
        driver=record['driver']
        assert driver is not None and driver.process.pid==pid
        if driver.process.exited:raise mod.psutil.NoSuchProcess(pid)
        return SimpleNamespace(create_time=lambda:2.,ppid=lambda:os.getpid())
    monkeypatch.setattr(mod.psutil,'Process',lookup)
    class Driver:
        def __init__(self,**callbacks):
            self.callbacks=callbacks;self.process=None;self.closed=True;self.cache_binding=None;self.positive=False
            state.constructed.append(self)
        def comparison_parent_bytes(self):return 0
        def comparison_resources_closed(self):return self.closed
        def start(self,deadline):
            assert not manager._lock._is_owned() and not permit._condition._is_owned()
            state.starts.append((self,deadline));self.closed=False;self.process=SyntheticProcess()
            if state.before_bind:state.before_bind(self)
            if self.callbacks['bind_child'](self.process) is not True:raise RuntimeError('synthetic_bind_failed')
            if state.raise_start:raise RuntimeError('synthetic_start_failed')
            return {'ready':True}
        def begin(self,token,**kwargs):state.begins.append((self,token,kwargs))
        def rebase(self,token):raise AssertionError('No second task or rebase')
        def close(self):
            assert not manager._lock._is_owned() and not permit._condition._is_owned()
            state.closes.append(self)
            if self.positive:return {'positive':True}
            if state.fail_close:return {'positive':False}
            self.closed=True
            if self.process is None:
                self.positive=True;return {'positive':True,'no_child':True}
            self.process.exited=True
            positive=self.callbacks['finish_child'](self.process,deadline=time.monotonic()+1.)
            self.positive=positive
            return {'positive':positive,'positive_wait':True}
    monkeypatch.setattr(process_module,'ComparisonProcess',Driver)
    state.permit,state.manager,state.record,state.permit_record=permit,manager,record,permit_record
    yield state
    if record['driver'] is not None:
        state.fail_close=False
        # Tests replacing the claim restore it before teardown.
        assert manager._close_history_comparison_driver(record)
    elif record.get('driver_start_claim') is not None:
        assert manager._close_history_comparison_driver(record)
    assert permit.release_comparison(grant)


def test_readiness_race_waits_in_same_comparison_call_without_new_budget(comparison_case):
    case=comparison_case;permit,record=case.permit,case.record
    token={'work_deadline':time.monotonic()+4.,'cancelled':threading.Event()}
    original_begin=permit.begin_comparison_start;claims=[];waits=[]
    saved=(record['snapshot'],record['deadline'],record['admission_start'],record['task_sequence'])
    def begin(grant):
        claims.append(grant)
        if len(claims)==1:
            permit.reserve_optional(token,token['cancelled'],token['cancelled'].set,
                owner_kind='automatic_primary_probe',work_deadline=token['work_deadline'])
        return original_begin(grant)
    def wait(seconds):
        assert 0 < seconds <= .05
        assert not permit._condition._is_owned() and not case.manager._lock._is_owned()
        assert record['driver'] is None and not case.closes
        assert record['snapshot'] is saved[0] and (record['deadline'],record['admission_start'],record['task_sequence'])==saved[1:]
        assert permit.status()['comparison_reserved_bytes']==192*1024**2
        waits.append(seconds);permit.release_optional(token,cleaned=True)
    with patch.object(permit,'begin_comparison_start',side_effect=begin), \
            patch.object(record['cancelled'],'wait',side_effect=wait), \
            patch.object(permit,'resume_comparison',side_effect=AssertionError('No scheduling census poll')):
        driver=case.manager._history_comparison_driver(record,record['snapshot']['token'])
    assert driver is record['driver'] and len(claims)==2 and len(waits)==1
    assert len(case.constructed)==len(case.starts)==len(case.begins)==1 and not case.closes
    assert record['task_sequence']==1 and record['snapshot'] is saved[0]
    assert (record['deadline'],record['admission_start'])==saved[1:3]
    assert case.permit_record['start_claim'] is None


@pytest.mark.parametrize('terminal',['cancel','deadline','configuration'])
def test_comparison_start_pending_terminal_exit_keeps_original_deadline(comparison_case,terminal):
    case=comparison_case;record=case.record
    token={'work_deadline':time.monotonic()+4.,'cancelled':threading.Event()}
    case.permit.reserve_optional(token,token['cancelled'],token['cancelled'].set,
        owner_kind='automatic_primary_probe',work_deadline=token['work_deadline'])
    deadline=record['deadline'];now=[time.monotonic()]
    def wait(seconds):
        if terminal=='cancel':record['cancelled'].set()
        elif terminal=='configuration':case.configured=False
        else:now[0]=deadline
    try:
        with patch.object(record['cancelled'],'wait',side_effect=wait), \
                patch.object(jobs,'time',SimpleNamespace(monotonic=lambda:now[0])), \
                patch.object(case.permit,'resume_comparison',side_effect=AssertionError('No census poll')):
            assert case.manager._history_comparison_driver(record,record['snapshot']['token']) is None
        assert record['deadline']==deadline and record['task_sequence']==0
        assert not case.constructed and case.permit_record['start_claim'] is None
    finally:case.permit.release_optional(token,cleaned=True)


@pytest.mark.parametrize('race',['cancel','shutdown','success','stale_claim'])
def test_possible_child_is_retained_before_refusal_and_positive_cleanup(comparison_case,race):
    case=comparison_case;permit,record=case.permit,case.record;pending=[]
    token={'work_deadline':time.monotonic()+4.,'cancelled':threading.Event()}
    def before_bind(driver):
        assert driver.process is not None and case.permit_record['child'] is None
        with pytest.raises(mod.AdmissionError) as caught:
            permit.reserve_optional(token,token['cancelled'],token['cancelled'].set,
                owner_kind='automatic_primary_probe',work_deadline=token['work_deadline'])
        assert permit.primary_probe_admission_pending(caught.value,token,work_deadline=token['work_deadline'])
        pending.append(caught.value)
        assert permit._optional is None and not permit.release_comparison(record['grant'])
        if race=='cancel':record['cancelled'].set()
        elif race=='shutdown':permit.close_admission()
        elif race=='stale_claim':case.permit_record['start_claim']=object()
    case.before_bind=before_bind
    if race=='success':
        assert case.manager._history_comparison_driver(record,record['snapshot']['token']) is record['driver']
    else:
        with pytest.raises(RuntimeError):case.manager._history_comparison_driver(record,record['snapshot']['token'])
    assert len(case.starts)==1 and len(pending)==1
    child=case.permit_record['child']
    assert child['proc'] is record['driver'].process and child['owner'] is record['driver']
    assert permit.status()['comparison_reserved_bytes']==192*1024**2
    if race=='stale_claim':
        replacement=case.permit_record['start_claim']
        assert not permit.end_comparison_start(record['grant'],record['driver_start_claim'],'closed',owner=record['driver'])
        assert case.permit_record['start_claim'] is replacement
        case.permit_record['start_claim']=record['driver_start_claim']
    case.fail_close=True
    assert case.manager._close_history_comparison_driver(record) is False
    assert permit.status()['comparison_reserved_bytes']==192*1024**2
    if race!='success':assert case.permit_record['start_claim'] is record['driver_start_claim']
    case.fail_close=False
    assert case.manager._close_history_comparison_driver(record) is True
    assert case.permit_record['start_claim'] is None and child['closed']
    assert len(case.starts)==1

@pytest.mark.parametrize('change',['cancel','unavailable','configuration','claim'])
def test_prestart_revalidation_prevents_new_start_after_binding(comparison_case,change):
    case=comparison_case;permit,record=case.permit,case.record;original=permit.bind_comparison_owner
    def bind(*args,**kwargs):
        original(*args,**kwargs)
        if change=='cancel':record['cancelled'].set()
        elif change=='unavailable':permit.mark_unavailable('fixture_revoked')
        elif change=='configuration':permit._configuration_current=lambda:False
        else:case.permit_record['start_claim']=object()
    with patch.object(permit,'bind_comparison_owner',side_effect=bind),pytest.raises(Exception):
        case.manager._history_comparison_driver(record,record['snapshot']['token'])
    assert len(case.constructed)==1 and not case.starts and not case.begins
    assert record['driver'] is case.constructed[0] and case.permit_record['owner'] is record['driver']
    assert permit.status()['comparison_reserved_bytes']==192*1024**2
    if change=='claim':
        replacement=case.permit_record['start_claim']
        assert case.manager._close_history_comparison_driver(record) is False
        assert case.permit_record['start_claim'] is replacement
        case.permit_record['start_claim']=record['driver_start_claim']
    assert case.manager._close_history_comparison_driver(record)


def test_foreign_claim_grant_and_owner_never_erase_or_rebind_original(comparison_case):
    case=comparison_case;permit,record=case.permit,case.record
    case.manager._history_comparison_driver(record,record['snapshot']['token'])
    driver=record['driver'];child=case.permit_record['child'];generation=permit._sample_generation
    for grant,owner,claim in ((object(),driver,None),(record['grant'],object(),None),
                             (record['grant'],driver,object())):
        with pytest.raises(mod.AdmissionError):
            permit.bind_comparison_process(grant,driver.process,owner=owner,claim=claim)
        assert case.permit_record['child'] is child and permit._sample_generation is generation
    assert not permit.end_comparison_start(record['grant'],object(),'closed',owner=driver)
    assert not permit.release_comparison(object())
    assert child['proc'] is driver.process and not child['closed']


def test_real_positive_comparison_release_still_invalidates_primary_census(comparison_case):
    case=comparison_case;permit,record=case.permit,case.record
    case.manager._history_comparison_driver(record,record['snapshot']['token'])
    assert case.manager._close_history_comparison_driver(record)
    assert case.permit_record['child']['closed'] and case.permit_record['start_claim'] is None
    deadline=time.monotonic()+5.;event=threading.Event()
    token={'cancelled':event,'work_deadline':deadline}
    callback=lambda:None
    permit.reserve_optional(token,event,callback,owner_kind='automatic_primary_probe',work_deadline=deadline)
    primary=SimpleNamespace(permit=permit,token=token,event=event,callback=callback,callbacks=[],deadline=deadline)
    manager,_,_,_=manager_and_worker(primary);reads=[];calls=[]
    manager.history.dispatch_current=lambda *args,**kwargs:reads.append(True) or True
    generation,observed=permit._sample_generation,dict(permit._observed)
    def census(_):
        calls.append(1)
        assert permit.release_comparison(record['grant']) is True
        assert permit._sample_generation is not generation
        return sample()
    permit._sampler=census
    try:
        with pytest.raises(mod.AdmissionError,match='process_sample_unavailable') as caught:
            manager._primary_dispatch_current(token,reserved=True)
        detail=mod.optional_sample_failure(caught.value)
        assert detail['subtype']=='stale_generation' and detail['wait_ms']==0 and not detail['grace_used']
        assert calls==[1] and not reads and event.is_set()
        assert permit._observed==observed and permit._optional is token and token['operation'] is None
        assert token['work_deadline']==deadline and permit._sample_flight is None
    finally:permit.release_optional(token,cleaned=True)


@pytest.mark.parametrize('case',['expired_before','expired_at_entry','clipped','wall_equal','wall_overshoot','rows'])
def test_probe_deadline_is_distinct_from_genuine_sampler_budget(monkeypatch,case):
    permit=mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,
        disk_reserve_bytes=0),sampler=lambda _:sample())
    event=threading.Event();token={'cancelled':event,'work_deadline':.1}
    monkeypatch.setattr(mod,'time',SimpleNamespace(monotonic=lambda:0.))
    permit.reserve_optional(token,event,event.set,owner_kind='automatic_primary_probe',work_deadline=.1)
    clocks=[];enumerations=[]
    def clock():
        clocks.append(1)
        if case=='expired_at_entry':return 1.
        if case=='rows':return 0.
        if case=='wall_equal':return 0. if len(clocks)==1 else .25
        if case=='wall_overshoot':return 0. if len(clocks)==1 else .251
        return 0. if len(clocks)<3 else .101
    caller=SimpleNamespace(pid=os.getpid(),create_time=lambda:1.)
    monkeypatch.setattr(mod.psutil,'Process',lambda pid:caller)
    def process_iter(attrs):
        enumerations.append(attrs)
        count=mod.MAX_ROWS+1 if case=='rows' else 2
        return (SimpleNamespace(info={'pid':os.getpid()+i,'ppid':0}) for i in range(count))
    monkeypatch.setattr(mod.psutil,'process_iter',process_iter)
    # Keep the canonical function object and only supply its deterministic
    # budget clock; no custom sampler acquires caller-expiry authentication.
    monkeypatch.setattr(mod.service_sample,'__kwdefaults__',{**mod.service_sample.__kwdefaults__,'clock':clock})
    permit._sampler=mod.service_sample
    previous={'operation':'previous_fixture'};permit._primary_observation_failure=previous
    if case=='expired_before':monkeypatch.setattr(mod,'time',SimpleNamespace(monotonic=lambda:.1))
    try:
        with pytest.raises(mod.AdmissionError) as caught:permit.check_optional(token,'before_spawn')
        error=caught.value
        assert event.is_set() and permit._optional is token and token['work_deadline']==.1
        assert permit._sample_flight is None and permit._primary_observation_failure is previous
        if case in ('expired_before','expired_at_entry','clipped'):
            assert error.reason=='optional_cancelled' and error._optional_execution_deadline is True
            assert not permit._sampling_pending and permit._unavailable is None
            assert mod.optional_sample_failure(error)['subtype']=='deadline'
            assert mod._authenticated_probe_deadline(error) is (case!='expired_before')
        else:
            assert error.reason=='process_sample_budget' and permit.sampling_recovery_pending
            assert mod._authenticated_exhaustion(error)[0]==('rows' if case=='rows' else 'wall')
            assert not mod._authenticated_probe_deadline(error)
        if case=='expired_before':assert not clocks and not enumerations
        else:assert len(enumerations)==1
    finally:permit.release_optional(token,cleaned=True)


def test_identity_failure_coincident_with_expiry_remains_original_failure(monkeypatch):
    now=[0.]
    def clock():return now[0]
    def identity():now[0]=.101;raise mod.psutil.AccessDenied(os.getpid())
    monkeypatch.setattr(mod.psutil,'Process',lambda pid:SimpleNamespace(create_time=identity))
    with pytest.raises(mod.AdmissionError) as caught:
        mod.service_sample(SimpleNamespace(data_dir='.'),clock=clock,deadline=.1,primary_probe_deadline=True)
    error=caught.value
    assert error.reason=='process_sample_unavailable' and not mod._authenticated_probe_deadline(error)
    assert error._sample_observation['cause_class']=='AccessDenied'


@pytest.mark.parametrize('marker_kind',['flag','foreign','tuple_subclass','equality_object','exception_subclass','noncanonical'])
def test_forged_deadline_helpers_and_observer_context_cannot_exempt_failures(marker_kind):
    class TupleSubclass(tuple):pass
    class Equality:
        def __eq__(self,other):return True
    class ErrorSubclass(mod.AdmissionError):pass
    error=ErrorSubclass('optional_cancelled') if marker_kind=='exception_subclass' else mod.AdmissionError('optional_cancelled')
    error._optional_execution_deadline=True
    marker=(mod._OPTIONAL_SAMPLE,'automatic_primary_probe')
    if marker_kind=='foreign':marker=(object(),'automatic_primary_probe')
    elif marker_kind=='tuple_subclass':marker=TupleSubclass(marker)
    elif marker_kind=='equality_object':marker=Equality()
    if marker_kind!='flag':error._optional_sample_deadline=marker
    if marker_kind!='noncanonical':assert mod._authenticated_probe_deadline(error) is False
    permit=mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,
        disk_reserve_bytes=0),sampler=lambda _:(_ for _ in ()).throw(error))
    with pytest.raises(mod.AdmissionError):
        permit._take_sample(observe=True,primary_probe_deadline=True,sample_deadline=.1)
    assert permit._unavailable=='primary_ownership_uncertain' and permit._primary_observation_failure is not None
    assert permit._sample_flight is None


def test_existing_sticky_sampling_failure_is_not_cleared_by_probe_expiry():
    with reservation() as case:
        permit=case.permit
        prior={'kind':'wall'}
        permit._sampling_pending=True;permit._sampling_failure=prior
        permit._unavailable='primary_sampling_unavailable'
        permit._sampler=lambda _:(_ for _ in ()).throw(AssertionError('No census while sticky'))
        with pytest.raises(mod.AdmissionError):permit.check_optional(case.token,'running')
        assert permit._sampling_pending and permit._sampling_failure is prior
        assert permit._unavailable=='primary_sampling_unavailable' and case.event.is_set()

@pytest.mark.parametrize('cutoff',['work','grace'])
def test_busy_wait_uses_one_original_cutoff_and_never_retries_census(monkeypatch,cutoff):
    now=[10.];waits=[];calls=[]
    monkeypatch.setattr(mod,'time',SimpleNamespace(monotonic=lambda:now[0]))
    permit=mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,
        disk_reserve_bytes=0),sampler=lambda _:sample())
    deadline=10.1 if cutoff=='work' else 10.5
    token={'work_deadline':deadline,'cancelled':threading.Event()}
    permit.reserve_optional(token,token['cancelled'],token['cancelled'].set,
        owner_kind='automatic_primary_probe',work_deadline=deadline)
    permit._sample_flight=object();permit._sampler=lambda _:calls.append(1) or sample()
    def wait(seconds):
        assert 0 < seconds <= .05
        waits.append(seconds);now[0]+=seconds
    with patch.object(permit._condition,'wait',side_effect=wait),pytest.raises(mod.AdmissionError) as caught:
        permit.check_optional(token,'running')
    assert not calls and token['cancelled'].is_set() and token['work_deadline']==deadline
    assert now[0]==pytest.approx(min(deadline,10.25)) and 1 <= len(waits) <= 6
    assert mod.optional_sample_failure(caught.value)['subtype']==('deadline' if cutoff=='work' else 'busy_flight')
    permit._sample_flight=None;permit.release_optional(token,cleaned=True)


@pytest.mark.parametrize('failure',['constructor','binding','invalid_source'])
def test_resource_free_comparison_failure_settles_only_its_claim(comparison_case,monkeypatch,failure):
    from audio_backend import recording_speaker_history_process as process_module
    case=comparison_case;record=case.record
    if failure=='constructor':
        def constructor(**kwargs):raise RuntimeError('synthetic_constructor_failure')
        monkeypatch.setattr(process_module,'ComparisonProcess',constructor)
    elif failure=='binding':
        monkeypatch.setattr(case.permit,'bind_comparison_owner',
            lambda *args,**kwargs:(_ for _ in ()).throw(mod.AdmissionError('comparison_ownership_changed')))
    else:case.manager.history.comparison_current=lambda snapshot:'invalid'
    if failure=='invalid_source':
        assert case.manager._history_comparison_driver(record,record['snapshot']['token']) is None
        assert record['cancelled'].is_set()
    else:
        with pytest.raises(RuntimeError):case.manager._history_comparison_driver(record,record['snapshot']['token'])
    assert not case.starts and not case.begins
    if failure=='binding':
        assert case.permit_record['start_claim'] is record['driver_start_claim']
        assert case.manager._close_history_comparison_driver(record)
    assert case.permit_record['start_claim'] is None
    assert case.permit.status()['comparison_reserved_bytes']==192*1024**2


def test_fresh_claim_is_already_owned_before_any_competing_sampler_can_run():
    with reservation() as case:
        permit=case.permit;permit._sample_flight=object();calls=[]
        def wait(seconds):permit._sample_flight=None
        def census(_):
            assert not permit._condition._is_owned() and permit._sample_flight is not None
            calls.append(1)
            with pytest.raises(mod._SampleBusy):permit._take_sample(observe=True)
            return sample()
        permit._sampler=census
        with patch.object(permit._condition,'wait',side_effect=wait):permit.check_optional(case.token,'before_spawn')
        assert calls==[1] and not case.event.is_set() and permit._sample_flight is None


def test_bound_stable_comparison_is_not_globally_paused_by_probe(comparison_case):
    case=comparison_case;record=case.record;permit=case.permit
    case.manager._history_comparison_driver(record,record['snapshot']['token'])
    token={'work_deadline':time.monotonic()+4.,'cancelled':threading.Event()}
    permit.reserve_optional(token,token['cancelled'],token['cancelled'].set,
        owner_kind='automatic_primary_probe',work_deadline=token['work_deadline'])
    try:
        with patch.object(record['cancelled'],'wait',side_effect=AssertionError('stable owner paused')), \
                patch.object(permit,'resume_comparison',side_effect=AssertionError('stable owner resampled')):
            assert case.manager._comparison_checkpoint(record,record['snapshot']) is True
        assert permit.comparison_grant_valid(record['grant'])
        assert permit.status()['comparison_reserved_bytes']==192*1024**2
    finally:permit.release_optional(token,cleaned=True)


def test_exact_candidate_origins_and_unchanged_probe_phase_wiring():
    import ast
    import hashlib
    import inspect
    from pathlib import Path
    import textwrap
    root=Path(__file__).resolve().parents[1]
    for module,expected in ((mod,'78c8226979b0a389a7d6d51c8267780d7f02d1d805b0a8f83a99c52dd843960c'),
                           (jobs,'018dcc688ff0e2cebe35398c1493617dace9afcaa0d25a9c9088252c4f4377fe')):
        path=Path(module.__file__).resolve()
        assert path==root/'audio_backend'/path.name
        assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
    assert runner.ProbeDriver.start is controller.OwnedDriver.start
    assert runner.ProbeDriver._check_start_gate is controller.OwnedDriver._check_start_gate
    def phases(function,method,index=0):
        tree=ast.parse(textwrap.dedent(inspect.getsource(function)))
        nodes=sorted((node for node in ast.walk(tree) if isinstance(node,ast.Call)
            and isinstance(node.func,ast.Attribute) and node.func.attr==method
            and len(node.args)>index and isinstance(node.args[index],ast.Constant)),key=lambda n:n.lineno)
        return [node.args[index].value for node in nodes]
    assert phases(jobs.MossJobs._primary_dispatch_current,'check_optional',1)==['before_input']
    assert phases(runner.ProbeOperation.run,'check')==['before_input']
    assert phases(runner.ProbeDriver.start,'_check_start_gate')==['before_spawn','before_admission']
    assert phases(runner.ProbeDriver.wait,'check')==['running']


@pytest.mark.parametrize('marker_kind',['flag','foreign','tuple_subclass','equality_object','exact_tag'])
def test_deadline_looking_markers_cannot_hide_canonical_nonobserver_wall_budget(monkeypatch,marker_kind):
    class TupleSubclass(tuple):pass
    class Equality:
        def __eq__(self,other):return True
    permit=mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',process_memory_limit_bytes=6*1024**3,
        disk_reserve_bytes=0),sampler=lambda _:sample())
    token={'work_deadline':.1,'cancelled':threading.Event()}
    monkeypatch.setattr(mod,'time',SimpleNamespace(monotonic=lambda:0.))
    permit.reserve_optional(token,token['cancelled'],token['cancelled'].set,
        owner_kind='automatic_primary_probe',work_deadline=.1)
    observations=[]
    def clock():observations.append(True);return 0. if len(observations)==1 else .251
    monkeypatch.setattr(mod.service_sample,'__kwdefaults__',{**mod.service_sample.__kwdefaults__,'clock':clock})
    monkeypatch.setattr(mod.psutil,'Process',lambda pid:SimpleNamespace(pid=pid,create_time=lambda:1.))
    monkeypatch.setattr(mod.psutil,'process_iter',lambda attrs:iter([SimpleNamespace(info={'pid':os.getpid(),'ppid':0})]))
    original=mod._sample_exhausted
    def exhausted(*args,**kwargs):
        error=original(*args,**kwargs);error._optional_execution_deadline=True
        marker=(mod._OPTIONAL_SAMPLE,'automatic_primary_probe')
        if marker_kind=='foreign':marker=(object(),'automatic_primary_probe')
        elif marker_kind=='tuple_subclass':marker=TupleSubclass(marker)
        elif marker_kind=='equality_object':marker=Equality()
        if marker_kind!='flag':error._optional_sample_deadline=marker
        return error
    monkeypatch.setattr(mod,'_sample_exhausted',exhausted)
    permit._sampler=mod.service_sample
    try:
        with pytest.raises(mod.AdmissionError) as caught:permit.check_optional(token,'running')
        assert caught.value.reason=='process_sample_budget'
        assert mod._authenticated_exhaustion(caught.value)[0]=='wall'
        assert not mod._authenticated_probe_deadline(caught.value)
        assert permit._sampling_pending and permit._unavailable=='primary_sampling_unavailable'
        assert permit._sampling_exhaustions==1 and permit._sample_flight is None
        assert token['cancelled'].is_set() and permit._optional is token
    finally:permit.release_optional(token,cleaned=True)
