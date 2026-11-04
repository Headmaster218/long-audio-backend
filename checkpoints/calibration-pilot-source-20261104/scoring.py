"""Model-free, single-reference calibration/holdout pilot helpers.

Only synthetic tests have been run. This module neither loads audio/models nor
certifies risk. One query against a full fixed gallery is one decision, not K
independent trials. Call sequence: score C -> select/write lock -> score T ->
summarize T with separately supplied truth. The lock is an audit boundary, not
an access-control or blinding system; the caller must keep T outputs unopened.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

SCHEMA = 1
DIMENSION = 512
MARGIN = 0.10
BASELINE = 0.75
GRID = tuple(i / 100 for i in range(101))
RULE = 'C_zero_wrong_known_zero_unknown_accept_max_correct_tie_higher_v1'
WARNING = 'Candidate/error estimates only; no low-risk certification or production authorization.'
EVIDENCE_KEYS = {'protocol', 'calibration_manifest', 'calibration_terminal',
                 'calibration_result', 'calibration_vectors', 'calibration_journal',
                 'calibration_predictor', 'calibration_truth', 'holdout_predictor',
                 'calibration_run_plan', 'holdout_run_plan', 'orchestrator',
                 'calibration_diagnostic_predictor', 'calibration_diagnostic_truth',
                 'calibration_report', 'source_roles', 'holdout_diagnostic_predictor'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normal64(raw):
    """Exact fixed-512 specialization of support_vectors.normalized_vector."""
    if not isinstance(raw, list) or len(raw) != DIMENSION or any(not _finite(v) for v in raw):
        raise ValueError('invalid 512-D vector')
    scale = max(abs(v) for v in raw)
    if scale <= 1e-30:
        raise ValueError('zero vector')
    norm = math.sqrt(math.fsum((v / scale) ** 2 for v in raw))
    return [(v / scale) / norm for v in raw]


def match_score(reference, query):
    """Stored f64 normalization, then normalized one-item centroid, then fsum."""
    reference, query = normal64(reference), normal64(query)
    centroid = normal64([math.fsum([v]) for v in reference])
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(centroid, query))))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def validate_evidence_pins(pins, protocol_sha256):
    """Shape only; the sole real-data orchestrator verifies bytes and semantics."""
    if not isinstance(pins, dict) or set(pins) != EVIDENCE_KEYS:
        raise ValueError('complete protocol/run/output/primary evidence pins required')
    for pin in pins.values():
        if (not isinstance(pin, dict) or set(pin) != {'path', 'bytes', 'sha256'} or
                not _text(pin['path']) or not Path(pin['path']).is_absolute() or
                type(pin['bytes']) is not int or pin['bytes'] <= 0 or not _hash(pin['sha256'])):
            raise ValueError('invalid evidence pin')
    if pins['protocol']['sha256'] != protocol_sha256:
        raise ValueError('protocol evidence pin mismatch')


def validate_predictor(predictor):
    """Query truth is not accepted here. K and the complete population are fixed."""
    if set(predictor) != {'split', 'gallery', 'queries'} or predictor['split'] not in ('C', 'T'):
        raise ValueError('predictor must contain only split, gallery, queries')
    gallery, queries = predictor['gallery'], predictor['queries']
    if not isinstance(gallery, list) or len(gallery) < 2 or not isinstance(queries, list) or not queries:
        raise ValueError('K >= 2 and a nonempty planned query population required')
    if any(set(g) != {'person_id', 'sample_id'} or not all(_text(v) for v in g.values()) for g in gallery):
        raise ValueError('each person requires exactly one labeled reference')
    labels, refs = [g['person_id'] for g in gallery], [g['sample_id'] for g in gallery]
    if len(set(labels)) != len(labels) or len(set(refs)) != len(refs):
        raise ValueError('duplicate gallery person or reference')
    if any(not _text(q) for q in queries) or len(set(queries)) != len(queries) or set(queries) & set(refs):
        raise ValueError('duplicate, invalid, or reused query/reference sample')
    return labels, refs


def read_candidate_lock(path, expected_sha256):
    """Caller retains the returned SHA outside the lock before opening T."""
    raw = Path(path).read_bytes()
    if not _hash(expected_sha256) or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('candidate lock hash mismatch')
    lock = json.loads(raw)
    if (lock.get('schema') != SCHEMA or lock.get('rule') != RULE or
            lock.get('status') != 'candidate_locked' or lock.get('margin') != MARGIN or
            lock.get('baseline') != BASELINE or lock.get('grid') != list(GRID) or
            lock.get('threshold') not in GRID or
            lock.get('scoring_sha256') != file_digest(__file__)):
        raise ValueError('invalid or stale candidate lock')
    validate_evidence_pins(lock.get('evidence_pins'), lock.get('protocol_sha256'))
    return lock


def score_population(predictor, vectors, *, candidate_lock=None, lock_sha256=None):
    """Compute scores without query labels; missing/invalid vectors stay in rows.

    For T, a successfully written, source-bound lock is checked before touching
    any vector. Invalid/missing references invalidate the entire fixed gallery;
    neither K nor the query denominator may be silently reduced.
    """
    labels, refs = validate_predictor(predictor)
    lock = None
    if predictor['split'] == 'T':
        if candidate_lock is None:
            raise ValueError('write and retain a candidate lock before scoring T')
        lock = read_candidate_lock(candidate_lock, lock_sha256)
        if digest(predictor) not in (lock['holdout_predictor_sha256'], lock['holdout_diagnostic_predictor_sha256']):
            raise ValueError('holdout population differs from candidate lock')
    if not isinstance(vectors, dict) or set(vectors) - set(refs + predictor['queries']):
        raise ValueError('vectors contain samples outside the fixed split')
    invalid_refs = []
    for ref in refs:
        try:
            normal64(vectors.get(ref))
        except ValueError:
            invalid_refs.append(ref)
    rows = []
    for query in predictor['queries']:
        row = {'query_id': query, 'scores': [], 'unavailable_reason': None}
        if invalid_refs:
            row['unavailable_reason'] = 'incomplete_gallery'
        else:
            try:
                normal64(vectors.get(query))
                row['scores'] = [{'person_id': g['person_id'],
                                  'score': match_score(vectors[g['sample_id']], vectors[query])}
                                 for g in predictor['gallery']]
            except ValueError:
                row['unavailable_reason'] = 'invalid_or_missing_query_vector'
        rows.append(row)
    return {'schema': SCHEMA, 'predictor': predictor, 'rows': rows,
            'unavailable_reference_ids': invalid_refs,
            'candidate_lock_sha256': lock_sha256 if lock else None,
            'allowed_thresholds': [lock['threshold'], BASELINE] if lock else list(GRID)}


def decide(row, gallery_labels, threshold):
    """No synthetic runner-up: an incomplete full gallery is unavailable."""
    if not _finite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('threshold must be finite within [0, 1]')
    result = {'query_id': row['query_id'], 'emitted': None, 'winner': None,
              'score': None, 'margin': None, 'available': False,
              'reason': row.get('unavailable_reason')}
    if result['reason']:
        return result
    scores = row.get('scores', [])
    if (len(gallery_labels) < 2 or len(set(gallery_labels)) != len(gallery_labels) or
            len(scores) != len(gallery_labels) or
            {s.get('person_id') for s in scores} != set(gallery_labels) or
            any(not _finite(s.get('score')) or not -1 <= s['score'] <= 1 for s in scores)):
        result['reason'] = 'missing_or_invalid_full_gallery_rival'
        return result
    ordered = sorted(scores, key=lambda s: (-s['score'], s['person_id']))
    best, second = ordered[:2]
    margin = best['score'] - second['score']
    reason = ('unknown_below_threshold' if best['score'] < threshold else
              'unknown_ambiguous' if margin < MARGIN else 'experimental_identity_match')
    result.update(winner=best['person_id'], score=best['score'], margin=margin,
                  available=True, reason=reason,
                  emitted=best['person_id'] if reason == 'experimental_identity_match' else None)
    return result


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def summarize(scored, truth, threshold):
    """Truth maps every query to kind, person_id, speaker_id, session_id.

    kind is known/unknown/unresolved; person_id is a gallery ID only for known.
    Metadata are anonymous audit IDs, not claimed independent trials. No CI is
    attached automatically. T may be summarized only at the candidate/baseline.
    """
    labels, _ = validate_predictor(scored['predictor'])
    queries = scored['predictor']['queries']
    if threshold not in scored['allowed_thresholds']:
        raise ValueError('threshold outside the predeclared evaluated set')
    if (len(scored['rows']) != len(queries) or
            [r['query_id'] for r in scored['rows']] != queries or set(truth) != set(queries)):
        raise ValueError('complete ordered population and complete truth required')
    counts = dict.fromkeys(('planned', 'known', 'unknown', 'unresolved_truth', 'known_correct',
                            'known_wrong', 'known_refused', 'known_unavailable',
                            'unknown_accepted', 'unknown_refused', 'unknown_unavailable',
                            'unresolved_emitted', 'unresolved_unavailable', 'unresolved_no_output'), 0)
    reasons, outcomes, people, sessions = {}, [], {}, set()
    for row in scored['rows']:
        q, annotation = row['query_id'], truth[row['query_id']]
        if (set(annotation) != {'kind', 'person_id', 'speaker_id', 'session_id'} or
                annotation['kind'] not in ('known', 'unknown', 'unresolved') or
                not _text(annotation['speaker_id']) or not _text(annotation['session_id'])):
            raise ValueError('invalid truth or missing anonymous source metadata')
        kind, person = annotation['kind'], annotation['person_id']
        if (kind == 'known' and person not in labels) or (kind != 'known' and person is not None):
            raise ValueError('truth/gallery mismatch')
        decision = decide(row, labels, threshold)
        counts['planned'] += 1
        counts['unresolved_truth' if kind == 'unresolved' else kind] += 1
        if not decision['available']:
            outcome = kind + '_unavailable'
        elif kind == 'unresolved':
            outcome = 'unresolved_emitted' if decision['emitted'] is not None else 'unresolved_no_output'
        elif decision['emitted'] is None:
            outcome = kind + '_refused'
        elif kind == 'unknown':
            outcome = 'unknown_accepted'
        else:
            outcome = 'known_correct' if decision['emitted'] == person else 'known_wrong'
        counts[outcome] = counts.get(outcome, 0) + 1
        reasons[decision['reason']] = reasons.get(decision['reason'], 0) + 1
        outcomes.append(dict(decision, outcome=outcome))
        key = annotation['speaker_id']
        p = people.setdefault(key, {'kind': kind, 'person_id': person, 'n': 0, 'correct': 0})
        if (p['kind'], p['person_id']) != (kind, person):
            raise ValueError('inconsistent per-speaker truth')
        p['n'] += 1
        p['correct'] += outcome == 'known_correct'
        sessions.add(annotation['session_id'])
    k, u = counts['known'], counts['unknown']
    rates = {name: _ratio(counts[name], k) for name in
             ('known_correct', 'known_wrong', 'known_refused', 'known_unavailable')}
    rates.update({name: _ratio(counts[name], u) for name in
                  ('unknown_accepted', 'unknown_refused', 'unknown_unavailable')})
    emitted = counts['known_correct'] + counts['known_wrong'] + counts['unknown_accepted']
    rates['verified_output_precision'] = _ratio(counts['known_correct'], emitted)
    rates['known_not_correct'] = _ratio(k - counts['known_correct'], k)
    rates['known_available'] = _ratio(k - counts['known_unavailable'], k)
    rates['unknown_available'] = _ratio(u - counts['unknown_unavailable'], u)
    rates['all_planned_available'] = _ratio(sum(o['available'] for o in outcomes), counts['planned'])
    known_people = [p for p in people.values() if p['kind'] == 'known']
    return {'threshold': threshold, 'margin': MARGIN, 'counts': counts, 'rates': rates,
            'failure_sensitivity_ranges_not_ci': {
                'known_wrong': [rates['known_wrong'], _ratio(counts['known_wrong'] + counts['known_unavailable'], k)],
                'unknown_accepted': [rates['unknown_accepted'], _ratio(counts['unknown_accepted'] + counts['unknown_unavailable'], u)]},
            'known_correct_speaker_macro': _ratio(math.fsum(p['correct'] / p['n'] for p in known_people), len(known_people)),
            'units': {'gallery_size': len(labels), 'query_speakers': len(people), 'query_sessions': len(sessions),
                      'known_query_speakers': len(known_people),
                      'unknown_query_speakers': sum(p['kind'] == 'unknown' for p in people.values()),
                      'queries': len(queries), 'available_pair_scores': sum(len(labels) for o in outcomes if o['available']),
                      'independent_trials': None},
            'reason_counts': reasons, 'outcomes': outcomes, 'warning': WARNING,
            'precision_note': 'Precision excludes unresolved truth and depends on the planned known/unknown mix.',
            'units_note': 'Source counts use supplied query metadata only; enrollment sources and dependence blocks require the frozen manifest. Counts do not establish independence.'}


def select_candidate(scored, truth):
    """C only. Incomplete inputs are BLOCKED, not a model/policy negative."""
    if scored['predictor']['split'] != 'C':
        raise ValueError('selection accepts calibration C only; never T')
    labels, _ = validate_predictor(scored['predictor'])
    baseline = summarize(scored, truth, BASELINE)
    counts = baseline['counts']
    reasons = []
    if not counts['known'] or not counts['unknown']:
        reasons.append('missing_required_known_or_unknown_population')
    if counts['unresolved_truth']:
        reasons.append('unresolved_required_truth')
    if counts['known_unavailable'] or counts['unknown_unavailable']:
        reasons.append('unavailable_required_predictions')
    if len({a['speaker_id'] for a in truth.values()}) != len(truth):
        reasons.append('requires_one_prespecified_primary_query_per_speaker')
    known_people = [a['person_id'] for a in truth.values() if a['kind'] == 'known']
    if sorted(known_people) != sorted(labels):
        reasons.append('known_primary_person_ids_must_cover_exact_gallery_once')
    if reasons:
        return {'status': 'BLOCKED_INCONCLUSIVE', 'threshold': None, 'rule': RULE,
                'stop_before_T': True, 'selection_blocked_by_incomplete_data': True,
                'block_reasons': reasons, 'grid_results': [],
                'chosen_calibration_report': None, 'warning': WARNING}
    reports = [summarize(scored, truth, t) for t in GRID]
    feasible = [r for r in reports if r['counts']['known_wrong'] == 0
                and r['counts']['unknown_accepted'] == 0 and r['counts']['known_correct'] > 0]
    chosen = max(feasible, key=lambda r: (r['counts']['known_correct'], r['threshold'])) if feasible else None
    return {'status': 'candidate' if chosen else 'no_useful_candidate_stop_before_T',
            'threshold': chosen['threshold'] if chosen else None, 'rule': RULE,
            'stop_before_T': chosen is None, 'selection_blocked_by_incomplete_data': False,
            'block_reasons': [], 'warning': WARNING,
            'grid_results': [{'threshold': r['threshold'], 'counts': r['counts']} for r in reports],
            'chosen_calibration_report': chosen}


def write_candidate_lock(path, scored, truth, holdout_predictor, *, protocol_sha256, evidence_pins, holdout_diagnostic_predictor):
    """Exclusive create after orchestrator validation; no T vectors/truth inputs."""
    validate_evidence_pins(evidence_pins, protocol_sha256)
    validate_predictor(holdout_predictor)
    validate_predictor(holdout_diagnostic_predictor)
    if (holdout_diagnostic_predictor['split'] != 'T' or
            holdout_diagnostic_predictor['gallery'] != holdout_predictor['gallery'] or
            not set(holdout_predictor['queries']) <= set(holdout_diagnostic_predictor['queries'])):
        raise ValueError('frozen full-query T diagnostic predictor required')
    if holdout_predictor['split'] != 'T' or not _hash(protocol_sha256):
        raise ValueError('frozen T predictor and protocol SHA-256 required')
    selection = select_candidate(scored, truth)
    if selection['status'] == 'BLOCKED_INCONCLUSIVE':
        raise ValueError('BLOCKED_INCONCLUSIVE: stop before T; ' + ', '.join(selection['block_reasons']))
    if selection['threshold'] is None:
        raise ValueError('no useful candidate: stop before T')
    lock = {'schema': SCHEMA, 'status': 'candidate_locked', 'rule': RULE,
            'threshold': selection['threshold'], 'margin': MARGIN, 'baseline': BASELINE,
            'grid': list(GRID), 'scoring_sha256': file_digest(__file__),
            'protocol_sha256': protocol_sha256, 'evidence_pins': evidence_pins,
            'calibration_scores_sha256': digest(scored), 'calibration_truth_sha256': digest(truth),
            'calibration_selection_sha256': digest(selection),
            'holdout_predictor_sha256': digest(holdout_predictor),
            'holdout_diagnostic_predictor_sha256': digest(holdout_diagnostic_predictor), 'warning': WARNING}
    with Path(path).open('xb') as output:
        output.write(canonical(lock) + b'\n')
        output.flush()
        import os
        os.fsync(output.fileno())
    return {'path': str(Path(path).resolve()), 'sha256': file_digest(path), 'lock': lock}


def _binomial_cdf(k, n, p):
    if k < 0:
        return 0.0
    if k >= n or p == 0:
        return 1.0
    if p == 1:
        return 0.0
    terms = [math.lgamma(n + 1) - math.lgamma(j + 1) - math.lgamma(n - j + 1)
             + j * math.log(p) + (n - j) * math.log1p(-p) for j in range(k + 1)]
    maximum = max(terms)
    return min(1.0, math.exp(maximum) * math.fsum(math.exp(v - maximum) for v in terms))


def binomial_interval(errors, trials, *, independence_basis, denominator,
                      confidence=0.95, sides='two-sided'):
    """Exact Clopper-Pearson inversion, conditional on an explicit iid claim.

    This helper cannot establish that claim. Do not pass correlated crops/pairs,
    post-selection C evidence, unresolved outcomes, or effective sample sizes.
    No bootstrap or continuity correction. n=0 returns undefined bounds.
    """
    if (type(errors) is not int or type(trials) is not int or not 0 <= errors <= trials or
            not _finite(confidence) or not 0 < confidence < 1 or sides not in ('upper', 'two-sided') or
            not _text(independence_basis) or not _text(denominator)):
        raise ValueError('valid counts, explicit independence basis and denominator required')
    alpha = (1 - confidence) / (2 if sides == 'two-sided' else 1)
    lower = upper = None
    if trials:
        lower = 0.0
        if sides == 'two-sided' and errors:
            lo, hi = 0.0, 1.0
            for _ in range(80):
                mid = (lo + hi) / 2
                if _binomial_cdf(trials - errors, trials, 1 - mid) < alpha:
                    lo = mid
                else:
                    hi = mid
            lower = (lo + hi) / 2
        if errors == trials:
            upper = 1.0
        elif errors == 0:
            upper = -math.expm1(math.log(alpha) / trials)
        else:
            lo, hi = 0.0, 1.0
            for _ in range(80):
                mid = (lo + hi) / 2
                if _binomial_cdf(errors, trials, mid) > alpha:
                    lo = mid
                else:
                    hi = mid
            upper = (lo + hi) / 2
    return {'method': 'Clopper-Pearson exact binomial inversion', 'errors': errors, 'trials': trials,
            'confidence': confidence, 'sides': sides, 'lower': lower, 'upper': upper,
            'independence_basis_asserted_by_caller': independence_basis, 'denominator': denominator,
            'qualification': 'Conditional on the stated independent Bernoulli sampling model; not established by this helper. No multiplicity adjustment or safety certification.'}
