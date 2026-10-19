"""Real Worker/start gates and inert original children; inference stays synthetic.

Reuse the published primary-history fixture unchanged. Real ProbeDriver creation,
OwnedDriver.start, PID/birth visibility and cleanup own every inert child. Only
the narrowly checked child command and post-reap vector edge are synthetic.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import native_execution_permit as permit_module
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_probe_runner as runner
from test_moss_automatic_integration import automatic_app_factory
from test_moss_handy_jobs import ManualThread
from test_moss_owner_admission_defer import TrackedInput
from test_recording_speaker_history_integration import (
    primary_history_factory, ingest_recording, _cohort, _counts,
    _observations, _observation_identity, _primary_rows, RATE,
)
from test_recording_speaker_links import vector


INERT_CODE = '"""Frozen inert admission child: no files, model, audio, network or subprocesses."""\nimport sys\nraise SystemExit(0 if sys.stdin.buffer.read(1) == b"1" else 2)\n'
LAUNCHED = []


@pytest.mark.parametrize('phase,transition', [
    ('before_spawn', 'turn_exit'),
    ('before_admission', 'queue_demand'),
    ('before_spawn', 'owner_callback'),
    ('before_admission', 'comparison_owner_bind'),
])
def test_real_worker_start_boundary(primary_history_factory, monkeypatch, phase, transition):
    with primary_history_factory() as h:
        cid = ingest_recording(h, frames=10 * RATE)[0]
        assert _cohort(h) == cid
        probe, permit = h.jobs._primary_pending, h.jobs.permit
        admitted = _observations(h)[0]
        primary_rows = _primary_rows(h)
        native_calls = [list(native.calls) for native in h.natives]
        reservation, crop, node = probe['reservation'], probe['crop'], probe['node']
        invocation, attempt_id = probe['invocation_id'], probe['attempt_id']
        reservation_value = tuple(sorted(reservation.items()))
        entered, resume, census = threading.Event(), threading.Event(), threading.Event()
        checks, target_results, target_samples, executions, reservations, callbacks = [], [], [], [], [], []
        children, child_samples, original_waits = [], [], []
        original_sample, original_check = permit._sampler, permit.check_optional
        original_reserve, original_wait = permit.reserve_optional, permit._condition.wait
        original_factory, original_popen = h.jobs._probe_factory, subprocess.Popen

        def spawn(command, **kwargs):
            assert command == [sys.executable, '-I', '-B', '-S',
                str(controller.ROOT / 'audio_backend/moss_handy_runner.py'),
                str(h.jobs.directory), h.probes[0].request_sha]
            assert not children and len(LAUNCHED) < 3
            assert kwargs['cwd'] == controller.ROOT and kwargs['stdin'] == subprocess.PIPE
            assert kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL
            assert kwargs['env']['PYTHONDONTWRITEBYTECODE'] == '1'
            assert all(item.poll() is not None for item in LAUNCHED)
            process = original_popen([sys.executable, '-I', '-B', '-S', '-c', INERT_CODE], **kwargs)
            process.stdin = TrackedInput(process.stdin)
            wait = process.wait
            def positive_wait(*args, **options):
                result = wait(*args, **options)
                original_waits.append((process.pid, result))
                return result
            process.wait = positive_wait
            children.append(process)
            LAUNCHED.append(process)
            return process

        class RealStartProbe(runner.ProbeDriver):
            def __init__(self, directory, digest):
                super().__init__(directory, digest)
                self.synthetic = original_factory(directory, digest)
                assert h.probes[-1] is self.synthetic
                h.probes[-1] = self
                self.starts = self.cleanups = 0
                self.synthetic_returns = 0
                self.positive_wait_returncode = None

            def start(self, cancelled):
                self.starts += 1
                assert self.starts == 1 and h.capture.analyzer is None
                assert not h.jobs._lock._is_owned()
                return super().start(cancelled)

            def wait(self, cancelled):
                # This is the declared synthetic inference/result edge. The real
                # inherited start has already written its single admission byte.
                assert children == [self.proc] and self.proc.stdin.writes == [b'1']
                self._service_gate.check('running')
                remaining = probe['work_deadline'] - time.monotonic()
                assert remaining > 0 and not cancelled.is_set()
                self.positive_wait_returncode = self.proc.wait(timeout=remaining)
                assert self.positive_wait_returncode == 0
                self.synthetic_returns += 1
                return self.synthetic.wait(cancelled)

            def cleanup(self, stop):
                self.cleanups += 1
                return super().cleanup(stop)

        def sampled(settings):
            current = original_sample(settings)
            identities = dict(current.identities)
            added_rss = 0
            for process in children:
                if process.poll() is not None:
                    continue
                try:
                    observed = psutil.Process(process.pid)
                    birth, parent, rss = observed.create_time(), observed.ppid(), observed.memory_info().rss
                    assert parent == os.getpid() and observed.create_time() == birth
                except psutil.NoSuchProcess:
                    assert process.poll() is not None
                    continue
                assert process.pid not in identities
                identities[process.pid] = (birth, parent, rss)
                added_rss += rss
                child_samples.append((process.pid, birth, parent, rss))
            current = permit_module.ProcessSample(identities, current.rss + added_rss,
                current.caller_rss, current.available, current.disk_free,
                resampled=current.resampled, confirmed_exits=current.confirmed_exits)
            if census.is_set():
                assert not permit._condition._is_owned()
                assert not h.jobs._lock._is_owned() and not h.owner._publication_guard._is_owned()
                assert permit._optional is probe and probe['operation'].driver is h.probes[0]
                target_samples.append(threading.get_ident())
                assert len(target_samples) == 1
                if phase == 'before_spawn':
                    assert not children and h.probes[0].proc is None
                else:
                    assert children == [h.probes[0].proc] and children[0].poll() is None
                    assert children[0].stdin.writes == []
                    bound = h.probes[0].guard.own_roots
                    assert len(bound) == 1 and bound[0].pid == children[0].pid
                    assert current.identities[bound[0].pid][0] == bound[0].created
                    assert current.identities[bound[0].pid][1] == os.getpid()
                entered.set()
                assert resume.wait(5.), 'real start-gate census was not released'
            return current

        def checked(token, where='running', **kwargs):
            assert token is probe
            checks.append(where)
            if where != phase:
                return original_check(token, where, **kwargs)
            assert checks.count(phase) == 1 and not census.is_set()
            census.set()
            try:
                result = original_check(token, where, **kwargs)
            except BaseException as error:
                target_results.append(error)
                raise
            else:
                target_results.append(None)
                return result
            finally:
                census.clear()

        def reserved(token, *args, **kwargs):
            assert token is probe and kwargs['owner_kind'] == 'automatic_primary_probe'
            reservations.append(token)
            return original_reserve(token, *args, **kwargs)

        def condition_wait(*args, **kwargs):
            assert not census.is_set(), 'target primary gate cannot wait or regrant'
            return original_wait(*args, **kwargs)

        monkeypatch.setattr(permit, '_sampler', sampled)
        monkeypatch.setattr(permit, 'check_optional', checked)
        monkeypatch.setattr(permit, 'reserve_optional', reserved)
        monkeypatch.setattr(permit._condition, 'wait', condition_wait)
        monkeypatch.setattr(h.jobs, '_probe_factory', RealStartProbe)
        monkeypatch.setattr(controller.subprocess, 'Popen', spawn)
        assert permit.request_automatic_turn()
        turn = permit.automatic_turn()
        assert turn.__enter__()
        claim = waiting = executing = None
        try:
            assert h.jobs.submit_primary() and reservations == [probe]
            comparison, comparison_permit = h.jobs._history_comparison, permit._comparison
            comparison_thread = comparison['thread']
            assert type(comparison_thread) is ManualThread
            assert comparison_thread.is_alive() and comparison['driver'] is comparison['snapshot'] is None
            assert comparison_permit['owner'] is comparison_permit['child'] is None
            callback, generation = permit._cancel_callback, permit._sample_generation
            binding, deadline = probe['dispatch_binding'], probe['work_deadline']
            assert time.monotonic() < deadline
            original_run = probe['thread'].run
            def run_once():
                executions.append(probe['thread'])
                assert len(executions) == 1
                original_run()
            monkeypatch.setattr(probe['thread'], 'run', run_once)
            if transition != 'turn_exit':
                turn.__exit__(None, None, None)
                turn = None
            executing = threading.Thread(target=probe['thread'].run,
                name='primary-real-start-boundary', daemon=True)
            executing.start()
            assert entered.wait(5.), 'real start-gate census was not reached'
            assert probe['dispatch_binding'] is binding and probe['work_deadline'] == deadline
            assert probe['reservation'] is reservation and probe['crop'] is crop and probe['node'] is node
            assert probe['attempt_id'] == attempt_id and probe['invocation_id'] == invocation
            assert not probe['cancelled'].is_set() and not probe['completed'].is_set()
            assert _counts(h) == {'observations':1, 'vectors':0, 'pending':1, 'candidate_missing':0, 'attempts':1}
            assert comparison['driver'] is comparison['snapshot'] is None
            assert comparison_permit['owner'] is comparison_permit['child'] is None
            if transition == 'turn_exit':
                assert permit._turn is permit._handoff and permit._turn is not None
                turn.__exit__(None, None, None)
                turn = None
                assert permit._turn is permit._handoff is None
            elif transition == 'queue_demand':
                claim = permit.claim_probe()
                claim.__enter__()
                waiting = permit.primary_intent('worker')
                assert permit._probe and not waiting.granted and not waiting.entered and not waiting.released
            elif transition == 'owner_callback':
                with permit._condition:
                    permit._cancel_callback = lambda: callbacks.append(True)
                assert permit._cancel_callback is not callback
            else:
                comparison_owner = SimpleNamespace(comparison_parent_bytes=lambda: 0,
                    comparison_resources_closed=lambda: True)
                permit.bind_comparison_owner(comparison['grant'], comparison_owner)
                assert comparison_permit['owner'] is comparison_owner
            assert (permit._sample_generation is generation) is (transition != 'comparison_owner_bind')
            assert permit._optional is probe and permit._cancel is probe['cancelled']
            resume.set()
            executing.join(5.)
            assert not executing.is_alive(), 'original primary producer did not close'
        finally:
            resume.set()
            if executing is not None:
                executing.join(5.)
                assert not executing.is_alive()
            if claim is not None:
                claim.__exit__(None, None, None)
            if waiting is not None:
                waiting.release()
            if turn is not None:
                turn.__exit__(None, None, None)
            # Only retained original handles; this is a failing-test safeguard.
            for process in children:
                if process.poll() is None:
                    if not process.stdin.closed:
                        process.stdin.close()
                    process.terminate()
                try:
                    process.wait(timeout=3.)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.)

        success = transition in ('turn_exit', 'queue_demand')
        operation, driver = probe['operation'], h.probes[0]
        assert len(h.probes) == driver.starts == driver.cleanups == 1
        assert operation.cleaned and runner.cleanup_verified(operation.receipt)
        assert driver.guard.deadline <= deadline and operation.work_deadline == probe['work_deadline'] == deadline
        assert probe['reservation'] is reservation and probe['node'] is node
        assert tuple(sorted(reservation.items())) == reservation_value
        assert probe['attempt_id'] == attempt_id and probe['invocation_id'] == invocation
        assert reservations == [probe] and executions == [probe['thread']]
        assert target_samples == [executing.ident] and len(target_results) == 1 and not callbacks
        assert len(children) == (0 if transition == 'owner_callback' else 1)
        assert all(process.poll() is not None for process in children)
        assert all(any(pid == process.pid for pid, code in original_waits) for process in children)
        if success:
            assert target_results == [None] and not probe['cancelled'].is_set()
            assert checks == ['before_input', 'before_input', 'before_spawn', 'before_admission', 'running']
            assert driver.synthetic_returns == 1 and driver.positive_wait_returncode == 0
            assert children[0].stdin.writes == [b'1']
            assert operation.child_dispatch == 'returned_validated' and operation.failure_json is None
        else:
            error = target_results[0]
            assert type(error) is permit_module.AdmissionError and error._moss_start_gate == phase
            assert error.reason == ('optional_ownership_changed' if transition == 'owner_callback' else 'process_sample_unavailable')
            assert probe['cancelled'].is_set() and driver.synthetic_returns == 0
            assert all(process.stdin.writes == [] for process in children)
            assert checks == ['before_input', 'before_input', 'before_spawn'] + (['before_admission'] if phase == 'before_admission' else [])
            assert operation.child_dispatch == 'possible'
        assert probe['completed'].is_set() and not probe['thread'].is_alive()
        assert permit._optional is permit._sample_flight is None
        assert h.jobs._maintain_primary() and h.jobs._primary_pending is None
        assert probe['crop'] is None and probe['charge_bytes'] == h.jobs._probe_staging_bytes == 0
        assert not h.owner._pins and not tuple(h.jobs.directory.iterdir())
        observation = _observations(h)[0]
        assert _observation_identity(observation) == _observation_identity(admitted)
        assert _counts(h) == {'observations':1, 'vectors':int(success), 'pending':0,
            'candidate_missing':int(not success), 'attempts':1}
        with h.store.db() as db:
            attempt = dict(db.execute(f'SELECT * FROM {history.ATTEMPTS} WHERE token=?', (attempt_id,)).fetchone())
        assert attempt['state'] == ('success' if success else 'failure')
        assert attempt['reserved_bytes'] == attempt['staging_bytes'] == 0
        assert attempt['invocation_id'] == invocation
        assert attempt['terminal_sha256'] == hashlib.sha256(attempt['terminal']).hexdigest()
        if success:
            expected = struct.pack('<512f', *vector(1., 0.))
            assert observation['vector'] == expected and observation['vector_sha256'] == hashlib.sha256(expected).hexdigest()
        else:
            assert observation['vector'] is None and probe['failure_frozen']
            assert attempt['terminal'] == probe['failure_json'] == operation.failure_json
            failure = json.loads(attempt['terminal'])
            assert failure['stage'] == 'child_start' and failure['child_dispatch'] == 'possible'
            assert failure['reason'] == 'optional_cancelled' and failure['error_type'] == 'AdmissionError'
            assert failure['sampling'] == permit_module.optional_sample_failure(target_results[0])
            assert failure['sampling']['subtype'] == ('ownership_changed' if transition == 'owner_callback' else 'stale_generation')
            assert failure['sampling']['reservation_held'] is True
            assert failure['sampling']['wait_ms'] == 0 and not failure['sampling']['grace_used']
        for _ in range(2):
            assert h.jobs._maintain_primary() and not h.jobs.submit_primary()
        assert len(h.probes) == 1 and driver.starts == driver.cleanups == 1
        assert _primary_rows(h) == primary_rows and h.store.chunk(cid)['next_offset'] == 10.
        assert [list(native.calls) for native in h.natives] == native_calls
        assert comparison['driver'] is comparison['snapshot'] is None and not h.drivers
        assert comparison_thread.is_alive() and not comparison['completed'].is_set()
        comparison['cancelled'].set()
        comparison_thread.run()
        assert comparison['completed'].is_set() and not comparison_thread.is_alive()
        assert comparison['driver'] is comparison['snapshot'] is None
        assert comparison_permit['child'] is None and not comparison_permit['running']
        assert h.jobs._stop_history_comparison()
        assert h.jobs._history_comparison is permit._comparison is None
        assert not h.owner._pins and not tuple(h.jobs.directory.iterdir())
        evidence = {'phase':phase, 'transition':transition, 'success':success,
            'gate_checks':checks, 'target_sampler_calls':len(target_samples),
            'children':[{'pid':process.pid, 'returncode':process.returncode,
                'admission_writes':[value.hex() for value in process.stdin.writes]} for process in children],
            'original_waits':original_waits, 'real_child_samples':child_samples,
            'cleanup':operation.receipt, 'deadline_unchanged':probe['work_deadline'] == deadline,
            'durable_counts':_counts(h), 'synthetic_result_returns':driver.synthetic_returns,
            'failed_attempt':None if success else json.loads(attempt['terminal'])}
        (Path(h.store.settings.data_dir) / 'start-boundary-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
