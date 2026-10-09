"""Isolated, read-only compressed-source observations. No runtime/SourcePlan hooks.

PCM coordinates are zero-based half-open *emitted* interleaved PCM16 frames.
Presentation is separate. No cropping, source aliasing, or capture-time inference.
Only controlled immutable local files and the pinned FFmpeg build are supported.
"""
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import shutil
import stat
import struct
import subprocess
import time

from .fragment_core import SourceChanged, UnsupportedAudio, _canonical

FFMPEG_SHA = 'e8a8d46f5225f3062cec7c07fb145d58ae73c603cb740dcd5bad34bfb54e455a'
FFMPEG_VERSION_SHA = 'b241942a091ee91966e5b04017a34f1f2a3bcfb1cf80a195e274db03874459d9'
FFPROBE_SHA = 'cefc5f81c0b18429cdbf8fa5a89e1e9c759235e10f93f9b329ff1b2fdff26f46'
FFPROBE_VERSION_SHA = '02412fef1eca820a65af53c5fa03b637f92ef643783005ccf9c4b4e3a31fb699'
RECIPE = ('decoded-source-s16le-v1;native-rate;native-channels;sequential-start;'
          'native-decoder;default-demux-skip-discard;no-async;no-seek;no-user-filter;threads=1')
CHUNK = 65536
MAX_METADATA = 1048576
MAX_PACKET = 65536
MAX_RECEIPT = 16384


class DecodeCancelled(RuntimeError):
    pass


class DecodeLimit(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    """Wall/output limits are per operation; CPU is per subprocess.

    Parent-process CPU has no independent cap; wall time bounds the operation.
    RSS is sampled, not an OS-hard bound.
    """
    max_source_bytes: int = 64 * 1024 * 1024
    max_frames: int = 48000 * 600
    max_read_frames: int = 48000
    wall_seconds: float = 30.0
    cpu_seconds: int = 20
    rss_bytes: int = 256 * 1024 * 1024
    host_available_bytes: int = 2 * 1024**3
    disk_reserve_bytes: int = 11 * 1024**3 // 2
    cpu: int = 7

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == 'wall_seconds':
                if type(value) not in (int, float) or not 0 < value <= 120:
                    raise ValueError('wall_seconds must be finite and in (0,120]')
            elif type(value) is not int or value < (0 if name == 'cpu' else 1):
                raise ValueError(f'Invalid limit: {name}')
        if self.max_read_frames > 48000 or self.max_frames > 48000 * 3600:
            raise ValueError('Read <=48000 frames; decode <=172800000 frames, subject to wall budget')
        if self.max_source_bytes > 1024**3 or self.rss_bytes > 256 * 1024**2:
            raise ValueError('Source <=1 GiB and sampled RSS <=256 MiB')


@dataclass(frozen=True)
class RationalSpan:
    """Times are rational seconds, not presumed PCM indexes; media=-1 means empty edit."""
    duration_num: int
    duration_den: int
    media_start_num: int
    media_start_den: int
    empty: bool = False
    unit_rate: bool = True


@dataclass(frozen=True)
class Accounting:
    packet_count: int
    packet_duration_ticks: int
    time_base_num: int
    time_base_den: int
    signaled_skip_frames: int
    signaled_discard_frames: int
    skip_signal_packets: int
    discard_signal_packets: int
    coded_frames: int | None = None
    container_pre_skip: int | None = None
    container_end_discard: int | None = None


@dataclass(frozen=True)
class DecoderIdentity:
    executable_sha256: str
    version_sha256: str
    probe_sha256: str
    probe_version_sha256: str
    codec: str
    recipe: str = RECIPE
    shared_libraries: str = 'trusted immutable environment; not individually hashed'


@dataclass(frozen=True)
class DecodedSourceReceipt:
    compressed_sha256: str
    compressed_bytes: int
    decoder: DecoderIdentity
    sample_rate: int
    channels: int
    emitted_frames: int
    emitted_pcm_sha256: str
    presentation: tuple[RationalSpan, ...]
    accounting: Accounting
    structural_status: str
    endpoint_status: str
    container_json: str
    original_preencoding_frames: None = None
    capture_continuity: str = 'unproven'
    coordinate_space: str = 'decoder-emitted-pcm16le-v1'
    automatic_admission: bool = False

    def __post_init__(self):
        for value in (self.compressed_sha256, self.emitted_pcm_sha256):
            if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Receipt requires lowercase SHA256 identities')
        if (type(self.compressed_bytes) is not int or not 0 < self.compressed_bytes <= 1024**3 or
                type(self.emitted_frames) is not int or not 0 < self.emitted_frames <= 48000 * 3600 or
                self.sample_rate not in (44100, 48000) or self.channels not in (1, 2)):
            raise ValueError('Receipt source/output format or size outside contract')
        if (type(self.presentation) is not tuple or len(self.presentation) > 16 or
                any(type(span) is not RationalSpan for span in self.presentation)):
            raise ValueError('Receipt requires at most 16 immutable presentation spans')
        if type(self.container_json) is not str or len(self.container_json) > 8192:
            raise ValueError('Receipt container metadata bound')
        for value in (self.structural_status, self.endpoint_status):
            if type(value) is not str or len(value) > 256:
                raise ValueError('Receipt status metadata bound')
        if (type(self.decoder) is not DecoderIdentity or type(self.accounting) is not Accounting or
                self.original_preencoding_frames is not None or self.capture_continuity != 'unproven' or
                self.coordinate_space != 'decoder-emitted-pcm16le-v1' or self.automatic_admission is not False):
            raise ValueError('Receipt cannot assert original count, continuity, or admission')
        for value in asdict(self.decoder).values():
            if type(value) is not str or len(value) > 256:
                raise ValueError('Decoder identity metadata bound')
        for value in asdict(self.accounting).values():
            if value is not None and (type(value) is not int or not 0 <= value < 2**63):
                raise ValueError('Invalid bounded accounting aggregate')
        for span in self.presentation:
            values = (span.duration_num, span.duration_den, span.media_start_num, span.media_start_den)
            if (any(type(x) is not int or not -1 <= x < 2**64 for x in values) or
                    span.duration_num < 0 or span.duration_den <= 0 or span.media_start_den <= 0 or
                    type(span.empty) is not bool or type(span.unit_rate) is not bool):
                raise ValueError('Invalid rational presentation span')

    @property
    def sha256(self):
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_json(self):
        value = _canonical(asdict(self))
        if len(value.encode()) > MAX_RECEIPT:
            raise DecodeLimit('Receipt exceeds fixed metadata bound')
        return value


def _signature(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _rss(pid):
    try:
        with open(f'/proc/{pid}/status') as stream:
            return next(int(line.split()[1]) * 1024 for line in stream if line.startswith('VmRSS:'))
    except (OSError, StopIteration):
        return 0


class _Guard:
    def __init__(self, limits, path, cancel):
        self.limits, self.path, self.cancel = limits, path, cancel
        self.started = time.monotonic()
        self.max_rss = 0

    def check(self, child=None):
        if self.cancel is not None and self.cancel():
            raise DecodeCancelled('Decode cancelled; no completed receipt or range')
        if time.monotonic() - self.started > self.limits.wall_seconds:
            raise DecodeLimit('Operation wall-time limit; replay work grows with source duration')
        rss = _rss(os.getpid()) + (_rss(child.pid) if child is not None else 0)
        self.max_rss = max(self.max_rss, rss)
        if rss > self.limits.rss_bytes:
            raise DecodeLimit('Sampled own process-tree RSS limit')
        with open('/proc/meminfo') as stream:
            available = next(int(line.split()[1]) * 1024 for line in stream if line.startswith('MemAvailable:'))
        if available < self.limits.host_available_bytes:
            raise DecodeLimit('Host available-memory reserve')
        if shutil.disk_usage(self.path.parent).free < self.limits.disk_reserve_bytes:
            raise DecodeLimit('Host free-disk reserve')


def _digest(stream, guard):
    stream.seek(0)
    h = hashlib.sha256()
    while block := stream.read(CHUNK):
        guard.check()
        h.update(block)
    stream.seek(0)
    return h.hexdigest()


def _run(command, guard, *, fd=None, output_limit):
    """Pull-driven, bounded pipes; no queue or file grows with decoded duration.

    Closing the generator kills/reaps the subprocess. Consumer pauses exert OS
    pipe backpressure. Cancellation is observed while consuming, or by close().
    """
    def child_limits():
        os.sched_setaffinity(0, {guard.limits.cpu})
        resource.setrlimit(resource.RLIMIT_CPU, (guard.limits.cpu_seconds,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

    guard.check()
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, pass_fds=(() if fd is None else (fd,)),
                             preexec_fn=child_limits, bufsize=0)
    count = 0
    errors = bytearray()
    sel = selectors.DefaultSelector()
    try:
        for stream in (child.stdout, child.stderr):
            os.set_blocking(stream.fileno(), False)
            sel.register(stream, selectors.EVENT_READ)
        while sel.get_map():
            guard.check(child)
            for key, _ in sel.select(.05):
                block = os.read(key.fd, CHUNK)
                if not block:
                    sel.unregister(key.fileobj)
                    continue
                if key.fileobj is child.stderr:
                    if len(errors) + len(block) > 8192:
                        raise DecodeLimit('Decoder diagnostic metadata limit')
                    errors.extend(block)
                else:
                    count += len(block)
                    if count > output_limit:
                        raise DecodeLimit('Subprocess output limit')
                    yield block
        while child.poll() is None:
            guard.check(child)
            time.sleep(.01)
        if child.returncode or errors:
            # -v error means even recovered decoder errors reject the observation.
            raise UnsupportedAudio(f'Decoder/probe failed or reported errors: {bytes(errors).decode(errors="replace")[:1000]}')
    finally:
        sel.close()
        if child.poll() is None:
            child.kill()
        child.wait()
        child.stdout.close()
        child.stderr.close()


def _collect(command, guard, *, fd=None, cap=32768):
    return b''.join(_run(command, guard, fd=fd, output_limit=cap))


def _tools(guard):
    result = []
    for name, expected, version_expected in [('ffmpeg', FFMPEG_SHA, FFMPEG_VERSION_SHA),
                                              ('ffprobe', FFPROBE_SHA, FFPROBE_VERSION_SHA)]:
        path = Path('/usr/bin') / name
        before = _signature(path.stat())
        with path.open('rb') as stream:
            actual = _digest(stream, guard)
        version = _collect([str(path), '-version'], guard, cap=16384)
        if before != _signature(path.stat()) or actual != expected or hashlib.sha256(version).hexdigest() != version_expected:
            raise UnsupportedAudio('Pinned FFmpeg 7.1.5 executable/build identity differs')
        result.append((path, before))
    return result


def _verify_tools(tools):
    if any(_signature(path.stat()) != before for path, before in tools):
        raise SourceChanged('Pinned decoder changed')


def _read(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise UnsupportedAudio('Truncated container structure')
    return data


def _crc_table():
    result = []
    for value in range(256):
        crc = value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
        result.append(crc)
    return tuple(result)


_CRC_TABLE = _crc_table()


def _ogg_crc(page):
    crc = 0
    for value in page:
        crc = ((crc << 8) ^ _CRC_TABLE[((crc >> 24) ^ value) & 255]) & 0xFFFFFFFF
    return crc


def _opus_frames(packet):
    if not packet:
        raise UnsupportedAudio('Empty Opus audio packet')
    config, code = packet[0] >> 3, packet[0] & 3
    if config >= 16:
        size = 120 << (config & 3)
    elif config >= 12:
        size = 480 << (config & 1)
    else:
        size = 2880 if config & 3 == 3 else 480 << (config & 3)
    if code == 3 and len(packet) < 2:
        raise UnsupportedAudio('Truncated Opus frame-count header')
    count = 1 if code == 0 else ((packet[1] & 63) if code == 3 else 2)
    if not count or count * size > 5760:
        raise UnsupportedAudio('Invalid Opus packet duration')
    return count * size


def _ogg(stream, guard):
    """Narrow mapping-family-0, zero-granule-origin, single logical Opus stream."""
    stream.seek(0)
    sequence = packets = coded = last_packet = prior_granule = 0
    serial = head = None
    partial = bytearray()
    eos = False
    while True:
        guard.check()
        header = stream.read(27)
        if not header:
            break
        if eos or len(header) != 27 or header[:5] != b'OggS\x00':
            raise UnsupportedAudio('Trailing/chained or malformed Ogg page')
        flags, granule, this_serial, seq, crc, segments = struct.unpack_from('<BQIIIB', header, 5)
        if flags & ~7 or bool(flags & 1) != bool(partial):
            raise UnsupportedAudio('Invalid Ogg flags/continuation')
        if sequence == 0:
            serial = this_serial
            if flags != 2 or seq != 0:
                raise UnsupportedAudio('First Ogg page must be BOS sequence zero')
        elif flags & 2 or this_serial != serial:
            raise UnsupportedAudio('Multiplexed/chained Ogg is outside contract')
        if seq != sequence:
            raise UnsupportedAudio('Ogg sequence discontinuity')
        sequence += 1
        lacing = _read(stream, segments)
        payload = _read(stream, sum(lacing))
        page = bytearray(header + lacing + payload)
        page[22:26] = b'\x00' * 4
        if _ogg_crc(page) != crc:
            raise UnsupportedAudio('Ogg CRC mismatch')
        position = completed_audio = 0
        before_packets = packets
        for index, length in enumerate(lacing):
            if len(partial) + length > MAX_PACKET:
                raise DecodeLimit('Ogg packet/comment metadata bound')
            partial.extend(payload[position:position + length])
            position += length
            if length == 255:
                continue
            packet = bytes(partial)
            partial.clear()
            packets += 1
            if packets == 1:
                if len(packet) != 19 or packet[:8] != b'OpusHead' or packet[8] != 1 or packet[18] != 0 or packet[9] not in (1, 2):
                    raise UnsupportedAudio('Only Opus v1 mono/stereo mapping family zero')
                if index != len(lacing) - 1 or sequence != 1:
                    raise UnsupportedAudio('OpusHead must occupy first page alone')
                head = {'channels': packet[9], 'pre_skip': struct.unpack_from('<H', packet, 10)[0],
                        'input_rate_hint': struct.unpack_from('<I', packet, 12)[0],
                        'gain_q8_db': struct.unpack_from('<h', packet, 16)[0]}
            elif packets == 2:
                if not packet.startswith(b'OpusTags') or len(packet) < 16 or index != len(lacing) - 1:
                    raise UnsupportedAudio('Invalid/badly placed OpusTags')
                # Validate comment lengths without retaining tags in the receipt.
                vendor = struct.unpack_from('<I', packet, 8)[0]
                at = 12 + vendor
                if at + 4 > len(packet):
                    raise UnsupportedAudio('Truncated OpusTags vendor')
                comments = struct.unpack_from('<I', packet, at)[0]
                at += 4
                if comments > MAX_PACKET // 4:
                    raise DecodeLimit('OpusTags count limit')
                for _ in range(comments):
                    if at + 4 > len(packet):
                        raise UnsupportedAudio('Truncated OpusTags comment')
                    size = struct.unpack_from('<I', packet, at)[0]
                    at += 4 + size
                    if at > len(packet):
                        raise UnsupportedAudio('Truncated OpusTags content')
            else:
                last_packet = _opus_frames(packet)
                coded += last_packet
                completed_audio += 1
                if coded > guard.limits.max_frames + 65536 + 5760:
                    raise DecodeLimit('Opus coded-frame budget')
        eos = bool(flags & 4)
        if packets <= 2:
            if eos or granule != (0 if packets > before_packets else 0xFFFFFFFFFFFFFFFF):
                raise UnsupportedAudio('Invalid header granule/EOS')
        elif completed_audio:
            if eos:
                if partial or not max(prior_granule, head['pre_skip'], coded - last_packet) <= granule <= coded:
                    raise UnsupportedAudio('Unsupported/inconsistent terminal Opus granule')
            elif granule != coded:
                raise UnsupportedAudio('Nonzero-origin/cropped Opus granules outside narrow contract')
            prior_granule = granule
        elif granule != 0xFFFFFFFFFFFFFFFF or eos:
            raise UnsupportedAudio('Invalid page without completed audio packet')
    if not eos or partial or packets < 3:
        raise UnsupportedAudio('Incomplete Opus stream: complete EOS is required')
    stream.seek(0)
    return {'kind': 'ogg-opus', 'pages': sequence, 'audio_packets': packets - 2,
            'coded_frames': coded, 'final_granule': prior_granule,
            'end_discard': coded - prior_granule, **head}


def _mp4(stream, guard, size):
    """Bound-check boxes and retain bounded timing only; not a full MP4 integrity proof."""
    stream.seek(0)
    found = {}
    count = 0
    metadata_bytes = 0
    def boxes(start, end, parent='', depth=0):
        nonlocal count, metadata_bytes
        if depth > 8:
            raise DecodeLimit('MP4 nesting bound')
        at = start
        while at < end:
            guard.check()
            if end - at < 8:
                raise UnsupportedAudio('Incomplete MP4 box header')
            stream.seek(at)
            length, kind = struct.unpack('>I4s', _read(stream, 8))
            header = 8
            if length == 1:
                if end - at < 16:
                    raise UnsupportedAudio('Incomplete extended MP4 header')
                length = struct.unpack('>Q', _read(stream, 8))[0]
                header = 16
            if length == 0:
                # EOF-sized media is not proof of finalization in this profile.
                raise UnsupportedAudio('Open-ended MP4 box outside finalized-box contract')
            if length < header or length > end - at:
                raise UnsupportedAudio('MP4 box exceeds parent/file')
            count += 1
            if count > 4096:
                raise DecodeLimit('MP4 box-count bound')
            path = parent + '/' + kind.decode('latin1')
            payload, stop = at + header, at + length
            if kind in (b'moof', b'mvex'):
                raise UnsupportedAudio('Fragmented MP4 outside contract')
            if kind in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'edts'):
                if kind == b'moov' and length > MAX_METADATA:
                    raise DecodeLimit('MP4 moov metadata bound')
                found.setdefault(path, []).append(None)
                boxes(payload, stop, path, depth + 1)
            elif kind in (b'mvhd', b'mdhd', b'elst', b'stts', b'stsz', b'hdlr'):
                metadata_bytes += stop - payload
                if metadata_bytes > MAX_METADATA:
                    raise DecodeLimit('MP4 timing metadata bound')
                stream.seek(payload)
                data = _read(stream, stop - payload)
                found.setdefault(path, []).append(data)
            elif kind in (b'ftyp', b'mdat'):
                found.setdefault(path, []).append((payload, stop))
            at = stop
    boxes(0, size)
    def one(path):
        values = found.get(path, [])
        if len(values) != 1:
            raise UnsupportedAudio('Exactly one MP4 movie/audio track/timing field required: ' + path)
        return values[0]
    one('/moov'); one('/moov/trak'); one('/ftyp')
    if not found.get('/mdat'):
        raise UnsupportedAudio('MP4 media box missing')
    handler = one('/moov/trak/mdia/hdlr')
    if len(handler) < 12 or handler[8:12] != b'soun':
        raise UnsupportedAudio('MP4 requires a single audio track')
    def timing(path):
        data = one(path)
        if not data or data[0] not in (0, 1):
            raise UnsupportedAudio('Unsupported MP4 timing version')
        off = 20 if data[0] else 12
        if len(data) < off + (12 if data[0] else 8):
            raise UnsupportedAudio('Truncated MP4 timing')
        scale = struct.unpack_from('>I', data, off)[0]
        duration = struct.unpack_from('>Q' if data[0] else '>I', data, off + 4)[0]
        if not scale:
            raise UnsupportedAudio('Zero MP4 timescale')
        return scale, duration
    movie_scale, movie_duration = timing('/moov/mvhd')
    track_scale, track_duration = timing('/moov/trak/mdia/mdhd')
    edit_values = found.get('/moov/trak/edts/elst', [])
    if len(edit_values) > 1:
        raise UnsupportedAudio('Multiple edit lists')
    edits = []
    if edit_values:
        data = edit_values[0]
        if len(data) < 8 or data[0] not in (0, 1):
            raise UnsupportedAudio('Invalid MP4 edit list')
        n = struct.unpack_from('>I', data, 4)[0]
        width = 20 if data[0] else 12
        if n > 16 or len(data) != 8 + n * width:
            raise UnsupportedAudio('Unbounded/truncated MP4 edit list')
        for i in range(n):
            duration, media, ri, rf = struct.unpack_from('>Qqhh' if data[0] else '>Iihh', data, 8 + i * width)
            edits.append((duration, media, ri, rf))
    stts = one('/moov/trak/mdia/minf/stbl/stts')
    if len(stts) < 8:
        raise UnsupportedAudio('Truncated MP4 sample timing')
    n = struct.unpack_from('>I', stts, 4)[0]
    if n > 65536 or len(stts) != 8 + 8 * n:
        raise UnsupportedAudio('Invalid MP4 sample timing count')
    samples = ticks = 0
    for i in range(n):
        c, d = struct.unpack_from('>II', stts, 8 + 8 * i)
        samples += c
        ticks += c * d
    stream.seek(0)
    return {'kind': 'mp4-aac', 'movie_timescale': movie_scale, 'movie_duration_ticks': movie_duration,
            'track_timescale': track_scale, 'track_duration_ticks': track_duration,
            'edits': edits, 'stts_samples': samples, 'stts_duration_ticks': ticks,
            'box_count': count, 'media_payload_integrity': 'not independently verified'}


def _input(fd, kind):
    args = ['-protocol_whitelist', 'file,pipe']
    if kind == 'mp4-aac':
        args += ['-enable_drefs', '0', '-use_absolute_path', '0']
    return args + ['-i', f'/proc/self/fd/{fd}']


def _probe(fd, kind, guard):
    data = _collect(['/usr/bin/ffprobe', '-v', 'error', *_input(fd, kind),
                     '-show_entries', 'stream=index,codec_name,profile,sample_rate,channels,time_base,start_pts,duration_ts',
                     '-of', 'json'], guard, fd=fd)
    streams = json.loads(data).get('streams', [])
    if len(streams) != 1:
        raise UnsupportedAudio('Exactly one stream required')
    stream = streams[0]
    rate, channels = int(stream.get('sample_rate', 0)), int(stream.get('channels', 0))
    if rate not in (44100, 48000) or channels not in (1, 2):
        raise UnsupportedAudio('Only native 44.1/48 kHz mono/stereo')
    expected = 'opus' if kind == 'ogg-opus' else 'aac'
    if stream.get('codec_name') != expected or (expected == 'aac' and stream.get('profile') != 'LC'):
        raise UnsupportedAudio('Only pinned AAC-LC or Opus decoder')
    tb = Fraction(stream['time_base'])
    if tb <= 0 or tb.numerator > 2**32 or tb.denominator > 2**32:
        raise UnsupportedAudio('Invalid stream time base')
    return stream, rate, channels, tb


def _account(fd, kind, guard, tb):
    command = ['/usr/bin/ffprobe', '-v', 'error', *_input(fd, kind), '-select_streams', 'a:0',
               '-show_packets', '-show_entries', 'packet=pts,duration,side_data_list', '-of', 'compact']
    pending = bytearray()
    packets = duration = skip = discard = skip_n = discard_n = 0
    for block in _run(command, guard, fd=fd, output_limit=32 * 1024 * 1024):
        pending.extend(block)
        while b'\n' in pending:
            line, _, pending = pending.partition(b'\n')
            if len(line) > 4096:
                raise DecodeLimit('Packet metadata line bound')
            if not line:
                continue
            if not line.startswith(b'packet|'):
                raise UnsupportedAudio('Unexpected packet metadata')
            fields = {}
            for field in line.decode('ascii').split('|')[1:]:
                if '=' in field:
                    key, value = field.split('=', 1)
                    fields[key.rsplit(':', 1)[-1]] = value
            packets += 1
            if packets > 1000000:
                raise DecodeLimit('Packet-count bound')
            duration += int(fields.get('duration', '0'))
            s, d = int(fields.get('skip_samples', '0')), int(fields.get('discard_padding', '0'))
            if s < 0 or d < 0:
                raise UnsupportedAudio('Negative skip/discard metadata')
            skip += s; discard += d
            skip_n += int('skip_samples' in fields)
            discard_n += int('discard_padding' in fields)
        if len(pending) > 4096:
            raise DecodeLimit('Unterminated packet metadata line bound')
    if pending:
        raise UnsupportedAudio('Incomplete probe metadata line')
    return Accounting(packets, duration, tb.numerator, tb.denominator, skip, discard, skip_n, discard_n)


def _pcm(fd, kind, guard, channels):
    command = ['/usr/bin/ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-threads', '1',
               '-filter_threads', '1', '-filter_complex_threads', '1',
               '-c:a', 'opus' if kind == 'ogg-opus' else 'aac', *_input(fd, kind),
               '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', '-threads', '1', '-f', 's16le', 'pipe:1']
    pending = b''
    frame_bytes = channels * 2
    for block in _run(command, guard, fd=fd, output_limit=guard.limits.max_frames * frame_bytes):
        data = pending + block
        complete = len(data) // frame_bytes * frame_bytes
        if complete:
            yield data[:complete]
        pending = data[complete:]
    if pending:
        raise UnsupportedAudio('Partial emitted PCM frame')


class _Source:
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
            self.tools = _tools(self.guard)
            self.verify()
        except BaseException:
            self.file.close()
            raise

    def verify(self, rehash=False):
        if (_signature(os.fstat(self.file.fileno())) != self.signature or
                _signature(self.path.stat()) != self.signature):
            raise SourceChanged('Compressed source changed/replaced')
        if rehash and _digest(self.file, self.guard) != self.hash:
            raise SourceChanged('Compressed source hash changed')
        if _signature(os.fstat(self.file.fileno())) != self.signature or _signature(self.path.stat()) != self.signature:
            raise SourceChanged('Compressed source changed during verification')
        _verify_tools(self.tools)

    def close(self):
        self.file.close()


def inspect_decoded_source(path, *, enabled=False, limits=None, cancel=None):
    """Hash full sequential decoder output; preserve all emitted frames.

    Structural completeness does NOT prove original capture continuity. AAC is
    always unresolved; Opus agreement proves this narrow decoder/presentation
    count only. Neither outcome authorizes a deduplication or timeline edit.
    """
    if enabled is not True:
        raise UnsupportedAudio('Experimental compressed receipts are default OFF')
    source = _Source(path, limits or Limits(), cancel)
    try:
        start = source.file.read(8)
        source.file.seek(0)
        if start[:4] == b'OggS':
            container = _ogg(source.file, source.guard)
        elif start[4:8] == b'ftyp':
            container = _mp4(source.file, source.guard, source.signature[2])
        else:
            raise UnsupportedAudio('Only finalized single-stream Ogg Opus or bounded AAC MP4')
        kind, fd = container['kind'], source.file.fileno()
        stream, rate, channels, tb = _probe(fd, kind, source.guard)
        accounting = _account(fd, kind, source.guard, tb)
        digest, frames = hashlib.sha256(), 0
        for block in _pcm(fd, kind, source.guard, channels):
            digest.update(block)
            frames += len(block) // (channels * 2)
        if not frames:
            raise UnsupportedAudio('No emitted PCM')
        if kind == 'ogg-opus':
            if rate != 48000 or channels != container['channels']:
                raise UnsupportedAudio('Opus head/probe mismatch')
            expected = container['final_granule'] - container['pre_skip']
            if (frames != expected or accounting.packet_count != container['audio_packets'] or
                    accounting.signaled_skip_frames != container['pre_skip'] or
                    accounting.signaled_discard_frames != container['end_discard']):
                raise UnsupportedAudio('Opus decoder/container accounting mismatch')
            accounting = Accounting(**{**asdict(accounting), 'coded_frames': container['coded_frames'],
                                      'container_pre_skip': container['pre_skip'],
                                      'container_end_discard': container['end_discard']})
            presentation = (RationalSpan(expected, 48000, 0, 1),)
            structural = 'single-stream-sequence-crc-eos-verified'
            endpoint = 'emitted-count-agrees-with-zero-origin-opus-presentation'
        else:
            presentation = tuple(RationalSpan(d, container['movie_timescale'], m, container['track_timescale'],
                                              m == -1, (ri, rf) == (1, 0))
                                 for d, m, ri, rf in container['edits'])
            structural = 'box-bounds-complete; sample-extents-not-independently-verified'
            endpoint = 'unresolved-aac-presentation-versus-emitted-pcm'
        container['demux_time_base'] = [tb.numerator, tb.denominator]
        container['demux_start_pts'] = stream.get('start_pts')
        container['demux_duration_ts'] = stream.get('duration_ts')
        receipt = DecodedSourceReceipt(source.hash, source.signature[2],
                    DecoderIdentity(FFMPEG_SHA, FFMPEG_VERSION_SHA, FFPROBE_SHA, FFPROBE_VERSION_SHA, stream['codec_name']),
                    rate, channels, frames, digest.hexdigest(), presentation, accounting, structural, endpoint,
                    _canonical(container))
        source.verify(rehash=True)
        receipt.to_json()
        return receipt
    finally:
        source.close()


class SequentialDecodedReader:
    """Bounded-memory exact emitted ranges, never presentation-range trimming.

    Every read rehashes the source and replays the FULL sequential decode, checks
    its count/hash against the receipt, then returns <=48000 requested frames.
    Cost is O(compressed bytes + full emitted duration) per call, even near the
    beginning. No bounded seek latency, cache, or hour-long performance claim.
    """
    def __init__(self, path, receipt, *, enabled=False, limits=None):
        if enabled is not True:
            raise UnsupportedAudio('Experimental compressed reader is default OFF')
        if not isinstance(receipt, DecodedSourceReceipt):
            raise TypeError('DecodedSourceReceipt required')
        self.path, self.receipt, self.limits = Path(path), receipt, limits or Limits()
        expected = DecoderIdentity(FFMPEG_SHA, FFMPEG_VERSION_SHA, FFPROBE_SHA, FFPROBE_VERSION_SHA, receipt.decoder.codec)
        if receipt.decoder != expected or receipt.decoder.codec not in ('aac', 'opus'):
            raise UnsupportedAudio('Receipt decoder contract mismatch')
        if receipt.sample_rate not in (44100, 48000) or receipt.channels not in (1, 2):
            raise UnsupportedAudio('Receipt PCM format mismatch')
        if (receipt.coordinate_space != 'decoder-emitted-pcm16le-v1' or
                type(receipt.emitted_frames) is not int or not 0 < receipt.emitted_frames <= self.limits.max_frames):
            raise UnsupportedAudio('Receipt emitted coordinate contract mismatch')
        receipt.to_json()

    def read(self, start, end, *, cancel=None):
        if (type(start) is not int or type(end) is not int or
                not 0 <= start < end <= self.receipt.emitted_frames or end - start > self.limits.max_read_frames):
            raise ValueError('Range must be bounded half-open emitted PCM frames')
        source = _Source(self.path, self.limits, cancel)
        try:
            if source.hash != self.receipt.compressed_sha256 or source.signature[2] != self.receipt.compressed_bytes:
                raise SourceChanged('Compressed source identity differs from receipt')
            frame_bytes = self.receipt.channels * 2
            output = bytearray()
            digest, position = hashlib.sha256(), 0
            kind = 'ogg-opus' if self.receipt.decoder.codec == 'opus' else 'mp4-aac'
            stream, rate, channels, _ = _probe(source.file.fileno(), kind, source.guard)
            if (rate, channels, stream['codec_name']) != (self.receipt.sample_rate, self.receipt.channels, self.receipt.decoder.codec):
                raise SourceChanged('Receipt native PCM format differs from source')
            for block in _pcm(source.file.fileno(), kind, source.guard, self.receipt.channels):
                digest.update(block)
                next_position = position + len(block) // frame_bytes
                lo, hi = max(start, position), min(end, next_position)
                if lo < hi:
                    output.extend(block[(lo - position) * frame_bytes:(hi - position) * frame_bytes])
                position = next_position
            source.verify(rehash=True)
            if position != self.receipt.emitted_frames or digest.hexdigest() != self.receipt.emitted_pcm_sha256:
                raise SourceChanged('Sequential emitted PCM differs from receipt')
            if len(output) != (end - start) * frame_bytes:
                raise SourceChanged('Short emitted PCM range')
            return bytes(output)
        finally:
            source.close()
