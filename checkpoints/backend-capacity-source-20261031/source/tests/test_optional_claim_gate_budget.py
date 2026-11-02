"""Manual claim settling uses original job/driver budgets; synthetic-only."""
import threading
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from audio_backend import native_execution_permit as mod
from audio_backend import moss_handy_controller as controller
from audio_backend.fragment_model_guard import ResourceLimit
from test_optional_running_sample_busy import gate_fixture
from test_native_execution_permit import sample


def manual_gate():
    p, record, event, gate, job_now = gate_fixture()
    record.pop('automatic_key')
    p._optional_kind = 'preemptible'
    gate.owner._validate = lambda r: None
    return p, record, event, gate, job_now


@pytest.mark.parametrize('barrier', ['before', 'after'])
@pytest.mark.parametrize('deadline', ['job', 'driver'])
def test_original_shorter_deadline_clips_both_claim_barriers(barrier, deadline):
    p, record, event, gate, job_now = manual_gate()
    now, calls = [10.], []
    record['deadline'] = 10.04 if deadline == 'job' else 13.
    driver_deadline = 10.04 if deadline == 'driver' else 13.
    def census(_):
        calls.append(1)
        if barrier == 'after': p._probe = True
        return sample()
    p._sampler = census
    p._probe = barrier == 'before'
    def wake(seconds):
        assert 0 < seconds <= .01
        now[0] += seconds
        job_now[0] += seconds
    with patch.object(mod.time, 'monotonic', side_effect=lambda: now[0]), \
         patch.object(p._condition, 'wait', side_effect=wake):
        with pytest.raises(ResourceLimit if deadline == 'driver' else mod.AdmissionError) as caught:
            gate.check('running', _driver_deadline=driver_deadline,
                       _driver_cancelled=threading.Event())
    assert now[0] <= 10.051 and event.is_set() and p._optional is record
    assert getattr(caught.value, '_optional_execution_deadline', False)
    assert calls == ([] if barrier == 'before' else [1])


@pytest.mark.parametrize('change', ['lease', 'controller', 'job', 'driver'])
def test_manual_final_durable_guard_followed_by_original_deadline_and_cancel(change):
    p, record, event, gate, job_now = manual_gate()
    controller_event, now, calls = threading.Event(), [10.], []
    def current(token):
        assert not p._condition._is_owned()
        calls.append(1)
        if len(calls) == 2:
            if change == 'lease': record['lease'].cancelled.set()
            elif change == 'controller': controller_event.set()
            elif change == 'job': job_now[0] = 13.
            else: now[0] = 13.
    gate.owner.cell_source.publication_current = current
    with patch.object(mod.time, 'monotonic', side_effect=lambda: now[0]):
        with pytest.raises(ResourceLimit if change == 'driver' else mod.AdmissionError):
            gate.check('running', _driver_deadline=13., _driver_cancelled=controller_event)
    assert calls == [1, 1] and p._optional is record


@pytest.mark.parametrize('phase', ['running', 'before_admission'])
@pytest.mark.parametrize('bounded', [True, False])
def test_owned_driver_original_deadline_and_legacy_gate_signature(phase, bounded):
    calls, event = [], threading.Event()
    proc = SimpleNamespace(returncode=0)
    polls = iter([None, 0]); proc.poll = lambda: next(polls)
    if bounded:
        gate = SimpleNamespace(_bounded_claim_check=True,
            check=lambda phase, **kw: calls.append((phase, kw)))
    else:
        gate = SimpleNamespace(check=lambda phase: calls.append((phase, {})))
    driver = controller.OwnedDriver.__new__(controller.OwnedDriver)
    driver.proc, driver._service_gate = proc, gate
    driver.directory, driver.identity = 'synthetic', object()
    driver.guard = SimpleNamespace(deadline=123., check=lambda: None)
    with patch.object(controller, 'check_directory'), patch.object(controller.time, 'sleep'), \
         patch.object(controller, 'read_owned', return_value=b'{"raw_retained":false}'):
        if phase == 'running': assert driver.wait(event) == ({'raw_retained': False}, None)
        else: driver._check_start_gate(phase, event)
    assert calls == [(phase, {'_driver_deadline':123., '_driver_cancelled':event} if bounded else {})]
    assert driver.proc is proc and driver.guard.deadline == 123.


@pytest.mark.parametrize('change', ['generation', 'revocation_pending', 'readiness'])
def test_manual_post_settle_rechecks_real_durable_source_guard(change):
    from audio_backend.native_owned_cell_jobs import CellJobsSource
    from audio_backend.native_owned_support_cache import ReadError
    p, record, event, gate, job_now = manual_gate()
    source = CellJobsSource.__new__(CellJobsSource)
    source._closed = False; source._configuration_allowed = lambda: True
    source.epoch, source._identity = 'synthetic-adapter', object()
    source.store = SimpleNamespace(settings=SimpleNamespace(native_owned_cells_enabled=True, moss_jobs_enabled=True))
    owner = SimpleNamespace(_epoch=object(), qualification=object(), _publication_revoke_pending=False, ready=True)
    owner.authority = SimpleNamespace(owner=owner, identity=source._identity)
    owner.health = lambda: {'ready':owner.ready and not owner._publication_revoke_pending}
    source.owner = owner; gate.owner.cell_source = source
    record['token'] = SimpleNamespace(owner=owner, adapter_epoch=source.epoch, generation=owner._epoch)
    observed, original = [], source.publication_current
    def current(token):
        assert not p._condition._is_owned()
        observed.append(1); return original(token)
    source.publication_current = current
    def census(_):
        if change == 'generation': owner._epoch = object()
        elif change == 'revocation_pending': owner._publication_revoke_pending = True
        else: owner.ready = False
        return sample()
    p._sampler = census
    with pytest.raises(ReadError) as caught:
        gate.check('running', _driver_deadline=mod.time.monotonic()+3., _driver_cancelled=threading.Event())
    assert observed == [1, 1] and p._optional is record
    assert caught.value.reason == ('cell_revoked' if change == 'generation' else 'cell_owner_unavailable')


def test_actual_controller_before_input_has_no_driver_then_spawn_uses_original_guard(tmp_path):
    from test_moss_handy_controller import FakeDriver, support
    from audio_backend.moss_handy_profile import NAME
    calls, drivers = [], []
    def factory(directory, sha):
        driver = FakeDriver(directory, sha)
        driver.guard = SimpleNamespace(deadline=123., check=lambda: None)
        drivers.append(driver); return driver
    owner = controller.MossController(tmp_path, profile_name=NAME,
        model_path='/synthetic-model.fixture', _driver_factory=factory)
    jid = owner.submit(*support())
    def check(phase, **kwargs):
        if phase == 'before_input': assert not drivers and owner._driver is None
        calls.append((phase, kwargs))
    owner._service_gate = SimpleNamespace(_bounded_claim_check=True, check=check,
        inputs_written=lambda: None, cleaning=lambda: None, cleanup_sample=lambda: None)
    result = owner.run(jid)
    assert result['state'] == 'returned_diagnostic'
    assert calls == [('before_input', {}), ('before_spawn',
        {'_driver_deadline':123., '_driver_cancelled':owner._cancel})]
    assert len(drivers) == 1 and drivers[0].starts == drivers[0].waits == 1
    assert drivers[0].guard.deadline == 123. and result['cleanup']['cleanup_verified']
