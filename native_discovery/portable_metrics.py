"""Model-free integer-sample interval and cached-availability accounting helpers.

These helpers reproduce scoring arithmetic, not native inference, production
planning, the full cache-eligibility gate, or transcript-level oracle matching.
"""

def _frame(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected nonnegative integer sample index')
    return value


def union(intervals, total_frames):
    """Union validated half-open intervals on an exact integer sample grid."""
    if _frame(total_frames) == 0:
        raise ValueError('Input must be nonempty')
    checked = []
    for start, end in intervals:
        _frame(start)
        _frame(end)
        if not 0 <= start < end <= total_frames:
            raise ValueError('Invalid or out-of-input interval')
        checked.append((start, end))
    out = []
    for start, end in sorted(checked):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def distinct_overlap(turns, total_frames):
    """Return intervals with at least two distinct local labels active."""
    events = {}
    for start, end, label in turns:
        union([(start, end)], total_frames)
        if not isinstance(label, str) or not label:
            raise ValueError('Nonempty local speaker label required')
        events.setdefault(start, []).append((label, 1))
        events.setdefault(end, []).append((label, -1))
    active = {}
    previous = 0
    out = []
    for point, changes in sorted(events.items()):
        if point > previous and sum(count > 0 for count in active.values()) >= 2:
            out.append((previous, point))
        for label, delta in changes:
            active[label] = active.get(label, 0) + delta
        previous = point
    return union(out, total_frames)


def discovery_counts(reference_intervals, predicted_intervals, total_frames):
    """Zero-collar binary union TP/FP/FN, exact in 16kHz sample units."""
    ref = union(reference_intervals, total_frames)
    pred = union(predicted_intervals, total_frames)
    reference = sum(end - start for start, end in ref)
    predicted = sum(end - start for start, end in pred)
    i = j = tp = 0
    while i < len(ref) and j < len(pred):
        start = max(ref[i][0], pred[j][0])
        end = min(ref[i][1], pred[j][1])
        tp += max(0, end - start)
        if ref[i][1] <= pred[j][1]:
            i += 1
        else:
            j += 1
    return {'input_frames': total_frames,
            'reference_overlap_frames': reference,
            'predicted_overlap_frames': predicted,
            'true_positive_frames': tp,
            'false_positive_frames': predicted - tp,
            'false_negative_frames': reference - tp,
            'precision': tp / predicted if predicted else None,
            'recall': tp / reference if reference else None}


def shift_recording_intervals(intervals, support_start_frame, total_frames):
    """Convert exact recording-absolute reference samples to input-local samples."""
    _frame(support_start_frame)
    return union([(a - support_start_frame, b - support_start_frame)
                  for a, b in intervals], total_frames)


def cached_availability_score(population, cached_errors, available,
                              expected_reference_units):
    """Fixed-population accounting over previously computed oracle error counts.

    available is an explicit gate result, not inferred from text quality. Missing
    availability is conservatively false. Missing/failed/no-trigger inputs keep
    their full reference denominator. This helper does not validate live sample,
    model, prompt, runtime or adaptive-budget equivalence; policy.json states
    those independently required production gates.
    """
    ids = [row['input_id'] for row in population]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate population input')
    if any(key not in ids for key in cached_errors) or any(key not in ids for key in available):
        raise ValueError('Unexpected input ID')
    rows = []
    for row in population:
        sid = row['input_id']
        den = row['reference_characters']
        if type(den) is not int or den <= 0:
            raise ValueError('Positive frozen reference denominator required')
        gate = available.get(sid, False)
        if type(gate) is not bool:
            raise ValueError('Availability must be an explicit boolean')
        if gate:
            errors = cached_errors[sid]
            if type(errors) is not int or errors < 0:
                raise ValueError('Nonnegative cached error count required')
        else:
            errors = den
        rows.append({'input_id': sid, 'available': gate,
                     'errors': errors, 'reference_units': den})
    den = sum(row['reference_units'] for row in rows)
    if den != expected_reference_units:
        raise ValueError('Frozen denominator changed')
    errors = sum(row['errors'] for row in rows)
    return {'errors': errors, 'reference_units': den,
            'error_rate': errors / den if den else None,
            'inputs': rows, 'fresh_MOSS_calls': 0,
            'metric_scope': 'availability-penalized cached per-input oracle error counts'}
