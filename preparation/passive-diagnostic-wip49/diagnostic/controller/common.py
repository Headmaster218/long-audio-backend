# Unexecuted portable template; generate every configuration placeholder before use.
"""Finite qualification primitives. Importing this module never loads providers."""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
import io
import json
import math
import os
from pathlib import Path
import stat
import struct
import threading
import time
import wave

MIB = 2**20
FRAMES = 336000
CELL_FRAMES = 320000
SOURCE_HEAD = '1fc066910ca78b2e57d7a28f79b91cafc06f5dd5'

class Refused(RuntimeError):
    pass

def require(value, reason):
    if not value:
        raise Refused(reason)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def finite(value):
    return type(value) in (int, float) and math.isfinite(value)

PHASES = {'preflight': (180.,180.), 'primary': (900.,1080.),
          'support': (60.,1140.), 'moss-compute': (300.,1440.),
          'moss-cleanup': (60.,1500.), 'lifecycle': (120.,1620.),
          'posthash': (120.,1740.), 'outer-cleanup': (60.,1800.)}


class Deadlines:
    """Fixed outer endpoints plus non-borrowable per-phase maxima."""
    def __init__(self, env=None, clock=time.monotonic):
        env = os.environ if env is None else env
        self.clock, self.primary = clock, None
        self.phase = self.phase_end = None
        self.phase_records = {}
        try:
            self.guard_work = float(env['GUARD_WORK_END'])
            self.absolute = float(env['GUARD_ABSOLUTE_END'])
            self.origin = self.absolute - 1800.
            self.work, self.posthash = self.origin+1620., self.origin+1740.
            self.stop = Path(env['GUARD_STOP_PATH'])
            require(float(env.get('REAL_CAPTURE_POSTHASH_END',self.posthash)) == self.posthash,
                    'posthash_deadline_mismatch')
        except (KeyError,ValueError) as exc:
            raise Refused('missing_immutable_outer_deadlines') from exc
        require(all(map(finite,(self.origin,self.guard_work,self.work,self.posthash,self.absolute))),
                'nonfinite_deadline')
        require(self.guard_work == self.posthash and self.absolute-self.clock() <= 1800.,
                'outer_clock_geometry')
        self.service_check()

    def service_check(self):
        require(not self.stop.exists(), 'outer_STOP')
        require(self.clock() < self.work, 'work_deadline')

    def enter(self, name, *, record=None, spec_sha256=None):
        require(name in PHASES, 'unknown_phase')
        maximum, offset = PHASES[name]
        if record is None:
            started = self.clock()
            record = {'phase':name,'created_at':started,
                      'deadline':min(started+maximum,self.origin+offset),
                      'spec_sha256':spec_sha256}
        require(set(record) == {'phase','created_at','deadline','spec_sha256'} and
                record['phase'] == name and record['spec_sha256'] == spec_sha256 and
                finite(record['created_at']) and finite(record['deadline']) and
                self.origin <= record['created_at'] <= self.clock() and
                record['deadline'] == min(record['created_at']+maximum,self.origin+offset),
                'phase_window_binding')
        require(name not in self.phase_records or self.phase_records[name] == record,
                'phase_cannot_rebind')
        if self.phase is not None:
            require(PHASES[name][1] >= PHASES[self.phase][1], 'phase_cannot_reverse')
        self.phase_records[name] = dict(record)
        self.phase, self.phase_end = name, record['deadline']
        self.check()
        return dict(record)

    def check(self, end=None):
        require(not self.stop.exists(), 'outer_STOP')
        limit = self.phase_end if self.phase_end is not None else self.work
        require(self.clock() < min(limit,limit if end is None else end), 'work_deadline')

    def call_end(self, maximum):
        self.check()
        return min(self.phase_end if self.phase_end is not None else self.work,
                   self.clock()+maximum)

    def bind_primary(self, record, spec_sha256):
        require(self.primary is None, 'primary_deadline_already_bound')
        require(record.get('phase') == 'primary' and record['created_at'] <= self.origin+180. and
                record['deadline'] == record['created_at']+900., 'full_primary_window_missing')
        self.enter('primary',record=record,spec_sha256=spec_sha256)
        self.primary = record['deadline']

    def cleanup_check(self):
        require(self.clock() < self.absolute, 'cleanup_deadline')


def bounded_read(path, maximum, check=lambda: None):
    """Regular no-follow snapshot; never allocates the advertised file size."""
    check()
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= maximum, 'bounded_file_shape')
        out = bytearray()
        while True:
            check()
            block = os.read(fd, min(65536, maximum + 1 - len(out)))
            if not block:
                break
            out.extend(block)
            require(len(out) <= maximum, 'bounded_file_growth')
        after = os.fstat(fd)
        entry = path.stat(follow_symlinks=False)
        key = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        require(key(before) == key(after) == key(entry), 'file_changed_during_read')
        check()
        return bytes(out)
    finally:
        os.close(fd)


def atomic_json(path, value, maximum=MIB):
    data = canonical(value)
    require(len(data) <= maximum, 'receipt_byte_cap')
    path = Path(path)
    tmp = path.with_name(path.name + '.pending')
    with open(tmp, 'xb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


ROLE_FRAMES = {'source':336000, 'upload_a':168000, 'upload_b':168000,
               'analyzer_a':200000, 'analyzer_b':200000, 'composite':320000}
SOURCE_WAV_BYTES = 672044
SOURCE_WAV_SHA = '@GENERATED_SOURCE_WAV_SHA256@'
ASSEMBLER_WORKING_BYTES = 4194304


def validate_role_pin(pins, role):
    require(role in ROLE_FRAMES and type(pins) is dict and
            set(pins) == {'frames', 'wav_sha256', 'pcm16_sha256', 'float32_sha256'} and
            type(pins['frames']) is int and pins['frames'] == ROLE_FRAMES[role] and
            all(type(pins[k]) is str and len(pins[k]) == 64 and
                all(c in '0123456789abcdef' for c in pins[k]) for k in
                ('wav_sha256', 'pcm16_sha256', 'float32_sha256')), 'exact_materialized_role_pins')
    return dict(pins)


def validate_input_pins(pins):
    require(type(pins) is dict and set(pins) == set(ROLE_FRAMES), 'exact_materialized_input_pins')
    result = {role:validate_role_pin(pins[role],role) for role in ROLE_FRAMES}
    require(result['source']['wav_sha256'] == SOURCE_WAV_SHA, 'current_source_wav_pin')
    require(result['upload_a']['wav_sha256'] != result['upload_b']['wav_sha256'],
            'split_wav_digests_must_differ')
    return result


def input_pins(check=lambda: None):
    """No implicit historical receipt or unpinned local input discovery."""
    raise Refused('explicit_current_input_pins_required')


def fixed_wav(path, check=lambda: None, *, encoded_pin=False, pins=None, role='source'):
    pins = input_pins(check)[role] if pins is None else validate_role_pin(pins,role)
    frames = ROLE_FRAMES[role]
    data = bounded_read(path, MIB, check)
    with wave.open(io.BytesIO(data), 'rb') as f:
        require((f.getframerate(), f.getnchannels(), f.getsampwidth(), f.getnframes()) ==
                (16000, 1, 2, frames), 'fixed_input_geometry')
        pcm = f.readframes(frames + 1)
    require(len(pcm) == frames * 2 and sha(pcm) == pins['pcm16_sha256'], 'fixed_input_pcm_pin')
    floats = b''.join(struct.pack('<f', item[0] / 32768.) for item in struct.iter_unpack('<h', pcm))
    require(sha(floats) == pins['float32_sha256'], 'fixed_input_float_pin')
    if encoded_pin:
        require(sha(data) == pins['wav_sha256'], 'fixed_input_wav_pin')
    check()
    return {'frames': frames, 'wav_sha256': sha(data), 'pcm16_sha256': sha(pcm),
            'float32_sha256': sha(floats)}, data, pcm


class Journal:
    """One process per file; small append-only receipts survive abrupt child exit."""
    def __init__(self, directory, role, maximum=2*MIB):
        self.path = Path(directory) / f'{role}-{os.getpid()}.jsonl'
        self.bytes = 0
        self.maximum = maximum
        self.lock = threading.Lock()

    def emit(self, event, **fields):
        raw = canonical({'event': event, 'monotonic': time.monotonic(), **fields}) + b'\n'
        with self.lock:
            require(len(raw) <= 128*1024 and self.bytes + len(raw) <= self.maximum, 'journal_cap')
            with self.path.open('ab') as out:
                out.write(raw)
            self.bytes += len(raw)


class Admission:
    """Pre-entry caps; exceptions/late returns never count as accepted returns."""
    def __init__(self, caps, check, emit=lambda *a, **kw: None, sequence=None):
        self.caps = dict(caps)
        self.check = check
        self.emit = emit
        self.sequence = tuple(sequence) if sequence is not None else None
        self.index = 0
        self.counts = {name: {'entered': 0, 'returned': 0, 'accepted': 0, 'rejected': 0,
                              'exceptions': 0} for name in caps}
        self.lock = threading.RLock()

    @contextmanager
    def _locked(self):
        # Short bounded wait, followed by the caller's deadline recheck while held.
        require(self.lock.acquire(timeout=.05), 'admission_lock_deadline')
        try:
            yield
        finally:
            self.lock.release()

    def invoke(self, name, original, *args, validate=None, before_entry=None, return_check=None, **kwargs):
        with self._locked():
            require(name in self.caps, 'unregistered_operation')
            c = self.counts[name]
            try:
                self.check()
                require(c['entered'] < self.caps[name], 'pre_entry_cap_' + name)
                if self.sequence is not None:
                    require(self.index < len(self.sequence) and self.sequence[self.index] == name,
                            'pre_entry_sequence_' + name)
                if before_entry is not None:
                    before_entry()
            except BaseException:
                c['rejected'] += 1
                self.emit('rejected_entry', operation=name, counts=dict(c))
                raise
            c['entered'] += 1
            if self.sequence is not None:
                self.index += 1
            self.emit('entered', operation=name, counts=dict(c))
        # The journal and pre-entry callback can themselves use time. 'entered'
        # is a conservative entry intent, not proof that original() was invoked.
        try:
            self.check()  # Last gate immediately before delegation, outside the lock.
        except BaseException:
            with self._locked():
                c['rejected'] += 1
                self.emit('rejected_delegation', operation=name, counts=dict(c))
            raise
        try:
            value = original(*args, **kwargs)
        except BaseException as exc:
            try:
                with self._locked():
                    c['exceptions'] += 1
                    self.emit('original_exception', operation=name, exception=type(exc).__name__, counts=dict(c))
                    self.check()
            except BaseException:
                # Qualification bookkeeping failure cannot replace the original exception.
                # This operation has no accepted return and cannot qualify the run.
                pass
            raise
        with self._locked():
            c['returned'] += 1
            self.emit('returned', operation=name, counts=dict(c))
            (return_check or self.check)()  # Preserve the observed late-return count, never accept it.
        (return_check or self.check)()  # Strict before validation, even on immediate returns.
        if validate is not None:
            validate(value)
        (return_check or self.check)()
        with self._locked():
            (return_check or self.check)()
            c['accepted'] += 1
            self.emit('accepted', operation=name, counts=dict(c))
        return value


def retain_raw(path, destination, *, maximum=8*MIB, prefix=65536, check=lambda: None):
    """Keep original bytes before production unlink; oversized evidence is a prefix only.

    Oversized/unreadable diagnostics never claim a complete file hash. A whole-file hash
    is calculated only for a stable, fully read file within the pre-reserved envelope.
    """
    path, destination = Path(path), Path(destination)
    receipt = {'source_basename': path.name, 'complete_raw': False, 'full_sha256': None}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return dict(receipt, state='not_present')
    try:
        st = os.fstat(fd)
        receipt['observed_size'] = st.st_size
        require(stat.S_ISREG(st.st_mode), 'raw_not_regular')
        limit = maximum if st.st_size <= maximum else prefix
        raw = bytearray()
        while len(raw) < limit:
            check()
            block = os.read(fd, min(65536, limit-len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        unchanged = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns) == (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        complete = unchanged and st.st_size <= maximum and len(raw) == st.st_size
        with destination.open('xb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        receipt.update(state='retained' if complete else 'bounded_prefix', complete_raw=complete,
                       retained_bytes=len(raw), retained_sha256=sha(raw),
                       full_sha256=sha(raw) if complete else None, artifact=destination.name)
        return receipt
    finally:
        os.close(fd)


# Passive exception diagnostics; inherited safe types only, never exception messages.
import sqlite3

SQLITE_TYPES = frozenset((sqlite3.Error, sqlite3.DatabaseError, sqlite3.DataError,
    sqlite3.IntegrityError, sqlite3.InterfaceError, sqlite3.InternalError,
    sqlite3.NotSupportedError, sqlite3.OperationalError, sqlite3.ProgrammingError))

def safe_record(stage, exc):
    """No repr/str/traceback/message or user-overridable exception getters."""
    kind = type(exc)
    name = type.__getattribute__(kind, '__name__')
    if type(name) is not str or not 1 <= len(name) <= 80 or not name.isascii():
        name = 'ExceptionClassOmitted'
    code = sql_name = None
    # Only exact built-in sqlite exception types have their C-owned dict read.
    if kind in SQLITE_TYPES:
        values = BaseException.__dict__['__dict__'].__get__(exc, kind)
        candidate = dict.get(values, 'sqlite_errorcode')
        if type(candidate) is int and 0 <= candidate <= 65535:
            code = candidate
        candidate = dict.get(values, 'sqlite_errorname')
        if (type(candidate) is str and 1 <= len(candidate) <= 80
                and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for c in candidate)):
            sql_name = candidate
    return {'stage': stage, 'exception_type': name,
            'sqlite_errorcode': code, 'sqlite_errorname': sql_name}

class Diagnostics:
    """At most 64 records; contention drops an observation, never waits."""
    def __init__(self):
        self.records = []
        self.dropped = 0
        self.lock = threading.Lock()
        self.flushed = 0

    def record(self, stage, exc):
        try:
            item = safe_record(stage, exc)
            if not self.lock.acquire(False):
                self.dropped = min(self.dropped + 1, 65535)
                return
            try:
                if len(self.records) < 64:
                    self.records.append(item)
                else:
                    self.dropped = min(self.dropped + 1, 65535)
            finally:
                self.lock.release()
        except BaseException:
            # A diagnostic failure cannot replace the original exception.
            pass

    def wrap(self, stage, function):
        def observed(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except BaseException as exc:
                try:self.record(stage, exc)
                except BaseException:pass
                raise
        return observed

    def context(self, stage, function):
        @contextmanager
        def observed(*args, **kwargs):
            try:
                with function(*args, **kwargs) as value:
                    yield value
            except BaseException as exc:
                try:self.record(stage, exc)
                except BaseException:pass
                raise
        return observed

    def flush(self, journal):
        try:
            if not self.lock.acquire(False):return
            try:
                first = self.flushed
                rows = self.records[first:64]
                self.flushed = len(self.records)
                dropped = self.dropped
            finally:self.lock.release()
            if rows:journal.emit('passive_exception_diagnostics', records=rows, dropped=dropped)
        except BaseException:
            pass
