"""Create fixed 11-second synthetic PCM deliveries for opt-in native span smoke.

Generated silence is not recorded audio, provenance, continuity or accuracy
 evidence. No service, network, models, microphone or existing input is opened.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def digest(data):
    return hashlib.sha256(data).hexdigest()


def utf(value):
    # Every string in this fixed fixture is ASCII, also valid modified UTF-8.
    raw = value.encode('ascii')
    return struct.pack('>H', len(raw)) + raw


def create(root):
    root = Path(root).resolve()
    root.mkdir(mode=0o700, exist_ok=False)
    rows = []
    capture = 'synthetic-selected-span'
    cursor = 100003
    for index, frames in enumerate((64000, 64000, 48000)):
        seq = 11 + index
        path = root / str(seq)
        path.mkdir(mode=0o700)
        pcm = b'\0\0' * frames
        wav = struct.pack('<4sI8sIHHIIHH4sI', b'RIFF', 36 + len(pcm), b'WAVEfmt ',
            16, 1, 1, 16000, 32000, 2, 16, b'data', len(pcm)) + pcm
        reason = 'INTERRUPTION' if index == 2 else 'ROTATION'
        local = (b'PCM1' + struct.pack('>i', 1) + utf(capture) + utf('synthetic-restarted-run')
            + struct.pack('>qii', 7, 16000, 1) + utf('synthetic') + utf('synthetic')
            + utf('RESTART') + struct.pack('>qqqq', seq, cursor, 64000, frames)
            + utf(digest(pcm)) + utf(digest(wav)) + utf(reason) + bytes([index > 0]))
        local += hashlib.sha256(local).digest()
        source = 'segment-' + digest(capture.encode()) + '-' + str(seq) + '.ready'
        envelope = b'RFT1' + utf('synthetic-span-v1') + utf(source) + struct.pack('>i', len(local)) + local
        binding = digest((source + '\n' + digest(wav) + '\nsynthetic-span-v1').encode())
        marker = '\n'.join(('RECORDER-DELIVERY-V1', 'r-' + binding + '-' + '0' * 32,
            'synthetic-span-v1', source, str(len(wav)), digest(wav), str(len(envelope)), digest(envelope), ''))
        for name, data in (('audio.wav', wav), ('segment.v1.bin', envelope), ('delivery.v1.txt', marker.encode())):
            (path / name).write_bytes(data)
        rows.append(dict(path=str(path), sequence=seq, epoch=7, frame_start=cursor, frames=frames,
            close_reason=reason, hashes={p.name:digest(p.read_bytes()) for p in sorted(path.iterdir())}))
        cursor += frames
    summary = dict(schema='generated-selected-span-fixture-v1', generated=True, frames=176000,
        epoch=7, first_sequence=11, original_start_frame=100003, terminal_close_reason='INTERRUPTION',
        hardware_provenance=False, acoustic_accuracy_qualified=False, deliveries=rows)
    (root / 'FIXTURE.json').write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    print(json.dumps(create(parser.parse_args().output), sort_keys=True))
