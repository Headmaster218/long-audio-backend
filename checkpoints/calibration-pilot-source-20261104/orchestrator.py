"""Small, model-free bridge from sealed native C outputs to one candidate lock.

No inference, audio processing, downloads, fitting of old samples, or automatic
T execution. The caller must freeze the protocol pin BEFORE running C.

Required protocol fields (other descriptive fields may coexist):
  scoring_source, orchestrator: exact file pins
  scoring_inputs: {C: {predictor: pin, truth: pin},
                   T: {predictor: pin, truth: pin}}
  diagnostic_inputs: same C/T predictor/truth pin shape, all three queries/person
  source_roles: full identity/date/role metadata pin (schema 1)
  calibration_run_plan, holdout_run_plan: pins of full native manifest templates
Both plans contain protocol_sha256=null and release_pin=null. C substitutes only
the frozen protocol hash. T substitutes only that hash and the actual lock pin.
The explicit primary lists in scoring_inputs are frozen before C; no selection
by score or by successful-row subset is allowed. Full C includes all diagnostic
queries and all four repeated model calls, not merely the fitted primaries.

This is an auditable sequencing guard, not a security boundary against an actor
changing both files and their externally retained pins. Root orchestration owns
the premodel freeze and corpus/source eligibility. choose/release-t never open T truth files or use T scores. evaluate-t opens T
truth files only after an exclusive prediction seal. Source-role T identity and
known/unknown metadata are audited before C, so this is not a label-blind test.
"""
from __future__ import annotations

import argparse
import copy
from datetime import date
import json
from pathlib import Path

import scoring as s
import native_runner as runner

h = runner.h


def _pin(path, sha256):
    pin = h.pinned(path)
    if pin['sha256'] != sha256:
        raise ValueError('externally retained input SHA mismatch')
    return pin


def _read(pin):
    runner.validate_pin_shape(pin)
    h.verify_pin(pin)
    return h.read_json(pin['path'])


def _verify_source(pin, expected):
    runner.validate_pin_shape(pin)
    h.verify_pin(pin)
    if pin['path'] != str(Path(expected).resolve()):
        raise ValueError('source path mismatch')


def _plan(protocol, protocol_pin, field, partition):
    plan = _read(protocol[field])
    runner.validate_plan_template(plan)
    if plan['partition'] != partition:
        raise ValueError('native run plan partition mismatch')
    _verify_source(plan['source'], runner.__file__)
    h.verify_pin(plan['helper_source'])
    return plan


def _audit_roles(protocol, primary, diagnostic, c_truth, c_diagnostic_truth, plans):
    """Audit frozen source identity metadata; never consume any T scores."""
    metadata = _read(protocol['source_roles'])
    if metadata.get('schema') != 1 or not s._hash(metadata.get('source_selection_slots_sha256')):
        raise ValueError('source role metadata binding required')
    rows = metadata['records']
    required = {'id', 'split', 'speaker_id', 'person_id', 'roster_role', 'role', 'recording_date', 'session_id'}
    if any(set(row) != required for row in rows) or len({row['id'] for row in rows}) != len(rows):
        raise ValueError('invalid or duplicate source-role records')
    sources = {}
    for split in ('C', 'T'):
        local = [row for row in rows if row['split'] == split]
        if {row['id'] for row in local} != {row['id'] for row in plans[split]['samples']}:
            raise ValueError('source roles must cover every planned sample exactly once')
        refs = [row for row in local if row['role'] == 'reference']
        queries = [row for row in local if row['role'] in ('primary', 'diagnostic')]
        if len(refs) + len(queries) != len(local):
            raise ValueError('invalid source role')
        for row in local:
            date.fromisoformat(row['recording_date'])
            if (row['roster_role'] not in ('known', 'unknown') or not s._text(row['speaker_id']) or
                    not s._text(row['session_id']) or
                    (row['roster_role'] == 'known') != s._text(row['person_id'])):
                raise ValueError('invalid source identity metadata')
        gallery = [{'person_id': row['person_id'], 'sample_id': row['id']} for row in refs]
        for predictor in (primary[split], diagnostic[split]):
            if predictor['split'] != split or sorted(predictor['gallery'], key=lambda x: x['person_id']) != sorted(gallery, key=lambda x: x['person_id']):
                raise ValueError('reference role/gallery mismatch')
        if set(primary[split]['queries']) != {row['id'] for row in queries if row['role'] == 'primary'}:
            raise ValueError('primary predictor differs from frozen source roles')
        if set(diagnostic[split]['queries']) != {row['id'] for row in queries}:
            raise ValueError('all-query diagnostic predictor differs from frozen source roles')
        by_speaker = {}
        for row in local:
            by_speaker.setdefault(row['speaker_id'], []).append(row)
        for group in by_speaker.values():
            query = [row for row in group if row['role'] != 'reference']
            reference = [row for row in group if row['role'] == 'reference']
            if (len(query) != 3 or len({row['recording_date'] for row in query}) != 3 or
                    sum(row['role'] == 'primary' for row in query) != 1 or
                    min(query, key=lambda row: row['recording_date'])['role'] != 'primary' or
                    len({(row['roster_role'], row['person_id']) for row in group}) != 1):
                raise ValueError('exact earliest-primary plus two cross-date diagnostics required')
            known = query[0]['roster_role'] == 'known'
            if len(reference) != int(known) or (known and reference[0]['recording_date'] >= min(row['recording_date'] for row in query)):
                raise ValueError('known reference must be from a separate earlier recording date')
        if split == 'C':
            expected = {row['id']: {'kind': row['roster_role'], 'person_id': row['person_id'],
                                    'speaker_id': row['speaker_id'], 'session_id': row['session_id']} for row in queries}
            if c_diagnostic_truth != expected or c_truth != {q: expected[q] for q in primary['C']['queries']}:
                raise ValueError('C truth differs from frozen source identities and dates')
        sources[split] = ({row['speaker_id'] for row in local}, {row['session_id'] for row in local})
    if any(row['split'] not in ('C', 'T') for row in rows) or any(sources['C'][i] & sources['T'][i] for i in (0, 1)):
        raise ValueError('C/T source identity/session overlap or unknown partition')


def _protocol_inputs(protocol_pin):
    protocol = _read(protocol_pin)
    _verify_source(protocol['orchestrator'], __file__)
    _verify_source(protocol['scoring_source'], s.__file__)
    inputs = protocol['scoring_inputs']
    if set(inputs) != {'C', 'T'} or any(set(inputs[p]) != {'predictor', 'truth'} for p in ('C', 'T')):
        raise ValueError('frozen scoring input pins required')
    c_predictor = _read(inputs['C']['predictor'])
    c_truth = _read(inputs['C']['truth'])
    t_predictor = _read(inputs['T']['predictor'])
    runner.validate_pin_shape(inputs['T']['truth'])  # Deliberately do NOT open T truth.
    for predictor, split in ((c_predictor, 'C'), (t_predictor, 'T')):
        s.validate_predictor(predictor)
        if predictor['split'] != split:
            raise ValueError('predictor partition mismatch')
    diagnostic = protocol['diagnostic_inputs']
    if set(diagnostic) != {'C', 'T'} or any(set(diagnostic[p]) != {'predictor', 'truth'} for p in ('C', 'T')):
        raise ValueError('frozen diagnostic input pins required')
    cd = _read(diagnostic['C']['predictor'])
    cdt = _read(diagnostic['C']['truth'])
    td = _read(diagnostic['T']['predictor'])
    s.validate_predictor(cd)
    s.validate_predictor(td)
    runner.validate_pin_shape(diagnostic['T']['truth'])  # Do not open T truth.
    c_plan = _plan(protocol, protocol_pin, 'calibration_run_plan', 'C')
    t_plan = _plan(protocol, protocol_pin, 'holdout_run_plan', 'T')
    for plan, predictor in ((c_plan, c_predictor), (t_plan, t_predictor)):
        planned = {row['id'] for row in plan['samples']}
        required = {g['sample_id'] for g in predictor['gallery']} | set(predictor['queries'])
        if not required <= planned:
            raise ValueError('frozen primary/gallery population absent from full run plan')
    if {row['id'] for row in c_plan['samples']} & {row['id'] for row in t_plan['samples']}:
        raise ValueError('C/T opaque sample IDs overlap')
    for key in ('source', 'helper_source', 'runtime_contract', 'limits', 'affinity_cpus'):
        if c_plan[key] != t_plan[key]:
            raise ValueError('C/T native contract differs')
    _audit_roles(protocol, {'C': c_predictor, 'T': t_predictor}, {'C': cd, 'T': td},
                 c_truth, cdt, {'C': c_plan, 'T': t_plan})
    rule = {'grid_start': 0, 'grid_stop': 1, 'grid_step': .01, 'margin': s.MARGIN, 'baseline': s.BASELINE,
            'C_primaries_only': True, 'C_max_wrong_known': 0, 'C_max_unknown_accepted': 0,
            'maximize_known_correct': True, 'tie_break': 'higher_threshold', 'no_useful_candidate': 'stop_before_T'}
    if protocol['selection_rule'] != rule:
        raise ValueError('protocol selection rule differs from fixed scorer')
    known = sum(a['kind'] == 'known' for a in c_truth.values())
    unknown = sum(a['kind'] == 'unknown' for a in c_truth.values())
    counts = protocol['counts']
    expected_counts = {'K_per_split': len(c_predictor['gallery']), 'unknown_people_per_split': unknown,
                       'primary_queries_per_split': len(c_predictor['queries']), 'all_queries_per_split': len(cd['queries']),
                       'distinct_clips_total': len(c_plan['samples']) + len(t_plan['samples']), 'repeat_calls_total': 8,
                       'max_encoder_calls_total': len(c_plan['samples']) + len(t_plan['samples']) + 8}
    if counts != expected_counts or len(t_predictor['queries']) != len(c_predictor['queries']) or len(td['queries']) != len(cd['queries']):
        raise ValueError('frozen pilot populations or total call budget differ')
    policy = protocol['interval_policy']
    expected_policy = {'formal_population_or_safety_CI': None, 'reference_only': 'conditional_iid_binomial_model_not_validated_sampling_guarantee',
                       'primary_n_known': known, 'primary_n_unknown': unknown, 'confidence': .95, 'C_selected_point_CI': None}
    if policy != expected_policy:
        raise ValueError('undeclared or changed interval policy/primary denominators')
    for field, per_stage in (('model_wall_seconds_total', 'wall_seconds'), ('model_cpu_seconds_total', 'cpu_seconds'),
                             ('model_output_bytes_total', 'output_bytes')):
        limit = protocol['resource_limits'][field]
        if not s._finite(limit) or not 0 < limit <= 2 * runner.LIMITS[per_stage]:
            raise ValueError('invalid combined resource budget')
    return protocol, c_predictor, c_truth, t_predictor, c_plan, t_plan, cd, cdt


def _sealed_run(protocol_pin, manifest_pin, terminal_pin, expected):
    """Shared full-population/native journal/NPZ validation, without truth."""
    manifest = _read(manifest_pin)
    if manifest != expected:
        raise ValueError('actual run manifest differs from premodel full run plan')
    runner.validate_manifest(manifest)
    terminal = _read(terminal_pin)
    ids = [sample['id'] for sample in manifest['samples']]
    stage = terminal.get('stage') or {}
    if (terminal.get('status') != 'completed_bounded_native_stage' or
            terminal.get('partition') != expected['partition'] or terminal.get('manifest_sha256') != manifest_pin['sha256'] or
            terminal.get('protocol_sha256') != protocol_pin['sha256'] or terminal.get('release_pin') != expected['release_pin'] or
            terminal.get('planned_ids') != ids or terminal.get('completed_ids') != ids or
            terminal.get('unrun_ids') != [] or terminal.get('planned_calls') != len(ids) + 4 or
            stage.get('returncode') != 0 or stage.get('positive_original_wait') is not True or
            stage.get('owned_process_group_absent') is not True or stage.get('termination_needed') is not False or
            stage.get('cleanup_error') is not None or stage.get('stop_reason') is not None):
        raise ValueError('BLOCKED_INCONCLUSIVE: complete positive-wait terminal required')
    output = Path(terminal_pin['path']).parent
    if Path(terminal_pin['path']).name != 'TERMINAL.json':
        raise ValueError('expected native TERMINAL.json')
    artifacts = terminal.get('artifacts', [])
    if len(artifacts) != 3:
        raise ValueError('complete output seals required')
    sealed = {}
    for artifact in artifacts:
        runner.validate_pin_shape(artifact)
        name = Path(artifact['path']).name
        if name not in ('vectors.npz', 'result.json', 'journal.jsonl') or name in sealed:
            raise ValueError('unexpected or duplicate run output seal')
        if artifact['path'] != str((output / 'native' / name).resolve()):
            raise ValueError('run output path mismatch')
        h.verify_pin(artifact)
        sealed[name] = artifact
    result = _read(sealed['result.json'])
    if result.get('manifest_sha256') != manifest_pin['sha256'] or result.get('vectors') != sealed['vectors.npz']:
        raise ValueError('worker output/manifest seal mismatch')
    runner.validate_success(result, manifest, output)
    for key, limit in (('elapsed_seconds', 'wall_seconds'), ('aggregate_cpu_seconds', 'cpu_seconds'),
                       ('allocated_output_bytes', 'output_bytes')):
        if not s._finite(terminal.get(key)) or not 0 <= terminal[key] <= manifest['limits'][limit]:
            raise ValueError('missing or exceeded terminal resource accounting')
    import numpy as np
    with np.load(sealed['vectors.npz']['path'], allow_pickle=False) as saved:
        values = {identity: vector.tolist() for identity, vector in zip(saved['ids'].tolist(), saved['normalized'])}
    return values, sealed, terminal


def _sealed_calibration(protocol_pin, manifest_pin, terminal_pin):
    """Validate complete C and recompute primary scores from sealed normalized NPZ."""
    protocol, cp, truth, tp, c_plan, t_plan, cd, cdt = _protocol_inputs(protocol_pin)
    expected = copy.deepcopy(c_plan)
    expected['protocol_sha256'] = protocol_pin['sha256']
    values, sealed, _ = _sealed_run(protocol_pin, manifest_pin, terminal_pin, expected)
    primary_needed = {g['sample_id'] for g in cp['gallery']} | set(cp['queries'])
    scored = s.score_population(cp, {q: values[q] for q in primary_needed})
    diagnostic_scored = s.score_population(cd, values)
    evidence = {'protocol': protocol_pin, 'calibration_manifest': manifest_pin,
                'calibration_terminal': terminal_pin, 'calibration_result': sealed['result.json'],
                'calibration_vectors': sealed['vectors.npz'], 'calibration_journal': sealed['journal.jsonl'],
                'calibration_predictor': protocol['scoring_inputs']['C']['predictor'],
                'calibration_truth': protocol['scoring_inputs']['C']['truth'],
                'holdout_predictor': protocol['scoring_inputs']['T']['predictor'],
                'holdout_diagnostic_predictor': protocol['diagnostic_inputs']['T']['predictor'],
                'calibration_diagnostic_predictor': protocol['diagnostic_inputs']['C']['predictor'],
                'calibration_diagnostic_truth': protocol['diagnostic_inputs']['C']['truth'],
                'source_roles': protocol['source_roles'],
                'calibration_run_plan': protocol['calibration_run_plan'],
                'holdout_run_plan': protocol['holdout_run_plan'], 'orchestrator': protocol['orchestrator']}
    return scored, truth, tp, evidence, t_plan, diagnostic_scored, cdt


def _report(selection, scored, truth, diagnostic_scored, diagnostic_truth):
    report = copy.deepcopy(selection)
    extras = copy.deepcopy(diagnostic_scored)
    primary = set(scored['predictor']['queries'])
    extras['predictor']['queries'] = [q for q in extras['predictor']['queries'] if q not in primary]
    extras['rows'] = [row for row in extras['rows'] if row['query_id'] not in primary]
    extra_truth = {q: diagnostic_truth[q] for q in extras['predictor']['queries']}
    report['descriptive_reports'] = {}
    for name, batch, labels in (('primary', scored, truth), ('all_three_queries', diagnostic_scored, diagnostic_truth),
                                ('two_extra_queries_only', extras, extra_truth)):
        report['descriptive_reports'][name] = {
            'baseline_0_75': s.summarize(batch, labels, s.BASELINE),
            'candidate': s.summarize(batch, labels, selection['threshold']) if selection['status'] == 'candidate' else None}
    report['formal_population_or_safety_ci'] = None
    report['ci_unavailable_reason'] = 'Independent sampling is not established; C selected-point intervals are not independent validation.'
    report['diagnostic_note'] = 'All-three includes primary; two-extra excludes primary. Neither diagnostic table participates in selection or adds a gate.'
    return report


def choose(protocol_pin, manifest_pin, terminal_pin, output_lock, output_selection):
    """A failed/incomplete C writes BLOCKED, never a candidate or T release."""
    try:
        scored, truth, tp, evidence, _, diagnostic_scored, diagnostic_truth = _sealed_calibration(protocol_pin, manifest_pin, terminal_pin)
        selection = s.select_candidate(scored, truth)
    except Exception as exc:
        blocked = {'status': 'BLOCKED_INCONCLUSIVE', 'threshold': None, 'stop_before_T': True,
                   'reason': {'type': type(exc).__name__, 'message': str(exc)}, 'warning': s.WARNING}
        h.write_json(output_selection, blocked)
        return blocked
    report = _report(selection, scored, truth, diagnostic_scored, diagnostic_truth)
    h.write_json(output_selection, report)
    if selection['status'] != 'candidate':
        return selection
    evidence['calibration_report'] = h.pinned(output_selection)
    receipt = s.write_candidate_lock(output_lock, scored, truth, tp,
                                    protocol_sha256=protocol_pin['sha256'], evidence_pins=evidence,
                                    holdout_diagnostic_predictor=_read(evidence['holdout_diagnostic_predictor']))
    return {'status': 'candidate_locked', 'threshold': selection['threshold'],
            'lock_pin': h.pinned(receipt['path']), 'selection_pin': h.pinned(output_selection), 'warning': s.WARNING}


def _locked_holdout_manifest(protocol_pin, lock_pin):
    """Validate the chosen C again, then instantiate the exact planned T only."""
    runner.validate_pin_shape(lock_pin)
    h.verify_pin(lock_pin)
    lock = s.read_candidate_lock(lock_pin['path'], lock_pin['sha256'])
    evidence = lock['evidence_pins']
    if evidence['protocol'] != protocol_pin:
        raise ValueError('lock belongs to a different frozen protocol')
    scored, truth, tp, current, plan, diagnostic_scored, diagnostic_truth = _sealed_calibration(
        protocol_pin, evidence['calibration_manifest'], evidence['calibration_terminal'])
    selection = s.select_candidate(scored, truth)
    report = _report(selection, scored, truth, diagnostic_scored, diagnostic_truth)
    if _read(evidence['calibration_report']) != report:
        raise ValueError('C descriptive report differs from sealed outputs')
    current['calibration_report'] = evidence['calibration_report']
    if (current != evidence or s.digest(scored) != lock['calibration_scores_sha256'] or
            s.digest(truth) != lock['calibration_truth_sha256'] or s.digest(tp) != lock['holdout_predictor_sha256'] or
            s.digest(selection) != lock['calibration_selection_sha256'] or selection['status'] != 'candidate' or
            selection['threshold'] != lock['threshold']):
        raise ValueError('candidate lock differs from sealed C evidence')
    manifest = copy.deepcopy(plan)
    manifest['protocol_sha256'] = protocol_pin['sha256']
    manifest['release_pin'] = lock_pin
    runner.validate_manifest(manifest)
    # All T waveforms are already frozen; verify bytes, never choose replacements.
    for sample in manifest['samples']:
        h.verify_pin(sample['wav'])
    return manifest, lock


def release_holdout(protocol_pin, lock_pin, output_manifest):
    manifest, _ = _locked_holdout_manifest(protocol_pin, lock_pin)
    h.write_json(output_manifest, manifest)
    return h.pinned(output_manifest)


def evaluate_holdout(protocol_pin, lock_pin, manifest_pin, terminal_pin, output_predictions, output_report):
    """Seal all T predictions before first reading sealed T truth; no selection."""
    expected, lock = _locked_holdout_manifest(protocol_pin, lock_pin)
    values, artifacts, terminal = _sealed_run(protocol_pin, manifest_pin, terminal_pin, expected)
    protocol = _read(protocol_pin)
    c_terminal = _read(lock['evidence_pins']['calibration_terminal'])
    budget = protocol['resource_limits']
    for field, maximum in (('elapsed_seconds', 'model_wall_seconds_total'),
                           ('aggregate_cpu_seconds', 'model_cpu_seconds_total'),
                           ('allocated_output_bytes', 'model_output_bytes_total')):
        if c_terminal[field] + terminal[field] > budget[maximum]:
            raise ValueError('combined C+T resource budget exceeded')
    if c_terminal['planned_calls'] + terminal['planned_calls'] != protocol['counts']['max_encoder_calls_total']:
        raise ValueError('combined C+T model-call count mismatch')
    primary = _read(protocol['scoring_inputs']['T']['predictor'])
    full = _read(protocol['diagnostic_inputs']['T']['predictor'])
    kwargs = {'candidate_lock': lock_pin['path'], 'lock_sha256': lock_pin['sha256']}
    needed = {g['sample_id'] for g in primary['gallery']} | set(primary['queries'])
    scored = s.score_population(primary, {q: values[q] for q in needed}, **kwargs)
    diagnostic = s.score_population(full, values, **kwargs)
    predictions = {'schema': 1, 'status': 'predictions_sealed_before_truth', 'protocol': protocol_pin,
                   'candidate_lock': lock_pin, 'T_manifest': manifest_pin, 'T_terminal': terminal_pin,
                   'T_artifacts': artifacts, 'primary': scored, 'all_three_queries': diagnostic,
                   'fixed_decisions': {name: {
                       str(t): [s.decide(row, [g['person_id'] for g in batch['predictor']['gallery']], t)
                                for row in batch['rows']]
                       for t in sorted({lock['threshold'], s.BASELINE})}
                       for name, batch in (('primary', scored), ('all_three_queries', diagnostic))}}
    h.write_json(output_predictions, predictions)  # Exclusive, before any T truth read.
    prediction_pin = h.pinned(output_predictions)
    truth = _read(protocol['scoring_inputs']['T']['truth'])
    all_truth = _read(protocol['diagnostic_inputs']['T']['truth'])
    roles = _read(protocol['source_roles'])['records']
    expected_truth = {r['id']: {'kind': r['roster_role'], 'person_id': r['person_id'],
                              'speaker_id': r['speaker_id'], 'session_id': r['session_id']}
                      for r in roles if r['split'] == 'T' and r['role'] != 'reference'}
    if all_truth != expected_truth or truth != {q: expected_truth[q] for q in primary['queries']}:
        raise ValueError('sealed T truth disagrees with frozen source-role audit')
    report = _report({'status': 'candidate', 'threshold': lock['threshold']}, scored, truth, diagnostic, all_truth)
    report.update(status='holdout_descriptive_complete', prediction_seal=prediction_pin,
                  protocol=protocol_pin, candidate_lock=lock_pin,
                  ci_unavailable_reason='Source sampling does not establish iid or general population coverage; this pilot provides no safety certification.')
    policy = protocol['interval_policy']
    if (policy['formal_population_or_safety_CI'] is not None or policy['C_selected_point_CI'] is not None or
            policy['reference_only'] != 'conditional_iid_binomial_model_not_validated_sampling_guarantee' or
            policy['confidence'] != .95):
        raise ValueError('undeclared interval policy')
    assumption = ('Hypothetical iid Bernoulli primary-query outcomes from independently sampled participants, '
                  'conditional on this fixed gallery and source domain; source evidence does not establish this assumption.')
    model = {'label': 'conditional_iid_model', 'purpose': 'Reference intervals under an assumed model only, not a population-coverage guarantee or promotion criterion.',
             'independence_assumption': assumption, 'working_points': {}}
    for name, summary in report['descriptive_reports']['primary'].items():
        c = summary['counts']
        if (c['known'], c['unknown']) != (policy['primary_n_known'], policy['primary_n_unknown']):
            raise ValueError('primary interval denominators differ from frozen policy')
        model['working_points'][name] = {}
        for outcome, total in (('known_wrong', 'known'), ('unknown_accepted', 'unknown'), ('known_correct', 'known')):
            model['working_points'][name][outcome] = {
                sides: s.binomial_interval(c[outcome], c[total], confidence=policy['confidence'], sides=sides,
                                          independence_basis=assumption,
                                          denominator='One prespecified primary query per ' + total + ' participant against the complete fixed gallery; no repeated queries or pair counts')
                for sides in ('upper', 'two-sided')}
    for working_point in model['working_points'].values():
        for event, intervals in working_point.items():
            for interval in intervals.values():
                interval['event_count'] = interval.pop('errors')
                interval['event'] = event
                interval['event_definition'] = {
                    'known_wrong': 'Known primary query emitted as another gallery person',
                    'unknown_accepted': 'Unknown primary query emitted as any gallery person',
                    'known_correct': 'Known primary query emitted as its true gallery person'}[event]
    report['conditional_iid_model'] = model
    h.verify_pin(prediction_pin)
    h.write_json(output_report, report)
    return {'status': report['status'], 'prediction_pin': prediction_pin, 'report_pin': h.pinned(output_report)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('choose', 'release-t', 'evaluate-t'):
        command = sub.add_parser(name)
        for flag in ('protocol', 'protocol-sha'):
            command.add_argument('--' + flag, required=True)
        if name == 'choose':
            for flag in ('manifest', 'manifest-sha', 'terminal', 'terminal-sha', 'output-lock', 'output-selection'):
                command.add_argument('--' + flag, required=True)
        elif name == 'release-t':
            for flag in ('lock', 'lock-sha', 'output-manifest'):
                command.add_argument('--' + flag, required=True)
        else:
            for flag in ('lock', 'lock-sha', 'manifest', 'manifest-sha', 'terminal', 'terminal-sha', 'output-predictions', 'output-report'):
                command.add_argument('--' + flag, required=True)
    args = parser.parse_args()
    protocol = _pin(args.protocol, args.protocol_sha)
    if args.command == 'choose':
        result = choose(protocol, _pin(args.manifest, args.manifest_sha),
                        _pin(args.terminal, args.terminal_sha), args.output_lock, args.output_selection)
    elif args.command == 'release-t':
        result = release_holdout(protocol, _pin(args.lock, args.lock_sha), args.output_manifest)
    else:
        result = evaluate_holdout(protocol, _pin(args.lock, args.lock_sha),
                                  _pin(args.manifest, args.manifest_sha), _pin(args.terminal, args.terminal_sha),
                                  args.output_predictions, args.output_report)
    print(json.dumps(result, sort_keys=True))
    return 0 if args.command != 'choose' or result['status'] == 'candidate_locked' else 2


if __name__ == '__main__':
    raise SystemExit(main())
