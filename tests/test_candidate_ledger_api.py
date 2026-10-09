"""Synthetic persistence/ownership tests, not audio-model accuracy claims."""
from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend.settings import Settings

def test_candidate_ledger_preserves_context_support_and_public_feed(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store;sid=store.create_session('candidates')['id']
        cid,_=store.add_chunk(sid,'x','x',tmp_path/'source.audio',60,offset=100);chunk=store.claim()
        store.commit_window(chunk,0,30,{'segments':[],'events':[]})
        candidate={'start':0.,'end':3.,'support_start':-2.,'support_end':3.,'time_axis':'core_relative','ownership':'core_intersection',
                   'top_labels':[{'label':'Rain on surface','score':.18}], 'category_scores':{'rain':{'score':.18,'source_label':'Rain on surface'}},'speech_score':.8}
        result={'segments':[],'events':[],'event_candidates':[candidate],
                'speaker_turns':[{'start':0.,'end':2.,'speaker_local_id':'uncertain','speaker_embedding':None}],
                'metadata':{'event_model':'fixture-model','event_window_seconds':5.,'event_hop_seconds':2.5}}
        assert store.commit_window(chunk,30,60,result)
        assert not store.commit_window(chunk,30,60,result)
        rows=client.get(f'/sessions/{sid}/event-candidates').json()['event_candidates']
        assert len(rows)==1;row=rows[0]
        assert (row['start'],row['end'],row['support_start'],row['support_end'])==(130,133,128,133)
        assert row['status']=='unverified_model_candidate' and not row['scores_calibrated']
        assert row['category_scores']['rain']['score']==.18
        assert client.get(f'/sessions/{sid}/events').json()['events']==[]
        changes=client.get(f'/sessions/{sid}/changes').json()['changes']
        candidate_changes=[r for r in changes if r['entity']=='event_candidates']
        assert len(candidate_changes)==1 and 'payload' not in candidate_changes[0]['data']
        assert candidate_changes[0]['data']['top_labels'][0]['label']=='Rain on surface'
        assert any(r['entity']=='speaker_turns' for r in changes)
        assert not any('embedding' in str(r['data']).lower() for r in candidate_changes)
        response=client.delete(f'/sessions/{sid}');assert response.status_code==200
        with store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM event_candidates').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM result_changes WHERE session_id=?',(sid,)).fetchone()[0]==0

def test_primary_silence_rule_is_not_claimed_as_model_execution(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store;sid=store.create_session('silence-rule')['id']
        cid,_=store.add_chunk(sid,'x','x',tmp_path/'source.audio',5);chunk=store.claim()
        store.commit_window(chunk,0,5,{'segments':[],'events':[{'start':0,'end':5,'label':'silence','score':1.,'source_label':'digital_or_near_digital_silence','time_precision':'window_support'}]})
        row=client.get(f'/sessions/{sid}/events').json()['events'][0]
        assert row['inference_source']=='rule' and row['verification_status']=='unverified_inference'
        feed=client.get(f'/sessions/{sid}/changes').json()['changes']
        assert [r['data'] for r in feed if r['entity']=='events'][0]['inference_source']=='rule'
