"""Separate v2q1p1: unchanged q1 gate plus one native alias-resistant veto.

A periodic component erased as DC by stride-s sampling has a period dividing s.
The native lag is a multiple of s and >=20ms. This closes that specific blind
spot; it is not a universal periodicity or replay-intent detector.
"""
import math
import sys

from .fragment_matcher import (
    MAX_MATCH_SECONDS, MIN_PERIOD_MS, PERIODIC_HZ, PERIODIC_REJECT,
    _match_boundary, _periodic_peak, _samples,
)
from .fragment_matcher_v2 import _informative_quantized

POLICY = 'exact-pcm16-v2q1p1'


def native_alias_correlation(pcm, rate, channels):
    """Per-channel centered correlation, widened integer scalar accumulation.

    The supplied candidate is already bounded PCM, not a path. Little-endian
    hosts reuse its buffer. No per-frame floating-point vectors are created.
    Zero variance is conservatively rejected and explicitly identified.
    """
    if not 8000 <= rate <= 48000 or channels not in (1, 2):
        raise ValueError('Unsupported native PCM16 format')
    if len(pcm) % (channels * 2) or len(pcm) // (channels * 2) > rate * MAX_MATCH_SECONDS:
        raise ValueError('Native alias check requires complete <=30s PCM frames')
    values = memoryview(pcm).cast('h') if sys.byteorder == 'little' else _samples(pcm)
    frames = len(values) // channels
    stride = max(1, rate // PERIODIC_HZ)
    minimum_lag = (rate * MIN_PERIOD_MS + 999) // 1000
    lag = stride * ((minimum_lag + stride - 1) // stride)
    count = frames - lag
    if count <= 0:
        return {'peak': 1.0, 'lag_frames': lag, 'pair_count_per_channel': 0,
                'channels': [], 'status': 'insufficient_native_pairs'}
    result = []
    for channel in range(channels):
        sx = sy = sxx = syy = sxy = 0
        for frame in range(count):
            x = int(values[frame * channels + channel])
            y = int(values[(frame + lag) * channels + channel])
            sx += x
            sy += y
            sxx += x * x
            syy += y * y
            sxy += x * y
        # Keep centering in widened integer arithmetic to avoid cancellation
        # from a large DC offset or signed16 multiplication/subtraction overflow.
        covariance = count * sxy - sx * sy
        variance_x = count * sxx - sx * sx
        variance_y = count * syy - sy * sy
        if variance_x <= 0 or variance_y <= 0:
            correlation, status = 1.0, 'zero_variance_conservative_reject'
        else:
            correlation = max(-1.0, min(1.0, covariance / math.sqrt(variance_x * variance_y)))
            status = 'measured'
        result.append({'channel': channel, 'correlation': correlation, 'status': status,
                       'centered_covariance_n': covariance,
                       'centered_variance_x_n': variance_x, 'centered_variance_y_n': variance_y})
    return {'peak': max(item['correlation'] for item in result), 'lag_frames': lag,
            'pair_count_per_channel': count, 'channels': result, 'status': 'measured'}


def _periodic_peak_p1(pcm, rate, channels):
    inherited = _periodic_peak(pcm, rate, channels)
    if inherited >= PERIODIC_REJECT:
        return inherited
    return max(inherited, native_alias_correlation(pcm, rate, channels)['peak'])


def match_boundary_p1(left_pcm, right_pcm, *, sample_rate, channels, declaration):
    return _match_boundary(left_pcm, right_pcm, sample_rate=sample_rate, channels=channels,
                           declaration=declaration, information_gate=_informative_quantized,
                           policy=POLICY, rejection_reason='quantized_nondegeneracy_failed',
                           periodic_gate=_periodic_peak_p1)
