"""Bounded real SQLite refusals; no model, service, or historical-database mutation."""
import errno
import logging
import sqlite3
from types import SimpleNamespace

import pytest

from audio_backend import moss_result_store as history
from audio_backend.native_owned_support_cache import ReadError
from audio_backend.settings import Settings

QUERY = 'WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n'
FIELDS = {'operation', 'phase', 'exception_class', 'sqlite_code', 'sqlite_name', 'errno', 'vm_steps', 'budget_category'}


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / 'private-fixture-name.sqlite3'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE sample(value INTEGER UNIQUE)')
    db.commit(); db.close()
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    return history.MossResultStore(SimpleNamespace(path=path, settings=settings))


def records(caplog):
    found = [r.result_storage_failure for r in caplog.records if hasattr(r, 'result_storage_failure')]
    for record in found:
        assert set(record) == FIELDS
    for record in caplog.records:
        assert 'private-fixture-name' not in record.getMessage()
        assert 'PRIVATE_EXCEPTION_PAYLOAD' not in record.getMessage()
    return found


def storage_refusal():
    return pytest.raises(ReadError, match='^result_storage_unavailable$')


@pytest.mark.parametrize('write', [False, True])
def test_success_is_silent_preserves_clock_count_and_transactions(archive, caplog, write):
    calls = []
    archive.clock = lambda: calls.append(None) or 0
    with archive._database(write=write) as db:
        assert db.execute('PRAGMA query_only').fetchone()[0] == int(not write)
        if write:db.execute('INSERT INTO sample VALUES(7)')
        else:assert db.execute('SELECT count(*) FROM sample').fetchone()[0] == 0
    assert len(calls) == (5 if write else 4)
    assert records(caplog) == []
    with archive._database() as db:
        assert db.execute('SELECT count(*) FROM sample').fetchone()[0] == int(write)


@pytest.mark.parametrize('write', [False, True])
def test_busy_is_one_zero_wait_attempt(archive, caplog, monkeypatch, write):
    real_connect = sqlite3.connect
    blocker = real_connect(archive.path, timeout=0)
    blocker.execute('BEGIN EXCLUSIVE')
    calls = []
    def connect(*args, **kwargs):
        calls.append(kwargs)
        return real_connect(*args, **kwargs)
    monkeypatch.setattr(history.sqlite3, 'connect', connect)
    try:
        with storage_refusal():
            with archive._database(write=write) as db:
                db.execute('SELECT count(*) FROM sample').fetchone()
    finally:
        blocker.rollback(); blocker.close()
    assert len(calls) == 1 and calls[0]['timeout'] == 0
    record, = records(caplog)
    assert record['operation'] == ('write' if write else 'read')
    assert record['phase'] == ('begin' if write else 'body')
    assert record['sqlite_code'] == sqlite3.SQLITE_BUSY and record['sqlite_name'] == 'SQLITE_BUSY'
    assert record['budget_category'] is None
    with archive._database() as db:assert db.execute('SELECT count(*) FROM sample').fetchone()[0] == 0


def test_vm_interrupt_retains_exact_limit_and_no_extra_clock(archive, caplog):
    archive.clock = lambda: 0
    budget = [.25, 0]
    with storage_refusal():
        with archive._database(budget=budget) as db:db.execute(QUERY).fetchone()
    record, = records(caplog)
    assert budget[1] == history.MAX_VM_STEPS == record['vm_steps'] == 50000
    assert record['phase'] == 'body' and record['budget_category'] == 'vm_steps'
    assert record['sqlite_code'] == sqlite3.SQLITE_INTERRUPT
    assert record['sqlite_name'] == 'SQLITE_INTERRUPT'


@pytest.mark.parametrize('at_progress', [False, True])
def test_clock_deadline_and_clock_progress_interrupt(archive, caplog, at_progress):
    values = iter(([0, 0, 0, 1] if at_progress else [0, 1]))
    archive.clock = lambda: next(values)
    with storage_refusal():
        with archive._database() as db:db.execute(QUERY).fetchone()
    record, = records(caplog)
    assert record['budget_category'] == 'deadline'
    assert record['phase'] == ('body' if at_progress else 'pre_open_check')
    assert record['sqlite_code'] == (sqlite3.SQLITE_INTERRUPT if at_progress else None)
    assert record['vm_steps'] == (100 if at_progress else 0)


def test_first_budget_category_is_observed_not_inferred(archive, caplog):
    # check() retains deadline-first priority when both limits were already exceeded.
    archive.clock = lambda: 1
    with storage_refusal():
        with archive._database(budget=[0, 50000]):pytest.fail('must refuse before opening')
    record, = records(caplog)
    assert record['budget_category'] == 'deadline'


def test_os_error_has_only_numeric_errno(archive, caplog):
    archive.path.rename(archive.path.with_suffix('.moved'))
    with storage_refusal():
        with archive._database():pytest.fail('missing path')
    record, = records(caplog)
    assert record['phase'] == 'stat' and record['exception_class'] == 'FileNotFoundError'
    assert record['errno'] == errno.ENOENT
    assert record['budget_category'] is None and record['sqlite_code'] is None


def test_extended_sqlite_code_and_rollback(archive, caplog):
    with storage_refusal():
        with archive._database(write=True) as db:
            db.execute('INSERT INTO sample VALUES(7)')
            db.execute('INSERT INTO sample VALUES(7)')
    record, = records(caplog)
    assert record['sqlite_code'] == sqlite3.SQLITE_CONSTRAINT_UNIQUE
    assert record['sqlite_name'] == 'SQLITE_CONSTRAINT_UNIQUE'
    assert record['exception_class'] == 'IntegrityError'
    with archive._database() as db:assert db.execute('SELECT count(*) FROM sample').fetchone()[0] == 0


@pytest.mark.parametrize('sink_error', [RuntimeError, KeyboardInterrupt])
def test_broken_sink_never_replaces_original_refusal(archive, monkeypatch, sink_error):
    def broken(*args, **kwargs):raise sink_error('PRIVATE_EXCEPTION_PAYLOAD')
    monkeypatch.setattr(logging.getLogger(history.__name__), 'warning', broken)
    archive.clock = lambda: 0
    with storage_refusal():
        with archive._database() as db:db.execute(QUERY).fetchone()


@pytest.mark.parametrize('late_clock', [False, True])
def test_commit_uncertainty_remains_committed_without_retry(archive, monkeypatch, caplog, late_clock):
    real_connect = sqlite3.connect
    counts = {'connect': 0, 'commit': 0}
    class Uncertain(sqlite3.Connection):
        def commit(self):
            counts['commit'] += 1
            super().commit()
            if not late_clock:raise sqlite3.OperationalError('PRIVATE_EXCEPTION_PAYLOAD')
    def connect(*args, **kwargs):
        counts['connect'] += 1
        return real_connect(*args, **kwargs, factory=Uncertain)
    monkeypatch.setattr(history.sqlite3, 'connect', connect)
    times = iter([0, 0, 0, 0, 1])
    archive.clock = (lambda: next(times)) if late_clock else (lambda: 0)
    with storage_refusal():
        with archive._database(write=True) as db:db.execute('INSERT INTO sample VALUES(7)')
    assert counts == {'connect': 1, 'commit': 1}
    record, = records(caplog)
    assert record['phase'] == ('post_commit_check' if late_clock else 'commit')
    assert record['budget_category'] == ('deadline' if late_clock else None)
    db = real_connect(archive.path)
    try:assert db.execute('SELECT value FROM sample').fetchall() == [(7,)]
    finally:db.close()


def test_other_read_error_unchanged_and_silent(archive, caplog):
    original = ReadError(409, 'PRIVATE_EXCEPTION_PAYLOAD')
    with pytest.raises(ReadError) as caught:
        with archive._database():raise original
    assert caught.value is original
    assert records(caplog) == []


def test_post_commit_headroom_failure_remains_success_with_write_blocked(archive, monkeypatch, caplog):
    calls = []
    def headroom():
        calls.append(None)
        if len(calls) == 3:raise OSError(errno.ENOSPC, 'PRIVATE_EXCEPTION_PAYLOAD')
    monkeypatch.setattr(archive, '_headroom', headroom)
    with archive._database(write=True) as db:db.execute('INSERT INTO sample VALUES(7)')
    assert archive.write_blocked
    record, = records(caplog)
    assert record['phase'] == 'post_commit_headroom' and record['errno'] == errno.ENOSPC
    with archive._database() as db:assert db.execute('SELECT value FROM sample').fetchone()[0] == 7
