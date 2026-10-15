"""Opt-in, finalized recording batches feeding the unchanged legacy worker.

A capture ID is an explicit shared PCM sample-coordinate declaration, never a
wall-clock or microphone identity inference. Original WAV files are retained.
Only byte-equal, declared overlap is aliased. A single unresolved source blocks
all work; original and derived frame ranges remain distinct durable receipts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
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
            ('receipt_attempts', 'INTEGER NOT NULL DEFAULT 0')):
        if name not in columns:
            db.execute(f'ALTER TABLE recording_work ADD COLUMN {name} {definition}')
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
        AND batch_id IN (SELECT id FROM recording_batches WHERE state='pending_receipts')""")
    db.execute("""UPDATE recording_batches SET state='interrupted',blockers=?
        WHERE state IN ('preparing','staging')""", (_json([{'kind': 'interrupted_publication',
        'detail': 'Retained staged work is not queued; delete the session to reset this batch'}]),))


def erase(db, sid):
    """Called inside the existing session erasure transaction, before chunks."""
    db.execute('''INSERT OR IGNORE INTO session_file_cleanup(session_id,path,created)
        SELECT b.session_id,s.path,? FROM recording_sources s
        JOIN recording_batches b ON b.id=s.batch_id WHERE b.session_id=?''', (time.time(), sid))
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
        self._next_tick = 0.0

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

    def source_path(self, batch):
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
                os.replace(path, final); _fsync_dir(self.uploads)
                db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,frames,
                    bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (source.fragment_id, batch['id'], key, source.sha256, str(final), source.frames,
                     size, source_start_frame, FORMAT, time.time()))
                db.execute("UPDATE recording_batches SET revision=revision+1,state='open',blockers='[]' WHERE id=?",
                           (batch['id'],))
            committed = True
            return {'source_id': source.fragment_id, 'duplicate': False}
        finally:
            if not committed:
                final.unlink(missing_ok=True)

    def status(self, sid, capture_id):
        with self.store.db() as db:
            db.execute('BEGIN')  # One public snapshot across async publication.
            self._session(db, sid)
            batch = self._batch(db, sid, capture_id)
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
                blockers.append({'kind': 'original_unavailable', 'source_id': source['id'], 'detail': str(exc)[:256]})
        return blockers

    def _materialize(self, batch, owners, origin, end, cells):
        pieces = []
        try:
            for position, start in enumerate(range(origin, end, CHUNK_FRAMES)):
                stop = min(start + CHUNK_FRAMES, end)
                path = self.uploads / f"ro-{batch['id']}-work-{position:03}.part"
                final = path.with_suffix('.audio')
                if final.exists() or path.exists():
                    raise ValueError('Retained interrupted work exists; restart cleanup or erase this session')
                piece = {'position': position, 'start': start, 'end': stop,
                         'path': path, 'final': final, 'token': None, 'native': 'disabled'}
                pieces.append(piece)
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
            _fsync_dir(self.uploads)
            return pieces
        except BaseException:
            self._discard(pieces)
            raise

    @staticmethod
    def _discard(pieces):
        for piece in pieces:
            if piece['token'] is not None:
                piece['token'].close(); piece['token'] = None
            piece['path'].unlink(missing_ok=True)
            piece['final'].unlink(missing_ok=True)

    def finish(self, sid, capture_id, key, expected_revision, cells=None):
        _text(key, 'Finish idempotency_key')
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('expected_revision must be a nonnegative integer')
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
            if not committed:
                self._discard(pieces)
            else:
                for piece in pieces:
                    if piece['token'] is not None:
                        piece['token'].close(); piece['token'] = None

    def _observed_receipt(self, cells, piece, sid, origin, stream_id):
        """Freeze actual observation; never construct a Receipt from a filename."""
        from .native_owned_cell_store import Receipt
        receipt = None
        if cells is not None and piece['token'] is not None:
            try:
                receipt = cells.observe_recording_receipt(piece['token'], sid, piece['cid'],
                    piece['final'], (piece['start'] - origin) / RATE, stream_id)
            except Exception:
                receipt = None
        if piece['token'] is not None:
            piece['token'].close(); piece['token'] = None
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
            sc.stream_id,sc.position AS stream_position,sc.stream_final FROM recording_work w
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
                    or row['path'] != str(path) or row['digest'] != piece['digest']
                    or row['position'] != piece['position'] or row['bytes'] != piece['bytes']
                    or row['frames'] != piece['end'] - piece['start'] or row['duration'] != row['frames'] / RATE
                    or row['timeline_start'] != (piece['start'] - manifest['origin']) / RATE
                    or row['capture_start_frame'] != piece['start']
                    or row['stream_id'] != f"recording.{batch['id']}" or row['stream_position'] != piece['position']
                    or bool(row['stream_final']) != (piece is pieces[-1])):
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

    def _publish_pending(self, bid, cells):
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
            db.execute("UPDATE chunks SET status='queued' WHERE status='staging' AND id IN (SELECT chunk_id FROM recording_work WHERE batch_id=?)", (bid,))
            db.execute("UPDATE recording_batches SET state='queued',blockers=? WHERE id=?", (_json(gaps), bid))
            return True

    def _interrupt_pending(self, bid, reason):
        with self.store.db() as db:
            db.execute('PRAGMA busy_timeout=5')  # Bounded maintenance admission, primary DB only.
            db.execute("""UPDATE recording_batches SET state='interrupted',blockers=?
                WHERE id=? AND state='pending_receipts'""", (_json([{'kind': 'recording_authority_unavailable',
                'detail': str(reason)[:256]}]), bid))

    def _receipt_status(self, cid, status, *, durable=False, gap=False, attempted=False):
        with self.store.db() as db:
            db.execute('PRAGMA busy_timeout=5')  # Bounded maintenance admission, primary DB only.
            db.execute('''UPDATE recording_work SET native_receipt_status=?,
                receipt_state=CASE WHEN ? THEN 'durable' WHEN ? THEN 'gap' ELSE receipt_state END,
                receipt_attempts=MIN(2147483647,receipt_attempts+?) WHERE chunk_id=? AND receipt_state='pending'
                AND batch_id IN (SELECT id FROM recording_batches WHERE state='pending_receipts')''',
                (status[:128], int(durable), int(gap), int(attempted), cid))

    def advance(self, cells, *, force=False, batch_id=None):
        """One nonwaiting maintenance tick in the EXISTING worker loop.

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
            self._next_tick = time.monotonic() + 1.0
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
                        self._receipt_status(cid, 'receipt_retained', durable=True)
                        with self.store.db() as db:
                            pending = db.execute("SELECT 1 FROM recording_work WHERE batch_id=? AND receipt_state='pending' LIMIT 1", (bid,)).fetchone()
                        return self._publish_pending(bid, cells) if pending is None else False
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
                self._receipt_status(cid, 'existing_receipt', durable=True)
                return False
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
