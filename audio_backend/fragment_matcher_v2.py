"""Experimental v2q1 digital-nondegeneracy gate, NOT calibrated confidence.

Only anchor qualification changes. Offset search, byte equality, periodicity,
provenance and replay rules remain in the shared exact matcher. V1 is default.
"""
from collections import Counter

from .fragment_matcher import ANCHOR_MS, MIN_ANCHOR_SPACING_MS, _match_boundary, _samples

POLICY = 'exact-pcm16-v2q1'


def quantized_statistics(values):
    """Consecutive samples of ONE channel; widened Python integer differences."""
    n = len(values)
    counts = Counter(values)
    deltas = [int(values[i]) - int(values[i - 1]) for i in range(1, n)]
    nonzero = {value for value in deltas if value != 0}
    changes = sum(value != 0 for value in deltas)
    modal = max(counts.values(), default=0)
    checks = {
        'sample_levels': len(counts) >= 16,
        'modal_fraction': n > 0 and 4 * modal <= n,
        'transition_fraction': n > 1 and 4 * changes >= n - 1,
        'signed_nonzero_delta_types': len(nonzero) >= 8,
    }
    return {'frames': n, 'distinct_sample_levels': len(counts), 'modal_count': modal,
            'changed_transitions': changes, 'transition_denominator': max(0, n - 1),
            'distinct_signed_nonzero_deltas': len(nonzero),
            'minimum_delta': min(deltas, default=0), 'maximum_delta': max(deltas, default=0),
            'checks': checks, 'qualifies': all(checks.values())}


def anchor_statistics(pcm, rate, channels):
    values = _samples(pcm)
    frames = len(values) // channels
    width = (rate * ANCHOR_MS + 999) // 1000
    if frames < width:
        return []
    starts = (0, (frames - width) // 2, frames - width)
    if starts[1] - starts[0] < (rate * MIN_ANCHOR_SPACING_MS + 999) // 1000:
        return []
    result = []
    for start in starts:
        channel_stats = [quantized_statistics(values[start * channels + ch:(start + width) * channels:channels])
                         for ch in range(channels)]
        result.append({'start_frame': start, 'end_frame': start + width, 'channels': channel_stats,
                       'qualifies': all(stats['qualifies'] for stats in channel_stats)})
    return result


def _informative_quantized(pcm, rate, channels):
    anchors = anchor_statistics(pcm, rate, channels)
    return len(anchors) == 3 and all(anchor['qualifies'] for anchor in anchors)


def match_boundary_v2(left_pcm, right_pcm, *, sample_rate, channels, declaration):
    return _match_boundary(left_pcm, right_pcm, sample_rate=sample_rate, channels=channels,
                           declaration=declaration, information_gate=_informative_quantized,
                           policy=POLICY, rejection_reason='quantized_nondegeneracy_failed')
