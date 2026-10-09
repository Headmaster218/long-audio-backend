"""Bounded per-core ASR input accounting, independent of model output timing.

This records invocation/return evidence, never a speech or recognition-quality
decision. Read context is input support, not ownership. No audio is copied here.
The historical AnalyzerBridge remains unchanged; the CPU adapter opts in below.
"""
import copy
import re

from .fragment_cpu_meter import INPUT_ENCODING
from .fragment_model_bridges import AnalyzerBridge
from .fragment_runtime_contracts import RATE, bounded_json, digest, window_id

SCHEMA = 'fragment-asr-input-coverage-v1'
MAX_RECORDS = 512
MAX_ASR_CALLS = 32
MAX_BYTES = 512 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _span(value, lower, upper):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
        or any(type(v) is not int for v in value) or not lower <= value[0] < value[1] <= upper):
        raise ValueError('ASR support must be bounded half-open integer frames')
    return list(value)


def _hash(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError('Invalid ASR coverage source/input hash')
    return value


def _receipt(value, frames):
    if (not isinstance(value, dict) or set(value) != {'sample_rate', 'sample_count', 'pcm_sha256', 'encoding'}
        or type(value.get('sample_rate')) is not int or value['sample_rate'] != RATE
        or type(value.get('sample_count')) is not int or value['sample_count'] != frames
        or value.get('encoding') != INPUT_ENCODING):
        raise ValueError('ASR actual input receipt differs from selected frame support')
    _hash(value.get('pcm_sha256'))
    return copy.deepcopy(value)


def _union(spans):
    result = []
    for a, b in sorted(spans):
        if result and a <= result[-1][1]:
            result[-1][1] = max(b, result[-1][1])
        else:
            result.append([a, b])
    return result


def _gaps(start, end, support):
    result, cursor = [], start
    for a, b in support:
        if cursor < a:
            result.append([cursor, a])
        cursor = b
    if cursor < end:
        result.append([cursor, end])
    return result


def _source_map(mapping, start, end):
    """Exact PCM16 owners only; transformed reads retain filter-support indexes."""
    if mapping[0].get('kind') == 'native-filter-support-v1':
        from .fragment_normalized_runtime import validate_support
        validate_support(mapping, mapping[0]['canonical_start'], mapping[-1]['canonical_end'])
        return {'status': 'canonical_selection_with_native_filter_support',
                'binding_mapping_indexes': [i for i, part in enumerate(mapping)
                    if part['canonical_start'] < end and part['canonical_end'] > start],
                'semantics': 'Many-to-many filter support; not exact original-source sample ownership'}
    result, cursor = [], start
    for part in mapping:
        span = part['span']
        if (any(type(span.get(k)) is not int for k in ('logical_start', 'logical_end', 'source_start', 'source_end'))
            or not 0 <= span['source_start'] < span['source_end']
            or not 0 <= span['logical_start'] < span['logical_end']
            or not isinstance(span.get('fragment_id'), str) or not 0 < len(span['fragment_id']) <= 512):
            raise ValueError('Invalid integer ASR source owner span')
        a, b = max(start, span['logical_start']), min(end, span['logical_end'])
        if a < b:
            if a != cursor or span['source_end'] - span['source_start'] != span['logical_end'] - span['logical_start']:
                raise ValueError('ASR source mapping is not exact and contiguous')
            result.append({'logical_frames': [a, b], 'fragment_id': span['fragment_id'],
                           'source_frames': [span['source_start'] + a - span['logical_start'],
                                             span['source_start'] + b - span['logical_start']],
                           'source_artifact_sha256': _hash(part['source_sha256'])})
            cursor = b
    if cursor != end:
        raise ValueError('ASR source mapping does not cover selected input')
    return {'status': 'exact_pcm16_owners', 'parts': result,
            'semantics': 'Original source artifact hashes are distinct from actual float32 input hashes'}


def build_asr_coverage(binding, records):
    """Project only this core attempt's existing durable call receipts.

    A started receipt is an invocation-boundary acknowledgement. Process loss
    immediately afterward can be ambiguous. Only a confirmed returned AND
    completed call enters successful support. Neither union proves speech.
    """
    window = binding['window']
    start, end = window['read_start'], window['read_end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= start + 34 * RATE:
        raise ValueError('ASR coverage requires one bounded analyzer read')
    core_start, core_end = _span([window['core_start'], window['core_end']], start, end)
    mapping = binding['mapping']
    if (not isinstance(mapping, (list, tuple)) or not 0 < len(mapping) <= 128
        or digest(mapping) != binding['map_hash']):
        raise ValueError('Invalid bounded ASR source mapping')
    if mapping[0].get('kind') == 'native-filter-support-v1':
        from .fragment_normalized_runtime import validate_support
        validate_support(mapping, start, end)
    _source_map(mapping, start, end)
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError('ASR coverage call inventory exceeds one bounded core')
    inputs, submitted, decoded = [], [], []
    for index, record in enumerate(records):
        if record.get('kind') != 'sensevoice':
            continue
        if len(inputs) >= MAX_ASR_CALLS:
            raise ValueError('ASR coverage exceeds bounded call inventory')
        status = record.get('status')
        started, returned = record.get('execution_started', False), record.get('execution_returned', False)
        if (status not in {'reserved', 'completed', 'failed'} or type(started) is not bool
            or type(returned) is not bool or returned and not started or status == 'completed' and not started):
            raise ValueError('Inconsistent ASR attempt lifecycle')
        scope = record.get('scope')
        if scope is not None and (scope.get('window') != binding['window_id']
                                  or scope.get('session') != binding['run_id']
                                  or scope.get('plan') != binding.get('plan_hash')):
            raise ValueError('ASR receipt belongs to another core/session')
        planned = record.get('context')
        if not isinstance(planned, dict):
            raise ValueError('ASR reservation lacks exact input inventory')
        a, b = _span(planned.get('read_frames'), 0, end - start)
        expected = _receipt(planned.get('input'), b - a)
        actual, selected = record.get('input'), record.get('selection')
        if actual is not None or selected is not None or started:
            if (not isinstance(selected, dict) or _span(selected.get('read_frames'), 0, end - start) != [a, b]
                or _receipt(actual, b - a) != expected):
                raise ValueError('ASR actual selection differs from reserved exact input')
        logical = [start + a, start + b]
        owned = [max(core_start, logical[0]), min(core_end, logical[1])]
        owned = owned if owned[0] < owned[1] else None
        success = status == 'completed' and returned
        output = record.get('output')
        has_text = None
        if output is not None:
            count = output.get('raw_token_count')
            if (not returned or type(count) is not int or not 0 <= count <= 4096
                or type(output.get('has_text')) is not bool or output['has_text'] != (count > 0)):
                raise ValueError('Invalid ASR raw-token presence receipt')
            has_text = output['has_text']
        item = {'record_index': index, 'ticket_id': record.get('ticket_id'), 'role': 'primary_mixture_asr',
                'status': 'started' if status == 'reserved' and started else status,
                'meter_status': status, 'execution_started': started, 'execution_returned': returned,
                'planned_read_frames': [a, b], 'planned_input': expected,
                'read_frames': [a, b] if actual is not None else None,
                'input': copy.deepcopy(actual), 'logical_input_frames': logical if actual is not None else None,
                'core_intersection_frames': owned if actual is not None else None,
                'source_mapping': _source_map(mapping, *logical) if actual is not None else None,
                'submitted': started, 'successfully_decoded': success, 'has_text': has_text}
        if 'failure' in record:
            item['failure'] = copy.deepcopy(record['failure'])
        inputs.append(item)
        if started and owned:
            submitted.append(owned)
        if success and owned:
            decoded.append(owned)
    submitted, decoded = _union(submitted), _union(decoded)
    result = {'schema': SCHEMA, 'sample_rate': RATE, 'window_id': binding['window_id'],
              'role': 'primary_mixture_asr', 'read_frames': [start, end], 'core_frames': [core_start, core_end],
              'read_pcm16_sha256': _hash(binding['pcm_hash']), 'source_map_sha256': _hash(binding['map_hash']),
              'inputs': inputs, 'submitted_union': submitted, 'successfully_decoded_union': decoded,
              'gaps': _gaps(core_start, core_end, decoded),
              'semantics': {'coordinates': 'Half-open integer logical 16 kHz frames; read_frames on inputs are read-relative',
                  'submitted': 'Durable invocation-boundary receipt; an interrupted call may have an uncertain outcome',
                  'successfully_decoded': 'Confirmed decoder return and completed receipt; not accuracy or speech evidence',
                  'gaps': 'Core complement of successfully_decoded_union; missing support is not silence',
                  'has_text': 'Raw token count > 0 only; punctuation and empty output do not establish speech or silence',
                  'ownership': 'Input context is retained; only its intersection with this core enters these unions'}}
    bounded_json(result, MAX_BYTES)
    return result


class CoverageAnalyzerBridge(AnalyzerBridge):
    """New CPU provenance layer; no change to historical projection bytes."""
    def analyze(self, pcm, status):
        context, record_start = self.context, len(self.budget.records)
        self.failure_ref = None

        def augment(ref):
            value = self.evidence_store.read(ref)
            snapshot, window, _ = context
            if value['binding']['window_id'] != window_id(snapshot.run_id, window, snapshot.policy):
                raise ValueError('ASR coverage evidence belongs to another core')
            records = copy.deepcopy(self.budget.records[record_start:])
            value['model_call_records'] = records
            value['asr_input_coverage'] = build_asr_coverage(value['binding'], records)
            return self.evidence_store.put(value)

        try:
            result = super().analyze(pcm, status)
            result['evidence_ref'] = augment(result['evidence_ref'])
            return result
        except BaseException:
            # Cancellation may fence evidence writes. Durable meter tickets are
            # retained regardless; a failure never advances the core cursor.
            if context is not None and self.failure_ref is not None:
                self.failure_ref = augment(self.failure_ref)
            raise
