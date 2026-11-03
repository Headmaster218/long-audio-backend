"""Real SQLite transaction placement, no native service activation."""
from pathlib import Path
import sqlite3
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_session_requests_candidate import server
from audio_backend import strict_recording,session_requests


def test_strict_and_feature_schema_share_one_transaction(tmp_path,monkeypatch):
    trace=[];connections=[]
    original_strict=strict_recording.init_schema
    original_feature=session_requests.initialize
    def strict(db,*args,**kwargs):
        assert db.in_transaction
        connections.append(db);db.set_trace_callback(trace.append)
        return original_strict(db,*args,**kwargs)
    def feature(db):
        assert db is connections[0] and db.in_transaction
        assert not any(x.strip().upper() in ('COMMIT','ROLLBACK') for x in trace)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='strict_recording_policy'").fetchone()
        return original_feature(db)
    monkeypatch.setattr(strict_recording,'init_schema',strict)
    monkeypatch.setattr(session_requests,'initialize',feature)
    with server(tmp_path/'store'):pass
    assert sum(x.strip().upper()=='COMMIT' for x in trace)==1
    assert not any(x.strip().upper()=='ROLLBACK' for x in trace)


@pytest.mark.parametrize('after_feature',[False,True])
def test_feature_initialization_fault_rolls_back_both_namespaces(tmp_path,monkeypatch,after_feature):
    root=tmp_path/'store';original=session_requests.initialize
    def failure(db):
        assert db.in_transaction
        if after_feature:original(db)
        raise RuntimeError('controlled_feature_initializer_failure')
    with monkeypatch.context() as patch:
        patch.setattr(session_requests,'initialize',failure)
        with pytest.raises(RuntimeError,match='controlled_feature_initializer_failure'):
            with server(root):pass
    db=sqlite3.connect((root/'jobs.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        names={r[0] for r in db.execute('SELECT name FROM sqlite_master')}
        assert 'sessions' in names  # Earlier original base phase is still durable.
        assert not any(n.startswith(('strict_','session_requests_')) for n in names)
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    finally:db.close()
    # Existing strict gate remains closed; this is not whole-Store recovery.
    with pytest.raises(ValueError,match='strict_archive_fresh_only'):
        with server(root):pass
