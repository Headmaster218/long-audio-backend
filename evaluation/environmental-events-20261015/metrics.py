"""Frozen pilot metrics only. Standard library; no inference or downloads.

Inputs are interval dictionaries with start/end and event or label.
The segment scorer is fixed to broad door; exhaustive truth is mandatory.
Weak clip tags never establish absent-event ground truth.
"""
import math


def segments(records, duration, label):
    """Half-open one-second bins, any strictly positive overlap, duplicate-safe."""
    if duration <= 0 or int(duration) != duration:
        raise ValueError('This pilot requires complete integer-second recordings')
    active = set()
    for row in records:
        if row.get('event', row.get('label')) != label:
            continue
        a, b = row['start'], row['end']
        if not all(math.isfinite(x) for x in (a, b)) or not 0 <= a < b <= duration:
            raise ValueError('Invalid or incomplete temporal support')
        for second in range(math.floor(a), min(int(duration), math.ceil(b))):
            if max(a, second) < min(b, second + 1):
                active.add(second)
    return active


def rates(counts):
    tp, fp, fn = (counts[k] for k in ('tp', 'fp', 'fn'))
    return dict(counts, precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None,
                f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)


def segment_score(reference, predicted, duration, *, exhaustive=False):
    if not exhaustive:
        raise ValueError('Weak tags cannot authorize segment negatives')
    r, p = (segments(rows, duration, 'door') for rows in (reference, predicted))
    return rates({'tp':len(r&p), 'fp':len(p-r), 'fn':len(r-p), 'tn':int(duration)-len(r|p), 'segments':int(duration)})


def weak_hit(category, events):
    target = {'rain':'rain', 'door_wood_knock':'door', 'door_wood_creaks':'door'}.get(category)
    return {'source_category':category, 'positive_target':target,
            'hit': any(e['label']==target for e in events) if target else None,
            'precision':None, 'negative_truth':False}
