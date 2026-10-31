"""Fresh opt-in accepted-prefix policy. No replay scheduler or retrospective proof."""
from dataclasses import dataclass
import hashlib
import json
import math
import uuid
import re
from . import strict_lifetime

POLICY = 'strict-primary-manual-v1'
ARCHIVE_POLICY = 'strict-primary-archive-v2'
PAYLOAD_POLICY = 'strict-primary-payload-v2'
MAX_ROWS = 2056  # existing compact capacity; includes deleted-session tombstones


def frame(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('strict_frame')
    result = round(value * 16000)
    if result > 1 << 53 or result / 16000 != value:raise ValueError('strict_frame')
    return result


def namespace_present(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE lower(name) GLOB 'strict_*' LIMIT 1").fetchone() is not None


def marked(db, sid):
    if not namespace_present(db):return False
    # Every Store connection's initial admission already checks exact namespace.
    # A malformed or case-variant standalone use must not silently become legacy.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strict_recording_policy' COLLATE BINARY").fetchone():
        raise ValueError('strict_schema_mismatch')
    return db.execute('SELECT 1 FROM strict_recording_policy WHERE sid=?', (sid,)).fetchone() is not None


def _statements(lifetime_records=MAX_ROWS):
    lifetime_records = strict_lifetime.validate(lifetime_records)
    statements = [
    '''CREATE TABLE IF NOT EXISTS strict_recording_policy (
        sid TEXT PRIMARY KEY, policy TEXT NOT NULL CHECK(policy='strict-primary-manual-v1'))''',
    '''CREATE TABLE IF NOT EXISTS strict_recording_control (
        cid TEXT PRIMARY KEY, sid TEXT NOT NULL, source TEXT NOT NULL CHECK(length(source)=64),
        frames INTEGER NOT NULL CHECK(frames>0), state TEXT NOT NULL
        CHECK(state IN ('ready','active','waiting','manual','completed','cancelled')),
        nonce TEXT CHECK(nonce IS NULL OR length(nonce)=32))''',
    '''CREATE TABLE IF NOT EXISTS strict_recording_outcome (
        cid TEXT NOT NULL, start_frame INTEGER NOT NULL CHECK(typeof(start_frame)='integer' AND start_frame>=0),
        end_frame INTEGER NOT NULL CHECK(typeof(end_frame)='integer'),
        nonce TEXT NOT NULL CHECK(length(nonce)=32), source TEXT NOT NULL CHECK(length(source)=64),
        slot TEXT NOT NULL CHECK(length(slot)=64), status TEXT NOT NULL
        CHECK(status IN ('retained','existing','empty','unknown_only')),
        packet TEXT, PRIMARY KEY(cid,start_frame), UNIQUE(cid,end_frame), CHECK(end_frame>start_frame))''',
    '''CREATE TRIGGER IF NOT EXISTS strict_policy_fresh BEFORE INSERT ON strict_recording_policy BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM sessions WHERE id=NEW.sid AND deleted=0 AND timeline_end=0 AND processing_mode='legacy')
          OR EXISTS(SELECT 1 FROM chunks WHERE session_id=NEW.sid) THEN RAISE(ABORT,'strict_fresh_only') END; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_policy_update BEFORE UPDATE ON strict_recording_policy BEGIN SELECT RAISE(ABORT,'strict_policy_immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_policy_delete BEFORE DELETE ON strict_recording_policy BEGIN SELECT RAISE(ABORT,'strict_policy_retained'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_control_source BEFORE UPDATE OF cid,sid,source,frames ON strict_recording_control BEGIN SELECT RAISE(ABORT,'strict_source_immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_control_delete BEFORE DELETE ON strict_recording_control BEGIN SELECT RAISE(ABORT,'strict_control_retained'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_outcome_update BEFORE UPDATE ON strict_recording_outcome BEGIN SELECT RAISE(ABORT,'strict_outcome_immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_outcome_delete BEFORE DELETE ON strict_recording_outcome BEGIN SELECT RAISE(ABORT,'strict_outcome_retained'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_outcome_admit BEFORE INSERT ON strict_recording_outcome BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM chunks c JOIN strict_recording_control s ON s.cid=c.id
          JOIN strict_recording_policy p ON p.sid=c.session_id WHERE c.id=NEW.cid AND c.status='running'
          AND s.state='active' AND s.nonce=NEW.nonce AND s.source=NEW.source AND c.digest=NEW.source
          AND c.next_offset=NEW.start_frame/16000.0 AND c.duration=s.frames/16000.0
          AND NEW.end_frame<=s.frames AND EXISTS(SELECT 1 FROM native_core_commits n WHERE n.slot=NEW.slot AND n.cid=c.id))
          THEN RAISE(ABORT,'strict_outcome_authorization') END;
        SELECT CASE WHEN (SELECT COUNT(*) FROM strict_recording_outcome)>=2056 THEN RAISE(ABORT,'strict_outcome_capacity') END; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_source_fence BEFORE UPDATE OF session_id,digest,path,duration,timeline_start ON chunks
        WHEN EXISTS(SELECT 1 FROM strict_recording_policy WHERE sid=OLD.session_id)
        AND (NEW.session_id IS NOT OLD.session_id OR NEW.digest IS NOT OLD.digest OR NEW.path IS NOT OLD.path
          OR NEW.duration IS NOT OLD.duration OR NEW.timeline_start IS NOT OLD.timeline_start)
        BEGIN SELECT RAISE(ABORT,'strict_source_immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_advance_fence BEFORE UPDATE OF next_offset ON chunks
        WHEN EXISTS(SELECT 1 FROM strict_recording_policy WHERE sid=OLD.session_id) AND NEW.next_offset IS NOT OLD.next_offset BEGIN
        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM strict_recording_control s JOIN strict_recording_outcome o ON o.cid=s.cid
          WHERE s.cid=OLD.id AND s.state='active' AND s.nonce=o.nonce AND s.source=o.source AND o.source=OLD.digest
          AND OLD.status='running' AND o.start_frame/16000.0=OLD.next_offset AND o.end_frame/16000.0=NEW.next_offset
          AND NEW.next_offset>OLD.next_offset AND NEW.next_offset<=OLD.duration)
          THEN RAISE(ABORT,'strict_advance_requires_outcome') END; END''',
    '''CREATE TRIGGER IF NOT EXISTS strict_state_fence BEFORE UPDATE OF status ON chunks
        WHEN EXISTS(SELECT 1 FROM strict_recording_policy WHERE sid=OLD.session_id) BEGIN
        SELECT CASE WHEN NEW.status='running' AND NOT EXISTS(SELECT 1 FROM strict_recording_control WHERE cid=OLD.id AND state='active' AND nonce IS NOT NULL)
          THEN RAISE(ABORT,'strict_running_authorization') END;
        SELECT CASE WHEN NEW.status='queued' AND NOT EXISTS(SELECT 1 FROM strict_recording_control WHERE cid=OLD.id AND state IN ('ready','waiting') AND nonce IS NULL)
          THEN RAISE(ABORT,'strict_manual_retry_required') END;
        SELECT CASE WHEN NEW.status='completed' AND NOT EXISTS(SELECT 1 FROM strict_recording_control s JOIN strict_recording_outcome o ON o.cid=s.cid
          WHERE s.cid=OLD.id AND s.state='active' AND s.nonce=o.nonce AND s.source=OLD.digest
          AND o.end_frame=s.frames AND NEW.next_offset=o.end_frame/16000.0 AND NEW.next_offset=OLD.duration)
          THEN RAISE(ABORT,'strict_completion_requires_terminal_outcome') END; END''',
    ]

    # Only the retained total changes; all authorization and per-core bounds stay.
    return [sql.replace(')>=2056', ')>='+str(lifetime_records)) for sql in statements]


def _canonical_ddl(sql):
    return ''.join(sql.replace(' IF NOT EXISTS','').split())


def schema_statements(policy=POLICY, *, lifetime_records=MAX_ROWS, receipt_records=strict_lifetime.DEFAULT_RECEIPTS, source_storage='inline'):
    extra = strict_lifetime.schema_statements(lifetime_records,receipt_records)
    if policy == POLICY:return _statements(lifetime_records)+extra
    if policy == PAYLOAD_POLICY:
        from .strict_payload_v2 import schema_statements as payload_statements
        statements=_statements(lifetime_records)
        statements[0]=statements[0].replace(POLICY,PAYLOAD_POLICY)
        return statements+payload_statements(lifetime_records=lifetime_records,source_storage=source_storage)+extra
    if policy != ARCHIVE_POLICY:raise ValueError('strict_primary_policy')
    from .strict_archive_v2 import schema_statements as archive_statements
    statements=_statements(lifetime_records)
    # V1 output is untouched. V2 is an explicit different durable policy.
    statements[0]=statements[0].replace(POLICY,ARCHIVE_POLICY)
    return statements+archive_statements()+extra


def admit_schema(db, policy=POLICY, *, lifetime_records=MAX_ROWS, receipt_records=strict_lifetime.DEFAULT_RECEIPTS, source_storage='inline'):
    expected={}
    for sql in schema_statements(policy,lifetime_records=lifetime_records,receipt_records=receipt_records,source_storage=source_storage):
        match=re.match(r'CREATE (TABLE|TRIGGER|INDEX) IF NOT EXISTS ([a-z_]+)',sql)
        expected[match[2]]=(match[1].lower(),_canonical_ddl(sql))
    rows=db.execute("SELECT CASE WHEN length(name)<=64 THEN name ELSE NULL END,type,CASE WHEN length(sql)<=4096 THEN sql ELSE NULL END FROM sqlite_master WHERE lower(name) GLOB 'strict_*' LIMIT ?", (len(expected)+1,)).fetchall()
    if len(rows)!=len(expected):raise ValueError('strict_schema_mismatch')
    for name,kind,sql in rows:
        if type(sql) is not str or name not in expected or (kind,_canonical_ddl(sql))!=expected[name]:raise ValueError('strict_schema_mismatch')
    if policy==PAYLOAD_POLICY:
        from .strict_payload_v2 import admit_payload_schema
        admit_payload_schema(db,lifetime_records=lifetime_records,source_storage=source_storage)

    strict_lifetime.admit(db,lifetime_records,receipt_records)


def init_schema(db, policy=POLICY, *, lifetime_records=MAX_ROWS, receipt_records=strict_lifetime.DEFAULT_RECEIPTS, source_storage='inline'):
    if namespace_present(db):
        admit_schema(db,policy,lifetime_records=lifetime_records,receipt_records=receipt_records,source_storage=source_storage);return
    for statement in schema_statements(policy,lifetime_records=lifetime_records,receipt_records=receipt_records,source_storage=source_storage):db.execute(statement)
    strict_lifetime.initialize(db,lifetime_records,receipt_records)
    if policy==PAYLOAD_POLICY:
        from .strict_payload_v2 import format_for_storage
        db.execute('INSERT INTO strict_payload_policy(singleton,policy,format) VALUES(1,?,?)',
                   (PAYLOAD_POLICY,format_for_storage(source_storage)))
    admit_schema(db,policy,lifetime_records=lifetime_records,receipt_records=receipt_records,source_storage=source_storage)


def new_policy(db, sid, settings):
    cap = settings.max_sessions
    if type(cap) is not int or not 1 <= cap <= 1000000:raise ValueError('strict_policy_capacity_configuration')
    if db.execute('SELECT COUNT(*) FROM strict_recording_policy').fetchone()[0] >= min(cap, 1000):raise OverflowError('strict_policy_capacity')
    policy=getattr(settings,'strict_primary_policy',POLICY)
    if policy not in (POLICY,ARCHIVE_POLICY,PAYLOAD_POLICY):raise ValueError('strict_primary_policy')
    db.execute('INSERT INTO strict_recording_policy VALUES(?,?)', (sid, policy))


def add_control(db, cid, sid, digest, duration):
    if db.execute('SELECT COUNT(*) FROM strict_recording_control').fetchone()[0] >= strict_lifetime.read_limit(db):raise OverflowError('strict_control_capacity')
    db.execute("INSERT INTO strict_recording_control VALUES(?,?,?,?, 'ready',NULL)", (cid,sid,digest,frame(duration)))


def claim(db, chunk):
    control = db.execute('SELECT * FROM strict_recording_control WHERE cid=?', (chunk['id'],)).fetchone()
    if control is None or control['state'] not in ('ready','waiting') or control['nonce'] is not None:raise ValueError('strict_manual_retry_required')
    reconcile(db, chunk)
    nonce = uuid.uuid4().hex
    db.execute("UPDATE strict_recording_control SET state='active',nonce=? WHERE cid=?", (nonce,chunk['id']))
    chunk['_strict_primary'] = True; chunk['_strict_attempt_nonce'] = nonce
    return chunk


def reconcile(db, chunk):
    """Bounded accepted-tip read, including an earlier COMMIT with lost ack."""
    control=db.execute('SELECT * FROM strict_recording_control WHERE cid=?',(chunk['id'],)).fetchone()
    if control is None or control['source']!=chunk['digest'] or control['frames']!=frame(chunk['duration']):raise ValueError('strict_source_changed')
    cursor=frame(chunk['next_offset'])
    tip=db.execute('SELECT end_frame FROM strict_recording_outcome WHERE cid=? ORDER BY end_frame DESC LIMIT 1',(chunk['id'],)).fetchone()
    if cursor != (tip[0] if tip else 0):raise ValueError('strict_durable_tip_mismatch')
    return control


def active(db, chunk, offset):
    current=db.execute('SELECT * FROM chunks WHERE id=?',(chunk['id'],)).fetchone()
    if current is None:raise ValueError('strict_chunk_missing')
    control=reconcile(db,current)
    if (current['status']!='running' or current['next_offset']!=offset or control['state']!='active'
            or control['nonce'] is None or control['nonce']!=chunk.get('_strict_attempt_nonce') or current['digest']!=chunk['digest']):
        raise ValueError('strict_attempt_revoked')
    return current


@dataclass(frozen=True)
class CaptureOutcome:
    sid: str
    cid: str
    source: str
    nonce: str
    start: int
    end: int
    status: str
    evidence: str
    rows: tuple


def row_fingerprint(chunk, offset, result):
    from . import recording_primary_provenance as pp
    rows=result.get('segments')
    if type(rows) is not list or len(rows)>128:raise ValueError('strict_rows_shape')
    total=0; hashes=[]; units_total=0
    for row in rows:
        if type(row) is not dict:raise ValueError('strict_rows_shape')
        content=pp._row_content(chunk,offset,row,result['metadata'])
        row_size=256
        for key in ('text','raw_text','asr_backend','timestamp_kind','speaker_local_id'):
            value=content[key]
            if value is not None:
                if type(value) is not str or len(value)>pp.MAX_BODY:raise ValueError('strict_text_shape')
                size=len(value.encode('utf-8'));total+=size;row_size+=size
        if total>pp.MAX_BODY:raise ValueError('strict_rows_capacity')
        units=row.get('_primary_units'); parent=row.get('_primary_parent_ordinal')
        if type(parent) is not int or not 0<=parent<128 or type(units) is not list or not 1<=len(units)<=4096:
            raise ValueError('strict_units_shape')
        units_total+=len(units)
        if units_total>4096:raise ValueError('strict_units_capacity')
        for unit in units:
            if (type(unit) is not list or len(unit)!=2 or type(unit[0]) is not int or type(unit[1]) is not int
                or unit[0]!=parent or not 0<=unit[1]<4096):raise ValueError('strict_unit_shape')
        hashes.append((pp.digest(content),pp.digest([parent,units]),row_size))
    return tuple(hashes)


def make_capture_outcome(chunk, offset, end, result, status, evidence_sha256):
    if status not in ('retained','empty','unknown_only'):raise ValueError('strict_capture_incomplete')
    nonce=chunk.get('_strict_attempt_nonce')
    if type(nonce) is not str or len(nonce)!=32 or any(c not in '0123456789abcdef' for c in nonce):raise ValueError('strict_capture_attempt')
    rows=row_fingerprint(chunk,offset,result)
    if (status=='empty') != (len(rows)==0):raise ValueError('strict_empty_shape')
    if status=='unknown_only' and any(r.get('speaker_local_id') is not None for r in result['segments']):raise ValueError('strict_unknown_shape')
    if type(evidence_sha256) is not str or len(evidence_sha256)!=64:raise ValueError('strict_capture_evidence')
    a,b=frame(offset),frame(end)
    if not a<b<=frame(chunk['duration']):raise ValueError('strict_capture_interval')
    return CaptureOutcome(chunk['session_id'],chunk['id'],chunk['digest'],nonce,a,b,status,evidence_sha256,rows)


def require_prepared(chunk, offset, end, result):
    value=result.get('_strict_capture')
    if type(value) is not CaptureOutcome:raise ValueError('strict_capture_required')
    expected=make_capture_outcome(chunk,offset,end,result,value.status,value.evidence)
    if expected != value:raise ValueError('strict_capture_changed')
    packet=result.get('_native_core_seam')
    if type(packet) is not dict or packet.get('commit',{}).get('evidence_sha256')!=value.evidence:raise ValueError('strict_compact_required')
    from .native_owned_core_seams import validate_commit
    part=validate_commit(packet['commit'])
    if (part.session_id,part.chunk_id,part.source_sha256,part.source_start,part.source_end)!=(value.sid,value.cid,value.source,value.start,value.end):raise ValueError('strict_compact_changed')
    return value


def commit_outcome(db, chunk, offset, end, result, status, row_ids, *, turn_row_ids=None):
    from .native_owned_core_seams import slot, canonical, COMPACT_BYTES
    from .recording_primary_provenance import TABLE
    value=require_prepared(chunk,offset,end,result); active(db,chunk,offset)
    if status not in (('retained','existing') if value.status=='retained' else (value.status,)):
        # Preserve the known bounded refusal; never expose arbitrary producer data
        # and never weaken the mandatory provenance transaction boundary.
        from .recording_primary_provenance import REASONS
        reason=status if type(status) is str and status in REASONS else 'invalid_status'
        if reason=='capacity':
            # Failure-only counters describe the existing budget, never renew it.
            # Keep this fixed-size and body/identifier free, even if malformed.
            try:
                import time
                from .native_owned_cell_store import Budget
                handoff=result.get('_native_bounded_support')
                budget=handoff.get('budget') if type(handoff) is dict else None
                if not isinstance(budget,Budget):raise ValueError
                elapsed=budget.elapsed+(time.monotonic()-budget._began if budget._began is not None else 0.)
                counters=(budget.steps,budget.sql_bytes,elapsed*1000,budget.seconds*1000)
                if any(type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=2**53 for n in counters):raise ValueError
                reason+=';steps={};sql_bytes={};elapsed_ms={};limit_ms={}'.format(
                    *[round(n,3) for n in counters])
            except Exception:
                reason+=';budget_unavailable'
        raise ValueError('strict_provenance_required:'+reason)
    commit=result['_native_core_seam']['commit']; key=slot(commit)
    core=db.execute('SELECT body FROM native_core_commits WHERE slot=? AND length(body)<=?',(key,COMPACT_BYTES)).fetchone()
    if core is None or core[0]!=canonical(commit,COMPACT_BYTES):raise ValueError('strict_compact_not_persisted')
    packets=db.execute(f'SELECT digest FROM {TABLE} WHERE cid=? AND core_start=? AND core_end=? LIMIT 2',(chunk['id'],value.start,value.end)).fetchall()
    if status=='unknown_only':
        from . import recording_primary_provenance as pp,transcript_contract as contract
        budget=result['_native_bounded_support']['budget']
        budget.reserve_projection(sum(item[2] for item in value.rows)+1024)
        for ident,expected_row in zip(row_ids,value.rows):
            size=db.execute('SELECT '+ '+'.join('COALESCE(length(CAST('+k+' AS BLOB)),0)' for k in
                ('text','raw_text','asr_backend','timestamp_kind','speaker_local_id'))+'+256 FROM segments WHERE id=?',(ident,)).fetchone()
            if size is None or size[0]!=expected_row[2]:raise ValueError('strict_unknown_row_changed')
            row=db.execute('SELECT '+','.join(contract.PRIMARY_FIELDS)+' FROM segments WHERE id=?',(ident,)).fetchone()
            if row is None or row['speaker_local_id'] is not None or pp.digest({k:row[k] for k in contract.PRIMARY_FIELDS if k!='id'})!=expected_row[0]:
                raise ValueError('strict_unknown_row_changed')
    expected=1 if status in ('retained','existing') else 0
    if len(packets)!=expected or len(row_ids)!=len(value.rows):raise ValueError('strict_outcome_cardinality')
    db.execute('INSERT INTO strict_recording_outcome VALUES(?,?,?,?,?,?,?,?)',
        (chunk['id'],value.start,value.end,value.nonce,value.source,key,status,packets[0][0] if packets else None))
    if turn_row_ids is not None:
        # Only the original live Store dispatch supplies the full INSERT-order
        # IDs. The helper rechecks the durable format and prepared carrier;
        # this keyword is not format/placement authority. Any failure rolls
        # back this outcome together with the same original transaction.
        from .strict_payload_v2 import commit_packetless_witness
        commit_packetless_witness(db,chunk,offset,end,result,value,status,row_ids,turn_row_ids,key)
