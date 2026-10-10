"""Deterministic assembly regressions; model accuracy is measured separately."""
import copy
from audio_backend.boundaries import safe_cut, overlap_matches
from audio_backend.worker import own_core,validate_result
from audio_backend.inference.analyzer import align_tokens


def result(words):
    asr=[{'tokens':[{'id':i,'text':text,'offsets':{'from':a*1000,'to':b*1000},'p':.9}
                    for i,(a,b,text) in enumerate(words)]}]
    return {'segments':align_tokens(asr,[],{},60), 'events':[], 'metadata':{}}


def test_core_owns_words_not_five_second_display_segments():
    r=result([(28,29,'alpha'),(29,30,' beta'),(30,31,' gamma'),(31,32,' delta')])
    left=own_core(copy.deepcopy(r),0,30)
    right=own_core(copy.deepcopy(r),30,60,left['metadata']['boundary_tail'])
    assert ' '.join(s['text']for s in left['segments'])=='alpha beta'
    assert ' '.join(s['text']for s in right['segments'])=='gamma delta'


def test_native_word_intervals_are_not_clipped_to_core():
    r=result([(29.2,30.2,'crossing')])
    r=own_core(r,0,30)
    assert r['segments'][0]['end']==30.2
    assert 'native timing retained' in r['metadata']['boundary_policy']


def test_overlap_alignment_recovers_word_with_timestamp_jitter():
    previous=own_core(result([(28.8,29.2,'first'),(29.7,30.7,' second')]),0,30)
    # Same real second word shifts left of boundary on the next decode.
    current=own_core(result([(28.6,29,'first'),(29.4,30.,' second'),(30.4,31,' third')]),
                     30,60,previous['metadata']['boundary_tail'])
    assert ' '.join(s['text']for s in current['segments'])=='second third'
    assert current['metadata']['boundary_alignment_matches']==2


def test_overlap_alignment_drops_duplicate_after_timestamp_jitter():
    previous=own_core(result([(29.2,29.8,'first'),(30.3,30.8,' second')]),0,30)
    current=own_core(result([(29.8,30.6,'first'),(30.7,31.2,' second')]),30,60,
                     previous['metadata']['boundary_tail'])
    assert ' '.join(s['text']for s in current['segments'])=='second'


def test_repeated_words_do_not_align_to_distant_repetitions():
    p=[{'text':'yes','ownership_anchor':10.}]
    c=[{'text':'yes','ownership_anchor':1.},{'text':'yes','ownership_anchor':10.1}]
    assert overlap_matches(p,c)==[(0,1)]


def test_safe_cut_uses_neural_gap_and_keeps_continuous_speech_bounded():
    turns=[{'start':0.,'end':27.5},{'start':28.5,'end':34.}]
    assert safe_cut(turns,30.,27.,34.)==28.
    assert safe_cut([{'start':0.,'end':34.}],30.,27.,34.)==30.
    assert safe_cut(turns,34.,31.,34.)==34.


def test_utf8_and_latin_pieces_survive_whole_word_ownership():
    r=result([(29,29.2,' inter'),(29.2,29.5,'national'),(30,30.2,'中'),(30.2,30.4,'文')])
    left=own_core(copy.deepcopy(r),0,30)
    right=own_core(copy.deepcopy(r),30,60,left['metadata']['boundary_tail'])
    assert left['segments'][0]['text']=='international'
    assert right['segments'][0]['text']=='中文'


def test_sequence_alignment_does_not_transfer_speaker_identity():
    a=result([(29,29.5,'hello'),(30,30.5,' world')]);a['segments'][0]['speaker_local_id']='prior'
    left=own_core(a,0,30)
    b=result([(29,29.5,'hello'),(30,30.5,' world')]);b['segments'][0]['speaker_local_id']='independent'
    right=own_core(b,30,60,left['metadata']['boundary_tail'])
    assert right['segments'][0]['speaker_local_id']=='independent'


def test_gold_time_boundary_scores_use_equal_reference_scope():
    from scripts.benchmarks.boundary_metrics import boundary_score
    words=[{'start':0,'end':1,'text':'outside'}, {'start':25,'end':26,'text':'inside'},
           {'start':26,'end':27,'text':'target'}, {'start':40,'end':41,'text':'later'}]
    b=boundary_score(words,'outside inside inside target later')
    assert b['wer']['reference_items']==2
    assert b['wer']['counts']['insertion']==1 and b['wer']['counts']['match']==2
    assert b['wer']['error_rate']==.5


def test_nested_speaker_turn_never_discards_main_voice(monkeypatch):
    import numpy as np
    import soundfile as sf
    from audio_backend.inference.analyzer import AudioAnalyzer
    a=object.__new__(AudioAnalyzer);seen=[]
    def transcribe(path):
        samples,sr=sf.read(path);seen.append(len(samples)/sr)
        return {'transcription':[], 'result':{'language':'zh'}}
    monkeypatch.setattr(a,'_transcribe',transcribe)
    turns=[{'start':0.,'end':20.,'speaker_local_id':'main'},
           {'start':.2,'end':1.,'speaker_local_id':'interrupt'},
           {'start':12.,'end':13.,'speaker_local_id':'interrupt'}]
    a._transcribe_turns(np.zeros(20*16000,dtype=np.float32),turns)
    assert seen==[20.]


def test_ctc_long_window_groups_whole_word_before_ownership(tmp_path,monkeypatch):
    import sys
    import numpy as np
    import soundfile as sf
    from types import SimpleNamespace
    from audio_backend.inference.analyzer import AudioAnalyzer
    monkeypatch.setitem(sys.modules,'sherpa_onnx',SimpleNamespace())
    class Stream:
        def accept_waveform(self,sr,audio):pass
    class Recognizer:
        count=0
        def create_stream(self):return Stream()
        def decode_stream(self,stream):
            start=[0.,27.][self.count];self.count+=1
            stream.result=SimpleNamespace(lang='<|en|>',tokens=['hel','lo',' next'],
                timestamps=[27.9-start,28.1-start,28.8-start])
    a=object.__new__(AudioAnalyzer);a._sensevoice=Recognizer();a.config={}
    path=tmp_path/'long.wav';sf.write(path,np.zeros(32*16000),16000)
    r=a._transcribe_sensevoice(path)
    tokens=[t for seg in r['transcription']for t in seg['tokens']]
    assert [t['text']for t in tokens]==['hello',' next']
    assert tokens[0]['offsets']=={'from':27900,'to':28800}


def test_mixed_legacy_adapter_never_silently_drops_text():
    r=result([(1,2,'native')]);r['segments'].append({'start':3,'end':4,'text':'legacy'})
    r=own_core(r,0,10)
    assert [s['text']for s in r['segments']]==['native','legacy']
    assert 'legacy adapter' in r['metadata']['boundary_policy']


def test_raw_speaker_turns_are_clipped_independently_of_lexical_output():
    r={'segments':[],'events':[],'speaker_turns':[
        {'start':0.,'end':10.,'speaker_local_id':'a'},
        {'start':4.,'end':6.,'speaker_local_id':'b'}]}
    r=own_core(r,5,8)
    assert r['speaker_turns']==[{'start':0.,'end':3.,'speaker_local_id':'a'},
                                {'start':0.,'end':1.,'speaker_local_id':'b'}]


def test_distinct_source_tracks_never_deduplicate_each_other():
    from audio_backend.boundaries import overlap_matches
    prior=[{'text':'yes','ownership_anchor':29.9,'source_track_id':'source-a'}]
    current=[{'text':'yes','ownership_anchor':30.,'source_track_id':'source-b'}]
    assert overlap_matches(prior,current)==[]
    current[0]['source_track_id']='source-a'
    assert overlap_matches(prior,current)==[(0,0)]


def test_one_tracks_seam_does_not_delete_other_tracks_new_words():
    prior=result([(29.5,29.9,'hello')]);prior['segments'][0]['source_track_id']='A'
    previous=own_core(prior,0,30)['metadata']['boundary_tail']
    new=result([(30.1,30.3,'new')]);new['segments'][0]['source_track_id']='B'
    old=result([(29.5,29.9,'hello')]);old['segments'][0]['source_track_id']='A'
    # SourceB is listed first even though its word occurs after sourceA's word.
    new['segments']+=old['segments']
    r=own_core(new,30,60,previous)
    assert [s['text']for s in r['segments']]==['new']
    assert r['segments'][0]['source_track_id']=='B'


def test_event_candidates_keep_original_support_and_correct_core_axis():
    r={'segments':[],'events':[], 'metadata':{'event_candidates':[
        {'start':0.,'end':5.,'category_scores':{'door':{'score':.01,'source_label':'Door'}}},
        {'start':10.,'end':15.,'category_scores':{'rain':{'score':.2,'source_label':'Rain'}}}]}}
    result=own_core(r,2,12,base=28)
    a,b=result['event_candidates']
    assert (a['start'],a['end'],a['support_start'],a['support_end'])==(0.,3.,-2.,3.)
    assert (b['start'],b['end'],b['support_start'],b['support_end'])==(8.,10.,8.,13.)
    # Store adds the30s core checkpoint to both axes; originalsupport is28–33.
    assert (30+a['support_start'],30+a['support_end'])==(28.,33.)
    assert a['category_scores']['door']['score']==.01  # subthreshold retained


def test_overlap_metadata_is_explicit_and_chunk_relative():
    r={'segments':[],'events':[], 'metadata':{'overlapping_speech':{
        'regions':[{'start':1.,'end':4.,'speaker_local_ids':['a','b'],'status':'unresolved_mixture_asr'}],
        'separation_enabled':False,'original_mixture_retained':True}}}
    result=own_core(r,2,12,base=28)
    m=result['metadata']['overlapping_speech']
    assert m['regions'][0]['start']==30 and m['regions'][0]['end']==32
    assert m['time_axis']=='chunk_relative' and not m['separation_enabled']
