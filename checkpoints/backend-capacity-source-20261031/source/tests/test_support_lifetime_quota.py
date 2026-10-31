"""Metadata-only actual commit boundary; no PCM/model or memory qualification."""
import sqlite3
from types import SimpleNamespace
import pytest
from audio_backend import speaker_support as support
from audio_backend.strict_payload_v2 import PayloadCapacityError


def database(count):
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE speaker_support(id TEXT PRIMARY KEY,session_id TEXT,chunk_id TEXT,identity_group TEXT,generation INTEGER,window_start REAL,local_speaker_id TEXT,observation_id TEXT,manifest TEXT,manifest_sha256 TEXT,status TEXT,pcm BLOB,pcm_bytes INTEGER)')
    db.executemany('INSERT INTO speaker_support VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ((str(i),'s','c','g',0,0,'a',None,'{}','0'*64,'available',None,0) for i in range(count)))
    return db


@pytest.mark.parametrize('count,limit',[(4096,4097),(65535,65536)])
def test_retained_rows_cross_default_then_stop_exactly(monkeypatch,count,limit):
    monkeypatch.setattr(support,'generation',lambda db,group:0)
    store=SimpleNamespace(settings=SimpleNamespace(speaker_support_enabled=True,speaker_support_max_records=1),
        _speaker_support_max_records=limit,_strict_enabled=True,_strict_policy='strict-primary-payload-v2')
    with database(count) as db:
        ident,status=support.commit_support(store,db,{'id':'c','session_id':'s'},0,'g','new',None,{'generation':0,'status':'missing_lineage'})
        assert ident and status=='missing_lineage'
        assert db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0]==limit
        store.settings.speaker_support_max_records=262144
        with pytest.raises(PayloadCapacityError,match='support_row_quota'):
            support.commit_support(store,db,{'id':'c','session_id':'s'},0,'g','other',None,None)


def test_legacy_default_refusal_unchanged():
    store=SimpleNamespace(settings=SimpleNamespace(speaker_support_enabled=True,speaker_support_max_records=4096))
    with database(4096) as db:
        assert support.commit_support(store,db,{},0,'g','a',None,None)==(None,'support_row_quota')


def test_late_exact_count_uses_constant_vm_not_history_scan():
    with database(131664) as db:
        calls=[]
        db.set_progress_handler(lambda:calls.append(1) or 0,1)
        assert db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0]==131664
        db.set_progress_handler(None,0)
        assert len(calls)<100


def test_actual_store_freezes_operational_cap_and_reopen_is_explicit(tmp_path):
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0,
        speaker_support_max_records=65536)
    store=Store(tmp_path/'jobs.sqlite3',settings)
    settings.speaker_support_max_records=262144
    assert store._speaker_support_max_records==65536
    reopened=Store(store.path,settings)
    assert reopened._speaker_support_max_records==262144


@pytest.mark.parametrize('value',[True,-1,262145,1.5])
def test_invalid_support_cap_refused_before_store_creation(tmp_path,value):
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    settings.speaker_support_max_records=value
    with pytest.raises(ValueError):Store(tmp_path/'jobs.sqlite3',settings)
    assert not (tmp_path/'jobs.sqlite3').exists()
