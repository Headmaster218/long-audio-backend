"""Finite pre-dispatch read-contention contracts; no models or native children.

The small manager seam below fakes local authority and scheduling explicitly.
Its refusals are produced by the real zero-wait MossResultStore transaction,
using the existing numeric SQLite fault fixture. The two integration cases use
the genuine synthetic capture/Worker/history producer and existing fake model
edge; neither claims acoustic, resource, or throughput qualification.
"""
from contextlib import contextmanager
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from audio_backend import moss_handy_jobs as jobs
from audio_backend import moss_result_store as result_store
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_probe_runner as runner
from audio_backend.native_execution_permit import AdmissionError
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.native_owned_cells import RATE
from test_automatic_preflight_busy import injected
from test_moss_result_database_diagnostics import archive
from test_moss_automatic_integration import automatic_app_factory
from test_moss_handy_controller import clean
from test_recording_speaker_links import vector
from test_recording_speaker_history_integration import (
    primary_history_factory, ingest_recording, _cohort, _counts,
    _observations, _observation_identity, _primary_rows,
)


@pytest.fixture
def dispatch_case(archive, monkeypatch):
    """No live source claim: only the manager's finite retry/control seam."""
    owner = object.__new__(jobs.MossJobs)
    state = SimpleNamespace(now=100., source=True, configured=True, reads=[],
                            waits=[], checks=[], responses=[], closed=[])
    owner._lock = threading.RLock()
    owner._closed = owner.stopped = False
    owner._live = None
    owner.store = SimpleNamespace(settings=SimpleNamespace(recording_speaker_links_enabled=True))
    owner.speaker_links = object()
    publication = threading.RLock()
    owner.cell_source = SimpleNamespace(owner=SimpleNamespace(_publication_guard=publication))
    probe = {'owner':owner, 'state':'pending', 'admitted':True,
             'cancelled':threading.Event(), 'crop':b'\0\0' * 32000,
             'reservation':{'token':'a' * 32}, 'invocation_id':'b' * 32,
             'node':{}, 'thread':None, 'operation':None,
             'thread_start_attempted':False, 'completed':threading.Event(),
             'failure_json':None, 'failure_frozen':False, 'terminal_reason':None,
             'work_deadline':110., 'read_defer_deadline':None}
    probe['dispatch_binding'] = (probe['reservation'],probe['crop'],probe['invocation_id'],probe['node'],
                                 tuple(sorted(probe['reservation'].items())))
    owner._primary_pending = probe
    condition = threading.Condition(threading.RLock())
    permit = SimpleNamespace(_condition=condition, _optional=None, _optional_kind=None,
                             _configuration_current=lambda:state.configured)
    def check_optional(record, phase):
        with condition:
            state.checks.append((record,phase))
            if permit._optional is not record or not state.configured:
                raise AdmissionError('optional_cancelled')
    permit.check_optional = check_optional
    owner.permit = permit
    owner._history_enabled = lambda:not owner._closed and state.configured
    @contextmanager
    def guard(record):
        with publication:
            if not state.source:raise ReadError(409,'primary_probe_source_stale')
            if record['cancelled'].is_set() or not owner._history_enabled():
                raise ReadError(409,'primary_probe_cancelled')
            yield
            if not state.source:raise ReadError(409,'primary_probe_source_stale')
    owner._primary_guard = guard
    owner._primary_current = lambda record,db:state.source
    archive.clock = lambda:0.
    def read_failure(*,phase='body',code=sqlite3.SQLITE_BUSY,write=False,close_failure=False):
        with injected(monkeypatch,phase=phase,code=code,close_failure=close_failure) as (_,closed,_):
            state.closed.append(closed)
            with archive._database(write=write) as db:db.execute('SELECT 1')
    state.read_failure = read_failure
    def dispatch(reservation, *, current_check):
        assert reservation is probe['reservation']
        state.reads.append(reservation)
        action = state.responses.pop(0) if state.responses else True
        if callable(action):return action()
        if isinstance(action,BaseException):raise action
        with archive._database() as db:
            assert db.execute('SELECT 1').fetchone()[0] == 1
            return action and current_check(db)
    owner.history = SimpleNamespace(dispatch_current=dispatch)
    def wait(seconds):
        assert 0 < seconds <= .010
        assert not owner._lock._is_owned() and not publication._is_owned()
        assert not condition._is_owned()
        assert all(closed == [True] for closed in state.closed)
        assert probe['operation'] is None
        state.waits.append(seconds)
        state.now += seconds
        return probe['cancelled'].is_set()
    state.wait = wait
    monkeypatch.setattr(probe['cancelled'],'wait',wait)
    monkeypatch.setattr(jobs,'time',SimpleNamespace(monotonic=lambda:state.now))
    state.owner,state.probe,state.permit = owner,probe,permit
    return state


def _reserved(case, value):
    case.probe['state'] = 'running' if value else 'pending'
    case.permit._optional = case.probe if value else None
    case.permit._optional_kind = 'automatic_primary_probe' if value else None


@pytest.mark.parametrize('reserved,phase,code',[
    (False,'begin',sqlite3.SQLITE_BUSY), (True,'body',sqlite3.SQLITE_LOCKED_SHAREDCACHE),
])
def test_exact_closed_read_retries_same_binding_outside_every_lock(dispatch_case,reserved,phase,code):
    case = dispatch_case;probe = case.probe
    _reserved(case,reserved)
    original = probe['dispatch_binding']
    case.responses = [lambda:case.read_failure(phase=phase,code=code),True]
    assert case.owner._primary_dispatch_current(probe,reserved=reserved) is True
    assert len(case.reads) == 2 and case.waits == [.010]
    assert probe['dispatch_binding'] is original
    assert probe['reservation'] is original[0] and probe['crop'] is original[1]
    assert probe['invocation_id'] == original[2] and probe['node'] is original[3]
    assert probe['work_deadline'] == 110. and probe['read_defer_deadline'] == 100.250
    assert probe['operation'] is None and probe['failure_json'] is None
    assert case.permit._optional is (probe if reserved else None)


def test_grace_is_shared_between_submit_and_worker_and_never_renews(dispatch_case):
    case = dispatch_case;probe = case.probe
    case.responses = [case.read_failure,True]
    assert case.owner._primary_dispatch_current(probe,reserved=False)
    deadline = probe['read_defer_deadline']
    _reserved(case,True)
    case.now = deadline - .005
    case.responses = [case.read_failure,case.read_failure]
    with pytest.raises(ReadError,match='^primary_probe_read_contention$'):
        case.owner._primary_dispatch_current(probe,reserved=True)
    assert probe['read_defer_deadline'] == deadline == 100.250
    assert probe['work_deadline'] == 110.
    assert sum(case.waits) == pytest.approx(.015)
    assert probe['operation'] is None
    failure = json.loads(probe['failure_json'])
    assert failure['stage'] == 'dispatch_validation'
    assert failure['reason'] == 'pre_dispatch_read_contention'
    assert failure['child_dispatch'] == 'not_attempted'


@pytest.mark.parametrize('where',['entry','wait','successful_read'])
def test_original_work_deadline_cannot_be_renewed_or_outlived(dispatch_case,where):
    case = dispatch_case;probe = case.probe
    if where == 'entry':case.now = probe['work_deadline']
    elif where == 'wait':
        case.now = probe['work_deadline'] - .005
        case.responses = [case.read_failure]
    else:
        def late_success():
            case.now = probe['work_deadline']
            return True
        case.responses = [late_success]
    with pytest.raises(ReadError,match='^primary_probe_deadline$'):
        case.owner._primary_dispatch_current(probe,reserved=False)
    assert probe['work_deadline'] == 110. and probe['operation'] is None
    assert len(case.reads) == int(where != 'entry')
    assert json.loads(probe['failure_json'])['reason'] == 'pre_dispatch_deadline'
    assert json.loads(probe['failure_json'])['child_dispatch'] == 'not_attempted'
    _reserved(case,True)
    with pytest.raises(ReadError,match='^primary_probe_deadline$'):
        jobs._SpeakerProbeGate(case.owner,probe).check('running')


@pytest.mark.parametrize('veto',['cancel','stop','source','configuration','permit','owner','binding','token'])
def test_wait_rechecks_local_authority_before_another_read(dispatch_case,monkeypatch,veto):
    case = dispatch_case;probe = case.probe
    _reserved(case,True)
    case.responses = [case.read_failure,True]
    def wait(seconds):
        case.wait(seconds)
        if veto == 'cancel':probe['cancelled'].set()
        elif veto == 'stop':case.owner._closed = True
        elif veto == 'source':case.source = False
        elif veto == 'configuration':case.configured = False
        elif veto == 'permit':case.permit._optional = None
        elif veto == 'owner':case.owner._primary_pending = None
        elif veto == 'binding':probe['reservation'] = dict(probe['reservation'])
        else:probe['reservation']['token'] = 'c' * 32
        return probe['cancelled'].is_set()
    monkeypatch.setattr(probe['cancelled'],'wait',wait)
    with pytest.raises((ReadError,AdmissionError)):
        case.owner._primary_dispatch_current(probe,reserved=True)
    assert len(case.reads) == len(case.waits) == 1
    assert probe['operation'] is None and probe['work_deadline'] == 110.


@pytest.mark.parametrize('kind',['unmarked','write','close','malformed_marker'])
def test_only_exact_closed_read_contention_is_retryable(dispatch_case,kind):
    case = dispatch_case
    if kind == 'unmarked':
        error = sqlite3.OperationalError('PRIVATE_UNMARKED_READ')
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        action = error
    elif kind == 'write':action = lambda:case.read_failure(write=True)
    elif kind == 'close':action = lambda:case.read_failure(close_failure=True)
    else:action = result_store._ReadDeferred('body',True)
    case.responses = [action]
    with pytest.raises((ReadError,sqlite3.OperationalError,OSError)):
        case.owner._primary_dispatch_current(case.probe,reserved=False)
    assert len(case.reads) == 1 and case.waits == []
    assert case.probe['read_defer_deadline'] is None and case.probe['operation'] is None


def test_real_producer_defers_both_dispatch_reads_then_invokes_exactly_once(primary_history_factory,monkeypatch):
    with primary_history_factory() as h:
        cid = ingest_recording(h,frames=10*RATE)[0]
        assert _cohort(h) == cid
        probe = h.jobs._primary_pending
        committed = _primary_rows(h);admitted = _observations(h)[0]
        reservation,crop,invocation = probe['reservation'],probe['crop'],probe['invocation_id']
        original = h.jobs.history.dispatch_current
        reads=[];closed=[];waits=[];reservations=[]
        reserve = h.jobs.permit.reserve_optional
        def reserve_once(*args,**kwargs):
            reservations.append(args[0]);return reserve(*args,**kwargs)
        monkeypatch.setattr(h.jobs.permit,'reserve_optional',reserve_once)
        def dispatch(*args,**kwargs):
            reads.append(args[0])
            if len(reads) in (1,3):
                with injected(monkeypatch,code=sqlite3.SQLITE_BUSY,phase='body') as (_,connections,_):
                    closed.append(connections)
                    return original(*args,**kwargs)
            return original(*args,**kwargs)
        monkeypatch.setattr(h.jobs.history,'dispatch_current',dispatch)
        def wait(seconds):
            assert 0 < seconds <= .010
            assert not h.jobs._lock._is_owned()
            assert not h.capture.owner._publication_guard._is_owned()
            assert not h.jobs.permit._condition._is_owned()
            assert all(item == [True] for item in closed)
            assert h.jobs._primary_pending is probe and probe['reservation'] is reservation
            assert probe['crop'] is crop and probe['invocation_id'] == invocation
            assert probe['operation'] is None and not h.probes
            assert h.jobs.permit._optional is (None if not waits else probe)
            waits.append((probe['work_deadline'],probe['read_defer_deadline']))
            return False
        monkeypatch.setattr(probe['cancelled'],'wait',wait)
        assert h.jobs.permit.request_automatic_turn()
        before = jobs.time.monotonic()
        with h.jobs.permit.automatic_turn() as granted:
            assert granted and h.jobs.submit_primary()
        after = jobs.time.monotonic()
        assert before + runner.WORK_SECONDS <= probe['work_deadline'] <= after + runner.WORK_SECONDS
        deadline = probe['work_deadline'];grace = probe['read_defer_deadline']
        probe['thread'].run()
        assert reads == [reservation] * 4 and reservations == [probe]
        assert waits == [(deadline,grace)] * 2
        assert len(h.probes) == 1 and h.probes[0].starts == 1
        assert probe['operation'].child_dispatch == 'returned_validated' and probe['operation'].cleaned
        assert probe['operation'].work_deadline == deadline
        assert h.jobs._maintain_primary()
        assert _counts(h) == {'observations':1,'vectors':1,'pending':0,'candidate_missing':0,'attempts':1}
        assert _observation_identity(_observations(h)[0]) == _observation_identity(admitted)
        assert _primary_rows(h) == committed and h.store.chunk(cid)['next_offset'] == 10.
        assert h.jobs._primary_pending is None and h.jobs.permit._optional is None
        assert probe['crop'] is None and h.jobs._probe_staging_bytes == 0
        assert not h.jobs.submit_primary() and len(h.probes) == 1


def test_after_dispatch_read_refusal_stays_storage_unknown_and_never_replays(primary_history_factory,monkeypatch):
    with primary_history_factory() as h:
        cid = ingest_recording(h,frames=10*RATE)[0]
        assert _cohort(h) == cid
        probe = h.jobs._primary_pending
        committed = _primary_rows(h);admitted = _observations(h)[0]
        appends=[];closed=[]
        def refuse_append(*args,**kwargs):
            appends.append(args[0])
            # The child already returned. Even this authentic private read
            # signal cannot authorize another dispatch or append attempt.
            assert probe['operation'].child_dispatch == 'returned_validated'
            with injected(monkeypatch,code=sqlite3.SQLITE_LOCKED,phase='begin') as (_,connections,_):
                closed.append(connections)
                with h.jobs.history.sql._database() as db:db.execute('SELECT 1')
        monkeypatch.setattr(h.jobs.history,'append_primary',refuse_append)
        monkeypatch.setattr(probe['cancelled'],'wait',lambda seconds:pytest.fail('post-dispatch retry'))
        assert h.jobs.permit.request_automatic_turn()
        with h.jobs.permit.automatic_turn() as granted:
            assert granted and h.jobs.submit_primary()
        probe['thread'].run()
        assert probe['state'] == 'append_unknown' and closed == [[True]]
        assert len(appends) == len(h.probes) == h.probes[0].starts == 1
        assert probe['operation'].cleaned and probe['validated_output'] is not None
        assert probe['failure_json'] is None and not probe['failure_frozen']
        assert h.jobs._maintain_primary()
        with h.store.db() as db:
            row = db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?',(probe['attempt_id'],)).fetchone()
        assert row['state'] == 'failure' and json.loads(row['terminal'])['reason'] == 'storage_unknown'
        assert _counts(h) == {'observations':1,'vectors':0,'pending':0,'candidate_missing':1,'attempts':1}
        assert _observation_identity(_observations(h)[0]) == _observation_identity(admitted)
        assert _primary_rows(h) == committed and h.store.chunk(cid)['next_offset'] == 10.
        assert h.jobs._primary_pending is None and h.jobs.permit._optional is None
        assert not h.jobs.submit_primary() and h.jobs._maintain_primary()
        assert len(appends) == len(h.probes) == h.probes[0].starts == 1


@pytest.mark.parametrize('where',['default','constructor','wait'])
def test_probe_operation_preserves_default_clock_and_enforces_absolute_deadline(tmp_path,monkeypatch,where):
    """Real claim/input/cleanup lifecycle, one tiny fake driver, no process."""
    directory = tmp_path / 'owned-probe'
    directory.mkdir(mode=0o700)
    now = [100.];drivers=[];events=[]
    monkeypatch.setattr(runner,'time',SimpleNamespace(monotonic=lambda:now[0]))
    space,space_id = runner.expected_space()
    request = {'kind':runner.KIND,'job_id':'a' * 32,'space_id':space_id,
               'model_path':'/synthetic-absent-model',
               'crops':[{'index':0,'pcm_sha256':'b' * 64,'float32_sha256':'c' * 64}]}
    output = {'kind':runner.KIND,'job_id':request['job_id'],'space':space,'space_id':space_id,
              'compute_attempts':1,'nodes':[{'index':0,'pcm_sha256':'b' * 64,'vector':vector(1.)}]}
    gate = SimpleNamespace(check=lambda phase:events.append(phase),cleaning=lambda:events.append('cleaning'))
    class Driver:
        def __init__(self,directory,digest):
            self.guard = SimpleNamespace(deadline=120.)
            self.starts = self.waits = self.cleanups = 0
            drivers.append(self)
            if where == 'constructor':now[0] = 110.
        def start(self,cancelled):self.starts += 1
        def wait(self,cancelled):
            self.waits += 1
            if where == 'wait':now[0] = 110.
            return output
        def cleanup(self,stop):
            self.cleanups += 1
            return clean()
    kwargs = {} if where == 'default' else {'work_deadline':110.}
    operation = runner.ProbeOperation(directory,request,(b'\0\0' * 32000,),gate=gate,
                                     driver_factory=Driver,**kwargs)
    validated=[]
    validate = operation._validate_return
    def validate_once(value):validated.append(value);return validate(value)
    monkeypatch.setattr(operation,'_validate_return',validate_once)
    if where == 'default':
        assert operation.run(threading.Event()) is output
        assert operation.work_deadline is None and drivers[0].guard.deadline == 120.
        assert operation.child_dispatch == 'returned_validated' and validated == [output]
    else:
        with pytest.raises(TimeoutError,match='Probe work deadline'):
            operation.run(threading.Event())
        assert operation.work_deadline == drivers[0].guard.deadline == 110.
        assert validated == []
        assert operation.child_dispatch == ('not_attempted' if where == 'constructor' else 'possible')
        if where == 'constructor':
            assert json.loads(operation.failure_json)['reason'] == 'pre_dispatch_deadline'
    assert len(drivers) == 1
    assert drivers[0].starts == drivers[0].waits == int(where != 'constructor')
    assert drivers[0].cleanups == 1 and operation.cleaned and runner.cleanup_verified(operation.receipt)
    assert events == ['before_input','cleaning'] and not tuple(directory.iterdir())
    with pytest.raises(RuntimeError,match='never replayed'):operation.run(threading.Event())
