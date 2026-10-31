"""Finite SQLite admission boundary controls; no audio, models or live database."""
import sqlite3,time
from types import SimpleNamespace
import pytest
from audio_backend import primary_commit_admission as admission
from audio_backend.native_owned_cell_store import Budget
from audio_backend.native_owned_cells import Unavailable


def error(code):
    exc=sqlite3.OperationalError('synthetic')
    if code is not None:exc.sqlite_errorcode=code
    return exc


def test_real_sqlite_busy_released_during_same_budget_backoff(tmp_path,monkeypatch):
    path=tmp_path/'db';holder=sqlite3.connect(path,timeout=0);holder.execute('CREATE TABLE t(x)');holder.commit();holder.execute('BEGIN IMMEDIATE')
    db=sqlite3.connect(path,timeout=0);budget=Budget();waits=[]
    def release(seconds):waits.append(seconds);holder.rollback()
    monkeypatch.setattr(admission.time,'sleep',release)
    try:
        with budget.notice_phase():admission.begin(db,budget)
        assert waits==[.01] and db.in_transaction
        db.execute('INSERT INTO t VALUES(1)');db.commit()
        assert db.execute('SELECT COUNT(*) FROM t').fetchone()[0]==1
    finally:db.close();holder.close()


def test_real_busy_exhaustion_is_four_attempts_same_budget(tmp_path,monkeypatch):
    path=tmp_path/'db';holder=sqlite3.connect(path,timeout=0);holder.execute('CREATE TABLE t(x)');holder.commit();holder.execute('BEGIN IMMEDIATE')
    db=sqlite3.connect(path,timeout=0);budget=Budget();waits=[]
    monkeypatch.setattr(admission.time,'sleep',lambda seconds:waits.append(seconds))
    try:
        with budget.notice_phase(),pytest.raises(sqlite3.OperationalError) as caught:admission.begin(db,budget)
        assert caught.value.sqlite_errorcode==sqlite3.SQLITE_BUSY
        assert waits==[.01,.02,.04] and not db.in_transaction
    finally:db.close();holder.rollback();holder.close()


@pytest.mark.parametrize('code',[None,True,'5',-251,2**32+5,sqlite3.SQLITE_LOCKED,sqlite3.SQLITE_IOERR])
def test_unclassified_or_permanent_error_not_retried(code,monkeypatch):
    calls=[];exc=error(code)
    def execute(sql):calls.append(sql);raise exc
    db=SimpleNamespace(execute=execute,in_transaction=False)
    monkeypatch.setattr(admission.time,'sleep',lambda _:pytest.fail('unclassified backoff'))
    with pytest.raises(sqlite3.OperationalError) as caught:admission.begin(db,Budget())
    assert caught.value is exc and calls==['BEGIN IMMEDIATE']


def test_acquired_begin_then_error_is_not_replayed(monkeypatch):
    db=sqlite3.connect(':memory:',timeout=0);calls=[]
    class Proxy:
        @property
        def in_transaction(self):return db.in_transaction
        def execute(self,sql):calls.append(sql);db.execute(sql);raise error(sqlite3.SQLITE_BUSY)
    monkeypatch.setattr(admission.time,'sleep',lambda _:pytest.fail('acquired backoff'))
    try:
        with pytest.raises(sqlite3.OperationalError):admission.begin(Proxy(),Budget())
        assert db.in_transaction and calls==['BEGIN IMMEDIATE']
    finally:db.rollback();db.close()


def test_original_deadline_expires_during_backoff(tmp_path):
    path=tmp_path/'db';holder=sqlite3.connect(path,timeout=0);holder.execute('CREATE TABLE t(x)');holder.commit();holder.execute('BEGIN IMMEDIATE')
    db=sqlite3.connect(path,timeout=0);budget=Budget();budget.deadline=time.monotonic()+.005
    try:
        with pytest.raises(Unavailable):
            with budget.notice_phase():admission.begin(db,budget)
        assert not db.in_transaction and budget.elapsed>=.005
    finally:db.close();holder.rollback();holder.close()


def test_body_and_commit_are_never_owned_by_admission():
    db=sqlite3.connect(':memory:');seen=[]
    db.set_trace_callback(seen.append)
    try:
        admission.begin(db,Budget());assert seen==['BEGIN IMMEDIATE']
    finally:db.rollback();db.close()


def test_diagnostic_logger_failure_preserves_original_error(monkeypatch):
    exc=error(sqlite3.SQLITE_IOERR)
    def fail(sql):raise exc
    monkeypatch.setattr(admission._LOG,'warning',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('logger unavailable')))
    with pytest.raises(sqlite3.OperationalError) as caught:
        admission.begin(SimpleNamespace(execute=fail,in_transaction=False),Budget())
    assert caught.value is exc


def test_postrollback_diagnostic_observation_cannot_mask_original(tmp_path,monkeypatch):
    from audio_backend import storage
    original_connect=sqlite3.connect
    exc=error(sqlite3.SQLITE_IOERR)
    class Connection(sqlite3.Connection):
        broken_observation=False
        def rollback(self):
            super().rollback();self.broken_observation=True
        @property
        def in_transaction(self):
            if self.broken_observation:raise RuntimeError('diagnostic read unavailable')
            return super().in_transaction
    monkeypatch.setattr(storage.sqlite3,'connect',lambda *a,**k:original_connect(*a,**k,factory=Connection))
    store=storage.Store.__new__(storage.Store);store.path=tmp_path/'db'
    budget=Budget();budget._primary_commit_context=True
    with pytest.raises(sqlite3.OperationalError) as caught:
        with store.db(budget=budget):raise exc
    assert caught.value is exc and budget.sql_outcome=='rolled_back'
