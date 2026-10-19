"""Prepared-only isolated SQLite tests for the scalar read-only hint.

One <=64KiB tmp_path database and one live connection. No production data,
network, model, audio or child. Uses the ordinary bounded SQL read context.
"""
from contextlib import contextmanager
import hashlib
import os
import socket
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from audio_backend import moss_handy_controller as controller
from audio_backend import moss_result_store as result_store
from audio_backend import native_execution_permit as permit
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_history_process as comparison_process
from audio_backend import recording_speaker_probe_runner as runner
from audio_backend.native_owned_support_cache import ReadError


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No network, model, native observation, or OS child')
    monkeypatch.setattr(controller.subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(runner, 'ProbeOperation', forbidden)
    monkeypatch.setattr(comparison_process, 'ComparisonProcess', forbidden)
    monkeypatch.setattr(runner, 'check_directory', forbidden)
    monkeypatch.setattr(runner, 'read_owned', forbidden)
    monkeypatch.setattr(permit.psutil, 'process_iter', forbidden)
    monkeypatch.setattr(permit.psutil, 'Process', forbidden)
    monkeypatch.setattr(permit.psutil, 'virtual_memory', forbidden)
    monkeypatch.setattr(permit.shutil, 'disk_usage', forbidden)


@contextmanager
def scalar_store(tmp_path, monkeypatch, row='dirty', failure=None):
    deadline = time.monotonic() + 5.
    path = tmp_path / 'availability.sqlite'
    original_connect = sqlite3.connect
    db = original_connect(path)
    try:
        db.execute('PRAGMA page_size=4096')
        db.execute('PRAGMA max_page_count=16')
        db.execute('CREATE TABLE recording_history_scopes_v1 (scope_seq INTEGER PRIMARY KEY, '
            'authority_blocked TEXT, state TEXT, observation_count INTEGER, building_slot INTEGER)')
        db.execute('CREATE TABLE recording_history_attempts_v1 (scope_seq INTEGER, state TEXT, token TEXT)')
        db.execute(history.DDL['recording_history_dirty_v1'])
        db.execute(history.DDL['recording_history_attempt_scope_v1'])
        db.execute("INSERT INTO recording_history_scopes_v1 VALUES(0,NULL,'pending',10,NULL)")
        if row != 'empty':
            state = row if row in ('none', 'unknown_rival') else 'pending'
            blocked = 'source_changed' if row == 'blocked' else None
            observations = 0 if row == 'no_observations' else 1
            db.execute('INSERT INTO recording_history_scopes_v1 VALUES(1,?,?,?,?)',
                (blocked, state, observations, 0 if row == 'abandoned_build' else None))
            if row in ('reserved', 'completed_rival'):
                db.execute('INSERT INTO recording_history_attempts_v1 VALUES(1,?,?)',
                    ('reserved' if row == 'reserved' else 'success', 'synthetic'))
        db.commit()
    finally:
        db.close()
    assert path.stat().st_size <= 65536
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    state = SimpleNamespace(opened=0, closed=0, live=0, statements=[], clock=0.)
    class Connection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            state.statements.append(statement)
            if statement.lstrip().startswith('SELECT 1 FROM recording_history_scopes_v1'):
                if failure == 'read':raise sqlite3.OperationalError('synthetic_read_error')
                if failure == 'budget':state.clock = .251
            return super().execute(statement, *args, **kwargs)
        def close(self):
            super().close()
            state.live -= 1
            state.closed += 1
            if failure == 'close':raise OSError('synthetic_close_error')
    expected = 'file:' + quote(str(path), safe='/') + '?mode=ro'
    def connect(database, **kwargs):
        assert database == expected and kwargs == {'uri': True, 'timeout': 0}
        assert state.live == 0
        result = original_connect(database, factory=Connection, **kwargs)
        state.opened += 1;state.live += 1
        return result
    monkeypatch.setattr(sqlite3, 'connect', connect)
    sql = result_store.MossResultStore.__new__(result_store.MossResultStore)
    stat = path.stat()
    sql.path, sql.database_identity, sql.clock = path, (stat.st_dev, stat.st_ino), lambda: state.clock
    store = history.HistoryStore.__new__(history.HistoryStore)
    store.sql = sql
    store.settings = SimpleNamespace(recording_primary_observation_policy=history.POLICY)
    try:
        yield store, state
    finally:
        assert state.live == 0 and state.closed == state.opened == 1
        assert path.stat().st_size <= 65536
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        assert time.monotonic() < deadline


@pytest.mark.parametrize('row', ['empty', 'none', 'unknown_rival', 'blocked', 'no_observations',
    'reserved', 'dirty', 'abandoned_build', 'completed_rival'])
def test_availability_matches_eligibility_and_closes_before_return(tmp_path, monkeypatch, row):
    with scalar_store(tmp_path, monkeypatch, row) as (store, state):
        answer = store.comparison_work_available()
        assert type(answer) is bool
        assert answer is (row in ('dirty', 'abandoned_build', 'completed_rival'))
        assert state.closed == 1 and state.live == 0
        queries = [sql for sql in state.statements if sql.lstrip().startswith('SELECT')]
        assert len(queries) == 1 and 'SELECT 1 ' in queries[0] and 'LIMIT 1' in queries[0]
        assert any(sql == 'PRAGMA query_only=ON' for sql in state.statements)
        assert all(not sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'BEGIN IMMEDIATE'))
            for sql in state.statements)


@pytest.mark.parametrize('failure', ['read', 'close', 'budget'])
def test_availability_never_converts_read_close_or_budget_error_to_empty(tmp_path, monkeypatch, failure):
    with scalar_store(tmp_path, monkeypatch, failure=failure) as (store, state):
        with pytest.raises(OSError if failure == 'close' else ReadError):
            store.comparison_work_available()
        assert state.closed == 1 and state.live == 0


def test_helper_keeps_existing_sql_resource_bounds():
    assert result_store.WRITE_SECONDS == .250
    assert result_store.MAX_VM_STEPS == 50000
