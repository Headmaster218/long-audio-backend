"""Bounded, model-free projection of validated transcript evidence.

Support coordinates alone choose the reading. Text and model phrase timestamps
never participate in selection, splitting, deduplication, or speaker matching.
The only removed duplication is overlapping *source coverage*: intrinsic model
repetitions and simultaneous/equal-text labels remain intact.
"""
from bisect import bisect_right
from copy import deepcopy

from .transcript_contract import (
    BLOCK_FRAMES, MAX_BINDINGS, MAX_PRIMARY, MAX_RESULTS, POLICY_HASH, RATE, fail,
)


def _intersects(start, end, item):
    """All source support is half-open, including at a block boundary."""
    return start < item['end_frame'] and item['start_frame'] < end


def _candidate_order(candidate):
    return (
        0 if candidate['kind'] in ('core_seam', 'reset_seam') else 1,
        candidate['start_frame'], candidate['archive_seq'],
        candidate['result_sha256'], candidate['candidate_key'],
    )


def _path_key(path, candidates):
    return len(path), tuple(_candidate_order(candidates[index]) for index in path)


def _legal(start, end, primary):
    # The envelope of an exact-adjacent path is its union. Only its outer
    # boundaries matter; an internal job join may bisect a whole primary unit.
    return all(
        not _intersects(start, end, item)
        or (start <= item['start_frame'] and item['end_frame'] <= end)
        for item in primary
    )


def _legal_runs(candidates, primary):
    """Best exact-adjacent path for each endpoint pair, never subset search.

    At most 2 * MAX_RESULTS endpoints form a positive-length interval DAG.
    For a fixed pair, every path has identical support and whole-unit legality,
    so retaining only the fewest-jobs/stable-order path loses no optimum.
    """
    endpoints = sorted({value for candidate in candidates
                        for value in (candidate['start_frame'], candidate['end_frame'])})
    outgoing = {point: [] for point in endpoints}
    for index, candidate in enumerate(candidates):
        outgoing[candidate['start_frame']].append(index)
    runs = []
    for start in endpoints:
        best = {start: ()}
        for point in endpoints:
            if point not in best:
                continue
            for index in outgoing[point]:
                end = candidates[index]['end_frame']
                path = best[point] + (index,)
                incumbent = best.get(end)
                if incumbent is None or _path_key(path, candidates) < _path_key(incumbent, candidates):
                    best[end] = path
        for end, path in best.items():
            if path and _legal(start, end, primary):
                runs.append((start, end, path))
    return sorted(runs, key=lambda run: (run[1], run[0], _path_key(run[2], candidates)))


def _schedule_key(state, candidates):
    frames, path, runs = state
    # Runs are already chronological. The last key only canonicalizes an
    # otherwise identical path partition, without changing selected evidence.
    return -frames, len(path), tuple(_candidate_order(candidates[i]) for i in path), len(runs)


def _choose_runs(candidates, primary):
    """Weighted interval scheduling over legal runs; adjacent runs may join."""
    runs = _legal_runs(candidates, primary)
    ends = [run[1] for run in runs]
    best = [(0, (), ())]
    for position, run in enumerate(runs):
        start, end, path = run
        previous = bisect_right(ends, start, 0, position)
        frames, chosen_path, chosen_runs = best[previous]
        include = (frames + end - start, chosen_path + path, chosen_runs + (run,))
        exclude = best[-1]
        best.append(min((include, exclude), key=lambda state: _schedule_key(state, candidates)))
    merged = []
    for start, end, path in best[-1][2]:
        if merged and merged[-1][1] == start:
            old_start, _, old_path = merged[-1]
            merged[-1] = (old_start, end, old_path + path)
        else:
            merged.append((start, end, path))
    return merged


def _evidence(item, block, candidate_key=None):
    if item['home_block'] == block:
        result = deepcopy(item)
        result['reference_only'] = False
    else:
        result = {key: deepcopy(item[key]) for key in ('source_ref', 'source', 'home_block')}
        result['reference_only'] = True
    if candidate_key is not None:
        result['candidate_key'] = candidate_key
    return result


def _candidate_reading(candidates, block):
    # Preserve archive phrase order, labels, repeated text and original model
    # timestamps. A candidate's phrases all share the support's home block.
    return [_evidence(phrase, block, candidate['candidate_key'])
            for candidate in candidates for phrase in candidate['phrases']]


def _supports(candidates):
    supports = []
    for candidate in candidates:
        support = {key: deepcopy(candidate[key]) for key in (
            'candidate_key', 'job_id', 'result_sha256', 'archive_seq', 'kind',
            'start_frame', 'end_frame', 'home_block', 'complete_content', 'eos',
        )}
        support.update(eligible=candidate.get('eligible', True),
                       ineligible_reason=candidate.get('ineligible_reason'))
        for key in ('result_ref', 'authority', 'source_current',
                    'inline_timing_projection', 'omitted_lexical_content'):
            if key in candidate:
                support[key] = deepcopy(candidate[key])
        supports.append(support)
    return supports


def _validate_intervals(items):
    for item in items:
        start, end = item['start_frame'], item['end_frame']
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            fail('transcript_projection_invalid_interval')


def project(block: int, primary: list[dict], candidates: list[dict], notices=()) -> dict:
    """Return one fixed 60-second block without reading or mutating a source.

    Inputs are complete bounded components from one validated snapshot. The
    unchanged DAG chooses global runs before any page filtering. Text remains
    whole at its existing source home, with references on other visible pages.
    """
    if type(block) is not int or block < 0:
        fail('transcript_projection_invalid_block', status=400)
    if len(primary) > MAX_PRIMARY:
        fail('transcript_projection_primary_capacity')
    if len(candidates) > MAX_RESULTS:
        fail('transcript_projection_result_capacity')
    _validate_intervals(primary)
    _validate_intervals(candidates)
    start, end = block * BLOCK_FRAMES, (block + 1) * BLOCK_FRAMES
    primary = sorted(primary,
                     key=lambda item: (item['start_frame'], item['end_frame'], item['source_ref']))
    candidates = sorted(candidates,
                        key=lambda item: (item['start_frame'], item['end_frame'], _candidate_order(item)))
    eligible = [candidate for candidate in candidates
                if candidate.get('eligible', True) and candidate['phrases']]
    runs = _choose_runs(eligible, primary)
    selected_keys = {eligible[index]['candidate_key']
                     for _, _, path in runs for index in path}
    selected_runs = []
    alternatives = []
    reading_groups = []
    replaced_refs = set()
    bindings = 0

    for run_start, run_end, path in runs:
        chosen = [eligible[index] for index in path]
        replaced = [item for item in primary if _intersects(run_start, run_end, item)]
        keys = [candidate['candidate_key'] for candidate in chosen]
        refs = [item['source_ref'] for item in replaced]
        replaced_refs.update(refs)
        if not (run_start < end and start < run_end): continue
        selected_runs.append({'start_frame': run_start, 'end_frame': run_end,
                              'candidate_keys': keys, 'primary_source_refs': refs,
                              'supports': _supports(chosen)})
        reading_groups.append((run_start, run_end, 1, keys[0], _candidate_reading(chosen, block)))
        if replaced:
            bindings += len(replaced)
            alternatives.append({
                'alternative_id': f'replaced:{run_start}:{run_end}',
                'reason': 'replaced_primary', 'status': 'alternative',
                'start_frame': run_start, 'end_frame': run_end,
                'candidate_keys': keys, 'selected_candidate_keys': keys,
                'supports': _supports(chosen),
                'candidate_reading': _candidate_reading(chosen, block),
                'primary_reading': [_evidence(item, block) for item in replaced],
            })

    for item in primary:
        if _intersects(start, end, item) and item['source_ref'] not in replaced_refs:
            reading_groups.append((item['start_frame'], item['end_frame'], 0,
                                   item['source_ref'], [_evidence(item, block)]))

    for candidate in candidates:
        if not _intersects(start, end, candidate) or candidate['candidate_key'] in selected_keys:
            continue
        candidate_start, candidate_end = candidate['start_frame'], candidate['end_frame']
        overlaps = [item for item in primary if _intersects(candidate_start, candidate_end, item)]
        selected_overlap = [eligible[index]['candidate_key']
                            for run_start, run_end, path in runs
                            if candidate_start < run_end and run_start < candidate_end
                            for index in path
                            if _intersects(candidate_start, candidate_end, eligible[index])]
        boundary = False  # Complete closure, so crossing a display page is resolved.
        if not candidate.get('eligible', True) or not candidate['phrases']:
            reason = candidate.get('ineligible_reason') or (
                'empty_candidate_reading' if not candidate['phrases'] else 'ineligible_candidate')
            status = 'unresolved'
        elif selected_overlap:
            reason, status = 'unselected_overlap', 'alternative'
        else:
            reason, status = 'partial_primary_unresolved', 'unresolved'
        bindings += len(overlaps)
        alternatives.append({
            'alternative_id': f"candidate:{candidate['candidate_key']}",
            'reason': reason, 'status': status,
            'boundary_unresolved': boundary,
            'start_frame': candidate_start, 'end_frame': candidate_end,
            'candidate_keys': [candidate['candidate_key']],
            'selected_candidate_keys': selected_overlap,
            'supports': _supports([candidate]),
            'candidate_reading': _candidate_reading([candidate], block),
            'primary_reading': [_evidence(item, block) for item in overlaps],
        })

    if bindings > MAX_BINDINGS:
        fail('transcript_projection_binding_capacity')
    reading_groups.sort(key=lambda group: group[:4])
    alternatives.sort(key=lambda group: (group['start_frame'], group['end_frame'], group['alternative_id']))
    return {
        'block': block, 'start_frame': start, 'end_frame': end,
        'sample_rate': RATE, 'policy_hash': POLICY_HASH,
        'baseline_reading': [_evidence(item, block) for item in primary if _intersects(start, end, item)],
        'reading': [item for group in reading_groups for item in group[4]],
        'selected_runs': selected_runs, 'alternatives': alternatives,
        'notices': deepcopy(list(notices)),
    }
