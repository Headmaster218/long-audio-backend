"""Read-only local transport preflight for Android RECORDER-DELIVERY-V1.

A hash-consistent local observation is not immutable publication, trusted capture
provenance, metadata semantic validation, or permission to ingest/delete files.
No application, model, database, network, or uploader imports occur here.
"""
from contextlib import ExitStack
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import time

MAX_METADATA = 8192 + 512
MAX_MARKER = 1024
MAX_WAV = 512 * 1024 * 1024
BLOCK = 65536
HEX = r'[0-9a-f]{64}'
SOURCE = r'segment-[0-9a-f]{64}-(0|[1-9][0-9]*)\.ready'


class Refused(ValueError):
    """Untrusted or inconsistent delivery; callers must not advance ingestion."""


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def parse_marker(data):
    require(len(data) <= MAX_MARKER, 'marker_size')
    try:
        lines = data.decode('ascii').split('\n')
    except UnicodeError as exc:
        raise Refused('marker_ascii') from exc
    require(len(lines) == 9 and lines[-1] == '' and lines[0] == 'RECORDER-DELIVERY-V1', 'marker_format')
    _, attempt, revision, source, size, wav_hash, metadata_size, metadata_hash, _ = lines
    require(re.fullmatch(r'r-' + HEX + r'-[0-9a-f]{32}', attempt), 'attempt_format')
    require(re.fullmatch(r'[A-Za-z0-9-]{1,128}', revision), 'revision_format')
    require(re.fullmatch(SOURCE, source), 'source_format')
    require(int(source[73:-6]) < 2**63 - 1, 'source_ordinal')
    require(re.fullmatch(HEX, wav_hash) and re.fullmatch(HEX, metadata_hash), 'marker_hash')
    require(re.fullmatch(r'[1-9][0-9]{0,10}', size) and re.fullmatch(r'[1-9][0-9]{0,4}', metadata_size), 'marker_lengths')
    require(44 < int(size) <= MAX_WAV and 12 <= int(metadata_size) <= MAX_METADATA, 'marker_bounds')
    binding = hashlib.sha256((source + '\n' + wav_hash + '\n' + revision).encode('ascii')).hexdigest()
    require(attempt[2:66] == binding, 'attempt_binding')
    return dict(attempt=attempt, revision=revision, source_id=source, wav_bytes=int(size),
                wav_sha256=wav_hash, metadata_bytes=int(metadata_size), metadata_sha256=metadata_hash)


def parse_envelope(data, marker):
    require(len(data) == marker['metadata_bytes'] and hashlib.sha256(data).hexdigest() == marker['metadata_sha256'], 'envelope_hash_or_size')
    require(data[:4] == b'RFT1', 'envelope_magic')
    offset = 4
    # Both transport fields are ASCII by producer contract, therefore their
    # canonical Java modified-UTF8 representation is ordinary ASCII with u16 length.
    def text():
        nonlocal offset
        require(offset + 2 <= len(data), 'envelope_truncated')
        count = struct.unpack_from('>H', data, offset)[0]; offset += 2
        require(count <= 128 and offset + count <= len(data), 'envelope_text_size')
        try:
            result = data[offset:offset + count].decode('ascii')
        except UnicodeError as exc:
            raise Refused('envelope_ascii') from exc
        offset += count
        return result
    require(text() == marker['revision'] and text() == marker['source_id'], 'envelope_identity')
    require(offset + 4 <= len(data), 'envelope_truncated')
    count = struct.unpack_from('>i', data, offset)[0]; offset += 4
    require(40 <= count <= 8192 and offset + count == len(data), 'metadata_size')
    local = data[offset:]
    require(local[:4] == b'PCM1', 'metadata_magic')
    version = struct.unpack_from('>i', local, 4)[0]
    require(version in (1, 2), 'metadata_version')
    require(hashlib.sha256(local[:-32]).digest() == local[-32:], 'metadata_checksum')
    return version


def _stamp(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def inspect_delivery(directory, *, deadline_seconds=120):
    """Observe three held regular files without writes; never emit an import plan.

    Caller supplies an already-downloaded directory. Parent path resolution and
    bytes remain under caller control. The result expires immediately: a later
    importer must use its own owned snapshot and complete metadata validation.
    """
    require(type(deadline_seconds) in (int, float) and 0 < deadline_seconds <= 120, 'deadline_bounds')
    deadline = time.monotonic() + deadline_seconds
    def check():
        require(time.monotonic() <= deadline, 'deadline_exceeded')
    names = ('delivery.v1.txt', 'segment.v1.bin', 'audio.wav')
    with ExitStack() as stack:
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, dfd)
        files = {}; stamps = {}
        for name in names:
            fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=dfd)
            stream = stack.enter_context(os.fdopen(fd, 'rb'))
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode), 'regular_file_required')
            files[name] = stream; stamps[name] = _stamp(info)
        def bounded(name, maximum):
            check()
            require(stamps[name][3] <= maximum, 'file_size')
            result = files[name].read(maximum + 1)
            require(len(result) <= maximum, 'file_size')
            return result
        marker = parse_marker(bounded(names[0], MAX_MARKER))
        version = parse_envelope(bounded(names[1], MAX_METADATA), marker)
        wav = files[names[2]]
        require(stamps[names[2]][3] == marker['wav_bytes'], 'wav_size')
        header = wav.read(44)
        require(len(header) == 44, 'wav_header')
        riff, size, wave_fmt, fmt_size, encoding, channels, rate, byte_rate, align, bits, data, length = struct.unpack('<4sI8sIHHIIHH4sI', header)
        require(riff == b'RIFF' and wave_fmt == b'WAVEfmt ' and fmt_size == 16 and encoding == 1 and data == b'data', 'wav_canonical')
        require(1 <= channels <= 32 and 0 < rate <= 2**31 - 1 and bits == 16 and align == channels * 2 and byte_rate == rate * align, 'wav_pcm16')
        require(length > 0 and length % align == 0 and size == length + 36 and length + 44 == marker['wav_bytes'], 'wav_geometry')
        full = hashlib.sha256(header); pcm = hashlib.sha256(); remaining = length
        while remaining:
            check()
            block = wav.read(min(BLOCK, remaining))
            require(block, 'wav_truncated')
            remaining -= len(block); full.update(block); pcm.update(block)
        require(wav.read(1) == b'' and full.hexdigest() == marker['wav_sha256'], 'wav_hash_or_trailing')
        for name in names:
            check()
            require(_stamp(os.fstat(files[name].fileno())) == stamps[name], 'file_changed')
            require(_stamp(os.stat(name, dir_fd=dfd, follow_symlinks=False)) == stamps[name], 'file_replaced')
        result = dict(marker, metadata_schema=version, sample_rate_hz=rate, channels=channels,
                      frames=length // align, pcm_sha256=pcm.hexdigest(),
                      transport_bytes_match=True, metadata_semantics_validated=False,
                      immutable_publication_proven=False, strict_import_ready=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect_delivery(args.directory)
    except (OSError, Refused) as exc:
        # Do not print paths, sidecar bodies, or incidental operating-system data.
        print(json.dumps({'transport_bytes_match': False, 'error': str(exc) if isinstance(exc, Refused) else 'file_access_failed'}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
