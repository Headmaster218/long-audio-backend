"""Synthetic/model-free normalized scheduler and crash-boundary coverage."""
from dataclasses import replace
import copy
import hashlib
import json
import sqlite3
import time

import pytest

from audio_backend.fragment_core import SourceChanged, UnsupportedAudio, inspect_pcm16_source
from audio_backend.fragment_model_bridges import check_pcm, embedding_input_maps, write_pcm
from audio_backend.fragment_normalized_runtime import (
    INPUT_FINGERPRINT, MAX_SUPPORT_PARTS, NormalizedLogicalRunner, NormalizedPCMReader,
    NormalizedPlan, NormalizedRuntimeStore, prepare_normalized_plan,
)
from audio_backend.fragment_runtime_contracts import (
    CORE, RATE, RuntimePolicy, digest, native_key, next_window, prefix_hash, window_id,
)
from audio_backend.fragment_runtime_store import RuntimeStore
from test_fragment_normalizer import pcm, plan_for, whole
from test_fragment_runtime import FakeAnalyzer, FakeNative, drain


def prepare(tmp_path, rate=48000, channels=1, n=3031, cuts=None, overlap=0, data=None):
    data = data if data is not None else pcm(rate, channels, n)
    native, paths = plan_for(tmp_path, data, rate, channels, cuts, overlap)
    plan = prepare_normalized_plan(native, paths, temp_root=tmp_path, enabled=True)
    return plan, paths


def setup(tmp_path, plan, paths, *, units=(), events=(), fault=None, store_fault=None):
    store = NormalizedRuntimeStore(tmp_path/'run.sqlite', enabled=True, fault=store_fault)
    store.create('normalized', plan)
    log = []
    analyzer = FakeAnalyzer(units, events)
    runner = NormalizedLogicalRunner(store, 'normalized', paths, analyzer, lambda: FakeNative(log),
                                     temp_root=tmp_path, enabled=True, fault=fault)
    return store, runner, log


@pytest.mark.parametrize('rate', [44100, 48000])
@pytest.mark.parametrize('channels', [1, 2])
@pytest.mark.parametrize('overlap', [False, True])
def test_whole_fragmented_runtime_exact_pcm_and_native_support(tmp_path, rate, channels, overlap):
    data = pcm(rate, channels, 2*rate+1)
    ref = whole(tmp_path, data, rate, channels)
    native_whole, whole_paths = plan_for(tmp_path, data, rate, channels)
    a = prepare_normalized_plan(native_whole, whole_paths, temp_root=tmp_path, enabled=True)
    b, paths = prepare(tmp_path, rate, channels, cuts=[0,rate+137,2*rate,2*rate+1],
                       overlap=rate//2 if overlap else 0, data=data)
    assert b.source_plan.spans[-1].source_end-b.source_plan.spans[-1].source_start == 1
    assert b.end_frame == a.end_frame == len(ref)//2
    for name, plan, bindings in [('whole', a, whole_paths), ('parts', b, paths)]:
        root = tmp_path/name; root.mkdir()
        word = {'a': 15900, 'b': 16500, 'anchor': 16100, 'text': 'cross-fragment'}
        store, runner, log = setup(root, plan, bindings, units=[word])
        assert drain(runner) == 'completed'
        saved = store.snapshot('normalized')
        assert saved.plan.source_plan == plan.source_plan
        assert saved.control['final_frame'] == len(ref)//2
        assert saved.control['acknowledged'] == 1
        assert saved.checkpoint['consumed_until'] == len(ref)//2
        assert saved.checkpoint['input_prefix_hash'] == prefix_hash(plan, len(ref)//2)
        output = store.commits('normalized')
        assert len(output) == 1 and len(output[0]['segments']) == 1
        assert output[0]['pcm_hash'] == hashlib.sha256(ref).hexdigest()
        assert output[0]['mapping'][0]['kind'] == 'native-filter-support-v1'
        assert output[0]['mapping'][-1]['native_context_end'] == 2*rate+1
        assert output[0]['mapping'][-1]['receipt'] == plan.receipt
        if name == 'parts':
            assert any(part['span']['fragment_id'] == '2' for part in output[0]['mapping'][-1]['owners'])
        assert runner.reader.normalizer.max_native_read <= 25465
        assert runner.reader.normalizer.max_tree_rss <= 256*1024**2
        assert runner.step() == 'completed'
        assert len([entry for entry in log if entry[0] == 'finish']) == 1
    assert bool(b.source_plan.aliases) is overlap


def test_arbitrary_repeated_resumed_reads_and_final_partial(tmp_path):
    plan, paths = prepare(tmp_path, rate=44100, n=90001, cuts=[0,13579,90000,90001])
    reader = NormalizedPCMReader(plan, paths, temp_root=tmp_path, enabled=True)
    full = reader.read(0, plan.end_frame)
    check_pcm(full)
    restored = NormalizedPlan.from_json(plan.to_json())
    reopened = NormalizedPCMReader(restored, paths, temp_root=tmp_path, enabled=True)
    for a, b in [(8001,16004), (0,1), (plan.end_frame-1,plan.end_frame), (3,12003), (8001,16004)]:
        w = reopened.read(a,b)
        assert w.pcm == full.pcm[a*2:b*2] == reader.read(a,b).pcm
        assert w.mapping == restored.mapping(a,b)
        check_pcm(w)
    assert full.mapping[-1]['receipt']['native_end'] == 90001
    assert full.mapping[-1]['native_context_end'] == 90001
    assert full.mapping[-1]['owners'][-1]['span']['source_end'] == 1
    assert not list(tmp_path.glob('fragment-normalizer-*'))


def test_word_crosses_fragment_and_core_once_and_short_throughput(tmp_path):
    # One 30s seam plus a nonintegral native tail; <6 MB fixture storage.
    rate = 48000
    plan, paths = prepare(tmp_path, rate=rate, n=31*rate+4, cuts=[0,30*rate+1,31*rate+4])
    word = {'a': CORE-1600, 'b': CORE+1600, 'anchor': CORE, 'text': 'unbroken'}
    event = {'start_frame': CORE-400, 'end_frame': CORE+400, 'event': 'synthetic', 'instance_id': 'e'}
    store, runner, log = setup(tmp_path, plan, paths, units=[word], events=[event])
    began = time.monotonic()
    assert runner.step() == 'committed'
    first_reader = runner.reader
    # Simulate a process restart after a committed core: replay bounded native
    # input while keeping output and lexical ownership exactly once.
    resumed = NormalizedLogicalRunner(store, 'normalized', paths, FakeAnalyzer([word], [event]),
        lambda: FakeNative(log), temp_root=tmp_path, enabled=True)
    assert drain(resumed) == 'completed'
    elapsed = time.monotonic()-began
    outputs = store.commits('normalized')
    assert [(o['window']['core_start'],o['window']['core_end']) for o in outputs] == [(0,CORE),(CORE,plan.end_frame)]
    assert [s['text'] for o in outputs for s in o['segments']] == ['unbroken']
    assert [(e['start_frame'],e['end_frame']) for o in outputs for e in o['events']] == [(CORE-400,CORE),(CORE,CORE+400)]
    assert outputs[-1]['mapping'][-1]['owners'][-1]['span']['source_end'] == rate+3
    assert store.snapshot('normalized').next_frame == plan.end_frame
    print('NORMALIZED_BENCHMARK', json.dumps({'canonical_seconds':plan.end_frame/RATE,
        'elapsed_seconds_including_restart':elapsed,
        'ffmpeg_invocations':first_reader.normalizer.invocations+resumed.reader.normalizer.invocations,
        'peak_tree_rss':max(first_reader.normalizer.max_tree_rss,resumed.reader.normalizer.max_tree_rss),
        'peak_normalizer_temp_bytes':max(first_reader.normalizer.max_temp_bytes,resumed.reader.normalizer.max_temp_bytes)}))


@pytest.mark.parametrize('point', ['native.after_push','finish.before_request','finish.after_request',
    'finish.after_response','core.after_read','core.after_analysis','core.after_output','ack.after_control',
    'core.after_cursor','core.before_commit','core.after_commit'])
def test_crash_receipts_restart_exactly_once(tmp_path, point):
    plan, paths = prepare(tmp_path)
    fired = []
    def fault(here):
        if here == point and not fired:
            fired.append(here)
            raise RuntimeError('simulated lost process/reply')
    store, runner, log = setup(tmp_path, plan, paths, fault=fault, store_fault=fault)
    with pytest.raises(RuntimeError, match='simulated'):
        runner.step()
    reopened = NormalizedRuntimeStore(tmp_path/'run.sqlite', enabled=True)
    # Idempotent same operation cannot replace its durable normalization receipt.
    reopened.create('normalized', plan)
    retry = NormalizedLogicalRunner(reopened, 'normalized', paths, FakeAnalyzer(), lambda: FakeNative(log),
                                    temp_root=tmp_path, enabled=True)
    assert drain(retry) == 'completed'
    assert len(reopened.commits('normalized')) == 1
    state = reopened.snapshot('normalized')
    assert state.control['acknowledged'] == 1
    assert json.loads(state.control['response'])['consumed_until'] == plan.end_frame
    assert state.control['response_hash'] == digest(json.loads(state.control['response']))
    assert retry.step() == 'completed'


@pytest.mark.parametrize('point', ['create.after_plan','plan.after_intent','create.before_commit','create.after_commit'])
def test_create_transaction_binds_native_plan_eos_receipt(tmp_path, point):
    plan, paths = prepare(tmp_path)
    once = []
    def fault(here):
        if here == point and not once:
            once.append(here); raise RuntimeError('crash')
    store = NormalizedRuntimeStore(tmp_path/'create.sqlite', enabled=True, fault=fault)
    with pytest.raises(RuntimeError): store.create('n',plan)
    clean = NormalizedRuntimeStore(tmp_path/'create.sqlite', enabled=True)
    result = clean.create('n',plan)
    assert result.plan.receipt == plan.receipt
    assert result.control['final_frame'] == plan.end_frame
    with sqlite3.connect(tmp_path/'create.sqlite') as db:
        assert db.execute('SELECT count(*) FROM fragment_plans').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM fragment_controls').fetchone()[0] == 1


def test_identity_and_closed_admission(tmp_path):
    plan, paths = prepare(tmp_path)
    for constructor, args in [(NormalizedRuntimeStore,(tmp_path/'off.sqlite',)),
        (NormalizedPCMReader,(plan,paths)), (prepare_normalized_plan,(plan.source_plan,paths))]:
        kwargs = {'temp_root':tmp_path} if constructor is not NormalizedRuntimeStore else {}
        with pytest.raises(UnsupportedAudio): constructor(*args,**kwargs)
    exact = RuntimeStore(tmp_path/'exact.sqlite')
    with pytest.raises(UnsupportedAudio): exact.create('n',plan)
    normalized = NormalizedRuntimeStore(tmp_path/'normalized.sqlite',enabled=True)
    with pytest.raises(UnsupportedAudio): normalized.create('n',plan.source_plan)
    with pytest.raises(UnsupportedAudio): normalized.create('n',plan,RuntimePolicy())
    policy = RuntimePolicy(input_fingerprint=INPUT_FINGERPRINT)
    assert policy.sha256 != RuntimePolicy().sha256
    assert native_key('same',0,policy) != native_key('same',0,RuntimePolicy())
    old_hash = digest({'run':'same','input':INPUT_FINGERPRINT})
    assert native_key('same',0,policy) != native_key(old_hash,0,RuntimePolicy())
    assert isinstance(native_key('same',0,policy)[0],dict)
    win = next_window(plan,0,policy)
    assert window_id('same',win,policy) != window_id('same',win,RuntimePolicy())
    normalized.create('n',plan)
    with pytest.raises((ValueError,UnsupportedAudio)):
        normalized.append('n',replace(plan,canonical_end=plan.end_frame+1),expected_revision=1,operation_id='append')
    for changed in [replace(plan,normalizer_fingerprint='0'*64),replace(plan,version='other'),
                    replace(plan,canonical_end=True),replace(plan,canonical_end=plan.end_frame+100)]:
        with pytest.raises((ValueError,UnsupportedAudio)): changed.validate()
    for changed in [replace(plan.source_plan,final=False),
                    replace(plan.source_plan,sources=(replace(plan.source_plan.sources[0],sample_rate=32000),))]:
        with pytest.raises((ValueError,UnsupportedAudio)):
            prepare_normalized_plan(changed,paths,temp_root=tmp_path,enabled=True)


def test_forged_eos_fails_before_provider_and_alias_cancellation(tmp_path):
    plan, paths = prepare(tmp_path)
    store, runner, log = setup(tmp_path,replace(plan,canonical_end=plan.end_frame+1),paths)
    with pytest.raises(SourceChanged,match='exact canonical EOS'): runner.step()
    assert not log and not store.commits('normalized')
    root=tmp_path/'stereo'; root.mkdir()
    data=pcm(44100,2,2*44100+1,cancellation=True)
    native, bindings=plan_for(root,data,44100,2,[0,44237,88201],22050)
    alias=native.aliases[0]
    with bindings[alias.fragment_id].open('r+b') as stream:
        stream.seek(44); v=int.from_bytes(stream.read(2),'little',signed=True)
        stream.seek(44); stream.write((v+1).to_bytes(2,'little',signed=True));stream.write((-v-1).to_bytes(2,'little',signed=True))
    changed=replace(native,sources=tuple(inspect_pcm16_source(s.fragment_id,bindings[s.fragment_id]) for s in native.sources))
    with pytest.raises(SourceChanged,match='all-channel alias'):
        prepare_normalized_plan(changed,bindings,temp_root=root,enabled=True)


def test_mutation_restart_and_invalid_ranges(tmp_path):
    plan, paths=prepare(tmp_path)
    read=NormalizedPCMReader(plan,paths,temp_root=tmp_path,enabled=True)
    for a,b in [(True,2),(-1,2),(0,0),(2,1),(0,plan.end_frame+1),(0,60*RATE+1)]:
        with pytest.raises(ValueError): read.read(a,b)
    with paths['0'].open('r+b') as stream: stream.seek(50);stream.write(b'\x01\x02')
    with pytest.raises(SourceChanged):read.read(0,1)
    with pytest.raises(SourceChanged):NormalizedPCMReader(plan,paths,temp_root=tmp_path,enabled=True)


def test_commit_and_bridge_reject_forged_native_mapping(tmp_path):
    plan, paths=prepare(tmp_path)
    store,runner,_=setup(tmp_path,plan,paths)
    assert runner.step()=='completed'
    window=runner.reader.read(0,plan.end_frame)
    check_pcm(window)
    for change in ['native_context_end','canonical_end']:
        mapping=copy.deepcopy(window.mapping)
        mapping[0][change]-=1
        with pytest.raises(ValueError):check_pcm(replace(window,mapping=mapping,map_sha256=digest(mapping)))
    # Correct map hash cannot make a different native owner admissible at commit.
    mapping=copy.deepcopy(window.mapping);mapping[0]['owners'][0]['source_sha256']='0'*64
    bad=replace(window,mapping=mapping,map_sha256=digest(mapping))
    # Shape alone is not source authentication; immutable plan is authoritative.
    check_pcm(bad)
    from audio_backend.fragment_runtime_contracts import validate_commit
    fresh=NormalizedRuntimeStore(tmp_path/'fresh.sqlite',enabled=True);snapshot=fresh.create('normalized',plan)
    payload=copy.deepcopy(store.commits('normalized')[0]);payload['mapping']=mapping;payload['map_hash']=digest(mapping)
    with pytest.raises(ValueError,match='accepted owners/support'):
        validate_commit(snapshot,next_window(plan,0,snapshot.policy),payload,payload['tail'],payload['checkpoint'])


def test_embedding_float32_receipt_and_many_to_many_support_are_separate(tmp_path):
    plan,paths=prepare(tmp_path,n=50001,cuts=[0,20001,50001])
    window=NormalizedPCMReader(plan,paths,temp_root=tmp_path,enabled=True).read(0,plan.end_frame)
    # This receipt represents the measured float32 model input, not PCM16 bytes.
    actual={'sample_count':500,'pcm_sha256':'a'*64,'dtype':'float32'}
    support={'complete':True,'sample_rate':RATE,'analyzer_frame_count':window.end,
             'pooled_sample_count':500,'pooled_pcm_sha256':'a'*64,'intervals_samples':[[100,300],[7900,8200]]}
    records=[{'kind':'embedding','status':'completed','input':actual,'selection':{'support':support}}]
    mapped=embedding_input_maps(records,window)[0]
    assert mapped['input']==actual
    assert mapped['source_mapping_status']=='canonical_selection_with_native_filter_support'
    assert 'source_mapping' not in mapped
    assert mapped['canonical_selection'][1]['support_chunk_indexes']==[0,1]
    assert mapped['native_filter_support']==window.mapping
    assert mapped['read_pcm_sha256']==window.pcm_sha256!='a'*64
    changed=copy.deepcopy(records);changed[0]['selection']['support']['pooled_pcm_sha256']='b'*64
    with pytest.raises(ValueError):embedding_input_maps(changed,window)


def test_actual_bridge_classes_model_free_with_persisted_evidence(tmp_path):
    from audio_backend.fragment_evidence import EvidenceStore
    from audio_backend.fragment_model_bridges import AnalyzerBridge, NativeBridge, NativeFactory
    from test_fragment_model_bridges import Process, Analyzer, Guard, budget, raw_result
    import numpy as np
    import wave
    from audio_backend.fragment_model_bridges import CallBudget
    from audio_backend.fragment_model_meter import MeteredEngine
    plan, paths = prepare(tmp_path,n=50004,cuts=[0,24001,50004])
    meter = CallBudget({kind:{'calls':10,'frames':10*RATE} for kind in ('analyzer','embedding')})
    class RawStream:
        def accept_waveform(self,sample_rate,waveform):
            assert sample_rate==RATE
            self.received=np.array(waveform,dtype='<f4',copy=True)
    class RawEmbedding:
        def create_stream(self): return RawStream()
        def compute(self,stream):
            self.actual_input=stream.received
            return [0.1,0.2]
    class MeasuredAnalyzer(Analyzer):
        def analyze(self,path,**kwargs):
            with wave.open(path) as source:
                assert (source.getframerate(),source.getnchannels(),source.getsampwidth())==(RATE,1,2)
                samples=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').astype('<f4')/32768.
            intervals=[[100,300],[7900,8200]]
            pooled=np.concatenate([samples[a:b] for a,b in intervals])
            self.support={'complete':True,'sample_rate':RATE,'analyzer_frame_count':len(samples),
                'pooled_sample_count':len(pooled),'pooled_pcm_sha256':hashlib.sha256(pooled.tobytes()).hexdigest(),
                'intervals_samples':intervals}
            def reserve(kind,frames,receipt):
                assert receipt['pcm_sha256']==self.support['pooled_pcm_sha256'] and frames==len(pooled)
                entry=meter.reserve(kind,frames)
                entry.update(input=copy.deepcopy(receipt),selection={'support':copy.deepcopy(self.support)})
                return entry
            raw=RawEmbedding(); engine=MeteredEngine(raw,'embedding',reserve)
            stream=engine.create_stream();stream.accept_waveform(RATE,pooled)
            assert engine.compute(stream)==[0.1,0.2]
            self.actual_input=raw.actual_input
            return raw_result()
    measured=MeasuredAnalyzer()
    policy = RuntimePolicy(native_fingerprint='n',analyzer_fingerprint='a',input_fingerprint=INPUT_FINGERPRINT)
    with EvidenceStore(tmp_path/'evidence') as evidence:
        store = NormalizedRuntimeStore(tmp_path/'bridges.sqlite',enabled=True,evidence_store=evidence)
        store.create('bridge',plan,policy)
        factory = NativeFactory(lambda: NativeBridge(Process,fingerprint='n',
            expected_provenance={'diarization_fingerprint':'f'},temp_root=tmp_path/'native',
            verify=lambda:None,budget=budget(),guard=Guard(),eos_control=store.snapshot('bridge').control['control_id'],
            eos_epoch=0,eos_final_frame=plan.end_frame))
        analyzer = AnalyzerBridge(measured,fingerprint='a',evidence_store=evidence,temp_root=tmp_path/'analyzer',
            verify=lambda:None,budget=meter,guard=Guard(),provenance={'test':'fake'},
            source_bindings={'source':'synthetic'})
        runner = NormalizedLogicalRunner(store,'bridge',paths,analyzer,factory,temp_root=tmp_path,enabled=True)
        try:
            assert runner.step()=='completed'
            payload = store.commits('bridge')[0]
            saved = evidence.read(payload['evidence_ref'])
            assert saved['raw_analyzer_result']==raw_result()
            assert saved['binding']['mapping']==payload['mapping']
            assert saved['binding']['pcm_hash']==payload['pcm_hash']
            push = next(call for call in saved['native']['calls'] if call['request']['action']=='push')
            assert push['pcm']['mapping']==payload['mapping']
            assert push['pcm']['sha256']==payload['pcm_hash']
            assert saved['native']['status']['consumed_until']==plan.end_frame
            assert saved['native']['status']['activity_until']<plan.end_frame
            embedding=saved['embedding_inputs'][0]
            actual_hash=hashlib.sha256(measured.actual_input.tobytes()).hexdigest()
            assert embedding['input']['pcm_sha256']==actual_hash==measured.support['pooled_pcm_sha256']
            assert embedding['input']['sample_count']==500 and embedding['status']=='completed'
            assert embedding['source_mapping_status']=='canonical_selection_with_native_filter_support'
            assert embedding['canonical_selection'][1]['support_chunk_indexes']==[0,1]
            assert any(len(part['owners'])==2 for part in embedding['native_filter_support'][:2])
            assert embedding['read_pcm_sha256']==payload['pcm_hash']!=actual_hash
            call=next(call for call in saved['model_call_records'] if call['kind']=='embedding')
            assert call['execution_started'] and call['input']==embedding['input']
            reopened = NormalizedRuntimeStore(tmp_path/'bridges.sqlite',enabled=True,evidence_store=evidence)
            assert reopened.snapshot('bridge').status=='completed'
            assert len(reopened.commits('bridge'))==1
        finally:
            factory.close()


def test_metadata_bounds_and_legacy_policy_identity(tmp_path):
    from dataclasses import asdict
    from audio_backend.fragment_core import Source
    from test_fragment_runtime import plan_for as native_plan
    # No PCM execution for this pure scheduler/support-map bound check.
    native = native_plan([Source('s','0'*64,48000,1,61*48000)])
    plan = NormalizedPlan(native,61*RATE).validate()
    mapping = plan.mapping(0,60*RATE)
    assert len(mapping)==120
    assert all(part['canonical_end']-part['canonical_start']<=8000 for part in mapping)
    assert all(part['native_context_end']-part['native_context_start']<=25465 for part in mapping)
    with pytest.raises(ValueError):plan.mapping(0,60*RATE+1)
    fragmented = native_plan([Source(str(i),'0'*64,48000,1,1) for i in range(129)])
    crowded = NormalizedPlan(fragmented,43).validate()
    with pytest.raises(ValueError,match='owner count'):crowded.mapping(0,43)
    legacy = asdict(RuntimePolicy());legacy.pop('input_fingerprint')
    from audio_backend.fragment_runtime_contracts import CONTRACT
    assert RuntimePolicy().sha256==digest({'contract':CONTRACT,**legacy})
    assert RuntimePolicy(**legacy)==RuntimePolicy()
