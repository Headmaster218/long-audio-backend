"""Synthetic contract/fault tests only. Never loads a real neural model."""
from dataclasses import asdict, replace
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from audio_backend.fragment_evidence import EvidenceStore, MAX_EVIDENCE_BYTES
from audio_backend.fragment_model_bridges import (
    AnalyzerBridge, CallBudget, NativeBridge, NativeFactory, check_pcm, frame, project_analyzer,
)
from audio_backend.fragment_model_guard import Identity, ResourceGuard, ResourceLimit, descendants, supervise
from audio_backend.fragment_model_meter import MeteredEngine
from audio_backend.fragment_runtime import LogicalPCMReader, LogicalRunner
from audio_backend.fragment_runtime_contracts import (
    CONTRACT, NativeStatus, RuntimePolicy, CorruptLedger, digest, native_key, window_id,
)
from audio_backend.fragment_runtime_store import RuntimeStore
from test_fragment_runtime import media, plan_for, FakeNative, FakeAnalyzer


class Guard:
    def check(self, *args):
        pass


def budget():
    return CallBudget({kind: {'calls': 100, 'frames': 10_000_000} for kind in (
        'native_reset', 'native_push', 'native_finish', 'native_turns', 'analyzer', 'sensevoice')})


class Process:
    instances = []

    def __init__(self):
        self.closed = False
        self.calls = []
        self.instances.append(self)

    def request(self, action, stop, **kwargs):
        stop()
        self.calls.append((action, kwargs))
        if action == 'reset':
            self.epoch = kwargs['epoch_id']
            self.frames = 0
            self.finished = False
            return {'state': self.state(), 'provenance': {'diarization_fingerprint': 'f'}}
        if action == 'push':
            import wave
            with wave.open(kwargs['wav']) as wav:
                self.frames += wav.getnframes()
            return self.state()
        if action == 'finish':
            self.finished = True
            return self.state()
        if action == 'turns':
            return {'turns': [{'start': 0., 'end': kwargs['end'], 'speaker_local_id': self.epoch+':speaker_1'},
                              {'start': 0., 'end': kwargs['end'], 'speaker_local_id': self.epoch+':speaker_2'}]}
        raise AssertionError(action)

    def state(self):
        return {'epoch_id': self.epoch, 'input_seconds': self.frames/16000,
                'processed_seconds': (self.frames//160)*.01,
                'stable_seconds': self.frames/16000 if self.finished else max(0., (self.frames//160)*.01-2),
                'finished': self.finished, 'fingerprint': 'f', 'local_capacity': 8}

    def close(self):
        self.closed = True


def native(tmp_path, factory=Process, **kwargs):
    return NativeBridge(factory, fingerprint='n', expected_provenance={'diarization_fingerprint': 'f'},
                        temp_root=tmp_path, verify=lambda: None, budget=budget(), guard=Guard(),
                        eos_control='eos', eos_epoch=0, eos_final_frame=480001, **kwargs)


def test_native_exact_final_sample_activity_and_simultaneous_tracks(tmp_path, media):
    _, _, paths, sources = media
    plan = plan_for([sources['tail']])
    pcm = LogicalPCMReader(plan, paths).read(0, plan.end_frame)
    provider = native(tmp_path)
    provider.reset(('run', 0, 0, 'n', CONTRACT), 0)
    provider.push(pcm)
    finished = provider.finish('eos')
    assert finished.consumed_until == finished.stable_until == 480001
    assert finished.activity_until == 480000
    assert len(finished.turns) == 2
    assert {x['end_frame'] for x in finished.turns} == {480000}
    assert provider.finish('eos') is finished
    assert [x['request']['action'] for x in provider.evidence()['calls']] == ['reset', 'push', 'turns', 'finish', 'turns']
    provider.close()


def test_native_nonzero_origin_never_rebased_through_float(tmp_path, media):
    _, _, paths, sources = media
    p = LogicalPCMReader(plan_for([sources['whole']]), paths).read(100, 160100)
    provider = native(tmp_path)
    provider.reset(('run', 0, 0, 'n', CONTRACT), 100)
    provider.push(p)
    assert provider.status().consumed_until == 160100
    assert provider.status().turns[0]['start_frame'] == 100
    provider.close()


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, True, .00001])
def test_bad_native_seconds(value):
    with pytest.raises(ValueError):
        frame(value)


def test_native_receipt_mismatch_closes_owned_child(tmp_path, media):
    class Wrong(Process):
        def state(self):
            value = super().state()
            if self.frames:
                value['input_seconds'] += 1/16000
            return value
    _, _, paths, sources = media
    p = LogicalPCMReader(plan_for([sources['short']]), paths).read(0, sources['short'].frames)
    provider = native(tmp_path, Wrong)
    provider.reset(('run', 0, 0, 'n', CONTRACT), 0)
    child = provider.process
    with pytest.raises(ValueError, match='acknowledgement'):
        provider.push(p)
    assert child.closed


def test_native_finish_requires_correct_control(tmp_path):
    provider = native(tmp_path)
    provider.reset(('run', 0, 0, 'n', CONTRACT), 0)
    with pytest.raises(ValueError, match='true-EOS'):
        provider.finish('other')
    provider.close()


def test_native_factory_closes_previous_on_rollover():
    made = []
    def create():
        obj = Process()
        made.append(obj)
        return obj
    factory = NativeFactory(create)
    factory()
    factory()
    assert made[0].closed and not made[1].closed
    factory.close()
    assert made[1].closed


def raw_result():
    return {'segments': [{'speaker_local_id': None, 'confidence': None, 'raw_text': 'Hello!',
                          'speaker_embedding': [0.1, 0.2],
                          '_units': [{'start': 0., 'end': .04, 'ownership_anchor': 0., 'text': 'Hello!',
                                      'raw_text': 'Hello!', 'confidence': None, 'anchor_kind': 'ctc_emission'}]}],
            'events': [{'start': 0., 'end': .1, 'label': 'door', 'source_label': 'Knock', 'score': .78}],
            'metadata': {'event_candidates': [{'top_labels': [{'label': 'Laughter', 'score': .3}]}]},
            'overlap_candidates': {'failure': 'unresolved raw example'}}


class Analyzer:
    def analyze(self, path, **kwargs):
        self.arguments = kwargs
        return raw_result()

    def close(self):
        pass


def test_raw_fields_confidence_speakers_events_survive_committed_evidence(tmp_path, media):
    _, _, paths, sources = media
    plan = plan_for([sources['short']])
    with EvidenceStore(tmp_path/'evidence') as evidence:
        store = RuntimeStore(tmp_path/'ledger.sqlite', evidence_store=evidence)
        store.create('run', plan, RuntimePolicy(native_fingerprint='n', analyzer_fingerprint='a'))
        factory = NativeFactory(
            lambda: NativeBridge(Process, fingerprint='n', expected_provenance={'diarization_fingerprint': 'f'},
                                 temp_root=tmp_path/'native', verify=lambda: None, budget=budget(), guard=Guard(),
                                 eos_control=store.snapshot('run').control['control_id'], eos_epoch=0, eos_final_frame=plan.end_frame))
        analyzer = AnalyzerBridge(Analyzer(), fingerprint='a', evidence_store=evidence, temp_root=tmp_path/'tmp',
                                  verify=lambda: None, budget=budget(), guard=Guard(), provenance={'test': 'fake'},
                                  source_bindings={'source': 'synthetic'})
        runner = LogicalRunner(store, 'run', paths, analyzer, factory)
        assert runner.step() == 'completed'
        payload = store.commits('run')[0]
        saved = evidence.read(payload['evidence_ref'])
        assert saved['raw_analyzer_result'] == raw_result()
        assert saved['projection_index']['units'][0]['speaker_local_id'] is None
        assert saved['projection_index']['events'][0]['source_label'] == 'Knock'
        assert saved['raw_analyzer_result']['overlap_candidates']['failure']
        assert saved['binding']['window'] == payload['window']
        assert payload['segments'][0]['text'] == 'Hello!'
        # File corruption on resume fails before another provider can run.
        path = evidence.root/(payload['evidence_ref']['sha256']+'.json')
        path.write_text('{}')
        with pytest.raises(CorruptLedger):
            store.snapshot('run')
        factory.close()


def test_projection_rejects_whitespace_but_preserves_punctuation(media):
    _, _, paths, sources = media
    pcm = LogicalPCMReader(plan_for([sources['short']]), paths).read(0, sources['short'].frames)
    raw = raw_result()
    raw['segments'][0]['_units'][0]['text'] = '!'
    assert project_analyzer(raw, pcm, 'call')[0]['segments'][0]['_units'][0]['text'] == '!'
    raw['segments'][0]['_units'][0]['text'] = '  '
    with pytest.raises(ValueError, match='whitespace'):
        project_analyzer(raw, pcm, 'call')


def test_evidence_fsync_fault_orphan_and_exclusive_lease(tmp_path):
    with EvidenceStore(tmp_path/'evidence') as evidence:
        ref = evidence.put({'full': 'raw'})
        with pytest.raises(RuntimeError, match='leased'):
            EvidenceStore(evidence.root)
        with pytest.raises(ValueError):
            evidence.put({'oversized': 'x' * MAX_EVIDENCE_BYTES})
        assert evidence.cleanup([ref]) == []
        assert evidence.cleanup([]) == [ref['sha256']+'.json']
        with pytest.raises(CorruptLedger):
            evidence.read(ref)


def test_budget_attempts_never_refunded():
    meter = CallBudget({'asr': {'calls': 1, 'frames': 100}})
    record = meter.reserve('asr', 100)
    assert record['status'] == 'attempted'
    with pytest.raises(ValueError, match='exhausted'):
        meter.reserve('asr', 1)


def test_meter_records_actual_stream_and_failed_compute():
    class RawStream:
        def accept_waveform(self, sample_rate, waveform):
            pass
    class Engine:
        def create_stream(self):
            return RawStream()
        def decode_stream(self, stream):
            raise RuntimeError('model fake failure')
    meter = budget()
    engine = MeteredEngine(Engine(), 'sensevoice', lambda kind, frames, receipt: meter.reserve(kind, frames))
    stream = engine.create_stream()
    stream.accept_waveform(16000, [0]*24)
    with pytest.raises(RuntimeError):
        engine.decode_stream(stream)
    assert meter.used['sensevoice'] == {'calls': 1, 'frames': 24}
    assert meter.records[0]['status'] == 'failed'
    assert meter.records[0]['execution_started']


def test_ppid_lifecycle_and_pid_reuse():
    root = Identity(3, 1.)
    child = Identity(4, 2.)
    assert descendants([root], {3: (1, 1.), 4: (3, 2.)}) == {3: root, 4: child}
    assert descendants([root], {}, [child]) == {}
    assert descendants([root], {4: (1, 3.)}, [child]) == {}
    with pytest.raises(ResourceLimit, match='reused'):
        descendants([root], {3: (1, 2.)})


def test_guard_survives_short_lived_child(tmp_path):
    import os, sys
    result = supervise([sys.executable, '-c', 'pass'], tmp_path, cpu=min(os.sched_getaffinity(0)), deadline=10)
    assert result['returncode'] == 0


def runner_with_evidence(tmp_path, media, fault=lambda point: None):
    _, _, paths, sources = media
    evidence = EvidenceStore(tmp_path/'evidence')
    plan = plan_for([sources['short']])
    store = RuntimeStore(tmp_path/'ledger.sqlite', evidence_store=evidence, fault=fault)
    store.create('run', plan, RuntimePolicy(native_fingerprint='n', analyzer_fingerprint='a'))
    control = store.snapshot('run').control
    factory = NativeFactory(lambda: NativeBridge(Process, fingerprint='n',
        expected_provenance={'diarization_fingerprint': 'f'}, temp_root=tmp_path/'native', verify=lambda: None,
        budget=budget(), guard=Guard(), eos_control=control['control_id'], eos_epoch=0, eos_final_frame=plan.end_frame))
    analyzer = AnalyzerBridge(Analyzer(), fingerprint='a', evidence_store=evidence, temp_root=tmp_path/'tmp',
        verify=lambda: None, budget=budget(), guard=Guard(), provenance={}, source_bindings={})
    return LogicalRunner(store, 'run', paths, analyzer, factory), store, evidence, factory


@pytest.mark.parametrize('point', ['core.after_output', 'ack.after_control', 'core.after_cursor', 'core.before_commit'])
def test_evidence_and_output_cursor_eos_rollback_atomically(tmp_path, media, point):
    def fault(actual):
        if actual == point:
            raise RuntimeError(point)
    runner, store, evidence, factory = runner_with_evidence(tmp_path, media, fault)
    try:
        with pytest.raises(RuntimeError, match=point):
            runner.step()
        snapshot = store.snapshot('run')
        assert snapshot.next_frame == 0 and not snapshot.control['acknowledged']
        assert not store.commits('run')
        assert list(evidence.root.glob('*.json'))  # file-first orphan is expected
        assert store.cleanup_evidence_orphans()
        assert not list(evidence.root.glob('*.json'))
    finally:
        factory.close(); evidence.close()


def test_crash_after_commit_preserves_evidence_and_complete_state(tmp_path, media):
    def fault(point):
        if point == 'core.after_commit':
            raise RuntimeError(point)
    runner, store, evidence, factory = runner_with_evidence(tmp_path, media, fault)
    try:
        with pytest.raises(RuntimeError):
            runner.step()
        assert store.snapshot('run').status == 'completed'
        refs = [p['evidence_ref'] for p in store.commits('run')]
        store.cleanup_evidence_orphans()
        assert all(evidence.read(ref) for ref in refs)
        without_root = RuntimeStore(store.path)
        with pytest.raises(CorruptLedger, match='fixed evidence store'):
            without_root.snapshot('run')
    finally:
        factory.close(); evidence.close()


def test_evidence_projection_failure_retains_full_raw(tmp_path, media):
    runner, store, evidence, factory = runner_with_evidence(tmp_path, media)
    class Unsupported(Analyzer):
        def analyze(self, *args, **kwargs):
            raw = raw_result()
            raw['segments'][0]['_units'][0]['text'] = '  '
            return raw
    runner.analyzer.analyzer = Unsupported()
    try:
        with pytest.raises(ValueError, match='whitespace'):
            runner.step()
        saved = evidence.read(runner.analyzer.failure_ref)
        assert saved['raw_analyzer_result']['segments'][0]['_units'][0]['text'] == '  '
        assert saved['failure']['type'] == 'ValueError'
        assert not store.commits('run')
    finally:
        factory.close(); evidence.close()


def test_evidence_wrong_binding_and_symlink_fail_closed(tmp_path):
    evidence = EvidenceStore(tmp_path/'evidence')
    try:
        ref = evidence.put({'binding': {'run_id': 'wrong'}})
        payload = {key: {} for key in ('window_id', 'window', 'revision', 'plan_hash', 'contract', 'policy_hash', 'mapping', 'map_hash', 'pcm_hash')}
        with pytest.raises(CorruptLedger, match='bound'):
            evidence.verify_core(ref, payload, 'run')
        file = evidence.root/(ref['sha256']+'.json')
        backup = tmp_path/'other.json'
        file.rename(backup); file.symlink_to(backup)
        with pytest.raises(CorruptLedger):
            evidence.read(ref)
    finally:
        evidence.close()


def test_native_early_eos_and_provenance_mismatch_are_rejected(tmp_path, media):
    provider = native(tmp_path)
    provider.reset(('run', 0, 0, 'n', CONTRACT), 0)
    with pytest.raises(ValueError, match='true-EOS'):
        provider.finish('eos')
    provider.close()
    class Wrong(Process):
        def request(self, *args, **kwargs):
            result = super().request(*args, **kwargs)
            if 'provenance' in result:
                result['provenance']['changed'] = True
            return result
    bad = native(tmp_path, Wrong)
    with pytest.raises(ValueError, match='provenance'):
        bad.reset(('run', 0, 0, 'n', CONTRACT), 0)
    assert bad.process is None


def test_native_oversized_turns_fail_without_truncation(tmp_path, media):
    class Oversized(Process):
        def request(self, action, *args, **kwargs):
            result = super().request(action, *args, **kwargs)
            if action == 'turns':
                result['turns'] *= 2049
            return result
    _, _, paths, sources = media
    pcm = LogicalPCMReader(plan_for([sources['short']]), paths).read(0, sources['short'].frames)
    provider = native(tmp_path, Oversized)
    provider.reset(('run', 0, 0, 'n', CONTRACT), 0)
    with pytest.raises(ValueError, match='4096-item'):
        provider.push(pcm)
    assert provider.process is None


def test_external_project_pid_must_really_be_visible(tmp_path):
    import os
    with pytest.raises(ResourceLimit, match='Cannot bind'):
        ResourceGuard(tmp_path, (Identity.bind(os.getpid()),), (Identity(2**30, 1.),))


def test_resource_feed_transitional_schema_and_partial_append(tmp_path):
    from datetime import datetime, timezone
    from audio_backend.fragment_resource_feed import EnduranceFeed
    metric, progress = tmp_path/'metrics.jsonl', tmp_path/'progress.json'
    metric.write_text(json.dumps({'rss_bytes': 1234, 'processes': [{'pid': 99, 'created': 1.}]})+'\n')
    progress.write_text(json.dumps({'utc': datetime.now(timezone.utc).isoformat()}))
    feed = EnduranceFeed(metric, progress)
    assert feed.sample()['rss_bytes'] == 1234  # Missing wall field is valid.
    with metric.open('a') as out:
        out.write('{"rss_bytes":')
    assert feed.sample()['transitional_read_using_last_fresh_sample']
    feed.latest['metric_mtime'] -= 30
    with pytest.raises(ResourceLimit, match='stale'):
        feed.sample()


def test_explicit_execution_receipt_rejects_budget_widening():
    from audio_backend.fragment_model_protocol import FULL_LIMITS, validate_approval
    frozen = {'example': 'freeze'}
    approval = {'freeze_sha256': digest(frozen), 'stage': 'base', 'authorization_reference': 'explicit parent test evidence',
                'arms': ['B_whole'], 'budgets': copy.deepcopy(FULL_LIMITS)}
    assert validate_approval(approval, frozen, 'base')[0] == ['B_whole']
    approval['budgets']['analyzer']['calls'] += 1
    with pytest.raises(ValueError, match='maxima'):
        validate_approval(approval, frozen, 'base')


def test_moss_gate_is_native_only(tmp_path, media):
    from audio_backend.fragment_model_compare import prepare_moss
    runner, store, evidence, factory = runner_with_evidence(tmp_path, media)
    try:
        runner.step()
        # Pure gate test: explicit exact B bounds and independent raw turns,
        # with no reference text, source labels or gold timing in planner input.
        commit = store.commits('run')[0]
        class Evidence:
            def verify_core(self, *args):
                return {'analyzer_request': {'diarization': {'turns': []}}}
        commit['window'].update(core_start=0, core_end=179680)
        result = prepare_moss('B_whole', commit, Evidence(), {})
        assert result['status'] == 'not_attempted_native_gate'
        assert not result['supports']
        with pytest.raises(ValueError, match='exact B'):
            prepare_moss('A_whole', commit, Evidence(), {})
    finally:
        factory.close(); evidence.close()


def test_model_free_transport_whole_split_equal(tmp_path, media):
    from audio_backend.fragment_model_protocol import materialize_arm
    _, pcm, paths, sources = media
    n = 32000
    source = sources['whole']
    fixture = {'id': 'A_en_midword', 'asset_id': 'fake', 'source_frames': [0, n],
        'canonical_pcm_sha256': hashlib.sha256(pcm[:2*n]).hexdigest(),
        'whole_view': {'source_frames': [0, n]}, 'split_views': [{'source_frames': [0, 16000]}, {'source_frames': [16000, n]}]}
    manifest = {'fixtures': [fixture], 'assets': [{'id': 'fake', 'path': str(paths['whole']), 'format': 'WAV', 'sha256': source.sha256}]}
    with materialize_arm('A_whole', manifest, '/', tmp_path, Guard()) as whole:
        assert whole['status'] == 'admitted_exact_pcm'
        whole_hash = whole['whole_pcm_sha256']
    with materialize_arm('A_split', manifest, '/', tmp_path, Guard()) as split:
        assert split['status'] == 'admitted_exact_pcm'
        assert whole_hash == split['whole_pcm_sha256']


def test_alignment_missing_speaker_and_repeat_denominators():
    from audio_backend.fragment_model_score import align, candidate_score, speaker_score
    assert align('abc', 'axc')['counts'] == {'equal': 2, 'substitute': 1, 'delete': 0, 'insert': 0}
    refs = [{'speaker': 'a', 'text': '出出好', 'logical_frames': [0, 100], 'gold_array_index': 1},
            {'speaker': 'b', 'text': '先先要要', 'logical_frames': [50, 150], 'gold_array_index': 2}]
    score = candidate_score(refs, [], [{'gold_array_index': 1, 'substring': '出出'}])
    assert score['reference_characters'] == score['edit_errors'] == 7
    assert score['cer'] == 1 and len(score['missing_reference_speakers']) == 2
    assert not score['repeat_substring_diagnostics'][0]['all_repeat_characters_equal']
    diar = speaker_score(refs, [])
    assert diar['miss_frames'] == 200 and diar['der_zero_collar_overlap_aware'] == 1
    assert diar['reference_overlap_frames'] == 50
    hyp = [{'speaker': 'x', 'start_frame': 50, 'end_frame': 150}, {'speaker': 'y', 'start_frame': 0, 'end_frame': 100}]
    assert speaker_score(refs, hyp)['der_zero_collar_overlap_aware'] == 0


def test_candidate_assignment_accounts_for_unmatched_insertions():
    from audio_backend.fragment_model_score import candidate_score
    refs = [{'speaker': 'a', 'text': '甲乙', 'logical_frames': [0, 100], 'gold_array_index': 1}]
    tracks = [{'scope_id': 'support', 'provenance': {'model_local_speaker_label': label}, 'text': text}
              for label, text in [('one', '甲乙'), ('two', '丙')]]
    score = candidate_score(refs, tracks)
    assert score['reference_characters'] == 2 and score['edit_errors'] == 1
    assert score['unmatched_hypothesis_insertions'] == 1


def test_embedding_input_source_map_is_complete_or_explicitly_unavailable():
    from audio_backend.fragment_model_bridges import embedding_input_maps
    from audio_backend.fragment_runtime_contracts import PCMWindow
    mapping = ({'span': {'fragment_id': 'one', 'logical_start': 100, 'logical_end': 105, 'source_start': 0, 'source_end': 5}, 'source_sha256': 'a'*64},
               {'span': {'fragment_id': 'two', 'logical_start': 105, 'logical_end': 110, 'source_start': 10, 'source_end': 15}, 'source_sha256': 'b'*64})
    raw = b'\0'*20
    pcm = PCMWindow(100, 110, raw, mapping, digest(mapping), hashlib.sha256(raw).hexdigest())
    receipt = {'sample_count': 6, 'pcm_sha256': hashlib.sha256(b'\0'*24).hexdigest()}
    support = {'sample_rate': 16000, 'analyzer_frame_count': 10, 'pooled_sample_count': 6,
               'pooled_pcm_sha256': receipt['pcm_sha256'], 'complete': True, 'intervals_samples': [[2, 8]]}
    record = {'kind': 'embedding', 'status': 'completed', 'input': receipt, 'selection': {'support': support}}
    mapped = embedding_input_maps([record], pcm)[0]
    assert mapped['source_mapping_status'] == 'exact_complete'
    assert [p['source_frames'] for p in mapped['source_mapping']] == [[2, 5], [10, 13]]
    support['complete'] = False
    assert embedding_input_maps([record], pcm)[0]['source_mapping_status'] == 'unavailable_incomplete_selection'


def test_native_process_observer_preserves_raw_error_before_exception(tmp_path):
    import io
    from types import SimpleNamespace
    from audio_backend.native_worker import NativeProcess
    process = NativeProcess.__new__(NativeProcess)
    process.root, process.counter, process.busy = tmp_path, 0, False
    process.proc = SimpleNamespace(poll=lambda: None, stdin=io.StringIO())
    process.settings = SimpleNamespace()
    observed = []
    process.response_observer = observed.append
    (tmp_path/'1.json').write_text(json.dumps({'_error': 'complete native diagnostic'}))
    with pytest.raises(RuntimeError, match='complete native diagnostic'):
        process.request('reset', lambda: False, epoch_id='epoch')
    assert observed == [{'_error': 'complete native diagnostic'}]
    assert not (tmp_path/'1.json').exists()


def test_stage_connections_close_without_gc_and_allow_repeated_cleanup(tmp_path):
    import gc, os
    from audio_backend.fragment_model_compare import StageStore
    if not Path('/proc/self/fd').is_dir():
        pytest.skip('Linux descriptor regression')
    was_enabled = gc.isenabled()
    gc.collect(); gc.disable()
    before = len(os.listdir('/proc/self/fd'))
    try:
        with EvidenceStore(tmp_path/'evidence') as evidence:
            for cycle in range(80):
                path = tmp_path/'stage.sqlite'
                store = StageStore(path, evidence)
                for index in range(3):
                    store.put(str(index), {'status': 'model_free', 'index': index})
                    assert len(store.rows()) == index+1
                path.unlink()
                assert len(os.listdir('/proc/self/fd')) <= before+2
        assert len(os.listdir('/proc/self/fd')) <= before+1
    finally:
        if was_enabled:
            gc.enable()


def test_stage_root_survives_reopened_generic_cleanup(tmp_path, media):
    from audio_backend.fragment_model_compare import StageStore
    runner, store, evidence, factory = runner_with_evidence(tmp_path, media)
    runner.step()
    stages = StageStore(tmp_path/'private-stages.sqlite', evidence)
    stage_ref = evidence.put({'kind': 'candidate_failure', 'full_raw': 'valuable diagnostic'})
    stages.put('failure', {'status': 'failed', 'evidence_ref': stage_ref})
    orphan = evidence.put({'orphan': True})
    factory.close(); evidence.close()
    with EvidenceStore(tmp_path/'evidence') as reopened:
        store = RuntimeStore(tmp_path/'ledger.sqlite', evidence_store=reopened)
        with pytest.raises(CorruptLedger, match='external evidence-root inventory'):
            store.cleanup_evidence_orphans()
        assert reopened.read(stage_ref)['full_raw'] == 'valuable diagnostic'
        stages = StageStore(tmp_path/'private-stages.sqlite', reopened)
        removed = store.cleanup_evidence_orphans(external_reference_provider=stages.references)
        assert orphan['sha256']+'.json' in removed
        assert reopened.read(stage_ref)['full_raw'] == 'valuable diagnostic'
        assert store.commits('run')


@pytest.mark.parametrize('failure', ['diagnostic_write', 'cleanup'])
def test_watchdog_always_exits_and_parent_watch_closes_child(tmp_path, failure):
    import os, subprocess, sys, time
    import psutil
    # Real process lifecycle, fake work: no neural import/construction.
    script = r'''
import json, os, subprocess, sys, threading, time
from pathlib import Path
import psutil
from audio_backend.fragment_model_compare import Watchdog
from audio_backend.fragment_model_guard import Identity, ResourceLimit
root=Path(sys.argv[1]); failure=sys.argv[2]
child_code="from audio_backend.runner import watch_parent; import threading,time; threading.Thread(target=watch_parent,daemon=True).start(); time.sleep(30)"
child=subprocess.Popen([sys.executable,'-c',child_code],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
    env={**os.environ,'AUDIO_PARENT_PID':str(os.getpid()),'AUDIO_PARENT_CREATED':str(psutil.Process().create_time())})
(root/'child.json').write_text(json.dumps({'pid':child.pid,'created':psutil.Process(child.pid).create_time()}))
class Guard:
    own_roots=(Identity.bind(os.getpid()),)
    def check(self):raise ResourceLimit('injected guard failure')
    def terminate_owned(self):
        (root/'cleanup_attempted').write_text('yes')
        if failure=='cleanup':raise PermissionError('injected cleanup access denial')
report=root if failure=='diagnostic_write' else root/'failure.json'
watchdog=Watchdog(Guard(),report);watchdog.start();time.sleep(30)
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), failure], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 75, (stdout, stderr)
    assert (tmp_path/'cleanup_attempted').read_text() == 'yes'
    child = json.loads((tmp_path/'child.json').read_text())
    deadline = time.monotonic()+3
    while time.monotonic() < deadline:
        try:
            observed = psutil.Process(child['pid'])
            if observed.create_time() != child['created'] or observed.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(.05)
    else:
        # Only the explicitly bound test child is eligible for emergency cleanup.
        observed.kill()
        pytest.fail('Owned child survived parent-death watcher')


def test_private_real_output_admission_and_packaging_exclusion(tmp_path, monkeypatch):
    import importlib.util
    from audio_backend.fragment_model_compare import require_private_output
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match='private_'):
        require_private_output(root/'reports'/'fragment-model-bridge')
    for folder in ('audio_backend', 'docs'):
        with pytest.raises(ValueError, match='reports/private_'):
            require_private_output(root/folder/'private_run')
    assert require_private_output(root/'reports'/'private_fragment_models').name == 'private_fragment_models'
    spec = importlib.util.spec_from_file_location('fragment_packager', root/'scripts'/'package_source.py')
    packager = importlib.util.module_from_spec(spec); spec.loader.exec_module(packager)
    fake = tmp_path/'package-root'
    private = fake/'reports'/'private_fragment_models'/'evidence'
    private.mkdir(parents=True)
    raw = private/('f'*64+'.json')
    raw.write_text(json.dumps({'raw_transcript': 'sensitive raw source', 'speaker_embedding': [1, 2]}))
    monkeypatch.setattr(packager, 'ROOT', fake)
    assert raw not in packager.selected_files()


def test_moss_attempt_is_durable_before_any_call_and_cannot_restart(tmp_path):
    from audio_backend.fragment_model_compare import StageStore
    with EvidenceStore(tmp_path/'evidence') as evidence:
        stages = StageStore(tmp_path/'private-stages.sqlite', evidence)
        stages.begin_moss_attempt('f'*64, ['B_whole'], {'moss_support': {'calls': 1, 'frames': 179680}})
        assert stages.rows()[0]['status'] == 'attempt_started'
        restarted = StageStore(tmp_path/'private-stages.sqlite', evidence)
        with pytest.raises(ValueError, match='already attempted'):
            restarted.begin_moss_attempt('f'*64, ['B_whole'], {})


def test_same_lease_cannot_race_cleanup_from_another_thread(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    with EvidenceStore(tmp_path/'evidence') as evidence:
        ref = evidence.put({'pending_writer': 'must stay intact'})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(evidence.cleanup, [])
            with pytest.raises(RuntimeError, match='one owning thread'):
                future.result()
        assert evidence.read(ref)['pending_writer'] == 'must stay intact'
