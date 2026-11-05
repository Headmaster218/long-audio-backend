"""Model-free simultaneous source-local turn geometry."""

def overlapping_regions(turns, duration, capacity=2):
    """Retain simultaneous IDs, including explicit 3+ source capacity failures."""
    edges = {0.0, float(duration)}
    clipped = []
    for t in turns:
        a, b = max(0., float(t['start'])), min(duration, float(t['end']))
        if b > a:
            clipped.append((a, b, t['speaker_local_id']))
            edges.update((a, b))
    regions = []
    points = sorted(edges)
    for a, b in zip(points, points[1:]):
        ids = sorted({sid for lo, hi, sid in clipped if lo < b and hi > a})
        if len(ids) < 2:
            continue
        status = 'capacity_exceeded' if len(ids) > capacity else 'pending_separation'
        if regions and regions[-1]['end'] == a and regions[-1]['speaker_local_ids'] == ids:
            regions[-1]['end'] = b
        else:
            regions.append({'start': a, 'end': b, 'speaker_local_ids': ids,
                            'detected_source_count': len(ids), 'capacity': capacity,
                            'status': status, 'original_mixture_retained': True})
    return regions

