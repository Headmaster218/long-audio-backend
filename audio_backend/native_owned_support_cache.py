"""Volatile, default-off native support storage and read-only primary-state gates.

No schema, provider, decoder or background worker belongs here. A fresh SQLite
snapshot authorizes each response; its first SELECT is the linearization point.
Leases remain owned by the ASGI response until sending finishes or is cancelled.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import threading
import time
from urllib.parse import quote
import uuid

MAX_RETAINED_BYTES = 4 * 1024 * 1024
MARKER_RESERVE_BYTES = 32 * 1024
MAX_SUPPORTS = 4
MAX_MARKERS = 64
TTL_SECONDS = 900
MAX_PCM_BYTES = 640000
MAX_PROOF_BYTES = 65536
MAX_PUBLIC_BYTES = 16384
STAGE_BYTES = MAX_PCM_BYTES + MAX_PROOF_BYTES + MAX_PUBLIC_BYTES + 2048
READ_RESERVATIONS = {'list': 768 * 1024, 'detail': 128 * 1024, 'audio': 768 * 1024,
    'jobs_result': 768 * 1024, 'jobs_raw': 256 * 1024, 'jobs_detail': 128 * 1024,
    'jobs_list': 256 * 1024, 'jobs_mutation': 128 * 1024}
MAX_DISPATCH_BYTES = 768 * 1024
MAX_REGISTRY_OWNERS = 8
MAX_READ_LEASES = 2
MAX_READ_BYTES = 1536 * 1024
# Separate transient bridge metadata accounting, never durable producer staging.
BRIDGE_OPERATION_BYTES = 32 * 1024 * 1024
MAX_ROWS = 1024
MAX_SQL_BYTES = 256 * 1024
MAX_VM_STEPS = 150000
MAX_SOURCE_BYTES = 40 * 1024 * 1024
READ_SECONDS = .5
LOCK_SECONDS = .005
_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_DECIMAL = re.compile(r'(?:0|[1-9][0-9]{0,18})\Z')
_CHUNK_FIELDS = ('id', 'session_id', 'status', 'duration', 'timeline_start',
                 'next_offset', 'recorded_at', 'created', 'error')


def _trigger_sql(timing):
    # Exact unchanged change_feed.initialize SQL for the pinned legacy schema.
    ref = 'OLD' if timing == 'DELETE' else 'NEW'
    operation = 'delete' if timing == 'DELETE' else 'upsert'
    pairs = ','.join(f"'{column}',{ref}.{column}" for column in _CHUNK_FIELDS)
    payload = 'NULL' if timing == 'DELETE' else f'json_object({pairs})'
    return f'''CREATE TRIGGER feed_chunks_{timing.lower()} AFTER {timing} ON chunks  BEGIN
                    INSERT INTO result_changes(session_id,entity,entity_id,operation,data,created)
                    SELECT session_id,'chunks',CAST({ref}.id AS TEXT),'{operation}',{payload},
                    (julianday('now')-2440587.5)*86400 FROM (SELECT {ref}.session_id AS session_id); END'''


PINNED_CHUNK_TRIGGER_SQL = {f'feed_chunks_{x.lower()}': _trigger_sql(x)
                            for x in ('INSERT', 'UPDATE', 'DELETE')}


class ReadError(Exception):
    def __init__(self, status_code, reason):
        super().__init__(reason)
        self.status_code, self.reason = status_code, reason


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('ascii')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ReadError(409, 'invalid_identifier')
    return value


def parse_query(raw, kind):
    """No URL-decoding ambiguity, duplicates, non-ASCII, or unbounded parsing."""
    if type(raw) is not bytes or len(raw) > 1024:
        raise ReadError(409, 'invalid_query')
    allowed = {'expected_capture_epoch', 'expected_revision'}
    if kind == 'list':
        allowed = {'after', 'limit', 'expected_capture_epoch'}
    elif kind not in ('detail', 'audio'):
        raise ReadError(409, 'invalid_query')
    try:
        text = raw.decode('ascii')
    except UnicodeDecodeError:
        raise ReadError(409, 'invalid_query') from None
    result = {}
    fields = text.split('&') if text else []
    if len(fields) > 4:
        raise ReadError(409, 'invalid_query')
    for field in fields:
        if field.count('=') != 1:
            raise ReadError(409, 'invalid_query')
        key, value = field.split('=')
        if key not in allowed or key in result:
            raise ReadError(409, 'invalid_query')
        if key == 'expected_capture_epoch':
            if not re.fullmatch('[a-f0-9]{32}', value):
                raise ReadError(409, 'invalid_query')
            result[key] = value
        else:
            if not _DECIMAL.fullmatch(value):
                raise ReadError(409, 'invalid_query')
            result[key] = int(value)
    if kind == 'list':
        if not 1 <= result.get('limit', 20) <= 20:
            raise ReadError(409, 'invalid_query')
        if result.get('after', 0) and 'expected_capture_epoch' not in result:
            raise ReadError(409, 'capture_epoch_required')
    return result


class ReadBudget:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + READ_SECONDS
        self.rows = self.sql_bytes = self.vm_steps = self.source_bytes = 0
        self.sql_exhausted = False

    def check(self):
        if self.clock() >= self.deadline:
            raise ReadError(503, 'validation_deadline')

    def remaining_lock(self):
        self.check()
        return max(0., min(LOCK_SECONDS, self.deadline - self.clock()))

    def account_sql(self, count):
        # SQLite invokes this with integer lengths, BEFORE returning any text.
        self.check()
        if type(count) is not int or count < 0 or self.sql_bytes + count > MAX_SQL_BYTES:
            self.sql_exhausted = True
            raise ReadError(503, 'sql_byte_budget')
        self.sql_bytes += count
        return 1

    def account_read(self, count):
        self.check()
        self.source_bytes += count
        if self.source_bytes > MAX_SOURCE_BYTES:
            raise ReadError(503, 'source_byte_budget')

    def progress(self):
        self.vm_steps += 100
        return int(self.vm_steps > MAX_VM_STEPS or self.clock() >= self.deadline)


def _text_sql(column, maximum, nullable=False):
    valid = f"typeof({column})='text' AND length(CAST({column} AS BLOB))<={maximum}"
    if nullable:
        valid = f'({column} IS NULL OR ({valid}))'
    # Validity flag distinguishes a genuine nullable value from a rejected field.
    return (f'CASE WHEN {valid} THEN CASE WHEN {column} IS NULL THEN NULL '
            f'WHEN nowned_transfer(length(CAST({column} AS BLOB))) THEN {column} END ELSE NULL END',
            f'CASE WHEN {valid} THEN 1 ELSE 0 END')


def _number_sql(column, integer=False):
    valid = f"typeof({column})='integer'" if integer else f"typeof({column}) IN ('integer','real')"
    return f'CASE WHEN {valid} THEN {column} ELSE NULL END', f'CASE WHEN {valid} THEN 1 ELSE 0 END'


def _signature(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _source_identity(path, frames, budget, digest=None):
    """Nonblocking regular-file inspection; no decoder or symlink following."""
    budget.check()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise ReadError(409, 'source_changed')
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or _signature(before) != _signature(opened)
                    or not 44 <= opened.st_size <= frames * 2 + 1024 * 1024):
                raise ReadError(409, 'source_changed')
            if digest is not None:
                h = hashlib.sha256()
                remaining = opened.st_size
                while remaining:
                    size = min(32768, remaining)
                    budget.account_read(size)
                    block = os.read(fd, size)
                    budget.check()
                    if not block:
                        raise ReadError(409, 'source_changed')
                    h.update(block)
                    remaining -= len(block)
                budget.account_read(1)
                if os.read(fd, 1) or h.hexdigest() != digest:
                    raise ReadError(409, 'source_changed')
            budget.check()
            if (_signature(os.fstat(fd)) != _signature(opened)
                    or _signature(os.lstat(path)) != _signature(opened)):
                raise ReadError(409, 'source_changed')
            budget.check()
            return _signature(opened)
        finally:
            os.close(fd)
    except OSError:
        raise ReadError(409, 'source_changed') from None


@dataclass(frozen=True, slots=True)
class _Reservation:
    sid: str


@dataclass(frozen=True, slots=True)
class PendingToken:
    epoch: str
    generation: int
    nonce: str
    sid: str
    cid: str
    watermark: int
    frames: int
    binding: str
    source_signature: tuple
    path: str
    digest: str


@dataclass(frozen=True, slots=True)
class _Entry:
    sid: str
    cid: str
    revision: int
    generation: int
    watermark: int
    frames: int
    binding: str
    source_generation: str
    policy: str
    proof: bytes
    pcm: bytes
    support: bytes
    selected: bool
    reason: str
    expires: float
    charged: int


@dataclass(frozen=True, slots=True)
class _ReadTarget:
    """Small immutable projection; only audio may retain a shared PCM reference."""
    sid: str
    cid: str
    revision: int
    generation: int
    watermark: int
    frames: int
    binding: str
    source_generation: str
    policy: str
    support: bytes
    selected: bool
    reason: str
    expires: float
    pcm: bytes = b''


def _read_target(entry, *, audio=False):
    return _ReadTarget(entry.sid, entry.cid, entry.revision, entry.generation,
                       entry.watermark, entry.frames, entry.binding,
                       entry.source_generation, entry.policy, entry.support,
                       entry.selected, entry.reason, entry.expires,
                       entry.pcm if audio else b'')


class Lease:
    """Exactly-once release, including cancellation and failed ASGI sends."""
    def __init__(self, owner, reserved):
        self.owner, self.reserved, self.released = owner, reserved, False
        self._release_requested = threading.Event()

    def release(self):
        self._release_requested.set()
        if not self.owner._lease_lock.acquire(timeout=LOCK_SECONDS):
            self.owner._lease_cleanup_pending.set()
            return
        try:
            self.owner._drain_leases_locked()
        finally:
            self.owner._lease_lock.release()


@dataclass(frozen=True, slots=True)
class ReadResult:
    body: bytes | tuple[bytes, bytes]
    media_type: str
    lease: Lease
    status_code: int = 200


@dataclass(frozen=True, slots=True)
class DispatchToken:
    owner: str
    database_identity: tuple
    epoch: str
    invalidation_serial: int
    support_id: str
    target: _ReadTarget


class DispatchLease:
    """One immutable support/PCM pin, charged through positive job cleanup."""
    def __init__(self, owner, token, support, pcm):
        self.owner, self.token, self.support = owner, token, support
        self.pcm = pcm
        self.cancelled = threading.Event()
        self.released = False
        self._release_requested = threading.Event()

    def release_pcm(self):
        self.pcm = None

    def release(self):
        self._release_requested.set()
        self.release_pcm()
        if not self.owner._lock.acquire(timeout=LOCK_SECONDS):
            self.cancelled.set()
            self.owner._dispatch_cleanup_pending.set()
            return False
        try:
            if self.owner._dispatch is self:
                self.owner._dispatch = None
            self.released = True
            self.owner._dispatch_cleanup_pending.clear()
            return True
        finally:
            self.owner._lock.release()


class BridgeOperationLease:
    """One worker owns this reservation until actual completion/serialization.

    It is deliberately not an ASGI response lease: disconnect cannot release a
    still-running worker's materialized canonical metadata. The original result
    response pool remains independently charged until ASGI completion.
    """
    def __init__(self, owner):
        self.owner = owner
        self.reserved = BRIDGE_OPERATION_BYTES
        self.released = False
        self._release_lock = threading.Lock()

    def release(self):
        with self._release_lock:
            if self.released:
                return
            self.released = True
            self.owner._bridge_operation_count = 0
            self.owner._bridge_operation_bytes = 0
            self.owner._bridge_operation_lock.release()


class ReadResponseBudget:
    """One aggregate lease pool shared by source, live-job and history responses."""
    def __init__(self):
        self._lease_lock = threading.Lock()
        self._lease_count = self._lease_bytes = 0
        self._leases = []
        self._lease_cleanup_pending = threading.Event()
        self._bridge_operation_lock = threading.Lock()
        self._bridge_operation_count = self._bridge_operation_bytes = 0

    def reserve_bridge_operation(self):
        if not self._bridge_operation_lock.acquire(blocking=False):
            raise ReadError(503, 'read_capacity')
        try:
            lease = BridgeOperationLease(self)
            self._bridge_operation_count = 1
            self._bridge_operation_bytes = BRIDGE_OPERATION_BYTES
            return lease
        except BaseException:
            self._bridge_operation_lock.release()
            raise

    def _drain_leases_locked(self):
        for lease in tuple(self._leases):
            if lease._release_requested.is_set() and not lease.released:
                lease.released = True
                self._lease_count -= 1
                self._lease_bytes -= lease.reserved
                self._leases.remove(lease)
        self._lease_cleanup_pending.clear()

    def _lease(self, kind, budget):
        if not self._lease_lock.acquire(timeout=budget.remaining_lock()):
            raise ReadError(503, 'read_capacity')
        try:
            budget.check()
            self._drain_leases_locked()
            amount = READ_RESERVATIONS[kind]
            if self._lease_count >= MAX_READ_LEASES or self._lease_bytes + amount > MAX_READ_BYTES:
                raise ReadError(503, 'read_capacity')
            self._lease_count += 1
            self._lease_bytes += amount
            lease = Lease(self, amount)
            self._leases.append(lease)
            return lease
        finally:
            self._lease_lock.release()

    def reserve_jobs_response(self, kind):
        return self._lease('jobs_' + kind, ReadBudget())


class NativeOwnedSupportCache:
    def __init__(self, store, *, clock=time.monotonic, response_budget=None):
        self.store = store
        self.path = str(Path(store.path).resolve())
        st = os.stat(self.path)
        self.database_identity = (st.st_dev, st.st_ino)
        self.clock = clock
        self.epoch = uuid.uuid4().hex
        self._lock = threading.Lock()
        self.response_budget = response_budget if response_budget is not None else ReadResponseBudget()
        self._lease_lock = self.response_budget._lease_lock
        self._blocked = threading.Event()
        self._cleanup_pending = threading.Event()
        self._entries = {}
        self._generations = {}
        self._pending = None
        self._revision = 0
        self._invalidation_serial = 0
        self._retained = 0
        self._marker_bytes = 0
        self._leases = self.response_budget._leases
        self._lease_cleanup_pending = self.response_budget._lease_cleanup_pending
        self._dispatch = None
        self._dispatch_owner = uuid.uuid4().hex
        self._dispatch_cleanup_pending = threading.Event()
        self.jobs_owner = None

    def _enabled(self):
        return bool(getattr(self.store.settings, 'native_owned_support_enabled', False))

    @contextmanager
    def _gate(self, budget=None, *, allow_blocked=False):
        wait = budget.remaining_lock() if budget else LOCK_SECONDS
        if not self._lock.acquire(timeout=wait):
            raise ReadError(503, 'cache_lock_unavailable')
        try:
            if budget:
                budget.check()
            if self._blocked.is_set() and not allow_blocked:
                raise ReadError(409, 'capture_epoch_closed')
            yield
        finally:
            self._lock.release()

    def _drain_dispatch_locked(self):
        lease = self._dispatch
        if lease is not None and lease._release_requested.is_set():
            lease.released = True
            self._dispatch = None
            self._dispatch_cleanup_pending.clear()

    def _expire(self):
        self._drain_dispatch_locked()
        now = self.clock()
        dispatch = self._dispatch
        if dispatch is not None and (now >= dispatch.token.target.expires or not self._enabled()):
            dispatch.cancelled.set()
        for key, entry in tuple(self._entries.items()):
            if now >= entry.expires:
                self._remove(key)

    def _remove(self, key):
        dispatch = self._dispatch
        if dispatch is not None and key == (dispatch.token.target.sid, dispatch.token.target.cid):
            dispatch.cancelled.set()
        entry = self._entries.pop(key)
        if entry.selected:
            self._retained -= entry.charged
        else:
            self._marker_bytes -= entry.charged

    @contextmanager
    def _database(self, budget):
        budget.check()
        try:
            st = os.stat(self.path)
            if (st.st_dev, st.st_ino) != self.database_identity:
                raise ReadError(409, 'database_binding_changed')
            db = sqlite3.connect('file:' + quote(self.path, safe='/') + '?mode=ro',
                                 uri=True, timeout=0)
            try:
                db.set_progress_handler(budget.progress, 100)
                db.create_function('nowned_transfer', 1, budget.account_sql)
                db.execute('PRAGMA busy_timeout=0')
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                yield db
                budget.check()
            finally:
                db.close()
        except (OSError, sqlite3.Error) as exc:
            budget.check()
            reason = ('sql_byte_budget' if budget.sql_exhausted else
                      'sqlite_vm_budget' if budget.vm_steps > MAX_VM_STEPS else 'primary_gate_unavailable')
            raise ReadError(503, reason) from exc

    def _fetch(self, db, sql, parameters, budget):
        budget.check()
        budget.account_sql(256)  # Bounds all numeric/NULL columns of these fixed SELECTs.
        row = db.execute(sql, parameters).fetchone()
        budget.check()
        return row

    def _highwater(self, db, budget):
        row = self._fetch(db, "SELECT CASE WHEN typeof(seq)='integer' AND seq>=0 THEN seq ELSE NULL END FROM sqlite_sequence WHERE name='result_changes'", (), budget)
        if row is None:
            return 0
        if row[0] is None:
            raise ReadError(503, 'malformed_feed_watermark')
        return row[0]

    def _triggers(self, db, budget):
        for name, expected in PINNED_CHUNK_TRIGGER_SQL.items():
            value, valid = _text_sql('sql', 8192)
            row = self._fetch(db, f"SELECT {value},{valid} FROM sqlite_master WHERE type='trigger' AND name=?", (name,), budget)
            if row is None or row[1] != 1 or row[0] != expected:
                raise ReadError(503, 'untrusted_change_feed_trigger')

    def _session(self, db, sid, budget):
        mode, valid = _text_sql('processing_mode', 32)
        row = self._fetch(db, f"SELECT CASE WHEN typeof(deleted)='integer' THEN deleted ELSE NULL END,{mode},{valid} FROM sessions WHERE id=?", (sid,), budget)
        if row is None or row[0] == 1:
            raise ReadError(404, 'session_not_found')
        if row[0] != 0 or row[2] != 1:
            raise ReadError(503, 'malformed_session')
        if row[1] != 'legacy':
            raise ReadError(409, 'unsupported_source_mode')

    def _private(self, db, sid, cid, budget, status):
        self._session(db, sid, budget)
        specs = [('c.id', 64, False), ('c.session_id', 64, False),
                 ('c.digest', 64, False), ('c.path', 4096, False),
                 ('c.idempotency_key', 256, False), ('c.recorded_at', 128, True),
                 ('c.status', 32, False), ('s.identity_group', 256, False)]
        expressions = []
        for col, cap, null in specs:
            expressions.extend(_text_sql(col, cap, null))
        for col in ('c.duration', 'c.timeline_start', 'c.next_offset', 'c.created', 's.created'):
            expressions.extend(_number_sql(col))
        expressions.append('EXISTS(SELECT 1 FROM stream_chunks WHERE chunk_id=c.id)')
        row = self._fetch(db, 'SELECT ' + ','.join(expressions) + ' FROM chunks c JOIN sessions s ON s.id=c.session_id WHERE c.id=? AND c.session_id=?', (cid, sid), budget)
        if row is None:
            raise ReadError(404, 'chunk_not_found')
        if any(row[i] != 1 for i in range(1, 26, 2)):
            raise ReadError(503, 'malformed_source_evidence')
        values = list(row[0:26:2])
        for value in values[8:]:
            if not math.isfinite(value):
                raise ReadError(503, 'malformed_source_evidence')
        ids, owner, digest, path, key, recorded, state, group, duration, origin, offset, created, session_created = values
        if _HASH.fullmatch(digest) is None:
            raise ReadError(503, 'malformed_source_digest')
        frames = round(duration * 16000)
        if (row[26] or origin != 0 or not 1 <= frames <= 320000 or duration != frames / 16000):
            raise ReadError(409, 'unsupported_source_mode')
        if state != status or offset != (0 if status == 'running' else duration):
            raise ReadError(409, 'source_control_changed')
        # Binding excludes the two expected mutable commit fields only.
        baseline = [ids, owner, digest, path, key, recorded, group, duration,
                    origin, created, session_created, 'legacy']
        return baseline, int(frames)

    def _projected_header(self, db, sid, cid, budget):
        # json_object intentionally emits SQLite's exact numeric representation,
        # which can differ from Python's raw REAL->float representation. Compare
        # feed fields to the same projection, without an epsilon. Raw private
        # values remain in the separate binding hash above.
        expression = ("CASE WHEN typeof(created) IN ('integer','real') AND "
                      "(recorded_at IS NULL OR (typeof(recorded_at)='text' AND "
                      "length(CAST(recorded_at AS BLOB))<=128)) THEN "
                      "json_object('created',created,'recorded_at',recorded_at) ELSE NULL END")
        value, valid = _text_sql(expression, 512)
        row = self._fetch(db, f'SELECT {value},{valid} FROM chunks WHERE id=? AND session_id=?',
                          (cid, sid), budget)
        if row is None or row[1] != 1:
            raise ReadError(503, 'malformed_source_evidence')
        budget.check()
        try:
            projected = json.loads(row[0], object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            raise ReadError(503, 'malformed_source_evidence') from None
        if (type(projected) is not dict or set(projected) != {'created', 'recorded_at'}
                or type(projected['created']) not in (int, float)
                or not math.isfinite(projected['created'])
                or (projected['recorded_at'] is not None
                    and (type(projected['recorded_at']) is not str
                         or len(projected['recorded_at'].encode('utf-8')) > 128))):
            raise ReadError(503, 'malformed_source_evidence')
        budget.check()
        return projected['created'], projected['recorded_at']

    def _binding(self, baseline, signature):
        return hashlib.sha256(_json([baseline, signature])).hexdigest()

    def _feed(self, db, sid, targets, highwater, budget, *, complete=True, headers=None):
        if not targets:
            return
        first = min(t.watermark for t in targets)
        if highwater <= first:
            raise ReadError(409, 'claim_anchor_missing')
        marks = ','.join('?' for _ in targets)
        relevant = f"entity='chunks' AND session_id=? AND entity_id IN ({marks})"
        expressions = ["CASE WHEN nowned_transfer(80) AND typeof(cursor)='integer' THEN cursor ELSE NULL END"]
        for name, maximum in (('session_id', 64), ('entity', 64), ('entity_id', 128), ('operation', 16)):
            expressions.extend(_text_sql(name, maximum))
        expressions.extend([f'CASE WHEN {relevant} THEN 1 ELSE 0 END',
                            f"CASE WHEN {relevant} AND typeof(data)='text' AND length(CAST(data AS BLOB))<=4096 THEN CASE WHEN nowned_transfer(length(CAST(data AS BLOB))) THEN data END ELSE NULL END"])
        params = (sid, *(t.cid for t in targets))
        query = ('SELECT ' + ','.join(expressions) + ' FROM result_changes WHERE cursor>? AND cursor<=? ORDER BY cursor LIMIT ?')
        cursor = db.execute(query, (*params, *params, first, highwater, MAX_ROWS - budget.rows + 1))
        seen = {t.cid: [] for t in targets}
        by_id = {t.cid: t for t in targets}
        previous = first
        for row in cursor:
            budget.rows += 1
            if budget.rows > MAX_ROWS:
                raise ReadError(503, 'feed_row_budget')
            if row[0] is None or row[0] <= previous or any(row[i] != 1 for i in (2, 4, 6, 8)):
                raise ReadError(503, 'malformed_feed_header')
            previous = row[0]
            if not row[9]:
                continue
            token = by_id[row[5]]
            if row[0] <= token.watermark:
                continue
            if row[7] != 'upsert' or row[10] is None:
                raise ReadError(409 if row[7] == 'delete' else 503, 'invalid_claim_feed')
            budget.check()
            try:
                data = json.loads(row[10], object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, RecursionError):
                raise ReadError(503, 'malformed_feed_data') from None
            budget.check()
            if type(data) is not dict or set(data) != set(_CHUNK_FIELDS):
                raise ReadError(503, 'malformed_feed_data')
            expected_status = 'running' if not seen[token.cid] else 'completed'
            expected_offset = 0 if not seen[token.cid] else token.frames / 16000
            if (len(seen[token.cid]) >= (2 if complete else 1)
                    or data['id'] != token.cid or data['session_id'] != sid
                    or data['status'] != expected_status or data['error'] is not None
                    or (headers is not None and (data['created'], data['recorded_at']) != headers[token.cid])
                    or type(data['duration']) not in (int, float) or data['duration'] != token.frames / 16000
                    or type(data['timeline_start']) not in (int, float) or data['timeline_start'] != 0
                    or type(data['next_offset']) not in (int, float) or data['next_offset'] != expected_offset
                    or type(data['created']) not in (int, float) or not math.isfinite(data['created'])
                    or (data['recorded_at'] is not None and (type(data['recorded_at']) is not str or len(data['recorded_at']) > 128))):
                raise ReadError(409, 'source_control_changed')
            seen[token.cid].append(data)
        for rows in seen.values():
            if len(rows) != (2 if complete else 1):
                raise ReadError(409, 'claim_anchor_missing')
            if complete and any(rows[0][key] != rows[1][key] for key in ('created', 'recorded_at')):
                raise ReadError(409, 'source_control_changed')
        budget.check()

    def preclaim(self):
        if not self._enabled() or self._blocked.is_set():
            return None
        budget = ReadBudget(self.clock)
        with self._database(budget) as db:
            return self._highwater(db, budget)

    def begin(self, chunk, watermark):
        if not self._enabled() or watermark is None:
            return None
        sid, cid = _identifier(chunk['session_id']), _identifier(chunk['id'])
        if type(watermark) is not int or watermark < 0:
            return None
        budget = ReadBudget(self.clock)
        with self._gate(budget):
            self._expire()
            if (self._pending is not None or sum(x.selected for x in self._entries.values()) >= MAX_SUPPORTS
                    or self._retained + STAGE_BYTES > MAX_RETAINED_BYTES - MARKER_RESERVE_BYTES):
                return None
            reservation = _Reservation(sid)
            self._pending = reservation
            generation = self._generations.get(sid, 0)
        try:
            with self._database(budget) as db:
                baseline, frames = self._private(db, sid, cid, budget, 'running')
                self._triggers(db, budget)
                highwater = self._highwater(db, budget)
                if (chunk.get('status') != 'queued' or chunk.get('next_offset') != 0
                        or chunk.get('path') != baseline[3] or chunk.get('digest') != baseline[2]
                        or chunk.get('duration') != frames / 16000 or chunk.get('timeline_start') != 0):
                    raise ReadError(409, 'claim_source_changed')
                signature = _source_identity(baseline[3], frames, budget)
                token = PendingToken(self.epoch, generation, uuid.uuid4().hex, sid, cid,
                                     watermark, frames, self._binding(baseline, signature),
                                     signature, baseline[3], baseline[2])
                self._feed(db, sid, [token], highwater, budget, complete=False,
                           headers={cid: self._projected_header(db, sid, cid, budget)})
            with self._gate(budget):
                if self._pending is not reservation or generation != self._generations.get(sid, 0):
                    raise ReadError(409, 'capture_invalidated')
                self._pending = token
            return token
        except BaseException:
            try:
                with self._gate(allow_blocked=True):
                    if self._pending is reservation:
                        self._pending = None
            except ReadError:
                self._blocked.set()
                self._cleanup_pending.set()
            raise

    def fail(self, token, reason='capture_unavailable'):
        if token is None:
            return
        try:
            with self._gate(allow_blocked=True):
                if self._pending is token:
                    self._pending = None
        except ReadError:
            self._blocked.set()
            self._cleanup_pending.set()

    def _validate_targets(self, db, sid, targets, budget):
        self._session(db, sid, budget)  # First SELECT pins the authorization snapshot.
        self._triggers(db, budget)
        highwater = self._highwater(db, budget)
        headers = {}
        for target in targets:
            baseline, frames = self._private(db, sid, target.cid, budget, 'completed')
            signature = _source_identity(baseline[3], frames, budget, baseline[2])
            if frames != target.frames or self._binding(baseline, signature) != target.binding:
                raise ReadError(409, 'source_changed')
            headers[target.cid] = self._projected_header(db, sid, target.cid, budget)
        self._feed(db, sid, targets, highwater, budget, headers=headers)

    def publish(self, token, artifact):
        if token is None:
            return False
        budget = ReadBudget(self.clock)
        try:
            if not self._enabled():
                return False
            with self._gate(budget):
                if self._pending is not token or token.epoch != self.epoch:
                    return False
            source = artifact.source
            if (source.path != token.path or source.digest != token.digest
                    or source.frames != token.frames or source.signature != token.source_signature
                    or type(artifact.proof) is not bytes or len(artifact.proof) > MAX_PROOF_BYTES
                    or type(artifact.pcm16le) is not bytes or len(artifact.pcm16le) > MAX_PCM_BYTES
                    or type(artifact.selected) is not bool
                    or _HASH.fullmatch(source.source_generation) is None
                    or _HASH.fullmatch(artifact.selection_policy_sha256) is None):
                return False
            selected = artifact.selected
            if selected:
                from .native_lexical_alternatives import SourceSupport
                if (type(artifact.support) is not SourceSupport
                        or len(artifact.pcm16le) != token.frames * 2
                        or artifact.support.source_generation != source.source_generation
                        or artifact.support.pcm16le_sha256 != hashlib.sha256(artifact.pcm16le).hexdigest()
                        or artifact.support.start_frame != 0 or artifact.support.end_frame != token.frames):
                    return False
                support = artifact.support.canonical_bytes()
                proof = artifact.proof
            else:
                if artifact.pcm16le or artifact.support is not None or artifact.reason != 'no_native_overlap':
                    return False
                support = proof = b''
            # Bounded metadata retained beside proof/PCM is included in the cap.
            charged = len(proof) + len(artifact.pcm16le) + len(support) + len(_json([
                token.sid, token.cid, token.watermark, token.frames, token.binding,
                source.source_generation, artifact.selection_policy_sha256])) + 128
            if not selected and charged > 512:
                return False
            with self._database(budget) as db:
                self._validate_targets(db, token.sid, [token], budget)
            with self._gate(budget):
                self._expire()
                if (not self._enabled() or self._pending is not token or token.epoch != self.epoch
                        or token.generation != self._generations.get(token.sid, 0)):
                    return False
                if (token.sid, token.cid) in self._entries:
                    return False
                if selected:
                    if (sum(x.selected for x in self._entries.values()) >= MAX_SUPPORTS
                            or self._retained + charged > MAX_RETAINED_BYTES - MARKER_RESERVE_BYTES):
                        return False
                elif (sum(not x.selected for x in self._entries.values()) >= MAX_MARKERS
                      or self._marker_bytes + charged > MARKER_RESERVE_BYTES):
                    return False
                self._revision += 1
                entry = _Entry(token.sid, token.cid, self._revision, token.generation,
                               token.watermark, token.frames, token.binding,
                               source.source_generation, artifact.selection_policy_sha256,
                               proof, artifact.pcm16le, support, selected,
                               'available' if selected else 'no_native_overlap',
                               self.clock() + TTL_SECONDS, charged)
                budget.check()
                if self._blocked.is_set():
                    return False
                self._entries[(token.sid, token.cid)] = entry
                if selected:
                    self._retained += charged
                else:
                    self._marker_bytes += charged
                self._pending = None
                return True
        except (ReadError, ValueError, TypeError, AttributeError, OverflowError):
            return False
        finally:
            self.fail(token)

    def _drain_leases_locked(self):
        return self.response_budget._drain_leases_locked()

    def _lease(self, kind, budget):
        return self.response_budget._lease(kind, budget)

    @property
    def _lease_count(self):return self.response_budget._lease_count

    @property
    def _lease_bytes(self):return self.response_budget._lease_bytes

    def _envelope(self, entry):
        result = {'schema': 'native-owned-support-v1', 'session_id': entry.sid,
                  'chunk_id': entry.cid, 'capture_epoch': self.epoch,
                  'capture_revision': entry.revision, 'revision': entry.revision,
                  'source_generation': entry.source_generation,
                  'source_frames': entry.frames, 'availability': entry.reason,
                  'reason': entry.reason, 'selected_for_overlap': entry.selected,
                  'selected': entry.selected, 'experimental': True,
                  'selection_policy_sha256': entry.policy,
                  'retention': {'kind': 'volatile', 'ttl_seconds': TTL_SECONDS},
                  'lexical_dispatch_available': self._jobs_configured() and entry.selected}
        if entry.selected:
            support = json.loads(entry.support)
            result['support'] = support
            result['support_id'] = hashlib.sha256(entry.support).hexdigest()
        return result

    def _read(self, kind, sid, cid=None, *, after=0, limit=20,
              expected_capture_epoch=None, expected_revision=None):
        budget = ReadBudget(self.clock)  # Includes lease and all cache admission work.
        if not self._enabled():
            raise ReadError(409, 'feature_disabled')
        _identifier(sid)
        if cid is not None:
            _identifier(cid)
        if (type(after) is not int or not 0 <= after < 10**19
                or (expected_revision is not None and (type(expected_revision) is not int or not 0 <= expected_revision < 10**19))
                or type(limit) is not int or not 1 <= limit <= 20
                or (after and expected_capture_epoch is None)):
            raise ReadError(409, 'invalid_cursor')
        lease = self._lease(kind, budget)
        try:
            with self._gate(budget):
                self._expire()
                if expected_capture_epoch is not None and expected_capture_epoch != self.epoch:
                    raise ReadError(409, 'capture_epoch_changed')
                if kind == 'list':
                    # Sort identifiers, never a list of entries owning PCM/proof.
                    eligible = sorted((x.revision, key) for key, x in self._entries.items()
                                      if x.sid == sid and x.revision > after)
                    targets = [_read_target(self._entries[key]) for _, key in eligible[:limit]]
                    more = len(eligible) > limit
                else:
                    key = (sid, cid)
                    targets = ([_read_target(self._entries[key], audio=kind == 'audio')]
                               if key in self._entries else [])
                    more = False
                    if (targets and expected_revision is not None
                            and expected_revision != targets[0].revision):
                        raise ReadError(409, 'capture_revision_changed')
                generation = self._generations.get(sid, 0)
                invalidation_serial = self._invalidation_serial
            with self._database(budget) as db:
                self._validate_targets(db, sid, targets, budget)
                if kind != 'list' and not targets:
                    # Distinguish missing primary ownership from missing volatile work.
                    self._private(db, sid, cid, budget, 'completed')
                    raise ReadError(409, 'artifact_unavailable')
            if kind == 'audio':
                entry = targets[0]
                if not entry.selected:
                    raise ReadError(409, entry.reason)
                n = len(entry.pcm)
                # No concatenation: after purge the response retains one immutable
                # PCM buffer plus a 44-byte header, within its 768 KiB lease.
                header = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', n + 36, b'WAVE',
                                   b'fmt ', 16, 1, 1, 16000, 32000, 2, 16, b'data', n)
                body = (header, entry.pcm)
                # Only the streaming response tuple retains PCM at authorization.
                targets = [replace(entry, pcm=b'')]
                del entry
                media = 'audio/wav'
            elif kind == 'detail':
                body = _json(self._envelope(targets[0]))
                if len(body) > MAX_PUBLIC_BYTES:
                    raise ReadError(503, 'projection_byte_budget')
                media = 'application/json'
            else:
                rows, emitted = [], []
                for entry in targets:
                    budget.check()
                    row = self._envelope(entry)
                    candidate = {'schema': 'native-owned-support-list-v1', 'capture_epoch': self.epoch,
                                 'supports': rows + [row], 'next_cursor': entry.revision,
                                 'has_more': more, 'lexical_dispatch_available': self._jobs_configured(), 'experimental': True}
                    if len(_json(candidate)) > 128 * 1024:
                        more = True
                        break
                    rows.append(row)
                    emitted.append(entry)
                targets = emitted
                body = _json({'schema': 'native-owned-support-list-v1', 'capture_epoch': self.epoch,
                              'supports': rows, 'next_cursor': targets[-1].revision if targets else after,
                              'has_more': more, 'lexical_dispatch_available': self._jobs_configured(), 'experimental': True})
                media = 'application/json'
            budget.check()
            with self._gate(budget):
                if (not self._enabled() or generation != self._generations.get(sid, 0)
                        or invalidation_serial != self._invalidation_serial):
                    raise ReadError(409, 'capture_invalidated')
                for entry in targets:
                    if (not self._matches(entry) or self.clock() >= entry.expires
                            or entry.generation != generation):
                        raise ReadError(409, 'capture_revision_changed')
                budget.check()
            return ReadResult(body, media, lease)
        except BaseException:
            lease.release()
            raise

    def _jobs_configured(self):
        return (self.jobs_owner is not None and
                bool(getattr(self.store.settings, 'moss_jobs_enabled', False)))

    def reserve_jobs_response(self, kind):
        return self._lease('jobs_' + kind, ReadBudget(self.clock))

    def authorize_session(self, sid):
        _identifier(sid)
        budget = ReadBudget(self.clock)
        with self._database(budget) as db:
            self._session(db, sid, budget)

    def acquire_dispatch(self, sid, cid, *, expected_capture_epoch, expected_revision, support_id):
        from .native_lexical_alternatives import SourceSupport
        _identifier(sid)
        _identifier(cid)
        if (type(expected_revision) is not int or not 0 <= expected_revision < 10**19
                or type(support_id) is not str or _HASH.fullmatch(support_id) is None):
            raise ReadError(409, 'invalid_source_binding')
        budget = ReadBudget(self.clock)
        with self._gate(budget):
            self._expire()
            if not self._enabled() or not self._jobs_configured():
                raise ReadError(409, 'feature_disabled')
            if expected_capture_epoch != self.epoch:
                raise ReadError(409, 'capture_epoch_changed')
            if self._dispatch is not None:
                raise ReadError(503, 'dispatch_pin_occupied')
            entry = self._entries.get((sid, cid))
            if entry is None or not entry.selected:
                raise ReadError(409, 'selected_support_unavailable')
            if expected_revision != entry.revision:
                raise ReadError(409, 'capture_revision_changed')
            target = _read_target(entry)
            support = SourceSupport.from_dict(json.loads(entry.support))
            if (support.canonical_bytes() != entry.support or support.support_id != support_id
                    or support.source_generation != entry.source_generation
                    or support.start_frame != 0 or support.end_frame != entry.frames
                    or len(entry.pcm) != 2 * entry.frames
                    or hashlib.sha256(entry.pcm).hexdigest() != support.pcm16le_sha256
                    or len(entry.support) + len(entry.pcm) + 4096 > MAX_DISPATCH_BYTES):
                raise ReadError(409, 'support_binding_changed')
            token = DispatchToken(self._dispatch_owner, self.database_identity, self.epoch,
                                  self._invalidation_serial, support_id, target)
            lease = DispatchLease(self, token, support, entry.pcm)
            self._dispatch = lease
        try:
            self.revalidate_dispatch(token, budget=budget)
            return lease
        except BaseException:
            lease.cancelled.set()
            lease.release()
            raise

    def revalidate_dispatch(self, token, *, budget=None):
        budget = budget or ReadBudget(self.clock)
        target = token.target
        if (token.owner != self._dispatch_owner or token.database_identity != self.database_identity
                or token.epoch != self.epoch):
            raise ReadError(409, 'dispatch_binding_changed')
        with self._database(budget) as db:
            self._validate_targets(db, target.sid, [target], budget)
        with self._gate(budget):
            self._expire()
            if (not self._enabled() or not self._jobs_configured()
                    or token.invalidation_serial != self._invalidation_serial
                    or target.generation != self._generations.get(target.sid, 0)
                    or not target.selected or not self._matches(target)
                    or self.clock() >= target.expires
                    or hashlib.sha256(target.support).hexdigest() != token.support_id):
                raise ReadError(409, 'capture_invalidated')
            budget.check()
        return True

    def _matches(self, projection):
        # The temporary entry reference dies before the cache gate is released.
        current = self._entries.get((projection.sid, projection.cid))
        return (current is not None and current.revision == projection.revision
                and current.generation == projection.generation
                and current.binding == projection.binding)

    def read_list(self, sid, **kwargs):
        return self._read('list', sid, **kwargs)

    def read_detail(self, sid, cid, **kwargs):
        return self._read('detail', sid, cid, **kwargs)

    def read_audio(self, sid, cid, **kwargs):
        return self._read('audio', sid, cid, **kwargs)

    def invalidate(self, sid):
        dispatch = self._dispatch
        if dispatch is not None and dispatch.token.target.sid == sid:
            dispatch.cancelled.set()
        try:
            with self._gate(allow_blocked=True):
                # Global serial produces session deletion generations without unbounded tombstones.
                self._revision += 1
                self._invalidation_serial += 1
                self._generations[sid] = self._revision
                if isinstance(self._pending, (PendingToken, _Reservation)) and self._pending.sid == sid:
                    self._pending = None
                for key in tuple(self._entries):
                    if key[0] == sid:
                        self._remove(key)
                if self._blocked.is_set():
                    self._clear()
                # No pending/entry survives this notification; identity checks also
                # prevent old tokens from being adopted after tombstone pruning.
                self._generations.pop(sid, None)
                return bool(dispatch is not None and not dispatch.released)
        except ReadError:
            # Never report successful purge when the bytes are still charged.
            self._blocked.set()
            self._cleanup_pending.set()
            return True

    def _clear(self):
        self._drain_dispatch_locked()
        if self._dispatch is not None:self._dispatch.cancelled.set()
        self._entries.clear()
        self._generations.clear()
        self._pending = None
        self._retained = self._marker_bytes = 0
        self._cleanup_pending.clear()

    def drain(self):
        try:
            with self._gate(allow_blocked=True):
                if self._blocked.is_set():
                    self._clear()
                else:
                    self._expire()
        except ReadError:
            return self._cleanup_pending.is_set() or self._lease_cleanup_pending.is_set() or self._dispatch is not None
        if not self._lease_lock.acquire(timeout=LOCK_SECONDS):
            return self._lease_cleanup_pending.is_set()
        try:
            self._drain_leases_locked()
        finally:
            self._lease_lock.release()
        return self._dispatch is not None or self._dispatch_cleanup_pending.is_set()

    def close(self):
        if self._dispatch is not None:self._dispatch.cancelled.set()
        self._blocked.set()
        self._cleanup_pending.set()
        return self.drain()


_registry_lock = threading.Lock()
_registry = {}


def _db_key(store):
    path = str(Path(store.path).resolve())
    st = os.stat(path)
    return st.st_dev, st.st_ino


def register(store, *, response_budget=None):
    """Call only after the existing application InstanceLock has been acquired."""
    if not getattr(store.settings, 'native_owned_support_enabled', False):
        return None
    if not _registry_lock.acquire(timeout=LOCK_SECONDS):
        raise ReadError(503, 'registry_unavailable')
    try:
        key = _db_key(store)
        for stale_key, stale in tuple(_registry.items()):
            if stale._blocked.is_set() and not stale.drain():
                if stale._lease_lock.acquire(timeout=LOCK_SECONDS):
                    try:
                        stale._drain_leases_locked()
                        if stale._lease_count == 0 and stale._pending is None and stale._dispatch is None and (stale.jobs_owner is None or stale.jobs_owner.stopped):
                            _registry.pop(stale_key, None)
                    finally:
                        stale._lease_lock.release()
        old = _registry.get(key)
        if old is not None and not old._blocked.is_set():
            raise ReadError(409, 'capture_owner_already_registered')
        if old is not None:
            if old._dispatch is not None or (old.jobs_owner is not None and not old.jobs_owner.stopped):
                raise ReadError(503, 'jobs_cleanup_pending')
            if old.drain():
                raise ReadError(503, 'cleanup_pending')
            if not old._lease_lock.acquire(timeout=LOCK_SECONDS):
                raise ReadError(503, 'response_cleanup_pending')
            try:
                old._drain_leases_locked()
                if old._lease_count:
                    raise ReadError(503, 'response_cleanup_pending')
            finally:
                old._lease_lock.release()
        if len(_registry) >= MAX_REGISTRY_OWNERS:
            raise ReadError(503, 'registry_capacity')
        cache = NativeOwnedSupportCache(store, response_budget=response_budget)
        _registry[key] = cache
        return cache
    finally:
        _registry_lock.release()


def lookup(store):
    # OFF/never-enabled is completely inert, including no filesystem lookup.
    if not _registry:
        return None
    try:
        key = _db_key(store)
        canonical = str(Path(store.path).resolve())
    except OSError:
        raise ReadError(503, 'database_binding_uncertain') from None
    snapshot = _registry.copy()  # Bounded owner references; no artifact copies.
    if key not in snapshot:
        if any(candidate.path == canonical for candidate in snapshot.values()):
            raise ReadError(503, 'database_binding_uncertain')
        # Positive inode/path distinction keeps an unrelated never-enabled DB
        # inert even if another owner's registry gate is temporarily occupied.
        return None
    if not _registry_lock.acquire(timeout=LOCK_SECONDS):
        raise ReadError(503, 'registry_unavailable')
    try:
        return _registry.get(key)
    finally:
        _registry_lock.release()


def notify_deleted(store, sid):
    """After primary commit only. None means no active/previously-enabled cache."""
    try:
        cache = lookup(store)
    except (ReadError, OSError, ValueError, TypeError, AttributeError):
        # Binding uncertainty cannot masquerade as successful optional erasure.
        for candidate in tuple(_registry.values()):
            candidate._blocked.set()
            candidate._cleanup_pending.set()
            if candidate._dispatch is not None:candidate._dispatch.cancelled.set()
        return True
    return cache.invalidate(sid) if cache is not None else None
