"""Candidate ownership/error isolation only; no simulated acoustic-quality claim."""
import copy,sys
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
from audio_backend.overlap_ownership import own_candidates,validate_candidates
from audio_backend.inference.analyzer import AudioAnalyzer


def bundle():
    r={'overlap_group_id':'g','start':1.,'end':3.,'support_start':0.,'support_end':4.,
       'detected_source_count':2,'detected_speaker_local_ids':['a','b'],'capacity':2,
       'status':'candidate_tracks_unverified','original_mixture_retained':True}
    tracks=[]
    for slot in range(2):
        tid=f'g:scope0:source{slot}'
        tracks.append({'source_track_id':tid,'overlap_group_id':'g','scope_id':'scope0','source_slot':slot,
            'start':0.,'end':4.,'support_start':0.,'support_end':4.,'text':'padding crossing later',
            'raw_text':'padding crossing later','identity_status':'unresolved','quality_status':'unverified',
            'speaker_id':None,'profile_update_eligible':False,
            '_units':[{'start':a,'end':b,'ownership_anchor':c,'text':text,'raw_text':text,'source_track_id':tid,'confidence':None}
                      for a,b,c,text in [(0,.8,.4,'padding'),(1.8,2.3,2.05,' crossing'),(3.2,3.8,3.5,' later')]]})
    return {'regions':[r],'tracks':tracks,'metadata':{}}


def test_both_source_words_preserve_native_bounds_and_exclude_context():
    b=bundle();validate_candidates(b,4)
    out=own_candidates(b,2,4,base=28)
    assert len(out['tracks'])==2
    assert [t['text']for t in out['tracks']]==['crossing','crossing']
    assert all(abs(t['start']+.2)<1e-8 and abs(t['end']-.3)<1e-8 for t in out['tracks'])
    assert all(t['support_start']==-2 and t['support_end']==2 for t in out['tracks'])
    assert out['regions'][0]['start']==0 and out['regions'][0]['end']==1
    assert all(t['speaker_id']is None and not t['profile_update_eligible']for t in out['tracks'])
    assert 'padding' not in str(out['tracks']) and 'later' not in str(out['tracks'])


def test_unresolved_and_no_text_regions_survive_without_fabrication():
    b=bundle();b['regions'][0]['status']='separation_failed';b['tracks']=[]
    assert own_candidates(b,2,4)['regions'][0]['status']=='separation_failed'
    b=bundle()
    for t in b['tracks']:t.update(text='',raw_text='',_units=[],quality_status='no_text')
    out=own_candidates(b,2,4);assert len(out['tracks'])==2 and all(not t['text']for t in out['tracks'])


def analyzer(tmp_path,monkeypatch):
    a=object.__new__(AudioAnalyzer);a.closed=False;a.backend='sensevoice';a.threads=2;a._sensevoice=None
    a.config={'chinese_script':'as_is','overlap_backend':'mossformer2_experimental'}
    a.paths={'asr':tmp_path/'model.int8.onnx'}
    turns=[{'start':0.,'end':4.,'speaker_local_id':'a'},{'start':1.,'end':3.,'speaker_local_id':'b'}]
    monkeypatch.setattr(a,'_events',lambda samples:([],[]))
    monkeypatch.setattr(a,'_diarize',lambda samples:(turns,{},512))
    monkeypatch.setattr(a,'_transcribe_turns',lambda *args:{'transcription':[{'text':'original mixture',
        'offsets':{'from':0,'to':4000},'tokens':[{'id':1,'text':'original mixture','offsets':{'from':0,'to':4000}}]}],
        'result':{'language':'en'}})
    p=tmp_path/'input.wav';sf.write(p,np.full(64000,.03,dtype=np.float32),16000)
    return a,p


def test_candidate_failure_does_not_modify_mixture_and_cancel_propagates(tmp_path,monkeypatch):
    from audio_backend import overlap_candidates as adapter
    a,p=analyzer(tmp_path,monkeypatch)
    def fail(*args):raise RuntimeError('separator failed')
    monkeypatch.setattr(adapter,'analyze_overlap_candidates',fail)
    r=a.analyze(str(p));assert r['segments'][0]['text']=='original mixture'
    assert len(r['speaker_turns'])==2
    assert r['overlap_candidates']['regions'][0]['status']=='separation_failed'
    def cancel(*args):raise InterruptedError('cancel')
    monkeypatch.setattr(adapter,'analyze_overlap_candidates',cancel)
    with pytest.raises(InterruptedError):a.analyze(str(p))


def test_invalid_candidate_identity_fails_only_candidate_branch(tmp_path,monkeypatch):
    from audio_backend import overlap_candidates as adapter
    a,p=analyzer(tmp_path,monkeypatch);bad=bundle();bad['tracks'][0]['speaker_id']='invented-person'
    monkeypatch.setattr(adapter,'analyze_overlap_candidates',lambda *args:bad)
    r=a.analyze(str(p));assert r['segments'][0]['text']=='original mixture'
    assert r['overlap_candidates']['tracks']==[] and r['overlap_candidates']['regions'][0]['status']=='separation_failed'


def test_default_does_not_load_optional_separator(tmp_path,monkeypatch):
    from audio_backend import overlap_candidates as adapter
    a,p=analyzer(tmp_path,monkeypatch);a.config['overlap_backend']='none'
    monkeypatch.setattr(adapter,'analyze_overlap_candidates',lambda *args:pytest.fail('Default must notrunseparator'))
    r=a.analyze(str(p));assert r['overlap_candidates']is None


def test_ctc_anchor_just_before_overlap_onset_does_not_erase_recovered_voice():
    b=bundle();b['regions'][0].update(start=.13,end=1.55,support_start=0.,support_end=1.55)
    b['tracks']=b['tracks'][:1];t=b['tracks'][0]
    t.update(start=0.,end=1.55,support_start=0.,support_end=1.55,text='Okay.',raw_text='Okay.')
    t['_units']=[dict(start=.12,end=1.55,ownership_anchor=.12,text='Okay.',raw_text='Okay.',source_track_id=t['source_track_id'],confidence=None)]
    out=own_candidates(b,0,1.55)
    assert out['tracks'][0]['text']=='Okay.' and out['tracks'][0]['start']==.12


def test_candidate_adapter_receives_actual_language_evidence(tmp_path,monkeypatch):
    from audio_backend import overlap_candidates as adapter
    a,p=analyzer(tmp_path,monkeypatch);seen=[]
    def candidates(path,turns,callback,config):
        seen.append(config);return bundle()
    monkeypatch.setattr(adapter,'analyze_overlap_candidates',candidates)
    r=a.analyze(str(p))
    assert seen[0]['overlap_detected_languages']==['en']
    assert 'overlap_detected_languages' not in a.config
    assert r['segments'][0]['text']=='original mixture'


def phrase_bundle():
    b=bundle();b['regions'][0].update(start=1.,end=2.,support_start=0.,support_end=4.)
    second=dict(b['regions'][0],overlap_group_id='g1',start=2.5,end=3.,status='candidate_evidence_shared')
    b['regions'].append(second);b['tracks']=b['tracks'][:1];t=b['tracks'][0]
    t.update(text='first second',raw_text='first second',provenance={'related_overlap_group_ids':['g','g1'],'shared_support_id':'support0'})
    t['_units']=[dict(start=0.,end=2.,ownership_anchor=1.,text='first',raw_text='first',
        source_track_id=t['source_track_id'],unit_kind='native_phrase',related_overlap_group_ids=['g'],
        ownership_anchor_kind='overlap_evidence_routing',boundary_uncertain=True),
        dict(start=1.8,end=3.5,ownership_anchor=2.5,text='second',raw_text='second',
        source_track_id=t['source_track_id'],unit_kind='native_phrase',related_overlap_group_ids=['g1'],
        ownership_anchor_kind='overlap_evidence_routing',boundary_uncertain=True)]
    return b


def test_coarse_phrase_routes_once_and_rehomes_shared_region_without_fake_times():
    b=phrase_bundle();left=own_candidates(copy.deepcopy(b),0,2.5);right=own_candidates(copy.deepcopy(b),2.5,4)
    assert [t['text']for t in left['tracks']]==['first']
    assert [t['text']for t in right['tracks']]==['second']
    t=right['tracks'][0];assert t['overlap_group_id']=='g1'
    assert t['source_track_id']==b['tracks'][0]['source_track_id']
    assert t['provenance']['related_overlap_group_ids']==['g1']
    assert abs(t['start']+.7)<1e-8 and t['end']==1. # Original1.8–3.5phrase, notclipped tooverlap2.5–3.
    native=t['provenance']['native_phrase_intervals'][0]
    assert abs(native['start']+.7)<1e-8 and native['end']==1.
    assert native['text']=='second' and native['ownership_anchor']==0.
    assert t['provenance']['boundary_uncertain'] and t['provenance']['native_time_axis']=='core_relative'


def test_unowned_phrase_does_not_claim_queryable_shared_evidence():
    b=phrase_bundle();b['tracks'][0]['_units']=b['tracks'][0]['_units'][:1]
    b['tracks'][0]['_units'][0].update(end=3.,related_overlap_group_ids=['g','g1'])
    r=own_candidates(b,2.5,4)
    assert r['tracks']==[]
    assert r['regions'][0]['status']=='unresolved_boundary_evidence'
    assert r['regions'][0]['reason_code']=='candidate_phrase_owned_outside_core'


def test_coarse_phrase_concatenation_adds_only_a_separator():
    b=phrase_bundle();r=own_candidates(b,0,4)
    assert r['tracks'][0]['text']=='first second'
    assert [u['text']for u in r['tracks'][0]['provenance']['native_phrase_intervals']]==['first','second']


def test_core_hints_forwarded_without_trimming_enrollment_audio(tmp_path,monkeypatch):
    from audio_backend import overlap_candidates as adapter
    a,p=analyzer(tmp_path,monkeypatch);seen=[]
    def candidates(path,turns,callback,config):
        seen.append((sf.info(path).duration,turns,config));return bundle()
    monkeypatch.setattr(adapter,'analyze_overlap_candidates',candidates)
    a.analyze(str(p),analysis_options={'candidate_core_start':2.,'candidate_core_end':3.})
    duration,turns,cfg=seen[0]
    assert duration==4 and turns[0]['start']==0 and turns[0]['end']==4
    assert cfg['candidate_core_start']==2 and cfg['candidate_core_end']==3


def test_adjacent_core_candidates_survive_restart_once_without_profile_changes(tmp_path):
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    store=Store(tmp_path/'state.sqlite',settings);sid=store.create_session('candidate recovery')['id']
    cid,_=store.add_chunk(sid,'audio','digest',tmp_path/'source.wav',4,offset=100)
    first=store.claim()
    left={'segments':[{'start':0.,'end':1.,'text':'original mixture'}],'events':[],
          'overlap_candidates':own_candidates(copy.deepcopy(phrase_bundle()),0,2.5)}
    assert store.commit_window(first,0,2.5,left)
    restarted=Store(store.path,settings);restarted.recover();second=restarted.claim()
    assert second['next_offset']==2.5
    right={'segments':[],'events':[],'overlap_candidates':own_candidates(copy.deepcopy(phrase_bundle()),2.5,4)}
    assert restarted.commit_window(second,2.5,4,right)
    assert not restarted.commit_window(second,2.5,4,right)
    page=restarted.overlap_candidates(sid)
    assert [t['text']for t in page['tracks']]==['first','second']
    assert page['tracks'][0]['start']==100 and abs(page['tracks'][1]['start']-101.8)<1e-8
    assert page['tracks'][1]['end']==103.5
    assert all(t['speaker_id']is None and not t['enrollment_available']for t in page['tracks'])
    assert restarted.speakers(sid)==[]
    assert [t['text']for t in restarted.results(sid)['segments']]==['original mixture']
    related=page['tracks'][1]['provenance']['related_overlap_group_ids']
    assert related==[page['regions'][1]['overlap_group_id']]
    provenance=page['tracks'][1]['provenance']
    assert provenance['native_time_axis']=='session_absolute'
    native=provenance['native_phrase_intervals'][0]
    assert abs(native['start']-101.8)<1e-8 and native['end']==103.5 and native['ownership_anchor']==102.5
    assert native['text']=='second' and native['boundary_uncertain']
