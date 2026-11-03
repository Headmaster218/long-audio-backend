"""Synthetic local transport checks; no authentication or strict-ingest qualification."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest
from audio_backend import recorder_delivery as d


def digest(data): return hashlib.sha256(data).hexdigest()
def utf(text):
    data = text.encode('ascii')
    return struct.pack('>H', len(data)) + data


def fixture(root, *, version=1, rate=16000, channels=1):
    root.mkdir(exist_ok=True)
    pcm = b'\x00\x01' * channels * 20
    header = struct.pack('<4sI8sIHHIIHH4sI', b'RIFF', len(pcm)+36, b'WAVEfmt ', 16, 1,
                         channels, rate, rate*channels*2, channels*2, 16, b'data', len(pcm))
    wav = header + pcm
    # Intentionally not a complete SegmentMetadata object: this layer MUST NOT
    # claim semantic validation based on a valid checksum and schema prefix.
    local = b'PCM1' + struct.pack('>i', version) + b'opaque synthetic unvalidated metadata'
    local += hashlib.sha256(local).digest()
    source = 'segment-' + 'a'*64 + '-0.ready'; revision = 'fixture-revision'
    metadata = b'RFT1' + utf(revision) + utf(source) + struct.pack('>i', len(local)) + local
    attempt = 'r-' + digest((source+'\n'+digest(wav)+'\n'+revision).encode()) + '-' + 'b'*32
    marker = '\n'.join(('RECORDER-DELIVERY-V1', attempt, revision, source, str(len(wav)), digest(wav), str(len(metadata)), digest(metadata), '')).encode()
    for name, value in (('audio.wav', wav), ('segment.v1.bin', metadata), ('delivery.v1.txt', marker)):
        (root/name).write_bytes(value)
    return root


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('rate,channels', [(16000, 1), (48000, 2)])
def test_observation_is_not_import_authority(tmp_path, version, rate, channels):
    root = fixture(tmp_path, version=version, rate=rate, channels=channels)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = d.inspect_delivery(root)
    assert result['transport_bytes_match'] is True
    assert result['frames'] == 20 and result['sample_rate_hz'] == rate and result['channels'] == channels
    assert result['metadata_schema'] == version
    assert result['metadata_semantics_validated'] is False
    assert result['immutable_publication_proven'] is False and result['strict_import_ready'] is False
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


@pytest.mark.parametrize('name', ['audio.wav', 'segment.v1.bin', 'delivery.v1.txt'])
def test_mutated_bytes_refused(tmp_path, name):
    root = fixture(tmp_path); path = root/name
    value = bytearray(path.read_bytes()); value[-2] ^= 1; path.write_bytes(value)
    with pytest.raises(d.Refused): d.inspect_delivery(root)


@pytest.mark.parametrize('name', ['audio.wav', 'segment.v1.bin', 'delivery.v1.txt'])
def test_symlinks_refused(tmp_path, name):
    root = fixture(tmp_path); path = root/name
    target = tmp_path/(name+'.real'); path.rename(target); path.symlink_to(target)
    with pytest.raises(OSError): d.inspect_delivery(root)


def test_fifo_refused_without_blocking(tmp_path):
    fixture(tmp_path); (tmp_path/'audio.wav').unlink(); os.mkfifo(tmp_path/'audio.wav')
    with pytest.raises(d.Refused, match='regular_file'): d.inspect_delivery(tmp_path)


def test_oversized_sparse_audio_refused_before_payload(tmp_path):
    fixture(tmp_path)
    with (tmp_path/'audio.wav').open('r+b') as file: file.truncate(d.MAX_WAV + 1)
    with pytest.raises(d.Refused, match='wav_size'): d.inspect_delivery(tmp_path)


@pytest.mark.parametrize('mutation', ['trailing', 'missing_lf', 'crlf', 'binding', 'negative', 'leading_zero', 'huge_ordinal'])
def test_marker_rejections(tmp_path, mutation):
    fixture(tmp_path); original = (tmp_path/'delivery.v1.txt').read_bytes()
    lines = original.decode().split('\n')
    if mutation == 'trailing': original += b'X'
    elif mutation == 'missing_lf': original = original[:-1]
    elif mutation == 'crlf': original = original.replace(b'\n', b'\r\n')
    else:
        if mutation == 'binding': lines[1] = 'r-' + 'c'*64 + '-' + 'd'*32
        if mutation == 'negative': lines[4] = '-1'
        if mutation == 'leading_zero': lines[4] = '0' + lines[4]
        if mutation == 'huge_ordinal': lines[3] = 'segment-' + 'a'*64 + '-' + str(2**63-1) + '.ready'
        original = '\n'.join(lines).encode()
    with pytest.raises(d.Refused): d.parse_marker(original)


@pytest.mark.parametrize('mutation', ['identity', 'checksum', 'version', 'size', 'trailing'])
def test_envelope_failures_even_when_outer_hash_recomputed(tmp_path, mutation):
    fixture(tmp_path)
    marker = d.parse_marker((tmp_path/'delivery.v1.txt').read_bytes())
    data = bytearray((tmp_path/'segment.v1.bin').read_bytes())
    if mutation == 'identity': data[6] = ord('x')
    elif mutation == 'checksum': data[-1] ^= 1
    elif mutation == 'version':
        i = data.index(b'PCM1'); data[i+7] = 3
    elif mutation == 'size': data[4:6] = b'\xff\xff'
    else: data += b'extra'
    marker.update(metadata_bytes=len(data), metadata_sha256=digest(data))
    with pytest.raises(d.Refused): d.parse_envelope(bytes(data), marker)


def test_replacement_detected(tmp_path, monkeypatch):
    fixture(tmp_path); original = d.parse_envelope
    def replace(data, marker):
        value = original(data, marker)
        path = tmp_path/'audio.wav'; replacement = tmp_path/'new.wav'
        replacement.write_bytes(path.read_bytes()); replacement.replace(path)
        return value
    monkeypatch.setattr(d, 'parse_envelope', replace)
    with pytest.raises(d.Refused, match='file_changed|file_replaced'): d.inspect_delivery(tmp_path)


def test_deadline_refused(tmp_path, monkeypatch):
    fixture(tmp_path); ticks = iter([0, 121])
    monkeypatch.setattr(d.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(d.Refused, match='deadline'): d.inspect_delivery(tmp_path)


def test_cli_json_and_no_model_imports(tmp_path):
    fixture(tmp_path)
    root = Path(__file__).resolve().parents[1]
    code = "from audio_backend.recorder_delivery import main; import sys; result=main(sys.argv[1:]); assert 'audio_backend.app' not in sys.modules; assert 'numpy' not in sys.modules; raise SystemExit(result)"
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=root, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['strict_import_ready'] is False
    (tmp_path/'delivery.v1.txt').unlink()
    result = subprocess.run([sys.executable, '-m', 'audio_backend.recorder_delivery', str(tmp_path)], cwd=root, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stdout)['error'] == 'file_access_failed'


@pytest.mark.parametrize('offset,value', [(0,b'RF64'), (20,b'\x03\x00'), (22,b'\x00\x00'), (34,b'\x18\x00'), (40,b'\x01\x00\x00\x00')])
def test_invalid_wav_geometry_with_matching_marker(tmp_path, offset, value):
    fixture(tmp_path); path = tmp_path/'audio.wav'; wav = bytearray(path.read_bytes())
    wav[offset:offset+len(value)] = value; path.write_bytes(wav)
    marker = tmp_path/'delivery.v1.txt'; lines = marker.read_text().split('\n')
    lines[5] = digest(wav)
    lines[1] = 'r-' + digest((lines[3]+'\n'+lines[5]+'\n'+lines[2]).encode()) + '-' + 'b'*32
    marker.write_text('\n'.join(lines))
    with pytest.raises(d.Refused, match='wav_'): d.inspect_delivery(tmp_path)
