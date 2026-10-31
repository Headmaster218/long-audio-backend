"""Two deterministic current-dev60 Worker terminal-release orderings.

Only process/driver/census/future edges are synthetic. Product methods execute
unchanged. Each case uses one original five-second cutoff, including cleanup.
These are constructed scheduler possibilities, not attribution of rank 5 or a
new release defect. A comparison that becomes terminal while a primary census
is active must invalidate that census. A comparison already terminal at the
first Worker maintenance is released before the next automatic step admits.
"""
from contextlib import nullcontext
import os
import threading
import time
from types import SimpleNamespace

import pytest

from audio_backend import moss_handy_jobs as jobs
from audio_backend import native_execution_permit as mod
from audio_backend import recording_speaker_history_process as process_module
from audio_backend.worker import Worker
from test_primary_probe_census_coordination_candidate import no_external_work, sample


MIB = 1024**2


@pytest.fixture
def terminal_case(monkeypatch):
    deadline = time.monotonic() + 5.
    permit = mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',
        process_memory_limit_bytes=6 * 1024**3, disk_reserve_bytes=0), sampler=lambda _: sample())
    manager = jobs.MossJobs.__new__(jobs.MossJobs)
    manager.permit, manager._lock = permit, threading.RLock()
    manager._history_enabled = manager._history_comparison_enabled = lambda: True
    manager._history_discovery = manager._history_maintenance = None
    manager._history_comparison_pending = False
    manager._history_compare_after = deadline  # No new discovery is due in this case.
    grant, cancelled, completed = object(), threading.Event(), threading.Event()
    record = manager._history_comparison = {
        'grant': grant, 'cancelled': cancelled, 'completed': completed, 'thread': None,
        'driver': None, 'driver_active': False, 'budget_class': 'cold',
        'deadline': deadline, 'admission_start': deadline - 5., 'task_sequence': 0,
        'budget_cache_binding': None, 'snapshot': {'token': object()},
        'state': 'starting', 'reason': None, 'finish_pending': None,
    }
    event, callbacks = threading.Event(), []
    callback = lambda: callbacks.append(event.is_set())
    probe = {'owner': manager, 'state': 'running', 'admitted': True, 'cancelled': event,
        'crop': object(), 'reservation': {'token': 'fixed'}, 'invocation_id': 'fixed',
        'node': {}, 'operation': None, 'failure_frozen': False,
        'thread_start_attempted': True, 'read_defer_deadline': None, 'work_deadline': deadline}
    probe['dispatch_binding'] = (probe['reservation'], probe['crop'], probe['invocation_id'],
        probe['node'], tuple(sorted(probe['reservation'].items())))
    manager._primary_pending = probe
    manager._primary_guard = lambda _: nullcontext()
    case = SimpleNamespace(permit=permit, manager=manager, record=record, probe=probe,
        event=event, callback=callback, callbacks=callbacks, deadline=deadline,
        remaining=lambda: max(0., deadline - time.monotonic()), threads=[], barriers=[],
        trace=[], forbidden=[], current_reads=[], child_starts=[], storage=[], constructed=[])
    def forbidden(*args, **kwargs):
        case.forbidden.append(True)
        raise AssertionError('No database, new task, new child, or unexpected step')
    manager._thread_factory = forbidden
    manager.history = SimpleNamespace(
        comparison_current=lambda value: case.current_reads.append(('comparison', value)) or 'current',
        dispatch_current=lambda value, **kwargs: case.current_reads.append(('primary', value)) or True,
        finish_comparison=forbidden)
    automatic = SimpleNamespace(jobs=manager,
        maintain_text_storage=lambda **kwargs: case.storage.append(kwargs['stop']()),
        primary_yield_due=forbidden, step=forbidden)
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(_native_execution_permit=permit)
    worker.stop_event, worker.moss_automatic = threading.Event(), automatic
    case.worker, case.automatic = worker, automatic

    class Process:
        pid = os.getpid() + 100000
        exited = False
        def poll(self):return 0 if self.exited else None
        def wait(self, timeout):
            assert timeout == 0 and self.exited
            return 0

    class Driver:
        def __init__(self, **edges):
            self.edges, self.process = edges, None
            self.closed, self.positive, self.cache_binding = True, False, None
            case.constructed.append(self)
        def comparison_parent_bytes(self):return 0
        def comparison_resources_closed(self):return self.closed
        def start(self, work_deadline):
            assert work_deadline == deadline
            assert not manager._lock._is_owned() and not permit._condition._is_owned()
            self.closed, self.process = False, Process()
            case.child_starts.append(self)
            assert self.edges['bind_child'](self.process) is True
            return {'ready': True}
        def begin(self, token, **kwargs):
            assert token is record['snapshot']['token'] and kwargs['deadline'] == deadline
        def rebase(self, token):forbidden()
        def close(self):
            assert not manager._lock._is_owned() and not permit._condition._is_owned()
            case.trace.append(('close', permit._sample_generation, permit._comparison))
            if not self.positive:
                self.closed, self.process.exited = True, True
                self.positive = self.edges['finish_child'](self.process, deadline=deadline)
            return {'positive': self.positive, 'positive_wait': self.positive}

    def identity(pid):
        driver = record['driver']
        assert driver is not None and driver.process.pid == pid
        if driver.process.exited:raise mod.psutil.NoSuchProcess(pid)
        return SimpleNamespace(create_time=lambda: 2., ppid=os.getpid)
    monkeypatch.setattr(process_module, 'ComparisonProcess', Driver)
    monkeypatch.setattr(mod.psutil, 'Process', identity)
    try:
        permit.reserve_comparison(grant, cancelled)
        case.comparison = permit._comparison
        permit.bind_comparison_thread(grant, None, completed)
        assert manager._history_comparison_driver(record, record['snapshot']['token']) is record['driver']
        assert manager._close_history_comparison_driver(record)
        permit.pause_comparison(grant)
        assert case.comparison['owner'] is record['driver']
        assert case.comparison['child']['closed'] and case.comparison['start_claim'] is None
        assert case.comparison['child']['original_returncode'] == 0
        assert record['driver'].comparison_resources_closed() is True
        assert permit.status()['comparison_reserved_bytes'] == 192 * MIB
        assert not completed.is_set()  # Synthetic comparison future has not completed yet.
        assert len(case.constructed) == len(case.child_starts) == 1
        case.trace.clear();case.current_reads.clear()
        yield case
    finally:
        for barrier in case.barriers:barrier.set()
        for thread in case.threads:
            if thread.ident is not None:thread.join(case.remaining())
        assert all(not thread.is_alive() for thread in case.threads)
        permit.release_optional(probe, cleaned=True)
        completed.set()
        assert manager._close_history_comparison_driver(record)
        assert permit.release_comparison(grant)
        assert permit._comparison is None and permit._optional is None
        assert permit._sample_flight is None and permit._primary_probe_admission is None
        assert not case.forbidden and time.monotonic() < deadline


def reserve_primary(case):
    case.permit.reserve_optional(case.probe, case.event, case.callback,
        owner_kind='automatic_primary_probe', work_deadline=case.deadline)


def exact_primary(case, *, cancelled):
    permit = case.permit
    assert permit._optional is case.probe and permit._optional_kind == 'automatic_primary_probe'
    assert permit._cancel is case.event and permit._cancel_callback is case.callback
    assert permit._optional_work_deadline == case.probe['work_deadline'] == case.deadline
    assert case.probe['owner'] is case.manager and case.manager._primary_pending is case.probe
    assert case.event.is_set() is cancelled and not case.callbacks
    assert case.probe['operation'] is None and len(case.child_starts) == 1
    assert permit._completed_sample_retries == 0 and permit._unavailable is None
    assert not permit._sampling_pending


def test_worker_terminal_release_during_held_primary_census_refuses_stale_generation(terminal_case):
    case = terminal_case
    permit, manager, record = case.permit, case.manager, case.record
    reserve_primary(case)
    entered, resume = threading.Event(), threading.Event()
    case.barriers.append(resume)
    outcomes, samples = [], []
    generation, revision = permit._sample_generation, case.comparison['revision']
    observed, sampled_at = dict(permit._observed), permit._last_sample_at
    binding = case.probe['dispatch_binding']
    def census(_):
        assert not permit._condition._is_owned() and not manager._lock._is_owned()
        assert permit._sample_generation is generation and permit._sample_flight is not None
        assert not record['completed'].is_set()
        samples.append((threading.get_ident(), permit._sample_flight))
        entered.set()
        assert resume.wait(case.remaining())
        return sample()
    def check():
        try:outcomes.append(manager._primary_dispatch_current(case.probe, reserved=True))
        except BaseException as error:outcomes.append(error)
    permit._sampler = census
    thread = threading.Thread(target=check, name='terminal-release-primary-census')
    case.threads.append(thread)
    thread.start()
    try:
        assert entered.wait(case.remaining())
        assert permit._sample_flight is samples[0][1]
        # Complete the synthetic task future only after primary admission/census.
        record['completed'].set()
        case.worker._automatic_handoff(request=False)
        assert case.storage == [False] and manager._history_maintenance is None
        assert manager._history_comparison is None and permit._comparison is None
        assert len(case.trace) == 1 and case.trace[0][0] == 'close'
        assert case.trace[0][1] is generation and case.trace[0][2] is case.comparison
        assert case.comparison['revision'] is revision  # Both terminal pauses are redundant.
        assert permit._sample_generation is not generation
        assert permit._sample_flight is samples[0][1]
        assert permit.status()['comparison_reserved_bytes'] == 0
        exact_primary(case, cancelled=False)
        assert not case.current_reads and case.probe['dispatch_binding'] is binding
    finally:
        resume.set();thread.join(case.remaining())
    assert not thread.is_alive() and len(outcomes) == 1
    error = outcomes[0]
    assert type(error) is mod.AdmissionError and error.reason == 'process_sample_unavailable'
    detail = mod.optional_sample_failure(error)
    assert detail['subtype'] == 'stale_generation' and detail['wait_ms'] == 0
    assert detail['grace_used'] is False and detail['reservation_held'] is True
    assert len(samples) == 1 and samples[0][0] == thread.ident
    assert permit._observed == observed and permit._last_sample_at == sampled_at
    assert permit._sample_flight is None and not case.current_reads
    assert case.probe['dispatch_binding'] is binding
    exact_primary(case, cancelled=True)


def test_worker_first_maintenance_releases_terminal_comparison_before_primary_admission(terminal_case):
    case = terminal_case
    permit, manager, record = case.permit, case.manager, case.record
    generation, revision = permit._sample_generation, case.comparison['revision']
    samples, steps = [], []
    def census(_):
        assert not permit._condition._is_owned() and not manager._lock._is_owned()
        assert permit._comparison is None and manager._history_comparison is None
        assert permit._sample_generation is not generation
        assert permit.status()['comparison_reserved_bytes'] == 0
        assert len(case.trace) == 1 and case.trace[0][0] == 'close'
        samples.append((permit._sample_generation, permit._primary_probe_admission, permit._optional))
        return sample()
    def step(**kwargs):
        assert kwargs['stop']() is False
        assert record['completed'].is_set() and permit._comparison is None
        assert permit._optional is None and manager._history_comparison is None
        steps.append(True)
        reserve_primary(case)
        assert manager._primary_dispatch_current(case.probe, reserved=True) is True
    permit._sampler, case.automatic.step = census, step
    record['completed'].set()
    assert permit.request_automatic_turn()
    case.worker._automatic_handoff(request=False)
    assert steps == [True] and case.storage == [False]
    assert len(samples) == 2 and samples[0][0] is samples[1][0] is permit._sample_generation
    assert samples[0][1] is not None and samples[0][2] is None
    assert samples[1][1] is None and samples[1][2] is case.probe
    assert case.current_reads == [('primary', case.probe['reservation'])]
    assert case.comparison['revision'] is revision
    assert case.trace[0][1] is generation and case.trace[0][2] is case.comparison
    assert manager._history_maintenance is None and manager._history_comparison is None
    assert permit._comparison is None and permit._sample_flight is None
    assert permit._turn is None and permit._handoff is None
    assert permit._observed == {os.getpid(): 1.}
    exact_primary(case, cancelled=False)
