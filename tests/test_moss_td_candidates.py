import copy
import pytest
from audio_backend.moss_td_candidates import plan_supports,parse_support
from audio_backend.overlap_ownership import own_candidates,validate_candidates
from audio_backend.overlap_store import _provenance


def turns(end=30):
    return [{'start':0,'end':end,'speaker_local_id':'a'},
            {'start':3,'end':4,'speaker_local_id':'b'},
            {'start':8,'end':9,'speaker_local_id':'b'}]


def test_shared_support_preserves_mistimed_minority_phrase_once():
    regions,plans=plan_supports(turns(),30,{'moss_td_max_seconds':40})
    assert len(plans)==1 and len(regions)==2
    tracks=parse_support(plans[0],[dict(speaker_local_id='S09',start=15,end=16,text='minority')],8)
    bundle=validate_candidates(dict(regions=regions,tracks=tracks),30)
    result=own_candidates(bundle,0,30)
    assert len(result['tracks'])==1
    track=result['tracks'][0]
    assert (track['start'],track['end'])==(15,16)
    assert len(track['provenance']['related_overlap_group_ids'])==2
    assert track['speaker_id'] is None
    assert _provenance(track['provenance'])['association_basis']=='support_not_model_timing'


def test_disjoint_supports_and_budget_retain_all_long_overlap():
    ts=[{'start':0,'end':60,'speaker_local_id':x} for x in ['a','b']]
    regions,plans=plan_supports(ts,60,{'moss_td_max_seconds':20})
    assert sum(r['end']-r['start'] for r in regions)==60
    assert len(plans)==1
    assert [r['status'] for r in regions]==['unresolved','unresolved_budget_limit','unresolved_budget_limit']
    assert len({r['shared_support_id'] for r in regions})==3


def test_core_truncation_preserves_native_interval_not_overlap_clipping():
    ts=[{'start':0,'end':35,'speaker_local_id':x} for x in ['a','b']]
    regions,plans=plan_supports(ts,35,{'candidate_core_start':2,'candidate_core_end':32,'moss_td_max_seconds':40})
    assert [(p['start'],p['end']) for p in plans]==[(2,22),(22,32)]
    tracks=parse_support(plans[0],[dict(speaker_local_id='S00',start=0,end=20,text='whole phrase')],8)
    result=own_candidates(dict(regions=regions,tracks=tracks),2,32)
    assert result['tracks'][0]['start']==0 and result['tracks'][0]['end']==20
    assert result['tracks'][0]['provenance']['boundary_uncertain']
    assert not own_candidates(dict(regions=regions,tracks=tracks),22,32)['tracks']


def test_eight_anonymous_slots_no_native_identity_mapping():
    regions,plans=plan_supports(turns(),30,{'moss_td_max_seconds':40})
    phrases=[dict(speaker_local_id=f'S{x+10}',start=x,end=x+.5,text=str(x)) for x in range(8)]
    tracks=parse_support(plans[0],phrases,8)
    validate_candidates(dict(regions=regions,tracks=tracks),30)
    assert [t['source_slot'] for t in tracks]==list(range(8))
    assert all(t['speaker_id'] is None and not t['profile_update_eligible'] for t in tracks)
    with pytest.raises(ValueError):parse_support(plans[0],phrases,2)
    for t in tracks:t['source_slot']=8
    with pytest.raises(ValueError):validate_candidates(dict(regions=regions,tracks=tracks),30)


@pytest.mark.parametrize('a,b',[(0,0),(-1,2),(1,21),(float('nan'),2)])
def test_bad_native_times_are_not_clamped(a,b):
    _,plans=plan_supports(turns(),30,{})
    with pytest.raises(ValueError):parse_support(plans[0],[dict(speaker_local_id='S00',start=a,end=b,text='x')],8)


def test_solo_has_no_support():
    assert plan_supports(turns()[:1],30,{})==([],[])


def test_support_association_idempotent_durable_links():
    import sqlite3
    from audio_backend import overlap_store as store
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('PRAGMA foreign_keys=ON;CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT);INSERT INTO chunks VALUES("c","s");')
    store.init_schema(db)
    regions,plans=plan_supports(turns(),30,{})
    tracks=parse_support(plans[0],[dict(speaker_local_id='S07',start=15,end=16,text='retained')],8)
    owned=own_candidates(dict(regions=regions,tracks=tracks),0,30)
    chunk=dict(id='c',timeline_start=100,duration=30)
    for _ in range(2):store.commit_candidates(db,chunk,0,copy.deepcopy(owned),{})
    assert db.execute('select count(*) from overlap_tracks').fetchone()[0]==1
    assert db.execute('select count(*) from overlap_track_regions').fetchone()[0]==2
    row=store.public_track(db.execute('select * from overlap_tracks').fetchone())
    assert row['start']==115 and row['end']==116
    assert row['provenance']['support_owner_anchor']==100
    assert row['conditioned_on_speaker_id'] is None
    assert row['provenance']['model_local_speaker_label']=='S07'
    assert db.execute('pragma foreign_key_check').fetchall()==[]


def test_runner_contract_failure_and_raw_inputs(tmp_path,monkeypatch):
    import json
    import numpy as np
    import soundfile as sf
    from audio_backend import moss_td_candidates as m
    wav=tmp_path/'input.wav';sf.write(wav,np.zeros(30*16000,dtype='int16'),16000,subtype='PCM_16')
    calls=[]
    def run(args,limits,**kwargs):
        request=json.loads(open(args[-1]).read());calls.append(request)
        assert set(request['supports'][0])=={'support_id','path'}
        assert str(tmp_path/'data'/'tmp') in request['supports'][0]['path']
        assert limits.disk_reserve_bytes==5*1024**3
        outputs=[dict(support_id=s['support_id'],status='ok',phrases=[dict(speaker_local_id='S00',start=15,end=16,text='x')]) for s in request['supports']]
        open(request['output'],'w').write(json.dumps(dict(supports=outputs)))
    monkeypatch.setattr(m,'run_bounded',run)
    config=dict(data_dir=str(tmp_path/'data'),moss_td_python='/unused')
    result=m.analyze_candidates(wav,turns(),config)
    assert len(result['tracks'])==1
    def fail(*a,**k):raise TimeoutError('bounded')
    monkeypatch.setattr(m,'run_bounded',fail)
    result=m.analyze_candidates(wav,turns(),config)
    assert not result['tracks'] and all(r['reason_code']=='candidate_timeout' for r in result['regions'])


def test_three_labels_are_explicitly_quality_unverified():
    _,plans=plan_supports(turns(),30,{})
    tracks=parse_support(plans[0],[dict(speaker_local_id=f'S{i}',start=i,end=i+.5,text='x') for i in range(3)],8)
    assert all(_provenance(t['provenance'])['possible_missing_minority'] for t in tracks)
    assert all(t['provenance']['multi_speaker_quality_unverified'] for t in tracks)


def test_many_short_supports_bounded_and_not_lost():
    ts=[{'start':0,'end':30,'speaker_local_id':x} for x in ['a','b']]
    regions,plans=plan_supports(ts,30,{'moss_td_max_support_seconds':1})
    assert len(plans)==4 and len(regions)==30
    assert sum(r['end']-r['start'] for r in regions)==30


def test_even_pair_minority_and_count_mismatch_remain_uncertain():
    _,plans=plan_supports(turns(),30,{})
    track=parse_support(plans[0],[dict(speaker_local_id='S01',start=1,end=2,text='one')],8)[0]
    p=_provenance(track['provenance'])
    assert p['possible_missing_minority'] and p['count_mismatch_uncertain']
    assert p['emitted_label_count']==1 and p['native_detected_label_count']==2
    assert p['multi_speaker_quality_unverified'] is False


def test_cancel_not_swallowed(tmp_path,monkeypatch):
    import numpy as np,soundfile as sf
    from audio_backend import moss_td_candidates as m
    wav=tmp_path/'input.wav';sf.write(wav,np.zeros(30*16000),16000)
    def stop(*a,**k):raise InterruptedError('cancel')
    monkeypatch.setattr(m,'run_bounded',stop)
    with pytest.raises(InterruptedError):m.analyze_candidates(wav,turns(),dict(data_dir=str(tmp_path),moss_td_python='/unused'))


def test_boolean_slot_rejected():
    regions,plans=plan_supports(turns(),30,{})
    tracks=parse_support(plans[0],[dict(speaker_local_id='S01',start=1,end=2,text='one')],8)
    tracks[0]['source_slot']=True
    with pytest.raises(ValueError):validate_candidates(dict(regions=regions,tracks=tracks),30)


def test_schema_failure_keeps_bounded_private_diagnostic(tmp_path,monkeypatch):
    import json,hashlib,sqlite3
    import numpy as np,soundfile as sf
    from audio_backend import moss_td_candidates as m,overlap_store as store
    wav=tmp_path/'input.wav';sf.write(wav,np.zeros(30*16000),16000)
    def bad(args,limits,**kwargs):
        req=json.loads(open(args[-1]).read());sid=req['supports'][0]['support_id']
        open(req['output'],'w').write(json.dumps({'supports':[dict(support_id=sid,status='unresolved',reason_code='unparsed_text',raw_text='malformed hypothetical text',schema_errors=['unparsed_text'],phrases=[])]}))
    monkeypatch.setattr(m,'run_bounded',bad)
    p=m.analyze_candidates(wav,turns(),dict(data_dir=str(tmp_path),moss_td_python='/unused'))
    assert not p['tracks'] and p['regions'][0]['schema_error_count']==1
    assert p['diagnostics'][0]['captures']['raw_text']['text']=='malformed hypothetical text'
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT);INSERT INTO chunks VALUES("c","s");')
    store.init_schema(db)
    store.commit_candidates(db,dict(id='c',duration=30,timeline_start=0),0,own_candidates(p,0,30),{})
    r=store.public_region(db.execute('select * from overlap_regions').fetchone())
    assert r['schema_error_count']==1
    assert 'malformed hypothetical text' not in json.dumps(r)
    assert db.execute('select count(*) from overlap_support_diagnostics').fetchone()[0]==1
    assert r['raw_output_sha256']==hashlib.sha256(b'malformed hypothetical text').hexdigest()
