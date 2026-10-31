"""Real primary producer ownership across one pre-dispatch before_input census.

The existing Worker/manager/permit/ProbeOperation/SQLite fixture stays unchanged.
Pause the real reserved=True dispatch check before any operation or driver is
constructed. Later ProbeOperation input and fake-driver running gates remain
real. No model, native child, comparator child, network, deadline change or replay.
PRIMARY_PROBE_BEFORE_INPUT_BASELINE exposes authenticated current-dev55 controls;
the older running-only test's historical baseline is not used here.
"""
import hashlib
import json
import struct
import threading

import pytest

from audio_backend import native_execution_permit as permit_module
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_probe_runner as runner
from test_moss_automatic_integration import automatic_app_factory
from test_moss_handy_jobs import ManualThread
from test_primary_probe_reservation_fingerprint import before_input_implementation
from test_recording_speaker_history_integration import (
    primary_history_factory, ingest_recording, _cohort, _counts,
    _observations, _observation_identity, _primary_rows, RATE,
)
from test_recording_speaker_links import vector


@pytest.mark.parametrize('transition', ['turn_exit', 'queue_demand', 'owner_callback'])
def test_real_primary_before_input_census_preserves_only_exact_authority(
        primary_history_factory, before_input_implementation, monkeypatch, transition):
    module, baseline = before_input_implementation
    # Bind both the real app constructor and diagnostic accessor to the same
    # authenticated implementation; preserve baseline exception/marker identity.
    monkeypatch.setattr(permit_module, 'NativeExecutionPermit', module.NativeExecutionPermit)
    monkeypatch.setattr(permit_module, 'optional_sample_failure', module.optional_sample_failure)
    with primary_history_factory() as h:
        cid = ingest_recording(h, frames=10 * RATE)[0]
        assert _cohort(h) == cid
        probe, permit = h.jobs._primary_pending, h.jobs.permit
        assert type(permit) is module.NativeExecutionPermit
        assert probe['owner'] is h.jobs and probe['state'] == 'pending'
        admitted = _observations(h)[0]
        primary_rows = _primary_rows(h)
        native_calls = [list(native.calls) for native in h.natives]
        reservation, node, crop = probe['reservation'], probe['node'], probe['crop']
        reservation_value = tuple(sorted(reservation.items()))
        invocation, attempt_id = probe['invocation_id'], probe['attempt_id']
        entered, resume = threading.Event(), threading.Event()
        dispatching, census = threading.Event(), threading.Event()
        samples, fingerprints, checks, results, mutations = [], [], [], [], []
        reservations, executions, callbacks, dispatches, later_checks, waits = [], [], [], [], [], []
        original_sample = permit._sampler
        original_state = permit._optional_state_locked
        original_check = permit.check_optional
        original_reserve = permit.reserve_optional
        original_condition_wait = permit._condition.wait
        original_dispatch = h.jobs._primary_dispatch_current
        original_factory = h.jobs._probe_factory

        def sampled(settings):
            result = original_sample(settings)
            if census.is_set():
                assert dispatching.is_set()
                assert not permit._condition._is_owned()
                assert not h.jobs._lock._is_owned()
                assert not h.owner._publication_guard._is_owned()
                assert probe['operation'] is None and not h.probes and not h.drivers
                samples.append(threading.get_ident())
                assert len(samples) == 1
                entered.set()
                assert resume.wait(5.), 'before_input census was not released'
            return result

        def fingerprint(**kwargs):
            state = original_state(**kwargs)
            if census.is_set():
                assert dispatching.is_set() and probe['operation'] is None
                assert permit._optional is probe
                assert permit._optional_kind == 'automatic_primary_probe'
                fingerprints.append(kwargs.get('primary_probe_running', False))
            return state

        def checked(token, phase='running', **kwargs):
            assert token is probe
            if dispatching.is_set():
                assert phase == 'before_input' and probe['operation'] is None
                assert not census.is_set() and not checks
                checks.append((token, phase))
                census.set()
                try:
                    value = original_check(token, phase, **kwargs)
                except BaseException as error:
                    results.append(error)
                    raise
                else:
                    results.append(None)
                    return value
                finally:
                    census.clear()
            assert probe['operation'] is not None and not census.is_set()
            later_checks.append((token, phase))
            return original_check(token, phase, **kwargs)

        def reserved(token, *args, **kwargs):
            assert kwargs['owner_kind'] == 'automatic_primary_probe'
            reservations.append(token)
            return original_reserve(token, *args, **kwargs)

        def condition_wait(*args, **kwargs):
            assert not census.is_set(), 'before_input must not wait or regrant'
            return original_condition_wait(*args, **kwargs)

        def dispatch_current(token, *, reserved):
            assert token is probe and probe['operation'] is None
            dispatches.append((token, reserved))
            if not reserved:
                return original_dispatch(token, reserved=False)
            assert not dispatching.is_set()
            dispatching.set()
            try:
                return original_dispatch(token, reserved=True)
            finally:
                dispatching.clear()

        def driver_factory(directory, digest):
            assert not census.is_set() and not dispatching.is_set()
            driver = original_factory(directory, digest)
            original_wait = driver.wait

            def wait(cancelled):
                assert cancelled is probe['cancelled']
                assert driver._service_gate.owner is h.jobs
                assert driver._service_gate.probe is probe
                assert h.jobs._primary_pending is probe and permit._optional is probe
                waits.append(driver)
                assert len(waits) == 1
                # The same gate-aware synthetic probe edge as the running
                # integration; do not replace or dispatch a MOSS text fixture.
                driver._service_gate.check('running')
                return original_wait(cancelled)

            driver.wait = wait
            return driver

        monkeypatch.setattr(permit, '_sampler', sampled)
        monkeypatch.setattr(permit, '_optional_state_locked', fingerprint)
        monkeypatch.setattr(permit, 'check_optional', checked)
        monkeypatch.setattr(permit, 'reserve_optional', reserved)
        monkeypatch.setattr(permit._condition, 'wait', condition_wait)
        monkeypatch.setattr(h.jobs, '_primary_dispatch_current', dispatch_current)
        monkeypatch.setattr(h.jobs, '_probe_factory', driver_factory)
        assert permit.request_automatic_turn()
        turn = permit.automatic_turn()
        assert turn.__enter__()
        claim = waiting = executing = None
        try:
            assert h.jobs.submit_primary()
            assert reservations == [probe] and permit._optional is probe
            assert dispatches == [(probe, False)]
            # automatic_idle legitimately queued this exact deferred comparison.
            # Its ManualThread remains undispatched until the cancelled drain.
            comparison = h.jobs._history_comparison
            comparison_permit = permit._comparison
            assert comparison is not None and comparison_permit is not None
            comparison_thread = comparison['thread']

            def assert_comparison_deferred():
                assert h.jobs._history_comparison is comparison
                assert permit._comparison is comparison_permit
                assert comparison['thread'] is comparison_thread
                assert type(comparison_thread) is ManualThread
                assert comparison_thread.target == h.jobs._run_history_comparison
                assert comparison_thread.args == (comparison,)
                assert comparison_thread.is_alive() and comparison['start_attempted']
                assert comparison['state'] == 'starting'
                assert not comparison['completed'].is_set()
                assert not comparison['cancelled'].is_set()
                assert comparison['driver'] is comparison['snapshot'] is None
                assert comparison['driver_active'] is False and not h.drivers
                assert comparison_permit['token'] is comparison['grant']
                assert comparison_permit['thread'] is comparison_thread
                assert comparison_permit['completed'] is comparison['completed']
                assert comparison_permit['cancelled'] is comparison['cancelled']
                assert comparison_permit['child'] is comparison_permit['owner'] is None
                assert comparison_permit['sampling'] is None
                assert comparison_permit['parent_bytes'] == 0

            assert_comparison_deferred()
            callback, generation = permit._cancel_callback, permit._sample_generation
            deadline, binding = probe['work_deadline'], probe['dispatch_binding']
            assert deadline is not None and probe['crop'] is crop
            assert probe['reservation'] is reservation and probe['node'] is node
            assert binding[:4] == (reservation, crop, invocation, node)
            assert binding[4] == reservation_value
            original_run = probe['thread'].run

            def run_once():
                executions.append(probe['thread'])
                assert len(executions) == 1, 'original producer must not replay'
                original_run()

            # Drain the original manager-owned ManualThread in one real thread,
            # so only the original caller can close its thread-local turn.
            monkeypatch.setattr(probe['thread'], 'run', run_once)
            if transition != 'turn_exit':
                turn.__exit__(None, None, None)
                turn = None
            executing = threading.Thread(target=probe['thread'].run,
                name='synthetic-primary-before-input-census', daemon=True)
            executing.start()
            assert entered.wait(5.), 'reserved before_input census was not reached'
            assert dispatches == [(probe, False), (probe, True)]
            assert checks == [(probe, 'before_input')] and len(samples) == 1
            assert fingerprints == [not baseline]
            assert permit._optional is probe and permit._cancel is probe['cancelled']
            assert not probe['cancelled'].is_set() and not probe['completed'].is_set()
            assert probe['operation'] is None and not h.probes and not h.drivers
            assert not tuple(h.jobs.directory.iterdir()) and not later_checks and not waits
            assert probe['dispatch_binding'] is binding and probe['work_deadline'] == deadline
            assert probe['reservation'] is reservation and probe['crop'] is crop
            assert probe['node'] is node and probe['attempt_id'] == attempt_id
            assert tuple(sorted(reservation.items())) == reservation_value
            assert _counts(h) == {'observations': 1, 'vectors': 0, 'pending': 1,
                                  'candidate_missing': 0, 'attempts': 1}
            assert_comparison_deferred()
            if transition == 'turn_exit':
                assert permit._turn is not None and permit._handoff is permit._turn
                turn.__exit__(None, None, None)
                turn = None
                assert permit._turn is permit._handoff is None
                assert getattr(permit._local, 'turn', None) is None
            elif transition == 'queue_demand':
                claim = permit.claim_probe()
                claim.__enter__()
                waiting = permit.primary_intent('worker')
                assert permit._probe and not waiting.granted and not waiting.entered
                assert not waiting.released
            else:
                with permit._condition:
                    assert permit._cancel_callback is callback
                    permit._cancel_callback = lambda: callbacks.append(True)
                assert permit._cancel_callback is not callback
            mutations.append(transition)
            assert permit._sample_generation is generation
            assert permit._optional is probe and permit._cancel is probe['cancelled']
            assert probe['operation'] is None and probe['work_deadline'] == deadline
            resume.set()
            executing.join(5.)
            assert not executing.is_alive(), 'original producer did not close'
        finally:
            resume.set()
            if executing is not None:
                executing.join(5.)
                assert not executing.is_alive(), 'original producer still owns its work'
            if claim is not None:claim.__exit__(None, None, None)
            if waiting is not None:waiting.release()
            if turn is not None:turn.__exit__(None, None, None)

        success = not baseline and transition != 'owner_callback'
        assert mutations == [transition] and reservations == [probe]
        assert executions == [probe['thread']] and checks == [(probe, 'before_input')]
        assert dispatches == [(probe, False), (probe, True)]
        assert samples == [executing.ident] and fingerprints == [not baseline] * 2
        assert len(results) == 1 and callbacks == []
        assert not census.is_set() and not dispatching.is_set()
        assert probe['reservation'] is reservation and probe['node'] is node
        assert tuple(sorted(reservation.items())) == reservation_value
        assert probe['attempt_id'] == attempt_id and probe['work_deadline'] == deadline
        operation = probe['operation']
        if success:
            assert results == [None] and not probe['cancelled'].is_set()
            assert later_checks == [(probe, 'before_input'), (probe, 'running')]
            assert type(operation) is runner.ProbeOperation and operation.started
            assert operation.cleaned and runner.cleanup_verified(operation.receipt)
            assert operation.work_deadline == deadline
            assert operation.child_dispatch == 'returned_validated'
            assert operation.claim is operation.claim_identity is None
            assert len(h.probes) == h.probes[0].starts == h.probes[0].cleanups == 1
            assert waits == [h.probes[0]]
        else:
            assert type(results[0]) is module.AdmissionError
            assert results[0].reason == 'optional_ownership_changed'
            assert probe['cancelled'].is_set()
            assert operation is None and not h.probes and not later_checks and not waits
        assert probe['completed'].is_set() and not probe['thread'].is_alive()
        assert permit._optional is None and permit._sample_flight is None
        assert h.jobs._maintain_primary()
        assert h.jobs._primary_pending is None and probe['crop'] is None
        assert probe['charge_bytes'] == h.jobs._probe_staging_bytes == 0
        assert not h.owner._pins and not tuple(h.jobs.directory.iterdir())
        assert_comparison_deferred()
        observation = _observations(h)[0]
        assert _observation_identity(observation) == _observation_identity(admitted)
        assert _counts(h) == {'observations': 1, 'vectors': int(success), 'pending': 0,
                              'candidate_missing': int(not success), 'attempts': 1}
        with h.store.db() as db:
            attempt = dict(db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?',
                                     (attempt_id,)).fetchone())
        assert attempt['token'] == attempt_id
        assert attempt['state'] == ('success' if success else 'failure')
        assert attempt['reserved_bytes'] == attempt['staging_bytes'] == 0
        assert attempt['invocation_id'] == probe['invocation_id'] == invocation
        assert attempt['terminal_sha256'] == hashlib.sha256(attempt['terminal']).hexdigest()
        if success:
            expected = struct.pack('<512f', *vector(1., 0.))
            assert observation['vector'] == expected
            assert observation['vector_sha256'] == hashlib.sha256(expected).hexdigest()
            assert operation.failure_json is probe['failure_json'] is None
        else:
            assert observation['vector'] is None and probe['failure_frozen']
            assert attempt['terminal'] == probe['failure_json']
            failure = json.loads(attempt['terminal'])
            assert failure['stage'] == 'dispatch_validation'
            assert failure['child_dispatch'] == 'not_attempted'
            assert failure['reason'] == 'optional_cancelled'
            assert failure['error_type'] == 'AdmissionError'
            assert failure['sampling'] == module.optional_sample_failure(results[0])
            assert failure['sampling']['subtype'] == 'ownership_changed'
            assert failure['sampling']['reservation_held'] is True
            assert failure['sampling']['wait_ms'] == 0 and not failure['sampling']['grace_used']
        for _ in range(2):
            assert h.jobs._maintain_primary() and not h.jobs.submit_primary()
        assert len(h.probes) == int(success)
        if success:assert h.probes[0].starts == h.probes[0].cleanups == 1
        assert reservations == [probe] and executions == [probe['thread']]
        assert dispatches == [(probe, False), (probe, True)]
        assert _primary_rows(h) == primary_rows and h.store.chunk(cid)['next_offset'] == 10.
        assert [list(native.calls) for native in h.natives] == native_calls
        assert_comparison_deferred()
        # Drain only the already-cancelled exact queued task, as fixture teardown
        # does. Its checkpoint must close before snapshot or child dispatch.
        comparison['cancelled'].set()
        comparison_thread.run()
        assert comparison['completed'].is_set() and not comparison_thread.is_alive()
        assert comparison['driver'] is comparison['snapshot'] is None
        assert comparison_permit['child'] is comparison_permit['owner'] is None
        assert comparison_permit['running'] is False
        assert h.jobs._stop_history_comparison()
        assert h.jobs._history_comparison is permit._comparison is None
        assert h.jobs._history_maintenance is None and not h.drivers
        assert h.jobs._primary_pending is permit._optional is None
        assert h.jobs._probe_staging_bytes == 0 and not h.owner._pins
        assert not tuple(h.jobs.directory.iterdir())
