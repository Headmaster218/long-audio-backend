"""Prepared-only v4 discovery/coordination tests using real MossJobs/Worker.

No model, audio, database, network or OS child. At most two test threads live
at once, with one original ten-second fixture cutoff including every join.
"""
from contextlib import contextmanager
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_primary_probe_census_coordination_candidate import (
    MIB, assert_exact_reservation, comparison_case, manager_and_worker,
    no_external_work, sample,
)
from audio_backend import moss_handy_jobs as jobs
from audio_backend import native_execution_permit as mod
from audio_backend import recording_speaker_history_process as comparison_process
from audio_backend import recording_speaker_probe_runner as runner
from audio_backend.native_owned_support_cache import ReadError


@pytest.fixture(autouse=True)
def no_real_operation_construction(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No ProbeOperation or ComparisonProcess construction')
    monkeypatch.setattr(runner, 'ProbeOperation', forbidden)
    # comparison_case supplies its existing bounded synthetic driver afterward.
    monkeypatch.setattr(comparison_process, 'ComparisonProcess', forbidden)


@contextmanager
def discovery_case():
    deadline = time.monotonic() + 10.
    permit = mod.NativeExecutionPermit(SimpleNamespace(data_dir='.',
        process_memory_limit_bytes=6 * 1024**3, disk_reserve_bytes=0), sampler=lambda _: sample())
    event, callbacks = threading.Event(), []
    case = SimpleNamespace(permit=permit, token={'cancelled': event, 'work_deadline': deadline},
        event=event, callbacks=callbacks, callback=lambda: callbacks.append(event.is_set()),
        deadline=deadline, remaining=lambda: max(0., deadline - time.monotonic()),
        threads=[], barriers=[], joins=[], configured=True, unresolved=None)
    manager, worker, forbidden, storage = manager_and_worker(case)
    case.manager, case.worker, case.forbidden, case.storage = manager, worker, forbidden, storage
    manager._history_discovery = None
    manager._history_comparison_enabled = lambda: case.configured
    manager._primary_pending = None
    manager._live = manager._pending_record = manager.cell_source = None
    manager._records, manager._probe_staging_bytes, manager.jobs_epoch = {}, 0, 'fixture'
    manager._enabled = lambda: case.configured
    manager._drain_pending = lambda: True
    manager._recover_acknowledged = lambda: None
    manager.results = SimpleNamespace(reconciliation_status=lambda: {})
    manager.history.comparison_work_available = lambda: False
    manager.history.begin_comparison = lambda **kwargs: None
    manager._closed = False
    class Thread(threading.Thread):
        def join(self, timeout=None):
            assert timeout is not None and 0 <= timeout <= 10.
            case.joins.append((self, timeout))
            return super().join(timeout=min(timeout, case.remaining()))
    def factory(**kwargs):
        assert sum(thread.is_alive() for thread in case.threads) < 2
        thread = Thread(**kwargs)
        case.threads.append(thread)
        return thread
    manager._thread_factory = factory
    case.factory = factory
    try:
        yield case
    finally:
        for barrier in case.barriers:barrier.set()
        for slot in ('_history_discovery', '_history_comparison'):
            record = getattr(manager, slot)
            if record is not None:record['cancelled'].set()
        for thread in case.threads:
            if thread.ident is not None:thread.join(case.remaining())
        assert all(not thread.is_alive() for thread in case.threads)
        if case.unresolved is None:
            assert manager._stop_history_comparison()
            assert manager._history_discovery is None and manager._history_comparison is None
            assert permit._comparison is None
        else:
            assert manager._history_comparison is case.unresolved
            assert not case.unresolved['completed'].is_set() and case.unresolved['driver'] is None
            assert not manager._stop_history_comparison()
            assert permit.status()['comparison_reserved_bytes'] == 192 * MIB
        permit.release_optional(case.token, cleaned=True)
        assert permit._optional is None and permit._sample_flight is None
        assert not case.forbidden and time.monotonic() < deadline


def no_grant(case, generation, observed, sampled_at):
    permit = case.permit
    assert permit._comparison is None and permit._sample_generation is generation
    assert permit._observed == observed and permit._last_sample_at == sampled_at
    status = permit.status()
    for key in ('comparison_reserved_bytes', 'comparison_reserved_cpu_lanes', 'comparison_cpu_lanes',
                'comparison_child_rss', 'comparison_child_rss_peak', 'comparison_parent_bytes',
                'comparison_parent_bytes_peak'):
        assert status[key] == 0
    assert status['comparison_startup_rss'] is None
    assert status['comparison_failure'] is None


def test_discovery_during_held_primary_census_preserves_admission_exclusion():
    with discovery_case() as case:
        manager, permit = case.manager, case.permit
        manager._primary_pending = case.token
        permit.reserve_optional(case.token, case.event, case.callback,
            owner_kind='automatic_primary_probe', work_deadline=case.deadline)
        generation, observed, sampled_at = permit._sample_generation, dict(permit._observed), permit._last_sample_at
        entered, resume = threading.Event(), threading.Event()
        case.barriers.append(resume)
        calls, outcomes, reads, discoveries = [], [], [], []
        manager.history.dispatch_current = lambda *args, **kwargs: reads.append(True) or True
        def census(_):
            assert not permit._condition._is_owned() and not manager._lock._is_owned()
            calls.append(threading.get_ident());entered.set()
            assert resume.wait(case.remaining())
            return sample()
        def check():
            try:manager._primary_dispatch_current(case.token, reserved=True)
            except BaseException as error:outcomes.append(error)
            else:outcomes.append(None)
        permit._sampler = census
        primary = case.factory(target=check)
        primary.start()
        assert entered.wait(case.remaining())
        flight = permit._sample_flight
        clock = [time.monotonic()]
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(permit, 'reserve_comparison', side_effect=AssertionError('No discovery grant')), \
                patch.object(manager.history, 'begin_comparison', side_effect=AssertionError('No authoritative begin')):
            for cycle in range(2):
                opened, close = threading.Event(), threading.Event()
                case.barriers.append(close)
                def available():
                    assert not manager._lock._is_owned() and not permit._condition._is_owned()
                    discoveries.append(threading.get_ident());opened.set()
                    assert close.wait(case.remaining())
                    return False
                manager.history.comparison_work_available = available
                case.worker._automatic_handoff(request=False)
                assert opened.wait(case.remaining())
                record = manager._history_discovery
                assert record is not None and manager._history_comparison is None
                thread = record['thread']
                assert thread is case.threads[-1] and thread is not primary
                assert not record['completed'].is_set() and record['snapshot'] is None and record['driver'] is None
                # Public ownership is empty, but the live discovery must be retained.
                manager._primary_pending = None
                assert manager.health()['history_comparison_state'] is None
                manager._primary_pending = case.token
                no_grant(case, generation, observed, sampled_at)
                assert permit._sample_flight is flight and calls == [primary.ident]
                close.set();thread.join(case.remaining())
                assert record['completed'].is_set() and not thread.is_alive()
                joins = len(case.joins)
                case.worker._automatic_handoff(request=False)
                assert any(t is thread and timeout == 0 for t, timeout in case.joins[joins:])
                assert manager._history_discovery is None
                no_grant(case, generation, observed, sampled_at)
                assert_exact_reservation(case, cancelled=False)
                clock[0] = manager._history_compare_after
        resume.set();primary.join(case.remaining())
        assert outcomes == [None] and reads == [True] and len(discoveries) == 2
        assert permit._sample_generation is generation and permit._sample_flight is None
        assert_exact_reservation(case, cancelled=False)
        assert len(case.threads) == 3 and all(not thread.is_alive() for thread in case.threads)


@pytest.mark.parametrize('failure', ['read', 'close'])
def test_discovery_errors_are_pending_without_native_admission(failure):
    now = time.monotonic()
    with discovery_case() as case, patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: now)):
        manager, permit = case.manager, case.permit
        generation, observed, stamp = permit._sample_generation, dict(permit._observed), permit._last_sample_at
        calls = []
        def available():
            calls.append(failure)
            raise OSError('synthetic_' + failure)
        manager.history.comparison_work_available = available
        with patch.object(permit, 'reserve_comparison', side_effect=AssertionError('No error grant')):
            assert manager.maintain_history_comparison()
            thread = case.threads[0];thread.join(case.remaining())
            record = manager._history_discovery
            assert record is not None and record['state'] == 'pending' and record['completed'].is_set()
            no_grant(case, generation, observed, stamp)
            assert manager.maintain_history_comparison()
        assert calls == [failure] and manager._history_discovery is None


@pytest.mark.parametrize('change', ['none', 'cancel_after_reserve', 'configuration_after_reserve', 'deadline_after_reserve'])
def test_true_hint_promotes_same_thread_event_token_and_original_clock(change):
    with discovery_case() as case:
        manager, permit = case.manager, case.permit
        opened, close = threading.Event(), threading.Event()
        case.barriers.append(close)
        def available():opened.set();assert close.wait(case.remaining());return True
        manager.history.comparison_work_available = available
        begins, reserves, bindings = [], [], []
        clock = [time.monotonic()]
        original_reserve, original_bind = permit.reserve_comparison, permit.bind_comparison_thread
        saved = {}
        def reserve(token, event, **kwargs):
            record = saved['record']
            assert manager._history_discovery is None and manager._history_comparison is record
            assert threading.current_thread() is saved['thread'] and record['thread'] is saved['thread']
            assert not manager._lock._is_owned() and not permit._condition._is_owned()
            assert (token, event) == (saved['grant'], saved['cancelled'])
            assert record['completed'] is saved['completed'] and record['generation'] is saved['generation']
            assert record['admission_start'] == saved['start'] and record['deadline'] == saved['deadline']
            assert kwargs == {'memory_bytes': 192 * MIB}
            reserves.append(token);answer = original_reserve(token, event, **kwargs)
            if change == 'cancel_after_reserve':event.set()
            elif change == 'configuration_after_reserve':case.configured = False
            elif change == 'deadline_after_reserve':clock[0] = record['deadline']
            return answer
        def bind(token, thread, completed):
            bindings.append((token, thread, completed))
            assert thread is threading.current_thread() and thread.is_alive()
            return original_bind(token, thread, completed)
        def begin(**kwargs):
            assert permit.status()['comparison_reserved_bytes'] == 192 * MIB
            assert permit._comparison['thread'] is threading.current_thread()
            begins.append(kwargs);return None  # True-to-empty/revoked hint grants no authority.
        manager.history.begin_comparison = begin
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(permit, 'reserve_comparison', side_effect=reserve), \
                patch.object(permit, 'bind_comparison_thread', side_effect=bind):
            assert manager.maintain_history_comparison()
            assert opened.wait(case.remaining())
            record = manager._history_discovery
            saved.update(record=record, thread=record['thread'], completed=record['completed'],
                grant=record['grant'], cancelled=record['cancelled'], generation=record['generation'],
                start=record['admission_start'], deadline=record['deadline'])
            assert saved['deadline'] == saved['start'] + 300.
            close.set();record['thread'].join(case.remaining())
            assert record['completed'].is_set() and len(case.threads) == 1
            assert reserves == [saved['grant']]
            assert bindings == [(saved['grant'], saved['thread'], saved['completed'])]
            assert len(begins) == (1 if change == 'none' else 0)
            assert record['snapshot'] is None and record['driver'] is None
            assert manager._history_discovery is None and manager._history_comparison is record
            assert manager.health()['history_comparison_state'] == record['state']
            assert permit.status()['comparison_reserved_bytes'] == 192 * MIB
            assert manager._stop_history_comparison()


def test_active_primary_refusal_waits_without_mutation_then_fresh_admission():
    with discovery_case() as case:
        manager, permit = case.manager, case.permit
        permit.reserve_optional(case.token, case.event, case.callback,
            owner_kind='automatic_primary_probe', work_deadline=case.deadline)
        generation, observed, stamp = permit._sample_generation, dict(permit._observed), permit._last_sample_at
        manager.history.comparison_work_available = lambda: True
        original_reserve = permit.reserve_comparison
        calls, waits, begins = [], [], []
        manager.history.begin_comparison = lambda **kwargs: begins.append(True) or None
        def reserve(token, event, **kwargs):
            calls.append(token)
            if len(calls) == 1:
                original_wait = event.wait
                def wait(seconds):
                    assert 0 < seconds <= .05
                    no_grant(case, generation, observed, stamp)
                    assert not begins and manager._history_comparison['snapshot'] is None
                    assert not manager._lock._is_owned() and not permit._condition._is_owned()
                    waits.append(seconds)
                    permit.release_optional(case.token, cleaned=True)
                    event.wait = original_wait
                    return False
                event.wait = wait
            return original_reserve(token, event, **kwargs)
        with patch.object(permit, 'reserve_comparison', side_effect=reserve):
            assert manager.maintain_history_comparison()
            case.threads[0].join(case.remaining())
        record = manager._history_comparison
        assert calls == [record['grant'], record['grant']] and len(waits) == 1 and begins == [True]
        assert record['completed'].is_set() and record['deadline'] == record['admission_start'] + 300.


@pytest.mark.parametrize('outcome', ['plain_error', 'reserve_unknown', 'bind_unknown'])
def test_nonpending_or_uncertain_grant_outcome_retains_original_owner(outcome):
    with discovery_case() as case:
        manager, permit = case.manager, case.permit
        manager.history.comparison_work_available = lambda: True
        reserves, binds, begins = [], [], []
        manager.history.begin_comparison = lambda **kwargs: begins.append(True) or None
        real_reserve, real_bind = permit.reserve_comparison, permit.bind_comparison_thread
        def reserve(*args, **kwargs):
            reserves.append(args[0])
            if outcome == 'plain_error':raise mod.AdmissionError('comparison_pending')
            answer = real_reserve(*args, **kwargs)
            if outcome == 'reserve_unknown':raise RuntimeError('uncertain_reserve')
            return answer
        def bind(*args):
            binds.append(args);real_bind(*args)
            raise RuntimeError('uncertain_bind')
        with patch.object(permit, 'reserve_comparison', side_effect=reserve), \
                patch.object(permit, 'bind_comparison_thread', side_effect=bind):
            assert manager.maintain_history_comparison()
            case.threads[0].join(case.remaining())
        record = manager._history_comparison
        assert record is not None and record['thread'] is case.threads[0] and record['completed'].is_set()
        assert len(reserves) == 1 and not begins and len(binds) == (1 if outcome == 'bind_unknown' else 0)
        assert permit.status()['comparison_reserved_bytes'] == (0 if outcome == 'plain_error' else 192 * MIB)
        assert manager._stop_history_comparison() and manager._history_comparison is None


@pytest.mark.parametrize('stop', ['session', 'close_admission'])
def test_stop_before_promotion_retains_discovery_until_positive_join(stop):
    with discovery_case() as case:
        manager, permit = case.manager, case.permit
        opened, resume = threading.Event(), threading.Event()
        case.barriers.append(resume)
        def available():opened.set();assert resume.wait(case.remaining());return True
        manager.history.comparison_work_available = available
        assert manager.maintain_history_comparison() and opened.wait(case.remaining())
        record = manager._history_discovery
        assert record['session_id'] is None
        if stop == 'close_admission':manager.close_admission()
        original_join = record['thread'].join
        with patch.object(record['thread'], 'join', side_effect=lambda timeout: None):
            assert manager._stop_history_comparison('some-session') is False
        assert record['cancelled'].is_set() and manager._history_discovery is record
        assert not record['completed'].is_set() and permit._comparison is None
        resume.set();original_join(case.remaining())
        joins = len(case.joins)
        assert manager._stop_history_comparison('some-session')
        assert any(t is record['thread'] for t, _ in case.joins[joins:])
        assert manager._history_discovery is None and manager._history_comparison is None and permit._comparison is None


def test_start_exception_retains_original_handle_and_forbids_late_promotion():
    with discovery_case() as case:
        manager = case.manager
        original_factory = case.factory
        starts = []
        def factory(**kwargs):
            thread = original_factory(**kwargs)
            original_start = thread.start
            def uncertain_start():starts.append(thread);raise RuntimeError('synthetic_start_uncertain')
            thread.start = uncertain_start
            thread.fixture_original_start = original_start
            return thread
        manager._thread_factory = factory
        assert manager.maintain_history_comparison() is False
        record = manager._history_discovery
        assert record is not None and record['thread'] is starts[0] and record['cancelled'].is_set()
        assert not record['completed'].is_set() and manager._history_comparison_pending
        assert not manager._stop_history_comparison()
        # A delayed original start can only observe cancellation and complete.
        record['thread'].fixture_original_start();record['thread'].join(case.remaining())
        assert record['completed'].is_set() and manager._history_comparison is None
        assert manager._stop_history_comparison() and case.permit._comparison is None


@pytest.mark.parametrize('kick', ['during_start', 'during_read', 'missed_periodic', 'restart'])
def test_kicks_and_ordinary_due_discovery_are_not_lost(kick):
    with discovery_case() as case:
        manager = case.manager
        opened, resume = threading.Event(), threading.Event()
        case.barriers.append(resume)
        clock = [time.monotonic()]
        reads = []
        def available():
            reads.append(True)
            if len(reads) == 1:opened.set();assert resume.wait(case.remaining())
            return False
        manager.history.comparison_work_available = available
        if kick == 'during_start':
            original_factory = case.factory
            def factory(**kwargs):
                thread = original_factory(**kwargs);start = thread.start
                def starting():manager._kick_history_comparison();return start()
                thread.start = starting
                return thread
            manager._thread_factory = factory
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: clock[0])):
            assert manager._history_compare_after == 0. and not manager._history_comparison_pending
            assert manager.maintain_history_comparison() and opened.wait(case.remaining())
            if kick == 'during_read':manager._kick_history_comparison()
            resume.set();case.threads[0].join(case.remaining())
            if kick in ('during_start', 'during_read'):assert manager._history_comparison_pending
            else:
                assert not manager._history_comparison_pending
                clock[0] = manager._history_compare_after if kick == 'missed_periodic' else clock[0]
                if kick == 'restart':manager._history_compare_after = 0.
            assert manager.maintain_history_comparison()
            case.threads[1].join(case.remaining())
            assert reads == [True, True] and len(case.threads) == 2


def test_unknown_finish_blocks_release_and_successor_until_acknowledged():
    now = time.monotonic()
    with discovery_case() as case, patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: now)):
        manager, permit = case.manager, case.permit
        manager.history.comparison_work_available = lambda: True
        snapshot = {'scope_seq': 1, 'session_id': 'fixture', 'token': object()}
        manager.history.begin_comparison = lambda **kwargs: snapshot
        manager._compare_history_snapshot = lambda record, value: {'state': 'refused', 'reason': 'unknown_rival'}
        attempts = []
        def finish(value, reason):
            attempts.append((value, reason))
            if len(attempts) <= 2:raise ReadError(503, 'storage_unknown')
        manager.history.finish_comparison = finish
        assert manager.maintain_history_comparison()
        case.threads[0].join(case.remaining())
        record = manager._history_comparison
        assert record['finish_pending'] == 'unknown_rival' and record['state'] == 'storage_pending'
        assert permit.status()['comparison_reserved_bytes'] == 192 * MIB
        assert manager.maintain_history_comparison() is False
        assert manager._history_comparison is record and len(case.threads) == 1
        assert manager.maintain_history_comparison()
        assert manager._history_comparison is None and permit._comparison is None
        assert attempts == [(snapshot, 'unknown_rival')] * 3


def test_late_rival_revalidates_original_snapshot_and_cannot_inherit_old_refusal():
    with discovery_case() as case:
        manager = case.manager
        manager.history.comparison_work_available = lambda: True
        snapshot = {'scope_seq': 1, 'session_id': 'fixture', 'token': object()}
        begins, finishes, current, comparisons = [], [], [], []
        manager.history.begin_comparison = lambda **kwargs: begins.append(kwargs) or snapshot
        manager._compare_history_snapshot = lambda record, value: comparisons.append(value) or {'saved': False, 'reason': 'stale'}
        manager.history.finish_comparison = lambda value, reason: finishes.append((value, reason))
        def currentness(value):current.append(value);return 'invalid'
        manager.history.comparison_current = currentness
        assert manager.maintain_history_comparison()
        case.threads[0].join(case.remaining())
        record = manager._history_comparison
        assert begins == [{'after_scope': None}] and comparisons == [snapshot]
        assert current == [snapshot] and finishes == [(snapshot, 'stale')]
        assert record['cancelled'].is_set() and record['reason'] == 'history_authority_changed'
        assert record['snapshot'] is snapshot and record['task_sequence'] == 0


@pytest.mark.parametrize('change', ['cancel', 'configuration', 'deadline', 'generation'])
def test_discovery_read_and_close_cannot_bypass_promotion_fence(change):
    with discovery_case() as case:
        manager, permit = case.manager, case.permit
        opened, resume = threading.Event(), threading.Event()
        case.barriers.append(resume)
        clock = [time.monotonic()]
        def available():opened.set();assert resume.wait(case.remaining());return True
        manager.history.comparison_work_available = available
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(permit, 'reserve_comparison', side_effect=AssertionError('No stale promotion')):
            assert manager.maintain_history_comparison() and opened.wait(case.remaining())
            record = manager._history_discovery
            if change == 'cancel':record['cancelled'].set()
            elif change == 'configuration':case.configured = False
            elif change == 'deadline':clock[0] = record['deadline']
            else:record['generation'] = object()
            resume.set();record['thread'].join(case.remaining())
            assert record['completed'].is_set() and manager._history_discovery is record
            assert manager._history_comparison is None and permit._comparison is None
            assert record['snapshot'] is None and record['driver'] is None


def test_typed_runner_deadline_is_not_overwritten_by_discovery_finally():
    with discovery_case() as case:
        manager = case.manager
        clock = [time.monotonic()]
        snapshot = {'scope_seq': 1, 'session_id': 'fixture', 'token': object()}
        manager.history.comparison_work_available = lambda: True
        manager.history.begin_comparison = lambda **kwargs: snapshot
        finishes, pauses = [], []
        manager.history.finish_comparison = lambda value, reason: finishes.append((value, reason))
        def compare(record, value):
            clock[0] = record['deadline']
            raise comparison_process.ProcessStepDeadline()
        manager._compare_history_snapshot = compare
        original_pause = case.permit.pause_comparison
        def pause(grant):pauses.append(grant);return original_pause(grant)
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(case.permit, 'pause_comparison', side_effect=pause):
            assert manager.maintain_history_comparison()
            case.threads[0].join(case.remaining())
            record = manager._history_comparison
            assert record['completed'].is_set()
            assert record['process_failure_reason'] == 'comparison_step_deadline'
            assert record['reason'] == 'comparison_step_deadline'
            assert finishes == [(snapshot, 'deadline')] and pauses == [record['grant']]


def test_runner_finally_exception_retains_uncompleted_original_record_and_charge():
    with discovery_case() as case:
        manager = case.manager
        manager.history.comparison_work_available = lambda: True
        errors = []
        original_factory = case.factory
        def factory(**kwargs):
            target, args = kwargs.pop('target'), kwargs.pop('args')
            def run():
                try:target(*args)
                except BaseException as error:errors.append(error)
            return original_factory(target=run, **kwargs)
        manager._thread_factory = factory
        error = RuntimeError('synthetic_finalization_failure')
        with patch.object(case.permit, 'pause_comparison', side_effect=error):
            assert manager.maintain_history_comparison()
            case.threads[0].join(case.remaining())
        record = manager._history_comparison
        case.unresolved = record
        assert errors == [error] and not record['completed'].is_set()
        assert record['thread'] is case.threads[0] and not record['thread'].is_alive()
        assert manager.maintain_history_comparison() is False
        assert manager._stop_history_comparison() is False
        assert manager._history_comparison is record and case.permit._comparison['thread'] is record['thread']
        assert case.permit.status()['comparison_reserved_bytes'] == 192 * MIB


def test_cached_owner_reuses_charged_path_without_discovery_or_new_grant(comparison_case):
    case = comparison_case
    manager, permit, record = case.manager, case.permit, case.record
    deadline = record['deadline']
    assert manager._history_comparison_driver(record, record['snapshot']['token']) is record['driver']
    driver = record['driver']
    cached = object()
    driver.cache_binding = cached
    driver.closed_cache_binding = lambda: cached
    manager._history_discovery = manager._history_maintenance = None
    manager._history_comparison = record
    manager._history_comparison_pending = True
    manager._history_compare_after = 0.
    record.update(completed=threading.Event(), thread=None, generation=object(), start_attempted=False,
        finish_pending=None, session_id='fixture')
    record['completed'].set()
    threads = []
    def factory(**kwargs):
        assert not threads
        thread = threading.Thread(**kwargs);threads.append(thread);return thread
    manager._thread_factory = factory
    manager.history.begin_comparison = lambda **kwargs: None
    manager.history.comparison_work_available = lambda: (_ for _ in ()).throw(AssertionError('No cached discovery'))
    def census(_):
        base = sample()
        identities = dict(base.identities)
        if not driver.process.exited:identities[driver.process.pid] = (2., os.getpid(), MIB)
        return mod.ProcessSample(identities, base.rss + (MIB if not driver.process.exited else 0),
            base.caller_rss, base.available, base.disk_free)
    permit._sampler = census
    try:
        with patch.object(permit, 'reserve_comparison', side_effect=AssertionError('No cached regrant')):
            assert manager.maintain_history_comparison()
            threads[0].join(max(0., deadline - time.monotonic()))
        assert not threads[0].is_alive() and record['completed'].is_set()
        assert manager._history_discovery is None and manager._history_comparison is record
        assert record['state'] == 'cached' and record['driver'] is driver and driver.cache_binding is cached
        assert permit._comparison is case.permit_record and permit._comparison['thread'] is threads[0]
        assert permit.status()['comparison_reserved_bytes'] == 192 * MIB
        assert record['deadline'] == record['admission_start'] + 300.
    finally:
        record['cancelled'].set()
        for thread in threads:
            if thread.ident is not None:thread.join(max(0., deadline - time.monotonic()))
        assert all(not thread.is_alive() for thread in threads)
        assert time.monotonic() < deadline


def test_active_primary_pending_wait_is_clipped_to_original_deadline():
    with discovery_case() as case:
        manager, permit = case.manager, case.permit
        permit.reserve_optional(case.token, case.event, case.callback,
            owner_kind='automatic_primary_probe', work_deadline=case.deadline)
        generation, observed, stamp = permit._sample_generation, dict(permit._observed), permit._last_sample_at
        clock, calls, waits = [10.], [], []
        def available():
            clock[0] = manager._history_discovery['deadline'] - .01
            return True
        manager.history.comparison_work_available = available
        original_reserve = permit.reserve_comparison
        def reserve(token, event, **kwargs):
            calls.append(token)
            def wait(seconds):
                assert 0 < seconds <= .010001
                waits.append(seconds)
                no_grant(case, generation, observed, stamp)
                clock[0] += seconds
                return False
            event.wait = wait
            return original_reserve(token, event, **kwargs)
        with patch.object(jobs, 'time', SimpleNamespace(monotonic=lambda: clock[0])), \
                patch.object(permit, 'reserve_comparison', side_effect=reserve), \
                patch.object(manager.history, 'begin_comparison', side_effect=AssertionError('No expired begin')):
            assert manager.maintain_history_comparison()
            case.threads[0].join(case.remaining())
            record = manager._history_comparison
            assert record['deadline'] == 310. and record['admission_start'] == 10.
            assert clock[0] == record['deadline'] and len(calls) == len(waits) == 1
            assert record['reason'] == 'comparison_deadline' and record['completed'].is_set()
            no_grant(case, generation, observed, stamp)


def test_stop_after_slot_transfer_tracks_same_unfinished_original_thread():
    with discovery_case() as case:
        manager, permit = case.manager, case.permit
        manager.history.comparison_work_available = lambda: True
        entered, resume = threading.Event(), threading.Event()
        case.barriers.append(resume)
        original_reserve = permit.reserve_comparison
        def reserve(*args, **kwargs):
            entered.set();assert resume.wait(case.remaining())
            return original_reserve(*args, **kwargs)
        with patch.object(permit, 'reserve_comparison', side_effect=reserve):
            assert manager.maintain_history_comparison() and entered.wait(case.remaining())
            record = manager._history_comparison
            assert manager._history_discovery is None and record['thread'] is case.threads[0]
            with patch.object(record['thread'], 'join', side_effect=lambda timeout: None):
                assert manager._stop_history_comparison() is False
            assert record['cancelled'].is_set() and manager._history_comparison is record
            assert not record['completed'].is_set()
            resume.set();record['thread'].join(case.remaining())
        assert record['completed'].is_set() and permit._comparison is None
        assert manager._stop_history_comparison() and manager._history_comparison is None
