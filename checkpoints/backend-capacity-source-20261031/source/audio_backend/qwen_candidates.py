"""Model-free, candidate-only Qwen window contracts.

The input window is the support of the entire native text. It is never a word
interval, speaker turn, silence assertion, or recognition-completeness claim.
No model, numerical package, primary store, or identity store is imported here.
"""
from array import array
import hashlib
import json
import math
import sys

from .fragment_core import CoreWindow, SourcePlan, UnsupportedAudio, core_windows
from .fragment_runtime import LogicalPCMReader
from .fragment_runtime_contracts import (
    CONTRACT, MAX_PARTS, PCMWindow, RATE, admit, bounded_json, canonical, digest,
    expected_mapping, integer,
)

SCHEMA = 'qwen-window-candidate-v1'
REQUEST_SCHEMA = 'qwen-window-request-v1'
NATIVE_SCHEMA = 'qwen-native-envelope-v1'
CORE_FRAMES = 320000
FLOAT_TRANSFORM = 'pcm16le-div32768-to-ieee754-float32le-v1'
MAX_TEXT_BYTES = 65536  # Combined native text and raw_text, not characters.
MAX_NATIVE_BYTES = 131072
MAX_REQUEST_BYTES = 131072
MAX_TOKENS = 1024
MAX_REQUESTS = 1024
DEFAULT_CONFIG = {
    'backend': 'cpu', 'n_threads': 2, 'kv_type': 'f16', 'n_ctx': 2048,
    'language': None, 'task': 'transcribe', 'timestamps': 'none',
    'context_frames': 0, 'generation_cap': 256, 'no_history': True,
}
_FROZEN_CONFIG = canonical(DEFAULT_CONFIG)
_STATUS_NAMES = {0: 'OK', 13: 'ABORTED', 17: 'INPUT_TOO_LONG', 18: 'OUTPUT_TRUNCATED'}
_SAFETY = {
    'role': 'optional_asr_window_candidate', 'candidate_only': True,
    'verification_status': 'unverified_model_output',
    'timestamp_semantics': 'input_window_only', 'word_timestamps': None,
    'speaker_id': None, 'speaker_local_id': None,
    'speaker_attribution': 'unavailable', 'profile_update_eligible': False,
    'enrollment_available': False, 'confidence': None,
    'confidence_semantics': 'native_token_p_uncalibrated',
    'generated_token_count': None, 'generated_token_count_known': False,
}
_IDS = {'binding_sha256', 'request_id', 'candidate_id'}
_REQUEST_FIELDS = {
    'schema', 'source_scope', 'source_generation', 'plan_sha256', 'plan_end_frame',
    'source_contract', 'sample_rate', 'source_clock', 'ownership', 'read',
    'context', 'source_mapping', 'source_map_sha256', 'pcm16_sha256',
    'float32_sha256', 'float32_count', 'float32_transform',
    'runtime_manifest_sha256', 'model_sha256', 'config', 'profile_sha256',
} | _IDS
_RAW_REQUIRED = {
    'native_status', 'native_status_name', 'result_available', 'text', 'raw_text',
    'language', 'was_truncated', 'truncation_available', 'exception_type',
    'exception_status', 'partial_result_available', 'native_token_count',
    'native_timestamp_kind',
}
_RAW_OPTIONAL = {'native_tokens', 'native_timings', 'execution_returned'}
_NATIVE_EXTRA = {
    'schema', 'status', 'complete', 'issues', 'native_status_canonical_name',
    'numeric_status_contract', 'normalization_version', 'truncation_state',
}


class UnsupportedSourceContract(UnsupportedAudio):
    reason_code = 'unsupported_source_contract'


def _object(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(f'Unexpected {label} fields')


def _text(value, label, limit=256, *, nullable=False, empty=False):
    if nullable and value is None:
        return
    if type(value) is not str or (not empty and not value) or len(value.encode('utf-8')) > limit:
        raise ValueError(f'Invalid bounded {label}')


def _hash(value, label):
    if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError(f'Invalid {label} SHA256')


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f'{label} must be a boolean')


def strict_loads(text, *, limit=MAX_NATIVE_BYTES):
    """Bounded JSON boundary rejecting duplicate keys and nonfinite values."""
    if type(text) is not str or len(text.encode('utf-8')) > limit:
        raise ValueError('Bounded JSON text required')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Nonfinite JSON value')
    result = json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
    bounded_json(result, limit)
    return result


def _copy(value):
    return json.loads(canonical(value))


def admit_source_plan(plan):
    """Only an original, immutable, finalized, contiguous exact-PCM plan."""
    if type(plan) is not SourcePlan or plan.final is not True:
        raise UnsupportedSourceContract('Finalized original SourcePlan required')
    try:
        admit(plan)
    except (TypeError, ValueError) as exc:
        raise UnsupportedSourceContract(str(exc)) from exc
    for source in plan.sources:
        if (type(source.sample_width) is not int or source.sample_width != 2 or
                source.format != (RATE, 1, 2, 'wav-pcm16le')):
            raise UnsupportedSourceContract('Original 16 kHz mono PCM16 WAV required')
    if plan.observed_frames != plan.end_frame:
        raise UnsupportedSourceContract('One contiguous source island required')
    return plan


def candidate_windows(plan):
    """Disjoint integer-frame ownership, zero context, including one-frame EOS."""
    admit_source_plan(plan)
    return core_windows(plan, core_frames=CORE_FRAMES, context_frames=0)


def pcm16_to_float32le(pcm):
    """Exact, endian-explicit model-input bytes; no implicit resampling/padding."""
    if type(pcm) is not bytes or not pcm or len(pcm) % 2 or len(pcm) > 2 * CORE_FRAMES:
        raise ValueError('Bounded nonempty PCM16 bytes required')
    samples = array('h')
    if samples.itemsize != 2:
        raise ValueError('Platform has no two-byte signed short')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    result = array('f', (sample / 32768.0 for sample in samples))
    if result.itemsize != 4:
        raise ValueError('Platform has no four-byte float')
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


def _validate_config(config):
    _object(config, DEFAULT_CONFIG, 'candidate config')
    # canonical JSON distinguishes bool/int and int/float equality pitfalls.
    if canonical(config) != _FROZEN_CONFIG:
        raise ValueError('Only the frozen CPU zero-context Qwen profile is supported')


def _validate_mapping(mapping, start, end):
    if type(mapping) is not list or not 0 < len(mapping) <= MAX_PARTS:
        raise ValueError('Bounded original-source mapping required')
    cursor = start
    for part in mapping:
        _object(part, {'span', 'source_sha256'}, 'source mapping')
        _hash(part['source_sha256'], 'source artifact')
        span = part['span']
        _object(span, {'fragment_id', 'source_start', 'source_end', 'logical_start', 'logical_end', 'island'}, 'source span')
        _text(span['fragment_id'], 'source ID')
        for key in ('source_start', 'source_end', 'logical_start', 'logical_end', 'island'):
            integer(span[key], key)
        if not (span['island'] == 0 and span['logical_start'] == cursor and
                cursor < span['logical_end'] <= end and
                span['source_end'] > span['source_start'] and
                span['source_end'] - span['source_start'] == span['logical_end'] - cursor):
            raise ValueError('Mapping must exactly cover read support in original samples')
        cursor = span['logical_end']
    if cursor != end:
        raise ValueError('Source mapping leaves uncovered input')


def _validate_pcm(pcm):
    if type(pcm) is not PCMWindow or pcm.contract != CONTRACT:
        raise ValueError('Exact-PCM reader receipt required')
    integer(pcm.start, 'PCM start')
    integer(pcm.end, 'PCM end')
    if not 0 < pcm.end - pcm.start <= CORE_FRAMES or type(pcm.pcm) is not bytes or len(pcm.pcm) != 2 * (pcm.end - pcm.start):
        raise ValueError('PCM input frame count differs from receipt')
    mapping = _copy(pcm.mapping)
    _validate_mapping(mapping, pcm.start, pcm.end)
    if pcm.map_sha256 != digest(mapping) or pcm.pcm_sha256 != hashlib.sha256(pcm.pcm).hexdigest():
        raise ValueError('PCM or mapping hash differs from reader receipt')
    return pcm16_to_float32le(pcm.pcm)


def make_candidate_request(plan, window, pcm, *, source_scope, source_generation,
                           runtime_manifest_sha256, model_sha256, config=None):
    """Bind exact input receipts to immutable source scope and decoder profile."""
    admit_source_plan(plan)
    if type(window) is not CoreWindow:
        raise ValueError('CoreWindow required')
    floats = _validate_pcm(pcm)
    for name in ('core_start', 'core_end', 'read_start', 'read_end', 'island'):
        integer(getattr(window, name), name)
    if not (window.island == 0 and window.core_start == window.read_start == pcm.start and
            window.core_end == window.read_end == pcm.end and
            window.core_start % CORE_FRAMES == 0 and
            window.core_end == min(window.core_start + CORE_FRAMES, plan.end_frame)):
        raise ValueError('Window is not an admitted zero-context ownership window')
    mapping = _copy(expected_mapping(plan, pcm.start, pcm.end))
    if canonical(mapping) != canonical(pcm.mapping):
        raise ValueError('PCM receipt differs from accepted SourcePlan')
    config = _copy(DEFAULT_CONFIG if config is None else config)
    _validate_config(config)
    request = {
        'schema': REQUEST_SCHEMA, 'source_scope': source_scope,
        'source_generation': source_generation, 'plan_sha256': plan.sha256,
        'plan_end_frame': plan.end_frame, 'source_contract': CONTRACT, 'sample_rate': RATE,
        'source_clock': 'logical_plan_integer_samples_exact_original_mapping',
        'ownership': {'start_frame': pcm.start, 'end_frame': pcm.end},
        'read': {'start_frame': pcm.start, 'end_frame': pcm.end},
        'context': {'left_frames': 0, 'right_frames': 0,
                    'left_reason': 'capture_start' if pcm.start == 0 else 'ownership_boundary',
                    'right_reason': 'true_eos' if pcm.end == plan.end_frame else 'ownership_boundary'},
        'source_mapping': mapping, 'source_map_sha256': pcm.map_sha256,
        'pcm16_sha256': pcm.pcm_sha256, 'float32_sha256': hashlib.sha256(floats).hexdigest(),
        'float32_count': pcm.end - pcm.start, 'float32_transform': FLOAT_TRANSFORM,
        'runtime_manifest_sha256': runtime_manifest_sha256, 'model_sha256': model_sha256,
        'config': config, 'profile_sha256': digest(config),
    }
    binding = digest(request)
    request.update(binding_sha256=binding, request_id=binding, candidate_id=binding)
    return validate_candidate_request(request)


def validate_candidate_request(request):
    _object(request, _REQUEST_FIELDS, 'candidate request')
    bounded_json(request, MAX_REQUEST_BYTES)
    if request['schema'] != REQUEST_SCHEMA or request['source_contract'] != CONTRACT:
        raise ValueError('Unsupported candidate/source contract')
    _text(request['source_scope'], 'source scope')
    integer(request['source_generation'], 'source generation')
    integer(request['plan_end_frame'], 'plan end', 1)
    integer(request['sample_rate'], 'sample rate')
    if request['sample_rate'] != RATE or request['source_clock'] != 'logical_plan_integer_samples_exact_original_mapping':
        raise ValueError('Exact 16 kHz source clock required')
    for key in ('plan_sha256', 'source_map_sha256', 'pcm16_sha256', 'float32_sha256',
                'runtime_manifest_sha256', 'model_sha256', 'profile_sha256', *_IDS):
        _hash(request[key], key)
    for key in ('ownership', 'read'):
        _object(request[key], {'start_frame', 'end_frame'}, key)
        for value in request[key].values():
            integer(value, key)
    start, end = request['read']['start_frame'], request['read']['end_frame']
    if not (request['ownership'] == request['read'] and 0 <= start < end <= request['plan_end_frame'] and
            start % CORE_FRAMES == 0 and end == min(start + CORE_FRAMES, request['plan_end_frame'])):
        raise ValueError('Invalid disjoint zero-context ownership/read window')
    context = request['context']
    _object(context, {'left_frames', 'right_frames', 'left_reason', 'right_reason'}, 'context')
    for key in ('left_frames', 'right_frames'):
        integer(context[key], key)
        if context[key] != 0:
            raise ValueError('Context requires a separately admitted profile')
    if (context['left_reason'] != ('capture_start' if start == 0 else 'ownership_boundary') or
            context['right_reason'] != ('true_eos' if end == request['plan_end_frame'] else 'ownership_boundary')):
        raise ValueError('Context boundary reason differs from source bounds')
    _validate_mapping(request['source_mapping'], start, end)
    if request['source_map_sha256'] != digest(request['source_mapping']):
        raise ValueError('Source mapping hash mismatch')
    integer(request['float32_count'], 'float32 count', 1)
    if request['float32_count'] != end - start or request['float32_transform'] != FLOAT_TRANSFORM:
        raise ValueError('Float32 input receipt mismatch')
    _validate_config(request['config'])
    if request['profile_sha256'] != digest(request['config']):
        raise ValueError('Profile hash mismatch')
    binding = digest({key: value for key, value in request.items() if key not in _IDS})
    if any(request[key] != binding for key in _IDS):
        raise ValueError('Candidate binding/identity mismatch')
    return request


def prepare_candidate_requests(plan, paths, *, source_scope, source_generation,
                               runtime_manifest_sha256, model_sha256, config=None):
    """Read one bounded window at a time; returned requests contain no PCM/path."""
    admit_source_plan(plan)
    if plan.end_frame > MAX_REQUESTS * CORE_FRAMES:
        raise ValueError('Candidate inventory exceeds the 1024-window bound')
    reader = LogicalPCMReader(plan, paths)
    return tuple(make_candidate_request(plan, window, reader.read(window.read_start, window.read_end),
        source_scope=source_scope, source_generation=source_generation,
        runtime_manifest_sha256=runtime_manifest_sha256, model_sha256=model_sha256, config=config)
        for window in candidate_windows(plan))


def validate_candidate_input(request, pcm):
    """Recheck the exact supplied PCM/map/floats against a frozen request."""
    validate_candidate_request(request)
    floats = _validate_pcm(pcm)
    if (request['read'] != {'start_frame': pcm.start, 'end_frame': pcm.end} or
            request['source_map_sha256'] != pcm.map_sha256 or
            canonical(request['source_mapping']) != canonical(pcm.mapping) or
            request['pcm16_sha256'] != pcm.pcm_sha256 or
            request['float32_sha256'] != hashlib.sha256(floats).hexdigest()):
        raise ValueError('Frozen candidate input differs from reader receipt')
    return floats


def _validate_raw(raw):
    if type(raw) is not dict or not _RAW_REQUIRED <= set(raw) or set(raw) - _RAW_REQUIRED - _RAW_OPTIONAL:
        raise ValueError('Unknown or missing native adapter fields')
    bounded_json(raw, MAX_NATIVE_BYTES)
    for key in ('result_available', 'truncation_available', 'partial_result_available'):
        _boolean(raw[key], key)
    _boolean(raw.get('execution_returned', True), 'execution_returned')
    for key in ('native_status', 'exception_status'):
        if raw[key] is not None:
            integer(raw[key], key)
            if raw[key] > 2147483647:
                raise ValueError('Native numeric status out of range')
    for key in ('native_status_name', 'language', 'exception_type', 'native_timestamp_kind'):
        _text(raw[key], key, nullable=True, empty=key == 'language')
    if raw['exception_type'] is not None and not all(c.isalnum() or c in '._' for c in raw['exception_type']):
        raise ValueError('Exception type must be an identifier, not traceback/message')
    if raw['was_truncated'] is not None:
        _boolean(raw['was_truncated'], 'was_truncated')
    total = 0
    for key in ('text', 'raw_text'):
        _text(raw[key], key, MAX_TEXT_BYTES, nullable=True, empty=True)
        if raw[key] is not None:
            total += len(raw[key].encode('utf-8'))
    if total > MAX_TEXT_BYTES:
        raise ValueError('Combined native text exceeds byte bound')
    if raw['native_token_count'] is not None:
        integer(raw['native_token_count'], 'native token inventory count')
        if raw['native_token_count'] > MAX_TOKENS:
            raise ValueError('Native token inventory exceeds bound')
    tokens = raw.get('native_tokens')
    if tokens is not None:
        if type(tokens) is not list or len(tokens) > MAX_TOKENS or raw['native_token_count'] != len(tokens):
            raise ValueError('Native token inventory count mismatch')
        for token in tokens:
            _object(token, {'id', 'text', 'p'}, 'native token')
            integer(token['id'], 'token ID')
            _text(token['text'], 'token text', 1024, empty=True)
            p = token['p']
            if p is not None and (type(p) not in (int, float) or not math.isfinite(p)):
                raise ValueError('Native token p must be finite or unavailable')
    timings = raw.get('native_timings')
    if timings is not None:
        if type(timings) is not dict or len(timings) > 32:
            raise ValueError('Bounded native computation timings required')
        for key, value in timings.items():
            _text(key, 'timing key', 64)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid native computation timing')


def normalize_native_result(raw):
    """Validate an adapter return; preserve errors/partials and unknown flags.

    Status numbers are pinned to transcribe.cpp 0.2.3, not arbitrary runtimes.
    The runner must verify its actual imported/native provenance before calling.
    Returned text is never trimmed, corrected, aligned, or assigned to speakers.
    """
    _validate_raw(raw)
    raw = _copy(raw)
    raw.setdefault('execution_returned', True)
    raw.setdefault('native_tokens', None)
    raw.setdefault('native_timings', None)
    issues = []
    status, exception = raw['native_status'], raw['exception_type']
    if not raw['execution_returned']:
        issues.append('execution_return_unconfirmed')
    if raw['truncation_available'] != (raw['was_truncated'] is not None):
        issues.append('inconsistent_truncation_receipt')
    if not raw['truncation_available']:
        issues.append('truncation_unavailable')
    if status == 18 and raw['was_truncated'] is False:
        issues.append('truncation_status_flag_disagree')
    if raw['exception_status'] is not None and (exception is None or raw['exception_status'] != status):
        issues.append('exception_status_mismatch')
    if status in _STATUS_NAMES and raw['native_status_name'] not in (None, _STATUS_NAMES[status]):
        issues.append('native_status_name_mismatch')
    if raw['result_available'] != (raw['text'] is not None and raw['raw_text'] is not None):
        issues.append('result_availability_mismatch')
    if not raw['result_available'] and any(raw[key] is not None for key in ('text', 'raw_text', 'language', 'native_timestamp_kind', 'native_token_count', 'native_tokens', 'native_timings')):
        issues.append('result_fields_without_result')
    if raw['partial_result_available'] and (not raw['result_available'] or exception is None):
        issues.append('partial_result_receipt_mismatch')
    if raw['native_timestamp_kind'] not in (None, 'none'):
        issues.append('unexpected_native_timestamp_kind')
    if status == 13:
        outcome = 'aborted'
    elif status == 18 or raw['was_truncated'] is True:
        outcome = 'output_truncated'
    elif exception is not None or status not in (None, 0):
        outcome = 'native_error'
    elif status is None or not raw['result_available'] or issues:
        outcome = 'schema_error'
    else:
        outcome = 'returned_empty' if raw['text'] == '' else 'returned_text'
    result = dict(raw, schema=NATIVE_SCHEMA, status=outcome,
        complete=outcome in ('returned_text', 'returned_empty'), issues=issues,
        native_status_canonical_name=_STATUS_NAMES.get(status),
        numeric_status_contract='transcribe.cpp-0.2.3',
        normalization_version='preserve-native-fields-v1',
        truncation_state='available' if raw['truncation_available'] else 'unavailable', **_SAFETY)
    bounded_json(result, MAX_NATIVE_BYTES)
    return result


def validate_native_result(result):
    _object(result, _RAW_REQUIRED | _RAW_OPTIONAL | _NATIVE_EXTRA | set(_SAFETY), 'normalized native result')
    raw = {key: result[key] for key in _RAW_REQUIRED | _RAW_OPTIONAL}
    expected = normalize_native_result(raw)
    if canonical(result) != canonical(expected):
        raise ValueError('Native envelope differs from raw receipts or candidate-only semantics')
    return result


def public_candidate(request, native):
    """Path-free projection. The whole read support applies to both text fields."""
    validate_candidate_request(request)
    validate_native_result(native)
    result = {'schema': SCHEMA, 'candidate_id': request['candidate_id'],
              'request_id': request['request_id'], 'binding_sha256': request['binding_sha256'],
              'request': _copy(request), 'native': _copy(native), **_SAFETY}
    # Token inventory is retained in evidence, but not public by default.
    result['native'].pop('native_tokens')
    return result


def validate_candidate(result, binding):
    """Validate a full evidence/public projection against its immutable request."""
    validate_candidate_request(binding)
    _object(result, {'schema', 'candidate_id', 'request_id', 'binding_sha256', 'request', 'native'} | set(_SAFETY), 'candidate projection')
    native = _copy(result['native'])
    native.setdefault('native_tokens', None)
    validate_native_result(native)
    expected = public_candidate(binding, native)
    if canonical(result) != canonical(expected):
        raise ValueError('Candidate projection differs from its frozen binding')
    return result


normalize_native = normalize_native_result
