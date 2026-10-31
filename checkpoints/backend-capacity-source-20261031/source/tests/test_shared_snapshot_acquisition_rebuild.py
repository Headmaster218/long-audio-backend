"""Real SQLite acquisition boundaries; no body or transaction retries."""
import sqlite3
import threading
import time
from types import SimpleNamespace
import pytest
from audio_backend import strict_payload_v2 as p


def owner():
 value=SimpleNamespace(budget=p.PayloadBudget(),original=None,unknown=False)
 def remember(error):
  if value.original is None:value.original=error
 def uncertain(error):remember(error);value.unknown=True
 value.remember=remember;value.uncertain=uncertain
 return value

@pytest.mark.parametrize('write',[False,True])
def test_real_lock_release_only_acquires_once(tmp_path,write):
 path=tmp_path/'db.sqlite';locker=sqlite3.connect(path,check_same_thread=False)
 locker.execute('CREATE TABLE data(value)');locker.execute('INSERT INTO data VALUES(1)');locker.commit();locker.execute('BEGIN EXCLUSIVE')
 def release():time.sleep(.03);locker.rollback();locker.close()
 thread=threading.Thread(target=release);thread.start();db=sqlite3.connect(path,timeout=0);o=owner()
 try:
  p._begin_shared_snapshot(o,db,write=write)
  assert db.in_transaction and db.execute('PRAGMA busy_timeout').fetchone()[0]==0
  assert db.execute('SELECT value FROM data').fetchone()[0]==1
  if write:db.execute('UPDATE data SET value=value+1')
  db.commit()
 finally:db.close();thread.join(1)
 assert not thread.is_alive() and not o.unknown

@pytest.mark.parametrize('write',[False,True])
def test_persistent_lock_respects_original_short_budget(tmp_path,write):
 path=tmp_path/'db.sqlite';locker=sqlite3.connect(path);locker.execute('CREATE TABLE data(value)');locker.commit();locker.execute('BEGIN EXCLUSIVE')
 db=sqlite3.connect(path,timeout=0);o=owner();o.budget.deadline=time.monotonic()+.04;started=time.monotonic()
 try:
  with pytest.raises(sqlite3.OperationalError):p._begin_shared_snapshot(o,db,write=write)
  assert time.monotonic()-started<.2 and db.execute('PRAGMA busy_timeout').fetchone()[0]==0
 finally:db.rollback();db.close();locker.rollback();locker.close()


def test_readonly_snapshot_is_established_before_body(tmp_path):
 path=tmp_path/'db.sqlite';writer=sqlite3.connect(path);writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE data(value)');writer.execute('INSERT INTO data VALUES(1)');writer.commit()
 db=sqlite3.connect(path,timeout=0);o=owner()
 try:
  p._begin_shared_snapshot(o,db,write=False)
  writer.execute('UPDATE data SET value=2');writer.commit()
  assert db.execute('SELECT value FROM data').fetchone()[0]==1
 finally:db.rollback();db.close();writer.close()


def test_unknown_nested_callback_gets_no_added_wait():
 o=owner();o.budget.caller_check=lambda:None
 assert o.budget.remaining_seconds()==0


def test_read_charge_refreshes_remaining_wait(monkeypatch):
 clock=[10.]
 monkeypatch.setattr(p.time,'monotonic',lambda:clock[0])
 o=owner();o.budget.deadline=10.1
 def progress():clock[0]+=.075;return False
 o.budget.progress=progress
 class DB:
  def __init__(self):self.calls=[]
  def execute(self,sql):self.calls.append(sql);return self
  def fetchall(self):return [(1,)]
  def close(self):pass
 db=DB();p._begin_shared_snapshot(o,db,write=False)
 waits=[int(x.split('=')[1]) for x in db.calls if x.startswith('PRAGMA busy_timeout=')]
 assert 98<=waits[0]<=100 and 23<=waits[1]<=25 and waits[-1]==0
