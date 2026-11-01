"""Reviewed non-frontier capture deltas: synthetic control seams, no models.

Packetless tests replace authority/provenance builders only to inspect original
Capture publication/cleanup ordering. They are not a native/carrier authority
qualification; test_strict_payload_turn_witness supplies original component work.
Priming tests exercise real SQLite and the original PrimaryAuthority read path.
"""
from contextlib import contextmanager
from types import SimpleNamespace
import sqlite3
import time

import pytest
from audio_backend import native_owned_cell_capture as capture
from audio_backend import recording_primary_provenance as provenance
from audio_backend import strict_recording as strict
from audio_backend import strict_payload_v2 as payload
from audio_backend import sqlite_admission
from audio_backend.native_owned_cell_store import Budget


def packetless_control(monkeypatch, fault=None):
    result = {'metadata': {'core_cut': {}}, '_primary_observation': object(),
              '_strict_capture': 'old', '_strict_payload_capture': 'old',
              '_strict_payload_draft': 'old'}
    outcome, carrier = object(), object()
    failure = RuntimeError('synthetic-boundary-failure')
    seen = []

    class Phase:
        @contextmanager
        def phase(self):
            yield
            assert '_strict_capture' not in result
            assert '_strict_payload_capture' not in result
            if fault == 'phase_exit': raise failure

    class Authority:
        store = SimpleNamespace(_strict_policy='strict-primary-payload-v2', moss_automatic=None)
        @contextmanager
        def list_read_scope(self, budget): yield
        def mapping(self, *args): return ()
        def original_pcm(self, *args): return b'\x01\x00'

    def freeze(*args, **kwargs):
        assert all(name not in result for name in ('_strict_capture', '_strict_payload_capture', '_strict_payload_draft'))
        result['_strict_payload_draft'] = object()
        return 'empty'

    def build(*args):
        assert args[4] is outcome
        assert '_strict_capture' not in result and '_strict_payload_capture' not in result
        if fault == 'carrier_build': raise failure
        return carrier

    monkeypatch.setattr(provenance, 'freeze', freeze)
    monkeypatch.setattr(strict, 'make_capture_outcome', lambda *args: outcome)
    monkeypatch.setattr(payload, 'make_packetless_capture', build)
    state = SimpleNamespace(authority=Authority(), budget=Phase(), analyzer=b'\x01\x00',
        analyzer_start=0, origin_frame=0, scope_start=0., primary_only=True,
        seam_enabled=False, require_strict_ready=lambda: None,
        _failure=lambda error: seen.append(error))
    return state, result, outcome, carrier, failure, seen


def test_packetless_carrier_published_only_after_positive_phase_exit(monkeypatch):
    state, result, outcome, carrier, _, seen = packetless_control(monkeypatch)
    assert capture.Capture._prepare_with_census(state, {'id': 'synthetic', '_strict_primary': True},
        {'state': {}}, 0., 1/16000, result, required=True) == ()
    assert result['_strict_capture'] is outcome and result['_strict_payload_capture'] is carrier
    assert '_strict_payload_draft' not in result and state.analyzer is None
    assert not seen


@pytest.mark.parametrize('fault', ['carrier_build', 'phase_exit'])
def test_packetless_failure_does_not_publish_or_retain_private_draft(monkeypatch, fault):
    state, result, _, _, failure, seen = packetless_control(monkeypatch, fault)
    with pytest.raises(RuntimeError) as caught:
        capture.Capture._prepare_with_census(state, {'id': 'synthetic', '_strict_primary': True},
            {'state': {}}, 0., 1/16000, result, required=True)
    assert caught.value is failure and seen == [failure]
    assert all(name not in result for name in ('_strict_capture', '_strict_payload_capture', '_strict_payload_draft'))
    assert state.analyzer is None


def authority(tmp_path):
    path = tmp_path / 'synthetic.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE facts(value INTEGER)')
        db.execute('INSERT INTO facts VALUES(1)')
    return capture.PrimaryAuthority(SimpleNamespace(path=path, settings=SimpleNamespace(data_dir=tmp_path)))


@pytest.mark.parametrize('phase,expected', [('session', 1), ('automatic_intent', 0)])
def test_authority_priming_respects_phase_and_original_budget(tmp_path, monkeypatch, phase, expected):
    owner = authority(tmp_path)
    budget = Budget()
    budget.deadline = time.monotonic() + .5
    original_deadline = budget.deadline
    original = sqlite_admission.prime_connection
    calls = []
    def observe(db, **kwargs):
        assert kwargs['check'].__self__ is budget
        assert kwargs['remaining_seconds'].__self__ is budget
        assert kwargs['charge'].__self__ is budget
        assert not db.in_transaction
        calls.append(db)
        return original(db, **kwargs)
    monkeypatch.setattr(sqlite_admission, 'prime_connection', observe)
    bodies = []
    def read(db):
        assert db.in_transaction and db.execute('PRAGMA busy_timeout').fetchone()[0] == 0
        bodies.append(True)
        return db.execute('SELECT value FROM facts').fetchone()[0]
    assert owner._read(budget, phase, read) == 1
    assert len(calls) == expected and bodies == [True]
    assert budget.deadline == original_deadline


def test_authority_priming_failure_preserves_error_and_skips_business_read(tmp_path, monkeypatch):
    owner = authority(tmp_path)
    budget = Budget()
    error = RuntimeError('synthetic-original-admission-refusal')
    seen = []
    connections = []
    def refuse(db, **kwargs):
        connections.append(db)
        raise error
    monkeypatch.setattr(sqlite_admission, 'prime_connection', refuse)
    with pytest.raises(RuntimeError) as caught:
        owner._read(budget, 'session', lambda db: seen.append(db))
    assert caught.value is error and not seen and len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError): connections[0].execute('SELECT 1')
