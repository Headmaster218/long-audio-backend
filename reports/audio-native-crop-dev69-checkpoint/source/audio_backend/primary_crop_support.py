"""Versioned native-metadata crop eligibility, never acoustic purity proof.

Only HistoryStore calls this with its same-snapshot, immutable packet and the
exact rederived certificate node. This is not an API for accepting caller
assertions. Full packet witnesses remain validated by that caller.
"""
from .recording_speaker_links import CROP_FRAMES, EROSION_FRAMES

POLICY = 'native_crop_support_v1'
LEGACY_POLICY = 'whole_packet_no_overlap_v1'


def derive(packet, node):
    """Fail closed unless the exact crop plus erosion margin is supported.

    Half-open intervals permit a competing turn to touch the outside edge of
    the erosion margin, but even one frame inside is rejected. Display origin
    is deliberately not used as a physical/logical frame offset.
    """
    try:
        part = packet['commit']['part']
        lo, hi = part['logical_start'], part['logical_end']
        start, end = node['start_frame'], node['end_frame']
        values = (lo, hi, start, end, part['source_start'], part['source_end'],
                  part['source_frames'])
        if any(type(value) is not int for value in values):
            return None
        if not (0 <= lo < hi and lo <= start < end <= hi
                and end - start == CROP_FRAMES
                and 0 <= part['source_start'] < part['source_end'] <= part['source_frames']
                and hi - lo == part['source_end'] - part['source_start']):
            return None
        left, right = start - EROSION_FRAMES, end + EROSION_FRAMES
        if left < lo or right > hi:
            return None
        spans = node['source_spans']
        if type(spans) is not list or len(spans) != 1:
            return None
        span = spans[0]
        expected = dict(part, source_start=part['source_start'] + start - lo,
                        source_end=part['source_start'] + end - lo,
                        logical_start=start, logical_end=end)
        if span != expected:
            return None
        labels = packet['labels']
        if (type(labels) is not list or not labels
                or any(type(label) is not str or not label for label in labels)
                or len(set(labels)) != len(labels)
                or node['native_label'] not in labels):
            return None
        target = labels.index(node['native_label'])
        intervals = []
        if type(packet['turns']) is not list or not packet['turns']:
            return None
        for turn in packet['turns']:
            if type(turn) is not list or len(turn) != 6:
                return None
            _, label, a, b, _, _ = turn
            if (any(type(value) is not int for value in (label, a, b))
                    or not 0 <= label < len(labels) or not 0 <= a < b):
                return None
            if label != target:
                if max(a, left) < min(b, right):
                    return None
            else:
                intervals.append((a, b))
        covered = left
        for a, b in sorted(intervals):
            if b <= covered:
                continue
            if a > covered:
                break
            covered = max(covered, b)
            if covered >= right:
                return {'policy': POLICY, 'extent': 'native_metadata_only',
                        'acoustic_purity_certified': False,
                        'start_frame': start, 'end_frame': end,
                        'erosion_frames': EROSION_FRAMES}
        return None
    except (KeyError, TypeError, ValueError, IndexError):
        return None
