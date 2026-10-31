"""New reconstruction controls; fake storage, real manager maintenance only."""
from types import SimpleNamespace
import threading
import pytest
from audio_backend import moss_handy_jobs as jobs


@pytest.fixture
def case(monkeypatch):
    owner = jobs.MossJobs.__new__(jobs.MossJobs)
    owner._lock = threading.RLock()
    owner.permit = SimpleNamespace(_condition=threading.Condition(), _optional=None,
                                  _unavailable=None)
    owner._probe_staging_bytes = 12
    events = []
    reservation = {'token':'a'*32}
    probe = dict(owner=owner, state='pending', admitted=True, reservation=reservation,
        thread=None, thread_start_attempted=False, operation=None,
        completed=threading.Event(), cancelled=threading.Event(), work_deadline=100.,
        terminal_reason=None, failure_json=None, failure_frozen=False,
        crop=b'private crop', charge_bytes=12, dispatch_binding=object(), append_binding=None)
    owner._primary_pending = probe
    owner._kick_history_comparison = lambda: events.append('kick')
    def finish(res, reason, *, failure_json):
        assert res is reservation and probe['crop'] is not None
        assert owner._primary_pending is probe and owner._probe_staging_bytes == 12
        events.append(('finish', reason, failure_json))
        return {'state':'failure'}
    def release(token):
        assert token == reservation['token'] and owner._primary_pending is None
        assert probe['crop'] is None and owner._probe_staging_bytes == 0
        events.append('release')
    owner.history = SimpleNamespace(finish_primary=finish, release_attempt=release)
    clock = SimpleNamespace(now=50.)
    monkeypatch.setattr(jobs, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    return SimpleNamespace(owner=owner, probe=probe, events=events, clock=clock)


@pytest.mark.parametrize('trigger', ['expired', 'sticky', 'exact_deadline'])
def test_known_terminal_condition_closes_after_storage_ack(case, trigger):
    if trigger == 'expired':case.clock.now = 101.
    elif trigger == 'exact_deadline':case.clock.now = 100.
    else:case.owner.permit._unavailable = 'primary_ownership_uncertain'
    deadline = case.probe['work_deadline']
    assert case.owner._maintain_primary() is True
    assert case.events == [('finish', 'unavailable' if trigger == 'sticky' else 'deadline', None), 'kick', 'release']
    assert case.probe['work_deadline'] == deadline
    assert case.owner.permit._optional is None
    assert case.owner.permit._unavailable == ('primary_ownership_uncertain' if trigger == 'sticky' else None)


@pytest.mark.parametrize('reason', [None, 'primary_sampling_unavailable', 'other', 'unknown'])
def test_recoverable_or_unknown_permit_state_retains_owner(case, reason):
    case.owner.permit._unavailable = reason
    assert case.owner._maintain_primary() is False
    assert case.events == [] and case.owner._primary_pending is case.probe
    assert case.probe['state'] == 'pending' and case.probe['crop'] is not None
    assert case.probe['work_deadline'] == 100.


@pytest.mark.parametrize('deadline', [None, True, '100', -1, 0, float('nan'), float('inf')])
def test_invalid_or_absent_deadline_is_not_created(case, deadline):
    case.probe['work_deadline'] = deadline
    case.clock.now = 101.
    assert case.owner._maintain_primary() is False
    assert case.probe['work_deadline'] is deadline
    assert case.events == []


@pytest.mark.parametrize('guard', ['owner', 'admitted', 'reservation', 'thread', 'started', 'operation', 'permit'])
def test_no_execution_or_foreign_ownership_can_close(case, guard):
    case.clock.now = 101.
    if guard == 'owner':case.probe['owner'] = object()
    if guard == 'admitted':case.probe['admitted'] = False
    if guard == 'reservation':case.probe['reservation'] = None
    if guard == 'thread':case.probe['thread'] = SimpleNamespace(is_alive=lambda:False, join=lambda **kw:None)
    if guard == 'started':case.probe['thread_start_attempted'] = True
    if guard == 'operation':case.probe['operation'] = SimpleNamespace(cleaned=True)
    if guard == 'permit':case.owner.permit._optional = case.probe
    assert case.owner._maintain_primary() is False
    assert case.events == [] and case.probe['state'] == 'pending'


def test_unacknowledged_storage_keeps_charge_and_retries_storage_only(case):
    original = case.owner.history.finish_primary
    calls = []
    def uncertain(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError('ack unknown')
    case.owner.history.finish_primary = uncertain
    case.clock.now = 101.
    binding = case.probe['dispatch_binding']
    assert case.owner._maintain_primary() is False
    assert case.probe['state'] == 'finish_pending'
    assert case.owner._primary_pending is case.probe and case.probe['crop'] is not None
    assert case.owner._probe_staging_bytes == 12 and case.events == []
    assert case.probe['dispatch_binding'] is binding
    case.owner.history.finish_primary = original
    assert case.owner._maintain_primary() is True
    assert len(calls) == 1 and case.probe['work_deadline'] == 100.


def test_permit_observation_failure_is_fail_closed(case):
    class Broken:
        _condition = threading.Condition()
        _optional = None
        @property
        def _unavailable(self):raise RuntimeError('unknown permit status')
    case.owner.permit = Broken()
    assert case.owner._maintain_primary() is False
    assert case.probe['state'] == 'pending' and case.events == []


def test_expired_sampling_pending_closes_for_deadline_not_reset(case):
    case.owner.permit._unavailable = 'primary_sampling_unavailable'
    case.clock.now = 101.
    assert case.owner._maintain_primary() is True
    assert case.events[0][1] == 'deadline'
    assert case.owner.permit._unavailable == 'primary_sampling_unavailable'


def test_sticky_loss_without_deadline_does_not_create_one(case):
    case.probe['work_deadline'] = None
    case.owner.permit._unavailable = 'primary_ownership_uncertain'
    assert case.owner._maintain_primary() is True
    assert case.probe['work_deadline'] is None
    assert case.events[0][1] == 'unavailable'
