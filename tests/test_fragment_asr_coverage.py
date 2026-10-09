"""Synthetic receipts and fake engines only; no recordings or model loads."""
import copy
from types import SimpleNamespace
import wave

import pytest

from audio_backend.fragment_asr_coverage import build_asr_coverage, CoverageAnalyzerBridge
from audio_backend.fragment_cpu_meter import INPUT_ENCODING, MeteredEngine, _receipt
from audio_backend.fragment_evidence import EvidenceStore
from audio_backend.fragment_model_bridges import CallBudget, NativeBridge, NativeFactory
from audio_backend.fragment_runtime import LogicalRunner
from audio_backend.fragment_runtime_contracts import RATE, RuntimePolicy, StaleWork, digest
from audio_backend.fragment_runtime_store import RuntimeStore
from test_fragment_model_bridges import Guard, Process, budget as native_budget
from test_fragment_runtime import media, plan_for


def binding(start=0, end=160, core=None):
    value = {'run_id': 'run', 'window_id': 'window',
            'window': dict(read_start=start, read_end=end, core_start=(core or [start, end])[0],
                           core_end=(core or [start, end])[1]),
            'pcm_hash': 'a' * 64, 'map_hash': 'b' * 64,
            'mapping': [{'span': {'fragment_id': 'a', 'logical_start': start, 'logical_end': end,
                                 'source_start': 21, 'source_end': 21 + end - start}, 'source_sha256': 'c' * 64}]}
    value['map_hash'] = digest(value['mapping'])
    return value


def record(a, b, *, status='completed', started=True, returned=True, tokens=1, ticket=1):
    receipt = {'sample_rate': RATE, 'sample_count': b - a, 'pcm_sha256': 'd' * 64, 'encoding': INPUT_ENCODING}
    value = {'kind': 'sensevoice', 'ticket_id': ticket, 'status': status,
             'context': {'read_frames': [a, b], 'input': copy.deepcopy(receipt)},
             'execution_started': started, 'execution_returned': returned}
    if started:
        value.update(input=receipt, selection={'read_frames': [a, b]})
    if returned:
        value['output'] = {'raw_token_count': tokens, 'has_text': tokens > 0}
    return value


def test_no_submitted_audio_is_gap_not_silence():
    value = build_asr_coverage(binding(), [])
    assert value['inputs'] == value['submitted_union'] == value['successfully_decoded_union'] == []
    assert value['gaps'] == [[0, 160]]
    assert 'not silence' in value['semantics']['gaps']


def test_context_is_not_ownership_and_exact_source_seam_is_retained():
    value = binding(80, 240, [100, 200])
    value['mapping'] = [
        {'span': dict(fragment_id='a', logical_start=80, logical_end=150, source_start=1000, source_end=1070), 'source_sha256': 'a' * 64},
        {'span': dict(fragment_id='b', logical_start=150, logical_end=240, source_start=31, source_end=121), 'source_sha256': 'b' * 64}]
    value['map_hash'] = digest(value['mapping'])
    result = build_asr_coverage(value, [record(0, 10), record(60, 130), record(130, 160)])
    assert result['submitted_union'] == result['successfully_decoded_union'] == [[140, 200]]
    assert result['gaps'] == [[100, 140]]
    assert result['inputs'][0]['core_intersection_frames'] is None
    middle = result['inputs'][1]
    assert middle['logical_input_frames'] == [140, 210]
    assert middle['source_mapping']['parts'] == [
        {'fragment_id': 'a', 'logical_frames': [140, 150], 'source_frames': [1060, 1070], 'source_artifact_sha256': 'a' * 64},
        {'fragment_id': 'b', 'logical_frames': [150, 210], 'source_frames': [31, 91], 'source_artifact_sha256': 'b' * 64}]
    assert middle['input']['encoding'] == INPUT_ENCODING


def test_overlapping_ctc_inputs_and_duplicate_attempts_are_union_not_sum():
    result = build_asr_coverage(binding(0, 34 * RATE), [record(0, 29 * RATE), record(27 * RATE, 34 * RATE),
                                                     record(0, 29 * RATE, ticket=2)])
    assert len(result['inputs']) == 3
    assert result['successfully_decoded_union'] == [[0, 34 * RATE]]
    assert result['gaps'] == []


def test_reserved_started_failed_and_unacknowledged_return_remain_distinct():
    rows = [record(0, 20, status='reserved', started=False, returned=False),
            record(20, 40, status='reserved', returned=False),
            record(40, 60, status='failed', returned=False),
            record(60, 80, status='reserved'),
            record(100, 120)]
    result = build_asr_coverage(binding(), rows)
    assert [item['status'] for item in result['inputs']] == ['reserved', 'started', 'failed', 'started', 'completed']
    assert result['inputs'][0]['input'] is None and result['inputs'][0]['read_frames'] is None
    assert result['submitted_union'] == [[20, 80], [100, 120]]
    assert result['successfully_decoded_union'] == [[100, 120]]
    assert result['gaps'] == [[0, 100], [120, 160]]


def test_completion_without_new_return_receipt_cannot_claim_success():
    result = build_asr_coverage(binding(), [record(0, 160, returned=False)])
    assert result['submitted_union'] == [[0, 160]]
    assert result['successfully_decoded_union'] == []
    assert result['inputs'][0]['has_text'] is None


def test_empty_raw_tokens_are_still_successful_decode_support():
    result = build_asr_coverage(binding(), [record(0, 160, tokens=0)])
    assert result['successfully_decoded_union'] == [[0, 160]] and result['gaps'] == []
    assert result['inputs'][0]['has_text'] is False


def test_one_frame_eos_and_missed_tail_do_not_follow_output_endpoints():
    end = 30 * RATE + 1
    result = build_asr_coverage(binding(0, end), [record(0, 30 * RATE)])
    assert result['gaps'] == [[30 * RATE, end]]
    end = 30 * RATE
    result = build_asr_coverage(binding(0, end), [record(0, end - 104000)])
    assert result['gaps'] == [[end - 104000, end]]  # synthetic 6.5-second omitted tail


@pytest.mark.parametrize('change', ['float', 'boolean', 'out_of_read', 'input_hash', 'count', 'rate', 'encoding', 'scope', 'return_before_start', 'token_count'])
def test_inconsistent_receipts_fail_closed(change):
    row = record(0, 160)
    if change == 'float': row['selection']['read_frames'][0] = 0.0
    elif change == 'boolean': row['context']['read_frames'][0] = False
    elif change == 'out_of_read': row['selection']['read_frames'][1] = 161
    elif change == 'input_hash': row['input']['pcm_sha256'] = 'e' * 64
    elif change == 'count': row['input']['sample_count'] = 159
    elif change == 'rate': row['input']['sample_rate'] = 48000
    elif change == 'encoding': row['input']['encoding'] = 'PCM16'
    elif change == 'scope': row['scope'] = {'session': 'run', 'window': 'other'}
    elif change == 'return_before_start': row['execution_started'] = False
    else: row['output']['has_text'] = False
    with pytest.raises(ValueError): build_asr_coverage(binding(), [row])


def test_inventory_bounds_are_independent_of_recording_length():
    with pytest.raises(ValueError): build_asr_coverage(binding(), [record(0, 160)] * 33)
    with pytest.raises(ValueError): build_asr_coverage(binding(), [{'kind': 'other'}] * 513)
    with pytest.raises(ValueError): build_asr_coverage(binding(0, 34 * RATE + 1), [])
    result = build_asr_coverage(binding(10**12, 10**12 + 160), [record(0, 160)])
    assert result['successfully_decoded_union'] == [[10**12, 10**12 + 160]]


def test_normalized_read_only_claims_existing_many_to_many_filter_support():
    from audio_backend.fragment_core import Source, SourcePlan, Span
    from audio_backend.fragment_normalized_runtime import NormalizedPlan
    source = Source('source', 'f' * 64, 48000, 1, 48000)
    native = SourcePlan((source,), (Span('source', 0, 48000, 0, 48000, 0),), (), (), True)
    plan = NormalizedPlan(native, RATE)
    value = binding(0, 160)
    value['mapping'] = plan.mapping(0, 160)
    value['map_hash'] = digest(value['mapping'])
    result = build_asr_coverage(value, [record(0, 160)])
    selection = result['inputs'][0]['source_mapping']
    assert selection['status'] == 'canonical_selection_with_native_filter_support'
    assert selection['binding_mapping_indexes'] == [0] and 'parts' not in selection
    assert result['successfully_decoded_union'] == [[0, 160]]


def test_invalid_source_map_hash_cannot_claim_coverage():
    value = binding()
    value['mapping'][0]['span']['source_start'] += 1
    with pytest.raises(ValueError, match='source mapping'): build_asr_coverage(value, [record(0, 160)])


class Ledger(CallBudget):
    def __init__(self):
        super().__init__({kind: {'calls': 1000, 'frames': 100_000_000} for kind in ('analyzer', 'sensevoice')})

    def reserve(self, *args, **kwargs):
        value = super().reserve(*args, **kwargs)
        value.update(status='reserved', ticket_id=len(self.records))
        return value

    def update(self, ticket, **fields):
        ticket.update(copy.deepcopy(fields))


@pytest.mark.parametrize('tokens', [[], ['!'], ['hello']])
def test_meter_uses_raw_tokens_before_projection(tokens):
    import numpy as np
    ledger = Ledger()
    stream = SimpleNamespace(result=SimpleNamespace(tokens=tokens), accept_waveform=lambda *a: None)
    engine = MeteredEngine(SimpleNamespace(create_stream=lambda: stream, decode_stream=lambda raw: None), 'sensevoice',
                          lambda kind, count, receipt: ledger.reserve(kind, count), ledger)
    value = engine.create_stream(); value.accept_waveform(RATE, np.zeros(160, dtype=np.float32))
    engine.decode_stream(value)
    receipt = ledger.records[0]
    assert receipt['status'] == 'completed' and receipt['execution_returned'] is True
    assert receipt['output']['has_text'] is bool(tokens)
    assert receipt['output']['raw_token_count'] == len(tokens)
    assert 'not speech' in receipt['output']['semantics']


def test_failed_engine_retains_started_input_without_return():
    import numpy as np
    ledger = Ledger()
    def fail(raw): raise RuntimeError('synthetic failed decode')
    stream = SimpleNamespace(accept_waveform=lambda *a: None)
    engine = MeteredEngine(SimpleNamespace(create_stream=lambda: stream, decode_stream=fail), 'sensevoice',
                          lambda kind, count, receipt: ledger.reserve(kind, count), ledger)
    value = engine.create_stream(); value.accept_waveform(RATE, np.zeros(160, dtype=np.float32))
    with pytest.raises(RuntimeError): engine.decode_stream(value)
    receipt = ledger.records[0]
    assert receipt['status'] == 'failed' and receipt['execution_started'] is True
    assert not receipt.get('execution_returned')


def test_decoder_return_without_completion_ack_never_fills_gap():
    import numpy as np
    class LostAck(Ledger):
        def complete(self, ticket): raise RuntimeError('completion not acknowledged')
    ledger = LostAck()
    def reserve(kind, frames, receipt):
        ticket = ledger.reserve(kind, frames, context={'read_frames': [0, frames], 'input': receipt})
        ledger.update(ticket, input=receipt, selection={'read_frames': [0, frames]})
        return ticket
    raw = SimpleNamespace(result=SimpleNamespace(tokens=['!']), accept_waveform=lambda *a: None)
    engine = MeteredEngine(SimpleNamespace(create_stream=lambda: raw, decode_stream=lambda raw: None), 'sensevoice', reserve, ledger)
    stream = engine.create_stream(); stream.accept_waveform(RATE, np.zeros(160, dtype=np.float32))
    with pytest.raises(RuntimeError, match='not acknowledged'): engine.decode_stream(stream)
    result = build_asr_coverage(binding(), ledger.records)
    assert result['inputs'][0]['execution_returned'] is True
    assert result['inputs'][0]['has_text'] is True and result['inputs'][0]['status'] == 'failed'
    assert result['submitted_union'] == result['gaps'] == [[0, 160]]
    assert result['successfully_decoded_union'] == []


class ReceiptAnalyzer:
    def __init__(self, ledger, *, fail=False, omit_tail=0):
        self.ledger, self.fail, self.omit_tail = ledger, fail, omit_tail

    def analyze(self, path, **kwargs):
        import numpy as np
        with wave.open(path) as source:
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').astype(np.float32) / 32768.
        stop = len(samples) - min(self.omit_tail, len(samples))
        if stop:
            receipt = _receipt(samples[:stop])[1]
            ticket = self.ledger.reserve('sensevoice', stop, context={'read_frames': [0, stop], 'input': receipt})
            self.ledger.update(ticket, input=receipt, selection={'read_frames': [0, stop]}, execution_started=True)
            if self.fail:
                self.ledger.update(ticket, status='failed')
                raise RuntimeError('synthetic failed analyze')
            self.ledger.update(ticket, execution_returned=True, output={'raw_token_count': 0, 'has_text': False})
            self.ledger.complete(ticket)
        return {'segments': [], 'events': [], 'metadata': {}}

    def close(self): pass


def runner_for(tmp_path, paths, store, evidence, *, fail=False, omit_tail=0, process=Process):
    ledger = Ledger()
    snapshot = store.snapshot('run')
    factory = NativeFactory(lambda: NativeBridge(process, fingerprint='n', expected_provenance={'diarization_fingerprint': 'f'},
        temp_root=tmp_path/'native', verify=lambda: None, budget=native_budget(), guard=Guard(),
        eos_control=snapshot.control['control_id'] if snapshot.control else None, eos_epoch=0, eos_final_frame=snapshot.plan.end_frame))
    analyzer = CoverageAnalyzerBridge(ReceiptAnalyzer(ledger, fail=fail, omit_tail=omit_tail), fingerprint='a',
        evidence_store=evidence, temp_root=tmp_path/'tmp', verify=lambda: None, budget=ledger,
        guard=Guard(), provenance={'test': 'synthetic'}, source_bindings={'source': 'synthetic'})
    return LogicalRunner(store, 'run', paths, analyzer, factory), analyzer, factory


def create(store, source, *, final=True):
    return store.create('run', plan_for([source], final=final), RuntimePolicy(native_fingerprint='n', analyzer_fingerprint='a'))


def test_failure_raw_evidence_keeps_meter_attempt_but_does_not_commit(tmp_path, media):
    _, _, paths, sources = media
    with EvidenceStore(tmp_path/'evidence') as evidence:
        store = RuntimeStore(tmp_path/'ledger.sqlite', evidence_store=evidence)
        create(store, sources['short'])
        runner, analyzer, factory = runner_for(tmp_path, paths, store, evidence, fail=True)
        with pytest.raises(RuntimeError, match='synthetic failed'): runner.step()
        saved = evidence.read(analyzer.failure_ref)
        coverage = saved['asr_input_coverage']
        assert coverage['submitted_union'] == [[0, sources['short'].frames]]
        assert coverage['successfully_decoded_union'] == []
        assert any(row['status'] == 'failed' for row in saved['model_call_records'])
        assert store.snapshot('run').next_frame == 0 and store.commits('run') == []
        factory.close()


@pytest.mark.parametrize('point', ['core.after_output', 'core.after_cursor', 'core.before_commit', 'core.after_commit'])
def test_coverage_reference_cursor_and_eos_commit_atomically_and_resume(tmp_path, media, point):
    _, _, paths, sources = media
    armed = [True]
    def fault(name):
        if name == point and armed:
            armed.pop()
            raise RuntimeError('synthetic transaction fault')
    with EvidenceStore(tmp_path/'evidence') as evidence:
        store = RuntimeStore(tmp_path/'ledger.sqlite', evidence_store=evidence, fault=fault)
        create(store, sources['short'])
        runner, analyzer, factory = runner_for(tmp_path, paths, store, evidence, omit_tail=1)
        with pytest.raises(RuntimeError, match='transaction fault'): runner.step()
        committed = point == 'core.after_commit'
        assert len(store.commits('run')) == int(committed)
        assert store.snapshot('run').next_frame == (sources['short'].frames if committed else 0)
        factory.close()
        reopened = RuntimeStore(store.path, evidence_store=evidence)
        if not committed:
            runner, analyzer, factory = runner_for(tmp_path, paths, reopened, evidence, omit_tail=1)
            assert runner.step() == 'completed'
            factory.close()
        payload = reopened.commits('run')[0]
        coverage = evidence.read(payload['evidence_ref'])['asr_input_coverage']
        assert coverage['gaps'] == [[sources['short'].frames - 1, sources['short'].frames]]
        assert len(coverage['inputs']) == 1  # prior orphan attempts are not a successful core's history
        snapshot = reopened.snapshot('run')
        assert snapshot.status == 'completed' and snapshot.control['acknowledged']
        again, analyzer, factory = runner_for(tmp_path, paths, reopened, evidence)
        assert again.step() == 'completed' and not analyzer.budget.records
        assert len(reopened.commits('run')) == 1
        factory.close()


def test_cancellation_after_coverage_before_commit_keeps_attempt_no_cursor(tmp_path, media):
    _, _, paths, sources = media
    with EvidenceStore(tmp_path/'evidence') as evidence:
        store = RuntimeStore(tmp_path/'ledger.sqlite', evidence_store=evidence)
        create(store, sources['short'])
        runner, analyzer, factory = runner_for(tmp_path, paths, store, evidence)
        finalize = analyzer.finalize_evidence
        refs = []
        def cancel(payload, reconciliation):
            ref = finalize(payload, reconciliation)
            refs.append(ref)
            store.cancel('run')
            return ref
        analyzer.finalize_evidence = cancel
        with pytest.raises(StaleWork): runner.step()
        assert evidence.read(refs[0])['asr_input_coverage']['successfully_decoded_union'] == [[0, sources['short'].frames]]
        assert any(row['kind'] == 'sensevoice' and row['status'] == 'completed' for row in analyzer.budget.records)
        assert store.snapshot('run').next_frame == 0 and store.commits('run') == []
        assert not store.snapshot('run').control['acknowledged']
        factory.close()


def test_final_one_sample_core_cannot_borrow_prior_context_coverage(tmp_path, media):
    _, _, paths, sources = media
    with EvidenceStore(tmp_path/'evidence') as evidence:
        store = RuntimeStore(tmp_path/'ledger.sqlite', evidence_store=evidence)
        create(store, sources['tail'])
        runner, analyzer, factory = runner_for(tmp_path, paths, store, evidence, omit_tail=1)
        assert runner.step() == 'committed'
        assert runner.step() == 'completed'
        payloads = store.commits('run')
        assert len(payloads) == 2
        first, last = [evidence.read(p['evidence_ref'])['asr_input_coverage'] for p in payloads]
        assert first['gaps'] == []
        assert last['core_frames'] == [30 * RATE, 30 * RATE + 1]
        assert last['gaps'] == [[30 * RATE, 30 * RATE + 1]]
        assert last['submitted_union'] == last['successfully_decoded_union'] == []
        assert len(last['inputs']) == 1 and last['inputs'][0]['core_intersection_frames'] is None
        factory.close()


def test_zero_audio_eos_ack_does_not_invent_another_coverage_attempt(tmp_path, media):
    class StableProcess(Process):
        def state(self):
            state = super().state()
            state['stable_seconds'] = state['input_seconds']
            return state
    _, _, paths, sources = media
    with EvidenceStore(tmp_path/'evidence') as evidence:
        store = RuntimeStore(tmp_path/'ledger.sqlite', evidence_store=evidence)
        live = plan_for([sources['grid']], final=False)
        store.create('run', live, RuntimePolicy(context_frames=0, native_fingerprint='n', analyzer_fingerprint='a'))
        runner, analyzer, factory = runner_for(tmp_path, paths, store, evidence, process=StableProcess)
        assert runner.step() == 'committed'
        assert runner.step() == 'waiting'
        before = store.commits('run')[0]['evidence_ref']
        factory.close()
        store.append('run', plan_for([sources['grid']]), expected_revision=1, operation_id='eos')
        runner, analyzer, factory = runner_for(tmp_path, paths, store, evidence, process=StableProcess)
        assert runner.step() == 'completed'
        assert not analyzer.budget.records
        assert store.commits('run')[0]['evidence_ref'] == before
        assert len(store.commits('run')) == 1
        assert evidence.read(before)['asr_input_coverage']['successfully_decoded_union'] == [[0, 30 * RATE]]
        factory.close()
