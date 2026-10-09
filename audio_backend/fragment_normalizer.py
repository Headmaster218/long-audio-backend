"""Experimental explicit-opt-in finalized SourcePlan normalization; no runtime hooks.

Only controlled immutable PCM16 files and pinned FFmpeg are admitted. Native
ownership/alias decisions precede mixing. This module never decides a seam.
"""
from dataclasses import asdict, dataclass
import hashlib
import math
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import time
import wave

from .fragment_core import (BoundedPCM16Reader, SourceChanged, UnsupportedAudio,
                            _canonical, _digest_stream, _integer, _signature)

RATE = 16000
CONTEXT = 512
MAX_READ = 8000
MAX_NATIVE_READ = 25465
MIN_ISLAND = 123
MAX_OUTPUT_BYTES = 20000
EXECUTABLE_SHA256 = 'e8a8d46f5225f3062cec7c07fb145d58ae73c603cb740dcd5bad34bfb54e455a'
VERSION_SHA256 = 'b241942a091ee91966e5b04017a34f1f2a3bcfb1cf80a195e274db03874459d9'
FILTER = ('aresample=16000:resampler=swr:osf=s16:tsf=s16p:filter_size=32:'
          'phase_shift=10:linear_interp=0:exact_rational=1:cutoff=0.97:'
          'filter_type=kaiser:kaiser_beta=9:dither_method=0:async=0')
STEREO = 'pan=mono|c0=0.5*c0+0.5*c1,'
CONTRACT = {'version': 'finalized-bounded-pcm16-v1', 'rates': [44100, 48000],
            'canonical_rate': RATE, 'context': CONTEXT, 'max_read': MAX_READ,
            'minimum_island': MIN_ISLAND, 'filter': FILTER, 'stereo': STEREO,
            'binary_sha256': EXECUTABLE_SHA256, 'version_sha256': VERSION_SHA256,
            'endpoint': 'phase-aligned-bounded-tail-v2',
            'endpoint_contract_sha256': 'e549c19d2ee3e8b18bdedcad7854955cfa1797b0fc5511b04f40d7f2c7e61753'}
FINGERPRINT = hashlib.sha256(_canonical(CONTRACT).encode()).hexdigest()


def _ceil(a, b):
    return (a + b - 1) // b


def _rss(pid):
    try:
        with open(f'/proc/{pid}/status') as stream:
            return next(int(line.split()[1]) * 1024 for line in stream if line.startswith('VmRSS:'))
    except (OSError, StopIteration):
        return 0


def _child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (131072, 131072))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))


@dataclass(frozen=True)
class NormalizedWindow:
    start: int
    end: int
    pcm: bytes
    native_context_start: int
    native_context_end: int
    owners: tuple
    aliases: tuple
    plan_sha256: str
    normalizer_fingerprint: str
    native_end: int
    canonical_end: int


class FinalizedPCM16Normalizer:
    """Read <=8000 canonical frames with <=25465 native context frames.

    `enabled=True` is mandatory; `plan.final` must independently declare true
    EOS. Exhausted transport files, missing receipts, and temporary tails do not
    imply EOS. Paths are external bindings, excluded from immutable plan IDs.
    Reopening rehashes sources by streaming. Do not use with adversarial mutable
    storage: inherited descriptor/signature checks assume controlled files.
    """
    def __init__(self, plan, paths, *, temp_root, enabled=False, executable='/usr/bin/ffmpeg'):
        if enabled is not True:
            raise UnsupportedAudio('Experimental normalizer is default OFF')
        plan.validate()
        if not plan.final:
            raise UnsupportedAudio('Explicit true EOS is required; a missing next fragment is not EOS')
        if plan.sample_rate not in (44100, 48000) or plan.channels not in (1, 2):
            raise UnsupportedAudio('Only same-rate 44.1/48kHz mono/stereo PCM16 is admitted')
        if any(span.island != 0 for span in plan.spans) or plan.observed_frames != plan.end_frame:
            raise UnsupportedAudio('Only one continuous island is admitted')
        if plan.end_frame < MIN_ISLAND:
            raise UnsupportedAudio('Standalone islands shorter than 123 native frames are unsupported')
        self.plan = plan
        self.plan_sha256 = plan.sha256
        self.native_end = plan.end_frame
        common = math.gcd(plan.sample_rate, RATE)
        self.p, self.q = RATE // common, plan.sample_rate // common
        self.temp_root = Path(temp_root).resolve(strict=True)
        if not self.temp_root.is_dir():
            raise ValueError('Explicit existing temporary directory required')
        self.executable = Path(executable).resolve(strict=True)
        before = _signature(self.executable)
        with self.executable.open('rb') as stream:
            binary_hash = _digest_stream(stream)
        if before != _signature(self.executable) or binary_hash != EXECUTABLE_SHA256:
            raise UnsupportedAudio('FFmpeg executable differs from pinned 7.1.5 build')
        self.executable_signature = before
        # The pinned binary emits bounded version text; reject dependency versions
        # different from the tested build. Library storage must also be immutable.
        version = subprocess.run([str(self.executable), '-version'], capture_output=True,
                                 timeout=10, check=True).stdout
        if len(version) > 16384 or hashlib.sha256(version).hexdigest() != VERSION_SHA256:
            raise UnsupportedAudio('FFmpeg build/library version fingerprint differs')
        self.reader = BoundedPCM16Reader(plan.sources, paths, plan)
        self.max_native_read = 0
        self.max_temp_bytes = 0
        self.max_tree_rss = 0
        self.invocations = 0
        self._verify_aliases()
        anchor = max(0, self.native_end - CONTEXT) // self.q * self.q
        tail, _ = self._native(anchor, self.native_end)
        output = self._resample(tail)
        self.canonical_end = anchor * self.p // self.q + len(output) // 2
        if abs(self.canonical_end * plan.sample_rate - self.native_end * RATE) > plan.sample_rate:
            raise UnsupportedAudio('Terminal output count violates pinned endpoint contract')
        self.verify()

    def verify(self):
        if _signature(self.executable) != self.executable_signature:
            raise SourceChanged('Pinned executable changed')
        for key, path in self.reader.paths.items():
            try:
                if _signature(path) != self.reader.signatures[key]:
                    raise SourceChanged('A bound original source changed')
            except OSError as exc:
                raise SourceChanged('A bound original source is unavailable') from exc

    def _native(self, start, end):
        if not 0 <= start < end <= self.native_end or end - start > MAX_NATIVE_READ:
            raise ValueError('Native replay exceeds bounded context contract')
        self.verify()
        owners = self.reader.mapping(start, end)
        pcm = self.reader.read(start, end)
        if len(pcm) != (end - start) * self.plan.channels * 2:
            raise SourceChanged('Short logical native PCM read')
        self.max_native_read = max(self.max_native_read, end - start)
        self.verify()
        return pcm, owners

    def _verify_aliases(self):
        # Serialized evidence is not permission to discard mismatching channels.
        for alias in self.plan.aliases:
            for start in range(alias.logical_start, alias.logical_end, MAX_NATIVE_READ):
                end = min(alias.logical_end, start + MAX_NATIVE_READ)
                owner, _ = self._native(start, end)
                local = alias.source_start + start - alias.logical_start
                redundant = self.reader.read_source(alias.fragment_id, local, local + end - start)
                if owner != redundant:
                    raise SourceChanged('Native all-channel alias differs from retained owners')
        self.verify()

    def _guard(self, child, directory):
        with open('/proc/meminfo') as stream:
            available = next(int(line.split()[1]) * 1024 for line in stream if line.startswith('MemAvailable:'))
        if available < 2 * 1024**3 or shutil.disk_usage(self.temp_root).free < 5905580032:
            raise RuntimeError('Host RAM/disk safety floor reached')
        rss = _rss(os.getpid()) + (_rss(child.pid) if child else 0)
        disk = sum(path.stat().st_size for path in directory.iterdir())
        self.max_tree_rss = max(self.max_tree_rss, rss)
        self.max_temp_bytes = max(self.max_temp_bytes, disk)
        if rss > 256 * 1024**2 or disk > 512 * 1024:
            raise RuntimeError('Normalizer RSS/temporary disk budget exceeded')

    def _resample(self, pcm):
        if not pcm or len(pcm) % (self.plan.channels * 2) or len(pcm) > MAX_NATIVE_READ * self.plan.channels * 2:
            raise ValueError('Invalid bounded native input')
        self.verify()
        with tempfile.TemporaryDirectory(prefix='fragment-normalizer-', dir=self.temp_root) as directory:
            directory = Path(directory)
            inp, out, err = (directory / name for name in ('in.wav', 'out.pcm', 'stderr'))
            self._guard(None, directory)
            with wave.open(str(inp), 'wb') as stream:
                stream.setnchannels(self.plan.channels)
                stream.setsampwidth(2)
                stream.setframerate(self.plan.sample_rate)
                stream.writeframes(pcm)
            af = (STEREO if self.plan.channels == 2 else '') + FILTER
            command = [str(self.executable), '-nostdin', '-hide_banner', '-v', 'error',
                       '-threads', '1', '-filter_threads', '1', '-filter_complex_threads', '1',
                       '-i', str(inp), '-map', '0:a:0', '-vn', '-af', af, '-ac', '1',
                       '-ar', str(RATE), '-c:a', 'pcm_s16le', '-threads', '1', '-f', 's16le', '-y', str(out)]
            child = None
            try:
                with err.open('wb') as stderr:
                    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=stderr, preexec_fn=_child_limits)
                    deadline = time.monotonic() + 15
                    while child.poll() is None:
                        self._guard(child, directory)
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Bounded FFmpeg replay timed out')
                        time.sleep(0.01)
                self._guard(None, directory)
                if child.returncode:
                    raise UnsupportedAudio(f'Pinned FFmpeg failed with status {child.returncode}')
                with out.open('rb') as stream:
                    output = stream.read(MAX_OUTPUT_BYTES + 1)
                if len(output) > MAX_OUTPUT_BYTES or len(output) % 2:
                    raise UnsupportedAudio('Pinned FFmpeg yielded invalid bounded output')
                self.invocations += 1
                self.verify()
                return output
            finally:
                if child is not None and child.poll() is None:
                    child.kill()
                    child.wait()

    def read(self, start, end):
        _integer(start, 'canonical start')
        _integer(end, 'canonical end')
        if not 0 <= start <= end <= self.canonical_end or end - start > MAX_READ:
            raise ValueError('Canonical request must be inside finalized extent and <=8000 frames')
        self.verify()
        if start == end:
            return NormalizedWindow(start, end, b'', 0, 0, (), (), self.plan_sha256,
                                    FINGERPRINT, self.native_end, self.canonical_end)
        near = start * self.q // self.p
        anchor = max(0, near - CONTEXT) // self.q * self.q
        limit = min(self.native_end, _ceil(end * self.q, self.p) + CONTEXT)
        pcm, owners = self._native(anchor, limit)
        output = self._resample(pcm)
        offset = anchor * self.p // self.q
        a, b = start - offset, end - offset
        cropped = output[a * 2:b * 2]
        if a < 0 or len(cropped) != (end - start) * 2:
            raise UnsupportedAudio('Exact canonical requested count not emitted; no padding permitted')
        # These are native context-support records, not a samplewise filter map.
        owner_records = tuple({'span': asdict(part), 'source_sha256': self.reader.sources[part.fragment_id].sha256}
                              for part in owners)
        alias_records = tuple({'alias': asdict(alias), 'source_sha256': self.reader.sources[alias.fragment_id].sha256}
                              for alias in self.plan.aliases
                              if alias.logical_start < limit and alias.logical_end > anchor)
        return NormalizedWindow(start, end, cropped, anchor, limit, owner_records, alias_records,
                                self.plan_sha256, FINGERPRINT, self.native_end, self.canonical_end)
