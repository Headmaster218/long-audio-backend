"""Candidate start-gate contrasts; no driver construction or dispatch.

Reuse the existing model-free reservation, sample and turn fixture helpers.
The barrier is the existing race_sample pattern with a real start-gate target.
Fresh paired baseline refusals/candidate survivals retain genuine authority negatives.
"""
import ast
import hashlib
import inspect
import importlib.util
import sys
from pathlib import Path
import socket
import sqlite3
import textwrap
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import moss_handy_jobs as jobs
from audio_backend import native_execution_permit as permit_module
from audio_backend import recording_speaker_probe_runner as runner
from test_primary_probe_reservation_fingerprint import reservation, sample, close_turn


SOURCE = Path(__file__).resolve().parents[1]
PINS = {
    'native_execution_permit.py': '08fae6fd05e9e0f269318e52cb4acac4bf2edc3e13d169b59107c8ccc9b6e278',
    'moss_handy_controller.py': 'e34f3df60b6241a7e7dd98081dde960e5ebe018ba3f8bb376e84b3a6062e38eb',
    'moss_handy_jobs.py': 'ddc4b7316809292d2e3644efbb887d1b13779ecf16f8466f2612e02f7b341920',
    'recording_speaker_probe_runner.py': '39f78574f3b08f1e8cdb3d175954f816cfaae43f58571340af3932153acbaae7',
}


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('This test cannot dispatch a process, open a DB or use a socket')
    monkeypatch.setattr(controller.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)


def _literal_calls(function, attribute, argument=0):
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    calls = sorted((node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute and len(node.args) > argument
        and isinstance(node.args[argument], ast.Constant)), key=lambda node: node.lineno)
    return [node.args[argument].value for node in calls]


def test_exact_candidate_real_primary_phase_wiring():
    for module in (permit_module, controller, jobs, runner):
        path = Path(module.__file__).resolve()
        assert path == SOURCE / 'audio_backend' / path.name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == PINS[path.name]
    fixture = Path(inspect.getsourcefile(inspect.unwrap(reservation))).resolve()
    assert fixture == SOURCE / 'tests/test_primary_probe_reservation_fingerprint.py'
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == '418922505875b51b5bd957e97bf92f52c59a006df62b246ab8b2de4cebc36af4'
    assert runner.ProbeDriver.start is controller.OwnedDriver.start
    assert runner.ProbeDriver._check_start_gate is controller.OwnedDriver._check_start_gate
    assert _literal_calls(jobs.MossJobs._primary_dispatch_current, 'check_optional', 1) == ['before_input']
    assert _literal_calls(runner.ProbeOperation.run, 'check') == ['before_input']
    assert _literal_calls(runner.ProbeDriver.start, '_check_start_gate') == ['before_spawn', 'before_admission']
    assert _literal_calls(runner.ProbeDriver.wait, 'check') == ['running']


def _race_start_gate(case, phase, change):
    """One complete fake census, paused outside the real permit condition."""
    entered, resume = threading.Event(), threading.Event()
    calls, outcomes = [], []
    permit = case.permit
    gate = jobs._SpeakerProbeGate(SimpleNamespace(permit=permit, _links_enabled=lambda: True), case.token)
    driver = object.__new__(runner.ProbeDriver)
    driver._service_gate = gate

    def controlled(_):
        assert not permit._condition._is_owned()
        current = sample(case.module)
        calls.append(threading.get_ident())
        entered.set()
        assert resume.wait(5.), 'start-gate census was not released'
        return current

    def checking():
        try:
            driver._check_start_gate(phase, case.event)
        except BaseException as error:
            outcomes.append(error)
        else:
            outcomes.append(None)

    permit._sampler = controlled
    thread = threading.Thread(target=checking, name='primary-probe-start-gate-census')
    with patch.object(permit._condition, 'wait', side_effect=AssertionError('unexpected permit wait')):
        thread.start()
        try:
            assert entered.wait(5.), 'start-gate census was not reached'
            assert permit._optional is case.token and not case.event.is_set()
            change()
        finally:
            resume.set()
            thread.join(5.)
    assert not thread.is_alive() and len(outcomes) == 1
    assert calls == [thread.ident] and permit._sample_flight is None
    assert permit._completed_sample_retries == 0 and not case.callbacks
    error = outcomes[0]
    if error is not None:
        assert type(error) is case.module.AdmissionError
        assert error._moss_start_gate == phase
    return error


@pytest.fixture
def baseline_permit(tmp_path):
    raw = Path(permit_module.__file__).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PINS['native_execution_permit.py']
    replacements = (
        (b"                if (self._comparison['running'] is False\n"
         b"                        and self._comparison['sampling'] is None):return\n", b""),
        (b"        # Once this primary probe owns its exact reservation, its four execution\n"
         b"        # gates ignore caller-turn and queue scheduling hints.\n"
         b"        # Reservation admission and every other owner/phase keep their full fingerprint.\n",
         b"        # Once this primary probe owns its exact reservation, before_input and\n"
         b"        # running checks ignore caller-turn and queue scheduling hints.\n"
         b"        # Admission and every other owner/phase retain their full fingerprint.\n"),
        (b"                and phase in ('before_input', 'before_spawn', 'before_admission', 'running'))\n",
         b"                and phase in ('before_input', 'running'))\n"),
    )
    for current, original in replacements:
        assert raw.count(current) == 1
        raw = raw.replace(current, original, 1)
    assert hashlib.sha256(raw).hexdigest() == '01730cb55980193d9079dde6a71d786d1987d94f51f61e829a4f1b734de06904'
    baseline = tmp_path / 'exact_dev56_native_execution_permit.py'
    baseline.write_bytes(raw)
    assert baseline.read_bytes() == raw
    name = '_exact_dev56_start_phase_baseline'
    assert name not in sys.modules
    spec = importlib.util.spec_from_file_location(name, baseline)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(baseline), 'exec'), module.__dict__)
        yield module
    finally:
        sys.modules.pop(name, None)


@pytest.mark.parametrize('phase,transition', [
    ('before_spawn', 'turn_exit'), ('before_spawn', 'queue_demand'),
    ('before_admission', 'turn_exit'), ('before_admission', 'queue_demand'),
])
def test_held_start_gate_survives_scheduling_hint(baseline_permit, phase, transition):
    observed = []
    for module, baseline in ((baseline_permit, True), (permit_module, False)):
        with reservation(module) as case:
            permit = case.permit
            generation, callback = permit._sample_generation, permit._cancel_callback
            deadline = case.token['work_deadline'] = runner.time.monotonic() + runner.WORK_SECONDS
            claim = None
            if transition == 'queue_demand':
                close_turn(case)
                claim = permit.claim_probe()

            def change():
                if transition == 'turn_exit':
                    assert permit._turn is not None and permit._handoff is permit._turn
                    close_turn(case)
                    assert permit._turn is permit._handoff is None
                else:
                    claim.__enter__()
                    intent = permit.primary_intent('worker')
                    assert permit._probe and not intent.granted and not intent.entered and not intent.released

            try:
                error = _race_start_gate(case, phase, change)
                if baseline:
                    assert type(error) is module.AdmissionError
                    assert error.reason == 'optional_ownership_changed'
                    detail = module.optional_sample_failure(error)
                    assert detail['subtype'] == 'ownership_changed' and detail['reservation_held'] is True
                    assert detail['wait_ms'] == 0 and detail['grace_used'] is False
                    assert case.event.is_set()
                else:
                    assert error is None and not case.event.is_set()
                    assert permit._state == 'OPTIONAL_RUNNING'
                assert permit._optional is case.token and permit._cancel is case.event
                assert permit._optional_kind == 'automatic_primary_probe'
                assert permit._cancel_callback is callback and permit._sample_generation is generation
                assert case.token['work_deadline'] == deadline
                observed.append((baseline, None if error is None else error.reason))
            finally:
                if claim is not None and permit._probe:
                    claim.__exit__(None, None, None)
    assert observed == [(True, 'optional_ownership_changed'), (False, None)]


@pytest.mark.parametrize('phase,change', [
    ('before_spawn', 'owner_token'), ('before_spawn', 'generation'),
    ('before_admission', 'owner_token'), ('before_admission', 'generation'),
])
def test_held_start_gate_rejects_real_authority_change(phase, change):
    with reservation() as case:
        close_turn(case)
        permit = case.permit
        generation, callback = permit._sample_generation, permit._cancel_callback
        replacement = object()

        def mutate():
            with permit._condition:
                if change == 'owner_token':
                    permit._optional = replacement
                else:
                    permit._sample_generation = replacement

        try:
            error = _race_start_gate(case, phase, mutate)
            detail = permit_module.optional_sample_failure(error)
            assert error.reason == ('optional_ownership_changed' if change == 'owner_token' else 'process_sample_unavailable')
            assert detail['subtype'] == ('ownership_changed' if change == 'owner_token' else 'stale_generation')
            assert detail['reservation_held'] is (change != 'owner_token')
            assert detail['wait_ms'] == 0 and detail['grace_used'] is False
            assert permit._optional is (replacement if change == 'owner_token' else case.token)
            assert permit._sample_generation is (generation if change == 'owner_token' else replacement)
            assert permit._cancel is case.event and permit._cancel_callback is callback
            assert case.event.is_set() is (change != 'owner_token')
        finally:
            # Restore only the deliberately replaced test owner so the unchanged
            # reservation fixture can release its original token in teardown.
            if change == 'owner_token' and permit._optional is replacement:
                with permit._condition:
                    permit._optional = case.token
