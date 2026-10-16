"""Opt-in immutable-file ingestion. Client journal, not server provenance.

Run ``python -m audio_backend.recording_uploader --help``. No worker/model defaults
are changed. One continuous decode per original, one page on disk, one request.
Keep legacy --final, or use bounded --append followed by explicit --finalize.
"""
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlsplit
import uuid
import wave

from .fragment_core import SourceChanged, inspect_pcm16_source
from .resources import InstanceLock, tree_rss

RATE = 16000
READ_BYTES = 65536
SOURCE_FRAMES = 120 * RATE
PAGE_FRAMES = 28 * 168000
PAGE_BYTES = PAGE_FRAMES * 2 + 3 * 44
JSON_BYTES = 512 * 1024
STDERR_BYTES = 8192
POLICY = 'finalized-file-mono16k-v1'
FORMAT = {'sample_rate': RATE, 'channels': 1, 'sample_width': 2, 'encoding': 'pcm_s16le'}


class Incomplete(RuntimeError):
    """Published prefix is retained; no successful final-file claim is made."""


class HTTPFailure(Incomplete):
    def __init__(self, status, detail='', retry_after=1):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f'HTTP {status}: {detail[:1000]}')


class Uncertain(Incomplete):
    """A request may have reached the server; reconcile before repeating it."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _parse(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Incomplete('Duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        raise Incomplete('Nonfinite JSON value')
    try:
        result = json.loads(data, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, UnicodeError) as exc:
        raise Incomplete('Invalid bounded JSON') from exc
    if not isinstance(result, dict):
        raise Incomplete('Expected JSON object')
    return result


def _signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _hash(stream, check=lambda: None):
    digest = hashlib.sha256()
    stream.seek(0)
    while block := stream.read(READ_BYTES):
        check(); digest.update(block)
    stream.seek(0)
    return digest.hexdigest()


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try: os.fsync(fd)
    finally: os.close(fd)


@dataclass(frozen=True)
class Limits:
    input_bytes: int = 8 * 1024**3
    output_frames: int = 2 * 3600 * RATE
    spool_bytes: int = 12 * 1024**2
    disk_floor: int = 11 * 1024**3 // 2
    rss_bytes: int = 512 * 1024**2
    deadline_seconds: float = 12 * 3600
    http_seconds: float = 30
    poll_seconds: float = 1

    def __post_init__(self):
        import math
        for name, value in vars(self).items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f'{name} must be finite')
            if value < 0 or (not value and name != 'disk_floor'):
                raise ValueError(f'{name} must be positive')
        for name in ('input_bytes', 'output_frames', 'spool_bytes', 'disk_floor', 'rss_bytes'):
            if type(getattr(self, name)) is not int: raise ValueError(f'{name} must be integer')
        if self.output_frames > 48 * 3600 * RATE:
            raise ValueError('This finite journal supports at most 48 emitted hours')
        if self.input_bytes > 64 * 1024**3:
            raise ValueError('Original byte capacity exceeds 64 GiB')
        if self.spool_bytes < PAGE_BYTES + 2 * JSON_BYTES + 65536:
            raise ValueError('Spool budget cannot reserve one page and atomic metadata')


class Guard:
    def __init__(self, limits, cancel=None):
        self.limits = limits
        self.cancel = cancel or (lambda: False)
        self.end = time.monotonic() + limits.deadline_seconds
        self.next_memory = 0
        self.peak_rss = 0

    def check(self):
        if self.cancel(): raise InterruptedError('Uploader cancelled; capture prefix retained')
        now = time.monotonic()
        if now >= self.end: raise Incomplete('Uploader deadline reached; capture prefix retained')
        if now >= self.next_memory:
            rss = tree_rss(); self.peak_rss = max(self.peak_rss, rss)
            self.next_memory = now + .1
            if rss > self.limits.rss_bytes: raise Incomplete('Uploader process-tree RSS guard reached')

    def pause(self, seconds):
        end = time.monotonic() + max(.01, seconds)
        while time.monotonic() < end:
            self.check(); time.sleep(min(.1, max(0, end - time.monotonic())))


class HTTPTransport:
    """One bounded request. Credentials exist only in memory and HTTP headers."""
    def __init__(self, base, guard, token=None):
        parts = urlsplit(base)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment or parts.path not in ('', '/')):
            raise ValueError('API must be an HTTP(S) origin without credentials/path/query')
        if parts.scheme == 'http' and parts.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Non-loopback API requires HTTPS')
        self.base = base.rstrip('/')
        self.parts, self.guard = parts, guard
        self.token = os.environ.get('AUDIO_API_KEY') if token is None else token
        self.max_read = self.max_send = 0

    def request(self, method, path, body=None, file=None, key=None):
        self.guard.check()
        cls = http.client.HTTPSConnection if self.parts.scheme == 'https' else http.client.HTTPConnection
        conn = cls(self.parts.hostname, self.parts.port,
                   timeout=min(self.guard.limits.http_seconds, max(.01, self.guard.end-time.monotonic())))
        request_end = min(self.guard.end, time.monotonic() + self.guard.limits.http_seconds)
        def operation():
            self.guard.check()
            remaining = request_end - time.monotonic()
            if remaining <= 0: raise Uncertain('HTTP operation deadline reached')
            if conn.sock is not None: conn.sock.settimeout(remaining)
        headers = {'Accept': 'application/json'}
        if self.token: headers['Authorization'] = 'Bearer ' + self.token
        if key: headers['Idempotency-Key'] = key
        payload = _json(body) if body is not None else b''
        if len(payload) > JSON_BYTES: raise Incomplete('Request JSON exceeds bound')
        headers['Content-Length'] = str(file.stat().st_size if file else len(payload))
        if body is not None: headers['Content-Type'] = 'application/json'
        if file: headers['Content-Type'] = 'audio/wav'
        try:
            conn.putrequest(method, path)
            for name, value in headers.items(): conn.putheader(name, value)
            conn.endheaders()
            if file:
                with file.open('rb') as stream:
                    while block := stream.read(READ_BYTES):
                        operation(); self.max_send = max(self.max_send, len(block)); conn.send(block)
            elif payload: conn.send(payload)
            operation()
            response = conn.getresponse()
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                raise Incomplete('Encoded HTTP responses are unsupported')
            declared = response.getheader('Content-Length')
            if declared is not None and (not declared.isdecimal() or int(declared) > JSON_BYTES):
                raise Incomplete('HTTP response exceeds bound')
            data = bytearray()
            while True:
                operation()
                size = min(READ_BYTES, JSON_BYTES + 1 - len(data))
                self.max_read = max(self.max_read, size)
                block = response.read1(size)
                if not block: break
                data.extend(block)
                if len(data) > JSON_BYTES: raise Incomplete('HTTP response exceeds bound')
            if not 200 <= response.status < 300:
                retry = response.getheader('Retry-After', '1')
                # Numeric Retry-After is bounded by the invocation deadline; dates use one second.
                retry = float(retry) if retry.isdecimal() else 1
                raise HTTPFailure(response.status, data.decode(errors='replace'), retry)
            return _parse(data)
        except (OSError, http.client.HTTPException) as exc:
            raise Uncertain('HTTP transport interrupted; reconcile persisted request') from exc
        finally:
            conn.close()


class Original:
    def __init__(self, path, guard):
        self.path = Path(os.path.abspath(path))
        self.guard = guard
        if self.path.is_symlink(): raise ValueError('Input must be a regular local file, not a symlink')
        fd = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        self.file = os.fdopen(fd, 'rb', buffering=0)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= guard.limits.input_bytes:
                raise ValueError('Input must be a nonempty bounded finalized regular file')
            self.identity = {'path': str(self.path), 'signature': _signature(info), 'sha256': _hash(self.file, guard.check)}
            self.verify()
        except BaseException:
            self.file.close(); raise

    def verify(self, rehash=False):
        if (_signature(os.fstat(self.file.fileno())) != self.identity['signature']
                or self.path.is_symlink() or _signature(self.path.stat()) != self.identity['signature']):
            raise SourceChanged('Original descriptor/path identity changed')
        if rehash and _hash(self.file, self.guard.check) != self.identity['sha256']:
            raise SourceChanged('Original file bytes changed')

    def close(self): self.file.close()


def _collect(command, guard, fd=None, cap=65536):
    """Finite metadata subprocess, always positively waited; no communicate()."""
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, pass_fds=(() if fd is None else (fd,)), bufsize=0)
    selector = selectors.DefaultSelector(); output = bytearray(); errors = bytearray()
    local_end = min(guard.end, time.monotonic() + 30)
    def check():
        guard.check()
        if time.monotonic() >= local_end: raise Incomplete('Decoder metadata deadline reached')
    try:
        for stream in (child.stdout, child.stderr):
            os.set_blocking(stream.fileno(), False); selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            check()
            for key, _ in selector.select(.1):
                block = os.read(key.fd, min(READ_BYTES, 8192))
                if not block: selector.unregister(key.fileobj); continue
                target, maximum = (output, cap) if key.fileobj is child.stdout else (errors, STDERR_BYTES)
                if len(target) + len(block) > maximum: raise Incomplete('Decoder metadata exceeds finite bound')
                target.extend(block)
        while child.poll() is None:
            check(); guard.pause(.02)
        if child.returncode: raise Incomplete('Decoder metadata tool failed: ' + errors.decode(errors='replace')[:1000])
        return bytes(output)
    finally:
        selector.close()
        if child.poll() is None: child.kill()
        child.wait(); child.stdout.close(); child.stderr.close()


def decoder_recipe(original, guard, ffmpeg='/usr/bin/ffmpeg', ffprobe='/usr/bin/ffprobe'):
    original.file.seek(0)
    try:
        with wave.open(original.file, 'rb') as stream:
            if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getcomptype()) == (RATE, 1, 2, 'NONE'):
                if stream.getnframes() <= 0: raise Incomplete('Empty PCM file')
                return {'kind': 'canonical-wav', 'policy': POLICY, 'format': FORMAT, 'declared_frames': stream.getnframes()}
    except (wave.Error, EOFError):
        pass
    finally: original.file.seek(0)
    if os.name != 'posix' or not Path('/proc/self/fd').exists():
        raise Incomplete('Compressed normalization currently requires POSIX /proc seekable descriptors')
    demuxer = {'.wav': 'wav', '.flac': 'flac', '.mp3': 'mp3', '.m4a': 'mov', '.ogg': 'ogg', '.opus': 'ogg'}.get(original.path.suffix.lower())
    if demuxer is None: raise Incomplete('Unsupported local container; use WAV, FLAC, MP3, M4A, Ogg/Opus')
    identities = {}
    for name, executable in (('ffmpeg', ffmpeg), ('ffprobe', ffprobe)):
        executable = str(Path(executable).resolve(strict=True))
        with open(executable, 'rb') as stream: digest = _hash(stream, guard.check)
        version = _collect([executable, '-version'], guard)
        identities[name] = {'path': executable, 'sha256': digest, 'build_sha256': hashlib.sha256(version).hexdigest()}
    source = f'/proc/self/fd/{original.file.fileno()}'
    probe = _parse(_collect([identities['ffprobe']['path'], '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-f', demuxer, '-show_entries', 'stream=index,codec_type,codec_name', '-of', 'json', source], guard, original.file.fileno()))
    streams = probe.get('streams', [])
    if not isinstance(streams, list) or len(streams) > 32: raise Incomplete('Ambiguous/excessive input streams')
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    codecs = {'wav': {'pcm_s16le', 'pcm_s24le', 'pcm_s32le', 'pcm_f32le', 'pcm_f64le', 'pcm_u8'},
              'flac': {'flac'}, 'mp3': {'mp3'}, 'mov': {'aac'}, 'ogg': {'opus'}}
    if len(audio) != 1 or audio[0].get('codec_name') not in codecs[demuxer]:
        raise Incomplete('Require exactly one supported audio stream in a self-contained container')
    options = ['-nostdin', '-hide_banner', '-v', 'error', '-xerror', '-threads', '1',
               '-filter_threads', '1', '-filter_complex_threads', '1', '-protocol_whitelist', 'file,pipe',
               '-f', demuxer, '-i', '{held_fd}', '-map', f"0:{audio[0]['index']}", '-vn', '-sn', '-dn',
               '-ac', '1', '-ar', str(RATE), '-af', 'aresample=16000:async=0',
               '-c:a', 'pcm_s16le', '-threads', '1', '-f', 's16le', 'pipe:1']
    return {'kind': 'ffmpeg', 'policy': POLICY, 'format': FORMAT, 'tools': identities,
            'stream': audio[0], 'arguments': options, 'coordinates': 'normalized-emitted-frames'}


class PCMReader:
    def __init__(self, original, recipe, guard):
        self.original, self.recipe, self.guard = original, recipe, guard
        self.frames = self.max_read = 0; self.clean = False; self.pending = b''
        self.child = self.wav = self.selector = self.stderr_thread = None
        self.stderr = bytearray(); self.stderr_error = None
        try:
            original.file.seek(0)
            if recipe['kind'] == 'canonical-wav':
                self.wav = wave.open(original.file, 'rb')
            else:
                command = [recipe['tools']['ffmpeg']['path']] + [f'/proc/self/fd/{original.file.fileno()}' if a == '{held_fd}' else a for a in recipe['arguments']]
                self.child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, pass_fds=(original.file.fileno(),), bufsize=0,
                    env={**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'})
                self.selector = selectors.DefaultSelector()
                os.set_blocking(self.child.stdout.fileno(), False)
                self.selector.register(self.child.stdout, selectors.EVENT_READ)
                self.stderr_thread = threading.Thread(target=self._errors, name='uploader-stderr', daemon=True)
                self.stderr_thread.start()
        except BaseException:
            self.close(); raise

    def _errors(self):
        try:
            while block := self.child.stderr.read(8192):
                self.stderr.extend(block)
                if len(self.stderr) > STDERR_BYTES: del self.stderr[:-STDERR_BYTES]
        except OSError as exc: self.stderr_error = exc

    def read(self, frames):
        if not 0 < frames <= READ_BYTES // 2: raise ValueError('PCM read exceeds 64 KiB')
        self.guard.check(); requested = frames * 2
        self.max_read = max(self.max_read, requested)
        if self.clean: return b''
        if self.wav:
            data = self.wav.readframes(frames)
            if len(data) % 2: raise Incomplete('Odd PCM byte at WAV EOF')
            if not data:
                if self.frames != self.recipe['declared_frames']: raise Incomplete('Truncated WAV payload')
                self.clean = True
        else:
            data = self.pending; self.pending = b''
            while len(data) < 2:
                self.guard.check()
                if not self.selector.select(.1): continue
                block = os.read(self.child.stdout.fileno(), requested-len(data))
                if block:
                    data += block
                    continue
                while self.child.poll() is None: self.guard.pause(.02)
                self.child.wait()
                self.stderr_thread.join(timeout=2)
                if self.stderr_thread.is_alive(): raise Incomplete('Decoder stderr did not close')
                if self.child.returncode or self.stderr_error:
                    raise Incomplete('Decoder failed; no EOS: ' + bytes(self.stderr).decode(errors='replace')[-1000:])
                if data: raise Incomplete('Odd trailing PCM byte from decoder')
                self.clean = True; break
            if len(data) % 2: self.pending, data = data[-1:], data[:-1]
        self.frames += len(data) // 2
        if self.frames > self.guard.limits.output_frames: raise Incomplete('Decoded output frame limit reached')
        return data

    def close(self):
        if self.wav is not None: self.wav.close()
        if self.child is not None:
            if self.child.poll() is None: self.child.kill()
            self.child.wait()
            if self.stderr_thread is not None: self.stderr_thread.join(timeout=3)
            self.child.stdout.close(); self.child.stderr.close()
            if self.stderr_thread is not None and self.stderr_thread.is_alive():
                raise Incomplete('Decoder stderr thread failed to reap')
        if self.selector is not None: self.selector.close()


class Journal:
    def __init__(self, directory, guard):
        self.path = Path(directory).absolute(); self.path.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink(): raise Incomplete('State directory cannot be a symlink')
        self.guard = guard; self.lock = InstanceLock(self.path / 'uploader.lock')
        self.data = None; self.max_spool = 0

    def load(self):
        path = self.path / 'uploader.json'
        if not path.exists(): return None
        if path.is_symlink() or path.stat().st_size > JSON_BYTES: raise Incomplete('Unsafe/oversized journal')
        with path.open('rb') as stream: raw = stream.read(JSON_BYTES + 1)
        if len(raw) > JSON_BYTES: raise Incomplete('Journal exceeds bound')
        self.data = _parse(raw)
        if self.data.get('policy') != POLICY or not re.fullmatch('[a-f0-9]{32}', self.data.get('job', '')):
            raise Incomplete('Journal policy/job mismatch')
        if not isinstance(self.data.get('pages'), list) or len(self.data['pages']) > 25:
            raise Incomplete('Journal page bound')
        for name in self.data.get('spool', []): self.owned(name)
        return self.data

    def owned(self, name):
        if not isinstance(name, str) or not re.fullmatch(re.escape(self.data['job']) + r'-p[0-9]{1,3}-s[0-9]\.wav(?:\.part)?', name):
            raise Incomplete('Journal contains an unowned spool name')
        path = self.path / name
        if path.is_symlink(): raise Incomplete('Owned spool cannot be a symlink')
        return path

    def save(self):
        self.budget()
        raw = _json(self.data)
        if len(raw) > JSON_BYTES: raise Incomplete('Journal exceeds finite bound')
        tmp = self.path / 'uploader.json.next'
        # Only this exact atomic replacement file belongs to the single locked writer.
        if tmp.is_symlink(): raise Incomplete('Unsafe atomic journal path')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, self.path / 'uploader.json'); _fsync_dir(self.path)

    def budget(self, reserve=0):
        self.guard.check()
        allocated = 0; wav_bytes = 0
        for name in self.data.get('spool', []):
            path = self.owned(name)
            if path.exists():
                info = path.stat()
                if not stat.S_ISREG(info.st_mode): raise Incomplete('Nonregular owned spool')
                allocated += max(info.st_size, info.st_blocks * 512); wav_bytes += info.st_size
        self.max_spool = max(self.max_spool, wav_bytes)
        if allocated + reserve + 2 * JSON_BYTES + 65536 > self.guard.limits.spool_bytes:
            raise Incomplete('One-page allocated-byte spool budget reached')
        if shutil.disk_usage(self.path).free < self.guard.limits.disk_floor + reserve + 2 * JSON_BYTES:
            raise Incomplete('Disk safety reservation reached; retained incomplete prefix')

    def cleanup(self):
        for name in self.data.get('spool', []): self.owned(name).unlink(missing_ok=True)
        self.data['spool'] = []; self.save()


class Uploader:
    def __init__(self, *, input, session_id, capture_id, capture_origin_frame, state_dir,
                 final, api='http://127.0.0.1:8765', limits=None, cancel=None, transport=None,
                 ffmpeg='/usr/bin/ffmpeg', ffprobe='/usr/bin/ffprobe'):
        if final is not True: raise ValueError('First slice requires one --final file; continuation is unsupported')
        if not isinstance(input, (str, os.PathLike)): raise ValueError('Exactly one input file is required')
        for value in (session_id, capture_id):
            if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value) is None:
                raise ValueError('Explicit bounded session and capture IDs are required')
        if type(capture_origin_frame) is not int or not 0 <= capture_origin_frame < (1 << 53) - 2 * 3600 * RATE:
            raise ValueError('Explicit integer capture origin is required')
        self.limits = limits or Limits()
        if self.limits.output_frames > 2 * 3600 * RATE:
            raise ValueError('Legacy journals support at most two emitted hours')
        self.guard = Guard(self.limits, cancel)
        self.http = transport or HTTPTransport(api, self.guard)
        self.api = api.rstrip('/'); self.input = input; self.origin = capture_origin_frame
        self.sid = session_id; self.capture = capture_id
        self.base = '/sessions/' + quote(session_id, safe='') + '/recordings/' + quote(capture_id, safe='')
        source_path, state_path = Path(input).resolve(strict=True), Path(state_dir).resolve()
        if source_path.is_relative_to(state_path): raise ValueError('Original input must be outside the state directory')
        original_stat = source_path.stat()
        for name in ('uploader.lock', 'uploader.json', 'uploader.json.next'):
            path = state_path / name
            if os.path.lexists(path):
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Reserved state control path must be a single-link regular file')
                if (info.st_dev, info.st_ino) == (original_stat.st_dev, original_stat.st_ino):
                    raise ValueError('Original aliases a reserved state control file')
        if (state_path / 'uploader.json.next').exists() and not (state_path / 'uploader.json').exists():
            raise ValueError('Unowned atomic journal file exists without a journal')
        self.journal = Journal(state_dir, self.guard); self.ffmpeg = ffmpeg; self.ffprobe = ffprobe
        self.reader = None; self.source = None

    def _retry(self, exc):
        if isinstance(exc, Uncertain): self.guard.pause(self.limits.poll_seconds); return
        if isinstance(exc, HTTPFailure) and (exc.status == 429 or 500 <= exc.status < 507):
            self.guard.pause(max(self.limits.poll_seconds, exc.retry_after)); return
        raise exc

    def get(self, path, missing=False):
        while True:
            self.guard.check()
            try: return self.http.request('GET', path)
            except HTTPFailure as exc:
                if missing and exc.status == 404: return None
                self._retry(exc)
            except Uncertain as exc: self._retry(exc)

    def _page_path(self, number): return self.base + '/pages/' + str(number)

    def _match(self, row, remote):
        if remote['page_number'] != row['number'] or remote['capture_id'] != self.capture or remote['capture_origin_frame'] != self.origin:
            raise Incomplete('Server page identity mismatch')
        if row.get('server_id') and row['server_id'] != remote['id']: raise Incomplete('Server page ID changed')
        row['server_id'] = remote['id']
        local = {p['key']: p for p in row['pieces']}
        if len(remote['sources']) > 3: raise Incomplete('Unexpected server source set')
        remote_keys = {source['idempotency_key'] for source in remote['sources']}
        if any(p.get('server_id') and key not in remote_keys for key, p in local.items()):
            raise Incomplete('Previously accepted server source disappeared')
        for source in remote['sources']:
            p = local.get(source['idempotency_key'])
            if p is None or (p['digest'], p['frames'], p['bytes'], p['capture_start']) != (source['digest'], source['frames'], source['bytes'], source['source_start_frame']):
                raise Incomplete('Server source bytes/key/placement mismatch')
            if p.get('server_id') and p['server_id'] != source['id']: raise Incomplete('Server source ID changed')
            p['server_id'] = source['id']
        if remote['state'] not in ('open', 'sealed', 'preparing', 'pending_receipts', 'published'):
            raise Incomplete('Server page blocked: ' + str(remote['state']))
        if remote['released']:
            seal = row.get('seal')
            if not seal or remote['state'] != 'published' or remote['end_frame'] != seal['end_frame'] or remote['eos'] != seal['eos']:
                raise Incomplete('Unexpected server publication')
            if len(remote['sources']) != len(row['pieces']): raise Incomplete('Published page has missing sources')
        return remote

    def _saved_pages(self):
        for row in self.journal.data['pages']:
            remote = self.get(self._page_path(row['number']), missing=True)
            if remote is None:
                if row.get('server_id') or row.get('released'): raise Incomplete('Previously accepted server page disappeared')
            else:
                self._match(row, remote)
                if row.get('released') and not remote['released']: raise Incomplete('Published server page lost release')
        self.journal.save()

    def _create(self, row):
        path = self._page_path(row['number'])
        while True:
            remote = self.get(path, missing=True)
            if remote is not None:
                self._match(row, remote); self.journal.save(); return remote
            body = {'page_number': row['number']}
            if row['number'] == 1: body['capture_origin_frame'] = self.origin
            row['create'] = body; self.journal.save()
            try:
                remote = self.http.request('POST', self.base + '/pages', body=body)
                self._match(row, remote); self.journal.save(); return remote
            except (HTTPFailure, Uncertain) as exc: self._retry(exc)

    def _put(self, row, piece):
        path = self._page_path(row['number'])
        while True:
            remote = self._match(row, self.get(path))
            if any(s['idempotency_key'] == piece['key'] for s in remote['sources']):
                self.journal.save(); return
            if remote['state'] != 'open': raise Incomplete('Server source missing from frozen page')
            self.journal.save()
            try:
                reply = self.http.request('PUT', path + '/sources?source_start_frame=' + str(piece['capture_start']),
                    file=self.journal.owned(piece['name']), key=piece['key'])
                self._match(row, reply); self.journal.save()
            except (HTTPFailure, Uncertain) as exc: self._retry(exc)

    def _blocked_worker(self):
        # Legacy v1/v2 retains its original finite scan policy.
        cursor = 0
        for _ in range(3):
            result = self.get('/sessions/' + self.sid + '/chunks?after=' + str(cursor) + '&limit=500')
            for chunk in result['chunks']:
                if chunk['status'] in ('failed', 'paused', 'cancelled'):
                    raise Incomplete('Admission blocked by worker ' + chunk['status'] + ': ' + chunk['id'])
            if len(result['chunks']) < 500: return
            cursor = result['next_cursor']
        raise Incomplete('Unexpected session chunk bound')

    def _seal(self, row, end, eos):
        path = self._page_path(row['number'])
        remote = self._match(row, self.get(path))
        if not row.get('seal'):
            row['seal'] = {'idempotency_key': self.journal.data['job'] + '.seal.' + str(row['number']),
                'expected_revision': remote['revision'], 'end_frame': self.origin + end, 'eos': eos}
            self.journal.save()
        if (row['seal']['end_frame'], row['seal']['eos']) != (self.origin + end, eos):
            raise Incomplete('Frozen seal differs from replayed page')
        while True:
            remote = self._match(row, self.get(path))
            if remote['state'] == 'preparing':
                self.guard.pause(self.limits.poll_seconds); continue
            try:
                # GET omits the frozen key/revision. Exact replay verifies their identity,
                # including a published page whose original response was lost.
                remote = self.http.request('POST', path + '/seal', body=row['seal'])
                self._match(row, remote)
                if remote['released']:
                    row['released'] = True
                    self.journal.data['published_frames'] = max(end, self.journal.data['published_frames'])
                    self.journal.save(); return
                self._blocked_worker(); self.guard.pause(self.limits.poll_seconds)
            except HTTPFailure as exc:
                if exc.status == 429: self._blocked_worker()
                self._retry(exc)
            except Uncertain as exc: self._retry(exc)

    def _finish(self):
        data = self.journal.data
        status = self.get(self.base)
        if not data.get('finish'):
            if status['eos']: raise Incomplete('Capture unexpectedly finalized without saved request')
            data['finish'] = {'idempotency_key': data['job'] + '.finish', 'expected_revision': status['revision']}
            self.journal.save()
        while True:
            # Replay the exact idempotent request even if GET says EOS: verifies the saved finish identity.
            try:
                status = self.http.request('POST', self.base + '/finish', body=data['finish'])
                if not status['eos'] or status['capture_end_frame'] != self.origin + data['emitted_frames']:
                    raise Incomplete('Finish receipt does not match complete input')
                return
            except (HTTPFailure, Uncertain) as exc:
                self.get(self.base); self._retry(exc)

    def _piece(self, row, index, start, frames):
        data = self.journal.data
        name = f"{data['job']}-p{row['number']}-s{index}.wav"
        final = self.journal.owned(name); part = self.journal.owned(name + '.part')
        if final.exists() or part.exists(): raise Incomplete('Unreconciled owned spool exists')
        data['spool'].extend([name, name + '.part']); self.journal.save()
        count = 0; pcm_digest = hashlib.sha256()
        with part.open('xb') as raw:
            with wave.open(raw, 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
                while count < frames:
                    self.journal.budget(READ_BYTES + 4096)
                    block = self.reader.read(min(READ_BYTES // 2, frames-count))
                    if not block: break
                    output.writeframesraw(block); pcm_digest.update(block); count += len(block)//2
            raw.flush(); os.fsync(raw.fileno())
        if not count:
            part.unlink(); data['spool'].remove(name); data['spool'].remove(name + '.part'); self.journal.save()
            return None
        os.replace(part, final); _fsync_dir(self.journal.path)
        inspected = inspect_pcm16_source(name, final)
        result = {'name': name, 'key': data['job'] + f'.p{row["number"]}.s{index}',
                  'normalized_start': start, 'capture_start': self.origin + start,
                  'frames': count, 'bytes': final.stat().st_size, 'digest': inspected.sha256,
                  'pcm_sha256': pcm_digest.hexdigest()}
        if index < len(row['pieces']):
            old = row['pieces'][index]
            if {k:v for k,v in old.items() if k != 'server_id'} != result:
                raise SourceChanged('Replayed normalized prefix or derived WAV differs')
            result = old
        else:
            row['pieces'].append(result); self.journal.save()
        self.journal.budget()
        return result

    def run(self):
        j = self.journal; j.lock.acquire()
        try:
            self.source = Original(self.input, self.guard)
            recipe = decoder_recipe(self.source, self.guard, self.ffmpeg, self.ffprobe)
            expected = {'api': self.api, 'session_id': self.sid, 'capture_id': self.capture,
                        'origin': self.origin, 'input': self.source.identity, 'decoder': recipe}
            if j.load() is None:
                # A lost local journal never authorizes adoption of an existing capture.
                session = self.get('/sessions/' + self.sid)
                if session.get('processing_mode', 'legacy') != 'legacy': raise Incomplete('Require an existing empty legacy session')
                if self.get(self.base, missing=True) is not None: raise Incomplete('Capture exists without this exact journal')
                if self.get('/sessions/' + self.sid + '/chunks?limit=1')['chunks']: raise Incomplete('Session is not empty')
                j.data = {'policy': POLICY, 'job': uuid.uuid4().hex, 'identity': expected,
                          'pages': [], 'spool': [], 'published_frames': 0, 'complete': False, 'clean_eof': False}
                j.save()
            elif j.data.get('identity') != expected:
                raise SourceChanged('Input, decoder recipe/build or target differs from journal')
            self._saved_pages()
            # Input/recipe and saved server keys are now reconciled. Only journal-owned leftovers are removed.
            j.cleanup()
            self.reader = PCMReader(self.source, recipe, self.guard)
            cursor = 0; number = 1
            while True:
                j.budget(PAGE_BYTES + 3 * 4096)
                if number <= len(j.data['pages']): row = j.data['pages'][number-1]
                else:
                    row = {'number': number, 'pieces': [], 'released': False}
                    j.data['pages'].append(row); j.save()
                page_start = cursor; index = 0
                while cursor - page_start < PAGE_FRAMES:
                    piece = self._piece(row, index, cursor, min(SOURCE_FRAMES, PAGE_FRAMES-(cursor-page_start)))
                    if piece is None: break
                    cursor += piece['frames']; index += 1
                    if self.reader.clean: break
                if index != len(row['pieces']): raise SourceChanged('Replay ended before completed prefix')
                if self.reader.clean:
                    self.source.verify(rehash=True)
                    j.data['clean_eof'] = True; j.data['emitted_frames'] = cursor; j.save()
                if cursor == page_start:
                    if not self.reader.clean: raise Incomplete('Missing clean decoder EOF')
                    if row.get('server_id') or row['pieces']: raise Incomplete('Unexpected empty accepted page')
                    j.data['pages'].pop(); j.save()
                    if not cursor: raise Incomplete('Empty normalized input is unsupported')
                    self._finish(); break
                if row['released']:
                    if not row.get('seal'): raise Incomplete('Released journal page lacks frozen seal')
                    if row['seal']['end_frame'] != self.origin + cursor: raise SourceChanged('Released page replay end changed')
                    self._seal(row, cursor, row['seal']['eos'])
                else:
                    self._create(row)
                    for piece in row['pieces']: self._put(row, piece)
                    self._seal(row, cursor, self.reader.clean)
                j.cleanup()
                if self.reader.clean: break
                number += 1
            j.data['complete'] = True; j.data['incomplete_reason'] = None; j.save(); j.cleanup()
            return {'complete': True, 'frames': cursor, 'pages': len(j.data['pages']),
                    'sources': sum(len(r['pieces']) for r in j.data['pages']), 'max_spool_bytes': j.max_spool,
                    'max_pcm_read': self.reader.max_read, 'peak_sampled_rss': self.guard.peak_rss,
                    'meaning': 'File accepted with EOS; analysis may still be pending'}
        except BaseException as exc:
            if j.data is not None:
                j.data['incomplete_reason'] = type(exc).__name__ + ': ' + str(exc)[:1000]
                with suppress(OSError, Incomplete): j.save()
            raise
        finally:
            try:
                if self.reader is not None: self.reader.close()
            finally:
                if self.source is not None: self.source.close()
                j.lock.release()


APPEND_POLICY = 'immutable-files-contiguous-mono16k-v2'
MAX_INPUTS = 32
MAX_PIECES = 105
PAGE_SOURCES = 16


class AppendJournal(Journal):
    """Separate finite journal: never adopts or migrates a finalized-file job."""
    def owned(self, name):
        if not isinstance(name, str) or not re.fullmatch(
                re.escape(self.data['job']) + r'-p[0-9]{1,2}-s(?:[0-9]|1[0-5])\.wav(?:\.part)?', name):
            raise Incomplete('Journal contains an unowned append spool name')
        path = self.path / name
        if path.is_symlink(): raise Incomplete('Owned spool cannot be a symlink')
        return path

    def validate(self):
        d = self.data
        try:
            if d['policy'] != APPEND_POLICY or not re.fullmatch('[a-f0-9]{32}', d['job']):
                raise Incomplete('Append journal policy/job mismatch; no migration')
            if type(d['max_inputs']) is not int or not 1 <= d['max_inputs'] <= MAX_INPUTS:
                raise Incomplete('Append input registry bound')
            if not isinstance(d['inputs'], list) or len(d['inputs']) > d['max_inputs']:
                raise Incomplete('Append input registry bound')
            if not isinstance(d['pages'], list) or len(d['pages']) > 25:
                raise Incomplete('Append page bound')
            if not isinstance(d['spool'], list) or len(d['spool']) > 2 * PAGE_SOURCES:
                raise Incomplete('Append spool ledger bound')
            if len(d['spool']) != len(set(d['spool'])): raise Incomplete('Duplicate spool name')
            if type(d['complete']) is not bool or type(d['finalize_requested']) is not bool:
                raise Incomplete('Append finalization flags must be booleans')
            if any(type(v) is not int for v in (d['identity']['origin'], d['emitted_frames'], d['published_frames'])):
                raise Incomplete('Append frame frontiers must be integers')
            global_data = {k:v for k,v in d.items() if k not in ('inputs', 'pages')}
            if len(_json(global_data)) > 16384: raise Incomplete('Append global metadata bound')
            inputs = {}; cursor = 0
            for number, entry in enumerate(d['inputs']):
                if len(_json(entry)) > 8192: raise Incomplete('Append input metadata bound')
                if not isinstance(entry['id'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', entry['id']):
                    raise Incomplete('Invalid input ID')
                if entry['id'] in inputs: raise Incomplete('Duplicate input ID')
                if len(entry['identity']['path'].encode()) > 4096: raise Incomplete('Input path bound')
                if (type(entry['frames']) is not int or type(entry['normalized_start']) is not int or entry['frames'] < 0
                        or entry['normalized_start'] != cursor
                        or type(entry['clean_eof']) is not bool
                        or (not entry['clean_eof'] and number != len(d['inputs']) - 1)):
                    raise Incomplete('Input registry coordinate/EOF mismatch')
                inputs[entry['id']] = entry; cursor += entry['frames']
            if sum(e['identity']['signature'][2] for e in d['inputs']) > self.guard.limits.input_bytes:
                raise Incomplete('Aggregate original byte bound')
            if cursor != d['emitted_frames'] or not 0 <= cursor <= self.guard.limits.output_frames:
                raise Incomplete('Aggregate emitted frame bound/mismatch')
            if d['finalize_requested'] and (not inputs or not all(e['clean_eof'] for e in inputs.values())):
                raise Incomplete('Finalization intent without completed inputs')
            emitted = 0; published = 0; count = 0; local_frames = {key: 0 for key in inputs}
            allowed_spool = set(); pending_names = set()
            for number, row in enumerate(d['pages'], 1):
                if type(row['number']) is not int or row['number'] != number or type(row['released']) is not bool:
                    raise Incomplete('Append page order/state mismatch')
                if not row['released'] and number != len(d['pages']):
                    raise Incomplete('Multiple pending append pages')
                if len(_json({k:v for k,v in row.items() if k != 'pieces'})) > 2048:
                    raise Incomplete('Append page control metadata bound')
                if not isinstance(row['pieces'], list) or len(row['pieces']) > PAGE_SOURCES:
                    raise Incomplete('Append page source bound')
                page_start = emitted
                if page_start != (number - 1) * PAGE_FRAMES: raise Incomplete('Permanent page origin mismatch')
                for index, piece in enumerate(row['pieces']):
                    if len(_json(piece)) > 1024: raise Incomplete('Append piece metadata bound')
                    entry = inputs[piece['input_id']]
                    if (any(type(piece[k]) is not int for k in ('frames', 'input_start', 'normalized_start', 'capture_start', 'bytes'))
                            or not 0 < piece['frames'] <= SOURCE_FRAMES
                            or piece['normalized_start'] != emitted
                            or piece['input_start'] != local_frames[entry['id']]
                            or emitted != entry['normalized_start'] + piece['input_start']
                            or piece['capture_start'] != d['identity']['origin'] + emitted
                            or piece['bytes'] != piece['frames'] * 2 + 44
                            or piece['name'] != f"{d['job']}-p{number}-s{index}.wav"
                            or piece['key'] != f"{d['job']}.p{number}.s{index}"
                            or not re.fullmatch('[a-f0-9]{64}', piece['digest'])
                            or not re.fullmatch('[a-f0-9]{64}', piece['pcm_sha256'])):
                        raise Incomplete('Append piece lineage/identity mismatch')
                    self.owned(piece['name']); emitted += piece['frames']; count += 1
                    local_frames[entry['id']] += piece['frames']
                    allowed_spool.update((piece['name'], piece['name'] + '.part'))
                    if not row['released']: pending_names.add(piece['name'])
                if not row['released'] and len(row['pieces']) < PAGE_SOURCES:
                    name = f"{d['job']}-p{number}-s{len(row['pieces'])}.wav"
                    allowed_spool.update((name, name + '.part'))
                if emitted - page_start > PAGE_FRAMES: raise Incomplete('Append page frame bound')
                if row.get('seal'):
                    if row['seal']['end_frame'] != d['identity']['origin'] + emitted:
                        raise Incomplete('Append frozen seal placement mismatch')
                    if row['seal']['eos'] and not d['finalize_requested']:
                        raise Incomplete('EOS without finalization intent')
                if row['released']:
                    if not row.get('seal'): raise Incomplete('Released append page lacks seal')
                    if not row['seal']['eos'] and emitted - page_start != PAGE_FRAMES:
                        raise Incomplete('Nonfinal released page must be full')
                    published = emitted
            if count > MAX_PIECES or emitted != cursor or published != d['published_frames']:
                raise Incomplete('Append piece/frontier bound or mismatch')
            if any(local_frames[key] != entry['frames'] for key, entry in inputs.items()):
                raise Incomplete('Append input-local cursor mismatch')
            if d['complete'] and (not d['finalize_requested'] or not d['inputs']
                    or not all(e['clean_eof'] for e in d['inputs']) or published != emitted):
                raise Incomplete('Invalid final append checkpoint')
            if not pending_names <= set(d['spool']) or not set(d['spool']) <= allowed_spool:
                raise Incomplete('Pending spool ownership ledger mismatch')
            for name in d['spool']: self.owned(name)
        except (KeyError, TypeError, AttributeError, UnicodeError, ValueError) as exc:
            raise Incomplete('Malformed bounded append journal') from exc

    def load(self):
        path = self.path / 'uploader.json'
        if not path.exists(): return None
        if path.is_symlink() or path.stat().st_size > JSON_BYTES: raise Incomplete('Unsafe/oversized journal')
        with path.open('rb') as stream: raw = stream.read(JSON_BYTES + 1)
        if len(raw) > JSON_BYTES: raise Incomplete('Journal exceeds bound')
        self.data = _parse(raw); self.validate(); return self.data

    def save(self):
        self.validate(); super().save()

    def cleanup(self):
        # Preserve every committed pending piece, including pieces of earlier originals.
        keep = {p['name'] for r in self.data['pages'] if not r['released'] for p in r['pieces']}
        for name in self.data['spool']:
            if name not in keep: self.owned(name).unlink(missing_ok=True)
        self.data['spool'] = [n for n in self.data['spool'] if n in keep]; self.save()


class LegacyAppendUploader(Uploader):
    """Sequential immutable originals; file completion never implies capture EOS."""
    def __init__(self, *, input=None, input_id=None, input_start_frame=None, max_inputs=None,
                 append=False, finalize=False, session_id, capture_id, capture_origin_frame,
                 state_dir, api='http://127.0.0.1:8765', limits=None, cancel=None, transport=None,
                 ffmpeg='/usr/bin/ffmpeg', ffprobe='/usr/bin/ffprobe'):
        if bool(append) == bool(finalize): raise ValueError('Select exactly one append/finalize mode')
        if append and (not isinstance(input, (str, os.PathLike)) or not isinstance(input_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', input_id)):
            raise ValueError('Append requires one immutable input and a stable input ID')
        if finalize and any(v is not None for v in (input, input_id, input_start_frame)):
            raise ValueError('Finalize takes no input, input ID, or input coordinate')
        if input_start_frame is not None and (type(input_start_frame) is not int or input_start_frame < 0):
            raise ValueError('Input start must be an integer capture frame')
        if max_inputs is not None and (type(max_inputs) is not int or not 1 <= max_inputs <= MAX_INPUTS):
            raise ValueError('Maximum inputs must be between 1 and 32')
        for value in (session_id, capture_id):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
                raise ValueError('Explicit bounded session and capture IDs are required')
        if type(capture_origin_frame) is not int or not 0 <= capture_origin_frame < (1 << 53) - 2 * 3600 * RATE:
            raise ValueError('Explicit integer capture origin is required')
        self.limits = limits or Limits()
        if self.limits.output_frames > 2 * 3600 * RATE:
            raise ValueError('Legacy journals support at most two emitted hours')
        self.guard = Guard(self.limits, cancel)
        self.http = transport or HTTPTransport(api, self.guard)
        self.api = api.rstrip('/'); self.input = input; self.input_id = input_id
        self.input_start = input_start_frame; self.max_inputs = max_inputs; self.finalize = finalize
        self.origin = capture_origin_frame; self.sid = session_id; self.capture = capture_id
        self.base = '/sessions/' + quote(session_id, safe='') + '/recordings/' + quote(capture_id, safe='')
        self.state_path = Path(state_dir).resolve()
        self._check_original_location(input)
        self.journal = AppendJournal(state_dir, self.guard); self.ffmpeg = ffmpeg; self.ffprobe = ffprobe
        self.source = self.reader = None; self.entry = None

    def _check_original_location(self, path):
        source = None
        if path is not None:
            source_path = Path(path).resolve(strict=True)
            if source_path.is_relative_to(self.state_path): raise ValueError('Original input must be outside the state directory')
            if len(str(Path(path).absolute()).encode()) > 4096: raise ValueError('Input path exceeds bound')
            source = source_path.stat()
        for name in ('uploader.lock', 'uploader.json', 'uploader.json.next'):
            control = self.state_path / name
            if os.path.lexists(control):
                info = control.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('Reserved state control path must be a single-link regular file')
                if source and (info.st_dev, info.st_ino) == (source.st_dev, source.st_ino):
                    raise ValueError('Original aliases a reserved state control file')
        if (self.state_path / 'uploader.json.next').exists() and not (self.state_path / 'uploader.json').exists():
            raise ValueError('Unowned atomic journal file exists without a journal')

    def _match(self, row, remote):
        if not row.get('create') and not row.get('server_id'):
            raise Incomplete('Server page exists without this journal\'s create request')
        if remote.get('policy') != 'sealed-recording-exact-overlap-v1':
            raise Incomplete('Server page policy changed')
        if (remote['page_number'], remote['capture_id'], remote['capture_origin_frame']) != (row['number'], self.capture, self.origin):
            raise Incomplete('Server page identity mismatch')
        if row.get('server_id') and row['server_id'] != remote['id']: raise Incomplete('Server page ID changed')
        row['server_id'] = remote['id']; local = {p['key']: p for p in row['pieces']}
        sources = remote['sources']; keys = [s['idempotency_key'] for s in sources]
        if len(sources) > PAGE_SOURCES or len(keys) != len(set(keys)):
            raise Incomplete('Unexpected/duplicate server source set')
        if any(p.get('server_id') and key not in keys for key, p in local.items()):
            raise Incomplete('Previously accepted server source disappeared')
        for source in sources:
            piece = local.get(source['idempotency_key'])
            if source.get('format') != 'wav-pcm16le-16000-mono': raise Incomplete('Server source format changed')
            if piece is None or (piece['digest'], piece['frames'], piece['bytes'], piece['capture_start']) != (
                    source['digest'], source['frames'], source['bytes'], source['source_start_frame']):
                raise Incomplete('Server source bytes/key/placement mismatch')
            if piece.get('server_id') and piece['server_id'] != source['id']:
                raise Incomplete('Server source ID changed')
            piece['server_id'] = source['id']
        if remote['state'] not in ('open', 'sealed', 'preparing', 'pending_receipts', 'published'):
            raise Incomplete('Server page blocked: ' + str(remote['state']))
        if remote['state'] != 'open' and (set(keys) != set(local) or not row.get('seal')):
            raise Incomplete('Frozen server page differs from local request')
        if remote['released']:
            seal = row.get('seal')
            if not seal or remote['state'] != 'published' or (remote['end_frame'], remote['eos']) != (seal['end_frame'], seal['eos']):
                raise Incomplete('Unexpected server publication')
        return remote

    def _iter_inputs(self): return iter(self.journal.data['inputs'])
    def _iter_pages(self): return iter(self.journal.data['pages'])

    def _verify_originals(self):
        for entry in self._iter_inputs():
            self._check_original_location(entry['identity']['path'])
            original = Original(entry['identity']['path'], self.guard)
            try:
                recipe = decoder_recipe(original, self.guard, self.ffmpeg, self.ffprobe)
                if original.identity != entry['identity'] or recipe != entry['decoder']:
                    raise SourceChanged('Registered original or normalization recipe changed')
            finally: original.close()

    def _verify_tail(self, allow_missing_active=False):
        for row in self._iter_pages():
            if row['released']: continue
            for piece in row['pieces']:
                path = self.journal.owned(piece['name'])
                if not path.is_file():
                    if (allow_missing_active and self.entry and not self.entry['clean_eof']
                            and piece['input_id'] == self.entry['id']): continue
                    raise Incomplete('Missing committed tail; preserve original and journal')
                found = inspect_pcm16_source(piece['name'], path)
                if path.stat().st_size != piece['bytes'] or found.sha256 != piece['digest']:
                    raise SourceChanged('Retained tail WAV changed')

    def _reconcile_capture(self):
        """No local tail or new input may silently adopt extra server history/EOS."""
        d = self.journal.data; present = []; released = []
        for row in d['pages']:
            remote = self.get(self._page_path(row['number']), missing=True)
            if remote is None:
                if row.get('server_id') or row['released']:
                    raise Incomplete('Previously accepted server page disappeared')
            else:
                self._match(row, remote); present.append(remote)
                if row['released'] and not remote['released']: raise Incomplete('Published server page lost release')
                if remote['released']: released.append(remote)
        status = self.get(self.base, missing=True)
        if status is None:
            if present or any(row.get('server_id') for row in d['pages']):
                raise Incomplete('Previously accepted capture disappeared')
            session = self.get('/sessions/' + self.sid)
            if (session.get('processing_mode', 'legacy') != 'legacy'
                    or self.get('/sessions/' + self.sid + '/chunks?limit=1')['chunks']):
                raise Incomplete('Local-only tail no longer has its empty legacy session')
        else:
            end = released[-1]['end_frame'] if released else self.origin
            pending = [r for r in present if not r['released']]
            pending_number = pending[0]['page_number'] if pending else None
            remote_pending = status['pending_page']['page_number'] if status['pending_page'] else None
            if ((status['capture_id'], status['capture_origin_frame'], status['mode']) !=
                    (self.capture, self.origin, 'sealed_pages') or status['state'] == 'aborted'
                    or status.get('policy') != 'sealed-recording-exact-overlap-v1'
                    or status.get('format') != 'wav-pcm16le-16000-mono'
                    or status['page_count'] != len(present) or status['released_pages'] != len(released)
                    or status['capture_end_frame'] != end or remote_pending != pending_number):
                raise Incomplete('Capture history/frontier differs from journal')
            saved_eos = d.get('finish') or any(r.get('seal', {}).get('eos') for r in d['pages'])
            if status['eos'] and (not d['finalize_requested'] or not saved_eos or end != self.origin + d['emitted_frames']):
                raise Incomplete('Capture unexpectedly finalized without saved EOS request')
            if d['complete'] and not status['eos']: raise Incomplete('Finalized capture lost EOS')
        self.journal.save()

    @staticmethod
    def _header(frames):
        return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + frames * 2, b'WAVE',
                           b'fmt ', 16, 1, 1, RATE, RATE * 2, 2, 16, b'data', frames * 2)

    def _read(self, frames):
        data = self.reader.read(frames)
        if self.entry['normalized_start'] + self.reader.frames > self.limits.output_frames:
            raise Incomplete('Aggregate decoded output frame limit reached')
        return data

    def _replay_active(self):
        """Only incomplete originals decode again; published spans never use spool."""
        j = self.journal
        for row in self._iter_pages():
            for piece in row['pieces']:
                if piece['input_id'] != self.entry['id']: continue
                if self.reader.frames != piece['input_start']: raise Incomplete('Replay cursor mismatch')
                path = j.owned(piece['name']); part = j.owned(piece['name'] + '.part')
                rebuild = not row['released'] and not path.exists(); output = None
                try:
                    if rebuild:
                        if part.exists() and piece['name'] + '.part' not in j.data['spool']:
                            raise Incomplete('Untracked replay part exists; preserve it')
                        part.unlink(missing_ok=True)
                        for name in (piece['name'], piece['name'] + '.part'):
                            if name not in j.data['spool']: j.data['spool'].append(name)
                        j.save(); output = part.open('xb'); output.write(self._header(piece['frames']))
                    pcm = hashlib.sha256(); wav = hashlib.sha256(self._header(piece['frames']))
                    left = piece['frames']
                    while left:
                        block = self._read(min(READ_BYTES // 2, left))
                        if not block: raise SourceChanged('Replay ended before saved normalized prefix')
                        pcm.update(block); wav.update(block); left -= len(block) // 2
                        if output:
                            j.budget(READ_BYTES + 4096); output.write(block)
                    if (pcm.hexdigest(), wav.hexdigest()) != (piece['pcm_sha256'], piece['digest']):
                        raise SourceChanged('Replayed normalized PCM or WAV differs')
                    if output:
                        output.flush(); os.fsync(output.fileno()); output.close(); output = None
                        os.replace(part, path); _fsync_dir(j.path)
                finally:
                    if output: output.close()
        if self.reader.frames != self.entry['frames']: raise Incomplete('Replay input cursor mismatch')

    def _new_piece(self, row):
        j = self.journal; index = len(row['pieces']); d = j.data
        if index >= PAGE_SOURCES: raise Incomplete('Pending page source capacity exhausted')
        name = f"{d['job']}-p{row['number']}-s{index}.wav"
        path = j.owned(name); part = j.owned(name + '.part')
        if path.exists() or part.exists(): raise Incomplete('Unreconciled owned append spool')
        d['spool'].extend([name, name + '.part']); j.save()
        start = d['emitted_frames']; local_start = self.entry['frames']
        capacity = min(SOURCE_FRAMES, PAGE_FRAMES - sum(p['frames'] for p in row['pieces']))
        count = 0; digest = hashlib.sha256()
        with part.open('xb') as raw:
            with wave.open(raw, 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
                while count < capacity:
                    j.budget(READ_BYTES + 4096)
                    block = self._read(min(READ_BYTES // 2, capacity - count))
                    if not block: break
                    output.writeframesraw(block); digest.update(block); count += len(block) // 2
            raw.flush(); os.fsync(raw.fileno())
        if not count:
            part.unlink(); d['spool'].remove(name); d['spool'].remove(name + '.part'); j.save(); return None
        os.replace(part, path); _fsync_dir(j.path)
        found = inspect_pcm16_source(name, path)
        piece = {'name': name, 'key': f"{d['job']}.p{row['number']}.s{index}",
                 'input_id': self.entry['id'], 'input_start': local_start, 'normalized_start': start,
                 'capture_start': self.origin + start, 'frames': count, 'bytes': path.stat().st_size,
                 'digest': found.sha256, 'pcm_sha256': digest.hexdigest()}
        if len(_json(piece)) + 256 > 1024: raise Incomplete('Piece receipt metadata reservation exceeded')
        row['pieces'].append(piece); self.entry['frames'] += count; d['emitted_frames'] += count
        j.save(); return piece

    def _pending(self):
        pages = self.journal.data['pages']
        return pages[-1] if pages and not pages[-1]['released'] else None

    def _publish(self, row, eos):
        end = row['pieces'][-1]['normalized_start'] + row['pieces'][-1]['frames']
        self._create(row)
        for piece in row['pieces']: self._put(row, piece)
        self._seal(row, end, eos); self.journal.cleanup()

    def _result(self, entry=None):
        d = self.journal.data
        return {'input_complete': bool(entry and entry['clean_eof']),
                'awaiting_input': not d['complete'] and not d['finalize_requested'] and all(e['clean_eof'] for e in d['inputs']),
                'capture_finalized': d['complete'], 'frames': d['emitted_frames'],
                'published_frames': d['published_frames'], 'staged_frames': d['emitted_frames'] - d['published_frames'],
                'inputs': len(d['inputs']), 'pages': len(d['pages']),
                'sources': sum(len(row['pieces']) for row in d['pages']),
                'max_spool_bytes': self.journal.max_spool,
                'max_pcm_read': self.reader.max_read if self.reader else 0,
                'peak_sampled_rss': self.guard.peak_rss,
                'meaning': 'EOS accepted; analysis may still be pending' if d['complete'] else 'Input accepted without capture EOS'}

    def run(self):
        j = self.journal; j.lock.acquire()
        try:
            target = {'api': self.api, 'session_id': self.sid, 'capture_id': self.capture, 'origin': self.origin, 'format': FORMAT}
            loaded = j.load()
            if loaded is not None:
                if loaded['identity'] != target: raise SourceChanged('Append target differs from journal')
                if self.max_inputs is not None and self.max_inputs != loaded['max_inputs']:
                    raise Incomplete('Maximum inputs differs from frozen journal')
                self._verify_originals()
            elif self.finalize:
                raise Incomplete('Finalize requires this capture\'s append journal')
            if self.input is not None:
                self.source = Original(self.input, self.guard)
                recipe = decoder_recipe(self.source, self.guard, self.ffmpeg, self.ffprobe)
            if loaded is None:
                if self.input_start is not None and self.input_start != self.origin:
                    raise Incomplete('First input must start at the declared capture origin')
                session = self.get('/sessions/' + self.sid)
                if session.get('processing_mode', 'legacy') != 'legacy': raise Incomplete('Require an existing empty legacy session')
                if self.get(self.base, missing=True) is not None: raise Incomplete('Capture exists without this exact journal')
                if self.get('/sessions/' + self.sid + '/chunks?limit=1')['chunks']: raise Incomplete('Session is not empty')
                j.data = {'policy': APPEND_POLICY, 'job': uuid.uuid4().hex, 'identity': target,
                          'max_inputs': self.max_inputs or MAX_INPUTS, 'inputs': [], 'pages': [], 'spool': [],
                          'emitted_frames': 0, 'published_frames': 0, 'complete': False, 'finalize_requested': False}
                j.save()
            d = j.data; existing = next((e for e in d['inputs'] if e['id'] == self.input_id), None)
            new_entry = None
            if self.finalize:
                if not d['inputs'] or not d['emitted_frames'] or not all(e['clean_eof'] for e in d['inputs']):
                    raise Incomplete('Finalize requires nonempty, clean EOF for every registered input')
            elif existing:
                if (existing['identity'] != self.source.identity or existing['decoder'] != recipe
                        or self.input_start is not None and self.input_start != self.origin + existing['normalized_start']):
                    raise SourceChanged('Input ID identity, recipe, or placement conflict')
                self.entry = existing
            else:
                if d['finalize_requested']: raise Incomplete('Capture finalization is fenced; no new inputs')
                if any(not e['clean_eof'] for e in d['inputs']): raise Incomplete('Resume the incomplete input ID before appending')
                if len(d['inputs']) >= d['max_inputs']: raise Incomplete('Input registry capacity reached')
                if self.input_start is not None and self.input_start != self.origin + d['emitted_frames']:
                    raise Incomplete('Only contiguous append is supported; gaps/overlap are refused')
                if any(e['identity']['path'] == self.source.identity['path'] or
                       e['identity']['signature'][:2] == self.source.identity['signature'][:2] for e in d['inputs']):
                    raise SourceChanged('Original already has a stable input ID')
                if sum(e['identity']['signature'][2] for e in d['inputs']) + self.source.identity['signature'][2] > self.limits.input_bytes:
                    raise Incomplete('Aggregate original byte limit reached')
                if d['emitted_frames'] >= self.limits.output_frames: raise Incomplete('Aggregate output frame capacity reached')
                row = self._pending(); pieces = row['pieces'] if row else []
                remaining = PAGE_FRAMES - sum(p['frames'] for p in pieces)
                if len(pieces) + (remaining + SOURCE_FRAMES - 1) // SOURCE_FRAMES > PAGE_SOURCES:
                    raise Incomplete('Insufficient pending-page source slots; retry existing input or finalize')
                new_entry = {'id': self.input_id, 'identity': self.source.identity, 'decoder': recipe,
                             'normalized_start': d['emitted_frames'], 'frames': 0, 'clean_eof': False}
                if len(_json(new_entry)) + 128 > 8192: raise Incomplete('Input metadata reservation exceeded')
            self._verify_tail(allow_missing_active=True)
            self._reconcile_capture()
            for row in d['pages']:
                if row['released']:
                    self._seal(row, row['seal']['end_frame'] - self.origin, row['seal']['eos'])
            j.cleanup()
            if existing and not existing['clean_eof']:
                self.reader = PCMReader(self.source, recipe, self.guard); self._replay_active()
                self._verify_tail()
            if d['complete']:
                if d.get('finish'): self._finish()
                return self._result(existing)
            if self.finalize:
                d['finalize_requested'] = True; j.save()
                row = self._pending()
                if row: self._publish(row, True)
                else: self._finish()
                d['complete'] = True; d['incomplete_reason'] = None; j.save(); j.cleanup()
                return self._result()
            if existing and existing['clean_eof']: return self._result(existing)
            if d['finalize_requested']: raise Incomplete('Finalization in progress; rerun --finalize')
            if new_entry:
                d['inputs'].append(new_entry); self.entry = new_entry; j.save()
                self.reader = PCMReader(self.source, recipe, self.guard)
            while True:
                row = self._pending()
                if row and sum(p['frames'] for p in row['pieces']) == PAGE_FRAMES:
                    self._publish(row, False); row = None
                if row is None:
                    row = {'number': len(d['pages']) + 1, 'pieces': [], 'released': False}
                    d['pages'].append(row); j.save()
                remaining = PAGE_FRAMES - sum(p['frames'] for p in row['pieces'])
                j.budget(remaining * 2 + (PAGE_SOURCES - len(row['pieces'])) * 4096)
                piece = self._new_piece(row)
                if piece is None or self.reader.clean:
                    if not row['pieces']: d['pages'].pop()
                    self.source.verify(rehash=True)
                    if not self.entry['frames']: raise Incomplete('Empty normalized input is unsupported')
                    self.entry['clean_eof'] = True; d['incomplete_reason'] = None; j.save(); j.cleanup()
                    return self._result(self.entry)
        except BaseException as exc:
            if j.data is not None:
                j.data['incomplete_reason'] = type(exc).__name__ + ': ' + str(exc)[:1000]
                with suppress(OSError, Incomplete): j.save()
            raise
        finally:
            try:
                if self.reader is not None: self.reader.close()
            finally:
                if self.source is not None: self.source.close()
                j.lock.release()


from .recording_uploader_history import (PagedJournal, POLICY as APPEND_V3_POLICY,
    DEFAULT_FRAMES as APPEND_DEFAULT_FRAMES, DEFAULT_INPUTS as APPEND_DEFAULT_INPUTS,
    MAX_INPUTS as APPEND_MAX_INPUTS, MAX_INPUT_BYTES as APPEND_MAX_INPUT_BYTES)


class AppendUploader(LegacyAppendUploader):
    """Fresh jobs use v3; old v2 journals keep their exact loader/resume policy."""
    def __new__(cls, **kwargs):
        # Read-only dispatch is revalidated by the selected loader under the lock.
        # Never choose a journal by temporary timestamps or an archive directory.
        path = Path(kwargs['state_dir']) / 'uploader.json'
        if os.path.lexists(path):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > JSON_BYTES:
                raise Incomplete('Unsafe/oversized journal policy probe')
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                if _signature(os.fstat(stream.fileno())) != _signature(info): raise Incomplete('Journal changed during policy probe')
                data = _parse(stream.read(JSON_BYTES + 1))
            if data.get('policy') == APPEND_POLICY:
                old = dict(kwargs)
                hours = old.pop('max_hours', None); size = old.pop('max_original_bytes', None)
                if hours is not None or size is not None:
                    raise Incomplete('Legacy v2 frozen policy does not accept v3 capacity options')
                return LegacyAppendUploader(**old)
        return object.__new__(cls)

    def __init__(self, *, input=None, input_id=None, input_start_frame=None, max_inputs=None,
                 max_hours=None, max_original_bytes=None, append=False, finalize=False,
                 session_id, capture_id, capture_origin_frame, state_dir,
                 api='http://127.0.0.1:8765', limits=None, cancel=None, transport=None,
                 ffmpeg='/usr/bin/ffmpeg', ffprobe='/usr/bin/ffprobe'):
        if bool(append) == bool(finalize): raise ValueError('Select exactly one append/finalize mode')
        if append and (not isinstance(input, (str, os.PathLike)) or not isinstance(input_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', input_id)):
            raise ValueError('Append requires one immutable input and a stable input ID')
        if finalize and any(v is not None for v in (input, input_id, input_start_frame)):
            raise ValueError('Finalize takes no input, input ID, or input coordinate')
        if input_start_frame is not None and (type(input_start_frame) is not int or not 0 <= input_start_frame <= (1 << 53) - 1):
            raise ValueError('Input start must be an exact integer capture frame')
        if max_inputs is not None and (type(max_inputs) is not int or not 1 <= max_inputs <= APPEND_MAX_INPUTS):
            raise ValueError('Maximum inputs must be between 1 and 128')
        if max_hours is not None and (type(max_hours) is not int or not 1 <= max_hours <= 48):
            raise ValueError('Maximum hours must be an integer between 1 and 48')
        if max_original_bytes is not None and (type(max_original_bytes) is not int or not 1 <= max_original_bytes <= APPEND_MAX_INPUT_BYTES):
            raise ValueError('Maximum original bytes must be between 1 and 64 GiB')
        for value in (session_id, capture_id):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
                raise ValueError('Explicit bounded session and capture IDs are required')
        if type(capture_origin_frame) is not int or not 0 <= capture_origin_frame <= (1 << 53) - 1:
            raise ValueError('Explicit exact integer capture origin is required')
        base_limits = limits or Limits()
        self.requested_frames = (max_hours * 3600 * RATE if max_hours is not None else
                                 base_limits.output_frames if limits and limits.output_frames != Limits.output_frames else None)
        self.requested_bytes = (max_original_bytes if max_original_bytes is not None else
                                base_limits.input_bytes if limits and limits.input_bytes != Limits.input_bytes else None)
        self.limits = replace(base_limits, output_frames=self.requested_frames or APPEND_DEFAULT_FRAMES,
                              input_bytes=self.requested_bytes or Limits.input_bytes)
        self.guard = Guard(self.limits, cancel); self.http = transport or HTTPTransport(api, self.guard)
        self.api = api.rstrip('/'); self.input = input; self.input_id = input_id
        self.input_start = input_start_frame; self.max_inputs = max_inputs; self.finalize = finalize
        self.origin = capture_origin_frame; self.sid = session_id; self.capture = capture_id
        self.base = '/sessions/' + quote(session_id, safe='') + '/recordings/' + quote(capture_id, safe='')
        self.state_path = Path(state_dir).resolve(); self._check_original_location(input)
        self.journal = PagedJournal(state_dir, self.guard, sys.modules[__name__])
        self.ffmpeg = ffmpeg; self.ffprobe = ffprobe; self.source = self.reader = None; self.entry = None

    def _iter_inputs(self): return self.journal.iter_inputs()
    def _iter_pages(self): return self.journal.iter_pages()

    def _blocked_worker(self):
        # Errors can contain 2,000 Unicode characters. Sixteen rows leave ample
        # room inside the unchanged 512 KiB wire limit, including JSON escaping.
        # Global rowids may be sparse; count rows, never compare cursor to a cap.
        batch = 16; maximum = (self.limits.output_frames + 168000 - 1) // 168000
        cursor = total = 0
        for _ in range(maximum // batch + 1):
            result = self.get('/sessions/' + self.sid + '/chunks?after=' + str(cursor) + '&limit=' + str(batch))
            if not isinstance(result, dict): raise Incomplete('Malformed worker response')
            rows = result.get('chunks'); next_cursor = result.get('next_cursor')
            if not isinstance(rows, list) or len(rows) > batch:
                raise Incomplete('Malformed worker cursor batch')
            previous = cursor
            for chunk in rows:
                if not isinstance(chunk, dict): raise Incomplete('Malformed worker row')
                if chunk.get('session_id', self.sid) != self.sid: raise Incomplete('Unexpected worker row membership')
                if (not isinstance(chunk.get('id'), str) or not 1 <= len(chunk['id']) <= 128
                        or not isinstance(chunk.get('status'), str)):
                    raise Incomplete('Malformed worker row identity/status')
                row_cursor = chunk.get('cursor')
                if type(row_cursor) is not int or row_cursor <= previous:
                    raise Incomplete('Malformed/non-progress worker cursor')
                previous = row_cursor; total += 1
                if total > maximum: raise Incomplete('Unexpected session chunk bound')
                if chunk.get('status') in ('failed', 'paused', 'cancelled'):
                    raise Incomplete('Admission blocked by worker ' + chunk['status'] + ': ' + str(chunk.get('id', '')))
            if type(next_cursor) is not int or next_cursor != previous:
                raise Incomplete('Malformed/non-progress worker next cursor')
            if len(rows) < batch: return
            cursor = next_cursor
        raise Incomplete('Unexpected session chunk bound')

    def _reconcile_capture(self):
        """Complete read-only remote reconciliation precedes replay or local mutation."""
        d = self.journal.data; present = released = 0; end = self.origin; pending = None; saved_eos = bool(d.get('finish'))
        for row in self._iter_pages():
            remote = self.get(self._page_path(row['number']), missing=True)
            saved_eos = saved_eos or bool(row.get('seal', {}).get('eos'))
            if remote is None:
                if row.get('server_id') or row['released']: raise Incomplete('Previously accepted server page disappeared')
                continue
            self._match(row, remote); present += 1
            if row['released'] and not remote['released']: raise Incomplete('Published server page lost release')
            if remote['released']: released += 1; end = remote['end_frame']
            elif pending is not None: raise Incomplete('Multiple pending remote pages')
            else: pending = remote['page_number']
        status = self.get(self.base, missing=True)
        if status is None:
            if present or d['pages_done']: raise Incomplete('Previously accepted capture disappeared')
            session = self.get('/sessions/' + self.sid)
            if (session.get('processing_mode', 'legacy') != 'legacy'
                    or self.get('/sessions/' + self.sid + '/chunks?limit=1')['chunks']):
                raise Incomplete('Local-only tail no longer has its empty legacy session')
        else:
            remote_pending = status['pending_page']['page_number'] if status['pending_page'] else None
            if ((status['capture_id'], status['capture_origin_frame'], status['mode']) !=
                    (self.capture, self.origin, 'sealed_pages') or status['state'] == 'aborted'
                    or status.get('policy') != 'sealed-recording-exact-overlap-v1'
                    or status.get('format') != 'wav-pcm16le-16000-mono'
                    or status['page_count'] != present or status['released_pages'] != released
                    or status['capture_end_frame'] != end or remote_pending != pending):
                raise Incomplete('Capture history/frontier differs from journal')
            if status['eos'] and (not d['finalize_requested'] or not saved_eos or end != self.origin + d['emitted_frames']):
                raise Incomplete('Capture unexpectedly finalized without saved EOS request')
            if d['complete'] and not status['eos']: raise Incomplete('Finalized capture lost EOS')

    def _pending(self): return self.journal.data['active_page']

    def _publish(self, row, eos):
        end = row['pieces'][-1]['normalized_start'] + row['pieces'][-1]['frames']
        self._create(row)
        for piece in row['pieces']: self._put(row, piece)
        self._seal(row, end, eos)
        self.journal.archive_page(); self.journal.cleanup()

    def _new_piece(self, row):
        j = self.journal; d = j.data; index = len(row['pieces'])
        if index >= PAGE_SOURCES: raise Incomplete('Pending page source capacity exhausted')
        if d['source_count'] >= 16 * j.max_pages: raise Incomplete('Source history capacity exhausted')
        capacity = min(SOURCE_FRAMES, PAGE_FRAMES - sum(p['frames'] for p in row['pieces']),
                       self.limits.output_frames - d['emitted_frames'])
        if capacity <= 0: raise Incomplete('Aggregate decoded output frame limit reached')
        name = f"{d['job']}-p{row['number']}-s{index}.wav"
        path = j.owned(name); part = j.owned(name + '.part')
        if path.exists() or part.exists(): raise Incomplete('Unreconciled owned append spool')
        d['spool'].extend([name, name + '.part']); j.save()
        start = d['emitted_frames']; local_start = self.entry['frames']; count = 0; digest = hashlib.sha256()
        with part.open('xb') as raw:
            with wave.open(raw, 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
                while count < capacity:
                    j.budget(READ_BYTES + 4096)
                    block = self._read(min(READ_BYTES // 2, capacity - count))
                    if not block: break
                    output.writeframesraw(block); digest.update(block); count += len(block) // 2
            raw.flush(); os.fsync(raw.fileno())
        if not count:
            part.unlink(); _fsync_dir(j.path)
            d['spool'].remove(name); d['spool'].remove(name + '.part'); j.save(); return None
        os.replace(part, path); _fsync_dir(j.path)
        found = inspect_pcm16_source(name, path)
        piece = {'name': name, 'key': f"{d['job']}.p{row['number']}.s{index}",
                 'input_id': self.entry['id'], 'input_start': local_start, 'normalized_start': start,
                 'capture_start': self.origin + start, 'frames': count, 'bytes': path.stat().st_size,
                 'digest': found.sha256, 'pcm_sha256': digest.hexdigest()}
        if len(_json(piece)) + 256 > 1024: raise Incomplete('Piece receipt metadata reservation exceeded')
        row['pieces'].append(piece); self.entry['frames'] += count; d['emitted_frames'] += count; d['source_count'] += 1
        j.save(); return piece

    def _result(self, entry=None):
        d = self.journal.data
        return {'input_complete': bool(entry and entry['clean_eof']),
                'awaiting_input': not d['complete'] and not d['finalize_requested'] and d['active_input'] is None,
                'capture_finalized': d['complete'], 'frames': d['emitted_frames'],
                'published_frames': d['published_frames'], 'staged_frames': d['emitted_frames'] - d['published_frames'],
                'inputs': d['input_count'], 'pages': d['page_count'], 'sources': d['source_count'],
                'max_spool_bytes': self.journal.max_spool,
                'max_pcm_read': self.reader.max_read if self.reader else 0, 'peak_sampled_rss': self.guard.peak_rss,
                'meaning': ('EOS accepted; analysis may still be pending' if d['complete'] else
                            'Finalization pending; resume --finalize' if d['finalize_requested'] else
                            'Input accepted without capture EOS')}

    def _complete_input(self):
        j = self.journal; d = j.data; row = d['active_page']
        if row is not None and not row['pieces']:
            d['active_page'] = None; d['page_count'] -= 1
        self.source.verify(rehash=True)
        if not self.entry['frames']: raise Incomplete('Empty normalized input is unsupported')
        self.entry['clean_eof'] = True; d['incomplete_reason'] = None; j.save()
        j.archive_input(); j.cleanup()
        return self._result(self.entry)

    def run(self):
        j = self.journal; j.lock.acquire()
        try:
            target = {'api': self.api, 'session_id': self.sid, 'capture_id': self.capture, 'origin': self.origin, 'format': FORMAT}
            loaded = j.load()
            if loaded is not None:
                if loaded['identity'] != target: raise SourceChanged('Append target differs from journal')
                caps = loaded['caps']
                if self.max_inputs is not None and self.max_inputs != caps['max_inputs']: raise Incomplete('Maximum inputs differs from frozen journal')
                if self.requested_frames is not None and self.requested_frames != caps['output_frames']: raise Incomplete('Maximum duration differs from frozen journal')
                if self.requested_bytes is not None and self.requested_bytes != caps['input_bytes']: raise Incomplete('Maximum original bytes differs from frozen journal')
                self.limits = replace(self.limits, output_frames=caps['output_frames'], input_bytes=caps['input_bytes']); self.guard.limits = self.limits
                self._verify_originals()
            elif self.finalize: raise Incomplete('Finalize requires this capture\'s append journal')
            if self.origin + self.limits.output_frames > (1 << 53) - 1: raise ValueError('Capture origin plus frozen frame cap exceeds exact integers')
            if self.input is not None:
                self.source = Original(self.input, self.guard)
                recipe = decoder_recipe(self.source, self.guard, self.ffmpeg, self.ffprobe)
            if loaded is None:
                if self.input_start is not None and self.input_start != self.origin: raise Incomplete('First input must start at the declared capture origin')
                session = self.get('/sessions/' + self.sid)
                if session.get('processing_mode', 'legacy') != 'legacy': raise Incomplete('Require an existing empty legacy session')
                if self.get(self.base, missing=True) is not None: raise Incomplete('Capture exists without this exact journal')
                if self.get('/sessions/' + self.sid + '/chunks?limit=1')['chunks']: raise Incomplete('Session is not empty')
                j.initialize(target, self.max_inputs or APPEND_DEFAULT_INPUTS)
            d = j.data; existing = next((e for e in self._iter_inputs() if e['id'] == self.input_id), None); new_entry = None
            if self.finalize:
                if not d['input_count'] or not d['emitted_frames'] or d['active_input'] and not d['active_input']['clean_eof']:
                    raise Incomplete('Finalize requires nonempty, clean EOF for every registered input')
            elif existing:
                if (existing['identity'] != self.source.identity or existing['decoder'] != recipe
                        or self.input_start is not None and self.input_start != self.origin + existing['normalized_start']):
                    raise SourceChanged('Input ID identity, recipe, or placement conflict')
                self.entry = existing
            else:
                if d['finalize_requested']: raise Incomplete('Capture finalization is fenced; no new inputs')
                if d['active_input'] and not d['active_input']['clean_eof']: raise Incomplete('Resume the incomplete input ID before appending')
                if d['input_count'] >= d['caps']['max_inputs']: raise Incomplete('Input registry capacity reached')
                if self.input_start is not None and self.input_start != self.origin + d['emitted_frames']:
                    raise Incomplete('Only contiguous append is supported; gaps/overlap are refused')
                for entry in self._iter_inputs():
                    if (entry['identity']['path'] == self.source.identity['path'] or entry['identity']['signature'][:2] == self.source.identity['signature'][:2]):
                        raise SourceChanged('Original already has a stable input ID')
                if d['input_bytes'] + self.source.identity['signature'][2] > self.limits.input_bytes: raise Incomplete('Aggregate original byte limit reached')
                if d['emitted_frames'] >= self.limits.output_frames: raise Incomplete('Aggregate output frame capacity reached')
                row = self._pending(); pieces = row['pieces'] if row and not row['released'] else []
                remaining = min(PAGE_FRAMES - sum(p['frames'] for p in pieces), self.limits.output_frames - d['emitted_frames'])
                if len(pieces) + (remaining + SOURCE_FRAMES - 1) // SOURCE_FRAMES > PAGE_SOURCES:
                    raise Incomplete('Insufficient pending-page source slots; retry existing input or finalize')
                new_entry = {'id': self.input_id, 'identity': self.source.identity, 'decoder': recipe,
                             'normalized_start': d['emitted_frames'], 'frames': 0, 'clean_eof': False}
                if len(_json(new_entry)) + 128 > 8192: raise Incomplete('Input metadata reservation exceeded')
            self._verify_tail(allow_missing_active=True)
            self._reconcile_capture()
            # Each archive is authenticated again here, after all local/remote GET checks.
            for row in self._iter_pages():
                if row['released']: self._seal(row, row['seal']['end_frame'] - self.origin, row['seal']['eos'])
            j.recover()
            if existing and not existing['clean_eof']:
                self.entry = d['active_input']; self.reader = PCMReader(self.source, recipe, self.guard)
                self._replay_active(); self._verify_tail()
            if d['complete']:
                if d.get('finish'): self._finish()
                return self._result(existing)
            if self.finalize:
                d['finalize_requested'] = True; j.save()
                row = self._pending()
                if row: self._publish(row, True)
                else:
                    last = j.read_archive('page', d['pages_done']) if d['pages_done'] else None
                    # Recovery already replayed the exact saved EOS seal. A lost
                    # completion checkpoint must not mint a second finish intent.
                    if not (last and last['seal']['eos']): self._finish()
                d['complete'] = True; d['incomplete_reason'] = None; j.save(); j.cleanup()
                return self._result()
            if existing and existing['clean_eof']: return self._result(existing)
            if d['finalize_requested']: raise Incomplete('Finalization in progress; rerun --finalize')
            if new_entry:
                d['active_input'] = new_entry; d['input_count'] += 1
                d['input_bytes'] += new_entry['identity']['signature'][2]; self.entry = new_entry; j.save()
                self.reader = PCMReader(self.source, recipe, self.guard)
            while True:
                row = self._pending()
                if row and sum(p['frames'] for p in row['pieces']) == PAGE_FRAMES:
                    self._publish(row, False); row = None
                # At the exact duration bound, only a one-frame EOF probe is legal.
                # Never register or allocate an empty page beyond max_pages.
                if d['emitted_frames'] == self.limits.output_frames:
                    if self._read(1): raise Incomplete('Aggregate decoded output frame limit reached')
                    return self._complete_input()
                if row is None:
                    if d['retirement']: raise Incomplete('Retiring page must finish cleanup before another page')
                    if d['page_count'] >= j.max_pages: raise Incomplete('Append page capacity exhausted')
                    row = {'number': d['page_count'] + 1, 'pieces': [], 'released': False}
                    d['active_page'] = row; d['page_count'] += 1; j.save()
                remaining = min(PAGE_FRAMES - sum(p['frames'] for p in row['pieces']), self.limits.output_frames - d['emitted_frames'])
                j.budget(remaining * 2 + (PAGE_SOURCES - len(row['pieces'])) * 4096)
                piece = self._new_piece(row)
                if piece is None or self.reader.clean: return self._complete_input()
        finally:
            # Never salvage a partially advanced in-memory transition into a new
            # checkpoint after an error. The last durable checkpoint is authority.
            try:
                if self.reader is not None: self.reader.close()
            finally:
                if self.source is not None: self.source.close()
                j.lock.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input')
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--capture-id', required=True)
    parser.add_argument('--capture-origin-frame', type=int, required=True)
    parser.add_argument('--state-dir', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--final', action='store_true')
    modes.add_argument('--append', action='store_true')
    modes.add_argument('--finalize', action='store_true')
    parser.add_argument('--input-id')
    parser.add_argument('--input-start-frame', type=int)
    parser.add_argument('--max-inputs', type=int)
    parser.add_argument('--max-hours', type=int, help='New append duration cap, 1..48 hours; default12; frozen on creation')
    parser.add_argument('--max-original-bytes', type=int, help='Aggregate original bytes, default8GiB; at most64GiB; frozen')
    parser.add_argument('--api', default='http://127.0.0.1:8765')
    parser.add_argument('--disk-floor-bytes', type=int, default=Limits.disk_floor)
    parser.add_argument('--deadline-seconds', type=float, default=Limits.deadline_seconds)
    args = vars(parser.parse_args(argv))
    try:
        limits = Limits(disk_floor=args.pop('disk_floor_bytes'), deadline_seconds=args.pop('deadline_seconds'))
        if args.pop('final'):
            if not args.get('input') or any(args.get(k) is not None for k in ('input_id', 'input_start_frame', 'max_inputs', 'max_hours', 'max_original_bytes')):
                raise ValueError('--final requires one input and no append-only options')
            for name in ('append', 'finalize', 'input_id', 'input_start_frame', 'max_inputs', 'max_hours', 'max_original_bytes'): args.pop(name)
            result = Uploader(**args, final=True, limits=limits).run()
        else: result = AppendUploader(**args, limits=limits).run()
        print(json.dumps(result, sort_keys=True)); return 0
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
        print(f'Incomplete ingestion: {exc}', file=sys.stderr); return 2


if __name__ == '__main__': raise SystemExit(main())
