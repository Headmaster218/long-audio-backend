"""Passive Worker/upload integration and current primary authorization gates.

No primary schema writes, original-file retirement, decoder/model construction
or dispatch. Optional failures never replace the caller's primary outcome.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
import struct
import threading

from .native_owned_cells import (Unavailable, RollingWitness, SourceSpan, Cell,
    canonical, sha, text, digest, integer, exact_frame, validate_mapping, make_cells,
    RATE, MAX_FRAME, CORE_FRAMES, MAX_PARTS, IO_BYTES, PROOF_BYTES)
from .native_owned_cell_store import (CellStore, Receipt, Budget, qualified_runtime,
    RECEIPT_BYTES, LOCK_SECONDS, Operation)

_REGISTRY = {}
_REGISTRY_LOCK = threading.Lock()


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


def _open(path, budget):
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_NONBLOCK'):
        raise Unavailable('source_descriptor_primitives_unavailable')
    budget.check()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0))
    try:
        info = regular(os.fstat(fd)); entry = regular(os.stat(path, follow_symlinks=False))
        if signature(info) != signature(entry):raise Unavailable('source_path_descriptor_mismatch')
        budget.check()
        return fd, signature(info)
    except BaseException:
        os.close(fd); raise


def _continuity(fd, path, expected, budget):
    budget.check()
    if (signature(regular(os.fstat(fd))) != expected
            or signature(regular(os.stat(path, follow_symlinks=False))) != expected):
        raise Unavailable('source_changed_during_unlocked_io')
    budget.check()


def riff_geometry(fd, size, budget):
    """At most 64 KiB header; data must be the final exact RIFF PCM extent."""
    integer(size, (1 << 32) + 8, 46)
    wanted = min(size, IO_BYTES); budget.read(wanted)
    header = os.pread(fd, wanted, 0); budget.check()
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
        block = os.pread(fd, size, offset); budget.check()
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
    finally:os.close(fd)


class PrimaryAuthority:
    """Read-only, zero-busy-timeout authority; never uses Store.db's write path."""
    def __init__(self, store):
        self.store = store; self.path = Path(store.path).absolute()
        self.uploads = Path(store.settings.data_dir).absolute() / 'uploads'
        self.identity = _identity(self.path); self.owner = None

    @contextmanager
    def db(self, budget):
        budget.check()
        if _identity(self.path) != self.identity:raise Unavailable('primary_database_replaced')
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=0., isolation_level=None)
        try:
            db.row_factory = sqlite3.Row; db.set_progress_handler(budget.progress, 100)
            db.execute('PRAGMA query_only=ON'); db.execute('PRAGMA busy_timeout=0')
            yield db
            budget.check()
            if _identity(self.path) != self.identity:raise Unavailable('primary_database_replaced')
        finally:db.close()

    def session_valid(self, sid, budget):
        text(sid)
        with self.db(budget) as db:
            row = db.execute("SELECT 1 FROM sessions WHERE id=? AND deleted=0 AND processing_mode='legacy'", (sid,)).fetchone()
        return row is not None

    def row(self, cid, budget):
        text(cid)
        with self.db(budget) as db:
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
        finally:os.close(fd)
        again = self.row(receipt.chunk_id, budget)
        stable = ('id', 'session_id', 'path', 'digest', 'duration', 'timeline_start', 'stream_id')
        return again is not None and all(again[key] == row[key] for key in stable)

    def cell_valid(self, doc, budget, *, publishing):
        if self.owner is None:raise Unavailable('receipt_owner_unavailable')
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
            with self.db(budget) as db:
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

    def mapping(self, chunk, scope_start, start, end, budget):
        """A bounded exact local snapshot; never materialize a whole SourcePlan."""
        row = self.row(chunk['id'], budget)
        if row is None:raise Unavailable('primary_source_unavailable')
        with self.db(budget) as db:
            if row['stream_id'] is None:rows = [row]
            else:
                rows = db.execute('''SELECT c.id,c.session_id,c.timeline_start,c.duration,
                    ti.timeline_start AS indexed_start,ti.timeline_end AS indexed_end
                    FROM stream_time_index ti INDEXED BY stream_time_end JOIN chunks c ON c.id=ti.chunk_id
                    WHERE ti.session_id=? AND ti.stream_id=? AND ti.timeline_end>?
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
            finally:os.close(fd)
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
    fd: int
    before: tuple
    digest: str
    total: int
    data_offset: int
    frames: int
    closed: bool = False

    def close(self):
        if not self.closed:
            self.closed = True; os.close(self.fd)


class Capture:
    def __init__(self, owner, authority):
        self.owner = owner; self.authority = authority
        self.witness = RollingWitness(); self.scope = None; self.scope_start = 0.
        self.origin_frame = 0; self.budget = Budget(); self.active = False
        self.analyzer = None; self.analyzer_start = 0; self.last_reason = 'not_observed'

    def _failure(self, exc):
        reason = str(exc) if isinstance(exc, Unavailable) else ''
        self.last_reason = reason if re.fullmatch('[a-z_]{1,80}', reason) else 'optional_capture_unavailable'
        self.analyzer = None

    def health(self):
        return dict(self.owner.health(), last_capture_reason=self.last_reason)

    def begin_prepare(self, chunk, info):
        self.active = False; self.analyzer = None; self.budget = Budget()
        if not self.owner.enabled or not self.owner.health()['ready']:return
        try:
            with self.budget.phase():
                scope = ('stream', chunk['session_id'], info['stream_id']) if info else ('upload', chunk['session_id'], chunk['id'])
                if scope != self.scope:self.witness = RollingWitness(); self.scope = scope
                self.scope_start = info['start'] if info else chunk['timeline_start']
                self.origin_frame = exact_frame(chunk['timeline_start'], origin=self.scope_start)
                self.active = True
        except Exception as exc:self._failure(exc)

    def end_prepare(self):self.active = False

    def observe_response(self, response):
        if not self.active:return
        try:
            with self.budget.phase():self.witness.observe(response)
        except Exception as exc:
            self.witness.fail('native_response_budget_or_proof'); self._failure(exc)

    def observe_pcm(self, kind, path, start, end):
        if not self.owner.enabled or self.scope is None or self.witness.failure:return
        if kind == 'native' and not self.active:return
        try:
            with self.budget.phase():
                a = exact_frame(start, origin=self.scope_start); b = exact_frame(end, origin=self.scope_start)
                if not 0 < b - a <= CORE_FRAMES:raise Unavailable('observed_window_capacity')
                pcm = observed_pcm(path, b - a, self.budget)
                if kind == 'native':self.witness.observe_pcm(a, b, pcm)
                elif kind == 'analyzer':
                    if pcm != self.witness.read(a, b):raise Unavailable('analyzer_native_context_mismatch')
                    self.analyzer = pcm; self.analyzer_start = a
                else:raise Unavailable('unknown_material_observer')
        except Exception as exc:
            self.witness.fail('material_observation_unavailable'); self._failure(exc)

    def prepare(self, chunk, native, offset, end, result):
        if self.analyzer is None or native is None:return ()
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
                return cells
        except Exception as exc:
            self._failure(exc); return ()
        finally:self.analyzer = None

    def committed(self, cells):
        if not cells:return {'status': self.last_reason}
        # Called only after the exact primary commit_window returned True.
        try:
            budget = Budget()
            with budget.phase():
                for cell in cells:
                    if not self.authority.cell_valid(json.loads(cell.proof), budget, publishing=True):
                        raise Unavailable('primary_commit_or_source_unavailable')
                result = self.owner.submit('publish', cells)
                self.last_reason = 'publication_pending' if isinstance(result, Operation) else result['status']
                return result
        except Exception as exc:
            self._failure(exc); return {'status': 'optional_unavailable', 'reason': self.last_reason}

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
            if fd is not None:os.close(fd)

    def finish_upload(self, token, sid, cid, final, created):
        if token is None:return {'status': 'receipt_unavailable'}
        try:
            if not created:return {'status': 'duplicate_no_new_receipt'}
            budget = Budget()
            with budget.phase():
                path = _owned_path(final, self.authority.uploads, '.audio')
                final_sig = signature(regular(os.fstat(token.fd)))
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
        return self.owner.close()


def configuration_reason(settings, fragment_provider_factory=None):
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
            return existing
        if _REGISTRY:raise Unavailable('one_optional_component_owner')
        reason = configuration_reason(settings, fragment_provider_factory)
        profile = None if reason else qualified_runtime(settings.data_dir)
        owner = CellStore(directory, authority, enabled=enabled, qualification=profile, instance_lock=instance_lock)
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
    return capture.owner.revoked()
