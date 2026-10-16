"""Bounded raw-first projection. Never invokes the legacy EOS parser or recovery."""
import hashlib
import json
import math
import re

from .moss_handy_profile import MODEL_SHA256, RUNTIME_SHA256, profile
from .native_lexical_alternatives import Alternative, ContractError, SourceSupport, MAX_RAW_BYTES, MAX_TEXT_BYTES

MAX_DIAGNOSTIC_BYTES = 65_536
MAX_PHRASES = 256
MAX_REJECTED_TIMESTAMP_PAIRS = 8
MAX_RESULT_BYTES = 229_376  # Leave 32 KiB for controller/native/cleanup envelopes.
_TIME = r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)'
_PHRASE = re.compile(r'\[(' + _TIME + r')\]\s*\[(S[0-9]{1,15})\](.*?)\[(' + _TIME + r')\]', re.S)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _rejected_timestamp(ordinal, start, end):
    """Numeric evidence only; one reason in this deterministic priority order.

    Priority: nonfinite, negative start, end <= start, end beyond support.
    These checks describe an already rejected pair, never decide acceptance.
    They make no claim about monotonicity between different phrases.
    """
    if not (math.isfinite(start) and math.isfinite(end)):
        reason = 'nonfinite'
    elif start < 0:
        reason = 'negative_start'
    elif start >= end:
        reason = 'end_not_after_start'
    else:
        reason = 'end_after_support'

    def number(value):
        if math.isfinite(value):
            return value, None
        return None, ('nan' if math.isnan(value) else
                      'positive_infinity' if value > 0 else 'negative_infinity')

    start, start_category = number(start)
    end, end_category = number(end)
    return reason, {'ordinal': ordinal, 'start': start, 'end': end,
        'start_nonfinite': start_category, 'end_nonfinite': end_category,
        'reason': reason}


def _attach_rejected_timestamps(result, duration, total, counts, pairs):
    """Add bounded evidence only after the legacy result-size decision.

    Trim only the new numeric pairs to fit the existing result limit. If even
    the header/counts cannot fit, omit this entire additive field: old fields
    and the old acceptance/oversize decisions are never changed for diagnostics.
    """
    if not total:
        return result
    pairs = list(pairs)
    while True:
        diagnostic = {'schema': 'rejected-model-timestamps-v1',
            'support_duration_seconds': duration, 'total': total,
            'omitted': total - len(pairs), 'reason_counts': counts, 'pairs': pairs}
        candidate = {**result, 'rejected_timestamps': diagnostic}
        if len(encoded(candidate)) <= MAX_RESULT_BYTES:
            return candidate
        if not pairs:
            return result
        pairs.pop()


def retain_raw(raw_text):
    """Retain exact native.raw_text UTF-8, or explicitly no exact raw output.

    A prefix is never mislabeled as exact. SDK native tokens/segments are not
    included in this deliberately named raw scope and are not reconstructed.
    """
    if type(raw_text) is not str:
        return None, 'raw_text_unavailable'
    if len(raw_text) > MAX_RAW_BYTES:
        return None, 'raw_text_oversize'
    try:
        raw = raw_text.encode('utf-8')
    except UnicodeEncodeError:
        return None, 'raw_text_invalid_unicode'
    if len(raw) > MAX_RAW_BYTES:
        return None, 'raw_text_oversize'
    return raw, None


def project(raw, *, support, profile_name, outcome='returned', truncated=False, aborted=False):
    """Call only after exact raw retention. Return one untimed alternative at most."""
    if type(support) is not SourceSupport or type(raw) is not bytes or len(raw) > MAX_RAW_BYTES:
        raise ValueError('Validated support and bounded retained raw are required')
    if outcome not in ('returned', 'failed', 'unknown') or type(truncated) is not bool or type(aborted) is not bool:
        raise ValueError('Invalid native outcome')
    chosen = profile(profile_name)
    result = {'schema': 'moss-handy-diagnostic-v1', 'support_id': support.support_id,
        'profile_sha256': chosen.sha256, 'raw_scope': 'exact-native-raw-text-utf8',
        'raw_bytes': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
        'eos': None, 'generated_token_count': None, 'complete_content': False,
        'timestamp_quality': 'model_generated_unverified', 'speaker_identity_valid': False,
        'profile_update_allowed': False, 'alternative': None, 'diagnostic_phrases': [],
        'schema_errors': [], 'truncated': truncated, 'aborted': aborted}
    errors = result['schema_errors']
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        errors.append('invalid_raw_utf8')
        return result
    matches, tail = [], 0
    for match in _PHRASE.finditer(text):
        if len(matches) == MAX_PHRASES:
            errors.append('phrase_limit')
            return result
        if text[tail:match.start()].strip():
            errors.append('unparsed_text')
        matches.append(match)
        tail = match.end()
    if text[tail:].strip():
        errors.append('unparsed_text')
    if not matches:
        errors.append('no_phrases')
    labels, phrases, words = {}, [], []
    rejected_total, rejected_pairs = 0, []
    rejected_counts = {reason: 0 for reason in (
        'nonfinite', 'negative_start', 'end_not_after_start', 'end_after_support')}
    duration = (support.end_frame - support.start_frame) / support.sample_rate
    for ordinal, match in enumerate(matches, 1):
        start, label, original, end = float(match[1]), match[2], match[3], float(match[4])
        words.append(original)
        if label not in labels:
            labels[label] = len(labels)
        if labels[label] >= 8:
            errors.append('speaker_capacity_exceeded')
            continue
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration):
            errors.append('invalid_model_timestamps')
            reason, pair = _rejected_timestamp(ordinal, start, end)
            rejected_total += 1
            rejected_counts[reason] += 1
            if len(rejected_pairs) < MAX_REJECTED_TIMESTAMP_PAIRS:
                rejected_pairs.append(pair)
            continue
        if not original.strip():
            errors.append('empty_phrase_text')
            continue
        phrases.append({'speaker_local_id': label, 'source_slot': labels[label],
            'start': start, 'end': end, 'raw_text': original,
            'timing_scope': 'support-relative-model-unverified',
            'speaker_identity_valid': False})
    if len(encoded(phrases)) <= MAX_DIAGNOSTIC_BYTES:
        result['diagnostic_phrases'] = phrases
    else:
        errors.append('diagnostic_projection_oversize')
    lexical = ''.join(words)
    if len(lexical.encode('utf-8')) > MAX_TEXT_BYTES:
        errors.append('untimed_text_oversize')
    else:
        try:
            alt = Alternative(support, 'moss', MODEL_SHA256, RUNTIME_SHA256, chosen.sha256,
                lexical, outcome, None, None, truncated, raw)
            result['alternative'] = alt.to_dict()
        except ContractError:
            errors.append('alternative_projection_oversize')
    result['schema_errors'] = list(dict.fromkeys(errors))
    if len(encoded(result)) > MAX_RESULT_BYTES:
        result.update(alternative=None, diagnostic_phrases=[], schema_errors=['result_projection_oversize'])
    return _attach_rejected_timestamps(result, duration, rejected_total,
                                       rejected_counts, rejected_pairs)
