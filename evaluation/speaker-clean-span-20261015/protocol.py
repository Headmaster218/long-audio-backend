"""Pure scoring/calibration protocol extracted unchanged from the completed pilot.

No audio, model loading, decoding, persistence, or product integration is included.
Callbacks supply synthetic inputs in the companion tests. See README.md for scope.
"""

import hashlib

import json

import math

RATE, FRAMES, DIM = 16000, 48000, 512

SPEAKERS = ('001-M', '006-F')

ARMS = ('contiguous', 'concatenated')

GRID = tuple((t, m) for t in (.55, .65, .75) for m in (.05, .10, .15))

FIXED = (.75, .10)

def require(ok, message):
    if not ok:
        raise ValueError(message)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()

def valid_vector(values):
    require(len(values) == DIM and all(math.isfinite(float(x)) for x in values),
            'Embedding must be a finite 512-vector')
    require(math.sqrt(sum(float(x) ** 2 for x in values)) > 1e-8,
            'Zero or near-zero embedding')

def score_row(gallery, query, expected, arm, role, dot):
    require(type(gallery) is tuple and len(gallery) == 2, 'Mutable or wrong-sized gallery')
    scores = {s: float(dot(v, query)) for s, v in gallery}
    require(set(scores) == set(SPEAKERS) and
            all(math.isfinite(x) and -1.0001 <= x <= 1.0001 for x in scores.values()),
            'Invalid cosine result')
    ranking = sorted(scores, key=lambda s: (-scores[s], s))
    best, second = ranking
    return {'expected': expected, 'arm': arm, 'role': role, 'scores': scores,
            'best_speaker': best, 'best_score': scores[best],
            'margin': scores[best] - scores[second]}

def decisions(rows, policy):
    tau, margin = policy
    counts = {'correct': 0, 'wrong_person_merge': 0, 'abstention': 0}
    labels = []
    for row in rows:
        label = row['best_speaker'] if row['best_score'] >= tau and row['margin'] >= margin else None
        outcome = ('abstention' if label is None else 'correct' if label == row['expected']
                   else 'wrong_person_merge')
        counts[outcome] += 1
        labels.append({'expected': row['expected'], 'accepted': label, 'outcome': outcome})
    return {'tau': tau, 'margin': margin, 'counts': counts, 'decisions': labels}

def select_policy(calibration):
    require(len(calibration) == 2 and all(r['role'] == 'calibration' for r in calibration),
            'Selection takes exactly two calibration rows only')
    candidates = [decisions(calibration, point) for point in GRID]
    selected = min(candidates, key=lambda x: (x['counts']['wrong_person_merge'],
                   x['counts']['abstention'], -x['tau'], -x['margin']))
    return {'selected': {'tau': selected['tau'], 'margin': selected['margin']},
            'grid': candidates, 'fixed': decisions(calibration, FIXED)}

def protocol(embed, dot, freeze, event):
    galleries = {}
    for arm in ARMS:
        galleries[arm] = tuple((s, tuple(embed(s, 'enrollment', arm))) for s in SPEAKERS)
    before = canonical(galleries)
    rows, calibration = [], {}
    for arm in ARMS:
        current = []
        for s in SPEAKERS:
            row = score_row(galleries[arm], embed(s, 'calibration', arm), s, arm,
                            'calibration', dot)
            current.append(row)
            rows.append(row)
            event('score', row)
        calibration[arm] = select_policy(current)
    freeze_receipt = freeze(calibration, rows)
    require(bool(freeze_receipt), 'Threshold freeze failed')
    evaluation = {}
    for arm in ARMS:
        current = []
        for s in SPEAKERS:
            row = score_row(galleries[arm], embed(s, 'evaluation', arm), s, arm,
                            'evaluation', dot)
            current.append(row)
            rows.append(row)
            event('score', row)
        policy = calibration[arm]['selected']
        evaluation[arm] = {'fixed': decisions(current, FIXED),
                           'calibration_selected': decisions(current, (policy['tau'], policy['margin']))}
    require(canonical(galleries) == before, 'Gallery mutation')
    return {'calibration': calibration, 'evaluation': evaluation, 'all_scores': rows,
            'cosine_score_count': sum(len(r['scores']) for r in rows),
            'frozen_thresholds_sha256': freeze_receipt,
            'gallery_sha256_before': hashlib.sha256(before).hexdigest(),
            'gallery_sha256_after': hashlib.sha256(canonical(galleries)).hexdigest()}
