"""Opt-in, finalized recording batches feeding the unchanged legacy worker.

A capture ID is an explicit shared PCM sample-coordinate declaration, never a
wall-clock or microphone identity inference. Original WAV files are retained.
Only byte-equal, declared overlap is aliased. A single unresolved source blocks
all work; original and derived frame ranges remain distinct durable receipts.
"""
from __future__ import annotations

import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
import threading
import uuid
import wave

from .fragment_core import inspect_pcm16_source
from .resources import check_resources
from .native_owned_cells import Unavailable

RATE = 16000
BLOCK_FRAMES = 32768
CHUNK_FRAMES = 168000  # 10.5 seconds; only the final piece may be shorter.
MAX_SOURCES = 16
MAX_CHUNKS = 32
MAX_SOURCE_FRAMES = 120 * RATE
MAX_CAPTURE_FRAMES = 300 * RATE
MAX_SOURCE_BYTES = MAX_SOURCE_FRAMES * 2 + 65536
MAX_BATCH_BYTES = 32 * 1024**2
MAX_TOTAL_BYTES = 128 * 1024**2
MAX_BATCHES = 64
METADATA_RESERVATION = 512 * 1024  # Bounded receipts, manifest, maps and SQLite projection.
MAX_COORDINATE = (1 << 53) - 1
FORMAT = 'wav-pcm16le-16000-mono'
POLICY = 'finalized-recording-exact-overlap-v1'
PAGE_POLICY = 'sealed-recording-exact-overlap-v1'
MAX_PAGES = 1024
MAX_PAGE_CHUNKS = 28
MAX_HISTORY_PER_SOURCE = 13
MAX_HISTORY_REFS = 208
RECEIPT_VISIT_STEPS = 2
RECEIPT_VISIT_SECONDS = .025
_RECEIPT_DURABLE_PROGRESS = object()
BOUNDED_COLUMNS = {
    'native_core_ordinal': 'INTEGER CHECK(native_core_ordinal IS NULL OR native_core_ordinal>=0)',
    'retention_store_key': 'TEXT CHECK(retention_store_key IS NULL OR length(retention_store_key)=64)',
}


def init_bounded_columns(db):
    """Called only by the encompassing all-or-none v2 automatic migration."""
    if not db.in_transaction:raise ValueError('Bounded recording migration requires transaction')
    for name,definition in BOUNDED_COLUMNS.items():
        db.execute(f'ALTER TABLE recording_batches ADD COLUMN {name} {definition}')


PRIMARY_CACHE_CORE_SCHEMA = {'native_core_commits': 'CREATE TABLE native_core_commits (\n'
                        '        slot TEXT PRIMARY KEY, sid TEXT NOT NULL, cid TEXT NOT NULL REFERENCES '
                        'chunks(id) ON DELETE CASCADE,\n'
                        '        scope_key TEXT NOT NULL, logical_end INTEGER NOT NULL,\n'
                        "        body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body) BETWEEN 1 AND "
                        '2048),\n'
                        "        pending BLOB CHECK(pending IS NULL OR (typeof(pending)='blob' AND "
                        'length(pending) BETWEEN 1 AND 131072)),\n'
                        '        state TEXT NOT NULL CHECK(state IN '
                        "('none','pending','published','gap','cancelled')),\n"
                        '        reason TEXT NOT NULL CHECK(length(reason)<=80))',
 'native_core_adjacent': 'CREATE INDEX native_core_adjacent ON '
                         'native_core_commits(sid,scope_key,logical_end)',
 'native_core_pending': 'CREATE INDEX native_core_pending ON native_core_commits(state,slot)',
 'native_core_cancel': 'CREATE TRIGGER native_core_cancel AFTER UPDATE OF status ON chunks\n'
                       "        WHEN NEW.status='cancelled' BEGIN\n"
                       '          UPDATE native_core_commits SET '
                       "state='cancelled',pending=NULL,reason='primary_cancelled'\n"
                       "          WHERE sid=NEW.session_id AND state='pending'; END",
 'native_core_delete': 'CREATE TRIGGER native_core_delete AFTER UPDATE OF deleted ON sessions\n'
                       '        WHEN NEW.deleted=1 BEGIN DELETE FROM native_core_commits WHERE sid=NEW.id; '
                       'END'}
PRIMARY_CACHE_BATCH_SCHEMA = ('CREATE TABLE recording_batches (\n'
 '        id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id),\n'
 '        capture_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,\n'
 "        state TEXT NOT NULL DEFAULT 'open', finish_key TEXT, finish_request TEXT,\n"
 '        capture_origin_frame INTEGER, capture_end_frame INTEGER,\n'
 "        manifest TEXT, blockers TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, mode TEXT NOT NULL "
 "DEFAULT 'legacy', eos INTEGER NOT NULL DEFAULT 0,\n"
 '        UNIQUE(session_id,capture_id))')

# Fresh exclusive-primary boundary. This changes the usage-table contract so
# older binaries fail their existing exact DDL check before accepting work.
PRIMARY_CACHE_POLICY = 'primary-cache-retirement-v1'
PRIMARY_CACHE_COLUMNS = {
    'primary_cache_policy': "TEXT CHECK(primary_cache_policy IS NULL OR primary_cache_policy='primary-cache-retirement-v1')",
    'primary_cache_store_key': "TEXT CHECK(primary_cache_store_key IS NULL OR (primary_cache_policy IS 'primary-cache-retirement-v1' AND typeof(primary_cache_store_key)='text' AND length(primary_cache_store_key)=64 AND primary_cache_store_key NOT GLOB '*[^0-9a-f]*'))",
}


def _cache_sql(sql):
    # Ignore formatting only; whitespace inside SQL literals is semantic.
    return re.sub(r"'(?:''|[^'])*'|\s+",
        lambda match:match.group(0) if match.group(0).startswith("'") else '',sql)


def _cache_add_columns(sql, columns):
    # SQLite places ADD COLUMN before existing table-level constraints.
    constraint=re.search(r',\s*(?:CHECK|UNIQUE)\s*\(',sql)
    at=constraint.start() if constraint is not None else len(sql)-1
    return sql[:at] + ', ' + ', '.join(name+' '+definition for name,definition in columns.items()) + sql[at:]


def _cache_marked_session(expression):
    return f"EXISTS(SELECT 1 FROM recording_batches b WHERE b.session_id={expression} AND b.primary_cache_policy IS NOT NULL)"


def _cache_marked_cid(expression):
    return (f"EXISTS(SELECT 1 FROM chunks c JOIN recording_batches b ON b.session_id=c.session_id "
        f"WHERE c.id={expression} AND b.primary_cache_policy IS NOT NULL)")


def _cache_marked_slot(expression):
    return (f"EXISTS(SELECT 1 FROM native_core_commits n LEFT JOIN chunks c ON c.id=n.cid "
        f"JOIN recording_batches b ON b.session_id=n.sid OR b.session_id=c.session_id "
        f"WHERE n.slot={expression} AND b.primary_cache_policy IS NOT NULL)")


def _cache_guard_ddl():
    from .recording_primary_provenance import USAGE
    from .moss_automatic import TABLE, DEPENDENCIES
    result = {
        'primary_cache_batch_insert': f'''CREATE TRIGGER primary_cache_batch_insert BEFORE INSERT ON recording_batches
            WHEN NEW.primary_cache_policy IS NOT NULL BEGIN
            SELECT CASE WHEN NEW.mode!='sealed_pages' OR NEW.primary_cache_store_key IS NOT NULL
                OR NEW.native_core_ordinal IS NOT NULL OR NEW.retention_store_key IS NOT NULL
                OR EXISTS(SELECT 1 FROM chunks WHERE session_id=NEW.session_id LIMIT 1)
                OR EXISTS(SELECT 1 FROM {TABLE} INDEXED BY auto_session_seq WHERE sid=NEW.session_id LIMIT 1)
                OR EXISTS(SELECT 1 FROM native_core_commits INDEXED BY native_core_adjacent WHERE sid=NEW.session_id LIMIT 1)
                THEN RAISE(ABORT,'Primary cache requires fresh exclusive session') END; END''',
        'primary_cache_batch_update': '''CREATE TRIGGER primary_cache_batch_update BEFORE UPDATE ON recording_batches
            WHEN NEW.primary_cache_policy IS NOT OLD.primary_cache_policy
                OR (OLD.primary_cache_policy IS NOT NULL AND (
                    NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id OR NEW.mode IS NOT OLD.mode
                    OR NEW.native_core_ordinal IS NOT NULL OR NEW.retention_store_key IS NOT NULL
                    OR (OLD.primary_cache_store_key IS NOT NULL AND NEW.primary_cache_store_key IS NOT OLD.primary_cache_store_key)))
            BEGIN SELECT RAISE(ABORT,'Immutable primary cache scope'); END''',
        'primary_cache_charge_insert': f'''CREATE TRIGGER primary_cache_charge_insert AFTER INSERT ON recording_batches
            WHEN NEW.primary_cache_policy IS NOT NULL BEGIN
            SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM {USAGE} WHERE scope='' AND cache_scope_charge<=32256)
                OR EXISTS(SELECT 1 FROM {USAGE} WHERE scope=NEW.id)
                THEN RAISE(ABORT,'Primary cache accounting mismatch') END;
            UPDATE {USAGE} SET charge=charge+512,cache_scope_charge=cache_scope_charge+512 WHERE scope='';
            INSERT INTO {USAGE}(scope,packets,charge,cache_scope_charge) VALUES(NEW.id,0,512,512); END''',
        'primary_cache_charge_delete': f'''CREATE TRIGGER primary_cache_charge_delete AFTER DELETE ON recording_batches
            WHEN OLD.primary_cache_policy IS NOT NULL BEGIN
            SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM {USAGE} WHERE scope='' AND charge>=512 AND cache_scope_charge>=512)
                OR NOT EXISTS(SELECT 1 FROM {USAGE} WHERE scope=OLD.id AND charge>=512 AND cache_scope_charge=512)
                THEN RAISE(ABORT,'Primary cache accounting mismatch') END;
            UPDATE {USAGE} SET charge=charge-512,cache_scope_charge=cache_scope_charge-512 WHERE scope IN ('',OLD.id);
            DELETE FROM {USAGE} WHERE scope=OLD.id AND packets=0 AND charge=0; END''',
    }
    intent = ' OR '.join((_cache_marked_session('NEW.sid'),
        "EXISTS(SELECT 1 FROM recording_batches b WHERE b.id=NEW.plan_batch_id AND b.primary_cache_policy IS NOT NULL)",
        *(_cache_marked_cid('NEW.'+name) for name in ('cid1','cid2')),
        *(_cache_marked_slot('NEW.'+name) for name in ('slot1','slot2'))))
    dependency = ' OR '.join((_cache_marked_cid('NEW.cid'), _cache_marked_slot('NEW.slot')))
    core = '('+' OR '.join((_cache_marked_session('NEW.sid'),_cache_marked_cid('NEW.cid')))+')'
    invalid = "(NEW.pending IS NOT NULL OR NEW.state IN ('pending','published') OR NEW.recording_batch_id IS NOT NULL OR NEW.commit_ordinal IS NOT NULL OR NEW.retention_store_key IS NOT NULL)"
    for operation in ('INSERT','UPDATE'):
        lower=operation.lower()
        for kind,table,predicate in (('intent',TABLE,intent), ('dependency',DEPENDENCIES,dependency),
                ('core','native_core_commits',core+' AND '+invalid)):
            name='primary_cache_'+kind+'_'+lower
            result[name]=f'''CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {predicate}
                BEGIN SELECT RAISE(ABORT,'Exclusive primary cache forbids MOSS ownership'); END'''
    return result


def _cache_read_sql(db, name, expected, budget=None):
    row=db.execute('SELECT length(CAST(sql AS BLOB)) FROM sqlite_master WHERE name=?',(name,)).fetchone()
    if row is None or type(row[0]) is not int or not 1<=row[0]<=max(4096,2*len(expected.encode('utf-8'))):
        raise ValueError('Missing or oversized primary cache schema: '+name)
    if budget is not None:budget.projection(row[0]+128)
    actual=db.execute('SELECT sql FROM sqlite_master WHERE name=? AND length(CAST(sql AS BLOB))=?',
        (name,row[0])).fetchone()
    if actual is None:raise ValueError('Primary cache schema changed')
    return actual[0]


def _cache_prerequisites(db, budget=None):
    """Validate existing contracts; never install missing prerequisite tables."""
    from .moss_automatic import SCHEMA, V2_SCHEMA, V2_COLUMNS, COMPACT_V2_COLUMNS
    objects = dict(SCHEMA)
    from .moss_automatic import TABLE
    objects[TABLE]=_cache_add_columns(objects[TABLE],V2_COLUMNS)
    objects.update(V2_SCHEMA)
    objects.update(PRIMARY_CACHE_CORE_SCHEMA)
    objects['native_core_commits']=_cache_add_columns(objects['native_core_commits'],COMPACT_V2_COLUMNS)
    for name,expected in objects.items():
        actual=_cache_read_sql(db,name,expected,budget)
        if _cache_sql(actual) != _cache_sql(expected):
            raise ValueError('Incompatible primary cache prerequisite: '+name)


def primary_cache_schema(db, store=None, budget=None):
    """Schema-cookie invalidation makes removed/changed guards lose authority."""
    version=db.execute('PRAGMA schema_version').fetchone()[0]
    if budget is not None:budget.projection(16)
    saved=getattr(store,'_primary_cache_schema_token',None) if store is not None else None
    if saved is not None and saved[0]==version:return saved[1]
    from .recording_primary_provenance import USAGE, CACHE_USAGE_DDL, DDL
    guards=_cache_guard_ddl()
    if budget is not None:budget.projection(33*128)
    fields=db.execute("""SELECT CASE WHEN length(CAST(name AS BLOB)) BETWEEN 1 AND 128
        THEN name ELSE NULL END FROM pragma_table_info('recording_batches') LIMIT 33""").fetchall()
    if len(fields)>32 or any(row[0] is None for row in fields):raise ValueError('Unknown primary cache batch schema')
    fields={row[0] for row in fields}
    usage=_cache_read_sql(db,USAGE,CACHE_USAGE_DDL,budget)
    if budget is not None:budget.projection(128*(len(guards)+1))
    names=db.execute("""SELECT CASE WHEN length(CAST(name AS BLOB)) BETWEEN 1 AND 128
        THEN name ELSE NULL END FROM sqlite_master WHERE name GLOB 'primary_cache_*' LIMIT ?""",
        (len(guards)+1,)).fetchall()
    if any(row[0] is None for row in names):raise ValueError('Unknown primary cache guard name')
    names={row[0] for row in names}
    flags=[name in fields for name in PRIMARY_CACHE_COLUMNS]+[usage==CACHE_USAGE_DDL]+[bool(names)]
    if not any(flags):
        if usage!=DDL[USAGE]:raise ValueError('Unknown primary cache usage schema')
        installed=False
    else:
        if not all(flags) or names!=set(guards):raise ValueError('Incomplete primary cache retirement schema')
        _cache_prerequisites(db,budget)
        for name,expected in DDL.items():
            expected=CACHE_USAGE_DDL if name==USAGE else expected
            if _cache_read_sql(db,name,expected,budget)!=expected:
                raise ValueError('Incompatible primary cache provenance schema')
        expected=_cache_add_columns(_cache_add_columns(PRIMARY_CACHE_BATCH_SCHEMA,BOUNDED_COLUMNS),PRIMARY_CACHE_COLUMNS)
        if _cache_sql(_cache_read_sql(db,'recording_batches',expected,budget))!=_cache_sql(expected):
            raise ValueError('Incompatible primary cache batch schema')
        for name,expected in guards.items():
            if _cache_sql(_cache_read_sql(db,name,expected,budget))!=_cache_sql(expected):
                raise ValueError('Incompatible primary cache retirement guard')
        installed=True
    if db.execute('PRAGMA schema_version').fetchone()[0]!=version:
        raise ValueError('Primary cache schema changed during validation')
    if store is not None:store._primary_cache_schema_token=(version,installed)
    return installed


def init_primary_cache_schema(db, *, enable=False):
    from .recording_primary_provenance import USAGE, CACHE_USAGE_COLUMN, primary_cache_usage
    if not db.in_transaction:raise ValueError('Primary cache migration requires transaction')
    if primary_cache_schema(db):
        primary_cache_usage(db)
        return
    if not enable:return
    _cache_prerequisites(db)
    batch=db.execute("SELECT sql FROM sqlite_master WHERE name='recording_batches'").fetchone()
    if batch is None or _cache_sql(batch[0])!=_cache_sql(_cache_add_columns(PRIMARY_CACHE_BATCH_SCHEMA,BOUNDED_COLUMNS)):
        raise ValueError('Incompatible primary cache batch prerequisite')
    for name,definition in PRIMARY_CACHE_COLUMNS.items():
        db.execute(f'ALTER TABLE recording_batches ADD COLUMN {name} {definition}')
    db.execute(f'ALTER TABLE {USAGE} ADD COLUMN cache_scope_charge {CACHE_USAGE_COLUMN}')
    for sql in _cache_guard_ddl().values():db.execute(sql)
    if not primary_cache_schema(db):raise ValueError('Missing primary cache retirement contract')
    primary_cache_usage(db)


def primary_cache_capable(store):
    from .settings import primary_only_enabled
    from .moss_automatic import Automatic
    from .moss_handy_jobs import MossJobs
    from .native_owned_cell_jobs import CellJobsSource
    automatic=getattr(store,'moss_automatic',None)
    if (not primary_only_enabled(store.settings) or type(automatic) is not Automatic
            or automatic.store is not store or not automatic.primary_only or not automatic.coordinator_current()):return False
    capture=automatic.capture;jobs=automatic.jobs
    return (type(jobs) is MossJobs and jobs.store is store and jobs.automatic is automatic
        and type(jobs.cell_source) is CellJobsSource and jobs.cell_source.owner is capture.owner
        and jobs.cell_source.store is store and capture.authority.store is store
        and capture.owner.authority is capture.authority and capture.authority.owner is capture.owner
        and jobs._coordinator_enabled())


def primary_cache_marked(db, sid, cid=None, *, store=None, budget=None):
    if not primary_cache_schema(db,store,budget):return False
    if budget is not None:budget.projection(32)
    row=db.execute('SELECT primary_cache_policy IS NOT NULL FROM recording_batches WHERE session_id=?',(sid,)).fetchone()
    if row is not None and row[0]:return True
    if cid is None:return False
    return db.execute('''SELECT 1 FROM chunks c JOIN recording_batches b ON b.session_id=c.session_id
        WHERE c.id=? AND b.primary_cache_policy IS NOT NULL''',(cid,)).fetchone() is not None


def primary_cache_qualified(store, db, sid, cid, store_key, budget=None):
    """Positive per-candidate authority; malformed/missing mapping is protected."""
    if budget is not None:budget.check()
    if (not primary_cache_schema(db,store,budget) or not primary_cache_capable(store)
            or not getattr(store.settings,'native_owned_compact_gc_enabled',False)
            or not getattr(store.settings,'native_owned_receipt_gc_enabled',False)
            or type(store_key) is not str or re.fullmatch('[0-9a-f]{64}',store_key) is None):return None
    if budget is not None:budget.projection(2048)
    row=db.execute('''SELECT b.id AS batch_id,b.session_id,b.primary_cache_store_key AS store_key,
        w.chunk_id AS cid,w.position,w.capture_start_frame,w.frames,b.capture_origin_frame AS origin,
        b.capture_end_frame,w.receipt_state,w.receipt_id,
        CASE WHEN typeof(w.native_receipt)='blob' AND length(w.native_receipt) BETWEEN 1 AND 1024
          THEN w.native_receipt ELSE NULL END AS native_receipt,c.digest,c.timeline_start,
        c.status='completed' AND c.next_offset=c.duration AND c.duration=w.frames/16000.0
          AND c.timeline_start=(w.capture_start_frame-b.capture_origin_frame)/16000.0
          AND c.session_id=b.session_id AND s.deleted=0 AND s.processing_mode='legacy'
          AND b.mode='sealed_pages' AND b.state!='aborted'
          AND b.primary_cache_policy=? AND b.primary_cache_store_key=?
          AND b.native_core_ordinal IS NULL AND b.retention_store_key IS NULL
          AND p.batch_id=b.id AND p.released=1 AND p.state='published'
          AND p.start_frame<=w.capture_start_frame AND w.capture_start_frame+w.frames<=p.end_frame
          AND sc.session_id=c.session_id AND sc.stream_id='recording.'||b.id AND sc.position=w.position
          AND ti.session_id=c.session_id AND ti.stream_id=sc.stream_id AND ti.position=sc.position
          AND ti.timeline_start=c.timeline_start AND ti.timeline_end=c.timeline_start+c.duration AS same
        FROM recording_work w JOIN recording_batches b ON b.id=w.batch_id
        JOIN recording_pages p ON p.id=w.page_id JOIN chunks c ON c.id=w.chunk_id
        JOIN sessions s ON s.id=c.session_id JOIN stream_chunks sc ON sc.chunk_id=c.id
        JOIN stream_time_index ti ON ti.chunk_id=c.id
        WHERE w.chunk_id=? AND b.session_id=?
          AND typeof(b.id)='text' AND length(CAST(b.id AS BLOB)) BETWEEN 1 AND 64
          AND typeof(b.session_id)='text' AND length(CAST(b.session_id AS BLOB)) BETWEEN 1 AND 64
          AND typeof(w.chunk_id)='text' AND length(CAST(w.chunk_id AS BLOB)) BETWEEN 1 AND 64
          AND typeof(w.receipt_id)='text' AND length(CAST(w.receipt_id AS BLOB)) BETWEEN 1 AND 64
          AND typeof(c.digest)='text' AND length(CAST(c.digest AS BLOB))=64
          AND w.receipt_state='durable' AND typeof(w.position)='integer'
          AND typeof(w.capture_start_frame)='integer' AND typeof(w.frames)='integer'
          AND typeof(b.capture_origin_frame)='integer' AND typeof(b.capture_end_frame)='integer'
          AND typeof(c.timeline_start) IN ('integer','real')''',
            (PRIMARY_CACHE_POLICY,store_key,cid,sid)).fetchone()
    if row is None or not row['same'] or row['receipt_state']!='durable':return None
    if (type(row['origin']) is not int or type(row['capture_end_frame']) is not int
            or type(row['capture_start_frame']) is not int or type(row['frames']) is not int
            or not 0<=row['origin']<=row['capture_start_frame']<row['capture_start_frame']+row['frames']<=row['capture_end_frame']<=MAX_COORDINATE):return None
    from .native_owned_cell_store import Receipt
    try:receipt=Receipt.decode(row['native_receipt'])
    except (ValueError,TypeError,Unavailable):return None
    if (receipt.session_id,receipt.chunk_id,receipt.receipt_id,receipt.digest,receipt.frames,
            receipt.timeline_start,receipt.stream_id)!=(sid,cid,row['receipt_id'],row['digest'],row['frames'],
                row['timeline_start'],'recording.'+row['batch_id']):return None
    from .recording_primary_provenance import primary_cache_usage
    primary_cache_usage(db,row['batch_id'],budget)
    from .moss_automatic import TABLE,DEPENDENCIES
    # Every query has an existing indexed session/cid/batch access path.
    if db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY auto_session_seq WHERE sid=? LIMIT 1',(sid,)).fetchone():return None
    for field,index in (('cid1','auto_cid1'),('cid2','auto_cid2')):
        if db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY {index} WHERE {field}=? LIMIT 1',(cid,)).fetchone():return None
    if db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY auto_v2_wait_range WHERE plan_batch_id=? LIMIT 1',(row['batch_id'],)).fetchone():return None
    if db.execute(f'SELECT 1 FROM {DEPENDENCIES} INDEXED BY auto_v2_dependency_cid WHERE cid=? LIMIT 1',(cid,)).fetchone():return None
    if db.execute(f'''SELECT 1 FROM native_core_commits n INDEXED BY native_core_v2_cid WHERE n.cid=? AND (
        EXISTS(SELECT 1 FROM {TABLE} INDEXED BY auto_slot1 WHERE slot1=n.slot)
        OR EXISTS(SELECT 1 FROM {TABLE} INDEXED BY auto_slot2 WHERE slot2=n.slot)
        OR EXISTS(SELECT 1 FROM {DEPENDENCIES} INDEXED BY auto_v2_dependency_slot WHERE slot=n.slot)) LIMIT 1''',
            (cid,)).fetchone():return None
    if db.execute('''SELECT 1 FROM native_core_commits INDEXED BY native_core_v2_cid WHERE cid=?
        AND (pending IS NOT NULL OR state IN ('pending','published') OR recording_batch_id IS NOT NULL
          OR commit_ordinal IS NOT NULL OR retention_store_key IS NOT NULL) LIMIT 1''',(cid,)).fetchone():return None
    version=db.execute('PRAGMA schema_version').fetchone()[0]
    if version!=store._primary_cache_schema_token[0]:raise Unavailable('primary_cache_schema_changed')
    if budget is not None:budget.projection(256);budget.check()
    return {name:row[name] for name in ('batch_id','session_id','cid','store_key','position','capture_start_frame','frames','origin')} | {'schema_version':version}


class RecordingStorageCapacityError(RuntimeError):
    """Retained recording copies need explicit storage capacity before retry."""


def init_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS recording_batches (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id),
        capture_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'open', finish_key TEXT, finish_request TEXT,
        capture_origin_frame INTEGER, capture_end_frame INTEGER,
        manifest TEXT, blockers TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL,
        UNIQUE(session_id,capture_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS recording_sources (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        batch_id TEXT NOT NULL REFERENCES recording_batches(id) ON DELETE CASCADE,
        idempotency_key TEXT NOT NULL, digest TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
        frames INTEGER NOT NULL, bytes INTEGER NOT NULL, source_start_frame INTEGER,
        format TEXT NOT NULL, created REAL NOT NULL,
        UNIQUE(batch_id,idempotency_key))''')
    db.execute('CREATE INDEX IF NOT EXISTS recording_source_path ON recording_sources(path)')
    batch_columns = {row['name'] for row in db.execute('PRAGMA table_info(recording_batches)')}
    for name, definition in (('mode', "TEXT NOT NULL DEFAULT 'legacy'"), ('eos', 'INTEGER NOT NULL DEFAULT 0')):
        if name not in batch_columns:
            db.execute(f'ALTER TABLE recording_batches ADD COLUMN {name} {definition}')
    db.execute("""CREATE TABLE IF NOT EXISTS recording_pages (
        id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES recording_batches(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL CHECK(page_number BETWEEN 1 AND 1024),
        revision INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'open',
        released INTEGER NOT NULL DEFAULT 0 CHECK(released IN (0,1)),
        seal_key TEXT, seal_request TEXT, start_frame INTEGER NOT NULL,
        end_frame INTEGER, eos INTEGER NOT NULL DEFAULT 0, manifest TEXT,
        blockers TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL,
        UNIQUE(batch_id,page_number))""")
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS recording_one_pending_page ON recording_pages(batch_id) WHERE released=0')
    db.execute("""CREATE TRIGGER IF NOT EXISTS recording_page_release_permanent
        BEFORE UPDATE OF released ON recording_pages WHEN OLD.released=1 AND NEW.released!=1
        BEGIN SELECT RAISE(ABORT,'Recording page publication is permanent'); END""")
    source_columns = {row['name'] for row in db.execute('PRAGMA table_info(recording_sources)')}
    if 'page_id' not in source_columns:
        db.execute('ALTER TABLE recording_sources ADD COLUMN page_id TEXT REFERENCES recording_pages(id) ON DELETE CASCADE')
    db.execute('CREATE INDEX IF NOT EXISTS recording_page_sources ON recording_sources(page_id,seq)')

    db.execute('''CREATE TABLE IF NOT EXISTS recording_work (
        chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        batch_id TEXT NOT NULL REFERENCES recording_batches(id) ON DELETE CASCADE,
        position INTEGER NOT NULL, frames INTEGER NOT NULL, bytes INTEGER NOT NULL,
        capture_start_frame INTEGER NOT NULL, native_receipt_status TEXT NOT NULL,
        UNIQUE(batch_id,position))''')
    columns = {row['name'] for row in db.execute('PRAGMA table_info(recording_work)')}
    for name, definition in (
            ('native_receipt', 'BLOB CHECK(native_receipt IS NULL OR length(native_receipt)<=1024)'),
            ('receipt_id', 'TEXT'), ('receipt_state', "TEXT NOT NULL DEFAULT 'gap'"),
            ('receipt_attempts', 'INTEGER NOT NULL DEFAULT 0'),
            ('page_id', 'TEXT REFERENCES recording_pages(id) ON DELETE CASCADE'),
            ('file_signature', 'TEXT CHECK(file_signature IS NULL OR length(file_signature)<=256)')):
        if name not in columns:
            db.execute(f'ALTER TABLE recording_work ADD COLUMN {name} {definition}')
    db.execute('CREATE INDEX IF NOT EXISTS recording_page_work ON recording_work(page_id,position)')
    db.execute('''CREATE TABLE IF NOT EXISTS recording_ranges (
        batch_id TEXT NOT NULL REFERENCES recording_batches(id) ON DELETE CASCADE,
        source_id TEXT NOT NULL REFERENCES recording_sources(id) ON DELETE CASCADE,
        chunk_id TEXT NOT NULL REFERENCES recording_work(chunk_id) ON DELETE CASCADE,
        source_start_frame INTEGER NOT NULL, capture_start_frame INTEGER NOT NULL,
        work_start_frame INTEGER NOT NULL, frames INTEGER NOT NULL,
        relation TEXT NOT NULL CHECK(relation IN ('owner','exact_alias')),
        PRIMARY KEY(source_id,source_start_frame))''')


def recover(db):
    # Only complete atomic outboxes resume. Reattest previous acknowledgements
    # through the current owner; incomplete pre-stage work remains interrupted.
    db.execute("""UPDATE recording_work SET receipt_state='pending',
        native_receipt_status='restart_recheck_pending' WHERE receipt_state='durable'
        AND batch_id IN (SELECT id FROM recording_batches WHERE state='pending_receipts')
        AND (page_id IS NULL OR page_id IN (SELECT id FROM recording_pages WHERE released=0 AND state='pending_receipts'))""")
    db.execute("""UPDATE recording_batches SET state='interrupted',blockers=?
        WHERE state IN ('preparing','staging') AND mode='legacy'""", (_json([{'kind': 'interrupted_publication',
        'detail': 'Retained staged work is not queued; delete the session to reset this batch'}]),))

    db.execute("UPDATE recording_pages SET state='sealed' WHERE state='preparing' AND released=0")

def erase(db, sid, uploads=None):
    """Called inside the existing session erasure transaction, before chunks."""
    db.execute('''INSERT OR IGNORE INTO session_file_cleanup(session_id,path,created)
        SELECT b.session_id,s.path,? FROM recording_sources s
        JOIN recording_batches b ON b.id=s.batch_id WHERE b.session_id=?''', (time.time(), sid))
    if uploads is not None:
        batch = db.execute("SELECT id FROM recording_batches WHERE session_id=? AND mode='sealed_pages'", (sid,)).fetchone()
        if batch:
            prefix = f"ro-{batch['id']}-page-"
            with os.scandir(uploads) as entries:
                for entry in entries:
                    if entry.name.startswith(prefix) and entry.name.endswith(('.part','.audio')):
                        db.execute('INSERT OR IGNORE INTO session_file_cleanup(session_id,path,created) VALUES(?,?,?)', (sid,entry.path,time.time()))
    db.execute('DELETE FROM recording_batches WHERE session_id=?', (sid,))


def verify_paths(store):
    """Extend startup's existing relocation guard to retained original receipts."""
    uploads = (store.settings.data_dir / 'uploads').resolve()
    with store.db() as db:
        for row in db.execute('SELECT path FROM recording_sources'):
            path = Path(row['path'])
            if (not path.is_absolute() or path.suffix != '.audio'
                    or path.parent.resolve() != uploads or str(path.resolve()) != str(path)):
                raise RuntimeError('Recording source path is outside the current owned uploads directory')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value, name):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or '\0' in value:
        raise ValueError(f'{name} must contain 1..128 characters')


def _coordinate(value):
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_COORDINATE):
        raise ValueError('source_start_frame must be an exact nonnegative integer')


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(BLOCK_FRAMES * 2), b''):
            digest.update(block)
    return digest.hexdigest()


def _equal(left, left_start, right, right_start, frames):
    """Compare every claimed overlapping sample, never a digest-only shortcut."""
    with wave.open(left['path'], 'rb') as a, wave.open(right['path'], 'rb') as b:
        a.setpos(left_start); b.setpos(right_start)
        remaining = frames
        while remaining:
            count = min(remaining, BLOCK_FRAMES)
            aa, bb = a.readframes(count), b.readframes(count)
            if len(aa) != count * 2 or len(bb) != count * 2:
                raise ValueError('Original source PCM changed or became truncated')
            if aa != bb:
                return False
            remaining -= count
    return True


def _plan(sources):
    """Return bounded interval lists, not a per-frame bitmap or joined PCM."""
    unknown = [{'kind': 'unknown_placement', 'source_id': source['id']}
               for source in sources if source['source_start_frame'] is None]
    if unknown:
        return [], [], unknown
    ordered = sorted(sources, key=lambda source: (source['source_start_frame'], source['seq']))
    owners, ranges, blockers = [], [], []
    end = None
    for source in ordered:
        start = source['source_start_frame']; stop = start + source['frames']
        if end is not None and start > end:
            blockers.append({'kind': 'capture_gap', 'start_frame': end, 'end_frame': start})
        for owner in owners:
            a, b = max(start, owner['start']), min(stop, owner['end'])
            if a >= b:
                continue
            previous = owner['source']
            if not _equal(source, a - start, previous,
                          a - previous['source_start_frame'], b - a):
                blockers.append({'kind': 'pcm_conflict', 'source_id': source['id'],
                    'other_source_id': previous['id'], 'start_frame': a, 'end_frame': b})
            else:
                ranges.append({'source_id': source['id'], 'start': a, 'end': b,
                               'relation': 'exact_alias'})
        a = start if end is None else max(start, end)
        if a < stop:
            owners.append({'source': source, 'start': a, 'end': stop})
            ranges.append({'source_id': source['id'], 'start': a, 'end': stop,
                           'relation': 'owner'})
        end = stop if end is None else max(end, stop)
    # Do not publish provisional aliases when any source was unresolved.
    return ([], [], blockers) if blockers else (owners, ranges, [])


class _StrictPublication:
    """One request owner, <=28 pieces, fixed live raw/token slots. No uncertain reclose."""
    def __init__(self, store):
        if store._strict_uncertain or store._strict_publication_owner is not None:
            raise RuntimeError('strict_publication_owner_busy')
        self.store=store; self.pieces=[]; self.current=None
        self.output=None; self.wave_output=None; self.source=None; self.wave_source=None
        self.directory=None; self.bound=None; self.uncertain=False; self.error=None
        store._strict_publication_owner=self

    def failed(self, error):
        self.uncertain=True
        if self.error is None:self.error=error
        self.store.strict_cleanup_unknown(self,kind='publication',error=error)

    def acquired(self, source):
        if self.bound is not None or self.uncertain:raise RuntimeError('strict_acquired_source_busy')
        self.bound=source

    def released(self, source):
        if self.bound is not source:raise RuntimeError('strict_source_owner_changed')
        self.bound=None

    def token(self, token):
        if self.current is None or self.current['token'] is not None:raise RuntimeError('strict_token_owner_changed')
        self.current['token']=token; self.bound=None

    def close(self, name, original=None):
        value=getattr(self,name)
        if value is None:return
        if self.uncertain:return
        try:
            if name=='directory':os.close(value)
            else:value.close()
        except BaseException as exc:
            self.failed(exc)
            if original is None:raise
        else:setattr(self,name,None)

    def close_token(self, piece):
        token=piece['token']
        if token is None or self.uncertain:return
        try:token.close()
        except BaseException as exc:self.failed(exc);raise
        else:piece['token']=None

    def finish(self, original=None):
        if original is not None and self.store._strict_original_error is None:self.store._strict_original_error=original
        if self.uncertain:return False
        for name in ('wave_source','source','wave_output','output','directory','bound'):
            self.close(name,original)
            if self.uncertain:return False
        for piece in self.pieces:
            try:self.close_token(piece)
            except BaseException:
                if original is None:raise
                return False
        if self.store._strict_publication_owner is not self:raise RuntimeError('strict_publication_owner_changed')
        self.store._strict_publication_owner=None
        if not self.store._strict_uncertain:self.store._strict_original_error=None
        return True


class RecordingOverlap:
    """HTTP's existing upload_lock serializes all methods that write audio.

    Filesystem quota counts BOTH committed and orphan .part/.audio copies in
    this feature's namespace, including failed session-erasure remnants.
    """
    def __init__(self, store):
        self.store = store
        self.settings = store.settings
        self.uploads = (self.settings.data_dir / 'uploads').resolve()
        self._maintenance_lock = threading.Lock()
        self._flight = None
        self._primary_cache_publish_unknown = None
        self._next_tick = 0.0
        self._reservation_lock = threading.RLock()
        self._reservations = {}

    def _strict_io_failure(self, error):
        if getattr(self.store,'_strict_enabled',False):
            owner=self.store._strict_publication_owner
            if isinstance(owner,_StrictPublication):owner.failed(error)
            else:self.store.strict_cleanup_unknown(error,kind='publication',error=error)

    def _sync_uploads(self):
        if not getattr(self.store,'_strict_enabled',False):return _fsync_dir(self.uploads)
        owner=self.store._strict_publication_owner
        temporary=owner is None
        if temporary:owner=_StrictPublication(self.store)
        if not isinstance(owner,_StrictPublication) or owner.uncertain:raise RuntimeError('strict_publication_cleanup_unknown')
        original=None
        try:
            owner.directory=os.open(self.uploads,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW)
            os.fsync(owner.directory)
        except BaseException as exc:original=exc;raise
        finally:
            owner.close('directory',original)
            if temporary:owner.finish(original)

    def _materialize_strict(self,batch,owners,origin,end,cells):
        owner=_StrictPublication(self.store); pieces=owner.pieces
        page=batch.get('_page'); first=(origin-batch['capture_origin_frame'])//CHUNK_FRAMES if page else 0
        original=None
        try:
            for position,start in enumerate(range(origin,end,CHUNK_FRAMES),first):
                if len(pieces)>=28:raise OverflowError('strict_publication_piece_capacity')
                stop=min(start+CHUNK_FRAMES,end)
                prefix=f"ro-{batch['id']}-page-{page['id']}" if page else f"ro-{batch['id']}"
                path=self.uploads/f"{prefix}-work-{position:03}.part"; final=path.with_suffix('.audio')
                if final.exists() or path.exists():raise ValueError('Retained interrupted work exists; restart cleanup or erase this session')
                piece={'position':position,'start':start,'end':stop,'path':path,'final':final,
                    'token':None,'native':'disabled','_strict_owner':owner}
                pieces.append(piece); owner.current=piece
                if page:self._page_resources((stop-start)*2+44)
                else:self.admit_bytes(batch['id'],(stop-start)*2+44)
                owner.output=path.open('x+b')
                owner.wave_output=wave.open(owner.output,'wb')
                wav=owner.wave_output; wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(RATE)
                cursor=start
                for source_owner in owners:
                    a,b=max(start,source_owner['start']),min(stop,source_owner['end'])
                    if a>=b:continue
                    if a!=cursor:raise ValueError('Noncontiguous recording materialization plan')
                    source=source_owner['source']
                    owner.source=open(source['path'],'rb')
                    owner.wave_source=wave.open(owner.source,'rb')
                    owner.wave_source.setpos(a-source['source_start_frame']);remaining=b-a
                    while remaining:
                        count=min(remaining,BLOCK_FRAMES);block=owner.wave_source.readframes(count)
                        if len(block)!=count*2:raise ValueError('Original changed while materializing work')
                        wav.writeframesraw(block);remaining-=count
                    owner.close('wave_source');owner.close('source');cursor=b
                if cursor!=stop:raise ValueError('Incomplete recording work piece')
                owner.close('wave_output');out=owner.output
                out.flush();os.fsync(out.fileno());out.seek(0);digest=hashlib.sha256()
                for block in iter(lambda:out.read(BLOCK_FRAMES*2),b''):digest.update(block)
                piece['digest']=digest.hexdigest();piece['bytes']=out.tell()
                if cells is not None:
                    observation=cells.streamed_descriptor(out.fileno(),piece['digest'],piece['bytes'])
                    piece['token']=cells.prepare_upload(path,piece['digest'],piece['bytes'],observation,strict_owner=owner)
                    piece['native']='prepared' if piece['token'] is not None else 'receipt_unavailable'
                if owner.uncertain:raise RuntimeError('strict_publication_cleanup_unknown')
                owner.close('output');os.replace(path,final);info=final.stat()
                piece['signature']=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            self._sync_uploads()
            return pieces
        except BaseException as exc:
            original=exc
            if isinstance(exc,OSError):owner.failed(exc)
            owner.finish(original)
            if not owner.uncertain:
                try:self._discard(pieces)
                except BaseException:pass
            raise

    def _finish_pieces(self,pieces,committed):
        owner=pieces[0].get('_strict_owner') if pieces else None
        if owner is not None:
            # Called from finally: preserve the active original exception.
            import sys
            original=sys.exc_info()[1]
            if not owner.finish(original):return
        if not committed:self._discard(pieces)
        elif owner is None:
            for piece in pieces:
                if piece['token'] is not None:piece['token'].close();piece['token']=None

    def _batch(self, db, sid, capture_id):
        row = db.execute('SELECT * FROM recording_batches WHERE session_id=? AND capture_id=?',
                         (sid, capture_id)).fetchone()
        if row is None:
            raise KeyError('Recording capture not found')
        return dict(row)

    def _session(self, db, sid):
        row = db.execute('SELECT * FROM sessions WHERE id=? AND deleted=0', (sid,)).fetchone()
        if row is None:
            raise KeyError('Session not found')
        if row['processing_mode'] != 'legacy':
            raise ValueError('Recording batches require an ordinary legacy session')
        return row

    def begin(self, sid, capture_id):
        if not isinstance(capture_id, str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', capture_id) is None:
            raise ValueError('capture_id must be a bounded explicit sample-coordinate domain')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._session(db, sid)
            old = db.execute('SELECT * FROM recording_batches WHERE session_id=?', (sid,)).fetchone()
            if old:
                if old['mode'] != 'legacy':
                    raise ValueError('Sealed recording captures require page source routes')
                if old['capture_id'] != capture_id:
                    raise ValueError('This session already has a different recording coordinate domain')
                return dict(old)
            if db.execute('SELECT 1 FROM chunks WHERE session_id=? LIMIT 1', (sid,)).fetchone():
                raise ValueError('Begin recording in a fresh empty legacy session')
            if db.execute('SELECT COUNT(*) FROM recording_batches').fetchone()[0] >= MAX_BATCHES:
                raise OverflowError('Recording batch capacity reached; delete unused sessions')
            bid = uuid.uuid4().hex
            self.admit_bytes(bid, new_batch=True)
            db.execute('INSERT INTO recording_batches(id,session_id,capture_id,created) VALUES(?,?,?,?)',
                       (bid, sid, capture_id, time.time()))
            return self._batch(db, sid, capture_id)

    def source_path(self, batch, page=None):
        if page is not None:
            return self.uploads / f"ro-{batch['id']}-page-{page['id']}-source-{uuid.uuid4().hex}.part"
        return self.uploads / f"ro-{batch['id']}-source-{uuid.uuid4().hex}.part"

    def admit_bytes(self, bid, additional=0, *, new_batch=False):
        """Conservative allocated-byte accounting; bounded memory directory scan."""
        with self.store.db() as db:
            batches = db.execute('SELECT COUNT(*) FROM recording_batches').fetchone()[0]
        total = (batches + int(new_batch)) * METADATA_RESERVATION
        batch = METADATA_RESERVATION
        with os.scandir(self.uploads) as entries:
            for entry in entries:
                if not entry.name.startswith('ro-'):
                    continue
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Unsafe file in the recording upload namespace')
                charge = max(info.st_size, info.st_blocks * 512)
                total += charge
                if entry.name.startswith(f'ro-{bid}-'):
                    batch += charge
        # Allow for the next block's allocation rounding, not only payload bytes.
        reserve = ((additional + 4095) // 4096) * 4096
        if batch + reserve > MAX_BATCH_BYTES or total + reserve > MAX_TOTAL_BYTES:
            raise OverflowError('Recording retained-original and staging-copy quota reached')
        check_resources(self.settings, reserve)

    def accept(self, sid, capture_id, key, path, source_start_frame=None):
        _text(key, 'Idempotency-Key'); _coordinate(source_start_frame)
        batch = self.begin(sid, capture_id)
        path = Path(path)
        if path.parent.resolve() != self.uploads or path.suffix != '.part' or not path.name.startswith(f"ro-{batch['id']}-source-"):
            raise ValueError('Recording upload must use its owned staging path')
        size = path.stat().st_size
        if not 0 < size <= min(MAX_SOURCE_BYTES, self.settings.max_upload_bytes):
            raise OverflowError('Recording source byte limit exceeded')
        source = inspect_pcm16_source(uuid.uuid4().hex, path)
        if source.format != (RATE, 1, 2, 'wav-pcm16le'):
            raise ValueError('Recording sources require original PCM16 mono 16000 Hz WAV')
        if source.frames > min(MAX_SOURCE_FRAMES, math.floor(self.settings.max_audio_seconds * RATE)):
            raise OverflowError('Recording source duration limit exceeded')
        if source_start_frame is not None and source_start_frame + source.frames > MAX_COORDINATE:
            raise ValueError('Capture coordinate exceeds exact integer range')
        self.admit_bytes(batch['id'])
        final = path.with_suffix('.audio')
        committed = False
        try:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self._session(db, sid)
                batch = self._batch(db, sid, capture_id)
                old = db.execute('SELECT * FROM recording_sources WHERE batch_id=? AND idempotency_key=?',
                                 (batch['id'], key)).fetchone()
                if old:
                    if (old['digest'], old['source_start_frame'], old['format']) != (source.sha256, source_start_frame, FORMAT):
                        raise ValueError('Source idempotency key reused with different bytes or placement')
                    return {'source_id': old['id'], 'duplicate': True}
                if batch['state'] != 'open':
                    raise ValueError('Recording source set is frozen')
                if db.execute('SELECT 1 FROM chunks WHERE session_id=? LIMIT 1', (sid,)).fetchone():
                    raise ValueError('Recording source acceptance requires an empty target session')
                if db.execute('SELECT COUNT(*) FROM recording_sources WHERE batch_id=?', (batch['id'],)).fetchone()[0] >= MAX_SOURCES:
                    raise OverflowError('Recording source count limit reached')
                os.replace(path, final); self._sync_uploads()
                db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,frames,
                    bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (source.fragment_id, batch['id'], key, source.sha256, str(final), source.frames,
                     size, source_start_frame, FORMAT, time.time()))
                db.execute("UPDATE recording_batches SET revision=revision+1,state='open',blockers='[]' WHERE id=?",
                           (batch['id'],))
            committed = True
            return {'source_id': source.fragment_id, 'duplicate': False}
        finally:
            if not committed and not getattr(self.store,'_strict_enabled',False):
                final.unlink(missing_ok=True)

    def status(self, sid, capture_id):
        with self.store.db() as db:
            db.execute('BEGIN')  # One public snapshot across async publication.
            self._session(db, sid)
            batch = self._batch(db, sid, capture_id)
            if batch['mode'] == 'sealed_pages':
                return self._capture_summary(db, batch)
            sources = [dict(row) for row in db.execute('''SELECT id,seq,idempotency_key,digest,frames,
                bytes,source_start_frame,format,created FROM recording_sources WHERE batch_id=? ORDER BY seq''',
                (batch['id'],))]
            chunks = [dict(row) for row in db.execute('''SELECT w.chunk_id,w.position,w.frames,w.bytes,
                w.capture_start_frame,w.native_receipt_status,w.receipt_state,w.receipt_id,w.receipt_attempts,
                c.status,c.digest,c.timeline_start,c.duration
                FROM recording_work w JOIN chunks c ON c.id=w.chunk_id WHERE w.batch_id=? ORDER BY w.position''',
                (batch['id'],))]
            ranges = [dict(row) for row in db.execute('''SELECT source_id,chunk_id,source_start_frame,
                capture_start_frame,work_start_frame,frames,relation FROM recording_ranges
                WHERE batch_id=? ORDER BY source_id,source_start_frame''', (batch['id'],))]
        return {'capture_id': capture_id, 'coordinate_domain': capture_id, 'format': FORMAT,
                'revision': batch['revision'], 'state': batch['state'],
                'capture_origin_frame': batch['capture_origin_frame'], 'capture_end_frame': batch['capture_end_frame'],
                'stream_id': f"recording.{batch['id']}" if chunks else None,
                'sources': sources, 'work_chunks': chunks, 'ranges': ranges,
                'blockers': json.loads(batch['blockers']), 'policy': POLICY,
                'optional_receipts': {'pending': sum(row['receipt_state'] == 'pending' for row in chunks),
                    'durable': sum(row['receipt_state'] == 'durable' for row in chunks),
                    'gaps': sum(row['receipt_state'] == 'gap' and row['native_receipt_status'] != 'disabled' for row in chunks),
                    'ready': bool(chunks) and all(row['receipt_state'] == 'durable' for row in chunks)},
                'limits': {'max_sources': MAX_SOURCES, 'max_chunks': MAX_CHUNKS,
                    'chunk_frames': CHUNK_FRAMES, 'max_source_frames': MAX_SOURCE_FRAMES,
                    'max_capture_frames': MAX_CAPTURE_FRAMES, 'max_batch_bytes': MAX_BATCH_BYTES,
                    'max_total_bytes': MAX_TOTAL_BYTES, 'max_batches': MAX_BATCHES,
                    'metadata_reservation_per_batch': METADATA_RESERVATION,
                    'disk_reserve_bytes': self.settings.disk_reserve_bytes}}

    def _verify_originals(self, sources):
        blockers = []
        for source in sources:
            try:
                path = Path(source['path'])
                if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError('Original source is not a regular owned file')
                current = inspect_pcm16_source(source['id'], path)
                if (current.sha256 != source['digest'] or current.frames != source['frames']
                        or current.format != (RATE, 1, 2, 'wav-pcm16le') or path.stat().st_size != source['bytes']):
                    raise ValueError('Original receipt no longer matches retained bytes')
            except (OSError, ValueError, wave.Error) as exc:
                if isinstance(exc,OSError):self._strict_io_failure(exc)
                blockers.append({'kind': 'original_unavailable', 'source_id': source['id'], 'detail': str(exc)[:256]})
        return blockers

    def _materialize(self, batch, owners, origin, end, cells):
        if getattr(self.store,'_strict_enabled',False):return self._materialize_strict(batch,owners,origin,end,cells)
        pieces = []
        page = batch.get('_page')
        first = (origin - batch['capture_origin_frame']) // CHUNK_FRAMES if page else 0
        try:
            for position, start in enumerate(range(origin, end, CHUNK_FRAMES), first):
                stop = min(start + CHUNK_FRAMES, end)
                prefix = f"ro-{batch['id']}-page-{page['id']}" if page else f"ro-{batch['id']}"
                path = self.uploads / f"{prefix}-work-{position:03}.part"
                final = path.with_suffix('.audio')
                if final.exists() or path.exists():
                    raise ValueError('Retained interrupted work exists; restart cleanup or erase this session')
                piece = {'position': position, 'start': start, 'end': stop,
                         'path': path, 'final': final, 'token': None, 'native': 'disabled'}
                pieces.append(piece)
                if page:
                    self._page_resources((stop - start) * 2 + 44)
                else:
                    self.admit_bytes(batch['id'], (stop - start) * 2 + 44)
                with path.open('x+b') as out:
                    with wave.open(out, 'wb') as wav:
                        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(RATE)
                        cursor = start
                        for owner in owners:
                            a, b = max(start, owner['start']), min(stop, owner['end'])
                            if a >= b:
                                continue
                            if a != cursor:
                                raise ValueError('Noncontiguous recording materialization plan')
                            source = owner['source']
                            with wave.open(source['path'], 'rb') as src:
                                src.setpos(a - source['source_start_frame'])
                                remaining = b - a
                                while remaining:
                                    count = min(remaining, BLOCK_FRAMES)
                                    block = src.readframes(count)
                                    if len(block) != count * 2:
                                        raise ValueError('Original changed while materializing work')
                                    wav.writeframesraw(block); remaining -= count
                            cursor = b
                        if cursor != stop:
                            raise ValueError('Incomplete recording work piece')
                    out.flush(); os.fsync(out.fileno())
                    out.seek(0); digest = hashlib.sha256()
                    for block in iter(lambda: out.read(BLOCK_FRAMES * 2), b''):
                        digest.update(block)
                    piece['digest'] = digest.hexdigest(); piece['bytes'] = out.tell()
                    if cells is not None:
                        try:
                            observation = cells.streamed_descriptor(out.fileno(), piece['digest'], piece['bytes'])
                            piece['token'] = cells.prepare_upload(path, piece['digest'], piece['bytes'], observation)
                            piece['native'] = 'prepared' if piece['token'] is not None else 'receipt_unavailable'
                        except Exception:
                            piece['native'] = 'receipt_unavailable'
                os.replace(path, final)
                info = final.stat()
                piece['signature'] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            self._sync_uploads()
            return pieces
        except BaseException:
            self._discard(pieces)
            raise

    def _discard(self,pieces):
        owner=pieces[0].get('_strict_owner') if pieces else None
        if owner is not None:
            if owner.uncertain:return
            if self.store._strict_publication_owner is owner and not owner.finish():return
            # Preserve generated audio even if COMMIT returned an unknown acknowledgement.
            # A later exact-page orphan census may rebuild only unreferenced work.
            return
        for piece in pieces:
            if piece['token'] is not None:
                piece['token'].close(); piece['token'] = None
            piece['path'].unlink(missing_ok=True)
            piece['final'].unlink(missing_ok=True)

    def finish(self, sid, capture_id, key, expected_revision, cells=None):
        _text(key, 'Finish idempotency_key')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('expected_revision must be a nonnegative integer')
        with self.store.db() as db:
            batch = self._batch(db, sid, capture_id)
            if batch['mode'] == 'sealed_pages':
                return self.finish_capture(sid, capture_id, key, expected_revision)
        request = _json({'expected_revision': expected_revision, 'timeline_start_frame': 0, 'policy': POLICY})
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._session(db, sid)
            batch = self._batch(db, sid, capture_id)
            if batch['finish_key'] is not None:
                if batch['finish_key'] != key or batch['finish_request'] != request:
                    raise ValueError('Recording already frozen with a different finish request')
                return self.status(sid, capture_id)
            if batch['revision'] != expected_revision:
                raise ValueError('Recording source revision changed; review sources before finishing')
            if db.execute('SELECT 1 FROM chunks WHERE session_id=? LIMIT 1', (sid,)).fetchone():
                raise ValueError('Finish requires the fresh empty target session')
            sources = [dict(row) for row in db.execute('SELECT * FROM recording_sources WHERE batch_id=? ORDER BY seq',
                                                      (batch['id'],))]
            if not sources:
                raise ValueError('No recording sources were accepted')
            # The request freezes the complete immutable source snapshot BEFORE
            # comparison or materialization. Process death cannot reopen it.
            frozen = {'policy': POLICY, 'sources': [{'id': source['id'], 'digest': source['digest'],
                'start': source['source_start_frame'], 'frames': source['frames']} for source in sources]}
            db.execute("""UPDATE recording_batches SET state='preparing',finish_key=?,
                finish_request=?,manifest=?,blockers='[]' WHERE id=?""",
                (key, request, _json(frozen), batch['id']))
        try:
            return self._finish_frozen(sid, capture_id, key, expected_revision, request, batch, sources, cells)
        except BaseException:
            with self.store.db() as db:
                db.execute("""UPDATE recording_batches SET state='interrupted',blockers=?
                    WHERE id=? AND state IN ('preparing','staging')""",
                    (_json([{'kind': 'interrupted_publication',
                        'detail': 'Frozen originals retained; unfinished work is not queued'}]), batch['id']))
            raise

    def _finish_frozen(self, sid, capture_id, key, expected_revision, request, batch, sources, cells):
        blockers = self._verify_originals(sources)
        owners, ranges, geometry = _plan(sources) if not blockers else ([], [], [])
        blockers.extend(geometry)
        if not blockers:
            origin, end = owners[0]['start'], owners[-1]['end']
            frames = end - origin
            if frames > min(MAX_CAPTURE_FRAMES, math.floor(self.settings.max_audio_seconds * RATE)) or math.ceil(frames / CHUNK_FRAMES) > MAX_CHUNKS:
                blockers.append({'kind': 'capture_duration_capacity'})
        if blockers:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current = self._batch(db, sid, capture_id)
                if current['revision'] != expected_revision or current['finish_key'] != key or current['state'] != 'preparing':
                    raise ValueError('Source set changed during recording finalization')
                db.execute("""UPDATE recording_batches SET state='unresolved',blockers=?,finish_key=?,
                    finish_request=?,manifest=? WHERE id=?""", (_json(blockers), key, request,
                    _json({'policy': POLICY, 'sources': [{'id': source['id'], 'digest': source['digest'],
                        'start': source['source_start_frame'], 'frames': source['frames']} for source in sources]}), batch['id']))
            return self.status(sid, capture_id)
        # All sources were received before sorting; out-of-order prefix uploads
        # therefore cannot be lost behind a previously dispatched frontier.
        manifest = {'policy': POLICY, 'origin': origin, 'end': end,
                    'sources': [{'id': source['id'], 'digest': source['digest'],
                                 'start': source['source_start_frame'], 'frames': source['frames']} for source in sources],
                    'owners': [{'source_id': span['source']['id'], 'start': span['start'], 'end': span['end']} for span in owners]}
        # Fixed metadata projection is reserved before any derived copy. All
        # source/map counts are bounded; no per-frame records are allocated.
        projected_rows = len(ranges) * (math.ceil((end - origin) / CHUNK_FRAMES) + 1)
        if projected_rows * 256 + len(_json(manifest)) * 2 + MAX_CHUNKS * 2048 > METADATA_RESERVATION:
            raise OverflowError('Recording range metadata projection limit exceeded')
        self.store.capacity(sid, f"recording.{batch['id']}")
        pieces = self._materialize(batch, owners, origin, end, cells)
        committed = False
        try:
            # Hash all originals again after bounded reads/materialization. No
            # source mutation can silently change the frozen receipt projection.
            if self._verify_originals(sources):
                raise ValueError('Original source changed during recording finalization')
            self.admit_bytes(batch['id'])
            by_id = {source['id']: source for source in sources}
            expected_ranges = []
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self._session(db, sid)
                current = self._batch(db, sid, capture_id)
                if current['revision'] != expected_revision or current['finish_key'] != key or current['state'] != 'preparing':
                    raise ValueError('Source set changed during recording finalization')
                if db.execute('SELECT 1 FROM chunks WHERE session_id=? LIMIT 1', (sid,)).fetchone():
                    raise ValueError('Target session is no longer empty')
                for piece in pieces:
                    cid, created = self.store._add_chunk(db, sid,
                        f"recording.{batch['id']}.{piece['position']}", piece['digest'], piece['final'],
                        (piece['end'] - piece['start']) / RATE, (piece['start'] - origin) / RATE,
                        stream_id=f"recording.{batch['id']}", stream_final=piece is pieces[-1], _status='staging')
                    if not created:
                        raise ValueError('Unexpected prior recording work ID')
                    piece['cid'] = cid
                    receipt = self._observed_receipt(cells, piece, sid, origin, f"recording.{batch['id']}")
                    piece['receipt_id'] = receipt.receipt_id if receipt is not None else None
                    status = 'receipt_pending' if receipt is not None else (
                        'disabled' if not self.settings.native_owned_cells_enabled and cells is None else 'observation_unavailable')
                    db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
                        capture_start_frame,native_receipt_status,native_receipt,receipt_id,receipt_state)
                        VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (cid, batch['id'], piece['position'], piece['end'] - piece['start'],
                         piece['bytes'], piece['start'], status, receipt.encoded if receipt is not None else None,
                         piece['receipt_id'], 'pending' if receipt is not None else 'gap'))
                    for item in ranges:
                        a, b = max(item['start'], piece['start']), min(item['end'], piece['end'])
                        if a >= b:
                            continue
                        expected_ranges.append((item['source_id'], cid,
                            a - by_id[item['source_id']]['source_start_frame'], a,
                            a - piece['start'], b - a, item['relation']))
                        db.execute('''INSERT INTO recording_ranges(batch_id,source_id,chunk_id,source_start_frame,
                            capture_start_frame,work_start_frame,frames,relation) VALUES(?,?,?,?,?,?,?,?)''',
                            (batch['id'], item['source_id'], cid, a - by_id[item['source_id']]['source_start_frame'],
                             a, a - piece['start'], b - a, item['relation']))
                manifest['work'] = [{'chunk_id': piece['cid'], 'digest': piece['digest'],
                    'position': piece['position'], 'start': piece['start'], 'end': piece['end'],
                    'bytes': piece['bytes'], 'signature': list(piece['signature']),
                    'receipt_id': piece['receipt_id']} for piece in pieces]
                manifest['ranges'] = sorted(expected_ranges)
                manifest['outbox_version'] = 1
                if len(_json(manifest).encode('utf-8')) > METADATA_RESERVATION // 2:
                    raise OverflowError('Recording frozen manifest capacity exceeded')
                db.execute('''UPDATE recording_batches SET state='pending_receipts',finish_key=?,finish_request=?,
                    capture_origin_frame=?,capture_end_frame=?,manifest=?,blockers='[]' WHERE id=?''',
                    (key, request, origin, end, _json(manifest), batch['id']))
            committed = True
            # Outbox and staged ordinary work are now one atomic commitment.
            # Only one nonwaiting maintenance tick is attempted in this request;
            # the existing worker loop continues any pending owner delivery.
            self.advance(cells, force=True, batch_id=batch['id'])
            return self.status(sid, capture_id)
        finally:
            self._finish_pieces(pieces,committed)

    def _observed_receipt(self, cells, piece, sid, origin, stream_id):
        """Freeze actual observation; never construct a Receipt from a filename."""
        from .native_owned_cell_store import Receipt
        receipt = None
        owner=piece.get('_strict_owner')
        if cells is not None and piece['token'] is not None:
            try:
                receipt = cells.observe_recording_receipt(piece['token'], sid, piece['cid'],
                    piece['final'], (piece['start'] - origin) / RATE, stream_id, **({'close_token':False} if owner is not None else {}))
            except Exception as exc:
                if owner is not None:
                    if isinstance(exc,OSError):owner.failed(exc)
                    raise
                receipt = None
        if piece['token'] is not None:
            if owner is not None:owner.close_token(piece)
            else:piece['token'].close(); piece['token'] = None
        if receipt is None:
            return None
        if (type(receipt) is not Receipt or receipt.session_id != sid or receipt.chunk_id != piece['cid']
                or receipt.digest != piece['digest'] or receipt.frames != piece['end'] - piece['start']
                or receipt.size != piece['bytes'] or receipt.signature != piece['signature']
                or receipt.timeline_start != (piece['start'] - origin) / RATE or receipt.stream_id != stream_id):
            raise ValueError('Observed recording receipt differs from the staged ordinary chunk')
        return receipt

    def _pending_manifest(self, db, batch, *, verify_files=False):
        """Read one bounded, atomically complete staged manifest. No reconstruction."""
        if batch['mode'] == 'sealed_pages':
            return self._pending_page_manifest(db, batch, verify_files=verify_files)
        if not batch['manifest'] or len(batch['manifest'].encode('utf-8')) > METADATA_RESERVATION // 2:
            raise ValueError('Recording manifest capacity or completeness changed')
        manifest = json.loads(batch['manifest'])
        if not isinstance(manifest, dict) or not all(key in manifest for key in ('sources', 'origin', 'ranges', 'work')):
            raise ValueError('Recording pending manifest is incomplete')
        pieces = manifest.get('work', [])
        if manifest.get('outbox_version') != 1 or not 1 <= len(pieces) <= MAX_CHUNKS:
            raise ValueError('Recording pending manifest is incomplete')
        rows = [dict(row) for row in db.execute('''SELECT c.*,w.position,w.frames,w.bytes,
            w.capture_start_frame,w.native_receipt,w.receipt_id,w.receipt_state,w.native_receipt_status,
            sc.stream_id,sc.position AS stream_position,sc.stream_final,sc.upload_stream_final,
            sc.session_id AS stream_session_id FROM recording_work w
            JOIN chunks c ON c.id=w.chunk_id LEFT JOIN stream_chunks sc ON sc.chunk_id=c.id
            WHERE w.batch_id=? ORDER BY w.position LIMIT ?''', (batch['id'], MAX_CHUNKS + 1))]
        if len(rows) != len(pieces):
            raise ValueError('Recording staged work count changed')
        ranges = [tuple(row) for row in db.execute('''SELECT source_id,chunk_id,source_start_frame,
            capture_start_frame,work_start_frame,frames,relation FROM recording_ranges
            WHERE batch_id=? ORDER BY source_id,source_start_frame LIMIT ?''',
            (batch['id'], MAX_SOURCES * (MAX_CHUNKS + MAX_SOURCES) + 1))]
        if sorted(ranges) != sorted(tuple(row) for row in manifest.get('ranges', [])):
            raise ValueError('Recording staged source-to-work map changed')
        sources = [dict(row) for row in db.execute('SELECT * FROM recording_sources WHERE batch_id=? ORDER BY seq LIMIT ?',
                                                  (batch['id'], MAX_SOURCES + 1))]
        projection = [{'id': row['id'], 'digest': row['digest'], 'start': row['source_start_frame'],
                       'frames': row['frames']} for row in sources]
        if projection != manifest['sources']:
            raise ValueError('Recording frozen source snapshot changed')
        if verify_files and self._verify_originals(sources):
            raise ValueError('Recording original authority changed before publication')
        for row, piece in zip(rows, pieces):
            path = self.uploads / f"ro-{batch['id']}-work-{piece['position']:03}.audio"
            if (row['id'] != piece['chunk_id'] or row['status'] != 'staging'
                    or row['session_id'] != batch['session_id'] or row['stream_session_id'] != batch['session_id']
                    or row['path'] != str(path) or row['digest'] != piece['digest']
                    or row['position'] != piece['position'] or row['bytes'] != piece['bytes']
                    or row['frames'] != piece['end'] - piece['start'] or row['duration'] != row['frames'] / RATE
                    or row['timeline_start'] != (piece['start'] - manifest['origin']) / RATE
                    or row['capture_start_frame'] != piece['start']
                    or row['stream_id'] != f"recording.{batch['id']}" or row['stream_position'] != piece['position']
                    or bool(row['stream_final']) != (piece is pieces[-1])
                    or row['stream_final'] != row['upload_stream_final']):
                raise ValueError('Recording staged ordinary work changed')
            payload = row['native_receipt']
            if payload is not None:
                if (type(payload) is not bytes or not 1 <= len(payload) <= 1024
                        or hashlib.sha256(payload).hexdigest() != piece['receipt_id']
                        or row['receipt_id'] != piece['receipt_id'] or row['receipt_state'] not in ('pending', 'durable', 'gap')):
                    raise ValueError('Recording persisted observation changed')
            elif piece['receipt_id'] is not None or row['receipt_state'] != 'gap':
                raise ValueError('Recording persisted observation is incomplete')
            if verify_files:
                info = path.stat()
                signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                if list(signature) != piece['signature'] or _hash(path) != piece['digest']:
                    raise ValueError('Recording derived audio changed before publication')
                after = path.stat()
                if signature != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('Recording derived audio changed while verifying publication')
        return rows

    @staticmethod
    def _decode_receipt(payload):
        from .native_owned_cell_store import Receipt
        try:
            return Receipt.decode(payload)
        except Exception as exc:
            raise ValueError('Recording canonical receipt payload changed') from exc

    def _cache_publication_snapshot(self, db, bid, pid):
        from .recording_primary_provenance import primary_cache_usage
        if not primary_cache_schema(db,self.store):raise ValueError('Primary cache publication schema changed')
        batch=db.execute('''SELECT id,session_id,mode,primary_cache_policy,primary_cache_store_key
            FROM recording_batches WHERE id=?''',(bid,)).fetchone()
        page=db.execute('SELECT id,batch_id,released,state FROM recording_pages WHERE id=?',(pid,)).fetchone()
        if batch is None or page is None:raise ValueError('Primary cache publication disappeared')
        usage=primary_cache_usage(db,bid)
        rows=db.execute('''SELECT w.chunk_id,w.receipt_state,w.receipt_id,w.native_receipt
            FROM recording_work w JOIN chunks c ON c.id=w.chunk_id WHERE w.page_id=?
            ORDER BY w.position LIMIT 29''',(pid,)).fetchall()
        if len(rows)>MAX_PAGE_CHUNKS:raise ValueError('Primary cache publication work changed')
        return (tuple(batch),tuple(page),usage[bid][2],tuple(tuple(row) for row in rows))

    def _reconcile_primary_cache_publication(self):
        saved=self._primary_cache_publish_unknown
        if saved is None:return
        bid,pid,before,after=saved
        with self.store.db() as db:
            db.execute('PRAGMA busy_timeout=5');db.execute('BEGIN IMMEDIATE')
            current=self._cache_publication_snapshot(db,bid,pid)
            if current!=before and current!=after:
                raise ValueError('Primary cache publication unknown outcome changed')
        # Exact reread resolves uncertainty, without attributing publication.
        self._primary_cache_publish_unknown=None

    def _publish_pending(self, bid, cells):
        if self._primary_cache_publish_unknown is not None:
            self._reconcile_primary_cache_publication()
            return False
        try:
            result=self._publish_pending_transaction(bid,cells)
        except BaseException:
            if self._primary_cache_publish_unknown is not None:
                try:self._reconcile_primary_cache_publication()
                except Exception:pass  # Keep the unresolved exact snapshots.
            raise
        self._primary_cache_publish_unknown=None
        return result

    def _publish_pending_transaction(self, bid, cells):
        """Only a complete terminal receipt set may release this batch's work."""
        with self.store.db() as db:
            db.execute('PRAGMA busy_timeout=5')  # Bounded maintenance admission, primary DB only.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM recording_batches WHERE id=? AND state='pending_receipts'", (bid,)).fetchone()
            if row is None:
                return False
            batch = dict(row); self._session(db, batch['session_id'])
            rows = self._pending_manifest(db, batch, verify_files=True)
            if any(row['receipt_state'] == 'pending' for row in rows):
                return False
            # Current-owner acknowledgement is rechecked even if recovery or
            # revocation occurred between the last receipt and this queue flip.
            owner = cells.owner if cells is not None and any(item['receipt_state'] == 'durable' for item in rows) else None
            epoch = owner._epoch if owner is not None else None
            for item in rows:
                if item['receipt_state'] != 'durable':
                    continue
                if cells is None or not cells.owner.health()['ready']:
                    return False
                if cells.owner is not owner or owner._epoch is not epoch:
                    return False
                receipt = self._decode_receipt(item['native_receipt'])
                if owner.receipt(item['id']) != receipt:
                    db.execute("UPDATE recording_work SET receipt_state='pending',native_receipt_status='owner_recheck_pending' WHERE chunk_id=?", (item['id'],))
                    return False
                from .native_owned_cell_store import Budget
                budget = Budget()
                with budget.phase():
                    if not cells.authority.receipt_valid(receipt, budget):
                        raise ValueError('Recording receipt authority changed before publication')
                if owner._epoch is not epoch or not owner.health()['ready']:
                    return False
            if any(item['receipt_state'] == 'durable' for item in rows) and (owner._epoch is not epoch or not owner.health()['ready']):
                return False
            gaps = [{'kind': 'optional_receipt_gap', 'chunk_id': row['id'], 'reason': row['native_receipt_status']}
                    for row in rows if row['receipt_state'] == 'gap' and row['native_receipt_status'] != 'disabled']
            from .boundary_store import publish_staged_stream_chunks
            if batch['mode'] == 'sealed_pages':
                if batch.get('primary_cache_policy') is not None:
                    from .recording_primary_provenance import primary_cache_usage
                    if not primary_cache_schema(db,self.store) or not primary_cache_capable(self.store):
                        raise ValueError('Primary cache coordinator unavailable for publication')
                    primary_cache_usage(db,bid)
                    page=db.execute("SELECT id FROM recording_pages WHERE batch_id=? AND released=0",(bid,)).fetchone()
                    if page is None:raise ValueError('Primary cache publication page missing')
                    before=self._cache_publication_snapshot(db,bid,page[0])
                    self._primary_cache_publish_unknown=(bid,page[0],before,None)
                    if owner is not None:
                        key=owner.retention_store_key
                        if (self.store.moss_automatic.capture is not cells or cells.owner is not owner
                                or owner._epoch is not epoch or not owner.health()['ready']
                                or type(key) is not str or re.fullmatch('[0-9a-f]{64}',key) is None):
                            raise ValueError('Primary cache binding owner changed')
                        prior=batch['primary_cache_store_key']
                        if prior is not None and prior!=key:
                            raise ValueError('Primary cache store binding changed')
                        if prior is None and db.execute('''UPDATE recording_batches SET primary_cache_store_key=?
                            WHERE id=? AND primary_cache_policy=? AND primary_cache_store_key IS NULL''',
                                (key,bid,PRIMARY_CACHE_POLICY)).rowcount!=1:
                            raise ValueError('Primary cache binding write changed')
                    released=self._release_page(db,batch,rows,gaps)
                    after=self._cache_publication_snapshot(db,bid,page[0])
                    self._primary_cache_publish_unknown=(bid,page[0],before,after)
                    if owner is not None and (cells.owner is not owner or owner._epoch is not epoch
                            or owner.retention_store_key!=key or not owner.health()['ready']):
                        raise ValueError('Primary cache binding owner changed before publication commit')
                    return released
                return self._release_page(db, batch, rows, gaps)
            publish_staged_stream_chunks(db, [row['id'] for row in rows])
            db.execute("UPDATE recording_batches SET state='queued',blockers=? WHERE id=?", (_json(gaps), bid))
            return True

    def _interrupt_pending(self, bid, reason):
        with self.store.db() as db:
            db.execute('PRAGMA busy_timeout=5')  # Bounded maintenance admission, primary DB only.
            db.execute("""UPDATE recording_batches SET state='interrupted',blockers=?
                WHERE id=? AND state='pending_receipts'""", (_json([{'kind': 'recording_authority_unavailable',
                'detail': str(reason)[:256]}]), bid))
            db.execute("UPDATE recording_pages SET state='unresolved',blockers=? WHERE batch_id=? AND released=0 AND state='pending_receipts'", (_json([{'kind':'recording_authority_unavailable','detail':str(reason)[:256]}]),bid))

    def _receipt_status(self, cid, status, *, durable=False, gap=False, attempted=False):
        with self.store.db() as db:
            db.execute('PRAGMA busy_timeout=5')  # Bounded maintenance admission, primary DB only.
            changed = db.execute('''UPDATE recording_work SET native_receipt_status=?,
                receipt_state=CASE WHEN ? THEN 'durable' WHEN ? THEN 'gap' ELSE receipt_state END,
                receipt_attempts=MIN(2147483647,receipt_attempts+?) WHERE chunk_id=? AND receipt_state='pending'
                AND batch_id IN (SELECT id FROM recording_batches WHERE state='pending_receipts')''',
                (status[:128], int(durable), int(gap), int(attempted), cid)).rowcount
        # Return only after the context manager positively commits.
        return changed

    def advance(self, cells, *, force=False, batch_id=None, stop=lambda:False):
        """At most two nonwaiting steps; only durable progress earns a second.

        The deadline is cooperative, not an interruption of SQLite or owner
        work. Existing per-operation budgets remain authoritative. A second
        step never bypasses another caller's cooldown, and no lock spans steps.
        """
        deadline = time.monotonic() + RECEIPT_VISIT_SECONDS
        for step in range(RECEIPT_VISIT_STEPS):
            if stop() or (step and time.monotonic() >= deadline):
                return False
            result = self._advance_receipt_step(cells,
                force=force if step == 0 else False, batch_id=batch_id)
            if result is not _RECEIPT_DURABLE_PROGRESS:
                return result
        return False

    def _advance_receipt_step(self, cells, *, force=False, batch_id=None):
        """One nonwaiting maintenance step in the EXISTING worker loop.

        At most one batch and one receipt/retirement per tick, with a one-second
        retry floor. No model, thread, busy polling, SQL outbox in CellStore, or
        regenerated observation. Other ordinary work stays claimable.
        """
        if not force and time.monotonic() < self._next_tick:
            return False
        if not self._maintenance_lock.acquire(blocking=False):
            return False
        bid = None
        try:
            # A competing HTTP/worker visit may have installed a retry floor
            # after the optimistic check but before this lock acquisition.
            if not force and time.monotonic() < self._next_tick:
                return False
            self._next_tick = time.monotonic() + 1.0
            if self._primary_cache_publish_unknown is not None:
                self._reconcile_primary_cache_publication()
                return False
            if self._flight is not None:
                flight = self._flight
                if not flight['operation'].slot_released.is_set():
                    return False
                self._flight = None
                bid, cid = flight['batch_id'], flight['chunk_id']
                with self.store.db() as db:
                    row = db.execute('''SELECT w.native_receipt FROM recording_work w JOIN recording_batches b
                        ON b.id=w.batch_id WHERE w.chunk_id=? AND b.state='pending_receipts'
                        AND w.receipt_state='pending' ''', (cid,)).fetchone()
                if row is None:
                    return False  # Cancelled/deleted; never resurrect an acknowledgement.
                outcome = flight['operation'].result
                if outcome.get('reason') in ('conflicting_ingestion_receipt', 'source_authority_unavailable',
                                             'source_changed_before_receipt_commit', 'source_changed_after_receipt_commit'):
                    self._interrupt_pending(bid, outcome['reason']); return False
                if outcome.get('reason') == 'source_receipt_capacity':
                    self._receipt_status(cid, 'source_receipt_capacity', gap=True)
                    return False  # Explicit terminal optional gap; payload retained.
                # A postcommit failure may leave no cache entry. Do not infer an
                # acknowledgement: a later exact owner replay reconciles its DB.
                if cells is not None and cells.owner is flight['owner']:
                    from .native_owned_cell_store import Receipt, Budget
                    receipt = self._decode_receipt(row['native_receipt'])
                    epoch = cells.owner._epoch
                    retained = cells.owner.receipt(cid)
                    if retained is not None and retained != receipt:
                        raise ValueError('Conflicting owner receipt for the staged recording')
                    if retained == receipt:
                        budget = Budget()
                        with budget.phase():
                            if not cells.authority.receipt_valid(receipt, budget):
                                raise ValueError('Recording receipt authority changed after delivery')
                        if cells.owner._epoch is not epoch or not cells.owner.health()['ready']:
                            return False
                        if self._receipt_status(cid, 'receipt_retained', durable=True) != 1:
                            return False
                        with self.store.db() as db:
                            pending = db.execute("SELECT 1 FROM recording_work WHERE batch_id=? AND receipt_state='pending' LIMIT 1", (bid,)).fetchone()
                        if pending is None:
                            return self._publish_pending(bid, cells)
                        # The durable write and pending read both returned.
                        # Clear only our successful step's cooldown, under the
                        # maintenance lock. Unknown writes never reach here.
                        self._next_tick = 0.
                        return _RECEIPT_DURABLE_PROGRESS
                self._receipt_status(cid, 'receipt_pending_retry')
                return False
            with self.store.db() as db:
                batch_row = db.execute("""SELECT * FROM recording_batches WHERE state='pending_receipts'
                    AND (? IS NULL OR id=?) ORDER BY created,id LIMIT 1""", (batch_id, batch_id)).fetchone()
                if batch_row is None:
                    return False
                batch = dict(batch_row); bid = batch['id']
                self._session(db, batch['session_id'])
                rows = self._pending_manifest(db, batch)
                pending = next((row for row in rows if row['receipt_state'] == 'pending'), None)
            if pending is None:
                return self._publish_pending(bid, cells)
            cid = pending['id']
            if cells is None or not cells.owner.health()['ready']:
                self._receipt_status(cid, 'owner_unavailable_pending'); return False
            from .native_owned_cell_store import Receipt, Budget, Operation
            receipt = self._decode_receipt(pending['native_receipt'])
            epoch = cells.owner._epoch
            budget = Budget()
            with budget.phase():
                if not cells.authority.receipt_valid(receipt, budget):
                    raise ValueError('Recording source authority changed before receipt delivery')
            # Never consume or close an observation to probe admission. Its exact
            # canonical payload is already durable and remains so on every race.
            if cells.owner._epoch is not epoch or not cells.owner.health()['ready']:
                return False
            occupied = cells.owner._slot
            if occupied is not None:
                self._receipt_status(cid, 'owner_busy_pending'); return False
            retained = cells.owner.receipt(cid)
            if retained is not None:
                if retained != receipt:
                    raise ValueError('Conflicting owner receipt for the staged recording')
                if self._receipt_status(cid, 'existing_receipt', durable=True) != 1:
                    return False
                self._next_tick = 0.
                return _RECEIPT_DURABLE_PROGRESS
            result = cells.owner.submit('receipt', receipt)
            self._receipt_status(cid, 'receipt_in_flight' if type(result) is Operation else 'receipt_pending_retry', attempted=True)
            if type(result) is Operation:
                self._flight = {'batch_id': bid, 'chunk_id': cid, 'owner': cells.owner, 'operation': result}
            return False
        except Unavailable:
            # Cooperative deadlines, revocation/shutdown and optional budgets
            # do not destroy an observed payload or release pending work.
            return False
        except (ValueError, OSError, KeyError, TypeError) as exc:
            if bid is not None:
                try:
                    self._interrupt_pending(bid, exc)
                except Exception:
                    pass  # Ordinary worker stays alive on contended reporting.
            return False
        except Exception:
            # Lock contention, owner shutdown and uncertain optional I/O remain
            # retryable. Durable payloads are not discarded or called ready.
            return False
        finally:
            self._maintenance_lock.release()

    def cancel(self, sid, capture_id):
        if not self._maintenance_lock.acquire(blocking=False):
            raise OverflowError('Recording maintenance is active; retry cancellation')
        try:
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self._session(db, sid); batch = self._batch(db, sid, capture_id)
                if batch['mode'] == 'sealed_pages':
                    abort_capture(db, batch['id'])
                    return self._capture_summary(db, self._batch(db, sid, capture_id))
                if batch['state'] == 'cancelled':
                    return self.status(sid, capture_id)
                if batch['state'] != 'pending_receipts':
                    raise ValueError('Only a recording awaiting receipts can use recording cancellation')
                db.execute("UPDATE recording_batches SET state='cancelled',blockers=? WHERE id=?",
                           (_json([{'kind': 'recording_cancelled', 'detail': 'Originals, maps and receipt payloads retained'}]), batch['id']))
                db.execute("UPDATE chunks SET status='cancelled' WHERE status='staging' AND id IN (SELECT chunk_id FROM recording_work WHERE batch_id=?)", (batch['id'],))
                db.execute("UPDATE recording_work SET receipt_state='cancelled',native_receipt_status='recording_cancelled' WHERE batch_id=? AND receipt_state='pending'", (batch['id'],))
            return self.status(sid, capture_id)
        finally:
            self._maintenance_lock.release()

    def _page(self, db, bid, number):
        row = db.execute('SELECT * FROM recording_pages WHERE batch_id=? AND page_number=?', (bid, number)).fetchone()
        if row is None:
            raise KeyError('Recording page not found')
        return dict(row)

    def _capture_summary(self, db, batch):
        counts = db.execute('SELECT COUNT(*),SUM(released) FROM recording_pages WHERE batch_id=?', (batch['id'],)).fetchone()
        pending = db.execute('SELECT page_number,state FROM recording_pages WHERE batch_id=? AND released=0', (batch['id'],)).fetchone()
        return {'capture_id': batch['capture_id'], 'coordinate_domain': batch['capture_id'],
                'mode': batch['mode'], 'state': batch['state'], 'revision': batch['revision'],
                'capture_origin_frame': batch['capture_origin_frame'], 'capture_end_frame': batch['capture_end_frame'],
                'eos': bool(batch['eos']), 'stream_id': f"recording.{batch['id']}", 'format': FORMAT,
                'policy': PAGE_POLICY, 'page_count': counts[0], 'released_pages': counts[1] or 0,
                'pending_page': dict(pending) if pending else None, 'blockers': json.loads(batch['blockers']),
                'limits': {'max_pages': MAX_PAGES, 'max_sources_per_page': MAX_SOURCES,
                    'max_chunks_per_page': MAX_PAGE_CHUNKS, 'max_source_frames': MAX_SOURCE_FRAMES,
                    'chunk_frames': CHUNK_FRAMES, 'max_page_file_bytes': MAX_BATCH_BYTES,
                    'metadata_bytes_per_page': METADATA_RESERVATION,
                    'max_capture_bytes': self.settings.recording_capture_bytes,
                    'max_total_bytes': self.settings.recording_total_bytes,
                    'disk_reserve_bytes': max(self.settings.disk_reserve_bytes, self.settings.recording_disk_reserve_bytes)}}

    def _page_summary(self, page):
        return {key: page[key] for key in ('id', 'page_number', 'revision', 'state', 'released',
            'start_frame', 'end_frame', 'eos', 'created')} | {'released': bool(page['released']),
            'eos': bool(page['eos']), 'blockers': json.loads(page['blockers'])}

    def _page_detail(self, db, batch, page):
        sources = [dict(row) for row in db.execute('''SELECT id,seq,idempotency_key,digest,frames,
            bytes,source_start_frame,format,created FROM recording_sources WHERE page_id=? ORDER BY seq LIMIT 17''', (page['id'],))]
        work = [dict(row) for row in db.execute('''SELECT w.chunk_id,w.position,w.frames,w.bytes,
            w.capture_start_frame,w.native_receipt_status,w.receipt_state,w.receipt_id,w.receipt_attempts,
            c.status,c.digest,c.timeline_start,c.duration FROM recording_work w JOIN chunks c ON c.id=w.chunk_id
            WHERE w.page_id=? ORDER BY w.position LIMIT 29''', (page['id'],))]
        ranges = [dict(row) for row in db.execute('''SELECT r.source_id,r.chunk_id,r.source_start_frame,
            r.capture_start_frame,r.work_start_frame,r.frames,r.relation FROM recording_ranges r
            JOIN recording_sources s ON s.id=r.source_id WHERE s.page_id=?
            ORDER BY r.source_id,r.source_start_frame LIMIT 913''', (page['id'],))]
        if len(sources) > MAX_SOURCES or len(work) > MAX_PAGE_CHUNKS or len(ranges) > 912:
            raise ValueError('Recording page projection exceeds finite bounds')
        return self._page_summary(page) | {'capture_id': batch['capture_id'], 'policy': PAGE_POLICY,
            'capture_origin_frame': batch['capture_origin_frame'], 'capture_end_frame': batch['capture_end_frame'],
            'sources': sources, 'work_chunks': work, 'ranges': ranges,
            'optional_receipts': {'pending': sum(r['receipt_state']=='pending' for r in work),
                'durable': sum(r['receipt_state']=='durable' for r in work),
                'gaps': sum(r['receipt_state']=='gap' and r['native_receipt_status']!='disabled' for r in work),
                'ready': bool(work) and all(r['receipt_state']=='durable' for r in work)}}

    def page_status(self, sid, capture_id, page_number):
        with self.store.db() as db:
            db.execute('BEGIN'); self._session(db, sid)
            batch = self._batch(db, sid, capture_id)
            return self._page_detail(db, batch, self._page(db, batch['id'], page_number))

    def list_pages(self, sid, capture_id, after=0, limit=32):
        if type(after) is not int or not 0 <= after <= MAX_PAGES or type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError('Page listing requires after=0..1024 and limit=1..64')
        with self.store.db() as db:
            db.execute('BEGIN'); self._session(db, sid); batch = self._batch(db, sid, capture_id)
            pages = [self._page_summary(dict(row)) for row in db.execute('''SELECT * FROM recording_pages
                WHERE batch_id=? AND page_number>? ORDER BY page_number LIMIT ?''', (batch['id'], after, limit))]
            return {'pages': pages, 'next_cursor': pages[-1]['page_number'] if pages else after}

    def _page_resources(self, additional=0):
        # Preserve live execution permits and avoid rerunning Settings validation
        # or policy-file loading for every streaming block.
        check_resources(self.settings, additional)
        floor = max(self.settings.disk_reserve_bytes, self.settings.recording_disk_reserve_bytes)
        if shutil.disk_usage(self.settings.data_dir).free < floor + additional:
            raise RuntimeError('Recording disk reserve reached; free space or delete retained sessions')

    def _page_census(self, bid, pid=None):
        with self.store.db() as db:
            counts = db.execute('SELECT COUNT(*) FROM recording_batches').fetchone()[0]
            pages = db.execute('SELECT COUNT(*) FROM recording_pages').fetchone()[0]
            own = db.execute('SELECT COUNT(*) FROM recording_pages WHERE batch_id=?', (bid,)).fetchone()[0]
        total = (counts + pages) * METADATA_RESERVATION
        capture = (1 + own) * METADATA_RESERVATION
        page = METADATA_RESERVATION if pid else 0
        with os.scandir(self.uploads) as entries:
            for entry in entries:
                if not entry.name.startswith('ro-'):
                    continue
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Unsafe file in the recording upload namespace')
                charge = max(info.st_size, info.st_blocks * 512)
                total += charge
                if entry.name.startswith(f'ro-{bid}-'):
                    capture += charge
                if pid and entry.name.startswith(f'ro-{bid}-page-{pid}-'):
                    page += charge
        return total, capture, page

    def reserve_page_bytes(self, batch, page, additional, *, new_page=False, new_batch=False):
        """One allocated-byte census and full reservation, held across all writes."""
        if type(additional) is not int or additional < 0:
            raise ValueError('Invalid recording reservation')
        with self._reservation_lock:
            total, capture, own = self._page_census(batch['id'], page['id'] if page else None)
            reserve = ((additional + 4095) // 4096) * 4096
            metadata = METADATA_RESERVATION * (int(new_page) + int(new_batch))
            total += metadata; capture += METADATA_RESERVATION * int(new_page)
            for value in self._reservations.values():
                total += value['bytes']
                if value['batch_id'] == batch['id']:
                    capture += value['bytes']
                if page and value['page_id'] == page['id']:
                    own += value['bytes']
            if (total + reserve > self.settings.recording_total_bytes
                    or capture + reserve > self.settings.recording_capture_bytes
                    or page and own + reserve > MAX_BATCH_BYTES + METADATA_RESERVATION):
                raise RecordingStorageCapacityError('Recording page/capture/retained-copy quota reached')
            self._page_resources(reserve + metadata + sum(r['bytes'] for r in self._reservations.values()))
            token = uuid.uuid4().hex
            self._reservations[token] = {'batch_id': batch['id'], 'page_id': page['id'] if page else None, 'bytes': reserve}
            return token

    def release_reservation(self, token):
        with self._reservation_lock:
            self._reservations.pop(token, None)

    def create_page(self, sid, capture_id, page_number, capture_origin_frame=None):
        if not isinstance(capture_id, str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', capture_id) is None:
            raise ValueError('capture_id must be a bounded explicit sample-coordinate domain')
        if type(page_number) is not int or not 1 <= page_number <= MAX_PAGES:
            raise ValueError('page_number must be 1..1024')
        _coordinate(capture_origin_frame)
        with self._reservation_lock, self.store.db() as db:
            db.execute('BEGIN IMMEDIATE'); self._session(db, sid)
            old = db.execute('SELECT * FROM recording_batches WHERE session_id=?', (sid,)).fetchone()
            new_batch = old is None
            if new_batch:
                if page_number != 1 or capture_origin_frame is None:
                    raise ValueError('First page must declare capture_origin_frame and page_number=1')
                if db.execute('SELECT 1 FROM chunks WHERE session_id=? LIMIT 1', (sid,)).fetchone():
                    raise ValueError('Begin recording in a fresh empty legacy session')
                if db.execute('SELECT COUNT(*) FROM recording_batches').fetchone()[0] >= MAX_BATCHES:
                    raise OverflowError('Recording capture capacity reached')
                batch = {'id': uuid.uuid4().hex, 'capture_origin_frame': capture_origin_frame}
            else:
                batch = dict(old)
                if batch['mode'] != 'sealed_pages' or batch['capture_id'] != capture_id:
                    raise ValueError('Existing capture cannot be upgraded or replaced by pages')
                if capture_origin_frame is not None and capture_origin_frame != batch['capture_origin_frame']:
                    raise ValueError('Capture origin is immutable')
                if batch.get('primary_cache_policy') is not None:
                    from .recording_primary_provenance import primary_cache_usage
                    if not primary_cache_schema(db,self.store):raise ValueError('Primary cache schema changed')
                    primary_cache_usage(db,batch['id'])
                prior = db.execute('SELECT * FROM recording_pages WHERE batch_id=? AND page_number=?', (batch['id'], page_number)).fetchone()
                if prior:
                    return self._page_detail(db, batch, dict(prior))
                if batch['eos'] or batch['state'] == 'aborted':
                    raise ValueError('Capture is finalized or aborted')
                last = db.execute('SELECT page_number,released FROM recording_pages WHERE batch_id=? ORDER BY page_number DESC LIMIT 1', (batch['id'],)).fetchone()
                if last and (not last['released'] or page_number != last['page_number'] + 1):
                    raise ValueError('Only the next page after a released page can be created')
            token = self.reserve_page_bytes(batch, None, 0, new_page=True, new_batch=new_batch)
            self.release_reservation(token)
            cache_schema=primary_cache_schema(db,self.store)
            if new_batch:
                marker=None
                if (cache_schema and primary_cache_capable(self.store)
                        and self.settings.native_owned_compact_gc_enabled and self.settings.native_owned_receipt_gc_enabled):
                    from .moss_automatic import TABLE
                    if (db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY auto_session_seq WHERE sid=? LIMIT 1',(sid,)).fetchone()
                            or db.execute('SELECT 1 FROM native_core_commits INDEXED BY native_core_adjacent WHERE sid=? LIMIT 1',(sid,)).fetchone()):
                        raise ValueError('Primary cache requires fresh exclusive session')
                    from .recording_primary_provenance import primary_cache_admission,Refused
                    try:primary_cache_admission(self.store,db,batch['id'])
                    except Refused:pass  # Only positively known pre-write optional refusal.
                    else:marker=PRIMARY_CACHE_POLICY
                if cache_schema:
                    db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created,mode,
                        capture_origin_frame,capture_end_frame,primary_cache_policy) VALUES(?,?,?,?,?,?,?,?)''',
                        (batch['id'],sid,capture_id,time.time(),'sealed_pages',capture_origin_frame,capture_origin_frame,marker))
                else:
                    db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created,mode,
                        capture_origin_frame,capture_end_frame) VALUES(?,?,?,?,?,?,?)''',
                        (batch['id'],sid,capture_id,time.time(),'sealed_pages',capture_origin_frame,capture_origin_frame))
            elif batch.get('primary_cache_policy') is not None:
                from .recording_primary_provenance import primary_cache_usage
                primary_cache_usage(db,batch['id'])
            batch = self._batch(db, sid, capture_id)
            db.execute('INSERT INTO recording_pages(id,batch_id,page_number,start_frame,created) VALUES(?,?,?,?,?)',
                (uuid.uuid4().hex, batch['id'], page_number, batch['capture_end_frame'], time.time()))
            return self._page_detail(db, batch, self._page(db, batch['id'], page_number))

    def accept_page(self, sid, capture_id, page_number, key, path, source_start_frame=None):
        _text(key, 'Idempotency-Key'); _coordinate(source_start_frame)
        with self.store.db() as db:
            self._session(db, sid); batch = self._batch(db, sid, capture_id)
            page = self._page(db, batch['id'], page_number)
        path = Path(path)
        if (path.parent.resolve() != self.uploads or path.suffix != '.part'
                or not path.name.startswith(f"ro-{batch['id']}-page-{page['id']}-source-")):
            raise ValueError('Recording upload must use its owned page staging path')
        size = path.stat().st_size
        if not 0 < size <= min(MAX_SOURCE_BYTES, self.settings.max_upload_bytes):
            raise OverflowError('Recording source byte limit exceeded')
        source = inspect_pcm16_source(uuid.uuid4().hex, path)
        if source.format != (RATE, 1, 2, 'wav-pcm16le'):
            raise ValueError('Recording sources require original PCM16 mono 16000 Hz WAV')
        if not 0 < source.frames <= min(MAX_SOURCE_FRAMES, math.floor(self.settings.max_audio_seconds * RATE)):
            raise OverflowError('Recording source duration limit exceeded')
        if source_start_frame is not None and source_start_frame + source.frames > MAX_COORDINATE:
            raise ValueError('Capture coordinate exceeds exact integer range')
        final = path.with_suffix('.audio'); committed = False
        try:
            with self._reservation_lock, self.store.db() as db:
                db.execute('BEGIN IMMEDIATE'); self._session(db, sid)
                batch = self._batch(db, sid, capture_id); page = self._page(db, batch['id'], page_number)
                old = db.execute('SELECT * FROM recording_sources WHERE batch_id=? AND idempotency_key=?', (batch['id'], key)).fetchone()
                if old:
                    if (old['page_id'], old['digest'], old['source_start_frame'], old['format']) != (page['id'], source.sha256, source_start_frame, FORMAT):
                        raise ValueError('Source idempotency key reused with different page, bytes or placement')
                    return {'source_id': old['id'], 'duplicate': True}
                if page['state'] != 'open' or batch['eos'] or batch['state'] == 'aborted':
                    raise ValueError('Recording page source set is frozen or capture closed')
                if db.execute('SELECT COUNT(*) FROM recording_sources WHERE page_id=?', (page['id'],)).fetchone()[0] >= MAX_SOURCES:
                    raise OverflowError('Recording page source count limit reached')
                # Direct callers have already written the owned staging file;
                # HTTP holds a full upload reservation across this method.
                if not any(r['page_id'] == page['id'] for r in self._reservations.values()):
                    token = self.reserve_page_bytes(batch, page, 0); self.release_reservation(token)
                os.replace(path, final); self._sync_uploads()
                db.execute('''INSERT INTO recording_sources(id,batch_id,page_id,idempotency_key,digest,path,
                    frames,bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    (source.fragment_id, batch['id'], page['id'], key, source.sha256, str(final), source.frames,
                     size, source_start_frame, FORMAT, time.time()))
                db.execute('UPDATE recording_pages SET revision=revision+1 WHERE id=?', (page['id'],))
                db.execute('UPDATE recording_batches SET revision=revision+1 WHERE id=?', (batch['id'],))
            committed = True
            return {'source_id': source.fragment_id, 'duplicate': False}
        finally:
            if not committed and not getattr(self.store,'_strict_enabled',False):
                final.unlink(missing_ok=True)

    def _history(self, db, batch, position):
        from .boundary_store import PUBLISHED_CHUNK_SQL
        row = db.execute(f'''SELECT w.chunk_id,w.position,w.frames,w.bytes,w.capture_start_frame,
            w.file_signature,c.path,c.digest,c.status,c.timeline_start,c.duration,
            ({PUBLISHED_CHUNK_SQL}) AS published FROM recording_work w JOIN chunks c ON c.id=w.chunk_id
            WHERE w.batch_id=? AND w.position=?''', (batch['id'], position)).fetchone()
        if row is None:
            raise ValueError('Historical canonical work is missing')
        item = dict(row)
        if (not item['published'] or item['status'] == 'cancelled' or item['file_signature'] is None
                or item['capture_start_frame'] != batch['capture_origin_frame'] + position * CHUNK_FRAMES
                or item['timeline_start'] != position * CHUNK_FRAMES / RATE
                or not 0 < item['frames'] <= CHUNK_FRAMES or item['duration'] != item['frames'] / RATE):
            raise ValueError('Historical canonical work authority changed')
        path = Path(item['path'])
        if (path.parent != self.uploads or not path.name.startswith(f"ro-{batch['id']}-page-")
                or not path.name.endswith(f'-work-{position:03}.audio')):
            raise ValueError('Historical canonical work path changed')
        return item

    def _verify_work_file(self, item):
        path = Path(item['path'])
        info = path.lstat()
        signature = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        if not stat.S_ISREG(info.st_mode) or signature != json.loads(item['file_signature']):
            raise ValueError('Canonical retained file signature changed')
        source = inspect_pcm16_source(item['chunk_id'], path)
        after = path.stat()
        if (source.sha256 != item['digest'] or source.frames != item['frames']
                or source.format != (RATE, 1, 2, 'wav-pcm16le') or info.st_size != item['bytes']
                or signature != [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns]):
            raise ValueError('Canonical retained work bytes changed')

    def _verify_aliases(self, db, batch, expected, *, verify_files):
        if not isinstance(expected, list) or len(expected) > MAX_HISTORY_REFS:
            raise ValueError('Historical alias reference capacity changed')
        for old in expected:
            item = self._history(db, batch, old['position'])
            projection = {key: item[key] for key in old}
            if projection != old:
                raise ValueError('Historical alias authority changed')
            if verify_files:
                self._verify_work_file(item)

    def _plan_page(self, batch, page, sources):
        frontier, end = page['start_frame'], page['end_frame']
        blockers = self._verify_originals(sources)
        if not sources:
            blockers.append({'kind': 'no_sources'})
        if len(sources) > MAX_SOURCES:
            blockers.append({'kind': 'page_source_capacity'})
        for source in sources:
            start = source['source_start_frame']
            if start is None:
                blockers.append({'kind': 'unknown_placement', 'source_id': source['id']})
            elif start < batch['capture_origin_frame'] or start + source['frames'] > end:
                blockers.append({'kind': 'source_outside_seal', 'source_id': source['id']})
        if blockers:
            return [], [], [], blockers
        historical, references, ranges = {}, 0, []
        try:
            with self.store.db() as db:
                for source in sources:
                    start = source['source_start_frame']; stop = min(start + source['frames'], frontier)
                    if start >= stop:
                        continue
                    first = (start - batch['capture_origin_frame']) // CHUNK_FRAMES
                    last = (stop - 1 - batch['capture_origin_frame']) // CHUNK_FRAMES
                    if last - first + 1 > MAX_HISTORY_PER_SOURCE:
                        raise ValueError('Historical source reference bound exceeded')
                    for position in range(first, last + 1):
                        references += 1
                        if references > MAX_HISTORY_REFS:
                            raise ValueError('Historical page reference bound exceeded')
                        if position not in historical:
                            item = self._history(db, batch, position); self._verify_work_file(item)
                            historical[position] = item
                        item = historical[position]; a = max(start, item['capture_start_frame'])
                        b = min(stop, item['capture_start_frame'] + item['frames'])
                        if a >= b or not _equal(source, a - start, item, a - item['capture_start_frame'], b - a):
                            blockers.append({'kind': 'pcm_conflict', 'source_id': source['id'], 'chunk_id': item['chunk_id']})
                        ranges.append({'source_id': source['id'], 'chunk_id': item['chunk_id'],
                            'start': a, 'end': b, 'work_start': item['capture_start_frame'], 'relation': 'exact_alias'})
        except (ValueError, OSError, wave.Error) as exc:
            blockers.append({'kind': 'historical_authority_unavailable', 'detail': str(exc)[:256]})
        # The interval planner is applied only to the novel suffix. Historical
        # aliases can be disjoint; no missing historical audio is new work.
        owners = []; cursor = frontier
        ordered = sorted(sources, key=lambda s: (s['source_start_frame'], s['seq']))
        for source in ordered:
            start = max(frontier, source['source_start_frame']); stop = source['source_start_frame'] + source['frames']
            if start >= stop:
                continue
            if start > cursor:
                blockers.append({'kind': 'capture_gap', 'start_frame': cursor, 'end_frame': start})
            for owner in owners:
                a, b = max(start, owner['start']), min(stop, owner['end'])
                if a < b:
                    if not _equal(source, a-source['source_start_frame'], owner['source'], a-owner['source']['source_start_frame'], b-a):
                        blockers.append({'kind': 'pcm_conflict', 'source_id': source['id'], 'other_source_id': owner['source']['id']})
                    ranges.append({'source_id': source['id'], 'start': a, 'end': b, 'relation': 'exact_alias'})
            a = max(start, cursor)
            if a < stop:
                owners.append({'source': source, 'start': a, 'end': stop})
                ranges.append({'source_id': source['id'], 'start': a, 'end': stop, 'relation': 'owner'})
            cursor = max(cursor, stop)
        if cursor != end:
            blockers.append({'kind': 'capture_gap', 'start_frame': cursor, 'end_frame': end})
        aliases = [{k: item[k] for k in ('chunk_id','position','frames','bytes','capture_start_frame','file_signature','path','digest')}
                   for _, item in sorted(historical.items())]
        return owners, ranges, aliases, blockers

    def _cleanup_page_work(self, batch, page):
        """Only exact-page, unreferenced generated copies may be rebuilt."""
        prefix = f"ro-{batch['id']}-page-{page['id']}-work-"
        with self.store.db() as db, os.scandir(self.uploads) as entries:
            for entry in entries:
                if not entry.name.startswith(prefix) or not re.fullmatch(re.escape(prefix)+r'\d+\.(part|audio)', entry.name):
                    continue
                if db.execute('SELECT 1 FROM chunks WHERE path=?', (entry.path,)).fetchone():
                    raise ValueError('Frozen page unexpectedly has staged work; refusing rebuild')
                if db.execute('SELECT 1 FROM session_file_cleanup WHERE path=?', (entry.path,)).fetchone():
                    raise ValueError('Frozen page has retained cleanup authority')
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError('Unsafe page staging file')
                os.unlink(entry.path)

    def seal_page(self, sid, capture_id, page_number, key, expected_revision, end_frame, eos=False, cells=None):
        _text(key, 'Seal idempotency_key'); _coordinate(end_frame)
        if type(expected_revision) is not int or expected_revision < 0 or type(eos) is not bool or end_frame is None:
            raise ValueError('Invalid page seal revision, end or EOS')
        request = _json({'expected_revision': expected_revision, 'end_frame': end_frame, 'eos': eos, 'policy': PAGE_POLICY})
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE'); self._session(db, sid)
            batch = self._batch(db, sid, capture_id); page = self._page(db, batch['id'], page_number)
            if page['seal_key'] is not None:
                if (page['seal_key'], page['seal_request']) != (key, request):
                    raise ValueError('Recording page frozen with a different seal request')
                if page['state'] not in ('sealed', 'preparing'):
                    return self._page_detail(db, batch, page)
            else:
                if batch['eos'] or batch['state'] == 'aborted' or page['state'] != 'open':
                    raise ValueError('Capture is finalized or aborted')
                if page['revision'] != expected_revision:
                    raise ValueError('Recording page source revision changed')
                frames = end_frame - batch['capture_end_frame']
                if (frames < 0 or frames > MAX_PAGE_CHUNKS * CHUNK_FRAMES
                        or not eos and (end_frame - batch['capture_origin_frame']) % CHUNK_FRAMES):
                    raise ValueError('Page end must fit 28 chunks and the capture-origin-relative grid unless EOS')
                db.execute('''UPDATE recording_pages SET seal_key=?,seal_request=?,start_frame=?,end_frame=?,eos=?,state='sealed'
                    WHERE id=?''', (key, request, batch['capture_end_frame'], end_frame, int(eos), page['id']))
                page = self._page(db, batch['id'], page_number)
            if batch['state'] == 'aborted' or batch['capture_end_frame'] != page['start_frame']:
                raise ValueError('Frozen recording page frontier changed')
            db.execute("UPDATE recording_pages SET state='preparing',blockers='[]' WHERE id=?", (page['id'],))
            sources = [dict(row) for row in db.execute('SELECT * FROM recording_sources WHERE page_id=? ORDER BY seq LIMIT 17', (page['id'],))]
        token = None; pieces = []; committed = False
        try:
            owners, ranges, aliases, blockers = self._plan_page(batch, page, sources)
            if blockers:
                with self.store.db() as db:
                    db.execute("UPDATE recording_pages SET state='unresolved',blockers=? WHERE id=? AND state='preparing'", (_json(blockers), page['id']))
                return self.page_status(sid, capture_id, page_number)
            count = math.ceil((end_frame-page['start_frame'])/CHUNK_FRAMES)
            if count:
                self.store.capacity(sid, f"recording.{batch['id']}", additional=count)
            self._cleanup_page_work(batch, page)
            token = self.reserve_page_bytes(batch, page, (end_frame-page['start_frame'])*2 + count*4096)
            pieces = self._materialize(dict(batch, _page=page), owners, page['start_frame'], end_frame, cells)
            if self._verify_originals(sources):
                raise ValueError('Original page source changed during materialization')
            manifest = {'policy': PAGE_POLICY, 'outbox_version': 1, 'origin': batch['capture_origin_frame'],
                'start': page['start_frame'], 'end': end_frame, 'eos': eos, 'revision': expected_revision,
                'page_id': page['id'], 'seal_key': key, 'seal_request': request,
                'sources': [{'id': s['id'], 'digest': s['digest'], 'start': s['source_start_frame'], 'frames': s['frames']} for s in sources],
                'aliases': aliases, 'work': [], 'ranges': []}
            by_id = {s['id']:s for s in sources}
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE'); self._session(db, sid)
                current_batch = self._batch(db, sid, capture_id); current = self._page(db, batch['id'], page_number)
                if (current['state'] != 'preparing' or current['seal_request'] != request
                        or current['revision'] != expected_revision or current_batch['state'] == 'aborted'
                        or current_batch['capture_end_frame'] != page['start_frame']):
                    raise ValueError('Frozen recording page changed before stage')
                self._verify_aliases(db, batch, aliases, verify_files=True)
                if count:
                    self.store._check_queue_limit(db, sid, f"recording.{batch['id']}", additional=count)
                for piece in pieces:
                    final = eos and piece is pieces[-1]
                    cid, created = self.store._add_chunk(db, sid, f"recording.{batch['id']}.{piece['position']}",
                        piece['digest'], piece['final'], (piece['end']-piece['start'])/RATE,
                        (piece['start']-batch['capture_origin_frame'])/RATE, stream_id=f"recording.{batch['id']}",
                        stream_final=final, _status='staging')
                    if not created:
                        raise ValueError('Unexpected prior recording page work ID')
                    piece['cid'] = cid
                    receipt = self._observed_receipt(cells, piece, sid, batch['capture_origin_frame'], f"recording.{batch['id']}")
                    piece['receipt_id'] = receipt.receipt_id if receipt else None
                    status = 'receipt_pending' if receipt else ('disabled' if not self.settings.native_owned_cells_enabled and cells is None else 'observation_unavailable')
                    db.execute('''INSERT INTO recording_work(chunk_id,batch_id,page_id,position,frames,bytes,capture_start_frame,
                        native_receipt_status,native_receipt,receipt_id,receipt_state,file_signature) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (cid,batch['id'],page['id'],piece['position'],piece['end']-piece['start'],piece['bytes'],piece['start'],
                         status,receipt.encoded if receipt else None,piece['receipt_id'],'pending' if receipt else 'gap',_json(list(piece['signature']))))
                    manifest['work'].append({'chunk_id':cid,'position':piece['position'],'start':piece['start'],'end':piece['end'],
                        'bytes':piece['bytes'],'digest':piece['digest'],'signature':list(piece['signature']),'receipt_id':piece['receipt_id']})
                for item in ranges:
                    targets = [item] if 'chunk_id' in item else [dict(item, chunk_id=p['cid'], work_start=p['start'],
                        start=max(item['start'],p['start']), end=min(item['end'],p['end'])) for p in pieces
                        if max(item['start'],p['start']) < min(item['end'],p['end'])]
                    for target in targets:
                        a,b=target['start'],target['end']
                        row=(target['source_id'],target['chunk_id'],a-by_id[target['source_id']]['source_start_frame'],
                            a,a-target['work_start'],b-a,target['relation'])
                        manifest['ranges'].append(row)
                        db.execute('''INSERT INTO recording_ranges(batch_id,source_id,chunk_id,source_start_frame,
                            capture_start_frame,work_start_frame,frames,relation) VALUES(?,?,?,?,?,?,?,?)''', (batch['id'],*row))
                manifest['ranges'].sort()
                if len(_json(manifest).encode()) > METADATA_RESERVATION//2 or len(manifest['ranges']) > 912:
                    raise OverflowError('Recording page manifest capacity exceeded')
                db.execute("UPDATE recording_pages SET state='pending_receipts',manifest=?,blockers='[]' WHERE id=?", (_json(manifest),page['id']))
                db.execute("UPDATE recording_batches SET state='pending_receipts',blockers='[]' WHERE id=?", (batch['id'],))
            committed = True
            self.advance(cells,force=True,batch_id=batch['id'])
            return self.page_status(sid,capture_id,page_number)
        except (OverflowError, RuntimeError) as exc:
            with self.store.db() as db:
                db.execute("UPDATE recording_pages SET state='sealed',blockers=? WHERE id=? AND state='preparing'", (_json([{'kind':'retryable_backpressure','detail':str(exc)[:256]}]),page['id']))
            raise
        except (ValueError, OSError) as exc:
            capacity = isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT)
            with self.store.db() as db:
                db.execute("UPDATE recording_pages SET state=?,blockers=? WHERE id=? AND state='preparing'",
                    ('sealed' if capacity else 'unresolved', _json([{
                        'kind':'retryable_backpressure' if capacity else 'page_integrity_conflict',
                        'detail':str(exc)[:256]}]),page['id']))
            raise
        except BaseException:
            with self.store.db() as db:
                db.execute("UPDATE recording_pages SET state='sealed' WHERE id=? AND state='preparing'", (page['id'],))
            raise
        finally:
            # Failed unlink remains charged by the next census, but cannot leak
            # an in-memory reservation after the request has finished.
            if token is not None:
                self.release_reservation(token)
            self._finish_pieces(pieces,committed)

    def _pending_page_manifest(self, db, batch, *, verify_files=False):
        page = db.execute("SELECT * FROM recording_pages WHERE batch_id=? AND released=0 AND state='pending_receipts'", (batch['id'],)).fetchone()
        if page is None or not page['manifest'] or len(page['manifest'].encode()) > METADATA_RESERVATION//2:
            raise ValueError('Recording pending page manifest missing or oversized')
        manifest = json.loads(page['manifest'])
        if (manifest['policy'] != PAGE_POLICY or manifest['outbox_version'] != 1 or manifest['revision'] != page['revision']
                or manifest['page_id'] != page['id'] or manifest['seal_key'] != page['seal_key'] or manifest['seal_request'] != page['seal_request']
                or manifest['origin'] != batch['capture_origin_frame'] or manifest['start'] != batch['capture_end_frame']
                or manifest['start'] != page['start_frame'] or manifest['end'] != page['end_frame'] or manifest['eos'] != bool(page['eos'])
                or len(manifest['work']) > MAX_PAGE_CHUNKS):
            raise ValueError('Recording frozen page geometry changed')
        sources = [dict(r) for r in db.execute('SELECT * FROM recording_sources WHERE page_id=? ORDER BY seq LIMIT 17', (page['id'],))]
        projection = [{'id':s['id'],'digest':s['digest'],'start':s['source_start_frame'],'frames':s['frames']} for s in sources]
        if len(sources)>MAX_SOURCES or projection != manifest['sources']:
            raise ValueError('Recording frozen page sources changed')
        if verify_files and self._verify_originals(sources):
            raise ValueError('Recording page original authority changed')
        self._verify_aliases(db,batch,manifest['aliases'],verify_files=verify_files)
        ranges = [tuple(r) for r in db.execute('''SELECT r.source_id,r.chunk_id,r.source_start_frame,
            r.capture_start_frame,r.work_start_frame,r.frames,r.relation FROM recording_ranges r
            JOIN recording_sources s ON s.id=r.source_id WHERE s.page_id=? ORDER BY r.source_id,r.source_start_frame LIMIT 913''', (page['id'],))]
        if sorted(ranges) != sorted(tuple(r) for r in manifest['ranges']) or len(ranges)>912:
            raise ValueError('Recording frozen page maps changed')
        rows = [dict(r) for r in db.execute('''SELECT c.*,w.chunk_id,w.position,w.frames,w.bytes,w.capture_start_frame,
            w.native_receipt,w.receipt_id,w.receipt_state,w.native_receipt_status,w.file_signature,
            sc.stream_id,sc.position AS stream_position,sc.stream_final,sc.upload_stream_final,sc.session_id AS stream_session_id
            FROM recording_work w JOIN chunks c ON c.id=w.chunk_id LEFT JOIN stream_chunks sc ON sc.chunk_id=c.id
            WHERE w.page_id=? ORDER BY w.position LIMIT 29''', (page['id'],))]
        if len(rows) != len(manifest['work']):
            raise ValueError('Recording staged page work count changed')
        for row,piece in zip(rows,manifest['work']):
            path = self.uploads/f"ro-{batch['id']}-page-{page['id']}-work-{piece['position']:03}.audio"
            if (row['id']!=piece['chunk_id'] or row['status']!='staging' or row['path']!=str(path)
                    or row['session_id']!=batch['session_id'] or row['stream_session_id']!=batch['session_id']
                    or row['digest']!=piece['digest'] or row['position']!=piece['position'] or row['bytes']!=piece['bytes']
                    or row['frames']!=piece['end']-piece['start'] or row['capture_start_frame']!=piece['start']
                    or row['duration']!=row['frames']/RATE or row['timeline_start']!=(piece['start']-manifest['origin'])/RATE
                    or row['stream_id']!=f"recording.{batch['id']}" or row['stream_position']!=piece['position']
                    or bool(row['stream_final'])!=(bool(page['eos']) and piece is manifest['work'][-1])
                    or row['stream_final']!=row['upload_stream_final'] or json.loads(row['file_signature'])!=piece['signature']):
                raise ValueError('Recording staged page work authority changed')
            payload=row['native_receipt']
            if payload is not None:
                if (type(payload) is not bytes or not 1<=len(payload)<=1024 or hashlib.sha256(payload).hexdigest()!=piece['receipt_id']
                        or row['receipt_id']!=piece['receipt_id'] or row['receipt_state'] not in ('pending','durable','gap')):
                    raise ValueError('Recording persisted page observation changed')
            elif piece['receipt_id'] is not None or row['receipt_state']!='gap':
                raise ValueError('Recording persisted page observation incomplete')
            if verify_files:
                self._verify_work_file(row)
        return rows

    def _release_page(self, db, batch, rows, gaps):
        from .boundary_store import publish_staged_stream_chunks, _wake_published_stream
        page = db.execute("SELECT * FROM recording_pages WHERE batch_id=? AND released=0 AND state='pending_receipts'", (batch['id'],)).fetchone()
        if page is None or batch['capture_end_frame']!=page['start_frame'] or batch['eos']:
            raise ValueError('Page release frontier changed')
        if rows:
            publish_staged_stream_chunks(db,[r['id'] for r in rows])
        elif page['eos']:
            tail=db.execute('SELECT chunk_id FROM recording_work WHERE batch_id=? ORDER BY position DESC LIMIT 1',(batch['id'],)).fetchone()
            if tail:
                db.execute('UPDATE stream_chunks SET stream_final=1 WHERE chunk_id=?',(tail['chunk_id'],))
        db.execute("UPDATE recording_pages SET released=1,state='published',blockers=? WHERE id=?",(_json(gaps),page['id']))
        db.execute('UPDATE recording_batches SET capture_end_frame=?,eos=?,state=?,revision=revision+1,blockers=? WHERE id=?',
            (page['end_frame'],page['eos'],'queued' if page['eos'] else 'open',_json(gaps),batch['id']))
        _wake_published_stream(db,batch['session_id'],f"recording.{batch['id']}")
        return True

    def finish_capture(self,sid,capture_id,key,expected_revision):
        from .boundary_store import _wake_published_stream
        request=_json({'expected_revision':expected_revision,'policy':PAGE_POLICY,'eos':True})
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');self._session(db,sid);batch=self._batch(db,sid,capture_id)
            if batch['finish_key'] is not None:
                if (batch['finish_key'],batch['finish_request'])!=(key,request):
                    raise ValueError('Capture frozen with a different finish request')
                return self._capture_summary(db,batch)
            if batch['revision']!=expected_revision or batch['state']=='aborted' or batch['eos']:
                raise ValueError('Capture revision changed or capture is closed')
            if db.execute('SELECT 1 FROM recording_pages WHERE batch_id=? AND released=0',(batch['id'],)).fetchone():
                raise ValueError('Cannot finish a capture with a pending page')
            tail=db.execute('SELECT chunk_id FROM recording_work WHERE batch_id=? ORDER BY position DESC LIMIT 1',(batch['id'],)).fetchone()
            if tail:
                db.execute('UPDATE stream_chunks SET stream_final=1 WHERE chunk_id=?',(tail['chunk_id'],))
            db.execute("UPDATE recording_batches SET eos=1,state='queued',finish_key=?,finish_request=? WHERE id=?",(key,request,batch['id']))
            _wake_published_stream(db,sid,f"recording.{batch['id']}")
            return self._capture_summary(db,self._batch(db,sid,capture_id))


def abort_capture(db, bid):
    """Abort further extension without turning cancellation into acoustic EOF."""
    db.execute("UPDATE recording_batches SET state='aborted',blockers=? WHERE id=? AND mode='sealed_pages'",
        (_json([{'kind':'recording_aborted','detail':'Outstanding work cancelled; released history retained'}]),bid))
    db.execute("UPDATE recording_pages SET state='cancelled',blockers=? WHERE batch_id=? AND released=0",
        (_json([{'kind':'recording_aborted'}]),bid))
    db.execute("""UPDATE chunks SET status='cancelled',error='Recording capture aborted; completed results retained'
        WHERE status IN ('queued','running','paused','failed','waiting_input','staging')
        AND id IN (SELECT chunk_id FROM recording_work WHERE batch_id=?)""",(bid,))
    db.execute("UPDATE recording_work SET receipt_state='cancelled',native_receipt_status='recording_aborted' WHERE batch_id=? AND receipt_state='pending'",(bid,))


# Receipt GC uses existing publication authority, never a new durable frontier.
RECEIPT_GC_PREFIX_ROWS = MAX_PAGE_CHUNKS
RECEIPT_GC_PREFIX_STREAMS = 8


def receipt_gc_source(db, receipt, budget):
    """Exact, completed sealed-page source; retained outbox is historical evidence.

    receipt_state='durable' records its original acknowledgement, not a promise
    that optional source metadata remains retained forever. The outbox payload,
    recording mapping, original files and primary outputs are never rewritten.
    """
    budget.projection(256)
    row = db.execute('''SELECT w.batch_id,w.position,w.capture_start_frame,w.frames,
        b.capture_origin_frame AS origin,b.capture_end_frame,b.state AS batch_state,
        p.page_number,p.start_frame,p.end_frame,
        c.status='completed' AND c.next_offset=c.duration AND c.session_id=?
          AND c.digest=? AND c.duration=? AND c.timeline_start=?
          AND s.deleted=0 AND s.processing_mode='legacy'
          AND b.session_id=c.session_id AND b.mode='sealed_pages' AND b.state!='aborted'
          AND p.batch_id=b.id AND p.released=1 AND p.state='published'
          AND w.receipt_state='durable' AND w.receipt_id=? AND w.native_receipt=?
          AND sc.session_id=c.session_id AND sc.stream_id=? AND sc.position=w.position
          AND ti.session_id=c.session_id AND ti.stream_id=sc.stream_id AND ti.position=sc.position
          AND ti.timeline_start=c.timeline_start AND ti.timeline_end=c.timeline_start+c.duration AS same
        FROM recording_work w JOIN recording_batches b ON b.id=w.batch_id
        JOIN recording_pages p ON p.id=w.page_id JOIN chunks c ON c.id=w.chunk_id
        JOIN sessions s ON s.id=c.session_id JOIN stream_chunks sc ON sc.chunk_id=c.id
        JOIN stream_time_index ti ON ti.chunk_id=c.id WHERE w.chunk_id=?
          AND typeof(w.batch_id)='text' AND length(CAST(w.batch_id AS BLOB)) BETWEEN 1 AND 64
          AND typeof(b.state)='text' AND length(CAST(b.state AS BLOB)) BETWEEN 1 AND 64
          AND typeof(w.position)='integer' AND typeof(w.capture_start_frame)='integer'
          AND typeof(w.frames)='integer' AND typeof(b.capture_origin_frame)='integer'
          AND typeof(b.capture_end_frame)='integer' AND typeof(p.page_number)='integer'
          AND typeof(p.start_frame)='integer' AND typeof(p.end_frame)='integer' ''',
        (receipt.session_id,receipt.digest,receipt.frames/RATE,receipt.timeline_start,
         receipt.receipt_id,receipt.encoded,receipt.stream_id,receipt.chunk_id)).fetchone()
    if row is None or not row['same'] or receipt.stream_id != 'recording.' + row['batch_id']:return None
    origin = row['origin']
    if (type(origin) is not int or not 0 <= origin <= MAX_COORDINATE
            or type(row['capture_end_frame']) is not int
            or not origin <= row['capture_start_frame'] < row['capture_start_frame'] + row['frames'] <= row['capture_end_frame']
            or row['frames'] != receipt.frames
            or receipt.timeline_start != (row['capture_start_frame']-origin)/RATE
            or not row['start_frame'] <= row['capture_start_frame'] < row['capture_start_frame']+row['frames'] <= row['end_frame']):
        return None
    return dict(row)


def receipt_gc_prefix(db, sid, source, previous, budget, remaining, *, qualified_primary=False):
    """Prove at most one complete page within the attempt's shared row budget.

    The memo is process/owner-epoch local and capped by the caller. Completed
    chunks cannot be pause/resume/retry/cancel targets (Store.set_state), and
    recovery changes only running chunks. Released pages cannot be unreleased;
    sealed-page publication appends only at capture_end_frame. Those supported
    API invariants make a proved prefix monotone. A restart starts at page 1;
    deletion/revocation/binding changes discard the memo. No history scan or
    empty-queue inference can manufacture a frontier.
    """
    bid=source['batch_id'];origin=source['origin'];stream='recording.'+bid
    initial={'page_number':0,'position':0,'frame':origin,'origin':origin,'anchor':None,
        'qualified_primary':bool(qualified_primary)}
    prior=initial if previous is None else previous
    if (prior['origin'] != origin or prior.get('qualified_primary',False)!=bool(qualified_primary)):
        raise Unavailable('receipt_gc_prefix_changed')
    if prior['anchor'] is not None:
        # Exact bounded anchor check detects loss/replacement of the memo's edge.
        row=db.execute('''SELECT w.chunk_id,w.position,w.capture_start_frame,w.frames,p.id,p.end_frame
            FROM recording_work w JOIN chunks c ON c.id=w.chunk_id
            JOIN recording_pages p ON p.id=w.page_id
            WHERE w.batch_id=? AND w.position=? AND c.session_id=? AND c.status='completed'
              AND c.next_offset=c.duration AND p.released=1 AND p.state='published' ''',
            (bid,prior['position']-1,sid)).fetchone()
        budget.projection(256)
        if row is None or tuple(row) != prior['anchor']:raise Unavailable('receipt_gc_prefix_changed')
    if remaining <= 0:return prior,0
    page=db.execute('''SELECT id,start_frame,end_frame,eos,released,state
        FROM recording_pages WHERE batch_id=? AND page_number=?''',
        (bid,prior['page_number']+1)).fetchone()
    budget.projection(192)
    if page is None or page['released'] != 1 or page['state'] != 'published':return prior,0
    a,b=page['start_frame'],page['end_frame']
    if (type(a) is not int or type(b) is not int or a != prior['frame'] or not a < b <= source['capture_end_frame']
            or b-a > MAX_PAGE_CHUNKS*CHUNK_FRAMES or (not page['eos'] and (b-origin)%CHUNK_FRAMES)):
        return prior,0
    expected=(b-a+CHUNK_FRAMES-1)//CHUNK_FRAMES
    if expected > remaining:return prior,0
    # page_id,position and batch_id,position are existing indexed orderings.
    # LEFT joins preserve missing authority rows, which must stop the prefix.
    rows=db.execute('''SELECT w.chunk_id,w.position,w.capture_start_frame,w.frames,
        c.status='completed' AND c.next_offset=c.duration AND c.duration=w.frames/16000.0
          AND c.timeline_start=(w.capture_start_frame-?)/16000.0 AND c.session_id=?
          AND w.batch_id=? AND (w.receipt_state='durable' OR (?=1 AND w.receipt_state='gap'))
          AND sc.session_id=c.session_id AND sc.stream_id=? AND sc.position=w.position
          AND ti.session_id=c.session_id AND ti.stream_id=sc.stream_id AND ti.position=sc.position
          AND ti.timeline_start=c.timeline_start AND ti.timeline_end=c.timeline_start+c.duration AS same
        FROM recording_work w LEFT JOIN chunks c ON c.id=w.chunk_id
        LEFT JOIN stream_chunks sc ON sc.chunk_id=w.chunk_id LEFT JOIN stream_time_index ti ON ti.chunk_id=w.chunk_id
        WHERE w.page_id=? ORDER BY w.position LIMIT ?''',
        (origin,sid,bid,int(qualified_primary),stream,page['id'],expected)).fetchall()
    budget.projection(len(rows)*256)
    # An exact end-position probe catches an extra row without projecting it.
    extra=db.execute('SELECT 1 FROM recording_work WHERE page_id=? AND position>=? LIMIT 1',
        (page['id'],prior['position']+expected)).fetchone();budget.projection(16)
    if len(rows) != expected or extra is not None:return prior,len(rows)
    frame=a
    for index,row in enumerate(rows):
        if (not row['same'] or row['position'] != prior['position']+index
                or row['capture_start_frame'] != frame or row['frames'] != min(CHUNK_FRAMES,b-frame)):
            return prior,len(rows)
        frame+=row['frames']
    if frame != b:return prior,len(rows)
    last=rows[-1]
    return {'page_number':prior['page_number']+1,'position':prior['position']+expected,
        'frame':b,'origin':origin,'qualified_primary':bool(qualified_primary),'anchor':(last['chunk_id'],last['position'],last['capture_start_frame'],
            last['frames'],page['id'],b)},len(rows)


def receipt_gc_pending_roots(db, bid, candidates, budget):
    """Pending historical aliases remain roots even before outbox staging."""
    page=db.execute('SELECT id FROM recording_pages WHERE batch_id=? AND released=0',(bid,)).fetchone()
    budget.projection(128)
    if page is None:return set(),()
    rows=db.execute('''SELECT source_start_frame,frames FROM recording_sources
        WHERE page_id=? ORDER BY seq LIMIT ?''',(page[0],MAX_SOURCES+1)).fetchall()
    budget.projection(len(rows)*32)
    if len(rows)>MAX_SOURCES:raise Unavailable('receipt_gc_roots_changed')
    intervals=[]
    for a,n in rows:
        if type(a) is not int or type(n) is not int or not 0<=a<a+n<=MAX_COORDINATE or n>MAX_SOURCE_FRAMES:
            raise Unavailable('receipt_gc_context_unknown')
        intervals.append((a,a+n))
    # The existing sealed-page manifest admits at most 912 mapping rows,
    # including repeated per-source mappings. Probe one extra row without
    # materializing payloads; excess metadata is not accepted as authority.
    count=db.execute('''SELECT COUNT(*) FROM (SELECT 1
        FROM recording_sources s INDEXED BY recording_page_sources
        CROSS JOIN recording_ranges r ON r.source_id=s.id
        WHERE s.page_id=? LIMIT 913)''',(page[0],)).fetchone()[0]
    budget.projection(16)
    if count>912:raise Unavailable('receipt_gc_roots_changed')
    marks=','.join('?' for _ in candidates)
    # Keep the indexed page-sources side outermost; ranges has source_id as
    # its primary-key prefix. Only the eight exact candidate IDs are projected.
    roots={text_row[0] for text_row in db.execute('''SELECT DISTINCT r.chunk_id
        FROM recording_sources s INDEXED BY recording_page_sources
        CROSS JOIN recording_ranges r ON r.source_id=s.id
        WHERE s.page_id=? AND r.chunk_id IN ('''+marks+')',(page[0],*candidates))}
    budget.projection(len(roots)*128)
    return roots,tuple(intervals)


def _strict_inspection_boundary(method):
    def guarded(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except OSError as exc:
            self._strict_io_failure(exc)
            raise
    guarded.__name__=method.__name__
    guarded.__doc__=method.__doc__
    return guarded

# No additional IO or retries; inherited read/context-close uncertainty stops strict lifetime.
for _name in ('begin','admit_bytes','accept','accept_page','seal_page','finish','_cleanup_page_work','_plan_page'):
    if hasattr(RecordingOverlap,_name):setattr(RecordingOverlap,_name,_strict_inspection_boundary(getattr(RecordingOverlap,_name)))
