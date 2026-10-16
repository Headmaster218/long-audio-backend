"""Opt-in, one-finalized-file ingestion. Client journal, not server provenance.

Run ``python -m audio_backend.recording_uploader --help``. No worker/model defaults
are changed. One continuous decode, one page on disk, one synchronous request.
"""
from contextlib import contextmanager, suppress
from dataclasses import dataclass
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
        if self.output_frames > 2 * 3600 * RATE:
            raise ValueError('This finite journal supports at most two emitted hours')
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
        self.limits = limits or Limits(); self.guard = Guard(self.limits, cancel)
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
        # Bounded pagination; do not wait for waiting_input/completed analysis at publication.
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--capture-id', required=True)
    parser.add_argument('--capture-origin-frame', type=int, required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--final', action='store_true', required=True)
    parser.add_argument('--api', default='http://127.0.0.1:8765')
    parser.add_argument('--disk-floor-bytes', type=int, default=Limits.disk_floor)
    parser.add_argument('--deadline-seconds', type=float, default=Limits.deadline_seconds)
    args = vars(parser.parse_args(argv))
    limits = Limits(disk_floor=args.pop('disk_floor_bytes'), deadline_seconds=args.pop('deadline_seconds'))
    try: print(json.dumps(Uploader(**args, limits=limits).run(), sort_keys=True)); return 0
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
        print(f'Incomplete ingestion: {exc}', file=sys.stderr); return 2


if __name__ == '__main__': raise SystemExit(main())
