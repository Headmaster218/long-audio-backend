"""Paired baseline/candidate pause mechanisms; no model, child or DB.

The reservation and barrier follow test_primary_probe_reservation_fingerprint;
the completed manager follows the compact history integration polling fixture.
All waits share one original future deadline. This is not historical attribution.
"""
from contextlib import contextmanager
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from audio_backend import moss_handy_jobs as jobs
from audio_backend import native_execution_permit as permit_module
from audio_backend.worker import Worker
import test_primary_probe_reservation_fingerprint as fingerprint


SOURCE = Path(jobs.__file__).resolve().parents[1]
BASELINE_SHA256 = 'e780fe60621c0df26d74e4f0d4b60b71f15043a884372f7909b03219891c7597'
CANDIDATE_SHA256 = '08fae6fd05e9e0f269318e52cb4acac4bf2edc3e13d169b59107c8ccc9b6e278'
AUTHORITY_TEST_SHA256 = '418922505875b51b5bd957e97bf92f52c59a006df62b246ab8b2de4cebc36af4'
PAUSE_GUARD = (b"                if (self._comparison['running'] is False\n"
               b"                        and self._comparison['sampling'] is None):return\n")


@pytest.fixture(scope='session')
def implementations(tmp_path_factory):
    candidate_path = Path(permit_module.__file__).resolve(strict=True)
    raw = candidate_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CANDIDATE_SHA256
    assert permit_module.NativeExecutionPermit.__module__ == 'audio_backend.native_execution_permit'
    assert raw.count(PAUSE_GUARD) == 1
    raw = raw.replace(PAUSE_GUARD, b'', 1)
    assert hashlib.sha256(raw).hexdigest() == BASELINE_SHA256
    path = tmp_path_factory.mktemp('pause-baseline') / 'exact_dev57_native_execution_permit.py'
    path.write_bytes(raw)
    assert path.read_bytes() == raw
    name = '_redundant_comparison_pause_authenticated_baseline'
    assert name not in sys.modules
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), 'exec'), module.__dict__)
        assert Path(module.__file__).resolve() == path
        assert module.NativeExecutionPermit.__module__ == name
        yield {'baseline': module, 'candidate': permit_module}
    finally:
        assert sys.modules.pop(name) is module


MIB = 1024**2


class Handle:
    """A retained synthetic original handle; never starts an OS process."""
    pid = 1900000001
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        assert timeout == 0 and self.returncode == 0
        return self.returncode


@contextmanager
def cached_case(module, *, running=False):
    deadline = time.monotonic() + 5.
    state = {'visible': False, 'closed': False, 'cache_reads': [], 'dispatch': []}
    process = Handle()

    def sample(_):
        base = fingerprint.sample(module)
        rows = dict(base.identities)
        if state['visible']:
            rows[process.pid] = (2., os.getpid(), 16 * MIB)
        return module.ProcessSample(rows, sum(row[2] for row in rows.values()),
                                 base.caller_rss, base.available, base.disk_free)

    permit = module.NativeExecutionPermit(fingerprint.census.settings(), sampler=sample)
    assert type(permit) is module.NativeExecutionPermit
    grant, cancelled, completed = object(), threading.Event(), threading.Event()
    completed.set()
    binding = {'fixture': 'exact_retained_closed_prefix'}
    driver = SimpleNamespace(process=process, cache_binding=binding, _deadline=deadline,
        _generation=object(), comparison_parent_bytes=lambda: 0 if state['closed'] else MIB,
        comparison_resources_closed=lambda: state['closed'])
    permit.reserve_comparison(grant, cancelled, memory_bytes=192 * MIB)
    permit.bind_comparison_owner(grant, driver)
    identity = SimpleNamespace(create_time=lambda: 2., ppid=os.getpid)
    with patch.object(module.psutil, 'Process', return_value=identity):
        assert permit.bind_comparison_process(grant, process, owner=driver)
    state['visible'] = True
    assert permit.resume_comparison(grant)
    permit.bind_comparison_thread(grant, None, completed)
    if not running:
        permit.pause_comparison(grant)
    comparison = permit._comparison

    def closed_cache_binding():
        assert not state['closed'] and process.returncode is None
        state['cache_reads'].append(comparison['revision'])
        return dict(binding)

    driver.closed_cache_binding = closed_cache_binding
    manager = jobs.MossJobs.__new__(jobs.MossJobs)
    manager.permit = permit
    manager._lock = threading.RLock()
    manager._gate = lambda: manager._lock
    manager._history_maintenance = None
    manager._history_comparison_enabled = lambda: True
    manager._history_comparison_pending = False
    manager._history_compare_after = deadline
    record = manager._history_comparison = {
        'grant': grant, 'generation': object(), 'cancelled': cancelled,
        'completed': completed, 'thread': None, 'driver': driver, 'snapshot': None,
        'state': 'cached', 'deadline': deadline, 'admission_start': deadline - 5.,
        'budget_class': 'incremental', 'budget_cache_binding': binding,
        'start_attempted': False, 'task_sequence': 1, 'driver_active': False,
        'finish_pending': None, 'reason': None,
    }

    def forbidden(*args, **kwargs):
        state['dispatch'].append('forbidden')
        raise AssertionError('No task, child, storage, inference, or new admission')

    manager._thread_factory = forbidden
    manager.history = SimpleNamespace(finish_comparison=forbidden)
    storage = []
    automatic = SimpleNamespace(jobs=manager,
        maintain_text_storage=lambda **kwargs: storage.append(kwargs['stop']()),
        primary_yield_due=forbidden, step=forbidden)
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(_native_execution_permit=permit)
    worker.stop_event = threading.Event()
    worker.moss_automatic = automatic

    event, callbacks = threading.Event(), []
    token = {'cancelled': event, 'work_deadline': deadline}
    callback = lambda: callbacks.append(event.is_set())
    assert permit.request_automatic_turn()
    turn = permit.automatic_turn()
    assert turn.__enter__()
    try:
        permit.reserve_optional(token, event, callback, owner_kind='automatic_primary_probe')
        case = SimpleNamespace(module=module, permit=permit, comparison=comparison, manager=manager,
            record=record, driver=driver, grant=grant, cancelled=cancelled,
            completed=completed, process=process, state=state, storage=storage,
            worker=worker, token=token, event=event, callback=callback,
            callbacks=callbacks, deadline=deadline, sample=sample)
        yield case
        assert time.monotonic() < deadline
        assert not state['dispatch']
    finally:
        turn.__exit__(None, None, None)
        permit.release_optional(token, cleaned=True)
        permit._sampler = sample
        state.update(visible=False, closed=True)
        process.returncode = 0
        with patch.object(module.psutil, 'Process', side_effect=module.psutil.NoSuchProcess(process.pid)):
            assert permit.finish_comparison_process(grant, process, deadline=deadline)
        assert permit.release_comparison(grant)
        assert permit._comparison is None and permit._optional is None


def stable(case):
    """All retained fields except the four explicitly asserted transition fields."""
    permit, record = case.permit, case.comparison
    child = dict(record['child'])
    child['children'] = dict(child['children'])
    comparison = {key: value for key, value in record.items()
                  if key not in ('revision', 'running', 'sampling', 'failure', 'child')}
    return (permit._comparison, comparison, record['child'], child,
        permit._sample_generation, dict(permit._observed),
        permit._optional, permit._optional_kind, permit._cancel, permit._cancel_callback,
        permit._turn, permit._handoff, permit._probe, dict(permit._primary),
        permit._closed, permit._unavailable, permit._configuration_current,
        case.manager._history_comparison, dict(case.record),
        case.manager._history_compare_after, case.manager._history_comparison_pending,
        case.driver, case.driver.process, case.driver.cache_binding,
        dict(case.driver.cache_binding), case.driver._deadline, case.driver._generation,
        case.driver.comparison_parent_bytes(), case.driver.comparison_resources_closed(),
        dict(case.token), case.completed.is_set())


def fingerprint_state(case):
    with case.permit._condition:
        return case.permit._optional_state_locked(allow_queued=True, primary_probe_running=True)


def only_revision_changed(before, after):
    assert len(before[0]) == len(after[0]) == 10
    assert all(left is right for left, right in zip(before[0][:-1], after[0][:-1]))
    assert before[0][-1] is not after[0][-1]
    assert before[1] == after[1]


def blocked_census(case, operation, change):
    """Original two-thread census barrier, clipped to one fixed test deadline."""
    entered, resume = threading.Event(), threading.Event()
    calls, outcomes = [], []
    permit = case.permit

    def remaining():
        return max(0., case.deadline - time.monotonic())

    def controlled(settings):
        current = case.sample(settings)
        calls.append(threading.get_ident())
        entered.set()
        assert resume.wait(remaining()), 'census barrier deadline'
        return current

    def checking():
        try:
            outcomes.append(('returned', operation()))
        except BaseException as error:
            outcomes.append(('raised', error))

    permit._sampler = controlled
    thread = threading.Thread(target=checking, name='redundant-pause-census')
    with patch.object(permit._condition, 'wait', side_effect=AssertionError('unexpected permit wait')):
        thread.start()
        try:
            assert entered.wait(remaining()), 'census was not reached'
            change()
        finally:
            resume.set()
            thread.join(remaining())
            permit._sampler = case.sample
    assert not thread.is_alive() and calls == [thread.ident] and len(outcomes) == 1
    assert permit._sample_flight is None and case.comparison['sampling'] is None
    return outcomes[0]


def assert_probe_refused(case, result):
    module = case.module
    assert result[0] == 'raised' and type(result[1]) is module.AdmissionError
    assert result[1].reason == 'optional_ownership_changed'
    detail = module.optional_sample_failure(result[1])
    assert detail['subtype'] == 'ownership_changed' and detail['reservation_held'] is True
    assert detail['wait_ms'] == 0 and detail['grace_used'] is False
    assert case.event.is_set() and not case.callbacks
    assert not case.cancelled.is_set() and case.comparison['failure'] is None
    assert case.permit._optional is case.token


@pytest.mark.parametrize('implementation', ['baseline', 'candidate'])
def test_cached_worker_handoff_running_probe(implementations, implementation):
    module = implementations[implementation]
    with cached_case(module) as case:
        permit, comparison = case.permit, case.comparison
        before = stable(case)
        original = dict(comparison)
        permit.check_optional(case.token, 'running')
        assert stable(case) == before and dict(comparison) == original
        assert not case.event.is_set()
        permit.pause_comparison(object())
        assert stable(case) == before and dict(comparison) == original

        def redundant_handoff():
            assert not comparison['running'] and comparison['sampling'] is None
            state = fingerprint_state(case)
            case.worker._automatic_handoff(request=False)
            assert stable(case) == before
            assert not comparison['running'] and comparison['sampling'] is None
            assert case.manager._history_maintenance is None
            assert case.storage == [False] and len(case.state['cache_reads']) == 2
            first, second = case.state['cache_reads']
            after = fingerprint_state(case)
            if implementation == 'baseline':
                assert first is not state[0][-1] and second is not first
                assert comparison['revision'] is second
                only_revision_changed(state, after)
            else:
                assert first is second is comparison['revision'] is state[0][-1]
                assert len(state[0]) == len(after[0]) == 10
                assert all(left is right for left, right in zip(state[0], after[0]))
                assert state[1] == after[1]

        result = blocked_census(case, lambda: permit.check_optional(case.token, 'running'),
                                redundant_handoff)
        if implementation == 'baseline':
            assert_probe_refused(case, result)
        else:
            assert result == ('returned', None)
            assert not case.event.is_set() and not case.callbacks
            assert not case.cancelled.is_set() and comparison['failure'] is None
            assert permit._optional is case.token and permit._state == 'OPTIONAL_RUNNING'
        assert stable(case) == before
        assert permit.status()['comparison_reserved_bytes'] == 192 * MIB
        assert permit.status()['comparison_cpu_lanes'] == 0


def test_running_to_paused_invalidates_running_probe(implementations):
    module = implementations['candidate']
    with cached_case(module, running=True) as case:
        before = stable(case)

        def pause():
            assert case.comparison['running'] and case.comparison['sampling'] is None
            state = fingerprint_state(case)
            case.permit.pause_comparison(case.grant)
            assert not case.comparison['running'] and case.comparison['sampling'] is None
            only_revision_changed(state, fingerprint_state(case))
            assert stable(case) == before

        result = blocked_census(case,
            lambda: case.permit.check_optional(case.token, 'running'), pause)
        assert_probe_refused(case, result)
        assert stable(case) == before and case.state['cache_reads'] == []


def test_paused_refresh_pending_pause_prevents_resume(implementations):
    module = implementations['candidate']
    with cached_case(module) as case:
        permit, comparison = case.permit, case.comparison
        before, revision = stable(case), comparison['revision']

        def pause():
            flight = comparison['sampling']
            assert not comparison['running'] and flight is not None
            assert permit._refresh_comparison(comparison) is False
            assert comparison['sampling'] is flight and comparison['revision'] is revision
            assert not permit.release_comparison(case.grant)
            permit.pause_comparison(case.grant)
            assert comparison['sampling'] is flight and comparison['revision'] is not revision
            assert not comparison['running'] and stable(case) == before

        result = blocked_census(case, lambda: permit._refresh_comparison(comparison), pause)
        assert result == ('returned', False)
        assert not comparison['running'] and not case.cancelled.is_set()
        assert comparison['failure'] is None and not case.event.is_set() and not case.callbacks
        assert stable(case) == before


def test_paused_observation_pending_pause_invalidates_observation(implementations):
    module = implementations['candidate']
    with cached_case(module) as case:
        permit, comparison = case.permit, case.comparison
        before, revision = stable(case), comparison['revision']

        def pause():
            flight = comparison['sampling']
            assert not comparison['running'] and flight is not None
            assert permit._refresh_comparison(comparison) is False
            assert comparison['sampling'] is flight and comparison['revision'] is revision
            assert not permit.release_comparison(case.grant)
            permit.pause_comparison(case.grant)
            assert comparison['sampling'] is flight and comparison['revision'] is not revision
            assert not comparison['running'] and stable(case) == before

        result = blocked_census(case,
            lambda: permit.observe_comparison_process(case.grant, case.process, deadline=case.deadline),
            pause)
        assert result[0] == 'raised' and type(result[1]) is module.AdmissionError
        assert result[1].reason == 'comparison_pending'
        assert case.cancelled.is_set() and comparison['failure'] == 'comparison_pending'
        assert not comparison['running'] and not case.event.is_set() and not case.callbacks
        assert stable(case) == before


@pytest.mark.parametrize('change', ['generation', 'comparison_record', 'comparison_revision'])
def test_existing_authority_refusal(implementations, change):
    module = implementations['candidate']
    source = Path(fingerprint.__file__).resolve()
    assert source == SOURCE / 'tests/test_primary_probe_reservation_fingerprint.py'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == AUTHORITY_TEST_SHA256
    original_reservation = fingerprint.reservation

    @contextmanager
    def candidate_reservation():
        deadline = time.monotonic() + 5.
        with original_reservation(module=module) as case:
            case.token['work_deadline'] = deadline
            yield case
            assert case.token['work_deadline'] == deadline and time.monotonic() < deadline

    # Only the existing test helper's implementation choice changes. Its actual
    # test body and every product authority/fingerprint method remain unchanged.
    with patch.object(fingerprint, 'mod', module), patch.object(fingerprint, 'reservation',
            candidate_reservation):
        fingerprint.test_real_authority_change_during_census_still_refuses(change)
