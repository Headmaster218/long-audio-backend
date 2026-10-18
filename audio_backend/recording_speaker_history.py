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
import sys
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
# Fixed process-controller metadata reserve is charged by both shared admission guards.
CONTROLLER_METADATA_BYTES = 512 * 1024
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
    'cleanup_unknown','storage_unknown','probe_failed','pending','deadline','resource_pending','unknown_rival','other')
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
    observation_count INTEGER NOT NULL DEFAULT 0 {_INT.format('observation_count')},
    vector_count INTEGER NOT NULL DEFAULT 0 CHECK(vector_count BETWEEN 0 AND observation_count),
    witness_count INTEGER NOT NULL DEFAULT 0 {_INT.format('witness_count')},
    source_count INTEGER NOT NULL DEFAULT 0 {_INT.format('source_count')},
    active_sources INTEGER NOT NULL DEFAULT 0 {_INT.format('active_sources')},
    source_revision INTEGER NOT NULL DEFAULT 0 {_INT.format('source_revision')},
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
    owner TEXT NOT NULL CHECK(length(owner)=32), invocation_id TEXT NOT NULL CHECK(length(invocation_id)=32 AND invocation_id NOT GLOB '*[^0-9a-f]*'), state TEXT NOT NULL CHECK(state IN ('reserved','success','failure')),
    authority_revision INTEGER NOT NULL CHECK(authority_revision>=0),
    reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes BETWEEN 0 AND 1204736),
    staging_bytes INTEGER NOT NULL CHECK(staging_bytes BETWEEN 0 AND 524288),
    charge INTEGER NOT NULL CHECK(charge>=1024),
    witness_start INTEGER NOT NULL DEFAULT 0 CHECK(witness_start>=0), witness_count INTEGER NOT NULL DEFAULT 0 CHECK(witness_count>=0),
    basis BLOB CHECK(basis IS NULL OR (typeof(basis)='blob' AND length(basis) BETWEEN 1 AND 512)),
    terminal BLOB CHECK(terminal IS NULL OR (typeof(terminal)='blob' AND length(terminal) BETWEEN 1 AND 512)),
    terminal_sha256 TEXT CHECK(terminal_sha256 IS NULL OR length(terminal_sha256)=64),
    UNIQUE(scope_seq,origin_kind,origin_id),
    CHECK((state='reserved' AND terminal IS NULL AND terminal_sha256 IS NULL) OR
      (state='success' AND basis IS NOT NULL AND terminal IS NOT NULL AND terminal_sha256 IS NOT NULL AND reserved_bytes=0 AND staging_bytes=0) OR
      (state='failure' AND basis IS NOT NULL AND terminal IS NOT NULL AND terminal_sha256 IS NOT NULL AND reserved_bytes=0 AND staging_bytes=0))) WITHOUT ROWID''',
CERTIFICATES: f'''CREATE TABLE {CERTIFICATES} (
    scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq), sequence INTEGER NOT NULL CHECK(sequence>0),
    attempt TEXT NOT NULL REFERENCES {ATTEMPTS}(token), observation_id TEXT NOT NULL UNIQUE CHECK(length(observation_id)=64),
    invocation_id TEXT NOT NULL CHECK(length(invocation_id)=32 AND invocation_id NOT GLOB '*[^0-9a-f]*'),
    evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('pending','candidate_missing','vector')),
    certificate_id TEXT UNIQUE CHECK(certificate_id IS NULL OR length(certificate_id)=64),
    header BLOB NOT NULL CHECK(typeof(header)='blob' AND length(header) BETWEEN 1 AND 2048),
    vector BLOB, vector_sha256 TEXT, charge INTEGER NOT NULL,
    PRIMARY KEY(scope_seq,sequence),
    CHECK((evidence_kind IN ('pending','candidate_missing') AND vector IS NULL AND vector_sha256 IS NULL
           AND certificate_id IS NULL AND charge=length(header)+1024) OR
          (evidence_kind='vector' AND typeof(vector)='blob' AND length(vector)=2048
           AND vector_sha256 IS NOT NULL AND length(vector_sha256)=64 AND certificate_id IS NOT NULL
           AND charge=length(header)+3072))) WITHOUT ROWID''',
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
    chunk_id TEXT NOT NULL CHECK(length(chunk_id) BETWEEN 1 AND 64),
    receipt_digest BLOB NOT NULL CHECK(typeof(receipt_digest)='blob' AND length(receipt_digest)=32),
    mapping_digest BLOB NOT NULL CHECK(typeof(mapping_digest)='blob' AND length(mapping_digest)=32),
    state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','retired','revoked')),
    admission_revision INTEGER NOT NULL DEFAULT 0 {_INT.format('admission_revision')},
    observation_count INTEGER NOT NULL DEFAULT 0 {_INT.format('observation_count')},
    vector_count INTEGER NOT NULL DEFAULT 0 CHECK(vector_count BETWEEN 0 AND observation_count),
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
    observation_count INTEGER NOT NULL CHECK(observation_count>=0), vector_count INTEGER NOT NULL CHECK(vector_count BETWEEN 0 AND observation_count), witness_count INTEGER NOT NULL CHECK(witness_count>=0),
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
'recording_history_dirty_v1': f"CREATE INDEX recording_history_dirty_v1 ON {SCOPES}(scope_seq) WHERE scope_seq>0 AND authority_blocked IS NULL AND state NOT IN ('none','unknown_rival')",
'recording_history_attempt_scope_v1': f'CREATE INDEX recording_history_attempt_scope_v1 ON {ATTEMPTS}(scope_seq,state,token)',
'recording_history_witness_v1': f"CREATE INDEX recording_history_witness_v1 ON {ATTEMPTS}(scope_seq,witness_start,token) WHERE state!='reserved' AND witness_count>0",
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
    ('chunks','id',('id','session_id','digest','path','duration','timeline_start'),None,'source'),
    ('stream_chunks','chunk_id',('chunk_id','session_id','stream_id','position'),None,'mapping'),
    ('recording_work','chunk_id',('chunk_id','batch_id','position','frames','capture_start_frame','native_receipt','receipt_id','file_signature'),None,'mapping'),
    ('segments','id',('id','chunk_id','start','end','text','raw_text','asr_backend','timestamp_kind','speaker_local_id','speaker_window_start'),None,'primary')):
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
    when=" WHEN OLD.id IS NOT NEW.id OR OLD.session_id IS NOT NEW.session_id OR OLD.capture_origin_frame IS NOT NEW.capture_origin_frame OR NEW.state IN ('cancelled','aborted')" if event=='UPDATE' else ''
    DDL[name]=f'''CREATE TRIGGER {name} AFTER {event}{' OF id,session_id,capture_origin_frame,state' if event=='UPDATE' else ''} ON recording_batches{when}
        BEGIN {_fence_sql('recording_id=OLD.id')} END'''
for table in (REFS,DEPENDENCIES):
    name=table+'_immutable'
    DDL[name]=f"CREATE TRIGGER {name} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'Immutable history evidence'); END"
DDL[CERTIFICATES+'_immutable']=f'''CREATE TRIGGER {CERTIFICATES}_immutable BEFORE UPDATE ON {CERTIFICATES}
    WHEN OLD.evidence_kind!='pending' OR NEW.evidence_kind NOT IN ('candidate_missing','vector') OR
    OLD.scope_seq IS NOT NEW.scope_seq OR OLD.sequence IS NOT NEW.sequence OR OLD.attempt IS NOT NEW.attempt OR
    OLD.observation_id IS NOT NEW.observation_id OR OLD.invocation_id IS NOT NEW.invocation_id OR OLD.header IS NOT NEW.header
    BEGIN SELECT RAISE(ABORT,'Immutable observation identity or terminal evidence'); END'''
DDL['recording_history_object_immutable_v1']=f'''CREATE TRIGGER recording_history_object_immutable_v1
    BEFORE UPDATE ON {OBJECTS} WHEN OLD.scope_seq IS NOT NEW.scope_seq OR OLD.kind IS NOT NEW.kind OR
    OLD.digest IS NOT NEW.digest OR OLD.body IS NOT NEW.body OR OLD.charge IS NOT NEW.charge
    BEGIN SELECT RAISE(ABORT,'Immutable history proof'); END'''
DDL['recording_history_terminal_immutable_v1']=f'''CREATE TRIGGER recording_history_terminal_immutable_v1
    BEFORE UPDATE ON {ATTEMPTS} WHEN OLD.state!='reserved' OR OLD.scope_seq IS NOT NEW.scope_seq OR
    OLD.origin_kind IS NOT NEW.origin_kind OR OLD.origin_id IS NOT NEW.origin_id OR OLD.identity_sha256 IS NOT NEW.identity_sha256 OR
    OLD.owner IS NOT NEW.owner OR OLD.invocation_id IS NOT NEW.invocation_id OR OLD.authority_revision IS NOT NEW.authority_revision OR
    (OLD.basis IS NOT NULL AND (OLD.basis IS NOT NEW.basis OR OLD.witness_start IS NOT NEW.witness_start OR OLD.witness_count IS NOT NEW.witness_count))
    BEGIN SELECT RAISE(ABORT,'Immutable terminal history attempt'); END'''
DDL['recording_history_source_immutable_v1']=f'''CREATE TRIGGER recording_history_source_immutable_v1
    BEFORE UPDATE ON {SOURCES} WHEN OLD.version IS NOT NEW.version OR OLD.scope_seq IS NOT NEW.scope_seq OR OLD.chunk_id IS NOT NEW.chunk_id OR
    OLD.receipt_digest IS NOT NEW.receipt_digest OR OLD.mapping_digest IS NOT NEW.mapping_digest OR
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
    try:value=json.loads(raw)
    except (ValueError,UnicodeError,RecursionError):_fail('history_proof_changed')
    if encoded(value)!=raw:_fail('history_proof_changed')
    return value


def _hash(raw):return hashlib.sha256(raw).hexdigest()


def _source_document(row):
    """Storage uses compact digest references; public opaque versions stay hex.

    The source row's fixed payload is <=256 bytes: two 64-byte keys, two
    32-byte digests, five 8-byte integers and its bounded state. Path digest is
    already bound into version and is rederived from exact live facts before
    retirement, then frozen in the separately charged retirement marker.
    """
    value=dict(row)
    for name in ('receipt_digest','mapping_digest'):
        if type(value[name]) is not bytes or len(value[name])!=32:_fail('history_proof_changed')
        value[name]=value[name].hex()
    return value


def moss_staging_bound(batch,receipts,*,crop_bytes=None):
    """Conservative complete new-route owned peak, never a history-size cap.

    Three graph/wire copies cover live owner, admission decode and serializer.
    Per receipt/ref allowances cover source facts and dependency/edge tuples;
    128 KiB covers four vector results, pending headers and fixed control state.
    The original native lease still owns candidate construction until this passes.
    """
    count=0
    def size(value,depth=0):
        nonlocal count
        count+=1
        if count>8192 or depth>16:_fail('history_staging_capacity',429)
        amount=sys.getsizeof(value)
        if type(value) is dict:
            for key,item in value.items():amount+=size(key,depth+1)+size(item,depth+1)
        elif type(value) in (list,tuple):
            for item in value:amount+=size(item,depth+1)
        elif value is not None and type(value) not in (str,int,float,bool,bytes):_fail('history_staging_capacity',429)
        return amount
    graph=size(batch)+size(receipts)
    wire=len(encoded(batch))+sum(len(raw) for raw in receipts)
    refs=sum(len(saved['packet']['refs']) for saved in batch.get('primary_packets',[]))
    crops=64000*len(batch['nodes']) if crop_bytes is None else crop_bytes
    peak=3*(graph+wire)+crops+131072+4096*len(receipts)+1024*refs
    if peak>PROBE_STAGING_BYTES:_fail('history_staging_capacity',429)
    return peak


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
            or old_staging+staged+staging+CONTROLLER_METADATA_BYTES>STAGING_BYTES):_fail('capacity_exhausted',429)


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
            db.execute(f'''UPDATE {SCOPES} SET high_water=?,last_bucket=MAX(last_bucket,?),skipped_count=MIN(2147483647,skipped_count+1),state=CASE WHEN state='unknown_rival' THEN state ELSE ? END WHERE scope_seq=?''',
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
            'authority_revision':row['authority_revision'],'invocation_id':row['invocation_id'],'should_probe':should_probe,'state':row['state']}

    def _authorize_admission(self,attempt_id,saved_or_batch,node,invocation,capability,*,moss=False):
        if capability is not self._capability or self._success_owner is None:_fail('history_admission_required')
        if type(invocation) is not str or len(invocation)!=32 or any(c not in '0123456789abcdef' for c in invocation):_fail('history_invocation_token')
        name='_moss_admission_matches' if moss else '_primary_admission_matches'
        args=(attempt_id,saved_or_batch,invocation) if moss else (attempt_id,saved_or_batch,node,invocation)
        if getattr(self._success_owner,name)(*args) is not True:_fail('history_admission_unverified')

    def _claim(self,db,scope,attempt_id,origin_kind,origin_id,identity,maximum,staging,current_check,payload):
        if type(attempt_id) is not str or len(attempt_id)!=32:_fail('history_attempt_token')
        existing=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=? OR (scope_seq=? AND origin_kind=? AND origin_id=?)',
            (attempt_id,scope['scope_seq'],origin_kind,origin_id)).fetchone()
        if existing is not None:
            if existing['identity_sha256']!=identity or existing['invocation_id']!=payload['invocation_id']:_fail('history_attempt_conflict')
            return self._reservation(existing,scope)  # Lookup never grants execution.
        if scope['authority_blocked'] is not None or scope['building_slot'] is not None:_fail('history_pending')
        if current_check(db) is not True:_fail('history_source_changed')
        _admit(db,scope,maximum,staging)
        db.execute(f'''INSERT INTO {ATTEMPTS}(token,scope_seq,origin_kind,origin_id,identity_sha256,owner,invocation_id,state,authority_revision,reserved_bytes,staging_bytes,charge)
            VALUES(?,?,?,?,?,?,?,'reserved',?,?,?,1024)''',
            (attempt_id,scope['scope_seq'],origin_kind,origin_id,identity,self.owner,payload['invocation_id'],scope['authority_revision'],maximum-ATTEMPT_CHARGE,staging))
        _change_usage(db,scope['scope_seq'],retained=ATTEMPT_CHARGE,reserved=maximum-ATTEMPT_CHARGE,staging=staging)
        row=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(attempt_id,)).fetchone()
        self._retain_admission(db,row,scope,payload)
        row=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(attempt_id,)).fetchone()
        return self._reservation(row,scope,should_probe=True)

    def claim_primary(self,sid,rid,attempt_id,saved,node,receipt_bytes,*,current_check,space,admission_capability,invocation_id):
        from .recording_primary_probe import validate_standalone_primary_node
        validate_standalone_primary_node(saved,node)
        self._authorize_admission(attempt_id,saved,node,invocation_id,admission_capability)
        packet=saved['packet'];frame=packet['commit']['part']['logical_start']
        identity=_hash(encoded(primary_identity(saved,node,node['space_id'])))
        with self._database(write=True) as db:
            scope=self._scope(db,sid,rid,space,create=True)
            previous=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(attempt_id,)).fetchone()
            if previous is not None:
                if previous['identity_sha256']!=identity or previous['invocation_id']!=invocation_id:_fail('history_attempt_conflict')
                return self._reservation(previous,scope)
            if not self._due(scope,frame)['due']:_fail('history_not_due')
            fact=self._source_fact(db,receipt_bytes,packet['commit']['part']['source_end'])
            self._source_admissible(db,scope['scope_seq'],fact)
            count=len(packet['refs'])
            if not 1<=count<=128:_fail('history_dependency_capacity')
            pending={'saved':saved,'node':node,'receipts':(bytes(receipt_bytes),),'sources':(fact,),
                'identity':identity,'terminal':None,'payload':None,'invocation_id':invocation_id}
            payload=self._prepare_admission(attempt_id,'primary_commit',pending,[node])
            # Freeze before first submission; unknown acknowledgement retains only
            # exact owned metadata until its storage-only resolution is acknowledged.
            with self._memory_lock:self._frozen[attempt_id]=pending
            self._authorize_admission(attempt_id,saved,node,invocation_id,admission_capability)
            maximum=10240+512*count+512
            answer=self._claim(db,scope,attempt_id,'primary_commit',saved['header']['digest'],identity,maximum,
                PROBE_STAGING_BYTES,current_check,payload)
            if answer['should_probe']:
                bucket=(frame-120*16000)//scope['interval_frames'] if frame>=120*16000 else -1
                db.execute(f'''UPDATE {SCOPES} SET high_water=?,last_claim_frame=?,last_bucket=MAX(last_bucket,?),
                    bootstrap_count=bootstrap_count+?,state='pending' WHERE scope_seq=?''',
                    (frame,frame,bucket,int(frame<120*16000),scope['scope_seq']))
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

    @staticmethod
    def _failure_bytes(reason,failure_json=None):
        if failure_json is not None:
            from .recording_speaker_link_store import validate_failure
            validate_failure(failure_json)
            return bytes(failure_json)
        return _raw({'schema':1,'outcome':'failure','reason':_reason(reason)},512)

    @staticmethod
    def _finish_missing(db,row,scope,terminal):
        if row['basis'] is None:_fail('history_basis_missing')
        db.execute(f"UPDATE {CERTIFICATES} SET evidence_kind='candidate_missing' WHERE attempt=? AND evidence_kind='pending'",(row['token'],))
        _change_usage(db,row['scope_seq'],reserved=-row['reserved_bytes'],staging=-row['staging_bytes'])
        db.execute(f"UPDATE {ATTEMPTS} SET state='failure',reserved_bytes=0,staging_bytes=0,terminal=?,terminal_sha256=? WHERE token=?",
            (terminal,_hash(terminal),row['token']))
        if scope['history_revision']>=MAX_REVISION:_fail('capacity_exhausted')
        db.execute(f"UPDATE {SCOPES} SET history_revision=history_revision+1,current_slot=NULL,state='pending' WHERE scope_seq=?",(row['scope_seq'],))

    def resolve_primary(self,attempt_id,identity=None,*,failure_json=None):
        # Exact unknown-outcome closure never grants inference permission. A
        # positively committed success is observed, never replaced or replayed.
        terminal=self._failure_bytes('storage_unknown',failure_json)
        with self._database(write=True) as db:
            row=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(attempt_id,)).fetchone()
            if row is None:return {'found':False,'resolved':True,'should_probe':False}
            expected=_hash(encoded(identity)) if type(identity) is dict else identity
            if expected is None or expected!=row['identity_sha256']:_fail('history_attempt_conflict')
            scope=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?',(row['scope_seq'],)).fetchone()
            if row['state']=='reserved':
                self._finish_missing(db,row,scope,terminal)
                row=db.execute(f'SELECT * FROM {ATTEMPTS} WHERE token=?',(attempt_id,)).fetchone()
            elif failure_json is not None and (row['state']!='failure' or row['terminal']!=terminal):_fail('history_terminal_conflict')
            return {**self._reservation(row,scope),'found':True,'resolved':True,'terminal_sha256':row['terminal_sha256']}

    resolve_moss=resolve_primary

    def finish_primary(self,reservation,reason,*,failure_json=None):
        token=reservation['token'];candidate=self._failure_bytes(reason,failure_json)
        with self._memory_lock:
            pending=self._frozen.setdefault(token,{'terminal':None,'payload':None})
            if pending.get('failure_terminal') is None:pending['failure_terminal']=candidate
            elif pending['failure_terminal']!=candidate:_fail('history_terminal_conflict')
            terminal=pending['failure_terminal']
        with self._database(write=True) as db:
            row,scope=self._attempt(db,reservation)
            if row['state']!='reserved':
                if row['state']!='failure' or row['terminal']!=terminal:_fail('history_terminal_conflict')
                return self._reservation(row,scope)
            self._finish_missing(db,row,scope,terminal)
        return {'token':token,'state':'failure','should_probe':False}

    finish_moss=finish_primary

    def claim_moss(self,sid,rid,attempt_id,batch,receipt_bytes,space,*,current_check,admission_capability,invocation_id):
        from .recording_speaker_links import validate_batch
        validate_batch(batch,require_vectors=False)
        if any('vector' in n for n in batch['nodes']):_fail('history_fresh_reservation_required')
        self._authorize_admission(attempt_id,batch,None,invocation_id,admission_capability,moss=True)
        receipts=tuple(receipt_bytes.values()) if type(receipt_bytes) is dict else tuple(receipt_bytes)
        if not 1<=len(receipts)<=128:_fail('history_dependency_capacity')
        moss_staging_bound(batch,receipts)
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
            pending={'batch_raw':_raw(batch,524288),'receipts':tuple(bytes(x) for x in receipts),
                'sources':facts,'identity':identity,'terminal':None,'payload':None,'invocation_id':invocation_id}
            payload=self._prepare_admission(attempt_id,'moss_result',pending,batch['nodes'])
            with self._memory_lock:self._frozen[attempt_id]=pending
            self._authorize_admission(attempt_id,batch,None,invocation_id,admission_capability,moss=True)
            answer=self._claim(db,scope,attempt_id,'moss_result',batch['job_id'],identity,1204736,
                PROBE_STAGING_BYTES,current_check,payload)
        return answer

    def _authorize_success(self,reservation,node,vector,invocation,capability,*,moss=False):
        if capability is not self._capability or self._success_owner is None:_fail('history_fresh_success_required')
        name='_moss_success_matches' if moss else '_primary_success_matches'
        args=(reservation,node,invocation) if moss else (reservation,node,vector,invocation)
        if getattr(self._success_owner,name)(*args) is not True:_fail('history_cleanup_unverified')

    def _prepare_admission(self,token,origin_kind,pending,nodes):
        """Freeze authenticated native facts before the first claim submission."""
        from .recording_speaker_links import history_node
        moss=origin_kind=='moss_result'
        if not 0<=len(nodes)<=(4 if moss else 1) or not moss and len(nodes)!=1:_fail('history_observation_capacity')
        batch=_decode(pending['batch_raw'],524288) if moss else None
        packets=batch.get('primary_packets',[]) if moss else [pending['saved']]
        packet_refs=[];dependencies=[]
        for saved in packets:
            header,packet=saved['header'],saved['packet']
            packet_refs.append((str(header['sequence']),header['digest']))
            for ref,_ in packet['refs']:
                dependencies.append(('primary',ref.split('.')[1],_raw({'ref':ref},256)))
        objects=[('receipt',_hash(raw),raw) for raw in pending['receipts']]
        basis={'schema':1,'kind':origin_kind,'packet_digests':[p['header']['digest'] for p in packets]}
        if moss:
            proof_raw=pending['batch_raw']
            basis['proof_digest']=_hash(proof_raw);objects.append(('proof',_hash(proof_raw),proof_raw))
            witnesses=tuple(batch.get('witnesses',[]))
        else:
            packet=pending['saved']['packet']
            from .recording_speaker_links import _witnesses
            from .recording_primary_probe import _namespace
            turns=[{'label':packet['labels'][t[1]],'original':t[2:4]} for t in packet['turns']]
            witnesses=tuple(_witnesses([(_hash(encoded(_namespace(packet))),turns,0,0)],pending['saved']['header']['digest']))
        # Full proof is immutable; witnesses are derived rather than duplicated.
        basis['witness_count']=len(witnesses)
        basis_raw=_raw(basis,512);observations=[]
        for node in nodes:
            header_raw=_raw(history_node(node,origin_kind=origin_kind),2048)
            observation_id=_hash(encoded([token,_hash(basis_raw),_hash(header_raw)]))
            observations.append((observation_id,pending['invocation_id'],'pending',None,header_raw,None,None,len(header_raw)+1024))
        for fact in pending['sources']:
            dependencies.extend((('source',fact['chunk_id'],_raw({'version':fact['version'],'receipt':fact['receipt_digest']},256)),
                ('mapping',fact['chunk_id'],_raw({'mapping':fact['mapping_digest']},256))))
        limits=(128,128,512) if moss else (1,1,128)
        dependencies=list(dict.fromkeys(dependencies))
        for kind,limit in zip(('source','mapping','primary'),limits):
            if sum(x[0]==kind for x in dependencies)>limit:_fail('history_dependency_capacity')
        payload={'basis':basis_raw,'invocation_id':pending['invocation_id'],'observations':tuple(observations),'objects':tuple(objects),'packets':tuple(packet_refs),
            'dependencies':tuple(dependencies),'sources':pending['sources'],'witness_count':len(witnesses)}
        pending['admission']=payload
        return payload

    @staticmethod
    def _validate_payload_sources(db,scope,payload):
        for fact in payload['sources']:
            fresh=HistoryStore._source_fact(db,fact['receipt'],fact['required_end'])
            if fresh['version']!=fact['version']:_fail('history_source_changed')
            HistoryStore._source_admissible(db,scope['scope_seq'],fact)
        for sequence,digest in payload['packets']:
            from .recording_primary_provenance import TABLE as PACKETS
            if not db.execute(f'SELECT 1 FROM {PACKETS} WHERE sequence=? AND digest=?',(int(sequence),digest)).fetchone():
                _fail('history_proof_missing')

    def _retain_admission(self,db,row,scope,payload):
        token=row['token'];self._validate_payload_sources(db,scope,payload)
        charge=ATTEMPT_CHARGE+BASIS_CHARGE+sum(c[-1] for c in payload['observations'])
        for kind,digest,raw in payload['objects']:
            existing=db.execute(f'SELECT body,refcount,charge FROM {OBJECTS} WHERE scope_seq=? AND kind=? AND digest=?',(scope['scope_seq'],kind,digest)).fetchone()
            if existing is not None and existing['body']!=raw:_fail('history_proof_changed')
            if existing is None:
                obj_charge=len(raw)+(256 if kind=='receipt' else 512);charge+=obj_charge
                db.execute(f'INSERT INTO {OBJECTS} VALUES(?,?,?,?,1,?)',(scope['scope_seq'],kind,digest,raw,obj_charge))
            else:
                if existing['refcount']>=MAX_REVISION:_fail('capacity_exhausted')
                db.execute(f'UPDATE {OBJECTS} SET refcount=refcount+1 WHERE scope_seq=? AND kind=? AND digest=?',(scope['scope_seq'],kind,digest))
            edge_charge=256 if kind=='receipt' else 0;charge+=edge_charge
            db.execute(f'INSERT INTO {REFS} VALUES(?,?,?,?,?,?)',(token,scope['scope_seq'],kind,digest,digest,edge_charge))
        for sequence,digest in payload['packets']:
            charge+=512;db.execute(f'INSERT INTO {REFS} VALUES(?,?,?,?,?,512)',(token,scope['scope_seq'],'packet',sequence,digest))
        for kind,key,fact in payload['dependencies']:
            charge+=512;db.execute(f'INSERT INTO {DEPENDENCIES} VALUES(?,?,?,?,?,512)',(scope['scope_seq'],kind,key,token,fact))
        for fact in payload['sources']:
            old=db.execute(f'SELECT * FROM {SOURCES} WHERE scope_seq=? AND version=?',(scope['scope_seq'],fact['version'])).fetchone()
            if old is None:
                charge+=512
                db.execute(f'UPDATE {SCOPES} SET source_count=source_count+1,active_sources=active_sources+1 WHERE scope_seq=?',(scope['scope_seq'],))
                db.execute(f'''INSERT INTO {SOURCES}(scope_seq,version,chunk_id,receipt_digest,mapping_digest,required_end)
                    VALUES(?,?,?,?,?,?)''',(scope['scope_seq'],fact['version'],fact['chunk_id'],bytes.fromhex(fact['receipt_digest']),bytes.fromhex(fact['mapping_digest']),fact['required_end']))
            db.execute(f'''UPDATE {SOURCES} SET admission_revision=admission_revision+1,observation_count=observation_count+?,required_end=MAX(required_end,?)
                WHERE scope_seq=? AND version=?''',(len(payload['observations']),fact['required_end'],scope['scope_seq'],fact['version']))
        growth=2048*len(payload['observations'])
        if charge+growth>row['charge']+row['reserved_bytes']:_fail('history_reservation_exceeded')
        if scope['history_revision']>=MAX_REVISION:_fail('capacity_exhausted')
        base=scope['observation_count']
        for index,observation in enumerate(payload['observations'],1):
            db.execute(f'INSERT INTO {CERTIFICATES} VALUES(?,?,?,?,?,?,?,?,?,?,?)',(scope['scope_seq'],base+index,token,*observation))
        _change_usage(db,scope['scope_seq'],retained=charge-row['charge'],reserved=growth-row['reserved_bytes'])
        # Attempt charge owns only its fixed row and basis; referenced objects,
        # edges and observations carry their separate exactly-once charges.
        db.execute(f'''UPDATE {ATTEMPTS} SET basis=?,reserved_bytes=?,charge=?,witness_start=?,witness_count=? WHERE token=?''',
            (payload['basis'],growth,ATTEMPT_CHARGE+BASIS_CHARGE,scope['witness_count'],payload['witness_count'],token))
        db.execute(f'''UPDATE {SCOPES} SET history_revision=history_revision+1,observation_count=observation_count+?,
            witness_count=witness_count+?,state='pending',current_slot=NULL WHERE scope_seq=?''',
            (len(payload['observations']),payload['witness_count'],scope['scope_seq']))

    def _prepare_success(self,reservation,nodes,invocation,*,moss=False):
        from .recording_speaker_links import valid_vector,history_node
        token=reservation['token'];pending=self._frozen.get(token)
        if pending is None:_fail('history_unknown_ack_no_replay')
        if invocation!=pending['invocation_id']:_fail('history_terminal_conflict')
        input_digest=_hash(encoded([nodes,invocation]))
        if pending['payload'] is not None:
            if pending.get('success_input_digest')!=input_digest:_fail('history_terminal_conflict')
            return pending['payload']
        admission=pending['admission']
        if not 1<=len(nodes)==len(admission['observations']):_fail('history_observation_capacity')
        certs=[]
        for node,observation in zip(nodes,admission['observations']):
            vector=node['vector'];valid_vector(vector)
            raw_vector=struct.pack('<512f',*vector)
            if list(struct.unpack('<512f',raw_vector))!=vector:_fail('history_original_float32_required')
            if _raw(history_node(node,origin_kind=reservation['origin_kind']),2048)!=observation[4]:_fail('history_node_changed')
            digest=_hash(raw_vector);certificate_id=_hash(encoded([observation[0],invocation,digest]))
            certs.append((observation[0],certificate_id,raw_vector,digest))
        terminal=_raw({'schema':1,'outcome':'success','invocation':invocation,'basis':_hash(admission['basis']),
            'certificates':[x[1] for x in certs]},512)
        payload={'admission':admission,'terminal':terminal,'certificates':tuple(certs)}
        pending['terminal']=terminal;pending['payload']=payload;pending['success_input_digest']=input_digest
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
            self._validate_payload_sources(db,scope,payload['admission'])
            growth=2048*len(payload['certificates'])
            if growth!=row['reserved_bytes']:_fail('history_reservation_changed')
            for observation_id,certificate_id,vector,digest in payload['certificates']:
                changed=db.execute(f'''UPDATE {CERTIFICATES} SET evidence_kind='vector',certificate_id=?,vector=?,vector_sha256=?,charge=charge+2048
                    WHERE attempt=? AND observation_id=? AND evidence_kind='pending' ''',
                    (certificate_id,vector,digest,token,observation_id)).rowcount
                if changed!=1:_fail('history_observation_changed')
            _change_usage(db,scope['scope_seq'],retained=growth,reserved=-row['reserved_bytes'],staging=-row['staging_bytes'])
            db.execute(f'''UPDATE {ATTEMPTS} SET state='success',terminal=?,terminal_sha256=?,reserved_bytes=0,staging_bytes=0 WHERE token=?''',
                (payload['terminal'],_hash(payload['terminal']),token))
            for fact in payload['admission']['sources']:
                db.execute(f'UPDATE {SOURCES} SET vector_count=vector_count+? WHERE scope_seq=? AND version=?',
                    (len(payload['certificates']),scope['scope_seq'],fact['version']))
            if scope['history_revision']>=MAX_REVISION:_fail('capacity_exhausted')
            db.execute(f"UPDATE {SCOPES} SET history_revision=history_revision+1,vector_count=vector_count+?,current_slot=NULL,state='pending' WHERE scope_seq=?",
                (len(payload['certificates']),scope['scope_seq']))
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
        if _raw(bare,524288)!=pending['batch_raw']:_fail('history_batch_changed')
        payload=self._prepare_success(reservation,batch['nodes'],invocation_id,moss=True)
        return self._append(reservation,payload,current_check,authorize)


    def read_attempt(self,sid,rid,token):
        """Passive exact diagnostic/basis read; never grants execution authority."""
        if type(token) is not str or len(token)!=32:_fail('history_attempt_token')
        with self._database() as db:
            _live(db,sid)
            row=db.execute(f'''SELECT a.token,a.origin_kind,a.origin_id,a.state,a.invocation_id,a.terminal_sha256,typeof(a.basis) AS basis_type,length(a.basis) AS basis_bytes,
                    typeof(a.terminal) AS terminal_type,length(a.terminal) AS terminal_bytes,
                    h.history_revision,h.authority_revision,h.authority_blocked
                FROM {ATTEMPTS} a JOIN {SCOPES} h ON h.scope_seq=a.scope_seq
                WHERE a.token=? AND h.session_id=? AND h.recording_id=?''',(token,sid,rid)).fetchone()
            if row is None:return None
            if (row['basis_type']!='blob' or type(row['basis_bytes']) is not int or not 1<=row['basis_bytes']<=512
                    or row['state']=='reserved' and (row['terminal_type']!='null' or row['terminal_bytes'] is not None)
                    or row['state']!='reserved' and (row['terminal_type']!='blob' or type(row['terminal_bytes']) is not int
                        or not 1<=row['terminal_bytes']<=512)):_fail('history_terminal_capacity')
            blobs=db.execute(f'SELECT basis,terminal FROM {ATTEMPTS} WHERE token=?',(token,)).fetchone()
            basis_raw,terminal_raw=blobs['basis'],blobs['terminal']
            basis=_decode(basis_raw,512)
            if type(basis) is not dict:_fail('history_proof_changed')
            terminal=None;failure=None
            if row['state']!='reserved':
                if terminal_raw is None or _hash(terminal_raw)!=row['terminal_sha256']:_fail('history_terminal_changed')
                terminal=_decode(terminal_raw,512)
                if type(terminal) is not dict:_fail('history_terminal_changed')
                if row['state']=='failure':
                    if terminal.get('schema')==1:
                        if (set(terminal)!={'schema','outcome','reason'} or terminal['outcome']!='failure'
                                or terminal['reason'] not in _REASONS):_fail('history_terminal_changed')
                    else:
                        from .recording_speaker_link_store import validate_failure
                        failure=validate_failure(terminal_raw)
                elif (set(terminal)!={'schema','outcome','invocation','basis','certificates'}
                        or terminal['schema']!=1 or terminal['outcome']!='success'
                        or terminal['invocation']!=row['invocation_id'] or terminal['basis']!=_hash(basis_raw)
                        or type(terminal['certificates']) is not list or not 1<=len(terminal['certificates'])<=4
                        or any(type(value) is not str or len(value)!=64 for value in terminal['certificates'])):
                    _fail('history_terminal_changed')
            return {'token':token,'origin_kind':row['origin_kind'],'origin_id':row['origin_id'],'state':row['state'],
                'invocation_id':row['invocation_id'],'basis':basis,'terminal':terminal,'terminal_sha256':row['terminal_sha256'],
                'failure':failure,'terminal_bytes':terminal_raw,'basis_bytes':basis_raw,'execution_authorized':False,
                'audit_only':True,'history_revision':row['history_revision'],'authority_revision':row['authority_revision'],
                'authority_blocked':row['authority_blocked']}

    def release_attempt(self,token):
        """Positive terminal acknowledgement only; no replay authority retained."""
        with self._memory_lock:self._frozen.pop(token,None)

    @staticmethod
    def _token(scope):
        from .recording_speaker_history_compare import SnapshotToken
        return SnapshotToken(scope['scope_id'],POLICY_SHA256,scope['space_id'],scope['history_revision'],
            scope['authority_revision'],scope['observation_count'],scope['witness_count'],scope['vector_count'])

    def _build_current(self,db,build):
        scope=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?',(build['scope_seq'],)).fetchone()
        if (scope is None or scope['build_token']!=build['build_token'] or scope['authority_blocked'] is not None
                or self._token(scope)!=build['token'] or scope['source_revision']!=build['source_revision']):_fail('history_snapshot_changed')
        if db.execute(f"SELECT 1 FROM {ATTEMPTS} WHERE scope_seq=? AND state='reserved' LIMIT 1",(scope['scope_seq'],)).fetchone():_fail('history_pending')
        _live(db,scope['session_id'])
        return scope

    def begin_comparison(self,after_scope=None):
        """Bounded dirty-header discovery. Does not score or read full evidence."""
        if not self.enabled:return None
        with self._database(write=True) as db:
            cursor=db.execute(f'SELECT compare_cursor FROM {SCOPES} WHERE scope_seq=0').fetchone()[0] if after_scope is None else after_scope
            predicate=f"scope_seq>0 AND authority_blocked IS NULL AND state NOT IN ('none','unknown_rival') AND observation_count>0 AND NOT EXISTS (SELECT 1 FROM {ATTEMPTS} a WHERE a.scope_seq={SCOPES}.scope_seq AND a.state='reserved')"
            scope=db.execute(f'SELECT * FROM {SCOPES} WHERE {predicate} AND scope_seq>? ORDER BY scope_seq LIMIT 1',(cursor,)).fetchone()
            if scope is None:scope=db.execute(f'SELECT * FROM {SCOPES} WHERE {predicate} AND scope_seq<=? ORDER BY scope_seq LIMIT 1',(cursor,)).fetchone()
            if scope is None:return None
            # Restart abandons an incomplete projection, never observations.
            slot=1-scope['current_slot'] if scope['current_slot'] is not None else 0
            old=db.execute(f'SELECT charge,observation_count,complete FROM {DECISIONS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],slot)).fetchone()
            abandoned=0 if old is None or old['complete'] else 66560+1920*old['observation_count']-384-old['charge']
            if old is not None:
                db.execute(f'DELETE FROM {ROWS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],slot))
                db.execute(f'DELETE FROM {DECISIONS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],slot))
                _change_usage(db,scope['scope_seq'],retained=-old['charge'],reserved=-abandoned)
            token=self._token(scope);build_token=uuid.uuid4().hex
            maximum=66560+1920*scope['observation_count']-384
            current=dict(scope);current['retained_bytes']-=old['charge'] if old is not None else 0
            current['reserved_bytes']-=abandoned
            _admit(db,current,maximum)
            header=_raw({'schema':1,'token':token.document(),'boot_id':BOOT_ID,'state':'building'},65536)
            # Reserve entire possible projection before the first row is written.
            _change_usage(db,scope['scope_seq'],retained=len(header)+1024,reserved=maximum-len(header)-1024)
            db.execute(f'INSERT INTO {DECISIONS}(scope_seq,slot,build_token,history_revision,authority_revision,observation_count,vector_count,witness_count,header,charge) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (scope['scope_seq'],slot,build_token,scope['history_revision'],scope['authority_revision'],scope['observation_count'],scope['vector_count'],scope['witness_count'],header,len(header)+1024))
            db.execute(f'UPDATE {SCOPES} SET building_slot=?,build_token=?,state=\'pending\' WHERE scope_seq=?',(slot,build_token,scope['scope_seq']))
        return {'token':token,'scope_seq':scope['scope_seq'],'session_id':scope['session_id'],'slot':slot,
            'build_token':build_token,'maximum':maximum,'deadline':self.clock()+COMPARE_SECONDS,'source_after':'',
            'source_pages_complete':False,'source_revision':scope['source_revision'],'validated_source_count':0,'all_sources_retired':True,'reserved_remaining':maximum-len(header)-1024}

    def active_source_page(self,build,after=0):
        cursor='' if after==0 else after
        with self._database() as db:
            self._build_current(db,build)
            values=db.execute(f'''SELECT s.*,o.body AS receipt FROM {SOURCES} s LEFT JOIN {OBJECTS} o
                ON o.scope_seq=s.scope_seq AND o.kind='receipt' AND o.digest=lower(hex(s.receipt_digest))
                WHERE s.scope_seq=? AND s.version>? ORDER BY s.version LIMIT 33''',(build['scope_seq'],cursor)).fetchall()
            rows=[_source_document(x) for x in values[:32]]
        if sum(len(x['receipt'] or b'')+2048 for x in rows)+2048>PAGE_BYTES:_fail('history_page_capacity')
        return {'rows':rows,'after':after,'next_after':rows[-1]['version'] if rows else after,'complete':len(values)<=32}

    def revalidate_active_sources(self,build,page,checkpoint=lambda:True):
        """Bounded owner-thread receipt checks; passive reads never call this."""
        if page['after']!=build['source_after'] and not (page['after']==0 and build['source_after']==''):_fail('history_source_cursor')
        from .native_owned_cell_store import Receipt,Budget
        owner=self._success_owner
        source=getattr(owner,'cell_source',None)
        if source is None:_fail('history_source_authority_missing')
        for row in page['rows']:
            if not checkpoint():_fail('history_comparison_cancelled')
            receipt=Receipt.decode(row['receipt'])
            if receipt.receipt_id!=row['receipt_digest']:_fail('history_proof_changed')
            if row['state']=='retired':
                marker=_decode(row['marker'],1024)
                if marker['version']!=row['version'] or marker['admission_revision']!=row['admission_revision'] or marker['observation_count']!=row['observation_count'] or marker['vector_count']!=row['vector_count']:
                    _fail('history_retirement_changed')
            elif row['state']=='active':
                budget=Budget();budget.seconds=.25
                with budget.phase():
                    if not source.owner.authority.receipt_valid(receipt,budget):_fail('history_source_unknown')
            else:_fail('history_source_revoked')
            if not checkpoint():_fail('history_comparison_cancelled')
            with self._database() as db:
                self._build_current(db,build)
                fact=self._source_fact(db,receipt,row['required_end'])
                if fact['version']!=row['version']:_fail('history_source_changed')
            if not checkpoint():_fail('history_comparison_cancelled')
            build['validated_source_count']+=1
            build['all_sources_retired'] &= row['state']=='retired'
        build['source_after']=page['next_after'];build['source_pages_complete']=page['complete']
        return True

    def observation_page(self,build,after=0):
        from .recording_speaker_history_compare import Observation
        with self._database() as db:
            self._build_current(db,build)
            # Admit SQL projection before copying headers/vectors. Scalar length
            # census plus row/column framing is included, not only vector bytes.
            lengths=db.execute(f'''SELECT sequence,length(header),COALESCE(length(vector),0) FROM {CERTIFICATES}
                WHERE scope_seq=? AND sequence>? ORDER BY sequence LIMIT 32''',(build['scope_seq'],after)).fetchall()
            projected=4096+len(lengths)*256;count=0
            for _,header_bytes,vector_bytes in lengths:
                amount=header_bytes+vector_bytes+512
                if projected+amount>PAGE_BYTES:break
                projected+=amount;count+=1
            found=db.execute(f'''SELECT sequence,observation_id,invocation_id,evidence_kind,certificate_id,header,vector,vector_sha256 FROM {CERTIFICATES}
                WHERE scope_seq=? AND sequence>? ORDER BY sequence LIMIT ?''',(build['scope_seq'],after,count)).fetchall()
        rows=[];used=len(encoded({'token':build['token'].document(),'after':after,'complete':False,'count':32}))+4
        for row in found:
            h=_decode(row['header'],2048)
            if row['evidence_kind']=='pending':_fail('history_pending')
            if row['evidence_kind']=='vector' and _hash(row['vector'])!=row['vector_sha256']:_fail('history_vector_changed')
            value={k:h[k] for k in ('node_id','origin_kind','source_key','pcm_hash','float_hash','attributions','spans')}
            value.update(sequence=row['sequence'],observation_id=row['observation_id'],invocation_id=row['invocation_id'],
                evidence_kind=row['evidence_kind'],certificate_id=row['certificate_id'],vector=row['vector'],
                vector_sha256=row['vector_sha256'],refs=((h['ref_kind'],h['ref_key']),))
            observation=Observation.from_record(value)
            size=8+len(encoded(observation.metadata()))+len(row['vector'] or b'')
            if used+size>PAGE_BYTES:break
            used+=size;rows.append(value)
        next_after=after+len(rows);complete=next_after==build['token'].observation_count
        if not rows and not complete:_fail('history_observation_page_missing')
        return {'rows':rows,'next_after':next_after,'complete':complete}

    def witness_page(self,build,after=0):
        total=build['token'].witness_count
        if after==total:return {'rows':[],'next_after':after,'complete':True}
        with self._database() as db:
            self._build_current(db,build)
            attempt=db.execute(f'''SELECT token,basis,witness_start,witness_count FROM {ATTEMPTS}
                WHERE scope_seq=? AND state!='reserved' AND witness_count>0 AND witness_start<=?
                ORDER BY witness_start DESC LIMIT 1''',(build['scope_seq'],after)).fetchone()
            if attempt is None or not attempt['witness_start']<=after<attempt['witness_start']+attempt['witness_count']:_fail('history_witness_missing')
            basis=_decode(attempt['basis'],512)
            if basis['kind']=='moss_result':
                raw=db.execute(f"SELECT body FROM {OBJECTS} WHERE scope_seq=? AND kind='proof' AND digest=?",(build['scope_seq'],basis['proof_digest'])).fetchone()
                if raw is None:_fail('history_proof_missing')
                # A separately bounded proof object may exceed a public page.
                proof=_decode(raw[0],524288)
                witnesses=proof['witnesses']
            else:
                from .recording_primary_provenance import TABLE as PACKETS
                ref=db.execute(f"SELECT object_key,digest FROM {REFS} WHERE attempt=? AND kind='packet' ORDER BY object_key LIMIT 1",(attempt['token'],)).fetchone()
                if ref is None or ref['digest']!=basis['packet_digests'][0]:_fail('history_proof_missing')
                raw=db.execute(f'SELECT body,digest FROM {PACKETS} WHERE sequence=?',(int(ref['object_key']),)).fetchone()
                if raw is None or _hash(raw[0])!=raw[1]:_fail('history_proof_missing')
                packet=_decode(raw[0],65536)
                from .recording_speaker_links import _witnesses
                from .recording_primary_probe import _namespace
                turns=[{'label':packet['labels'][t[1]],'original':t[2:4]} for t in packet['turns']]
                witnesses=_witnesses([(_hash(encoded(_namespace(packet))),turns,0,0)],raw[1])
        if len(witnesses)!=attempt['witness_count']:_fail('history_witness_changed')
        offset=after-attempt['witness_start']
        rows=[{k:x[k] for k in ('native_scope_id','labels','proof_sha256')} for x in witnesses[offset:offset+32]]
        if len(encoded({'token':build['token'].document(),'after':after,'rows':rows,'complete':False}))+4>PAGE_BYTES:_fail('history_page_capacity')
        return {'rows':rows,'next_after':after+len(rows),'complete':after+len(rows)==total}

    def write_decision_page(self,build,page):
        if page['token']!=build['token'] and page['token']!=build['token'].document():_fail('history_snapshot_changed')
        rows=page['rows']
        if len(rows)>32 or len(encoded({k:v for k,v in page.items() if k!='token'}))+1024>PAGE_BYTES:_fail('history_page_capacity')
        with self._database(write=True) as db:
            self._build_current(db,build)
            decision=db.execute(f'SELECT row_count,charge FROM {DECISIONS} WHERE scope_seq=? AND slot=?',(build['scope_seq'],build['slot'])).fetchone()
            charge=0
            for i,row in enumerate(rows,decision['row_count']+1):
                if row['ordinal']!=i:_fail('history_decision_cursor')
                raw=_raw(row['body'],128 if row['kind']=='merge' else 512);amount=len(raw)+256;charge+=amount
                db.execute(f'INSERT INTO {ROWS} VALUES(?,?,?,?,?,?,?,?)',(build['scope_seq'],build['slot'],i,row['kind'],row['ref_kind'],row['ref_key'],raw,amount))
            if decision['charge']+charge>build['maximum']:_fail('history_projection_capacity')
            _change_usage(db,build['scope_seq'],retained=charge,reserved=-charge)
            db.execute(f'UPDATE {DECISIONS} SET row_count=row_count+?,charge=charge+? WHERE scope_seq=? AND slot=?',(len(rows),charge,build['scope_seq'],build['slot']))
        build['reserved_remaining']-=charge

    def publish_comparison(self,build,decision_counts):
        if not build['source_pages_complete']:_fail('history_sources_incomplete')
        with self._database(write=True) as db:
            scope=self._build_current(db,build)
            actual=dict(db.execute(f'SELECT kind,COUNT(*) FROM {ROWS} WHERE scope_seq=? AND slot=? GROUP BY kind',(scope['scope_seq'],build['slot'])).fetchall())
            if actual.get('annotation',0)!=scope['observation_count'] or actual.get('merge',0)>max(0,scope['observation_count']-1) or actual.get('round',0)>scope['observation_count']:
                _fail('history_decision_incomplete')
            expected=decision_counts
            for kind in ('annotation','merge','round'):
                target=expected.get(kind,expected.get(kind+'s',0))
                if target!=actual.get(kind,0):_fail('history_decision_incomplete')
            if build['validated_source_count']!=scope['source_count']:_fail('history_source_changed')
            old=db.execute(f'SELECT header,charge FROM {DECISIONS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],build['slot'])).fetchone()
            remaining=build['maximum']-old['charge']
            header=_raw({'schema':1,'token':build['token'].document(),'boot_id':BOOT_ID,'state':'complete',
                'all_sources_retired':build['all_sources_retired'],'source_revision':scope['source_revision'],'counts':actual},65536)
            delta=len(header)-len(old['header'])
            if delta>remaining:_fail('history_projection_capacity')
            _change_usage(db,scope['scope_seq'],retained=delta,reserved=-remaining)
            db.execute(f'UPDATE {DECISIONS} SET complete=1,header=?,charge=charge+? WHERE scope_seq=? AND slot=?',(header,delta,scope['scope_seq'],build['slot']))
            db.execute(f"UPDATE {SCOPES} SET current_slot=?,building_slot=NULL,build_token=NULL,state='none' WHERE scope_seq=?",(build['slot'],scope['scope_seq']))
            db.execute(f'UPDATE {SCOPES} SET compare_cursor=? WHERE scope_seq=0',(scope['scope_seq'],))
        build['reserved_remaining']=0
        return {'saved':True}

    def finish_comparison(self,build,reason):
        with self._database(write=True) as db:
            scope=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?',(build['scope_seq'],)).fetchone()
            if scope is None or scope['build_token']!=build['build_token']:return False
            decision=db.execute(f'SELECT charge,observation_count,complete FROM {DECISIONS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],build['slot'])).fetchone()
            if decision is not None:
                db.execute(f'DELETE FROM {ROWS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],build['slot']))
                db.execute(f'DELETE FROM {DECISIONS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],build['slot']))
                remaining=0 if decision['complete'] else 66560+1920*decision['observation_count']-384-decision['charge']
                _change_usage(db,scope['scope_seq'],retained=-decision['charge'],reserved=-remaining)
            db.execute(f'UPDATE {SCOPES} SET building_slot=NULL,build_token=NULL,state=? WHERE scope_seq=?',(_reason(reason),scope['scope_seq']))
            db.execute(f'UPDATE {SCOPES} SET compare_cursor=? WHERE scope_seq=0',(scope['scope_seq'],))
        build['reserved_remaining']=0
        return True


    @staticmethod
    def _retirement_unreserved(db,scope_seq,chunk_id):
        if db.execute(f'''SELECT 1 FROM {DEPENDENCIES} d JOIN {ATTEMPTS} a ON a.token=d.attempt
            WHERE d.scope_seq=? AND d.kind='source' AND d.dependency_key=? AND a.state='reserved' LIMIT 1''',
            (scope_seq,chunk_id)).fetchone():_fail('history_source_busy')

    def prepare_retirement(self,sid,rid,source_version,*,policy_id='explicit-verified-retirement-v1'):
        """Begin a bounded, explicit retention operation; grants no unlink."""
        owner=self._success_owner
        if owner is None or not owner._history_source_quiescent(source_version):_fail('history_source_busy')
        with self._database() as db:
            scope=self._scope(db,sid,rid)
            if scope is None or scope['authority_blocked'] is not None:_fail('history_authority_changed')
            source=db.execute(f'SELECT * FROM {SOURCES} WHERE scope_seq=? AND version=?',(scope['scope_seq'],source_version)).fetchone()
            if source is None or source['state']!='active' or source['admission_revision']==0:_fail('history_retirement_unavailable')
            self._retirement_unreserved(db,scope['scope_seq'],source['chunk_id'])
            source=_source_document(source)
            raw=db.execute(f"SELECT body FROM {OBJECTS} WHERE scope_seq=? AND kind='receipt' AND digest=?",(scope['scope_seq'],source['receipt_digest'])).fetchone()
            if raw is None:_fail('history_proof_missing')
            fact=self._source_fact(db,raw[0],source['required_end'])
            if fact['version']!=source_version:_fail('history_source_changed')
            # All path aliases must be this exact version; no shared active unlink.
            aliases=db.execute(f'''SELECT 1 FROM chunks c LEFT JOIN {SOURCES} s ON s.chunk_id=c.id
                WHERE c.path=? AND (s.version IS NULL OR s.scope_seq!=? OR s.version!=?) LIMIT 1''',
                (fact['path'],scope['scope_seq'],source_version)).fetchone()
            if aliases is not None:_fail('history_active_source_alias')
        from .native_owned_cell_store import Receipt,Budget
        with Budget().phase() as budget:
            if not owner.cell_source.owner.authority.receipt_valid(Receipt.decode(raw[0]),budget):_fail('history_source_unknown')
        prepared={'token':uuid.uuid4().hex,'scope_seq':scope['scope_seq'],'sid':sid,'rid':rid,'source':dict(source),'fact':fact,
            'authority_revision':scope['authority_revision'],'source_revision':scope['source_revision'],'policy_id':policy_id,
            'after_attempt':'','attempt':None,'after_ref':('', ''),'set_hash':hashlib.sha256(),'observation_count':0,'vector_count':0,'basis_count':0,
            'proof_count':0,'complete':False,'marker':None,'owner':owner}
        if len(policy_id.encode('utf-8'))>64:_fail('history_retirement_policy')
        if getattr(self,'_retirement',None) is not None:_fail('history_retirement_busy')
        self._retirement=prepared
        return prepared

    def retirement_page(self,prepared):
        """One bounded basis/certificate or proof-edge checkpoint, never a scan."""
        if getattr(self,'_retirement',None) is not prepared:_fail('history_retirement_owner')
        if prepared['complete']:return {'complete':True}
        if not prepared['owner']._history_source_quiescent(prepared['source']['version']):_fail('history_source_busy')
        with self._database() as db:
            source=db.execute(f'''SELECT s.*,h.authority_revision,h.authority_blocked FROM {SOURCES} s JOIN {SCOPES} h ON h.scope_seq=s.scope_seq
                WHERE s.scope_seq=? AND s.version=?''',(prepared['scope_seq'],prepared['source']['version'])).fetchone()
            if (source is None or source['state']!='active' or source['authority_blocked'] is not None
                    or source['authority_revision']!=prepared['authority_revision']
                    or any(source[k]!=prepared['source'][k] for k in ('admission_revision','observation_count','vector_count','required_end'))):_fail('history_retirement_changed')
            self._retirement_unreserved(db,prepared['scope_seq'],source['chunk_id'])
            if prepared['attempt'] is None:
                attempt=db.execute(f'''SELECT a.token,a.basis,a.state FROM {DEPENDENCIES} d JOIN {ATTEMPTS} a ON a.token=d.attempt
                    WHERE d.scope_seq=? AND d.kind='source' AND d.dependency_key=? AND d.attempt>? ORDER BY d.attempt LIMIT 1''',
                    (prepared['scope_seq'],source['chunk_id'],prepared['after_attempt'])).fetchone()
                if attempt is None:
                    if (prepared['observation_count']!=source['observation_count'] or prepared['vector_count']!=source['vector_count']
                            or prepared['basis_count']!=source['admission_revision']):_fail('history_retirement_count')
                    prepared['complete']=True;return {'complete':True}
                certs=db.execute(f'SELECT observation_id,invocation_id,evidence_kind,certificate_id,header,vector,vector_sha256 FROM {CERTIFICATES} WHERE scope_seq=? AND attempt=? ORDER BY sequence LIMIT 5',
                    (prepared['scope_seq'],attempt['token'])).fetchall()
                if len(certs)>4 or attempt['state']=='reserved':_fail('history_basis_observation_count')
                _decode(attempt['basis'],512)
                prepared['basis_count']+=1
                prepared['set_hash'].update(encoded([attempt['token'],_hash(attempt['basis'])]))
                for cert in certs:
                    header=_decode(cert['header'],2048)
                    if cert['evidence_kind']=='pending':_fail('history_pending')
                    if cert['evidence_kind']=='vector':
                        if _hash(cert['vector'])!=cert['vector_sha256']:_fail('history_vector_changed')
                        prepared['vector_count']+=1
                    prepared['set_hash'].update(encoded([attempt['token'],cert['observation_id'],cert['invocation_id'],
                        cert['evidence_kind'],cert['certificate_id'],_hash(cert['header']),cert['vector_sha256']]))
                prepared['observation_count']+=len(certs);prepared['attempt']=attempt['token'];prepared['after_ref']=('','')
            else:
                ref=db.execute(f'''SELECT kind,object_key,digest FROM {REFS} WHERE attempt=? AND (kind,object_key)>(?,?) ORDER BY kind,object_key LIMIT 1''',
                    (prepared['attempt'],*prepared['after_ref'])).fetchone()
                if ref is None:
                    prepared['after_attempt']=prepared['attempt'];prepared['attempt']=None
                    return {'complete':False}
                if ref['kind']=='packet':
                    from .recording_primary_provenance import TABLE as PACKETS
                    raw=db.execute(f'SELECT body FROM {PACKETS} WHERE sequence=? AND digest=?',(int(ref['object_key']),ref['digest'])).fetchone()
                    maximum=65536
                else:
                    raw=db.execute(f'SELECT body,refcount FROM {OBJECTS} WHERE scope_seq=? AND kind=? AND digest=?',
                        (prepared['scope_seq'],ref['kind'],ref['digest'])).fetchone();maximum=1024 if ref['kind']=='receipt' else 524288
                    count=db.execute(f'SELECT COUNT(*) FROM {REFS} INDEXED BY recording_history_ref_object_v1 WHERE scope_seq=? AND kind=? AND digest=?',
                        (prepared['scope_seq'],ref['kind'],ref['digest'])).fetchone()[0]
                    if raw is None or raw['refcount']!=count:_fail('history_refcount_changed')
                if raw is None or _hash(raw[0])!=ref['digest']:_fail('history_proof_missing')
                _decode(raw[0],maximum)
                prepared['set_hash'].update(encoded([prepared['attempt'],ref['kind'],ref['object_key'],ref['digest']]))
                prepared['proof_count']+=1;prepared['after_ref']=(ref['kind'],ref['object_key'])
        return {'complete':False}

    def commit_retirement(self,prepared):
        """Marker-before-unlink CAS. Unknown acknowledgement authorizes no unlink."""
        if getattr(self,'_retirement',None) is not prepared or not prepared['complete']:_fail('history_retirement_incomplete')
        owner=prepared['owner'];source=prepared['source'];fact=prepared['fact']
        if not owner._history_source_quiescent(source['version']):_fail('history_source_busy')
        from .native_owned_cell_store import Receipt,Budget
        with Budget().phase() as budget:
            if not owner.cell_source.owner.authority.receipt_valid(Receipt.decode(fact['receipt']),budget):_fail('history_source_unknown')
        if prepared['marker'] is None:
            prepared['marker']=_raw({'schema':1,'token':prepared['token'],'version':source['version'],'receipt':source['receipt_digest'],
                'path':fact['path_digest'],'mapping':source['mapping_digest'],'admission_revision':source['admission_revision'],
                'observation_count':source['observation_count'],'vector_count':source['vector_count'],'basis_count':source['admission_revision'],'required_end':source['required_end'],'set_digest':prepared['set_hash'].hexdigest(),
                'validation_digest':_hash(encoded([source['version'],fact['receipt'].hex(),prepared['proof_count']])),
                'authority_revision':prepared['authority_revision'],'policy':prepared['policy_id']},1024)
        marker=prepared['marker']
        # The existing publication guard excludes new source admissions while
        # quiescence and durable source counters are checked in this transaction.
        with owner.cell_source.owner._publication_guard, self._database(write=True) as db:
            if not owner._history_source_quiescent(source['version']):_fail('history_source_busy')
            scope=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?',(prepared['scope_seq'],)).fetchone()
            row=db.execute(f'SELECT * FROM {SOURCES} WHERE scope_seq=? AND version=?',(prepared['scope_seq'],source['version'])).fetchone()
            already=(row is not None and row['marker_token']==prepared['token'] and row['marker']==marker)
            if not already:
                if (scope is None or row is None or row['state']!='active' or scope['authority_blocked'] is not None
                        or scope['authority_revision']!=prepared['authority_revision']
                        or any(row[k]!=source[k] for k in ('admission_revision','observation_count','vector_count','required_end'))):_fail('history_retirement_changed')
                self._retirement_unreserved(db,prepared['scope_seq'],source['chunk_id'])
                if self._source_fact(db,fact['receipt'],source['required_end'])['version']!=source['version']:_fail('history_source_changed')
                aliases=db.execute(f'''SELECT 1 FROM chunks c LEFT JOIN {SOURCES} s ON s.chunk_id=c.id
                    WHERE c.path=? AND (s.version IS NULL OR s.scope_seq!=? OR s.version!=?) LIMIT 1''',
                    (fact['path'],scope['scope_seq'],source['version'])).fetchone()
                if aliases is not None:_fail('history_active_source_alias')
                charge=len(marker)+512
                # Projection source authority metadata changes without changing any
                # vector, competitor, alias, witness or exact comparison score.
                decision=None;new_header=None;delta=0
                if scope['current_slot'] is not None:
                    decision=db.execute(f'SELECT header FROM {DECISIONS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],scope['current_slot'])).fetchone()
                    if decision is not None:
                        header=_decode(decision['header'],65536);header['source_revision']=scope['source_revision']+1
                        header['all_sources_retired']=scope['active_sources']==1;new_header=_raw(header,65536);delta=len(new_header)-len(decision['header'])
                _admit(db,scope,charge+max(0,delta))
                db.execute(f"UPDATE {SOURCES} SET state='retired',marker=?,marker_token=?,cleanup='pending',charge=charge+? WHERE scope_seq=? AND version=?",
                    (marker,prepared['token'],charge,prepared['scope_seq'],source['version']))
                db.execute(f'UPDATE {SCOPES} SET active_sources=active_sources-1,source_revision=source_revision+1 WHERE scope_seq=?',(prepared['scope_seq'],))
                _change_usage(db,prepared['scope_seq'],retained=charge+delta)
                if new_header is not None:db.execute(f'UPDATE {DECISIONS} SET header=?,charge=charge+? WHERE scope_seq=? AND slot=?',(new_header,delta,prepared['scope_seq'],scope['current_slot']))
        # Both initial commit and exact lost-ack retry release only this owner,
        # after the transaction context has positively acknowledged closure.
        if self._retirement is prepared:self._retirement=None
        return {'acknowledged':True,'token':prepared['token'],'version':source['version'],'scope_seq':prepared['scope_seq']}

    def read_retirement(self,scope_seq,source_version,token):
        """Positive exact marker read for a later independently owned cleanup."""
        with self._database() as db:
            row=db.execute(f'''SELECT s.*,c.path,o.body AS receipt,h.authority_blocked FROM {SOURCES} s
                JOIN {SCOPES} h ON h.scope_seq=s.scope_seq JOIN chunks c ON c.id=s.chunk_id
                JOIN {OBJECTS} o ON o.scope_seq=s.scope_seq AND o.kind='receipt' AND o.digest=lower(hex(s.receipt_digest))
                WHERE s.scope_seq=? AND s.version=? AND s.marker_token=?''',(scope_seq,source_version,token)).fetchone()
            if row is None or row['state']!='retired' or row['authority_blocked'] is not None:_fail('history_retirement_missing')
            self._retirement_unreserved(db,scope_seq,row['chunk_id'])
            aliases=db.execute(f'''SELECT 1 FROM chunks c LEFT JOIN {SOURCES} s ON s.chunk_id=c.id
                WHERE c.path=? AND (s.version IS NULL OR s.scope_seq!=? OR s.version!=?) LIMIT 1''',
                (row['path'],scope_seq,source_version)).fetchone()
            if aliases is not None:_fail('history_active_source_alias')
            marker=_decode(row['marker'],1024)
            if marker['token']!=token or marker['version']!=source_version or _hash(row['path'].encode())!=marker['path']:_fail('history_retirement_changed')
            return _source_document(row)

    def record_retirement_cleanup(self,scope_seq,source_version,token,state,reason=None):
        if state not in ('pending','done','failed','unknown'):_fail('history_cleanup_state')
        reason=reason if reason in ('io_unavailable','path_changed','storage_unknown','other') else None
        with self._database(write=True) as db:
            changed=db.execute(f'''UPDATE {SOURCES} SET cleanup=?,cleanup_reason=?,cleanup_attempts=MIN(2147483647,cleanup_attempts+1)
                WHERE scope_seq=? AND version=? AND marker_token=? AND state='retired' ''',
                (state,reason,scope_seq,source_version,token)).rowcount
            if changed!=1:_fail('history_retirement_changed')
        return True


def primary_identity(saved,node,space_id):
    return {'sid':saved['header']['sid'],'rid':saved['header']['rid'],'origin_kind':'primary_commit',
        'origin_id':saved['header']['digest'],'node_sha256':_hash(encoded(node)),'space_id':space_id}


def moss_identity(sid,rid,batch):
    return {'sid':sid,'rid':rid,'origin_kind':'moss_result','origin_id':batch['job_id'],
        'result_sha256':batch['result_sha256'],'batch_sha256':batch['batch_sha256'],'space_id':batch['space_id']}


def read_session_revision(db,sid):
    """Passive scalar headers in the caller snapshot, including authority fences."""
    from . import transcript_contract as contract
    rows=[tuple(row) for row in db.execute(f'''SELECT scope_id,history_revision,authority_revision,source_revision,
        authority_blocked,state,current_slot,building_slot FROM {SCOPES} INDEXED BY recording_history_session_v1
        WHERE session_id=? ORDER BY scope_seq LIMIT ?''',(sid,contract.MAX_BINDINGS+1))]
    # This is a read-admission ceiling, never a retained-history/scope count cap.
    # Refuse the whole cursor rather than bind it to a truncated revision set.
    if len(rows)>contract.MAX_BINDINGS:contract.fail('unresolved_capacity')
    raw=encoded(rows)
    if len(raw)>contract.MAX_BINDING_BYTES:contract.fail('unresolved_capacity')
    return _hash(raw)


def read_saved_for_refs(db,sid,rid,refs,*,byte_limit=PAGE_BYTES):
    """Indexed exact refs only. Incomplete optional reads never return a positive.

    The complete matching stream for each ref participates, including duplicates
    on later pages. No recovery, filesystem check, comparison or model starts.
    """
    scope=db.execute(f'SELECT * FROM {SCOPES} WHERE session_id=? AND recording_id=?',(sid,rid)).fetchone()
    if scope is None:return None
    database_path=db.execute('PRAGMA database_list').fetchone()[2]
    with _AUTHORITY_UNKNOWN_LOCK:
        authority_unknown=_AUTHORITY_UNKNOWN_OVERFLOW or bool(_AUTHORITY_UNKNOWN_DATABASES.get(database_path))
    result={'history_policy':True,'scope_id':scope['scope_id'],'state':'links_stale','history_status':'pending',
        'revision':scope['history_revision'],'decision_revision':-1,'policy_sha256':POLICY_SHA256,
        'space_id':scope['space_id'],'annotations':[],'authority_revision':scope['authority_revision'],
        'observation_count':scope['observation_count'],'vector_count':scope['vector_count']}
    if authority_unknown:
        result.update(history_status='pending',reason='source_authority_unknown');return result
    if scope['authority_blocked'] is not None:
        result.update(history_status='invalid',reason=scope['authority_blocked']);return result
    if scope['state']=='unknown_rival':
        result.update(reason='unknown_rival');return result
    if db.execute(f"SELECT 1 FROM {ATTEMPTS} WHERE scope_seq=? AND state='reserved' LIMIT 1",(scope['scope_seq'],)).fetchone():
        result.update(reason='pending');return result
    if scope['current_slot'] is None:return result
    decision=db.execute(f'SELECT * FROM {DECISIONS} WHERE scope_seq=? AND slot=?',(scope['scope_seq'],scope['current_slot'])).fetchone()
    if decision is None:return result
    header=_decode(decision['header'],65536);result['decision_revision']=decision['history_revision']
    current=(decision['complete']==1 and decision['history_revision']==scope['history_revision'] and
        decision['authority_revision']==scope['authority_revision'] and decision['observation_count']==scope['observation_count'] and
        decision['vector_count']==scope['vector_count'] and header.get('source_revision')==scope['source_revision'])
    retired=header.get('all_sources_retired') is True and scope['active_sources']==0
    if not current:return result
    if header.get('boot_id')!=BOOT_ID and not retired:
        result.update(history_status='historical',reason='active_source_revalidation_pending');return result
    result.update(state='source_scoped',history_status='current',
        validation_semantics='validated_at_admission_source_version_verified_retired' if retired else 'validated_at_decision_revision')
    used=len(encoded({k:v for k,v in result.items() if k!='annotations'}))+1024
    annotations=[]
    for ref_kind,ref_key in dict.fromkeys(refs):
        after=0
        while True:
            lengths=db.execute(f'''SELECT ordinal,length(body) FROM {ROWS} INDEXED BY recording_history_exact_ref_v1
                WHERE scope_seq=? AND slot=? AND ref_kind=? AND ref_key=? AND ordinal>? ORDER BY ordinal LIMIT 32''',
                (scope['scope_seq'],scope['current_slot'],ref_kind,ref_key,after)).fetchall()
            if not lengths:break
            prospective=sum(x[1]+256 for x in lengths)+256
            if used+prospective>byte_limit:
                result.update(state='capacity_exhausted',history_status='pending',reason='saved_decision_bytes',complete=False,annotations=[])
                return result
            used+=prospective
            found=db.execute(f'''SELECT ordinal,body FROM {ROWS} INDEXED BY recording_history_exact_ref_v1
                WHERE scope_seq=? AND slot=? AND ref_kind=? AND ref_key=? AND ordinal>? ORDER BY ordinal LIMIT 32''',
                (scope['scope_seq'],scope['current_slot'],ref_kind,ref_key,after)).fetchall()
            for row in found:
                item=_decode(row['body'],512)
                item.update(source_kind='native_primary' if ref_kind=='primary' else 'moss')
                if ref_kind=='primary':item['primary_source_ref']=ref_key
                else:
                    job,result_digest,phrase=json.loads(ref_key)
                    item.update(job_id=job,result_sha256=result_digest,phrase_index=phrase)
                annotations.append(item)
            after=found[-1]['ordinal']
            if len(found)<32:break
    result.update(annotations=annotations,complete=True,read_bytes=used)
    return result


_AUTHORITY_UNKNOWN_DATABASES={}
_AUTHORITY_UNKNOWN_LOCK=threading.RLock()
_AUTHORITY_UNKNOWN_BYTES=0
_AUTHORITY_UNKNOWN_OVERFLOW=False


def notify_source_mismatch(store,chunk_ids):
    """Semantic mismatch only; ordinary owner shutdown never calls this hook."""
    global _AUTHORITY_UNKNOWN_BYTES,_AUTHORITY_UNKNOWN_OVERFLOW
    path=str(store.path.resolve())
    ids=tuple(dict.fromkeys(chunk_ids))
    if not ids or len(ids)>128 or any(type(cid) is not str or not 1<=len(cid)<=64 for cid in ids):
        _fail('history_dependency_capacity')
    # The newest per-source notification owns the fence. A later precise write
    # may cover older unknown notifications, but an earlier commit returning
    # late cannot clear a newer notification, even for the exact same source.
    token=object()
    with _AUTHORITY_UNKNOWN_LOCK:
        pending=_AUTHORITY_UNKNOWN_DATABASES.get(path)
        new=[cid for cid in ids if pending is None or cid not in pending]
        # Bounded controller metadata has a fixed 512 KiB reservation in both
        # shared 8 MiB staging admission guards. Overflow keeps positive reads closed
        # until restart/revalidation rather than allocating or dropping a fence.
        extra=(len(path.encode('utf-8'))+512 if pending is None else 0)+sum(len(cid.encode('utf-8'))+256 for cid in new)
        if _AUTHORITY_UNKNOWN_BYTES+extra>CONTROLLER_METADATA_BYTES:
            _AUTHORITY_UNKNOWN_OVERFLOW=True
        else:
            pending=_AUTHORITY_UNKNOWN_DATABASES.setdefault(path,{})
            _AUTHORITY_UNKNOWN_BYTES+=extra
            for cid in ids:pending[cid]=token
    try:
        with MossResultStore(store)._database(write=True) as db:invalidate_sources(db,ids)
    except BaseException:
        return False  # Unknown is not a durable revocation claim.
    with _AUTHORITY_UNKNOWN_LOCK:
        pending=_AUTHORITY_UNKNOWN_DATABASES.get(path)
        if pending is not None:
            for cid in ids:
                if pending.get(cid) is token:
                    pending.pop(cid);_AUTHORITY_UNKNOWN_BYTES-=len(cid.encode('utf-8'))+256
            if not pending:
                _AUTHORITY_UNKNOWN_DATABASES.pop(path,None)
                _AUTHORITY_UNKNOWN_BYTES-=len(path.encode('utf-8'))+512
    return True
