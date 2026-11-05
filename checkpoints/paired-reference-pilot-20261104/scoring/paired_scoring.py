#!/usr/bin/env python3
"""Research-only paired scoring of already saved vectors. No inference or network.

Both arms share every query and all three planned references must be available,
including for the single-reference arm. C chooses each threshold independently.
A single atomic paired lock releases T, whose predictions are sealed before any
T truth parsing. Hash seals are audit boundaries, not security/blinding controls.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PRODUCT = ROOT / 'backend-voice-source-20261104'
LEGACY = ROOT / 'backend-calibration-pilot-20261104/scoring.py'
sys.dont_write_bytecode = True
sys.path.insert(0, str(PRODUCT))
from audio_backend import verified_identity, support_vectors  # exact product code

_spec = importlib.util.spec_from_file_location('paired_legacy_reporting', LEGACY)
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)

ARMS = ('single', 'multi3')
SCHEMA = 1
DESIGN = 'paired_three_turns_phase_a_to_d_same_day_label_not_wallclock_or_strict_D3'
RULE = legacy.RULE
GRID = legacy.GRID
MARGIN = legacy.MARGIN
WARNING = ('Research only. No production threshold recommendation, formal population CI, '
           'independence claim, low-risk certification, wall-clock chronology proof, or strict D3 session claim.')
SOURCE_FILES = {'paired_scoring': Path(__file__).resolve(), 'legacy_reporting': LEGACY}
for _name in ('__init__', 'verified_identity', 'support_vectors', 'speaker_support', 'speaker_evidence', 'identity_canonical'):
    SOURCE_FILES['product_' + _name] = PRODUCT / 'audio_backend' / (_name + '.py')
for _name, _module in tuple(sys.modules.items()):
    if _name == 'audio_backend' or _name.startswith('audio_backend.'):
        _path = Path(_module.__file__).resolve()
        if _path.parent != PRODUCT / 'audio_backend':
            raise ValueError('product module resolved outside pinned source tree')
        if _path not in SOURCE_FILES.values():
            raise ValueError('unlisted product import dependency')

canonical = legacy.canonical
digest = legacy.digest
file_digest = legacy.file_digest


def pin(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def verify_pin(value):
    if (not isinstance(value, dict) or set(value) != {'path', 'bytes', 'sha256'} or
            not isinstance(value['path'], str) or not Path(value['path']).is_absolute() or
            type(value['bytes']) is not int or value['bytes'] <= 0 or not legacy._hash(value['sha256'])):
        raise ValueError('invalid file pin')
    if pin(value['path']) != value:
        raise ValueError('file pin mismatch: ' + value['path'])
    return value


def source_pins():
    return {name: pin(path) for name, path in SOURCE_FILES.items()}


IMPORTED_SOURCES = source_pins()


def verify_sources(expected):
    if expected != IMPORTED_SOURCES or expected != source_pins():
        raise ValueError('changed source or stale imported source')


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate JSON key: ' + key)
        out[key] = value
    return out


def read_json(path):
    return json.loads(Path(path).read_bytes(), object_pairs_hook=_no_duplicates,
                      parse_constant=lambda s: (_ for _ in ()).throw(ValueError('nonfinite JSON: ' + s)))


def read_pinned(value):
    verify_pin(value)
    return read_json(value['path'])


def seal(path, value):
    """Publish a complete fsynced file atomically; never overwrite an old seal.

    The temp file is on the same filesystem. link() is atomic and exclusive;
    failure cannot publish a partial paired lock. External retention of the SHA
    is required. This does not defend against an actor replacing all pins.
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # FileExistsError is deliberately not retried.
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    return pin(path)


def validate_predictor(value):
    if (not isinstance(value, dict) or set(value) != {'split', 'gallery', 'queries', 'primary_queries'} or
            value['split'] not in ('C', 'T')):
        raise ValueError('predictor requires split, gallery, queries, primary_queries only')
    gallery, queries, primary = value['gallery'], value['queries'], value['primary_queries']
    if not isinstance(gallery, list) or len(gallery) < 2:
        raise ValueError('at least two fixed gallery people required')
    labels, refs = [], []
    for person in gallery:
        if (not isinstance(person, dict) or set(person) != {'person_id', 'reference_ids'} or
                not legacy._text(person['person_id']) or not isinstance(person['reference_ids'], list) or
                len(person['reference_ids']) != 3 or not all(legacy._text(r) for r in person['reference_ids'])):
            raise ValueError('exactly three ordered references per known gallery person required')
        labels.append(person['person_id'])
        refs.extend(person['reference_ids'])
    if len(set(labels)) != len(labels) or len(set(refs)) != len(refs):
        raise ValueError('duplicate gallery person/reference')
    for population in (queries, primary):
        if (not isinstance(population, list) or not population or
                not all(legacy._text(q) for q in population) or len(set(population)) != len(population)):
            raise ValueError('nonempty unique ordered queries required')
    if set(queries) & set(refs) or not set(primary) <= set(queries):
        raise ValueError('reference/query overlap or primary outside all queries')
    if [q for q in queries if q in set(primary)] != primary:
        raise ValueError('primary order must follow all-query order')
    return labels, refs


def _legacy_predictor(predictor, queries):
    return {'split': predictor['split'],
            'gallery': [{'person_id': p['person_id'], 'sample_id': p['reference_ids'][0]}
                        for p in predictor['gallery']], 'queries': list(queries)}


def selection_rule():
    return {'grid': list(GRID), 'margin': MARGIN, 'selection': RULE,
            'arms': list(ARMS), 'single_reference': 'reference_ids[0]',
            'multi_reference': 'all_three_exact_product_centroid_score',
            'selection_population': 'prespecified_primary_only',
            'both_arms_required': True, 'one_arm_no_candidate': 'stop_before_T',
            'full_gallery_missing_reference': 'both_arms_unavailable_no_reduction',
            'holdout_thresholds': 'each_arm_locked_candidate_only'}


def freeze_protocol(c_predictor, c_truth, t_predictor, t_truth, source_evidence, output):
    """Freeze before inference. Truth is only byte-hashed, not parsed here."""
    inputs = {split: {'predictor': pin(p), 'truth': pin(t)} for split, p, t in
              (('C', c_predictor, c_truth), ('T', t_predictor, t_truth))}
    protocol = {'schema': SCHEMA, 'design': DESIGN, 'sources': source_pins(),
                'inputs': inputs, 'source_evidence': pin(source_evidence),
                'selection_rule': selection_rule(), 'warning': WARNING}
    _validate_protocol(protocol)
    return seal(output, protocol)


def _validate_protocol(protocol):
    if (not isinstance(protocol, dict) or set(protocol) !=
            {'schema', 'design', 'sources', 'inputs', 'source_evidence', 'selection_rule', 'warning'} or
            protocol['schema'] != SCHEMA or protocol['design'] != DESIGN or
            protocol['selection_rule'] != selection_rule() or protocol['warning'] != WARNING):
        raise ValueError('invalid or altered paired protocol')
    verify_sources(protocol['sources'])
    source_evidence = read_pinned(protocol['source_evidence'])
    if not isinstance(protocol['inputs'], dict) or set(protocol['inputs']) != {'C', 'T'}:
        raise ValueError('both C and T inputs required')
    predictors = {}
    for split in ('C', 'T'):
        local = protocol['inputs'][split]
        if not isinstance(local, dict) or set(local) != {'predictor', 'truth'}:
            raise ValueError('predictor and truth pins required')
        predictor = read_pinned(local['predictor'])
        validate_predictor(predictor)
        if predictor['split'] != split:
            raise ValueError('split mismatch')
        # Never parse T truth here. Verifying bytes is not label blinding.
        verify_pin(local['truth'])
        predictors[split] = predictor
    cp, tp = predictors['C'], predictors['T']
    cl, cr = validate_predictor(cp)
    tl, tr = validate_predictor(tp)
    if (set(cl) & set(tl) or (set(cr) | set(cp['queries'])) & (set(tr) | set(tp['queries']))):
        raise ValueError('C/T gallery person or sample IDs overlap')
    validate_source_roles(source_evidence, predictors)
    return predictors


def validate_source_roles(evidence, predictors):
    """Check supplied source-role claims, not audio purity or corpus identity.

    Three distinct phase-a meeting turns are deliberately NOT three sessions.
    Extra evidence fields may hold the upstream acquisition/native audit pins.
    This metadata exposes labels, so the workflow is not label-blinded.
    """
    if not isinstance(evidence, dict) or evidence.get('schema') != 1 or not isinstance(evidence.get('records'), list):
        raise ValueError('source evidence requires schema 1 and complete source-role records')
    required = {'id', 'split', 'role', 'kind', 'person_id', 'speaker_id', 'session_id', 'recording_day_label', 'meeting_phase', 'turn_id'}
    records, sessions = {}, {}
    for row in evidence['records']:
        if (not isinstance(row, dict) or not required <= set(row) or
                any(not legacy._text(row[k]) for k in ('id', 'speaker_id', 'session_id', 'recording_day_label', 'turn_id')) or
                row['id'] in records or row['split'] not in ('C', 'T') or
                row['role'] not in ('reference', 'query') or row['kind'] not in ('known', 'unknown') or
                row['meeting_phase'] != ('a' if row['role'] == 'reference' else 'd') or
                (row['kind'] == 'known' and not legacy._text(row['person_id'])) or
                (row['kind'] == 'unknown' and row['person_id'] is not None)):
            raise ValueError('invalid or duplicate source-role record')
        session = (row['split'], row['recording_day_label'], row['meeting_phase'])
        if row['session_id'] in sessions and sessions[row['session_id']] != session:
            raise ValueError('one source meeting cannot have conflicting split, day label, or phase')
        sessions[row['session_id']] = session
        records[row['id']] = row
    speakers = {}
    expected_ids = set()
    for split, predictor in predictors.items():
        labels, refs = validate_predictor(predictor)
        ids = set(refs) | set(predictor['queries'])
        expected_ids |= ids
        if ids != {r['id'] for r in records.values() if r['split'] == split}:
            raise ValueError('source roles must exactly cover the full frozen population')
        local = [records[q] for q in ids]
        speakers[split] = {r['speaker_id'] for r in local}
        for row in local:
            if (row['role'] != ('reference' if row['id'] in refs else 'query') or
                    (row['kind'] == 'known' and row['person_id'] not in labels)):
                raise ValueError('source-role/predictor identity mismatch')
        for person in predictor['gallery']:
            reference = [records[r] for r in person['reference_ids']]
            if (any(r['kind'] != 'known' or r['person_id'] != person['person_id'] for r in reference) or
                    len({r['speaker_id'] for r in reference}) != 1 or
                    len({r['session_id'] for r in reference}) != 1 or
                    len({r['recording_day_label'] for r in reference}) != 1 or
                    len({r['turn_id'] for r in reference}) != 3):
                raise ValueError('known references require three distinct turns of one speaker in one phase-a meeting')
            query = [r for r in local if r['role'] == 'query' and r['person_id'] == person['person_id']]
            if not query or any(r['speaker_id'] != reference[0]['speaker_id'] or
                                r['session_id'] == reference[0]['session_id'] or
                                r['recording_day_label'] != reference[0]['recording_day_label'] for r in query):
                raise ValueError('known queries must match reference speaker in phase-d meeting with same raw day label')
        expected_truth = {r['id']: {k: r[k] for k in ('kind', 'person_id', 'speaker_id', 'session_id')}
                          for r in local if r['role'] == 'query'}
        validate_truth(predictor, expected_truth)
    if set(records) != expected_ids or speakers['C'] & speakers['T']:
        raise ValueError('unexpected source records or C/T speaker identity overlap')
    return records


def load_protocol(path, sha256):
    actual = pin(path)
    if not legacy._hash(sha256) or actual['sha256'] != sha256:
        raise ValueError('protocol differs from externally retained SHA')
    protocol = read_pinned(actual)
    predictors = _validate_protocol(protocol)
    return actual, protocol, predictors


def normalize(raw):
    if not isinstance(raw, list) or len(raw) != 512:
        raise ValueError('exact finite 512-D vector required')
    return support_vectors.normalized_vector(raw)[0]


def load_vectors(value):
    """Accept canonical JSON map or saved native NPZ; never silently zip/drop IDs."""
    verify_pin(value)
    if Path(value['path']).suffix.lower() != '.npz':
        vectors = read_json(value['path'])
        if not isinstance(vectors, dict):
            raise ValueError('JSON vectors must be an ID-to-vector map')
        return vectors
    import numpy as np
    with np.load(value['path'], allow_pickle=False) as saved:
        if not {'ids', 'normalized', 'valid'} <= set(saved.files):
            raise ValueError('native NPZ requires ids, normalized, valid arrays')
        ids, vectors, valid = saved['ids'], saved['normalized'], saved['valid']
        if (ids.ndim != 1 or any(not isinstance(q, str) or not q for q in ids.tolist()) or
                len(set(ids.tolist())) != len(ids) or vectors.shape != (len(ids), 512) or
                vectors.dtype.kind != 'f' or valid.shape != (len(ids),) or valid.dtype.kind != 'b'):
            raise ValueError('malformed or duplicate native vector population')
        return {q: vector.tolist() if okay else None for q, vector, okay in zip(ids.tolist(), vectors, valid)}


def _raw_scores(predictor, vectors):
    labels, refs = validate_predictor(predictor)
    if not isinstance(vectors, dict) or set(vectors) - set(refs + predictor['queries']):
        raise ValueError('vector IDs outside the frozen population')
    normalized, invalid = {}, []
    for ref in refs:
        try:
            normalized[ref] = normalize(vectors.get(ref))
        except (ValueError, TypeError, OverflowError):
            invalid.append(ref)
    # A degenerate multi-reference centroid also makes both arms unavailable.
    if not invalid:
        for person in predictor['gallery']:
            try:
                verified_identity.centroid_score([normalized[r] for r in person['reference_ids']],
                                                  normalized[person['reference_ids'][0]])
            except (ValueError, TypeError, OverflowError):
                invalid.extend(person['reference_ids'])
    rows = {arm: [] for arm in ARMS}
    for query in predictor['queries']:
        reason = 'incomplete_or_invalid_gallery' if invalid else None
        if not reason:
            try:
                vector = normalize(vectors.get(query))
            except (ValueError, TypeError, OverflowError):
                reason = 'invalid_or_missing_query_vector'
        for arm in ARMS:
            scores = []
            if not reason:
                for person in predictor['gallery']:
                    reference_ids = person['reference_ids'][:1] if arm == 'single' else person['reference_ids']
                    score = verified_identity.centroid_score([normalized[r] for r in reference_ids], vector)
                    scores.append({'person_id': person['person_id'], 'score': score})
            rows[arm].append({'query_id': query, 'scores': scores, 'unavailable_reason': reason})
    return rows, invalid


def read_lock(lock_pin, protocol_pin, protocol, predictors):
    lock = read_pinned(lock_pin)
    expected = {'schema', 'status', 'protocol', 'sources', 'rule', 'margin', 'grid', 'thresholds',
                'predictor_sha256', 'C_predictions', 'C_report', 'C_vectors', 'warning'}
    if (not isinstance(lock, dict) or set(lock) != expected or lock['schema'] != SCHEMA or
            lock['status'] != 'both_candidates_locked' or lock['protocol'] != protocol_pin or
            lock['rule'] != RULE or lock['margin'] != MARGIN or lock['grid'] != list(GRID) or
            lock['warning'] != WARNING or lock['sources'] != protocol['sources'] or
            lock['predictor_sha256'] != {k: digest(v) for k, v in predictors.items()} or
            not isinstance(lock['thresholds'], dict) or set(lock['thresholds']) != set(ARMS) or
            any(type(t) not in (int, float) or t not in GRID for t in lock['thresholds'].values())):
        raise ValueError('invalid, partial, or stale paired lock')
    verify_sources(lock['sources'])
    for field in ('C_predictions', 'C_report', 'C_vectors'):
        verify_pin(lock[field])
    report = read_pinned(lock['C_report'])
    if (report['status'] != 'both_candidates' or report['predictions'] != lock['C_predictions'] or
            report['protocol'] != protocol_pin or
            lock['thresholds'] != {a: report['arms'][a]['selection']['threshold'] for a in ARMS}):
        raise ValueError('paired lock differs from sealed C selections')
    calibration = read_pinned(lock['C_predictions'])
    if (calibration['split'] != 'C' or calibration['protocol'] != protocol_pin or
            calibration['sources'] != protocol['sources'] or calibration['vectors'] != lock['C_vectors'] or
            calibration['predictor'] != predictors['C'] or calibration['predictor_sha256'] != digest(predictors['C']) or
            calibration['paired_lock'] is not None or calibration['thresholds'] != {a: None for a in ARMS} or
            calibration['fixed_decisions'] is not None):
        raise ValueError('paired lock differs from sealed C prediction evidence')
    rows, invalid = _raw_scores(predictors['C'], load_vectors(lock['C_vectors']))
    if rows != calibration['rows'] or invalid != calibration['unavailable_reference_ids']:
        raise ValueError('C predictions differ from sealed vector evidence')
    truth, primary_truth = read_truth(protocol, predictors['C'])
    for arm in ARMS:
        selection = legacy.select_candidate(_view(calibration, arm, True), primary_truth)
        if selection != report['arms'][arm]['selection'] or selection['threshold'] != lock['thresholds'][arm]:
            raise ValueError('paired lock differs from independently recomputed C selection')
    return lock


def score_and_seal(protocol_path, protocol_sha256, split, vectors_path, vectors_sha256, output, lock_pin=None):
    protocol_pin, protocol, predictors = load_protocol(protocol_path, protocol_sha256)
    if split not in ('C', 'T'):
        raise ValueError('invalid split')
    lock = None
    if split == 'T':
        if lock_pin is None:
            raise ValueError('both arm candidate locks must be sealed before touching T vectors')
        lock = read_lock(lock_pin, protocol_pin, protocol, predictors)
    vectors_pin = pin(vectors_path)
    if not legacy._hash(vectors_sha256) or vectors_pin['sha256'] != vectors_sha256:
        raise ValueError('saved vectors differ from externally retained SHA')
    predictor = predictors[split]
    rows, invalid = _raw_scores(predictor, load_vectors(vectors_pin))
    thresholds = lock['thresholds'] if lock else {arm: None for arm in ARMS}
    labels, _ = validate_predictor(predictor)
    predictions = {'schema': SCHEMA, 'status': 'predictions_sealed_before_truth', 'split': split,
                   'protocol': protocol_pin, 'sources': protocol['sources'], 'predictor': predictor,
                   'predictor_sha256': digest(predictor), 'vectors': vectors_pin,
                   'paired_lock': lock_pin, 'thresholds': thresholds,
                   'rows': rows, 'unavailable_reference_ids': invalid,
                   'fixed_decisions': {arm: [legacy.decide(r, labels, thresholds[arm]) for r in rows[arm]]
                                       for arm in ARMS} if lock else None, 'warning': WARNING}
    return seal(output, predictions)


def _view(predictions, arm, primary=False):
    predictor = predictions['predictor']
    queries = predictor['primary_queries'] if primary else predictor['queries']
    row_map = {r['query_id']: r for r in predictions['rows'][arm]}
    return {'schema': 1, 'predictor': _legacy_predictor(predictor, queries),
            'rows': [row_map[q] for q in queries],
            'allowed_thresholds': [predictions['thresholds'][arm]] if predictions['split'] == 'T' else list(GRID)}


def validate_truth(predictor, truth):
    if not isinstance(truth, dict) or set(truth) != set(predictor['queries']):
        raise ValueError('truth must exactly cover all frozen queries')
    # Delegate the old truth shape and per-speaker consistency checks.
    empty = {'predictor': _legacy_predictor(predictor, predictor['queries']),
             'rows': [{'query_id': q, 'scores': [], 'unavailable_reason': 'truth_validation_only'}
                      for q in predictor['queries']], 'allowed_thresholds': [1.]}
    legacy.summarize(empty, truth, 1.)
    if any(t['kind'] not in ('known', 'unknown') for t in truth.values()):
        raise ValueError('clean known/unknown manual annotations required; unresolved truth blocks')
    primary = {q: truth[q] for q in predictor['primary_queries']}
    if (len({t['speaker_id'] for t in primary.values()}) != len(primary) or
            {t['speaker_id'] for t in primary.values()} != {t['speaker_id'] for t in truth.values()}):
        raise ValueError('exactly one prespecified primary per query speaker required')
    labels, _ = validate_predictor(predictor)
    if sorted(t['person_id'] for t in primary.values() if t['kind'] == 'known') != sorted(labels):
        raise ValueError('known primaries must cover fixed gallery once; unknown references prohibited')
    if not any(t['kind'] == 'unknown' for t in primary.values()):
        raise ValueError('unknown primary population required')
    return primary


def read_truth(protocol, predictor):
    split = predictor['split']
    truth = read_pinned(protocol['inputs'][split]['truth'])
    primary = validate_truth(predictor, truth)
    source_roles = read_pinned(protocol['source_evidence'])['records']
    expected = {r['id']: {k: r[k] for k in ('kind', 'person_id', 'speaker_id', 'session_id')}
                for r in source_roles if r['split'] == split and r['role'] == 'query'}
    if truth != expected:
        raise ValueError('truth disagrees with frozen source-role evidence')
    return truth, primary


def _conditional_reference(report):
    c = report['counts']
    if c['known_unavailable'] or c['unknown_unavailable'] or c['unresolved_truth']:
        return {'available': False, 'reason': 'incomplete outcomes; descriptive failure ranges only'}
    out = {'available': True, 'formal_population_or_safety_ci': None,
           'note': 'Conditional iid-binomial reference calculations only; independence is not established. '
                   'Shared gallery, meeting, and speaker dependencies remain; no paired-effect CI.'}
    for measure, trials in (('known_wrong', c['known']), ('unknown_accepted', c['unknown'])):
        out[measure] = legacy.binomial_interval(c[measure], trials, sides='upper',
            independence_basis='Hypothetical iid Bernoulli reference model only; NOT established for these recordings.',
            denominator='Prespecified primary queries only, one per speaker; fixed gallery decision, not pairs.')
    return out


def report_sealed(protocol_path, protocol_sha256, predictions_pin, output_report, output_lock=None):
    """No threshold overrides. C report may produce one paired lock; T cannot."""
    protocol_pin, protocol, predictors = load_protocol(protocol_path, protocol_sha256)
    predictions = read_pinned(predictions_pin)  # seal exists before truth parsing
    split = predictions['split']
    if split not in predictors:
        raise ValueError('invalid prediction split')
    if (predictions['status'] != 'predictions_sealed_before_truth' or predictions['protocol'] != protocol_pin or
            predictions['sources'] != protocol['sources'] or predictions['predictor'] != predictors[split] or
            predictions['predictor_sha256'] != digest(predictors[split])):
        raise ValueError('prediction/protocol/population mismatch')
    lock = None
    if split == 'T':
        if output_lock is not None:
            raise ValueError('T cannot create or retune candidate locks')
        lock = read_lock(predictions['paired_lock'], protocol_pin, protocol, predictors)
        if predictions['thresholds'] != lock['thresholds']:
            raise ValueError('holdout thresholds differ from paired lock')
    elif predictions['paired_lock'] is not None or predictions['thresholds'] != {a: None for a in ARMS}:
        raise ValueError('unexpected calibration lock/thresholds')
    # Recompute truth-free scores from sealed saved vectors before reading labels.
    rows, invalid = _raw_scores(predictors[split], load_vectors(predictions['vectors']))
    if rows != predictions['rows'] or invalid != predictions['unavailable_reference_ids']:
        raise ValueError('sealed predictions differ from saved vectors')
    labels, _ = validate_predictor(predictors[split])
    expected_decisions = {a: [legacy.decide(r, labels, lock['thresholds'][a]) for r in rows[a]] for a in ARMS} if lock else None
    if predictions['fixed_decisions'] != expected_decisions:
        raise ValueError('sealed fixed decisions mismatch')
    truth, primary_truth = read_truth(protocol, predictors[split])
    report = {'schema': SCHEMA, 'split': split, 'protocol': protocol_pin, 'predictions': predictions_pin,
              'truth': protocol['inputs'][split]['truth'], 'arms': {}, 'warning': WARNING,
              'formal_population_or_safety_ci': None,
              'diagnostic_note': 'All-query diagnostic includes primary. It is not selection evidence or independent trials.'}
    for arm in ARMS:
        primary, all_queries = _view(predictions, arm, True), _view(predictions, arm)
        selection = legacy.select_candidate(primary, primary_truth) if split == 'C' else None
        threshold = selection['threshold'] if selection else lock['thresholds'][arm]
        report['arms'][arm] = {'selection': selection,
            'primary': legacy.summarize(primary, primary_truth, threshold) if threshold is not None else None,
            'all_queries_diagnostic': legacy.summarize(all_queries, truth, threshold) if threshold is not None else None,
            'conditional_iid_reference_only': None}
        if split == 'T':
            report['arms'][arm]['conditional_iid_reference_only'] = _conditional_reference(report['arms'][arm]['primary'])
    success = split == 'T' or all(report['arms'][a]['selection']['status'] == 'candidate' for a in ARMS)
    report['status'] = ('both_candidates' if success else 'stop_before_T') if split == 'C' else 'holdout_reported_at_locked_thresholds'
    report['stop_before_T'] = split == 'C' and not success
    if split == 'T':
        for population in ('primary', 'all_queries_diagnostic'):
            single = report['arms']['single'][population]['counts']
            multi = report['arms']['multi3'][population]['counts']
            report.setdefault('paired_count_differences_multi3_minus_single', {})[population] = {
                k: multi[k] - single[k] for k in ('known_correct', 'known_wrong', 'unknown_accepted',
                                                 'known_refused', 'unknown_refused', 'known_unavailable', 'unknown_unavailable')}
    report_pin = seal(output_report, report)
    receipt = {'status': report['status'], 'report': report_pin, 'lock': None}
    if split == 'C' and success and output_lock is not None:
        paired = {'schema': SCHEMA, 'status': 'both_candidates_locked', 'protocol': protocol_pin,
                  'sources': protocol['sources'], 'rule': RULE, 'margin': MARGIN, 'grid': list(GRID),
                  'thresholds': {a: report['arms'][a]['selection']['threshold'] for a in ARMS},
                  'predictor_sha256': {k: digest(v) for k, v in predictors.items()},
                  'C_predictions': predictions_pin, 'C_report': report_pin,
                  'C_vectors': predictions['vectors'], 'warning': WARNING}
        receipt['lock'] = seal(output_lock, paired)
    return receipt


def _pin_with_sha(path, sha):
    value = pin(path)
    if not legacy._hash(sha) or value['sha256'] != sha:
        raise ValueError('externally retained SHA mismatch')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    freeze = sub.add_parser('freeze')
    for name in ('c-predictor', 'c-truth', 't-predictor', 't-truth', 'source-evidence', 'output'):
        freeze.add_argument('--' + name, required=True)
    for command in ('score', 'report'):
        local = sub.add_parser(command)
        local.add_argument('--protocol', required=True)
        local.add_argument('--protocol-sha256', required=True)
        local.add_argument('--output', required=True)
        if command == 'score':
            local.add_argument('--split', choices=('C', 'T'), required=True)
            local.add_argument('--vectors', required=True)
            local.add_argument('--vectors-sha256', required=True)
            local.add_argument('--lock')
            local.add_argument('--lock-sha256')
        else:
            local.add_argument('--predictions', required=True)
            local.add_argument('--predictions-sha256', required=True)
            local.add_argument('--output-lock')
    args = parser.parse_args()
    if args.command == 'freeze':
        result = freeze_protocol(args.c_predictor, args.c_truth, args.t_predictor, args.t_truth, args.source_evidence, args.output)
    elif args.command == 'score':
        if bool(args.lock) != bool(args.lock_sha256):
            parser.error('--lock and --lock-sha256 must be supplied together')
        result = score_and_seal(args.protocol, args.protocol_sha256, args.split, args.vectors, args.vectors_sha256,
                                args.output, _pin_with_sha(args.lock, args.lock_sha256) if args.lock else None)
    else:
        result = report_sealed(args.protocol, args.protocol_sha256,
                               _pin_with_sha(args.predictions, args.predictions_sha256), args.output, args.output_lock)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
