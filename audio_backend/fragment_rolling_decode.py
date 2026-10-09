"""Experimental forward rolling decoder. No runtime, ownership, or HTTP hooks.

Every returned chunk is provisional until this exact session produces a completed
receipt after full emitted count/hash verification. Failure invalidates all its
provisional chunks. Compressed/recipe identity alone is not full PCM verification.
"""
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import selectors
import secrets
import stat
import struct
import subprocess
import threading
import time
import uuid

from .fragment_core import SourceChanged, UnsupportedAudio
from .fragment_decoded_source import (
    CHUNK, DecodeCancelled, DecodeLimit, DecodedSourceReceipt, Limits,
    SequentialDecodedReader, _Source, _Guard, _digest, _signature, _input,
    FFMPEG_SHA, FFMPEG_VERSION_SHA, FFPROBE_SHA, FFPROBE_VERSION_SHA,
)


class ReplayRequired(LookupError):
    """Requested frames have left this session's bounded ring; no seek was made."""
    def __init__(self, requested_start, cached_start, cached_end):
        super().__init__(f'Frame {requested_start} precedes cached [{cached_start}, {cached_end}); explicit replay required')
        self.requested_start, self.cached_start, self.cached_end = requested_start, cached_start, cached_end


@dataclass(frozen=True)
class ProvisionalPCMWindow:
    session_id: str
    start: int
    end: int
    pcm: bytes
    coordinate_space: str = 'decoder-emitted-pcm16le-v1'
    integrity_token: str = ''
    verification: str = 'provisional-until-this-session-completes'


@dataclass(frozen=True)
class CompletedDecode:
    session_id: str
    source_receipt_sha256: str
    compressed_sha256: str
    emitted_pcm_sha256: str
    emitted_frames: int
    sample_rate: int
    channels: int
    verification: str = 'full-session-emitted-count-and-hash-verified'
    structural_labels: str = 'not authenticated by this reader'
    automatic_admission: bool = False


_ACTIVE_SESSION = threading.Lock()


def _wrapped(command, limits):
    return ['/usr/bin/taskset', '-c', str(limits.cpu), '/usr/bin/prlimit',
            f'--cpu={limits.cpu_seconds}:{limits.cpu_seconds}', '--fsize=0:0', '--', *command]


def _safe_collect(command, guard, *, fd=None, cap=32768):
    """Bounded startup probe/version output, with no Python preexec_fn."""
    guard.check()
    child = subprocess.Popen(_wrapped(command, guard.limits), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                             pass_fds=(() if fd is None else (fd,)))
    selector = selectors.DefaultSelector()
    output, errors = bytearray(), bytearray()
    try:
        for stream in (child.stdout, child.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            guard.check(child)
            for key, _ in selector.select(.025):
                block = os.read(key.fd, 8192)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                target = output if key.fileobj is child.stdout else errors
                bound = cap if key.fileobj is child.stdout else 8192
                if len(target) + len(block) > bound:
                    raise DecodeLimit('Rolling startup metadata/diagnostic bound')
                target.extend(block)
        while child.poll() is None:
            guard.check(child)
            time.sleep(.01)
        if child.returncode or errors:
            raise UnsupportedAudio('Rolling startup tool failed or reported errors: ' + bytes(errors).decode(errors='replace')[:1000])
        return bytes(output)
    finally:
        selector.close()
        if child.poll() is None:
            with suppress(ProcessLookupError):
                child.kill()
        child.wait()
        child.stdout.close()
        child.stderr.close()


class _RollingSource(_Source):
    """Baseline descriptor contract, using safe native-wrapper startup helpers."""
    def __init__(self, path, limits, cancel):
        self.path = Path(path).resolve(strict=True)
        self.guard = _Guard(limits, self.path, cancel)
        self.guard.check()
        self.file = os.fdopen(os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb')
        try:
            st = os.fstat(self.file.fileno())
            if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= limits.max_source_bytes:
                raise UnsupportedAudio('Bounded regular immutable source required')
            self.signature = _signature(st)
            self.hash = _digest(self.file, self.guard)
            self.tools = []
            for name, expected, version_expected in [('ffmpeg', FFMPEG_SHA, FFMPEG_VERSION_SHA),
                                                      ('ffprobe', FFPROBE_SHA, FFPROBE_VERSION_SHA)]:
                executable = Path('/usr/bin') / name
                before = _signature(executable.stat())
                with executable.open('rb') as stream:
                    actual = _digest(stream, self.guard)
                version = _safe_collect([str(executable), '-version'], self.guard, cap=16384)
                if (_signature(executable.stat()) != before or actual != expected or
                        hashlib.sha256(version).hexdigest() != version_expected):
                    raise UnsupportedAudio('Pinned decoder/probe executable/build differs')
                self.tools.append((executable, before))
            self.verify()
        except BaseException:
            self.file.close()
            raise


def _safe_probe(fd, kind, guard):
    data = _safe_collect(['/usr/bin/ffprobe', '-v', 'error', *_input(fd, kind),
                          '-show_entries', 'stream=codec_name,profile,sample_rate,channels', '-of', 'json'],
                         guard, fd=fd)
    streams = json.loads(data).get('streams', [])
    if len(streams) != 1:
        raise UnsupportedAudio('Exactly one native audio stream required')
    stream = streams[0]
    if stream.get('codec_name') == 'aac' and stream.get('profile') != 'LC':
        raise UnsupportedAudio('AAC-LC profile required')
    return stream, int(stream.get('sample_rate', 0)), int(stream.get('channels', 0))


def _decoder_command(fd, kind, limits):
    # Native wrappers avoid running a Python preexec_fn inside the producer thread.
    # The decoder's exact binary/build/recipe was pinned by _Source.
    return _wrapped(['/usr/bin/ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-threads', '1',
                     '-filter_threads', '1', '-filter_complex_threads', '1',
                     '-c:a', 'opus' if kind == 'ogg-opus' else 'aac', *_input(fd, kind),
                     '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', '-threads', '1', '-f', 's16le', 'pipe:1'], limits)


class RollingDecodedReader:
    """One decoder, <=two-second ring + two <=64 KiB working/handoff chunks.

    One caller may read/finish at a time. cancel()/close() are thread-safe. An
    external cancel predicate must itself be thread-safe. A five-second default
    idle lease and whole-session wall budget include consumer pauses; they are
    not active-decode-only budgets. CPU is per decoder subprocess, not aggregate.
    This compact profile keeps the receipt baseline's frame/file caps; it does
    not admit 48-hour material or any automatic timeline edits.
    """
    def __init__(self, path, receipt, *, enabled=False, limits=None, idle_seconds=5.0, cancel=None):
        # Reuse immutable receipt/format/recipe validation, not its per-read replay.
        validated = SequentialDecodedReader(path, receipt, enabled=enabled, limits=limits)
        if type(idle_seconds) not in (int, float) or not 0 < idle_seconds <= validated.limits.wall_seconds:
            raise ValueError('Idle lease must be finite, positive, and no greater than session wall budget')
        for wrapper in ('/usr/bin/taskset', '/usr/bin/prlimit'):
            if not Path(wrapper).is_file() or not os.access(wrapper, os.X_OK):
                raise UnsupportedAudio('Existing taskset/prlimit wrappers required; no installation attempted')
        self.receipt, self.limits = receipt, validated.limits
        self.session_id = uuid.uuid4().hex
        self._window_key = secrets.token_bytes(32)
        self.frame_bytes = receipt.channels * 2
        self.ring_capacity = receipt.sample_rate * 2 * self.frame_bytes
        self.max_read_frames = min(receipt.sample_rate, self.limits.max_read_frames)
        self.ring = bytearray()
        self.ring_start = self.consumed_end = 0
        self.max_ring_bytes = self.max_queued_bytes = 0
        self.decoder_invocations = 0
        self.compressed_and_recipe_identity_verified = False
        self._queue = queue.Queue(maxsize=1)
        # One handoff is queued OR being copied by the consumer. The other
        # bounded chunk is the producer's fixed readv buffer.
        self._handoff_slot = threading.Semaphore(1)
        self._cancel_event = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._consumer_lock = threading.Lock()
        self._state = 'initializing'
        self._failure = self._completion = None
        self._idle_expired = False
        self._idle_seconds, self._external_cancel = idle_seconds, cancel
        self._last_activity = time.monotonic()
        if not _ACTIVE_SESSION.acquire(blocking=False):
            raise RuntimeError('Only one active rolling decoder session per process')
        self._owns_session = True
        self._source = self._child = self._thread = None
        try:
            self._source = _RollingSource(path, self.limits, cancel)
            if self._source.hash != receipt.compressed_sha256 or self._source.signature[2] != receipt.compressed_bytes:
                raise SourceChanged('Compressed source identity differs from receipt')
            self._kind = 'ogg-opus' if receipt.decoder.codec == 'opus' else 'mp4-aac'
            stream, rate, channels = _safe_probe(self._source.file.fileno(), self._kind, self._source.guard)
            if (rate, channels, stream['codec_name']) != (receipt.sample_rate, receipt.channels, receipt.decoder.codec):
                raise SourceChanged('Receipt native PCM format differs from source')
            self._source.verify()
            self.compressed_and_recipe_identity_verified = True
            self._last_activity = time.monotonic()
            self._source.guard.cancel = self._cancel_requested
            self._child = subprocess.Popen(_decoder_command(self._source.file.fileno(), self._kind, self.limits),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            pass_fds=(self._source.file.fileno(),), bufsize=0)
            self.decoder_invocations = 1
            self._state = 'open-provisional'
            self._thread = threading.Thread(target=self._pump, name='bounded-rolling-decode', daemon=True)
            self._thread.start()
        except BaseException:
            if self._child is not None:
                with suppress(ProcessLookupError):
                    self._child.kill()
                self._child.wait()
                self._child.stdout.close()
                self._child.stderr.close()
            if self._source is not None:
                self._source.close()
            self._release_session()
            raise

    def _release_session(self):
        if self._owns_session:
            self._owns_session = False
            _ACTIVE_SESSION.release()

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def completed_receipt(self):
        with self._lock:
            return self._completion

    @property
    def failure(self):
        with self._lock:
            return self._failure

    @property
    def decoder_pid(self):
        return self._child.pid

    @property
    def decoder_reaped(self):
        return self._done.is_set() and self._child.poll() is not None

    def _touch(self):
        self._last_activity = time.monotonic()

    def _cancel_requested(self):
        if self._cancel_event.is_set():
            return True
        if self._external_cancel is not None and self._external_cancel():
            return True
        if time.monotonic() - self._last_activity > self._idle_seconds:
            self._idle_expired = True
            return True
        return False

    def _check_live(self):
        self._source.guard.check(self._child)

    def _pump(self):
        """Producer owns child/descriptor cleanup, even during an idle consumer."""
        child = self._child
        selector = selectors.DefaultSelector()
        digest = hashlib.sha256()
        emitted = 0
        buffer = bytearray(CHUNK)
        pending = 0
        errors = bytearray()
        success = None
        failure = None
        try:
            for stream in (child.stdout, child.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                self._check_live()
                for key, _ in selector.select(.025):
                    if key.fileobj is child.stderr:
                        block = os.read(key.fd, 8192)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        if len(errors) + len(block) > 8192:
                            raise DecodeLimit('Decoder diagnostic metadata limit')
                        errors.extend(block)
                        continue
                    if not self._handoff_slot.acquire(timeout=.025):
                        # Retain at most one queued/consumer-held handoff. The
                        # bounded stdout pipe supplies the remaining backpressure.
                        continue
                    try:
                        count = os.readv(key.fd, [memoryview(buffer)[pending:]])
                    except BlockingIOError:
                        self._handoff_slot.release()
                        continue
                    if not count:
                        self._handoff_slot.release()
                        selector.unregister(key.fileobj)
                        continue
                    total = pending + count
                    complete = total // self.frame_bytes * self.frame_bytes
                    pending = total - complete
                    if not complete:
                        self._handoff_slot.release()
                        continue
                    next_emitted = emitted + complete // self.frame_bytes
                    if next_emitted > min(self.receipt.emitted_frames, self.limits.max_frames):
                        self._handoff_slot.release()
                        raise DecodeLimit('Rolling decoder emitted-frame limit')
                    view = memoryview(buffer)[:complete]
                    digest.update(view)
                    self._queue.put_nowait((emitted, bytes(view)))
                    self.max_queued_bytes = max(self.max_queued_bytes, complete)
                    if pending:
                        buffer[:pending] = buffer[complete:total]
                    emitted = next_emitted
            while child.poll() is None:
                self._check_live()
                time.sleep(.01)
            if child.returncode or errors:
                raise UnsupportedAudio('Decoder failed or reported errors: ' + bytes(errors).decode(errors='replace')[:1000])
            if pending:
                raise UnsupportedAudio('Partial emitted PCM frame')
            self._source.verify(rehash=True)
            actual = digest.hexdigest()
            if emitted != self.receipt.emitted_frames or actual != self.receipt.emitted_pcm_sha256:
                raise SourceChanged('Full rolling emitted count/hash differs from receipt; provisional stream invalidated')
            success = CompletedDecode(self.session_id, self.receipt.sha256, self.receipt.compressed_sha256,
                                      actual, emitted, self.receipt.sample_rate, self.receipt.channels)
        except BaseException as exc:
            failure = DecodeLimit('Idle session lease expired; provisional stream invalidated') if self._idle_expired else exc
        finally:
            selector.close()
            if child.poll() is None:
                with suppress(ProcessLookupError):
                    child.kill()
            child.wait()
            child.stdout.close()
            child.stderr.close()
            with self._lock:
                self._source.close()
                if self._state == 'open-provisional':
                    if success is not None and not self._cancel_event.is_set():
                        self._completion = success
                        self._state = 'verified-complete'
                    else:
                        self._failure = failure or DecodeCancelled('Session cancelled before final verification')
                        self._state = 'cancelled' if isinstance(self._failure, DecodeCancelled) else 'failed-invalidated'
                # A concurrent explicit close/cancel/failure cannot be changed to success.
                elif self._state != 'verified-complete' and self._failure is None:
                    self._failure = failure
                self._release_session()
                self._done.set()

    def _raise_if_failed(self):
        with self._lock:
            state, failure = self._state, self._failure
        if state not in ('open-provisional', 'verified-complete'):
            raise failure or DecodeCancelled(f'Session is {state}; provisional chunks are not fully verified')

    def _invalidate(self, exc):
        with self._lock:
            if self._state == 'open-provisional':
                self._state, self._failure = 'failed-invalidated', exc
        self._cancel_event.set()
        self._join()

    def _receive(self):
        while True:
            self._touch()
            self._raise_if_failed()
            try:
                start, block = self._queue.get(timeout=.025)
                if start != self.consumed_end or len(block) % self.frame_bytes:
                    raise SourceChanged('Rolling output order/frame alignment differs')
                try:
                    excess = max(0, len(self.ring) + len(block) - self.ring_capacity)
                    if excess:
                        del self.ring[:excess]
                    self.ring.extend(block)
                    self.consumed_end += len(block) // self.frame_bytes
                    self.ring_start = self.consumed_end - len(self.ring) // self.frame_bytes
                    self.max_ring_bytes = max(self.max_ring_bytes, len(self.ring))
                finally:
                    del block
                    self._handoff_slot.release()
                self._raise_if_failed()
                return True
            except queue.Empty:
                if self._done.is_set():
                    self._raise_if_failed()
                    return False

    def read(self, start, end):
        """Return provisional bytes; stale backward cache misses never silently seek."""
        if (type(start) is not int or type(end) is not int or
                not 0 <= start < end <= self.receipt.emitted_frames or end - start > self.max_read_frames):
            raise ValueError('Read must be <=one second of half-open native emitted frames')
        if not self._consumer_lock.acquire(blocking=False):
            raise RuntimeError('Only one read/finish consumer is supported')
        try:
            self._touch()
            self._raise_if_failed()
            if start < self.ring_start:
                raise ReplayRequired(start, self.ring_start, self.consumed_end)
            with self._lock:
                if self._state == 'open-provisional':
                    # Signature checks do not seek the producer descriptor.
                    # The same lock excludes final descriptor closure.
                    self._source.verify()
            while self.consumed_end < end:
                if not self._receive():
                    raise SourceChanged('Short rolling stream')
            lo = (start - self.ring_start) * self.frame_bytes
            hi = (end - self.ring_start) * self.frame_bytes
            if lo < 0 or hi > len(self.ring):
                raise SourceChanged('Requested window escaped bounded ring')
            pcm = bytes(memoryview(self.ring)[lo:hi])
            result = ProvisionalPCMWindow(self.session_id, start, end, pcm,
                                          integrity_token=self._window_token(start, end, pcm))
            self._raise_if_failed()
            return result
        except ReplayRequired:
            raise
        except BaseException as exc:
            self._invalidate(exc)
            raise
        finally:
            self._consumer_lock.release()

    def _window_token(self, start, end, pcm):
        token = hmac.new(self._window_key, self.session_id.encode() + struct.pack('<QQ', start, end), hashlib.sha256)
        token.update(pcm)
        return token.hexdigest()

    def is_verified_window(self, window, completion):
        """In-process pairing; copied/forged/stale completion records are not authority.

        No issued-window ledger grows with duration. The bounded window token
        also rejects altered bytes/coordinates. This is not a security boundary
        against hostile code with direct access to this reader's internals.
        """
        with self._lock:
            valid_completion = self._state == 'verified-complete' and completion is self._completion and completion is not None
        if not valid_completion or type(window) is not ProvisionalPCMWindow:
            return False
        if (window.session_id != self.session_id or type(window.start) is not int or type(window.end) is not int or
                not 0 <= window.start < window.end <= self.receipt.emitted_frames or
                window.end - window.start > self.max_read_frames or type(window.pcm) is not bytes or
                len(window.pcm) != (window.end - window.start) * self.frame_bytes or
                type(window.integrity_token) is not str or len(window.integrity_token) != 64 or
                any(c not in '0123456789abcdef' for c in window.integrity_token) or
                window.coordinate_space != 'decoder-emitted-pcm16le-v1' or
                window.verification != 'provisional-until-this-session-completes'):
            return False
        return hmac.compare_digest(window.integrity_token, self._window_token(window.start, window.end, window.pcm))

    def finish(self):
        """Drain through EOS and return the only authoritative session completion."""
        if not self._consumer_lock.acquire(blocking=False):
            raise RuntimeError('Only one read/finish consumer is supported')
        try:
            while self._receive():
                pass
            if self.completed_receipt is None:
                raise SourceChanged('Session did not verify to completion')
            return self.completed_receipt
        except BaseException as exc:
            self._invalidate(exc)
            raise
        finally:
            self._consumer_lock.release()

    def _join(self):
        if self._child.poll() is None:
            with suppress(ProcessLookupError):
                self._child.kill()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise RuntimeError('Decoder cleanup did not finish within two seconds')

    def cancel(self):
        with self._lock:
            if self._state == 'open-provisional':
                self._state = 'cancelled'
                self._failure = DecodeCancelled('Explicit cancellation invalidates provisional stream')
        self._cancel_event.set()
        self._join()

    def close(self):
        with self._lock:
            if self._state == 'open-provisional':
                self._state = 'closed-unverified'
                self._failure = DecodeCancelled('Closed before EOS verification; provisional stream invalidated')
        self._cancel_event.set()
        self._join()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
