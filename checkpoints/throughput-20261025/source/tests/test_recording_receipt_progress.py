"""Finite model-free cadence controls; run only in a copied candidate tree."""
import sqlite3
from types import SimpleNamespace

import pytest

from audio_backend import recording_overlap as mod
from audio_backend.native_owned_cells import Unavailable
from test_recording_overlap import native, native_batch, payloads


def test_visit_has_exact_two_step_fairness_bound(monkeypatch):
    fixed_clock(monkeypatch)
    recording = object.__new__(mod.RecordingOverlap)
    calls = []
    def step(cells, **kwargs):
        calls.append(kwargs)
        return mod._RECEIPT_DURABLE_PROGRESS
    recording._advance_receipt_step = step
    assert recording.advance(None, force=True, batch_id='batch') is False
    assert calls == [dict(force=True, batch_id='batch'),
                     dict(force=False, batch_id='batch')]


@pytest.mark.parametrize('outcome', [False, True])
def test_no_progress_or_publication_never_repeats(outcome):
    recording = object.__new__(mod.RecordingOverlap)
    calls = []
    def step(*args, **kwargs):
        calls.append(1)
        return outcome
    recording._advance_receipt_step = step
    assert recording.advance(None) is outcome
    assert calls == [1]


def test_deadline_prevents_second_step(monkeypatch):
    recording = object.__new__(mod.RecordingOverlap)
    now = [10.]
    monkeypatch.setattr(mod, 'time', SimpleNamespace(monotonic=lambda:now[0]))
    calls = []
    def step(*args, **kwargs):
        calls.append(1)
        now[0] += mod.RECEIPT_VISIT_SECONDS
        return mod._RECEIPT_DURABLE_PROGRESS
    recording._advance_receipt_step = step
    assert recording.advance(None) is False
    assert calls == [1]


@pytest.mark.parametrize('initial_stop', [False, True])
def test_stop_prevents_any_further_step(initial_stop):
    recording = object.__new__(mod.RecordingOverlap)
    stopped = [initial_stop]
    calls = []
    def step(*args, **kwargs):
        calls.append(1)
        stopped[0] = True
        return mod._RECEIPT_DURABLE_PROGRESS
    recording._advance_receipt_step = step
    assert recording.advance(None, stop=lambda:stopped[0]) is False
    assert len(calls) == (0 if initial_stop else 1)


def prepared_flight(native):
    native_batch(native)
    flight = native.recording._flight
    assert flight is not None and flight['operation'].slot_released.wait(3)
    return flight, payloads(native)


def fixed_clock(monkeypatch):
    # Only replace this module's reference, never global time used by owner.
    monkeypatch.setattr(mod, 'time', SimpleNamespace(monotonic=lambda:100.))


def test_positive_ack_submits_exact_next_receipt_in_same_visit(native, monkeypatch):
    flight, before = prepared_flight(native)
    fixed_clock(monkeypatch)
    assert native.recording.advance(native.capture, force=True) is False
    after = native.recording.status(native.sid, 'capture.one')
    assert [r['receipt_state'] for r in after['work_chunks']] == ['durable', 'pending']
    assert [r['receipt_attempts'] for r in after['work_chunks']] == [1, 1]
    assert native.recording._flight is not flight
    assert native.recording._flight['chunk_id'] == before[1][0]
    assert native.recording._next_tick == 101.
    assert payloads(native) == before and native.store.claim() is None
    assert native.recording._flight['operation'].slot_released.wait(3)


def test_unfinished_operation_is_not_waited_or_repeated(native, monkeypatch):
    flight, before = prepared_flight(native)
    class Pending:
        def is_set(self):return False
        def wait(self, *args):raise AssertionError('maintenance must never wait')
    original = flight['operation']
    flight['operation'] = SimpleNamespace(slot_released=Pending())
    fixed_clock(monkeypatch)
    try:
        assert native.recording.advance(native.capture, force=True) is False
        assert native.recording._flight is flight
        assert native.recording._next_tick == 101.
        assert payloads(native) == before and native.store.claim() is None
    finally:
        flight['operation'] = original


def test_existing_owner_receipt_ack_also_earns_only_one_next_step(native, monkeypatch):
    flight, before = prepared_flight(native)
    # Simulate an uncertain prior delivery whose owner receipt survived but
    # whose in-memory flight did not. Do not regenerate its payload.
    native.recording._flight = None
    fixed_clock(monkeypatch)
    assert native.recording.advance(native.capture, force=True) is False
    result = native.recording.status(native.sid, 'capture.one')
    assert result['work_chunks'][0]['native_receipt_status'] == 'existing_receipt'
    assert [r['receipt_state'] for r in result['work_chunks']] == ['durable', 'pending']
    assert [r['receipt_attempts'] for r in result['work_chunks']] == [1, 1]
    assert native.recording._flight['chunk_id'] == before[1][0]
    assert payloads(native) == before and native.store.claim() is None
    assert native.recording._flight['operation'].slot_released.wait(3)


@pytest.mark.parametrize('failure', ['before_write', 'uncertain_commit', 'zero_rows', 'authority'])
def test_ack_failures_do_not_earn_second_step(native, monkeypatch, failure):
    flight, before = prepared_flight(native)
    fixed_clock(monkeypatch)
    original = native.recording._receipt_status
    def fail_status(*args, **kwargs):
        if failure == 'uncertain_commit':
            original(*args, **kwargs)
        if failure == 'zero_rows':return 0
        raise sqlite3.OperationalError('injected uncertain or refused transaction')
    if failure == 'authority':
        def unavailable(*args, **kwargs):raise Unavailable('source_authority_unavailable')
        monkeypatch.setattr(native.capture.authority, 'receipt_valid', unavailable)
    else:
        monkeypatch.setattr(native.recording, '_receipt_status', fail_status)
    submitted = []
    monkeypatch.setattr(native.owner, 'submit', lambda *args:submitted.append(args))
    assert native.recording.advance(native.capture, force=True) is False
    assert submitted == [] and native.recording._next_tick == 101.
    assert payloads(native) == before and native.store.claim() is None
    result = native.recording.status(native.sid, 'capture.one')
    assert result['state'] == 'pending_receipts'
    assert result['work_chunks'][1]['receipt_attempts'] == 0


def test_concurrent_cooldown_is_not_bypassed_on_second_step(monkeypatch):
    fixed_clock(monkeypatch)
    recording = object.__new__(mod.RecordingOverlap)
    calls = []
    def step(cells, *, force, batch_id):
        calls.append(force)
        if len(calls) == 1:return mod._RECEIPT_DURABLE_PROGRESS
        assert force is False
        return False
    recording._advance_receipt_step = step
    assert recording.advance(None, force=True) is False
    assert calls == [True, False]


def test_cooldown_rechecked_after_acquiring_maintenance_lock(monkeypatch):
    inspected = []
    class Recording(mod.RecordingOverlap):
        @property
        def _primary_cache_publish_unknown(self):
            inspected.append(True)
            return None
    recording = object.__new__(Recording)
    recording._next_tick = 0.
    released = []
    class RacingLock:
        def acquire(self, blocking):
            assert blocking is False
            recording._next_tick = 101.
            return True
        def release(self):released.append(True)
    recording._maintenance_lock = RacingLock()
    fixed_clock(monkeypatch)
    assert recording._advance_receipt_step(None) is False
    assert recording._next_tick == 101. and released == [True] and inspected == []
