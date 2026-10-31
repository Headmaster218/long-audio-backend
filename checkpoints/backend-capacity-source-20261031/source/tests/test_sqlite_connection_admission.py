"""Fresh SQLite connection admission: real lock controls, no inference/network."""
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from audio_backend.sqlite_admission import prime_connection, budget_remaining_seconds
from audio_backend.moss_result_store import MossResultStore, ReadError


def database(path, mode='DELETE'):
    db=sqlite3.connect(path,timeout=0,check_same_thread=False)
    db.execute('PRAGMA journal_mode='+mode)
    db.execute('CREATE TABLE facts(value INTEGER NOT NULL)')
    db.execute('INSERT INTO facts VALUES(0)');db.commit()
    return db


def allowance(seconds):
    deadline=time.monotonic()+seconds
    def check():
        if time.monotonic()>=deadline:raise TimeoutError('original_budget')
    return check,lambda:max(0.,deadline-time.monotonic())


def test_short_exclusive_lock_uses_one_native_read(tmp_path):
    path=tmp_path/'short.sqlite';writer=database(path);writer.execute('BEGIN EXCLUSIVE')
    reader=sqlite3.connect(path,timeout=0)
    trace=[];reader.set_trace_callback(trace.append)
    released=threading.Event()
    def release():
        time.sleep(.035);writer.rollback();released.set()
    thread=threading.Thread(target=release);thread.start()
    try:
        check,remaining=allowance(.5)
        prime_connection(reader,check=check,remaining_seconds=remaining)
        assert released.is_set() and not reader.in_transaction
        assert reader.execute('PRAGMA busy_timeout').fetchone()[0]==0
        assert sum(s=='SELECT rootpage FROM sqlite_schema LIMIT 1' for s in trace)==1
    finally:
        thread.join(1);reader.close();writer.close()
    assert not thread.is_alive()


def test_persistent_exclusive_lock_never_enters_business_body(tmp_path):
    path=tmp_path/'persistent.sqlite';writer=database(path);writer.execute('BEGIN EXCLUSIVE')
    reader=sqlite3.connect(path,timeout=0);body=[];start=time.monotonic()
    try:
        check,remaining=allowance(.07)
        with pytest.raises((sqlite3.OperationalError,TimeoutError)):
            prime_connection(reader,check=check,remaining_seconds=remaining)
            body.append(True)
        assert not body and time.monotonic()-start<.35
        assert not reader.in_transaction and reader.execute('PRAGMA busy_timeout').fetchone()[0]==0
    finally:reader.close();writer.rollback();writer.close()


def test_wal_prime_does_not_freeze_original_business_snapshot(tmp_path):
    path=tmp_path/'snapshot.sqlite';writer=database(path,'WAL');reader=sqlite3.connect(path,timeout=0)
    try:
        check,remaining=allowance(.5);prime_connection(reader,check=check,remaining_seconds=remaining,restore_ms=15000)
        assert not reader.in_transaction and reader.execute('PRAGMA busy_timeout').fetchone()[0]==15000
        writer.execute('UPDATE facts SET value=7');writer.commit()
        reader.execute('BEGIN');assert reader.execute('SELECT value FROM facts').fetchone()[0]==7
        reader.rollback()
    finally:reader.close();writer.close()


def test_existing_transaction_refuses_before_any_sql(tmp_path):
    db=database(tmp_path/'transaction.sqlite');db.execute('BEGIN');seen=[];db.set_trace_callback(seen.append)
    try:
        with pytest.raises(sqlite3.OperationalError,match='inside_transaction'):
            prime_connection(db,check=lambda:None,remaining_seconds=lambda:1)
        assert seen==[] and db.in_transaction
    finally:db.close()


class Cursor:
    def __init__(self,*,fetch_error=None,close_error=None):
        self.fetch_error=fetch_error;self.close_error=close_error;self.closed=0
    def fetchall(self):
        if self.fetch_error:raise self.fetch_error
        return [(1,)]
    def close(self):
        self.closed+=1
        if self.close_error:raise self.close_error


class Connection:
    in_transaction=False
    def __init__(self,cursor=None,reset_error=None):self.cursor=cursor or Cursor();self.sql=[];self.reset_error=reset_error
    def execute(self,sql):
        self.sql.append(sql)
        if sql=='PRAGMA busy_timeout=0' and self.reset_error:raise self.reset_error
        return self.cursor if sql.startswith('SELECT') else SimpleNamespace()


def test_timeout_clips_after_charge_work_not_before():
    now=[100.];deadline=100.1;db=Connection()
    def check():
        if now[0]>=deadline:raise TimeoutError('deadline')
    def charge():now[0]+=.08;return 0
    prime_connection(db,check=check,remaining_seconds=lambda:deadline-now[0],charge=charge)
    timeout=int(next(s.rsplit('=',1)[1] for s in db.sql if s.startswith('PRAGMA busy_timeout=')))
    assert timeout<=20  # The read must not receive the obsolete pre-charge100ms.


def test_charge_refusal_preserves_original_error_and_no_select():
    db=Connection(reset_error=OSError('reset_failed'));failure=TimeoutError('exact_budget');exhausted=[False]
    def check():
        if exhausted[0]:raise failure
    def charge():exhausted[0]=True;return 1
    with pytest.raises(TimeoutError) as raised:
        prime_connection(db,check=check,remaining_seconds=lambda:1,charge=charge)
    assert raised.value is failure and not any(s.startswith('SELECT') for s in db.sql)
    assert isinstance(failure.sqlite_admission_cleanup_error,OSError)


@pytest.mark.parametrize('where',['fetch','close'])
def test_cursor_close_attempted_once(where):
    failure=OSError(where);cursor=Cursor(fetch_error=failure if where=='fetch' else None,close_error=failure if where=='close' else None)
    db=Connection(cursor)
    with pytest.raises(OSError) as raised:prime_connection(db,check=lambda:None,remaining_seconds=lambda:1)
    assert raised.value is failure and cursor.closed==1
    assert db.sql[-1]=='PRAGMA busy_timeout=0'


def test_custom_clock_remaining_adapter_and_unknown_budget():
    b=SimpleNamespace(deadline=101.,clock=lambda:100.75,check=lambda:None)
    assert budget_remaining_seconds(b)==.25
    assert budget_remaining_seconds(SimpleNamespace(check=lambda:None))==0


def moss(path):
    store=MossResultStore.__new__(MossResultStore);store.path=Path(path);store.clock=time.monotonic
    stat=store.path.stat();store.database_identity=(stat.st_dev,stat.st_ino)
    store.settings=SimpleNamespace(disk_reserve_bytes=0,native_owned_cells_enabled=False);store.write_blocked=False
    return store


def test_moss_begin_short_writer_lock_resets_before_body(tmp_path):
    path=tmp_path/'moss-short.sqlite';writer=database(path,'WAL');writer.execute('BEGIN IMMEDIATE')
    store=moss(path);body=[]
    thread=threading.Thread(target=lambda:(time.sleep(.035),writer.rollback()));thread.start()
    try:
        with store._database(write=True,budget=[time.monotonic()+.4,0]) as db:
            body.append(True);assert db.execute('PRAGMA busy_timeout').fetchone()[0]==0
            db.execute('UPDATE facts SET value=1')
        assert body==[True] and writer.execute('SELECT value FROM facts').fetchone()[0]==1
    finally:thread.join(1);writer.close()
    assert not thread.is_alive()


def test_moss_begin_deadline_never_enters_body(tmp_path):
    path=tmp_path/'moss-persistent.sqlite';writer=database(path,'WAL');writer.execute('BEGIN IMMEDIATE');store=moss(path);body=[]
    try:
        with pytest.raises(ReadError):
            with store._database(write=True,budget=[time.monotonic()+.06,0]) as db:body.append(True)
        assert body==[] and writer.execute('SELECT value FROM facts').fetchone()[0]==0
    finally:writer.rollback();writer.close()


def test_moss_body_failure_not_replayed_and_rolls_back(tmp_path):
    path=tmp_path/'moss-body.sqlite';observer=database(path,'WAL');store=moss(path);body=[]
    try:
        with pytest.raises(ReadError):
            with store._database(write=True) as db:
                body.append(True);db.execute('UPDATE facts SET value=2');raise sqlite3.OperationalError('injected body failure')
        assert body==[True] and observer.execute('SELECT value FROM facts').fetchone()[0]==0
    finally:observer.close()


def test_moss_real_commit_busy_has_no_body_replay(tmp_path):
    path=tmp_path/'moss-commit.sqlite';reader=database(path,'DELETE');reader.execute('BEGIN');reader.execute('SELECT * FROM facts').fetchall()
    store=moss(path);body=[]
    try:
        with pytest.raises(ReadError):
            with store._database(write=True) as db:
                body.append(True);db.execute('UPDATE facts SET value=3')
        assert body==[True]
        reader.rollback();assert reader.execute('SELECT value FROM facts').fetchone()[0]==0
    finally:reader.close()


def test_real_wal_fresh_connection_churn_with_paced_writer(tmp_path):
    """1000 fresh reads +125 writes; actual SQLite, not mocked BUSY codes."""
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'churn.sqlite';setup=database(path,'WAL');setup.close()
    barrier=threading.Barrier(5)
    def role(writer=False):
        barrier.wait(timeout=3);count=0
        for i in range(125 if writer else 250):
            db=sqlite3.connect(path,timeout=0)
            try:
                check,remaining=allowance(.5)
                prime_connection(db,check=check,remaining_seconds=remaining,restore_ms=250 if writer else 0)
                if writer:
                    db.execute('BEGIN IMMEDIATE');db.execute('UPDATE facts SET value=value+1');db.commit()
                else:
                    db.execute('BEGIN');row=db.execute('SELECT value FROM facts').fetchone()
                    assert 0<=row[0]<=125;db.rollback()
                count+=1
            finally:db.close()
            if writer:time.sleep(.001)
        return count
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures=[pool.submit(role,False) for _ in range(4)]+[pool.submit(role,True)]
        assert [f.result(timeout=15) for f in futures]==[250,250,250,250,125]
    db=sqlite3.connect(path)
    try:
        assert db.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert db.execute('SELECT value FROM facts').fetchone()==(125,)
    finally:db.close()


@pytest.mark.parametrize('previous_outcome',[None,'commit_attempted'])
def test_store_priming_refusal_initializes_budget_outcome(tmp_path,monkeypatch,previous_outcome):
    from audio_backend.storage import Store
    from audio_backend.native_owned_cell_store import Budget,Unavailable
    from audio_backend import sqlite_admission
    from test_strict_archive_v2 import configuration,positive_close
    store=Store(tmp_path/'audio.sqlite',configuration(tmp_path));budget=Budget();failure=Unavailable('injected_pre_body_admission_refusal');body=[]
    if previous_outcome is not None:budget.sql_outcome=previous_outcome
    def refuse(*a,**kw):raise failure
    monkeypatch.setattr(sqlite_admission,'prime_connection',refuse)
    try:
        with pytest.raises(Unavailable) as raised:
            with store.db(budget=budget):body.append(True)
        assert raised.value is failure and body==[]
        assert budget.sql_outcome=='rolled_back' and budget.primary_sql is False
        assert not store.strict_cleanup_uncertain()
    finally:positive_close(store)
