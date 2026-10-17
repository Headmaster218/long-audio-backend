"""Passive Worker/upload integration and current primary authorization gates.

No primary schema writes, original-file retirement, decoder/model construction
or dispatch. Optional failures never replace the caller's primary outcome.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
from fractions import Fraction
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import stat
import struct
import threading
import time

from .native_owned_cells import (Unavailable, RollingWitness, SourceSpan, Cell,
    canonical, sha, text, digest, integer, exact_frame, validate_mapping, make_cells,
    RATE, MAX_FRAME, CORE_FRAMES, MAX_PARTS, IO_BYTES, PROOF_BYTES,
    intersect_mapping, BATCH_CELLS, HANDOFF_BYTES)
from .native_owned_cell_store import (CellStore, Receipt, Budget, qualified_runtime,
    RECEIPT_BYTES, LOCK_SECONDS, Operation, _diagnostic_failure, _diagnostic)

_REGISTRY = {}
_REGISTRY_LOCK = threading.Lock()
# In-process witness only: same public exception, no new replay authority.
_CLOSED_AUTOMATIC_READ_BUSY = object()
_PRELAUNCH_READ_BUSY_CODES = frozenset((5, 261, 517, 773, 6, 262, 518))


def bounded_primary_snapshot(db, sid, scope_origin, scan_frames, budget):
    """One complete indexed primary band, screened before projecting bodies.

    This is also the transaction/publication read: it discovers inserted
    parents again, rather than trusting a list of previously known row IDs.
    No source files or model requests are used here.
    """
    from . import lexical_evidence as lexical, transcript_contract as contract
    text(sid)
    if (type(scan_frames) not in (list, tuple) or len(scan_frames) != 2
            or not 0 <= scan_frames[0] < scan_frames[1]):
        raise Unavailable('bounded_primary_geometry')
    for value in scan_frames:integer(value)
    start = scope_origin + scan_frames[0] / RATE
    end = scope_origin + scan_frames[1] / RATE
    lower, upper = max(0., start - 5.), end + 5.
    budget.check()
    chunks = list(db.execute('''SELECT id,timeline_start,duration FROM chunks INDEXED BY chunk_timeline
        WHERE session_id=? AND timeline_start<=? ORDER BY timeline_start DESC LIMIT 1''', (sid, lower)))
    chunks += list(db.execute('''SELECT id,timeline_start,duration FROM chunks INDEXED BY chunk_timeline
        WHERE session_id=? AND timeline_start>? AND timeline_start<? ORDER BY timeline_start LIMIT 129''',
        (sid, lower, upper)))
    if len(chunks) > 128:raise Unavailable('bounded_source_capacity')
    budget.projection(len(chunks) * 256)
    previous_end = None; screened = []; source_ids = set(); lexical_source_ids = set()
    floor = math.nextafter(start - 1 / RATE, -math.inf)
    ceiling = math.nextafter(end + 1 / RATE, math.inf)
    variable = ('text','raw_text','asr_backend','timestamp_kind','speaker_local_id','lexical_evidence')
    lengths_sql = '+'.join('COALESCE(length(CAST(s.' + name + ' AS BLOB)),0)' for name in variable)
    for chunk in chunks:
        a, duration = chunk['timeline_start'], chunk['duration']
        contract.primary_frame(a); contract.primary_frame(duration, end=True)
        if duration <= 0 or previous_end is not None and a < previous_end:
            raise Unavailable('bounded_source_geometry')
        previous_end = a + duration
        if previous_end <= lower:continue
        text(chunk['id']); source_ids.add(chunk['id'])
        found = db.execute(f'''SELECT s.id,{lengths_sql}+256 AS bytes,
            COALESCE(length(CAST(s.lexical_evidence AS BLOB)),0) AS lexical_bytes
            FROM segments s INDEXED BY segments_chunk
            WHERE s.chunk_id=? AND s.start<? AND s.end>? ORDER BY s.start,s.id LIMIT 129''',
            (chunk['id'], ceiling, floor)).fetchall()
        if len(screened) + len(found) > 128:raise Unavailable('bounded_primary_capacity')
        for row in found:
            if not 0 <= row['lexical_bytes'] <= lexical.MAX_SEGMENT_BYTES:
                raise Unavailable('bounded_inventory_capacity')
            budget.projection(row['bytes'])
            screened.append((row['id'], chunk))
    rows = []; fields = ','.join(contract.PRIMARY_FIELDS) + ',lexical_evidence'
    for ident, chunk in screened:
        budget.check()
        raw = db.execute('SELECT ' + fields + ' FROM segments WHERE id=? AND chunk_id=?',
                         (ident, chunk['id'])).fetchone()
        if raw is None:raise Unavailable('bounded_primary_missing')
        row = dict(raw); parent = contract.primary_evidence(sid, row)
        a = contract.primary_frame(chunk['timeline_start'])
        b = contract.primary_frame(chunk['timeline_start'] + chunk['duration'], end=True)
        if parent['start_frame'] < max(0, a - 5 * RATE) or parent['end_frame'] > b + 5 * RATE:
            raise Unavailable('bounded_primary_geometry')
        if parent['end_frame'] <= contract.primary_frame(start) or parent['start_frame'] >= contract.primary_frame(end, end=True):
            continue
        rows.append(row)
        if row['lexical_evidence'] is not None:
            try:
                value = json.loads(row['lexical_evidence'])
                if (type(value) is dict and value.get('schema') == lexical.SCHEMA
                        and lexical.encoded(value).decode('ascii') == row['lexical_evidence']):
                    parts = lexical._sources(value.get('source_parts'), sid)
                    lexical_source_ids.update(part['id'] for part in parts)
            except (ValueError, KeyError, TypeError, UnicodeError, OverflowError):
                # Invalid lexical evidence is the whole known opaque parent.
                # Its arbitrary source IDs cannot invent a source lookup.
                pass
        if len(source_ids | lexical_source_ids) > 128:raise Unavailable('bounded_source_capacity')
    sources = {}
    for cid in sorted(source_ids | lexical_source_ids):
        budget.projection(1024); budget.check()
        length = db.execute('SELECT length(id),length(session_id),length(digest) FROM chunks WHERE id=?', (cid,)).fetchone()
        if length is None or any(type(n) is not int or n < 1 or n > 512 for n in length):
            if cid in source_ids:raise Unavailable('bounded_source_missing')
            continue
        row = db.execute('SELECT id,session_id,digest,timeline_start,duration FROM chunks WHERE id=?', (cid,)).fetchone()
        if row is None or row['session_id'] != sid:
            if cid in source_ids:raise Unavailable('bounded_source_scope')
            continue
        sources[cid] = dict(row)
    budget.check()
    return {'rows': sorted(rows, key=lambda row: row['id']), 'sources': sources}


def bounded_prospective_rows(chunk, offset, end, result, sources, budget):
    """Exact prospective lexical payloads; fake IDs never leave the prepare plan.

    The primary transaction must replace every temporary ID and rebuild all
    inventory hashes from the actually retained rows before binding a proof.
    """
    from . import lexical_evidence as lexical
    if result.get('segments') == []:return []
    prepared, reason = lexical.prepare(result, chunk, offset, end)
    if prepared is None:raise Unavailable('bounded_prospective_inventory_unavailable')
    drafts, parts = prepared
    if len(drafts) > 128:raise Unavailable('bounded_primary_capacity')
    if not lexical._sources_current(None, parts, source_lookup=sources.get):
        raise Unavailable('bounded_prospective_source_changed')
    metadata = result.get('metadata', {}); rows = []
    for index, (segment, draft) in enumerate(zip(result['segments'], drafts)):
        budget.check()
        row = {'id': MAX_FRAME - index, 'chunk_id': chunk['id'],
            'start': chunk['timeline_start'] + offset + segment['start'],
            'end': chunk['timeline_start'] + offset + segment['end'],
            'text': segment.get('text', ''), 'raw_text': segment.get('raw_text'),
            'asr_backend': segment.get('asr_backend') or metadata.get('asr_backend'),
            'timestamp_kind': segment.get('timestamp_kind') or metadata.get('timestamp_semantics'),
            'confidence': segment.get('confidence'), 'speaker_local_id': segment.get('speaker_local_id'),
            'speaker_window_start': offset}
        lexical._geometry(draft, row, chunk, parts, absolute=True)
        value = {'schema': lexical.SCHEMA, 'primary_sha256': lexical.primary_sha(chunk['session_id'], row),
            'source_provenance': lexical.SOURCE_NOTE, 'source_parts': parts,
            'timestamp_semantics': lexical.TIME_NOTE, **draft}
        raw = lexical.encoded(value).decode('ascii')
        if len(raw) > lexical.MAX_SEGMENT_BYTES:raise Unavailable('bounded_inventory_capacity')
        budget.projection(len(raw) + 256)
        rows.append(dict(row, lexical_evidence=raw))
    return rows


def _sample_gap_cut(turns, start, target, a, b, window):
    """The safe_cut gap policy on validated samples, never on external floats.

    A rational configuration guard preserves window/4 exactly. Only the internally
    selected gap point is put on the sample lattice: an odd midpoint chooses
    the earlier sample (at most half a sample); a fractional lower guard is
    rounded inward. No observed native/source endpoint is rounded here.
    """
    for value in (start, target, a, b):integer(value)
    if (type(window) not in (int, Fraction) or not RATE <= window <= 60 * RATE
            or not a <= start < target <= b):
        raise Unavailable('native_cut_geometry')
    lower4 = max((start + RATE // 2) * 4, target * 4 - min(3 * RATE * 4, window))
    if target >= b or target * 4 <= lower4:return target
    intervals = []
    for turn in sorted(turns, key=lambda t: t.start):
        lo, hi = max(a, turn.start), min(b, turn.end)
        if hi <= lo:continue
        if intervals and lo <= intervals[-1][1]:intervals[-1][1] = max(hi, intervals[-1][1])
        else:intervals.append([lo, hi])
    if not intervals:return target
    candidates = [];cursor = a
    for lo, hi in intervals + [[b, b]]:
        if lo - cursor >= RATE // 5:
            minimum4 = max(lower4, (cursor + RATE // 20) * 4)
            minimum = -(-minimum4 // 4)
            maximum = min(target, lo - RATE // 20)
            if maximum >= minimum:
                candidates.append(min(maximum, max(minimum, (cursor + lo) // 2)))
        cursor = max(cursor, hi)
    return max(candidates) if candidates else target


def signature(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def regular(st):
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid():
        raise Unavailable('source_not_owned_regular_file')
    return st


def _identity(path):
    info = regular(Path(path).lstat())
    return sha({'path': str(Path(path).absolute()), 'device': info.st_dev, 'inode': info.st_ino})


def _owned_path(path, directory, suffix):
    path = Path(path)
    if not path.is_absolute() or path.parent != directory or path.suffix != suffix:
        raise Unavailable('source_path_not_owned')
    for parent in (directory, directory.parent):
        st = parent.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise Unavailable('unsafe_source_parent')
    return path


def _directory_chain(directory, budget):
    """Walk from root without following any component; retain only final fd."""
    directory = Path(directory)
    if (not directory.is_absolute() or '..' in directory.parts or len(directory.parts) > 33
            or len(os.fsencode(directory)) > 4096):
        raise Unavailable('source_parent_path_capacity')
    if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_NONBLOCK', 'O_DIRECTORY')):
        raise Unavailable('source_descriptor_primitives_unavailable')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
    fd = os.open('/', flags); identities = []
    try:
        root = os.fstat(fd)
        if not stat.S_ISDIR(root.st_mode):raise Unavailable('unsafe_source_parent')
        identities.append((root.st_dev, root.st_ino))
        for name in directory.parts[1:]:
            budget.check(); child = os.open(name, flags, dir_fd=fd)
            try:
                info = os.fstat(child); entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode) or not stat.S_ISDIR(entry.st_mode)
                        or (info.st_dev, info.st_ino) != (entry.st_dev, entry.st_ino)):
                    raise Unavailable('source_parent_descriptor_mismatch')
            except BaseException:
                os.close(child); raise
            os.close(fd); fd = child; identities.append((info.st_dev, info.st_ino))
        budget.check(); result = fd; fd = None
        return result, tuple(identities)
    finally:
        if fd is not None:os.close(fd)


@dataclass(slots=True)
class BoundSource:
    fd: int
    parent_fd: int
    directory: Path
    parents: tuple
    closed: bool = False

    def close(self):
        if not self.closed:
            self.closed = True
            try:os.close(self.fd)
            finally:os.close(self.parent_fd)


def _parents_current(source, budget):
    current, identities = _directory_chain(source.directory, budget)
    try:
        if identities != source.parents:raise Unavailable('source_parent_replaced')
        before = os.fstat(source.parent_fd); after = os.fstat(current)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise Unavailable('source_parent_replaced')
    finally:os.close(current)


def _open(path, budget):
    path = Path(path); parent = fd = None
    if not path.name or path.name in ('.', '..'):raise Unavailable('source_path_not_owned')
    try:
        parent, identities = _directory_chain(path.parent, budget)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0), dir_fd=parent)
        source = BoundSource(fd, parent, path.parent, identities); fd = parent = None
        try:
            info = regular(os.fstat(source.fd))
            _continuity(source, path, signature(info), budget)
            return source, signature(info)
        except BaseException:
            source.close(); raise
    finally:
        if fd is not None:os.close(fd)
        if parent is not None:os.close(parent)


def _continuity(source, path, expected, budget):
    budget.check()
    if source.closed or path.parent != source.directory:raise Unavailable('source_parent_replaced')
    if (signature(regular(os.fstat(source.fd))) != expected
            or signature(regular(os.stat(path.name, dir_fd=source.parent_fd, follow_symlinks=False))) != expected):
        raise Unavailable('source_changed_during_unlocked_io')
    _parents_current(source, budget)
    budget.check()


def riff_geometry(fd, size, budget):
    """At most 64 KiB header; data must be the final exact RIFF PCM extent."""
    integer(size, (1 << 32) + 8, 46)
    wanted = min(size, IO_BYTES); budget.read(wanted)
    header = os.pread(fd.fd, wanted, 0); budget.check()
    if (len(header) != wanted or header[:4] != b'RIFF' or header[8:12] != b'WAVE'
            or struct.unpack_from('<I', header, 4)[0] + 8 != size):
        raise Unavailable('bounded_riff_header_mismatch')
    at = 12; fmt = False
    for _ in range(128):
        if at + 8 > len(header):raise Unavailable('riff_header_capacity')
        kind, count = struct.unpack_from('<4sI', header, at); start = at + 8; end = start + count
        if end + (count & 1) > size:raise Unavailable('riff_extent_mismatch')
        if kind == b'fmt ':
            if fmt or count not in (16, 18) or end > len(header):raise Unavailable('riff_format_shape')
            if struct.unpack_from('<HHIIHH', header, start) != (1, 1, RATE, RATE * 2, 2, 16):
                raise Unavailable('original_pcm16_mono_16k_required')
            if count == 18 and header[start + 16:end] != b'\0\0':raise Unavailable('riff_format_extension')
            fmt = True
        elif kind == b'data':
            if not fmt or count < 2 or count % 2 or end != size:
                raise Unavailable('riff_data_not_exact_final_extent')
            return start, count // 2
        at = end + (count & 1)
    raise Unavailable('riff_chunk_capacity')


def _read_frames(fd, offset, frames, budget):
    integer(frames, CORE_FRAMES, 1); integer(offset)
    result = bytearray(); remaining = frames * 2
    while remaining:
        size = min(IO_BYTES, remaining); budget.read(size)
        block = os.pread(fd.fd, size, offset); budget.check()
        if len(block) != size:raise Unavailable('source_short_read')
        result.extend(block); offset += size; remaining -= size
    return bytes(result)


def observed_pcm(path, expected_frames, budget):
    fd, before = _open(Path(path), budget)
    try:
        data_offset, frames = riff_geometry(fd, before[2], budget)
        if frames != expected_frames:raise Unavailable('observed_pcm_frame_mismatch')
        data = _read_frames(fd, data_offset, frames, budget)
        _continuity(fd, Path(path), before, budget)
        return data
    finally:fd.close()


def seam_commit_projection(db, cid, budget):
    """Only the two current commitment objects cross the SQLite/Python boundary.

    Full metadata is screened but never returned. Nested CASE evaluation guards
    every JSON operation, including a second length screen in the same SELECT,
    so malformed or concurrently replaced metadata cannot reach extraction.
    Irrelevant analyzer metadata never consumes the 256 KiB projection budget.
    """
    text(cid); budget.check()
    length = db.execute('SELECT length(metadata) FROM chunks WHERE id=?', (cid,)).fetchone()
    if length is None:return None
    if length[0] is None or not 1 <= length[0] <= PROOF_BYTES:
        raise Unavailable('primary_commit_projection_capacity')
    projected = db.execute("""SELECT
        CASE WHEN typeof(metadata)='text' THEN
          CASE WHEN length(metadata) BETWEEN 1 AND ? THEN
            CASE WHEN json_valid(metadata)=1 THEN
              CASE WHEN json_type(metadata)='object' THEN
                CASE WHEN json_type(metadata,'$.native_diarization')='object' THEN
                  CASE WHEN json_type(metadata,'$.core_cut')='object' THEN
                    CASE WHEN (SELECT COUNT(*) FROM json_each(metadata)
                      WHERE key IN ('native_diarization','core_cut'))=2 THEN
                      CASE WHEN length(CAST(json_extract(metadata,'$.native_diarization') AS BLOB))
                        +length(CAST(json_extract(metadata,'$.core_cut') AS BLOB))<=? THEN
                        json_array(json_extract(metadata,'$.native_diarization'),
                                   json_extract(metadata,'$.core_cut'))
                      END
                    END
                  END
                END
              END
            END
          END
        END FROM chunks WHERE id=?""", (PROOF_BYTES, PROOF_BYTES - 3, cid)).fetchone()
    budget.check()
    if projected is None or type(projected[0]) is not str:return None
    encoded = projected[0].encode('utf-8')
    if not 1 <= len(encoded) <= PROOF_BYTES:return None
    budget.projection(len(encoded))
    values = json.loads(encoded)
    if type(values) is not list or len(values) != 2 or any(type(value) is not dict for value in values):
        return None
    return {'native_diarization': values[0], 'core_cut': values[1]}


class PrimaryAuthority:
    """Read-only, zero-busy-timeout authority; never uses Store.db's write path."""
    def __init__(self, store):
        self.store = store; self.path = Path(store.path).absolute()
        self.uploads = Path(store.settings.data_dir).absolute() / 'uploads'
        self.identity = _identity(self.path); self.owner = None
        self._list_reads = threading.local()

    @contextmanager
    def list_read_scope(self, budget, *, bounded_planning=False):
        """Cache only a connection for one list, never a read snapshot or result."""
        if getattr(self._list_reads, 'current', None) is not None:
            raise Unavailable('nested_primary_list_read')
        scope = {'budget': budget, 'thread': threading.get_ident(), 'db': None,
                 'bounded_planning': bounded_planning is True}
        self._list_reads.current = scope
        try:yield
        finally:
            self._list_reads.current = None
            db = scope['db']; scope['db'] = None
            if db is not None:db.close()

    @contextmanager
    def db(self, budget, *, _closure=None, _planning_read=None):
        scope = getattr(self._list_reads, 'current', None)
        db = scope['db'] if scope is not None else None
        retained = False
        try:
            budget.check()
            if scope is not None and (scope['budget'] is not budget
                    or scope['thread'] != threading.get_ident()):
                raise Unavailable('primary_list_read_scope_mismatch')
            if _identity(self.path) != self.identity:raise Unavailable('primary_database_replaced')
            if db is None:
                db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=0., isolation_level=None)
                db.row_factory = sqlite3.Row; db.set_progress_handler(budget.progress, 100)
                db.execute('PRAGMA query_only=ON'); db.execute('PRAGMA busy_timeout=0')
                if scope is not None:scope['db'] = db
            if db.in_transaction:raise Unavailable('primary_read_snapshot_not_ended')
            if _planning_read is not None:
                db.set_progress_handler(_planning_read['progress'], 100)
            yield db
            budget.check()
            if _identity(self.path) != self.identity:raise Unavailable('primary_database_replaced')
            if scope is not None:
                # _read still starts BEGIN for EVERY authority phase. Ending
                # it here is essential: later phases and final list checks
                # must see newly committed state, even on this connection.
                db.execute('ROLLBACK')
                budget.check()
                if db.in_transaction:raise Unavailable('primary_read_snapshot_not_ended')
                retained = True
        finally:
            if not retained and db is not None:
                if scope is not None:scope['db'] = None
                try:
                    if _planning_read is not None and _planning_read['interrupt'] is not None:
                        # Only a positively Budget-interrupted read body gets
                        # this cleanup. The exhausted callback must not abort
                        # ROLLBACK or replace its original refusal. Cleanup
                        # failure remains a hard error, never a safe defer.
                        db.set_progress_handler(None, 0)
                        if db.in_transaction:
                            db.execute('ROLLBACK')
                            if db.in_transaction:
                                raise sqlite3.OperationalError('primary_read_snapshot_not_ended')
                            _closure['rolled_back'] = True
                finally:
                    db.close()
                    if _closure is not None:_closure['closed'] = True

    def _read(self, budget, phase, reader):
        """Retry only a complete read snapshot, within the ORIGINAL budget.

        No write/commit is retried. Every failed connection is closed before
        backoff. Projection/step/read counters and elapsed time accumulate.
        """
        if phase not in ('session', 'source_row', 'cell_commit', 'seam_commit', 'seam_adjacent', 'mapping', 'core_commit', 'core_previous', 'core_pending', 'retention', 'automatic_intent'):
            raise Unavailable('primary_read_phase')
        attempt = 0
        try:
            with budget.phase():
                for attempt in range(1, 4):
                    budget.check()
                    closure = {'closed': False, 'rolled_back': False}; body_error = None
                    scope = getattr(self._list_reads, 'current', None)
                    planning = (scope is not None and scope.get('bounded_planning') is True
                        and scope['budget'] is budget and scope['thread'] == threading.get_ident()
                        and budget._planning_limits == (.4, 120000, 128 * 1024))
                    read_attempt = None
                    if planning:
                        read_attempt = {'body': False, 'refusal': None, 'interrupt': None}
                        def progress():
                            # Same charge/check as Budget.progress, but capture
                            # this callback's own exception, never its shared
                            # historical _progress_refusal marker.
                            budget.steps += 100
                            try:budget.check()
                            except Unavailable as exc:
                                budget._progress_refusal = str(exc)
                                if (read_attempt['body'] and not getattr(budget, 'primary_sql', False)
                                        and budget._planning_limits == (.4, 120000, 128 * 1024)
                                        and str(exc) in ('bounded_planning_capacity',
                                            'cooperative_deadline', 'operation_work_capacity')):
                                    read_attempt['refusal'] = exc
                                return 1
                            return 0
                        read_attempt['progress'] = progress
                    try:
                        context = (self.db(budget, _closure=closure, _planning_read=read_attempt) if planning
                                   else self.db(budget, _closure=closure) if phase == 'automatic_intent'
                                   else self.db(budget))
                        with context as db:
                            try:
                                db.execute('BEGIN')
                                if read_attempt is not None:read_attempt['body'] = True
                                try:result = reader(db)
                                finally:
                                    if read_attempt is not None:read_attempt['body'] = False
                            except sqlite3.OperationalError as exc:
                                body_error = exc
                                if (read_attempt is not None and read_attempt['refusal'] is not None
                                        and type(exc) is sqlite3.OperationalError
                                        and getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_INTERRUPT):
                                    read_attempt['interrupt'] = exc
                                raise
                        budget.check()
                        return result
                    except sqlite3.OperationalError as exc:
                        code = getattr(exc, 'sqlite_errorcode', None)
                        if (read_attempt is not None and type(exc) is sqlite3.OperationalError
                                and exc is body_error and exc is read_attempt['interrupt']
                                and code == sqlite3.SQLITE_INTERRUPT
                                and closure['rolled_back'] and closure['closed']
                                and not getattr(budget, 'primary_sql', False)):
                            raise read_attempt['refusal'] from exc
                        if (phase == 'automatic_intent' and type(exc) is sqlite3.OperationalError
                                and exc is body_error and closure['closed']
                                and type(code) is int and code in _PRELAUNCH_READ_BUSY_CODES):
                            # Only this positively closed read body can be deferred
                            # by the controller before invocation becomes possible.
                            budget.check()
                            if _identity(self.path) != self.identity:
                                raise Unavailable('primary_database_replaced')
                            budget.check()
                            exc._closed_automatic_read_busy = _CLOSED_AUTOMATIC_READ_BUSY
                        if type(code) is not int or code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_BUSY_RECOVERY) or attempt == 3:
                            raise
                    # Existing authority deadlines include this finite backoff; no
                    # timeout, counter or proof/identity check is reset on retry.
                    budget.check(); time.sleep(.005); budget.check()
        except Exception as exc:
            name = type(exc).__name__
            if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', name) is None:name = 'other'
            code = getattr(exc, 'sqlite_errorcode', None)
            if type(code) is not int or not 0 <= code <= (1 << 31) - 1:code = None
            sql_name = getattr(exc, 'sqlite_errorname', None)
            if type(sql_name) is not str or re.fullmatch('SQLITE_[A-Z0-9_]{1,64}', sql_name) is None:sql_name = None
            _diagnostic('native_owned_authority_read_failure phase=%s attempt=%d exception_class=%s sqlite_code=%s sqlite_name=%s',
                        phase, attempt, name, code, sql_name)
            raise

    def retention_counts(self, budget):
        from .native_owned_core_seams import retention_counts
        return self._read(budget, 'retention', lambda db: retention_counts(db, budget))

    def bounded_inventory_current(self, snapshot, budget):
        from .native_owned_core_seams import lexical_snapshot, validate_inventory
        validate_inventory(snapshot)
        origin = float.fromhex(snapshot['origin'])
        # Discover the whole stored band afresh before schema-specific source
        # filtering. V1/v2 retain broad equality; v3 binds the selected band.
        def read(db):
            found = bounded_primary_snapshot(db, snapshot['session_id'], origin,
                                             snapshot['scan_frames'], budget)
            return lexical_snapshot(snapshot['session_id'], found['rows'], found['sources'], origin,
                                    scan_frames=snapshot['scan_frames'], schema=snapshot['schema'])
        return self._read(budget, 'core_commit', read) == snapshot

    def session_valid(self, sid, budget):
        text(sid)
        def read(db):
            return db.execute("SELECT 1 FROM sessions WHERE id=? AND deleted=0 AND processing_mode='legacy'", (sid,)).fetchone()
        return self._read(budget, 'session', read) is not None

    def row(self, cid, budget):
        text(cid)
        def read(db):
            lengths = db.execute('SELECT length(path),length(digest) FROM chunks WHERE id=?', (cid,)).fetchone()
            if lengths is None:return None
            if not 1 <= lengths[0] <= 4096 or lengths[1] != 64:raise Unavailable('primary_source_projection_capacity')
            row = db.execute('''SELECT c.id,c.session_id,c.path,c.digest,c.duration,c.timeline_start,c.next_offset,c.status,
                sc.stream_id FROM chunks c JOIN sessions s ON c.session_id=s.id
                LEFT JOIN stream_chunks sc ON sc.chunk_id=c.id
                WHERE c.id=? AND s.deleted=0 AND s.processing_mode='legacy' ''', (cid,)).fetchone()
            if row is None:return None
            result = dict(row); budget.projection(len(canonical(result, 8192)))
            return result
        return self._read(budget, 'source_row', read)

    def receipt_valid(self, receipt, budget):
        if type(receipt) is not Receipt:raise Unavailable('receipt_type')
        row = self.row(receipt.chunk_id, budget)
        if row is None:return False
        if (row['session_id'] != receipt.session_id or row['digest'] != receipt.digest
                or row['duration'] != receipt.frames / RATE or row['timeline_start'] != receipt.timeline_start
                or row['stream_id'] != receipt.stream_id):
            return False
        path = _owned_path(row['path'], self.uploads, '.audio')
        try:fd, current = _open(path, budget)
        except FileNotFoundError:return False
        try:
            if current != receipt.signature:return False
            _continuity(fd, path, current, budget)
        finally:fd.close()
        again = self.row(receipt.chunk_id, budget)
        stable = ('id', 'session_id', 'path', 'digest', 'duration', 'timeline_start', 'stream_id')
        return again is not None and all(again[key] == row[key] for key in stable)

    def cell_valid(self, doc, budget, *, publishing):
        if self.owner is None:raise Unavailable('receipt_owner_unavailable')
        from .native_owned_core_seams import is_core, current as core_current
        if is_core(doc):return core_current(self, doc, budget)
        from .native_owned_seam_cells import is_composite
        if is_composite(doc):return self.seam_valid(doc, budget)
        dependencies = doc.get('dependencies')
        if type(dependencies) is not list or not 1 <= len(dependencies) <= MAX_PARTS:
            raise Unavailable('source_dependencies_capacity')
        parts = tuple(SourceSpan(**p) for p in dependencies)
        support = doc['support']; validate_mapping(parts, support['start_frame'], support['end_frame'])
        commit = doc['commit']; evidence = doc['commit_evidence']
        if sha(evidence) != commit['sha256'] or evidence['chunk_id'] != commit['chunk_id']:
            raise Unavailable('commit_evidence_digest')
        seen = set()
        for part in parts:
            if part.chunk_id in seen:continue
            seen.add(part.chunk_id)
            receipt = self.owner.receipt(part.chunk_id)
            if (receipt is None or receipt.receipt_id != part.receipt_sha256
                    or receipt.session_id != part.session_id or receipt.digest != part.source_sha256
                    or receipt.frames != part.source_frames or not self.receipt_valid(receipt, budget)):
                return False
        row = self.row(commit['chunk_id'], budget)
        if (row is None or row['session_id'] != parts[0].session_id
                or exact_frame(row['next_offset']) < commit['end']):
            return False
        if publishing:
            if row['status'] not in ('running', 'completed'):return False
            def read(db):
                length = db.execute('SELECT length(metadata) FROM chunks WHERE id=?', (commit['chunk_id'],)).fetchone()
                if length is None:return False
                if length[0] is None or not 1 <= length[0] <= PROOF_BYTES:raise Unavailable('primary_commit_projection_capacity')
                raw = db.execute('SELECT metadata FROM chunks WHERE id=?', (commit['chunk_id'],)).fetchone()[0]
                budget.projection(len(raw.encode('utf-8'))); metadata = json.loads(raw)
                # The owner may be delayed behind another successful primary
                # window. Such a missed optional publication fails closed.
                if (exact_frame(row['next_offset']) != commit['end']
                        or metadata.get('native_diarization') != evidence['native']
                        or metadata.get('core_cut') != evidence['core_cut']):
                    return False
                return True
            if not self._read(budget, 'cell_commit', read):return False
        return True

    def v1_publication_superseded(self, doc, budget):
        """Classify a failed publication, never make it successful.

        Only an ordinary v1 core with a strictly advanced committed frontier,
        unchanged fresh source/session authority and running/completed status
        can avoid revoking unrelated pending work. Equal-frontier drift and
        cancellation/deletion/mutation still fail closed.
        """
        from .native_owned_cells import POLICY_SHA256
        if (type(doc) is not dict or 'schema' in doc or doc.get('policy') != POLICY_SHA256
                or self.owner is None):return False
        generation = self.owner._epoch
        if not self.cell_valid(doc, budget, publishing=False):return False
        commit = doc['commit']; row = self.row(commit['chunk_id'], budget)
        if (row is None or row['status'] not in ('running', 'completed')
                or exact_frame(row['next_offset']) <= commit['end']):return False
        if not self.session_valid(row['session_id'], budget):return False
        if not self.cell_valid(doc, budget, publishing=False):return False
        again = self.row(commit['chunk_id'], budget)
        stable = ('id', 'session_id', 'path', 'digest', 'duration', 'timeline_start', 'stream_id')
        return (again is not None and all(again[key] == row[key] for key in stable)
            and again['status'] in ('running', 'completed')
            and exact_frame(again['next_offset']) >= exact_frame(row['next_offset'])
            and generation is self.owner._epoch)

    def seam_commit_current(self, commit, scope, part, budget):
        """Both completed rows retain their ORIGINAL commitment, even on reads."""
        receipt = self.owner.receipt(part.chunk_id)
        if (receipt is None or receipt.receipt_id != part.receipt_sha256
                or receipt.session_id != scope['session_id'] or receipt.stream_id != scope['stream_id']
                or receipt.digest != part.source_sha256 or receipt.frames != part.source_frames
                or not self.receipt_valid(receipt, budget)):
            return False
        row = self.row(commit['chunk_id'], budget)
        origin = float.fromhex(scope['origin_seconds_hex'])
        if (row is None or row['session_id'] != scope['session_id'] or row['stream_id'] != scope['stream_id']
                or row['status'] != 'completed'
                or exact_frame(row['duration']) != commit['local_end_frame']
                or exact_frame(row['next_offset']) != commit['local_end_frame']
                or exact_frame(row['timeline_start'], origin=origin) != commit['source_origin_frame']):
            return False
        def read(db):
            metadata = seam_commit_projection(db, commit['chunk_id'], budget)
            if metadata is None:return None
            first = db.execute("""SELECT c.timeline_start FROM stream_chunks sc INDEXED BY stream_order JOIN chunks c ON c.id=sc.chunk_id
                WHERE sc.session_id=? AND sc.stream_id=? ORDER BY sc.position LIMIT 1""",
                (scope['session_id'], scope['stream_id'])).fetchone()
            return metadata, first
        observed = self._read(budget, 'seam_commit', read)
        if observed is None:return False
        metadata, first = observed
        if (first is None or first[0] != origin
                or metadata.get('native_diarization') != commit['evidence']['native']
                or metadata.get('core_cut') != commit['evidence']['core_cut']):return False
        again = self.row(commit['chunk_id'], budget)
        return again == row and self.receipt_valid(receipt, budget)

    def seam_adjacent(self, left, right, scope, budget):
        def read(db):
            return db.execute("""SELECT chunk_id,session_id,stream_id,position FROM stream_chunks
                WHERE chunk_id IN (?,?) ORDER BY position LIMIT 2""",
                (left['chunk_id'], right['chunk_id'])).fetchall()
        rows = self._read(budget, 'seam_adjacent', read)
        return (len(rows) == 2 and [r['chunk_id'] for r in rows] == [left['chunk_id'], right['chunk_id']]
            and all(r['session_id'] == scope['session_id'] and r['stream_id'] == scope['stream_id'] for r in rows)
            and rows[1]['position'] == rows[0]['position'] + 1)

    def seam_valid(self, doc, budget):
        from .native_owned_seam_cells import validate_document
        doc, support, parts = validate_document(canonical(doc))
        if self.owner is None:raise Unavailable('receipt_owner_unavailable')
        generation = self.owner._epoch
        # No single B anchor or a durable sidecar may authorize A's contribution.
        for _ in range(2):
            for commit, part in zip(doc['commits'], parts):
                if not self.seam_commit_current(commit, doc['scope'], part, budget):return False
            if not self.seam_adjacent(*doc['commits'], doc['scope'], budget):return False
            if generation is not self.owner._epoch:return False
        return True

    def mapping(self, chunk, scope_start, start, end, budget):
        """A bounded exact local snapshot; never materialize a whole SourcePlan."""
        from .boundary_store import PUBLISHED_CHUNK_SQL
        row = self.row(chunk['id'], budget)
        if row is None:raise Unavailable('primary_source_unavailable')
        def read(db):
            if not db.execute(f'SELECT 1 FROM chunks c WHERE c.id=? AND {PUBLISHED_CHUNK_SQL}',
                    (chunk['id'],)).fetchone():
                raise Unavailable('primary_source_unpublished')
            if row['stream_id'] is None:rows = [row]
            else:
                rows = db.execute(f'''SELECT c.id,c.session_id,c.timeline_start,c.duration,
                    ti.timeline_start AS indexed_start,ti.timeline_end AS indexed_end
                    FROM stream_time_index ti INDEXED BY stream_time_end JOIN chunks c ON c.id=ti.chunk_id
                    WHERE ti.session_id=? AND ti.stream_id=? AND ti.timeline_end>? AND {PUBLISHED_CHUNK_SQL}
                    ORDER BY ti.timeline_end,ti.position LIMIT 129''',
                    (chunk['session_id'], row['stream_id'], scope_start + start / RATE)).fetchall()
            parts = []
            for source in rows:
                origin = exact_frame(source['timeline_start'], origin=scope_start)
                if origin >= end:break
                frames = exact_frame(source['duration'])
                if 'indexed_start' in source.keys() and (source['indexed_start'] != source['timeline_start']
                        or source['indexed_end'] != source['timeline_start'] + frames / RATE):
                    raise Unavailable('source_index_geometry_mismatch')
                a, b = max(start, origin), min(end, origin + frames)
                if a >= b:continue
                if len(parts) >= MAX_PARTS:raise Unavailable('source_part_capacity')
                receipt = self.owner.receipt(source['id'])
                if (receipt is None or receipt.frames != frames or receipt.timeline_start != source['timeline_start']
                        or receipt.session_id != chunk['session_id'] or receipt.stream_id != row['stream_id']):
                    raise Unavailable('source_ingestion_receipt_unavailable')
                parts.append(SourceSpan(chunk['session_id'], source['id'], receipt.receipt_id,
                    receipt.digest, receipt.frames, a - origin, b - origin, a, b))
            return tuple(parts)
        parts = self._read(budget, 'mapping', read)
        validate_mapping(tuple(parts), start, end)
        return tuple(parts)

    def original_pcm(self, parts, budget):
        data = bytearray()
        for part in parts:
            receipt = self.owner.receipt(part.chunk_id)
            if receipt is None or receipt.receipt_id != part.receipt_sha256 or not self.receipt_valid(receipt, budget):
                raise Unavailable('source_ingestion_receipt_unavailable')
            row = self.row(part.chunk_id, budget)
            if row is None:raise Unavailable('source_deleted_before_read')
            path = _owned_path(row['path'], self.uploads, '.audio')
            fd, before = _open(path, budget)
            try:
                if before != receipt.signature:raise Unavailable('source_signature_replaced')
                block = _read_frames(fd, receipt.data_offset + part.source_start * 2,
                                     part.source_end - part.source_start, budget)
                _continuity(fd, path, before, budget)
            finally:fd.close()
            if not self.receipt_valid(receipt, budget):raise Unavailable('source_deleted_after_read')
            data.extend(block)
            if len(data) > CORE_FRAMES * 2:raise Unavailable('original_cell_material_capacity')
        return bytes(data)


@dataclass(frozen=True, slots=True)
class StreamIdentity:
    digest: str
    total: int
    signature: tuple
    budget: Budget


@dataclass(slots=True)
class UploadObservation:
    fd: BoundSource
    before: tuple
    digest: str
    total: int
    data_offset: int
    frames: int
    closed: bool = False

    def close(self):
        if not self.closed:
            self.closed = True; self.fd.close()


class Capture:
    def __init__(self, owner, authority):
        self.owner = owner; self.authority = authority
        self.witness = RollingWitness(); self.scope = None; self.scope_start = 0.
        self.origin_frame = 0; self.budget = Budget(); self.active = False
        self.analyzer = None; self.analyzer_start = 0; self.last_reason = 'not_observed'
        self.seam_enabled = bool(getattr(owner, 'seam_enabled', False))
        self.observed_reset_generation = 0
        self._seam_generation = object(); self._seam_owner_generation = owner._epoch
        self._seam_tail = None; self._seam_provisional = None; self._seam_disabled = False
        self.last_seam_reason = 'not_observed'
        self.core_seams = None
        self._retention_worker = None
        if getattr(owner, 'core_seam_enabled', False):
            from .native_owned_core_seams import CoreSeams
            self.core_seams = CoreSeams(self)

    def bind_retention_worker(self, worker):
        """Only the app's one ordinary Worker.loop supplies the idle fence.

        Direct calls to process/_process_windows/prepare are not this contract.
        Bind before owner.start; an absent/replaced/dead worker never means idle.
        """
        if not self.owner.compact_gc_enabled:return False
        from .worker import Worker
        if (type(worker) is not Worker or worker.store is not self.authority.store
                or worker.settings is not self.authority.store.settings
                or worker.native_owned_cells is not self or self.authority.owner is not self.owner
                or self.owner.authority is not self.authority or self.core_seams is None
                or configuration_reason(worker.settings) is not None
                or not worker.settings.native_owned_compact_gc_enabled
                or not worker.settings.native_owned_cells_enabled or not worker.settings.native_owned_core_seams_enabled
                or self.owner.configuration_reason is not None
                or self._retention_worker not in (None, worker)
                or self.owner._retention_capture not in (None, self)):
            raise Unavailable('compact_gc_binding_unavailable')
        self._retention_worker = worker; self.owner._retention_capture = self
        self.retention_roots()
        return True

    def retention_roots(self):
        """Bounded in-memory roots; call only behind the primary claim fence."""
        from .worker import Worker
        worker = self._retention_worker; owner = self.owner
        target = getattr(getattr(worker, 'thread', None), '_target', None)
        if (type(worker) is not Worker or getattr(target, '__self__', None) is not worker
                or getattr(target, '__func__', None) is not Worker.loop
                or worker.store is not self.authority.store or worker.settings is not self.authority.store.settings
                or worker.native_owned_cells is not self or owner._retention_capture is not self
                or owner.authority is not self.authority or self.authority.owner is not owner
                or configuration_reason(worker.settings) is not None
                or not worker.settings.native_owned_compact_gc_enabled
                or not worker.settings.native_owned_cells_enabled or not worker.settings.native_owned_core_seams_enabled
                or not owner.enabled or not owner.core_seam_enabled or not owner.compact_gc_enabled
                or owner.configuration_reason is not None or self.core_seams is None):
            raise Unavailable('compact_gc_binding_unavailable')
        if worker.current_chunk is not None:raise Unavailable('compact_gc_producer_active')
        if self.core_seams.generation is not owner._epoch:raise Unavailable('compact_gc_generation_changed')
        if self.core_seams.flight is not None:raise Unavailable('compact_gc_pending_proof')
        if self.active or self._seam_provisional is not None:
            raise Unavailable('compact_gc_producer_roots_pending')
        # Source GC additionally proves a completed-prefix replay frontier.
        # Core preparation's exact previous compact is protected in SQL.
        return frozenset(() if self._seam_tail is None else (self._seam_tail.part.chunk_id,))

    def receipt_retention_start(self, sid, stream, future_frame):
        """Future reset replay plus context; an empty queue is never a proof."""
        self.retention_roots()
        if not self.owner.receipt_gc_enabled or not self._retention_worker.settings.native_owned_receipt_gc_enabled:
            raise Unavailable('receipt_gc_binding_unavailable')
        integer(future_frame)
        # Both native_worker and native-owned proof policy use 300-second
        # epochs and 339200 frames (21.2 seconds) of replay/context preroll.
        start = max(0, future_frame // (300 * RATE) * (300 * RATE) - 339200)
        context = self._retention_worker.settings.context_seconds
        if type(context) not in (int, float) or not 0 <= context <= 5:
            raise Unavailable('receipt_gc_context_unknown')
        # This currently cannot precede the 21.2-second replay bound, but is
        # explicit even with no retained analyzer and follows Worker extraction.
        start = min(start, max(0, future_frame - round(context * RATE)))
        if self.analyzer is not None:
            if self.scope is None or len(self.scope) != 3:
                raise Unavailable('receipt_gc_context_unknown')
            if self.scope == ('stream', sid, stream):
                if self.scope_start != 0.:raise Unavailable('receipt_gc_context_unknown')
                start = min(start, integer(self.analyzer_start))
        return start

    def prepare_core_seam(self, *args, required=False):
        if self.core_seams is not None:
            return self.core_seams.prepare(*args,required=True) if required else self.core_seams.prepare(*args)
        if required:raise Unavailable('automatic_core_seams_required')

    def start_bounded(self, result):
        """One optional planning budget and packet for this primary opportunity."""
        if (not getattr(self.owner, 'bounded_core_enabled', False)
                or not getattr(self.authority.store.settings, 'native_owned_bounded_core_support_enabled', False)
                or self.core_seams is None):
            raise Unavailable('bounded_configuration_required')
        store_key = getattr(self.owner, 'retention_store_key', None)
        digest(store_key)
        budget = Budget.for_bounded(self.authority.store.settings.native_owned_bounded_core_support_seconds)
        result['_native_bounded_support'] = {'budget': budget, 'store_key': store_key,
            'origins': [], 'plans': [], 'waiters': [], 'reason': 'pending',
            'draft_ids': [], 'snapshot': None, 'batch': None}

    def bounded_prepare(self, chunk, native, offset, end, result, coordinator, stop,
                        commit, left, ordinary_turns, observed, budget):
        """Prepare one numeric plan and one authentic current query per turn.

        Waiting retains only origin certificates. PCM is retained only for a
        transaction-ready numeric proposal, under the existing handoff ceiling.
        The transaction still has to validate actual lexical retention and CAS.
        """
        from . import moss_automatic as automatic, native_owned_core_seams as core
        from .native_owned_support_cache import ReadError
        handoff = result['_native_bounded_support']
        if handoff['budget'] is not budget:raise Unavailable('bounded_budget_mismatch')
        packet = result['_native_core_seam']; scope = commit['scope']
        # Native may already have finished lookahead while later primary cores
        # are still uncommitted. Only this committed frontier reaching its true
        # input end closes waiters for missing future authority.
        handoff['true_eof'] = (observed['true_eof'] and commit['part']['logical_end']
            == observed['replay_start'] + observed['state']['input']['frame'])
        handoff['logical_end'] = commit['part']['logical_end']
        evidence = {'chunk_id': chunk['id'], 'start': exact_frame(offset), 'end': exact_frame(end),
                    'native': native['state'], 'core_cut': result['metadata']['core_cut']}
        candidates = []; used_bytes = 0
        def reason(exc):
            value = str(exc) if isinstance(exc, (Unavailable, ReadError)) else 'bounded_prepare_unavailable'
            return value if re.fullmatch('[a-z_]{1,80}', value) else 'bounded_prepare_unavailable'
        def add_origin(plan, commits):
            origin = core.origin_certificate(plan['proof'], commits, plan['binding'])
            target = core.original_target(origin)
            prepared = dict(plan, target=target)
            handoff['origins'].append(prepared)
            existing = next((item for item in candidates if item['target']['key'] == target['key']), None)
            if existing is not None:
                existing['target'] = core.append_target_origin(existing['target'], origin)
            else:candidates.append({'owner_key': plan['key'], 'target': target, 'new': True})
        try:
            with budget.planning_phase(), self.authority.list_read_scope(budget, bounded_planning=True):
                def context(db):
                    batch = automatic.batch_context(db, chunk, handoff['store_key'], budget)
                    waiters = automatic.read_waiters(db, batch['batch_id'], sha(scope), budget, limit=4)
                    return batch, waiters
                handoff['batch'], handoff['waiters'] = self.authority._read(budget, 'automatic_intent', context)
                packet['_bounded_context'] = handoff['batch']
                for row in handoff['waiters']:
                    candidates.append({'owner_key': row['candidate_key'], 'target': row['target'],
                                       'new': False, 'waiter': row})
                # These are the genuine unchanged original ordinary cells.
                for plan in automatic.plans(result['_native_automatic_cells'], packet):
                    add_origin(plan, [commit])
                result['_native_automatic_cells'] = ()
                # Read at most three exact predecessor links. A missing or
                # different-domain predecessor supplies no invented authority.
                chain = [commit]; current = left
                while current is not None and len(chain) < 4:
                    if current['scope'] != scope:break
                    chain.insert(0, current)
                    if len(chain) == 4:break
                    at = current['part']['logical_start']
                    def previous(db):
                        rows = db.execute('''SELECT slot FROM native_core_commits
                            WHERE sid=? AND scope_key=? AND logical_end=? LIMIT 2''',
                            (chunk['session_id'], core.scope_key(scope), at)).fetchall()
                        budget.projection(len(rows) * 64)
                        if len(rows) > 1:raise Unavailable('core_left_ambiguous')
                        return core.read_commit(db, rows[0][0], budget, reserve=True) if rows else None
                    current = self.authority._read(budget, 'core_previous', previous)
                ca, cb = core.bounded_chain(chain)
                found = self.authority._read(budget, 'core_commit', lambda db:
                    bounded_primary_snapshot(db, chunk['session_id'], self.scope_start, [ca, cb], budget))
                drafts = bounded_prospective_rows(chunk, offset, end, result, found['sources'], budget)
                if len(found['rows']) + len(drafts) > 128:raise Unavailable('bounded_primary_capacity')
                if {row['id'] for row in drafts} & {row['id'] for row in found['rows']}:
                    raise Unavailable('bounded_prospective_id_collision')
                rows = sorted([*found['rows'], *drafts], key=lambda row: row['id'])
                snapshot = core.lexical_snapshot(chunk['session_id'], rows, found['sources'],
                    self.scope_start, scan_frames=[ca, cb], schema=core.COMPACT_INVENTORY_SCHEMA)
                handoff['snapshot'] = snapshot; handoff['draft_ids'] = [row['id'] for row in drafts]
                handoff['commits'] = chain
                used_bytes = len(canonical(snapshot)) + sum(
                    len(item['proof']) + len(item['binding']) + len(canonical(item['target']))
                    for item in handoff['origins']) + sum(
                    len(item['proof'] or b'') + len(item['binding']) + len(canonical(item['target']))
                    for item in handoff['waiters'])
                if used_bytes > HANDOFF_BYTES:raise Unavailable('bounded_handoff_capacity')
                ranges = []
                for index, candidate in enumerate(candidates):
                    try:
                        if index >= 4:raise Unavailable('waiting_service_capacity')
                        target = candidate['target']; identity, roots = core.validate_target(target)
                        if any(root['scope'] != scope for root in roots):raise Unavailable('unresolved_scope_transition')
                        waiter = candidate.get('waiter')
                        manager = getattr(self.authority.store, 'moss_automatic', None)
                        if manager is None:raise Unavailable('bounded_wait_clock_unavailable')
                        if manager.bounded_expired(target['key'], waiter['wait_deadline'] if waiter else None):
                            raise Unavailable('unresolved_wait_deadline')
                        if waiter is not None and (waiter['wait_deadline'] <= time.time()
                                or handoff['batch']['ordinal'] > waiter['wait_last_ordinal']):
                            raise Unavailable('unresolved_wait_deadline')
                        first, last = identity['components'][0][0], identity['components'][-1][1]
                        # Every legal <=20s support containing O fits this
                        # <=40s numeric envelope; this is not query evidence.
                        lo, hi = max(ca, last - 20 * RATE), min(cb, first + 20 * RATE)
                        shape = core.select_support(target, chain, snapshot, lo, hi)
                        candidate['support'] = shape
                        ranges.append((shape['start_frame'], shape['end_frame']))
                    except (Unavailable, ReadError, ValueError, TypeError, KeyError) as exc:
                        candidate['reason'] = reason(exc)
                seam_shape = None
                if left is not None and left['scope'] == scope:
                    seam_shape = core.geometry([left, commit])
                    seam_range = (seam_shape['start_frame'], seam_shape['end_frame'])
                    if not ranges or max(b for _, b in [*ranges, seam_range]) - min(a for a, _ in [*ranges, seam_range]) <= CORE_FRAMES:
                        ranges.append(seam_range)
                    else:seam_shape = None
                qa = max(self.origin_frame + round(native['extract_start'] * RATE), observed['replay_start'])
                qb = self.origin_frame + exact_frame(native['extract_end'])
                if ranges:
                    lo, hi = min(a for a, _ in ranges), max(b for _, b in ranges)
                    if hi - lo > CORE_FRAMES:
                        # Oldest waiter first; other numeric choices stay fixed
                        # and visibly wait rather than invoking another query.
                        lo, hi = ranges[0]
                    if not qa <= lo < hi <= qb:qa, qb = lo, hi
                if (not observed['replay_start'] <= qa < qb
                        <= observed['replay_start'] + observed['state']['processed']['frame']
                        or qb - qa > CORE_FRAMES or qa < self.witness.ring_start
                        or qb > self.witness.ring_end):
                    raise Unavailable('witness_unavailable')
            turns, proof = ordinary_turns, observed
            original_qa = max(self.origin_frame + round(native['extract_start'] * RATE), observed['replay_start'])
            original_qb = self.origin_frame + exact_frame(native['extract_end'])
            if (qa, qb) != (original_qa, original_qb):
                if stop():raise Unavailable('core_seam_cancelled')
                token = self.observed_reset_generation; actions = self.witness.actions
                old_budget = self.budget; self.budget = budget; self.active = True
                self._bounded_response_budget = budget
                self._bounded_response_error = None
                try:
                    # This replaces the opportunity's ordinary seam request.
                    response = coordinator.process.request('turns', stop,
                        start=(qa - observed['replay_start']) / RATE,
                        end=(qb - observed['replay_start']) / RATE)
                finally:
                    self.active = False; self.budget = old_budget
                    self._bounded_response_budget = None
                with budget.planning_phase():
                    if self._bounded_response_error is not None:
                        raise Unavailable(self._bounded_response_error)
                    if (token != self.observed_reset_generation or self.owner._epoch is not self.core_seams.generation
                            or self.witness.actions != actions + 1 or self.witness.state != observed['state']):
                        raise Unavailable('core_seam_query_not_observed')
                    transformed = []
                    for turn in response['turns']:
                        a = max(qa / RATE, observed['replay_start'] / RATE + turn['start'])
                        b = min(qb / RATE, observed['replay_start'] / RATE + turn['end'])
                        if a < b:transformed.append(dict(turn, start=a - qa / RATE, end=b - qa / RATE))
                    query_native = dict(native, extract_start=(qa - self.origin_frame) / RATE,
                        extract_end=(qb - self.origin_frame) / RATE, turns=transformed)
                    turns, proof = self.witness.prepared(query_native, self.scope_start, self.origin_frame)
            with budget.planning_phase(), self.authority.list_read_scope(budget, bounded_planning=True):
                query_digest = hashlib.sha256(self.witness.read(qa, qb)).hexdigest()
                base_query = {'query_start_frame': qa, 'query_end_frame': qb,
                    'query_pcm16le_sha256': query_digest, 'native': proof,
                    'turns': [{'label': turn.label, 'original': [turn.start, turn.end]} for turn in turns]}
                if seam_shape is not None and qa <= seam_shape['start_frame'] < seam_shape['end_frame'] <= qb:
                    try:
                        pcm = self.witness.read(seam_shape['start_frame'], seam_shape['end_frame'])
                        expected = core.mapping([left, commit], seam_shape)
                        if pcm != self.authority.original_pcm(expected, budget):raise Unavailable('core_seam_source_pcm_mismatch')
                        seam = core.make_cell([left, commit], dict(base_query,
                            support_pcm16le_sha256=hashlib.sha256(pcm).hexdigest()), evidence, pcm)
                        packet['proof'] = seam.proof
                        seam_plan = automatic.plans((), packet)[0]
                        used_bytes += len(seam_plan['proof']) + len(seam_plan['binding'])
                        add_origin(seam_plan, [left, commit])
                        used_bytes += len(canonical(handoff['origins'][-1]['target']))
                        if used_bytes > HANDOFF_BYTES:raise Unavailable('bounded_handoff_capacity')
                        del seam, pcm
                    except (Unavailable, ReadError, ValueError, TypeError, KeyError) as exc:
                        packet['reason'] = reason(exc)
                # Origin registration has a separate four-origin ceiling, but
                # only four targets total may receive this bind opportunity.
                # No waiter ever gains a second numeric support selection.
                for index, candidate in enumerate(candidates):
                    try:
                        if index >= 4:raise Unavailable('waiting_service_capacity')
                        if candidate.get('reason'):raise Unavailable(candidate['reason'])
                        manager = getattr(self.authority.store, 'moss_automatic', None)
                        if manager is None:raise Unavailable('bounded_wait_clock_unavailable')
                        waiter = candidate.get('waiter')
                        if manager.bounded_expired(candidate['target']['key'], waiter['wait_deadline'] if waiter else None):
                            raise Unavailable('unresolved_wait_deadline')
                        shape = candidate.get('support')
                        if shape is None:
                            shape = core.select_support(candidate['target'], chain, snapshot, qa, qb)
                            candidate['support'] = shape
                        a, b = shape['start_frame'], shape['end_frame']
                        if not qa <= a < b <= qb:raise Unavailable('witness_unavailable')
                        current_overlap = core.overlap_components(turns, a, b)
                        if any(not any(x <= lo < hi <= y for x, y in current_overlap)
                               for lo, hi in candidate['target']['identity']['components']):
                            raise Unavailable('bounded_current_target_disagreement')
                        _, original_roots = core.validate_target(candidate['target'])
                        wanted = {core.slot(root) for root in original_roots}
                        needed = [position for position, item in enumerate(chain)
                            if core.slot(item) in wanted or item is commit
                            or item['part']['logical_start'] < b and a < item['part']['logical_end']]
                        final_chain = chain[min(needed):max(needed) + 1]
                        query = dict(base_query, capture_run=scope['run'],
                            turns=[dict(turn, label=scope['run'] + ':' + turn['label']) for turn in base_query['turns']],
                            support_pcm16le_sha256='0' * 64)
                        charge = 2 * (b - a) + len(canonical(candidate['target'])) + len(canonical(snapshot)) + len(canonical(query))
                        if used_bytes + charge > HANDOFF_BYTES:raise Unavailable('bounded_handoff_capacity')
                        pcm = self.witness.read(a, b)
                        if pcm != self.authority.original_pcm(core.bounded_mapping(final_chain, a, b), budget):
                            raise Unavailable('core_seam_source_pcm_mismatch')
                        query['support_pcm16le_sha256'] = hashlib.sha256(pcm).hexdigest()
                        used_bytes += charge
                        handoff['plans'].append({'owner_key': candidate['owner_key'], 'target': candidate['target'],
                            'commits': final_chain, 'snapshot': snapshot, 'query': query, 'evidence': evidence,
                            'support': shape, 'pcm': pcm, 'reason': None})
                    except (Unavailable, ReadError, ValueError, TypeError, KeyError) as exc:
                        handoff['plans'].append({'owner_key': candidate['owner_key'],
                            'target': candidate['target'], 'reason': reason(exc)})
                    finally:pcm = None
                handoff['reason'] = 'prepared'; packet['reason'] = 'bounded_prepared'
                handoff['logical_end'] = cb
        except (Unavailable, ReadError, ValueError, TypeError, KeyError) as exc:
            handoff['reason'] = reason(exc); packet['reason'] = handoff['reason']
            handoff['plans'] = []
        finally:
            # The ordinary originals now live only as bounded proof/binding
            # proposals. They cannot accidentally use legacy automatic enqueue.
            result['_native_automatic_cells'] = ()
            self.analyzer = None

    def drain_core_seams(self, status=None):
        if self.core_seams is not None:
            if status is not None:
                self.core_seams.last_reason = status
                self.owner.retention_changed()
            return self.core_seams.drain()

    def _failure(self, exc):
        _diagnostic_failure('capture', exc)
        reason = str(exc) if isinstance(exc, Unavailable) else ''
        self.last_reason = reason if re.fullmatch('[a-z_]{1,80}', reason) else 'optional_capture_unavailable'
        self.analyzer = None
        self.invalidate_seam(self.last_reason)

    def health(self):
        result = dict(self.owner.health(), last_capture_reason=self.last_reason)
        if 'retention' in result:
            result['retention']['roots'].update(producer_active=int(self.active),
                seam_tail=int(self._seam_tail is not None), seam_provisional=int(self._seam_provisional is not None),
                core_flight=int(self.core_seams is not None and self.core_seams.flight is not None))
        if self.core_seams is not None:
            result['last_core_seam_reason'] = self.core_seams.last_reason
            result['core_seam_pending'] = self.core_seams.work_pending or self.core_seams.flight is not None
            result['core_seam_reset_support'] = 'observed_replay'
        if self.seam_enabled:
            result.update(last_seam_reason=self.last_seam_reason, seam_tail_bytes=(
                self._seam_tail.material_bytes if self._seam_tail is not None else 0),
                seam_working_reservation_bytes=4 * 1024 * 1024)
        return result

    def begin_prepare(self, chunk, info):
        self.active = False; self.analyzer = None; self.budget = Budget()
        # An unconsumed provisional has no known True commit. It cannot become
        # a tail merely because a later prepare starts, even for the same chunk.
        if self._seam_provisional is not None:self.invalidate_seam('unconfirmed_primary_commit')
        if self._seam_owner_generation is not self.owner._epoch:self.invalidate_seam('capture_revoked')
        if not self.owner.enabled or not self.owner.health()['ready']:return
        try:
            with self.budget.phase():
                scope = ('stream', chunk['session_id'], info['stream_id']) if info else ('upload', chunk['session_id'], chunk['id'])
                if scope != self.scope:
                    self.invalidate_seam('seam_scope_changed')
                    self.witness = RollingWitness(); self.scope = scope
                self.scope_start = info['start'] if info else chunk['timeline_start']
                self.origin_frame = exact_frame(chunk['timeline_start'], origin=self.scope_start)
                self.active = True
        except Exception as exc:self._failure(exc)

    def end_prepare(self):self.active = False

    def observe_response(self, response):
        if not self.active:return
        try:
            # The reset serial belongs to Capture; RollingWitness.__init__ is
            # deliberately allowed to clear its own action counter on reset.
            if type(response) is dict and set(response) == {'state', 'provenance'}:
                self.invalidate_seam('seam_native_reset')
                if self.observed_reset_generation >= (1 << 31) - 1:
                    self._seam_disabled = True; raise Unavailable('seam_reset_generation_overflow')
                self.observed_reset_generation += 1
            phase = (self.budget.planning_phase if getattr(self, '_bounded_response_budget', None) is self.budget
                     else self.budget.phase)
            with phase():self.witness.observe(response)
            if self.witness.failure:
                self.invalidate_seam('seam_invalid_native_observation')
                if type(response) is dict and set(response) == {'state', 'provenance'}:self._seam_disabled = True
        except Exception as exc:
            if (getattr(self, '_bounded_response_budget', None) is self.budget
                    and isinstance(exc, Unavailable)):
                # A refused optional TURNS projection does not corrupt the
                # unchanged native push/frontier state. This response cannot
                # authorize a plan; a later ordinary observation remains valid.
                self._bounded_response_error = str(exc)
                return
            self.witness.fail('native_response_budget_or_proof'); self._failure(exc)

    def observe_pcm(self, kind, path, start, end, analyzer_local=None):
        if not self.owner.enabled or self.scope is None or self.witness.failure:return
        if kind == 'native' and not self.active:return
        try:
            with self.budget.phase():
                expected = None
                if analyzer_local is not None:
                    # Only NativeCoordinator's integer-frame-derived local bounds
                    # may be translated this way. Do not round arbitrary absolute
                    # timestamps, native responses, or safe_cut commit boundaries.
                    if (kind != 'analyzer' or type(analyzer_local) is not tuple or len(analyzer_local) != 3
                            or any(type(v) not in (int, float) for v in (*analyzer_local, start, end))):
                        raise Unavailable('invalid_analyzer_coordinates')
                    origin, local_start, local_end = analyzer_local
                    if exact_frame(origin, origin=self.scope_start) != integer(self.origin_frame):
                        raise Unavailable('analyzer_origin_mismatch')
                    if start != origin + local_start or end != origin + local_end:
                        raise Unavailable('analyzer_translation_mismatch')
                    # Preceding-upload context has valid negative local bounds.
                    local_frames = tuple(exact_frame(abs(v)) * (-1 if v < 0 else 1)
                                         for v in (local_start, local_end))
                    expected = tuple(integer(self.origin_frame + v) for v in local_frames)
                    start, end = (self.scope_start + v / RATE for v in expected)
                a = exact_frame(start, origin=self.scope_start); b = exact_frame(end, origin=self.scope_start)
                if expected is not None and (a, b) != expected:raise Unavailable('analyzer_frame_translation_loss')
                if not 0 < b - a <= CORE_FRAMES:raise Unavailable('observed_window_capacity')
                pcm = observed_pcm(path, b - a, self.budget)
                if kind == 'native':self.witness.observe_pcm(a, b, pcm)
                elif kind == 'analyzer':
                    if pcm != self.witness.read(a, b):raise Unavailable('analyzer_native_context_mismatch')
                    self.analyzer = pcm; self.analyzer_start = a
                else:raise Unavailable('unknown_material_observer')
        except Exception as exc:
            self.witness.fail('material_observation_unavailable'); self._failure(exc)

    def native_core_cut(self, chunk, native, offset, nominal_end, result, window_seconds, legacy_end):
        """Select an internal core boundary only after exact native witnessing.

        Worker uses this before ownership, capture, compact evidence and the
        primary transaction, so all consumers receive the same frame/RATE.
        Global exact_frame, raw turns, sources and old proof policies stay strict.
        """
        try:
            with self.budget.phase():
                if self.scope is None or self.analyzer is None:
                    raise Unavailable('native_cut_witness_unavailable')
                if (exact_frame(chunk['timeline_start'], origin=self.scope_start) != self.origin_frame
                        or native['end'] != nominal_end
                        or result.get('metadata', {}).get('diarization_turns', []) != native['turns']):
                    raise Unavailable('native_cut_transform_mismatch')
                turns, _ = self.witness.prepared(native, self.scope_start, self.origin_frame)
                local = native['extract_start']
                a = self.origin_frame + exact_frame(abs(local)) * (-1 if local < 0 else 1)
                b = self.origin_frame + exact_frame(native['extract_end'])
                start = self.origin_frame + exact_frame(offset)
                target = self.origin_frame + exact_frame(nominal_end)
                if self.analyzer_start != a or len(self.analyzer) != (b - a) * 2:
                    raise Unavailable('native_cut_analyzer_geometry')
                if (type(window_seconds) not in (int, float) or not math.isfinite(window_seconds)
                        or not 1 <= window_seconds <= 60):raise Unavailable('native_cut_window_config')
                # Settings permits non-grid window durations. This is a guard,
                # not an observed endpoint: retain its exact rational value.
                window = Fraction(window_seconds) * RATE
                frame = _sample_gap_cut(turns, start, target, a, b, window)
                local_frame = integer(frame - self.origin_frame)
                selected = local_frame / RATE
                if exact_frame(selected) != local_frame:raise Unavailable('native_cut_precision_loss')
                return selected
        except Exception:
            # Bounded numeric-only diagnostic. The lost legacy value is never
            # used as evidence or accepted through an epsilon tolerance.
            try:
                if type(legacy_end) in (int, float) and math.isfinite(legacy_end):
                    raw = float(legacy_end); nearest = round(raw * RATE)
                    _diagnostic('native_owned_cut_refusal value=%r hex=%s frame_residual=%r permitted_error_frames=0',
                                raw, raw.hex(), raw * RATE - nearest)
            except Exception:pass  # Diagnostics must preserve the original refusal.
            raise

    def prepare(self, chunk, native, offset, end, result, *, required=False):
        if self.analyzer is None or native is None:
            if required:raise Unavailable('automatic_capture_unavailable')
            return ()
        try:
            with self.budget.phase():
                start_local = exact_frame(offset); end_local = exact_frame(end)
                a, b = self.origin_frame + start_local, self.origin_frame + end_local
                parts = self.authority.mapping(chunk, self.scope_start, a, b, self.budget)
                original = self.authority.original_pcm(parts, self.budget)
                observed = self.analyzer[2 * (a - self.analyzer_start):2 * (b - self.analyzer_start)]
                if original != observed:raise Unavailable('original_native_analyzer_mismatch')
                evidence = {'chunk_id': chunk['id'], 'start': start_local, 'end': end_local,
                            'native': native['state'], 'core_cut': result['metadata']['core_cut']}
                commit_hash = sha(evidence)
                cells = make_cells(witness=self.witness, native=native, scope_start=self.scope_start,
                    origin_frame=self.origin_frame, core_start=a, core_end=b,
                    analyzer_start=self.analyzer_start, analyzer_pcm=self.analyzer, mapping=parts,
                    chunk_id=chunk['id'], commit_start=start_local, commit_end=end_local,
                    commit_sha256=commit_hash, commit_evidence=evidence)
                self.last_reason = 'validated_selected' if cells else 'no_native_overlap'
                if self.seam_enabled:
                    try:self.prepare_seam(chunk, native, parts, evidence, cells, a, b)
                    except Exception as exc:
                        _diagnostic_failure('seam_prepare', exc)
                        # Optional composition must not discard already valid v1 cells.
                        self.invalidate_seam(str(exc) if isinstance(exc, Unavailable) else 'seam_prepare_unavailable')
                return cells
        except Exception as exc:
            self._failure(exc)
            if required:raise
            return ()
        finally:self.analyzer = None

    def committed(self, cells):
        if self.seam_enabled:
            try:cells = self.commit_seam(cells)
            except Exception as exc:
                _diagnostic_failure('seam_commit', exc)
                self.invalidate_seam(str(exc) if isinstance(exc, Unavailable) else 'seam_commit_unavailable')
        if not cells:return {'status': self.last_reason}
        # Called only after the exact primary commit_window returned True.
        try:
            budget = Budget()
            with budget.phase():
                for cell in cells:
                    if not self.authority.cell_valid(json.loads(cell.proof), budget, publishing=True):
                        raise Unavailable('primary_commit_or_source_unavailable')
                result = self.owner.submit('publish', cells)
                if self.seam_enabled and not isinstance(result, Operation):self.invalidate_seam('seam_publication_unavailable')
                self.last_reason = 'publication_pending' if isinstance(result, Operation) else result['status']
                return result
        except Exception as exc:
            self._failure(exc); return {'status': 'optional_unavailable', 'reason': self.last_reason}

    def invalidate_seam(self, reason):
        # Revocation can race an unlocked source read. A replaced token makes
        # every local reference unusable; its bounded bytes stay charged until
        # that synchronous hook returns, and no replacement slot is admitted.
        self._seam_generation = object(); self._seam_owner_generation = self.owner._epoch
        self._seam_tail = None; self._seam_provisional = None
        self.last_seam_reason = reason if type(reason) is str and re.fullmatch('[a-z_]{1,80}', reason) else 'seam_unavailable'

    def _seam_current(self, token, reset):
        if (token is not self._seam_generation or self._seam_owner_generation is not self.owner._epoch
                or reset != self.observed_reset_generation or self._seam_disabled
                or not self.seam_enabled or not self.owner.enabled):
            raise Unavailable('seam_generation_revoked')

    def prepare_seam(self, chunk, native, mapping, evidence, cells, a, b):
        from .native_owned_seam_cells import Piece, SIDE_FRAMES, TAIL_BYTES, WORKING_BYTES, STORE_POLICY_SHA256
        from .native_owned_core_seams import store_policy
        if self.owner._store_policy not in (STORE_POLICY_SHA256, store_policy()):
            raise Unavailable('incompatible_legacy_cell_policy')
        if self._seam_disabled or self.scope is None or self.scope[0] != 'stream':
            raise Unavailable('seam_explicit_stream_required')
        token, reset = self._seam_generation, self.observed_reset_generation
        self._seam_current(token, reset)
        if b != self.origin_frame + exact_frame(chunk['duration']):raise Unavailable('seam_terminal_core_required')
        if len(mapping) != 1 or mapping[0].chunk_id != chunk['id']:raise Unavailable('seam_one_source_per_core_required')
        turns, native_proof = self.witness.prepared(native, self.scope_start, self.origin_frame)
        scope = {'session_id': chunk['session_id'], 'stream_id': self.scope[2],
                 'origin_seconds_hex': float(self.scope_start).hex(), 'epoch_id': native_proof['epoch_id'],
                 'epoch_index': native_proof['epoch_index'], 'reset_generation': reset}
        commit = {'chunk_id': chunk['id'], 'source_origin_frame': self.origin_frame,
            'local_start_frame': evidence['start'], 'local_end_frame': evidence['end'],
            'logical_start_frame': a, 'logical_end_frame': b, 'evidence': evidence, 'evidence_sha256': sha(evidence)}
        # Full response intervals remain clipped to their actual query. The
        # original analyzer context is hashed before its ordinary release.
        witness = {'commit_index': 0, 'query_start_frame': max(self.analyzer_start, native_proof['replay_start']),
            'query_end_frame': self.analyzer_start + len(self.analyzer) // 2,
            'analyzer_start_frame': self.analyzer_start, 'analyzer_end_frame': self.analyzer_start + len(self.analyzer) // 2,
            'analyzer_pcm16le_sha256': hashlib.sha256(self.analyzer).hexdigest(),
            'piece_pcm16le_sha256': '0' * 64, 'native': native_proof,
            'turns': [{'label': t.label, 'original': [t.start, t.end]} for t in turns]}
        # Detach mutable producer dictionaries once, inside the proof ceiling.
        metadata = json.loads(canonical({'scope': scope, 'commit': commit, 'witness': witness}))
        def piece(lo, hi):
            part = intersect_mapping(mapping, lo, hi)[0]
            pcm = self.analyzer[2 * (lo - self.analyzer_start):2 * (hi - self.analyzer_start)]
            if pcm != self.witness.read(lo, hi):raise Unavailable('seam_native_analyzer_mismatch')
            if pcm != self.authority.original_pcm((part,), self.budget):raise Unavailable('seam_original_piece_mismatch')
            self._seam_current(token, reset)
            return Piece(metadata['scope'], metadata['commit'],
                dict(metadata['witness'], piece_pcm16le_sha256=hashlib.sha256(pcm).hexdigest()), part, pcm)
        # Current candidate has at most a prefix plus a terminal tail. The old
        # single tail remains charged; no pending queue or retained analyzer.
        old = self._seam_tail
        tail_start = max(a, b - SIDE_FRAMES)
        projected = (old.material_bytes if old is not None else 0) + 4 * SIDE_FRAMES + 6 * PROOF_BYTES
        if projected > WORKING_BYTES:raise Unavailable('seam_assembly_working_capacity')
        tail = piece(tail_start, b)
        prefix = None
        if old is not None:
            if (old.scope != scope or evidence['start'] != 0 or old.commit['logical_end_frame'] != a
                    or old.commit['chunk_id'] == chunk['id']):
                self.invalidate_seam('seam_nonadjacent_tail'); old = None
                token = self._seam_generation
            else:
                if not self.authority.seam_commit_current(old.commit, scope, old.part, self.budget):
                    raise Unavailable('seam_previous_commit_unavailable')
                prefix_end = min(b, a + SIDE_FRAMES)
                prefix = tail if tail_start == a and prefix_end == b else piece(a, prefix_end)
        self._seam_current(token, reset)
        self._seam_provisional = (cells, token, reset, old, prefix, tail)
        self.last_seam_reason = 'seam_provisional'

    def commit_seam(self, cells):
        from .native_owned_seam_cells import make_composite, geometry, validate_witness
        provisional = self._seam_provisional; self._seam_provisional = None
        if provisional is None:return cells
        original, token, reset, left, right, tail = provisional
        if cells is not original:raise Unavailable('seam_commit_hook_mismatch')
        self._seam_current(token, reset)
        budget = Budget()
        with budget.phase():
            # Promotion is reached only by the unchanged Worker's True-return
            # hook, including an A which selected no ordinary cell at all.
            if not self.authority.seam_commit_current(tail.commit, tail.scope, tail.part, budget):
                raise Unavailable('seam_current_commit_unavailable')
            self._seam_current(token, reset)
            result = cells
            if left is not None and right is not None:
                if len(cells) >= BATCH_CELLS:
                    self.last_seam_reason = 'seam_batch_saturated'
                else:
                    try:composite = make_composite(left, right)
                    except Unavailable as exc:
                        # An unselected A-B seam must not discard valid B's
                        # terminal tail before the next B-C seam is considered.
                        if exc.args != ('no_same_response_native_overlap',):raise
                        # Selection precedes full composite admission. Validate
                        # unselected witnesses too, including a distinct tail.
                        shape = geometry(left.commit['logical_start_frame'],
                            left.commit['logical_end_frame'], right.commit['logical_end_frame'])
                        validate_mapping((left.part, right.part), shape['start_frame'], shape['end_frame'])
                        for index, piece in ((0, left), (1, right), (0, tail)):
                            validate_witness(dict(piece.witness, commit_index=index),
                                piece.commit, piece.scope, index,
                                piece.part.logical_start, piece.part.logical_end)
                        if (left.witness['native']['provenance'] != right.witness['native']['provenance']
                                or left.witness['native']['observed_action_count'] >= right.witness['native']['observed_action_count']):
                            raise Unavailable('seam_native_continuity')
                        self.last_seam_reason = 'seam_no_native_overlap'
                    else:
                        if sum(len(c.pcm) + len(c.proof) for c in cells) + len(composite.pcm) + len(composite.proof) > HANDOFF_BYTES:
                            self.last_seam_reason = 'seam_handoff_saturated'
                        elif not self.authority.cell_valid(json.loads(composite.proof), budget, publishing=True):
                            raise Unavailable('seam_both_commits_unavailable')
                        else:
                            self._seam_current(token, reset)
                            result = cells + (composite,); self.last_seam_reason = 'seam_validated_selected'
            else:self.last_seam_reason = 'seam_tail_promoted'
            self._seam_current(token, reset)
            self._seam_tail = tail
            return result

    def streamed_descriptor(self, fd, streamed_digest, total):
        if not self.owner.enabled or not self.owner.health()['ready']:return None
        try:
            budget = Budget()
            with budget.phase():
                digest(streamed_digest); integer(total, (1 << 32) + 8)
                st = regular(os.fstat(fd))
                if st.st_size != total:raise Unavailable('streamed_descriptor_size_mismatch')
                return StreamIdentity(streamed_digest, total, signature(st), budget)
        except Exception as exc:self._failure(exc); return None

    def prepare_upload(self, path, streamed_digest, total, streamed_identity=None):
        if not self.owner.enabled or not self.owner.health()['ready']:return None
        fd = None
        try:
            if type(streamed_identity) is not StreamIdentity:raise Unavailable('streamed_descriptor_unobserved')
            budget = streamed_identity.budget
            with budget.phase():
                digest(streamed_digest); integer(total, (1 << 32) + 8)
                path = _owned_path(path, self.authority.uploads, '.part')
                fd, before = _open(path, budget)
                if (before[2] != total or (streamed_identity.digest, streamed_identity.total, streamed_identity.signature)
                        != (streamed_digest, total, before)):
                    raise Unavailable('streamed_upload_descriptor_mismatch')
                data_offset, frames = riff_geometry(fd, total, budget)
                _continuity(fd, path, before, budget)
                token = UploadObservation(fd, before, streamed_digest, total, data_offset, frames)
                fd = None; return token
        except Exception as exc:self._failure(exc); return None
        finally:
            if fd is not None:fd.close()

    def observe_recording_receipt(self, token, sid, cid, final, timeline_start, stream_id):
        """Freeze genuine upload observation for the primary recording outbox.

        This is NOT committed primary authority. The caller atomically stores
        the canonical payload beside its staged ordinary chunk; the existing
        owner validates the committed row/path before any receipt acceptance.
        No observation may be reconstructed from a path after restart.
        """
        if token is None:return None
        try:
            if type(token) is not UploadObservation or token.closed:
                raise Unavailable('recording_upload_observation_required')
            budget = Budget()
            with budget.phase():
                path = _owned_path(final, self.authority.uploads, '.audio')
                final_sig = signature(regular(os.fstat(token.fd.fd)))
                if final_sig[:4] != token.before[:4]:raise Unavailable('upload_descriptor_changed_before_rename')
                _continuity(token.fd, path, final_sig, budget)
                return Receipt(sid, cid, token.digest, token.frames, token.data_offset,
                               token.total, final_sig, timeline_start, stream_id)
        except Exception as exc:
            self._failure(exc); return None
        finally:token.close()

    def finish_upload(self, token, sid, cid, final, created):
        if token is None:return {'status': 'receipt_unavailable'}
        try:
            if not created:return {'status': 'duplicate_no_new_receipt'}
            budget = Budget()
            with budget.phase():
                path = _owned_path(final, self.authority.uploads, '.audio')
                final_sig = signature(regular(os.fstat(token.fd.fd)))
                # Rename may change ctime, but not descriptor, size or mtime.
                if final_sig[:4] != token.before[:4]:raise Unavailable('upload_descriptor_changed_before_rename')
                _continuity(token.fd, path, final_sig, budget)
                row = self.authority.row(cid, budget)
                if (row is None or row['session_id'] != sid or row['digest'] != token.digest
                        or row['path'] != str(path) or row['duration'] != token.frames / RATE):
                    raise Unavailable('upload_primary_receipt_mismatch')
                receipt = Receipt(sid, cid, token.digest, token.frames, token.data_offset,
                                  token.total, final_sig, row['timeline_start'], row['stream_id'])
                if not self.authority.receipt_valid(receipt, budget):raise Unavailable('upload_changed_after_add')
                return self.owner.submit('receipt', receipt)
        except Exception as exc:
            self._failure(exc); return {'status': 'receipt_unavailable', 'reason': self.last_reason}
        finally:token.close()

    def duplicate(self, cid, streamed_digest):
        try:
            budget = Budget()
            with budget.phase():
                receipt = self.owner.receipt(cid)
                if receipt is None or receipt.digest != streamed_digest or not self.authority.receipt_valid(receipt, budget):
                    return {'status': 'duplicate_without_qualifying_receipt'}
                return {'status': 'existing_receipt', 'receipt_id': receipt.receipt_id}
        except Exception as exc:self._failure(exc); return {'status': 'receipt_unavailable'}

    def close(self):
        self.active = False; self.analyzer = None; self.witness.fail('capture_closed')
        self.invalidate_seam('capture_closed')
        return self.owner.close()


def configuration_reason(settings, fragment_provider_factory=None):
    bounded = getattr(settings, 'native_owned_bounded_core_support_enabled', False)
    if type(bounded) is not bool:return 'bounded_configuration_required'
    if bounded and not (getattr(settings, 'moss_automatic_enabled', False)
            and getattr(settings, 'native_owned_core_seams_enabled', False)
            and getattr(settings, 'native_owned_cells_enabled', False)
            and getattr(settings, 'native_owned_support_enabled', False)
            and getattr(settings, 'lexical_evidence_enabled', False)):
        return 'bounded_configuration_required'
    if not getattr(settings, 'native_owned_cells_enabled', False):return None
    if (settings.diarization_backend != 'nemotron' or settings.inference_device != 'cpu'
            or settings.overlap_backend != 'none' or not settings.start_worker
            or fragment_provider_factory is not None):
        return 'unsupported_optional_configuration'
    return None


def register(store, *, fragment_provider_factory=None, instance_lock=None):
    settings = store.settings; enabled = bool(getattr(settings, 'native_owned_cells_enabled', False))
    directory = Path(settings.data_dir) / 'native-owned-cells'
    if not enabled and not os.path.lexists(directory):return None
    authority = PrimaryAuthority(store)
    key = str(authority.path)
    if not _REGISTRY_LOCK.acquire(timeout=LOCK_SECONDS):raise Unavailable('registry_busy')
    try:
        existing = _REGISTRY.get(key)
        if existing is not None:
            if existing.authority.identity != authority.identity:raise Unavailable('primary_database_replaced')
            if getattr(existing.owner, 'bounded_core_enabled', False) != bool(getattr(settings, 'native_owned_bounded_core_support_enabled', False)):
                raise Unavailable('bounded_activation_requires_new_owner')
            return existing
        if _REGISTRY:raise Unavailable('one_optional_component_owner')
        reason = configuration_reason(settings, fragment_provider_factory)
        profile = None if reason else qualified_runtime(settings.data_dir)
        owner = CellStore(directory, authority, enabled=enabled, qualification=profile, instance_lock=instance_lock,
            seam_enabled=bool(getattr(settings, 'native_owned_seam_cells_enabled', False)),
            core_seam_enabled=bool(getattr(settings, 'native_owned_core_seams_enabled', False)),
            compact_gc_enabled=bool(getattr(settings, 'native_owned_compact_gc_enabled', False)),
            receipt_gc_enabled=bool(getattr(settings, 'native_owned_receipt_gc_enabled', False)))
        authority.owner = owner; capture = Capture(owner, authority)
        _REGISTRY[key] = capture
    finally:_REGISTRY_LOCK.release()
    # Caller binds this exact registry owner before starting its thread.
    if reason:owner._status = reason; owner.configuration_reason = reason
    return capture


def unregister(capture):
    if capture is None:return True
    stopped = capture.close()
    if not stopped:return False
    if not _REGISTRY_LOCK.acquire(timeout=LOCK_SECONDS):return False
    try:
        key = str(capture.authority.path)
        if _REGISTRY.get(key) is capture:_REGISTRY.pop(key)
    finally:_REGISTRY_LOCK.release()
    return True


def notify_deleted(store, sid):
    """Post-primary-deletion hook, including alternate Store object callers."""
    key = str(Path(store.path).absolute())
    if not _REGISTRY_LOCK.acquire(timeout=LOCK_SECONDS):return True
    try:capture = _REGISTRY.get(key)
    finally:_REGISTRY_LOCK.release()
    if capture is None:return None
    capture.invalidate_seam('capture_revoked')
    return capture.owner.revoked()
