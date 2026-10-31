"""Fresh finite metadata admission; generated SQLite, no models or long-run claim."""
import sqlite3
from types import SimpleNamespace
import pytest
from audio_backend import strict_lifetime as quota, strict_recording as strict


def database(records=2056,receipts=4096):
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE sessions(id TEXT,deleted INTEGER,timeline_end INTEGER,processing_mode TEXT)')
    db.execute('CREATE TABLE chunks(id TEXT,status TEXT,session_id TEXT,digest TEXT,path TEXT,duration REAL,timeline_start REAL,next_offset REAL)')
    db.execute('CREATE TABLE native_core_commits(slot TEXT,cid TEXT)')
    strict.init_schema(db,lifetime_records=records,receipt_records=receipts)
    return db


@pytest.mark.parametrize('bad',[True,False,None,0,-1,131073,1.0,'32768'])
def test_invalid_core_quota(bad):
    with pytest.raises(ValueError):quota.validate(bad)


@pytest.mark.parametrize('bad',[True,None,0,-1,20001,4096.0,'8192'])
def test_invalid_receipt_quota(bad):
    with pytest.raises(ValueError):quota.validate_receipts(bad)


def test_default_schema_and_limits_unchanged():
    with database() as db:
        assert quota.read_limit(db)==2056 and quota.read_receipt_limit(db)==4096
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'strict_lifetime_%'").fetchone()[0]==0
        assert '>=2056' in db.execute("SELECT sql FROM sqlite_master WHERE name='strict_outcome_admit'").fetchone()[0]
        strict.admit_schema(db)
        with pytest.raises(ValueError):strict.admit_schema(db,lifetime_records=32768)


@pytest.mark.parametrize('records,receipts',[(32768,8192),(131072,20000),(2056,8192),(32768,4096)])
def test_fresh_persisted_exact_profile(records,receipts):
    with database(records,receipts) as db:
        assert (quota.read_limit(db),quota.read_receipt_limit(db))==(records,receipts)
        strict.init_schema(db,lifetime_records=records,receipt_records=receipts)
        for other in [(2056,4096),(records+1 if records<131072 else 32768,receipts),(records,receipts-1)]:
            with pytest.raises(ValueError):strict.admit_schema(db,lifetime_records=other[0],receipt_records=other[1])
        for sql in ['UPDATE strict_lifetime_policy SET records=records','DELETE FROM strict_lifetime_policy']:
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)


def test_legacy_is_not_migrated_and_quota_crosses_old_boundary():
    with database() as old:
        before=old.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()
        with pytest.raises(ValueError):strict.init_schema(old,lifetime_records=32768,receipt_records=8192)
        assert old.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()==before
    with database(2057) as db:
        for i in range(2057):strict.add_control(db,f'{i:032x}','s','a'*64,10.5)
        with pytest.raises(OverflowError,match='strict_control_capacity'):
            strict.add_control(db,'f'*32,'s','a'*64,10.5)
        assert db.execute('SELECT COUNT(*) FROM strict_recording_control').fetchone()[0]==2057
        with pytest.raises(sqlite3.IntegrityError,match='retained'):db.execute('DELETE FROM strict_recording_control')


def test_authorization_and_outcome_count_limit_both_enforced():
    with database(1) as db:
        db.execute("INSERT INTO sessions VALUES('s',0,0,'legacy')")
        strict.new_policy(db,'s',SimpleNamespace(max_sessions=1,strict_primary_policy=strict.POLICY))
        db.execute("INSERT INTO chunks VALUES('c','queued','s',?,'unused',2,0,0)",('a'*64,))
        strict.add_control(db,'c','s','a'*64,2)
        db.execute("UPDATE strict_recording_control SET state='active',nonce=? WHERE cid='c'",('b'*32,))
        db.execute("UPDATE chunks SET status='running' WHERE id='c'")
        db.execute("INSERT INTO native_core_commits VALUES(?,'c')",('d'*64,))
        args=('c',0,16000,'b'*32,'a'*64,'d'*64,'empty',None)
        with pytest.raises(sqlite3.IntegrityError,match='authorization'):
            db.execute('INSERT INTO strict_recording_outcome VALUES(?,?,?,?,?,?,?,?)',(*args[:3],'e'*32,*args[4:]))
        db.execute('INSERT INTO strict_recording_outcome VALUES(?,?,?,?,?,?,?,?)',args)
        db.execute("UPDATE chunks SET next_offset=1 WHERE id='c'")
        with pytest.raises(sqlite3.IntegrityError,match='strict_outcome_capacity'):
            db.execute('INSERT INTO strict_recording_outcome VALUES(?,?,?,?,?,?,?,?)',('c',16000,32000,*args[3:]))


def test_missing_policy_row_is_not_default_adoption():
    db=sqlite3.connect(':memory:')
    for sql in quota.schema_statements(32768,8192):db.execute(sql)
    with pytest.raises(ValueError,match='missing'):quota.read_limit(db)


def test_real_store_freezes_and_reopens_exact_configuration(tmp_path):
    from audio_backend.storage import Store
    from test_recording_primary_provenance import settings
    cfg=settings(tmp_path);cfg.strict_primary_enabled=True
    cfg.strict_primary_lifetime_records=32768;cfg.strict_primary_receipt_records=8192
    path=tmp_path/'fresh.sqlite'
    store=Store(path,cfg)
    assert store._strict_lifetime_records==32768
    assert store._strict_receipt_records==8192
    # Caller mutation cannot rewrite initialized schema or alter frozen admission.
    cfg.strict_primary_lifetime_records=131072;cfg.strict_primary_receipt_records=20000
    with store.db() as db:assert (quota.read_limit(db),quota.read_receipt_limit(db))==(32768,8192)
    with pytest.raises(ValueError):Store(path,cfg)
    cfg.strict_primary_lifetime_records=32768;cfg.strict_primary_receipt_records=8192
    reopened=Store(path,cfg)
    with reopened.db() as db:strict.admit_schema(db,lifetime_records=32768,receipt_records=8192)


def test_invalid_store_quota_does_not_hold_initializer_lock(tmp_path):
    from audio_backend.storage import Store
    from test_recording_primary_provenance import settings
    cfg=settings(tmp_path);cfg.strict_primary_enabled=True;cfg.strict_primary_lifetime_records=True
    with pytest.raises(ValueError):Store(tmp_path/'bad.sqlite',cfg)
    cfg.strict_primary_lifetime_records=2056
    Store(tmp_path/'good.sqlite',cfg)


def test_payload_bucket_crosses_old_lifetime_boundary_with_original_io(tmp_path):
    from audio_backend import strict_payload_v2 as payload
    from audio_backend.storage import Store
    from test_strict_payload_v2 import configuration
    cfg=configuration(tmp_path);cfg.strict_primary_lifetime_records=32768
    cfg.strict_primary_receipt_records=8192
    store=Store(tmp_path/'payload.sqlite',cfg)
    with payload._operation(store,payload.PayloadBudget()) as owner:
        path,fd=payload._bucket(owner,129,create=True)
        assert path.name=='00000081'
        payload._close(owner,fd)
    with payload._operation(store,payload.PayloadBudget()) as owner:
        path,fd=payload._bucket(owner,(32768-1)//16,create=True)
        payload._close(owner,fd)
    with pytest.raises(payload.PayloadIntegrityError,match='payload_bucket_index'):
        with payload._operation(store,payload.PayloadBudget()) as owner:
            payload._bucket(owner,32768//16,create=True)
    assert store._strict_archive_owner is None and not store.strict_cleanup_uncertain()
