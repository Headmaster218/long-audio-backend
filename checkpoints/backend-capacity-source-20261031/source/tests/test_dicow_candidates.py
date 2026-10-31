import copy,json
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
from audio_backend import dicow_candidates as dc
from audio_backend import dicow_assets as assets
from audio_backend.overlap_ownership import validate_candidates as validate_owned,own_candidates

@pytest.fixture
def clip(tmp_path):
    path=tmp_path/'audio.wav';sf.write(path,np.zeros(30*16000,dtype=np.float32),16000)
    return path

@pytest.fixture
def turns():
    return [{'speaker_local_id':'epoch:A','start':0.,'end':8.},
            {'speaker_local_id':'epoch:B','start':10.,'end':18.},
            {'speaker_local_id':'epoch:A','start':21.,'end':24.},
            {'speaker_local_id':'epoch:B','start':22.,'end':25.}]

@pytest.fixture
def config(tmp_path):
    return {'data_dir':str(tmp_path),'language':'en','dicow_python':'python',
        'dicow_model_dir':'unused','disk_reserve_bytes':0,'overlap_max_seconds':12,
        'overlap_context_seconds':1}

def ready(monkeypatch):monkeypatch.setattr(dc,'dicow_readiness',lambda c:{'ready':True})

def test_no_overlap_never_loads_model(clip,config,monkeypatch):
    monkeypatch.setattr(dc,'run_bounded',lambda *a,**k:pytest.fail('No overlap must not run model'))
    result=dc.analyze_dicow_candidates(clip,[{'speaker_local_id':'one','start':0.,'end':28.}],None,config)
    assert result['regions']==result['tracks']==[]

@pytest.mark.parametrize('languages,language,reason',[(['zh'],'en','non_english_or_mixed_language'),
    (['en','zh'],'auto','non_english_or_mixed_language'),([], 'auto','english_evidence_required')])
def test_language_fail_closed(clip,turns,config,monkeypatch,languages,language,reason):
    config.update(language=language,overlap_detected_languages=languages)
    monkeypatch.setattr(dc,'run_bounded',lambda *a,**k:pytest.fail('Language gate must not run'))
    result=dc.analyze_dicow_candidates(clip,turns,None,config)
    assert result['tracks']==[]
    assert all(r['status']=='unsupported_language' and r['reason_code']==reason for r in result['regions'])

def test_full_overlap_needs_enrollment(clip,config,monkeypatch):
    ready(monkeypatch)
    rows=[{'speaker_local_id':x,'start':0.,'end':5.} for x in ['a','b']]
    monkeypatch.setattr(dc,'run_bounded',lambda *a,**k:pytest.fail('No enrollment must not run'))
    result=dc.analyze_dicow_candidates(clip,rows,None,config)
    assert result['tracks']==[] and result['regions'][0]['status']=='needs_more_evidence'
    assert {r['conditioning_local_id'] for r in result['regions'][0]['conditioning_evidence']}=={'a','b'}

def fake_runtime(args,limits,**kwargs):
    request=json.loads(Path(args[-1]).read_text());groups=[]
    for job in request['jobs']:
        duration=sf.info(job['path']).duration
        group={'overlap_group_id':job['overlap_group_id'],'tracks':[]}
        for target in job['targets']:
            assert sf.info(target['enrollment_path']).duration<=30
            assert 'text' not in str(target['enrollment_intervals'])
            phrase={'text':'hello there','raw_text':'hello there','start':.1,'end':duration-.1,
                    'ownership_anchor':.1,'unit_kind':'native_phrase'}
            group['tracks'].append({'conditioning_local_id':target['conditioning_local_id'],
                'source_slot':target['source_slot'],'text':'hello there','raw_text':'hello there','phrases':[phrase]})
        groups.append(group)
    Path(request['output']).write_text(json.dumps({'groups':groups,'metadata':{'mock':True}}))

def test_bounded_candidate_contract_and_readonly_turns(clip,turns,config,monkeypatch):
    ready(monkeypatch);monkeypatch.setattr(dc,'run_bounded',fake_runtime)
    before=copy.deepcopy(turns)
    result=dc.analyze_dicow_candidates(clip,turns,lambda _:pytest.fail('Do not call mixture ASR again'),config)
    assert turns==before and len(result['tracks'])==2
    assert result['regions'][0]['status']=='candidate_tracks_unverified'
    for t in result['tracks']:
        assert t['speaker_id'] is None and t['profile_update_eligible'] is False
        assert t['conditioning_local_id'] in ['epoch:A','epoch:B']
        assert t['_units'][0]['unit_kind']=='native_phrase'
        assert t['provenance']['enrollment_end']<=30
    validate_owned(result,30)
    owned=own_candidates(result,20,25)
    assert len(owned['tracks'])==2
    assert all(t['identity_status']=='unresolved' for t in owned['tracks'])
    assert all(t['provenance']['ownership_anchor_kind']=='overlap_evidence_routing' for t in owned['tracks'])

def test_nearby_regions_share_one_decode_without_duplicate_phrase(clip,config,monkeypatch):
    ready(monkeypatch);calls=[]
    rows=[{'speaker_local_id':'a','start':0.,'end':5.},
          {'speaker_local_id':'b','start':6.,'end':11.},
          {'speaker_local_id':'a','start':20.,'end':20.5},
          {'speaker_local_id':'b','start':20.2,'end':21.5},
          {'speaker_local_id':'a','start':21.,'end':22.}]
    def run(args,*a,**k):
        calls.append(json.loads(Path(args[-1]).read_text()))
        fake_runtime(args,*a,**k)
    monkeypatch.setattr(dc,'run_bounded',run)
    result=dc.analyze_dicow_candidates(clip,rows,None,config)
    assert len(calls)==1 and len(calls[0]['jobs'])==1
    assert len(result['regions'])==2 and len(result['tracks'])==2
    assert result['regions'][0]['shared_support_id']==result['regions'][1]['shared_support_id']
    assert result['regions'][1]['status']=='candidate_evidence_shared'
    assert all(len(t['provenance']['related_overlap_group_ids'])==2 for t in result['tracks'])
    assert sum(t['text'].count('hello there') for t in result['tracks'])==2  # Once for each real target, not per region.
    first=own_candidates(result,0,20.3)
    second=own_candidates(result,20.3,30)
    assert len(first['tracks'])==2 and second['tracks']==[]
    assert first['tracks'][0]['text']=='hello there'
    assert first['tracks'][0]['end']>20.3  # Native phrase is not cut into fake words at a core edge.

def test_short_input_and_small_budget_fail_explicitly(clip,turns,config,monkeypatch,tmp_path):
    ready(monkeypatch);monkeypatch.setattr(dc,'run_bounded',lambda *a,**k:pytest.fail('No narrow crop inference'))
    config['overlap_max_seconds']=3
    result=dc.analyze_dicow_candidates(clip,turns,None,config)
    assert result['tracks']==[] and result['regions'][0]['reason_code']=='insufficient_dicow_context_budget'
    short=tmp_path/'short.wav';sf.write(short,np.zeros(9*16000,dtype=np.float32),16000)
    rows=[{'speaker_local_id':'a','start':0.,'end':4.}, {'speaker_local_id':'b','start':3.,'end':8.}]
    config['overlap_max_seconds']=12
    result=dc.analyze_dicow_candidates(short,rows,None,config)
    assert result['tracks']==[] and result['regions'][0]['reason_code']=='insufficient_bounded_context'

def test_core_context_cannot_starve_owned_overlap(clip,turns,config,monkeypatch):
    ready(monkeypatch);calls=[]
    rows=turns+[{'speaker_local_id':'epoch:B','start':1.,'end':2.}]
    config.update(candidate_core_start=20.,candidate_core_end=26.)
    def run(args,*a,**k):calls.append(json.loads(Path(args[-1]).read_text()));fake_runtime(args,*a,**k)
    monkeypatch.setattr(dc,'run_bounded',run)
    result=dc.analyze_dicow_candidates(clip,rows,None,config)
    assert len(calls)==1 and len(result['regions'])==1
    assert result['regions'][0]['start']>=20 and len(result['tracks'])==2

def test_shared_context_can_recenter_without_duplicate_budget(clip,config,monkeypatch):
    ready(monkeypatch);calls=[]
    rows=[{'speaker_local_id':'a','start':0.,'end':5.}, {'speaker_local_id':'b','start':6.,'end':11.},
          {'speaker_local_id':'a','start':18.,'end':24.},
          {'speaker_local_id':'b','start':18.1,'end':18.5}, {'speaker_local_id':'b','start':23.,'end':23.5}]
    config.update(candidate_core_start=15.,candidate_core_end=26.)
    def run(args,*a,**k):calls.append(json.loads(Path(args[-1]).read_text()));fake_runtime(args,*a,**k)
    monkeypatch.setattr(dc,'run_bounded',run)
    result=dc.analyze_dicow_candidates(clip,rows,None,config)
    assert len(calls[0]['jobs'])==1 and len(result['regions'])==2 and len(result['tracks'])==2
    assert result['regions'][0]['support_start']<=18.1 and result['regions'][0]['support_end']>=23.5

def test_child_failure_retains_unresolved_region(clip,turns,config,monkeypatch):
    ready(monkeypatch)
    def fail(*a,**k):raise RuntimeError('guard cap')
    monkeypatch.setattr(dc,'run_bounded',fail)
    result=dc.analyze_dicow_candidates(clip,turns,None,config)
    assert result['tracks']==[] and result['regions'][0]['status']=='separation_failed'
    assert result['regions'][0]['original_mixture_retained'] is True

def test_malformed_child_rejected(clip,turns,config,monkeypatch):
    ready(monkeypatch)
    def bad(args,*a,**k):
        req=json.loads(Path(args[-1]).read_text());Path(req['output']).write_text('{"groups":[]}')
    monkeypatch.setattr(dc,'run_bounded',bad)
    result=dc.analyze_dicow_candidates(clip,turns,None,config)
    assert result['tracks']==[] and result['regions'][0]['status']=='separation_failed'

@pytest.mark.parametrize('error,reason',[(TimeoutError('deadline'),'timeout'),
    (RuntimeError('Disk reserve reached'),'disk_reserve'),
    (RuntimeError('Host available memory below reserve'),'host_memory_reserve'),
    (RuntimeError('Service process-tree memory budget reached'),'aggregate_memory_guard')])
def test_distinct_failure_reason(clip,turns,config,monkeypatch,error,reason):
    ready(monkeypatch)
    def bad(*a,**k):raise error
    monkeypatch.setattr(dc,'run_bounded',bad)
    result=dc.analyze_dicow_candidates(clip,turns,None,config)
    assert result['regions'][0]['reason_code']==reason

def test_structured_child_guard_reason(clip,turns,config,monkeypatch):
    ready(monkeypatch)
    def bad(args,*a,**k):
        req=json.loads(Path(args[-1]).read_text())
        Path(req['output']).with_suffix('.error.json').write_text(json.dumps({'reason_code':'memory_guard','message':'cap exceeded'}))
        raise RuntimeError('python exited with code75')
    monkeypatch.setattr(dc,'run_bounded',bad)
    result=dc.analyze_dicow_candidates(clip,turns,None,config)
    assert result['regions'][0]['reason_code']=='memory_guard'

def test_enrollment_minimum_and_capacity():
    with pytest.raises(ValueError):dc.choose_enrollment([], 'a',30,minimum=1)
    rows=[{'speaker_local_id':str(i),'start':0.,'end':1.} for i in range(9)]
    with pytest.raises(ValueError):dc._turns(rows,30)

def test_asset_hash_checked_before_execution(tmp_path,monkeypatch):
    p=tmp_path/'model.py';p.write_text('safe')
    import hashlib
    spec={'runtime_files':[{'name':'model.py','bytes':4,'sha256':hashlib.sha256(b'safe').hexdigest()}]}
    monkeypatch.setattr(assets,'manifest',lambda:spec)
    assets.verify_model_directory(tmp_path)
    p.write_text('evil')
    with pytest.raises(ValueError,match='SHA256'):assets.verify_model_directory(tmp_path)
