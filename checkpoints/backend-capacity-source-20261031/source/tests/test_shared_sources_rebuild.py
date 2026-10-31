"""Oct31 fresh reconstruction. Real SQLite/WAV/owned IO; no models or inference."""
from contextlib import contextmanager
import hashlib
import sqlite3
from types import SimpleNamespace
import wave
import pytest
from audio_backend import strict_payload_v2 as p, strict_shared_sources as shared


def component(tmp_path,monkeypatch):
 dbpath=tmp_path/'body.sqlite';db=sqlite3.connect(dbpath)
 for sql in shared.schema_statements():db.execute(sql)
 db.commit();db.close()
 store=SimpleNamespace(path=dbpath,settings=SimpleNamespace(data_dir=tmp_path,disk_reserve_bytes=0,recording_disk_reserve_bytes=0),_strict_archive_owner=None)
 owner=p._Owner(store,p.PayloadBudget());store._strict_archive_owner=owner
 @contextmanager
 def database(o,*,write=False,**kwargs):
  assert o is owner
  db=sqlite3.connect(dbpath);db.row_factory=sqlite3.Row;p.bind_connection(store,db)
  try:
   db.execute('BEGIN IMMEDIATE' if write else 'BEGIN');yield db;db.commit()
  except BaseException:db.rollback();raise
  finally:db.close()
 monkeypatch.setattr(p,'_db',database)
 (tmp_path/'uploads').mkdir()
 return owner,database


def spec(owner,name,seconds,amplitude):
 path=owner.store.settings.data_dir/'uploads'/f'{name}.wav'
 with wave.open(str(path),'wb') as w:
  w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(amplitude.to_bytes(2,'little',signed=True)*(seconds*16000))
 value=p._source_spec(owner,name,'recording_source',str(path),path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest(),seconds*16000)
 value['_shared_identity']=['store','session','recording_source','batch',name]
 return value


def test_real_120_120_54_geometry_reuses_identity_bodies(tmp_path,monkeypatch):
 owner,database=component(tmp_path,monkeypatch)
 sources=[spec(owner,str(i),seconds,i+1) for i,seconds in enumerate((120,120,54))]
 refs=[shared.ensure(owner,s) for s in sources]
 with database(owner) as db:before=tuple(db.execute('SELECT * FROM strict_shared_source_usage').fetchone())
 for s,ref in zip(sources,refs):assert shared.ensure(owner,s)==ref
 with database(owner) as db:
  assert tuple(db.execute('SELECT * FROM strict_shared_source_usage').fetchone())==before
  assert db.execute("SELECT count(*) FROM strict_shared_source_body WHERE state='published'").fetchone()[0]==3
 assert before[1]==3 and before[2]==0 and before[3]>=sum(s['bytes'] for s in sources)
 assert len({r['sha256'] for r in refs})==3 and not owner.fds and owner.scan is None
 assert owner.budget.read_bytes<=p.MAX_READ


def test_same_identity_changed_content_and_different_identity_same_content(tmp_path,monkeypatch):
 owner,database=component(tmp_path,monkeypatch);a=spec(owner,'a',1,1);ref=shared.ensure(owner,a)
 b=spec(owner,'b',1,1);assert shared.ensure(owner,b)['body_id']!=ref['body_id']
 changed={**a,'sha256':'0'*64}
 with pytest.raises(p.PayloadIntegrityError,match='identity_changed'):shared.ensure(owner,changed)


def test_corrupted_published_body_and_unexpected_file_refuse(tmp_path,monkeypatch):
 owner,database=component(tmp_path,monkeypatch);a=spec(owner,'a',1,1);ref=shared.ensure(owner,a)
 body=tmp_path/'strict-shared-sources-v1'/ref['body_id'][:4]/(ref['body_id']+'.body')
 with body.open('r+b') as f:f.seek(44);f.write(b'\xff\xff')
 with pytest.raises(Exception):shared.validate(owner,ref)
 owner.original=None
 b=spec(owner,'b',1,2);other=shared.reference(owner.store,b);directory=tmp_path/'strict-shared-sources-v1'/other['body_id'][:4];directory.mkdir(exist_ok=True)
 (directory/(other['body_id']+'.stage')).write_bytes(b'unknown')
 with pytest.raises(p.PayloadIntegrityError,match='unexpected_file'):shared.ensure(owner,b)
 with database(owner) as db:assert db.execute('SELECT count(*) FROM strict_shared_source_body').fetchone()[0]==1


def test_actual_store_shared_archive_cold_reader_reopen(tmp_path,monkeypatch):
 from audio_backend.storage import Store
 from audio_backend import change_feed
 from test_strict_payload_v2 import configuration,completed_component,make_cold,exact_reads,logical_snapshot
 cfg=configuration(tmp_path);cfg.strict_payload_source_storage='shared-source-v1'
 store=Store(tmp_path/'primary.sqlite',cfg);store.moss_automatic=SimpleNamespace(primary_only=True);change_feed.initialize(store)
 value=completed_component(store);before=logical_snapshot(store);make_cold(value,monkeypatch)
 assert logical_snapshot(store)==before;exact_reads(value)
 with store.db() as db:
  assert db.execute('SELECT format FROM strict_payload_policy').fetchone()[0]==p.SHARED_FORMAT
  assert db.execute('SELECT count(*) FROM strict_shared_source_body').fetchone()[0]>=2
 reopened=Store(store.path,cfg)
 assert reopened._strict_source_storage=='shared-source-v1'
 wrong=configuration(tmp_path)
 with pytest.raises(Exception):Store(store.path,wrong)


def test_different_key_hex_orphan_is_not_adopted(tmp_path,monkeypatch):
 owner,database=component(tmp_path,monkeypatch);a=spec(owner,'a',1,1);ref=shared.reference(owner.store,a)
 directory=tmp_path/'strict-shared-sources-v1'/ref['body_id'][:4];directory.mkdir(parents=True)
 other=ref['body_id'][:4]+'0'*60
 assert other!=ref['body_id']
 (directory/(other+'.body')).write_bytes(b'undocumented allocation')
 with pytest.raises(p.PayloadIntegrityError,match='unregistered_file'):shared.ensure(owner,a)
 with database(owner) as db:assert db.execute('SELECT count(*) FROM strict_shared_source_body').fetchone()[0]==0


def test_reserved_copy_failure_stays_charged_and_not_adopted(tmp_path,monkeypatch):
 owner,database=component(tmp_path,monkeypatch);a=spec(owner,'a',1,1)
 def fail(*args):raise OSError('controlled copy failure')
 monkeypatch.setattr(p.base,'_copy_spec',fail)
 with pytest.raises(OSError,match='controlled'):shared.ensure(owner,a)
 with database(owner) as db:
  assert db.execute('SELECT state FROM strict_shared_source_body').fetchone()[0]=='reserved'
  usage=tuple(db.execute('SELECT bodies,reserved,allocated FROM strict_shared_source_usage').fetchone())
  assert usage[0]==1 and usage[1]>0 and usage[2]==0
 with pytest.raises(p.PayloadIntegrityError,match='reserved_unknown'):shared.ensure(owner,a)
 assert not owner.fds and owner.scan is None


def test_reserved_two_names_require_same_inode(tmp_path,monkeypatch):
 owner,database=component(tmp_path,monkeypatch);a=spec(owner,'a',1,1);ref=shared.reference(owner.store,a)
 other=ref['body_id'][:4]+'0'*60;assert other!=ref['body_id']
 directory=tmp_path/'strict-shared-sources-v1'/other[:4];directory.mkdir(parents=True)
 with database(owner,write=True) as db:
  owner.transition_db=db;owner.transition={('shared',other)}
  try:db.execute("INSERT INTO strict_shared_source_body VALUES(?,?,?,?,?,'reserved',?,NULL,NULL)",
    (other,'[]',100,'0'*64,1,8192))
  finally:owner.transition.clear();owner.transition_db=None
 for suffix in ('stage','body'):(directory/(other+'.'+suffix)).write_bytes(b'x')
 with pytest.raises(p.PayloadIntegrityError,match='multiple_inodes'):shared.ensure(owner,a)
 assert not owner.fds and owner.scan is None
