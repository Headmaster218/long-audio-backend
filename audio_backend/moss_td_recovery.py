"""Bounded partial candidate extraction; strict parsing remains authoritative.

No timestamp is repaired. A complete raw envelope is independently checked,
including the text and labels on rows whose timestamps cannot be exposed.
"""
import hashlib
import math
import re
from fractions import Fraction

# Keep this validator usable by the API without importing optional inference
# dependencies. The strict parser's canonical grammar is intentionally mirrored;
# synthetic parity tests protect it while the original parser remains untouched.
_TIMESTAMP = r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)'
_CANONICAL = re.compile(r'\[(' + _TIMESTAMP + r')\]\s*\[(S[0-9]+)\](.*?)\[(' + _TIMESTAMP + r')\]', re.S)
_LABEL = re.compile(r'S[0-9]{1,15}\Z')

POLICY = 'partial_schema_valid_phrases_v1'
TIMESTAMP_ERROR = 'out_of_range_or_invalid_timestamps'
MAX_PHRASES = 128
MAX_RAW_BYTES = 100000
MAX_TIMESTAMP_CHARS = 128


def partial_phrases(group, *, frame_count, sample_rate, start_frame, capacity):
    """Return independent rows/provenance, or None when recovery is ineligible.

    Frame arguments come from the parent's actual extraction, never the runner.
    Original indexes and first-seen slots include rejected rows. Lexical bounds
    use exact rational arithmetic so tiny positive overshoots cannot round in.
    """
    if (type(frame_count) is not int or not 0 < frame_count <= 320000 or
            type(sample_rate) is not int or sample_rate != 16000 or
            type(start_frame) is not int or not 0 <= start_frame <= 960000 or
            start_frame + frame_count > 960000 or
            type(capacity) is not int or not 2 <= capacity <= 8):
        return None
    if (not isinstance(group, dict) or group.get('status') != 'unresolved' or
            group.get('phrases') != [] or
            group.get('schema_errors') != [TIMESTAMP_ERROR] or
            group.get('reason_code') != TIMESTAMP_ERROR):
        return None
    metadata = group.get('metadata')
    if (not isinstance(metadata, dict) or
            metadata.get('runtime_provider') != 'transcribe_cpp' or
            metadata.get('inference_run') is not True or
            metadata.get('finish_reason') != 'eos' or
            metadata.get('native_output_truncated') is not False or
            metadata.get('token_limit_reached') is not False):
        return None
    raw = group.get('raw_text')
    if not isinstance(raw, str) or not raw or len(raw) > MAX_RAW_BYTES:
        return None
    try:
        encoded = raw.encode('utf-8')
    except UnicodeError:
        return None
    if len(encoded) > MAX_RAW_BYTES:
        return None
    matches = list(_CANONICAL.finditer(raw))
    if not 0 < len(matches) <= MAX_PHRASES or _CANONICAL.sub('', raw).strip():
        return None
    slots = {}
    selected = []
    for index, match in enumerate(matches):
        label, body = match[2], match[3]
        if (not _LABEL.fullmatch(label) or not body.strip() or
                len(body) > 10000 or len(match[1]) > MAX_TIMESTAMP_CHARS or
                len(match[4]) > MAX_TIMESTAMP_CHARS):
            return None
        if label not in slots:
            slots[label] = len(slots)
        if len(slots) > capacity:
            return None
        a, b = float(match[1]), float(match[4])
        if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b and
                Fraction(match[1]) < Fraction(match[4]) <= Fraction(frame_count, sample_rate)):
            continue
        selected.append({'speaker_local_id': label, 'start': a, 'end': b,
                         'text': body.strip(), 'raw_text': body,
                         'native_phrase_index': index,
                         'native_phrase_sha256': hashlib.sha256(match[0].encode('utf-8')).hexdigest(),
                         'original_start_timestamp': match[1], 'original_end_timestamp': match[4]})
    # A contradictory label receipt cannot silently redefine complete-support
    # source slots. No rows are sourced from diagnostic_phrases.
    if (metadata.get('speaker_label_slots') != slots or
            any(type(slot) is not int for slot in metadata['speaker_label_slots'].values()) or
            type(metadata.get('model_local_speaker_count')) is not int or
            metadata['model_local_speaker_count'] != len(slots) or
            not selected or len(selected) == len(matches)):
        return None
    common = {'recovery_policy': POLICY, 'support_parse_status': 'unresolved',
              'support_evidence_status': 'partial', 'native_phrase_count': len(matches),
              'recovered_phrase_count': len(selected), 'rejected_phrase_count': len(matches) - len(selected),
              'raw_output_sha256': hashlib.sha256(encoded).hexdigest(),
              'support_sample_rate': sample_rate, 'support_frame_count': frame_count,
              'support_start_frame': start_frame, 'support_end_frame': start_frame + frame_count,
              'support_frame_axis': 'analyzer_window', 'original_timestamp_axis': 'support_relative',
              'timestamp_adjustment': 'none'}
    return selected, slots, common
