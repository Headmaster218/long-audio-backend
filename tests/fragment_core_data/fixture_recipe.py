"""Frozen, deterministic synthetic fixtures; no matcher or plan imports.

Ranges/expected labels are scorer-only. No fixture paths or source offsets are
passed to the matcher. 'Repeated phrase' is a signal surrogate, not speech.
"""
import math
from pathlib import Path
import random
import struct
import wave


def make_fixture(case, root):
    rng = random.Random(case['seed'])
    n, channels, rate = case['length'], case['channels'], case['rate']
    kind = case.get('kind', 'random')
    cycle = [rng.randrange(-12000, 12001) for _ in range(rate // 5)]
    samples = []
    for i in range(n):
        for ch in range(channels):
            value = rng.randrange(-12000, 12001)
            if kind == 'silence':
                value = 0
            elif kind == 'tone':
                value = round(10000 * math.sin(2 * math.pi * (i % 40) / 40))
            elif kind == 'periodic':
                value = round(8000 * math.sin(2 * math.pi * (i % 320) / 320))
            elif kind == 'near_periodic':
                value = round(8000 * math.sin(2 * math.pi * (i % 1600) / 1600)) + rng.randrange(-3, 4)
            elif kind == 'periodic_random':
                value = cycle[i % len(cycle)]
            elif kind == 'quiet':
                value = rng.randrange(-20, 21)
            elif kind == 'repeated_phrase_surrogate':
                # Same repeated envelope, independent realization, no same frames.
                value = round(value * (0.2 + 0.8 * abs(math.sin(math.pi * (i % 8000) / 8000))))
            samples.append(value)
    raw = struct.pack('<' + 'h' * len(samples), *samples)
    frame_bytes = channels * 2
    cut, overlap = case['cut'], case['overlap']
    pieces = [raw[:cut * frame_bytes], raw[(cut - overlap) * frame_bytes:]]
    if kind == 'channel_mismatch':
        values = list(struct.unpack('<' + 'h' * (len(pieces[1]) // 2), pieces[1]))
        for i in range(1, len(values), 2):
            values[i] += 1
        pieces[1] = struct.pack('<' + 'h' * len(values), *values)
    elif kind == 'quantized':
        values = struct.unpack('<' + 'h' * (len(pieces[1]) // 2), pieces[1])
        pieces[1] = struct.pack('<' + 'h' * len(values), *(v // 8 * 8 for v in values))
    paths = []
    for i, piece in enumerate(pieces):
        path = Path(root) / f'{case["id"]}-{i}.wav'
        with wave.open(str(path), 'wb') as target:
            target.setnchannels(channels)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(piece)
        paths.append(path)
    return paths, raw, pieces
