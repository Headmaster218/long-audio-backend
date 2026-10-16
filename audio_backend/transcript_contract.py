"""Fixed, model-free contracts for a current unified transcript projection."""
import base64
import hashlib
import json
import math
import re

from .native_owned_support_cache import ReadError

RATE, BLOCK_FRAMES = 16000, 960000
MAX_FRAME = (1 << 53) - 1
MAX_BINDINGS, MAX_BINDING_BYTES = 1024, 4 * 1024**2
MAX_PRIMARY, MAX_PRIMARY_BYTES = 128, 256 * 1024
MAX_RESULTS, MAX_RESULT_BYTES, MAX_RESULTS_BYTES = 16, 256 * 1024, 2 * 1024**2
MAX_RESPONSE_BYTES, MAX_METADATA_BYTES = 256 * 1024, 16384
PRIMARY_FIELDS = ('id', 'chunk_id', 'start', 'end', 'text', 'raw_text',
                  'asr_backend', 'timestamp_kind', 'confidence',
                  'speaker_local_id', 'speaker_window_start')
HASH = re.compile(r'[a-f0-9]{64}\Z')
POLICY = {'schema': 'current-unified-transcript-v1', 'rate': RATE,
    'block_frames': BLOCK_FRAMES, 'selection': 'exact-adjacent-path-whole-primary-v1',
    'rank': ['maximum-support-frames', 'fewer-jobs', 'seam-ordinary-start-archive-hash'],
    'moss_scope': 'durable-automatic-timestamped-recording-only',
    'block_crossing': 'unresolved-alternative', 'word_times': None,
    'primary_frames': 'outward-envelope-snap-within-1e-6-frame',
    'primary_context_seconds': 5,
    'identity': 'anonymous-source-local', 'completeness': 'not-asserted'}


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('ascii')


POLICY_HASH = hashlib.sha256(encoded(POLICY)).hexdigest()


def fail(reason, status=503):
    raise ReadError(status, reason)


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate key')
        result[key] = value
    return result


def parse_json(raw, maximum):
    if type(raw) not in (bytes, str) or not 0 < len(raw) <= maximum:
        fail('unresolved_capacity')
    try:
        return json.loads(raw, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        fail('transcript_evidence_invalid')


def frame(seconds):
    if (type(seconds) not in (int, float) or not math.isfinite(seconds)
            or not 0 <= seconds <= MAX_FRAME / RATE):
        fail('transcript_coordinate_invalid')
    value = round(seconds * RATE)
    if not 0 <= value <= MAX_FRAME or seconds != value / RATE:
        fail('transcript_coordinate_unplaced')
    return value


def primary_frame(seconds, *, end=False):
    """Conservative display geometry; the original ASR float stays immutable."""
    if (type(seconds) not in (int, float) or not math.isfinite(seconds)
            or not 0 <= seconds <= MAX_FRAME / RATE):
        fail('transcript_primary_invalid')
    scaled = seconds * RATE
    nearest = round(scaled)
    if abs(scaled - nearest) <= 1e-6:
        value = nearest
    else:
        value = math.ceil(scaled) if end else math.floor(scaled)
    if not 0 <= value <= MAX_FRAME: fail('transcript_primary_invalid')
    return value


def primary_evidence(sid, row):
    """Current exact ASR content identity, deliberately excluding person assignment."""
    data = {'session_id': sid, **{key: row[key] for key in PRIMARY_FIELDS}}
    if (type(data['id']) is not int or data['id'] < 1
            or type(data['text']) is not str
            or any(data[key] is not None and type(data[key]) is not str
                   for key in ('raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id'))):
        fail('transcript_primary_invalid')
    for name in ('confidence', 'speaker_window_start'):
        value = data[name]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            fail('transcript_primary_invalid')
    start, end = primary_frame(data['start']), primary_frame(data['end'], end=True)
    if data['start'] >= data['end'] or start >= end: fail('transcript_primary_invalid')
    sha = digest(data)
    return {'source_ref': f"p.{data['id']}.{sha}", 'source': 'primary',
        'content_sha256': sha, 'home_block': start // BLOCK_FRAMES,
        'start_frame': start, 'end_frame': end, 'text': data['text'],
        'raw_text': data['raw_text'], 'timestamp_kind': data['timestamp_kind'],
        'speaker_scope': [data['chunk_id'], data['speaker_window_start'], data['speaker_local_id']]
            if data['speaker_local_id'] is not None and data['speaker_window_start'] is not None else None,
        'word_times': None, 'current_evidence': 'exact_current_asr_content',
        'person_identity': 'not_projected', 'canonical_asr': data}


def cursor(sid, block, revision):
    return base64.urlsafe_b64encode(encoded({'policy': POLICY_HASH, 'session': sid,
        'block': block, 'revision': revision})).decode('ascii').rstrip('=')


def parse_cursor(token, sid):
    if type(token) is not str or not 1 <= len(token) <= 512 or re.fullmatch(r'[A-Za-z0-9_-]+', token) is None:
        fail('invalid_transcript_cursor', 409)
    try:
        raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        value = parse_json(raw, 384)
        if (type(value) is not dict or set(value) != {'policy', 'session', 'block', 'revision'}
                or value['policy'] != POLICY_HASH or value['session'] != sid
                or type(value['block']) is not int or not 0 <= value['block'] <= MAX_FRAME // BLOCK_FRAMES
                or type(value['revision']) is not str or HASH.fullmatch(value['revision']) is None
                or cursor(sid, value['block'], value['revision']) != token):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, ReadError):
        fail('invalid_transcript_cursor', 409)


def parse_reference(ref):
    if type(ref) is not str or len(ref) > 160: fail('invalid_source_ref', 409)
    match = re.fullmatch(r'p\.([1-9][0-9]{0,18})\.([a-f0-9]{64})', ref)
    if match:
        ident = int(match[1])
        if ident >= 1 << 63: fail('invalid_source_ref', 409)
        return 'primary', ident, match[2], None
    match = re.fullmatch(r'm\.([a-f0-9]{32})\.([a-f0-9]{64})\.(all|0|[1-9][0-9]{0,2})', ref)
    if match:
        index = match[3] if match[3] == 'all' else int(match[3])
        if index != 'all' and index >= 256: fail('invalid_source_ref', 409)
        return 'moss', match[1], match[2], index
    fail('invalid_source_ref', 409)


def response(value):
    raw = encoded(value)
    if len(raw) > MAX_RESPONSE_BYTES: fail('unresolved_capacity')
    return raw
