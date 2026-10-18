"""Opt-in immutable observation history and replaceable, revision-bound views.

This module never opens a model or audio crop. A saved packet is not admission:
only the registered live manager's positively cleaned fresh operation can append
certificates. Unknown acknowledgements resolve storage ownership only.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
import os
import sqlite3
import struct
import threading
import time
import uuid

from .moss_result_store import MossResultStore, encoded
from .native_owned_support_cache import ReadError

POLICY = 'primary-clean-history-v1'
POLICY_SHA256 = hashlib.sha256(encoded({'schema': POLICY, 'score': 'original-float32-fsum-float64',
    'linkage': 'complete-all-history', 'threshold': .55, 'margin': .10,
    'missing_runner_up': 'abstain', 'aliases': 'source-or-pcm',
    'source_overlap': 'rank-all-then-veto-trio'})).hexdigest()
BOOT_ID = uuid.uuid4().hex
MAX_REVISION = (1 << 63) - 1
RECORDING_BYTES, GLOBAL_BYTES = 64 * 1024**2, 128 * 1024**2
STAGING_BYTES, PROBE_STAGING_BYTES = 8 * 1024**2, 512 * 1024
SCOPE_CHARGE, ATTEMPT_CHARGE, BASIS_CHARGE = 32768, 1024, 1024
PAGE_ROWS, PAGE_BYTES = 32, 128 * 1024
COMPARE_BYTES, COMPARE_SECONDS = 128 * 1024**2, 180.
SCOPES = 'recording_history_scopes_v1'
ATTEMPTS = 'recording_history_attempts_v1'
CERTIFICATES = 'recording_history_certificates_v1'
OBJECTS = 'recording_history_objects_v1'
REFS = 'recording_history_refs_v1'
DEPENDENCIES = 'recording_history_dependencies_v1'
SOURCES = 'recording_history_sources_v1'
DECISIONS = 'recording_history_decisions_v1'
ROWS = 'recording_history_decision_rows_v1'
_REASONS = ('none','busy','missed','capacity','source_changed','cancelled','proof_missing',
    'cleanup_unknown','storage_unknown','probe_failed','pending','deadline','resource_pending','other')
_STATE_SQL = ','.join(repr(x) for x in _REASONS)
_INT = "CHECK(typeof({0})='integer' AND {0} BETWEEN 0 AND 9223372036854775807)"

DDL = {
SCOPES: f'''CREATE TABLE {SCOPES} (
    scope_seq INTEGER PRIMARY KEY CHECK(scope_seq>=0), scope_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL, recording_id TEXT NOT NULL, policy TEXT NOT NULL,
    space_id TEXT NOT NULL, space_json BLOB NOT NULL CHECK(typeof(space_json)='blob' AND length(space_json)<=16384),
    interval_frames INTEGER NOT NULL CHECK(interval_frames=960000),
    retained_bytes INTEGER NOT NULL DEFAULT 0 {_INT.format('retained_bytes')},
    reserved_bytes INTEGER NOT NULL DEFAULT 0 {_INT.format('reserved_bytes')},
    staging_bytes INTEGER NOT NULL DEFAULT 0 CHECK(staging_bytes BETWEEN 0 AND 8388608),
    history_revision INTEGER NOT NULL DEFAULT 0 {_INT.format('history_revision')},
    authority_revision INTEGER NOT NULL DEFAULT 0 {_INT.format('authority_revision')},
    certificate_count INTEGER NOT NULL DEFAULT 0 {_INT.format('certificate_count')},
    witness_count INTEGER NOT NULL DEFAULT 0 {_INT.format('witness_count')},
    authority_blocked TEXT CHECK(authority_blocked IN ({_STATE_SQL})),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ({_STATE_SQL})),
    high_water INTEGER NOT NULL DEFAULT -1 CHECK(high_water>=-1),
    bootstrap_count INTEGER NOT NULL DEFAULT 0 CHECK(bootstrap_count BETWEEN 0 AND 6),
    last_claim_frame INTEGER NOT NULL DEFAULT -160000 CHECK(last_claim_frame>=-160000),
    last_bucket INTEGER NOT NULL DEFAULT -1 CHECK(last_bucket>=-1),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count BETWEEN 0 AND 2147483647),
    current_slot INTEGER CHECK(current_slot IN (0,1)), building_slot INTEGER CHECK(building_slot IN (0,1)),
    build_token TEXT CHECK(build_token IS NULL OR length(build_token)=32),
    compare_cursor INTEGER NOT NULL DEFAULT 0 CHECK(compare_cursor>=0),
    UNIQUE(session_id,recording_id),
    CHECK((scope_seq=0 AND scope_id='' AND session_id='' AND recording_id='' AND policy='') OR
        (scope_seq>0 AND length(scope_id)=64 AND length(session_id) BETWEEN 1 AND 64
         AND length(recording_id) BETWEEN 1 AND 64 AND length(space_id) BETWEEN 1 AND 256 AND policy='{POLICY}')),
    CHECK((building_slot IS NULL AND build_token IS NULL) OR (building_slot IS NOT NULL AND build_token IS NOT NULL)))''',
ATTEMPTS: f'''CREATE TABLE {ATTEMPTS} (
    token TEXT PRIMARY KEY CHECK(length(token)=32), scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq),
    origin_kind TEXT NOT NULL CHECK(origin_kind IN ('primary_commit','moss_result')),
    origin_id TEXT NOT NULL CHECK(length(origin_id) BETWEEN 1 AND 256), identity_sha256 TEXT NOT NULL CHECK(length(identity_sha256)=64),
    owner TEXT NOT NULL CHECK(length(owner)=32), state TEXT NOT NULL CHECK(state IN ('reserved','success','failure')),
    authority_revision INTEGER NOT NULL CHECK(authority_revision>=0),
    reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes BETWEEN 0 AND 1204736),
    staging_bytes INTEGER NOT NULL CHECK(staging_bytes BETWEEN 0 AND 524288),
    charge INTEGER NOT NULL CHECK(charge>=1024),
    basis BLOB CHECK(basis IS NULL OR (typeof(basis)='blob' AND length(basis) BETWEEN 1 AND 512)),
    terminal BLOB CHECK(terminal IS NULL OR (typeof(terminal)='blob' AND length(terminal) BETWEEN 1 AND 512)),
    terminal_sha256 TEXT CHECK(terminal_sha256 IS NULL OR length(terminal_sha256)=64),
    UNIQUE(scope_seq,origin_kind,origin_id),
    CHECK((state='reserved' AND basis IS NULL AND terminal IS NULL AND terminal_sha256 IS NULL) OR
      (state='success' AND basis IS NOT NULL AND terminal IS NOT NULL AND terminal_sha256 IS NOT NULL AND reserved_bytes=0 AND staging_bytes=0) OR
      (state='failure' AND basis IS NULL AND terminal IS NOT NULL AND terminal_sha256 IS NOT NULL AND reserved_bytes=0 AND staging_bytes=0))) WITHOUT ROWID''',
CERTIFICATES: f'''CREATE TABLE {CERTIFICATES} (
    scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq), sequence INTEGER NOT NULL CHECK(sequence>0),
    attempt TEXT NOT NULL REFERENCES {ATTEMPTS}(token), certificate_id TEXT NOT NULL UNIQUE CHECK(length(certificate_id)=64),
    header BLOB NOT NULL CHECK(typeof(header)='blob' AND length(header) BETWEEN 1 AND 2048),
    vector BLOB NOT NULL CHECK(typeof(vector)='blob' AND length(vector)=2048),
    vector_sha256 TEXT NOT NULL CHECK(length(vector_sha256)=64),
    charge INTEGER NOT NULL CHECK(charge=length(header)+3072),
    PRIMARY KEY(scope_seq,sequence)) WITHOUT ROWID''',
OBJECTS: f'''CREATE TABLE {OBJECTS} (
    scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq), kind TEXT NOT NULL CHECK(kind IN ('receipt','proof')),
    digest TEXT NOT NULL CHECK(length(digest)=64), body BLOB NOT NULL CHECK(typeof(body)='blob'),
    refcount INTEGER NOT NULL CHECK(typeof(refcount)='integer' AND refcount BETWEEN 1 AND 9223372036854775807),
    charge INTEGER NOT NULL,
    PRIMARY KEY(scope_seq,kind,digest),
    CHECK((kind='receipt' AND length(body) BETWEEN 1 AND 1024 AND charge=length(body)+256) OR
          (kind='proof' AND length(body) BETWEEN 1 AND 524288 AND charge=length(body)+512))) WITHOUT ROWID''',
REFS: f'''CREATE TABLE {REFS} (
    attempt TEXT NOT NULL REFERENCES {ATTEMPTS}(token), scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq),
    kind TEXT NOT NULL CHECK(kind IN ('receipt','proof','packet')),
    object_key TEXT NOT NULL CHECK(length(object_key) BETWEEN 1 AND 192), digest TEXT NOT NULL CHECK(length(digest)=64),
    charge INTEGER NOT NULL CHECK(charge=CASE WHEN kind='packet' THEN 512 WHEN kind='receipt' THEN 256 ELSE 0 END),
    PRIMARY KEY(attempt,kind,object_key)) WITHOUT ROWID''',
DEPENDENCIES: f'''CREATE TABLE {DEPENDENCIES} (
    scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq), kind TEXT NOT NULL CHECK(kind IN ('source','mapping','primary')),
    dependency_key TEXT NOT NULL CHECK(length(dependency_key) BETWEEN 1 AND 192),
    attempt TEXT NOT NULL REFERENCES {ATTEMPTS}(token), fact BLOB NOT NULL CHECK(typeof(fact)='blob' AND length(fact) BETWEEN 1 AND 256),
    charge INTEGER NOT NULL CHECK(charge=512),
    PRIMARY KEY(scope_seq,kind,dependency_key,attempt)) WITHOUT ROWID''',
SOURCES: f'''CREATE TABLE {SOURCES} (
    scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq), version TEXT NOT NULL CHECK(length(version)=64),
    chunk_id TEXT NOT NULL CHECK(length(chunk_id) BETWEEN 1 AND 64), receipt_digest TEXT NOT NULL CHECK(length(receipt_digest)=64),
    path_digest TEXT NOT NULL CHECK(length(path_digest)=64), mapping_digest TEXT NOT NULL CHECK(length(mapping_digest)=64),
    state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','retired','revoked')),
    admission_revision INTEGER NOT NULL DEFAULT 0 {_INT.format('admission_revision')},
    certificate_count INTEGER NOT NULL DEFAULT 0 {_INT.format('certificate_count')},
    required_end INTEGER NOT NULL CHECK(required_end>=0),
    marker BLOB CHECK(marker IS NULL OR (typeof(marker)='blob' AND length(marker) BETWEEN 1 AND 1024)),
    marker_token TEXT UNIQUE CHECK(marker_token IS NULL OR length(marker_token)=32),
    cleanup TEXT CHECK(cleanup IN ('pending','done','failed','unknown')),
    cleanup_attempts INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_attempts BETWEEN 0 AND 2147483647),
    cleanup_reason TEXT CHECK(cleanup_reason IN ('io_unavailable','path_changed','storage_unknown','other')),
    charge INTEGER NOT NULL DEFAULT 512 CHECK(charge=512+CASE WHEN marker IS NULL THEN 0 ELSE length(marker)+512 END),
    PRIMARY KEY(scope_seq,version),
    CHECK((marker IS NULL AND marker_token IS NULL AND cleanup IS NULL) OR
          (marker IS NOT NULL AND marker_token IS NOT NULL AND cleanup IS NOT NULL AND state IN ('retired','revoked')))) WITHOUT ROWID''',
DECISIONS: f'''CREATE TABLE {DECISIONS} (
    scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq), slot INTEGER NOT NULL CHECK(slot IN (0,1)),
    build_token TEXT NOT NULL UNIQUE CHECK(length(build_token)=32),
    history_revision INTEGER NOT NULL CHECK(history_revision>=0), authority_revision INTEGER NOT NULL CHECK(authority_revision>=0),
    certificate_count INTEGER NOT NULL CHECK(certificate_count>=0), witness_count INTEGER NOT NULL CHECK(witness_count>=0),
    header BLOB NOT NULL CHECK(typeof(header)='blob' AND length(header) BETWEEN 1 AND 65536),
    complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)),
    row_count INTEGER NOT NULL DEFAULT 0 CHECK(row_count>=0), charge INTEGER NOT NULL CHECK(charge>=1024),
    PRIMARY KEY(scope_seq,slot)) WITHOUT ROWID''',
ROWS: f'''CREATE TABLE {ROWS} (
    scope_seq INTEGER NOT NULL, slot INTEGER NOT NULL CHECK(slot IN (0,1)),
    ordinal INTEGER NOT NULL CHECK(ordinal>0), kind TEXT NOT NULL CHECK(kind IN ('annotation','merge','round')),
    ref_kind TEXT CHECK(ref_kind IN ('primary','moss')), ref_key TEXT CHECK(ref_key IS NULL OR length(ref_key) BETWEEN 1 AND 192),
    body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body) BETWEEN 1 AND CASE WHEN kind='merge' THEN 128 ELSE 512 END),
    charge INTEGER NOT NULL CHECK(charge=length(body)+256),
    PRIMARY KEY(scope_seq,slot,ordinal), FOREIGN KEY(scope_seq,slot) REFERENCES {DECISIONS}(scope_seq,slot),
    CHECK((kind='annotation' AND ref_kind IS NOT NULL AND ref_key IS NOT NULL) OR
          (kind!='annotation' AND ref_kind IS NULL AND ref_key IS NULL))) WITHOUT ROWID''',
'recording_history_session_v1': f'CREATE INDEX recording_history_session_v1 ON {SCOPES}(session_id,scope_seq)',
'recording_history_dirty_v1': f"CREATE INDEX recording_history_dirty_v1 ON {SCOPES}(scope_seq) WHERE scope_seq>0 AND authority_blocked IS NULL AND state!='none'",
'recording_history_attempt_scope_v1': f'CREATE INDEX recording_history_attempt_scope_v1 ON {ATTEMPTS}(scope_seq,state,token)',
'recording_history_certificate_attempt_v1': f'CREATE INDEX recording_history_certificate_attempt_v1 ON {CERTIFICATES}(scope_seq,attempt,sequence)',
'recording_history_ref_object_v1': f'CREATE INDEX recording_history_ref_object_v1 ON {REFS}(scope_seq,kind,digest)',
'recording_history_dependency_fact_v1': f'CREATE INDEX recording_history_dependency_fact_v1 ON {DEPENDENCIES}(kind,dependency_key,scope_seq,attempt)',
'recording_history_dependency_attempt_v1': f'CREATE INDEX recording_history_dependency_attempt_v1 ON {DEPENDENCIES}(attempt,kind,dependency_key)',
'recording_history_source_chunk_v1': f'CREATE INDEX recording_history_source_chunk_v1 ON {SOURCES}(chunk_id,scope_seq,version)',
'recording_history_exact_ref_v1': f'CREATE INDEX recording_history_exact_ref_v1 ON {ROWS}(scope_seq,slot,ref_kind,ref_key,ordinal)',
}


def _fence_sql(where, reason='source_changed'):
    return f'''UPDATE {SCOPES} SET authority_revision=CASE WHEN authority_revision<{MAX_REVISION}
        THEN authority_revision+1 ELSE RAISE(ROLLBACK,'History authority exhausted') END,
        authority_blocked='{reason}',state='{reason}',current_slot=NULL WHERE scope_seq>0 AND ({where});
        UPDATE {SOURCES} SET state='revoked' WHERE scope_seq IN
            (SELECT scope_seq FROM {SCOPES} WHERE scope_seq>0 AND ({where}));'''


def _dependency_scope(kind, key):
    return f"scope_seq IN (SELECT scope_seq FROM {DEPENDENCIES} INDEXED BY recording_history_dependency_fact_v1 WHERE kind='{kind}' AND dependency_key={key})"


# Exact source facts are dependencies, never broad transcript change hints.
for table, key, columns, predicate, depkind in (
    ('chunks','id',('session_id','digest','path','duration','timeline_start'),None,'source'),
    ('stream_chunks','chunk_id',('session_id','stream_id','position'),None,'mapping'),
    ('recording_work','chunk_id',('batch_id','position','frames','capture_start_frame','native_receipt','receipt_id','file_signature'),None,'mapping'),
    ('segments','id',('chunk_id','start','end','text','raw_text','asr_backend','timestamp_kind','speaker_local_id','speaker_window_start'),None,'primary')):
    different=' OR '.join(f'OLD.{c} IS NOT NEW.{c}' for c in columns)
    lookup=f"CAST(OLD.{key} AS TEXT)"
    for event in ('UPDATE','DELETE'):
        name=f'recording_history_{table}_{event.lower()}_v1'
        changed=f" WHEN {different}" if event=='UPDATE' else ''
        DDL[name]=f'''CREATE TRIGGER {name} AFTER {event}{' OF '+','.join(columns) if event=='UPDATE' else ''} ON {table}{changed}
            BEGIN {_fence_sql(_dependency_scope(depkind,lookup))} END'''
DDL['recording_history_progress_v1']=f'''CREATE TRIGGER recording_history_progress_v1 AFTER UPDATE OF next_offset,status ON chunks
    WHEN NEW.status='cancelled' OR NEW.next_offset<OLD.next_offset BEGIN
    {_fence_sql(f"scope_seq IN (SELECT scope_seq FROM {SOURCES} INDEXED BY recording_history_source_chunk_v1 WHERE chunk_id=OLD.id AND (NEW.status='cancelled' OR NEW.next_offset*16000<required_end))")} END'''
for event in ('UPDATE','DELETE'):
    name=f'recording_history_recording_{event.lower()}_v1'
    when=" WHEN OLD.session_id IS NOT NEW.session_id OR OLD.capture_origin_frame IS NOT NEW.capture_origin_frame OR NEW.state IN ('cancelled','aborted')" if event=='UPDATE' else ''
    DDL[name]=f'''CREATE TRIGGER {name} AFTER {event}{' OF session_id,capture_origin_frame,state' if event=='UPDATE' else ''} ON recording_batches{when}
        BEGIN {_fence_sql('recording_id=OLD.id')} END'''
for table in (CERTIFICATES,REFS,DEPENDENCIES):
    name=table+'_immutable'
    DDL[name]=f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'Immutable history evidence'); END"
DDL['recording_history_object_immutable_v1']=f'''CREATE TRIGGER recording_history_object_immutable_v1
    BEFORE UPDATE ON {OBJECTS} WHEN OLD.scope_seq IS NOT NEW.scope_seq OR OLD.kind IS NOT NEW.kind OR
    OLD.digest IS NOT NEW.digest OR OLD.body IS NOT NEW.body OR OLD.charge IS NOT NEW.charge
    BEGIN SELECT RAISE(ABORT,'Immutable history proof'); END'''
DDL['recording_history_terminal_immutable_v1']=f'''CREATE TRIGGER recording_history_terminal_immutable_v1
    BEFORE UPDATE ON {ATTEMPTS} WHEN OLD.state!='reserved' OR OLD.scope_seq IS NOT NEW.scope_seq OR
    OLD.origin_kind IS NOT NEW.origin_kind OR OLD.origin_id IS NOT NEW.origin_id OR OLD.identity_sha256 IS NOT NEW.identity_sha256 OR
    OLD.owner IS NOT NEW.owner OR OLD.authority_revision IS NOT NEW.authority_revision
    BEGIN SELECT RAISE(ABORT,'Immutable terminal history attempt'); END'''
DDL['recording_history_source_immutable_v1']=f'''CREATE TRIGGER recording_history_source_immutable_v1
    BEFORE UPDATE ON {SOURCES} WHEN OLD.version IS NOT NEW.version OR OLD.scope_seq IS NOT NEW.scope_seq OR OLD.chunk_id IS NOT NEW.chunk_id OR
    OLD.receipt_digest IS NOT NEW.receipt_digest OR OLD.path_digest IS NOT NEW.path_digest OR OLD.mapping_digest IS NOT NEW.mapping_digest OR
    (OLD.marker IS NOT NULL AND (OLD.marker IS NOT NEW.marker OR OLD.marker_token IS NOT NEW.marker_token)) OR
    (OLD.state='retired' AND NEW.state='active') OR (OLD.state='revoked' AND NEW.state!='revoked')
    BEGIN SELECT RAISE(ABORT,'Immutable source version or retirement'); END'''
DDL['recording_history_scope_policy_v1']=f'''CREATE TRIGGER recording_history_scope_policy_v1
    BEFORE UPDATE OF scope_id,session_id,recording_id,policy,space_id,space_json,interval_frames ON {SCOPES}
    BEGIN SELECT RAISE(ABORT,'Immutable history scope policy'); END'''
for table in (ATTEMPTS,CERTIFICATES,OBJECTS,REFS,DEPENDENCIES,SOURCES):
    name=table+'_erase_only'
    DDL[name]=f'''CREATE TRIGGER {name} BEFORE DELETE ON {table} WHEN NOT EXISTS
        (SELECT 1 FROM {SCOPES} h JOIN sessions s ON s.id=h.session_id WHERE h.scope_seq=OLD.scope_seq AND s.deleted=1)
        BEGIN SELECT RAISE(ABORT,'History requires explicit session erasure'); END'''


def init_schema(db):
    """Exact all-or-none additive schema; caller owns transaction and rollback."""
    if not db.in_transaction:raise ValueError('History migration requires transaction')
    present={r['name']:r['sql'] for r in db.execute('SELECT name,sql FROM sqlite_master WHERE name IN ('+
        ','.join('?' for _ in DDL)+')',tuple(DDL))}
    if present:
        norm=lambda value:' '.join(value.split())
        if set(present)!=set(DDL) or any(norm(present[k])!=norm(v) for k,v in DDL.items()):
            raise ValueError('Incompatible recording history schema')
        return
    for sql in DDL.values():db.execute(sql)
    db.execute(f'''INSERT INTO {SCOPES}(scope_seq,scope_id,session_id,recording_id,policy,space_id,space_json,interval_frames)
        VALUES(0,'','','','','',?,960000)''',(b'null',))


def enabled(settings):return getattr(settings,'recording_primary_observation_policy','off')==POLICY


def _fail(reason='history_unavailable',status=409):raise ReadError(status,reason)


def _raw(value,maximum):
    raw=encoded(value)
    if not 1<=len(raw)<=maximum:_fail('history_component_capacity')
    return raw


def _decode(raw,maximum):
    if type(raw) is not bytes or not 1<=len(raw)<=maximum:_fail('history_proof_missing')
    value=json.loads(raw)
    if encoded(value)!=raw:_fail('history_proof_changed')
    return value


def _hash(raw):return hashlib.sha256(raw).hexdigest()


def _reason(value):return value if value in _REASONS else 'other'


def _live(db,sid):
    row=db.execute('SELECT deleted FROM sessions WHERE id=?',(sid,)).fetchone()
    if row is None or row[0]!=0:_fail('session_not_found',404)


def _legacy_usage(db):
    from .recording_speaker_link_store import SCOPES as LEGACY
    row=db.execute(f'SELECT retained_bytes,staging_bytes FROM {LEGACY} WHERE scope_seq=0').fetchone()
    if row is None:_fail('history_usage_invalid',503)
    return tuple(row)


def aggregate_usage(db):
    row=db.execute(f'SELECT retained_bytes,reserved_bytes,staging_bytes FROM {SCOPES} WHERE scope_seq=0').fetchone()
    if row is None or any(type(n) is not int or n<0 for n in row):_fail('history_usage_invalid',503)
    return tuple(row)


def _change_usage(db,seq,retained=0,reserved=0,staging=0):
    for key in (0,seq):
        if key==0 and seq==0:continue
        changed=db.execute(f'''UPDATE {SCOPES} SET retained_bytes=retained_bytes+?,reserved_bytes=reserved_bytes+?,staging_bytes=staging_bytes+?
            WHERE scope_seq=? AND retained_bytes+? BETWEEN 0 AND ? AND reserved_bytes+? BETWEEN 0 AND ? AND staging_bytes+? BETWEEN 0 AND ?''',
            (retained,reserved,staging,key,retained,MAX_REVISION,reserved,MAX_REVISION,staging,STAGING_BYTES)).rowcount
        if changed!=1:_fail('history_usage_invalid',503)


def _admit(db,scope,extra,staging=0):
    old,old_staging=_legacy_usage(db);retained,reserved,staged=aggregate_usage(db)
    own=0 if scope is None else scope['retained_bytes']+scope['reserved_bytes']
    if (extra<0 or old+retained+reserved+extra>GLOBAL_BYTES or own+extra>RECORDING_BYTES
            or old_staging+staged+staging>STAGING_BYTES):_fail('capacity_exhausted',429)


def invalidate_sources(db,chunk_ids,reason='source_changed'):
    """Observed semantic mismatch; failed durable write cannot claim revocation."""
    if not db.in_transaction:raise ValueError('History invalidation requires transaction')
    ids=tuple(dict.fromkeys(chunk_ids))
    if not ids or len(ids)>128:_fail('history_dependency_capacity')
    marks=','.join('?' for _ in ids)
    db.execute(f'''UPDATE {SCOPES} SET authority_revision=CASE WHEN authority_revision<? THEN authority_revision+1
        ELSE NULL END,authority_blocked=?,state=?,current_slot=NULL WHERE scope_seq IN
        (SELECT scope_seq FROM {SOURCES} INDEXED BY recording_history_source_chunk_v1 WHERE chunk_id IN ({marks}))''',
        (MAX_REVISION,_reason(reason),_reason(reason),*ids))
    db.execute(f"UPDATE {SOURCES} SET state='revoked' WHERE chunk_id IN ({marks})",ids)


def erase_session(db,sid):
    if not db.in_transaction:raise ValueError('History erasure requires transaction')
    dead=db.execute('SELECT deleted FROM sessions WHERE id=?',(sid,)).fetchone()
    if dead is None or dead[0]!=1:_fail('history_erasure_requires_tombstone')
    totals=db.execute(f'''SELECT COALESCE(SUM(retained_bytes),0),COALESCE(SUM(reserved_bytes),0),COALESCE(SUM(staging_bytes),0)
        FROM {SCOPES} INDEXED BY recording_history_session_v1 WHERE session_id=?''',(sid,)).fetchone()
    where=f'scope_seq IN (SELECT scope_seq FROM {SCOPES} WHERE session_id=?)'
    for table in (ROWS,DECISIONS,CERTIFICATES,REFS,DEPENDENCIES,OBJECTS,SOURCES,ATTEMPTS):db.execute(f'DELETE FROM {table} WHERE '+where,(sid,))
    db.execute(f'DELETE FROM {SCOPES} WHERE session_id=?',(sid,))
    changed=db.execute(f'''UPDATE {SCOPES} SET retained_bytes=retained_bytes-?,reserved_bytes=reserved_bytes-?,staging_bytes=staging_bytes-?
        WHERE scope_seq=0 AND retained_bytes>=? AND reserved_bytes>=? AND staging_bytes>=?''',(*totals,*totals)).rowcount
    if changed!=1:_fail('history_usage_invalid',503)


class HistoryStore:
    """Primary DB ownership; manager alone supplies fresh operation authority."""
    def __init__(self,store,*,clock=time.monotonic):
        self.store,self.settings,self.clock=store,store.settings,clock
        self.sql=MossResultStore(store,clock=clock)
        self.owner=uuid.uuid4().hex
        self._success_owner=None;self._capability=None
        self._frozen={};self._active_ready={}
        self._memory_lock=threading.RLock()

    @property
    def enabled(self):return enabled(self.settings)

    def bind_success_owner(self,owner):
        if self._success_owner is not None and self._success_owner is not owner:_fail('history_owner_changed')
        self._success_owner=owner
        if self._capability is None:self._capability=object()
        return self._capability

    @contextmanager
    def _database(self,*,write=False):
        with self.sql._database(write=write) as db:yield db

    def _scope(self,db,sid,rid,space=None,*,create=False):
        _live(db,sid)
        row=db.execute(f'SELECT * FROM {SCOPES} WHERE session_id=? AND recording_id=?',(sid,rid)).fetchone()
        from .recording_speaker_link_store import SCOPES as OLD
        if db.execute(f'SELECT 1 FROM {OLD} WHERE session_id=? AND recording_id=?',(sid,rid)).fetchone():
            _fail('history_legacy_scope_frozen')
        if row is not None:
            if space is not None and row['space_json']!=_raw(space,16384):_fail('history_space_changed')
            return row
        if not create:return None
        if not self.enabled or space is None:_fail('history_not_enabled')
        from .embedding_space import validate_space
        space_id=validate_space(space);space_raw=_raw(space,16384)
        membership=db.execute('SELECT 1 FROM recording_batches WHERE id=? AND session_id=?',(rid,sid)).fetchone()
        if membership is None:_fail('history_recording_missing')
        _admit(db,None,SCOPE_CHARGE)
        seq=db.execute(f'SELECT MAX(scope_seq) FROM {SCOPES}').fetchone()[0]+1
        if seq>MAX_REVISION:_fail('capacity_exhausted')
        scope_id=_hash(encoded({'session_id':sid,'recording_id':rid}))
        db.execute(f'''INSERT INTO {SCOPES}(scope_seq,scope_id,session_id,recording_id,policy,space_id,space_json,interval_frames,retained_bytes)
            VALUES(?,?,?,?,?,?,?,960000,0)''',(seq,scope_id,sid,rid,POLICY,space_id,space_raw))
        _change_usage(db,seq,retained=SCOPE_CHARGE)
        return db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?',(seq,)).fetchone()

    @staticmethod
    def _due(scope,frame):
        if type(frame) is not int or frame<0:_fail('history_source_frame')
        if scope is None:return {'due':True,'reason':'none'}
        if scope['authority_blocked'] is not None:return {'due':False,'reason':'source_changed'}
        if frame<=scope['high_water']:return {'due':False,'reason':'missed'}
        if scope['building_slot'] is not None:return {'due':False,'reason':'pending'}
        if frame<120*16000:
            due=scope['bootstrap_count']<6 and frame-scope['last_claim_frame']>=10*16000
        else:due=(frame-120*16000)//scope['interval_frames']>scope['last_bucket']
        return {'due':due,'reason':'none' if due else 'missed'}

    def uses_history(self,sid,rid):
        if not self.enabled:return False
        from .recording_speaker_link_store import SCOPES as OLD
        with self._database() as db:
            _live(db,sid)
            if db.execute(f'SELECT 1 FROM {OLD} WHERE session_id=? AND recording_id=?',(sid,rid)).fetchone():return False
            return True

    def primary_due(self,sid,rid,source_start_frame):
        if not self.enabled:return {'due':False,'reason':'other'}
        with self._database() as db:return self._due(self._scope(db,sid,rid),source_start_frame)

    def note_primary_skip(self,sid,rid,source_start_frame,reason,space):
        with self._database(write=True) as db:
            scope=self._scope(db,sid,rid,space,create=True)
            if source_start_frame<=scope['high_water']:return False
            bucket=(source_start_frame-120*16000)//scope['interval_frames'] if source_start_frame>=120*16000 else -1
            db.execute(f'''UPDATE {SCOPES} SET high_water=?,last_bucket=MAX(last_bucket,?),skipped_count=MIN(2147483647,skipped_count+1),state=? WHERE scope_seq=?''',
                (source_start_frame,bucket,_reason(reason),scope['scope_seq']))
        return True

    @staticmethod
    def _source_fact(db,receipt,required_end):
        from .native_owned_cell_store import Receipt
        receipt=Receipt.decode(receipt) if type(receipt) is bytes else receipt
        if type(receipt) is not Receipt:_fail('history_receipt_shape')
        row=db.execute('''SELECT c.id,c.session_id,c.digest,c.path,c.duration,c.timeline_start,w.batch_id,w.position,w.frames,w.capture_start_frame,
            sc.session_id AS stream_session,sc.stream_id,sc.position AS stream_position FROM chunks c
            JOIN recording_work w ON w.chunk_id=c.id LEFT JOIN stream_chunks sc ON sc.chunk_id=c.id WHERE c.id=?''',(receipt.chunk_id,)).fetchone()
        if (row is None or row['session_id']!=receipt.session_id or row['digest']!=receipt.digest
                or row['duration']!=receipt.frames/16000 or row['timeline_start']!=receipt.timeline_start
                or row['stream_id']!=receipt.stream_id):_fail('history_source_changed')
        mapping={k:row[k] for k in ('id','session_id','batch_id','position','frames','capture_start_frame','stream_session','stream_id','stream_position')}
        mapping_digest=_hash(encoded(mapping));path_digest=_hash(row['path'].encode('utf-8'))
        version=_hash(encoded([receipt.receipt_id,path_digest,mapping_digest]))
        return {'version':version,'chunk_id':receipt.chunk_id,'receipt_digest':receipt.receipt_id,
            'path_digest':path_digest,'mapping_digest':mapping_digest,'receipt':receipt.encoded,'required_end':required_end,
            'mapping':mapping,'path':row['path']}

    @staticmethod
    def _reservation(row,scope,*,should_probe=False):
        return {'token':row['token'],'scope_seq':row['scope_seq'],'scope_id':scope['scope_id'],
            'session_id':scope['session_id'],'recording_id':scope['recording_id'],
            'identity':row['identity_sha256'],'origin_kind':row['origin_kind'],'owner':row['owner'],
            'authority_revision':row['authority_revision'],'should_probe':should_probe,'state':row['state']}

    def _claim(self,db,scope,attempt_id,origin_kind,origin_id,identity,maximum,staging,current_check):
        if type(attempt_id) is not str or len(attempt_id)!=32:_fail('history_attempt_token')
        existing=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=? OR (scope_seq=? AND origin_kind=? AND origin_id=?)',
            (attempt_id,scope['scope_seq'],origin_kind,origin_id)).fetchone()
        if existing is not None:
            if existing['identity_sha256']!=identity:_fail('history_attempt_conflict')
            return self._reservation(existing,scope)  # Lookup never grants execution.
        if scope['authority_blocked'] is not None or scope['building_slot'] is not None:_fail('history_pending')
        if current_check(db) is not True:_fail('history_source_changed')
        _admit(db,scope,maximum,staging)
        db.execute(f'''INSERT INTO {ATTEMPTS}(token,scope_seq,origin_kind,origin_id,identity_sha256,owner,state,authority_revision,reserved_bytes,staging_bytes,charge)
            VALUES(?,?,?,?,?,?,'reserved',?,?,?,1024)''',
            (attempt_id,scope['scope_seq'],origin_kind,origin_id,identity,self.owner,scope['authority_revision'],maximum-ATTEMPT_CHARGE,staging))
        _change_usage(db,scope['scope_seq'],retained=ATTEMPT_CHARGE,reserved=maximum-ATTEMPT_CHARGE,staging=staging)
        row=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(attempt_id,)).fetchone()
        return self._reservation(row,scope,should_probe=True)

    def claim_primary(self,sid,rid,attempt_id,saved,node,receipt_bytes,*,current_check,space):
        from .recording_primary_probe import validate_standalone_primary_node
        validate_standalone_primary_node(saved,node)
        packet=saved['packet'];frame=packet['commit']['part']['logical_start']
        identity=_hash(encoded(primary_identity(saved,node,node['space_id'])))
        with self._database(write=True) as db:
            scope=self._scope(db,sid,rid,space,create=True)
            previous=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(attempt_id,)).fetchone()
            if previous is not None:
                if previous['identity_sha256']!=identity:_fail('history_attempt_conflict')
                return self._reservation(previous,scope)
            if not self._due(scope,frame)['due']:_fail('history_not_due')
            fact=self._source_fact(db,receipt_bytes,packet['commit']['part']['source_end'])
            self._source_admissible(db,scope['scope_seq'],fact)
            count=len(packet['refs'])
            if not 1<=count<=128:_fail('history_dependency_capacity')
            maximum=10240+512*count+512
            answer=self._claim(db,scope,attempt_id,'primary_commit',saved['header']['digest'],identity,maximum,64000,current_check)
            if answer['should_probe']:
                bucket=(frame-120*16000)//scope['interval_frames'] if frame>=120*16000 else -1
                db.execute(f'''UPDATE {SCOPES} SET high_water=?,last_claim_frame=?,last_bucket=MAX(last_bucket,?),
                    bootstrap_count=bootstrap_count+?,state='pending' WHERE scope_seq=?''',
                    (frame,frame,bucket,int(frame<120*16000),scope['scope_seq']))
        # Acknowledged live basis only. Never reconstructed by resolve_primary.
        with self._memory_lock:self._frozen[attempt_id]={'saved':_decode(_raw(saved,70000),70000),
            'node':_decode(_raw(node,16384),16384),'receipts':(bytes(receipt_bytes),),'sources':(fact,),
            'identity':identity,'terminal':None,'payload':None}
        return answer

    @staticmethod
    def _source_admissible(db,seq,fact):
        row=db.execute(f'SELECT state FROM {SOURCES} WHERE scope_seq=? AND version=?',(seq,fact['version'])).fetchone()
        if row is not None and row['state']!='active':_fail('history_source_admission_closed')
        changed=db.execute(f'SELECT 1 FROM {SOURCES} WHERE chunk_id=? AND scope_seq=? AND version!=? LIMIT 1',
            (fact['chunk_id'],seq,fact['version'])).fetchone()
        if changed is not None:_fail('history_source_version_changed')

    def _attempt(self,db,reservation):
        if type(reservation) is not dict:_fail('history_reservation_shape')
        row=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(reservation.get('token'),)).fetchone()
        if row is None or row['identity_sha256']!=reservation.get('identity') or row['owner']!=self.owner:_fail('history_reservation_changed')
        scope=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?',(row['scope_seq'],)).fetchone()
        _live(db,scope['session_id'])
        return row,scope

    def dispatch_current(self,reservation,*,current_check):
        with self._database() as db:
            row,scope=self._attempt(db,reservation)
            if row['state']!='reserved' or scope['authority_blocked'] is not None or row['authority_revision']!=scope['authority_revision']:
                _fail('history_authority_changed')
            pending=self._frozen.get(row['token'])
            if pending is None:_fail('history_unknown_ack_no_replay')
            for fact in pending['sources']:self._source_admissible(db,scope['scope_seq'],fact)
            if current_check(db) is not True:_fail('history_source_changed')
        return True

    def resolve_primary(self,attempt_id,identity=None):
        with self._database() as db:
            row=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(attempt_id,)).fetchone()
            if row is None:return {'found':False,'should_probe':False}
            expected=_hash(encoded(identity)) if type(identity) is dict else identity
            if expected is not None and expected!=row['identity_sha256']:_fail('history_attempt_conflict')
            scope=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?',(row['scope_seq'],)).fetchone()
            return {**self._reservation(row,scope),'found':True,'terminal_sha256':row['terminal_sha256']}

    resolve_moss=resolve_primary

    def finish_primary(self,reservation,reason):
        token=reservation['token']
        with self._memory_lock:
            pending=self._frozen.setdefault(token,{'terminal':None,'payload':None})
            if pending['terminal'] is None:pending['terminal']=_raw({'schema':1,'outcome':'failure','reason':_reason(reason)},512)
            terminal=pending['terminal']
        with self._database(write=True) as db:
            row,scope=self._attempt(db,reservation)
            if row['state']!='reserved':
                if row['terminal']!=terminal:_fail('history_terminal_conflict')
                return self._reservation(row,scope)
            _change_usage(db,row['scope_seq'],reserved=-row['reserved_bytes'],staging=-row['staging_bytes'])
            db.execute(f'''UPDATE {ATTEMPTS} SET state='failure',reserved_bytes=0,staging_bytes=0,terminal=?,terminal_sha256=? WHERE token=?''',
                (terminal,_hash(terminal),token))
        return {'token':token,'state':'failure','should_probe':False}

    finish_moss=finish_primary

    def claim_moss(self,sid,rid,attempt_id,batch,receipt_bytes,space,*,current_check):
        from .recording_speaker_links import validate_batch
        validate_batch(batch,require_vectors=False)
        if any('vector' in n for n in batch['nodes']):_fail('history_fresh_reservation_required')
        receipts=tuple(receipt_bytes.values()) if type(receipt_bytes) is dict else tuple(receipt_bytes)
        if not 1<=len(receipts)<=128:_fail('history_dependency_capacity')
        identity=_hash(encoded(moss_identity(sid,rid,batch)))
        packets=batch.get('primary_packets',[])
        if len(packets)>4 or sum(len(encoded(x['packet'])) for x in packets)>65536:_fail('history_packet_capacity')
        if sum(len(x['packet']['refs']) for x in packets)>512:_fail('history_dependency_capacity')
        with self._database(write=True) as db:
            scope=self._scope(db,sid,rid,space,create=True)
            from .native_owned_cells import validate_document
            _,_,parts=validate_document(encoded(batch['proof']))
            ends={}
            for p in parts:ends[p.chunk_id]=max(ends.get(p.chunk_id,0),p.source_end)
            for p in packets:
                part=p['packet']['commit']['part'];ends[part['chunk_id']]=max(ends.get(part['chunk_id'],0),part['source_end'])
            facts=tuple(self._source_fact(db,r,0) for r in receipts)
            if set(ends)!={f['chunk_id'] for f in facts}:_fail('history_missing_source_receipt')
            for fact in facts:
                fact['required_end']=ends[fact['chunk_id']]
                self._source_admissible(db,scope['scope_seq'],fact)
            answer=self._claim(db,scope,attempt_id,'moss_result',batch['job_id'],identity,1204736,
                min(PROBE_STAGING_BYTES,64000*len(batch['nodes'])),current_check)
        if answer['should_probe']:
            with self._memory_lock:self._frozen[attempt_id]={'batch':_decode(_raw(batch,524288),524288),
                'receipts':tuple(bytes(x) for x in receipts),'sources':facts,'identity':identity,'terminal':None,'payload':None}
        return answer

    def _authorize_success(self,reservation,node,vector,invocation,capability,*,moss=False):
        if capability is not self._capability or self._success_owner is None:_fail('history_fresh_success_required')
        name='_moss_success_matches' if moss else '_primary_success_matches'
        args=(reservation,node,invocation) if moss else (reservation,node,vector,invocation)
        if getattr(self._success_owner,name)(*args) is not True:_fail('history_cleanup_unverified')

    def _prepare_success(self,reservation,nodes,invocation,*,moss=False):
        from .recording_speaker_links import valid_vector,history_node
        token=reservation['token'];pending=self._frozen.get(token)
        if pending is None:_fail('history_unknown_ack_no_replay')
        if pending['payload'] is not None:return pending['payload']
        if not 1<=len(nodes)<= (4 if moss else 1):_fail('history_certificate_capacity')
        packets=pending['batch'].get('primary_packets',[]) if moss else [pending['saved']]
        packet_refs=[];dependencies=[]
        for saved in packets:
            header,packet=saved['header'],saved['packet']
            packet_refs.append((str(header['sequence']),header['digest']))
            for ref,_ in packet['refs']:
                dependencies.append(('primary',ref.split('.')[1],_raw({'ref':ref},256)))
        objects=[('receipt',_hash(raw),raw) for raw in pending['receipts']]
        basis={'schema':1,'kind':reservation['origin_kind'],'packet_digests':[p['header']['digest'] for p in packets]}
        if moss:
            proof_raw=_raw(pending['batch'],524288)
            basis['proof_digest']=_hash(proof_raw);objects.append(('proof',_hash(proof_raw),proof_raw))
            witnesses=list(pending['batch'].get('cannot_link_witnesses',pending['batch'].get('witnesses',[])))
        else:
            packet=pending['saved']['packet']
            from .recording_speaker_links import _witnesses
            from .recording_primary_probe import _namespace
            turns=[{'label':packet['labels'][t[1]],'original':t[2:4]} for t in packet['turns']]
            witnesses=_witnesses([(_hash(encoded(_namespace(packet))),turns,0,0)],pending['saved']['header']['digest'])
        # Witnesses are derivable from immutable basis; no extra uncharged ledger.
        basis['witness_count']=len(witnesses)
        basis_raw=_raw(basis,512)
        certs=[]
        for node in nodes:
            vector=node['vector'];valid_vector(vector)
            raw_vector=struct.pack('<512f',*vector)
            if list(struct.unpack('<512f',raw_vector))!=vector:_fail('history_original_float32_required')
            metadata=history_node(node,origin_kind=reservation['origin_kind'])
            metadata['invocation_id']=invocation
            header_raw=_raw(metadata,2048)
            digest=_hash(encoded([token,_hash(basis_raw),_hash(header_raw),_hash(raw_vector)]))
            certs.append((digest,header_raw,raw_vector,_hash(raw_vector),len(header_raw)+3072))
        for fact in pending['sources']:
            dependencies.extend((('source',fact['chunk_id'],_raw({'version':fact['version'],'receipt':fact['receipt_digest']},256)),
                ('mapping',fact['chunk_id'],_raw({'mapping':fact['mapping_digest']},256))))
        limits=(128,128,512) if moss else (1,1,128)
        dependencies=list(dict.fromkeys(dependencies))
        for kind,limit in zip(('source','mapping','primary'),limits):
            if sum(x[0]==kind for x in dependencies)>limit:_fail('history_dependency_capacity')
        terminal=_raw({'schema':1,'outcome':'success','invocation':invocation,'basis':_hash(basis_raw),
            'certificates':[x[0] for x in certs]},512)
        payload={'basis':basis_raw,'terminal':terminal,'certificates':tuple(certs),'objects':tuple(objects),
            'packets':tuple(packet_refs),'dependencies':tuple(dependencies),'sources':pending['sources'],
            'witnesses':tuple(witnesses)}
        pending['terminal']=terminal;pending['payload']=payload
        return payload

    def _append(self,reservation,payload,current_check,authorize):
        token=reservation['token']
        with self._database(write=True) as db:
            row,scope=self._attempt(db,reservation)
            if row['state']!='reserved':
                if row['state']!='success' or row['terminal']!=payload['terminal']:_fail('history_terminal_conflict')
                return {'token':token,'state':'success','should_probe':False,'existing':True}
            if scope['authority_blocked'] is not None or row['authority_revision']!=scope['authority_revision']:_fail('history_authority_changed')
            authorize()
            if current_check(db) is not True:_fail('history_source_changed')
            for fact in payload['sources']:
                fresh=self._source_fact(db,fact['receipt'],fact['required_end'])
                if fresh['version']!=fact['version']:_fail('history_source_changed')
                self._source_admissible(db,scope['scope_seq'],fact)
            for sequence,digest in payload['packets']:
                from .recording_primary_provenance import TABLE as PACKETS
                if not db.execute(f'SELECT 1 FROM {PACKETS} WHERE sequence=? AND digest=?',(int(sequence),digest)).fetchone():
                    _fail('history_proof_missing')
            charge=ATTEMPT_CHARGE+BASIS_CHARGE+sum(c[-1] for c in payload['certificates'])
            for kind,digest,raw in payload['objects']:
                existing=db.execute(f'SELECT body,refcount,charge FROM {OBJECTS} WHERE scope_seq=? AND kind=? AND digest=?',(scope['scope_seq'],kind,digest)).fetchone()
                if existing is not None and existing['body']!=raw:_fail('history_proof_changed')
                if existing is None:
                    obj_charge=len(raw)+(256 if kind=='receipt' else 512);charge+=obj_charge
                    db.execute(f'INSERT INTO {OBJECTS} VALUES(?,?,?,?,1,?)',(scope['scope_seq'],kind,digest,raw,obj_charge))
                else:
                    if existing['refcount']>=MAX_REVISION:_fail('capacity_exhausted')
                    db.execute(f'UPDATE {OBJECTS} SET refcount=refcount+1 WHERE scope_seq=? AND kind=? AND digest=?',(scope['scope_seq'],kind,digest))
                edge_charge=256 if kind=='receipt' else 0
                charge+=edge_charge
                db.execute(f'INSERT INTO {REFS} VALUES(?,?,?,?,?,?)',(token,scope['scope_seq'],kind,digest,digest,edge_charge))
            for sequence,digest in payload['packets']:
                charge+=512;db.execute(f'INSERT INTO {REFS} VALUES(?,?,?,?,?,512)',(token,scope['scope_seq'],'packet',sequence,digest))
            for kind,key,fact in payload['dependencies']:
                charge+=512;db.execute(f'INSERT INTO {DEPENDENCIES} VALUES(?,?,?,?,?,512)',(scope['scope_seq'],kind,key,token,fact))
            for fact in payload['sources']:
                old=db.execute(f'SELECT * FROM {SOURCES} WHERE scope_seq=? AND version=?',(scope['scope_seq'],fact['version'])).fetchone()
                if old is None:
                    charge+=512
                    db.execute(f'''INSERT INTO {SOURCES}(scope_seq,version,chunk_id,receipt_digest,path_digest,mapping_digest,required_end)
                        VALUES(?,?,?,?,?,?,?)''',(scope['scope_seq'],fact['version'],fact['chunk_id'],fact['receipt_digest'],fact['path_digest'],fact['mapping_digest'],fact['required_end']))
                bound=sum(any(span[0]==_decode(fact['receipt'],1024)['digest'] for span in _decode(c[1],2048)['spans']) for c in payload['certificates'])
                if bound==0:bound=len(payload['certificates'])  # Full proof sources remain authority dependencies.
                db.execute(f'''UPDATE {SOURCES} SET admission_revision=admission_revision+1,certificate_count=certificate_count+?,required_end=MAX(required_end,?)
                    WHERE scope_seq=? AND version=?''',(bound,fact['required_end'],scope['scope_seq'],fact['version']))
            if charge>row['charge']+row['reserved_bytes']:_fail('history_reservation_exceeded')
            if scope['history_revision']>=MAX_REVISION:_fail('capacity_exhausted')
            base=scope['certificate_count']
            for index,cert in enumerate(payload['certificates'],1):
                db.execute(f'INSERT INTO {CERTIFICATES} VALUES(?,?,?,?,?,?,?,?)',(scope['scope_seq'],base+index,token,*cert))
            _change_usage(db,scope['scope_seq'],retained=charge-row['charge'],reserved=-row['reserved_bytes'],staging=-row['staging_bytes'])
            db.execute(f'''UPDATE {ATTEMPTS} SET state='success',basis=?,terminal=?,terminal_sha256=?,reserved_bytes=0,staging_bytes=0,charge=? WHERE token=?''',
                (payload['basis'],payload['terminal'],_hash(payload['terminal']),charge,token))
            db.execute(f'''UPDATE {SCOPES} SET history_revision=history_revision+1,certificate_count=certificate_count+?,witness_count=witness_count+?,state='pending' WHERE scope_seq=?''',
                (len(payload['certificates']),len(payload['witnesses']),scope['scope_seq']))
        with self._memory_lock:
            for fact in payload['sources']:self._active_ready[(scope['scope_seq'],fact['version'])]=scope['authority_revision']
        return {'token':token,'state':'success','should_probe':False,'history_revision':scope['history_revision']+1}

    def append_primary(self,reservation,node,vector,invocation_id,*,success_capability,current_check):
        def authorize():self._authorize_success(reservation,node,vector,invocation_id,success_capability)
        authorize()
        pending=self._frozen.get(reservation['token'])
        if pending is None or pending['node']!=node:_fail('history_node_changed')
        from .recording_primary_probe import validate_standalone_primary_node
        validate_standalone_primary_node(pending['saved'],node)
        payload=self._prepare_success(reservation,[dict(node,vector=vector)],invocation_id)
        return self._append(reservation,payload,current_check,authorize)

    def append_moss(self,reservation,batch,invocation_id,*,success_capability,current_check):
        def authorize():self._authorize_success(reservation,batch,None,invocation_id,success_capability,moss=True)
        authorize()
        from .recording_speaker_links import validate_batch
        validate_batch(batch)
        pending=self._frozen.get(reservation['token'])
        if pending is None:_fail('history_unknown_ack_no_replay')
        bare={**batch,'nodes':[{k:v for k,v in n.items() if k!='vector'} for n in batch['nodes']]}
        if bare!=pending['batch']:_fail('history_batch_changed')
        payload=self._prepare_success(reservation,batch['nodes'],invocation_id,moss=True)
        return self._append(reservation,payload,current_check,authorize)


def primary_identity(saved,node,space_id):
    return {'sid':saved['header']['sid'],'rid':saved['header']['rid'],'origin_kind':'primary_commit',
        'origin_id':saved['header']['digest'],'node_sha256':_hash(encoded(node)),'space_id':space_id}


def moss_identity(sid,rid,batch):
    return {'sid':sid,'rid':rid,'origin_kind':'moss_result','origin_id':batch['job_id'],
        'result_sha256':batch['result_sha256'],'batch_sha256':batch['batch_sha256'],'space_id':batch['space_id']}
