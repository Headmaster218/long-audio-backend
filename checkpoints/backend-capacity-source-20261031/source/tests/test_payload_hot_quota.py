"""Query/qualification tests only; synthetic SQL is not archive producer proof."""
from contextlib import contextmanager
from types import SimpleNamespace
import sqlite3
import pytest
from audio_backend import strict_payload_v2 as payload
from audio_backend import payload_hot_quota as quota


class Owner:
    def __init__(self,path):
        self.path=path;self._initializing=True;self._initializer_lock_owned=True
        self._strict_enabled=True;self._strict_policy=payload.POLICY
        self._strict_source_storage='shared-source-v1';self._strict_lifetime_records=32768;self._strict_receipt_records=8192
        self.settings=SimpleNamespace(strict_primary_enabled=True,strict_primary_policy=payload.POLICY,strict_payload_source_storage='shared-source-v1')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path);quota.bind(self,db)
        try:
            yield db;db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()


def seed(tmp_path,n,monkeypatch):
    monkeypatch.setattr(payload,'is_live_db',lambda db:True)
    owner=Owner(tmp_path/'test.sqlite3')
    with owner.db() as db:
        db.executescript('''CREATE TABLE speaker_support(id TEXT PRIMARY KEY,pcm BLOB,pcm_bytes INTEGER,manifest TEXT);
        CREATE TABLE speaker_support_vectors(id TEXT PRIMARY KEY,raw_vector BLOB,retained_bytes INTEGER,vector_sha256 TEXT);
        CREATE TABLE strict_payload_unit(unit_id TEXT PRIMARY KEY,state TEXT,bytes INTEGER);
        CREATE TABLE strict_payload_object(kind TEXT,owner_id TEXT,unit_id TEXT,bytes INTEGER,sha256 TEXT,offset INTEGER,PRIMARY KEY(kind,owner_id));
        INSERT INTO strict_payload_unit VALUES('unit','committed',1000);''')
        db.executemany('INSERT INTO speaker_support VALUES (?,NULL,32,?)',((str(i),'{"support":{"pooled_pcm_sha256":"hash"}}') for i in range(n)))
        db.executemany('INSERT INTO speaker_support_vectors VALUES (?,NULL,8,?)',((str(i),'hash') for i in range(n)))
        db.executemany('INSERT INTO strict_payload_object VALUES (?,?,?,?,?,10)',((kind,str(i),'unit',size,'hash') for kind,size in [('pcm',32),('vector',8)] for i in range(n)))
        for i in range(3):
            db.execute('INSERT INTO speaker_support VALUES (?,?,32,?)',('hot'+str(i),b'x'*32,'{}'))
            db.execute('INSERT INTO speaker_support_vectors VALUES (?,?,8,?)',('hot'+str(i),b'x'*8,'hash'))
        db.execute("INSERT INTO speaker_support VALUES ('empty',NULL,0,'{}')")
    return owner


@pytest.mark.parametrize('chunks',[337,4116,16458])
def test_large_cold_history_exact_bytes_and_bounded_vm(tmp_path,monkeypatch,chunks):
    owner=seed(tmp_path,chunks*2,monkeypatch)
    quota.qualify(owner)
    with owner.db() as db:
        assert payload._full_hot_bytes(db)==120
        steps=[0]
        def progress():steps[0]+=1;return 0
        db.set_progress_handler(progress,1)
        assert payload.hot_bytes(db)==120  # includes first schema guard admission
        db.set_progress_handler(None,0)
        assert steps[0]<5000,steps
        steps[0]=0;db.set_progress_handler(progress,1)
        assert payload.hot_bytes(db)==120
        db.set_progress_handler(None,0)
        assert steps[0]<1000,steps
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT INTO speaker_support VALUES ('new',?,32,'{}')",(b'x'*32,))
        assert payload.hot_bytes(db)==payload._full_hot_bytes(db)==152
        db.rollback()
        assert payload.hot_bytes(db)==120


@pytest.mark.parametrize('mutation',[
    "DELETE FROM strict_payload_object WHERE kind='pcm' AND owner_id='0'",
    "UPDATE strict_payload_object SET sha256='wrong' WHERE kind='pcm' AND owner_id='0'",
    "UPDATE strict_payload_object SET offset=999 WHERE kind='vector' AND owner_id='0'",
    "UPDATE strict_payload_unit SET state='published'",
])
def test_cold_corruption_rejected_at_qualification(tmp_path,monkeypatch,mutation):
    owner=seed(tmp_path,5,monkeypatch)
    with owner.db() as db:db.execute(mutation)
    with pytest.raises(payload.PayloadIntegrityError):quota.qualify(owner)
    assert getattr(owner,'_payload_hot_qualification',None) is None


def test_index_existence_not_authority_and_raw_connection_fullscan(tmp_path,monkeypatch):
    owner=seed(tmp_path,5,monkeypatch)
    with owner.db() as db:quota._indexes(db,create=True)
    with sqlite3.connect(owner.path) as db:
        db.execute("DELETE FROM strict_payload_object WHERE kind='pcm' AND owner_id='0'")
        with pytest.raises(payload.PayloadIntegrityError):payload.hot_bytes(db)
    with owner.db() as db:
        with pytest.raises(payload.PayloadIntegrityError):payload.hot_bytes(db)


@pytest.mark.parametrize('change',['policy','schema','replaced'])
def test_qualification_drift_refuses(tmp_path,monkeypatch,change):
    owner=seed(tmp_path,5,monkeypatch);quota.qualify(owner)
    with owner.db() as db:
        if change=='policy':owner.settings.strict_payload_source_storage='inline'
        elif change=='schema':db.execute('DROP INDEX payload_hot_pcm_v1')
        else:
            owner.path.rename(tmp_path/'old.sqlite3')
            with sqlite3.connect(owner.path) as replacement:replacement.execute('CREATE TABLE extra(x)')
        with pytest.raises(sqlite3.OperationalError):payload.hot_bytes(db)


def test_hot_contradiction_still_refused(tmp_path,monkeypatch):
    owner=seed(tmp_path,5,monkeypatch);quota.qualify(owner)
    with owner.db() as db:
        db.execute("INSERT INTO strict_payload_object VALUES ('pcm','hot0','unit',32,'hash',10)")
        with pytest.raises(payload.PayloadIntegrityError):payload.hot_bytes(db)


def test_qualification_requires_owned_initializer(tmp_path,monkeypatch):
    owner=seed(tmp_path,5,monkeypatch);owner._initializing=False
    with pytest.raises(ValueError,match='owned_initialization'):quota.qualify(owner)


def test_unrelated_lazy_schema_does_not_change_original_proof(tmp_path,monkeypatch):
    owner=seed(tmp_path,5,monkeypatch);quota.qualify(owner)
    proof=owner._payload_hot_qualification
    with owner.db() as db:
        db.execute('CREATE TABLE unrelated_lazy_worker_table(x)')
        assert payload.hot_bytes(db)==120
        assert owner._payload_hot_qualification is proof
