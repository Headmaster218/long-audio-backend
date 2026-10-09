import importlib.util,json,sqlite3
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('capture_observer',Path(__file__).parents[1]/'scripts/benchmarks/capture_endurance_observations.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

@pytest.fixture(autouse=True)
def synthetic_disk_headroom(monkeypatch):
 from types import SimpleNamespace
 monkeypatch.setattr(m.shutil,'disk_usage',lambda path:SimpleNamespace(free=10*1024**3))

def fixture(tmp_path):
 run=tmp_path/'run';(run/'checkpoint').mkdir(parents=True)
 for name in ['run.json','effective-settings.json']:(run/name).write_text('{}')
 db=run/'checkpoint/jobs.sqlite3'
 with sqlite3.connect(db) as c:
  c.executescript('CREATE TABLE speaker_observations(id TEXT,chunk_id TEXT,window_start REAL,local_speaker_id TEXT,embedding TEXT,matched_speaker_id TEXT);CREATE TABLE chunks(next_offset REAL);INSERT INTO chunks VALUES(30);CREATE TABLE speaker_turns(chunk_id TEXT,speaker_window_start REAL,speaker_local_id TEXT,speaker_status TEXT);CREATE TABLE segments(candidate_observation_id TEXT);')
  c.execute('insert into speaker_observations values(?,?,?,?,?,?)',('a','c',0,'voice','[1,0]','person'))
  c.execute('insert into speaker_turns values(?,?,?,?)',('c',0,'voice','provisional'));c.execute('insert into segments values(?)',('a',))
 return run,db,tmp_path/'private.sqlite3'

def test_idempotence_and_scope(tmp_path):
 run,db,out=fixture(tmp_path);before=m.sha(db);r=m.capture(run,out)
 assert r['added']==1 and r['missing_durable_observation_pointers']==0
 assert r['durable_query_scopes_by_status']['provisional']=={'total':1,'captured':1}
 assert m.capture(run,out)['unchanged'] and m.sha(db)==before

def test_evicted_original_survives(tmp_path):
 run,db,out=fixture(tmp_path);m.capture(run,out)
 with sqlite3.connect(db) as c:c.execute('delete from speaker_observations')
 r=m.capture(run,out);assert r['total']==1 and r['missing_durable_observation_pointers']==0

def test_changed_original_refused(tmp_path):
 run,db,out=fixture(tmp_path);m.capture(run,out)
 with sqlite3.connect(db) as c:c.execute("update speaker_observations set embedding='[0,1]'")
 with pytest.raises(RuntimeError,match='changed'):m.capture(run,out)
 with sqlite3.connect(out) as c:assert json.loads(c.execute('select payload from observations').fetchone()[0])['embedding']=='[1,0]'

def test_missing_pointer_visible(tmp_path):
 run,db,out=fixture(tmp_path)
 with sqlite3.connect(db) as c:c.execute("insert into segments values('lost')")
 assert m.capture(run,out)['missing_durable_observation_pointers']==1

def test_null_scope_deduplicates(tmp_path):
 run,db,out=fixture(tmp_path)
 with sqlite3.connect(db) as c:c.execute("insert into speaker_turns values('c',NULL,NULL,'unknown')")
 m.capture(run,out)
 with sqlite3.connect(db) as c:c.execute('update chunks set next_offset=31')
 m.capture(run,out)
 with sqlite3.connect(out) as c:assert c.execute('select count(*) from query_scopes').fetchone()[0]==2

def test_snapshot_fds_closed_even_without_gc(tmp_path):
 import gc,os
 if not Path('/proc/self/fd').exists():pytest.skip('Linux FD inspection')
 run,db,out=fixture(tmp_path);gc.collect();gc.disable()
 try:
  for _ in range(5):m.capture(run,out)
  leaked=[]
  for f in Path('/proc/self/fd').iterdir():
   try:
    target=os.readlink(f)
    if str(tmp_path) in target and 'snapshot.sqlite3 (deleted)' in target:leaked.append(target)
   except OSError:pass
  assert leaked==[]
 finally:gc.enable();gc.collect()

def test_exception_closes_all_sqlite_fds(tmp_path):
 import gc,os,contextlib
 if not Path('/proc/self/fd').exists():pytest.skip('Linux FD inspection')
 run,db,out=fixture(tmp_path)
 with contextlib.closing(sqlite3.connect(db)) as c:c.execute('drop table speaker_observations');c.commit()
 gc.collect();gc.disable()
 try:
  with pytest.raises(sqlite3.OperationalError):m.capture(run,out)
  leaked=[]
  for f in Path('/proc/self/fd').iterdir():
   try:
    target=os.readlink(f)
    if str(tmp_path) in target:leaked.append(target)
   except OSError:pass
  assert leaked==[]
 finally:gc.enable();gc.collect()

def test_disk_reserve_refuses_before_backup(tmp_path,monkeypatch):
 from types import SimpleNamespace
 run,db,out=fixture(tmp_path)
 monkeypatch.setattr(m.shutil,'disk_usage',lambda path:SimpleNamespace(free=5*1024**3))
 with pytest.raises(RuntimeError,match='disk reserve'):m.capture(run,out)
 assert not out.exists()
