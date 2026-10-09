"""Internal actual-provider adapters; importing this module loads no neural model.

NativeProcess owns the native ABI child. AudioAnalyzer keeps its full result in
private evidence; the strict logical envelope is only a reviewable projection.
"""
from dataclasses import asdict
import copy
import hashlib
import math
from pathlib import Path
import tempfile
import time
import wave

from .fragment_runtime_contracts import (
    CONTRACT, MAX_ITEMS, RATE, NativeStatus, bounded_json, digest, integer, window_id,
)
from .fragment_evidence import MAX_EVIDENCE_BYTES

TIMING = 'SenseVoice native CTC starts; estimated ends; Latin pieces may form lexical words; not forced alignment'


def frame(seconds):
    """Convert a local provider endpoint exactly once; at most 1ns float drift."""
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Nonfinite/negative native seconds')
    value = round(seconds * RATE)
    if abs(value / RATE - seconds) > 1e-9:
        raise ValueError('Native endpoint is not on the integer 16 kHz frame grid')
    return value


def check_pcm(pcm):
    integer(pcm.start, 'PCM start')
    integer(pcm.end, 'PCM end')
    if (pcm.contract != CONTRACT or not 0 < pcm.end - pcm.start <= 60 * RATE
        or not isinstance(pcm.pcm, bytes) or len(pcm.pcm) != 2 * (pcm.end-pcm.start)
        or hashlib.sha256(pcm.pcm).hexdigest() != pcm.pcm_sha256
        or digest(pcm.mapping) != pcm.map_sha256 or not 0 < len(pcm.mapping) <= 128):
        raise ValueError('PCM window bytes, contract, bound or map hash disagree')
    if pcm.mapping[0].get('kind') == 'native-filter-support-v1':
        from .fragment_normalized_runtime import validate_support
        validate_support(pcm.mapping, pcm.start, pcm.end)
        return
    cursor = pcm.start
    for item in pcm.mapping:
        span = item['span']
        if (span['logical_start'] != cursor or span['source_end'] - span['source_start']
            != span['logical_end'] - span['logical_start']):
            raise ValueError('Noncontiguous/inconsistent PCM source mapping')
        cursor = span['logical_end']
    if cursor != pcm.end:
        raise ValueError('PCM source map does not cover the complete read')


def write_pcm(path, pcm, reserve=lambda size: None):
    check_pcm(pcm)
    reserve(len(pcm.pcm) + 44)
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(pcm.pcm)


class CallBudget:
    """Attempts consume reservations before execution; failures never refund."""
    def __init__(self, limits):
        self.limits = copy.deepcopy(limits)
        self.used = {name: {'calls': 0, 'frames': 0} for name in limits}
        self.records = []

    def reserve(self, name, frames=0, *, calls=1, context=None):
        integer(frames, 'budget frames')
        integer(calls, 'budget calls', 1)
        if name not in self.limits:
            raise ValueError(f'Unapproved model/call category: {name}')
        used, limit = self.used[name], self.limits[name]
        if used['calls']+calls > limit['calls'] or used['frames']+frames > limit['frames']:
            raise ValueError(f'Call budget exhausted before {name} attempt')
        used['calls'] += calls
        used['frames'] += frames
        entry = {'kind': name, 'calls': calls, 'frames': frames, 'status': 'attempted', 'context': context}
        if len(self.records) >= 1024:
            raise ValueError('Call record bound exceeded')
        self.records.append(entry)
        return entry

    @staticmethod
    def complete(entry):
        entry['status'] = 'completed'


class NativeBridge:
    def __init__(self, process_factory, *, fingerprint, expected_provenance, temp_root,
                 verify, budget, guard, eos_control=None, eos_epoch=None, eos_final_frame=None, failure_sink=None, expected_imports=None):
        self.process_factory = process_factory
        self.fingerprint = fingerprint
        self.expected_provenance = copy.deepcopy(expected_provenance)
        self.root = Path(temp_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.verify, self.budget, self.guard = verify, budget, guard
        self.eos_control, self.eos_epoch, self.eos_final_frame = eos_control, eos_epoch, eos_final_frame
        self.expected_imports = copy.deepcopy(expected_imports)
        self.failure_sink = failure_sink or (lambda value: None)
        self.process = None
        self.calls = []
        self._status = None
        self._finish_control = None
        self.provenance = {}

    def close(self):
        if self.process is not None:
            process, self.process = self.process, None
            process.close()

    def _request(self, action, **kwargs):
        self.guard.check()
        self.verify()
        began = time.monotonic()
        request = {'action': action, **kwargs}
        # Preserve the actual NativeProcess wire path/counter as well as args.
        if hasattr(self.process, 'counter') and hasattr(self.process, 'root'):
            request['output'] = str(self.process.root / f'{self.process.counter+1}.json')
        entry = {'request': request}
        self.calls.append(entry)
        def stop():
            self.guard.check()
            if time.monotonic() - began > 300:
                raise TimeoutError('Native request exceeded approved 300s deadline')
            return False
        try:
            self.process.response_observer = lambda value: entry.update(response=copy.deepcopy(value))
            response = self.process.request(action, stop, **kwargs)
            entry['response'] = copy.deepcopy(response)
            if self.expected_imports is not None:
                attest = response.get('implementation_attestation')
                if not isinstance(attest, dict) or set(attest) != {'interpreter', 'modules'} or not attest['modules']:
                    raise ValueError('Native process lacks actual imported-wrapper/interpreter attestation')
                for imported in [attest['interpreter'], *attest['modules'].values()]:
                    expected = self.expected_imports.get(imported.get('path'))
                    if expected is None or {k: imported.get(k) for k in ('sha256', 'bytes')} != expected:
                        raise ValueError('Native actual imported file differs from frozen closure')
            bounded_json(self.calls, MAX_EVIDENCE_BYTES)
            self.verify()
            self.guard.check()
            return response
        except BaseException as exc:
            entry['failure'] = {'type': type(exc).__name__, 'message': str(exc)[:2000]}
            try:
                self.failure_sink({'native_calls': self.calls, 'kind': 'native_failure'})
            finally:
                self.close()
            raise

    def reset(self, key, replay_start):
        self.close()
        integer(replay_start, 'replay origin')
        if len(key) != 5 or key[3] != self.fingerprint or key[4] != CONTRACT:
            raise ValueError('Native reset key does not bind the frozen provider')
        self.key, self.replay_start, self.pushed_until = tuple(key), replay_start, replay_start
        self.epoch_id = digest(list(key))
        self.calls, self._finish_control = [], None
        self.verify()
        self.guard.check()
        ticket = self.budget.reserve('native_reset', context=self.epoch_id)
        self.process = self.process_factory()
        try:
            response = self._request('reset', epoch_id=self.epoch_id)
            self.provenance = copy.deepcopy(response['provenance'])
            if self.provenance != self.expected_provenance:
                raise ValueError('Actual native reset provenance differs from frozen closure/config')
            self._accept(response['state'], replay_start, query=False)
            self.budget.complete(ticket)
        except BaseException as exc:
            self.failure_sink({'kind': 'native_validation_failure', 'native_calls': self.calls, 'error': str(exc)[:2000]})
            self.close()
            raise

    def _accept(self, state, expected_end, *, query=True):
        if (state.get('epoch_id') != self.epoch_id or state.get('fingerprint') != self.provenance.get('diarization_fingerprint')
            or state.get('local_capacity') != 8 or type(state.get('finished')) is not bool):
            raise ValueError('Native epoch, capacity, terminal flag or fingerprint mismatch')
        consumed = self.replay_start + frame(state['input_seconds'])
        activity = self.replay_start + frame(state['processed_seconds'])
        stable = self.replay_start + frame(state['stable_seconds'])
        if consumed != expected_end:
            raise ValueError('Native input acknowledgement differs from exact pushed PCM frames')
        projected = []
        if query and activity > self.replay_start:
            ticket = self.budget.reserve('native_turns')
            result = self._request('turns', start=0., end=(activity-self.replay_start)/RATE)
            turns = result['turns']
            if not isinstance(turns, list) or len(turns) > MAX_ITEMS:
                raise ValueError('Native turns exceed the logical 4096-item bound; no truncation')
            for turn in turns:
                sid = turn['speaker_local_id']
                if not isinstance(sid, str) or not sid.startswith(self.epoch_id+':speaker_'):
                    raise ValueError('Native turn speaker is not scoped to this epoch')
                a, b = self.replay_start+frame(turn['start']), self.replay_start+frame(turn['end'])
                projected.append({'start_frame': a, 'end_frame': b, 'speaker': sid,
                                  'instance_id': digest([self.epoch_id, sid, a, b])})
            self.budget.complete(ticket)
        result = NativeStatus(consumed, stable, activity, state['finished'], tuple(projected))
        result.validate(expected_end, self.replay_start)
        self._status, self.pushed_until = result, expected_end

    def push(self, pcm):
        check_pcm(pcm)
        if self._status is None or self._status.finished or pcm.start != self.pushed_until:
            raise ValueError('Native push is not contiguous or instance is terminal')
        if pcm.end - self.replay_start > 360*RATE:
            raise ValueError('Native replay exceeds 360 seconds')
        ticket = self.budget.reserve('native_push', pcm.end-pcm.start)
        with tempfile.TemporaryDirectory(prefix='native-pcm-', dir=self.root) as directory:
            path = Path(directory)/'input.wav'
            write_pcm(path, pcm, self.guard.check)
            response = self._request('push', wav=str(path))
            self.calls[-1]['pcm'] = {'start': pcm.start, 'end': pcm.end, 'sha256': pcm.pcm_sha256,
                                    'mapping': pcm.mapping, 'map_sha256': pcm.map_sha256}
            try:
                self._accept(response, pcm.end)
                self.budget.complete(ticket)
            except BaseException as exc:
                self.failure_sink({'kind': 'native_validation_failure', 'native_calls': self.calls, 'error': str(exc)[:2000]})
                self.close()
                raise

    def status(self):
        if self.process is None or self._status is None:
            raise RuntimeError('Native instance is not live')
        self.verify()
        return self._status

    def finish(self, control_id):
        if (control_id != self.eos_control or self.key[2] != self.eos_epoch
            or self.eos_final_frame is None or self.pushed_until != self.eos_final_frame):
            raise ValueError('Native finish lacks matching true-EOS intent')
        if self._finish_control is not None:
            if control_id != self._finish_control:
                raise ValueError('Native finish control changed')
            return self.status()
        ticket = self.budget.reserve('native_finish')
        try:
            response = self._request('finish')
            self._accept(response, self.pushed_until)
            if not self._status.finished:
                raise ValueError('Native finish did not confirm terminal state')
            self._finish_control = control_id
            self.budget.complete(ticket)
            return self.status()
        except BaseException as exc:
            self.failure_sink({'kind': 'native_validation_failure', 'native_calls': self.calls, 'error': str(exc)[:2000]})
            self.close()
            raise

    def evidence(self):
        return {'key': list(self.key), 'replay_start': self.replay_start,
                'status': asdict(self.status()), 'provenance': copy.deepcopy(self.provenance),
                'calls': copy.deepcopy(self.calls)}


class NativeFactory:
    """Own live children even when LogicalRunner discards a failed reference."""
    def __init__(self, factory):
        self.factory, self.current = factory, None

    def __call__(self):
        self.close()
        self.current = self.factory()
        return self.current

    def close(self):
        if self.current is not None:
            current, self.current = self.current, None
            current.close()


class AnalyzerBridge:
    def __init__(self, analyzer, *, fingerprint, evidence_store, temp_root, verify,
                 budget, guard, provenance, source_bindings):
        self.analyzer, self.fingerprint = analyzer, fingerprint
        self.evidence_store, self.root = evidence_store, Path(temp_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.verify, self.budget, self.guard = verify, budget, guard
        self.provenance, self.source_bindings = copy.deepcopy(provenance), copy.deepcopy(source_bindings)
        self.context, self.failure_ref = None, None

    def bind_context(self, snapshot, window, native):
        from .fragment_runtime_contracts import next_window
        if window != next_window(snapshot.plan, snapshot.next_frame, snapshot.policy):
            raise ValueError('Analyzer context does not match immutable scheduler snapshot')
        self.context = (snapshot, window, native)

    def close(self):
        self.analyzer.close()

    def analyze(self, pcm, status):
        check_pcm(pcm)
        if self.context is None:
            raise ValueError('Analyzer requires explicit immutable core/read context')
        snapshot, window, native = self.context
        self.context = None
        if (pcm.start, pcm.end) != (window.read_start, window.read_end) or status != native.status():
            raise ValueError('Analyzer PCM/native snapshot differs from bound call context')
        self.verify()
        self.guard.check()
        turns = []
        for turn in status.turns:
            a, b = max(pcm.start, turn['start_frame']), min(pcm.end, turn['end_frame'])
            if a < b:
                turns.append({'start': (a-pcm.start)/RATE, 'end': (b-pcm.start)/RATE,
                              'speaker_local_id': turn['speaker']})
        diarization = {'turns': turns, 'provenance': copy.deepcopy(native.provenance)}
        options = {'candidate_core_start': (window.core_start-pcm.start)/RATE,
                   'candidate_core_end': (window.core_end-pcm.start)/RATE,
                   'candidate_core_apply_safe_cut': False}
        binding = {'run_id': snapshot.run_id, 'window_id': window_id(snapshot.run_id, window, snapshot.policy),
                   'window': asdict(window), 'revision': snapshot.revision, 'plan_hash': snapshot.plan.sha256,
                   'contract': CONTRACT, 'policy_hash': snapshot.policy.sha256, 'mapping': pcm.mapping,
                   'map_hash': pcm.map_sha256, 'pcm_hash': pcm.pcm_sha256}
        evidence = {'binding': binding, 'projection_version': 'real-provider-projection-v1',
                    'provider_binding': self.provenance, 'original_transport_bindings': self.source_bindings,
                    'native': native.evidence(), 'analyzer_request': {'diarization': diarization, 'analysis_options': options},
                    'timing_semantics': TIMING, 'event_timing': 'window_support',
                    'analysis_source_track': 'mixture (not a speaker or person)'}
        record_start = len(self.budget.records)
        ticket = self.budget.reserve('analyzer', pcm.end-pcm.start, context=binding['window_id'])
        try:
            with tempfile.TemporaryDirectory(prefix='analyzer-pcm-', dir=self.root) as directory:
                path = Path(directory)/'read.wav'
                write_pcm(path, pcm, self.guard.check)
                evidence['analyzer_request']['wav'] = str(path)
                raw = self.analyzer.analyze(str(path), diarization=diarization, analysis_options=options)
            evidence['raw_analyzer_result'] = copy.deepcopy(raw)
            evidence['model_call_records'] = copy.deepcopy(self.budget.records[record_start:])
            evidence['global_budget_used'] = copy.deepcopy(self.budget.used)
            # A schema/projection error must never discard a valid raw response.
            self.failure_ref = self.evidence_store.put(evidence)
            evidence['actual_parent_imports'] = self.verify()
            self.guard.check()
            result, index = project_analyzer(raw, pcm, binding['window_id'])
            evidence['projection_index'] = index
            evidence['embedding_inputs'] = embedding_input_maps(self.budget.records[record_start:], pcm)
            self.budget.complete(ticket)
            evidence['model_call_records'] = copy.deepcopy(self.budget.records[record_start:])
            evidence['global_budget_used'] = copy.deepcopy(self.budget.used)
            result['evidence_ref'] = self.evidence_store.put(evidence)
            return result
        except BaseException as exc:
            evidence['failure'] = {'type': type(exc).__name__, 'message': str(exc)[:2000]}
            self.failure_ref = self.evidence_store.put(evidence)
            raise


    def finalize_evidence(self, payload, reconciliation):
        value = self.evidence_store.read(payload['evidence_ref'])
        self.evidence_store.verify_core(payload['evidence_ref'], payload, value['binding']['run_id'])
        value['publication_projection'] = {key: copy.deepcopy(payload[key]) for key in ('segments', 'turns', 'events', 'tail')}
        value['lexical_reconciliation'] = copy.deepcopy(reconciliation)
        base = payload['window']['read_start']/RATE
        source = value['raw_analyzer_result']['segments']
        index = []
        for pi, published in enumerate(payload['segments']):
            candidates = []
            for si, segment in enumerate(source):
                for ui, unit in enumerate(segment['_units']):
                    if (unit['start']+base == published['start'] and unit['end']+base == published['end']
                        and unit['ownership_anchor']+base == published['ownership_anchor']
                        and unit['text'].strip() == published['text']):
                        candidates.append({'segment': si, 'unit': ui})
            if not candidates:
                raise ValueError('Published lexical unit has no original raw native support/anchor')
            index.append({'published_segment': pi, 'raw_candidates': candidates,
                          'index_identity': 'unique' if len(candidates) == 1 else 'identical_raw_units_ambiguous',
                          'native_anchor': published['ownership_anchor'], 'routing_anchor': published['routing_anchor']})
        value['publication_unit_index'] = index
        return self.evidence_store.put(value)


def project_analyzer(raw, pcm, call_id):
    from .fragment_runtime import _validate_analyzer
    if not isinstance(raw, dict) or not isinstance(raw.get('segments'), list) or not isinstance(raw.get('events'), list):
        raise ValueError('Actual analyzer omitted segments/events')
    result, index = {'segments': [], 'events': []}, {'units': [], 'events': []}
    for si, segment in enumerate(raw['segments']):
        units = segment.get('_units')
        if not isinstance(units, list) or not units:
            raise ValueError('Analyzer segment lacks supported native units')
        out = {'source_track_id': 'mixture', '_units': []}
        for ui, unit in enumerate(units):
            # Unit_kind is a storage category only. CTC end estimates and all
            # source confidence/anchor fields remain literally in raw evidence.
            text = unit.get('text')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('Unsupported empty/whitespace lexical unit; raw evidence retained')
            projected = {key: unit[key] for key in ('start', 'end', 'ownership_anchor', 'text')}
            projected['unit_kind'] = 'native_word'
            out['_units'].append(projected)
            index['units'].append({'segment': si, 'unit': ui, 'projected_segment': len(result['segments']),
                                   'projected_unit': len(out['_units'])-1,
                                   'speaker_local_id': segment.get('speaker_local_id'),
                                   'speaker_assignment': 'uncertain' if segment.get('speaker_local_id') is None else 'epoch_local_only',
                                   'confidence': unit.get('confidence'), 'timestamp_semantics': TIMING})
        result['segments'].append(out)
    for ei, event in enumerate(raw['events']):
        a, b = pcm.start+frame(event['start']), pcm.start+frame(event['end'])
        result['events'].append({'start_frame': a, 'end_frame': b, 'event': event['label'],
                                 'instance_id': digest([call_id, 'event', ei, event])})
        index['events'].append({'raw_event': ei, 'projected_event': ei, 'source_label': event.get('source_label'),
                                'score': event.get('score'), 'time_precision': event.get('time_precision', 'window_support'),
                                'instance_semantics': 'model evidence, not a proven occurrence'})
    _validate_analyzer(result, pcm)
    return result, index


def embedding_input_maps(records, pcm):
    """Project captured exact selections through this logical read's owner map."""
    result = []
    for record in records:
        if record['kind'] != 'embedding':
            continue
        selection = record.get('selection')
        receipt = record.get('input')
        item = {'input': copy.deepcopy(receipt), 'status': record['status'], 'selection': copy.deepcopy(selection)}
        if not selection or not receipt or not selection['support'].get('complete'):
            item['source_mapping_status'] = 'unavailable_incomplete_selection'
            result.append(item)
            continue
        support = selection['support']
        if (support['sample_rate'] != RATE or support['analyzer_frame_count'] != pcm.end-pcm.start
            or support['pooled_sample_count'] != receipt['sample_count']
            or support['pooled_pcm_sha256'] != receipt['pcm_sha256']):
            raise ValueError('Embedding selected support differs from actual logical read/compute input')
        if pcm.mapping[0].get('kind') == 'native-filter-support-v1':
            # Keep actual float32 pooled-input receipts literally. Native support
            # is many-to-many filter context, never exact source sample selection.
            check_pcm(pcm)
            selected = []
            count = 0
            for left, right in support['intervals_samples']:
                a, b = pcm.start+left, pcm.start+right
                if type(left) is not int or type(right) is not int or not pcm.start <= a < b <= pcm.end:
                    raise ValueError('Embedding selection leaves canonical read')
                selected.append({'canonical_frames': [a, b], 'support_chunk_indexes': [
                    i for i, part in enumerate(pcm.mapping)
                    if part['canonical_start'] < b and part['canonical_end'] > a]})
                count += b-a
            if count != receipt['sample_count']:
                raise ValueError('Embedding canonical selection count differs from actual pool')
            item.update(source_mapping_status='canonical_selection_with_native_filter_support',
                        canonical_selection=selected, native_filter_support=copy.deepcopy(pcm.mapping),
                        read_pcm_sha256=pcm.pcm_sha256)
            result.append(item)
            continue
        mappings = []
        count = 0
        for left, right in support['intervals_samples']:
            a, b = pcm.start+left, pcm.start+right
            if not pcm.start <= a < b <= pcm.end:
                raise ValueError('Embedding selection leaves logical read')
            for part in pcm.mapping:
                span = part['span']
                x, y = max(a, span['logical_start']), min(b, span['logical_end'])
                if x < y:
                    mappings.append({'logical_frames': [x, y], 'fragment_id': span['fragment_id'],
                        'source_frames': [span['source_start']+x-span['logical_start'],
                                          span['source_start']+y-span['logical_start']],
                        'source_sha256': part['source_sha256']})
                    count += y-x
        if count != receipt['sample_count']:
            raise ValueError('Embedding source map does not cover the complete actual pool')
        item.update(source_mapping_status='exact_complete', source_mapping=mappings, read_pcm_sha256=pcm.pcm_sha256)
        result.append(item)
    return result
