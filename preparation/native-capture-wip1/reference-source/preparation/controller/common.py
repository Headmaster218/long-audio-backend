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
FRAMES = 307040
PCM_SHA = '64bbdbb11ddd8508c244685ec31c0c9545a30329294d129456f5f37b54ee693b'
FLOAT_SHA = 'c5fd0b3075ec050dcfc0404859361a05b285457ee048c5603c2a59841d464f4b'
WAV_SHA = '6f13e1689993903034e25d486b2dd12fad48fee949447fadee72270e85092665'
DESIGN_SHA = '5c71577ca40f5f3a220b3982582a14dbbfa2da4fd642a0220592fc490e99d6ec'
ADDENDUM_SHA = 'e12683a74153f5942ce54673e098d2fbc9c2a536ac0cf32f9a0f4a94babf1078'
SOURCE_HEAD = '2c841d84a9b3204e510628f4aef17fe90334e76c'
SOURCE_TREE = 'bb31c31a9411ea4c0ec5e185cc9bae57ac31a1f0'

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

class Deadlines:
    """Only the accepted outer guard chooses deadlines; no CLI duration override."""
    def __init__(self, env=None, clock=time.monotonic):
        env = os.environ if env is None else env
        self.clock = clock
        self.primary = None
        try:
            self.guard_work = float(env['GUARD_WORK_END'])
            self.absolute = float(env['GUARD_ABSOLUTE_END'])
            self.work = self.absolute - 180.
            self.posthash = self.absolute - 60.
            require(float(env.get('REAL_CAPTURE_POSTHASH_END', self.posthash)) == self.posthash,
                    'posthash_deadline_mismatch')
            self.stop = Path(env['GUARD_STOP_PATH'])
        except (KeyError, ValueError) as exc:
            raise Refused('missing_immutable_outer_deadlines') from exc
        require(all(map(math.isfinite, (self.guard_work, self.work, self.posthash, self.absolute))), 'nonfinite_deadline')
        require(self.guard_work == self.posthash, 'outer_child_accounting_deadline_mismatch')
        require(self.work < self.posthash <= self.absolute - 60, 'missing_cleanup_reserve')
        require(self.posthash - self.work >= 120, 'missing_posthash_reserve')
        require(self.absolute - self.clock() <= 900, 'outer_budget_exceeds_900s')
        self.check()

    def check(self, end=None):
        require(not self.stop.exists(), 'outer_STOP')
        require(self.clock() < min(self.work, self.primary if self.primary is not None else self.work,
                                   end if end is not None else self.work), 'work_deadline')

    def call_end(self, maximum):
        self.check()
        return min(self.work, self.primary if self.primary is not None else self.work, self.clock() + maximum)

    def bind_primary(self, record, spec_sha256):
        require(self.primary is None, 'primary_deadline_already_bound')
        require(record['spec_sha256'] == spec_sha256 and finite(record['created_at']) and
                finite(record['deadline']) and record['deadline'] == record['created_at'] + 600 and
                record['deadline'] <= self.work, 'invalid_fixed_primary_window')
        self.primary = record['deadline']
        self.check()

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


def fixed_wav(path, check=lambda: None, *, encoded_pin=False):
    data = bounded_read(path, MIB, check)
    with wave.open(io.BytesIO(data), 'rb') as f:
        require((f.getframerate(), f.getnchannels(), f.getsampwidth(), f.getnframes()) ==
                (16000, 1, 2, FRAMES), 'fixed_input_geometry')
        pcm = f.readframes(FRAMES + 1)
    require(len(pcm) == FRAMES * 2 and sha(pcm) == PCM_SHA, 'fixed_input_pcm_pin')
    floats = b''.join(struct.pack('<f', item[0] / 32768.) for item in struct.iter_unpack('<h', pcm))
    require(sha(floats) == FLOAT_SHA, 'fixed_input_float_pin')
    if encoded_pin:
        require(sha(data) == WAV_SHA, 'fixed_input_wav_pin')
    check()
    return {'frames': FRAMES, 'wav_sha256': sha(data), 'pcm16_sha256': sha(pcm),
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
