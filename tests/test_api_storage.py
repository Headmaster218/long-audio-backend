"""Infrastructure tests use explicit fake inference. These do NOT establish ML accuracy."""
import io
import math
import wave
import pytest
from fastapi.testclient import TestClient
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.app import create_app
from audio_backend.worker import validate_result

@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, disk_reserve_bytes=0, start_worker=False)
@pytest.fixture
def store(settings): return Store(settings.data_dir/'test.sqlite',settings)
def result(vec=None):
    return {'segments':[{'start':0,'end':1,'text':'test only','speaker_local_id':'s1','speaker_embedding':vec or [1.0]+[0.0]*7}], 'events':[{'start':0,'end':1,'label':'mock','score':.9}]}
def chunk(store,sid,key='k',duration=30,offset=None):
    cid,_=store.add_chunk(sid,key,key,'/fake/audio.wav',duration,offset)
    return store.claim()

def test_checkpoint_restart_and_atomicity(store,settings):
    sid=store.create_session('test')['id']; c=chunk(store,sid,duration=60)
    assert store.commit_window(c,0,30,result())
    assert not store.commit_window(c,0,30,result())
    reloaded=Store(store.path,settings); reloaded.recover(); c2=reloaded.claim()
    assert c2['next_offset']==30
    assert reloaded.commit_window(c2,30,60,result())
    assert reloaded.chunk(c['id'])['status']=='completed'
    assert [s['start'] for s in reloaded.results(sid)['segments']]==[0,30]

def test_idempotency_offsets_overlap(store):
    sid=store.create_session('test')['id']
    cid,created=store.add_chunk(sid,'k','sha','/a',30)
    assert store.add_chunk(sid,'k','sha','/b',30)==(cid,False)
    with pytest.raises(ValueError):store.add_chunk(sid,'k','different','/b',30)
    cid2,_=store.add_chunk(sid,'k2','sha2','/a',10)
    assert store.chunk(cid2)['timeline_start']==30
    with pytest.raises(ValueError):store.add_chunk(sid,'k3','sha3','/a',10,5)
    cid3,_=store.add_chunk(sid,'k4','sha4','/a',10,86400)
    assert store.chunk(cid3)['timeline_start']==86400

def test_cross_session_registry_correction_merge(store):
    sid=store.create_session('day1')['id']; c=chunk(store,sid)
    store.commit_window(c,0,30,result()); p1=store.speakers(sid)[0]['id']
    sid2=store.create_session('day2')['id']; c2=chunk(store,sid2)
    store.commit_window(c2,0,30,result())
    assert store.results(sid2)['segments'][0]['speaker_id']==p1
    c3=chunk(store,sid2,'other'); store.commit_window(c3,0,30,result([0.0,1.0]+[0.0]*6))
    p2=[p['id'] for p in store.speakers(sid2) if p['id']!=p1][0]
    store.correct_speaker(sid2,p1,name='Alice')
    store.correct_speaker(sid2,p2,merge_into=p1)
    assert {s['speaker_id'] for s in store.results(sid2)['segments']}=={p1}
    row=store.results(sid2)['segments'][0]
    store.assign_segment(sid2,row['id'],None)
    assert store.results(sid2)['segments'][0]['speaker_id'] is None
    store.delete_session(sid)
    assert store.speakers(sid2)[0]['id']==p1

def test_queue_guard(store,settings):
    settings.max_pending_chunks=1
    sid=store.create_session('test')['id']; c=chunk(store,sid)
    with pytest.raises(OverflowError): store.add_chunk(sid,'b','b','/b',1)
    store.set_state(c['id'],'cancel')
    store.add_chunk(sid,'b','b','/b',1)

def test_identity_groups_isolated(store):
    s1=store.create_session('a','a')['id']; c=chunk(store,s1);store.commit_window(c,0,30,result())
    s2=store.create_session('b','b')['id']; c=chunk(store,s2);store.commit_window(c,0,30,result())
    assert store.speakers(s1)[0]['id']!=store.speakers(s2)[0]['id']

def test_validation():
    with pytest.raises(ValueError):validate_result({'segments':[{'start':float('nan'),'end':1}],'events':[]},30)
    with pytest.raises(ValueError):validate_result({'segments':[],'events':[{'start':0,'end':1,'label':'x','score':float('inf')}]},30)

def wav_bytes():
    out=io.BytesIO()
    with wave.open(out,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0'*32000)
    return out.getvalue()

def test_stream_upload_and_duplicate(settings):
    with TestClient(create_app(settings)) as client:
        sid=client.post('/sessions',json={'name':'test'}).json()['id']
        url=f'/sessions/{sid}/chunks'; headers={'Idempotency-Key':'first'}
        r=client.put(url,headers=headers,content=wav_bytes());assert r.status_code==202,r.text
        r2=client.put(url,headers=headers,content=wav_bytes());assert r2.json()['duplicate']
        assert client.put(url,headers=headers,content=b'invalid').status_code==409
        assert client.put(url,headers={'Idempotency-Key':'bad'},content=b'invalid').status_code==422
        assert len(list((settings.data_dir/'uploads').glob('*')))==1
        assert client.delete(f'/sessions/{sid}').status_code==200
        assert not list((settings.data_dir/'uploads').glob('*'))

def test_auth_and_upload_limits(settings):
    settings.api_key='secret'; settings.max_upload_bytes=10
    with TestClient(create_app(settings)) as client:
        assert client.get('/health').status_code==401
        client.headers['Authorization']='Bearer secret'
        sid=client.post('/sessions',json={}).json()['id']
        assert client.put(f'/sessions/{sid}/chunks',headers={'Idempotency-Key':'x'},content=b'x'*11).status_code==413
        assert client.put(f'/sessions/{sid}/chunks',headers={'Idempotency-Key':'x'},content=b'').status_code==400

def test_single_instance_lock(settings):
    from audio_backend.resources import InstanceLock
    a=InstanceLock(settings.data_dir/'lock');b=InstanceLock(settings.data_dir/'lock')
    a.acquire()
    try:
        with pytest.raises(RuntimeError):b.acquire()
    finally:a.release()

def test_resource_rejection(settings,monkeypatch):
    from audio_backend import resources
    monkeypatch.setattr(resources,'tree_rss',lambda:settings.process_memory_limit_bytes+1)
    with pytest.raises(RuntimeError,match='memory budget'):resources.check_resources(settings)

def test_context_ownership():
    from audio_backend.worker import own_core
    r={'segments':[{'start':0,'end':2,'text':'prior'},{'start':1,'end':4,'text':'owned'},{'start':31,'end':34,'text':'next'}],
       'events':[{'start':0,'end':34,'label':'noise','score':.5}]}
    r=own_core(r,2,32)
    assert [s['text'] for s in r['segments']]==['owned']
    assert r['segments'][0]['start']==0
    assert r['events'][0]['start']==0 and r['events'][0]['end']==30

def test_failed_retry_keeps_cursor(store):
    sid=store.create_session('x')['id']; c=chunk(store,sid,duration=60)
    store.commit_window(c,0,30,result());store.fail(c['id'],'injected failure')
    store.set_state(c['id'],'retry');retried=store.claim()
    assert retried['next_offset']==30
    store.commit_window(retried,30,60,result())
    assert len(store.results(sid)['segments'])==2

def test_fake_worker_runs_real_ffmpeg(settings):
    from audio_backend.worker import Worker
    class FakeAnalyzer:
        def __init__(self,config):pass
        def analyze(self,path): return result()
        def close(self):pass
    settings.window_seconds=1; settings.context_seconds=0
    (settings.data_dir/'tmp').mkdir(exist_ok=True)
    path=settings.data_dir/'test.wav';path.write_bytes(wav_bytes())
    s=Store(settings.data_dir/'w.sqlite',settings);sid=s.create_session('x')['id']
    cid,_=s.add_chunk(sid,'x','x',path,1)
    w=Worker(s,settings,FakeAnalyzer);w.process(s.claim())
    assert s.chunk(cid)['status']=='completed'
    assert s.results(sid)['segments'][0]['text']=='test only'

def test_merged_templates_and_undo(store):
    sid=store.create_session('x')['id']
    c=chunk(store,sid);store.commit_window(c,0,30,result());p1=store.speakers(sid)[0]['id']
    other=[0.0,1.0]+[0.0]*6
    c=chunk(store,sid,'b');store.commit_window(c,0,30,result(other));p2=[p['id'] for p in store.speakers(sid) if p['id']!=p1][0]
    store.correct_speaker(sid,p2,merge_into=p1)
    c=chunk(store,sid,'c');store.commit_window(c,0,30,result(other))
    assert len(store.speakers(sid))==2
    assert store.results(sid)['segments'][-1]['speaker_id']==p1
    store.correct_speaker(sid,p2,undo_merge=True)
    assert store.results(sid)['segments'][1]['speaker_id']==p2

def test_chunk_pagination_ties(settings,monkeypatch):
    with TestClient(create_app(settings)) as client:
        sid=client.post('/sessions',json={}).json()['id'];store=client.app.state.store
        monkeypatch.setattr('audio_backend.storage.time.time',lambda:1234)
        store.add_chunk(sid,'a','a','/a',1);store.add_chunk(sid,'b','b','/b',1)
        a=client.get(f'/sessions/{sid}/chunks?limit=1').json()
        b=client.get(f'/sessions/{sid}/chunks?limit=1&after={a["next_cursor"]}').json()
        assert len(b['chunks'])==1
        assert a['chunks'][0]['id']!=b['chunks'][0]['id']

def test_startup_orphan_cleanup(settings):
    uploads=settings.data_dir/'uploads';uploads.mkdir()
    (uploads/'orphan.audio').write_bytes(b'orphan')
    with TestClient(create_app(settings)):assert not (uploads/'orphan.audio').exists()

def test_validation_drops_clipped_zero_length():
    r=validate_result({'segments':[{'start':1.05,'end':1.1,'text':'out'}],'events':[]},1)
    assert r['segments']==[]

def test_subprocess_output_cap_after_fast_exit(settings,monkeypatch):
    from audio_backend import resources
    output=settings.data_dir/'oversized.out'
    class FastProcess:
        returncode=0
        pid=999999999
        def __init__(self,*args,**kwargs):
            kwargs['stdout'].write(b'0'*(16*1024**2+1));kwargs['stdout'].flush()
        def poll(self):return 0
        def wait(self,timeout=None):return 0
    monkeypatch.setattr(resources.subprocess,'Popen',FastProcess)
    with pytest.raises(RuntimeError,match='output exceeded'):
        resources.run_bounded(['explicit-test-fake'],settings,stdout_path=output)

def test_startup_does_not_materialize_directory(settings,monkeypatch):
    from pathlib import Path
    app=create_app(settings)
    def forbidden(*args,**kwargs):raise AssertionError('Directory scan must use streaming os.scandir')
    monkeypatch.setattr(Path,'glob',forbidden)
    monkeypatch.setattr(Path,'iterdir',forbidden)
    with TestClient(app):pass

def test_per_row_provenance_and_trailing_silence_api(settings):
    with TestClient(create_app(settings)) as client:
        sid=client.post('/sessions',json={}).json()['id'];store=client.app.state.store
        c=chunk(store,sid,duration=90)
        first=result();first['segments'][0]['text']='汉字';first['segments'][0]['raw_text']='漢字'
        first['metadata']={'asr_backend':'whisper','timestamp_semantics':'Approximate Whisper token timestamps',
                           'models':{'asr':'model-one'},'warnings':['timestamps approximate'],'processing_seconds':4}
        store.commit_window(c,0,30,first)
        second=result();second['segments'][0]['asr_backend']='sensevoice'
        second['segments'][0]['timestamp_kind']='CTC anchors, estimated interval ends'
        second['metadata']={'asr_backend':'whisper','timestamp_semantics':'fallback must not override segment',
                            'models':{'asr':'model-two'}}
        store.commit_window(c,30,60,second)
        store.commit_window(c,60,90,{'segments':[],'events':[],
            'metadata':{'models':{},'silence_fast_path':True,'processing_seconds':.001}})
        rows=client.get(f'/sessions/{sid}/segments').json()['segments']
        assert rows[0]['asr_backend']=='whisper' and rows[0]['raw_text']=='漢字'
        assert rows[0]['timestamp_kind']=='Approximate Whisper token timestamps'
        assert rows[1]['asr_backend']=='sensevoice'
        assert rows[1]['timestamp_kind']=='CTC anchors, estimated interval ends'
        assert rows[1]['raw_text'] is None
        detail=client.get(f'/chunks/{c["id"]}').json()
        assert detail['metadata']['silence_fast_path'] is True
        assert detail['metadata']['models']=={}
        variants=detail['provenance']['variants']
        assert len(variants)==2 and variants[0]['details']['models']['asr']=='model-one'
        assert 'processing_seconds' not in variants[0]['details']
        assert variants[0]['details']['warnings']==['timestamps approximate']

def test_additive_provenance_migration_does_not_guess_history(store,settings):
    sid=store.create_session('legacy')['id'];c=chunk(store,sid,duration=60)
    r=result();r['metadata']={'asr_backend':'whisper','timestamp_semantics':'latest is not history'}
    store.commit_window(c,0,30,r)
    with store.db() as db:
        for col in ('asr_backend','timestamp_kind','raw_text'):db.execute(f'ALTER TABLE segments DROP COLUMN {col}')
        db.execute('ALTER TABLE chunks DROP COLUMN provenance')
    migrated=Store(store.path,settings)
    old=migrated.results(sid)['segments'][0]
    assert old['asr_backend'] is None and old['timestamp_kind'] is None and old['raw_text'] is None
    assert migrated.chunk(c['id'])['provenance'] is None
    newer=result();newer['metadata']={'asr_backend':'sensevoice','timestamp_semantics':'CTC anchors'}
    migrated.commit_window(c,30,60,newer)
    assert migrated.results(sid)['segments'][1]['asr_backend']=='sensevoice'
    assert migrated.results(sid)['segments'][0]['asr_backend'] is None
    Store(store.path,settings)  # additive migration is idempotent

def test_provenance_history_bound():
    import json
    from audio_backend.storage import update_provenance
    value=None
    for i in range(100):value=json.dumps(update_provenance(value,{'asr_backend':'backend-'+str(i)},i))
    result=json.loads(value)
    assert len(result['variants'])==16 and result['variants_truncated']

def test_ambiguous_matches_do_not_multiply_templates(store):
    from audio_backend.storage import normalize
    sid=store.create_session('ambiguous')['id']
    a=[1.0]+[0.0]*7;b=[.7,math.sqrt(1-.7**2)]+[0.0]*6
    # Seed two similar pre-existing identities under a deliberately permissive novelty setting.
    store.settings.speaker_new_threshold=.74
    for i,vector in enumerate((a,b)):
        c=chunk(store,sid,str(i));store.commit_window(c,0,30,result(vector))
    assert len(store.speakers(sid))==2
    store.settings.speaker_new_threshold=.45
    query=normalize([x+y for x,y in zip(a,b)])
    for i in range(20):
        c=chunk(store,sid,'amb-'+str(i));store.commit_window(c,0,30,result(query))
    assert len(store.speakers(sid))==2
    for row in store.results(sid)['segments'][2:]:
        assert row['speaker_id'] is None and row['speaker_status']=='unknown_ambiguous'
        assert row['speaker_match_score']>.9

def test_silence_config_does_not_claim_executed_provenance():
    from audio_backend.storage import update_provenance
    p=update_provenance(None,{'silence_fast_path':True,'asr_backend':'whisper','models':{'asr':'configured-not-run'}},0)
    assert p['variants']==[]

def test_novelty_gray_zone_no_growth_and_strong_matches(store):
    sid=store.create_session('gray-zone')['id']
    first=[1.0]+[0.0]*7;c=chunk(store,sid);store.commit_window(c,0,30,result(first))
    known=store.speakers(sid)[0]['id']
    gray=[.65,math.sqrt(1-.65**2)]+[0.0]*6
    for i in range(20):
        c=chunk(store,sid,'gray-'+str(i));store.commit_window(c,0,30,result(gray))
    assert len(store.speakers(sid))==1
    for row in store.results(sid)['segments'][1:]:
        assert row['speaker_id'] is None and row['speaker_status']=='unknown_uncertain'
    confident=[.95,math.sqrt(1-.95**2)]+[0.0]*6
    c=chunk(store,sid,'confident');store.commit_window(c,0,30,result(confident))
    assert store.results(sid)['segments'][-1]['speaker_id']==known
    different=[0.0,1.0]+[0.0]*6
    c=chunk(store,sid,'different');store.commit_window(c,0,30,result(different))
    assert len(store.speakers(sid))==2
    assert store.results(sid)['segments'][-1]['speaker_id']!=known
    assert store.results(sid)['segments'][-1]['speaker_status']=='provisional'

def test_novelty_threshold_validation(tmp_path):
    with pytest.raises(ValueError):Settings(data_dir=tmp_path,speaker_new_threshold=.8,speaker_match_threshold=.75)

def seed_enrollment_source(store,sid,key='unknown',overlap=False,embedding=True):
    if not store.speakers(sid):
        c=chunk(store,sid,'known-'+key);store.commit_window(c,0,30,result())
    vector=[.65,math.sqrt(1-.65**2)]+[0.0]*6
    c=chunk(store,sid,key)
    r=result(vector);r['segments'][0]['overlap']=overlap
    if not embedding:r['segments'][0]['speaker_embedding']=None
    r['segments'].append({**r['segments'][0],'start':1,'end':2,'text':'same window voice'})
    store.commit_window(c,0,30,r)
    return store.results(sid)['segments'][-2:],vector

def test_explicit_enrollment_learns_without_vector_exposure(settings):
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store;sid=store.create_session('enroll')['id']
        rows,vector=seed_enrollment_source(store,sid)
        assert all(row['enrollment_available'] for row in rows)
        with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_observations WHERE matched_speaker_id IS NULL').fetchone()[0]==1
        response=client.post(f'/sessions/{sid}/speakers',json={'name':'New person','source_segment_id':rows[0]['id']})
        assert response.status_code==201,response.text
        pid=response.json()['id']
        assert response.json()['state']=='confirmed'
        c=chunk(store,sid,'later');store.commit_window(c,0,30,result(vector))
        future=client.get(f'/sessions/{sid}/segments').json()['segments'][-1]
        assert future['speaker_id']==pid and future['speaker_status']=='confirmed'
        seg_json=client.get(f'/sessions/{sid}/segments').json()
        for row in seg_json['segments']:
            assert 'embedding' not in row and 'speaker_embedding' not in row and 'candidate_observation_id' not in row
        assert all('embedding' not in p for p in client.get(f'/sessions/{sid}/speakers').json()['speakers'])
        assert client.post(f'/sessions/{sid}/speakers',json={'name':'duplicate','source_segment_id':rows[0]['id']}).status_code==409
        assert client.post(f'/sessions/{sid}/speakers',json={'name':'duplicate','source_segment_id':rows[1]['id']}).status_code==409
        assert client.post(f'/sessions/{sid}/speakers',json={'name':'arbitrary','source_segment_id':rows[1]['id'],'embedding':vector}).status_code==422

def test_enrollment_ownership_overlap_missing_and_caps(settings):
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store;sid=store.create_session('enroll')['id']
        rows,_=seed_enrollment_source(store,sid)
        other=store.create_session('unrelated','other')['id']
        assert client.post(f'/sessions/{other}/speakers',json={'name':'bad','source_segment_id':rows[0]['id']}).status_code==404
        for key,overlap,embedding in [('overlap',True,True),('missing',False,False)]:
            rejected,_=seed_enrollment_source(store,sid,key,overlap,embedding)
            assert not rejected[0]['enrollment_available']
            assert client.post(f'/sessions/{sid}/speakers',json={'name':'bad','source_segment_id':rejected[0]['id']}).status_code==409
        settings.max_speakers_per_session=1
        assert client.post(f'/sessions/{sid}/speakers',json={'name':'full','source_segment_id':rows[0]['id']}).status_code==429

def test_private_observation_capacity_and_cleanup(settings):
    settings.max_speaker_observations_per_group=1
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store;sid=store.create_session('enroll')['id']
        rows,_=seed_enrollment_source(store,sid)
        full,_=seed_enrollment_source(store,sid,'full')
        assert rows[0]['enrollment_available'] and not full[0]['enrollment_available']
        assert client.delete('/identity-groups/default/speakers').status_code==200
        with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==0
        assert not any(row['enrollment_available'] for row in store.results(sid)['segments'])
        more,_=seed_enrollment_source(store,sid,'new-after-reset')
        assert more[0]['enrollment_available']
        store.delete_session(sid)
        with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==0

def test_observation_additive_migration(store,settings):
    sid=store.create_session('legacy')['id'];rows,_=seed_enrollment_source(store,sid)
    with store.db() as db:
        db.execute('DROP TABLE speaker_observations')
        db.execute('ALTER TABLE segments DROP COLUMN candidate_observation_id')
    migrated=Store(store.path,settings)
    assert not any(row['enrollment_available'] for row in migrated.results(sid)['segments'])

def test_raw_turn_and_verified_example_routes(settings):
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store;sid=store.create_session('turns')['id']
        c=chunk(store,sid);r=result();r['speaker_turns']=[dict(r['segments'][0],end=20)]
        store.commit_window(c,0,30,r);pid=store.speakers(sid)[0]['id']
        response=client.get(f'/sessions/{sid}/speaker-turns');assert response.status_code==200
        turn=response.json()['speaker_turns'][0];assert turn['end']==20 and turn['speaker_id']==pid
        assert 'speaker_embedding' not in turn and 'embedding' not in turn
        c=chunk(store,sid,'second');store.commit_window(c,0,30,result())
        source=store.results(sid)['segments'][-1]
        assert source['profile_example_available']
        added=client.post(f'/sessions/{sid}/speakers/{pid}/examples',json={'source_segment_id':source['id']})
        assert added.status_code==201,added.text
        assert added.json()['template_count']==2
        assert client.post(f'/sessions/{sid}/speakers/{pid}/examples',json={'source_segment_id':source['id']}).status_code==409
        assert client.post(f'/sessions/{sid}/speakers/{pid}/examples',json={'source_segment_id':source['id'],'embedding':[1]*8}).status_code==422
        assert client.delete('/identity-groups/default/speakers').status_code==200
        assert client.get(f'/sessions/{sid}/speaker-turns').json()['speaker_turns'][0]['speaker_id'] is None
        with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_examples').fetchone()[0]==0

def test_waiting_queue_bound_uses_explicit_stream_work_units(settings):
    settings.max_pending_chunks=1
    store=Store(settings.data_dir/'queue.sqlite',settings);sid=store.create_session('streams')['id']
    first,_=store.add_chunk(sid,'a','a','/fake/a',10,stream_id='one')
    with store.db() as db:db.execute("UPDATE chunks SET status='waiting_input' WHERE id=?",(first,))
    with pytest.raises(OverflowError):store.capacity(sid,'two')
    with pytest.raises(OverflowError):store.add_chunk(sid,'unrelated','b','/fake/b',10,stream_id='two')
    for key in ('next','third'):
        store.add_chunk(sid,key,key,'/fake/'+key,10,stream_id='one')
    assert store.capacity(sid,'one')==1
    with store.db() as db:assert db.execute('SELECT SUM(duration) FROM chunks').fetchone()[0]==30
    store.set_state(first,'pause');store.set_state(first,'resume');store.set_state(first,'cancel')
    with store.db() as db:assert {r[0] for r in db.execute('SELECT status FROM chunks')}=={'cancelled'}


def test_stream_fragment_floor_rollback_cap_and_finish(settings):
    from audio_backend.boundary_store import finish_stream
    settings.max_pending_chunks=1
    store=Store(settings.data_dir/'fragments.sqlite',settings);sid=store.create_session('fragments')['id']
    with pytest.raises(ValueError,match='0.5'):
        store.add_chunk(sid,'tiny','bad','/fake/tiny',.1,stream_id='one')
    assert store.session(sid)['timeline_end']==0 and store.duplicate(sid,'tiny') is None
    first,_=store.add_chunk(sid,'tiny','good','/fake/good',.5,stream_id='one')
    assert store.add_chunk(sid,'tiny','good','/fake/good',.5,stream_id='one')==(first,False)
    for index in range(1,128):store.add_chunk(sid,str(index),str(index),'/fake/piece',.5,stream_id='one')
    with pytest.raises(OverflowError,match='128'):
        store.add_chunk(sid,'overflow','overflow','/fake/overflow',10,stream_id='one')
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]==128
        assert db.execute('SELECT SUM(duration) FROM chunks').fetchone()[0]==64
        db.execute("UPDATE chunks SET status='waiting_input' WHERE id=?",(first,))
    assert finish_stream(store,sid,'one')['stream_final']
    assert store.claim()['id']==first
    # A tiny final flush is allowed in an unfilled stream, never discarded.
    store.set_state(first,'cancel')
    other=store.create_session('final')['id']
    tiny,_=store.add_chunk(other,'final','final','/fake/final',.1,stream_id='two',stream_final=True)
    assert store.chunk(tiny)['duration']==.1
