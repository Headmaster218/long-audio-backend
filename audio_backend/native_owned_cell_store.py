"""Optional serialized cell storage; all SQLite mutation belongs to one owner.

One exact runtime has a finite conditional SQLite/unix-VFS allocation and
process-crash recovery qualification. This is not a universal quota or a
power-loss/fsync guarantee. The 64 MiB working-memory reservation remains
unmeasured, never a hard RSS claim. No caller runs a rollback, recovery,
checkpoint or fsync. There is no publication retry queue. Default remains OFF.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import threading
import time

from .native_owned_cells import (Unavailable, Cell, SourceSupport, canonical, digest,
    integer, text, POLICY_SHA256, PROOF_BYTES, CELL_PCM_BYTES, MAX_PARTS,
    BATCH_CELLS, HANDOFF_BYTES, IO_BYTES, validate_document, float32_digest)
from .native_owned_seam_cells import (POLICY_SHA256 as SEAM_POLICY_SHA256,
    STORE_POLICY_SHA256, is_composite, anchor as seam_anchor, coverage as seam_coverage)

TOTAL_BYTES = 64 * 1024**2
DATABASE_BYTES = 24 * 1024**2
JOURNAL_BYTES = 32 * 1024**2
ANCILLARY_BYTES = 2 * 1024**2
RESERVED_ENVELOPE_BYTES = 58 * 1024**2
WORKING_RESERVATION_BYTES = 64 * 1024**2
PAGE_SIZE = 4096
MAX_PAGE_COUNT = 6144
MAX_CELLS = 1024
MAX_RECEIPTS = 4096
RECEIPT_BYTES = 1024
MAX_PINS = 2
PIN_RESERVATION_BYTES = 768 * 1024
PAGE_ROWS = 20
OP_SECONDS = .5
OWNER_OP_SECONDS = 2.
LOCK_SECONDS = .005
SQL_STEPS = 150000
SQL_PROJECTION_BYTES = 256 * 1024
RETENTION_REFRESH_SECONDS = 1.
SOURCE_READ_BYTES = 40 * 1024**2
SCHEMA = 'native-owned-cell-store-v1'
ALLOWED_FILES = frozenset(('cells.sqlite3', 'cells.sqlite3-journal',
                          'cells.sqlite3-wal', 'cells.sqlite3-shm'))

_DIAGNOSTIC_LIMIT = 32
_DIAGNOSTIC_COUNT = 0
_DIAGNOSTIC_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


def _diagnostic(message, *args):
    """Best-effort fixed-field records; never alter optional failure handling."""
    global _DIAGNOSTIC_COUNT
    if not _DIAGNOSTIC_LOCK.acquire(blocking=False):return
    try:
        if _DIAGNOSTIC_COUNT >= _DIAGNOSTIC_LIMIT:return
        _DIAGNOSTIC_COUNT += 1
    finally:_DIAGNOSTIC_LOCK.release()
    try:_LOGGER.warning('%s', (message % args)[:1024])
    except Exception:pass


def _diagnostic_failure(stage, exc):
    # Do not log exception messages, tracebacks, identifiers or source paths.
    try:
        if stage not in ('capture', 'seam_prepare', 'seam_commit', 'core_seam_prepare', 'core_seam_drain'):return
        name = type(exc).__name__
        if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', name) is None:name = 'other'
        code = getattr(exc, 'sqlite_errorcode', None)
        if type(code) is not int or not 0 <= code <= (1 << 31) - 1:code = None
        sql_name = getattr(exc, 'sqlite_errorname', None)
        if type(sql_name) is not str or re.fullmatch('SQLITE_[A-Z0-9_]{1,64}', sql_name) is None:sql_name = None
        _diagnostic('native_owned_capture_failure stage=%s exception_class=%s sqlite_code=%s sqlite_name=%s',
                    stage, name, code, sql_name)
    except Exception:pass


class Budget:
    """Finite cooperative work, not a hard bound on individual OS calls."""
    def __init__(self):
        self.elapsed = 0.; self.read_bytes = 0; self.sql_bytes = 0; self.steps = 0
        self._began = None
        self.seconds = OP_SECONDS; self.deadline = None
        self._refusal_diagnosed = False
        self._planning_limits = None
        self._bounded_seconds = None
        self._progress_refusal = None

    def reserve_planning(self):
        """Reserve transaction/notice headroom in this same cumulative budget."""
        now = time.monotonic()
        elapsed = self.elapsed + (now-self._began if self._began is not None else 0.)
        return (elapsed <= .4 and self.steps <= 120000 and self.sql_bytes <= 128*1024
                and self.read_bytes <= SOURCE_READ_BYTES)

    @contextmanager
    def _bounded_phase(self, limits):
        previous = self._planning_limits
        self._planning_limits = limits
        try:
            with self.phase():yield self
        finally:self._planning_limits = previous

    def planning_phase(self):return self._bounded_phase((.4,120000,128*1024))
    def finalization_phase(self):
        return self._bounded_phase(((self._bounded_seconds or .5)-.02,148000,252*1024))
    def notice_phase(self):
        return self._bounded_phase((self._bounded_seconds or .5,150000,256*1024))

    @classmethod
    def for_bounded(cls, seconds):
        # Opted-in fresh capture only; ordinary/owner budgets keep their exact
        # existing defaults. Planning and every numeric work cap are unchanged.
        if type(seconds) not in (int,float) or not .5 <= seconds <= 1.:
            raise Unavailable('invalid_bounded_budget_seconds')
        budget = cls(); budget._bounded_seconds = float(seconds)
        budget.seconds = float(seconds)
        return budget

    @classmethod
    def for_owner(cls, operation=None):
        budget = cls(); budget.seconds = OWNER_OP_SECONDS
        # Queue time consumes the original admission deadline, never a reset.
        budget.deadline = operation.deadline if operation is not None else None
        return budget

    @contextmanager
    def phase(self):
        if self._began is not None:
            self.check(); yield self; self.check(); return
        self._began = time.monotonic()
        try:
            self.check(); yield self; self.check()
        finally:
            self.elapsed += time.monotonic() - self._began
            self._began = None

    def check(self):
        now = time.monotonic()
        elapsed = self.elapsed + (now - self._began if self._began is not None else 0.)
        if self._planning_limits is not None:
            seconds, steps, sql_bytes = self._planning_limits
            if elapsed > seconds or self.steps > steps or self.sql_bytes > sql_bytes:
                self._diagnose_refusal('bounded_planning_capacity', elapsed, now)
                raise Unavailable('bounded_planning_capacity')
        if elapsed > self.seconds or (self.deadline is not None and now >= self.deadline):
            self._diagnose_refusal('cooperative_deadline', elapsed, now)
            raise Unavailable('cooperative_deadline')
        if self.read_bytes > SOURCE_READ_BYTES or self.sql_bytes > SQL_PROJECTION_BYTES or self.steps > SQL_STEPS:
            self._diagnose_refusal('operation_work_capacity', elapsed, now)
            raise Unavailable('operation_work_capacity')

    def _diagnose_refusal(self, reason, elapsed, now):
        if self._refusal_diagnosed:return
        self._refusal_diagnosed = True
        phase = {( .4, 120000, 128*1024): 'planning',
                 ((self._bounded_seconds or .5)-.02, 148000, 252*1024): 'finalization',
                 (self._bounded_seconds or .5, 150000, 256*1024): 'notice'}.get(self._planning_limits, 'operation')
        limits = self._planning_limits or (self.seconds, SQL_STEPS, SQL_PROJECTION_BYTES)
        _diagnostic('native_owned_budget_refusal reason=%s elapsed=%g seconds=%g deadline_remaining=%s '
                    'elapsed_exceeded=%d deadline_expired=%d '
                    'read_bytes=%d read_limit=%d sql_bytes=%d sql_limit=%d steps=%d step_limit=%d '
                    'phase=%s phase_seconds=%g phase_step_limit=%d phase_sql_limit=%d '
                    'phase_elapsed_exceeded=%d phase_steps_exceeded=%d phase_sql_exceeded=%d',
                    reason, elapsed, self.seconds,
                    None if self.deadline is None else '%g' % (self.deadline - now),
                    elapsed > self.seconds, self.deadline is not None and now >= self.deadline,
                    self.read_bytes, SOURCE_READ_BYTES, self.sql_bytes, SQL_PROJECTION_BYTES, self.steps, SQL_STEPS,
                    phase, *limits, elapsed > limits[0], self.steps > limits[1], self.sql_bytes > limits[2])

    def read(self, count):
        integer(count, IO_BYTES); self.read_bytes += count; self.check()

    def projection(self, count):
        integer(count, SQL_PROJECTION_BYTES); self.sql_bytes += count; self.check()

    def progress(self):
        self.steps += 100
        try:self.check()
        except Unavailable as exc:
            self._progress_refusal=str(exc)
            return 1
        return 0


def _runtime_hash(path, budget, maximum=64 * 1024**2):
    resolved = Path(path).resolve(strict=True)
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0))
    try:
        before = os.fstat(fd)
        signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise Unavailable('runtime_binary_file_capacity')
        h = hashlib.sha256(); remaining = before.st_size
        while remaining:
            count = min(IO_BYTES, remaining); budget.read(count)
            block = os.read(fd, count); budget.check()
            if not block:raise Unavailable('runtime_binary_truncated')
            h.update(block); remaining -= len(block)
        for current in (os.fstat(fd), os.stat(resolved, follow_symlinks=False)):
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != signature:
                raise Unavailable('runtime_binary_changed')
        return resolved, before, h.hexdigest()
    finally:os.close(fd)


def runtime_fingerprints(budget=None):
    """One observed built-in form; mapping absence is not a static-link proof."""
    import _sqlite3
    from importlib.machinery import BuiltinImporter
    budget = budget or Budget()
    with budget.phase():
        if sys.platform != 'linux':raise Unavailable('runtime_mapping_platform')
        spec = _sqlite3.__spec__
        if (spec is None or spec.name != '_sqlite3' or spec.origin != 'built-in'
                or spec.loader is not BuiltinImporter or getattr(_sqlite3, '__file__', None) is not None):
            raise Unavailable('unqualified_sqlite_binding_form')
        executable, executable_info, executable_hash = _runtime_hash(sys.executable, budget)
        actual = os.stat('/proc/self/exe')
        if (actual.st_dev, actual.st_ino, actual.st_size) != (
                executable_info.st_dev, executable_info.st_ino, executable_info.st_size):
            raise Unavailable('runtime_interpreter_container_mismatch')
        package = Path(sqlite3.__file__).resolve(strict=True).parent
        if (Path(sqlite3.__file__).name != '__init__.py'
                or Path(sqlite3.dbapi2.__file__).resolve(strict=True) != package / 'dbapi2.py'):
            raise Unavailable('sqlite_package_origin_mismatch')
        inventory = {}; entry_count = 0
        for directory in (package, package / '__pycache__'):
            if directory.name == '__pycache__' and not os.path.lexists(directory):continue
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode):raise Unavailable('sqlite_package_directory_alias')
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > 32:raise Unavailable('sqlite_package_entry_capacity')
                    suffix = '.pyc' if directory.name == '__pycache__' else '.py'
                    if not entry.name.endswith(suffix):continue
                    if len(inventory) >= 16:raise Unavailable('sqlite_package_file_capacity')
                    _, info, hashed = _runtime_hash(entry.path, budget, 256 * 1024)
                    name = str(Path(entry.path).relative_to(package))
                    inventory[name] = {'bytes': info.st_size, 'sha256': hashed}
        if not {'__init__.py', 'dbapi2.py'}.issubset(inventory):raise Unavailable('sqlite_package_missing')
        mappings = bytearray()
        with open('/proc/self/maps', 'rb', buffering=0) as stream:
            while True:
                budget.read(IO_BYTES)
                block = stream.read(IO_BYTES); budget.check()
                if not block:break
                mappings.extend(block)
                if len(mappings) > 256 * 1024:raise Unavailable('runtime_mapping_capacity')
        for line in mappings.splitlines():
            fields = line.split(None, 5)
            if len(fields) == 6 and b'libsqlite3' in fields[5]:
                raise Unavailable('unexpected_separate_sqlite_mapping')
        return {'interpreter_bytes': executable_info.st_size,
            'interpreter_sha256': executable_hash,
            'interpreter_realpath_sha256': hashlib.sha256(os.fsencode(executable)).hexdigest(),
            'sqlite3_package_sha256': hashlib.sha256(canonical(inventory)).hexdigest(),
            'sqlite3_binding': 'observed-built-in-no-separate-sqlite-mapping-v1',
            'sqlite3_extension_sha256': None, 'sqlite3_library_sha256': None,
            'sqlite3_library_realpath_sha256': None}


@dataclass(frozen=True, slots=True)
class RuntimeQualification:
    """Reviewed evidence for one exact runtime + local allocation/VFS profile.

    Constructing this record is not qualification. Production accepts only a
    record shipped in QUALIFIED_RUNTIMES after the independent finite gate.
    Fixtures may inject a simulated record into the private store constructor.
    """
    sqlite_source_id: str
    sqlite_version: str
    compile_options_sha256: str
    platform: str
    vfs: str
    filesystem_device: int
    sector_bytes_max: int
    journal_headers_max: int
    allocation_slack_bytes_max: int
    evidence_sha256: str
    interpreter_bytes: int
    interpreter_sha256: str
    interpreter_realpath_sha256: str
    sqlite3_package_sha256: str
    sqlite3_binding: str
    sqlite3_extension_sha256: str | None
    sqlite3_library_sha256: str | None
    sqlite3_library_realpath_sha256: str | None

    def __post_init__(self):
        text(self.sqlite_source_id, 256); text(self.sqlite_version, 64)
        digest(self.compile_options_sha256); digest(self.evidence_sha256)
        integer(self.interpreter_bytes, 64 * 1024**2, 1)
        for name in ('interpreter_sha256', 'interpreter_realpath_sha256', 'sqlite3_package_sha256'):
            digest(getattr(self, name))
        if (self.sqlite3_binding != 'observed-built-in-no-separate-sqlite-mapping-v1'
                or self.sqlite3_extension_sha256 is not None or self.sqlite3_library_sha256 is not None
                or self.sqlite3_library_realpath_sha256 is not None):
            raise Unavailable('unqualified_sqlite_binding_form')
        if self.platform != 'linux' or self.vfs != 'unix':
            raise Unavailable('unqualified_platform_vfs')
        integer(self.filesystem_device)
        integer(self.sector_bytes_max, 65536, 512)
        integer(self.journal_headers_max, 1024, 1)
        integer(self.allocation_slack_bytes_max, ANCILLARY_BYTES)
        # Every DB page can appear once; no cache-spill journal segmentation.
        journal_bound = MAX_PAGE_COUNT * (PAGE_SIZE + 8) + self.sector_bytes_max * self.journal_headers_max
        if journal_bound > JOURNAL_BYTES or DATABASE_BYTES + JOURNAL_BYTES + self.allocation_slack_bytes_max > RESERVED_ENVELOPE_BYTES:
            raise Unavailable('unproved_rollback_allocation_envelope')

    def verify(self, db, directory, budget=None):
        budget = budget or Budget()
        with budget.phase():
            if (sys.platform != self.platform or directory.stat().st_dev != self.filesystem_device
                    or db.execute('SELECT sqlite_source_id()').fetchone()[0] != self.sqlite_source_id
                    or sqlite3.sqlite_version != self.sqlite_version):
                raise Unavailable('runtime_qualification_mismatch')
            options = [r[0] for r in db.execute('PRAGMA compile_options')]
            if len(options) > 512 or hashlib.sha256(canonical(sorted(options))).hexdigest() != self.compile_options_sha256:
                raise Unavailable('sqlite_build_qualification_mismatch')
            self.verify_binaries(budget)

    def verify_binaries(self, budget):
        fingerprints = runtime_fingerprints(budget)
        if any(getattr(self, name) != value for name, value in fingerprints.items()):
            raise Unavailable('runtime_binary_qualification_mismatch')


QUALIFIED_RUNTIMES: tuple[RuntimeQualification, ...] = (RuntimeQualification(
    sqlite_source_id='2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9',
    sqlite_version='3.53.1',
    compile_options_sha256='600e922c0614cd07a1640976eb2e283b43072322af46cbeb0df7a998990095cb',
    platform='linux', vfs='unix', filesystem_device=27,
    sector_bytes_max=65536, journal_headers_max=1,
    allocation_slack_bytes_max=2097152,
    evidence_sha256='7097319b7e75d603dd0730033e29847b76631d71ae1bf8215f2052fc01377f55',
    interpreter_bytes=30894944,
    interpreter_sha256='fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7',
    interpreter_realpath_sha256='57c6e5fe045b039c86e05cea65b65abe385014580d745dfdb38674b8acafb5d8',
    sqlite3_package_sha256='274ebd26b21834cfd405783cb389f8d7d22d4ce27db918c9781a363b0718f437',
    sqlite3_binding='observed-built-in-no-separate-sqlite-mapping-v1',
    sqlite3_extension_sha256=None, sqlite3_library_sha256=None,
    sqlite3_library_realpath_sha256=None),)


def qualified_runtime(data_dir):
    # No inferred Linux/default-VFS qualification and no user override flag.
    return next((q for q in QUALIFIED_RUNTIMES if q.platform == sys.platform
                 and q.filesystem_device == Path(data_dir).stat().st_dev), None)


@dataclass(frozen=True, slots=True)
class Receipt:
    session_id: str
    chunk_id: str
    digest: str
    frames: int
    data_offset: int
    size: int
    signature: tuple[int, int, int, int, int]
    timeline_start: float
    stream_id: str | None

    def __post_init__(self):
        text(self.session_id); text(self.chunk_id); digest(self.digest)
        integer(self.frames, (1 << 32) // 2, 1)
        integer(self.data_offset, IO_BYTES, 44); integer(self.size, (1 << 32) + 8, 46)
        if self.data_offset + self.frames * 2 != self.size:
            raise Unavailable('receipt_riff_extent')
        if (type(self.signature) is not tuple or len(self.signature) != 5
                or any(type(n) is not int or not 0 <= n < (1 << 64) for n in self.signature)
                or self.signature[2] != self.size):
            raise Unavailable('receipt_signature')
        if type(self.timeline_start) not in (int, float) or not 0 <= self.timeline_start <= (1 << 53) / 16000:
            raise Unavailable('receipt_timeline')
        if self.stream_id is not None:text(self.stream_id)
        canonical(asdict(self), RECEIPT_BYTES)

    @property
    def encoded(self):return canonical(asdict(self), RECEIPT_BYTES)

    @property
    def receipt_id(self):return hashlib.sha256(self.encoded).hexdigest()

    @classmethod
    def decode(cls, value):
        if type(value) is not bytes or len(value) > RECEIPT_BYTES:
            raise Unavailable('receipt_length')
        item = json.loads(value); item['signature'] = tuple(item['signature'])
        result = cls(**item)
        if result.encoded != value:raise Unavailable('noncanonical_receipt')
        return result


class Operation:
    """One handoff; dispatch delivery has one claimant or one drainage owner.

    No application callback is invoked under the delivery lock. The admission
    boolean passed to claim must be sampled under the caller's admission gate.
    A timed-out dispatch never exposes its copied Pin through result or wait.
    """
    def __init__(self, kind, payload, epoch, wake=None):
        self.kind = kind; self.payload = payload; self.epoch = epoch
        self.done = threading.Event(); self.result = {'status': 'publication_pending'}
        self.deadline = time.monotonic() + OWNER_OP_SECONDS
        self.cancelled = threading.Event(); self.drained = threading.Event()
        self.slot_released = threading.Event()
        self._delivery_lock = threading.Lock(); self._wake = wake
        self._pin = None; self.state = 'accepted'
        self._abandon_reason = 'dispatch_abandoned'

    def wait(self, timeout=OWNER_OP_SECONDS):
        # Internal model-free/read callers only. Worker/upload never call wait.
        if type(timeout) not in (int, float) or not 0 <= timeout <= OWNER_OP_SECONDS:
            raise ValueError('Bounded wait required')
        if self.kind == 'dispatch':
            self.done.wait(min(timeout, max(0., self.deadline - time.monotonic())))
            if not self.done.is_set() or time.monotonic() >= self.deadline:
                self.abandon('dispatch_read_timeout')
            # Only claim may transfer a dispatch Pin. done alone is not an
            # ownership receipt, even when an error result is ready.
            if self.state == 'ready' and not self.cancelled.is_set():
                return {'status': 'ready'}
            return {'status': 'unavailable' if self.drained.is_set() else 'pending',
                    'reason': self._abandon_reason}
        self.done.wait(min(timeout, max(0., self.deadline - time.monotonic())))
        return self.result if self.done.is_set() else {'status': 'pending'}

    def check(self):
        if self.kind == 'dispatch' and (self.cancelled.is_set()
                or time.monotonic() >= self.deadline):
            raise Unavailable('dispatch_read_timeout' if not self.cancelled.is_set()
                              else 'dispatch_abandoned')

    def claim(self, *, admission_open):
        if self.kind != 'dispatch' or type(admission_open) is not bool:
            raise ValueError('Dispatch and an admission snapshot required')
        if not admission_open:
            self.abandon('dispatch_admission_closed')
        if not self._delivery_lock.acquire(timeout=LOCK_SECONDS):
            self.abandon('dispatch_delivery_busy')
            return {'status': 'pending', 'reason': 'dispatch_delivery_busy'}
        try:
            if self.state in ('claimed', 'drained'):
                return {'status': 'unavailable', 'reason': 'dispatch_already_resolved'}
            if self.cancelled.is_set() or time.monotonic() >= self.deadline:
                self.cancelled.set(); self.state = 'abandoned'
                return {'status': 'pending', 'reason': self._abandon_reason}
            if self.state != 'ready':return {'status': 'pending'}
            result = dict(self.result)
            if self._pin is not None:result['pin'] = self._pin
            # Construct the bounded delivery before changing its cleanup owner.
            if self.cancelled.is_set() or time.monotonic() >= self.deadline:
                self.cancelled.set(); self.state = 'abandoned'
                return {'status': 'pending', 'reason': self._abandon_reason}
            if self._pin is None:
                # A failed read transfers no lease. Positively resolve that
                # empty delivery instead of stranding a claimed operation.
                self.state = 'drained'; self.drained.set()
            else:
                self.state = 'claimed'; self._pin = None
            self.result = {'status': self.state}
            return result
        finally:
            self._delivery_lock.release()
            if self._wake is not None:self._wake.set()

    def abandon(self, reason='dispatch_abandoned'):
        if self.kind != 'dispatch':raise ValueError('Dispatch required')
        if (type(reason) is not str or not 1 <= len(reason) <= 80
                or any(c not in 'abcdefghijklmnopqrstuvwxyz_' for c in reason)):
            reason = 'dispatch_abandoned'
        # Cancellation is visible even if a claimant or owner temporarily owns
        # the lock. The lock still chooses the sole final delivery owner.
        self.cancelled.set()
        if self._wake is not None:self._wake.set()
        if not self._delivery_lock.acquire(timeout=LOCK_SECONDS):return False
        try:
            if self.state == 'claimed':return False
            if self.state != 'drained':
                self._abandon_reason = reason; self.state = 'abandoned'
                self.done.set()
            return self.drained.is_set()
        finally:self._delivery_lock.release()

    def _begin_dispatch(self):
        if not self._delivery_lock.acquire(timeout=LOCK_SECONDS):return False
        try:
            if self.cancelled.is_set() or time.monotonic() >= self.deadline:
                self.cancelled.set(); self.state = 'abandoned'; return False
            if self.state != 'accepted':return False
            self.state = 'running'; return True
        finally:self._delivery_lock.release()

    def _advance_delivery(self):
        """Owner-only polling; no store or delivery lock spans Pin.release."""
        if not self._delivery_lock.acquire(timeout=LOCK_SECONDS):return False
        try:
            if self.state == 'claimed':return True
            if self.state == 'drained':return True
            if self.cancelled.is_set() or time.monotonic() >= self.deadline:
                self.cancelled.set(); self.state = 'abandoned'
            if self.state != 'abandoned':
                self.state = 'ready'; self.done.set(); return False
            pin = self._pin
        finally:self._delivery_lock.release()
        if pin is not None:
            pin.release()
            if not pin.release_confirmed:return False
        if not self._delivery_lock.acquire(timeout=LOCK_SECONDS):return False
        try:
            # An abandoned delivery can never subsequently become claimed.
            self._pin = None; self.state = 'drained'
            self.result = {'status': 'unavailable', 'reason': self._abandon_reason}
            self.drained.set(); self.done.set(); return True
        finally:self._delivery_lock.release()


class Pin:
    """Copied material, no SQLite transaction or owner lock spans this lease."""
    def __init__(self, owner, cell_id, document, pcm):
        self.owner = owner; self.cell_id = cell_id; self.document = document
        self._pcm = pcm; self._released = False; self.cancelled = threading.Event()
        self._release_lock = threading.Lock(); self._release_requested = False
        self.release_done = threading.Event()

    @property
    def release_confirmed(self):
        return self.release_done.is_set()

    def read(self):
        if self._released or self.cancelled.is_set():raise Unavailable('pin_cancelled')
        budget = Budget.for_owner()
        with budget.phase():
            if not self.owner.authority.cell_valid(self.document, budget, publishing=False):
                self.cancelled.set(); raise Unavailable('source_authority_unavailable')
            data = self._pcm
            if self._released or self.cancelled.is_set():raise Unavailable('pin_cancelled')
            return data

    def release(self):
        self.cancelled.set(); self._release_requested = True
        if self.release_confirmed:return False
        return self.owner._release_pin(self)

    def __enter__(self):return self
    def __exit__(self, *args):self.release()


class CellStore:
    """No growing thread set, active+pending queue, or retained omitted-row log."""
    def __init__(self, directory, authority, *, enabled, qualification=None, instance_lock=None,
                 seam_enabled=False, core_seam_enabled=False, compact_gc_enabled=False, receipt_gc_enabled=False):
        settings=getattr(getattr(authority,'store',None),'settings',None)
        self.bounded_core_enabled=bool(getattr(settings,'native_owned_bounded_core_support_enabled',False))
        if self.bounded_core_enabled:
            if not enabled or not core_seam_enabled:raise Unavailable('bounded_core_configuration')
            directory=Path(directory).with_name(Path(directory).name+'-bounded-v1')
        self.retention_store_key=None
        self.directory = Path(directory)
        self.path = self.directory / 'cells.sqlite3'
        self.authority = authority; self.enabled = enabled; self.qualification = qualification
        if type(seam_enabled) is not bool:raise Unavailable('invalid_seam_flag')
        self.seam_enabled = seam_enabled
        self._store_policy = STORE_POLICY_SHA256 if seam_enabled else POLICY_SHA256
        if type(core_seam_enabled) is not bool:raise Unavailable('invalid_core_seam_flag')
        self.core_seam_enabled = core_seam_enabled
        if type(compact_gc_enabled) is not bool or compact_gc_enabled and not (enabled and core_seam_enabled):
            raise Unavailable('invalid_compact_gc_flag')
        self.compact_gc_enabled = compact_gc_enabled
        self._retention_capture = None; self._compact_gc_cursor = ''
        self._compact_gc_next = 0.; self._compact_gc_unknown = None
        self._compact_gc_attempts = 0; self._compact_gc_retired = 0
        self._compact_gc_last = {'status': 'not_observed' if compact_gc_enabled else 'disabled',
            'reason': 'compact_gc_not_observed' if compact_gc_enabled else 'compact_gc_disabled',
            'scanned': 0, 'retired': 0, 'protected': 0, 'body_bytes': 0}
        if type(receipt_gc_enabled) is not bool or receipt_gc_enabled and not compact_gc_enabled:
            raise Unavailable('invalid_receipt_gc_flag')
        self.receipt_gc_enabled = receipt_gc_enabled
        self._receipt_gc_cursor = ''; self._receipt_gc_next = 0.; self._receipt_gc_unknown = None
        self._receipt_gc_prefixes = {}; self._receipt_gc_prefix_generation = None
        self._receipt_gc_attempts = 0; self._receipt_gc_retired = 0
        self._receipt_gc_last = {'status': 'not_observed' if receipt_gc_enabled else 'disabled',
            'reason': 'receipt_gc_not_observed' if receipt_gc_enabled else 'receipt_gc_disabled',
            'scanned': 0, 'retired': 0, 'protected': 0, 'body_bytes': 0, 'prefix_rows': 0}
        if core_seam_enabled:
            from .native_owned_core_seams import store_policy
            self._store_policy = (store_policy(bounded=True) if self.bounded_core_enabled else store_policy())
        self.instance_lock = instance_lock  # Retained while owner cleanup is unresolved.
        self._publication_guard = threading.RLock()
        self._publication_revoke_pending = False
        self._lock = threading.Lock(); self._wake = threading.Event(); self._stop = threading.Event()
        self._reconcile = threading.Event(); self._ready = threading.Event()
        self._slot = None; self._epoch = object(); self._pins = {}; self._receipts = {}
        self._thread = None; self._db = None; self._namespace_identity = None; self._db_identity = None
        self._ancestor_identity = None
        self.configuration_reason = None
        self._closed = False; self._cleanup_pending = False; self._status = 'initializing'
        self._admission_closed = False
        self._accepted = 0; self._dropped = 0; self._durable = 0; self._charge = 0
        self._sweep_cell = 0; self._sweep_source = ''; self._sweep_generation = None
        from .native_owned_core_seams import MAX_COMPACTS, MAX_PENDING, PENDING_BYTES
        from .native_owned_retention import row_counts
        self._retention_change = object(); self._retention_failure = None
        self._retention_next_refresh = 0.
        self._retention_snapshot = (None, None, {
            'status': 'optional_unavailable', 'reason': 'retention_not_observed',
            'observed_at_monotonic': None, 'occupancy_complete': False,
            'roots_complete': False, 'producer_frontier_known': False,
            'consistency': 'separate_database_observations', 'reclamation_enabled': False,
            'cells': row_counts('cells', None, MAX_CELLS),
            'sources': row_counts('sources', None, MAX_RECEIPTS),
            'compacts': row_counts('compacts', None, MAX_COMPACTS),
            'metadata_only_cells': None,
            'pending_proof_limit': MAX_PENDING, 'pending_proof_bytes_limit': MAX_PENDING * PENDING_BYTES,
            'pending_proof_bytes': None,
            'storage': {'page_count': None, 'freelist_pages': None, 'reusable_bytes': None,
                        'last_observed_allocated_bytes': None},
            'roots': {'unreleased_outbox': None, 'producer_replay': None,
                      'retained_moss_results': None, 'moss_results_covered_by_retained_cells': True}})

    def start(self):
        if self._thread is not None:return self
        if self.configuration_reason:
            self._status = self.configuration_reason; self._cleanup_pending = os.path.lexists(self.directory); return self
        if not self.enabled and not os.path.lexists(self.directory):
            self._status = 'disabled_absent'; return self
        if os.path.lexists(self.directory) and not stat.S_ISDIR(self.directory.lstat().st_mode):
            self._status = 'unsafe_cell_namespace'; self._cleanup_pending = True; return self
        if self.qualification is None:
            self._status = 'runtime_unqualified'
            self._cleanup_pending = os.path.lexists(self.directory)
            return self
        self._thread = threading.Thread(target=self._run, name='native-owned-cell-owner', daemon=True)
        self._thread.start()
        return self

    def health(self):
        # Fixed-size in-memory projection; no path, SQL, filesystem or lease.
        return {'enabled': self.enabled, 'status': self._status,
            'ready': self._ready.is_set() and not self._closed and not self._admission_closed and not self._publication_revoke_pending,
            'publication_revocation_pending':self._publication_revoke_pending,
            'handoffs_active_or_pending': int(self._slot is not None),
            'pins': len(self._pins), 'cleanup_pending': self._cleanup_pending or self._publication_revoke_pending,
            'owner_alive': bool(self._thread and self._thread.is_alive()),
            'accepted': self._accepted, 'dropped': self._dropped, 'durable': self._durable,
            'last_observed_allocated_bytes': self._charge, 'total_bytes_limit': TOTAL_BYTES,
            'working_reservation_bytes': WORKING_RESERVATION_BYTES,
            'working_memory_measured': False, 'quota_qualification': 'pinned-runtime-only' if self.qualification else 'unqualified',
            'retention': self._retention_health(), 'compact_gc': self._compact_gc_health(),
            'receipt_gc': self._receipt_gc_health()}

    def _compact_gc_health(self):
        from .native_owned_core_seams import COMPACT_GC_BATCH, COMPACT_GC_BODY_BYTES
        last = self._compact_gc_last
        return {'enabled': self.compact_gc_enabled, 'status': last['status'], 'reason': last['reason'],
            'attempts': self._compact_gc_attempts, 'retired': self._compact_gc_retired,
            'last_scanned': last['scanned'], 'last_retired': last['retired'],
            'last_protected': last['protected'], 'last_body_bytes': last['body_bytes'],
            'uncertain_ids': len(self._compact_gc_unknown or ()), 'max_batch': COMPACT_GC_BATCH,
            'body_projection_limit': COMPACT_GC_BODY_BYTES, 'scope': 'completed_stream_sources_only',
            'source_receipt_limit': MAX_RECEIPTS, 'cell_limit': MAX_CELLS}

    def _schedule_compact_gc(self):
        """One internal operation in the existing slot, never a second queue."""
        if not self.compact_gc_enabled or time.monotonic() < self._compact_gc_next:return False
        occupied = self._retention_snapshot[2]['compacts']['occupied']
        if self._compact_gc_unknown is None and (occupied is None or occupied < 2):return False
        if not self._lock.acquire(blocking=False):return False
        try:
            if self._slot is not None or self._closed or self._admission_closed or self._reconcile.is_set():return False
            self._slot = Operation('compact_gc', (), self._epoch, self._wake)
            self._wake.set(); return True
        finally:self._lock.release()

    def _collect_compacts(self, op, budget):
        """Owner-only wrapper; maintenance failures never poison normal status."""
        from .native_owned_core_seams import collect_compacts
        self._compact_gc_attempts = min(self._compact_gc_attempts + 1, (1 << 31) - 1)
        result = {'status': 'deferred', 'reason': 'compact_gc_unavailable',
                  'scanned': 0, 'retired': 0, 'protected': 0, 'body_bytes': 0}
        try:
            with budget.phase():
                if not self.compact_gc_enabled:raise Unavailable('compact_gc_disabled')
                if self._slot is not op or op.kind != 'compact_gc':raise Unavailable('compact_gc_slot_unavailable')
                self._generation(op)
                if self._admission_closed or not self._ready.is_set() or op.cancelled.is_set():
                    raise Unavailable('compact_gc_admission_closed')
                self._db.set_progress_handler(budget.progress, 100); self._scan(budget)
                result = collect_compacts(self, op, budget, result=result)
                self._scan(budget); budget.check(); self._generation(op)
                if self._admission_closed or op.cancelled.is_set():raise Unavailable('compact_gc_admission_closed')
        except Exception as exc:
            reason = str(exc) if isinstance(exc, Unavailable) else ''
            allowed = ('compact_gc_disabled', 'compact_gc_slot_unavailable', 'compact_gc_admission_closed',
                'compact_gc_binding_unavailable', 'compact_gc_producer_active', 'compact_gc_pending_proof',
                'compact_gc_producer_roots_pending', 'compact_gc_generation_changed',
                'compact_gc_primary_changed', 'compact_gc_roots_changed', 'compact_gc_unknown_changed',
                'cooperative_deadline', 'operation_work_capacity')
            result.update(status='unknown' if self._compact_gc_unknown is not None else 'deferred',
                          reason=reason if reason in allowed else 'compact_gc_unavailable', retired=0)
        finally:
            try:self._db.set_progress_handler(None, 0)
            except Exception:result.update(status='unknown' if self._compact_gc_unknown is not None else 'deferred',
                                           reason='compact_gc_unavailable', retired=0)
            self._compact_gc_next = time.monotonic() + RETENTION_REFRESH_SECONDS
        if result['status'] in ('retired', 'reconciled', 'no_eligible_compacts'):
            if result['status'] == 'retired':
                self._compact_gc_retired = min(self._compact_gc_retired + result['retired'], (1 << 31) - 1)
            self._compact_gc_unknown = None
        self._compact_gc_last = dict(result)
        return result

    def _receipt_gc_health(self):
        from .recording_overlap import RECEIPT_GC_PREFIX_ROWS, RECEIPT_GC_PREFIX_STREAMS
        last = self._receipt_gc_last
        return {'enabled': self.receipt_gc_enabled, 'status': last['status'], 'reason': last['reason'],
            'attempts': self._receipt_gc_attempts, 'retired': self._receipt_gc_retired,
            'last_scanned': last['scanned'], 'last_retired': last['retired'],
            'last_protected': last['protected'], 'last_body_bytes': last['body_bytes'],
            'last_prefix_rows': last['prefix_rows'], 'prefix_row_limit': RECEIPT_GC_PREFIX_ROWS,
            'prefix_stream_limit': RECEIPT_GC_PREFIX_STREAMS,
            'uncertain_ids': len(self._receipt_gc_unknown or ()), 'max_batch': 8,
            'scope': 'orphan_completed_sealed_page_receipts_only',
            'source_receipt_limit': MAX_RECEIPTS, 'cell_limit': MAX_CELLS}

    def _schedule_receipt_gc(self):
        # Called after existing compact GC, in its same single owner slot.
        if not self.receipt_gc_enabled or time.monotonic() < self._receipt_gc_next:return False
        occupied = self._retention_snapshot[2]['sources']['occupied']
        if self._receipt_gc_unknown is None and (occupied is None or occupied < 1):return False
        if not self._lock.acquire(blocking=False):return False
        try:
            if self._slot is not None or self._closed or self._admission_closed or self._reconcile.is_set():return False
            self._slot = Operation('receipt_gc', (), self._epoch, self._wake)
            self._wake.set(); return True
        finally:self._lock.release()

    def _collect_receipts(self, op, budget):
        """Owner-only bounded maintenance; uncertain cache entries stay hidden."""
        from .native_owned_core_seams import collect_receipts
        self._receipt_gc_attempts = min(self._receipt_gc_attempts + 1, (1 << 31) - 1)
        result = {'status': 'deferred', 'reason': 'receipt_gc_unavailable',
            'scanned': 0, 'retired': 0, 'protected': 0, 'body_bytes': 0, 'prefix_rows': 0}
        try:
            with budget.phase():
                if not self.receipt_gc_enabled or not self.compact_gc_enabled:
                    raise Unavailable('receipt_gc_disabled')
                if self._slot is not op or op.kind != 'receipt_gc':raise Unavailable('receipt_gc_slot_unavailable')
                self._generation(op)
                if self._admission_closed or not self._ready.is_set() or op.cancelled.is_set():
                    raise Unavailable('receipt_gc_admission_closed')
                self._db.set_progress_handler(budget.progress, 100); self._scan(budget)
                result = collect_receipts(self, op, budget, result=result)
                self._scan(budget); budget.check(); self._generation(op)
                if self._admission_closed or op.cancelled.is_set():raise Unavailable('receipt_gc_admission_closed')
        except Exception as exc:
            reason = str(exc) if isinstance(exc, Unavailable) else ''
            allowed = ('receipt_gc_disabled', 'receipt_gc_slot_unavailable', 'receipt_gc_admission_closed',
                'receipt_gc_binding_unavailable', 'receipt_gc_context_unknown', 'receipt_gc_prefix_changed',
                'receipt_gc_roots_changed', 'receipt_gc_unknown_changed', 'receipt_gc_compact_pending',
                'compact_gc_binding_unavailable', 'compact_gc_producer_active', 'compact_gc_pending_proof',
                'compact_gc_producer_roots_pending', 'compact_gc_generation_changed',
                'compact_gc_primary_changed', 'compact_gc_admission_closed',
                'publication_generation_revoked', 'cooperative_deadline', 'operation_work_capacity')
            result.update(status='unknown' if self._receipt_gc_unknown is not None else 'deferred',
                reason=reason if reason in allowed else 'receipt_gc_unavailable', retired=0)
            self._receipt_gc_prefixes.clear()
        finally:
            try:self._db.set_progress_handler(None, 0)
            except Exception:result.update(status='unknown' if self._receipt_gc_unknown is not None else 'deferred',
                reason='receipt_gc_unavailable', retired=0)
            self._receipt_gc_next = time.monotonic() + RETENTION_REFRESH_SECONDS
            # Leave the existing retry-floor gap after the pair, even when
            # a slow compact/source pass consumed its entire owner budget.
            self._compact_gc_next = max(self._compact_gc_next, self._receipt_gc_next)
        if result['status'] == 'retired':
            for cid, _ in self._receipt_gc_unknown:self._receipts.pop(cid, None)
            self._receipt_gc_retired = min(self._receipt_gc_retired + result['retired'], (1 << 31) - 1)
            self._receipt_gc_unknown = None
        elif result['status'] == 'reconciled':self._receipt_gc_unknown = None
        self._receipt_gc_last = dict(result)
        return result

    def retention_changed(self):
        """Invalidate sampled counts without doing work on a producer thread."""
        self._retention_change = object(); self._wake.set()

    def _retention_health(self):
        generation, change, cached = self._retention_snapshot
        result = {key: dict(value) if type(value) is dict else value for key, value in cached.items()}
        observed = result['observed_at_monotonic']
        age = None if observed is None else max(0., time.monotonic() - observed)
        state = ('unobserved' if observed is None else
                 'generation_changed' if generation is not self._epoch else
                 'dirty' if change is not self._retention_change else
                 'expired' if age >= RETENTION_REFRESH_SECONDS else 'last_observed')
        if self._retention_failure is not None:state = 'unavailable'
        result.update(snapshot_state=state, sample_age_seconds=age,
                      counts_are_last_observed=True, last_observed_status=result['status'])
        if state != 'last_observed':
            result.update(status='optional_unavailable', occupancy_complete=False,
                          reason=self._retention_failure or 'retention_snapshot_' + state)
        result['roots'].update(pins=len(self._pins), in_flight_operations=int(self._slot is not None))
        return result

    def _refresh_retention(self, budget):
        """Owner-only bounded aggregate sample. Never a live retirement permit.

        Each database is observed separately. Direct source dependencies and
        pending compact holders are lower bounds: the left proof side, retained
        results, outboxes and producer tails/replay may add roots. All remaining
        rows stay unknown/protected until a future complete root/frontier API.
        """
        from .native_owned_core_seams import MAX_COMPACTS
        from .native_owned_retention import row_counts
        generation, change = self._epoch, self._retention_change
        try:
            with budget.phase():
                if self._slot is not None:raise Unavailable('retention_refresh_deferred')
                self._db.set_progress_handler(budget.progress, 100)
                cells = self._db.execute('SELECT COUNT(*),COALESCE(SUM(pcm IS NULL),0) FROM cells').fetchone()
                budget.projection(64); budget.check()
                if self._slot is not None:raise Unavailable('retention_refresh_deferred')
                sources = self._db.execute('''SELECT COUNT(*),COALESCE(SUM(EXISTS(
                    SELECT 1 FROM dependencies d WHERE d.cid=sources.cid)),0) FROM sources''').fetchone()
                budget.projection(64); budget.check()
                pages = self._db.execute('PRAGMA page_count').fetchone()[0]
                free = self._db.execute('PRAGMA freelist_count').fetchone()[0]
                budget.projection(64); budget.check()
                if self._slot is not None:raise Unavailable('retention_refresh_deferred')
                provider = getattr(self.authority, 'retention_counts', None)
                primary = provider(budget) if provider is not None else None
                result = {key: dict(value) if type(value) is dict else value
                          for key, value in self._retention_snapshot[2].items()}
                result.update(cells=row_counts('cells', cells[0], MAX_CELLS, cells[0], 0),
                              sources=row_counts('sources', sources[0], MAX_RECEIPTS, sources[1], 0,bounded_protection=self.bounded_core_enabled),
                              compacts=row_counts('compacts', None, MAX_COMPACTS),
                              metadata_only_cells=cells[1], pending_proof_bytes=None,
                              occupancy_complete=primary is not None)
                if primary is not None:
                    result['compacts'] = row_counts('compacts', primary['occupied'], MAX_COMPACTS,
                                                   primary['pending'], primary['pending'],bounded_protection=self.bounded_core_enabled)
                    result['pending_proof_bytes'] = primary.get('pending_bytes')
                result['storage'] = {'page_count': pages, 'freelist_pages': free,
                    'reusable_bytes': free * PAGE_SIZE, 'last_observed_allocated_bytes': self._charge}
                result.update(status='observed', reason='retention_root_closure_unknown')
                for kind in ('cells', 'sources', 'compacts'):
                    counts = result[kind]
                    if counts['occupied'] == counts['limit']:
                        if counts['referenced'] == counts['occupied']:
                            result.update(status='blocked_referenced_capacity', reason=kind + '_referenced_capacity')
                            break
                        result.update(status='optional_unavailable', reason=kind + '_unknown_protected_capacity')
                budget.check()
                if generation is not self._epoch:raise Unavailable('retention_generation_changed')
                if change is not self._retention_change or self._slot is not None:
                    raise Unavailable('retention_refresh_deferred')
                result['observed_at_monotonic'] = time.monotonic()
                self._retention_snapshot = (generation, change, result)
                self._retention_failure = None
            return True
        except Exception as exc:
            reason = str(exc) if isinstance(exc, Unavailable) else ''
            allowed = ('retention_refresh_deferred', 'retention_generation_changed',
                       'cooperative_deadline', 'operation_work_capacity',
                       'retention_compact_capacity', 'retention_pending_capacity', 'retention_count_capacity')
            self._retention_failure = reason if reason in allowed else 'retention_refresh_unavailable'
            return False
        finally:
            try:self._db.set_progress_handler(None, 0)
            except Exception:self._retention_failure = 'retention_refresh_unavailable'
            # Completion-based cadence also bounds repeated failures/slow reads.
            self._retention_next_refresh = time.monotonic() + RETENTION_REFRESH_SECONDS

    def receipt(self, cid):
        if not self._ready.is_set() or self._closed or self._publication_revoke_pending:return None
        # A late optional DELETE commit cannot leave ambiguous cache authority.
        if any(item[0] == cid for item in (self._receipt_gc_unknown or ())):return None
        # Receipt objects are immutable and only installed after durable commit.
        return self._receipts.get(cid)

    def submit(self, kind, payload):
        if not self._ready.is_set() or self._closed or self._admission_closed or self._publication_revoke_pending or not self.enabled:
            return {'status': 'optional_unavailable', 'reason': self._status}
        if kind == 'receipt':
            if type(payload) is not Receipt:raise Unavailable('receipt_required')
        elif kind == 'publish_auto':
            if type(payload) is not tuple or len(payload)!=2 or type(payload[1]) is not Cell:
                raise Unavailable('automatic_publication_binding')
            digest(payload[0]); cell=payload[1]
            if len(cell.proof)+len(cell.pcm)>HANDOFF_BYTES:raise Unavailable('publication_material_capacity')
            reason=self._publication_policy((cell,))
            if reason:return {'status':'optional_unavailable','reason':reason}
        elif kind == 'publish':
            if type(payload) is not tuple or not 1 <= len(payload) <= BATCH_CELLS or any(type(c) is not Cell for c in payload):
                raise Unavailable('publication_batch_capacity')
            if sum(len(c.proof) + len(c.pcm) for c in payload) > HANDOFF_BYTES:
                raise Unavailable('publication_material_capacity')
            reason = self._publication_policy(payload)
            if reason:return {'status': 'optional_unavailable', 'reason': reason}
        elif kind == 'jobs_list':
            if type(payload) is not tuple or len(payload) != 3:raise Unavailable('cell_query_binding')
            text(payload[0]); integer(payload[1], (1 << 63) - 1); integer(payload[2], PAGE_ROWS, 1)
        elif kind == 'jobs_detail':
            if type(payload) is not tuple or len(payload) != 2:raise Unavailable('cell_query_binding')
            text(payload[0]); digest(payload[1])
        elif kind in ('dispatch', 'validate_dispatch'):
            if type(payload) is not tuple or len(payload) != 7:raise Unavailable('cell_query_binding')
            sid, cell_id, sequence, revision, proof_hash, support_id, generation = payload
            text(sid); digest(cell_id)
            integer(sequence, (1 << 63) - 1, 1); integer(revision, (1 << 63) - 1, 1)
            digest(proof_hash); digest(support_id)
            if generation is not self._epoch:return {'status': 'unavailable', 'reason': 'cell_revoked'}
        elif kind not in ('list', 'detail', 'read', 'expire', 'delete'):
            raise Unavailable('unknown_optional_operation')
        if not self._lock.acquire(timeout=LOCK_SECONDS):return {'status': 'busy', 'reason': 'owner_lock_busy'}
        try:
            if self._closed or self._admission_closed or self._slot is not None:
                self._dropped = min(self._dropped + 1, (1 << 31) - 1)
                return {'status': 'busy', 'reason': 'one_total_handoff_slot'}
            op = Operation(kind, payload, self._epoch, self._wake); self._slot = op
            if kind in ('receipt', 'publish', 'publish_auto', 'expire', 'delete'):self.retention_changed()
            self._accepted = min(self._accepted + 1, (1 << 31) - 1)
            self._wake.set(); return op
        finally:self._lock.release()

    def _publication_policy(self, cells):
        # Profile admission happens before handoff and is rechecked by the sole
        # owner before SQL. _insert_cell/transactions/caps stay unchanged; the
        # opt-in post-publication revocation classification is a reviewed delta.
        for cell in cells:
            doc, _, _ = validate_document(cell.proof)
            from .native_owned_core_seams import is_core, store_policy, BOUNDED_SCHEMA
            if is_core(doc):
                if (not self.core_seam_enabled or self._store_policy != (store_policy(bounded=True) if self.bounded_core_enabled else store_policy())
                        or doc['schema']==BOUNDED_SCHEMA and not self.bounded_core_enabled):
                    return 'core_seams_disabled_or_policy'
            elif is_composite(doc):
                if self._store_policy not in (STORE_POLICY_SHA256, (store_policy(bounded=True) if self.bounded_core_enabled else store_policy())):
                    return 'incompatible_legacy_cell_policy'
                if not self.seam_enabled:return 'seam_composites_disabled'
        return None

    def _advance_publication_revoke(self):
        if not self._publication_revoke_pending:return True
        if not self._publication_guard.acquire(timeout=LOCK_SECONDS):return False
        try:
            if self._publication_revoke_pending:
                self._epoch=object(); self._publication_revoke_pending=False
                self._receipt_gc_prefixes.clear(); self._cleanup_pending=True; self._reconcile.set()
            return True
        finally:self._publication_guard.release()

    def revoked(self):
        # A contended result transaction linearized first. Request closure now,
        # but never claim a completed generation transition on a lock timeout.
        self._publication_revoke_pending=True;self._cleanup_pending=True
        for pin in tuple(self._pins.values()):pin.cancelled.set()
        self._advance_publication_revoke()
        op=self._slot
        if op is not None and op.kind=='dispatch':op.abandon('cell_revoked')
        self._wake.set()
        return True  # Existing notification contract: cleanup remains pending.

    def close_admission(self):
        self._admission_closed=True
        self.revoked()
        return not self._publication_revoke_pending

    def close(self, timeout=.5):
        if type(timeout) not in (int, float) or not 0 <= timeout <= .5:
            raise ValueError('Bounded shutdown wait required')
        deadline = time.monotonic() + timeout
        self.close_admission(); self._closed = True; self._ready.clear()
        self._stop.set(); self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(max(0., deadline - time.monotonic()))
        unresolved = (bool(self._thread and self._thread.is_alive()) or self._db is not None
                      or self._slot is not None or bool(self._pins) or self._publication_revoke_pending)
        if unresolved:
            self._cleanup_pending = True; self._status = 'shutdown_pending'
            return False
        self._cleanup_pending = False; self._status = 'closed'
        self.instance_lock = None
        return True

    def _release_pin(self, pin):
        # Lock order is pin -> store, never delivery -> pin/store. Callers drop
        # every owner/delivery lock before release; no application callbacks.
        if not pin._release_lock.acquire(timeout=LOCK_SECONDS):
            self._wake.set(); return False
        try:
            if pin.release_confirmed:return False
            if not self._lock.acquire(timeout=LOCK_SECONDS):return False
            try:
                if self._pins.get(id(pin)) is not pin:return False
                self._pins.pop(id(pin)); pin._pcm = b''; pin._released = True
                # Only this exact removal, after copied PCM is dropped, may
                # acknowledge the reservation. An absent/replacement pin cannot.
                pin.release_done.set(); return True
            finally:self._lock.release()
        finally:
            pin._release_lock.release(); self._wake.set()

    def _drain_released_pins(self):
        for pin in tuple(self._pins.values()):
            if pin._release_requested:pin.release()

    def _check_parents(self, budget=None):
        """Finite private-namespace fence, not an atomic SQLite path open."""
        budget = budget or Budget(); directory = self.directory.parent
        if (not directory.is_absolute() or '..' in directory.parts or len(directory.parts) > 33
                or len(os.fsencode(directory)) > 4096):
            raise Unavailable('cell_ancestor_path_capacity')
        if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_NONBLOCK', 'O_DIRECTORY')):
            raise Unavailable('cell_ancestor_primitives_unavailable')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
        with budget.phase():
            fd = os.open('/', flags); identities = []
            try:
                def accepted(info):
                    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
                            or info.st_mode & 0o022):
                        raise Unavailable('unsafe_cell_ancestor')
                    return info.st_dev, info.st_ino
                identities.append(accepted(os.fstat(fd)))
                for name in directory.parts[1:]:
                    budget.check(); child = os.open(name, flags, dir_fd=fd)
                    try:
                        info = os.fstat(child); entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        identity = accepted(info)
                        if identity != accepted(entry):raise Unavailable('cell_ancestor_replaced')
                    except BaseException:
                        os.close(child); raise
                    os.close(fd); fd = child; identities.append(identity)
                if os.fstat(fd).st_uid != os.getuid():raise Unavailable('cell_data_directory_not_owned')
                identities = tuple(identities)
                if self._ancestor_identity is not None and identities != self._ancestor_identity:
                    raise Unavailable('cell_ancestor_replaced')
                self._ancestor_identity = identities; budget.check()
            finally:os.close(fd)

    def _scan(self, budget=None):
        self._check_parents(budget)
        root = self.directory.lstat()
        if (not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode) or root.st_uid != os.getuid()
                or root.st_mode & 0o077):
            raise Unavailable('unsafe_cell_namespace')
        identity = (root.st_dev, root.st_ino)
        if self._namespace_identity is not None and identity != self._namespace_identity:
            raise Unavailable('cell_namespace_replaced')
        self._namespace_identity = identity
        if not hasattr(root, 'st_blocks'):raise Unavailable('unqualified_directory_allocation')
        directory_charge = max(root.st_size, root.st_blocks * 512)
        total = directory_charge; charges = {}; identities = {}; sizes = {}
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if entry.name not in ALLOWED_FILES:raise Unavailable('unexpected_namespace_entry')
                st = entry.stat(follow_symlinks=False)
                if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid() or st.st_dev != root.st_dev
                        or not hasattr(st, 'st_blocks')):
                    raise Unavailable('unsafe_namespace_file')
                charged = max(st.st_size, st.st_blocks * 512)
                charges[entry.name] = charged; sizes[entry.name] = st.st_size; total += charged
                identities[entry.name] = (st.st_dev, st.st_ino)
        self._charge = total
        if any(name in charges for name in ('cells.sqlite3-wal', 'cells.sqlite3-shm')):
            raise Unavailable('unexpected_wal_shm_counted')
        slack = total - sum(sizes.values())
        allowed_slack = self.qualification.allocation_slack_bytes_max if self.qualification else 0
        if (total > TOTAL_BYTES or total > RESERVED_ENVELOPE_BYTES
                or sizes.get('cells.sqlite3', 0) > DATABASE_BYTES
                or sizes.get('cells.sqlite3-journal', 0) > JOURNAL_BYTES
                or slack > allowed_slack):
            raise Unavailable('allocated_namespace_capacity')
        if self._db_identity is not None and identities.get('cells.sqlite3') != self._db_identity:
            raise Unavailable('cell_database_replaced')
        return identities

    def _open(self):
        budget = Budget.for_owner()
        self._check_parents(budget)
        # Verify build and actual loaded binaries before any on-disk connection
        # could inspect or recover an existing rollback journal.
        with budget.phase():
            qualification_directory = self.directory if os.path.lexists(self.directory) else self.directory.parent
            probe = sqlite3.connect(':memory:', timeout=0., isolation_level=None)
            try:
                probe.set_progress_handler(budget.progress, 100)
                self.qualification.verify(probe, qualification_directory, budget)
            finally:probe.close()
        with budget.phase():
            if not os.path.lexists(self.directory):
                if not self.enabled:raise Unavailable('disabled_absent')
                self.directory.mkdir(mode=0o700)
            identities = self._scan(budget)  # Charge hot-journal evidence before opening.
            existing = 'cells.sqlite3' in identities
            if not existing and not self.enabled:raise Unavailable('missing_optional_database')
            uri = self.path.as_uri() + ('?mode=rw&vfs=unix' if existing else '?mode=rwc&vfs=unix')
            db = sqlite3.connect(uri, uri=True, timeout=0., isolation_level=None)
            self._db = db; db.row_factory = sqlite3.Row
            # Detect observed drift immediately. This cannot prevent a hostile
            # same-UID race during Python SQLite's absolute-path open/recovery.
            self._scan(budget)
            db.set_progress_handler(budget.progress, 100)
            if db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'delete':
                raise Unavailable('incompatible_existing_journal_mode')
            if not existing:db.execute('PRAGMA page_size=4096')
            for pragma in ('PRAGMA synchronous=FULL', 'PRAGMA temp_store=MEMORY', 'PRAGMA busy_timeout=0',
                    'PRAGMA cache_spill=OFF', 'PRAGMA cache_size=-4096', 'PRAGMA mmap_size=0',
                    'PRAGMA foreign_keys=ON', 'PRAGMA trusted_schema=OFF', 'PRAGMA max_page_count=6144'):
                db.execute(pragma)
            expected = {'page_size': PAGE_SIZE, 'max_page_count': MAX_PAGE_COUNT, 'synchronous': 2,
                        'temp_store': 2, 'busy_timeout': 0, 'cache_spill': 0, 'mmap_size': 0,
                        'foreign_keys': 1, 'trusted_schema': 0, 'auto_vacuum': 0}
            for name, value in expected.items():
                if db.execute('PRAGMA ' + name).fetchone()[0] != value:
                    raise Unavailable('pragma_readback_mismatch')
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, HANDOFF_BYTES)
            db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, SQL_PROJECTION_BYTES)
            db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            binding = (SCHEMA, self._store_policy, self.authority.identity,
                       hashlib.sha256(canonical(asdict(self.qualification))).hexdigest(),
                       canonical({'namespace': list(self._namespace_identity),
                                  'ancestors': [list(item) for item in self._ancestor_identity]}).decode('ascii'),
                       canonical(list(self._scan(budget)['cells.sqlite3'])).decode('ascii'))
            if existing:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 6")}
                if tables != {'store_meta', 'sources', 'cells', 'dependencies'}:
                    raise Unavailable('optional_schema_mismatch')
                row = db.execute('SELECT schema,policy,primary_identity,qualification,namespace_identity,database_identity FROM store_meta WHERE id=1').fetchone()
                from .native_owned_core_seams import store_policy
                accepted_store_policies = (((store_policy(bounded=True) if self.bounded_core_enabled else store_policy()),) if self.core_seam_enabled else (POLICY_SHA256, STORE_POLICY_SHA256))
                if (row is None or row['policy'] not in accepted_store_policies
                        or tuple(row)[:1] + tuple(row)[2:] != binding[:1] + binding[2:]):
                    raise Unavailable('optional_primary_binding_mismatch')
                # Existing known profiles keep their durable policy. No upgrade,
                # metadata rewrite or migration follows a production-flag change.
                self._store_policy = row['policy']
            else:
                self._schema(db)
                db.execute('INSERT INTO store_meta(id,schema,policy,primary_identity,qualification,namespace_identity,database_identity) VALUES(1,?,?,?,?,?,?)',
                           binding)
            verified=db.execute('SELECT schema,policy,primary_identity,qualification,namespace_identity,database_identity FROM store_meta WHERE id=1').fetchone()
            self.retention_store_key=hashlib.sha256(canonical(list(verified))).hexdigest()
            if db.execute('SELECT COUNT(*) FROM cells').fetchone()[0] > MAX_CELLS or db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] > MAX_RECEIPTS:
                raise Unavailable('existing_row_capacity')
            self._db_identity = self._scan(budget)['cells.sqlite3']
        db.set_progress_handler(None, 0)
        # Paged startup; each batch has its own finite owner operation budget.
        after = ''
        while True:
            if self._stop.is_set():raise Unavailable('shutdown_pending')
            budget = Budget.for_owner()
            with budget.phase():
                self._scan(budget); db.set_progress_handler(budget.progress, 100)
                rows = db.execute('SELECT cid,length(receipt) AS n FROM sources WHERE cid>? ORDER BY cid LIMIT 20', (after,)).fetchall()
                if not rows:break
                for row in rows:
                    after = row['cid']
                    if not 1 <= row['n'] <= RECEIPT_BYTES:raise Unavailable('stored_receipt_length')
                    raw = db.execute('SELECT receipt FROM sources WHERE cid=?', (after,)).fetchone()[0]
                    budget.projection(len(raw)); receipt = Receipt.decode(raw)
                    if receipt.chunk_id != after:raise Unavailable('stored_receipt_identity')
                    self._receipts[after] = receipt
                self._scan(budget)
            db.set_progress_handler(None, 0)
        db.set_progress_handler(None, 0)
        self._reconcile.set(); self._cleanup_pending = True

    @staticmethod
    def _schema(db):
        statements = (
            'CREATE TABLE store_meta(id INTEGER PRIMARY KEY CHECK(id=1),schema TEXT NOT NULL,policy TEXT NOT NULL,primary_identity TEXT NOT NULL,qualification TEXT NOT NULL,namespace_identity TEXT NOT NULL,database_identity TEXT NOT NULL)',
            'CREATE TABLE sources(cid TEXT PRIMARY KEY,sid TEXT NOT NULL,digest TEXT NOT NULL,receipt BLOB NOT NULL CHECK(length(receipt)<=1024))',
            'CREATE INDEX sources_session ON sources(sid,cid)',
            '''CREATE TABLE cells(seq INTEGER PRIMARY KEY AUTOINCREMENT,cell_id TEXT NOT NULL UNIQUE,
                ownership TEXT NOT NULL UNIQUE,sid TEXT NOT NULL,cid TEXT NOT NULL,proof BLOB NOT NULL CHECK(length(proof)<=65536),
                pcm BLOB CHECK(length(pcm)<=640000),state TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1)''',
            'CREATE INDEX cells_session ON cells(sid,seq)',
            '''CREATE TABLE dependencies(cell_id TEXT NOT NULL REFERENCES cells(cell_id) ON DELETE CASCADE,
                position INTEGER NOT NULL,cid TEXT NOT NULL,digest TEXT NOT NULL,receipt_id TEXT NOT NULL,
                source_start INTEGER NOT NULL,source_end INTEGER NOT NULL,logical_start INTEGER NOT NULL,logical_end INTEGER NOT NULL,
                PRIMARY KEY(cell_id,position))''',
            'CREATE INDEX dependencies_source ON dependencies(cid,cell_id)')
        db.execute('BEGIN IMMEDIATE')
        try:
            for sql in statements:db.execute(sql)
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:db.execute('ROLLBACK')
            raise

    @contextmanager
    def _transaction(self, budget):
        self._scan(budget); budget.check()
        self._db.execute('BEGIN IMMEDIATE')
        try:
            yield
            budget.check(); self._scan(budget); self._db.execute('COMMIT')
            # A slow commit may already be durable. Late completion is unknown,
            # resolved by exact ID; never blindly retry or claim successful fsync.
            budget.check(); self._scan(budget)
        except BaseException:
            if self._db.in_transaction:
                self._db.set_progress_handler(None, 0)
                self._db.execute('ROLLBACK')
                self._db.set_progress_handler(budget.progress, 100)
            raise

    def _generation(self, op):
        if self._closed or self._publication_revoke_pending or op.epoch is not self._epoch:raise Unavailable('publication_generation_revoked')

    def _receipt(self, op, budget):
        receipt = op.payload; self._generation(op)
        if not self.authority.receipt_valid(receipt, budget):raise Unavailable('source_authority_unavailable')
        if any(item[0] == receipt.chunk_id for item in (self._receipt_gc_unknown or ())):
            raise Unavailable('receipt_gc_commit_unknown')
        old = self._receipts.get(receipt.chunk_id)
        if old:
            if old != receipt:raise Unavailable('conflicting_ingestion_receipt')
            return {'status': 'existing_receipt', 'receipt_id': old.receipt_id}
        # A commit may succeed before a later budget/scan failure prevents the
        # cache assignment. Reconcile that exact durable ID on the owner thread;
        # repeated delivery must not loop on a UNIQUE violation until restart.
        retained = self._db.execute('''SELECT length(receipt) AS n,
            sid=? AND digest=? AS same FROM sources WHERE cid=?''',
            (receipt.session_id, receipt.digest, receipt.chunk_id)).fetchone()
        if retained is not None:
            if not 1 <= retained['n'] <= RECEIPT_BYTES or not retained['same']:
                raise Unavailable('conflicting_ingestion_receipt')
            raw = self._db.execute('SELECT receipt FROM sources WHERE cid=?',
                                   (receipt.chunk_id,)).fetchone()[0]
            budget.projection(len(raw)); existing = Receipt.decode(raw)
            if existing != receipt:raise Unavailable('conflicting_ingestion_receipt')
            self._generation(op)
            if not self.authority.receipt_valid(existing, budget):
                raise Unavailable('source_authority_unavailable')
            budget.check(); self._generation(op); self._receipts[receipt.chunk_id] = existing
            return {'status': 'existing_receipt', 'receipt_id': existing.receipt_id}
        with self._transaction(budget):
            if self._db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] >= MAX_RECEIPTS:
                raise Unavailable('source_receipt_capacity')
            self._db.execute('INSERT INTO sources(cid,sid,digest,receipt) VALUES(?,?,?,?)',
                (receipt.chunk_id, receipt.session_id, receipt.digest, receipt.encoded))
            self._generation(op)
            if not self.authority.receipt_valid(receipt, budget):raise Unavailable('source_changed_before_receipt_commit')
        self._generation(op)
        if not self.authority.receipt_valid(receipt, budget):
            self.revoked(); raise Unavailable('source_changed_after_receipt_commit')
        self._receipts[receipt.chunk_id] = receipt
        return {'status': 'receipt_retained', 'receipt_id': receipt.receipt_id}

    def _cell_current(self,op,cell,budget):
        doc=json.loads(cell.proof)
        if op.kind=='publish_auto':
            from .moss_automatic import authority_valid
            return authority_valid(self.authority,op.payload[0],doc,budget)
        return self.authority.cell_valid(doc,budget,publishing=True)

    def _publish(self, op, budget):
        cells=(op.payload[1],) if op.kind=='publish_auto' else op.payload
        # Validate the whole batch before the first retention decision.
        for cell in cells:
            cell.__post_init__(); self._generation(op)
            if not self._cell_current(op,cell,budget):
                raise Unavailable('primary_commit_or_source_unavailable')
        results = []
        for cell in cells:
            self._generation(op)
            old = self._db.execute('SELECT cell_id,length(proof) AS n,length(pcm) AS pn,state FROM cells WHERE ownership=?', (cell.ownership,)).fetchone()
            if old:
                if old['cell_id'] != cell.cell_id or not 1 <= old['n'] <= PROOF_BYTES:
                    raise Unavailable('conflicting_committed_ownership')
                proof = self._db.execute('SELECT proof FROM cells WHERE cell_id=?', (cell.cell_id,)).fetchone()[0]
                budget.projection(len(proof))
                if proof != cell.proof:raise Unavailable('conflicting_committed_proof')
                status='not_retained_pcm' if op.kind=='publish_auto' and (old['pn'] is None or old['state']!='retained') else 'existing'
                results.append({'cell_id': cell.cell_id, 'status': status})
                continue
            if self._db.execute('SELECT COUNT(*) FROM cells').fetchone()[0] >= MAX_CELLS:
                results.append({'cell_id': cell.cell_id, 'status': 'not_retained_metadata_capacity'})
                continue
            budget.projection(len(cell.proof))
            # Finite conservative preflight. SQLITE_FULL still rolls back and
            # falls back to metadata once, without evicting an earlier selection.
            page_count = self._db.execute('PRAGMA page_count').fetchone()[0]
            free_pages = self._db.execute('PRAGMA freelist_count').fetchone()[0]
            room = (MAX_PAGE_COUNT - page_count + free_pages) * PAGE_SIZE
            with_pcm = room >= len(cell.pcm) + len(cell.proof) * 2 + 65536
            # Automatic authority lives in the primary intent. Do not turn a
            # transient PCM-capacity miss into a permanent metadata-only cell.
            status = ('not_retained_pcm' if op.kind=='publish_auto' and not with_pcm
                      else self._insert_cell(op, cell, budget, with_pcm))
            results.append({'cell_id': cell.cell_id, 'status': status})
        self._generation(op)
        for cell in cells:
            doc = json.loads(cell.proof)
            if not self._cell_current(op,cell,budget):
                superseded = getattr(self.authority, 'v1_publication_superseded', None)
                try:advanced = self.core_seam_enabled and superseded is not None and superseded(doc, budget)
                except Exception:
                    self.revoked(); raise
                if advanced:
                    self._generation(op)
                    # Keep the failed/unknown publication outcome. Expected v1
                    # metadata supersession must not revoke independent v3 work.
                    raise Unavailable('primary_commit_advanced_after_publication')
                self.revoked(); raise Unavailable('source_changed_after_publication_commit')
        durable_states=('retained','existing') if op.kind=='publish_auto' else ('retained','not_retained_pcm','existing')
        self._durable = min(self._durable + sum(r['status'] in durable_states for r in results), (1 << 31) - 1)
        return {'status': 'durable', 'cells': results}

    def _insert_cell(self, op, cell, budget, with_pcm):
        state = 'retained' if with_pcm else 'not_retained_pcm'
        try:
            with self._transaction(budget):
                self._db.execute('INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state) VALUES(?,?,?,?,?,?,?)',
                    (cell.cell_id, cell.ownership, cell.session_id, cell.chunk_id, cell.proof, cell.pcm if with_pcm else None, state))
                for pos, values in enumerate(self._dependency_values(json.loads(cell.proof),cell.parts)):
                    self._db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',
                        (cell.cell_id,pos,*values))
                self._generation(op)
                if not self._cell_current(op,cell,budget):
                    raise Unavailable('source_changed_before_publication_commit')
            return state
        except sqlite3.Error as exc:
            if getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL:
                budget.check()
                if op.kind=='publish_auto':return 'not_retained_pcm'
                if with_pcm:return self._insert_cell(op, cell, budget, False)
                return 'not_retained_metadata_capacity'
            raise

    @staticmethod
    def _dependency_values(doc,parts):
        values=[(p.chunk_id,p.source_sha256,p.receipt_sha256,p.source_start,p.source_end,
                 p.logical_start,p.logical_end) for p in parts]
        from .native_owned_core_seams import BOUNDED_SCHEMA,validate_target
        if doc.get('schema')!=BOUNDED_SCHEMA:return values
        _,originals=validate_target(doc['target'])
        sources={p.chunk_id:p.source_sha256 for p in parts}
        for commit in [*originals,*doc['commits']]:
            p=commit['part']; old=sources.setdefault(p['chunk_id'],p['source_sha256'])
            if old!=p['source_sha256']:raise Unavailable('bounded_source_identity_changed')
        for source in doc['inventories']['sources']:
            old=sources.setdefault(source['id'],source['digest'])
            if old!=source['digest']:raise Unavailable('bounded_source_identity_changed')
        acoustic={p.chunk_id for p in parts}
        for cid in sorted({r['cid'] for r in doc['dependency_roots']}-acoustic):
            # Explicit zero-span retention-only index entry. It grants no PCM
            # mapping/receipt authority; that remains in the strict full proof.
            values.append((cid,sources[cid],'',0,0,0,0))
        if len(values)>MAX_PARTS:raise Unavailable('bounded_dependency_capacity')
        return values

    def _document(self, cell_id, budget, *, expected_session=None,
                  expected_sequence=None, expected_revision=None):
        digest(cell_id)
        row = self._db.execute('SELECT seq,sid,cid,state,revision,length(proof) AS pn,length(pcm) AS n FROM cells WHERE cell_id=?', (cell_id,)).fetchone()
        if row is None:raise Unavailable('cell_unavailable')
        # The existing length-first projection contains all scope guards. Do
        # not retrieve or disclose proof for another authenticated session.
        if expected_session is not None and row['sid'] != expected_session:
            raise Unavailable('cell_unavailable')
        if (expected_sequence is not None and row['seq'] != expected_sequence
                or expected_revision is not None and row['revision'] != expected_revision):
            raise Unavailable('cell_binding_changed')
        if not 1 <= row['pn'] <= PROOF_BYTES or row['n'] is not None and not 1 <= row['n'] <= CELL_PCM_BYTES:
            raise Unavailable('stored_cell_length')
        proof = self._db.execute('SELECT proof FROM cells WHERE cell_id=?', (cell_id,)).fetchone()[0]
        budget.projection(len(proof)); doc, support, parts = validate_document(proof)
        policy = doc['policy']
        anchor = seam_anchor(doc) if is_composite(doc) else doc['commit']
        accepted = (POLICY_SHA256, SEAM_POLICY_SHA256) if self._store_policy == STORE_POLICY_SHA256 else (POLICY_SHA256,)
        from .native_owned_core_seams import store_policy, POLICY_SHA256 as CORE_POLICY, RESET_POLICY_SHA256, RESTART_POLICY_SHA256,BOUNDED_POLICY_SHA256
        if self._store_policy == store_policy():accepted = (POLICY_SHA256, SEAM_POLICY_SHA256, CORE_POLICY, RESET_POLICY_SHA256, RESTART_POLICY_SHA256)
        if self.bounded_core_enabled and self._store_policy==store_policy(bounded=True):
            accepted=(POLICY_SHA256,SEAM_POLICY_SHA256,CORE_POLICY,RESET_POLICY_SHA256,RESTART_POLICY_SHA256,BOUNDED_POLICY_SHA256)
        if (canonical(doc) != proof or policy not in accepted
                or type(doc.get('dependencies')) is not list or not 1 <= len(doc['dependencies']) <= MAX_PARTS
                or anchor['chunk_id'] != row['cid']
                or any(p['session_id'] != row['sid'] for p in doc['dependencies'])
                or hashlib.sha256(canonical({'policy': policy, 'support': support.to_dict()})).hexdigest() != cell_id):
            raise Unavailable('stored_cell_proof')
        from .native_owned_core_seams import BOUNDED_SCHEMA
        if doc.get('schema')==BOUNDED_SCHEMA:
            expected=self._dependency_values(doc,parts)
            saved=self._db.execute('''SELECT position,cid,digest,receipt_id,source_start,source_end,logical_start,logical_end
                FROM dependencies WHERE cell_id=? ORDER BY position LIMIT 129''',(cell_id,)).fetchall()
            budget.projection(len(saved)*384)
            if [tuple(r) for r in saved]!=[(i,*v) for i,v in enumerate(expected)]:
                raise Unavailable('bounded_cell_dependency_index_mismatch')
        return row, doc, proof

    def _read(self, op, budget):
        scoped = op.kind in ('jobs_detail', 'dispatch', 'validate_dispatch')
        expected_sequence = expected_revision = None
        sid = None
        self._generation(op); op.check()
        if scoped:
            sid, cell_id = op.payload[:2]
            if op.kind != 'jobs_detail':
                _, _, expected_sequence, expected_revision, proof_hash, support_id, generation = op.payload
                if generation is not self._epoch:raise Unavailable('cell_revoked')
            if not self.authority.session_valid(sid, budget):raise Unavailable('session_unavailable')
            self._generation(op); op.check()
        else:cell_id = op.payload
        row, doc, proof = self._document(cell_id, budget, expected_session=sid,
            expected_sequence=expected_sequence, expected_revision=expected_revision)
        self._generation(op); op.check()
        support = SourceSupport.from_dict(doc['support']) if scoped else None
        metadata = None
        if scoped:
            metadata = {'cell_id': cell_id, 'sequence': row['seq'], 'revision': row['revision'],
                        'state': row['state'], 'proof_sha256': hashlib.sha256(proof).hexdigest(),
                        'support': support, 'owner_generation': op.epoch,
                        'pcm_retained': row['n'] is not None and row['state'] == 'retained'}
            if is_composite(doc):metadata['coverage'] = seam_coverage(doc)
            if op.kind != 'jobs_detail':
                if metadata['proof_sha256'] != proof_hash or support.support_id != support_id:
                    raise Unavailable('cell_binding_changed')
                if row['n'] is None or row['state'] != 'retained':raise Unavailable('cell_pcm_not_retained')
        if not self.authority.cell_valid(doc, budget, publishing=False):raise Unavailable('source_authority_unavailable')
        self._generation(op); op.check()
        if op.kind in ('detail', 'jobs_detail', 'validate_dispatch'):
            if not self.authority.cell_valid(doc, budget, publishing=False):raise Unavailable('source_changed_after_read')
            self._generation(op); op.check()
            if scoped:
                if not self.authority.session_valid(sid, budget):raise Unavailable('session_deleted_after_read')
                self._generation(op); op.check()
                result = dict(metadata, status='available')
                if op.kind in ('jobs_detail', 'validate_dispatch'):result['detail'] = proof
                return result
            # The detail itself is the admitted <=64 KiB proof, no large wrapper.
            result = {'status': 'available', 'detail': proof, 'regeneration_eligible': True}
            if is_composite(doc):result['coverage'] = seam_coverage(doc)
            return result
        if row['n'] is None:raise Unavailable('pcm_not_retained')
        if len(self._pins) >= MAX_PINS:raise Unavailable('read_pin_capacity')
        pcm = self._db.execute('SELECT pcm FROM cells WHERE cell_id=?', (cell_id,)).fetchone()[0]
        self._generation(op); op.check(); budget.check()
        if len(pcm) != row['n'] or hashlib.sha256(pcm).hexdigest() != doc['support']['pcm16le_sha256']:
            raise Unavailable('stored_pcm_mismatch')
        if scoped and float32_digest(pcm) != support.float32le_sha256:
            raise Unavailable('stored_pcm_mismatch')
        self._generation(op); op.check(); budget.check()
        if not self.authority.cell_valid(doc, budget, publishing=False):raise Unavailable('source_changed_after_read')
        self._generation(op); op.check()
        if scoped:
            if not self.authority.session_valid(sid, budget):raise Unavailable('session_deleted_after_read')
            self._generation(op); op.check()
        if not self._lock.acquire(timeout=LOCK_SECONDS):raise Unavailable('pin_lock_busy')
        try:
            self._generation(op); op.check()
            if len(self._pins) >= MAX_PINS:raise Unavailable('read_pin_capacity')
            pin = Pin(self, cell_id, doc, pcm); self._pins[id(pin)] = pin
            op._pin = pin
        finally:self._lock.release()
        if scoped:
            # Pin remains owned by the operation until claim or positive drain.
            # The exact proof bytes are shared with the token, never rebuilt.
            return dict(metadata, status='available', detail=proof)
        result = {'status': 'available', 'pin': pin, 'regeneration_eligible': True}
        if is_composite(doc):result['coverage'] = seam_coverage(doc)
        return result

    def _list(self, op, budget):
        # Authorities without this optional optimization retain the same reads.
        scope = getattr(self.authority, 'list_read_scope', None)
        if scope is None:return self._list_rows(op, budget)
        with scope(budget):return self._list_rows(op, budget)

    def _list_rows(self, op, budget):
        scoped = op.kind == 'jobs_list'
        if scoped:sid, after, limit = op.payload
        else:sid, after = op.payload; limit = PAGE_ROWS
        text(sid); integer(limit, PAGE_ROWS, 1)
        if scoped:integer(after, (1 << 63) - 1)
        else:integer(after)
        self._generation(op)
        session_start = budget.sql_bytes
        if not self.authority.session_valid(sid, budget):raise Unavailable('session_unavailable')
        final_sql = budget.sql_bytes - session_start
        self._generation(op)
        rows = self._db.execute('SELECT seq,cell_id,state,revision FROM cells WHERE sid=? AND seq>? ORDER BY seq LIMIT 20', (sid, after)).fetchall()
        result = []; checks = []; cursor = after
        for item in rows[:limit]:
            if budget.sql_bytes > 256 * 1024 - PROOF_BYTES - 8192:break
            # Leave source/projection room for the mandatory second authority
            # pass. An unexamined SQL row must never become the continuation.
            if scoped and checks and (budget.read_bytes > SOURCE_READ_BYTES // 3
                    or budget.sql_bytes > SQL_PROJECTION_BYTES // 3):break
            _, doc, proof = self._document(item['cell_id'], budget,
                expected_session=sid if scoped else None,
                expected_sequence=item['seq'] if scoped else None,
                expected_revision=item['revision'] if scoped else None)
            self._generation(op)
            authority_start = budget.sql_bytes
            if self.authority.cell_valid(doc, budget, publishing=False):
                authority_sql = budget.sql_bytes - authority_start
                if scoped:
                    support = SourceSupport.from_dict(doc['support'])
                    entry = {'sequence': item['seq'], 'cell_id': item['cell_id'],
                        'revision': item['revision'], 'state': item['state'],
                        'support_id': support.support_id, 'proof_sha256': hashlib.sha256(proof).hexdigest(),
                        'start_frame': support.start_frame, 'end_frame': support.end_frame,
                        'core_start_frame': support.core_start_frame, 'core_end_frame': support.core_end_frame}
                else:entry = dict(item)
                if is_composite(doc):entry['coverage'] = seam_coverage(doc)
                # Reserve only measured byte costs, never authority values or
                # snapshots. Every included cell is still revalidated below.
                # The attempted candidate's proof/reads remain fully charged.
                projected = len(canonical(result + [entry], PROOF_BYTES))
                if checks and budget.sql_bytes + projected + final_sql + authority_sql > SQL_PROJECTION_BYTES:
                    if budget.sql_bytes + len(canonical(result, PROOF_BYTES)) + final_sql > SQL_PROJECTION_BYTES:
                        raise Unavailable('operation_work_capacity')
                    break  # Do not include or advance past this candidate.
                result.append(entry); checks.append(doc); final_sql += authority_sql
            self._generation(op)
            cursor = item['seq']
        if rows and cursor == after:raise Unavailable('operation_work_capacity')
        encoded = canonical(result, PROOF_BYTES); budget.projection(len(encoded))
        self._generation(op)
        if any(not self.authority.cell_valid(doc, budget, publishing=False) for doc in checks):
            raise Unavailable('source_changed_after_list_read')
        self._generation(op)
        if not self.authority.session_valid(sid, budget):raise Unavailable('session_deleted_after_read')
        self._generation(op)
        response = {'status': 'available', 'cells': result, 'next_cursor': cursor}
        if scoped:
            response['has_more'] = bool(rows and (cursor < rows[-1]['seq'] or len(rows) == PAGE_ROWS))
        return response

    def _expire(self, op, budget):
        cell_id = digest(op.payload)
        if any(p.cell_id == cell_id and not p._released for p in tuple(self._pins.values())):
            raise Unavailable('cell_pinned')
        with self._transaction(budget):
            self._generation(op)
            if op.kind == 'expire':
                self._db.execute("UPDATE cells SET pcm=NULL,state='not_retained_pcm',revision=revision+1 WHERE cell_id=? AND pcm IS NOT NULL", (cell_id,))
            else:self._db.execute('DELETE FROM cells WHERE cell_id=?', (cell_id,))
        return {'status': 'expired' if op.kind == 'expire' else 'deleted', 'cell_id': cell_id}

    def _sweep(self, budget):
        if self._sweep_generation is not self._epoch:
            self._sweep_cell = 0; self._sweep_source = ''; self._sweep_generation = self._epoch
        rows = self._db.execute('SELECT seq,cell_id FROM cells WHERE seq>? ORDER BY seq LIMIT 20', (self._sweep_cell,)).fetchall()
        if rows:
            for item in rows:
                # Length-first one-at-a-time projections keep the 256 KiB cap.
                if budget.sql_bytes > SQL_PROJECTION_BYTES - PROOF_BYTES:break
                row, doc, _ = self._document(item['cell_id'], budget)
                if not self.authority.cell_valid(doc, budget, publishing=False):
                    for pin in tuple(self._pins.values()):
                        if pin.cell_id == item['cell_id']:pin.cancelled.set()
                    with self._transaction(budget):self._db.execute('DELETE FROM cells WHERE cell_id=?', (item['cell_id'],))
                self._sweep_cell = item['seq']
                # Yield only after this item and its cursor are complete.
                if self._slot is not None:return
            return
        rows = self._db.execute('SELECT cid FROM sources WHERE cid>? ORDER BY cid LIMIT 20', (self._sweep_source,)).fetchall()
        if rows:
            for item in rows:
                receipt = self._receipts.get(item['cid'])
                if receipt is None or not self.authority.receipt_valid(receipt, budget):
                    with self._transaction(budget):self._db.execute('DELETE FROM sources WHERE cid=?', (item['cid'],))
                    self._receipts.pop(item['cid'], None)
                self._sweep_source = item['cid']
                if self._slot is not None:return
            return
        if self._sweep_generation is self._epoch and not self._publication_revoke_pending:
            self._reconcile.clear(); self._cleanup_pending = False
            # A deletion may land between the first token check and clearing.
            # Recheck after clearing; a later notifier sets both flags itself.
            if self._sweep_generation is not self._epoch or self._publication_revoke_pending:
                self._cleanup_pending = True; self._reconcile.set()

    def _run(self):
        def finish(op):
            # Only the original owner can retire the handoff accounting. A
            # delivery lock miss or pin-release miss retains this exact slot.
            if op.kind == 'dispatch':
                if op.epoch is not self._epoch or self._admission_closed:
                    op.abandon('cell_revoked')
                if op._pin is not None and op.result.get('status') != 'available':
                    op.abandon(op.result.get('reason', 'dispatch_abandoned'))
                if not op._advance_delivery():return False
            elif op._pin is not None:
                op._pin.release()
                if not op._pin.release_confirmed:return False
                op._pin = None
            if not self._lock.acquire(timeout=LOCK_SECONDS):
                self._status = 'handoff_release_pending'; return False
            try:
                if self._slot is op:
                    self._slot = None; op.slot_released.set()
            finally:self._lock.release()
            if op.slot_released.is_set():op.done.set(); return True
            return False

        try:
            self._open(); self._status = 'ready' if self.enabled else 'cleanup_only'; self._ready.set()
            while True:
                self._wake.wait(.05); self._wake.clear()
                self._advance_publication_revoke()
                self._drain_released_pins()
                op = self._slot
                if op is not None and op.payload is None:
                    finish(op)
                    continue
                if self._stop.is_set():
                    if op is not None:
                        if op.kind == 'dispatch':op.abandon('dispatch_admission_closed')
                        else:op.result = {'status': 'unknown', 'reason': 'owner_shutdown'}
                        op.payload = None; finish(op)
                    elif not self._pins:break
                    else:
                        self._cleanup_pending = True; self._status = 'shutdown_pending'
                    continue
                if op is None and (self._admission_closed or self._publication_revoke_pending):continue
                if op is None and not self._reconcile.is_set():
                    if self._schedule_compact_gc():continue
                    if self._schedule_receipt_gc():continue
                    if time.monotonic() < self._retention_next_refresh:continue
                    budget = Budget.for_owner()
                    self._refresh_retention(budget)
                    continue
                if op is not None and op.kind == 'compact_gc':
                    op.result = self._collect_compacts(op, Budget.for_owner(op))
                    op.payload = None; finish(op)
                    continue
                if op is not None and op.kind == 'receipt_gc':
                    op.result = self._collect_receipts(op, Budget.for_owner(op))
                    op.payload = None; finish(op)
                    continue
                if op is not None and op.kind == 'dispatch':
                    if op.epoch is not self._epoch or self._admission_closed:op.abandon('cell_revoked')
                    if not op._begin_dispatch():
                        if op.cancelled.is_set() or time.monotonic() >= op.deadline:
                            op.payload = None; finish(op)
                        continue
                budget = Budget.for_owner(op)
                try:
                    with budget.phase():
                        self._db.set_progress_handler(budget.progress, 100)
                        self._scan(budget)
                        if op is None:
                            self.retention_changed(); self._sweep(budget)
                        elif op.kind == 'receipt':op.result = self._receipt(op, budget)
                        elif op.kind in ('publish','publish_auto'):
                            cells=(op.payload[1],) if op.kind=='publish_auto' else op.payload
                            reason = self._publication_policy(cells)
                            if reason:raise Unavailable(reason)
                            op.result = self._publish(op, budget)
                        elif op.kind in ('read', 'detail', 'jobs_detail', 'dispatch', 'validate_dispatch'):
                            op.result = self._read(op, budget)
                        elif op.kind in ('list', 'jobs_list'):op.result = self._list(op, budget)
                        else:op.result = self._expire(op, budget)
                        if op is not None:op.check()
                        self._scan(budget)
                        if op is not None:op.check()
                        if op is not None and op.kind in ('jobs_list', 'jobs_detail', 'dispatch', 'validate_dispatch'):
                            self._generation(op)
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, Unavailable) else 'optional_sql_or_io_failure'
                    self._status = reason
                    if op is not None:
                        # _read installs the pin on its operation immediately
                        # after registration, before any later scan can fail.
                        # Keep that reference until exact release is acknowledged.
                        op.result = {'status': 'unknown' if op.kind in ('publish', 'publish_auto', 'receipt') else 'unavailable', 'reason': reason}
                        self._dropped = min(self._dropped + 1, (1 << 31) - 1)
                    else:self._cleanup_pending = True
                finally:
                    self._db.set_progress_handler(None, 0)
                    if op is not None:
                        op.payload = None
                        if op.kind != 'dispatch' and op.result.get('pin') is not None:
                            # Preserve ordinary read delivery semantics. Dispatch
                            # alone requires the exclusive claim/abandon handshake.
                            op._pin = None
                        finish(op)
        except Exception as exc:
            self._status = str(exc) if isinstance(exc, Unavailable) else 'optional_startup_unavailable'
            self._cleanup_pending = os.path.lexists(self.directory)
        finally:
            self._ready.clear()
            # Even an exceptional owner exit cannot discard an unclaimed pin,
            # a contended release or the last live cleanup owner. No SQL/native
            # work or application callbacks occur in this drainage loop.
            self.close_admission()
            for pin in tuple(self._pins.values()):pin.cancelled.set()
            op = self._slot
            if op is not None:
                if op.kind == 'dispatch':op.abandon('owner_shutdown')
                else:
                    if op._pin is None:op._pin = op.result.get('pin')
                    op.result = {'status': 'unknown', 'reason': 'owner_shutdown'}
                op.payload = None
            while self._slot is not None or self._pins or self._publication_revoke_pending:
                self._advance_publication_revoke()
                self._cleanup_pending = True; self._status = 'shutdown_pending'
                self._drain_released_pins()
                if self._slot is not None:finish(self._slot)
                if self._slot is not None or self._pins or self._publication_revoke_pending:
                    self._wake.wait(.05); self._wake.clear()
            if self._db is not None:
                try:
                    if self._db.in_transaction:self._db.execute('ROLLBACK')
                    self._db.close(); self._db = None
                except Exception:
                    self._cleanup_pending = True; self._status = 'cleanup_pending'
            if self._closed and self._db is None:
                self._cleanup_pending = False; self._status = 'closed'
