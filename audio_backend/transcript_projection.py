"""Bounded, model-free projection of validated transcript evidence.

Support coordinates alone choose the reading. Text and model phrase timestamps
never participate in selection, splitting, deduplication, or speaker matching.
The only removed duplication is overlapping *source coverage*: intrinsic model
repetitions and simultaneous/equal-text labels remain intact.
"""
from bisect import bisect_right
from copy import deepcopy
import unicodedata

from .transcript_contract import (
    BLOCK_FRAMES, MAX_BINDINGS, MAX_PRIMARY, MAX_RESULTS, POLICY_HASH, RATE, fail,
    LEXICAL_POLICY_HASH, lexical_reference, lexical_fallback, primary_frame,
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
    if not all(not _intersects(start, end, item)
               or (start <= item['start_frame'] and item['end_frame'] <= end)
               for item in primary):
        return False
    # Granular atoms keep their original parent envelope. A support in an
    # estimated internal gap is not lexical evidence and cannot be inserted.
    parents = {}
    for item in primary:
        if item.get('source') == 'primary_lexical':
            parents.setdefault(item['segment_id'], []).append(item)
    for groups in parents.values():
        parent = groups[0]
        if start < parent['parent_end_frame'] and parent['parent_start_frame'] < end:
            touched = [group for group in groups if _intersects(start, end, group)]
            if not touched: return False
            touched.sort(key=lambda group: group['unit_range'][0])
            if any(a['unit_range'][1] != b['unit_range'][0] for a, b in zip(touched, touched[1:])):
                return False
    return True


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


def _classification(value):
    value = value.strip()  # Classification only; emitted substrings are exact.
    if not value or all(unicodedata.category(char).startswith('P') for char in value):
        return 'decoration'
    if any('A' <= char <= 'Z' or 'a' <= char <= 'z' or '0' <= char <= '9' for char in value):
        return 'lexical'
    if len(value) == 1 and (0x3400 <= ord(value) <= 0x4dbf or
            0x4e00 <= ord(value) <= 0x9fff or 0x20000 <= ord(value) <= 0x3134f):
        return 'lexical'
    return None


def lexical_groups(parent, current):
    """Pure ordinal grouping of an already current, validated inventory.

    None leaves the complete parent opaque. No sorting, retokenization,
    normalization, inferred anchor, or timing clamp repairs unsuitable inputs.
    """
    inventory = current['inventory']; units = inventory['units']
    if not units or any(not unit['granular_eligible'] for unit in units): return None
    tracks = {( 'source_track_id' in unit, unit.get('source_track_id')) for unit in units}
    if len(tracks) != 1: return None
    if any(a['ownership_anchor'] > b['ownership_anchor'] for a,b in zip(units,units[1:])):
        return None
    lexical = []
    for index, unit in enumerate(units):
        raw, display = _classification(unit['raw_text']), _classification(unit['text'])
        if raw is None or raw != display: return None
        if raw == 'lexical':
            if unit['end'] <= unit['start']: return None
            lexical.append(index)
    if not lexical: return None
    for text_key, span_key in (('text','display_span'),('raw_text','raw_span')):
        expected = parent[text_key]
        cursor = 0; pieces = []
        for unit in units:
            a,b = unit[span_key]
            if a != cursor or not 0 <= a <= b <= len(expected): return None
            pieces.append(expected[a:b]); cursor = b
        if cursor != len(expected) or ''.join(pieces).encode('utf-8') != expected.encode('utf-8'):
            return None
    ranges = []
    for position, index in enumerate(lexical):
        first = 0 if position == 0 else index
        end = lexical[position+1] if position+1 < len(lexical) else len(units)
        anchor = units[index]['ownership_anchor']
        if ranges and ranges[-1][2] == anchor:
            ranges[-1] = (ranges[-1][0], end, anchor)
        else: ranges.append((first,end,anchor))
    original = parent['canonical_asr']; coordinates = inventory['coordinates']
    own = next(part for part in inventory['source_parts'] if part['id'] == original['chunk_id'])
    def absolute(value):
        return own['timeline_start'] + original['speaker_window_start'] + (value - coordinates['core_start'])
    groups = []
    for first,end,anchor in ranges:
        chosen = units[first:end]
        a = primary_frame(min(absolute(unit['start']) for unit in chosen))
        b = primary_frame(max(absolute(unit['end']) for unit in chosen),end=True)
        if b <= a: return None
        display_span = [chosen[0]['display_span'][0],chosen[-1]['display_span'][1]]
        raw_span = [chosen[0]['raw_span'][0],chosen[-1]['raw_span'][1]]
        groups.append({'source_ref':lexical_reference(original['id'],current['inventory_sha256'],first,end),
            'source':'primary_lexical', 'parent_source_ref':parent['source_ref'],
            'segment_id':original['id'], 'inventory_sha256':current['inventory_sha256'],
            'policy_sha256':LEXICAL_POLICY_HASH, 'unit_range':[first,end],
            'display_span':display_span, 'raw_span':raw_span,
            'text':parent['text'][slice(*display_span)], 'raw_text':parent['raw_text'][slice(*raw_span)],
            'start_frame':a, 'end_frame':b, 'home_block':a//BLOCK_FRAMES,
            'parent_start_frame':parent['start_frame'], 'parent_end_frame':parent['end_frame'],
            'timestamp_kind':parent['timestamp_kind'], 'speaker_scope':deepcopy(parent['speaker_scope']),
            'word_times':None, 'complete_content':False, 'acoustic_coverage':'unproven',
            'person_identity':'not_projected', 'timestamp_semantics':inventory['timestamp_semantics']})
    return groups


def _span(groups):
    """Merge consecutive whole groups without copying the full parent/inventory."""
    first, last = groups[0], groups[-1]
    value = deepcopy(first)
    value['unit_range'] = [first['unit_range'][0],last['unit_range'][1]]
    for text_key, span_key in (('text','display_span'),('raw_text','raw_span')):
        value[text_key] = ''.join(group[text_key] for group in groups)
        value[span_key] = [first[span_key][0],last[span_key][1]]
    value['start_frame'] = min(group['start_frame'] for group in groups)
    value['end_frame'] = max(group['end_frame'] for group in groups)
    value['home_block'] = value['start_frame']//BLOCK_FRAMES
    value['source_ref'] = lexical_reference(value['segment_id'],value['inventory_sha256'],*value['unit_range'])
    return value


def _consecutive_spans(groups):
    runs = []
    for group in sorted(groups,key=lambda item:item['unit_range'][0]):
        if runs and runs[-1][-1]['unit_range'][1] == group['unit_range'][0]:
            runs[-1].append(group)
        else:runs.append([group])
    return [_span(run) for run in runs]


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


def project(block: int, primary: list[dict], candidates: list[dict], notices=(),
            *, lexical=None, policy_hash=POLICY_HASH) -> dict:
    """Return one fixed 60-second block without reading or mutating a source.

    Inputs are complete bounded components from one validated snapshot. The
    unchanged DAG chooses global runs before any page filtering. Text remains
    whole at its existing source home, with references on other visible pages.
    """
    if policy_hash not in (POLICY_HASH, LEXICAL_POLICY_HASH):
        fail('invalid_transcript_projection', status=409)
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
    granular = policy_hash == LEXICAL_POLICY_HASH
    lexical = (lexical or {}) if granular else {}
    atoms = ([atom for parent in primary for atom in
             (lexical.get(parent['canonical_asr']['id']) or [parent])] if granular else primary)
    runs = _choose_runs(eligible, atoms)
    selected_keys = {eligible[index]['candidate_key']
                     for _, _, path in runs for index in path}
    selected_runs = []
    alternatives = []
    reading_groups = []
    replaced_refs = set()
    replaced_atoms = set()
    bindings = 0

    for run_start, run_end, path in runs:
        chosen = [eligible[index] for index in path]
        removed = [item for item in atoms if _intersects(run_start, run_end, item)]
        replaced_atoms.update(item['source_ref'] for item in removed)
        replaced = [item for item in primary if _intersects(run_start, run_end, item)]
        spans = [span for parent in replaced for span in _consecutive_spans([
            item for item in removed if item.get('parent_source_ref') == parent['source_ref']])]
        if granular:
            bindings += len(spans) + len(replaced)
        keys = [candidate['candidate_key'] for candidate in chosen]
        refs = [item['source_ref'] for item in replaced]
        replaced_refs.update(refs)
        if not (run_start < end and start < run_end): continue
        selected_runs.append({'start_frame': run_start, 'end_frame': run_end,
                              'candidate_keys': keys, 'primary_source_refs': refs,
                              'supports': _supports(chosen),
                              **({'replaced_span_refs':[item['source_ref'] for item in spans]} if granular else {})})
        reading_groups.append((run_start, run_end, 1, keys[0], _candidate_reading(chosen, block)))
        if replaced:
            if not granular:bindings += len(replaced)
            alternatives.append({
                'alternative_id': f'replaced:{run_start}:{run_end}',
                'reason': 'replaced_primary', 'status': 'alternative',
                'start_frame': run_start, 'end_frame': run_end,
                'candidate_keys': keys, 'selected_candidate_keys': keys,
                'supports': _supports(chosen),
                'candidate_reading': _candidate_reading(chosen, block),
                'primary_reading': [_evidence(item, block) for item in replaced],
                **({'replaced_spans':[_evidence(item,block) for item in spans]} if granular else {}),
            })

    for item in primary:
        groups = lexical.get(item['canonical_asr']['id']) if granular else None
        kept = (_consecutive_spans([group for group in groups if group['source_ref'] not in replaced_atoms])
                if groups else [item] if item['source_ref'] not in replaced_refs else [])
        for retained in kept:
            if _intersects(start,end,retained):
                # Numeric unit ordinals, not reference strings, break span ties.
                order = ((retained['segment_id'],retained['unit_range'][0]) if groups else
                         (item['canonical_asr']['id'],0)) if granular else item['source_ref']
                reading_groups.append((retained['start_frame'],retained['end_frame'],0,
                                       order,[_evidence(retained,block)]))

    for candidate in candidates:
        if candidate['candidate_key'] in selected_keys:continue
        visible = _intersects(start,end,candidate)
        if not granular and not visible:continue
        candidate_start, candidate_end = candidate['start_frame'], candidate['end_frame']
        overlaps = [item for item in primary if _intersects(candidate_start, candidate_end, item)]
        if granular:
            bindings += len(overlaps)
            if not visible:continue
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
        if not granular:bindings += len(overlaps)
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

    if bindings > MAX_BINDINGS and not granular:
        fail('transcript_projection_binding_capacity')
    reading_groups.sort(key=lambda group: group[:4])
    alternatives.sort(key=lambda group: (group['start_frame'], group['end_frame'], group['alternative_id']))
    value = {
        'block': block, 'start_frame': start, 'end_frame': end,
        'sample_rate': RATE, 'policy_hash': policy_hash,
        'baseline_reading': [_evidence(item, block) for item in primary if _intersects(start, end, item)],
        'reading': [item for group in reading_groups for item in group[4]],
        'selected_runs': selected_runs, 'alternatives': alternatives,
        'notices': deepcopy(list(notices)),
    }
    if granular:
        value['lexical_projection'] = {'complete':True,'selection_performed':True,
            'alternatives_complete':True,'acoustic_coverage':'unproven'}
        if bindings > MAX_BINDINGS:lexical_fallback(value,'bindings')
    return value
