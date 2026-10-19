"""Real primary producer ownership across one synthetic running census.

The existing Worker/manager/permit/ProbeOperation/SQLite fixture stays unchanged.
Only this test's fake driver instance gains the production running-gate call.
No model, native child, comparator child, network, deadline change or replay.
The existing authenticated implementation fixture also exposes exact pre-fix
baseline controls when PRIMARY_PROBE_FINGERPRINT_BASELINE is explicitly set.
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
from test_primary_probe_reservation_fingerprint import implementation
from test_recording_speaker_history_integration import (
    primary_history_factory, ingest_recording, _cohort, _counts,
    _observations, _observation_identity, _primary_rows, RATE,
)
from test_recording_speaker_links import vector


@pytest.mark.parametrize('transition', ['turn_exit', 'queue_demand', 'owner_callback'])
def test_real_primary_running_census_preserves_only_exact_authority(
        primary_history_factory, implementation, monkeypatch, transition):
    module, baseline = implementation
    # Bind the real app's constructor and the production diagnostic accessor to
    # the same authenticated implementation. Baseline exception/marker identity
    # must not be interpreted by the candidate accessor or rewritten in the test.
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
        invocation = probe['invocation_id']
        entered, resume, running = threading.Event(), threading.Event(), threading.Event()
        samples, fingerprints, checks, results, mutations = [], [], [], [], []
        reservations, executions, callbacks = [], [], []
        original_sample = permit._sampler
        original_state = permit._optional_state_locked
        original_check = permit.check_optional
        original_reserve = permit.reserve_optional
        original_condition_wait = permit._condition.wait
        original_factory = h.jobs._probe_factory

        def sampled(settings):
            result = original_sample(settings)
            if running.is_set():
                assert not permit._condition._is_owned()
                assert not h.jobs._lock._is_owned()
                assert not h.owner._publication_guard._is_owned()
                samples.append(threading.get_ident())
                assert len(samples) == 1
                entered.set()
                assert resume.wait(5.), 'running census was not released'
            return result

        def fingerprint(**kwargs):
            state = original_state(**kwargs)
            if running.is_set():
                assert permit._optional is probe
                assert permit._optional_kind == 'automatic_primary_probe'
                fingerprints.append(kwargs.get('primary_probe_running', False))
            return state

        def checked(token, phase='running', **kwargs):
            if phase == 'running':
                assert running.is_set() and token is probe
                checks.append((token, phase))
            return original_check(token, phase, **kwargs)

        def reserved(token, *args, **kwargs):
            reservations.append(token)
            return original_reserve(token, *args, **kwargs)

        def condition_wait(*args, **kwargs):
            assert not running.is_set(), 'running primary probe must not wait or regrant'
            return original_condition_wait(*args, **kwargs)

        def driver_factory(directory, digest):
            driver = original_factory(directory, digest)
            original_wait = driver.wait

            def wait(cancelled):
                assert cancelled is probe['cancelled']
                assert driver._service_gate.owner is h.jobs
                assert driver._service_gate.probe is probe
                assert h.jobs._primary_pending is probe and permit._optional is probe
                running.set()
                try:
                    driver._service_gate.check('running')
                except BaseException as error:
                    results.append(error)
                    raise
                else:
                    results.append(None)
                finally:
                    running.clear()
                return original_wait(cancelled)

            driver.wait = wait
            return driver

        monkeypatch.setattr(permit, '_sampler', sampled)
        monkeypatch.setattr(permit, '_optional_state_locked', fingerprint)
        monkeypatch.setattr(permit, 'check_optional', checked)
        monkeypatch.setattr(permit, 'reserve_optional', reserved)
        monkeypatch.setattr(permit._condition, 'wait', condition_wait)
        monkeypatch.setattr(h.jobs, '_probe_factory', driver_factory)
        assert permit.request_automatic_turn()
        turn = permit.automatic_turn()
        assert turn.__enter__()
        claim = waiting = executing = None
        try:
            assert h.jobs.submit_primary()
            assert reservations == [probe] and permit._optional is probe
            # submit_primary legitimately queues a deferred comparison through
            # automatic_idle. Keep its exact owner without dispatching its task.
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
            deadline = probe['work_deadline']
            assert deadline is not None and probe['crop'] is crop
            assert probe['reservation'] is reservation and probe['node'] is node
            original_run = probe['thread'].run

            def run_once():
                executions.append(probe['thread'])
                assert len(executions) == 1, 'original producer must not replay'
                original_run()

            # Preserve the real manager-owned ManualThread; this one real test
            # thread only drains it so the original caller can close its turn.
            monkeypatch.setattr(probe['thread'], 'run', run_once)
            if transition != 'turn_exit':
                turn.__exit__(None, None, None)
                turn = None
            executing = threading.Thread(target=probe['thread'].run,
                name='synthetic-primary-running-census', daemon=True)
            executing.start()
            assert entered.wait(5.), 'running census was not reached'
            assert checks == [(probe, 'running')] and len(samples) == 1
            assert fingerprints == [not baseline]
            assert permit._optional is probe and permit._cancel is probe['cancelled']
            assert not probe['cancelled'].is_set() and not probe['completed'].is_set()
            assert probe['operation'].child_dispatch == 'possible'
            assert _counts(h) == {'observations': 1, 'vectors': 0, 'pending': 1,
                                  'candidate_missing': 0, 'attempts': 1}
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
        assert executions == [probe['thread']] and checks == [(probe, 'running')]
        assert samples == [executing.ident] and fingerprints == [not baseline] * 2
        assert len(results) == 1 and callbacks == []
        if success:
            assert results == [None] and not probe['cancelled'].is_set()
        else:
            assert type(results[0]) is module.AdmissionError
            assert results[0].reason == 'optional_ownership_changed'
            assert probe['cancelled'].is_set()
        operation = probe['operation']
        assert type(operation) is runner.ProbeOperation and operation.started
        assert operation.cleaned and runner.cleanup_verified(operation.receipt)
        assert operation.work_deadline == probe['work_deadline'] == deadline
        assert operation.child_dispatch == ('returned_validated' if success else 'possible')
        assert operation.claim is operation.claim_identity is None
        assert len(h.probes) == h.probes[0].starts == h.probes[0].cleanups == 1
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
                                     (probe['attempt_id'],)).fetchone())
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
            assert attempt['terminal'] == probe['failure_json'] == operation.failure_json
            failure = json.loads(attempt['terminal'])
            assert failure['stage'] == 'child_wait' and failure['child_dispatch'] == 'possible'
            assert failure['reason'] == 'optional_cancelled'
            assert failure['error_type'] == 'AdmissionError'
            assert failure['sampling'] == module.optional_sample_failure(results[0])
            assert failure['sampling']['subtype'] == 'ownership_changed'
            assert failure['sampling']['reservation_held'] is True
            assert failure['sampling']['wait_ms'] == 0 and not failure['sampling']['grace_used']
        for _ in range(2):
            assert h.jobs._maintain_primary() and not h.jobs.submit_primary()
        assert len(h.probes) == h.probes[0].starts == h.probes[0].cleanups == 1
        assert reservations == [probe] and executions == [probe['thread']]
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
