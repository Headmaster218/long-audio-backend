"""Frozen native alias-veto fixtures, no matcher or feature imports."""
from array import array
import math
import random
import sys


def pcm(values):
    output = array('h', values)
    if sys.byteorder != 'little':
        output.byteswap()
    return output.tobytes()


def make_fixture(case):
    rate = case['rate']
    channels = case.get('channels', 1)
    count, cut, overlap = 6 * rate + 17, 3 * rate + 3, (rate + 1) // 2
    period = rate // 400
    rngs = [random.Random(case['seed'] + channel) for channel in range(channels)]
    values = []
    for frame in range(count):
        for channel, rng in enumerate(rngs):
            if case['kind'] == 'locked_tone_jitter':
                phase = case['phase'] + channel * 11
                value = round(case['amplitude'] * math.sin(2 * math.pi * ((frame + phase) % period) / period))
                value += rng.randrange(-1, 2)
            else:
                value = rng.randrange(-case['amplitude'], case['amplitude'] + 1)
            values.append(value + case['dc'])
    reference = pcm(values)
    pieces = [reference[:cut * channels * 2], reference[(cut - overlap) * channels * 2:]]
    oracle = overlap if case['physical_truth'] == 'same_source_overlap' else 0
    return pieces, oracle, reference if oracle else None
