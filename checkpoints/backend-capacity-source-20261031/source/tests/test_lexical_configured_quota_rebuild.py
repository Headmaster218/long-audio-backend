"""New Oct31 reconstruction checks; no Oct30 artifacts or pass claims reused."""
import sqlite3
from dataclasses import replace
import pytest
from audio_backend.settings import Settings
from audio_backend.storage import Store
from test_lexical_evidence import window


def fresh(path,limit=32*1024**2):
 settings=Settings(data_dir=path,model_dir=path/'unused',start_worker=False,
  lexical_evidence_enabled=True,lexical_evidence_session_bytes=limit)
 store=Store(path/'jobs.sqlite3',settings)
 sid=store.create_session('quota')['id'];cid,_=store.add_chunk(sid,'test','a'*64,path/'not-opened.wav',300)
 with store.db() as db:db.execute("UPDATE chunks SET status='running' WHERE id=?",(cid,))
 return store,settings,sid,store.chunk(cid)


def test_default_and_sql_binding(tmp_path):
 store,settings,sid,chunk=fresh(tmp_path)
 assert settings.lexical_evidence_session_bytes==33554432
 with store.db() as db:
  definition=db.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[0]
  assert 'lexical_evidence_bytes<=33554432' in definition
 with pytest.raises(sqlite3.IntegrityError):
  with store.db() as db:db.execute('UPDATE sessions SET lexical_evidence_bytes=? WHERE id=?',(33554433,sid))


def test_small_bound_reopen_and_frozen_settings(tmp_path):
 store,settings,sid,chunk=fresh(tmp_path,4096)
 settings.lexical_evidence_session_bytes=8192  # Runtime mutation cannot widen the bound captured at admission.
 with store.db() as db:db.execute('UPDATE sessions SET lexical_evidence_bytes=4095 WHERE id=?',(sid,))
 assert store.commit_window(chunk,0,1,window(chunk))
 with store.db() as db:
  assert db.execute('SELECT lexical_evidence_bytes FROM sessions WHERE id=?',(sid,)).fetchone()[0]==4095
  assert db.execute('SELECT lexical_evidence FROM segments').fetchone()[0] is None
 reopened=Store(store.path,replace(settings,lexical_evidence_session_bytes=4096))
 assert reopened._lexical_session_bytes==4096
 with pytest.raises(ValueError,match='lexical budget'):
  Store(store.path,replace(settings,lexical_evidence_session_bytes=8192))
 assert Store(store.path,replace(settings,lexical_evidence_session_bytes=4096))._lexical_session_bytes==4096


def test_cross_original_bound_without_large_allocation(tmp_path):
 store,settings,sid,chunk=fresh(tmp_path,256*1024**2)
 with store.db() as db:db.execute('UPDATE sessions SET lexical_evidence_bytes=? WHERE id=?',(33554431,sid))
 assert store.commit_window(chunk,0,1,window(chunk))
 with store.db() as db:
  assert db.execute('SELECT lexical_evidence_bytes FROM sessions WHERE id=?',(sid,)).fetchone()[0]>33554432
  assert db.execute('SELECT lexical_evidence FROM segments').fetchone()[0] is not None

@pytest.mark.parametrize('limit',[0,-1,True,1.5,1024**3+1])
def test_invalid_quota_refuses(limit,tmp_path):
 with pytest.raises(ValueError,match='lexical_evidence_session_bytes'):
  Settings(data_dir=tmp_path,model_dir=tmp_path/'unused',lexical_evidence_session_bytes=limit)
