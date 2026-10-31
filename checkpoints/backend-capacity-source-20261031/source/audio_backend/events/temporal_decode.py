"""Pure bounded decoding helpers; no model dependencies or inference side effects."""
from __future__ import annotations

import math
import numpy as np

SAMPLE_RATE = 16000
GRID_SAMPLES = 640
TILE_SAMPLES = 160000
TILE_FRAMES = 250
MAX_ANALYZER_SAMPLES = 960160
MAX_ANALYZER_FRAMES = (MAX_ANALYZER_SAMPLES + GRID_SAMPLES - 1) // GRID_SAMPLES
PROFILE = 'knock-001-median9-experimental-v1'
THRESHOLD = .01
MEDIAN_FRAMES = 9


def median_probabilities(probabilities, width=9):
    """Half-sample reflection, matching np.pad(..., mode='symmetric').

    Call once on the concatenated *valid* analyzer stream: reflecting each tile
    separately would invent boundaries at the internal 10-second seams.
    """
    p = np.asarray(probabilities)
    if p.ndim != 2 or p.shape[1] == 0 or not np.isfinite(p).all():
        raise ValueError('temporal_invalid_probabilities')
    if type(width) is not int or width < 1 or width % 2 != 1:
        raise ValueError('temporal_invalid_median_width')
    pad = width // 2
    return np.median(np.lib.stride_tricks.sliding_window_view(
        np.pad(p, ((0, 0), (pad, pad)), mode='symmetric'), width, axis=1), axis=-1)


def frame_runs(mask, step_seconds, duration_seconds):
    """Compatibility helper: return support intervals clipped to real audio."""
    a = np.asarray(mask, dtype=bool)
    if (a.ndim != 1 or not math.isfinite(step_seconds) or step_seconds <= 0
            or not math.isfinite(duration_seconds) or duration_seconds < 0):
        raise ValueError('temporal_invalid_frame_geometry')
    changes = np.diff(np.r_[False, a, False].astype(np.int8))
    return [(float(st * step_seconds), min(float(en * step_seconds), duration_seconds))
            for st, en in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))
            if st * step_seconds < duration_seconds]


def frame_index_runs(mask):
    """Tile-relative [start, end) integer frame pairs, at most 125 per tile."""
    a = np.asarray(mask)
    if a.ndim != 1 or a.dtype != np.dtype(bool) or len(a) > TILE_FRAMES:
        raise ValueError('temporal_invalid_tile_mask')
    changes = np.diff(np.r_[False, a, False].astype(np.int8))
    pairs = [[int(st), int(en)] for st, en in zip(
        np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))]
    if len(pairs) > 125:
        raise ValueError('temporal_run_limit')
    return pairs


def decode_knock_tiles(probabilities, analyzer_sample_count):
    """Decode the one retained Knock stream using integer sample geometry.

    Tail score frames beyond ceil(real samples / 640) must already be discarded.
    Every returned tile has real sample support, including a partial final frame.
    No complete class-by-time matrix is accepted or retained here.
    """
    if (type(analyzer_sample_count) is not int
            or not 0 < analyzer_sample_count <= MAX_ANALYZER_SAMPLES):
        raise ValueError('temporal_invalid_sample_count')
    p = np.asarray(probabilities)
    expected = (analyzer_sample_count + GRID_SAMPLES - 1) // GRID_SAMPLES
    if (p.ndim != 1 or len(p) != expected or p.dtype != np.dtype(np.float32)
            or not np.isfinite(p).all() or np.any(p < 0) or np.any(p > 1)):
        raise ValueError('temporal_invalid_knock_stream')
    filtered = median_probabilities(p[None, :], MEDIAN_FRAMES)[0]
    result = []
    for st in range(0, analyzer_sample_count, TILE_SAMPLES):
        count = min(TILE_SAMPLES, analyzer_sample_count - st)
        first = st // GRID_SAMPLES
        frames = (count + GRID_SAMPLES - 1) // GRID_SAMPLES
        raw = p[first:first + frames]
        smooth = filtered[first:first + frames]
        result.append({
            'profile': PROFILE, 'target_label': 'Knock', 'target_class_index': 228,
            'threshold': THRESHOLD, 'median_frames': MEDIAN_FRAMES,
            'grid_samples': GRID_SAMPLES, 'sample_rate': SAMPLE_RATE,
            'tile_start_sample': st, 'tile_sample_count': count,
            'analyzer_sample_count': analyzer_sample_count,
            'frame_runs': frame_index_runs(smooth > THRESHOLD),
            'raw_target_peak': float(raw.max()),
            'filtered_target_peak': float(smooth.max()),
        })
    return result
