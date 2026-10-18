"""Bounded, default-off recording-local probe evidence; never model authority.

Only two sidecar tables are owned here. A reservation is a consumed attempt, not
permission to retry inference. Original vectors and source/proof metadata are
immutable. Decisions are replaceable, provisional annotations of that evidence.
All computation callbacks run after the read transaction and without our locks.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import re
import threading
import time
import uuid

from .moss_result_store import MossResultStore, encoded, TABLE as HISTORY
from .moss_automatic import TABLE as AUTOMATIC
from .native_owned_support_cache import ReadError, _identifier

SCOPES = 'recording_speaker_link_scopes_v1'
BATCHES = 'recording_speaker_probe_batches_v1'
MAX_SCOPE_NODES, MAX_SCOPE_BATCHES = 32, 32
MAX_GLOBAL_NODES, MAX_GLOBAL_BATCHES = 1024, 1024
MAX_SCOPE_BYTES, MAX_GLOBAL_BYTES = 4 * 1024**2, 128 * 1024**2
MAX_STAGING_BYTES = 8 * 1024**2
MAX_BATCH_BYTES, MAX_DECISION_BYTES = 512 * 1024, 64 * 1024
MAX_BATCH_NODES, MAX_VECTOR_DIMENSION = 4, 2048
SCOPE_BYTES, BATCH_BYTES = 90112, 4096
RESERVATION_BYTES = MAX_BATCH_BYTES + BATCH_BYTES
MAX_REVISION = (1 << 63) - 1
STATES = ('source_scoped', 'insufficient_clean_support', 'probe_unavailable',
          'probe_interrupted', 'links_stale', 'capacity_exhausted', 'provisional_linked')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_TOKEN = re.compile(r'[a-f0-9]{32}\Z')
_STATE_SQL = ','.join(repr(x) for x in STATES)
SCHEMA = {
    SCOPES: f'''CREATE TABLE {SCOPES} (
        scope_seq INTEGER PRIMARY KEY CHECK(scope_seq>=0),
        scope_id TEXT NOT NULL UNIQUE, session_id TEXT NOT NULL, recording_id TEXT NOT NULL,
        policy_sha256 TEXT NOT NULL, space_json BLOB NOT NULL CHECK(typeof(space_json)='blob' AND length(space_json)<=16384),
        space_id TEXT NOT NULL CHECK(length(space_id)<=256),
        node_count INTEGER NOT NULL DEFAULT 0 CHECK(typeof(node_count)='integer' AND node_count BETWEEN 0 AND 1024),
        batch_count INTEGER NOT NULL DEFAULT 0 CHECK(typeof(batch_count)='integer' AND batch_count BETWEEN 0 AND 1024),
        retained_bytes INTEGER NOT NULL DEFAULT 0 CHECK(typeof(retained_bytes)='integer' AND retained_bytes BETWEEN 0 AND 134217728),
        staging_bytes INTEGER NOT NULL DEFAULT 0 CHECK(typeof(staging_bytes)='integer' AND staging_bytes BETWEEN 0 AND 8388608),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(typeof(revision)='integer' AND revision>=0),
        decision_revision INTEGER NOT NULL DEFAULT -1,
        decision_session_revision INTEGER NOT NULL DEFAULT -1,
        decision_sha256 TEXT,
        decision_json BLOB CHECK(decision_json IS NULL OR (typeof(decision_json)='blob' AND length(decision_json)<=65536)),
        state TEXT NOT NULL DEFAULT 'source_scoped' CHECK(state IN ({_STATE_SQL})),
        inflight_token TEXT, inflight_job_id TEXT, inflight_result_sha256 TEXT,
        inflight_nodes INTEGER NOT NULL DEFAULT 0 CHECK(inflight_nodes BETWEEN 0 AND 4),
        inflight_owner TEXT,
        inflight_batch_json BLOB CHECK(inflight_batch_json IS NULL OR (typeof(inflight_batch_json)='blob' AND length(inflight_batch_json)<=262144)),
        UNIQUE(session_id,recording_id),
        CHECK((scope_seq=0 AND scope_id='' AND session_id='' AND recording_id='' AND policy_sha256='') OR
              (scope_seq>0 AND length(scope_id)=64 AND length(session_id) BETWEEN 1 AND 64
               AND length(recording_id) BETWEEN 1 AND 64 AND length(policy_sha256)=64
               AND node_count<=32 AND batch_count<=32 AND retained_bytes<=4194304)),
        CHECK((inflight_token IS NULL AND inflight_job_id IS NULL AND inflight_result_sha256 IS NULL
               AND inflight_owner IS NULL AND inflight_batch_json IS NULL AND inflight_nodes=0 AND staging_bytes=0) OR
              (scope_seq=0 AND inflight_token IS NULL) OR
              (scope_seq>0 AND length(inflight_token)=32 AND length(inflight_job_id)=32
               AND length(inflight_result_sha256)=64 AND length(inflight_owner)=32 AND staging_bytes=524288)))''',
    BATCHES: f'''CREATE TABLE {BATCHES} (
        scope_seq INTEGER NOT NULL REFERENCES {SCOPES}(scope_seq),
        batch_seq INTEGER NOT NULL CHECK(batch_seq BETWEEN 1 AND 32),
        session_id TEXT NOT NULL, job_id TEXT NOT NULL CHECK(length(job_id)=32),
        result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64),
        policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64), space_id TEXT NOT NULL,
        reservation_token TEXT NOT NULL UNIQUE CHECK(length(reservation_token)=32),
        node_count INTEGER NOT NULL CHECK(node_count BETWEEN 0 AND 4),
        batch_json BLOB NOT NULL CHECK(typeof(batch_json)='blob' AND length(batch_json) BETWEEN 1 AND 524288),
        batch_sha256 TEXT NOT NULL CHECK(length(batch_sha256)=64),
        state TEXT NOT NULL CHECK(state IN ({_STATE_SQL})),
        charge_bytes INTEGER NOT NULL CHECK(charge_bytes=length(batch_json)+4096),
        PRIMARY KEY(scope_seq,batch_seq), UNIQUE(job_id,result_sha256,policy_sha256)) WITHOUT ROWID''',
    'recording_link_session_scope': f'CREATE INDEX recording_link_session_scope ON {SCOPES}(session_id,scope_seq)',
    'recording_link_inflight_token': f'CREATE UNIQUE INDEX recording_link_inflight_token ON {SCOPES}(inflight_token)',
    'recording_link_inflight_seq': f'CREATE INDEX recording_link_inflight_seq ON {SCOPES}(scope_seq) WHERE inflight_token IS NOT NULL',
    'recording_probe_session_scope': f'CREATE INDEX recording_probe_session_scope ON {BATCHES}(session_id,scope_seq)',
    'recording_link_membership_revision': '''CREATE TRIGGER recording_link_membership_revision
        AFTER UPDATE OF batch_id ON recording_work WHEN OLD.batch_id IS NOT NEW.batch_id BEGIN
        UPDATE sessions SET transcript_revision=CASE WHEN transcript_revision<9223372036854775807
            THEN transcript_revision+1 ELSE RAISE(ROLLBACK,'Transcript revision exhausted') END
            WHERE id IN (SELECT session_id FROM chunks WHERE id IN (OLD.chunk_id,NEW.chunk_id)
                UNION SELECT session_id FROM recording_batches WHERE id IN (OLD.batch_id,NEW.batch_id)); END''',
    'recording_probe_immutable': f'''CREATE TRIGGER recording_probe_immutable BEFORE UPDATE ON {BATCHES}
        BEGIN SELECT RAISE(ABORT,'Recording speaker probe batches are immutable'); END''',
}


def init_schema(db):
    """Additive exact-schema migration in the caller's transaction; no recovery."""
    if not db.in_transaction:
        raise ValueError('Recording speaker link migration requires a transaction')
    present = [db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone() for name in SCHEMA]
    if any(present) and not all(present):
        raise ValueError('Incomplete recording speaker link schema')
    for (name, sql), row in zip(SCHEMA.items(), present):
        if row is None:
            db.execute(sql)
        elif ' '.join(row[0].split()) != ' '.join(sql.split()):
            raise ValueError('Incompatible recording speaker link schema: ' + name)
    if not any(present):
        db.execute(f'''INSERT INTO {SCOPES}(scope_seq,scope_id,session_id,recording_id,policy_sha256,space_json,space_id)
            VALUES(0,'','','','',?,'')''', (b'null',))


def _fail(reason='probe_unavailable', status=409):
    raise ReadError(status, reason)


def _hash(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        _fail('invalid_speaker_link_identity')
    return value


def _session(db, sid):
    _identifier(sid)
    row = db.execute('SELECT deleted,transcript_revision FROM sessions WHERE id=?', (sid,)).fetchone()
    if row is None or row[0] == 1:
        _fail('session_not_found', 404)
    if type(row[0]) is not int or row[0] != 0 or type(row[1]) is not int or row[1] < 0:
        _fail('probe_unavailable', 503)
    return row[1]


def _json(raw, maximum):
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        _fail('probe_unavailable', 503)
    try:
        value = json.loads(raw)
        if encoded(value) != raw:
            raise ValueError()
    except (ValueError, UnicodeError, RecursionError, TypeError):
        _fail('probe_unavailable', 503)
    return value


def _space(value):
    if type(value) is not dict:
        _fail('invalid_speaker_link_space')
    raw = encoded(value)
    if not 1 <= len(raw) <= 16384:
        _fail('invalid_speaker_link_space')
    if type(value) is dict:
        from .embedding_space import validate_space
        ident = validate_space(value)
    else:
        ident = value
    if not 1 <= len(ident) <= 256:
        _fail('invalid_speaker_link_space')
    return raw, ident


def scope_identity(sid, recording_id):
    _identifier(sid); _identifier(recording_id)
    return hashlib.sha256(encoded({'session_id': sid, 'recording_id': recording_id})).hexdigest()


def _cancelled(value):
    return bool(value() if callable(value) else value.is_set() if hasattr(value, 'is_set') else value)


def _usage(db, own=None):
    total = db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=0').fetchone()
    if total is None:
        _fail('speaker_link_usage_invalid', 503)
    for row, limits in ((total, (MAX_GLOBAL_NODES, MAX_GLOBAL_BATCHES, MAX_GLOBAL_BYTES, MAX_STAGING_BYTES)),
                        (own, (MAX_SCOPE_NODES, MAX_SCOPE_BATCHES, MAX_SCOPE_BYTES, MAX_BATCH_BYTES))):
        if row is None:
            continue
        for key, limit in zip(('node_count', 'batch_count', 'retained_bytes', 'staging_bytes'), limits):
            if type(row[key]) is not int or not 0 <= row[key] <= limit:
                _fail('speaker_link_usage_invalid', 503)
            if own is row and row[key] > total[key]:
                _fail('speaker_link_usage_invalid', 503)
    if own is not None:
        actual = db.execute(f'''SELECT COUNT(*),COALESCE(SUM(node_count),0),COALESCE(SUM(charge_bytes),0)
            FROM {BATCHES} WHERE scope_seq=?''', (own['scope_seq'],)).fetchone()
        pending = int(own['inflight_token'] is not None)
        if (own['batch_count'] != actual[0] + pending or own['node_count'] != actual[1] + own['inflight_nodes']
                or own['retained_bytes'] != SCOPE_BYTES + actual[2] + pending * RESERVATION_BYTES
                or own['staging_bytes'] != pending * MAX_BATCH_BYTES):
            _fail('speaker_link_usage_invalid', 503)
    return total


def erase_session(db, sid):
    """Only explicit session erasure may delete evidence, in its own transaction.

    The erasure caller may already have written its deleted tombstone. Ordinary
    writers always require a live session; this is the sole intentional exception.
    """
    _identifier(sid)
    if not db.in_transaction:
        raise ValueError('Recording speaker erasure requires a transaction')
    total = _usage(db)
    values = db.execute(f'''SELECT COALESCE(SUM(node_count),0),COALESCE(SUM(batch_count),0),
        COALESCE(SUM(retained_bytes),0),COALESCE(SUM(staging_bytes),0)
        FROM {SCOPES} INDEXED BY recording_link_session_scope WHERE session_id=?''', (sid,)).fetchone()
    if any(a > total[k] for a, k in zip(values, ('node_count','batch_count','retained_bytes','staging_bytes'))):
        _fail('speaker_link_usage_invalid', 503)
    db.execute(f'DELETE FROM {BATCHES} WHERE session_id=?', (sid,))
    db.execute(f'DELETE FROM {SCOPES} WHERE session_id=?', (sid,))
    db.execute(f'''UPDATE {SCOPES} SET node_count=node_count-?,batch_count=batch_count-?,
        retained_bytes=retained_bytes-?,staging_bytes=staging_bytes-? WHERE scope_seq=0''', tuple(values))


def _saved(row, session_revision):
    if row is None:
        value = {'state': 'source_scoped', 'annotations': [], 'revision': 0, 'decision_revision': -1,
                 'policy_sha256': None, 'space_id': None, 'scope_id': None}
    else:
        current = (row['decision_revision'] == row['revision']
                   and row['decision_session_revision'] == session_revision
                   and row['inflight_token'] is None)
        if row['decision_json'] is not None and hashlib.sha256(row['decision_json']).hexdigest()!=row['decision_sha256']:
            _fail('probe_unavailable',503)
        decision = _json(row['decision_json'], MAX_DECISION_BYTES) if row['decision_json'] is not None else None
        from .recording_speaker_links import SUPPORTED_POLICIES
        current=current and row['policy_sha256'] in SUPPORTED_POLICIES
        if decision is not None:
            current = current and type(decision.get('diagnostics')) is dict and (
                decision['diagnostics'].get('policy_sha256') == row['policy_sha256'])
        state = row['state']
        if not current and state not in ('capacity_exhausted','probe_interrupted','probe_unavailable') and (decision is not None or state == 'provisional_linked'):
            state = 'links_stale'
        value = {'scope_id': row['scope_id'], 'state': state,
                 'annotations': decision.get('annotations', []) if current and decision is not None else [],
                 'revision': row['revision'], 'decision_revision': row['decision_revision'],
                 'policy_sha256': row['policy_sha256'], 'space_id': row['space_id'],
                 'decision_sha256': row['decision_sha256'],
                 'probe_outcomes': decision.get('probe_outcomes',[]) if decision is not None else [],
                 'jobs': decision.get('jobs',[]) if current and decision is not None else [],
                 'diagnostics': decision.get('diagnostics',{}) if current and decision is not None else {}}
    value['session_revision'] = session_revision
    value['revision_sha256'] = hashlib.sha256(encoded(value)).hexdigest()
    return value


def read_saved_for_recording(db, sid, recording_id, *, session_revision=None):
    """Pure indexed getter in the caller's SQLite snapshot; never infer/recompute."""
    revision = _session(db, sid) if session_revision is None else session_revision
    _identifier(recording_id)
    row = db.execute(f'''SELECT scope_id,revision,decision_revision,decision_session_revision,
        decision_json,decision_sha256,policy_sha256,space_id,state,inflight_token
        FROM {SCOPES} WHERE session_id=? AND recording_id=?''', (sid, recording_id)).fetchone()
    return _saved(row, revision)


def read_session_revision(db, sid, *, session_revision=None):
    """Indexed bounded headers only, for the enclosing transcript cursor digest."""
    revision=_session(db,sid) if session_revision is None else session_revision
    rows=db.execute(f'''SELECT scope_id,revision,decision_revision,decision_session_revision,
        decision_sha256,state,policy_sha256,space_id FROM {SCOPES}
        INDEXED BY recording_link_session_scope WHERE session_id=? ORDER BY scope_seq LIMIT 1025''',(sid,)).fetchall()
    if len(rows)>MAX_GLOBAL_BATCHES: _fail('capacity_exhausted',503)
    from .recording_speaker_links import SUPPORTED_POLICIES
    return hashlib.sha256(encoded({'session_id':sid,'session_revision':revision,'supported_policy_sha256':SUPPORTED_POLICIES,
        'scopes':[tuple(row) for row in rows]})).hexdigest()


class RecordingSpeakerLinkStore:
    def __init__(self, store, *, clock=time.monotonic):
        self.store, self.settings, self.clock = store, store.settings, clock
        self.sql = MossResultStore(store, clock=clock)
        self.owner = uuid.uuid4().hex
        self._recovery_lock = self._recovery_file = None
        self.recovery_boundary = None
        self.recovery_after = 0
        self.recovery_remaining = None
        self._staging_lock, self._staging = threading.Lock(), 0
        self._primary_source = None

    def bind_primary_source(self, source):
        """Only the already-owned CellJobsSource; no new owner/provider path."""
        from .native_owned_cell_jobs import CellJobsSource
        if type(source) is not CellJobsSource or source.store is not self.store:
            _fail('primary_probe_authority_unavailable')
        if self._primary_source is not None and self._primary_source is not source:
            _fail('primary_probe_authority_changed')
        self._primary_source = source

    def capture_policy(self, sid, proof):
        """New scopes choose v2 before their first ordinary or bounded capture."""
        from .native_owned_cells import validate_document
        from .recording_speaker_links import POLICY_SHA256_V2, SUPPORTED_POLICIES
        _, _, parts = validate_document(proof)
        with self._database() as db:
            _session(db, sid)
            recordings = set()
            for cid in sorted({p.chunk_id for p in parts}):
                row = db.execute('''SELECT w.batch_id FROM recording_work w
                    JOIN recording_batches b ON b.id=w.batch_id JOIN chunks c ON c.id=w.chunk_id
                    WHERE w.chunk_id=? AND c.session_id=? AND b.session_id=?''', (cid,sid,sid)).fetchone()
                if row is None:_fail('probe_unavailable')
                recordings.add(row[0])
            if len(recordings) != 1:_fail('probe_unavailable')
            row = db.execute(f'SELECT policy_sha256 FROM {SCOPES} WHERE session_id=? AND recording_id=?',
                (sid, next(iter(recordings)))).fetchone()
            policy = POLICY_SHA256_V2 if row is None else row[0]
            if policy not in SUPPORTED_POLICIES:_fail('speaker_link_policy_conflict')
            return policy

    def capture_primary_packets(self, proof):
        from .recording_primary_probe import read_named
        with self._database() as db:
            return read_named(db, proof, self._primary_source)

    def _primary_current(self, db, batch, *, boundary=None):
        from .recording_primary_probe import current_packets
        from .recording_speaker_links import SCHEMA_V2
        from .native_owned_cells import Unavailable
        if batch.get('schema') != SCHEMA_V2:return
        try:
            current = current_packets(db, encoded(batch['proof']), batch.get('primary_packets', []),
                                      self._primary_source, boundary=boundary)
        except (Unavailable, ReadError, ValueError, TypeError, KeyError):
            _fail('primary_probe_source_stale')
        if not current:
            _fail('primary_probe_source_stale')

    @property
    def enabled(self):
        return bool(getattr(self.settings, 'recording_speaker_links_enabled', False))

    @contextmanager
    def _database(self, *, write=False):
        # Reuse the exact inode/headroom/zero-wait/250ms/50k-VM-step envelope.
        with self.sql._database(write=write) as db:
            yield db

    @contextmanager
    def _authority_database(self, *, write=False):
        from .recording_primary_probe import packet_boundary
        from .native_owned_cells import Unavailable
        # The final generation check runs before the SQL context commits.
        with self._database(write=write) as db:
            try:
                with packet_boundary(self._primary_source) as boundary:
                    yield db, boundary
            except Unavailable:
                _fail('primary_probe_source_stale')

    @staticmethod
    def _bound_job(db, sid, jid, digest, recording_id=None):
        _session(db, sid)
        if type(jid) is not str or _TOKEN.fullmatch(jid) is None:
            _fail('invalid_speaker_link_identity')
        _hash(digest)
        from .transcript_store import TranscriptStore
        query=TranscriptStore._DESCRIPTOR_SQL.format(index='INDEXED BY auto_job',where='WHERE a.job_id=? AND a.sid=?')
        descriptor=db.execute(query,(jid,sid)).fetchone()
        if (descriptor is None or descriptor['state']!='committed' or descriptor['history_state']!='committed'
                or descriptor['result_sha256']!=digest): _fail('probe_unavailable')
        row=TranscriptStore._check_descriptor_lengths([descriptor], db=db)[0]
        binding,start,end,cancelled=TranscriptStore._binding(db,sid,row)
        candidate,_,_=TranscriptStore._result(db,sid,row,binding,start,end,cancelled)
        if cancelled or not candidate['eligible']: _fail('probe_unavailable')
        found = None
        for cid in row.get('source_roots', (row['cid1'], row['cid2'])):
            if cid is None:
                continue
            source = db.execute('''SELECT w.batch_id,c.status,b.session_id FROM recording_work w
                JOIN recording_batches b ON b.id=w.batch_id JOIN chunks c ON c.id=w.chunk_id
                WHERE w.chunk_id=? AND c.session_id=?''', (cid, sid)).fetchone()
            if (source is None or source['session_id'] != sid or source['status'] in ('cancelled','failed','staging')
                    or found is not None and source['batch_id'] != found):
                _fail('probe_unavailable')
            found = source['batch_id']
        if found is None or recording_id is not None and found != recording_id:
            _fail('probe_unavailable')
        return found, row['proof_sha256']

    def recording_for_job(self, sid, jid, result_sha256):
        with self._database() as db:
            return self._bound_job(db, sid, jid, result_sha256)[0]

    @staticmethod
    def _reservation(row, *, should_probe=False, state=None):
        return {'scope_id': row['scope_id'], 'token': row['inflight_token'], 'revision': row['revision'],
                'should_probe': should_probe, 'state': state or row['state'], 'space_id': row['space_id']}

    def reserve(self, session_id, recording_id, job_id, result_sha256, policy_sha256, space,
                *, max_nodes=4, cancelled=None, batch=None):
        sid, rid = session_id, recording_id
        scope_id = scope_identity(sid, rid)
        _hash(result_sha256); _hash(policy_sha256)
        from .recording_speaker_links import POLICY_SHA256_V2, SUPPORTED_POLICIES
        if policy_sha256 not in SUPPORTED_POLICIES: _fail('speaker_link_policy_conflict')
        raw_space, space_id = _space(space)
        if type(max_nodes) is not int or not 0 <= max_nodes <= MAX_BATCH_NODES:
            _fail('invalid_speaker_link_capacity')
        if not self.enabled or _cancelled(cancelled):
            return {'scope_id': scope_id, 'token': None, 'should_probe': False, 'state': 'source_scoped'}
        pending_raw=None
        if batch is not None:
            pending_raw=self._validate_batch(batch)
            if len(pending_raw)>262144 or any('vector' in node for node in batch['nodes']):
                _fail('invalid_speaker_probe_batch')
            if (batch.get('session_id')!=sid or batch.get('job_id')!=job_id
                    or batch.get('result_sha256')!=result_sha256 or batch.get('policy_sha256')!=policy_sha256
                    or batch.get('space_id')!=space_id or len(batch['nodes'])>max_nodes):
                _fail('speaker_link_identity_conflict')
        token = uuid.uuid4().hex
        with self._authority_database(write=True) as (db, boundary):
            _session(db, sid)
            _,proof=self._bound_job(db, sid, job_id, result_sha256, rid)
            if batch is not None and batch['proof_sha256']!=proof: _fail('speaker_link_identity_conflict')
            own = db.execute(f'SELECT * FROM {SCOPES} WHERE scope_id=?', (scope_id,)).fetchone()
            if own is None and policy_sha256 != POLICY_SHA256_V2:
                _fail('speaker_link_policy_conflict')
            if own is not None and (own['policy_sha256'] != policy_sha256 or own['space_json'] != raw_space):
                _fail('speaker_link_policy_conflict')
            if batch is not None:self._primary_current(db, batch, boundary=boundary)
            previous = db.execute(f'''SELECT scope_seq,state FROM {BATCHES}
                WHERE job_id=? AND result_sha256=? AND policy_sha256=?''', (job_id,result_sha256,policy_sha256)).fetchone()
            if previous is not None:
                if own is None or own['scope_seq'] != previous['scope_seq']:
                    _fail('speaker_link_identity_conflict')
                return self._reservation(own, state=previous['state'])
            if own is not None and own['inflight_token'] is not None:
                # This includes lost admission acknowledgement. Never issue a second permission.
                return self._reservation(own, state='probe_unavailable')
            total = _usage(db, own)
            extra = RESERVATION_BYTES + (SCOPE_BYTES if own is None else 0)
            exhausted = (total['node_count'] + max_nodes > MAX_GLOBAL_NODES
                or total['batch_count'] + 1 > MAX_GLOBAL_BATCHES
                or total['retained_bytes'] + extra > MAX_GLOBAL_BYTES
                or total['staging_bytes'] + MAX_BATCH_BYTES > MAX_STAGING_BYTES
                or own is not None and (own['node_count'] + max_nodes > MAX_SCOPE_NODES
                    or own['batch_count'] + 1 > MAX_SCOPE_BATCHES
                    or own['retained_bytes'] + RESERVATION_BYTES > MAX_SCOPE_BYTES))
            if exhausted:
                # Refusing admission adds no evidence and must not stale a current
                # decision. State still participates in the transcript cursor.
                if own is not None and own['state'] != 'capacity_exhausted':
                    db.execute(f"UPDATE {SCOPES} SET state='capacity_exhausted' WHERE scope_id=?", (scope_id,))
                return {'scope_id': scope_id, 'token': None, 'should_probe': False, 'state': 'capacity_exhausted'}
            if _cancelled(cancelled):
                return {'scope_id': scope_id, 'token': None, 'should_probe': False, 'state': 'probe_unavailable'}
            if own is None:
                seq = db.execute(f'SELECT scope_seq FROM {SCOPES} ORDER BY scope_seq DESC LIMIT 1').fetchone()[0] + 1
                if seq > MAX_REVISION: _fail('capacity_exhausted', 429)
                db.execute(f'''INSERT INTO {SCOPES}(scope_seq,scope_id,session_id,recording_id,
                    policy_sha256,space_json,space_id,retained_bytes) VALUES(?,?,?,?,?,?,?,?)''',
                    (seq,scope_id,sid,rid,policy_sha256,raw_space,space_id,SCOPE_BYTES))
            elif own['revision'] >= MAX_REVISION:
                _fail('capacity_exhausted', 429)
            db.execute(f'''UPDATE {SCOPES} SET node_count=node_count+?,batch_count=batch_count+1,
                retained_bytes=retained_bytes+?,staging_bytes=staging_bytes+? WHERE scope_seq=0''',
                (max_nodes,extra,MAX_BATCH_BYTES))
            db.execute(f'''UPDATE {SCOPES} SET node_count=node_count+?,batch_count=batch_count+1,
                retained_bytes=retained_bytes+?,staging_bytes=?,revision=revision+1,state='links_stale',
                inflight_token=?,inflight_job_id=?,inflight_result_sha256=?,inflight_nodes=?,inflight_owner=?,inflight_batch_json=? WHERE scope_id=?''',
                (max_nodes,RESERVATION_BYTES,MAX_BATCH_BYTES,token,job_id,result_sha256,max_nodes,self.owner,pending_raw,scope_id))
            answer = self._reservation(db.execute(f'SELECT * FROM {SCOPES} WHERE scope_id=?', (scope_id,)).fetchone(), should_probe=True)
        return answer

    @staticmethod
    def _token(value):
        token = value.get('token') if type(value) is dict else value
        if type(token) is not str or _TOKEN.fullmatch(token) is None:
            _fail('invalid_speaker_link_reservation')
        return token

    @staticmethod
    def _validate_batch(batch):
        if type(batch) is not dict or type(batch.get('nodes')) is not list or len(batch['nodes']) > MAX_BATCH_NODES:
            _fail('invalid_speaker_probe_batch')
        try:
            raw = encoded(batch)
        except (ValueError, TypeError, RecursionError):
            _fail('invalid_speaker_probe_batch')
        if len(raw) > MAX_BATCH_BYTES:
            _fail('capacity_exhausted', 429)
        for node in batch['nodes']:
            if (type(node) is not dict or node.get('job_id') != batch.get('job_id')
                    or node.get('result_sha256') != batch.get('result_sha256')
                    or node.get('space_id') != batch.get('space_id')):
                _fail('invalid_speaker_probe_batch')
            vector = node.get('vector')
            if vector is not None and (type(vector) is not list or not 1 <= len(vector) <= MAX_VECTOR_DIMENSION
                    or any(type(x) not in (float,int) or not math.isfinite(x) for x in vector)
                    or not .999 <= math.sqrt(sum(x*x for x in vector)) <= 1.001):
                _fail('invalid_speaker_probe_vector')
        from .recording_speaker_links import validate_batch, valid_vector
        from .native_owned_cells import Unavailable
        try:
            validate_batch(batch,require_vectors=False)
            for node in batch['nodes']:
                if node.get('vector') is not None: valid_vector(node['vector'])
        except (Unavailable,ValueError,TypeError,KeyError):
            _fail('invalid_speaker_probe_batch')
        return raw

    @staticmethod
    def _finish(db, row, raw, nodes, state):
        _session(db, row['session_id']); _usage(db, row)
        if row['revision'] >= MAX_REVISION: _fail('capacity_exhausted', 429)
        charge = len(raw) + BATCH_BYTES
        db.execute(f'''INSERT INTO {BATCHES}(scope_seq,batch_seq,session_id,job_id,result_sha256,
            policy_sha256,space_id,reservation_token,node_count,batch_json,batch_sha256,state,charge_bytes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (row['scope_seq'],row['batch_count'],row['session_id'],
            row['inflight_job_id'],row['inflight_result_sha256'],row['policy_sha256'],row['space_id'],
            row['inflight_token'],nodes,raw,hashlib.sha256(raw).hexdigest(),state,charge))
        delta = (nodes-row['inflight_nodes'],charge-RESERVATION_BYTES,-MAX_BATCH_BYTES)
        db.execute(f'''UPDATE {SCOPES} SET node_count=node_count+?,retained_bytes=retained_bytes+?,
            staging_bytes=staging_bytes+? WHERE scope_seq=0''', delta)
        db.execute(f'''UPDATE {SCOPES} SET node_count=node_count+?,retained_bytes=retained_bytes+?,
            staging_bytes=0,revision=revision+1,state=?,inflight_token=NULL,
            inflight_job_id=NULL,inflight_result_sha256=NULL,inflight_nodes=0,inflight_owner=NULL,inflight_batch_json=NULL
            WHERE scope_seq=?''', (delta[0],delta[1],'links_stale' if nodes and state not in ('probe_unavailable','probe_interrupted','capacity_exhausted') else state,row['scope_seq']))

    def commit(self, token, batch, state='source_scoped', *, cancelled=None):
        token = self._token(token)
        if state not in STATES: _fail('invalid_speaker_link_state')
        raw = self._validate_batch(batch)
        with self._authority_database(write=True) as (db, boundary):
            row = db.execute(f'SELECT * FROM {SCOPES} WHERE inflight_token=?', (token,)).fetchone()
            if row is None:
                previous = db.execute(f'SELECT * FROM {BATCHES} WHERE reservation_token=?', (token,)).fetchone()
                if previous is None: _fail('speaker_link_reservation_missing', 404)
                _session(db, previous['session_id'])
                if previous['batch_json'] != raw or previous['state'] != state:
                    _fail('speaker_link_commit_conflict')
                own = db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?', (previous['scope_seq'],)).fetchone()
                return {'scope_id': own['scope_id'], 'revision': own['revision'], 'state': own['state']}
            _session(db, row['session_id'])
            if not self.enabled or _cancelled(cancelled): _fail('probe_unavailable')
            _, proof = self._bound_job(db,row['session_id'],row['inflight_job_id'],row['inflight_result_sha256'],row['recording_id'])
            if (batch.get('session_id') != row['session_id'] or batch.get('job_id') != row['inflight_job_id']
                    or batch.get('result_sha256') != row['inflight_result_sha256']
                    or batch.get('policy_sha256') != row['policy_sha256'] or batch.get('space_id') != row['space_id']
                    or batch.get('proof_sha256') != proof or len(batch['nodes']) > row['inflight_nodes']):
                _fail('speaker_link_commit_conflict')
            if row['inflight_batch_json'] is not None:
                original=_json(row['inflight_batch_json'],262144)
                if original.get('batch_sha256')!=batch.get('batch_sha256'): _fail('speaker_link_commit_conflict')
            if _cancelled(cancelled): _fail('probe_unavailable')
            self._primary_current(db, batch, boundary=boundary)
            self._finish(db,row,raw,len(batch['nodes']),state)
            answer = {'scope_id': row['scope_id'], 'revision': row['revision']+1,
                      'state': 'links_stale' if batch['nodes'] and state not in ('probe_unavailable','probe_interrupted','capacity_exhausted') else state}
        return answer

    def resolve_attempt(self, sid, rid, jid, result_sha256, prepared_batch, *, state='probe_unavailable', token=None):
        """Close only this owner's exact uncertain attempt, without model authority.

        Retired source authority does not prevent retaining unavailable historical
        evidence. No vectors are accepted and no committed batch is overwritten.
        """
        scope_id=scope_identity(sid,rid)
        _hash(result_sha256)
        if type(jid) is not str or _TOKEN.fullmatch(jid) is None: _fail('invalid_speaker_link_identity')
        if state not in ('probe_unavailable','probe_interrupted'): _fail('invalid_speaker_link_state')
        wanted_token=self._token(token) if token is not None else None
        raw=self._validate_batch(prepared_batch)
        if (len(raw)>262144 or any('vector' in node for node in prepared_batch['nodes'])
                or prepared_batch.get('session_id')!=sid or prepared_batch.get('job_id')!=jid
                or prepared_batch.get('result_sha256')!=result_sha256): _fail('speaker_link_identity_conflict')
        answer={'resolved':False,'scope_id':scope_id,'state':state}
        with self._database(write=True) as db:
            _session(db,sid)
            row=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_id=?',(scope_id,)).fetchone()
            if row is None: return {**answer,'resolved':True,'no_op':True}
            if (row['policy_sha256']!=prepared_batch['policy_sha256']
                    or row['space_id']!=prepared_batch['space_id']): return answer
            prior=db.execute(f'''SELECT reservation_token,batch_json,state FROM {BATCHES}
                WHERE scope_seq=? AND job_id=? AND result_sha256=? AND policy_sha256=?''',
                (row['scope_seq'],jid,result_sha256,prepared_batch['policy_sha256'])).fetchone()
            if prior is not None:
                if wanted_token is not None and prior['reservation_token']!=wanted_token: return answer
                original=_json(prior['batch_json'],MAX_BATCH_BYTES)
                if original.get('batch_sha256')!=prepared_batch.get('batch_sha256'): return answer
                original={**original,'nodes':[{k:v for k,v in node.items() if k!='vector'} for node in original['nodes']]}
                if encoded(original)!=raw: return answer
                return {**answer,'resolved':True,'state':prior['state'],'already_committed':True}
            if row['inflight_token'] is None: return {**answer,'resolved':True,'no_op':True}
            if (row['inflight_owner']!=self.owner or row['inflight_job_id']!=jid
                    or row['inflight_result_sha256']!=result_sha256
                    or wanted_token is not None and wanted_token!=row['inflight_token']
                    or row['inflight_batch_json']!=raw): return answer
            self._finish(db,row,raw,len(prepared_batch['nodes']),state)
            answer.update(resolved=True,revision=row['revision']+1)
        return answer

    def _current_batches(self, db, row, *, boundary=None):
        if boundary is not None:
            return self._current_batches_in_boundary(db, row, boundary)
        from .recording_primary_probe import packet_boundary
        with packet_boundary(self._primary_source) as boundary:
            return self._current_batches_in_boundary(db, row, boundary)

    def _current_batches_in_boundary(self, db, row, boundary):
        headers=db.execute(f'''SELECT batch_seq,job_id,result_sha256,policy_sha256,space_id,
            batch_sha256,state,length(batch_json) AS bytes FROM {BATCHES}
            WHERE scope_seq=? ORDER BY batch_seq LIMIT 33''',(row['scope_seq'],)).fetchall()
        if (len(headers)>MAX_SCOPE_BATCHES or any(type(x['bytes']) is not int or not 1<=x['bytes']<=MAX_BATCH_BYTES for x in headers)
                or sum(x['bytes'] for x in headers)>MAX_SCOPE_BYTES): _fail('capacity_exhausted',503)
        values=[]
        for header in headers:
            if header['policy_sha256']!=row['policy_sha256'] or header['space_id']!=row['space_id']:
                _fail('speaker_link_identity_conflict')
            _,proof=self._bound_job(db,row['session_id'],header['job_id'],header['result_sha256'],row['recording_id'])
            raw=db.execute(f'SELECT batch_json FROM {BATCHES} WHERE scope_seq=? AND batch_seq=?',
                (row['scope_seq'],header['batch_seq'])).fetchone()[0]
            if hashlib.sha256(raw).hexdigest()!=header['batch_sha256']: _fail('probe_unavailable',503)
            batch = _json(raw, MAX_BATCH_BYTES)
            self._primary_current(db, batch, boundary=boundary)
            values.append({**dict(header),'batch_json':raw,'current_proof_sha256':proof})
        return values

    def snapshot(self, scope_id, *, _failure_context=None):
        _hash(scope_id)
        with self._authority_database() as (db, boundary):
            row = db.execute(f'SELECT * FROM {SCOPES} WHERE scope_id=?', (scope_id,)).fetchone()
            if row is None: _fail('speaker_link_scope_missing', 404)
            if _failure_context is not None: _failure_context['revision']=row['revision']
            session_revision = _session(db,row['session_id']); _usage(db,row)
            inventory = self._current_batches(db,row,boundary=boundary)
            # Read immutable bounded bytes under SQL; decode/reconcile after close.
            values = [dict(x) for x in inventory]
            result = {key: row[key] for key in ('scope_id','session_id','recording_id','policy_sha256','space_id','revision','decision_revision','decision_session_revision','state')}
            result.update(session_revision=session_revision, space_json=row['space_json'],
                          inflight=row['inflight_token'] is not None)
        batches=[]; evidence=[]; terminal=[]; terminal_states={}
        for value in values:
            if hashlib.sha256(value['batch_json']).hexdigest()!=value['batch_sha256']:
                _fail('probe_unavailable',503)
            batch=_json(value['batch_json'],MAX_BATCH_BYTES)
            if (batch.get('schema')!='recording-speaker-probe-interrupted-v1'
                    and batch.get('proof_sha256')!=value['current_proof_sha256']): _fail('speaker_link_identity_conflict')
            batches.append(batch)
            if value['state'] in ('probe_interrupted','probe_unavailable','capacity_exhausted'):
                terminal.append({'job_id':batch['job_id'],'result_sha256':batch['result_sha256'],'state':value['state']})
                if batch.get('batch_sha256') is not None:
                    terminal_states[batch['batch_sha256']]=value['state']
            if batch.get('schema')!='recording-speaker-probe-interrupted-v1':
                evidence.append(batch)
        result['space']=_json(result.pop('space_json'),16384)
        result['batches']=batches
        result['evidence_batches']=evidence
        result['terminal_batches']=terminal
        result['terminal_states']=terminal_states
        return result

    def save_decision(self, scope_id, revision, decision, *, cancelled=None, session_revision=None, policy_sha256=None):
        _hash(scope_id)
        if type(revision) is not int or revision<0 or type(session_revision) is not int or session_revision<0:
            _fail('invalid_speaker_link_revision')
        if (type(decision) is not dict or type(decision.get('annotations')) is not list
                or len(decision['annotations'])>MAX_SCOPE_NODES):
            _fail('invalid_speaker_link_decision')
        from .recording_speaker_links import POLICY_SHA256, POLICY_SHA256_V2, SUPPORTED_POLICIES
        policy_sha256=POLICY_SHA256 if policy_sha256 is None else policy_sha256
        if (policy_sha256 not in SUPPORTED_POLICIES or type(decision.get('diagnostics')) is not dict
                or decision['diagnostics'].get('policy_sha256')!=policy_sha256):
            _fail('speaker_link_policy_conflict')
        for annotation in decision['annotations']:
            if (type(annotation) is not dict or annotation.get('speaker_identity_valid') is not False
                    or annotation.get('status') not in STATES): _fail('invalid_speaker_link_decision')
            _hash(annotation.get('result_sha256')); _hash(annotation.get('node_id'))
        state=decision.get('state')
        if type(state) is dict:
            if state.get('policy_sha256') != policy_sha256: _fail('speaker_link_policy_conflict')
            state='provisional_linked' if state.get('linked_cluster_count',0)>0 else 'source_scoped'
        if state not in STATES: _fail('invalid_speaker_link_state')
        raw=encoded(decision)
        if len(raw)>MAX_DECISION_BYTES: _fail('capacity_exhausted',429)
        if not self.enabled or _cancelled(cancelled): return False
        with self._authority_database(write=True) as (db, boundary):
            row=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_id=?',(scope_id,)).fetchone()
            if row is None: return False
            source_revision=_session(db,row['session_id'])
            if (row['revision']!=revision or row['inflight_token'] is not None or source_revision!=session_revision
                    or policy_sha256 is not None and row['policy_sha256']!=policy_sha256 or _cancelled(cancelled)):
                return False
            originals={}
            for retained in self._current_batches(db,row,boundary=boundary):
                batch=_json(retained['batch_json'],MAX_BATCH_BYTES)
                if (batch.get('schema')!='recording-speaker-probe-interrupted-v1'
                        and batch.get('proof_sha256')!=retained['current_proof_sha256']): _fail('speaker_link_identity_conflict')
                for node in batch['nodes']:
                    originals[node['node_id']]=node
            seen=set()
            for annotation in decision['annotations']:
                node=originals.get(annotation['node_id'])
                if (node is None or annotation['node_id'] in seen or any(annotation.get(key)!=node.get(key)
                        for key in ('job_id','result_sha256','phrase_index','space_id','proof_sha256','source_key',
                                    'source_kind','primary_source_ref','primary_packet_sha256'))):
                    _fail('invalid_speaker_link_decision')
                if policy_sha256 == POLICY_SHA256_V2:
                    expected = {k:v for k,v in node.items() if k not in
                                ('vector','source_spans','primary_commit')}
                    observed = {k:v for k,v in annotation.items() if k not in
                                ('status','provisional_speaker_id','reason',
                                 'duplicate_evidence','speaker_identity_valid')}
                    if observed != expected:_fail('invalid_speaker_link_decision')
                seen.add(annotation['node_id'])
            # Cancellation of a source chunk is checked independently of callback state.
            cancelled_source=db.execute('''SELECT 1 FROM recording_work w JOIN chunks c ON c.id=w.chunk_id
                WHERE w.batch_id=? AND c.status='cancelled' LIMIT 1''',(row['recording_id'],)).fetchone()
            if (cancelled_source is not None or not self.enabled or _cancelled(cancelled)
                    or any(a.get('space_id')!=row['space_id'] for a in decision['annotations'])): return False
            changed=db.execute(f'''UPDATE {SCOPES} SET decision_json=?,decision_sha256=?,decision_revision=?,
                decision_session_revision=?,state=? WHERE scope_id=? AND revision=? AND inflight_token IS NULL''',
                (raw,hashlib.sha256(raw).hexdigest(),revision,session_revision,state,scope_id,revision)).rowcount
            return changed==1

    def bind_recovery(self, instance_lock):
        """Bind only the existing real startup owner; never infer from a stale PID."""
        from pathlib import Path
        from .resources import InstanceLock
        from .moss_handy_jobs import ownership_fence
        if (type(instance_lock) is not InstanceLock or instance_lock.file is None or instance_lock.file.closed
                or Path(instance_lock.path).resolve()!=self.sql.path.parent/'instance.lock'):
            _fail('speaker_link_recovery_unavailable',503)
        ownership_fence(self.sql.path.parent)
        if self._recovery_file is not None: _fail('speaker_link_recovery_already_bound',503)
        with self._database() as db:
            boundary=db.execute(f'SELECT scope_seq FROM {SCOPES} ORDER BY scope_seq DESC LIMIT 1').fetchone()[0]
        self._recovery_lock,self._recovery_file=instance_lock,instance_lock.file
        self.recovery_boundary,self.recovery_after=boundary,0
        self._recovery_current()

    def revoke_recovery(self):
        self._recovery_lock=self._recovery_file=None
        self.recovery_boundary=None
        self.recovery_remaining=None

    def _recovery_current(self):
        if (self.recovery_boundary is None or self._recovery_lock.file is not self._recovery_file
                or self._recovery_file.closed): _fail('speaker_link_recovery_unavailable',503)

    @contextmanager
    def _stage(self, size):
        with self._staging_lock:
            if self._staging+size>MAX_STAGING_BYTES: _fail('capacity_exhausted',429)
            self._staging+=size
        try: yield
        finally:
            with self._staging_lock: self._staging-=size

    def _mark_unavailable(self, scope_id, error, *, expected_revision=None):
        """One bounded best-effort invalidation; never erase or replay evidence."""
        reason=getattr(error,'reason','probe_unavailable')
        state='capacity_exhausted' if 'capacity' in reason else 'probe_unavailable'
        answer={'saved':False,'state':state,'scope_id':scope_id}
        try:
            with self._database(write=True) as db:
                row=db.execute(f'SELECT session_id,revision,decision_revision,state FROM {SCOPES} WHERE scope_id=?',
                    (scope_id,)).fetchone()
                if row is None: return answer
                _session(db,row['session_id'])
                if expected_revision is not None and row['revision']!=expected_revision:
                    return {**answer,'state':'links_stale'}
                # An unchanged failed scope does not endlessly churn cursor revisions.
                if row['decision_revision']==-1 and row['state']==state: return answer
                revision=min(row['revision']+1,MAX_REVISION)
                db.execute(f'''UPDATE {SCOPES} SET decision_revision=-1,decision_session_revision=-1,
                    state=?,revision=? WHERE scope_id=? AND revision=?''',
                    (state,revision,scope_id,row['revision']))
                answer['revision']=revision
        except Exception:
            # A deleted session, busy database or unavailable device cannot turn
            # optional annotation failure into a primary-text startup veto. No retry.
            pass
        return answer

    def recompute_scope(self, scope_id, recompute, *, cancelled=None):
        """Contain one optional scope's failure; preserve every immutable batch."""
        context={}
        try:
            return self._recompute_scope(scope_id,recompute,cancelled=cancelled,_failure_context=context)
        except Exception as error:
            return self._mark_unavailable(scope_id,error,expected_revision=context.get('revision'))

    def _recompute_scope(self, scope_id, recompute, *, cancelled=None, _failure_context=None):
        """One bounded model-free snapshot/CAS shared by commit and recovery."""
        with self._stage(MAX_SCOPE_BYTES+MAX_DECISION_BYTES):
            snapshot=self.snapshot(scope_id,_failure_context=_failure_context)
            if (snapshot['inflight'] or not self.enabled or _cancelled(cancelled)
                    or snapshot['decision_revision']==snapshot['revision']
                    and snapshot['decision_session_revision']==snapshot['session_revision']):
                return {'saved':False,'state':snapshot['state'],'scope_id':scope_id}
            decision=recompute(snapshot['evidence_batches'],terminal_states=snapshot['terminal_states'])
            decision={**decision,'probe_outcomes':snapshot['terminal_batches']}
            jobs=list(decision.get('jobs',[]))
            keys={(job['job_id'],job['result_sha256']) for job in jobs}
            for terminal in snapshot['terminal_batches']:
                if (terminal['job_id'],terminal['result_sha256']) not in keys:
                    jobs.append({'job_id':terminal['job_id'],'result_sha256':terminal['result_sha256'],
                                 'default_status':terminal['state']})
            decision['jobs']=jobs
            if not snapshot['evidence_batches'] and snapshot['terminal_batches']:
                decision['state']=snapshot['terminal_batches'][-1]['state']
            saved=self.save_decision(scope_id,snapshot['revision'],decision,
                session_revision=snapshot['session_revision'],policy_sha256=snapshot['policy_sha256'],cancelled=cancelled)
            return {'saved':saved,'state':decision['state'] if saved else 'links_stale',
                    'scope_id':scope_id,'revision':snapshot['revision']}

    def reconcile(self, recompute=None, *, cancelled=None, limit=16):
        """At most 16 scopes per call; uncertain attempts never invoke a model.

        The caller supplies the pure evidence-to-decision function. No callback
        runs under a SQL transaction or lock. Continue while remaining_work.
        """
        if type(limit) is not int or not 1<=limit<=16: _fail('invalid_speaker_link_capacity')
        self._recovery_current()
        with self._database() as db:
            if self.recovery_remaining is False:
                self.recovery_boundary=db.execute(f'SELECT scope_seq FROM {SCOPES} ORDER BY scope_seq DESC LIMIT 1').fetchone()[0]
                self.recovery_after=0
            rows=db.execute(f'''SELECT scope_seq,scope_id FROM {SCOPES}
                WHERE scope_seq>? AND scope_seq<=? ORDER BY scope_seq LIMIT ?''',
                (self.recovery_after,self.recovery_boundary,limit+1)).fetchall()
        interrupted,reconciled=0,0
        for descriptor in rows[:limit]:
            self._recovery_current()
            if _cancelled(cancelled): break
            try:
                with self._database(write=True) as db:
                    row=db.execute(f'SELECT * FROM {SCOPES} WHERE scope_seq=?',(descriptor['scope_seq'],)).fetchone()
                    if row is not None:
                        _session(db,row['session_id'])
                        if row['inflight_token'] is not None and row['inflight_owner']!=self.owner:
                            batch={'schema':'recording-speaker-probe-interrupted-v1','session_id':row['session_id'],
                                'job_id':row['inflight_job_id'],'result_sha256':row['inflight_result_sha256'],
                                'policy_sha256':row['policy_sha256'],'space_id':row['space_id'],
                                'nodes':[],'outcomes':[{'status':'probe_interrupted'}],'witnesses':[]}
                            if row['inflight_batch_json'] is not None:
                                batch=_json(row['inflight_batch_json'],262144)
                            self._finish(db,row,encoded(batch),len(batch['nodes']),'probe_interrupted')
                            interrupted+=1
                if row is not None and recompute is not None and self.enabled:
                    reconciled+=int(self.recompute_scope(descriptor['scope_id'],recompute,cancelled=cancelled)['saved'])
            except Exception as error:
                self._mark_unavailable(descriptor['scope_id'],error)
            self.recovery_after=descriptor['scope_seq']
        self.recovery_remaining=any(x['scope_seq']>self.recovery_after for x in rows)
        return {'interrupted':interrupted,'reconciled':reconciled,'remaining_work':self.recovery_remaining}

    def read_saved(self, sid, recording_id):
        with self._database() as db:
            return read_saved_for_recording(db,sid,recording_id)
