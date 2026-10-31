"""Fresh live payload placement. Logical history and original source files stay hot.

One immutable complete-source/proof bundle per completed unit; only support PCM
and original vector BLOB placement changes. This is not whole-database backup.
"""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from types import MappingProxyType
from dataclasses import dataclass
from collections import Counter
import hashlib
import json
import math
import os
import re
import stat
import sqlite3
import struct
import threading
import time
from . import strict_archive_v2 as base
from . import strict_lifetime

POLICY = 'strict-primary-payload-v2'
FORMAT = 'strict-payload-placement-v2-turn-witness-v1'
SHARED_FORMAT = 'strict-payload-placement-v2-shared-source-v1'

HOT_CANDIDATE_SQL = "SELECT c.id FROM speaker_support p INDEXED BY strict_payload_hot_support\n CROSS JOIN chunks c ON c.id=p.chunk_id\n CROSS JOIN recording_work w ON w.chunk_id=c.id\n CROSS JOIN recording_batches b ON b.id=w.batch_id\n CROSS JOIN stream_chunks s ON s.chunk_id=c.id\n CROSS JOIN strict_recording_control k ON k.cid=c.id\n WHERE p.status='available' AND p.pcm_bytes>0 AND p.pcm IS NOT NULL\n AND c.session_id=? AND s.stream_id=? AND (c.timeline_start+c.duration)*16000<=?\n AND c.status='completed' AND c.next_offset=c.duration AND k.state='completed' AND k.nonce IS NULL"
HOT_SUPPORT_INDEX = "CREATE INDEX IF NOT EXISTS strict_payload_hot_support ON speaker_support(chunk_id) WHERE status='available' AND pcm_bytes>0 AND pcm IS NOT NULL"

def format_for_storage(source_storage='inline'):
    if source_storage not in ('inline','shared-source-v1'):
        raise ValueError('strict_payload_source_storage')
    return FORMAT if source_storage=='inline' else SHARED_FORMAT

MAGIC = b'SPV2\x00\x01'
MAX_METADATA = base.MAX_METADATA
MAX_PAYLOAD = base.MAX_PAYLOAD
MAX_READ = base.MAX_READ
BUFFER = base.BUFFER
MAX_FRAMES = base.MAX_FRAMES
MAX_OUTCOMES = base.MAX_OUTCOMES
MAX_SEGMENTS = base.MAX_SEGMENTS
MAX_TURNS = base.MAX_TURNS
MAX_SUPPORTS = base.MAX_SUPPORTS
MAX_PARTS = 128
MAX_ROOTS = 518  # exact bounded pin/dependency/tail/probe/pending root shape
MAX_SOURCE_REFS = 208
MAX_SOURCE_FRAMES = 120*16000
MAX_OBJECTS = MAX_PARTS+MAX_SOURCE_REFS+2*MAX_SUPPORTS
BUCKET_UNITS = 16
MAX_UNIT_BYTES = len(MAGIC)+4+MAX_METADATA+MAX_PAYLOAD
MAX_BUCKET_NAMES = 2*BUCKET_UNITS
MAX_BUCKET_BYTES = 2*BUCKET_UNITS*MAX_UNIT_BYTES
_ID = re.compile(r'^[a-f0-9]{64}$')
_PENDING_PAYLOAD_OWNER = None


class PayloadIntegrityError(RuntimeError): pass
class PayloadCapacityError(RuntimeError): pass
class PayloadAdmissionBusy(RuntimeError): pass
class PayloadBeginBusy(RuntimeError): pass


def _require(ok,reason):
    if not ok:raise PayloadIntegrityError(reason)


def refuse_pending_payload():
    if _PENDING_PAYLOAD_OWNER is not None:
        owner=_PENDING_PAYLOAD_OWNER
        error=owner.original if isinstance(owner,_Owner) else owner.first_error
        raise error


def retain_bridge_unknown(store,db):
    global _PENDING_PAYLOAD_OWNER
    from .reviewed_primary_persons import _PayloadBridgeOwner
    owner=store._strict_payload_bridge_owner
    _require(type(owner) is _PayloadBridgeOwner and owner.store is store and owner.db is db,'payload_bridge_owner')
    if owner.first_error is None:owner.first_error=store._strict_original_error or store._strict_cleanup_error
    _require(isinstance(owner.first_error,BaseException),'payload_bridge_original_error')
    if _PENDING_PAYLOAD_OWNER is None:_PENDING_PAYLOAD_OWNER=owner


class PayloadBudget(base.ArchiveBudget):
    """One envelope, intersected with the original caller; never renewed per object."""
    def __init__(self,caller=None,check=None):
        super().__init__()
        self.caller=caller
        self.caller_check=check

    def remaining_seconds(self):
        self.check()
        remaining=max(0.,self.deadline-time.monotonic())
        if self.caller_check is not None:return 0.
        if self.caller is not None:
            method=getattr(self.caller,'remaining_seconds',None)
            if not callable(method):return 0.
            value=method()
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:return 0.
            remaining=min(remaining,value)
        return remaining

    def check(self):
        if self.caller is not None:self.caller.check()
        if self.caller_check is not None:self.caller_check()
        super().check()

    def read(self,n):
        super().read(n)
        # A native caller's projection allowance includes returned payload bytes.
        if self.caller is not None:
            charge=getattr(self.caller,'projection',None)
            if charge is not None:charge(n)

    def projection(self,n):
        self.read(n);self.metadata(n)

    def progress(self):
        if self.caller is not None and self.caller.progress():return 1
        return super().progress()


class _Owner(base._Owner):
    def __init__(self,store,budget):
        super().__init__(store,budget)
        self.transition=set()
        self.locators={}
        self.transition_db=None
        self.snapshot_db=None
        self.resolver_active=False

    def uncertain(self,error):
        global _PENDING_PAYLOAD_OWNER
        self.remember(error)
        if _PENDING_PAYLOAD_OWNER is None:_PENDING_PAYLOAD_OWNER=self
        self.unknown=True
        if self.store._strict_original_error is None:self.store._strict_original_error=self.original
        self.store.strict_cleanup_unknown(self,kind='archive',error=error)


def schema_statements(*, lifetime_records=2056, source_storage='inline'):
    from .strict_lifetime import validate
    limit=validate(lifetime_records)
    statements = [
    '''CREATE TABLE IF NOT EXISTS strict_payload_policy (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), policy TEXT NOT NULL CHECK(policy='strict-primary-payload-v2'),
      format TEXT NOT NULL CHECK(format='strict-payload-placement-v2-turn-witness-v1'))''',
    '''CREATE TABLE IF NOT EXISTS strict_payload_unit (
      sequence INTEGER PRIMARY KEY CHECK(sequence>0 AND sequence<=2056), unit_id TEXT NOT NULL UNIQUE CHECK(length(unit_id)=64),
      cid TEXT NOT NULL UNIQUE CHECK(length(cid)=32), store_key TEXT NOT NULL CHECK(length(store_key)=64),
      evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
      manifest_bytes INTEGER NOT NULL CHECK(typeof(manifest_bytes)='integer' AND manifest_bytes BETWEEN 1 AND 4194304), bucket INTEGER NOT NULL CHECK(typeof(bucket)='integer' AND bucket=(sequence-1)/16),
      state TEXT NOT NULL CHECK(state IN ('reserved','published','committed')), manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64),
      bytes INTEGER NOT NULL CHECK(typeof(bytes)='integer' AND bytes>0 AND bytes<=37748746), file_sha256 TEXT, signature TEXT,
      CHECK((state='reserved' AND file_sha256 IS NULL AND signature IS NULL) OR
        (state IN ('published','committed') AND length(file_sha256)=64 AND length(signature) BETWEEN 1 AND 512)))''',
    '''CREATE TABLE IF NOT EXISTS strict_payload_object (
      kind TEXT NOT NULL CHECK(kind IN ('pcm','vector')), owner_id TEXT NOT NULL CHECK(length(owner_id)=32),
      unit_id TEXT NOT NULL REFERENCES strict_payload_unit(unit_id), object_id TEXT NOT NULL CHECK(length(object_id) BETWEEN 1 AND 80),
      bytes INTEGER NOT NULL CHECK(typeof(bytes)='integer' AND bytes>0 AND bytes<=1280000), sha256 TEXT NOT NULL CHECK(length(sha256)=64),
      offset INTEGER NOT NULL CHECK(typeof(offset)='integer' AND offset>=10), PRIMARY KEY(kind,owner_id))''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_policy_update BEFORE UPDATE ON strict_payload_policy
      BEGIN SELECT RAISE(ABORT,'strict_payload_policy_immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_policy_delete BEFORE DELETE ON strict_payload_policy
      BEGIN SELECT RAISE(ABORT,'strict_payload_policy_retained'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_unit_update BEFORE UPDATE ON strict_payload_unit BEGIN
      SELECT CASE WHEN NEW.sequence IS NOT OLD.sequence OR NEW.unit_id IS NOT OLD.unit_id OR NEW.cid IS NOT OLD.cid
        OR NEW.bucket IS NOT OLD.bucket OR NEW.manifest_sha256 IS NOT OLD.manifest_sha256 OR NEW.bytes IS NOT OLD.bytes
        OR NEW.store_key IS NOT OLD.store_key OR NEW.evidence_sha256 IS NOT OLD.evidence_sha256
        OR NEW.manifest_bytes IS NOT OLD.manifest_bytes OR strict_payload_transition('unit',OLD.unit_id)!=1
        OR NOT ((OLD.state='reserved' AND NEW.state='published') OR (OLD.state='published' AND NEW.state='committed'
          AND NEW.file_sha256 IS OLD.file_sha256 AND NEW.signature IS OLD.signature))
        THEN RAISE(ABORT,'strict_payload_unit_transition') END; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_unit_delete BEFORE DELETE ON strict_payload_unit
      BEGIN SELECT RAISE(ABORT,'strict_payload_unit_retained'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_object_insert BEFORE INSERT ON strict_payload_object BEGIN
      SELECT CASE WHEN strict_payload_locator(NEW.kind,NEW.owner_id,NEW.unit_id,NEW.object_id,NEW.bytes,NEW.sha256,NEW.offset)!=1 OR NOT EXISTS(
        SELECT 1 FROM strict_payload_unit WHERE unit_id=NEW.unit_id AND state='committed'
          AND NEW.offset+NEW.bytes<=bytes) THEN RAISE(ABORT,'strict_payload_locator_unauthorized') END; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_object_update BEFORE UPDATE ON strict_payload_object
      BEGIN SELECT RAISE(ABORT,'strict_payload_locator_immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_object_delete BEFORE DELETE ON strict_payload_object
      WHEN (OLD.kind='pcm' AND EXISTS(SELECT 1 FROM speaker_support WHERE id=OLD.owner_id))
        OR (OLD.kind='vector' AND EXISTS(SELECT 1 FROM speaker_support_vectors WHERE id=OLD.owner_id))
      BEGIN SELECT RAISE(ABORT,'strict_payload_locator_retained'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_support_erasure AFTER DELETE ON speaker_support BEGIN
      DELETE FROM strict_payload_object WHERE kind='pcm' AND owner_id=OLD.id; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_vector_erasure AFTER DELETE ON speaker_support_vectors BEGIN
      DELETE FROM strict_payload_object WHERE kind='vector' AND owner_id=OLD.id; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_support_update BEFORE UPDATE ON speaker_support BEGIN
      SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id OR NEW.chunk_id IS NOT OLD.chunk_id
        OR NEW.identity_group IS NOT OLD.identity_group OR NEW.generation IS NOT OLD.generation
        OR NEW.window_start IS NOT OLD.window_start OR NEW.local_speaker_id IS NOT OLD.local_speaker_id
        OR NEW.observation_id IS NOT OLD.observation_id OR NEW.manifest IS NOT OLD.manifest
        OR NEW.manifest_sha256 IS NOT OLD.manifest_sha256 OR NEW.status IS NOT OLD.status OR NEW.pcm_bytes IS NOT OLD.pcm_bytes
        OR OLD.pcm IS NULL OR NEW.pcm IS NOT NULL OR strict_payload_transition('pcm',OLD.id)!=1
        OR NOT EXISTS(SELECT 1 FROM strict_payload_object o JOIN strict_payload_unit u USING(unit_id)
          WHERE o.kind='pcm' AND o.owner_id=OLD.id AND o.bytes=OLD.pcm_bytes
          AND o.sha256=json_extract(OLD.manifest,'$.support.pooled_pcm_sha256') AND u.state='committed')
        THEN RAISE(ABORT,'strict_payload_support_immutable') END; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_vector_update BEFORE UPDATE ON speaker_support_vectors BEGIN
      SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.support_id IS NOT OLD.support_id OR NEW.identity_group IS NOT OLD.identity_group
        OR NEW.generation IS NOT OLD.generation OR NEW.vector_sha256 IS NOT OLD.vector_sha256 OR NEW.dimension IS NOT OLD.dimension
        OR NEW.space_id IS NOT OLD.space_id OR NEW.space IS NOT OLD.space OR NEW.normalization IS NOT OLD.normalization
        OR NEW.normalized_sha256 IS NOT OLD.normalized_sha256 OR NEW.evidence_key IS NOT OLD.evidence_key
        OR NEW.retained_bytes IS NOT OLD.retained_bytes OR OLD.raw_vector IS NULL OR NEW.raw_vector IS NOT NULL
        OR strict_payload_transition('vector',OLD.id)!=1 OR NOT EXISTS(
          SELECT 1 FROM strict_payload_object o JOIN strict_payload_unit u USING(unit_id)
          WHERE o.kind='vector' AND o.owner_id=OLD.id AND o.bytes=OLD.retained_bytes
          AND o.sha256=OLD.vector_sha256 AND u.state='committed')
        THEN RAISE(ABORT,'strict_payload_vector_immutable') END; END''',
    '''CREATE TABLE IF NOT EXISTS strict_payload_core_witness (
      cid TEXT NOT NULL CHECK(length(cid)=32), core_start INTEGER NOT NULL CHECK(typeof(core_start)='integer' AND core_start>=0),
      core_end INTEGER NOT NULL CHECK(typeof(core_end)='integer' AND core_end>core_start), slot TEXT NOT NULL UNIQUE CHECK(length(slot)=64),
      status TEXT NOT NULL CHECK(status IN ('empty','unknown_only')),
      version TEXT NOT NULL CHECK(version='strict-payload-core-witness-v1'), digest TEXT NOT NULL CHECK(length(digest)=64),
      body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body) BETWEEN 1 AND 65536), PRIMARY KEY(cid,core_start))''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_witness_insert BEFORE INSERT ON strict_payload_core_witness BEGIN
      SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM strict_recording_outcome o JOIN chunks c ON c.id=o.cid
        JOIN strict_recording_control k ON k.cid=o.cid WHERE o.cid=NEW.cid AND o.start_frame=NEW.core_start
        AND o.end_frame=NEW.core_end AND o.slot=NEW.slot AND o.status=NEW.status AND o.packet IS NULL
        AND c.status='running' AND k.state='active' AND k.nonce=o.nonce AND k.source=o.source
        AND c.digest=o.source AND c.next_offset=NEW.core_start/16000.0)
        OR (SELECT COUNT(*) FROM strict_payload_core_witness)>=2056
        THEN RAISE(ABORT,'strict_payload_witness_unauthorized') END; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_witness_update BEFORE UPDATE ON strict_payload_core_witness
      BEGIN SELECT RAISE(ABORT,'strict_payload_witness_immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_payload_witness_delete BEFORE DELETE ON strict_payload_core_witness
      BEGIN SELECT RAISE(ABORT,'strict_payload_witness_retained'); END''',
    ]
    fmt=format_for_storage(source_storage)
    result=[sql.replace('2056',str(limit)).replace(FORMAT,fmt) for sql in statements]
    if source_storage!='inline' or limit!=strict_lifetime.DEFAULT_RECORDS:
        result.append(HOT_SUPPORT_INDEX)
    if source_storage=='shared-source-v1':
        from .strict_shared_sources import schema_statements as shared_schema
        result.extend(shared_schema())
    return result


def is_live_db(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='strict_payload_policy' AND type='table'").fetchone():return False
    rows=db.execute('SELECT singleton,policy,format FROM strict_payload_policy LIMIT 2').fetchall()
    _require(len(rows)==1 and tuple(rows[0]) in ((1,POLICY,FORMAT),(1,POLICY,SHARED_FORMAT)),'payload_policy_marker')
    return True


def admit_payload_schema(db, *, lifetime_records=2056, source_storage='inline'):
    from . import speaker_support,support_vectors
    from .strict_recording import _canonical_ddl
    for module in (speaker_support,support_vectors):
        for sql in module.schema_statements(policy=POLICY):
            match=re.match(r'CREATE (TABLE|TRIGGER|INDEX) IF NOT EXISTS ([a-z_]+)',sql)
            _require(match is not None,'payload_schema_definition')
            row=db.execute('SELECT type,sql FROM sqlite_master WHERE name=?',(match[2],)).fetchone()
            _require(row is not None and row[0]==match[1].lower() and type(row[1]) is str
                     and _canonical_ddl(row[1])==_canonical_ddl(sql),'payload_body_schema_mismatch')
    _require(is_live_db(db),'payload_policy_marker')


def bind_connection(store,db):
    def transition(kind,owner_id):
        owner=store._strict_archive_owner
        return int(type(owner) is _Owner and owner.thread==threading.get_ident() and not owner.unknown
                   and owner.transition_db is db and (kind,owner_id) in owner.transition)
    def locator(kind,owner_id,unit_id,object_id,size,digest,offset):
        owner=store._strict_archive_owner
        return int(transition(kind,owner_id)==1 and owner.locators.get((kind,owner_id))
                   ==(kind,owner_id,unit_id,object_id,size,digest,offset))
    db.create_function('strict_payload_transition',2,transition)
    db.create_function('strict_payload_locator',7,locator)
    from .payload_hot_quota import bind
    bind(store,db)


def projection_predicate(db,support_alias='s',vector_alias='v'):
    _require((support_alias,vector_alias)==('s','v'),'payload_projection_alias')
    if not is_live_db(db):return 'length(s.pcm) IS s.pcm_bytes'
    return """(length(s.pcm) IS s.pcm_bytes OR (s.pcm IS NULL AND EXISTS(
      SELECT 1 FROM strict_payload_object po JOIN strict_payload_unit pu USING(unit_id)
      WHERE po.kind='pcm' AND po.owner_id=s.id AND po.bytes=s.pcm_bytes
      AND po.sha256=json_extract(s.manifest,'$.support.pooled_pcm_sha256')
      AND pu.state='committed' AND po.offset>=10 AND po.offset+po.bytes<=pu.bytes)))"""


def hot_limit(store):
    limit=store.settings.speaker_support_total_pcm_bytes
    _require(type(limit) is int and 0<limit<=64*1024**2,'payload_hot_limit')
    return limit


def _full_hot_bytes(db):
    _require(is_live_db(db),'payload_live_quota_policy')
    bad=db.execute("""SELECT 1 FROM speaker_support s WHERE
      (s.pcm IS NOT NULL AND length(s.pcm)!=s.pcm_bytes) OR
      (s.pcm IS NOT NULL AND EXISTS(SELECT 1 FROM strict_payload_object o WHERE o.kind='pcm' AND o.owner_id=s.id)) OR
      (s.pcm IS NULL AND s.pcm_bytes>0 AND NOT EXISTS(
        SELECT 1 FROM strict_payload_object o JOIN strict_payload_unit u USING(unit_id)
        WHERE o.kind='pcm' AND o.owner_id=s.id AND o.bytes=s.pcm_bytes
          AND o.sha256=json_extract(s.manifest,'$.support.pooled_pcm_sha256')
          AND u.state='committed' AND o.offset+o.bytes<=u.bytes)) LIMIT 1""").fetchone()
    _require(bad is None,'payload_pcm_quota_integrity')
    bad=db.execute("""SELECT 1 FROM speaker_support_vectors v WHERE
      (v.raw_vector IS NOT NULL AND length(v.raw_vector)!=v.retained_bytes) OR
      (v.raw_vector IS NOT NULL AND EXISTS(SELECT 1 FROM strict_payload_object o WHERE o.kind='vector' AND o.owner_id=v.id)) OR
      (v.raw_vector IS NULL AND NOT EXISTS(
        SELECT 1 FROM strict_payload_object o JOIN strict_payload_unit u USING(unit_id)
        WHERE o.kind='vector' AND o.owner_id=v.id AND o.bytes=v.retained_bytes AND o.sha256=v.vector_sha256
          AND u.state='committed' AND o.offset+o.bytes<=u.bytes)) LIMIT 1""").fetchone()
    _require(bad is None,'payload_vector_quota_integrity')
    return db.execute("""SELECT (SELECT COALESCE(SUM(length(pcm)),0) FROM speaker_support)+
      (SELECT COALESCE(SUM(length(raw_vector)),0) FROM speaker_support_vectors)""").fetchone()[0]


def hot_bytes(db):
    # A raw or not-yet-qualified connection retains the complete old audit.
    try:qualified=db.execute('SELECT payload_hot_qualified_v1()').fetchone()[0]
    except sqlite3.OperationalError as exc:
        if str(exc)!='no such function: payload_hot_qualified_v1':raise
        qualified=0
    if qualified!=1:return _full_hot_bytes(db)
    _require(is_live_db(db),'payload_live_quota_policy')
    bad=db.execute("""SELECT 1 FROM speaker_support s INDEXED BY payload_hot_pcm_v1
      WHERE s.pcm IS NOT NULL AND (length(s.pcm)!=s.pcm_bytes OR EXISTS(
        SELECT 1 FROM strict_payload_object o WHERE o.kind='pcm' AND o.owner_id=s.id)) LIMIT 1""").fetchone()
    _require(bad is None,'payload_pcm_quota_integrity')
    bad=db.execute("""SELECT 1 FROM speaker_support_vectors v INDEXED BY payload_hot_vector_v1
      WHERE v.raw_vector IS NOT NULL AND (length(v.raw_vector)!=v.retained_bytes OR EXISTS(
        SELECT 1 FROM strict_payload_object o WHERE o.kind='vector' AND o.owner_id=v.id)) LIMIT 1""").fetchone()
    _require(bad is None,'payload_vector_quota_integrity')
    return db.execute("""SELECT (SELECT COALESCE(SUM(length(pcm)),0) FROM speaker_support INDEXED BY payload_hot_pcm_v1 WHERE pcm IS NOT NULL)+
      (SELECT COALESCE(SUM(length(raw_vector)),0) FROM speaker_support_vectors INDEXED BY payload_hot_vector_v1 WHERE raw_vector IS NOT NULL)""").fetchone()[0]


@contextmanager
def _operation(store,budget):
    from .settings import payload_live_enabled
    from .storage import StrictMutationBusy
    refuse_pending_payload()
    _require(type(budget) is PayloadBudget,'payload_original_budget')
    budget.check()
    _require(store._strict_policy==POLICY and payload_live_enabled(store.settings),'payload_live_profile')
    if store._strict_uncertain:store._strict_refuse()
    if not store._strict_gate.acquire(False):raise PayloadAdmissionBusy('strict_payload_busy')
    owner=None
    try:
        if store._strict_uncertain:store._strict_refuse()
        if (store._strict_archive_owner is not None or store._strict_depth or any(store._strict_connections)
                or store._strict_worker_owner is not None or store._strict_publication_owner is not None):
            raise PayloadAdmissionBusy('strict_payload_busy')
        owner=_Owner(store,budget);store._strict_archive_owner=owner
        try:
            yield owner
            budget.check()
        except BaseException as error:owner.remember(error);raise
        finally:
            owner.transition.clear();owner.locators.clear();owner.transition_db=None;owner.cleanup()
            if owner.unknown:raise owner.original
            _require(not owner.fds and owner.scan is None,'payload_descriptors_retained')
            if store._strict_archive_owner is owner:store._strict_archive_owner=None
    finally:store._strict_gate.release()


def _begin_shared_snapshot(owner,db,*,write):
    """Native wait only for acquisition; never replay body or COMMIT."""
    budget=owner.budget;budget.check();original=None;acquire_deadline=time.monotonic()+.250
    try:
        milliseconds=max(0,int(min(max(0.,acquire_deadline-time.monotonic()),budget.remaining_seconds())*1000))
        db.execute('PRAGMA busy_timeout='+str(milliseconds))
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        if not write:
            if budget.progress():budget.check()
            milliseconds=max(0,int(min(max(0.,acquire_deadline-time.monotonic()),budget.remaining_seconds())*1000))
            db.execute('PRAGMA busy_timeout='+str(milliseconds))
            cursor=db.execute('SELECT rootpage FROM sqlite_schema LIMIT 1')
            try:cursor.fetchall()
            finally:cursor.close()
        budget.check()
    except BaseException as error:
        original=error;raise
    finally:
        try:db.execute('PRAGMA busy_timeout=0')
        except BaseException as error:
            if original is not None:owner.remember(original)
            owner.uncertain(error)
            if original is not None:
                original.add_note('Shared acquisition timeout reset failed; original owner remains uncertain')
                raise original
            raise


@contextmanager
def _db(owner,*,write=False,witness=None,shared_acquire=False):
    _require(not owner._db_allowed,'payload_database_scope')
    owner._db_allowed=True
    begin_error=None;connection=None
    try:
        # Guard locks remain through original COMMIT and positive SQLite close.
        with ExitStack() as guards:
            with owner.store.db(budget=owner.budget) as db:
                connection=db
                try:
                    if shared_acquire:
                        _begin_shared_snapshot(owner,db,write=write)
                    else:db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                except BaseException as error:
                    # Only a failed, unstarted write BEGIN can be deferred.
                    if not db.in_transaction:begin_error=error
                    raise
                snapshot=None if witness is None else guards.enter_context(witness.guard(db,owner.budget))
                yield (db,snapshot) if witness is not None else db
    except BaseException as error:
        code=getattr(error,'sqlite_errorcode',None)
        if (write and error is begin_error and type(error) is sqlite3.OperationalError
                and type(code) is int and code&0xff==sqlite3.SQLITE_BUSY
                and owner.original is None and not owner.unknown and _PENDING_PAYLOAD_OWNER is None
                and not owner.store.strict_cleanup_uncertain()
                and connection not in owner.store._strict_connections
                and owner.budget.sql_outcome=='rolled_back' and owner.budget.first_error is None):
            # Only after original rollback and close returned; same deadline
            # check must still succeed. Never classify LOCKED or a body/COMMIT.
            owner.budget.check()
            raise PayloadBeginBusy('payload_begin_busy') from error
        owner.remember(error)
        if owner.store.strict_cleanup_uncertain() or owner.budget.sql_outcome in ('commit_attempted','unknown'):
            owner.uncertain(error)
        raise owner.original
    finally:owner._db_allowed=False


# Original bounded byte/file primitives; offline API/global limits are unchanged.
_canonical=base._canonical
_jsonable=base._jsonable
_sha=base._sha
_signature=base._signature
_one=base._one
_rows=base._rows
_open_file=base._open_file
_read_fd=base._read_fd
_write_bytes=base._write_bytes
_close=base._close
_close_all=base._close_all
_file_digest=base._file_digest
_range_equal=base._range_equal
_pcm_digest=base._pcm_digest
_blob_hash=base._blob_hash


def _bucket(owner,bucket,*,create):
    _require(type(bucket) is int and 0<=bucket<=(getattr(owner.store,'_strict_lifetime_records',2056)-1)//BUCKET_UNITS,'payload_bucket_index')
    data=Path(owner.store.settings.data_dir).absolute()
    _require(data.is_dir() and not data.is_symlink(),'payload_data_directory')
    root=data/'strict-payload-v2'; path=root/f'{bucket:08x}'
    if create:
        for directory,parent in ((root,data),(path,root)):
            try:directory.mkdir(mode=0o700)
            except FileExistsError:pass
            _require(directory.is_dir() and not directory.is_symlink(),'payload_bucket_directory')
            fd=_open_file(owner,parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            except BaseException as error:owner.remember(error);raise
            finally:_close(owner,fd)
    _require(root.is_dir() and not root.is_symlink() and path.is_dir() and not path.is_symlink(),
             'payload_bucket_directory')
    fd=_open_file(owner,path,os.O_RDONLY|os.O_DIRECTORY)
    return path,fd


def _reserve_bucket(owner,fd,size):
    owner.budget.check();owner.scan=os.scandir(fd);count=total=0
    try:
        for entry in owner.scan:
            owner.budget.check();count+=1
            _require(count+2<=MAX_BUCKET_NAMES,'payload_bucket_names')
            _require(re.fullmatch(r'[a-f0-9]{64}\.(unit|stage)',entry.name),'payload_bucket_unknown_name')
            info=entry.stat(follow_symlinks=False)
            _require(stat.S_ISREG(info.st_mode) and 0<=info.st_size<=MAX_UNIT_BYTES,'payload_bucket_entry')
            total+=info.st_size
        _require(count+2<=MAX_BUCKET_NAMES and total+2*size<=MAX_BUCKET_BYTES,'payload_bucket_capacity')
    except BaseException as error:owner.remember(error);raise
    finally:owner.close_scan()
    disk=os.fstatvfs(fd)
    reserve=max(owner.store.settings.disk_reserve_bytes,owner.store.settings.recording_disk_reserve_bytes)
    if disk.f_bavail*disk.f_frsize<reserve+2*size:raise PayloadCapacityError('payload_disk_reserve')


def _query_locator(db,kind,owner_id,logical_bytes,digest):
    _require(kind in ('pcm','vector') and type(owner_id) is str and re.fullmatch('[a-f0-9]{32}',owner_id)
             and type(logical_bytes) is int and 0<logical_bytes<=(1280000 if kind=='pcm' else 65536)
             and type(digest) is str and _ID.fullmatch(digest),'payload_locator_request')
    row=db.execute('''SELECT o.kind,o.owner_id,o.object_id,o.bytes AS object_bytes,o.sha256,o.offset,
        u.sequence,u.bucket,u.unit_id,u.cid,u.store_key,u.state,u.manifest_sha256,u.manifest_bytes,u.bytes,u.file_sha256,u.signature
        FROM strict_payload_object o JOIN strict_payload_unit u USING(unit_id)
        WHERE o.kind=? AND o.owner_id=?''',(kind,owner_id)).fetchone()
    _require(row is not None,'payload_locator_missing')
    row=dict(row)
    _require(row['state']=='committed' and row['object_bytes']==logical_bytes and row['sha256']==digest
             and type(row['offset']) is int and 10<=row['offset']<row['offset']+logical_bytes<=row['bytes']
             and type(row['sequence']) is int and 1<=row['sequence']<=strict_lifetime.read_limit(db)
             and row['bucket']==(row['sequence']-1)//16
             and all(type(row[k]) is str and _ID.fullmatch(row[k]) for k in ('unit_id','manifest_sha256','file_sha256','store_key'))
             and type(row['manifest_bytes']) is int and 0<row['manifest_bytes']<=MAX_METADATA
             and type(row['signature']) is str and len(row['signature'])<=512,'payload_locator_invalid')
    if kind=='pcm':
        logical=db.execute('''SELECT s.chunk_id,s.pcm_bytes,json_extract(s.manifest,'$.support.pooled_pcm_sha256')
          FROM speaker_support s JOIN sessions a ON a.id=s.session_id AND a.deleted=0 AND a.identity_group=s.identity_group
          JOIN speaker_identity_generations g ON g.identity_group=s.identity_group AND g.generation=s.generation
          JOIN chunks c ON c.id=s.chunk_id AND c.session_id=s.session_id WHERE s.id=?''',(owner_id,)).fetchone()
    else:
        logical=db.execute('''SELECT s.chunk_id,v.retained_bytes,v.vector_sha256 FROM speaker_support_vectors v
          JOIN speaker_support s ON s.id=v.support_id AND s.generation=v.generation AND s.identity_group=v.identity_group
          JOIN sessions a ON a.id=s.session_id AND a.deleted=0 AND a.identity_group=s.identity_group
          JOIN speaker_identity_generations g ON g.identity_group=s.identity_group AND g.generation=s.generation
          JOIN chunks c ON c.id=s.chunk_id AND c.session_id=s.session_id WHERE v.id=?''',(owner_id,)).fetchone()
    _require(logical is not None and tuple(logical)==(row['cid'],logical_bytes,digest),'payload_logical_owner_changed')
    return row


def _snapshot_current(store,db):
    if db in store._strict_connections:return db.in_transaction
    from .reviewed_primary_persons import payload_bridge_current
    return payload_bridge_current(store,db)


class _Resolver:
    def __init__(self,owner,db):
        self.owner,self.db=owner,db;self.active=True;self.last_manifest=None
    def _check(self):
        _require(self.active and self.owner.thread==threading.get_ident()
                 and self.owner.store._strict_archive_owner is self.owner
                 and _snapshot_current(self.owner.store,self.db),'payload_snapshot_changed')
        self.owner.budget.check()
    def hot(self,kind,owner_id,logical_bytes,digest):
        self._check();self.owner.budget.projection(128)
        _require(kind in ('pcm','vector'),'payload_hot_kind')
        _require(self.db.execute('SELECT 1 FROM strict_payload_object WHERE kind=? AND owner_id=?',
                                (kind,owner_id)).fetchone() is None,'payload_dual_placement')
        return True
    def _file(self,row):
        owner=self.owner
        _require(row['store_key']==base._store_key(owner.store),'payload_store_changed')
        path,directory=_bucket(owner,row['bucket'],create=False);_close(owner,directory)
        fd=_open_file(owner,path/(row['unit_id']+'.unit'),os.O_RDONLY)
        before=os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_size==row['bytes']
                 and _signature(before)==json.loads(row['signature']),'payload_file_identity')
        return fd,before
    def available(self,kind,owner_id,logical_bytes,digest):
        self._check();owner=self.owner;owner.budget.projection(2048)
        row=_query_locator(self.db,kind,owner_id,logical_bytes,digest)
        fd=None
        try:
            fd,before=self._file(row)
            _require(_signature(os.fstat(fd))==_signature(before),'payload_metadata_file_changed')
            return True
        except BaseException as error:owner.remember(error);raise
        finally:
            if fd is not None:_close(owner,fd)
    def resolve(self,kind,owner_id,logical_bytes,digest):
        self._check();owner=self.owner;owner.budget.projection(2048)
        row=_query_locator(self.db,kind,owner_id,logical_bytes,digest)
        fd=None
        try:
            fd,before=self._file(row)
            header=_read_fd(owner,fd,len(MAGIC)+4)
            _require(header[:len(MAGIC)]==MAGIC and len(header)==len(MAGIC)+4,'payload_file_header')
            size=struct.unpack('>I',header[len(MAGIC):])[0]
            _require(size==row['manifest_bytes'] and 0<size<=MAX_METADATA,'payload_manifest_size')
            key=(row['unit_id'],tuple(_signature(before)),row['manifest_sha256'],size)
            if self.last_manifest!=key:
                # Hash the full immutable manifest without allocating its full
                # proof graph inside the original bridge's 32 MiB reservation.
                h=hashlib.sha256();left=size
                while left:
                    block=_read_fd(owner,fd,min(BUFFER,left));_require(block,'payload_manifest_short')
                    h.update(block);left-=len(block)
                _require(h.hexdigest()==row['manifest_sha256']==row['unit_id'],'payload_manifest_hash')
                self.last_manifest=key
            # This exact immutable SQL locator was inserted only by the original
            # active placement transition, derived from the validated manifest.
            _require(row['offset']>=len(MAGIC)+4+size,'payload_object_position')
            os.lseek(fd,row['offset'],os.SEEK_SET);out=bytearray()
            while len(out)<logical_bytes:
                block=_read_fd(owner,fd,min(BUFFER,logical_bytes-len(out)));_require(block,'payload_object_short');out.extend(block)
            _require(_sha(out)==digest and _signature(os.fstat(fd))==_signature(before),'payload_object_integrity')
            owner.budget.payload(len(out));return bytes(out)
        except BaseException as error:owner.remember(error);raise
        finally:
            if fd is not None:_close(owner,fd)


def reader_progress(store,db):
    """One exact existing SQLite callback; no SQL or replacement handler here."""
    owner=store._strict_archive_owner
    if (type(owner) is not _Owner or not owner.resolver_active or owner.snapshot_db is not db
            or owner.thread!=threading.get_ident()):return 0
    try:
        result=base.ArchiveBudget.progress(owner.budget)
        if result and isinstance(owner.budget.first_error,BaseException):owner.remember(owner.budget.first_error)
        return result
    except BaseException as error:
        owner.remember(error)
        if owner.budget.first_error is None:owner.budget.first_error=error
        return 1


def finish_reader_database(store,db,original,close_error,outcome):
    """Observe this API's existing outer SQLite owner, never close it again."""
    owner=store._strict_payload_snapshot_owner
    if owner is None or owner.snapshot_db is not db:return
    if (close_error is not None or store.strict_cleanup_uncertain()
            or outcome in ('commit_attempted','unknown')):
        error=owner.original or original or store._strict_original_error or close_error or store._strict_cleanup_error
        if error is None:error=PayloadIntegrityError('payload_reader_database_unknown')
        owner.uncertain(error)
    else:
        store._strict_payload_snapshot_owner=None


@contextmanager
def payload_resolver(store,db,*,budget=None,check=None):
    if store._strict_policy!=POLICY:
        yield None;return
    from .storage import StrictMutationBusy
    from .settings import payload_live_enabled
    refuse_pending_payload()
    _require(payload_live_enabled(store.settings),'payload_reader_live_profile')
    _require(_snapshot_current(store,db),'payload_original_snapshot_required')
    if store._strict_uncertain:store._strict_refuse()
    if not store._strict_gate.acquire(False):raise StrictMutationBusy('strict_payload_reader_busy')
    owner=None;resolver=None;installed=False;original_handler=None
    try:
        if store._strict_uncertain:store._strict_refuse()
        if store._strict_archive_owner is not None:raise StrictMutationBusy('strict_payload_reader_busy')
        slot=next((i for i,c in enumerate(store._strict_connections) if c is db),None)
        bridge=None
        if slot is not None:
            actual=store._strict_connection_budgets[slot]
            if actual is not None and budget is not None:
                _require(budget is actual or type(budget) is PayloadBudget and budget.caller is actual,
                         'payload_original_caller_budget')
            caller=actual if actual is not None else (None if type(budget) is PayloadBudget else budget)
            previous=store._strict_payload_snapshot_owner
            original_handler=actual.progress if actual is not None else None
        else:
            from .reviewed_primary_persons import payload_bridge_current
            _require(payload_bridge_current(store,db),'payload_original_bridge')
            bridge=store._strict_payload_bridge_owner
            _require(check is None or check is bridge.check,'payload_original_bridge_check')
            check=bridge.check;caller=budget
            previous=bridge.payload_owner
        if previous is not None:
            _require(type(previous) is _Owner and previous.snapshot_db is db
                     and previous.thread==threading.get_ident() and not previous.resolver_active
                     and not previous.fds and previous.scan is None,'payload_snapshot_owner_busy')
            if previous.unknown or previous.original is not None:raise previous.original
            _require(budget is None or budget is previous.budget or budget is previous.budget.caller,
                     'payload_snapshot_budget_changed')
            _require(check is None or check is previous.budget.caller_check,'payload_snapshot_check_changed')
            owner=previous;selected=owner.budget
        else:
            selected=budget if type(budget) is PayloadBudget and check is None else PayloadBudget(caller=caller,check=check)
            owner=_Owner(store,selected);owner.snapshot_db=db
            if bridge is None:store._strict_payload_snapshot_owner=owner
            else:bridge.payload_owner=owner
        selected.check();store._strict_archive_owner=owner;owner.resolver_active=True
        resolver=_Resolver(owner,db)
        try:
            if bridge is None:
                # Original connection-slot metadata is authority even when the
                # caller omitted its budget argument. Never erase its handler.
                caller_progress=original_handler
                if caller_progress is None and selected.caller is not None:caller_progress=selected.caller.progress
                def composed_progress():
                    try:
                        if selected is actual:
                            result=selected.progress()
                            if result and isinstance(selected.first_error,BaseException):owner.remember(selected.first_error)
                            return result
                        first=caller_progress() if caller_progress is not None else 0
                        second=reader_progress(store,db)
                        return int(bool(first or second))
                    except BaseException as error:owner.remember(error);return 1
                try:db.set_progress_handler(composed_progress,100)
                except BaseException as error:owner.uncertain(error);raise
                installed=True
            # Marker and locator SQL are inside the same effective VM envelope.
            _require(is_live_db(db),'payload_reader_live_marker')
            yield resolver
            selected.check()
        except BaseException as error:
            owner.remember(error)
            raise owner.original
        finally:
            resolver.active=False;owner.resolver_active=False
            if installed:
                try:db.set_progress_handler(original_handler,100 if original_handler is not None else 0)
                except BaseException as error:owner.uncertain(error)
            owner.cleanup()
            if owner.unknown:raise owner.original
            _require(not owner.fds,'payload_reader_descriptor_retained')
            if store._strict_archive_owner is owner:store._strict_archive_owner=None
            # Same exact snapshot reuses this owner/envelope until its original
            # outer DB owner reports positive completion and close.
    finally:store._strict_gate.release()


def _source_spec(owner,object_id,kind,path,size,digest,frames,signature=None):
    path=Path(path);uploads=Path(owner.store.settings.data_dir).absolute()/'uploads'
    _require(path.is_absolute() and path.parent==uploads and type(frames) is int
             and 0<frames<=MAX_SOURCE_FRAMES and 44<=size<=frames*2+65536,'payload_source_bound')
    actual,sig=_file_digest(owner,path,size,signature,wave_frames=frames)
    _require(actual==digest,'payload_source_digest')
    return {'object_id':object_id,'kind':kind,'bytes':size,'sha256':digest,
            '_path':str(path),'_signature':sig,'_frames':frames}


# Fresh-format packetless authority. This is not a normal attributed packet.
WITNESS_SCHEMA = 'strict-payload-core-witness-v1'
WITNESS_TABLE = 'strict_payload_core_witness'
MAX_WITNESS_BYTES = 64 * 1024
TURN_FIELDS = ('id','chunk_id','start','end','speaker_local_id','speaker_window_start')
_WITNESS_BINDING = ('sid','cid','source','nonce','start','end','status','evidence','slot')


@dataclass(frozen=True, slots=True)
class PacketlessCapture:
    binding: tuple
    body: bytes
    sha256: str


def _projection_charge(budget,count):
    from .native_owned_cell_store import reserve_projection
    reserve_projection(budget,count)


def _finite_time(value):
    _require(type(value) in (int,float) and math.isfinite(value),'payload_witness_time')
    return value


def projected_turns(packet):
    """Exact original validate -> own_core -> Store arithmetic; never epsilon."""
    from .native_owned_core_seams import validate_commit
    from .native_owned_cells import integer
    part=validate_commit(packet['commit']);scope=packet['commit']['scope']
    query=packet['query'];labels=packet['labels'];turns=packet['turns']
    _require(type(query) is list and len(query)==7,'payload_witness_query')
    qa,qb,replay,processed,stable,actions,eof=query
    for value in query[:6]:integer(value)
    _require(qa<=part.logical_start<part.logical_end<=qb and qb-qa<=60*16000
             and part.logical_end<=replay+stable<=replay+processed and actions>=1
             and type(eof) is bool,'payload_witness_query')
    _require(type(labels) is list and len(labels)<=8 and len(set(labels))==len(labels)
             and all(type(label) is str and label in {scope['epoch']+':speaker_'+str(i) for i in range(1,9)}
                     for label in labels),'payload_witness_labels')
    _require(type(turns) is list and len(turns)<=4096,'payload_witness_turn_capacity')
    origin_frame=part.logical_start-part.source_start
    timeline=float.fromhex(scope['origin'])+origin_frame/16000
    extract_start=(qa-origin_frame)/16000
    offset=part.source_start/16000;end=part.source_end/16000
    core_start=offset-extract_start;core_end=end-extract_start;duration=(qb-qa)/16000
    result=[];last=-1
    for turn in turns:
        _require(type(turn) is list and len(turn)==6,'payload_witness_turn_shape')
        ordinal,label,a,b,lo,hi=turn
        for value in turn[:4]:integer(value)
        _finite_time(lo);_finite_time(hi)
        _require(last<ordinal<4096 and label<len(labels) and a<b
                 and lo==max(qa/16000,replay/16000+(a-replay)/16000)-qa/16000
                 and hi==min(qb/16000,replay/16000+(b-replay)/16000)-qa/16000
                 and 0<=lo<hi<=duration+.15,'payload_witness_turn_geometry')
        last=ordinal
        lo=min(lo,duration);hi=min(hi,duration)
        if hi<=lo:continue
        low=max(core_start,lo);high=min(core_end,hi)
        if high>low:
            _require(len(result)<MAX_TURNS,'payload_witness_owned_turn_capacity')
            result.append(((timeline+offset)+(low-core_start),
                           (timeline+offset)+(high-core_start),labels[label]))
    return result


def _binding(value,key):
    return (value.sid,value.cid,value.source,value.nonce,value.start,value.end,
            value.status,value.evidence,key)


def _witness_shape(doc,*,complete):
    from .native_owned_core_seams import validate_commit,slot
    required={'schema','status','binding','commit','query','labels','turns',
              'owned_pcm16le_sha256','captured_rows'}
    if complete:required|={'turn_refs','primary_refs'}
    _require(type(doc) is dict and set(doc)==required and doc['schema']==WITNESS_SCHEMA
             and doc['status'] in ('empty','unknown_only'),'payload_witness_shape')
    part=validate_commit(doc['commit']);bind=doc['binding']
    _require(type(bind) is list and len(bind)==len(_WITNESS_BINDING),'payload_witness_binding')
    sid,cid,source,nonce,start,end,status,evidence,key=bind
    _require((sid,cid,source,start,end,status,evidence,key)==(part.session_id,part.chunk_id,
             part.source_sha256,part.source_start,part.source_end,doc['status'],
             doc['commit']['evidence_sha256'],slot(doc['commit']))
             and type(nonce) is str and re.fullmatch('[a-f0-9]{32}',nonce)
             and type(doc['owned_pcm16le_sha256']) is str and _ID.fullmatch(doc['owned_pcm16le_sha256']),
             'payload_witness_binding')
    projected=projected_turns(doc)
    rows=doc['captured_rows']
    _require(type(rows) is list and len(rows)<=128 and ((status=='empty')==(len(rows)==0)),
             'payload_witness_primary_shape')
    for row in rows:
        _require(type(row) is list and len(row)==3 and type(row[0]) is str and _ID.fullmatch(row[0])
                 and type(row[1]) is str and _ID.fullmatch(row[1])
                 and type(row[2]) is int and 256<=row[2]<=MAX_WITNESS_BYTES+256,
                 'payload_witness_primary_shape')
    if not complete:return projected
    turns=doc['turn_refs'];primary=doc['primary_refs']
    _require(type(turns) is list and len(turns)<=MAX_TURNS and type(primary) is list
             and len(primary)==len(rows),'payload_witness_row_shape')
    ids=[];actual=[]
    for row in turns:
        _require(type(row) is list and len(row)==6 and type(row[0]) is int and 0<row[0]<=(1<<63)-1
                 and row[1]==cid and row[5]==start/16000,'payload_witness_turn_ref')
        _finite_time(row[2]);_finite_time(row[3]);_finite_time(row[5])
        _require(row[2]<row[3] and type(row[4]) is str and row[4] in doc['labels'],
                 'payload_witness_turn_ref')
        ids.append(row[0]);actual.append((row[2],row[3],row[4]))
    _require(len(ids)==len(set(ids)) and Counter(actual)==Counter(projected),'payload_turn_geometry')
    primary_ids=[]
    for row,captured in zip(primary,rows):
        _require(type(row) is list and len(row)==3 and type(row[0]) is int and 0<row[0]<=(1<<63)-1
                 and row[1:]==[captured[0],captured[2]],'payload_witness_primary_ref')
        primary_ids.append(row[0])
    _require(len(primary_ids)==len(set(primary_ids)),'payload_witness_duplicate_primary')
    return projected


def _witness_encode(doc,budget,*,complete):
    # Validate bounded source structure before serializing a full body. Each
    # temporary item has a fixed small bound, charged before its allocation.
    _require(type(doc) is dict and type(doc.get('turns')) is list and len(doc['turns'])<=4096,
             'payload_witness_turn_capacity')
    _projection_charge(budget,4096+min(len(doc['turns']),MAX_TURNS)*512)
    _witness_shape(doc,complete=complete)
    arrays=('labels','turns','captured_rows')+(('turn_refs','primary_refs') if complete else ())
    header={key:([] if key in arrays else value) for key,value in doc.items()}
    header_raw=_canonical(header)
    _require(len(header_raw)<=4096,'payload_witness_header_capacity')
    size=len(header_raw)
    for name in arrays:
        for index,item in enumerate(doc[name]):
            _projection_charge(budget,512)
            raw=_canonical(item)
            _require(len(raw)<=512,'payload_witness_item_capacity')
            size+=len(raw)+(1 if index else 0)
            _require(size<=MAX_WITNESS_BYTES,'payload_witness_body_capacity')
    _projection_charge(budget,size)
    raw=_canonical(doc)
    _require(len(raw)==size and 0<len(raw)<=MAX_WITNESS_BYTES,'payload_witness_body_capacity')
    return raw


def packetless_draft(capture,commit,observed,qa,qb,labels,turns,owned_pcm,status,row_hashes,row_sizes=()):
    """Called only after original freeze has verified native/row attribution."""
    _require(getattr(capture.authority.store,'_strict_policy',None)==POLICY
             and status in ('empty','unknown_only') and type(owned_pcm) is bytes,
             'payload_witness_capture_scope')
    capture.budget.check()
    _require(type(labels) is list and len(labels)<=8 and type(turns) is list and len(turns)<=4096
             and type(row_hashes) in (list,tuple) and type(row_sizes) in (list,tuple)
             and len(row_hashes)==len(row_sizes)<=128,'payload_witness_draft_capacity')
    _projection_charge(capture.budget,1024+128*len(row_hashes))
    return {'commit':commit,'query':[qa,qb,observed['replay_start'],observed['state']['processed']['frame'],
             observed['state']['stable']['frame'],observed['observed_action_count'],observed['true_eof']],
            'labels':labels,'turns':turns,'owned_pcm16le_sha256':hashlib.sha256(owned_pcm).hexdigest(),
            'status':status,'row_hashes':tuple(zip(row_hashes,row_sizes))}


def make_packetless_capture(chunk,offset,end,result,value,budget):
    from .native_owned_core_seams import slot
    draft=result.get('_strict_payload_draft')
    if value.status=='retained':
        _require(draft is None,'payload_unexpected_packetless_draft');return None
    _require(value.status in ('empty','unknown_only') and type(draft) is dict
             and set(draft)=={'commit','query','labels','turns','owned_pcm16le_sha256','status','row_hashes'}
             and draft['status']==value.status and value.cid==chunk['id']
             and value.start/16000==offset and value.end/16000==end,'payload_witness_draft_missing')
    _projection_charge(budget,4096+128*len(value.rows))
    _require(tuple((row[0],row[2]) for row in value.rows)==draft['row_hashes'],'payload_witness_captured_rows')
    key=slot(draft['commit']);binding=_binding(value,key)
    doc={key:draft[key] for key in ('commit','query','labels','turns','owned_pcm16le_sha256','status')}
    doc.update(schema=WITNESS_SCHEMA,binding=list(binding),captured_rows=[list(row) for row in value.rows])
    raw=_witness_encode(doc,budget,complete=False)
    return PacketlessCapture(binding,raw,_sha(raw))


def _turn_rows(db,cid,offset,expected_ids,budget):
    _require(type(expected_ids) is list and len(expected_ids)<=MAX_TURNS
             and all(type(i) is int and 0<i<=(1<<63)-1 for i in expected_ids)
             and len(expected_ids)==len(set(expected_ids)),'payload_witness_turn_ids')
    _projection_charge(budget,512*(len(expected_ids)+1)+512)
    total=db.execute('SELECT COUNT(*) FROM speaker_turns WHERE chunk_id=?',(cid,)).fetchone()[0]
    _require(type(total) is int and total<=MAX_TURNS,'payload_witness_chunk_turn_capacity')
    # Return only bounded integer/null preflight scalars until byte and type
    # bounds are known. An unbounded tampered label is never materialized.
    sizes=db.execute("""SELECT CASE WHEN typeof(id)='integer' AND id>0 THEN id END,
        CASE WHEN typeof(chunk_id)='text' AND length(CAST(chunk_id AS BLOB))=32
        AND typeof(start) IN ('integer','real') AND typeof(end) IN ('integer','real')
        AND typeof(speaker_window_start) IN ('integer','real')
        AND (speaker_local_id IS NULL OR (typeof(speaker_local_id)='text'
             AND length(CAST(speaker_local_id AS BLOB))<=128))
        THEN 128+length(CAST(chunk_id AS BLOB))+COALESCE(length(CAST(speaker_local_id AS BLOB)),0) END
        FROM speaker_turns WHERE chunk_id=? AND speaker_window_start=? ORDER BY id LIMIT ?""",
        (cid,offset,len(expected_ids)+1)).fetchall()
    _require([row[0] for row in sizes]==expected_ids and all(type(row[1]) is int for row in sizes),
             'payload_witness_turn_projection')
    _projection_charge(budget,sum(row[1] for row in sizes))
    rows=[dict(r) for r in db.execute('SELECT '+','.join(TURN_FIELDS)+
        ' FROM speaker_turns WHERE chunk_id=? AND speaker_window_start=? ORDER BY id LIMIT ?',
        (cid,offset,len(expected_ids)+1))]
    _require([row['id'] for row in rows]==expected_ids,'payload_witness_turn_id_binding')
    return rows


def commit_packetless_witness(db,chunk,offset,end,result,value,status,row_ids,turn_row_ids,key):
    """Mandatory fresh-live hook inside the ORIGINAL outcome transaction."""
    from . import recording_primary_provenance as pp, transcript_contract as contract
    handoff=result.get('_native_bounded_support')
    _require(type(handoff) is dict and handoff.get('reserved') is True,'payload_witness_original_budget')
    budget=handoff['budget'];budget.check()
    _projection_charge(budget,1536)
    _require(db.in_transaction and is_live_db(db),'payload_witness_transaction')
    count=db.execute('SELECT COUNT(*) FROM strict_recording_outcome WHERE cid=?',(chunk['id'],)).fetchone()[0]
    _require(type(count) is int and 1<=count<=MAX_OUTCOMES,'payload_witness_chunk_outcome_capacity')
    _require(type(row_ids) is list and len(row_ids)<=128 and len(row_ids)==len(set(row_ids))
             and all(type(i) is int and i>0 for i in row_ids),'payload_witness_primary_ids')
    turns=_turn_rows(db,chunk['id'],offset,turn_row_ids,budget)
    _projection_charge(budget,512)
    existing=db.execute('SELECT 1 FROM strict_payload_core_witness WHERE cid=? AND core_start=? LIMIT 1',
                        (chunk['id'],value.start)).fetchone()
    _require(existing is None,'payload_witness_duplicate')
    if status in ('retained','existing'):
        _require(result.get('_strict_payload_capture') is None and value.status=='retained',
                 'payload_unexpected_packetless_capture')
        draft=result.get('_primary_provenance')
        _require(type(draft) is dict and type(draft.get('packet')) is dict,'payload_retained_capture_missing')
        _projection_charge(budget,4096+len(turns)*256)
        expected=projected_turns(draft['packet'])
        _require(Counter(expected)==Counter((r['start'],r['end'],r['speaker_local_id']) for r in turns),
                 'payload_turn_geometry')
        return
    _require(status==value.status and status in ('empty','unknown_only'),'payload_witness_status')
    capsule=result.get('_strict_payload_capture')
    _require(type(capsule) is PacketlessCapture and capsule.binding==_binding(value,key)
             and type(capsule.body) is bytes and 0<len(capsule.body)<=MAX_WITNESS_BYTES
             and _sha(capsule.body)==capsule.sha256,'payload_witness_capture_binding')
    _projection_charge(budget,2*len(capsule.body)+sum(row[2] for row in value.rows)+4096)
    doc=json.loads(capsule.body);_witness_shape(doc,complete=False)
    _require(tuple(doc['binding'])==capsule.binding and tuple(tuple(row) for row in doc['captured_rows'])==value.rows,
             'payload_witness_capture_binding')
    _require(len(row_ids)==len(value.rows),'payload_witness_primary_cardinality')
    _projection_charge(budget,512+256*len(turns)+128*len(row_ids))
    primary=[]
    for ident,captured in zip(row_ids,value.rows):
        row=db.execute('SELECT '+','.join(contract.PRIMARY_FIELDS)+' FROM segments WHERE id=?',(ident,)).fetchone()
        _require(row is not None and row['chunk_id']==chunk['id'] and row['speaker_window_start']==offset
                 and row['speaker_local_id'] is None
                 and pp.digest({field:row[field] for field in contract.PRIMARY_FIELDS if field!='id'})==captured[0],
                 'payload_witness_unknown_row')
        primary.append([ident,captured[0],captured[2]])
    doc['turn_refs']=[[row[field] for field in TURN_FIELDS] for row in turns]
    doc['primary_refs']=primary
    raw=_witness_encode(doc,budget,complete=True)
    # This body includes only immutable geometry/primary content. Identity and
    # overlap presentation remain ordinary complete archive snapshot fields.
    db.execute('INSERT INTO strict_payload_core_witness VALUES(?,?,?,?,?,?,?,?)',
               (chunk['id'],value.start,value.end,key,status,WITNESS_SCHEMA,_sha(raw),raw))


def _read_core_witnesses(owner,db,cid):
    # Deliberately local to live-v2; offline base._TABLES stays untouched.
    owner.budget.projection((MAX_OUTCOMES+1)*1024)
    heads=db.execute("""SELECT
        CASE WHEN typeof(cid)='text' AND length(CAST(cid AS BLOB))=32 THEN cid END AS cid,
        CASE WHEN typeof(core_start)='integer' THEN core_start END AS core_start,
        CASE WHEN typeof(core_end)='integer' THEN core_end END AS core_end,
        CASE WHEN typeof(slot)='text' AND length(CAST(slot AS BLOB))=64 THEN slot END AS slot,
        CASE WHEN status IN ('empty','unknown_only') THEN status END AS status,
        CASE WHEN version='strict-payload-core-witness-v1' THEN version END AS version,
        CASE WHEN typeof(digest)='text' AND length(CAST(digest AS BLOB))=64 THEN digest END AS digest,
        length(body) AS size FROM strict_payload_core_witness WHERE cid=? ORDER BY core_start LIMIT ?""",
        (cid,MAX_OUTCOMES+1)).fetchall()
    _require(len(heads)<=MAX_OUTCOMES,'payload_witness_count')
    result=[]
    for head in heads:
        row=dict(head);size=row.pop('size')
        _require(type(size) is int and 0<size<=MAX_WITNESS_BYTES and row['version']==WITNESS_SCHEMA
                 and row['cid']==cid and type(row['core_start']) is int and type(row['core_end']) is int
                 and 0<=row['core_start']<row['core_end'] and row['status'] in ('empty','unknown_only')
                 and type(row['slot']) is str and _ID.fullmatch(row['slot'])
                 and type(row['digest']) is str and _ID.fullmatch(row['digest']),'payload_witness_header')
        owner.budget.read(size+512);owner.budget.metadata(3*size+4096)
        raw=db.execute('SELECT body FROM strict_payload_core_witness WHERE cid=? AND slot=? AND length(body)=?',
                       (cid,row['slot'],size)).fetchone()
        _require(raw is not None and type(raw[0]) is bytes and _sha(raw[0])==row['digest'],
                 'payload_witness_body_digest')
        row['body']=raw[0];doc=json.loads(raw[0])
        _witness_shape(doc,complete=True)
        _require(_canonical(doc)==raw[0] and doc['binding'][1]==cid
                 and (doc['binding'][4],doc['binding'][5],doc['binding'][6],doc['binding'][8])
                    ==(row['core_start'],row['core_end'],row['status'],row['slot']),
                 'payload_witness_header_binding')
        result.append((row,doc))
    return result


def _collect(owner,cid):
    """Only payload owners retire; their full original source/proof closure is saved."""
    from . import strict_recording as strict,native_owned_core_seams as core
    from . import recording_primary_provenance as pp,lexical_evidence as lexical
    from . import speaker_support as support,support_vectors as vectors,transcript_contract as contract
    from .native_owned_cell_store import Receipt
    _require(type(cid) is str and re.fullmatch('[a-f0-9]{32}',cid),'payload_chunk_id')
    specs=[];sources={};closures={};meta={}
    with _db(owner) as db:
        get=lambda table,where,args,cap,**kw:_rows(owner,db,table,where,args,cap,**kw)
        chunk=_one(get('chunks','id=?',(cid,),1),'payload_chunk_missing');sid=chunk['session_id']
        session=_one(get('sessions','id=?',(sid,),1),'payload_session_missing')
        policy=_one(get('strict_recording_policy','sid=?',(sid,),1),'payload_policy_missing')
        control=_one(get('strict_recording_control','cid=?',(cid,),1),'payload_control_missing')
        frames=strict.frame(chunk['duration'])
        _require(session['deleted']==0 and policy['policy']==POLICY and 0<frames<=MAX_FRAMES
                 and chunk['status']=='completed' and chunk['next_offset']==chunk['duration']
                 and control['state']=='completed' and control['nonce'] is None
                 and control['sid']==sid and control['source']==chunk['digest'] and control['frames']==frames,
                 'payload_completed_prefix_required')
        def source_closure(ident):
            if ident in closures:return closures[ident]
            _require(len(closures)<MAX_PARTS,'payload_source_part_capacity')
            c=chunk if ident==cid else _one(get('chunks','id=?',(ident,),1),'payload_dependency_missing')
            _require(c['session_id']==sid,'payload_dependency_session')
            work=_one(get('recording_work','chunk_id=?',(ident,),1),'payload_work_missing')
            batch=_one(get('recording_batches','id=?',(work['batch_id'],),1),'payload_batch_missing')
            stream=get('stream_chunks','chunk_id=?',(ident,),1)
            receipt=Receipt.decode(work['native_receipt'])
            _require(batch['session_id']==sid and work['receipt_state']=='durable'
                     and receipt.session_id==sid and receipt.chunk_id==ident and receipt.digest==c['digest']
                     and receipt.frames==strict.frame(c['duration'])==work['frames']
                     and receipt.timeline_start==c['timeline_start'] and receipt.size==work['bytes']
                     and receipt.receipt_id==work['receipt_id'] and list(receipt.signature)==json.loads(work['file_signature'])
                     and receipt.stream_id==(stream[0]['stream_id'] if stream else None),'payload_receipt_binding')
            spec=_source_spec(owner,'work_'+ident,'canonical_source',c['path'],receipt.size,
                              receipt.digest,receipt.frames,receipt.signature)
            spec['_shared_identity']=[base._store_key(owner.store),sid,'canonical_source',batch['id'],ident,work['receipt_id']]
            specs.append(spec)
            ranges=get('recording_ranges','chunk_id=?',(ident,),MAX_SOURCE_REFS)
            _require(ranges,'payload_source_ranges_missing');coverage=[]
            for r in ranges:
                src_id=r['source_id']
                if src_id not in sources:
                    _require(len(sources)<MAX_SOURCE_REFS,'payload_source_ref_capacity')
                    src=_one(get('recording_sources','id=?',(src_id,),1),'payload_source_missing')
                    _require(src['batch_id']==batch['id'] and src['source_start_frame'] is not None
                             and src['format']=='wav-pcm16le-16000-mono','payload_source_binding')
                    obj=_source_spec(owner,'source_'+src_id,'recording_source',src['path'],src['bytes'],src['digest'],src['frames'])
                    obj['_shared_identity']=[base._store_key(owner.store),sid,'recording_source',batch['id'],src_id]
                    sources[src_id]=(src,obj);specs.append(obj)
                src,obj=sources[src_id]
                _require(r['batch_id']==batch['id']==src['batch_id'] and r['relation'] in ('owner','exact_alias')
                         and type(r['frames']) is int and r['frames']>0
                         and 0<=r['work_start_frame']<r['work_start_frame']+r['frames']<=receipt.frames
                         and 0<=r['source_start_frame']<r['source_start_frame']+r['frames']<=src['frames']
                         and r['capture_start_frame']==work['capture_start_frame']+r['work_start_frame']
                         and r['capture_start_frame']==src['source_start_frame']+r['source_start_frame'],
                         'payload_source_range_binding')
                _range_equal(owner,spec,obj,r)
                if r['relation']=='owner':coverage.append((r['work_start_frame'],r['work_start_frame']+r['frames']))
            cursor=0
            for lo,hi in sorted(coverage):_require(lo==cursor,'payload_source_coverage');cursor=hi
            _require(cursor==receipt.frames,'payload_source_coverage')
            stable_chunk={k:c[k] for k in ('id','session_id','digest','path','duration','timeline_start')}
            stable_work={k:work[k] for k in ('chunk_id','batch_id','position','frames','bytes','capture_start_frame',
                                           'native_receipt','receipt_id','page_id','file_signature')}
            closure={'chunk':stable_chunk,'work':stable_work,'batch':{k:batch[k] for k in ('id','session_id','capture_id','capture_origin_frame')},
                     'stream':stream,'ranges':ranges}
            closures[ident]=(closure,c,receipt,spec)
            return closures[ident]
        own,_,receipt,spec=source_closure(cid)
        batch=own['batch']
        outcomes=get('strict_recording_outcome','cid=?',(cid,),MAX_OUTCOMES);outcomes.sort(key=lambda x:x['start_frame'])
        cursor=0
        for out in outcomes:
            _require(out['status'] in ('retained','existing','empty','unknown_only') and out['start_frame']==cursor
                     and out['end_frame']>cursor and out['source']==chunk['digest'],'payload_outcome_binding')
            cursor=out['end_frame']
        _require(outcomes and cursor==frames,'payload_terminal_outcome_missing')
        commits=get('native_core_commits','cid=?',(cid,),MAX_OUTCOMES)
        _require(len(commits)==len(outcomes),'payload_compact_count');by_slot={r['slot']:r for r in commits}
        def source_current(part):
            closure,_,r,_=source_closure(part['chunk_id'])
            return (part['session_id']==sid and part['source_sha256']==r.digest
                    and part['receipt_sha256']==r.receipt_id and part['source_frames']==r.frames)
        packet_rows=get(pp.TABLE,'cid=?',(cid,),MAX_OUTCOMES)
        for row in packet_rows:
            _require(row['sid']==sid and row['cid']==cid and row['rid']==batch['id']
                     and all(type(row[k]) is str and 1<=len(row[k])<=64 for k in ('sid','cid','rid'))
                     and type(row['core_slot']) is str and _ID.fullmatch(row['core_slot'])
                     and type(row['digest']) is str and _ID.fullmatch(row['digest'])
                     and type(row['body']) is bytes and 0<len(row['body'])<=pp.MAX_BODY
                     and all(type(row[k]) is int for k in ('sequence','core_start','core_end','charge'))
                     and 0<row['sequence']<=pp.MAX_COUNTER and 0<=row['core_start']<row['core_end']<=frames
                     and 0<row['charge']<=4*pp.MAX_BODY,'payload_packet_header_bound')
        witnesses=_read_core_witnesses(owner,db,cid)
        witness_by_slot={header['slot']:(header,doc) for header,doc in witnesses}
        _require(len(witness_by_slot)==len(witnesses),'payload_witness_duplicate')
        packets=[];packetless=[];used_witnesses=set()
        for out in outcomes:
            row=by_slot.get(out['slot']);_require(row is not None,'payload_compact_missing')
            _require(row['state']=='none' and row['pending'] is None,'payload_external_compact_dependency')
            commit=json.loads(row['body']);core.validate_commit(commit)
            _require(row['sid']==sid and row['cid']==cid and row['scope_key']==core.scope_key(commit['scope'])
                     and row['logical_end']==commit['part']['logical_end']
                     and core.canonical(commit,core.COMPACT_BYTES)==row['body'] and core.slot(commit)==out['slot']
                     and source_current(commit['part']) and commit['part']['chunk_id']==cid
                     and commit['part']['source_start']==out['start_frame'] and commit['part']['source_end']==out['end_frame']
                     and chunk['timeline_start']==float.fromhex(commit['scope']['origin'])
                         +(commit['part']['logical_start']-commit['part']['source_start'])/16000,'payload_compact_binding')
            owner.budget.read(4*pp.MAX_BODY);owner.budget.metadata(2*pp.MAX_BODY+4096)
            saved=pp.read_exact(db,cid=cid,start=out['start_frame'],end=out['end_frame'])
            if out['status'] in ('retained','existing'):
                _require(out['slot'] not in witness_by_slot,'payload_unexpected_packetless_witness')
                _require(saved is not None and saved['header']['digest']==out['packet']
                         and pp.current(db,saved,source_current=source_current),'payload_packet_currentness')
                _require(_pcm_digest(owner,spec,out['start_frame'],out['end_frame'])==saved['packet']['owned_pcm16le_sha256'],
                         'payload_packet_pcm_binding');packets.append(saved)
            else:
                _require(saved is None and out['packet'] is None,'payload_empty_packet')
                pair=witness_by_slot.get(out['slot'])
                _require(pair is not None,'payload_witness_missing')
                header,doc=pair
                _require(doc['binding']==[sid,cid,chunk['digest'],out['nonce'],out['start_frame'],
                         out['end_frame'],out['status'],commit['evidence_sha256'],out['slot']]
                         and doc['commit']==commit,'payload_witness_outcome_binding')
                _require(_pcm_digest(owner,spec,out['start_frame'],out['end_frame'])==doc['owned_pcm16le_sha256'],
                         'payload_witness_pcm_binding')
                used_witnesses.add(out['slot']);packetless.append(doc)
        _require(used_witnesses==set(witness_by_slot),'payload_extra_witness')
        _require(len(packet_rows)==len(packets),'payload_extra_packet')
        segments=get('segments','chunk_id=?',(cid,),MAX_SEGMENTS)
        turns=get('speaker_turns','chunk_id=?',(cid,),MAX_TURNS)
        lexical_refs={}
        for row in segments:
            if row.get('lexical_evidence') is not None:
                draft=json.loads(row['lexical_evidence']);parts=draft.get('source_parts')
                _require(type(parts) is list and 1<=len(parts)<=lexical.MAX_PARTS,'payload_lexical_parts')
                for part in parts:source_closure(part['id'])
                inv=lexical.current_inventory(db,sid,row,source_lookup=lambda ident:closures[ident][1] if ident in closures else None)
                _require(inv is not None,'payload_lexical_current');lexical_refs[row['id']]=inv['source_ref']
        ids={r['id'] for r in segments};segment_by_id={r['id']:r for r in segments}
        turn_by_id={r['id']:r for r in turns};referenced=[];turn_referenced=[]
        _require(len(ids)==len(segments) and len(turn_by_id)==len(turns),'payload_duplicate_logical_id')
        for saved in packets:
            packet=saved['packet'];offset=packet['commit']['part']['source_start']/16000
            for pref,lref in packet['refs']:
                ident=int(pref.split('.')[1]);_require(ident in ids,'payload_primary_missing');referenced.append(ident)
                if lref is not None:_require(lexical_refs.get(ident)==lref,'payload_lexical_binding')
            # Match each original committed window separately. The stored ID
            # partition is complete; duplicate identical native turns count.
            selected=[r for r in turns if r['speaker_window_start']==offset]
            owner.budget.projection(4096+min(len(packet['turns']),MAX_TURNS)*512)
            _require(Counter(projected_turns(packet))==Counter(
                     (r['start'],r['end'],r['speaker_local_id']) for r in selected),'payload_turn_geometry')
            turn_referenced.extend(r['id'] for r in selected)
        for doc in packetless:
            offset=doc['binding'][4]/16000
            for ref in doc['turn_refs']:
                row=turn_by_id.get(ref[0])
                _require(row is not None and [row[field] for field in TURN_FIELDS]==ref,
                         'payload_witness_turn_row_changed')
                turn_referenced.append(ref[0])
            for ref,captured in zip(doc['primary_refs'],doc['captured_rows']):
                row=segment_by_id.get(ref[0])
                _require(row is not None and row['speaker_window_start']==offset and row['speaker_local_id'] is None,
                         'payload_witness_unknown_row')
                text_fields=('text','raw_text','asr_backend','timestamp_kind','speaker_local_id')
                _require(all(row[field] is None or type(row[field]) is str for field in text_fields),
                         'payload_witness_unknown_row')
                owner.budget.projection(768+4*sum(len(row[field]) if row[field] is not None else 0
                                                for field in text_fields))
                content={field:row[field] for field in contract.PRIMARY_FIELDS if field!='id'}
                size=256+sum(len(content[field].encode('utf-8')) if content[field] is not None else 0
                             for field in text_fields)
                _require(size==captured[2] and pp.digest(content)==captured[0]==ref[1],
                         'payload_witness_unknown_row')
                referenced.append(ref[0])
        _require(len(referenced)==len(set(referenced)) and set(referenced)==ids,'payload_primary_unbound')
        _require(len(turn_referenced)==len(set(turn_referenced)) and set(turn_referenced)==set(turn_by_id),
                 'payload_turn_unbound')
        supports=get('speaker_support','chunk_id=?',(cid,),MAX_SUPPORTS,exclude=('pcm',));support_rows=[];links=[];vector_rows=[]
        observations=get('speaker_observations','chunk_id=?',(cid,),MAX_SUPPORTS)
        observation_ids={r['id'] for r in observations}
        generation=_one(get('speaker_identity_generations','identity_group=?',(session['identity_group'],),1),'payload_generation')
        for row in supports:
            if row['status']!='available' or row['pcm_bytes']==0:continue
            m=json.loads(row['manifest']);support.validate_manifest(m)
            _require(row['session_id']==sid and row['identity_group']==session['identity_group']
                     and row['generation']==generation['generation'] and 0<row['pcm_bytes']<=support.MAX_PCM_BYTES
                     and row['pcm_bytes']==m['support']['pooled_sample_count']*4
                     and _sha(support.canonical(m))==row['manifest_sha256']
                     and (row['observation_id'] is None or row['observation_id'] in observation_ids),'payload_support_binding')
            for part in m['source_parts']:
                _,c,_,_=source_closure(part['chunk_id'])
                _require(part['source_sha256']==c['digest'],'payload_support_source')
            linked=get('speaker_support_sources','support_id=?',(row['id'],),MAX_PARTS)
            _require({r['chunk_id'] for r in linked}=={p['chunk_id'] for p in m['source_parts']},'payload_support_sources')
            links.extend(linked)
            digest=_blob_hash(owner,db,'speaker_support','pcm',row['id'],row['pcm_bytes'],finite=True)
            _require(digest==m['support']['pooled_pcm_sha256'],'payload_support_pcm')
            specs.append({'object_id':'pcm_'+row['id'],'kind':'pcm','owner_id':row['id'],'bytes':row['pcm_bytes'],'sha256':digest,
                          '_table':'speaker_support','_column':'pcm','_key':row['id']});support_rows.append(row)
            headers=get('speaker_support_vectors','support_id=?',(row['id'],),1,exclude=('raw_vector',))
            if not headers:continue
            header=headers[0]
            n=db.execute('SELECT length(raw_vector),length(CAST(space AS BLOB)) FROM speaker_support_vectors WHERE id=?',(header['id'],)).fetchone()
            _require(n is not None and type(n[0]) is int and 0<n[0]<=65536 and 0<n[1]<=16384,'payload_vector_bound')
            projected_bytes=len(_canonical(_jsonable(header)))+len(_canonical({k:row[k] for k in ('identity_group','generation','manifest','manifest_sha256')}))
            owner.budget.read(n[0]+projected_bytes);owner.budget.metadata(projected_bytes)
            vector,_=vectors.read(db,row['id'])
            _require({k:v for k,v in vector.items() if k!='raw_vector'}==header,'payload_vector_current')
            specs.append({'object_id':'vector_'+header['id'],'kind':'vector','owner_id':header['id'],
                          'bytes':len(vector['raw_vector']),'sha256':header['vector_sha256'],
                          '_table':'speaker_support_vectors','_column':'raw_vector','_key':header['id']});vector_rows.append(header)
        _require(support_rows,'payload_no_hot_body')
        meta={'session':{'id':sid,'identity_group':session['identity_group']},'policy':policy,'control':control,
              'outcomes':outcomes,'commits':commits,'packets':packets,'packet_rows':packet_rows,
              'core_witnesses':[row for row,_ in witnesses],
              'segments':segments,'turns':turns,'supports':support_rows,'vectors':vector_rows,'support_sources':links,
              'generation':generation,'observations':observations,'source_closure':[closures[k][0] for k in sorted(closures)],
              'original_sources':[sources[k][0] for k in sorted(sources)],
              'logical_context':'Identity/review/history bodies remain authoritative in live DB; current result-row extras are snapshots, not new packet certificates.'}
    _require(len(specs)<=MAX_OBJECTS and sum(s['bytes'] for s in specs)<=MAX_PAYLOAD,'payload_bundle_capacity')
    specs.sort(key=lambda s:s['object_id'])
    public=[{k:s[k] for k in ('object_id','kind','owner_id','bytes','sha256') if k in s} for s in specs]
    source_storage=getattr(owner.store,'_strict_source_storage','inline')
    if source_storage=='shared-source-v1':
        from . import strict_shared_sources as shared
        for spec,obj in zip(specs,public):
            if spec['kind'] in ('recording_source','canonical_source'):
                obj['shared_source']=shared.reference(owner.store,spec)
    _require(len({s['object_id'] for s in public})==len(public),'payload_duplicate_object')
    manifest={'format':format_for_storage(source_storage),'policy':POLICY,'store_key':base._store_key(owner.store),'cid':cid,
              'source':chunk['digest'],'tip':frames,'metadata':_jsonable(meta),'objects':public}
    raw=_canonical(manifest);owner.budget.metadata(len(raw));_require(len(raw)<=MAX_METADATA,'payload_manifest_capacity')
    return manifest,specs


def _evidence_hash(manifest):
    # Full snapshot remains in the immutable archive. These named live identity
    # presentation fields alone are excluded from resume authority; every source,
    # generation, Receipt, range, original packet/primary/lexical binding and
    # support/vector byte/space binding remains included and freshly validated.
    mutable={'speaker_id','speaker_match_score','speaker_status','identity_decision_id',
             'identity_profile_revision','identity_state'}
    meta=dict(manifest['metadata'])
    for table in ('segments','turns'):
        meta[table]=[{k:v for k,v in row.items() if k not in mutable} for row in meta[table]]
    meta['observations']=[{k:v for k,v in row.items() if k not in ('enrolled_speaker_id','matched_speaker_id')}
                          for row in meta['observations']]
    stable=dict(manifest);stable['metadata']=meta
    return _sha(_canonical(stable))


def _snapshot(value):
    _require(type(value) is MappingProxyType and all(k in value for k in ('sid','stream_id','frontier_frame','roots')),
             'payload_frontier_shape')
    _require(type(value['sid']) is str and 1<=len(value['sid'])<=128
             and type(value['stream_id']) is str and 1<=len(value['stream_id'])<=128
             and type(value['frontier_frame']) is int and value['frontier_frame']>=0
             and type(value['roots']) is frozenset and len(value['roots'])<=MAX_ROOTS
             and all(type(x) is str and 1<=len(x)<=128 for x in value['roots']),'payload_frontier_shape')
    return value


def _eligible(db,cid,snapshot):
    snapshot=_snapshot(snapshot)
    row=db.execute('''SELECT c.timeline_start+c.duration AS end_time,
      c.status,c.next_offset,c.duration,k.state,k.nonce,c.session_id,s.stream_id
      FROM chunks c JOIN recording_work w ON w.chunk_id=c.id JOIN recording_batches b ON b.id=w.batch_id
      JOIN stream_chunks s ON s.chunk_id=c.id JOIN strict_recording_control k ON k.cid=c.id WHERE c.id=?''',(cid,)).fetchone()
    from .strict_recording import frame
    return (row is not None and row['session_id']==snapshot['sid'] and row['stream_id']==snapshot['stream_id']
            and frame(row['end_time'])<=snapshot['frontier_frame']
            and row['status']=='completed' and row['next_offset']==row['duration']
            and row['state']=='completed' and row['nonce'] is None and cid not in snapshot['roots'])


def maintain_payloads(store,witness,budget):
    from .native_owned_cell_capture import PayloadFrontier
    _require(type(witness) is PayloadFrontier and witness.store is store,'payload_frontier_owner')
    with _operation(store,budget) as owner:
        saved=None
        with _db(owner,write=True,witness=witness) as (db,snapshot):
            snapshot=_snapshot(snapshot)
            roots=sorted(snapshot['roots']);omit=''
            budget.projection(4096+sum(len(x) for x in roots))
            args=[snapshot['sid'],snapshot['stream_id'],snapshot['frontier_frame']]
            if roots:omit=' AND c.id NOT IN ('+','.join('?' for _ in roots)+')';args.extend(roots)
            if (getattr(store,'_strict_source_storage','inline')!='inline'
                    or getattr(store,'_strict_lifetime_records',2056)!=2056):
                row=db.execute(HOT_CANDIDATE_SQL+omit+' ORDER BY c.seq LIMIT 1',args).fetchone()
            else:
                row=db.execute("""SELECT c.id FROM chunks c JOIN recording_work w ON w.chunk_id=c.id
                  JOIN recording_batches b ON b.id=w.batch_id JOIN stream_chunks s ON s.chunk_id=c.id
                  JOIN strict_recording_control k ON k.cid=c.id WHERE c.session_id=? AND s.stream_id=?
                  AND (c.timeline_start+c.duration)*16000<=?
                  AND c.status='completed' AND c.next_offset=c.duration AND k.state='completed' AND k.nonce IS NULL
                  AND EXISTS(SELECT 1 FROM speaker_support p WHERE p.chunk_id=c.id AND p.status='available'
                    AND p.pcm_bytes>0 AND p.pcm IS NOT NULL)"""+omit+' ORDER BY c.seq LIMIT 1',args).fetchone()
            if row is None:return {'state':'idle','reason':'no_eligible_payload','retired_units':0}
            cid=row[0]
            previous=db.execute('SELECT * FROM strict_payload_unit WHERE cid=?',(cid,)).fetchone()
            if previous is not None:
                saved=dict(previous)
                if saved['state']!='published':raise PayloadCapacityError('payload_reserved_unit_pending')
        manifest,specs=_collect(owner,cid);raw=_canonical(manifest)
        if manifest['format']==SHARED_FORMAT:
            from . import strict_shared_sources as shared
            for spec,obj in zip(specs,manifest['objects']):
                if 'shared_source' in obj:
                    _require(shared.ensure(owner,spec)==obj['shared_source'],'payload_shared_reference')
        inline_specs=[spec for spec,obj in zip(specs,manifest['objects']) if 'shared_source' not in obj]
        evidence=_evidence_hash(manifest)
        targets=[s for s in specs if s['kind'] in ('pcm','vector')]
        if saved is None:
            unit_id=_sha(raw);manifest_bytes=len(raw)
            size=len(MAGIC)+4+manifest_bytes+sum(x['bytes'] for x in inline_specs)
            _require(size<=MAX_UNIT_BYTES,'payload_unit_capacity')
            # Durable reservation is not retirement authority. Failed/reserved
            # units remain charged; only a positive published receipt may resume.
            with _db(owner,write=True,witness=witness) as (db,current):
                _require(_eligible(db,cid,current),'payload_frontier_changed')
                _require(not ({c['chunk']['id'] for c in manifest['metadata']['source_closure']} & current['roots']),
                         'payload_source_root_active')
                last=db.execute('SELECT sequence FROM strict_payload_unit ORDER BY sequence DESC LIMIT 1').fetchone()
                sequence=1 if last is None else last[0]+1
                _require(sequence<=strict_lifetime.read_limit(db),'payload_existing_chunk_capacity');bucket=(sequence-1)//BUCKET_UNITS
                db.execute('INSERT INTO strict_payload_unit VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (sequence,unit_id,cid,manifest['store_key'],evidence,manifest_bytes,bucket,
                            'reserved',unit_id,size,None,None))
            path,directory=_bucket(owner,bucket,create=True);_reserve_bucket(owner,directory,size)
            stage=path/(unit_id+'.stage');final=path/(unit_id+'.unit')
            fd=_open_file(owner,stage,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
            try:
                _write_bytes(owner,fd,MAGIC+struct.pack('>I',manifest_bytes)+raw)
                for spec in inline_specs:base._copy_spec(owner,fd,spec)
                os.fsync(fd)
            except BaseException as error:owner.remember(error);raise
            finally:_close(owner,fd)
            file_hash,_=_file_digest(owner,stage,size)
            try:
                os.link(stage,final,follow_symlinks=False);os.fsync(directory)
                os.unlink(stage);os.fsync(directory)
            except BaseException as error:owner.remember(error);raise
            actual,signature=_file_digest(owner,final,size)
            _require(actual==file_hash,'payload_publication_changed');_close(owner,directory)
            # Positive file+directory close and then positive checkpoint COMMIT
            # permit only exact same-unit reconciliation, never replacement.
            with _db(owner,write=True) as db:
                owner.transition_db=db;owner.transition={('unit',unit_id)}
                try:
                    _require(db.execute("UPDATE strict_payload_unit SET state='published',file_sha256=?,signature=? WHERE unit_id=? AND state='reserved'",
                        (file_hash,_canonical(signature).decode(),unit_id)).rowcount==1,'payload_reservation_changed')
                finally:owner.transition.clear();owner.locators.clear();owner.transition_db=None
        else:
            _require(saved['store_key']==manifest['store_key'] and saved['evidence_sha256']==evidence,
                     'payload_published_source_changed')
            unit_id=saved['unit_id'];manifest_bytes=saved['manifest_bytes'];size=saved['bytes'];bucket=saved['bucket']
            _require(size==len(MAGIC)+4+manifest_bytes+sum(x['bytes'] for x in inline_specs),'payload_published_size')
            path,directory=_bucket(owner,bucket,create=False);_close(owner,directory)
            file_hash,signature=_file_digest(owner,path/(unit_id+'.unit'),size,json.loads(saved['signature']))
            _require(file_hash==saved['file_sha256'],'payload_published_file_changed')
            # The full existing file hash authenticates its preserved original
            # snapshot; no new manifest/file/reservation is written on resume.
        fresh,_=_collect(owner,cid)
        _require(_evidence_hash(fresh)==evidence,'payload_source_changed')
        shared_checks=[]
        if manifest['format']==SHARED_FORMAT:
            for obj in manifest['objects']:
                if 'shared_source' in obj:
                    ref=obj['shared_source'];body=shared.validate(owner,ref)
                    directory,fd=shared._directory(owner,ref['body_id'],create=False);_close(owner,fd)
                    shared_checks.append((directory/(ref['body_id']+'.body'),json.loads(body['signature'])))
        with _db(owner,write=True,witness=witness) as (db,current):
            _require(_eligible(db,cid,current),'payload_frontier_changed')
            _require(not ({c['chunk']['id'] for c in manifest['metadata']['source_closure']} & current['roots']),
                     'payload_source_root_active')
            for spec in specs:
                if '_path' in spec:
                    _require(_signature(os.stat(spec['_path'],follow_symlinks=False))==spec['_signature'],
                             'payload_source_file_changed')
            for path,body_signature in shared_checks:
                _require(_signature(os.stat(path,follow_symlinks=False))==body_signature,'payload_shared_source_changed')
            budget.projection(1408*len(manifest['metadata']['source_closure']))
            for closure in manifest['metadata']['source_closure']:
                work=closure['work']
                current_work=db.execute('SELECT native_receipt,receipt_id,file_signature FROM recording_work WHERE chunk_id=?',
                                        (work['chunk_id'],)).fetchone()
                expected=work['native_receipt']
                if isinstance(expected,dict):expected=bytes.fromhex(expected['$bytes'])
                _require(current_work is not None and tuple(current_work)==(expected,work['receipt_id'],work['file_signature']),
                         'payload_receipt_changed')
            owner.transition_db=db;owner.transition={('unit',unit_id)}|{(x['kind'],x['owner_id']) for x in targets}
            planned_offset=len(MAGIC)+4+manifest_bytes
            for obj in inline_specs:
                if obj['kind'] in ('pcm','vector'):
                    owner.locators[(obj['kind'],obj['owner_id'])]=(obj['kind'],obj['owner_id'],unit_id,
                        obj['object_id'],obj['bytes'],obj['sha256'],planned_offset)
                planned_offset+=obj['bytes']
            _require(planned_offset==size,'payload_locator_plan')
            try:
                changed=db.execute("UPDATE strict_payload_unit SET state='committed' WHERE unit_id=? AND state='published' AND evidence_sha256=? AND file_sha256=? AND signature=?",
                    (unit_id,evidence,file_hash,_canonical(signature).decode())).rowcount
                _require(changed==1,'payload_publication_changed')
                offset=len(MAGIC)+4+manifest_bytes
                for spec in inline_specs:
                    if spec['kind'] in ('pcm','vector'):
                        db.execute('INSERT INTO strict_payload_object VALUES(?,?,?,?,?,?,?)',
                                   (spec['kind'],spec['owner_id'],unit_id,spec['object_id'],spec['bytes'],spec['sha256'],offset))
                        changed=db.execute('UPDATE '+spec['_table']+' SET '+spec['_column']+'=NULL WHERE id=? AND length('+spec['_column']+')=?',
                                           (spec['_key'],spec['bytes'])).rowcount
                        _require(changed==1,'payload_hot_body_changed')
                    offset+=spec['bytes']
                _require(offset==size,'payload_offsets')
            finally:owner.transition.clear();owner.locators.clear();owner.transition_db=None
        return {'state':'committed','retired_units':1,'cid':cid,'unit_id':unit_id,'bucket':bucket,
                'resumed':saved is not None,'cold_bytes':sum(x['bytes'] for x in targets),'archive_bytes':size}
