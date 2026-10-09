"""Bounded frozen-reference diagnostics, distinct from exact PCM invariants.

No character-time interpolation, speaker identity transfer, or laughter taxonomy
extension. Failed/missing candidate speakers retain their whole denominator.
"""
from itertools import permutations
import re
import unicodedata

from .fragment_runtime_contracts import RATE


def chars(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def words(text):
    return [chars(x) for x in re.findall(r"[^\W_]+(?:['’][^\W_]+)*", unicodedata.normalize('NFKC', text)) if chars(x)]


def align(reference, hypothesis):
    """Levenshtein alignment with bounded byte backpointers and two cost rows."""
    n, m = len(reference), len(hypothesis)
    if n > 4096 or m > 4096:
        raise ValueError('Scoring alignment exceeds 4096-unit bound')
    trace = bytearray((n+1)*(m+1))
    previous = list(range(m+1))
    for j in range(1, m+1):
        trace[j] = 2
    for i in range(1, n+1):
        current = [i] + [0]*m
        trace[i*(m+1)] = 1
        for j in range(1, m+1):
            candidates = (previous[j-1]+(reference[i-1] != hypothesis[j-1]), previous[j]+1, current[j-1]+1)
            choice = min(range(3), key=candidates.__getitem__)
            current[j] = candidates[choice]
            trace[i*(m+1)+j] = choice
        previous = current
    i, j, operations = n, m, []
    while i or j:
        move = trace[i*(m+1)+j]
        if move == 0:
            i -= 1; j -= 1
            op = 'equal' if reference[i] == hypothesis[j] else 'substitute'
            operations.append({'op': op, 'reference_index': i, 'hypothesis_index': j})
        elif move == 1:
            i -= 1
            operations.append({'op': 'delete', 'reference_index': i, 'hypothesis_index': None})
        else:
            j -= 1
            operations.append({'op': 'insert', 'reference_index': None, 'hypothesis_index': j})
    operations.reverse()
    counts = {kind: sum(x['op'] == kind for x in operations) for kind in ('equal', 'substitute', 'delete', 'insert')}
    return {'reference_units': n, 'hypothesis_units': m, 'edit_errors': previous[m],
            'error_rate': previous[m]/n if n else None, 'counts': counts, 'operations': operations}


def union(intervals):
    result = []
    for a, b in sorted(intervals):
        if a >= b:
            continue
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def duration(intervals):
    return sum(b-a for a, b in union(intervals))


def intersection(left, right):
    return duration((max(a, c), min(b, d)) for a, b in union(left) for c, d in union(right))


def activity(turns, speaker_key, bounds_key=None):
    result = {}
    for turn in turns:
        bounds = turn[bounds_key] if bounds_key else [turn['start_frame'], turn['end_frame']]
        result.setdefault(turn[speaker_key], []).append(bounds)
    return {sid: union(intervals) for sid, intervals in result.items()}


def assignments(references, hypotheses):
    # Missing hypotheses are distinct empty dummy columns. Real labels remain
    # support/epoch-local; assignment is a disclosed scoring optimization.
    labels = list(hypotheses)
    choices = labels+[None]*max(0, len(references)-len(labels))
    if len(choices) > 8 or len(references) > 8:
        raise ValueError('Anonymous assignment exceeds capacity 8')
    return permutations(choices, len(references))


def speaker_score(reference_turns, hypothesis_turns):
    gold = activity(reference_turns, 'speaker', 'logical_frames')
    predicted = activity(hypothesis_turns, 'speaker')
    references = sorted(gold)
    best = max(assignments(references, predicted), key=lambda assigned: sum(
        intersection(gold[r], predicted.get(h, [])) for r, h in zip(references, assigned)), default=())
    mapping = dict(zip(references, best))
    inverse = {h: r for r, h in mapping.items() if h is not None}
    points = sorted({x for collection in (gold, predicted) for intervals in collection.values() for interval in intervals for x in interval})
    miss = false_alarm = confusion = overlap_ref = overlap_hyp = overlap_both = 0
    peak = 0
    for a, b in zip(points, points[1:]):
        ref = {r for r, intervals in gold.items() if any(x <= a < y for x, y in intervals)}
        hyp = {h for h, intervals in predicted.items() if any(x <= a < y for x, y in intervals)}
        correct = sum(inverse.get(h) in ref for h in hyp)
        frames = b-a
        miss += max(0, len(ref)-len(hyp))*frames
        false_alarm += max(0, len(hyp)-len(ref))*frames
        confusion += (min(len(ref), len(hyp))-correct)*frames
        overlap_ref += (len(ref) >= 2)*frames
        overlap_hyp += (len(hyp) >= 2)*frames
        overlap_both += (len(ref) >= 2 and len(hyp) >= 2)*frames
        peak = max(peak, len(hyp))
    total = sum(duration(x) for x in gold.values())
    per_speaker = []
    for ref, hyp in mapping.items():
        predicted_intervals = predicted.get(hyp, [])
        hit = intersection(gold[ref], predicted_intervals)
        combined = duration(gold[ref])+duration(predicted_intervals)-hit
        per_speaker.append({'reference_speaker': ref, 'hypothesis_local_label': hyp,
            'reference_frames': duration(gold[ref]), 'hypothesis_frames': duration(predicted_intervals),
            'intersection_frames': hit, 'iou': hit/combined if combined else None})
    return {'assignment_basis': 'one optimal anonymous interval-overlap assignment per fixture',
            'assignment': mapping, 'per_reference_speaker': per_speaker,
            'reference_speaker_frames': total, 'miss_frames': miss, 'false_alarm_frames': false_alarm,
            'confusion_frames': confusion, 'der_zero_collar_overlap_aware': (miss+false_alarm+confusion)/total if total else None,
            'native_detected_speaker_count': len(predicted), 'native_peak_simultaneous': peak,
            'reference_overlap_frames': overlap_ref, 'hypothesis_overlap_frames': overlap_hyp,
            'overlap_recall': overlap_both/overlap_ref if overlap_ref else None,
            'overlap_precision': overlap_both/overlap_hyp if overlap_hyp else None}


def english_score(fixture, segments):
    gold = fixture['reference']['words']
    end = fixture['logical_frames'][1]
    reference, refs = [], []
    for word in gold:
        if 0 <= word['logical_frames'][0] < end:
            for token in words(word['text']):
                reference.append(token); refs.append(word)
    hypothesis = words(' '.join(s['text'] for s in segments))
    historical = align(reference, hypothesis)
    complete_gold = [w for w in gold if 0 <= w['logical_frames'][0] < w['logical_frames'][1] <= end]
    lo = min(w['logical_frames'][0] for w in complete_gold)
    hi = max(w['logical_frames'][1] for w in complete_gold)
    selected_hyp = [i for i, s in enumerate(segments) if lo/RATE <= s['start'] < s['end'] <= hi/RATE]
    strict = align(words(' '.join(w['text'] for w in complete_gold)),
                   words(' '.join(segments[i]['text'] for i in selected_hyp)))
    seams = []
    for a, b in fixture['reference']['seam_metric_neighborhoods_logical_frames']:
        selected = {i for i, w in enumerate(refs) if a <= sum(w['logical_frames'])/2 < b}
        operations = []
        ambiguous = []
        for oi, op in enumerate(historical['operations']):
            ri = op['reference_index']
            if ri in selected:
                operations.append(op)
            elif ri is None:
                left = next((x['reference_index'] for x in reversed(historical['operations'][:oi]) if x['reference_index'] is not None), None)
                right = next((x['reference_index'] for x in historical['operations'][oi+1:] if x['reference_index'] is not None), None)
                if left in selected and right in selected:
                    operations.append(op)
                elif left in selected or right in selected:
                    ambiguous.append(op)
        errors = sum(x['op'] != 'equal' for x in operations)
        seams.append({'neighborhood_frames': [a, b], 'reference_word_ids': [refs[i]['source_id'] for i in sorted(selected)],
                      'operations': operations, 'boundary_ambiguous_insertions': ambiguous,
                      'attributable_edit_errors': errors, 'reference_units': len(selected),
                      'wer': errors/len(selected) if selected and not ambiguous else None,
                      'timing_selection': 'native gold midpoint, whole-excerpt edit alignment'})
    target = fixture['reference']['critical_cross_seam_word']['source_id']
    critical = [op for op in historical['operations'] if op['reference_index'] is not None and refs[op['reference_index']]['source_id'] == target]
    return {'normalization': 'NFKC/casefold/alphanumeric; Latin apostrophe words kept together before punctuation removal',
            'historical_start_anchored_wer': historical,
            'historical_reference_word_ids': [w['source_id'] for w in refs],
            'historical_cer': align(chars(' '.join(w['text'] for w in gold if 0 <= w['logical_frames'][0] < end)), chars(' '.join(s['text'] for s in segments))),
            'complete_word_wer': strict, 'excluded_edge_reference_ids': fixture['reference']['edge_partial_word_ids'],
            'excluded_hypothesis_segment_indices': [i for i in range(len(segments)) if i not in selected_hyp],
            'complete_hypothesis_selection': 'full native support contained in complete-gold-word exterior bounds',
            'seam_diagnostics': seams, 'critical_word_id': target, 'critical_word_operations': critical,
            'partial_edge_warning': 'whole-view transcript accuracy is not complete-utterance accuracy'}


def candidate_score(reference_turns, tracks, repeat_cases=()):
    gold, hypotheses, offsets = {}, {}, {}
    for turn in reference_turns:
        sid = turn['speaker']; text = chars(turn['text'])
        offset = len(gold.get(sid, ''))
        offsets[turn['gold_array_index']] = (sid, offset, text)
        gold[sid] = gold.get(sid, '')+text
    for track in tracks:
        label = (track['scope_id'], track['provenance']['model_local_speaker_label'])
        hypotheses[label] = hypotheses.get(label, '')+chars(track['text'])
    references = sorted(gold)
    matrix = {(r, h): align(gold[r], text) for r in references for h, text in hypotheses.items()}
    missing = {r: align(gold[r], '') for r in references}
    def loss(assigned):
        return sum((matrix[(r, h)] if h is not None else missing[r])['edit_errors'] for r, h in zip(references, assigned)) + sum(len(text) for h, text in hypotheses.items() if h not in assigned)
    best = min(assignments(references, hypotheses), key=loss, default=tuple(None for _ in references))
    paired = dict(zip(references, best))
    alignments = {r: matrix[(r, h)] if h is not None else missing[r] for r, h in paired.items()}
    repeat = []
    for case in repeat_cases:
        sid, offset, text = offsets[case['gold_array_index']]
        needle = chars(case['substring'])
        local = text.find(needle)
        if local < 0:
            raise ValueError('Frozen repeat substring missing from its full reference turn')
        selected = set(range(offset+local, offset+local+len(needle)))
        operations = [op for op in alignments[sid]['operations'] if op['reference_index'] in selected]
        repeat.append({'gold_array_index': case['gold_array_index'], 'substring': case['substring'],
            'reference_character_indices_within_speaker': sorted(selected), 'operations': operations,
            'all_repeat_characters_equal': len(operations) == len(needle) and all(op['op'] == 'equal' for op in operations),
            'character_timing': None})
    denominator = sum(map(len, gold.values()))
    assigned_labels = {x for x in best if x is not None}
    return {'assignment_basis': 'one minimum-edit anonymous label assignment over complete reference-speaker turns',
            'reference_characters': denominator, 'edit_errors': loss(best), 'cer': loss(best)/denominator if denominator else None,
            'assignments': [{'reference_speaker': r, 'hypothesis_scope_and_label': list(paired[r]) if paired[r] is not None else None,
                             'alignment': alignments[r]} for r in references],
            'unmatched_hypothesis_insertions': sum(len(text) for h, text in hypotheses.items() if h not in assigned_labels),
            'missing_reference_speakers': [r for r, h in paired.items() if h is None],
            'emitted_label_count': len(hypotheses), 'candidate_failure_or_not_attempted': not bool(tracks),
            'repeat_substring_diagnostics': repeat, 'per_turn_timing_attribution': 'unavailable; native candidate phrase times unverified',
            'all_reference_characters_retained_even_on_failure': True}


def score_base(fixture, commits):
    segments = [s for p in commits for s in p['segments']]
    if fixture['id'].startswith('A_'):
        return english_score(fixture, segments)
    if fixture['id'].startswith('B_'):
        turns = [t for p in commits for t in p['turns']]
        reference = fixture['reference']['turns']
        return {'speakers': speaker_score(reference, turns),
                'mixture_serialized_cer': align(chars(' '.join(t['text'] for t in reference)), chars(' '.join(s['text'] for s in segments))),
                'mixture_cer_caveat': 'serialized diagnostic, not source-separated recognition of both speakers'}
    return {'laughter_iou': None, 'laughter_missed_duration': None, 'laughter_extra_duration': None,
            'reason': 'unsupported_label', 'gold_interval_frames': fixture['reference']['event']['logical_frames'],
            'raw_laughter_top_labels': 'diagnostic only; inspect full event_candidates; absence is not a negative prediction'}
