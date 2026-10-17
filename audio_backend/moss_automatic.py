"""Bounded durable intents for timestamped recording overlap; no executor.

The existing Worker supplies one idle opportunity. The existing CellStore and
MossJobs own all PCM, native execution and cleanup. Historical admissions are
never inference retry instructions.
"""
from contextlib import contextmanager,nullcontext
from collections import deque
import hashlib
import json
import math
import sqlite3
import time

from .native_owned_cells import (Cell, Unavailable, canonical, sha, validate_document,
    SourceSpan, PROOF_BYTES, BATCH_CELLS, exact_frame)
from .native_owned_support_cache import ReadError

_WINDOW_READ_DEFERRED = object()

TABLE = 'moss_automatic_intents_v1'
USAGE = 'moss_automatic_usage_v1'
MAX_ROWS, SESSION_ROWS = 4096, 1024
MAX_BYTES, SESSION_BYTES = 64 * 1024**2, 16 * 1024**2
MAX_PENDING, SESSION_PENDING = 256, 64
MAX_PROOFS, SESSION_PROOFS = 16 * 1024**2, 4 * 1024**2
BINDING_BYTES, ACCOUNTING_BYTES = 4096, 16384
MAX_CHARGE = ACCOUNTING_BYTES + PROOF_BYTES + BINDING_BYTES
CORE_INTENTS = BATCH_CELLS + 1
ACTIVE = ('queued', 'published', 'accepted')
POLICY = sha({'schema':'automatic-recording-overlap-v1', 'coverage':'ordinary-and-core-seam',
    'selection':'existing-native-positive-overlap', 'execution':'one-attempt-no-unknown-replay',
    'output':'separate-primary-and-moss-unreconciled'})
COLUMNS = ('seq,candidate_key,sid,kind,binding,proof_sha256,cell_id,slot1,slot2,cid1,cid2,'
           'state,reason,created,updated,job_id,jobs_epoch,request_sha256,charge_bytes')
DEPENDENCIES = 'moss_automatic_dependencies_v2'
V2_COLUMNS = {
    'target_key': 'TEXT CHECK(target_key IS NULL OR length(target_key)=64)',
    'plan_phase': "TEXT CHECK(plan_phase IS NULL OR plan_phase IN ('waiting','bound','closed'))",
    'target_body': "BLOB CHECK(target_body IS NULL OR (typeof(target_body)='blob' AND length(target_body) BETWEEN 1 AND 65536))",
    'wait_deadline': 'REAL',
    'wait_start_ordinal': 'INTEGER CHECK(wait_start_ordinal IS NULL OR wait_start_ordinal>0)',
    'wait_last_ordinal': 'INTEGER CHECK(wait_last_ordinal IS NULL OR wait_last_ordinal>=wait_start_ordinal)',
    'plan_batch_id': 'TEXT',
    'plan_scope_key': 'TEXT CHECK(plan_scope_key IS NULL OR length(plan_scope_key)=64)',
    'plan_revision': 'INTEGER CHECK(plan_revision IS NULL OR plan_revision>=0)',
    'planning_bytes': 'INTEGER CHECK(planning_bytes IS NULL OR planning_bytes BETWEEN 1 AND 65536)',
}
COMPACT_V2_COLUMNS = {
    'recording_batch_id':'TEXT',
    'commit_ordinal':'INTEGER CHECK(commit_ordinal IS NULL OR commit_ordinal>0)',
    'retention_store_key':'TEXT CHECK(retention_store_key IS NULL OR length(retention_store_key)=64)',
}
V2_SCHEMA = {
DEPENDENCIES: f'''CREATE TABLE {DEPENDENCIES} (
    intent_seq INTEGER NOT NULL REFERENCES {TABLE}(seq) ON DELETE CASCADE,
    dependency_kind INTEGER NOT NULL CHECK(dependency_kind BETWEEN 1 AND 15),
    ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 131),
    cid TEXT NOT NULL CHECK(length(cid) BETWEEN 1 AND 128),
    slot TEXT CHECK(slot IS NULL OR length(slot)=64),
    PRIMARY KEY(intent_seq,ordinal))''',
'auto_v2_dependency_root': f"CREATE UNIQUE INDEX auto_v2_dependency_root ON {DEPENDENCIES}(intent_seq,cid,COALESCE(slot,''))",
'auto_v2_dependency_cid': f'CREATE INDEX auto_v2_dependency_cid ON {DEPENDENCIES}(cid,intent_seq)',
'auto_v2_dependency_slot': f'CREATE INDEX auto_v2_dependency_slot ON {DEPENDENCIES}(slot,intent_seq)',
'auto_v2_target': f'CREATE UNIQUE INDEX auto_v2_target ON {TABLE}(target_key)',
'auto_v2_wait_range': f'CREATE INDEX auto_v2_wait_range ON {TABLE}(plan_batch_id,plan_phase,wait_last_ordinal,wait_start_ordinal)',
'auto_v2_deadline': f'CREATE INDEX auto_v2_deadline ON {TABLE}(plan_phase,wait_deadline,seq)',
'native_core_v2_ordinal': 'CREATE UNIQUE INDEX native_core_v2_ordinal ON native_core_commits(recording_batch_id,commit_ordinal)',
'native_core_v2_cid': 'CREATE INDEX native_core_v2_cid ON native_core_commits(cid,retention_store_key,commit_ordinal)',
'auto_v2_cancel': f'''CREATE TRIGGER auto_v2_cancel BEFORE UPDATE OF status ON chunks WHEN NEW.status='cancelled' BEGIN
    UPDATE {USAGE} SET pending=pending-(SELECT COUNT(*) FROM {TABLE}
      WHERE sid=NEW.session_id AND target_key IS NOT NULL AND state IN ('queued','published','accepted'))
      WHERE scope IN ('',NEW.session_id);
    UPDATE {TABLE} SET state='cancelled',plan_phase='closed',reason='primary_cancelled',
      plan_revision=plan_revision+1 WHERE sid=NEW.session_id AND target_key IS NOT NULL
      AND state IN ('queued','published','accepted'); END''',
}
SCHEMA = {
TABLE: f'''CREATE TABLE {TABLE} (
    seq INTEGER PRIMARY KEY AUTOINCREMENT CHECK(seq>0),
    candidate_key TEXT NOT NULL UNIQUE CHECK(length(candidate_key)=64),
    sid TEXT NOT NULL REFERENCES sessions(id), kind TEXT NOT NULL CHECK(kind IN ('ordinary','core_seam','reset_seam')),
    binding BLOB NOT NULL CHECK(typeof(binding)='blob' AND length(binding) BETWEEN 1 AND 4096),
    proof BLOB CHECK(proof IS NULL OR (typeof(proof)='blob' AND length(proof) BETWEEN 1 AND 65536)),
    proof_sha256 TEXT NOT NULL CHECK(length(proof_sha256)=64), cell_id TEXT NOT NULL CHECK(length(cell_id)=64),
    slot1 TEXT NOT NULL CHECK(length(slot1)=64), slot2 TEXT CHECK(slot2 IS NULL OR length(slot2)=64),
    cid1 TEXT NOT NULL, cid2 TEXT,
    state TEXT NOT NULL CHECK(state IN ('queued','published','accepted','committed','no_result','interrupted','cancelled')),
    reason TEXT NOT NULL CHECK(length(reason)<=80), created REAL NOT NULL, updated REAL NOT NULL,
    job_id TEXT, jobs_epoch TEXT, request_sha256 TEXT,
    charge_bytes INTEGER NOT NULL CHECK(charge_bytes BETWEEN 16385 AND 86016),
    CHECK((state IN ('queued','published','accepted') AND proof IS NOT NULL) OR state NOT IN ('queued','published','accepted')),
    CHECK((job_id IS NULL AND jobs_epoch IS NULL AND request_sha256 IS NULL) OR
          (length(job_id)=32 AND length(jobs_epoch)=32 AND length(request_sha256)=64)))''',
USAGE: f'''CREATE TABLE {USAGE} (scope TEXT PRIMARY KEY, rows INTEGER NOT NULL,
    charge INTEGER NOT NULL, pending INTEGER NOT NULL, proofs INTEGER NOT NULL,
    CHECK(rows BETWEEN 0 AND 4096), CHECK(charge BETWEEN 0 AND 67108864),
    CHECK(pending BETWEEN 0 AND 256), CHECK(proofs BETWEEN 0 AND 16777216))''',
'auto_session_seq': f'CREATE INDEX auto_session_seq ON {TABLE}(sid,seq)',
'auto_state_seq': f'CREATE INDEX auto_state_seq ON {TABLE}(state,seq)',
'auto_cell': f'CREATE INDEX auto_cell ON {TABLE}(cell_id)',
'auto_job': f'CREATE UNIQUE INDEX auto_job ON {TABLE}(job_id)',
'auto_cid1': f'CREATE INDEX auto_cid1 ON {TABLE}(cid1,state)',
'auto_cid2': f'CREATE INDEX auto_cid2 ON {TABLE}(cid2,state)',
'auto_slot1': f'CREATE INDEX auto_slot1 ON {TABLE}(slot1,state)',
'auto_slot2': f'CREATE INDEX auto_slot2 ON {TABLE}(slot2,state)',
'auto_cancel': f'''CREATE TRIGGER auto_cancel AFTER UPDATE OF status ON chunks WHEN NEW.status='cancelled' BEGIN
    UPDATE {USAGE} SET
      charge=charge-COALESCE((SELECT SUM(length(proof)) FROM {TABLE} WHERE sid=NEW.session_id AND state IN ('queued','published','accepted')),0),
      pending=pending-(SELECT COUNT(*) FROM {TABLE} WHERE sid=NEW.session_id AND state IN ('queued','published','accepted')),
      proofs=proofs-COALESCE((SELECT SUM(length(proof)) FROM {TABLE} WHERE sid=NEW.session_id AND state IN ('queued','published','accepted')),0)
      WHERE scope IN ('',NEW.session_id);
    UPDATE {TABLE} SET state='cancelled',reason='primary_cancelled',charge_bytes=charge_bytes-length(proof),proof=NULL
      WHERE sid=NEW.session_id AND state IN ('queued','published','accepted'); END''',
}


def init_schema(db):
    present = [db.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone() for name in SCHEMA]
    if any(present) and not all(present):raise ValueError('Incomplete automatic MOSS schema')
    for (name, sql), row in zip(SCHEMA.items(), present):
        if row is None:db.execute(sql)
        elif not _base_schema_matches(name,row[0],sql):raise ValueError('Incompatible automatic MOSS schema')
    if not any(present):db.execute(f'INSERT INTO {USAGE} VALUES(?,0,0,0,0)',('',))


def _compact_sql(sql):return ''.join(sql.split())


def _base_schema_matches(name, actual, expected):
    if name == TABLE:
        compact = _compact_sql(actual)
        for column,definition in V2_COLUMNS.items():
            compact = compact.replace(','+_compact_sql(column+' '+definition),'')
        return compact == _compact_sql(expected)
    return ' '.join(actual.split()) == ' '.join(expected.split())


def has_bounded_schema(db):
    return db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(DEPENDENCIES,)).fetchone() is not None


def init_bounded_schema(db, *, enable=False):
    """One additive transaction. No legacy proof, key, row or store is adopted."""
    if not db.in_transaction:raise ValueError('Bounded automatic migration requires transaction')
    from .recording_overlap import BOUNDED_COLUMNS, init_bounded_columns
    fields={table:{r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
            for table in (TABLE,'recording_batches','native_core_commits')}
    shapes=[column in fields[table] for table,columns in ((TABLE,V2_COLUMNS),
        ('recording_batches',BOUNDED_COLUMNS),('native_core_commits',COMPACT_V2_COLUMNS)) for column in columns]
    existing=[db.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone() for name in V2_SCHEMA]
    present=shapes+[row is not None for row in existing]
    if any(present) and not all(present):raise ValueError('Incomplete bounded automatic schema')
    if not any(present):
        if not enable:return
        if any(not fields[table] for table in fields):raise ValueError('Missing bounded automatic prerequisite schema')
        for column,definition in V2_COLUMNS.items():db.execute(f'ALTER TABLE {TABLE} ADD COLUMN {column} {definition}')
        init_bounded_columns(db)
        for column,definition in COMPACT_V2_COLUMNS.items():db.execute(f'ALTER TABLE native_core_commits ADD COLUMN {column} {definition}')
        for sql in V2_SCHEMA.values():db.execute(sql)
        return
    for table,columns in ((TABLE,V2_COLUMNS),('recording_batches',BOUNDED_COLUMNS),('native_core_commits',COMPACT_V2_COLUMNS)):
        sql=_compact_sql(db.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0])
        for column,definition in columns.items():
            if _compact_sql(column+' '+definition) not in sql:raise ValueError('Incompatible bounded automatic column')
    for (name,sql),row in zip(V2_SCHEMA.items(),existing):
        if _compact_sql(sql)!=_compact_sql(row[0]):raise ValueError('Incompatible bounded automatic schema: '+name)


def bounded_enabled(settings):return bool(getattr(settings,'native_owned_bounded_core_support_enabled',False))


def extended_row(db,row,*,proof=False):
    if row is None:return None
    result=dict(row)
    if all(name in result for name in V2_COLUMNS):return result
    if has_bounded_schema(db):
        names=','.join(V2_COLUMNS)
        extra=db.execute(f'SELECT {names} FROM {TABLE} WHERE seq=?',(row['seq'],)).fetchone()
        result.update(dict(extra))
    else:result.update({name:None for name in V2_COLUMNS})
    return result


def _version(db,row):
    if 'target_key' in row:return row['target_key']
    if not has_bounded_schema(db):return None
    saved=db.execute(f'SELECT target_key FROM {TABLE} WHERE seq=?',(row['seq'],)).fetchone()
    if saved is None:raise Unavailable('automatic_intent_missing')
    return saved[0]


def enabled(settings):return bool(getattr(settings,'moss_automatic_enabled',False))


def recording(db, cid):
    return db.execute('SELECT 1 FROM recording_work WHERE chunk_id=? LIMIT 1',(cid,)).fetchone() is not None


def usage(db, sid):
    result=[]
    for scope in ('',sid):
        row=db.execute(f'SELECT rows,charge,pending,proofs FROM {USAGE} WHERE scope=?',(scope,)).fetchone()
        if row is None:
            if scope=='':raise ReadError(503,'automatic_usage_invalid')
            row=(0,0,0,0)
        values=tuple(row)
        # The upgraded ledger retains terminal evidence. Its existing aggregate
        # proof counter is bounded by retained rows, not pending work. Exact
        # per-row charges are checked on every v2 read/mutation; no proof scan
        # or additional persistent counter is hidden in this metadata helper.
        versioned=(has_bounded_schema(db) and db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY auto_v2_target WHERE target_key IS NOT NULL LIMIT 1').fetchone() is not None)
        if (any(type(v) is not int or v<0 for v in values) or values[0]>MAX_ROWS
                or values[1]>MAX_BYTES or values[2]>min(MAX_PENDING,values[0])
                or values[3]>min(MAX_PROOFS,(values[0] if versioned else values[2])*PROOF_BYTES)
                or values[1]<values[0]*ACCOUNTING_BYTES+(values[3] if versioned else 0)):
            raise ReadError(503,'automatic_usage_invalid')
        result.append(values)
    if any(a>b for a,b in zip(result[1],result[0])):raise ReadError(503,'automatic_usage_invalid')
    return result


def capacity(db, sid, *, rows=CORE_INTENTS, charge=CORE_INTENTS*MAX_CHARGE,
             pending=CORE_INTENTS, proofs=CORE_INTENTS*PROOF_BYTES):
    current=usage(db,sid)
    for have,limits in zip(current,((MAX_ROWS,MAX_BYTES,MAX_PENDING,MAX_PROOFS),
                                  (SESSION_ROWS,SESSION_BYTES,SESSION_PENDING,SESSION_PROOFS))):
        for a,b,c,name in zip(have,(rows,charge,pending,proofs),limits,
                             ('rows','bytes','pending','proof_bytes')):
            if a+b>c:raise ReadError(429,'automatic_'+name+'_capacity')
    return current


def _charge(db,sid,delta):
    usage(db,sid)
    db.execute(f'INSERT OR IGNORE INTO {USAGE} VALUES(?,0,0,0,0)',(sid,))
    for scope in ('',sid):
        db.execute(f'UPDATE {USAGE} SET rows=rows+?,charge=charge+?,pending=pending+?,proofs=proofs+? WHERE scope=?',(*delta,scope))


def _terminal(db,row,state,reason,*,budget=None):
    if row['state'] not in ACTIVE:return
    if _version(db,row) is not None and 'proof' not in row:
        row=_read_v2(db,key=row['candidate_key'],budget=budget)
    else:row=extended_row(db,row)
    if row['target_key'] is not None:
        if 'proof' not in row:row=_read_v2(db,key=row['candidate_key'],budget=budget)
        else:validate_v2_row(db,row,row['proof'],budget)
        # Terminal v2 evidence remains retained, charged and rooted. A terminal
        # state is not permission to forget original target ownership.
        _charge(db,row['sid'],(0,0,-1,0))
        db.execute(f'''UPDATE {TABLE} SET state=?,plan_phase='closed',reason=?,updated=?,
            plan_revision=plan_revision+1 WHERE candidate_key=? AND state=?''',
            (state,reason,time.time(),row['candidate_key'],row['state']))
        return
    if any(row[name] is not None for name in V2_COLUMNS):raise ReadError(503,'automatic_mixed_version')
    n=db.execute(f'SELECT length(proof) FROM {TABLE} WHERE candidate_key=?',(row['candidate_key'],)).fetchone()[0]
    if type(n) is not int or row['charge_bytes']!=ACCOUNTING_BYTES+len(row['binding'])+n:
        raise ReadError(503,'automatic_usage_invalid')
    charge=ACCOUNTING_BYTES+len(row['binding'])
    _charge(db,row['sid'],(0,charge-row['charge_bytes'],-1,-n))
    db.execute(f'UPDATE {TABLE} SET state=?,reason=?,proof=NULL,charge_bytes=?,updated=? WHERE candidate_key=? AND state=?',
        (state,reason,charge,time.time(),row['candidate_key'],row['state']))


def cancel_session(db,sid,reason='primary_cancelled'):
    # Bounded by the explicit per-session retained row cap; no proof projection.
    for row in db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE sid=? AND state IN (\'queued\',\'published\',\'accepted\')',(sid,)).fetchall():
        _terminal(db,row,'cancelled',reason)


def erase_session(db,sid):
    total,own=usage(db,sid)
    db.execute(f'DELETE FROM {TABLE} WHERE sid=?',(sid,))
    db.execute(f'UPDATE {USAGE} SET rows=?,charge=?,pending=?,proofs=? WHERE scope=?',
               (*(a-b for a,b in zip(total,own)),''))
    db.execute(f'DELETE FROM {USAGE} WHERE scope=?',(sid,))


# Legacy indexed lookup contract. GC must use retention_root below.
def root(db, *, cid=None, slot=None):
    name,value=('cid',cid) if cid is not None else ('slot',slot)
    if value is None:return False
    for suffix in ('1','2'):
        field=name+suffix
        if db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY auto_{field} WHERE {field}=? AND state IN (\'queued\',\'published\',\'accepted\') LIMIT 1',(value,)).fetchone():return True
    return False



def retention_root(db, *, cid=None, slot=None):
    """Fresh version-aware root lookup for every production retention fence."""
    name,value=('cid',cid) if cid is not None else ('slot',slot)
    if value is None:return False
    if has_bounded_schema(db):
        if db.execute(f'''SELECT 1 FROM {DEPENDENCIES} INDEXED BY auto_v2_dependency_{name}
                WHERE {name}=? LIMIT 1''',(value,)).fetchone():return True
        # Protect eligible successor commitments, including unserviced waiters.
        # Null/unknown/mismatched qualification is conservatively fenced by GC.
        where='c.cid=?' if cid is not None else 'c.slot=?'
        if db.execute(f'''SELECT 1 FROM native_core_commits c
            WHERE {where} AND c.recording_batch_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM {TABLE} a INDEXED BY auto_v2_wait_range
              WHERE a.plan_batch_id=c.recording_batch_id AND a.plan_phase='waiting'
              AND a.wait_last_ordinal>=c.commit_ordinal AND a.wait_start_ordinal<c.commit_ordinal
              AND a.wait_deadline>? AND a.plan_scope_key IS NOT NULL) LIMIT 1''',
              (value,time.time())).fetchone():return True
    return root(db,cid=cid,slot=slot)


def retention_eligible(db,store_key,*,cid=None,slot=None):
    """Cross-store fence: unknown roots never become orphanhood evidence."""
    if not has_bounded_schema(db):return False
    if type(store_key) is not str or len(store_key)!=64:return False
    # This finite first stage has no global certificate census. Therefore an
    # absent indexed root cannot establish orphanhood while any v2 target is
    # retained: a damaged/missing dependency entry must protect, never collect.
    # Quotas remain unchanged and expose this conservative retention pressure.
    if db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY auto_v2_target WHERE target_key IS NOT NULL LIMIT 1').fetchone():return False
    if slot is not None:
        row=db.execute('''SELECT recording_batch_id,commit_ordinal,retention_store_key,cid
            FROM native_core_commits WHERE slot=?''',(slot,)).fetchone()
        if row is None or row[0] is None or row[1] is None or row[2]!=store_key:return False
        cid=row[3]
    row=db.execute('''SELECT b.retention_store_key,b.native_core_ordinal,b.session_id,c.session_id
        FROM recording_work w JOIN recording_batches b ON b.id=w.batch_id
        JOIN chunks c ON c.id=w.chunk_id WHERE w.chunk_id=?''',(cid,)).fetchone()
    if row is None or row[0]!=store_key or type(row[1]) is not int or row[2]!=row[3]:return False
    if db.execute('''SELECT 1 FROM native_core_commits INDEXED BY native_core_v2_cid
        WHERE cid=? AND (retention_store_key IS NULL OR retention_store_key!=?
          OR commit_ordinal IS NULL OR recording_batch_id IS NULL) LIMIT 1''',(cid,store_key)).fetchone():return False
    return not retention_root(db,cid=cid) and (slot is None or not retention_root(db,slot=slot))


def batch_context(db,chunk,store_key,budget):
    """Read prospective ordinal only. The actual increment is primary-atomic."""
    from .native_owned_cells import digest
    digest(store_key)
    if not has_bounded_schema(db):raise Unavailable('bounded_schema_unavailable')
    row=db.execute('''SELECT b.id,b.session_id,b.native_core_ordinal,b.retention_store_key
        FROM recording_work w JOIN recording_batches b ON b.id=w.batch_id
        WHERE w.chunk_id=?''',(chunk['id'],)).fetchone()
    budget.projection(320)
    if row is None or row['session_id']!=chunk['session_id']:raise Unavailable('bounded_recording_required')
    ordinal=row['native_core_ordinal']
    if ordinal is None:
        if row['retention_store_key'] is not None:raise Unavailable('bounded_batch_counter_invalid')
        if db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY auto_session_seq WHERE sid=? LIMIT 1',(chunk['session_id'],)).fetchone():
            raise Unavailable('bounded_fresh_session_required')
        if db.execute('SELECT 1 FROM native_core_commits WHERE sid=? LIMIT 1',(chunk['session_id'],)).fetchone():
            raise Unavailable('bounded_fresh_session_required')
        primary=db.execute('''SELECT id,next_offset FROM chunks INDEXED BY chunk_timeline
            WHERE session_id=? ORDER BY timeline_start LIMIT 129''',(chunk['session_id'],)).fetchall()
        budget.projection(len(primary)*256)
        if len(primary)>128:raise Unavailable('bounded_fresh_session_required')
        for source in primary:
            if (source['next_offset']!=0 or db.execute('SELECT 1 FROM segments INDEXED BY segments_chunk WHERE chunk_id=? LIMIT 1',
                    (source['id'],)).fetchone()):raise Unavailable('bounded_fresh_session_required')
        ordinal=0
    elif type(ordinal) is not int or not 0<=ordinal<(1<<63)-2 or row['retention_store_key']!=store_key:
        raise Unavailable('bounded_batch_counter_invalid')
    return {'batch_id':row['id'],'ordinal':ordinal+1,'retention_store_key':store_key}


def commit_ordinal(db,packet,chunk):
    """Only a genuinely new compact advances the durable batch frontier."""
    context=packet.get('_bounded_context')
    if context is None:return
    from .native_owned_core_seams import slot
    if type(context) is not dict or set(context)!={'batch_id','ordinal','retention_store_key'}:
        raise Unavailable('bounded_batch_context')
    budget=packet.get('_bounded_budget')
    if budget is None:raise Unavailable('bounded_planning_unavailable')
    if not budget.reserve_planning():raise Unavailable('bounded_planning_capacity')
    with budget.finalization_phase():
        db.set_progress_handler(budget.progress,100)
        try:return _commit_ordinal(db,packet,chunk,context,budget)
        finally:db.set_progress_handler(budget.progress if getattr(budget,'primary_sql',False) else None,100)


def _commit_ordinal(db,packet,chunk,context,budget):
    from .native_owned_core_seams import slot
    ordinal=context['ordinal']; key=context['retention_store_key']
    if type(ordinal) is not int or not 1<=ordinal<(1<<63)-1:raise Unavailable('bounded_batch_counter_invalid')
    changed=db.execute('''UPDATE recording_batches SET native_core_ordinal=?,retention_store_key=?
        WHERE id=? AND session_id=? AND ((native_core_ordinal=? AND retention_store_key=?)
        OR (native_core_ordinal IS NULL AND retention_store_key IS NULL AND ?=1))''',
        (ordinal,key,context['batch_id'],chunk['session_id'],ordinal-1,key,ordinal)).rowcount
    if changed!=1:raise Unavailable('bounded_batch_counter_changed')
    if db.execute('''UPDATE native_core_commits SET recording_batch_id=?,commit_ordinal=?,retention_store_key=?
        WHERE slot=? AND recording_batch_id IS NULL AND commit_ordinal IS NULL AND retention_store_key IS NULL''',
        (context['batch_id'],ordinal,key,slot(packet['commit']))).rowcount!=1:
        raise Unavailable('bounded_compact_ordinal_changed')
    budget.projection(384)


def read_waiters(db,batch_id,scope_key,budget,limit=4):
    if type(limit) is not int or not 1<=limit<=4:raise Unavailable('bounded_waiter_capacity')
    if not has_bounded_schema(db):return []
    rows=db.execute(f'''SELECT seq,length(binding) AS bn,length(proof) AS pn,length(target_body) AS tn
        FROM {TABLE} INDEXED BY auto_v2_deadline WHERE plan_phase='waiting' AND plan_batch_id=?
        ORDER BY wait_deadline,seq LIMIT ?''',(batch_id,limit)).fetchall()
    result=[]
    for row in rows:
        if (type(row['pn']) is not int or type(row['tn']) is not int or not 1<=row['bn']<=BINDING_BYTES
                or not 2<=row['pn']+row['tn']<=PROOF_BYTES):raise Unavailable('bounded_waiter_payload')
        budget.projection(row['bn']+row['pn']+row['tn']+1024)
        value=dict(db.execute(f'SELECT {COLUMNS},proof,'+','.join(V2_COLUMNS)+f' FROM {TABLE} WHERE seq=?',(row['seq'],)).fetchone())
        validate_v2_row(db,value,value['proof'],budget)
        value['target']=json.loads(value['target_body'])
        value['scope_current']=value['plan_scope_key']==scope_key
        result.append(value)
    return result


def release_wait_pressure(db,sid,budget):
    """Close only the bounded oldest waits needed for the next reservation."""
    if not has_bounded_schema(db):return 0
    total,own=usage(db,sid)
    need_global=max(0,total[2]+CORE_INTENTS-MAX_PENDING)
    need_session=max(0,own[2]+CORE_INTENTS-SESSION_PENDING)
    # One certificate is guaranteed to fit even at the legal 64 KiB ceiling.
    # Repeated turns make durable progress instead of rolling back a full page.
    needed=min(1,max(need_global,need_session))
    if not needed:return 0
    extra=' AND sid=?' if need_session else ''
    args=(sid,needed) if need_session else (needed,)
    rows=db.execute(f'''SELECT {COLUMNS} FROM {TABLE} INDEXED BY auto_v2_deadline
        WHERE plan_phase='waiting' '''+extra+' ORDER BY wait_deadline,seq LIMIT ?',args).fetchall()
    budget.projection(len(rows)*1024)
    for row in rows:
        budget.check();_terminal(db,row,'no_result','unresolved_wait_capacity',budget=budget)
    return len(rows)


def _original_dependencies(target):
    from .native_owned_core_seams import validate_target,slot
    _,commits=validate_target(target)
    return sorted(({'kind':1,'cid':c['part']['chunk_id'],'slot':slot(c)} for c in commits),
                  key=lambda item:(item['cid'],item['slot']))


def _write_dependencies(db,seq,roots):
    if len(roots)>132 or len({r['cid'] for r in roots})>128:raise Unavailable('bounded_dependency_capacity')
    canonical(roots,BINDING_BYTES)
    db.execute(f'DELETE FROM {DEPENDENCIES} WHERE intent_seq=?',(seq,))
    for order,root_item in enumerate(roots):
        db.execute(f'INSERT INTO {DEPENDENCIES} VALUES(?,?,?,?,?)',
            (seq,root_item['kind'],order,root_item['cid'],root_item['slot']))


def _check_dependencies(db,seq,expected,budget=None):
    values=db.execute(f'''SELECT dependency_kind,ordinal,cid,slot FROM {DEPENDENCIES}
        WHERE intent_seq=? ORDER BY ordinal LIMIT 133''',(seq,)).fetchall()
    if budget is not None:budget.projection(sum(160+len(r['cid']) for r in values))
    actual=[{'kind':r['dependency_kind'],'cid':r['cid'],'slot':r['slot']} for r in values]
    if [r['ordinal'] for r in values]!=list(range(len(expected))) or actual!=expected:
        raise Unavailable('bounded_dependency_index_mismatch')


def source_binding_v2(kind,doc,support,parts,context):
    binding=json.loads(source_binding(kind,doc,support,parts,doc['commits']))
    binding.update(schema='automatic-recording-overlap-v2',target_key=doc['target']['key'],
        target_sha256=doc['target_sha256'],dependency_roots=doc['dependency_roots'],
        plan_ordinal=context['ordinal'],plan_batch_id=context['batch_id'],
        retention_store_key=context['retention_store_key'])
    return canonical(binding,BINDING_BYTES)


def validate_v2_row(db,row,proof,budget=None):
    """Strict version dispatch. Legacy candidate hashing is never relaxed."""
    from .native_owned_core_seams import validate_target,slot,BOUNDED_SCHEMA
    from .native_owned_cells import digest,text
    row=extended_row(db,row)
    if row['target_key'] is None:
        if any(row[name] is not None for name in V2_COLUMNS):raise Unavailable('automatic_mixed_version')
        return None
    if any(row[name] is None for name in V2_COLUMNS):raise Unavailable('automatic_mixed_version')
    raw=row['target_body']
    if type(raw) is not bytes or type(proof) is not bytes or not 2<=len(raw)+len(proof)<=PROOF_BYTES:
        raise Unavailable('bounded_certificate_capacity')
    target=json.loads(raw); identity,originals=validate_target(target)
    if canonical(target)!=raw or target['key']!=row['target_key'] or identity['scope']['session_id']!=row['sid']:
        raise Unavailable('bounded_target_binding')
    first=target['origins'][0]; original_binding=first['binding'].encode('ascii')
    first_commits=first['commits']; first_slots=[slot(c) for c in first_commits]
    digest(row['target_key']);digest(row['plan_scope_key']);text(row['plan_batch_id'])
    start,last,revision=(row[name] for name in ('wait_start_ordinal','wait_last_ordinal','plan_revision'))
    if (any(type(n) is not int or not 0<=n<(1<<63)-1 for n in (start,last,revision)) or start<1
            or not start<=last<=start+min(3,4-len(originals))
            or row['plan_scope_key']!=sha(first_commits[0]['scope'])
            or row['kind']!=json.loads(original_binding)['kind']
            or any(type(row[name]) not in (int,float) or not math.isfinite(row[name])
                   for name in ('created','updated','wait_deadline'))
            or row['wait_deadline']!=row['created']+300.
            or row['plan_phase'] not in ('waiting','bound','closed')
            or revision<(0 if row['plan_phase']=='waiting' else 1)
            or (row['plan_phase']=='closed')!=(row['state'] not in ACTIVE)
            or row['plan_phase']=='waiting' and row['state']!='queued'):
        raise Unavailable('bounded_wait_metadata')
    if budget is not None:budget.projection(320)
    anchor=db.execute('''SELECT recording_batch_id,commit_ordinal,retention_store_key
        FROM native_core_commits WHERE slot=?''',(first_slots[-1],)).fetchone()
    if (anchor is None or anchor['recording_batch_id']!=row['plan_batch_id']
            or anchor['commit_ordinal']!=start):raise Unavailable('bounded_wait_metadata')
    digest(anchor['retention_store_key'])
    if (hashlib.sha256(original_binding).hexdigest()!=row['candidate_key']
            or row['slot1']!=first_slots[0] or row['slot2']!=(first_slots[1] if len(first_slots)==2 else None)
            or row['cid1']!=first_commits[0]['part']['chunk_id']
            or row['cid2']!=(first_commits[1]['part']['chunk_id'] if len(first_commits)==2 else None)
            or row['planning_bytes']!=len(raw)
            or row['charge_bytes']!=ACCOUNTING_BYTES+len(raw)+len(proof)+len(row['binding'])
            or row['proof_sha256']!=hashlib.sha256(proof).hexdigest()):
        raise Unavailable('bounded_original_owner_binding')
    doc,support,parts=validate_document(proof)
    if row['cell_id']!=sha({'policy':doc['policy'],'support':support.to_dict()}):
        raise Unavailable('bounded_cell_binding')
    if doc.get('schema')==BOUNDED_SCHEMA:
        if row['plan_phase'] not in ('bound','closed') or doc['target']!=target:raise Unavailable('bounded_phase_binding')
        binding=json.loads(row['binding'])
        context={'ordinal':binding.get('plan_ordinal'),'batch_id':binding.get('plan_batch_id'),
                 'retention_store_key':binding.get('retention_store_key')}
        digest(context['retention_store_key'])
        ordinal=context['ordinal']
        if (type(ordinal) is not int or not row['wait_start_ordinal']<=ordinal<=row['wait_last_ordinal']
                or context['batch_id']!=row['plan_batch_id']
                or row['binding']!=source_binding_v2(row['kind'],doc,support,parts,context)):
            raise Unavailable('bounded_final_binding')
        expected=doc['dependency_roots']
    else:
        if row['plan_phase'] not in ('waiting','closed') or row['binding']!=original_binding or proof!=first['proof'].encode('ascii'):
            raise Unavailable('bounded_waiting_binding')
        if row['job_id'] is not None:raise Unavailable('bounded_waiter_admitted')
        expected=_original_dependencies(target)
    _check_dependencies(db,row['seq'],expected,budget)
    return target,doc,support,parts


def _read_v2(db,*,key=None,target_key=None,budget=None,with_validation=False,expected=None):
    # Publication already holds the validated canonical certificate. Compare
    # those exact bytes in this fresh snapshot rather than project them again.
    # No result/row survives the callback; other readers keep the full read.
    if expected is not None:
        if (type(expected) is not tuple or len(expected)!=2
                or any(type(raw) is not bytes for raw in expected)
                or not 2<=sum(map(len,expected))<=PROOF_BYTES):
            raise Unavailable('bounded_certificate_capacity')
    field,value=('candidate_key',key) if key is not None else ('target_key',target_key)
    sizes=db.execute(f'SELECT length(binding),length(proof),length(target_body) FROM {TABLE} WHERE {field}=?',(value,)).fetchone()
    if sizes is None:return None
    if (any(type(n) is not int for n in sizes) or not 1<=sizes[0]<=BINDING_BYTES
            or not 2<=sizes[1]+sizes[2]<=PROOF_BYTES):raise Unavailable('bounded_certificate_capacity')
    if expected is None:
        if budget is not None:budget.projection(sum(sizes)+1024)
        row=dict(db.execute(f'SELECT {COLUMNS},proof,'+','.join(V2_COLUMNS)+f' FROM {TABLE} WHERE {field}=?',(value,)).fetchone())
    else:
        proof,target=expected
        if (sizes[1],sizes[2])!=(len(proof),len(target)):return None
        if budget is not None:budget.projection(sizes[0]+1024+32)
        names=','.join(name for name in V2_COLUMNS if name!='target_body')
        found=db.execute(f'''SELECT {COLUMNS},{names},proof=? AS proof_same,
            target_body=? AS target_same FROM {TABLE} WHERE {field}=?
            AND typeof(proof)='blob' AND typeof(target_body)='blob' ''',
            (proof,target,value)).fetchone()
        if found is None or found['proof_same']!=1 or found['target_same']!=1:return None
        row=dict(found);del row['proof_same'];del row['target_same']
        row.update(proof=proof,target_body=target)
    validated=validate_v2_row(db,row,row['proof'],budget)
    return (row,validated) if with_validation else row


def _register_target(db,proposal,context,scope,now,budget):
    from .native_owned_core_seams import validate_target,append_target_origin
    target=proposal['target']; _,roots=validate_target(target)
    existing=_read_v2(db,target_key=target['key'],budget=budget)
    if existing is not None:
        if existing['plan_phase']!='waiting':return existing,'already_owned'
        saved=json.loads(existing['target_body'])
        for origin in target['origins']:saved=append_target_origin(saved,origin)
        raw=canonical(saved)
        if len(raw)+len(existing['proof'])>PROOF_BYTES:raise Unavailable('bounded_certificate_capacity')
        _,roots=validate_target(saved)
        last=min(existing['wait_last_ordinal'],existing['wait_start_ordinal']+min(3,4-len(roots)))
        delta=len(raw)-len(existing['target_body'])
        if delta:
            capacity(db,existing['sid'],rows=0,pending=0,proofs=delta,charge=delta)
            _charge(db,existing['sid'],(0,delta,0,delta))
            db.execute(f'''UPDATE {TABLE} SET target_body=?,planning_bytes=?,charge_bytes=charge_bytes+?,
                wait_last_ordinal=?,plan_revision=plan_revision+1,updated=? WHERE seq=? AND plan_phase='waiting' ''',
                (raw,len(raw),delta,last,now,existing['seq']))
            _write_dependencies(db,existing['seq'],_original_dependencies(saved))
        return _read_v2(db,key=existing['candidate_key'],budget=budget),'alias_registered' if delta else 'already_owned'
    raw=canonical(target); proof=proposal['proof']; binding=proposal['binding']
    if len(raw)+len(proof)>PROOF_BYTES:raise Unavailable('bounded_certificate_capacity')
    charge=ACCOUNTING_BYTES+len(raw)+len(proof)+len(binding)
    if charge>MAX_CHARGE:raise Unavailable('bounded_certificate_capacity')
    capacity(db,proposal['sid'],rows=1,pending=1,proofs=len(raw)+len(proof),charge=charge)
    ordinal=context['ordinal']; last=ordinal+min(3,4-len(roots))
    cur=db.execute(f'''INSERT INTO {TABLE}(candidate_key,sid,kind,binding,proof,proof_sha256,cell_id,
        slot1,slot2,cid1,cid2,state,reason,created,updated,charge_bytes,target_key,plan_phase,target_body,
        wait_deadline,wait_start_ordinal,wait_last_ordinal,plan_batch_id,plan_scope_key,plan_revision,planning_bytes)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued','waiting_for_support',?,?,?,?, 'waiting',?,?,?,?,?,?,0,?)''',
        (proposal['key'],proposal['sid'],proposal['kind'],binding,proof,proposal['proof_sha256'],proposal['cell_id'],
         proposal['slot1'],proposal['slot2'],proposal['cid1'],proposal['cid2'],now,now,charge,target['key'],raw,
         now+300.,ordinal,last,context['batch_id'],scope,len(raw)))
    _charge(db,proposal['sid'],(1,charge,1,len(raw)+len(proof)))
    _write_dependencies(db,cur.lastrowid,_original_dependencies(target))
    return _read_v2(db,key=proposal['key'],budget=budget),'registered'


def _exact_compacts(db,commits,context,budget):
    from .native_owned_core_seams import slot,canonical as compact_canonical,COMPACT_BYTES
    for commit in commits:
        raw=compact_canonical(commit,COMPACT_BYTES)
        row=db.execute('''SELECT body=?,state,recording_batch_id,commit_ordinal,retention_store_key
            FROM native_core_commits WHERE slot=?''',(raw,slot(commit))).fetchone()
        if budget is not None:budget.projection(320)
        if (row is None or not row[0] or row['state']=='cancelled' or row['recording_batch_id']!=context['batch_id']
                or row['retention_store_key']!=context['retention_store_key']
                or type(row['commit_ordinal']) is not int or not 1<=row['commit_ordinal']<=context['ordinal']):
            raise Unavailable('bounded_compact_changed')


def qualify_bounded_document(db,doc,budget,store_key,*,record=None,automatic_key=None):
    """Retained cell reads authenticate all roots without dispatch recursion."""
    from .native_owned_core_seams import validate_target
    if automatic_key is not None:
        if record is not None:raise Unavailable('bounded_authority_record_scope')
        raw=canonical(doc)
        row=_read_v2(db,key=automatic_key,budget=budget,expected=(raw,canonical(doc['target'])))
        if (row is None or row['state'] not in ACTIVE or row['plan_phase']!='bound'
                or row['proof_sha256']!=hashlib.sha256(raw).hexdigest()):return False
    else:
        row=record if record is not None else _read_v2(db,target_key=doc['target']['key'],budget=budget)
    if row is None or row['plan_phase'] not in ('bound','closed') or row['state']=='cancelled':return False
    if row['proof']!=canonical(doc):return False
    binding=json.loads(row['binding'])
    context={'ordinal':binding['plan_ordinal'],'batch_id':binding['plan_batch_id'],
             'retention_store_key':binding['retention_store_key']}
    if store_key is not None and store_key!=context['retention_store_key']:return False
    batch=db.execute('SELECT native_core_ordinal,retention_store_key FROM recording_batches WHERE id=?',
        (context['batch_id'],)).fetchone()
    if budget is not None:budget.projection(128)
    if (batch is None or type(batch[0]) is not int or batch[0]<context['ordinal']
            or batch[1]!=context['retention_store_key']):return False
    _,originals=validate_target(doc['target'])
    _exact_compacts(db,[*originals,*doc['commits']],context,budget)
    return True


def _waiting_reason(row,context,scope,now,clock_owner=None):
    if row['plan_scope_key']!=scope:return 'unresolved_scope_transition'
    if now>=row['wait_deadline']:return 'unresolved_wait_deadline'
    if clock_owner is not None and clock_owner.bounded_expired(row['target_key'],row['wait_deadline']):
        return 'unresolved_wait_deadline'
    if context['ordinal']>row['wait_last_ordinal']:return 'unresolved_commit_horizon'
    if row['plan_batch_id']!=context['batch_id']:return 'unresolved_scope_transition'
    return None


def _bind_target(db,row,proposal,context,actual_snapshot,budget,now):
    from .native_owned_core_seams import make_bounded_cell,validate_target
    if row['plan_phase']!='waiting' or row['state']!='queued' or row['job_id'] is not None:
        raise Unavailable('bounded_target_already_closed')
    target=json.loads(row['target_body'])
    if target!=proposal['target']:raise Unavailable('bounded_alias_set_changed')
    _,originals=validate_target(target)
    _exact_compacts(db,originals,context,budget)
    _exact_compacts(db,proposal['commits'],context,budget)
    cell=make_bounded_cell(proposal['commits'],proposal['query'],proposal['evidence'],
        proposal['pcm'],target,actual_snapshot)
    # This freshly constructed frozen Cell has already validated these exact
    # immutable bytes, support, mapping, PCM and anchor in __post_init__.
    # Reparse the canonical bytes; never accept a caller-supplied decoded view.
    doc=json.loads(cell.proof);support,parts=cell.support,cell.parts
    if doc['geometry']!=proposal['support']:raise Unavailable('bounded_frozen_support_changed')
    binding=source_binding_v2(row['kind'],doc,support,parts,context)
    if len(cell.proof)+len(row['target_body'])>PROOF_BYTES:raise Unavailable('bounded_certificate_capacity')
    new_charge=ACCOUNTING_BYTES+len(binding)+len(cell.proof)+len(row['target_body'])
    if new_charge>MAX_CHARGE:raise Unavailable('bounded_certificate_capacity')
    delta_proofs=len(cell.proof)-len(row['proof']); delta_charge=new_charge-row['charge_bytes']
    capacity(db,row['sid'],rows=0,pending=0,proofs=max(0,delta_proofs),charge=max(0,delta_charge))
    _charge(db,row['sid'],(0,delta_charge,0,delta_proofs))
    changed=db.execute(f'''UPDATE {TABLE} SET binding=?,proof=?,proof_sha256=?,cell_id=?,charge_bytes=?,
        plan_phase='bound',plan_revision=plan_revision+1,reason='awaiting_publication',updated=?
        WHERE seq=? AND plan_phase='waiting' AND state='queued' AND job_id IS NULL AND plan_revision=?''',
        (binding,cell.proof,hashlib.sha256(cell.proof).hexdigest(),cell.cell_id,new_charge,now,row['seq'],row['plan_revision'])).rowcount
    if changed!=1:raise Unavailable('bounded_bind_compare_and_swap')
    _write_dependencies(db,row['seq'],doc['dependency_roots'])
    return cell.cell_id


def enqueue_bounded(db,cells,packet,handoff,chunk,lexical_rows,lexical_status):
    """Optional primary-atomic registration/finalization. SQL errors are hard.

    Only already witnessed PCM and pure assembly enter here. No source reads,
    native requests, owner lease or model work occur inside the transaction.
    """
    from .native_owned_core_seams import (validate_target,lexical_snapshot,same_numeric_snapshot,
        slot, SUPPORT_FRAMES)
    from .native_owned_cell_capture import bounded_primary_snapshot
    if not db.in_transaction:raise ValueError('Bounded planning requires primary transaction')
    existing_owned=bool(isinstance(handoff,dict) and handoff.get('waiters'))
    notice={'schema':'bounded-core-support-notice-v1','status':'waiting' if existing_owned else 'discovery_unadmitted',
            'reason':'bounded_planning_unavailable','owned_targets_preserved':existing_owned}
    if (type(handoff) is not dict or packet is None or handoff.get('batch') is None
            or handoff.get('reserved') is not True):return notice
    budget=handoff['budget']; context=handoff['batch']; scope=sha(packet['commit']['scope'])
    now=max(time.time(),handoff.get('effective_now',0.))
    if not budget.reserve_planning():notice['reason']='bounded_planning_capacity'; return notice
    origins=handoff.get('origins',[]); proposals=handoff.get('plans',[])
    if len(origins)>CORE_INTENTS or sum(p.get('reason') is None for p in proposals)>4:
        notice['reason']='bounded_target_capacity';return notice
    registered=[]; bound=[]; refused=[]
    db.execute('SAVEPOINT bounded_optional')
    try:
        # Cooperative checks happen before statements. A VM interrupt or any
        # SQLite/IO/lock failure propagates and rolls back the entire primary.
        db.set_progress_handler(budget.progress,100)
        with budget.finalization_phase():
            batch=db.execute('SELECT native_core_ordinal,retention_store_key FROM recording_batches WHERE id=?',
                (context['batch_id'],)).fetchone();budget.projection(128)
            if batch is None or tuple(batch)!=(context['ordinal'],context['retention_store_key']):
                raise Unavailable('bounded_batch_counter_changed')
            _exact_compacts(db,[packet['commit']],context,budget)
            for original in origins:
                _,roots=validate_target(original['target']);_exact_compacts(db,roots,context,budget)
                row,status=_register_target(db,original,context,scope,now,budget)
                if handoff.get('clock_owner') is not None:
                    handoff['clock_owner'].bounded_expired(row['target_key'],row['wait_deadline'])
                registered.append((row['candidate_key'],status))
            # Finalize solely from the actually retained rows. A retention
            # refusal cannot silently convert prospective lexical atoms to opaque.
            actual=None
            if any(p.get('reason') is None for p in proposals):
                planned=handoff.get('snapshot')
                if planned is None:raise Unavailable('bounded_snapshot_missing')
                if handoff.get('draft_ids') and lexical_status!='retained':
                    raise Unavailable('bounded_inventory_not_retained')
                snapshot=bounded_primary_snapshot(db,chunk['session_id'],float.fromhex(planned['origin']),
                    planned['scan_frames'],budget)
                actual=lexical_snapshot(chunk['session_id'],snapshot['rows'],snapshot['sources'],
                    float.fromhex(planned['origin']),scan_frames=planned['scan_frames'],schema=planned['schema'])
                ids=handoff.get('draft_ids',[])
                if len(ids)!=len(lexical_rows):raise Unavailable('bounded_prospective_rows_changed')
                if not same_numeric_snapshot(planned,actual,dict(zip(ids,[r['id'] for r in lexical_rows]))):
                    raise Unavailable('bounded_inventory_changed')
            seen=set()
            for proposal in proposals:
                target=proposal['target'];key=target['key']
                if key in seen:continue
                seen.add(key)
                row=_read_v2(db,target_key=key,budget=budget)
                if row is None or row['plan_phase']!='waiting':continue
                reason=_waiting_reason(row,context,scope,now,handoff.get('clock_owner'))
                if reason is None and proposal.get('reason') is None:
                    # The arriving eligible core gets its attempt before the
                    # logical horizon or true-EOF closure below.
                    if proposal['commits'][-1]!=packet['commit']:raise Unavailable('bounded_publication_anchor_changed')
                    bound.append(_bind_target(db,row,proposal,context,actual,budget,now))
                    continue
                if reason is None:
                    components=target['identity']['components']
                    if handoff.get('true_eof'):reason='unresolved_end_of_source'
                    elif handoff.get('logical_end',0)>=components[0][0]+SUPPORT_FRAMES:
                        reason='unresolved_logical_horizon'
                    elif proposal.get('reason') in ('commitment_capacity','support_duration','source_gap',
                            'unresolved_scope_transition','unresolved_lexical_gap','no_legal_fixed_point'):
                        reason=proposal['reason']
                if reason is not None:
                    _terminal(db,row,'no_result',reason[:80],budget=budget);refused.append(reason)
            budget.check()
        db.set_progress_handler(budget.progress if getattr(budget,'primary_sql',False) else None,100)
        db.execute('RELEASE bounded_optional')
        return {'schema':notice['schema'],'status':'bound' if bound else 'waiting' if registered else 'unchanged',
                'reason':refused[0] if refused else 'bounded_support_pending','registered':len(registered),'bound':len(bound)}
    except (Unavailable,ReadError) as exc:
        # This is only the cooperative refusal path. Never catch sqlite.Error:
        # interrupts/full/IO/lock failures invalidate the whole primary commit.
        db.set_progress_handler(None,0)
        if not db.in_transaction:raise
        db.execute('ROLLBACK TO bounded_optional');db.execute('RELEASE bounded_optional')
        notice['reason']=(getattr(exc,'reason',None) or str(exc))[:80]
        return notice
    finally:db.set_progress_handler(budget.progress if getattr(budget,'primary_sql',False) else None,100)


def ordinary_binding(doc,commit):
    from .native_owned_core_seams import validate_commit
    p=validate_commit(commit); c=doc['commit']; evidence=doc['commit_evidence']; support=doc['support']
    if (c!={'chunk_id':p.chunk_id,'start':p.source_start,'end':p.source_end,'sha256':commit['evidence_sha256']}
            or sha(evidence)!=commit['evidence_sha256'] or sha(evidence['native'])!=commit['native_sha256']
            or sha(evidence['core_cut'])!=commit['cut_sha256']
            or sha(doc['native']['provenance'])!=commit['runtime_sha256']
            or support['core_start_frame']!=p.logical_start or support['core_end_frame']!=p.logical_end):
        raise Unavailable('automatic_core_binding')
    for raw in doc['dependencies']:
        part=SourceSpan(**raw)
        if (part.session_id!=p.session_id or part.chunk_id!=p.chunk_id
                or part.receipt_sha256!=p.receipt_sha256 or part.source_sha256!=p.source_sha256
                or part.source_frames!=p.source_frames or not p.source_start<=part.source_start<part.source_end<=p.source_end
                or part.logical_start-part.source_start!=p.logical_start-p.source_start):
            raise Unavailable('automatic_source_binding')


def source_binding(kind,doc,support,parts,commits):
    binding={'automation_policy':POLICY,'selection_policy':doc['policy'],
        'kind':kind,'session_id':parts[0].session_id,
        'parts':[p.content() for p in parts],
        'source_aliases':[{'chunk_id':p.chunk_id,'receipt_sha256':p.receipt_sha256} for p in parts],
        'support_frames':[support.start_frame,support.end_frame],
        'core_frames':[[c['part']['logical_start'],c['part']['logical_end']] for c in commits]}
    return canonical(binding,BINDING_BYTES)


def plans(cells,packet):
    from .native_owned_core_seams import slot, is_core, validate_commit, RESET_SCHEMA
    if type(cells) is not tuple or len(cells)>BATCH_CELLS:raise Unavailable('automatic_core_candidate_capacity')
    if type(packet) is not dict:raise Unavailable('automatic_core_commit_required')
    commit=packet['commit']; validate_commit(commit)
    raw=[]
    for cell in cells:
        if type(cell) is not Cell:raise Unavailable('automatic_cell_required')
        doc,support,parts=validate_document(cell.proof)
        if 'schema' in doc:raise Unavailable('automatic_ordinary_required')
        ordinary_binding(doc,commit)
        raw.append(('ordinary',cell.proof,support,parts,(commit,)))
    if packet['proof'] is not None:
        doc,support,parts=validate_document(packet['proof'])
        if not is_core(doc) or doc['commits'][1]!=commit:raise Unavailable('automatic_seam_binding')
        # Restart replay remains in the existing core_seam family. Its distinct
        # selection policy is already bound into ownership; no SQL migration,
        # relabeling of historical rows, or retry of accepted work is needed.
        raw.append(('reset_seam' if doc['schema']==RESET_SCHEMA else 'core_seam',packet['proof'],support,parts,tuple(doc['commits'])))
    if len(raw)>CORE_INTENTS:raise Unavailable('automatic_core_candidate_capacity')
    result=[]
    for kind,proof,support,parts,commits in raw:
        if len(proof)>PROOF_BYTES or not 1<=len(parts)<=2:raise Unavailable('automatic_proof_capacity')
        cell_id=sha({'policy':json.loads(proof)['policy'],'support':support.to_dict()})
        # Epoch/run labels are proof evidence, not the candidate's ownership key.
        body=source_binding(kind,json.loads(proof),support,parts,commits); key=hashlib.sha256(body).hexdigest()
        roots=[c['part']['chunk_id'] for c in commits]; slots=[slot(c) for c in commits]
        result.append(dict(key=key,sid=parts[0].session_id,kind=kind,binding=body,proof=proof,
            proof_sha256=hashlib.sha256(proof).hexdigest(),cell_id=cell_id,
            slot1=slots[0],slot2=slots[1] if len(slots)==2 else None,
            cid1=roots[0],cid2=roots[1] if len(roots)==2 else None))
    if len({r['key'] for r in result})!=len(result):raise Unavailable('automatic_duplicate_ownership')
    return result


def enqueue(db,cells,packet):
    from .native_owned_core_seams import slot,canonical as core_canonical,COMPACT_BYTES
    prepared=plans(cells,packet)
    if not prepared:return
    sid=prepared[0]['sid']; new=[]
    for p in prepared:
        old=db.execute(f'SELECT {COLUMNS},proof FROM {TABLE} WHERE candidate_key=?',(p['key'],)).fetchone()
        if old is not None:
            if old['binding']!=p['binding'] or old['proof_sha256']!=p['proof_sha256'] or old['cell_id']!=p['cell_id']:
                raise Unavailable('automatic_candidate_conflict')
            continue
        new.append(p)
    capacity(db,sid,rows=len(new),pending=len(new),proofs=sum(len(p['proof']) for p in new),
             charge=sum(ACCOUNTING_BYTES+len(p['proof'])+len(p['binding']) for p in new))
    # Exact compact existence is checked inside the very same primary commit.
    for p in new:
        for s in (p['slot1'],p['slot2']):
            if s is not None and not db.execute("SELECT 1 FROM native_core_commits WHERE slot=? AND state!='cancelled'",(s,)).fetchone():
                raise Unavailable('automatic_core_missing')
        charge=ACCOUNTING_BYTES+len(p['proof'])+len(p['binding']); now=time.time()
        db.execute(f'''INSERT INTO {TABLE}(candidate_key,sid,kind,binding,proof,proof_sha256,cell_id,slot1,slot2,cid1,cid2,state,reason,created,updated,charge_bytes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued','awaiting_publication',?,?,?)''',
            (p['key'],sid,p['kind'],p['binding'],p['proof'],p['proof_sha256'],p['cell_id'],p['slot1'],p['slot2'],p['cid1'],p['cid2'],now,now,charge))
        _charge(db,sid,(1,charge,1,len(p['proof'])))


def _linked(db,key,identity):
    row=db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE candidate_key=? AND sid=?',(key,identity.session_id)).fetchone()
    if row is None:raise ReadError(409,'automatic_intent_missing')
    if _version(db,row) is not None:
        row=_read_v2(db,key=key)
    if (row['job_id'],row['jobs_epoch'],row['request_sha256'])!=(identity.job_id,identity.original_jobs_epoch,identity.canonical_request_sha256):
        raise ReadError(409,'automatic_result_binding')
    return row


def admit(db,key,identity):
    row=db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE candidate_key=? AND sid=?',(key,identity.session_id)).fetchone()
    if row is None or row['state']!='published' or row['job_id'] is not None:raise ReadError(409,'automatic_admission_closed')
    if _version(db,row) is not None:
        row=_read_v2(db,key=key)
        if row['plan_phase']!='bound':raise ReadError(409,'automatic_admission_closed')
    if db.execute("SELECT 1 FROM chunks WHERE session_id=? AND status='cancelled' LIMIT 1",(identity.session_id,)).fetchone():
        raise ReadError(409,'automatic_cancelled')
    db.execute(f"UPDATE {TABLE} SET state='accepted',reason='admitted_no_replay',job_id=?,jobs_epoch=?,request_sha256=?,updated=? WHERE candidate_key=? AND state='published'",
               (identity.job_id,identity.original_jobs_epoch,identity.canonical_request_sha256,time.time(),key))


def complete(db,key,identity,*,state='committed',reason='result_committed'):
    row=_linked(db,key,identity)
    if row['state']==state:return
    if row['state']!='accepted':raise ReadError(409,'automatic_publication_veto')
    if db.execute("SELECT 1 FROM chunks WHERE session_id=? AND status='cancelled' LIMIT 1",(identity.session_id,)).fetchone():
        raise ReadError(409,'automatic_publication_veto')
    _terminal(db,row,state,reason)


def reconcile_job(db,job_id):
    row=db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE job_id=?',(job_id,)).fetchone()
    if row is not None and row['state']=='accepted':_terminal(db,row,'interrupted','prior_admission_unknown_no_replay')


def authority_valid(authority,key,doc,budget):
    from .native_owned_core_seams import read_commit,is_core,current,slot
    raw=canonical(doc); proof_hash=hashlib.sha256(raw).hexdigest()
    from .native_owned_core_seams import BOUNDED_SCHEMA
    if doc.get('schema')==BOUNDED_SCHEMA:
        from .native_owned_cells import digest
        digest(key)
        return current(authority,doc,budget,automatic_key=key)
    def read(db):
        row=db.execute(f'SELECT {COLUMNS},length(proof) AS pn FROM {TABLE} WHERE candidate_key=?',(key,)).fetchone()
        if row is not None and _version(db,row) is not None:return None
        if (row is None or row['state'] not in ACTIVE or row['pn']!=len(raw) or row['proof_sha256']!=proof_hash
                or hashlib.sha256(row['binding']).hexdigest()!=key):return None
        same=db.execute(f'SELECT proof=? FROM {TABLE} WHERE candidate_key=?',(raw,key)).fetchone()[0]
        if not same:return None
        commits=[]
        for s in (row['slot1'],row['slot2']):
            if s is not None:
                c=read_commit(db,s,budget)
                live=db.execute("SELECT 1 FROM native_core_commits WHERE slot=? AND state!='cancelled'",(s,)).fetchone()
                if c is None or not live:return None
                commits.append(c)
        budget.projection(len(row['binding'])+256)
        return row,commits
    found=authority._read(budget,'automatic_intent',read)
    if found is None:return False
    row,commits=found
    parsed,support,parts=validate_document(raw)
    if (row['binding']!=source_binding(row['kind'],doc,support,parts,commits)
            or row['cell_id']!=sha({'policy':doc['policy'],'support':support.to_dict()})
            or row['cid1']!=commits[0]['part']['chunk_id']
            or row['cid2']!=(commits[1]['part']['chunk_id'] if len(commits)==2 else None)):
        return False
    if is_core(doc):
        return doc['commits']==commits and current(authority,doc,budget)
    if len(commits)!=1:return False
    ordinary_binding(doc,commits[0])
    if not authority.cell_valid(doc,budget,publishing=False):return False
    c=authority.row(doc['commit']['chunk_id'],budget)
    if c is None or c['status'] in ('cancelled','failed','staging'):return False
    support=doc['support']; origin=float.fromhex(commits[0]['scope']['origin'])
    return (authority.mapping(c,origin,support['start_frame'],support['end_frame'],budget)
            ==tuple(SourceSpan(**p) for p in doc['dependencies']))


class Automatic:
    def __init__(self,store,results,*,capture=None,jobs=None):
        self.store,self.results,self.capture,self.jobs=store,results,capture,jobs
        self.flight=None; self.next_attempt=0.; self.last_reason='not_started'
        self._round={};self._sessions=deque()
        self.closed=False
        self._bounded_started_wall=time.time();self._bounded_started_mono=time.monotonic()
        self._bounded_startup=True
        self._bounded_startup_after=None
        self._bounded_deadlines={}

    @contextmanager
    def _bounded_database(self,budget,*,write=False):
        # Adapt the existing result-store list budget without resetting either
        # cumulative VM work or the tighter of its and the shared deadlines.
        from .moss_result_store import WRITE_SECONDS
        now=time.monotonic()
        elapsed=budget.elapsed+(now-budget._began if budget._began is not None else 0.)
        shared=[min(self.results.clock()+WRITE_SECONDS,now+max(0.,budget.seconds-elapsed)),budget.steps]
        try:
            with self.results._database(write=write,budget=shared) as db:yield db
        finally:budget.steps=max(budget.steps,shared[1])
        budget.check()

    def maintain_bounded_waiters(self,*,stop=lambda:False):
        """One indexed metadata-only turn, with no owner or model lease."""
        if stop() or not bounded_enabled(self.store.settings):return False
        from .native_owned_cell_store import Budget
        now=time.time();mono=time.monotonic()
        with Budget().phase() as budget:
            closed=[];next_after=None;startup_done=False
            with self._bounded_database(budget,write=True) as db:
                if not has_bounded_schema(db):return False
                # Startup closes capture-run-dependent waits. During a live
                # process the earlier frozen monotonic/wall deadline wins.
                deadline=now
                clause='' if self._bounded_startup else ' AND wait_deadline<=?'
                args=() if self._bounded_startup else (deadline,)
                if self._bounded_startup and self._bounded_startup_after is not None:
                    clause=' AND (wait_deadline,seq)>(?,?)';args=self._bounded_startup_after
                rows=db.execute(f'''SELECT {COLUMNS} FROM {TABLE} INDEXED BY auto_v2_deadline
                    WHERE plan_phase='waiting' '''+clause+' ORDER BY wait_deadline,seq LIMIT 1',args).fetchall()
                if not self._bounded_startup:
                    known={row['seq'] for row in rows}
                    expired=sorted((expiry,key) for key,expiry in self._bounded_deadlines.items() if expiry<=mono)[:1]
                    for _,key in expired:
                        row=db.execute(f'''SELECT {COLUMNS} FROM {TABLE} INDEXED BY auto_v2_target
                            WHERE target_key=? AND plan_phase='waiting' ''',(key,)).fetchone()
                        if row is not None and row['seq'] not in known:rows.append(row);known.add(row['seq'])
                    rows=sorted(rows,key=lambda row:row['seq'])[:1]
                budget.projection(len(rows)*1024)
                for base in rows:
                    row=_read_v2(db,key=base['candidate_key'],budget=budget)
                    next_after=(row['wait_deadline'],row['seq'])
                    run=getattr(getattr(self.capture,'core_seams',None),'run',None)
                    origin_run=json.loads(row['target_body'])['origins'][0]['commits'][0]['scope']['run']
                    if self._bounded_startup and origin_run==run and not self.bounded_expired(row['target_key'],row['wait_deadline']):continue
                    _terminal(db,row,'no_result','unresolved_scope_transition' if origin_run!=run else 'unresolved_wait_deadline',budget=budget)
                    closed.append(row['target_key'])
                if self._bounded_startup:
                    startup_done=not rows
            # Do not forget an expiry or advance startup scanning until the
            # existing result-store context confirms the transaction outcome.
            for key in closed:self._bounded_deadlines.pop(key,None)
            if startup_done:self._bounded_startup=False
            if next_after is not None:self._bounded_startup_after=next_after
            if closed:self.last_reason='bounded_waiters_closed'
            return bool(closed)

    def bounded_now(self):
        return time.time()

    def bounded_expired(self,key,wall_deadline=None):
        now=time.time();mono=time.monotonic()
        if key not in self._bounded_deadlines:
            if len(self._bounded_deadlines)>=MAX_PENDING:raise Unavailable('bounded_clock_capacity')
            remaining=300. if wall_deadline is None else min(300.,max(0.,wall_deadline-now))
            self._bounded_deadlines[key]=mono+remaining
        if wall_deadline is not None and now>=wall_deadline:
            self._bounded_deadlines[key]=min(self._bounded_deadlines[key],mono)
        return mono>=self._bounded_deadlines[key]

    def current(self):
        return (not self.closed and enabled(self.store.settings) and self.capture is not None
                and self.jobs is not None and self.jobs._enabled())

    def before_window(self,chunk):
        if not enabled(self.store.settings):return True
        from .moss_result_store import _ReadDeferred
        budget=None
        try:
            if bounded_enabled(self.store.settings):
                from .native_owned_cell_store import Budget
                with Budget().phase() as budget:
                    with self._bounded_database(budget,write=True) as db:
                        release_wait_pressure(db,chunk['session_id'],budget)
                        pending={r[0] for r in db.execute(f"SELECT target_key FROM {TABLE} INDEXED BY auto_v2_deadline WHERE plan_phase='waiting' LIMIT ?",(MAX_PENDING+1,))}
                        budget.projection(len(pending)*64)
                        if len(pending)>MAX_PENDING:raise ReadError(503,'automatic_pending_capacity')
                    self._bounded_deadlines={k:v for k,v in self._bounded_deadlines.items() if k in pending}
            with (budget.phase() if budget is not None else nullcontext()), \
                    (self._bounded_database(budget) if budget is not None else self.results._database()) as db:
                if not recording(db,chunk['id']):return True
                try:capacity(db,chunk['session_id'])
                except ReadError as exc:self.last_reason=exc.reason; return False
                from .native_owned_core_seams import MAX_COMPACTS
                if db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0]>=MAX_COMPACTS:
                    self.last_reason='automatic_compact_capacity'; return False
        except _ReadDeferred as exc:
            self.last_reason='automatic_preflight_read_deferred'
            try:
                import logging
                fields={'operation':'read','callsite':'before_window','phase':exc.phase,
                    'reason':'result_storage_unavailable','sqlite_code':exc.sqlite_code}
                logging.getLogger(__name__).warning('moss_automatic_preflight_deferred %s',
                    json.dumps(fields,sort_keys=True),extra={'automatic_preflight_deferred':fields})
            except BaseException:pass
            return _WINDOW_READ_DEFERRED
        except (ReadError,Unavailable,sqlite3.Error,OSError):
            if not bounded_enabled(self.store.settings):raise
            # This preflight contains optional metadata only, before native or
            # primary SQL. Retry re-reads durable state after an uncertain write.
            self.last_reason='bounded_preflight_deferred'
            return False
        if not self.current():self.last_reason='automatic_configuration_unavailable'; return False
        self.results._headroom()
        return True

    def read_candidate(self,key,*,proof=False):
        with self.results._database() as db:
            version=',target_key' if has_bounded_schema(db) else ',NULL'
            length=db.execute(f'SELECT length(binding),length(proof)'+version+f' FROM {TABLE} WHERE candidate_key=?',(key,)).fetchone()
            if length is None:return None
            if length[2] is not None:
                row=_read_v2(db,key=key)
                if not proof:row.pop('proof',None)
                return row
            if not 1<=length[0]<=BINDING_BYTES or proof and (length[1] is None or not 1<=length[1]<=PROOF_BYTES):
                raise ReadError(503,'automatic_payload_capacity')
            row=db.execute(f'SELECT {COLUMNS}'+(',proof' if proof else '')+f' FROM {TABLE} WHERE candidate_key=?',(key,)).fetchone()
            if row is None:return None
            return extended_row(db,row)

    def note(self,key,reason,*,published=False):
        if type(reason) is not str or len(reason)>80:reason='automatic_unavailable'
        with self.results._database(write=True) as db:
            guard=" AND (target_key IS NULL OR plan_phase='bound')" if has_bounded_schema(db) else ''
            db.execute(f"UPDATE {TABLE} SET reason=?,updated=?,state=CASE WHEN ? AND state='queued' THEN 'published' ELSE state END WHERE candidate_key=? AND state IN ('queued','published')"+guard,
                       (reason,time.time(),int(published),key))
        self.last_reason=reason

    def validate_record(self,record):
        from .native_owned_cell_store import Budget
        with Budget().phase() as budget:
            if not authority_valid(self.capture.authority,record['automatic_key'],json.loads(record['token'].proof),budget):
                raise ReadError(409,'automatic_source_revoked')
        return True

    def _snapshot(self):
        if self._sessions:return True
        with self.results._database() as db:
            rows=[]
            guard=" AND (target_key IS NULL OR plan_phase='bound')" if has_bounded_schema(db) else ''
            for state in ('queued','published'):
                rows.extend(db.execute(f"SELECT seq,sid,candidate_key,state FROM {TABLE} INDEXED BY auto_state_seq WHERE state=?"+guard+" ORDER BY seq LIMIT ?",
                    (state,MAX_PENDING+1)).fetchall())
        if len(rows)>MAX_PENDING:raise ReadError(503,'automatic_pending_capacity')
        grouped={}
        for row in sorted(rows,key=lambda x:x['seq']):
            group=grouped.setdefault(row['sid'],deque());group.append((row['seq'],row['candidate_key']))
            if len(group)>SESSION_PENDING:raise ReadError(503,'automatic_session_pending_capacity')
        self._round=grouped;self._sessions=deque(sorted(grouped))
        return bool(self._sessions)

    def _select_key(self):
        if not self._snapshot():return None
        sid=self._sessions.popleft();group=self._round[sid]
        _,key=group.popleft()
        if group:self._sessions.append(sid)
        else:del self._round[sid]
        return key

    def maintain_text_storage(self, *, stop=lambda:False):
        # Retained text ownership deliberately prevents a new automatic turn.
        # Its exact metadata-only acknowledgement must be reachable first.
        if self.jobs is None or stop() or time.monotonic() < self.next_attempt:return False
        try:return self.jobs.maintain_automatic_text_storage(self, stop=stop)
        except Exception:return False  # Ownership and the frozen payload remain.

    def primary_yield_due(self):
        """Bounded metadata hint; no proof/PCM, admission or storage retry."""
        try:
            if not self.current() or time.monotonic()<self.next_attempt:return False
            permit=self.jobs.permit
            state=permit.status()
            if state['admission_closed'] or state['optional_admission_unavailable'] or permit._optional is not None:return False
            # Storage acknowledgement is a separate turn even with no queued
            # model candidate. It never grants model readiness before the ack.
            if self.jobs.automatic_maintenance_hint():return True
            if not self.jobs.automatic_ready_hint():return False
            if self.flight is not None:return self.flight[1].slot_released.is_set()
            if self.capture.owner._slot is not None:return False
            return self._snapshot()
        except Exception:return False

    def _admit_key(self,key):
        selected=self.read_candidate(key)
        if selected is None or selected['state']!='published':return
        try:self.jobs.submit_automatic(selected)
        except ReadError as exc:self.note(key,exc.reason)

    def _finish_flight(self,stop):
        key,op,cell_id=self.flight
        # The existing owner deadline includes queue time. No new budget, no
        # second publication operation, and no wait on unknown model attempts.
        while not op.slot_released.is_set():
            remaining=op.deadline-time.monotonic()
            if stop() or remaining<=0:
                self.last_reason='automatic_publication_pending';return
            op.slot_released.wait(min(.05,remaining))
        if stop():return
        outcome=op.result or {};rows=outcome.get('cells',[])
        if outcome.get('status')=='durable' and len(rows)==1 and rows[0].get('cell_id')==cell_id:
            status=rows[0].get('status')
            if status in ('retained','existing'):
                self.note(key,'awaiting_admission',published=True)
                self.flight=None
                self._admit_key(key);return
            if status=='not_retained_pcm':self.note(key,'cell_pcm_not_retained');self.flight=None;return
            if status=='not_retained_metadata_capacity':self.note(key,'cell_metadata_capacity');self.flight=None;return
        self.note(key,'automatic_publication_unacknowledged');self.flight=None

    def step(self, *, stop=lambda:False):
        if not self.current() or time.monotonic()<self.next_attempt or stop():return
        self.next_attempt=time.monotonic()+1.
        try:
            if not self.jobs.automatic_idle():self.last_reason='automatic_job_slot_occupied';return
            if self.flight is not None:
                self._finish_flight(stop);return
            key=self._select_key()
            if key is None:
                with self.results._database() as db:
                    unknown=db.execute(f"SELECT 1 FROM {TABLE} INDEXED BY auto_state_seq WHERE state='accepted' LIMIT 1").fetchone()
                self.last_reason='automatic_recovery_required' if unknown else 'automatic_idle';return
            selected=self.read_candidate(key)
            if selected is None or selected['state'] not in ('queued','published'):return
            if selected.get('target_key') is not None and selected['plan_phase']!='bound':return
            if selected['state']=='published':self._admit_key(key);return
            from .native_owned_cell_store import Budget,Operation
            from .native_owned_seam_cells import anchor
            if self.capture.owner._slot is not None:self.note(key,'cell_owner_busy');return
            selected=self.read_candidate(key,proof=True)
            if selected is None or selected['state']!='queued' or stop():return
            with Budget().phase() as budget:
                doc,support,parts=validate_document(selected['proof'])
                if not authority_valid(self.capture.authority,key,doc,budget):
                    self.note(key,'automatic_source_unavailable');return
                pcm=self.capture.authority.original_pcm(parts,budget);a=anchor(doc)
                cell=Cell(support,parts,selected['proof'],pcm,selected['sid'],a['chunk_id'],a['start'],a['end'],a['sha256'])
                op=self.capture.owner.submit('publish_auto',(key,cell))
                if isinstance(op,Operation):self.flight=(key,op,cell.cell_id)
                else:self.note(key,'cell_owner_busy');return
            self._finish_flight(stop)
        except Exception as exc:
            self.last_reason=exc.reason if isinstance(exc,ReadError) else 'automatic_maintenance_pending'

    def _projection(self,row,epoch=None):
        row=dict(row)
        value={'sequence':row['seq'],'candidate_key':row['candidate_key'],'session_id':row['sid'],
            'kind':row['kind'],'state':row['state'],'reason':row['reason'],'created_at':row['created'],
            'updated_at':row['updated'],'cell_id':row['cell_id'],'proof_sha256':row['proof_sha256'],
            'source_binding':json.loads(row['binding']),'job_id':row['job_id'],
            'original_jobs_epoch':row['jobs_epoch'],'recovery_required':row['state']=='interrupted',
            'structured_result_url':('/sessions/'+row['sid']+'/moss-jobs/history/'+row['job_id']+'/result'
                if row['state']=='committed' and row['job_id'] else None)}
        if row.get('target_key') is not None:
            value.update(target_key=row['target_key'],plan_phase=row['plan_phase'],
                evidence_version=2,waiting=row['plan_phase']=='waiting')
        live=getattr(self.jobs,'_live',None) if self.jobs is not None else None
        exact_live=(live is not None and live.get('automatic_key')==row['candidate_key']
            and live.get('job_id')==row['job_id']
            and (not live.get('cleaned',False) or
                 live.get('storage_pending',False) and live.get('history_snapshot') is not None))
        if row['state']=='accepted' and (epoch!=row['jobs_epoch'] or not exact_live):
            value['state']='interrupted_or_unknown';value['recovery_required']=True
        return value

    def health(self):
        try:return self.status()
        except Exception:return {'enabled':enabled(self.store.settings),'candidate_count':None,
            'pending_count':None,'oldest_queue_age_seconds':None,'reason':'automatic_status_unavailable',
            'in_flight':self.flight is not None,'discovery_complete':False}

    def status(self,sid=None):
        with self.results._database() as db:
            if sid is not None:self.results._session(db,sid)
            scope=sid or ''
            row=db.execute(f'SELECT rows,charge,pending,proofs FROM {USAGE} WHERE scope=?',(scope,)).fetchone() or (0,0,0,0)
            params=(sid,) if sid else ()
            where=' AND sid=?' if sid else ''
            counts={state:db.execute(f'SELECT COUNT(*) FROM {TABLE} WHERE state=?'+where,(state,*params)).fetchone()[0] for state in ACTIVE}
            if has_bounded_schema(db):
                counts['waiting']=db.execute(f"SELECT COUNT(*) FROM {TABLE} INDEXED BY auto_v2_deadline WHERE plan_phase='waiting'"+where,params).fetchone()[0]
                counts['retained_certificate_policy']='charged_after_terminal'
                counts['bounded_gc']='conservative_root_preservation_only'
            oldest_rows=[db.execute(f'SELECT seq,created FROM {TABLE} INDEXED BY auto_state_seq WHERE state=?'+where+' ORDER BY seq LIMIT 1',(state,*params)).fetchone() for state in ('queued','published')]
            oldest=min((r for r in oldest_rows if r is not None),key=lambda r:r['seq'],default=None)
            pending_primary=db.execute("SELECT 1 FROM chunks WHERE (status IN ('queued','running','waiting_input','staging','paused') OR (status='failed' AND next_offset<duration-0.00001 AND EXISTS (SELECT 1 FROM recording_work rw WHERE rw.chunk_id=chunks.id)))"+(' AND session_id=?' if sid else '')+' LIMIT 1',params).fetchone()
        return {'enabled':enabled(self.store.settings),'candidate_count':row[0],'charge_bytes':row[1],
            'pending_count':row[2],'proof_bytes':row[3],**counts,
            'oldest_queue_age_seconds':max(0.,time.time()-oldest['created']) if oldest else None,
            'reason':self.last_reason,'in_flight':self.flight is not None,
            'discovery_complete':pending_primary is None,
            'limits':{'rows':MAX_ROWS,'session_rows':SESSION_ROWS,'bytes':MAX_BYTES,'session_bytes':SESSION_BYTES,
                'pending':MAX_PENDING,'session_pending':SESSION_PENDING,'proof_bytes':MAX_PROOFS,'session_proof_bytes':SESSION_PROOFS}}

    def list(self,sid,after=0,limit=20):
        if type(after) is not int or not 0<=after<(1<<63) or type(limit) is not int or not 1<=limit<=20:raise ReadError(409,'invalid_cursor')
        with self.results._database() as db:
            self.results._session(db,sid)
            extra=',target_key,plan_phase' if has_bounded_schema(db) else ''
            rows=db.execute(f'SELECT {COLUMNS}'+extra+f' FROM {TABLE} INDEXED BY auto_session_seq WHERE sid=? AND seq>? ORDER BY seq LIMIT ?',(sid,after,limit+1)).fetchall()
        values=[];last=after;more=False;epoch=self.jobs.jobs_epoch if self.jobs else None
        for row in rows:
            value=self._projection(row,epoch)
            if len(values)>=limit or len(canonical({'candidates':values+[value]},128*1024))>24576:more=True;break
            values.append(value);last=row['seq']
        return canonical({'candidates':values,'next_cursor':last,'has_more':more,'capacity':self.status(sid)},32768)

    def detail(self,sid,key):
        with self.results._database() as db:
            self.results._session(db,sid)
            extra=',target_key,plan_phase' if has_bounded_schema(db) else ''
            row=db.execute(f'SELECT {COLUMNS}'+extra+f' FROM {TABLE} WHERE sid=? AND candidate_key=?',(sid,key)).fetchone()
            if row is None:raise ReadError(404,'automatic_candidate_not_found')
        return canonical(self._projection(row,self.jobs.jobs_epoch if self.jobs else None),16384)
