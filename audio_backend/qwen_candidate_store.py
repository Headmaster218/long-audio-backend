"""Isolated offline candidate ledger. Never opens the primary application DB.

Reservations are durable before invocation. Reopening never resubmits an
uncertain attempt. All evidence and its event are committed in one FULL SQLite
transaction; there is no separate result file to race the terminal commit.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .fragment_runtime_contracts import canonical, digest

COMPLETE = frozenset({'returned_text', 'returned_empty'})
ACTIVE = frozenset({'reserved', 'invocation_started'})


class Conflict(ValueError):
    pass


class StaleOwner(RuntimeError):
    pass


def _json(value):
    text = canonical(value)
    if len(text.encode()) > 2 * 1024**2:
        raise ValueError('Candidate ledger envelope exceeds 2 MiB')
    return text


class CandidateStore:
    def __init__(self, root, *, reserve=lambda size: None, fault=lambda point: None):
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError('Candidate directory cannot contain symlinks')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'qwen-candidates.sqlite3'
        if self.path.is_symlink():
            raise ValueError('Unsafe candidate database')
        self.reserve_write, self.fault = reserve, fault
        self.owner = None
        self.reserve_write(65536)
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS run (
              id INTEGER PRIMARY KEY CHECK(id=1), frozen TEXT NOT NULL,
              limits TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
              frames INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0,
              owner TEXT, cancelled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS requests (
              id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
              status TEXT NOT NULL, attempt INTEGER);
            CREATE TABLE IF NOT EXISTS attempts (
              id INTEGER PRIMARY KEY, request_id TEXT NOT NULL,
              owner TEXT NOT NULL, generation INTEGER NOT NULL,
              retry_of INTEGER, retry_key TEXT UNIQUE, started INTEGER NOT NULL DEFAULT 0,
              returned INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
              evidence TEXT, evidence_sha256 TEXT);
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY, request_id TEXT, kind TEXT NOT NULL,
              payload TEXT NOT NULL, sha256 TEXT NOT NULL);
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA cache_size=-128')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        self.reserve_write(65536)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                self.fault('before_commit')
                db.commit()
                self.fault('after_commit')
            except BaseException:
                db.rollback()
                raise

    def _event(self, db, rid, kind, payload):
        text = _json(payload)
        self.reserve_write(len(text.encode()) + 8192)
        db.execute('INSERT INTO events(request_id,kind,payload,sha256) VALUES(?,?,?,?)',
                   (rid, kind, text, hashlib.sha256(text.encode()).hexdigest()))

    def initialize(self, frozen_sha256, requests, limits):
        from .qwen_candidates import validate_candidate_request
        if (not isinstance(frozen_sha256, str) or len(frozen_sha256) != 64
            or any(c not in '0123456789abcdef' for c in frozen_sha256)):
            raise ValueError('Invalid frozen fingerprint')
        if (set(limits) != {'max_calls', 'max_frames'} or any(
            type(v) is not int or v < 1 for v in limits.values())):
            raise ValueError('Explicit positive candidate budgets required')
        if not isinstance(requests, (list, tuple)) or not 0 < len(requests) <= 1024:
            raise ValueError('Bounded nonempty window inventory required')
        for request in requests:
            validate_candidate_request(request)
        if len({r['request_id'] for r in requests}) != len(requests):
            raise ValueError('Duplicate frozen windows')
        with self._transaction() as db:
            old = db.execute('SELECT * FROM run WHERE id=1').fetchone()
            if old and (old['frozen'] != frozen_sha256 or old['limits'] != canonical(limits)):
                raise Conflict('Output already binds another frozen run or budget')
            if not old:
                db.execute('INSERT INTO run(id,frozen,limits) VALUES(1,?,?)',
                           (frozen_sha256, canonical(limits)))
            for request in requests:
                self._register(db, request['request_id'], request)

    def _register(self, db, key, request):
        rid, payload = request['request_id'], _json(request)
        old = db.execute('SELECT * FROM requests WHERE key=? OR id=?', (key, rid)).fetchall()
        if old:
            if len(old) != 1 or old[0]['id'] != rid or old[0]['key'] != key or old[0]['payload'] != payload:
                raise Conflict('Idempotency key or candidate ID already has another payload')
            return
        db.execute('INSERT INTO requests VALUES(?,?,?, ?,NULL)', (rid, key, payload, 'planned'))
        self._event(db, rid, 'planned', {'status': 'planned', 'request_id': rid})

    def register(self, key, request):
        from .qwen_candidates import validate_candidate_request
        validate_candidate_request(request)
        if not isinstance(key, str) or not 0 < len(key) <= 256:
            raise ValueError('Bounded idempotency key required')
        with self._transaction() as db:
            self._register(db, key, request)

    def acquire(self, identity):
        """Caller must hold the directory's exclusive runner lease throughout.

        Recovery preserves uncertain reservations, including ones not started.
        No inference may be resumed from an existing attempt.
        """
        owner = {'token': uuid.uuid4().hex, 'identity': identity}
        with self._transaction() as db:
            run = db.execute('SELECT * FROM run WHERE id=1').fetchone()
            if run is None or run['cancelled']:
                raise StaleOwner('Run is absent or cancelled')
            for row in db.execute("SELECT * FROM attempts WHERE status IN ('reserved','invocation_started')").fetchall():
                db.execute("UPDATE attempts SET status='interrupted_outcome_unknown' WHERE id=?", (row['id'],))
                db.execute("UPDATE requests SET status='interrupted_outcome_unknown' WHERE id=?", (row['request_id'],))
                self._event(db, row['request_id'], 'interrupted_outcome_unknown',
                            {'attempt_id': row['id'], 'execution_started': bool(row['started']),
                             'status': 'interrupted_outcome_unknown', 'automatic_retry': False})
            # A crash can occur after a failed terminal result but before the
            # remaining-window stop transaction. Recovery must close that gap.
            blocked = db.execute("SELECT 1 FROM requests WHERE status NOT IN ('planned','returned_text','returned_empty') LIMIT 1").fetchone()
            if blocked:
                for row in db.execute("SELECT id FROM requests WHERE status='planned'").fetchall():
                    db.execute("UPDATE requests SET status='not_attempted_after_stop' WHERE id=?", (row['id'],))
                    self._event(db, row['id'], 'not_attempted_after_stop',
                                {'status': 'not_attempted_after_stop', 'reason': 'prior_noncomplete_attempt'})
            db.execute('UPDATE run SET owner=?,generation=generation+1 WHERE id=1', (canonical(owner),))
        self.owner = owner
        return owner

    def _fence(self, db):
        run = db.execute('SELECT * FROM run WHERE id=1').fetchone()
        if not run or run['cancelled'] or not self.owner or run['owner'] != canonical(self.owner):
            raise StaleOwner('Candidate owner cancelled or superseded')
        return run

    def check_owner(self):
        with self._db() as db:
            return dict(self._fence(db))

    def reserve(self, rid, *, retry_of=None, retry_key=None):
        with self._transaction() as db:
            run = self._fence(db)
            row = db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
            if row is None:
                raise ValueError('Unknown candidate')
            if retry_of is None:
                if retry_key is not None or row['status'] != 'planned':
                    raise Conflict('Existing attempt cannot automatically resubmit')
            else:
                prior = db.execute('SELECT * FROM attempts WHERE id=? AND request_id=?', (retry_of, rid)).fetchone()
                if (type(retry_of) is not int or not isinstance(retry_key, str) or not 0 < len(retry_key) <= 256
                    or prior is None or row['attempt'] != retry_of or prior['status'] in ACTIVE | COMPLETE):
                    raise Conflict('Explicit retry must name latest failed/uncertain attempt')
                if db.execute('SELECT 1 FROM attempts WHERE retry_key=?', (retry_key,)).fetchone():
                    raise Conflict('Retry key already consumed; no repeat call')
            request = json.loads(row['payload'])
            frames = request['read']['end_frame'] - request['read']['start_frame']
            limits = json.loads(run['limits'])
            if run['calls'] + 1 > limits['max_calls'] or run['frames'] + frames > limits['max_frames']:
                raise ValueError('Durable call/audio budget exhausted')
            cur = db.execute('INSERT INTO attempts(request_id,owner,generation,retry_of,retry_key,status) VALUES(?,?,?,?,?,?)',
                             (rid, canonical(self.owner), run['generation'], retry_of, retry_key, 'reserved'))
            aid = cur.lastrowid
            db.execute('UPDATE run SET calls=calls+1,frames=frames+? WHERE id=1', (frames,))
            db.execute("UPDATE requests SET status='reserved',attempt=? WHERE id=?", (aid, rid))
            self._event(db, rid, 'reserved', {'status': 'reserved', 'attempt_id': aid,
                                            'retry_of': retry_of, 'frames': frames})
        return aid

    def started(self, aid):
        with self._transaction() as db:
            run = self._fence(db)
            row = db.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
            if not row or row['status'] != 'reserved' or row['owner'] != run['owner'] or row['generation'] != run['generation']:
                raise StaleOwner('Attempt start is not current or is repeated')
            db.execute("UPDATE attempts SET started=1,status='invocation_started' WHERE id=?", (aid,))
            db.execute("UPDATE requests SET status='invocation_started' WHERE id=?", (row['request_id'],))
            self._event(db, row['request_id'], 'invocation_started',
                        {'status': 'invocation_started', 'attempt_id': aid, 'execution_started': True})

    def finish(self, aid, status, evidence, *, returned=False):
        if not isinstance(status, str) or not status or status in ACTIVE or type(returned) is not bool:
            raise ValueError('Explicit terminal status required')
        if not isinstance(evidence, dict):
            raise ValueError('Explicit evidence object required')
        if 'native' in evidence:
            from .qwen_candidates import validate_native_result
            native = validate_native_result(evidence['native'])
            validation_failure = (status in {'source_changed', 'provenance_mismatch'} and
                                  isinstance(evidence.get('validation_failure'), dict))
            if native['status'] != status and not validation_failure:
                raise ValueError('Terminal status disagrees with actual native evidence')
        elif status in COMPLETE:
            raise ValueError('Successful candidate requires validated native evidence')
        encoded = _json(evidence)
        with self._transaction() as db:
            row = db.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
            run = db.execute('SELECT * FROM run WHERE id=1').fetchone()
            cancelled_not_invoked = bool(row and row['status'] == 'cancelled_before_start' and
                                         not row['started'] and run['cancelled'])
            if not row or (row['status'] not in ACTIVE and not cancelled_not_invoked) or not self.owner or row['owner'] != canonical(self.owner):
                raise StaleOwner('Terminal receipt does not belong to active attempt')
            if cancelled_not_invoked and (returned or status in COMPLETE):
                raise ValueError('Cancelled uninvoked attempt cannot have a native return')
            if cancelled_not_invoked and row['evidence'] is not None:
                if row['evidence'] != encoded:
                    raise Conflict('Cancellation terminal evidence is immutable')
                return
            if status in COMPLETE and (not returned or not row['started']):
                raise ValueError('Success requires durable invocation and actual return')
            if run['cancelled'] or run['generation'] != row['generation'] or run['owner'] != row['owner']:
                status = 'cancelled_before_start' if run['cancelled'] and not row['started'] else 'interrupted_outcome_unknown'
            sha = hashlib.sha256(encoded.encode()).hexdigest()
            self.reserve_write(len(encoded.encode()) + 16384)
            db.execute('UPDATE attempts SET status=?,returned=?,evidence=?,evidence_sha256=? WHERE id=?',
                       (status, int(returned), encoded, sha, aid))
            db.execute('UPDATE requests SET status=? WHERE id=?', (status, row['request_id']))
            self._event(db, row['request_id'], status, {'status': status, 'attempt_id': aid,
                        'execution_started': bool(row['started']), 'execution_returned': returned,
                        'evidence_sha256': sha})

    def stop_remaining(self, reason):
        with self._transaction() as db:
            for row in db.execute("SELECT id FROM requests WHERE status='planned'").fetchall():
                db.execute("UPDATE requests SET status='not_attempted_after_stop' WHERE id=?", (row['id'],))
                self._event(db, row['id'], 'not_attempted_after_stop',
                            {'status': 'not_attempted_after_stop', 'reason': str(reason)[:512]})

    def cancel(self):
        with self._transaction() as db:
            db.execute('UPDATE run SET cancelled=1,generation=generation+1 WHERE id=1')
            for row in db.execute("SELECT id FROM requests WHERE status='planned'").fetchall():
                db.execute("UPDATE requests SET status='cancelled_before_start' WHERE id=?", (row['id'],))
                self._event(db, row['id'], 'cancelled_before_start', {'status': 'cancelled_before_start'})
            # Cancellation and invocation ACK are serialized by BEGIN IMMEDIATE.
            # An unstarted reservation is therefore positively not invoked.
            for row in db.execute("SELECT id,request_id FROM attempts WHERE status='reserved' AND started=0").fetchall():
                db.execute("UPDATE attempts SET status='cancelled_before_start' WHERE id=?", (row['id'],))
                db.execute("UPDATE requests SET status='cancelled_before_start' WHERE id=?", (row['request_id'],))
                self._event(db, row['request_id'], 'cancelled_before_start',
                            {'status': 'cancelled_before_start', 'reason_code': 'cancelled_not_invoked',
                             'attempt_id': row['id'], 'execution_started': False})
            self._event(db, None, 'cancel_requested', {'status': 'cancel_requested'})

    def receipt(self, value):
        with self._transaction() as db:
            self._event(db, None, 'owner_receipt', value)

    def request(self, rid):
        from .qwen_candidates import validate_candidate_request
        with self._db() as db:
            row = db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
            if row is None:
                raise KeyError(rid)
            request = validate_candidate_request(json.loads(row['payload']))
            attempts = []
            for item in db.execute('SELECT * FROM attempts WHERE request_id=? ORDER BY id', (rid,)):
                value = dict(item)
                if value['evidence'] is not None:
                    if hashlib.sha256(value['evidence'].encode()).hexdigest() != value['evidence_sha256']:
                        raise ValueError('Corrupt candidate evidence')
                    value['evidence'] = json.loads(value['evidence'])
                attempts.append(value)
            return {'request': request, 'status': row['status'], 'attempts': attempts}

    def events(self, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid candidate event cursor/page')
        items, size = [], 0
        with self._db() as db:
            rows = db.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?', (after, limit + 1)).fetchall()
            for row in rows[:limit]:
                if hashlib.sha256(row['payload'].encode()).hexdigest() != row['sha256']:
                    raise ValueError('Corrupt candidate event')
                item = {'seq': row['seq'], 'request_id': row['request_id'], 'kind': row['kind'],
                        'payload': json.loads(row['payload'])}
                cost = len(canonical(item).encode())
                if size + cost > 1024**2:
                    break
                items.append(item); size += cost
        return {'events': items, 'next_cursor': items[-1]['seq'] if items else after,
                'has_more': len(rows) > len(items), 'candidate_only': True,
                'cursor_semantics': 'append-only candidate event sequence'}
