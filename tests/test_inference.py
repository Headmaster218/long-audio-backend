from audio_backend.events.provenance import EventArtifactHashes
import numpy as np
import pytest
import soundfile as sf
from audio_backend.inference import AudioAnalyzer, ModelMissingError
from audio_backend.inference.analyzer import align_tokens, event_category

def test_missing_models(tmp_path):
    with pytest.raises(ModelMissingError,match='setup_models'):
        AudioAnalyzer({'model_dir':str(tmp_path),'whisper_cli':str(tmp_path/'missing')})

def test_independent_turn_split():
    asr=[{'tokens':[{'id':100,'text':'hello','offsets':{'from':0,'to':1000},'p':.9},
                    {'id':101,'text':' world','offsets':{'from':1000,'to':2000},'p':.8}]}]
    turns=[{'start':0,'end':.9,'speaker_local_id':'a'},{'start':1,'end':2,'speaker_local_id':'b'}]
    r=align_tokens(asr,turns,{'a':[1.,0.],'b':[0.,1.]},2)
    assert [x['speaker_local_id'] for x in r]==['a','b']
    assert [x['text'] for x in r]==['hello','world']

def test_overlap_unknown():
    asr=[{'text':'hi','offsets':{'from':0,'to':1000}}]
    turns=[{'start':0,'end':1,'speaker_local_id':'a'},{'start':0,'end':1,'speaker_local_id':'b'}]
    r=align_tokens(asr,turns,{},1)
    assert r[0]['speaker_local_id'] is None and r[0]['speaker_embedding'] is None

def test_event_mapping():
    assert event_category('Subway, metro, underground')=='subway'
    assert event_category('Rain on surface')=='rain'
    assert event_category('Doorbell')=='door'
    assert event_category('Speech') is None

@pytest.mark.parametrize('backend', ['whisper', 'sensevoice'])
def test_silence_fast_path_without_models_loaded(tmp_path, monkeypatch, backend):
    # Presence validation is separate; mock only construction paths, never neural outputs.
    a=object.__new__(AudioAnalyzer);a.closed=False;a.config={}
    a.backend=backend;a.threads=2;a._sensevoice=None
    a.paths={'asr':tmp_path/('model.int8.onnx' if backend=='sensevoice' else 'ggml-small-q5_1.bin')}
    a._event_artifact_hashes = EventArtifactHashes()
    for key in ('events', 'labels'):
        a.paths[key] = tmp_path / key; a.paths[key].write_bytes(b'synthetic artifact')
    for method in ('_diarize','_events','_transcribe','_transcribe_turns'):
        monkeypatch.setattr(a,method,lambda *args:pytest.fail('Silence must not load or execute models'))
    p=tmp_path/'silence.wav';sf.write(p,np.zeros(16000,dtype=np.float32),16000)
    r=a.analyze(str(p));assert r['segments']==[] and r['events'][0]['label']=='silence'
    m=r['metadata']
    assert m['asr_backend']==backend and m['models']['asr']==a.paths['asr'].name
    assert m['stage_seconds']=={'events':0.,'diarization':0.,'asr':0.}
    assert m['models_executed']==[] and m['model_execution']=='not_run_silence' and a._sensevoice is None
    assert m['asr_model_license_url'].startswith('https://github.com/')
    assert m['timestamp_semantics'] and m['warnings']
    if backend=='sensevoice':
        assert 'Whisper' not in ' '.join(m['warnings'])
        assert 'CTC' in m['timestamp_semantics'] and 'custom' in m['asr_model_license']
    else:
        assert 'Whisper' in ' '.join(m['warnings']) and m['asr_model_license']=='MIT'
    a.close()
    with pytest.raises(RuntimeError,match='closed'):a.analyze(str(p))

def test_utf8_fragments_and_zero_duration_preserved():
    raw='廣'.encode()
    tokens=[{'id':100,'text':raw[:2].decode('utf8','surrogateescape'),'offsets':{'from':0,'to':300}},
            {'id':101,'text':raw[2:].decode('utf8','surrogateescape'),'offsets':{'from':300,'to':500}},
            {'id':102,'text':'州','offsets':{'from':1000,'to':1000}}]
    r=align_tokens([{'tokens':tokens}],[],{},1)
    assert ''.join(x['text'] for x in r)=='廣州'
    assert all(0<=x['start']<x['end']<=1 for x in r)

def test_optional_sensevoice_does_not_require_whisper(tmp_path):
    for name in ['sensevoice/model.int8.onnx','sensevoice/tokens.txt','speaker.onnx',
                 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx',
                 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
                 'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv']:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
    analyzer=AudioAnalyzer({'asr_backend':'sensevoice','model_dir':str(tmp_path),
                            'whisper_cli':str(tmp_path/'absent-whisper')})
    assert analyzer._sensevoice is None
    analyzer.close()

def test_sensevoice_rejects_vulkan():
    with pytest.raises(ValueError,match='CPU-only'):
        AudioAnalyzer({'asr_backend':'sensevoice','device':'vulkan'})

def test_ctc_anchor_attribution_and_turn_end():
    asr=[{'tokens':[{'id':0,'text':'hello','offsets':{'from':100,'to':3000},'_ctc_anchor':True,'p':0.}]}]
    turns=[{'start':0,'end':1,'speaker_local_id':'a'}, {'start':1.1,'end':3,'speaker_local_id':'b'}]
    r=align_tokens(asr,turns,{},3)
    assert r[0]['speaker_local_id']=='a' and r[0]['end']==1

def test_ctc_subwords_preserve_whole_english_words():
    from audio_backend.inference.analyzer import join_ctc_words
    pieces=['M','r','.',' Qui','ter','是','bu','il','d',' AI']
    result=join_ctc_words([{'text':t,'offsets':{'from':i*100,'to':(i+1)*100}}for i,t in enumerate(pieces)])
    assert [x['text']for x in result]==['Mr.',' Quiter','是','build',' AI']


@pytest.mark.parametrize('backend', ['whisper', 'sensevoice'])
def test_segment_provenance_and_backend_warnings(tmp_path, monkeypatch, backend):
    # Structural presentation test only: no synthetic output is used as model evidence.
    a=object.__new__(AudioAnalyzer);a.closed=False
    a.config={'chinese_script':'as_is','recover_missing_speech':False}
    a.backend=backend;a.threads=2;a._sensevoice=None
    a.paths={'asr':tmp_path/('model.int8.onnx' if backend=='sensevoice' else 'ggml-small-q5_1.bin')}
    a._event_artifact_hashes = EventArtifactHashes()
    for key in ('events', 'labels'):
        a.paths[key] = tmp_path / key; a.paths[key].write_bytes(b'synthetic artifact')
    monkeypatch.setattr(a,'_events',lambda samples:([],[]))
    monkeypatch.setattr(a,'_diarize',lambda samples:([{'start':0.,'end':1.,'speaker_local_id':'a'}],{},512))
    monkeypatch.setattr(a,'_transcribe',lambda path:{'transcription':[{'tokens':[
        {'id':10,'text':'廣州 test','offsets':{'from':100,'to':900},'p':.8}]}],
        'result':{'language':'zh'}})
    path=tmp_path/'unit-structure.wav';sf.write(path,np.full(16000,.05,dtype=np.float32),16000)
    result=a.analyze(str(path));segment=result['segments'][0];metadata=result['metadata']
    assert segment['asr_backend']==backend
    assert segment['raw_text']==segment['text']=='廣州 test'
    assert segment['timestamp_kind']==metadata['timestamp_semantics']
    if backend=='sensevoice':
        assert segment['confidence'] is None and 'Whisper' not in ' '.join(metadata['warnings'])
    else:
        assert segment['confidence']==.8 and 'Whisper' in ' '.join(metadata['warnings'])

def test_sensevoice_multispeaker_routes_to_turn_decode(tmp_path, monkeypatch):
    # Routing regression test, not evidence of recognition accuracy.
    a=object.__new__(AudioAnalyzer);a.closed=False;a.backend='sensevoice';a.threads=2
    a.config={'chinese_script':'as_is'};a._sensevoice=None;a.paths={'asr':tmp_path/'model.int8.onnx'}
    a._event_artifact_hashes = EventArtifactHashes()
    for key in ('events', 'labels'):
        a.paths[key] = tmp_path / key; a.paths[key].write_bytes(b'synthetic artifact')
    turns=[{'start':0.,'end':1.,'speaker_local_id':'a'}, {'start':1.,'end':2.,'speaker_local_id':'b'}]
    monkeypatch.setattr(a,'_events',lambda samples:([],[]))
    monkeypatch.setattr(a,'_diarize',lambda samples:(turns,{},512))
    monkeypatch.setattr(a,'_transcribe',lambda path:pytest.fail('Multi-speaker input must not force whole-window decoding'))
    called=[]
    def decode(samples, detected):
        called.append(detected)
        return {'transcription':[],'result':{'language':'zh'},'asr_turn_batches':2}
    monkeypatch.setattr(a,'_transcribe_turns',decode)
    path=tmp_path/'route.wav';sf.write(path,np.full(32000,.05,dtype=np.float32),16000)
    result=a.analyze(str(path))
    assert called==[turns] and result['metadata']['asr_strategy']=='ctc_speaker_turns'


def test_turn_decode_preserves_ctc_anchor_flags(monkeypatch):
    a=object.__new__(AudioAnalyzer)
    monkeypatch.setattr(a,'_transcribe',lambda path:{'transcription':[
        {'text':'test','offsets':{'from':100,'to':500},'tokens':[
            {'id':0,'text':'test','offsets':{'from':100,'to':500},'_ctc_anchor':True,'p':0.}]}],
        'result':{'language':'en'}})
    turns=[{'start':0.,'end':2.,'speaker_local_id':'a'}, {'start':3.,'end':5.,'speaker_local_id':'b'}]
    result=a._transcribe_turns(np.full(80000,.05,dtype=np.float32),turns)
    tokens=[t for s in result['transcription']for t in s['tokens']]
    assert len(tokens)==2 and all(t['_ctc_anchor']for t in tokens)
    assert tokens[0]['offsets']['from']==100 and tokens[1]['offsets']['from']==2750
