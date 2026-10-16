"""Bounded immutable MOSS history in the primary database, never execution authority."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
from urllib.parse import quote

from .native_owned_support_cache import ReadError, _identifier

MAX_RESULT_BYTES, MAX_METADATA_BYTES = 262144, 16384
APPLICATION_METADATA_BYTES, MAX_DETAIL_BYTES, MAX_LIST_BYTES = 14336, 16384, 32768
ACCOUNTING_BYTES, RESERVATION_BYTES = 16384, 294912
MAX_SEQUENCE = (1 << 63) - 1
HARD_ROWS, HARD_SESSION_ROWS = 1048576, 262144
HARD_BYTES, HARD_SESSION_BYTES = 64 * 1024**3, 16 * 1024**3
WRITE_SECONDS, MAX_VM_STEPS = .250, 50000
WRITE_ALLOWANCE, SCRATCH_ALLOWANCE, CELL_ALLOWANCE = 8 * 1024**2, 2 * 1024**2, 64 * 1024**2
FREE_FLOOR = 11 * 1024**3 // 2
TABLE = 'moss_retained_results_v1'
USAGE = 'moss_result_usage_v1'
IDENTITY_COLUMNS = 'job_id,session_id,original_jobs_epoch,idempotency_key,canonical_request_sha256'
COLUMNS = 'archive_seq,' + IDENTITY_COLUMNS + ',state,schema_version,metadata_json,result_sha256,committed_at,charge_bytes'
SCHEMA = {
    TABLE: f'''CREATE TABLE {TABLE} (
        archive_seq INTEGER PRIMARY KEY AUTOINCREMENT CHECK(archive_seq>0),
        job_id TEXT NOT NULL UNIQUE CHECK(typeof(job_id)='text' AND length(job_id)=32 AND job_id NOT GLOB '*[^a-f0-9]*'),
        session_id TEXT NOT NULL CHECK(typeof(session_id)='text' AND length(session_id) BETWEEN 1 AND 64 AND session_id NOT GLOB '*[^A-Za-z0-9_-]*'),
        original_jobs_epoch TEXT NOT NULL CHECK(typeof(original_jobs_epoch)='text' AND length(original_jobs_epoch)=32 AND original_jobs_epoch NOT GLOB '*[^a-f0-9]*'),
        idempotency_key TEXT NOT NULL CHECK(typeof(idempotency_key)='text' AND length(idempotency_key) BETWEEN 1 AND 64 AND idempotency_key NOT GLOB '*[^A-Za-z0-9_-]*'),
        canonical_request_sha256 TEXT NOT NULL CHECK(typeof(canonical_request_sha256)='text' AND length(canonical_request_sha256)=64 AND canonical_request_sha256 NOT GLOB '*[^a-f0-9]*'),
        state TEXT NOT NULL CHECK(state IN ('accepted','committed','no_result','interrupted')),
        schema_version INTEGER NOT NULL CHECK(typeof(schema_version)='integer' AND schema_version=1),
        metadata_json BLOB NOT NULL CHECK(typeof(metadata_json)='blob' AND length(metadata_json)<=16384),
        result_json BLOB CHECK(result_json IS NULL OR (typeof(result_json)='blob' AND length(result_json)<=262144)),
        result_sha256 TEXT CHECK(result_sha256 IS NULL OR (typeof(result_sha256)='text' AND length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^a-f0-9]*')),
        committed_at REAL, charge_bytes INTEGER NOT NULL CHECK(typeof(charge_bytes)='integer' AND charge_bytes BETWEEN 16384 AND 294912),
        CHECK((state='committed' AND result_json IS NOT NULL AND result_sha256 IS NOT NULL AND typeof(committed_at)='real')
           OR (state!='committed' AND result_json IS NULL AND result_sha256 IS NULL AND committed_at IS NULL)),
        CHECK((state='accepted' AND charge_bytes=294912) OR
           (state='committed' AND charge_bytes=length(metadata_json)+length(result_json)+16384) OR
           (state IN ('no_result','interrupted') AND charge_bytes=length(metadata_json)+16384)),
        UNIQUE(session_id,original_jobs_epoch,idempotency_key))''',
    USAGE: f'''CREATE TABLE {USAGE} (
        scope TEXT PRIMARY KEY CHECK(scope='' OR (typeof(scope)='text' AND length(scope) BETWEEN 1 AND 64 AND scope NOT GLOB '*[^A-Za-z0-9_-]*')),
        row_count INTEGER NOT NULL CHECK(typeof(row_count)='integer' AND row_count BETWEEN 0 AND 1048576),
        charge_bytes INTEGER NOT NULL CHECK(typeof(charge_bytes)='integer' AND charge_bytes BETWEEN 0 AND 309237645312))''',
    'moss_result_accepted_seq': f"CREATE INDEX moss_result_accepted_seq ON {TABLE}(archive_seq) WHERE state='accepted'",
    'moss_result_session_seq': f'CREATE INDEX moss_result_session_seq ON {TABLE}(session_id,archive_seq)',
}


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('ascii')


def init_schema(db):
    """Additive only; reject unknown shapes rather than silently migrate history."""
    present = [db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone() for name in SCHEMA]
    if any(present) and not all(present):raise ValueError('Incomplete MOSS result schema')
    for (name, sql), row in zip(SCHEMA.items(), present):
        if row is None:db.execute(sql)
        elif ' '.join(row[0].split()) != ' '.join(sql.split()):raise ValueError('Incompatible MOSS result schema')
    if not any(present):db.execute(f'INSERT INTO {USAGE} VALUES(?,0,0)', ('',))


def _usage(db, sid):
    rows = []
    for scope in ('', sid):
        row = db.execute(f'SELECT row_count,charge_bytes FROM {USAGE} WHERE scope=?', (scope,)).fetchone()
        if row is None:
            if scope == '':raise ReadError(503, 'result_usage_invalid')
            row = (0, 0)
        count, size = row
        if (type(count) is not int or type(size) is not int or not 0 <= count <= HARD_ROWS
                or not count * ACCOUNTING_BYTES <= size <= count * RESERVATION_BYTES):
            raise ReadError(503, 'result_usage_invalid')
        if count == 0:
            query = (f'SELECT 1 FROM {TABLE} LIMIT 1' if scope == '' else
                     f'SELECT 1 FROM {TABLE} INDEXED BY moss_result_session_seq WHERE session_id=? LIMIT 1')
            if db.execute(query, () if scope == '' else (sid,)).fetchone():
                raise ReadError(503, 'result_usage_invalid')
        rows.append((count, size))
    if (rows[1][0] > min(rows[0][0], HARD_SESSION_ROWS)
            or rows[1][1] > min(rows[0][1], HARD_SESSION_BYTES) or rows[0][1] > HARD_BYTES):
        raise ReadError(503, 'result_usage_invalid')
    return rows


def erase_session(db, sid):
    total, own = _usage(db, sid)
    db.execute(f'DELETE FROM {TABLE} WHERE session_id=?', (sid,))
    db.execute(f'UPDATE {USAGE} SET row_count=?,charge_bytes=? WHERE scope=?',
               (total[0] - own[0], total[1] - own[1], ''))
    db.execute(f'DELETE FROM {USAGE} WHERE scope=?', (sid,))


@dataclass(frozen=True, slots=True)
class HistoryIdentity:
    job_id: str
    session_id: str
    original_jobs_epoch: str
    idempotency_key: str
    canonical_request_sha256: str

    def values(self):
        values = tuple(getattr(self, name) for name in IDENTITY_COLUMNS.split(','))
        for value, size in ((self.job_id, 32), (self.original_jobs_epoch, 32), (self.canonical_request_sha256, 64)):
            if type(value) is not str or re.fullmatch('[a-f0-9]{%d}' % size, value) is None:
                raise ReadError(409, 'invalid_history_identity')
        _identifier(self.session_id); _identifier(self.idempotency_key)
        return values


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    identity: HistoryIdentity
    metadata_json: bytes
    result_json: bytes
    result_sha256: str

    def __post_init__(self):
        self.identity.values()
        validate_metadata(self.metadata_json)
        if (type(self.result_json) is not bytes or not 1 <= len(self.result_json) <= MAX_RESULT_BYTES
                or hashlib.sha256(self.result_json).hexdigest() != self.result_sha256):
            raise ReadError(503, 'result_snapshot_invalid')


def validate_metadata(value):
    if type(value) is not bytes or not 1 <= len(value) <= APPLICATION_METADATA_BYTES:
        raise ReadError(503, 'result_metadata_capacity')
    try:
        parsed = json.loads(value)
        if type(parsed) is not dict or encoded(parsed) != value:raise ValueError()
    except (ValueError, UnicodeError, RecursionError):raise ReadError(503, 'result_metadata_invalid') from None
    return parsed


def build_metadata(identity, *, context, support, cell_binding=None, observed=None, outcome=None):
    fixed = {'identity':dict(zip(IDENTITY_COLUMNS.split(','), identity.values())),
             'context':context, 'observed':observed or {}}
    contract = {'retention_version':1, 'observation_contract':'completed-validation-observations-v1',
                'authority':'historical_snapshot', 'source_current':'not_checked',
                'source_valid_at_commit':'not_asserted', 'raw_retention':'volatile',
                'raw_scope':'exact-native-raw-text-utf8', 'timestamp_quality':'model_generated_unverified',
                'speaker_identity':'anonymous_model_local', 'eos':None, 'complete_content':False}
    for value, maximum in ((fixed,3072), (support,4096), (cell_binding,4096), (outcome or {},1024), (contract,1024)):
        if len(encoded(value)) > maximum:raise ReadError(503, 'result_metadata_capacity')
    metadata = encoded({**fixed, 'source_support':support, 'cell_binding':cell_binding,
                        'outcome':outcome or {}, 'contract':contract})
    validate_metadata(metadata)
    return metadata


class MossResultStore:
    def __init__(self, store, *, clock=time.monotonic):
        self.path, self.settings, self.clock = Path(store.path).resolve(), store.settings, clock
        st = self.path.stat(); self.database_identity = (st.st_dev, st.st_ino)
        self.limits = {name: getattr(self.settings, name) for name in (
            'moss_result_max_rows', 'moss_result_max_rows_per_session',
            'moss_result_max_bytes', 'moss_result_max_bytes_per_session')}
        self.write_blocked = False
        self._recovery_lock = self._recovery_file = None
        self.recovery_boundary = None
        self.recovery_remaining = None

    def _headroom(self):
        """Sampled refusal policy, not preallocation or a hard shared-WAL limit."""
        sizes = []
        for suffix in ('', '-wal', '-shm'):
            try:info = Path(str(self.path) + suffix).lstat()
            except FileNotFoundError:
                if not suffix:raise
                sizes.append(0); continue
            if not stat.S_ISREG(info.st_mode):raise OSError('Invalid database family')
            if not suffix and (info.st_dev, info.st_ino) != self.database_identity:raise OSError('Database replaced')
            sizes.append(info.st_size)
        required = (max(FREE_FLOOR, self.settings.disk_reserve_bytes) + WRITE_ALLOWANCE
                    + SCRATCH_ALLOWANCE + sizes[1]
                    + (CELL_ALLOWANCE if getattr(self.settings, 'native_owned_cells_enabled', False) else 0))
        self.write_blocked = shutil.disk_usage(self.path.parent).free < required
        if self.write_blocked:raise ReadError(503, 'result_storage_unavailable')
        return required

    @contextmanager
    def _database(self, *, write=False, budget=None):
        budget = budget if budget is not None else [self.clock() + WRITE_SECONDS, 0]
        deadline, db = budget[0], None
        def check():
            if self.clock() >= deadline or budget[1] >= MAX_VM_STEPS:raise ReadError(503, 'result_storage_unavailable')
        def progress():
            budget[1] += 100
            return int(budget[1] >= MAX_VM_STEPS or self.clock() >= deadline)
        try:
            if write:self._headroom()
            check()
            st = self.path.stat()
            if (st.st_dev, st.st_ino) != self.database_identity:raise OSError('Database replaced')
            db = sqlite3.connect('file:' + quote(str(self.path), safe='/') + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=0)
            db.row_factory = sqlite3.Row
            db.set_progress_handler(progress, 100)
            db.execute('PRAGMA busy_timeout=0')
            if write:self._headroom()
            else:db.execute('PRAGMA query_only=ON')
            check(); db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            check()
            if write:
                db.commit()
                check()  # Late/uncertain completion is reconciled on the next attempt.
                try:self._headroom()
                except (OSError, ReadError):self.write_blocked = True
        except (OSError, sqlite3.Error):
            raise ReadError(503, 'result_storage_unavailable') from None
        finally:
            if db is not None:db.close()

    def bind_recovery(self, instance_lock):
        """Capture a crash boundary only while the actual startup owner is held."""
        from .resources import InstanceLock
        from .moss_handy_jobs import ownership_fence
        if (type(instance_lock) is not InstanceLock or instance_lock.file is None
                or instance_lock.file.closed or Path(instance_lock.path).resolve() != self.path.parent / 'instance.lock'):
            raise ReadError(503, 'result_recovery_unavailable')
        ownership_fence(self.path.parent)
        if self._recovery_file is not None:raise ReadError(503, 'result_recovery_already_bound')
        with self._database() as db:
            row = db.execute(f'SELECT archive_seq FROM {TABLE} ORDER BY archive_seq DESC LIMIT 1').fetchone()
            boundary = row[0] if row is not None else 0
            if type(boundary) is not int or not 0 <= boundary <= MAX_SEQUENCE:
                raise ReadError(503, 'result_recovery_unavailable')
        self._recovery_lock, self._recovery_file = instance_lock, instance_lock.file
        self.recovery_boundary = boundary
        self._recovery_current()
        return boundary

    def reconciliation_status(self):
        return {'status':'unavailable' if self.recovery_boundary is None else
                'unknown' if self.recovery_remaining is None else 'ready',
                'remaining_work':'unknown' if self.recovery_remaining is None else self.recovery_remaining}

    def revoke_recovery(self):
        self.recovery_boundary = None
        self.recovery_remaining = None

    def _recovery_current(self):
        if (self.recovery_boundary is None or self._recovery_lock.file is not self._recovery_file
                or self._recovery_file.closed):raise ReadError(503, 'result_recovery_unavailable')

    def reconcile(self, *, budget=None):
        budget = budget if budget is not None else [self.clock() + WRITE_SECONDS, 0]
        self.recovery_remaining = None
        self._recovery_current()
        with self._database(write=True, budget=budget) as db:
            self._recovery_current()
            rows = db.execute(f"SELECT archive_seq,session_id,charge_bytes,length(metadata_json),result_json IS NULL,result_sha256 IS NULL,committed_at IS NULL,job_id FROM {TABLE} INDEXED BY moss_result_accepted_seq WHERE state='accepted' AND archive_seq<=? ORDER BY archive_seq LIMIT 32", (self.recovery_boundary,)).fetchall()
            for row in rows:
                seq, sid, charged, metadata_bytes, no_result, no_digest, no_commit, job_id = row
                if (charged != RESERVATION_BYTES or type(metadata_bytes) is not int
                        or not 1 <= metadata_bytes <= MAX_METADATA_BYTES or (no_result,no_digest,no_commit) != (1,1,1)):
                    raise ReadError(503, 'result_recovery_unavailable')
                charge = metadata_bytes + ACCOUNTING_BYTES
                self._charge(db, sid, charge - charged)
                changed = db.execute(f"UPDATE {TABLE} SET state='interrupted',charge_bytes=? WHERE archive_seq=? AND state='accepted' AND charge_bytes=? AND result_json IS NULL AND result_sha256 IS NULL AND committed_at IS NULL", (charge,seq,charged)).rowcount
                if changed != 1:raise ReadError(503, 'result_recovery_unavailable')
                from .moss_automatic import reconcile_job
                reconcile_job(db,job_id)
            self._recovery_current()
        with self._database(budget=budget) as db:
            self._recovery_current()
            remaining = db.execute(f"SELECT 1 FROM {TABLE} INDEXED BY moss_result_accepted_seq WHERE state='accepted' AND archive_seq<=? LIMIT 1", (self.recovery_boundary,)).fetchone() is not None
        self.recovery_remaining = remaining
        return {'reconciled':len(rows), 'remaining_work':remaining}

    def _session(self, db, sid):
        _identifier(sid)
        row = db.execute('SELECT deleted,processing_mode FROM sessions WHERE id=?', (sid,)).fetchone()
        if row is None or row[0] == 1:raise ReadError(404, 'session_not_found')
        if type(row[0]) is not int or row[0] != 0:raise ReadError(503, 'result_storage_unavailable')
        if row[1] != 'legacy':raise ReadError(409, 'unsupported_source_mode')

    def _capacity(self, db, sid):
        total, own = _usage(db, sid)
        limits = self.limits
        if (total[0] >= limits['moss_result_max_rows'] or own[0] >= limits['moss_result_max_rows_per_session']
                or total[1] + RESERVATION_BYTES > limits['moss_result_max_bytes']
                or own[1] + RESERVATION_BYTES > limits['moss_result_max_bytes_per_session']):
            exc = ReadError(429, 'result_retention_capacity')
            exc.summary = {'limits':limits, 'global_usage':total, 'session_usage':own,
                           'remedy':'explicit_session_deletion_or_operator_quota_increase',
                           'reconciliation_remaining_work':self.recovery_remaining,
                           'result_reconciliation':self.reconciliation_status()}
            raise exc
        return total, own

    def preflight(self, sid):
        self._recovery_current()
        with self._database() as db:
            self._session(db, sid)
            usage = _usage(db, sid)
            try:self._capacity(db, sid)
            except ReadError as exc:
                if exc.reason != 'result_retention_capacity' or not db.execute(
                        f"SELECT 1 FROM {TABLE} INDEXED BY moss_result_accepted_seq WHERE state='accepted' AND archive_seq<=? LIMIT 1",
                        (self.recovery_boundary,)).fetchone():raise
        try:self._headroom()
        except OSError:raise ReadError(503, 'result_storage_unavailable') from None
        return {'limits':self.limits, 'global_usage':usage[0], 'session_usage':usage[1]}

    def lookup_key(self, sid, epoch, key, digest):
        with self._database() as db:
            self._session(db, sid)
            row = db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE session_id=? AND original_jobs_epoch=? AND idempotency_key=?', (sid, epoch, key)).fetchone()
            if row is None:return None
            if row['canonical_request_sha256'] != digest:raise ReadError(409, 'idempotency_conflict')
            return self._projection(row, epoch, admission_unknown=True)

    def reserve(self, identity, metadata_json, *, automatic_key=None):
        identity.values(); validate_metadata(metadata_json)
        budget = [self.clock() + WRITE_SECONDS, 0]
        self.reconcile(budget=budget)
        with self._database(write=True, budget=budget) as db:
            self._session(db, identity.session_id)
            existing = db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE session_id=? AND original_jobs_epoch=? AND idempotency_key=?', identity.values()[1:4]).fetchone()
            if existing is not None:
                if existing['canonical_request_sha256'] != identity.canonical_request_sha256:raise ReadError(409, 'idempotency_conflict')
                return self._projection(existing, identity.original_jobs_epoch, admission_unknown=True)
            self._capacity(db, identity.session_id)
            if automatic_key is not None:
                from .moss_automatic import admit
                admit(db,automatic_key,identity)
            sequence = db.execute('SELECT seq FROM sqlite_sequence WHERE name=?', (TABLE,)).fetchone()
            if sequence is not None and (type(sequence[0]) is not int or not 0 <= sequence[0] < MAX_SEQUENCE):
                raise ReadError(429, 'result_retention_capacity')
            db.execute(f'INSERT INTO {TABLE}({IDENTITY_COLUMNS},state,schema_version,metadata_json,charge_bytes) VALUES(?,?,?,?,?,\'accepted\',1,?,?)', (*identity.values(), metadata_json, RESERVATION_BYTES))
            db.execute(f'INSERT OR IGNORE INTO {USAGE} VALUES(?,0,0)', (identity.session_id,))
            for scope in ('', identity.session_id):
                db.execute(f'UPDATE {USAGE} SET row_count=row_count+1,charge_bytes=charge_bytes+? WHERE scope=?', (RESERVATION_BYTES, scope))
        return {'job_id':identity.job_id, 'state':'accepted', 'admission_unknown':False,
                'reconciliation_remaining_work':self.recovery_remaining}

    def _row(self, db, identity):
        row = db.execute(f'SELECT *,length(result_json) AS result_bytes FROM {TABLE} WHERE job_id=? AND session_id=?', identity.values()[:2]).fetchone()
        if row is None:raise ReadError(404, 'result_reservation_missing')
        if tuple(row[name] for name in IDENTITY_COLUMNS.split(',')) != identity.values():raise ReadError(409, 'result_identity_conflict')
        return row

    def _charge(self, db, sid, delta):
        _usage(db, sid)
        for scope in ('', sid):
            db.execute(f'UPDATE {USAGE} SET charge_bytes=charge_bytes+? WHERE scope=?', (delta, scope))

    def commit(self, snapshot, *, automatic_key=None):
        if type(snapshot) is not HistorySnapshot:raise ReadError(503, 'result_snapshot_invalid')
        identity = snapshot.identity
        with self._database(write=True) as db:
            self._session(db, identity.session_id); row = self._row(db, identity)
            if row['state'] == 'committed':
                if automatic_key is not None:
                    from .moss_automatic import _linked
                    if _linked(db,automatic_key,identity)['state']!='committed':raise ReadError(409,'automatic_result_binding')
                if (row['metadata_json'], row['result_json'], row['result_sha256']) != (snapshot.metadata_json, snapshot.result_json, snapshot.result_sha256):
                    raise ReadError(409, 'result_snapshot_conflict')
                return self._projection(row, identity.original_jobs_epoch)
            if row['state'] != 'accepted':raise ReadError(409, 'result_reservation_closed')
            if automatic_key is not None:
                from .moss_automatic import complete
                complete(db,automatic_key,identity)
            charge = len(snapshot.metadata_json) + len(snapshot.result_json) + ACCOUNTING_BYTES
            self._charge(db, identity.session_id, charge - row['charge_bytes'])
            db.execute(f"UPDATE {TABLE} SET state='committed',metadata_json=?,result_json=?,result_sha256=?,committed_at=?,charge_bytes=? WHERE job_id=? AND state='accepted'",
                (snapshot.metadata_json, snapshot.result_json, snapshot.result_sha256, time.time(), charge, identity.job_id))
            answer = self._projection(self._row(db, identity), identity.original_jobs_epoch)
        return answer

    def match_committed(self,snapshot,*,automatic_key=None):
        with self._database() as db:
            self._session(db,snapshot.identity.session_id)
            row=self._row(db,snapshot.identity)
            same=(row['state']=='committed' and row['metadata_json']==snapshot.metadata_json
                and row['result_json']==snapshot.result_json and row['result_sha256']==snapshot.result_sha256)
            if same and automatic_key is not None:
                from .moss_automatic import _linked
                same=_linked(db,automatic_key,snapshot.identity)['state']=='committed'
            return same

    def no_result(self, identity, *, automatic_key=None, interrupted=False):
        with self._database(write=True) as db:
            self._session(db, identity.session_id); row = self._row(db, identity)
            if row['state'] == 'accepted':
                if automatic_key is not None:
                    from .moss_automatic import complete
                    complete(db,automatic_key,identity,state='interrupted' if interrupted else 'no_result',
                        reason='execution_interrupted_no_replay' if interrupted else 'execution_no_result')
                charge = len(row['metadata_json']) + ACCOUNTING_BYTES
                self._charge(db, identity.session_id, charge - row['charge_bytes'])
                db.execute(f"UPDATE {TABLE} SET state='no_result',charge_bytes=? WHERE job_id=? AND state='accepted'", (charge, identity.job_id))
            return {'job_id':identity.job_id}

    def _projection(self, row, epoch=None, *, admission_unknown=False):
        metadata = validate_metadata(row['metadata_json'])
        value = {name:row[name] for name in ('archive_seq', *IDENTITY_COLUMNS.split(','), 'state', 'result_sha256', 'committed_at', 'charge_bytes')}
        value.update(metadata=metadata, durable_result_available=row['state'] == 'committed',
            result_available=row['state'] == 'committed', retention='durable_until_explicit_session_deletion',
            expires_at=None, authority='historical_snapshot', source_current='not_checked',
            source_valid_at_commit='not_asserted', eos=None, complete_content=False, raw_retention='volatile')
        if row['state'] == 'accepted' and epoch != row['original_jobs_epoch']:value['state'] = 'interrupted_or_unknown'
        if admission_unknown:value['admission_unknown'] = True
        if len(encoded(value)) > MAX_DETAIL_BYTES:raise ReadError(503, 'result_metadata_capacity')
        return value

    def validate_snapshot(self, snapshot):
        row = dict(zip(IDENTITY_COLUMNS.split(','), snapshot.identity.values()))
        row.update(archive_seq=MAX_SEQUENCE, state='committed', result_sha256=snapshot.result_sha256,
            committed_at=1.7976931348623157e308, charge_bytes=RESERVATION_BYTES,
            metadata_json=snapshot.metadata_json)
        self._projection(row)

    def is_deleted(self, sid):
        with self._database() as db:
            try:self._session(db, sid)
            except ReadError as exc:
                if exc.reason == 'session_not_found':return True
                raise
        return False

    def list(self, sid, *, after=0, limit=20, jobs_epoch=None):
        if type(after) is not int or not 0 <= after <= MAX_SEQUENCE or type(limit) is not int or not 1 <= limit <= 20:
            raise ReadError(409, 'invalid_cursor')
        with self._database() as db:
            self._session(db, sid)
            cursor = db.execute(f'SELECT {COLUMNS} FROM {TABLE} INDEXED BY moss_result_session_seq WHERE session_id=? AND archive_seq>? ORDER BY archive_seq LIMIT ?', (sid, after, limit + 1))
            values, last, more = [], after, False
            for row in cursor:
                if len(values) >= limit:more = True; break
                value = self._projection(row, jobs_epoch)
                candidate = encoded({'jobs':values + [value], 'next_cursor':row['archive_seq'], 'has_more':True})
                if len(candidate) > MAX_LIST_BYTES:more = True; break
                values.append(value); last = row['archive_seq']
            return encoded({'jobs':values, 'next_cursor':last, 'has_more':more})

    def detail(self, sid, jid, *, jobs_epoch=None):
        with self._database() as db:
            self._session(db, sid)
            row = db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE job_id=? AND session_id=?', (jid, sid)).fetchone()
            if row is None:raise ReadError(404, 'job_not_found')
            return encoded(self._projection(row, jobs_epoch))

    def result(self, sid, jid):
        with self._database() as db:
            self._session(db, sid)
            row = db.execute(f'SELECT state,result_json,result_sha256 FROM {TABLE} WHERE job_id=? AND session_id=?', (jid, sid)).fetchone()
            if row is None:raise ReadError(404, 'job_not_found')
            if row['state'] != 'committed':raise ReadError(409, 'result_unavailable')
            body = row['result_json']
            if type(body) is not bytes or not 1 <= len(body) <= MAX_RESULT_BYTES or hashlib.sha256(body).hexdigest() != row['result_sha256']:
                raise ReadError(503, 'result_storage_unavailable')
            return body
