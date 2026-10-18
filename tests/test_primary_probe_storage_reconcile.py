"""Finite storage-only recovery contracts; execution is separately gated.

Three cases use the existing genuine capture/Worker/manager/permit producer.
The other 23 cases use its real HistoryStore, ProbeOperation and manager methods
over the existing 4-second history fixture, with explicit fake local authority,
permit admission and synthetic model edges. They do not qualify native model
execution, acoustic quality, concurrency, throughput or resource headroom.
Each case owns one scope/attempt/observation, one <=512-float synthetic result,
and one source (two work chunks only in the primary-progress integration).
All retries are finite explicit calls; module-local clocks never patch stdlib.
"""
from contextlib import contextmanager
from copy import copy
import json
import threading
from types import MethodType, SimpleNamespace

import pytest

from audio_backend import moss_handy_jobs as jobs
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_probe_runner as runner
from audio_backend.native_owned_cells import RATE
from audio_backend.native_owned_support_cache import ReadError
from test_moss_automatic_integration import automatic_app_factory
from test_moss_handy_controller import clean
from test_moss_handy_jobs import ManualThread
from test_recording_speaker_links import vector
from test_recording_speaker_history import (
    history_env, primary_fixture, claim, current, invocation_for,
    database_write_fault, snapshot, attempt_row, observation_rows,
    assert_accounting,
)
from test_recording_speaker_history_integration import (
    primary_history_factory, ingest_recording, _cohort, _primary_rows,
    _observation_identity,
)


def _record_calls(case, patch):
    case.appends, case.acks, case.database_modes = [], [], []
    append, acknowledge, database = (case.history.append_primary,
        case.history.acknowledge_primary_append, case.history._database)
    def append_once(*args, **kwargs):
        case.appends.append(args)
        return append(*args, **kwargs)
    def acknowledge_once(*args, **kwargs):
        case.acks.append(args)
        return acknowledge(*args, **kwargs)
    @contextmanager
    def observed_database(*, write=False):
        case.database_modes.append(write)
        with database(write=write) as db:yield db
    patch.setattr(case.history, 'append_primary', append_once)
    patch.setattr(case.history, 'acknowledge_primary_append', acknowledge_once)
    patch.setattr(case.history, '_database', observed_database)


def _freeze_references(case):
    probe = case.probe
    case.reservation, case.node = probe['reservation'], probe['node']
    case.operation, case.output = probe['operation'], probe['validated_output']
    case.invocation, case.deadline = probe['invocation_id'], probe['work_deadline']
    case.frozen = case.history._frozen[probe['attempt_id']]
    case.payload = case.frozen['payload']
    case.digest = case.frozen.get('success_input_digest')


def _eligible(case):
    case.now = max(case.now, case.probe.get('storage_retry_after', 0.))


def _ack(case):
    return case.history.acknowledge_primary_append(case.reservation, case.node,
        case.output['nodes'][0]['vector'], case.invocation,
        success_capability=case.owner._history_success_capability)


def _assert_same_producer(case):
    probe = case.probe
    assert probe['reservation'] is case.reservation and probe['node'] is case.node
    assert probe['operation'] is case.operation and probe['validated_output'] is case.output
    assert probe['invocation_id'] == case.invocation and probe['work_deadline'] == case.deadline
    assert case.operation.cleaned and runner.cleanup_verified(case.operation.receipt)
    assert case.operation.child_dispatch == 'returned_validated'
    assert len(case.drivers) == case.drivers[0].starts == 1


def _assert_pending(case):
    _assert_same_producer(case)
    probe = case.probe
    assert case.owner._primary_pending is probe and probe['state'] == 'append_unknown'
    assert case.owner.permit._optional is None and probe['crop'] is None
    assert case.owner._probe_staging_bytes == probe['charge_bytes'] == runner.STAGING_BYTES
    assert case.history._frozen[probe['attempt_id']] is case.frozen
    assert case.frozen['payload'] is case.payload and case.frozen['success_input_digest'] == case.digest
    row = attempt_row(case.store, probe['attempt_id'])
    assert row['state'] == 'reserved' and row['reserved_bytes'] == 2048
    assert row['staging_bytes'] == history.PROBE_STAGING_BYTES


def _assert_closed(case, state):
    probe = case.probe
    assert case.owner._primary_pending is None and case.owner.permit._optional is None
    assert probe['state'] == 'terminal' and probe['crop'] is None
    assert case.owner._probe_staging_bytes == probe['charge_bytes'] == 0
    row = attempt_row(case.store, probe['attempt_id'])
    assert row['state'] == state and row['reserved_bytes'] == row['staging_bytes'] == 0
    observation = observation_rows(case.store, probe['attempt_id'])[0]
    observation['basis'] = row['basis']
    assert _observation_identity(observation) == _observation_identity(case.admitted)
    assert observation['evidence_kind'] == ('vector' if state == 'success' else 'candidate_missing')
    assert len(observation_rows(case.store, probe['attempt_id'])) == 1


@pytest.fixture
def storage_case(history_env, monkeypatch):
    """Real store/operation/manager logic; local admission and source are fake."""
    env = history_env
    def opened(*, after_commit=False, mode='append'):
        item = primary_fixture(env)
        if mode == 'claim_unknown':
            with database_write_fault(monkeypatch, env.value, after_commit=True,
                                      reason='synthetic_claim_ack_unknown'):
                with pytest.raises(ReadError, match='synthetic_claim_ack_unknown'):claim(env, item)
            reservation = None
        else:reservation = claim(env, item)
        token = f'{item.number:032x}'
        owner = env.owner
        case = SimpleNamespace(env=env, item=item, store=env.store, history=env.value,
            owner=owner, now=100., source=True, configured=True, drivers=[], releases=[])
        for name in ('_gate', '_run_primary', '_primary_dispatch_local', '_primary_dispatch_current',
                '_primary_result_matches', '_primary_success_matches', '_primary_append_ack_matches',
                '_maintain_primary_append', '_maintain_primary', '_stop_primary',
                '_drop_primary_copy', '_kick_history_comparison'):
            setattr(owner, name, MethodType(getattr(jobs.MossJobs, name), owner))
        owner._observe_probe_failure = jobs.MossJobs._observe_probe_failure
        owner._freeze_probe_failure = jobs.MossJobs._freeze_probe_failure
        owner._lock = threading.RLock()
        owner.store, owner.history = env.store, env.value
        owner._closed = owner.stopped = False
        owner._history_success_capability = env.capability
        owner._history_enabled = lambda:case.configured and not owner._closed
        owner._links_enabled = owner._history_enabled
        owner._primary_current = lambda probe, db:case.source and current(item)(db)
        owner.directory = env.directory / 'owned-probe'
        owner.directory.mkdir(mode=0o700)
        owner._probe_staging_bytes = runner.STAGING_BYTES
        @contextmanager
        def guard(probe):
            with owner.cell_source.owner._publication_guard:
                if not case.source:raise ReadError(409, 'primary_probe_source_stale')
                if probe['cancelled'].is_set() or not owner._history_enabled():
                    raise ReadError(409, 'primary_probe_cancelled')
                yield
                if not case.source:raise ReadError(409, 'primary_probe_source_stale')
        owner._primary_guard = guard
        probe = {'owner':owner, 'session_id':item.sid, 'recording_id':item.rid,
            'state':'running', 'admitted':True, 'cancelled':threading.Event(),
            'crop':b'\0\0' * (2 * RATE), 'charge_bytes':runner.STAGING_BYTES,
            'reservation':reservation, 'attempt_id':token,
            'identity':attempt_row(env.store, token)['identity_sha256'],
            'node':item.node, 'space_id':item.node['space_id'],
            'invocation_id':invocation_for(token), 'operation':None, 'thread':None,
            'thread_start_attempted':False, 'completed':threading.Event(),
            'validated_output':None, 'failure_json':None, 'failure_frozen':False,
            'terminal_reason':None, 'work_deadline':110., 'read_defer_deadline':None,
            'storage_retry_after':0., 'append_binding':None, 'dispatch_binding':None}
        owner._primary_pending = case.probe = probe
        owner.permit = SimpleNamespace(_optional=probe, cleaning=lambda record:None)
        def check_optional(record, phase):
            assert record is probe and owner.permit._optional is probe
        def release_optional(record, *, cleaned):
            assert record is probe
            case.releases.append(cleaned)
            if cleaned:owner.permit._optional = None
        owner.permit.check_optional, owner.permit.release_optional = check_optional, release_optional
        owner.permit.reserve_optional = lambda *a, **kw:pytest.fail('storage retry regranted model permit')
        monkeypatch.setattr(jobs, 'time', SimpleNamespace(monotonic=lambda:case.now))
        monkeypatch.setattr(runner, 'time', SimpleNamespace(monotonic=lambda:case.now))
        monkeypatch.setattr(probe['cancelled'], 'wait', lambda seconds:pytest.fail('storage retry waited'))
        class Driver:
            def __init__(self, directory, digest):
                self.directory, self.starts, self.cleanups = directory, 0, 0
                self.cleanup_ok = mode != 'cleanup_unknown'
                case.drivers.append(self)
            def start(self, cancelled):self.starts += 1
            def wait(self, cancelled):
                request = json.loads((self.directory / 'request.json').read_bytes())
                return {'kind':runner.KIND, 'job_id':request['job_id'],
                    'space':runner.expected_space()[0], 'space_id':request['space_id'],
                    'compute_attempts':1, 'nodes':[{'index':0,
                        'pcm_sha256':request['crops'][0]['pcm_sha256'], 'vector':vector(1., 0.)}]}
            def cleanup(self, stop):
                self.cleanups += 1
                return clean() if self.cleanup_ok else {'cleanup_verified':False}
        owner._probe_factory = Driver
        _record_calls(case, monkeypatch)
        case.admitted = dict(observation_rows(env.store, token)[0],
                             basis=attempt_row(env.store, token)['basis'])
        if mode == 'claim_unknown':
            probe.update(state='claim_unknown', crop=None, admitted=False)
            owner.permit._optional = None
            return case
        probe['dispatch_binding'] = (reservation, probe['crop'], probe['invocation_id'],
                                     probe['node'], tuple(sorted(reservation.items())))
        thread = probe['thread'] = ManualThread(target=owner._run_primary, args=(probe,))
        probe['thread_start_attempted'] = True
        thread.start()
        with database_write_fault(monkeypatch, env.value, after_commit=after_commit,
                                  reason='synthetic_append_ack_unknown'):
            thread.run()
        assert probe['state'] == ('cleanup_unknown' if mode == 'cleanup_unknown' else 'append_unknown')
        _freeze_references(case)
        return case
    return opened


@pytest.fixture
def real_storage_case(primary_history_factory, monkeypatch):
    @contextmanager
    def opened(*, after_commit=False, frames=10 * RATE):
        with primary_history_factory() as h:
            cids = ingest_recording(h, frames=frames)
            assert _cohort(h) == cids[0]
            probe = h.jobs._primary_pending
            case = SimpleNamespace(h=h, store=h.store, history=h.jobs.history, owner=h.jobs,
                probe=probe, cids=cids, now=jobs.time.monotonic(), drivers=h.probes,
                primary=_primary_rows(h), admitted=dict(observation_rows(h.store, probe['attempt_id'])[0],
                    basis=attempt_row(h.store, probe['attempt_id'])['basis']))
            with monkeypatch.context() as patch:
                patch.setattr(jobs, 'time', SimpleNamespace(monotonic=lambda:case.now))
                patch.setattr(runner, 'time', SimpleNamespace(monotonic=lambda:case.now))
                _record_calls(case, patch)
                assert h.jobs.permit.request_automatic_turn()
                with h.jobs.permit.automatic_turn() as granted:
                    assert granted and h.jobs.submit_primary()
                with database_write_fault(patch, case.history, after_commit=after_commit,
                                          reason='synthetic_append_ack_unknown'):
                    probe['thread'].run()
                assert probe['state'] == 'append_unknown'
                _freeze_references(case)
                patch.setattr(probe['cancelled'], 'wait', lambda seconds:pytest.fail('storage retry waited'))
                patch.setattr(h.jobs.permit, 'reserve_optional',
                    lambda *a, **kw:pytest.fail('storage retry regranted model permit'))
                yield case
    return opened


@pytest.mark.parametrize('after_commit', [False, True], ids=['before_commit', 'lost_ack'])
def test_real_producer_reconciles_same_frozen_result_without_model_replay(real_storage_case, monkeypatch, after_commit):
    with real_storage_case(after_commit=after_commit) as case:
        _eligible(case)
        if after_commit:
            before = snapshot(case.store)
            modes = len(case.database_modes)
            for _ in range(2):
                assert _ack(case) == {'resolved':True, 'state':'success', 'should_probe':False, 'existing':True}
            assert snapshot(case.store) == before
            assert case.database_modes[modes:] == [False, False]
            assert len(case.appends) == 1
        else:
            _assert_pending(case)
            before = snapshot(case.store)
            for retry in range(2):
                with database_write_fault(monkeypatch, case.history, after_commit=False,
                                          reason='synthetic_retry_write_unknown'):
                    assert not case.owner._maintain_primary()
                assert len(case.appends) == retry + 2 and snapshot(case.store) == before
                _assert_pending(case)
                retry_after = case.probe['storage_retry_after']
                assert retry_after >= case.now + 1.
                calls = (len(case.acks), len(case.appends), len(case.database_modes))
                assert not case.owner._maintain_primary()
                case.now = retry_after - .001
                assert not case.owner._maintain_primary()
                assert calls == (len(case.acks), len(case.appends), len(case.database_modes))
                assert case.probe['storage_retry_after'] == retry_after
                _eligible(case)
        assert case.owner._maintain_primary()
        _assert_closed(case, 'success')
        _assert_same_producer(case)
        assert len(case.appends) == (1 if after_commit else 4)
        assert _primary_rows(case.h) == case.primary
        closed = snapshot(case.store)
        assert case.owner._maintain_primary() and snapshot(case.store) == closed
        assert case.h.store.chunk(case.cids[0])['next_offset'] == 10.


@pytest.mark.parametrize('changed', ['vector', 'invocation', 'owner', 'node', 'reservation', 'operation', 'terminal'])
def test_frozen_mismatch_cannot_fall_through_to_generic_success_ack(storage_case, changed):
    case = storage_case(after_commit=True)
    probe = case.probe
    if changed == 'vector':probe['validated_output']['nodes'][0]['vector'] = vector(0., 1.)
    elif changed == 'invocation':probe['invocation_id'] = 'f' * 32
    elif changed == 'owner':probe['owner'] = object()
    elif changed == 'node':probe['node'] = dict(probe['node'])
    elif changed == 'reservation':probe['reservation'] = dict(probe['reservation'])
    elif changed == 'operation':probe['operation'] = copy(probe['operation'])
    else:case.frozen['terminal'] = b'{"synthetic":"conflict"}'
    before = snapshot(case.store)
    _eligible(case)
    assert not case.owner._maintain_primary()
    assert case.owner._primary_pending is probe and probe['state'] == 'append_unknown'
    assert probe['charge_bytes'] == case.owner._probe_staging_bytes == runner.STAGING_BYTES
    assert case.history._frozen[probe['attempt_id']] is case.frozen
    assert snapshot(case.store) == before and len(case.appends) == 1
    assert case.owner.permit._optional is None and len(case.drivers) == case.drivers[0].starts == 1
    assert_accounting(case.env)


def _veto(case, change):
    if change == 'cancel':case.probe['cancelled'].set()
    elif change == 'configuration':case.configured = False
    elif change == 'revoke':case.source = False
    else:case.now = case.probe['work_deadline']


@pytest.mark.parametrize('after_commit', [False, True], ids=['reserved', 'committed'])
@pytest.mark.parametrize('change', ['cancel', 'configuration', 'revoke', 'deadline'])
def test_exact_ack_survives_guard_changes_but_reserved_success_does_not(storage_case, change, after_commit):
    case = storage_case(after_commit=after_commit)
    _veto(case, change)
    _eligible(case)
    before = snapshot(case.store)
    closed = case.owner._maintain_primary()
    if after_commit:
        assert closed and snapshot(case.store) == before
        _assert_closed(case, 'success')
    elif change == 'revoke':
        assert not closed and snapshot(case.store) == before
        _assert_pending(case)
        assert case.probe['storage_retry_after'] >= case.now + 1.
    else:
        assert closed
        _assert_closed(case, 'failure')
        assert json.loads(attempt_row(case.store, case.probe['attempt_id'])['terminal'])['reason'] == 'storage_unknown'
    assert len(case.appends) == 1
    _assert_same_producer(case)
    assert_accounting(case.env)


@pytest.mark.parametrize('change', ['cancel', 'configuration', 'revoke', 'deadline'])
def test_successful_reserved_read_rechecks_fresh_guard_before_any_write(storage_case, monkeypatch, change):
    case = storage_case()
    acknowledge = case.history.acknowledge_primary_append
    def cross_guard(*args, **kwargs):
        result = acknowledge(*args, **kwargs)
        assert result['state'] == 'reserved' and not result['resolved']
        _veto(case, change)
        return result
    monkeypatch.setattr(case.history, 'acknowledge_primary_append', cross_guard)
    _eligible(case)
    assert case.owner._maintain_primary() is (change != 'revoke')
    if change == 'revoke':_assert_pending(case)
    else:_assert_closed(case, 'failure')
    assert len(case.appends) == 1
    _assert_same_producer(case)
    assert_accounting(case.env)


def test_uncertain_original_cleanup_never_enters_storage_reconciliation(storage_case):
    case = storage_case(mode='cleanup_unknown')
    probe = case.probe
    assert not case.operation.cleaned and probe['validated_output'] is None
    assert case.owner.permit._optional is probe and case.payload is None
    before = snapshot(case.store)
    assert not case.owner._maintain_primary()
    assert snapshot(case.store) == before and not case.acks and not case.appends
    assert probe['charge_bytes'] == runner.STAGING_BYTES and case.owner._primary_pending is probe
    case.drivers[0].cleanup_ok = True
    assert case.owner._stop_primary()
    assert case.operation.cleaned and case.drivers[0].starts == 1
    _assert_closed(case, 'failure')
    assert not case.acks and not case.appends
    assert_accounting(case.env)


@pytest.mark.parametrize('missing', ['claim_unknown', 'restart', 'missing_payload'])
def test_missing_live_success_payload_keeps_old_metadata_closure(storage_case, monkeypatch, missing):
    case = storage_case(mode='claim_unknown' if missing == 'claim_unknown' else 'append')
    if missing == 'restart':
        old_owner = case.history.owner
        case.history = case.owner.history = history.HistoryStore(case.store, clock=lambda:0.)
        case.owner._history_success_capability = case.history.bind_success_owner(case.owner)
        assert case.history.owner != old_owner and not case.history._frozen
    elif missing == 'missing_payload':
        case.frozen['payload'] = None
    monkeypatch.setattr(case.history, '_prepare_success',
        lambda *a, **kw:pytest.fail('missing payload was reconstructed'))
    monkeypatch.setattr(case.history, 'append_primary',
        lambda *a, **kw:pytest.fail('missing live authority appended a success'))
    _eligible(case)
    assert case.owner._maintain_primary()
    _assert_closed(case, 'failure')
    assert len(case.drivers) == int(missing != 'claim_unknown')
    if case.drivers:assert case.drivers[0].starts == 1
    assert_accounting(case.env)


def test_next_real_primary_cohort_progresses_while_cleaned_storage_is_pending(real_storage_case):
    with real_storage_case(frames=20 * RATE) as case:
        _assert_pending(case)
        probe = case.probe
        assert _cohort(case.h) == case.cids[1]
        assert case.owner._primary_pending is probe and case.owner.permit._optional is None
        assert case.h.store.chunk(case.cids[0])['next_offset'] == 10.5
        assert case.h.store.chunk(case.cids[1])['next_offset'] == 9.5
        after_primary = _primary_rows(case.h)
        assert len(after_primary) > len(case.primary) and after_primary[:len(case.primary)] == case.primary
        assert len(case.drivers) == case.drivers[0].starts == 1
        _assert_pending(case)
        _eligible(case)
        assert case.owner._maintain_primary()
        _assert_closed(case, 'success')
        assert _primary_rows(case.h) == after_primary
        assert len(case.appends) == 2
