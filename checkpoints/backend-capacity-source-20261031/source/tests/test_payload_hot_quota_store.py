"""Real Store admission/guard tests on generated databases, no models or workers."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from audio_backend.storage import Store
from audio_backend import strict_payload_v2 as payload, payload_hot_quota as quota
from test_strict_payload_v2 import configuration
from test_strict_archive_v2 import positive_close, seed_source


def make(tmp_path):
    cfg=configuration(tmp_path)
    store=Store(tmp_path/'jobs.sqlite3',cfg)
    source=seed_source(store,tmp_path/'inputs')
    ident='a'*32;pcm=b'abcd';sha=hashlib.sha256(pcm).hexdigest()
    with store.db() as db:
        db.execute('''INSERT INTO speaker_support(id,session_id,chunk_id,identity_group,generation,window_start,
          local_speaker_id,manifest,manifest_sha256,status,pcm,pcm_bytes)
          VALUES(?,?,?,'default','g',0,'0',?,?,'unavailable',?,4)''',
          (ident,source.sid,source.cid,json.dumps({'support':{'pooled_pcm_sha256':sha}}),'b'*64,pcm))
    return store,cfg,ident,sha


def test_real_store_hot_all_statuses_raw_fallback_and_reopen(tmp_path):
    store,cfg,ident,sha=make(tmp_path)
    with store.db() as db:
        assert db.execute('SELECT payload_hot_qualified_v1()').fetchone()[0]==1
        assert payload.hot_bytes(db)==payload._full_hot_bytes(db)==4
    with sqlite3.connect(store.path) as raw:
        assert payload.hot_bytes(raw)==4
    positive_close(store)
    again=Store(store.path,cfg)
    with again.db() as db:assert payload.hot_bytes(db)==4
    positive_close(again)


def test_hot_null_guards_and_rollback(tmp_path):
    store,cfg,ident,sha=make(tmp_path)
    with pytest.raises(sqlite3.IntegrityError,match='strict_payload_support_immutable'):
        with store.db() as db:db.execute('UPDATE speaker_support SET pcm=NULL WHERE id=?',(ident,))
    with pytest.raises(sqlite3.IntegrityError,match='Fresh acoustic support'):
        with store.db() as db:
            db.execute("INSERT INTO speaker_support SELECT ?,session_id,chunk_id,identity_group,generation,1,local_speaker_id,observation_id,manifest,manifest_sha256,status,NULL,4 FROM speaker_support WHERE id=?",('c'*32,ident))
    with pytest.raises(RuntimeError,match='synthetic rollback'):
        with store.db() as db:
            db.execute("INSERT INTO speaker_support SELECT ?,session_id,chunk_id,identity_group,generation,1,local_speaker_id,observation_id,manifest,manifest_sha256,status,pcm,pcm_bytes FROM speaker_support WHERE id=?",('c'*32,ident))
            assert payload.hot_bytes(db)==8
            raise RuntimeError('synthetic rollback')
    with store.db() as db:
        assert payload.hot_bytes(db)==4
        db.execute("INSERT INTO speaker_support SELECT ?,session_id,chunk_id,identity_group,generation,1,local_speaker_id,observation_id,manifest,manifest_sha256,status,NULL,0 FROM speaker_support WHERE id=?",('d'*32,ident))
        assert payload.hot_bytes(db)==payload._full_hot_bytes(db)==4
    positive_close(store)


@pytest.mark.parametrize('damage',['missing','sha','unit_state'])
def test_offline_cold_corruption_is_refused_on_real_store_reopen(tmp_path,damage):
    store,cfg,ident,sha=make(tmp_path);positive_close(store)
    # Deliberately corrupt only this generated fixture, then restore exact DDL.
    # This is not a supported application mutation or a repair procedure.
    with sqlite3.connect(store.path) as raw:
        update=raw.execute("SELECT sql FROM sqlite_master WHERE name='strict_payload_support_update'").fetchone()[0]
        raw.execute('DROP TRIGGER strict_payload_support_update')
        if damage!='missing':
            raw.execute('''INSERT INTO strict_payload_unit(sequence,unit_id,cid,store_key,evidence_sha256,
              manifest_bytes,bucket,state,manifest_sha256,bytes,file_sha256,signature)
              VALUES(1,?,?,?, ?,1,0,?,?,100,?,'[]')''',
              ('e'*64,'f'*32,'1'*64,'2'*64,'published' if damage=='unit_state' else 'committed','e'*64,'3'*64))
            insert=raw.execute("SELECT sql FROM sqlite_master WHERE name='strict_payload_object_insert'").fetchone()[0]
            raw.execute('DROP TRIGGER strict_payload_object_insert')
            raw.execute("INSERT INTO strict_payload_object VALUES('pcm',?,?,'pcm',4,?,10)",(ident,'e'*64,'4'*64 if damage=='sha' else sha))
            raw.execute(insert)
        raw.execute('UPDATE speaker_support SET pcm=NULL WHERE id=?',(ident,));raw.execute(update)
    with pytest.raises(payload.PayloadIntegrityError,match='payload_pcm_quota_integrity'):
        Store(store.path,cfg)


def test_qualification_policy_change_and_wrong_store_refused(tmp_path):
    store,cfg,ident,sha=make(tmp_path)
    original=cfg.strict_payload_source_storage;cfg.strict_payload_source_storage='shared-source-v1'
    try:
        with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
            with store.db() as db:payload.hot_bytes(db)
    finally:cfg.strict_payload_source_storage=original
    with pytest.raises(ValueError,match='owned_initialization'):
        quota.qualify(store)
    proof=store._payload_hot_qualification
    from dataclasses import replace
    store._payload_hot_qualification=replace(proof,owner=object())
    try:
        with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
            with store.db() as db:payload.hot_bytes(db)
    finally:store._payload_hot_qualification=proof
    positive_close(store)


def test_replaced_database_inode_refuses_existing_qualification(tmp_path):
    store,cfg,ident,sha=make(tmp_path);positive_close(store)
    replacement=tmp_path/'replacement.sqlite3'
    with sqlite3.connect(store.path) as source,sqlite3.connect(replacement) as target:source.backup(target)
    store.path.rename(tmp_path/'old.sqlite3');replacement.rename(store.path)
    with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
        with store.db() as db:payload.hot_bytes(db)
    positive_close(store)
    reopened=Store(store.path,cfg)
    with reopened.db() as db:assert payload.hot_bytes(db)==4
    positive_close(reopened)


def test_vector_guards_and_cross_database_connection_binding(tmp_path):
    store,cfg,ident,sha=make(tmp_path)
    with store.db() as db:
        db.execute('''INSERT INTO speaker_support_vectors(id,support_id,identity_group,generation,
          raw_vector,vector_sha256,dimension,space_id,space,normalization,normalized_sha256,evidence_key,retained_bytes)
          VALUES(?,?,'default','g',?, ?,1,?,'{}','synthetic',?,?,4)''',
          ('c'*32,ident,b'abcd',sha,'d'*64,'e'*64,'f'*64))
        assert payload.hot_bytes(db)==8
    with pytest.raises(sqlite3.IntegrityError,match='strict_payload_vector_immutable'):
        with store.db() as db:db.execute('UPDATE speaker_support_vectors SET raw_vector=NULL')
    with sqlite3.connect(tmp_path/'different.sqlite3') as wrong:
        quota.bind(store,wrong)
        with pytest.raises(sqlite3.OperationalError,match='user-defined function'):
            wrong.execute('SELECT payload_hot_qualified_v1()').fetchone()
    with store.db() as db:assert payload.hot_bytes(db)==8
    positive_close(store)
