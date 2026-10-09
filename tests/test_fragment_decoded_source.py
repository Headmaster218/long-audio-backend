"""Fast structural tests and explicit-opt-in tests against frozen local assets."""
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import time

import pytest

from audio_backend.fragment_core import SourceChanged, UnsupportedAudio
from audio_backend.fragment_decoded_source import (
    Accounting, DecodeCancelled, DecodeLimit, Limits, SequentialDecodedReader,
    _Guard, _mp4, _ogg, _ogg_crc, _opus_frames, _run, inspect_decoded_source,
)


class TinyGuard:
    limits = Limits()
    def check(self):
        pass


def page(payload, seq, flags, granule, serial=42):
    lacing = bytes([255]) * (len(payload) // 255) + bytes([len(payload) % 255])
    header = struct.pack('<4sBBQIIIB', b'OggS', 0, flags, granule, serial, seq, 0, len(lacing))
    data = bytearray(header + lacing + payload)
    struct.pack_into('<I', data, 22, _ogg_crc(data))
    return bytes(data)


def synthetic_ogg():
    head = b'OpusHead' + struct.pack('<BBHIhB', 1, 1, 312, 48000, 0, 0)
    tags = b'OpusTags' + struct.pack('<II', 0, 0)
    # Structural-only TOC is not a valid sound fixture; no actual decode is run.
    return page(head, 0, 2, 0) + page(tags, 1, 0, 0) + page(b'\xf8\x00', 2, 4, 800)


def test_ogg_structure_crc_and_accounting():
    result = _ogg(io.BytesIO(synthetic_ogg()), TinyGuard())
    assert result['pre_skip'] == 312
    assert result['coded_frames'] == 960
    assert result['end_discard'] == 160
    assert result['final_granule'] - result['pre_skip'] == 488


@pytest.mark.parametrize('change', ['crc', 'truncated', 'eos_missing', 'chained', 'sequence', 'serial', 'origin'])
def test_reject_ogg_structural_errors(change):
    data = synthetic_ogg()
    prefix = len(data) - len(page(b'\xf8\x00', 2, 4, 800))
    if change == 'crc':
        data = data[:-1] + bytes([data[-1] ^ 1])
    elif change == 'truncated':
        data = data[:-1]
    elif change == 'eos_missing':
        data = data[:prefix] + page(b'\xf8\x00', 2, 0, 960)
    elif change == 'chained':
        data += data
    elif change == 'sequence':
        data = data[:prefix] + page(b'\xf8\x00', 3, 4, 800)
    elif change == 'serial':
        data = data[:prefix] + page(b'\xf8\x00', 2, 4, 800, serial=43)
    else:
        data = data[:prefix] + page(b'\xf8\x00', 2, 0, 1960) + page(b'\xf8\x00', 3, 4, 2800)
    with pytest.raises(UnsupportedAudio):
        _ogg(io.BytesIO(data), TinyGuard())


@pytest.mark.parametrize('packet, expected', [(b'\x00', 480), (b'\x18', 2880), (b'\x60', 480),
                                             (b'\x68', 960), (b'\x80', 120), (b'\x98', 960),
                                             (b'\x81', 240), (b'\x83\x03', 360)])
def test_opus_toc_frames(packet, expected):
    assert _opus_frames(packet) == expected


def test_metadata_bounds_and_default_off(tmp_path):
    with pytest.raises(UnsupportedAudio, match='default OFF'):
        inspect_decoded_source(tmp_path / 'absent')
    with pytest.raises(ValueError):
        Limits(max_read_frames=48001)
    with pytest.raises(ValueError):
        Limits(wall_seconds=float('nan'))
    with pytest.raises(UnsupportedAudio):
        _mp4(io.BytesIO(struct.pack('>I4s', 64, b'moov')), TinyGuard(), 8)
    with pytest.raises(UnsupportedAudio):
        _mp4(io.BytesIO(struct.pack('>I4s', 0, b'mdat')), TinyGuard(), 8)
    with pytest.raises(UnsupportedAudio):
        _opus_frames(b'\x83')
    with pytest.raises(UnsupportedAudio):
        _opus_frames(b'\x9b\x3f')


def test_nonregular_source_does_not_block(tmp_path):
    path = tmp_path / 'pipe'
    os.mkfifo(path)
    with pytest.raises(UnsupportedAudio, match='regular'):
        inspect_decoded_source(path, enabled=True)


def test_cancel_before_open(tmp_path):
    path = tmp_path / 'data'
    path.write_bytes(b'test')
    with pytest.raises(DecodeCancelled):
        inspect_decoded_source(path, enabled=True, cancel=lambda: True)


def test_pull_backpressure_and_close_reaps(tmp_path):
    pidfile = tmp_path / 'pid'
    # Child writes indefinitely. Only one bounded chunk is retained on pull.
    code = "import os; print(os.getpid(), flush=True); b=b'x'*65536\nwhile True: os.write(1,b)"
    guard = _Guard(Limits(), pidfile, None)
    iterator = _run(['/usr/bin/python3', '-c', code], guard, output_limit=1024**3)
    first = next(iterator)
    if b'\n' not in first:
        first += next(iterator)
    pid = int(first.split(b'\n', 1)[0])
    before = Path(f'/proc/{pid}/stat').read_text().split()
    time.sleep(.12)
    after = Path(f'/proc/{pid}/stat').read_text().split()
    assert sum(map(int, after[13:15])) - sum(map(int, before[13:15])) <= 3
    iterator.close()
    assert not Path(f'/proc/{pid}').exists()


def test_running_cancel_and_output_caps(tmp_path):
    cancelled = False
    guard = _Guard(Limits(), tmp_path / 'output', lambda: cancelled)
    iterator = _run(['/usr/bin/python3', '-c', 'import time; print("x",flush=True); time.sleep(5)'],
                    guard, output_limit=1024)
    assert next(iterator) == b'x\n'
    cancelled = True
    with pytest.raises(DecodeCancelled):
        next(iterator)
    with pytest.raises(DecodeLimit, match='output limit'):
        list(_run(['/usr/bin/python3', '-c', 'print("x"*100)'],
                  _Guard(Limits(), tmp_path / 'output', None), output_limit=10))
    with pytest.raises(DecodeLimit, match='wall-time'):
        list(_run(['/usr/bin/python3', '-c', 'import time;time.sleep(2)'],
                  _Guard(Limits(wall_seconds=.1), tmp_path / 'output', None), output_limit=10))


@pytest.fixture
def assets():
    value = os.environ.get('AUDIO_COMPRESSED_FIXTURES')
    if not value:
        pytest.skip('Set AUDIO_COMPRESSED_FIXTURES to frozen study; no downloads/generated audio')
    return Path(value)


@pytest.mark.parametrize('name, frames, endpoint', [
    ('whole4_opus', 192000, 'emitted-count-agrees'),
    ('whole4_aac', 192512, 'unresolved-aac'),
    ('endpoint44101', 45056, 'unresolved-aac'),
    ('offset_aac', 193536, 'unresolved-aac'),
])
def test_frozen_receipts_exact_ranges_and_uncertainty(assets, name, frames, endpoint):
    suffix = '.ogg' if 'opus' in name else '.m4a'
    path = assets / 'encoded' / (name + suffix)
    receipt = inspect_decoded_source(path, enabled=True)
    reference = (assets / 'pcm' / (name + '_decode.pcm')).read_bytes()
    assert receipt.emitted_frames == frames
    assert receipt.emitted_pcm_sha256 == hashlib.sha256(reference).hexdigest()
    assert receipt.endpoint_status.startswith(endpoint)
    assert receipt.original_preencoding_frames is None
    assert receipt.capture_continuity == 'unproven'
    assert receipt.automatic_admission is False
    assert len(receipt.to_json()) < 16384
    reader = SequentialDecodedReader(path, receipt, enabled=True)
    # Unaligned range and terminal samples have exact sequential baseline bytes.
    for start, end in [(1234, 17777), (frames - 997, frames)]:
        assert reader.read(start, end) == reference[start * 2:end * 2]
    if name == 'whole4_opus':
        assert receipt.accounting.container_pre_skip == 312
        assert receipt.accounting.container_end_discard == 648
    if name == 'whole4_aac':
        assert receipt.presentation[0].duration_num == 4000
        assert receipt.presentation[0].duration_den == 1000
        assert receipt.accounting.signaled_skip_frames == 1024
        assert receipt.accounting.signaled_discard_frames == 0
    if name == 'endpoint44101':
        assert receipt.sample_rate == 44100
        assert receipt.presentation[0].duration_num == receipt.presentation[0].duration_den == 1000
        assert json.loads(receipt.container_json)['track_duration_ticks'] == 45125


@pytest.mark.parametrize('name', ['truncated_opus.ogg', 'truncated_aac.m4a', 'offset_opus.ogg'])
def test_frozen_rejects_incomplete_or_outside_profile(assets, name):
    with pytest.raises(UnsupportedAudio):
        inspect_decoded_source(assets / 'encoded' / name, enabled=True)


def test_receipt_mutation_range_and_decode_limits(assets, tmp_path):
    source = assets / 'encoded' / 'whole4_opus.ogg'
    path = tmp_path / 'copy.ogg'
    path.write_bytes(source.read_bytes())
    receipt = inspect_decoded_source(path, enabled=True)
    reader = SequentialDecodedReader(path, receipt, enabled=True)
    with pytest.raises(ValueError):
        reader.read(True, 10)
    with pytest.raises(ValueError):
        reader.read(0, 48001)
    wrong = SequentialDecodedReader(path, replace(receipt, channels=2), enabled=True)
    with pytest.raises(SourceChanged, match='format'):
        wrong.read(0, 10)
    wrong = SequentialDecodedReader(path, replace(receipt, emitted_pcm_sha256='0' * 64), enabled=True)
    with pytest.raises(SourceChanged, match='emitted PCM'):
        wrong.read(0, 10)
    with pytest.raises(DecodeLimit):
        inspect_decoded_source(path, enabled=True, limits=Limits(max_frames=100))
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(SourceChanged, match='identity'):
        reader.read(0, 10)
