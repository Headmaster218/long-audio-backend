"""Explicit crop-only human claims, independent of acoustic enrollment.

The review ledger is authoritative; the single current table is a disposable
validated index. Read paths are query-only and never open audio, decode vectors,
load a model, or infer a person. All mutations share the result DB transaction.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import base64
import hashlib
import json
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from . import speaker_support as support, verified_identity as bank_core, identity_canonical
from . import recording_speaker_history as history
from .native_owned_support_cache import ReadResponseBudget, ReadError

TABLE = 'identity_primary_associations_v1'
CLAIM_ACTIONS = ('associate_primary', 'replace_primary', 'retract_primary')
MAX_REQUEST = MAX_EVENT = 8192
MAX_BINDING, MAX_INDEX, MAX_RECEIPT = 4096, 2048, 1024
MAX_PAGE, PAGE_BYTES = 32, 128 * 1024
READ_SECONDS, MAX_VM_STEPS = .75, 250000
ATTESTATION = {'version': 'human_exact_observation_person_claim_v1',
               'extent': 'observed_crop_only', 'confirmed': True}
CAS_FIELDS = {'expected_generation', 'expected_revision', 'idempotency_key'}
CLAIM_FIELDS = CAS_FIELDS | {'source_session_id', 'recording_id', 'observation_id',
    'binding_sha256', 'expected_source_authority_revision', 'expected_source_revision', 'attestation'}
CONTEXT = ContextVar('reviewed_primary_persons_cancel', default=None)
OPERATION = ContextVar('reviewed_primary_persons_operation', default=None)
_DIRECT_BUDGET = ReadResponseBudget()
# Covers direct calls as well as HTTP. No public read lazily repairs an index.
_GATE = threading.Lock()
_ID = re.compile(r'^[a-f0-9]{32}$')
_HASH = re.compile(r'^[a-f0-9]{64}$')

DDL = {
TABLE: f'''CREATE TABLE {TABLE} (
    event_id TEXT PRIMARY KEY REFERENCES identity_review_events(id) ON DELETE CASCADE,
    identity_group TEXT NOT NULL, generation TEXT NOT NULL, person_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL, source_recording_id TEXT NOT NULL,
    scope_id TEXT NOT NULL, observation_id TEXT NOT NULL, parent_ref TEXT NOT NULL,
    physical_crop_key TEXT NOT NULL, binding_sha256 TEXT NOT NULL, space_id TEXT NOT NULL,
    display_ranges TEXT NOT NULL CHECK(length(CAST(display_ranges AS BLOB))<=1024),
    UNIQUE(identity_group,generation,observation_id))''',
'primary_associations_person': f'''CREATE INDEX primary_associations_person ON {TABLE}
    (identity_group,generation,person_id,observation_id)''',
'primary_associations_crop': f'''CREATE INDEX primary_associations_crop ON {TABLE}
    (identity_group,generation,physical_crop_key)''',
'primary_associations_parent': f'''CREATE INDEX primary_associations_parent ON {TABLE}
    (source_session_id,parent_ref)''',
'primary_associations_scope': f'''CREATE INDEX primary_associations_scope ON {TABLE}
    (scope_id,identity_group,generation)''',
'primary_associations_source': f'''CREATE INDEX primary_associations_source ON {TABLE}
    (source_session_id,source_recording_id,observation_id)''',
'review_events_source': '''CREATE INDEX review_events_source ON identity_review_events
    (source_session_id,source_recording_id,identity_group,generation,primary_observation_id)''',
'review_events_observation': '''CREATE INDEX review_events_observation ON identity_review_events
    (identity_group,generation,primary_observation_id)''',
}
_POLICY_COLUMNS = ('identity_group','mode','policy','policy_sha256','space_id','revision','availability',
                   'change_reason','relation_count','relation_head','primary_associations_enabled')
_EVENT_COLUMNS = ('id','identity_group','generation','sequence','action','person_id','support_id',
    'vector_id','evidence_key','fingerprints','attestation','operator_context','prior_prediction',
    'previous_event_id','idempotency_key','request_sha256','response','created','review_kind',
    'source_session_id','source_recording_id','primary_observation_id')


class BridgeError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = self.reason = code
        self.status = self.status_code = status


def fail(code, status=409):
    raise BridgeError(code, status)



def _operation(fn):
    @wraps(fn)
    def run(store,*args,**kwargs):
        if OPERATION.get() is not None:
            return fn(store,*args,**kwargs)
        owner=getattr(store,'_reviewed_person_response_budget',_DIRECT_BUDGET)
        try:lease=owner.reserve_bridge_operation()
        except ReadError as exc:raise BridgeError(exc.reason,exc.status_code) from None
        token=OPERATION.set(lease)
        result=None;failure=None
        try:
            result=fn(store,*args,**kwargs)
            _encode(result,PAGE_BYTES)
        except (BridgeError,ReadError) as exc:
            failure=(exc.reason,exc.status_code)
            exc.__traceback__=None;exc.__context__=None;exc.__cause__=None
        except Exception as exc:
            failure=('reviewed_person_unavailable',503)
            exc.__traceback__=None;exc.__context__=None;exc.__cause__=None
        finally:
            if failure is not None:result=None
            OPERATION.reset(token)
            lease.release()
        if failure is not None:
            # Raise a fresh small exception only after dropping the original
            # DB/canonical stack and request references. Direct callers cannot
            # pin a materialized ledger through an exception after release.
            args=();kwargs={}
            raise BridgeError(*failure) from None
        return result
    return run


def schema_present(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone())


def _columns(db, table):
    return list(db.execute(f'PRAGMA table_info({table})'))


def validate_schema(db):
    """Read-only exact bridge recognizer; never normalize partial/future layouts."""
    norm = lambda value: ' '.join(value.split())
    present = {r['name']:r['sql'] for r in db.execute('SELECT name,sql FROM sqlite_master WHERE name IN (' +
        ','.join('?' for _ in DDL) + ')', tuple(DDL))}
    policy_names = {r['name'] for r in _columns(db,'identity_group_policy')}
    event_names = {r['name'] for r in _columns(db,'identity_review_events')}
    if not present and 'primary_associations_enabled' not in policy_names and not event_names.intersection(_EVENT_COLUMNS[-3:]):
        return False
    unknown = db.execute("SELECT name FROM sqlite_master WHERE name GLOB 'primary_associations_*' OR name GLOB 'identity_primary_associations*'").fetchall()
    if any(row['name'] not in DDL for row in unknown):
        raise ValueError('Incompatible reviewed person schema')
    if set(present) != set(DDL) or any(norm(present[k]) != norm(v) for k,v in DDL.items()):
        raise ValueError('Incompatible reviewed person schema')
    if tuple(r['name'] for r in _columns(db,'identity_group_policy')) != _POLICY_COLUMNS:
        raise ValueError('Incompatible reviewed person schema')
    if tuple(r['name'] for r in _columns(db,'identity_review_events')) != _EVENT_COLUMNS:
        raise ValueError('Incompatible reviewed person schema')
    flag = _columns(db,'identity_group_policy')[-1]
    if flag['type'] != 'INTEGER' or flag['dflt_value'] != '0' or not flag['notnull']:
        raise ValueError('Incompatible reviewed person schema')
    if any(r['type'] != 'TEXT' or r['notnull'] or r['dflt_value'] is not None
           for r in _columns(db,'identity_review_events')[-3:]):
        raise ValueError('Incompatible reviewed person schema')
    return True


def init_schema(db, *, enable=False):
    if not db.in_transaction:
        raise ValueError('Reviewed person migration requires transaction')
    policy_names = {r['name'] for r in _columns(db,'identity_group_policy')}
    event_names = {r['name'] for r in _columns(db,'identity_review_events')}
    markers = db.execute('SELECT 1 FROM sqlite_master WHERE name IN (' +
        ','.join('?' for _ in DDL) + ') LIMIT 1', tuple(DDL)).fetchone()
    if markers or 'primary_associations_enabled' in policy_names or event_names.intersection(_EVENT_COLUMNS[-3:]):
        validate_schema(db)
        # The history recognizer must already have accepted the exact guard.
        present = {r['name']:r['sql'] for r in db.execute('SELECT name,sql FROM sqlite_master')}
        if any(' '.join(present.get(k,'').split()) != ' '.join(v.split()) for k,v in history.BRIDGE_DDL.items()):
            raise ValueError('Incompatible reviewed person schema')
        return
    if not enable:
        return
    if tuple(r['name'] for r in _columns(db,'identity_group_policy')) != _POLICY_COLUMNS[:-1] or tuple(
            r['name'] for r in _columns(db,'identity_review_events')) != _EVENT_COLUMNS[:-3]:
        raise ValueError('Incompatible reviewed person schema')
    db.execute('ALTER TABLE identity_group_policy ADD COLUMN primary_associations_enabled INTEGER NOT NULL DEFAULT 0')
    for name in _EVENT_COLUMNS[-3:]:
        db.execute(f'ALTER TABLE identity_review_events ADD COLUMN {name} TEXT')
    for sql in DDL.values():
        db.execute(sql)
    validate_schema(db)
    history.install_bridge_guard(db)


def _encode(value, maximum=MAX_REQUEST):
    try:
        raw = support.canonical(value)
    except (ValueError, TypeError, OverflowError):
        fail('invalid_request')
    if len(raw) > maximum:
        fail('metadata_capacity', 429)
    return raw


def _decode(raw, maximum):
    if not isinstance(raw, (str, bytes)) or len(raw.encode('utf-8') if isinstance(raw,str) else raw) > maximum:
        fail('metadata_unavailable', 503)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        fail('metadata_unavailable', 503)


def _identifier(value, *, hash_id=False, event=False):
    pattern = _HASH if hash_id else _ID if event else None
    if not isinstance(value,str) or (not pattern and (not 1 <= len(value) <= 64 or not re.fullmatch(r'[A-Za-z0-9_-]+',value))) or (pattern and not pattern.fullmatch(value)):
        fail('invalid_identifier')
    return value


def _group(value):
    if not isinstance(value,str) or not 1<=len(value)<=100 or not re.fullmatch(r'[A-Za-z0-9_.-]+',value):
        fail('invalid_identity_group')
    return value


def _shape(request, fields):
    if type(request) is not dict or set(request) != fields:
        fail('invalid_request_fields')
    _encode(request)


def _operator(store):
    return 'bearer_authenticated_local_api' if store.settings.api_key else 'unauthenticated_local_api'


def _check_enabled(store):
    if not getattr(store.settings, 'reviewed_primary_persons_enabled', False):
        fail('reviewed_primary_persons_disabled', 404)


def _disk(store):
    # Charge conservative SQLite pages, journal/WAL copies and index growth in
    # addition to the actual bounded event, projection and response payloads.
    reserve = getattr(store.settings, 'disk_reserve_bytes', 0)
    try:
        free = shutil.disk_usage(Path(store.path).parent).free
    except OSError:
        fail('storage_unavailable', 503)
    if free < reserve + 1024 * 1024:
        fail('disk_capacity', 429)


@contextmanager
def _db(store, *, write=False):
    _check_enabled(store)
    if not _GATE.acquire(blocking=False):
        fail('read_capacity', 503)
    db = None
    deadline, steps = time.monotonic() + READ_SECONDS, 0
    def check():
        done = CONTEXT.get()
        if done is not None and done.is_set():
            fail('response_closed', 503)
        if time.monotonic() >= deadline or steps >= MAX_VM_STEPS:
            fail('metadata_capacity', 503)
    def progress():
        nonlocal steps
        steps += 100
        try: check()
        except BridgeError: return 1
        return 0
    try:
        if write: _disk(store)
        path = Path(store.path).resolve()
        db = sqlite3.connect('file:' + quote(str(path),safe='/') + ('?mode=rw' if write else '?mode=ro'),uri=True,timeout=0)
        db.row_factory = sqlite3.Row
        db.set_progress_handler(progress,100)
        db.execute('PRAGMA foreign_keys=ON')
        if not write: db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        if not schema_present(db): fail('reviewed_primary_persons_disabled',404)
        # HTTP response owner supplies transient metadata admission; its exact
        # reservation is reviewed separately from durable producer staging.
        yield db, check
        check()
        if write: db.commit()
    except BridgeError:
        raise
    except OverflowError:
        fail('review_capacity',429)
    except KeyError:
        fail('not_found',404)
    except ValueError as exc:
        code = str(exc)
        if code in ('stale_generation','stale_profile_revision','idempotency_conflict','primary_associations_not_enabled',
                'canonical_conflicting_people','duplicate_different_people_constraint',
                'duplicate_or_conflicting_positive_evidence','canonical_contradictory_review'):
            fail(code)
        fail('identity_integrity_unavailable',503)
    except (sqlite3.Error, OSError):
        check()
        fail('storage_unavailable',503)
    finally:
        if db is not None:
            db.set_progress_handler(None,0)
            if db.in_transaction: db.rollback()
            db.close()
        _GATE.release()



def _policy_preflight(db,group):
    # Refuse corrupt wide TEXT/BLOB scalars before group_policy's SELECT *.
    bad=db.execute('''SELECT 1 FROM identity_group_policy WHERE identity_group=? AND (
        typeof(identity_group)!='text' OR length(CAST(identity_group AS BLOB))>100
        OR typeof(mode)!='text' OR length(CAST(mode AS BLOB))>32
        OR typeof(policy)!='text' OR length(CAST(policy AS BLOB))>8192
        OR typeof(policy_sha256)!='text' OR length(CAST(policy_sha256 AS BLOB))!=64
        OR (space_id IS NOT NULL AND (typeof(space_id)!='text' OR length(CAST(space_id AS BLOB))>256))
        OR typeof(revision)!='integer' OR revision<0
        OR typeof(availability)!='integer' OR availability<0
        OR (change_reason IS NOT NULL AND (typeof(change_reason)!='text' OR length(CAST(change_reason AS BLOB))>128))
        OR typeof(relation_count)!='integer' OR relation_count<0 OR relation_count>4096
        OR typeof(relation_head)!='text' OR length(CAST(relation_head AS BLOB)) NOT IN (0,64)
        OR typeof(primary_associations_enabled)!='integer' OR primary_associations_enabled NOT IN (0,1)) LIMIT 1''',
        (group,)).fetchone()
    if bad:fail('identity_policy_unavailable',503)
    bad=db.execute('''SELECT 1 FROM speaker_identity_generations WHERE identity_group=? AND
        (typeof(generation)!='text' OR length(CAST(generation AS BLOB))!=32) LIMIT 1''',(group,)).fetchone()
    if bad:fail('identity_generation_unavailable',503)

def _context(db, group):
    _group(group)
    _policy_preflight(db,group)
    return bank_core.group_context(db, group, require_primary=True)


def _canonical_preflight_rows(db, table, where, args, fields, maximum):
    """Stream bounded scalar metadata before the materialized canonical loader.

    json_array preserves SQLite scalar types (and escapes embedded NUL); BLOBs
    are refused by SQLite rather than decoded as text. SQLite's connection-local
    length limit bounds construction of each JSON result before Python receives
    it. No unbounded source scalar or whole ledger is returned to Python here.
    All fields and SQL fragments are internal constants, never caller strings.
    """
    bound = 32768
    old_limit = db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
    db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, min(old_limit, bound))
    cursor = None
    try:
        columns = ','.join(field[0] for field in fields)
        cursor = db.execute(f'SELECT json_array({columns}) FROM {table} '
                            f'WHERE {where} LIMIT ?', (*args, maximum + 1))
        for count, row in enumerate(cursor, 1):
            if count > maximum:
                fail('canonical_integrity_unavailable', 503)
            raw = row[0]
            if type(raw) is not str or len(raw.encode('utf-8')) > bound:
                fail('canonical_integrity_unavailable', 503)
            values = json.loads(raw)
            if type(values) is not list or len(values) != len(fields):
                fail('canonical_integrity_unavailable', 503)
            for value, (_, kind, lower, upper, nullable) in zip(values, fields):
                if value is None and nullable:
                    continue
                if kind == 'text':
                    if type(value) is not str:
                        fail('canonical_integrity_unavailable', 503)
                    size = len(value.encode('utf-8'))
                    if not lower <= size <= upper:
                        fail('canonical_integrity_unavailable', 503)
                    if lower == -1 and size not in (0, upper):
                        fail('canonical_integrity_unavailable', 503)
                elif kind == 'integer':
                    if type(value) is not int or value < lower:
                        fail('canonical_integrity_unavailable', 503)
                elif type(value) not in (int, float):
                    fail('canonical_integrity_unavailable', 503)
    except sqlite3.DataError:
        # SQLITE_TOOBIG is a refused malformed stored scalar, not a relaxed cap.
        fail('canonical_integrity_unavailable', 503)
    except sqlite3.OperationalError as exc:
        if str(exc) == 'JSON cannot hold BLOB values':
            fail('canonical_integrity_unavailable', 503)
        raise  # Preserve interruption, storage errors and the outer budget check.
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, old_limit)


def _canonical_preflight(db,group,generation):
    # Exact former SQL type/UTF-8 byte bounds, applied to one capped JSON row
    # at a time. At most 3,556 legal relation text bytes can expand sixfold for
    # JSON control escapes; scalars/punctuation keep the result below 32 KiB.
    # The unchanged loader later reads original values, not JSON roundtrips.
    _canonical_preflight_rows(db, 'identity_relation_events',
        'identity_group=? AND generation=?', (group,generation), (
            ('response','text',0,2048,False),
            ('operator_context','text',0,512,False),
            ('idempotency_key','text',0,512,False),
            ('id','text',32,32,False),
            ('person_id','text',32,32,False),
            ('other_person_id','text',32,32,False),
            ('generation','text',32,32,False),
            ('identity_group','text',0,100,False),
            ('action','text',0,32,False),
            ('request_sha256','text',64,64,False),
            ('event_sha256','text',64,64,False),
            ('previous_sha256','text',-1,64,False),
            ('sequence','integer',1,None,False),
            ('cursor','integer',1,None,False),
            ('created','number',None,None,False),
            ('previous_event_id','text',32,32,True),
        ), identity_canonical.MAX_EVENTS)
    _canonical_preflight_rows(db, 'speakers', 'identity_group=?', (group,), (
        ('id','text',32,32,False),
        ('state','text',0,32,False),
        ('name','text',0,800,True),
        ('merged_into','text',32,32,True),
    ), bank_core.MAX_PERSONS)


def _canonical(db, group, generation, policy):
    _canonical_preflight(db,group,generation)
    return identity_canonical.load(db, group, generation, policy)


def _retry_preflight(db,group,generation,key):
    if not isinstance(key,str) or not 1<=len(key)<=128:fail('invalid_idempotency_key')
    for table,maximum in (('identity_review_events',MAX_EVENT),('identity_relation_events',2048)):
        bad=db.execute(f'''SELECT 1 FROM {table} WHERE identity_group=? AND generation=? AND idempotency_key=?
            AND (typeof(response)!='text' OR length(CAST(response AS BLOB))>?
                OR typeof(action)!='text' OR length(CAST(action AS BLOB))>32
                OR typeof(request_sha256)!='text' OR length(CAST(request_sha256 AS BLOB))!=64) LIMIT 1''',
            (group,generation,key,maximum)).fetchone()
        if bad:fail('retry_metadata_unavailable',503)


def _request(db, group, generation, policy, request, action, previous=None):
    if request.get('expected_generation')!=generation:fail('stale_generation')
    _retry_preflight(db,group,generation,request.get('idempotency_key'))
    hashed,retry=bank_core._request(db,group,generation,policy,request,action,previous)
    if retry is None:_revision_room(policy)
    return hashed,retry



def _revision_room(policy):
    if (type(policy['revision']) is not int or type(policy['availability']) is not int
            or not 0<=policy['revision']<9223372036854775807
            or not 0<=policy['availability']<9223372036854775807):
        fail('identity_revision_unavailable',503)

def _person_fields(db, group, person, state, generation, revision):
    row = db.execute('''SELECT id,name,state FROM speakers WHERE id=? AND identity_group=?
        AND state IN ('reviewed_reference','reviewed_person') AND merged_into IS NULL''',(person,group)).fetchone()
    if row is None: fail('person_not_found',404)
    root = state['mapping'].get(person) if state else None
    positives = db.execute("SELECT COUNT(*) FROM identity_active_reviews WHERE identity_group=? AND person_id=? AND kind='positive'",(group,person)).fetchone()[0]
    return {'person_id':person,'name':row['name'],'person_state':row['state'],
        'canonical_person_id':root,'canonical_revision':revision,'generation':generation,
        'active_positive_count':positives,'acoustic_reference_status':'available' if positives else 'none',
        'acoustic_support_certified':False,'operator_assertion_is_claim':True,'extent':'observed_crop_only'}


def _insert_event(db, group, generation, revision, action, request, hashed, response, *, event_id,
                  person=None, previous=None, binding=None, source=None, attestation=None,
                  operator_context='local_api_human_claim'):
    event = dict(id=event_id,identity_group=group,generation=generation,sequence=revision,action=action,
        person_id=person,support_id=None,vector_id=None,evidence_key=None,
        fingerprints=_encode(binding,MAX_BINDING).decode() if binding is not None else None,
        attestation=_encode(attestation).decode() if attestation is not None else None,
        operator_context=operator_context,prior_prediction=None,previous_event_id=previous,
        idempotency_key=request['idempotency_key'],request_sha256=hashed,
        response=_encode(response,MAX_EVENT).decode(),created=time.time(),review_kind=None,
        source_session_id=source[0] if source else None,source_recording_id=source[1] if source else None,
        primary_observation_id=source[2] if source else None)
    _encode(event,
            MAX_RECEIPT if action=='erase_primary' else MAX_EVENT)
    db.execute(f'INSERT INTO identity_review_events ({",".join(event)}) VALUES ({",".join("?" for _ in event)})',tuple(event.values()))


def _binding(db, sid, rid, observation_id):
    _identifier(sid); _identifier(rid); _identifier(observation_id,hash_id=True)
    try:
        result=history.binding(db,sid,rid,observation_id)
    except history.ReadError as exc:
        raise BridgeError(exc.reason,exc.status_code) from None
    if result['binding'] is not None: _encode(result['binding'],MAX_BINDING)
    return result


def _projection(row, binding):
    b=binding['binding']
    return dict(event_id=row['id'],identity_group=row['identity_group'],generation=row['generation'],
        person_id=row['person_id'],source_session_id=row['source_session_id'],
        source_recording_id=row['source_recording_id'],scope_id=b['scope_id'],
        observation_id=row['primary_observation_id'],parent_ref=b['parent_ref'],
        physical_crop_key=binding['physical_crop_key'],binding_sha256=binding['binding_sha256'],
        space_id=b['space_id'],display_ranges=_encode(b['logical_crop_ranges'],1024).decode())


_EXPECTED = '''SELECT e.* FROM identity_review_events e INDEXED BY review_events_generation
    WHERE e.identity_group=? AND e.generation=? AND e.action IN ('associate_primary','replace_primary')
    AND NOT EXISTS(SELECT 1 FROM identity_review_events r INDEXED BY review_events_previous
        WHERE r.previous_event_id=e.id AND r.identity_group=e.identity_group AND r.generation=e.generation
        AND r.action IN ('replace_primary','retract_primary'))'''


def projection_intact(db,group,generation):
    """Both anti-joins precede result filters, including empty occurrence reads."""
    mismatch=' OR '.join(f'e.{e} IS NOT a.{a}' for e,a in (
        ('identity_group','identity_group'),('generation','generation'),('person_id','person_id'),
        ('source_session_id','source_session_id'),('source_recording_id','source_recording_id'),
        ('primary_observation_id','observation_id')))
    # Fingerprints are the authoritative immutable bounded binding, not vectors.
    bad=db.execute(f'''SELECT 1 FROM ({_EXPECTED}) e LEFT JOIN {TABLE} a ON a.event_id=e.id
        LEFT JOIN speakers p ON p.id=e.person_id WHERE a.event_id IS NULL OR {mismatch}
        OR p.id IS NULL OR p.identity_group IS NOT e.identity_group OR p.merged_into IS NOT NULL
        OR p.state NOT IN ('reviewed_reference','reviewed_person')
        OR e.source_session_id IS NULL OR e.source_recording_id IS NULL OR e.primary_observation_id IS NULL
        OR length(CAST(e.fingerprints AS BLOB))>4096 OR length(CAST(e.response AS BLOB))>8192
        OR NOT json_valid(e.fingerprints) OR NOT json_valid(e.response)
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.scope_id') END IS NOT a.scope_id
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.parent_ref') END IS NOT a.parent_ref
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.space_id') END IS NOT a.space_id
        OR CASE WHEN json_valid(e.response) THEN json_extract(e.response,'$.binding_sha256') END IS NOT a.binding_sha256
        OR CASE WHEN json_valid(e.response) THEN json_extract(e.response,'$.physical_crop_key') END IS NOT a.physical_crop_key
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.logical_crop_ranges') END IS NOT a.display_ranges
        LIMIT 1''',(group,generation)).fetchone()
    if bad:return False
    bad=db.execute(f'''SELECT 1 FROM {TABLE} a INDEXED BY primary_associations_person
        LEFT JOIN identity_review_events e ON e.id=a.event_id WHERE a.identity_group=? AND
        (e.id IS NULL OR e.identity_group IS NOT ? OR e.generation IS NOT ? OR a.generation IS NOT ?
        OR e.action NOT IN ('associate_primary','replace_primary') OR EXISTS (
            SELECT 1 FROM identity_review_events r INDEXED BY review_events_previous WHERE r.previous_event_id=e.id
            AND r.identity_group=e.identity_group AND r.generation=e.generation
            AND r.action IN ('replace_primary','retract_primary'))) LIMIT 1''',(group,group,generation,generation)).fetchone()
    # Every source-bearing edge must stay in the same indexed observation tuple.
    cross=db.execute('''SELECT 1 FROM identity_review_events e INDEXED BY review_events_generation
        LEFT JOIN identity_review_events p ON p.id=e.previous_event_id
        WHERE e.identity_group=? AND e.generation=? AND e.action IN ('replace_primary','retract_primary')
        AND (p.id IS NULL OR p.action NOT IN ('associate_primary','replace_primary')
        OR p.identity_group IS NOT e.identity_group OR p.generation IS NOT e.generation
        OR p.source_session_id IS NOT e.source_session_id OR p.source_recording_id IS NOT e.source_recording_id
        OR p.primary_observation_id IS NOT e.primary_observation_id) LIMIT 1''',(group,generation)).fetchone()
    return not bad and not cross



def _projection_preflight(db,group,generation):
    columns=('event_id','identity_group','generation','person_id','source_session_id','source_recording_id',
             'scope_id','observation_id','parent_ref','physical_crop_key','binding_sha256','space_id','display_ranges')
    types=' OR '.join(f"typeof({column})!='text'" for column in columns)
    lengths='+'.join(f'length(CAST({column} AS BLOB))' for column in columns)
    if db.execute(f'''SELECT 1 FROM {TABLE} INDEXED BY primary_associations_person
        WHERE identity_group=? AND ({types} OR {lengths}>?) LIMIT 1''',(group,MAX_INDEX)).fetchone():
        fail('association_integrity_unavailable',503)
    # Include retracted history: no oversized key/owner can be fetched by a page
    # merely because it has no current projection.
    bad=db.execute('''SELECT 1 FROM identity_review_events INDEXED BY review_events_generation
        WHERE identity_group=? AND generation=? AND action IN ('associate_primary','replace_primary','retract_primary')
        AND (typeof(id)!='text' OR length(CAST(id AS BLOB))!=32 OR typeof(person_id)!='text' OR length(CAST(person_id AS BLOB))!=32
        OR typeof(source_session_id)!='text' OR length(CAST(source_session_id AS BLOB)) NOT BETWEEN 1 AND 64
        OR typeof(source_recording_id)!='text' OR length(CAST(source_recording_id AS BLOB)) NOT BETWEEN 1 AND 64
        OR typeof(primary_observation_id)!='text' OR length(CAST(primary_observation_id AS BLOB))!=64
        OR typeof(operator_context)!='text' OR length(CAST(operator_context AS BLOB))>512
        OR typeof(idempotency_key)!='text' OR length(CAST(idempotency_key AS BLOB))>512
        OR typeof(request_sha256)!='text' OR length(CAST(request_sha256 AS BLOB))!=64
        OR typeof(sequence)!='integer' OR sequence<1 OR typeof(created) NOT IN ('integer','real')
        OR typeof(fingerprints)!='text' OR length(CAST(fingerprints AS BLOB))>4096
        OR typeof(response)!='text' OR length(CAST(response AS BLOB))>8192
        OR (attestation IS NOT NULL AND (typeof(attestation)!='text' OR length(CAST(attestation AS BLOB))>1024))
        OR (previous_event_id IS NOT NULL AND (typeof(previous_event_id)!='text' OR length(CAST(previous_event_id AS BLOB))!=32))
        OR support_id IS NOT NULL OR vector_id IS NOT NULL OR evidence_key IS NOT NULL
        OR review_kind IS NOT NULL OR prior_prediction IS NOT NULL) LIMIT 1''',(group,generation)).fetchone()
    if bad:fail('association_integrity_unavailable',503)

def _require_projection(db,group,generation):
    _projection_preflight(db,group,generation)
    if not projection_intact(db,group,generation): fail('association_integrity_unavailable',503)


def _bank_response(db,group,generation,policy):
    counts={'persons':db.execute('SELECT COUNT(*) FROM speakers WHERE identity_group=?',(group,)).fetchone()[0],
        'active_associations':db.execute(f'SELECT COUNT(*) FROM {TABLE} WHERE identity_group=? AND generation=?',(group,generation)).fetchone()[0],
        'review_events':db.execute('SELECT COUNT(*) FROM identity_review_events WHERE identity_group=? AND generation=?',(group,generation)).fetchone()[0]}
    for kind,key in [('positive','active_positives'),('negative','active_negatives')]:
        counts[key]=db.execute('SELECT COUNT(*) FROM identity_active_reviews WHERE identity_group=? AND generation=? AND kind=?',(group,generation,kind)).fetchone()[0]
    return {'identity_group':group,'identity_mode':bank_core.MODE,'generation':generation,
        'profile_revision':policy['revision'],'canonical_revision':policy['revision'],
        'availability_sequence':policy['availability'],'policy_sha256':policy['policy_sha256'],'space_id':policy['space_id'],
        'explicitly_retained':True,'primary_associations_enabled':True,'automatic_enrollment':False,
        'acoustic_support_certified':False,'operator_assertion_is_claim':True,'counts':counts,
        'shared_event_capacity':bank_core.MAX_EVENTS,'reserved_retractions':bank_core.active_reservations(db),
        'limits':{'persons_global':bank_core.MAX_PERSONS,'events_global':bank_core.MAX_EVENTS,
            'active_positives_global':bank_core.MAX_POSITIVES,'active_negatives_global':bank_core.MAX_NEGATIVES,
            'positives_per_person':bank_core.MAX_PER_PERSON,'page_items':MAX_PAGE,'page_bytes':PAGE_BYTES},
        'retention':'Person catalog and bank generation survive source deletion until explicit identity reset.',
        'access_boundary':'existing optional bearer or local API; explicit group scope is not a separate ACL',
        'scope':'human exact observed crop claim; no listening, acoustic purity, or whole-parent certification'}


@_operation
def bank(store,group):
    with _db(store) as (db,check):
        group,generation,policy=_context(db,group)
        _require_projection(db,group,generation)
        return _bank_response(db,group,generation,policy)


@_operation
def activate_bank(store,group,request):
    _group(group)
    _shape(request,CAS_FIELDS|{'mode','policy_sha256','confirmed'})
    if type(request['expected_revision']) is not int or request['expected_revision'] < 0:
        fail('invalid_profile_revision')
    if request['mode']!=bank_core.MODE or request['confirmed'] is not True or not _HASH.fullmatch(str(request['policy_sha256'])):
        fail('invalid_bank_activation')
    with _db(store,write=True) as (db,check):
        _policy_preflight(db,group)
        policy=bank_core.group_policy(db,group)
        if policy is None:
            if request['expected_generation'] is not None or request['expected_revision']!=0:
                fail('stale_generation')
            if db.execute('SELECT 1 FROM sessions WHERE identity_group=? UNION ALL SELECT 1 FROM speakers WHERE identity_group=? LIMIT 1',(group,group)).fetchone():
                fail('legacy_group_conflict')
            configured=getattr(store.settings,'verified_identity_policy',None)
            if configured is None: fail('explicit_policy_required')
            raw=_encode(configured)
            if support.digest(raw)!=request['policy_sha256']: fail('policy_mismatch')
            if db.execute('SELECT COUNT(*) FROM identity_group_policy').fetchone()[0]>=bank_core.MAX_GROUPS:fail('group_capacity',429)
            # Validate generic key before minting a generation. All changes roll back together.
            if not isinstance(request['idempotency_key'],str) or not 1<=len(request['idempotency_key'])<=128:fail('invalid_idempotency_key')
            bank_core.event_capacity(db)
            db.execute('INSERT INTO identity_group_policy(identity_group,mode,policy,policy_sha256) VALUES(?,?,?,?)',
                (group,bank_core.MODE,raw.decode(),request['policy_sha256']))
            generation=support.generation(db,group)
            policy=bank_core.group_policy(db,group)
            hashed=support.digest(_encode({'action':'enable_primary_associations','previous':None,'request':request}))
        else:
            if policy['mode']!=bank_core.MODE:fail('legacy_group_conflict')
            current=db.execute('SELECT generation FROM speaker_identity_generations WHERE identity_group=?',(group,)).fetchone()
            if current is None:fail('identity_integrity_unavailable',503)
            generation=current['generation']
            # NULL generation has precisely one exception: an exact current-generation create retry.
            if request['expected_generation'] is None:
                _retry_preflight(db,group,generation,request['idempotency_key'])
                hashed=support.digest(_encode({'action':'enable_primary_associations','previous':None,'request':request}))
                prior=db.execute('''SELECT action,request_sha256,response FROM identity_review_events
                    WHERE identity_group=? AND generation=? AND idempotency_key=?''',(group,generation,request['idempotency_key'])).fetchone()
                if prior and prior['action']=='enable_primary_associations' and prior['request_sha256']==hashed:
                    return _decode(prior['response'],MAX_EVENT)
                fail('stale_generation')
            hashed,retry=_request(db,group,generation,policy,request,'enable_primary_associations')
            if retry is not None:return retry
            if request['policy_sha256']!=policy['policy_sha256']:fail('policy_mismatch')
            if policy['primary_associations_enabled']:fail('primary_associations_already_enabled')
            bank_core.event_capacity(db)
        event_id=uuid.uuid4().hex
        db.execute('UPDATE identity_group_policy SET primary_associations_enabled=1 WHERE identity_group=?',(group,))
        bank_core._invalidate(db,group,'primary_associations_enabled')
        response=_bank_response(db,group,generation,bank_core.group_policy(db,group))
        response.update(event_id=event_id,action='enable_primary_associations')
        # The event itself counts in the response without a second invalidation.
        response['counts']['review_events']+=1
        _insert_event(db,group,generation,policy['revision']+1,'enable_primary_associations',request,hashed,response,event_id=event_id,operator_context=_operator(store))
        return response


def _validate_claim(request):
    if type(request) is not dict:fail('invalid_request_fields')
    owner={'person_id'} if 'person_id' in request else {'new_person_name'}
    _shape(request,CLAIM_FIELDS|owner)
    if request['attestation'] != ATTESTATION or type(request['attestation'].get('confirmed')) is not bool:fail('invalid_attestation')
    if 'person_id' in owner:_identifier(request['person_id'],event=True)
    elif not isinstance(request['new_person_name'],str) or not 1<=len(request['new_person_name'])<=200:fail('invalid_person_name')
    for key in ('expected_source_authority_revision','expected_source_revision'):
        if type(request[key]) is not int or request[key]<0:fail('invalid_source_revision')
    _identifier(request['binding_sha256'],hash_id=True)


@_operation
def mutate(store,group,request,*,previous=None,retract=False):
    if retract:_shape(request,CAS_FIELDS)
    else:_validate_claim(request)
    if previous is not None:_identifier(previous,event=True)
    if retract and previous is None:fail('active_predecessor_required')
    action='retract_primary' if retract else 'replace_primary' if previous else 'associate_primary'
    with _db(store,write=True) as (db,check):
        group,generation,policy=_context(db,group)
        hashed,retry=_request(db,group,generation,policy,request,action,previous)
        if retry is not None:return retry
        _require_projection(db,group,generation)
        old=None
        if previous:
            old=db.execute(f'''SELECT a.*,e.fingerprints FROM {TABLE} a JOIN identity_review_events e ON e.id=a.event_id
                WHERE a.event_id=? AND a.identity_group=? AND a.generation=?''',(previous,group,generation)).fetchone()
            if old is None:fail('active_predecessor_required')
        state=_canonical(db,group,generation,policy)
        if retract:
            person=old['person_id']; b=_decode(old['fingerprints'],MAX_BINDING)
            source=(old['source_session_id'],old['source_recording_id'],old['observation_id'])
            binding_hash=old['binding_sha256']; crop_key=old['physical_crop_key']
            bank_core.event_capacity(db,removed_active=1)
        else:
            source=(request['source_session_id'],request['recording_id'],request['observation_id'])
            if old and source!=(old['source_session_id'],old['source_recording_id'],old['observation_id']):fail('observation_replacement_forbidden')
            bound=_binding(db,*source)
            if not bound['review_eligible']:fail(bound['reason'] or 'observation_not_eligible')
            if request['binding_sha256']!=bound['binding_sha256']:fail('binding_changed')
            if (request['expected_source_authority_revision']!=bound['source_authority_revision'] or
                    request['expected_source_revision']!=bound['source_revision']):fail('stale_source_revision')
            if not old and db.execute(f'SELECT 1 FROM {TABLE} WHERE identity_group=? AND generation=? AND observation_id=?',(group,generation,source[2])).fetchone():fail('observation_already_associated')
            person=request.get('person_id')
            if person:
                _person_fields(db,group,person,state,generation,policy['revision'])
            else:
                if db.execute("SELECT COUNT(*) FROM speakers WHERE state IN ('reviewed_reference','reviewed_person')").fetchone()[0]>=bank_core.MAX_PERSONS:fail('person_capacity',429)
                person=uuid.uuid4().hex
            root=identity_canonical.review_root(state,person)
            for alias in db.execute(f'''SELECT person_id,event_id FROM {TABLE} INDEXED BY primary_associations_crop
                    WHERE identity_group=? AND generation=? AND physical_crop_key=?''',(group,generation,bound['physical_crop_key'])):
                if alias['event_id']!=previous and identity_canonical.review_root(state,alias['person_id'])!=root:fail('conflicting_exact_crop_claims')
            bank_core.event_capacity(db,new_active=1,removed_active=int(old is not None))
            b=bound['binding']; binding_hash=bound['binding_sha256']; crop_key=bound['physical_crop_key']
            if 'new_person_name' in request:
                db.execute("INSERT INTO speakers(id,session_id,identity_group,name,embedding,state,created) VALUES(?,'',?,?,NULL,'reviewed_person',?)",
                    (person,group,request['new_person_name'],time.time()))
                state['mapping'][person]=person
        event_id=uuid.uuid4().hex
        response={**_person_fields(db,group,person,state,generation,policy['revision']+1),
            'event_id':event_id,'action':action,'previous_event_id':previous,'profile_revision':policy['revision']+1,
            'binding':b,'binding_sha256':binding_hash,'physical_crop_key':crop_key,
            'source_session_id':source[0],'recording_id':source[1],'observation_id':source[2],
            'active':not retract,'authority':'human_observation_claim','enrollment_allowed':False,
            'erasure_scope':'all_claim_history_for_exact_observation'}
        _insert_event(db,group,generation,policy['revision']+1,action,request,hashed,response,event_id=event_id,
            person=person,previous=previous,binding=b,source=source,attestation=None if retract else ATTESTATION,
            operator_context=_operator(store))
        if old:db.execute(f'DELETE FROM {TABLE} WHERE event_id=?',(previous,))
        if not retract:
            row=dict(id=event_id,identity_group=group,generation=generation,person_id=person,
                source_session_id=source[0],source_recording_id=source[1],primary_observation_id=source[2])
            projection=_projection(row,bound)
            _encode(projection,MAX_INDEX)
            db.execute(f'INSERT INTO {TABLE} ({",".join(projection)}) VALUES ({",".join("?" for _ in projection)})',tuple(projection.values()))
        bank_core._invalidate(db,group,'primary_association_changed')
        return response


def _erase_tuple(db,group,generation,observation_id,source=None):
    """Indexed complete observation history erasure; no chain/JSON materialization."""
    where='identity_group=? AND generation=? AND primary_observation_id=?'
    args=(group,generation,observation_id)
    if source is not None and db.execute(f'''SELECT 1 FROM identity_review_events INDEXED BY review_events_observation
        WHERE {where} AND action IN ('associate_primary','replace_primary','retract_primary')
        AND (source_session_id IS NOT ? OR source_recording_id IS NOT ?) LIMIT 1''',args+tuple(source)).fetchone():
        fail('association_integrity_unavailable',503)
    # No surviving source-bearing edge may depend on any removed event.
    bad=db.execute(f'''SELECT 1 FROM identity_review_events e INDEXED BY review_events_observation
        JOIN identity_review_events r INDEXED BY review_events_previous ON r.previous_event_id=e.id
        WHERE e.{where.replace(' AND ',' AND e.')} AND e.action IN ('associate_primary','replace_primary','retract_primary')
        AND r.action!='erase_primary' AND (r.identity_group IS NOT e.identity_group OR r.generation IS NOT e.generation
            OR r.primary_observation_id IS NOT e.primary_observation_id
            OR r.action NOT IN ('associate_primary','replace_primary','retract_primary')) LIMIT 1''',args).fetchone()
    if bad:fail('association_integrity_unavailable',503)
    db.execute(f'''DELETE FROM {TABLE} WHERE (identity_group=? AND generation=? AND observation_id=?)
        OR event_id IN (SELECT id FROM identity_review_events INDEXED BY review_events_observation
            WHERE {where} AND action IN ('associate_primary','replace_primary','retract_primary'))''',args+args)
    changed=db.execute(f'''DELETE FROM identity_review_events WHERE id IN (
        SELECT id FROM identity_review_events INDEXED BY review_events_observation WHERE {where}
        AND action IN ('associate_primary','replace_primary','retract_primary'))''',args).rowcount
    if not changed:fail('association_not_found',404)
    return changed


@_operation
def erase(store,group,event_id,request):
    _identifier(event_id,event=True)
    _shape(request,CAS_FIELDS|{'scope','confirmed'})
    if request['scope']!='all_claim_history_for_exact_observation' or request['confirmed'] is not True:
        fail('invalid_erasure_confirmation')
    try:
        key=uuid.UUID(request['idempotency_key'])
        if str(key)!=request['idempotency_key'] or key.version!=4 or key.variant!=uuid.RFC_4122:raise ValueError()
    except (ValueError,TypeError,AttributeError):fail('erasure_uuid4_required')
    with _db(store,write=True) as (db,check):
        group,generation,policy=_context(db,group)
        hashed,retry=_request(db,group,generation,policy,request,'erase_primary',event_id)
        if retry is not None:return retry
        bad=db.execute('''SELECT 1 FROM identity_review_events WHERE id=? AND identity_group=? AND generation=?
            AND action IN ('associate_primary','replace_primary','retract_primary') AND (
            typeof(primary_observation_id)!='text' OR length(CAST(primary_observation_id AS BLOB))!=64
            OR typeof(source_session_id)!='text' OR length(CAST(source_session_id AS BLOB)) NOT BETWEEN 1 AND 64
            OR typeof(source_recording_id)!='text' OR length(CAST(source_recording_id AS BLOB)) NOT BETWEEN 1 AND 64) LIMIT 1''',
            (event_id,group,generation)).fetchone()
        if bad:fail('association_integrity_unavailable',503)
        target=db.execute('''SELECT primary_observation_id,source_session_id,source_recording_id FROM identity_review_events
            WHERE id=? AND identity_group=? AND generation=? AND action IN ('associate_primary','replace_primary','retract_primary')''',
            (event_id,group,generation)).fetchone()
        if target is None:fail('association_not_found',404)
        if any(target[k] is None for k in target.keys()):fail('association_integrity_unavailable',503)
        # No canonical resolution, live source lookup or metadata binding required.
        _erase_tuple(db,group,generation,target['primary_observation_id'],
            (target['source_session_id'],target['source_recording_id']))
        # Net history removes >=1 row, inserts one receipt, releases any active reserve.
        bank_core.event_capacity(db)
        erasure_id=uuid.uuid4().hex
        response={'erasure_event_id':erasure_id,'target_event_id':event_id,'generation':generation,
            'profile_revision':policy['revision']+1,'status':'erased'}
        _insert_event(db,group,generation,policy['revision']+1,'erase_primary',request,hashed,response,
            event_id=erasure_id,previous=event_id,operator_context='local_api_association_erasure')
        bank_core._invalidate(db,group,'primary_association_erased')
        return response


def erase_source(db,sid,rid=None):
    """Caller-owned source erasure transaction; intentionally no per-claim receipt."""
    if not schema_present(db):return
    if not db.in_transaction:raise ValueError('Source erasure requires transaction')
    predicate='source_session_id=?'+(' AND source_recording_id=?' if rid is not None else '')
    args=(sid,rid) if rid is not None else (sid,)
    for table,observation in (('identity_review_events','primary_observation_id'),(TABLE,'observation_id')):
        actions=" AND action IN ('associate_primary','replace_primary','retract_primary')" if table=='identity_review_events' else ''
        bad=db.execute(f'''SELECT 1 FROM {table} WHERE {predicate}{actions} AND (
            typeof(identity_group)!='text' OR length(CAST(identity_group AS BLOB)) NOT BETWEEN 1 AND 100
            OR typeof(generation)!='text' OR length(CAST(generation AS BLOB))!=32
            OR typeof({observation})!='text' OR length({observation})!=64
            OR typeof(source_recording_id)!='text' OR length(CAST(source_recording_id AS BLOB)) NOT BETWEEN 1 AND 64) LIMIT 1''',args).fetchone()
        if bad:fail('association_integrity_unavailable',503)
    # Resolve complete tuple scopes from authoritative history AND projection.
    # Page keys only; every branch/reassociation is erased via the same primitive.
    groups=set(); deadline=time.monotonic()+READ_SECONDS
    while True:
        if time.monotonic()>=deadline:fail('metadata_capacity',503)
        done=CONTEXT.get()
        if done is not None and done.is_set():fail('response_closed',503)
        tuples=db.execute(f'''SELECT identity_group,generation,primary_observation_id AS observation_id
            FROM identity_review_events INDEXED BY review_events_source WHERE {predicate}
            AND action IN ('associate_primary','replace_primary','retract_primary')
            UNION SELECT identity_group,generation,observation_id FROM {TABLE}
            INDEXED BY primary_associations_source WHERE {predicate}
            ORDER BY identity_group,generation,observation_id LIMIT 32''',args+args).fetchall()
        if not tuples:break
        for key in tuples:
            group,generation,observation=key
            if not all(isinstance(v,str) for v in key):fail('association_integrity_unavailable',503)
            target=db.execute('''SELECT source_session_id,source_recording_id FROM identity_review_events
                INDEXED BY review_events_observation WHERE identity_group=? AND generation=? AND primary_observation_id=?
                AND action IN ('associate_primary','replace_primary','retract_primary') LIMIT 1''',
                (group,generation,observation)).fetchone()
            if target is None or target['source_session_id']!=sid or (rid is not None and target['source_recording_id']!=rid):
                fail('association_integrity_unavailable',503)
            _erase_tuple(db,group,generation,observation,(target['source_session_id'],target['source_recording_id']))
            groups.add(group)
            if len(groups)>bank_core.MAX_GROUPS:fail('association_integrity_unavailable',503)
    for group in groups:bank_core._invalidate(db,group,'primary_association_source_erased')


def invalidate_scope(db,scope_id,reason='primary_association_source_changed'):
    if schema_present(db):
        groups=db.execute(f'''SELECT DISTINCT identity_group FROM {TABLE}
            INDEXED BY primary_associations_scope WHERE scope_id=? LIMIT ?''',
            (scope_id,bank_core.MAX_GROUPS+1)).fetchall()
        if len(groups)>bank_core.MAX_GROUPS:fail('association_integrity_unavailable',503)
        for group in groups:bank_core._invalidate(db,group['identity_group'],reason)


def _page_limit(limit):
    if type(limit) is not int or not 1<=limit<=MAX_PAGE:fail('invalid_page_limit')


def _cursor(group,generation,policy,kind,filters,last):
    body={'g':group,'v':generation,'r':policy['revision'],'k':kind,'f':filters,'l':last}
    raw=_encode(body,1536)
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _seek(cursor,group,generation,policy,kind,filters,default):
    if cursor is None:return default
    if not isinstance(cursor,str) or len(cursor)>2048 or not re.fullmatch(r'[A-Za-z0-9_-]+',cursor):fail('invalid_cursor')
    try:body=_decode(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)),1536)
    except (BridgeError,ValueError,TypeError):fail('invalid_cursor')
    if type(body) is not dict or set(body)!={'g','v','r','k','f','l'}:fail('invalid_cursor')
    if body['g']!=group or body['v']!=generation or body['r']!=policy['revision'] or body['k']!=kind or body['f']!=filters:
        fail('stale_cursor')
    if type(body['l']) is not type(default):fail('invalid_cursor')
    return body['l']


def _page(items,group,generation,policy,kind,filters,last,has_more,*,complete=True):
    result={'items':items,'generation':generation,'profile_revision':policy['revision'],
        'next_cursor':_cursor(group,generation,policy,kind,filters,last) if has_more else None,
        'has_more':has_more,'complete':complete}
    _encode(result,PAGE_BYTES)
    return result


def _event_row(db,event_id,group,generation):
    row=db.execute('''SELECT
        CASE WHEN typeof(response)='text' AND length(CAST(response AS BLOB))<=8192
        AND typeof(fingerprints)='text' AND length(CAST(fingerprints AS BLOB))<=4096
        AND (attestation IS NULL OR (typeof(attestation)='text' AND length(CAST(attestation AS BLOB))<=1024))
        AND typeof(person_id)='text' AND length(CAST(person_id AS BLOB))=32
        AND typeof(source_session_id)='text' AND length(CAST(source_session_id AS BLOB)) BETWEEN 1 AND 64
        AND typeof(source_recording_id)='text' AND length(CAST(source_recording_id AS BLOB)) BETWEEN 1 AND 64
        AND typeof(primary_observation_id)='text' AND length(CAST(primary_observation_id AS BLOB))=64
        AND typeof(operator_context)='text' AND length(CAST(operator_context AS BLOB))<=512
        AND typeof(idempotency_key)='text' AND length(CAST(idempotency_key AS BLOB))<=512
        AND typeof(request_sha256)='text' AND length(CAST(request_sha256 AS BLOB))=64
        AND (previous_event_id IS NULL OR (typeof(previous_event_id)='text' AND length(CAST(previous_event_id AS BLOB))=32))
        AND typeof(sequence)='integer' AND sequence>0 AND typeof(created) IN ('integer','real')
        AND support_id IS NULL AND vector_id IS NULL AND evidence_key IS NULL AND review_kind IS NULL
        AND prior_prediction IS NULL THEN 1 ELSE 0 END AS bounded
        FROM identity_review_events WHERE id=? AND identity_group=? AND generation=?
        AND action IN ('associate_primary','replace_primary','retract_primary')''',(event_id,group,generation)).fetchone()
    if row is None:fail('association_not_found',404)
    if not row['bounded']:fail('metadata_unavailable',503)
    return db.execute('SELECT * FROM identity_review_events WHERE id=?',(event_id,)).fetchone()


def _project_event(db,row,policy,state):
    person=row['person_id']; group=row['identity_group']; generation=row['generation']
    b=_decode(row['fingerprints'],MAX_BINDING)
    saved=_decode(row['response'],MAX_EVENT)
    if support.digest(_encode(b,MAX_BINDING))!=saved.get('binding_sha256'):fail('association_integrity_unavailable',503)
    current=db.execute(f'SELECT * FROM {TABLE} WHERE event_id=?',(row['id'],)).fetchone()
    active=current is not None
    status='binding_stale'; source_state='unavailable'; valid=False
    try:
        bound=_binding(db,row['source_session_id'],row['source_recording_id'],row['primary_observation_id'])
        source_state=bound['source_state']
        if source_state in ('revoked','source_revoked'):status='source_revoked'
        elif bound['binding_sha256']==saved.get('binding_sha256') and bound['review_eligible']:
            valid=True; status='source_retired' if source_state in ('retired','source_retired') else 'human_associated'
    except BridgeError as exc:
        if exc.status not in (404,409):raise
    root=state['mapping'].get(person) if state else None
    if root is None:valid=False; status='canonical_unavailable'
    if active and root is not None:
        for alias in db.execute(f'''SELECT person_id FROM {TABLE} INDEXED BY primary_associations_crop
            WHERE identity_group=? AND generation=? AND physical_crop_key=?''',(group,generation,current['physical_crop_key'])):
            if state['mapping'].get(alias['person_id'])!=root:
                valid=False; status='conflicting_exact_crop_claims'; break
    result={**_person_fields(db,group,person,state,generation,policy['revision']),
        'event_id':row['id'],'action':row['action'],'previous_event_id':row['previous_event_id'],
        'operator_context':row['operator_context'],
        'profile_revision':policy['revision'],'event_revision':row['sequence'],
        'active':active,'status':status,'source_state':source_state,
        'effective_person_id':root if active and valid else None,
        'authority':'human_observation_claim','extent':'observed_crop_only',
        'acoustic_support_certified':False,'enrollment_allowed':False,
        'binding':b,'binding_sha256':saved['binding_sha256'],
        'physical_crop_key':saved['physical_crop_key'],
        'source_session_id':row['source_session_id'],'recording_id':row['source_recording_id'],
        'observation_id':row['primary_observation_id'],
        'parent_evidence_url':f'/sessions/{row["source_session_id"]}/transcript/evidence/{quote(b["parent_ref"],safe="")}',
        'erasure_scope':'all_claim_history_for_exact_observation',
        'source_diagnostic_snapshot':'current_projection' if active else 'response_only',
        'audio_availability':'unavailable' if source_state in ('retired','source_retired') else 'not_checked'}
    return result


def _safe_canonical(db,group,generation,policy):
    try:return _canonical(db,group,generation,policy)
    except ValueError:return None


@_operation
def event_detail(store,group,event_id):
    _identifier(event_id,event=True)
    with _db(store) as (db,check):
        group,generation,policy=_context(db,group)
        _require_projection(db,group,generation)
        row=_event_row(db,event_id,group,generation)
        return _project_event(db,row,policy,_safe_canonical(db,group,generation,policy))


@_operation
def events(store,group,after=None,limit=32):
    _page_limit(limit)
    with _db(store) as (db,check):
        group,generation,policy=_context(db,group)
        _require_projection(db,group,generation)
        last=_seek(after,group,generation,policy,'events',{},0)
        if last<0:fail('invalid_cursor')
        keys=list(db.execute('''SELECT o.cursor,e.id FROM identity_review_event_order o
            CROSS JOIN identity_review_events e ON e.id=o.event_id WHERE e.identity_group=? AND e.generation=?
            AND e.action IN ('associate_primary','replace_primary','retract_primary') AND o.cursor>?
            ORDER BY o.cursor LIMIT ?''',(group,generation,last,limit+1)))
        state=_safe_canonical(db,group,generation,policy)
        items=[]; total=1024; has_more=len(keys)>limit
        for key in keys[:limit]:
            check(); item=_project_event(db,_event_row(db,key['id'],group,generation),policy,state)
            size=len(_encode(item,PAGE_BYTES))
            if total+size>PAGE_BYTES-4096:
                has_more=True; break
            items.append(item); total+=size; last=key['cursor']
        return _page(items,group,generation,policy,'events',{},last,has_more)


@_operation
def persons(store,group,cursor=None,limit=32):
    _page_limit(limit)
    with _db(store) as (db,check):
        group,generation,policy=_context(db,group)
        last=_seek(cursor,group,generation,policy,'persons',{},'')
        state=_safe_canonical(db,group,generation,policy)
        rows=list(db.execute('''SELECT id FROM speakers INDEXED BY speakers_identity_group
            WHERE identity_group=? AND id>? AND state IN ('reviewed_reference','reviewed_person') ORDER BY id LIMIT ?''',
            (group,last,limit+1)))
        items=[_person_fields(db,group,r['id'],state,generation,policy['revision']) for r in rows[:limit]]
        return _page(items,group,generation,policy,'persons',{},rows[min(len(rows),limit)-1]['id'] if rows else last,len(rows)>limit)


@_operation
def rename(store,group,person_id,request):
    _identifier(person_id,event=True); _shape(request,CAS_FIELDS|{'name'})
    if not isinstance(request['name'],str) or not 1<=len(request['name'])<=200:fail('invalid_person_name')
    with _db(store,write=True) as (db,check):
        group,generation,policy=_context(db,group)
        hashed,retry=_request(db,group,generation,policy,request,'rename_person',person_id)
        if retry is not None:return retry
        state=_canonical(db,group,generation,policy)
        _person_fields(db,group,person_id,state,generation,policy['revision'])
        bank_core.event_capacity(db)
        db.execute('UPDATE speakers SET name=? WHERE id=? AND identity_group=?',(request['name'],person_id,group))
        event_id=uuid.uuid4().hex
        response={**_person_fields(db,group,person_id,state,generation,policy['revision']+1),
            'event_id':event_id,'action':'rename_person','profile_revision':policy['revision']+1}
        _insert_event(db,group,generation,policy['revision']+1,'rename_person',request,hashed,response,
            event_id=event_id,person=person_id,previous=person_id,operator_context=_operator(store))
        bank_core._invalidate(db,group,'reviewed_person_renamed')
        return response


@_operation
def occurrences(store,group,person_id,cursor=None,limit=32,include_unavailable=False):
    _identifier(person_id,event=True); _page_limit(limit)
    if type(include_unavailable) is not bool:fail('invalid_query')
    filters={'person_id':person_id,'include_unavailable':include_unavailable}
    with _db(store) as (db,check):
        group,generation,policy=_context(db,group)
        _require_projection(db,group,generation)
        state=_canonical(db,group,generation,policy)
        if person_id not in state['mapping']:fail('person_not_found',404)
        root=state['mapping'][person_id]
        members=sorted(p for p,r in state['mapping'].items() if r==root)
        last=_seek(cursor,group,generation,policy,'occurrences',filters,'')
        # Indexed per-original-owner seeks, merged by globally unique observation ID.
        # One row per member at a time; never materialize the person's occurrences.
        heads={}
        for member in members:
            check()
            row=db.execute(f'''SELECT observation_id,event_id FROM {TABLE} INDEXED BY primary_associations_person
                WHERE identity_group=? AND generation=? AND person_id=? AND observation_id>?
                ORDER BY observation_id LIMIT 1''',(group,generation,member,last)).fetchone()
            if row:heads[member]=row
        items=[]; examined=0; total=1024
        while heads and len(items)<limit and examined<MAX_PAGE:
            check(); member=min(heads,key=lambda p:heads[p]['observation_id']); row=heads.pop(member)
            item=_project_event(db,_event_row(db,row['event_id'],group,generation),policy,state)
            size=len(_encode(item,PAGE_BYTES))
            if total+size>PAGE_BYTES-4096:
                heads[member]=row; break
            last=row['observation_id']; examined+=1
            if include_unavailable or item['effective_person_id'] is not None:
                items.append(item); total+=size
            following=db.execute(f'''SELECT observation_id,event_id FROM {TABLE} INDEXED BY primary_associations_person
                WHERE identity_group=? AND generation=? AND person_id=? AND observation_id>?
                ORDER BY observation_id LIMIT 1''',(group,generation,member,last)).fetchone()
            if following:heads[member]=following
        return _page(items,group,generation,policy,'occurrences',filters,last,bool(heads),complete=not heads)


@_operation
def observation(store,sid,rid,observation_id):
    with _db(store) as (db,check):return _binding(db,sid,rid,observation_id)


@_operation
def observations(store,sid,rid,after=None,limit=32):
    _identifier(sid);_identifier(rid);_page_limit(limit)
    with _db(store) as (db,check):
        try:result=history.read_primary_observations(db,sid,rid,after=after,limit=limit)
        except history.ReadError as exc:raise BridgeError(exc.reason,exc.status_code) from None
        result.setdefault('has_more',result.get('next_cursor') is not None)
        _encode(result,PAGE_BYTES)
        return result


@_operation
def relations(store,group,after=0,limit=32):
    """Same relation wire contract with a bounded group-scoped ledger page."""
    _page_limit(limit)
    if type(after) is not int or after<0:fail('invalid_query')
    with _db(store) as (db,check):
        group,generation,policy=_context(db,group)
        state=_canonical(db,group,generation,policy)
        rows=db.execute('''SELECT cursor,id,action,person_id,other_person_id,previous_event_id,
            sequence,operator_context,created FROM identity_relation_events INDEXED BY relation_events_group
            WHERE identity_group=? AND generation=? AND cursor>? ORDER BY cursor LIMIT ?''',
            (group,generation,after,limit))
        items=[]
        for row in rows:
            item=dict(row); item.update(active=row['id'] in state['active'],operator_assertion_is_claim=True,
                                       acoustic_support_certified=False)
            items.append(item)
        result={'events':items,'generation':generation,'profile_revision':policy['revision'],
                'next_cursor':items[-1]['cursor'] if items else after}
        _encode(result,PAGE_BYTES)
        return result


@_operation
def relate(store,group,request,*,previous=None):
    _group(group);_encode(request)
    with _db(store,write=True) as (db,check):
        _,generation,policy=_context(db,group)
        if request.get('expected_generation')!=generation:fail('stale_generation')
        _retry_preflight(db,group,generation,request.get('idempotency_key'))
        _,retry=identity_canonical._request(db,group,generation,policy,request,previous)
        if retry is not None:return retry
        _revision_room(policy)
        _canonical_preflight(db,group,generation)
        return identity_canonical.mutate(store,None,request,previous=previous,group=group,db=db,operator_context=_operator(store))
