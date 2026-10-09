"""Registry safety only; see real benchmark for acoustic accuracy."""
from pathlib import Path
import json
from audio_backend.settings import Settings
from audio_backend.storage import Store

def commit(store,sid,key,version='model-a',seconds=5,fingerprint=None):
    cid,_=store.add_chunk(sid,key,key,Path('/tmp/test-evidence.wav'),10)
    chunk=store.claim()
    metadata={'speaker_embedding_version':version,'speaker_embedding_evidence':{'s':{'clean_speech_seconds':seconds,'overlap_excluded':True,'source':'neural_turns'}}}
    if fingerprint is not None:metadata['speaker_embedding_sha256']=fingerprint
    result={'segments':[{'start':0,'end':1,'text':'test','speaker_local_id':'s','speaker_embedding':[1.]+[0.]*7}], 'events':[], 'metadata':metadata}
    store.commit_window(chunk,0,10,result)
    return store.results(sid)['segments'][-1]

def test_same_dimensions_different_model_abstains_and_requires_enrollment(tmp_path):
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False))
    sid=store.create_session('a')['id'];original=commit(store,sid,'one')
    incompatible=commit(store,sid,'two',version='model-b')
    assert incompatible['speaker_id'] is None
    assert incompatible['speaker_status']=='unknown_model_version'
    assert incompatible['enrollment_available']
    enrolled=store.enroll_speaker(sid,'Verified',incompatible['id'])
    matched=commit(store,sid,'three',version='model-b')
    assert matched['speaker_id']==enrolled['id']!=original['speaker_id']
    with store.db() as db:
        row=db.execute('SELECT * FROM speakers WHERE id=?',(enrolled['id'],)).fetchone()
        assert row['embedding_version']=='model-b'
        assert json.loads(row['embedding_evidence'])['clean_speech_seconds']==5

def test_short_embedding_neither_enrolls_nor_creates_observation(tmp_path):
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False))
    sid=store.create_session('a')['id'];row=commit(store,sid,'one',seconds=1.5)
    assert row['speaker_status']=='unknown_insufficient_speech'
    assert not row['enrollment_available'] and not store.speakers(sid)
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==0

def test_legacy_templates_not_silently_reversioned(tmp_path):
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False))
    sid=store.create_session('a')['id'];original=commit(store,sid,'one',version=None)
    new=commit(store,sid,'two')
    assert new['speaker_id'] is None
    with store.db() as db:assert db.execute('SELECT embedding_version FROM speakers WHERE id=?',(original['speaker_id'],)).fetchone()[0] is None

def test_centroid_uses_explicit_merge_only_and_undo_restores(tmp_path):
    import math
    settings=Settings(data_dir=tmp_path,start_worker=False,speaker_profile_strategy='centroid')
    store=Store(tmp_path/'db',settings);sid=store.create_session('a')['id']
    left=[1.]+[0.]*7;right=[0.,1.]+[0.]*6;between=[1/math.sqrt(2),1/math.sqrt(2)]+[0.]*6
    with store.db() as db:
        a=store._speaker(db,sid,left)[0];b=store._speaker(db,sid,right)[0]
        assert store._speaker(db,sid,between)[0] is None
    store.correct_speaker(sid,b,merge_into=a)
    with store.db() as db:
        before=[r['embedding'] for r in db.execute('SELECT embedding FROM speakers ORDER BY id')]
        for _ in range(20):assert store._speaker(db,sid,between)[0]==a
        assert before==[r['embedding'] for r in db.execute('SELECT embedding FROM speakers ORDER BY id')]
    store.correct_speaker(sid,b,undo_merge=True)
    with store.db() as db:assert store._speaker(db,sid,between)[0] is None


def test_same_named_model_different_bytes_cannot_match(tmp_path):
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False))
    sid=store.create_session('a')['id'];first=commit(store,sid,'one',fingerprint='a'*64)
    assert commit(store,sid,'same',fingerprint='a'*64)['speaker_id']==first['speaker_id']
    assert commit(store,sid,'replacement',fingerprint='b'*64)['speaker_status']=='unknown_model_version'

def test_explicit_examples_bounded_private_and_no_automatic_drift(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,max_speaker_examples_per_person=3,max_matched_speaker_observations_per_group=2)
    store=Store(tmp_path/'db',settings);sid=store.create_session('a')['id'];original=commit(store,sid,'one')
    assert not original['profile_example_available']
    pid=original['speaker_id'];second=commit(store,sid,'two')
    assert second['profile_example_available'] and not second['enrollment_available']
    added=store.add_speaker_example(sid,pid,second['id']);assert added['template_count']==2
    third=commit(store,sid,'three');store.add_speaker_example(sid,pid,third['id'])
    fourth=commit(store,sid,'four')
    import pytest
    with pytest.raises(OverflowError):store.add_speaker_example(sid,pid,fourth['id'])
    assert all('embedding' not in r for r in store.results(sid)['segments'])
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_examples').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM speaker_observations WHERE matched_speaker_id IS NOT NULL').fetchone()[0]<=2
        db.execute('DELETE FROM speakers')
        assert db.execute('SELECT COUNT(*) FROM speaker_examples').fetchone()[0]==0

def test_raw_turns_survive_missing_asr_and_share_identity(tmp_path):
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False));sid=store.create_session('a')['id']
    cid,_=store.add_chunk(sid,'x','x',Path('/tmp/turn.wav'),10);chunk=store.claim()
    t={'start':0,'end':8,'speaker_local_id':'voice','speaker_embedding':[1.]+[0.]*7}
    store.commit_window(chunk,0,10,{'segments':[],'events':[],'speaker_turns':[t]})
    turns=store.speaker_turns(sid)['speaker_turns'];assert len(turns)==1 and turns[0]['speaker_id']
    assert 'embedding' not in turns[0] and 'speaker_embedding' not in turns[0]
    assert store.results(sid)['segments']==[]
    store.delete_session(sid)
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_turns').fetchone()[0]==0

def test_event_precision_metadata_survives_persistence(tmp_path):
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False));sid=store.create_session('a')['id']
    cid,_=store.add_chunk(sid,'x','x',Path('/tmp/event.wav'),10);chunk=store.claim()
    event={'start':0,'end':5,'label':'door_slam','score':.7,'time_precision':'window_support','source_label':'Slam','onset_precision_seconds':None,'method':'audioset_classifier'}
    store.commit_window(chunk,0,10,{'segments':[],'events':[event]})
    saved=store.events(sid)['events'][0]
    for key in ['time_precision','source_label','onset_precision_seconds','method']:assert saved[key]==event[key]

def test_short_query_can_match_but_not_enroll_or_adapt(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,speaker_min_query_seconds=1.)
    store=Store(tmp_path/'db',settings);sid=store.create_session('a')['id'];known=commit(store,sid,'one')
    matched=commit(store,sid,'short-query',seconds=1.2)
    assert matched['speaker_id']==known['speaker_id'] and not matched['profile_example_available']
    other=store.create_session('other','new-group')['id'];fresh=commit(store,other,'short-first',seconds=1.2)
    assert fresh['speaker_id'] is None and fresh['speaker_status']=='unknown_short_enrollment'
    assert not store.speakers(other) and not fresh['enrollment_available']

def test_global_private_template_bound_survives_deleted_sessions(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,max_speaker_templates_total=1,max_speaker_observations_total=2)
    store=Store(tmp_path/'db',settings);sid=store.create_session('a','a')['id'];commit(store,sid,'one');store.delete_session(sid)
    other=store.create_session('b','b')['id'];row=commit(store,other,'two')
    assert row['speaker_status']=='unknown_capacity'
    with store.db() as db:assert store._template_count(db)==1

def test_overlapping_turns_lossless_with_independent_window_scope(tmp_path):
    from audio_backend.worker import own_core
    store=Store(tmp_path/'db',Settings(data_dir=tmp_path,start_worker=False));sid=store.create_session('overlap')['id']
    cid,_=store.add_chunk(sid,'x','x',Path('/tmp/overlap.wav'),10);chunk=store.claim()
    turns=[{'start':0.,'end':8.,'speaker_local_id':'A','speaker_embedding':[1.]+[0.]*7},
           {'start':3.,'end':9.,'speaker_local_id':'B','speaker_embedding':[0.,1.]+[0.]*6}]
    result={'speaker_turns':turns,'segments':[],'events':[]}
    clipped=own_core(result,2,7)
    assert [(r['start'],r['end']) for r in clipped['speaker_turns']]==[(0.,5.),(1.,5.)]
    # Actual commit offset is0 here; both clipped voices retain their full overlap.
    store.commit_window(chunk,0,10,clipped)
    page=store.speaker_turns(sid,limit=1);second=store.speaker_turns(sid,after=page['next_cursor'])
    rows=page['speaker_turns']+second['speaker_turns']
    assert len(rows)==2 and all(r['overlap'] for r in rows)
    assert [(r['start'],r['end']) for r in rows]==[(0.,5.),(1.,5.)]
    assert len({r['speaker_id'] for r in rows})==2
    assert all(r['speaker_window_start']==0 for r in rows)
