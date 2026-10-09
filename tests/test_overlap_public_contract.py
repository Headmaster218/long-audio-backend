"""Synthetic wire-contract tests; these do not claim model accuracy."""
import json
from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend.settings import Settings


def candidate_payload():
    def region(gid,start,end,status='candidate_tracks_unverified'):
        return {'overlap_group_id':gid,'start':start,'end':end,'support_start':.5,'support_end':2.,
            'detected_source_count':2,'detected_speaker_local_ids':['A','B'],'capacity':2,
            'status':status,'reason_code':'unverified_text_recovery','original_mixture_retained':True,
            'shared_support_id':'shared-0','support_activity':{'max_simultaneous_speakers':2,
                'distinct_local_speakers':2,'speaker_local_ids':['A','B'],'identity_scope':'analysis_window_local'}}
    blocked=region('blocked',.31,.4,'support_capacity_exceeded')
    blocked.update(support_start=0.,support_end=1.,reason_code='recognition_support_exceeds_two_concurrent_sources',shared_support_id=None,
        support_activity={'max_simultaneous_speakers':3,'distinct_local_speakers':3,'speaker_local_ids':['A','B','C'],'private_vector':[1.,0.]})
    regions=[blocked,region('g1',.6,1.),region('g2',1.2,1.6,'candidate_evidence_shared')]
    tracks=[]
    for slot,sid in enumerate(['A','B']):
        text=f'voice {sid}'
        tracks.append({'source_track_id':f'g1:source-{slot}','overlap_group_id':'g1','scope_id':'conditioned',
            'source_slot':slot,'start':.6,'end':1.6,'support_start':.5,'support_end':2.,'text':text,'raw_text':text,
            'identity_status':'unresolved','quality_status':'unverified','speaker_id':None,'profile_update_eligible':False,
            'conditioning_local_id':sid,'provenance':{'model_id':'synthetic-contract-test',
                'conditioning_local_id':sid,'conditioning_source':'provided_local_diarization',
                'shared_support_id':'shared-0','related_overlap_group_ids':['g1','g2'],
                'native_unit_kind':'native_phrase','native_time_axis':'core_relative','native_phrase_intervals':[
                    {'start':.6,'end':1.6,'ownership_anchor':.6,'text':text,'unit_kind':'native_phrase','boundary_uncertain':True}],
                'audio_path':'/must-not-leak.wav','embedding':[1.,0.]}})
    return {'regions':regions,'tracks':tracks,'metadata':{}}


def test_overlap_rest_feed_scope_support_restart_and_erasure(tmp_path,monkeypatch):
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    source=tmp_path/'uploads'/'source.audio'
    with TestClient(create_app(settings)) as client:
        source.write_bytes(b'original retained source fixture')
        store=client.app.state.store;sid=store.create_session('overlap contract')['id']
        group=store.session(sid)['identity_group']
        with store.db() as db:
            db.execute('INSERT INTO speakers(id,session_id,identity_group,name,embedding,state,created) VALUES(?,?,?,?,?,?,?)',
                ('known-person',sid,group,'existing identity',None,'confirmed',0.))
        # Synthetic existing resolution only, never create identity from candidates.
        monkeypatch.setattr(store,'_window_identity',lambda db,chunk,offset,metadata,group,local_id,embedding:
            ('known-person',.8,'matched',None) if local_id=='A' else (None,None,'unknown',None))
        cid,_=store.add_chunk(sid,'fixture','fixture',source,2,offset=100);chunk=store.claim()
        result={'segments':[{'start':0.,'end':2.,'text':'original mixture','speaker_local_id':None}],
            'events':[],'speaker_turns':[{'start':0.,'end':2.,'speaker_local_id':'A'},
                {'start':0.,'end':2.,'speaker_local_id':'B'},{'start':0.,'end':.3,'speaker_local_id':'C'}],
            'overlap_candidates':candidate_payload(),'metadata':{}}
        assert store.commit_window(chunk,0,2,result)
        assert not store.commit_window(chunk,0,2,result)
        rest=client.get(f'/sessions/{sid}/overlap-candidates').json()
        assert len(rest['regions'])==3 and len(rest['tracks'])==2
        blocked=rest['regions'][0]
        assert blocked['status']=='support_capacity_exceeded'
        assert blocked['support_activity']['max_simultaneous_speakers']==3
        assert blocked['support_activity']['speaker_local_ids']==['A','B','C']
        a=rest['tracks'][0];hint=a['conditioning_evidence']
        assert a['speaker_id'] is None and a['conditioned_on_speaker_id']=='known-person'
        assert a['identity_status']=='unresolved' and a['profile_update_eligible'] is False
        assert hint['chunk_id']==cid and hint['speaker_window_start']==0. and hint['conditioning_local_id']=='A'
        turns=client.get(f'/sessions/{sid}/speaker-turns').json()['speaker_turns']
        assert hint['speaker_turn_ids']==[t['id'] for t in turns if t['speaker_local_id']=='A']
        assert not hint['independently_verified_source_identity']
        native=a['provenance']['native_phrase_intervals'][0]
        assert native['start']==100.6 and native['end']==101.6 and native['ownership_anchor']==100.6
        assert a['provenance']['native_time_axis']=='session_absolute'
        page1=client.get(f'/sessions/{sid}/overlap-candidates',params={'after':blocked['id'],'limit':1}).json()
        page2=client.get(f'/sessions/{sid}/overlap-candidates',params={'after':page1['next_cursor'],'limit':1}).json()
        assert [t['id'] for t in page1['tracks']]==[t['id'] for t in page2['tracks']]
        feed=client.get(f'/sessions/{sid}/changes').json()
        region_changes=[r['data'] for r in feed['changes'] if r['entity']=='overlap_regions']
        track_changes=[r['data'] for r in feed['changes'] if r['entity']=='overlap_tracks']
        assert region_changes==rest['regions'] and track_changes==rest['tracks']
        encoded=json.dumps(rest)+json.dumps(region_changes)+json.dumps(track_changes)
        assert 'must-not-leak' not in encoded and 'private_vector' not in encoded and 'embedding' not in encoded
        assert source.exists() and client.get(f'/sessions/{sid}/segments').json()['segments'][0]['text']=='original mixture'
        with store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM speakers').fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM speaker_examples').fetchone()[0]==0
            db.execute("DELETE FROM change_feed_migrations WHERE name='overlap_support_conditioning_v3'")
        previous_cursor=feed['next_cursor']
    # Simulate an existing v2 feed upgrade. One safe replay includes new fields.
    with TestClient(create_app(settings)) as client:
        again=client.get(f'/sessions/{sid}/overlap-candidates').json()
        assert again==rest
        replay=client.get(f'/sessions/{sid}/changes',params={'after':previous_cursor}).json()
        assert len([r for r in replay['changes'] if r['entity']=='overlap_regions'])==3
        assert len([r for r in replay['changes'] if r['entity']=='overlap_tracks'])==2
        assert [r['data'] for r in replay['changes'] if r['entity']=='overlap_tracks']==rest['tracks']
        cursor=replay['next_cursor']
    with TestClient(create_app(settings)) as client:
        assert client.get(f'/sessions/{sid}/changes',params={'after':cursor}).json()['changes']==[]
        assert client.delete(f'/sessions/{sid}').status_code==200
        assert not source.exists()
        with client.app.state.store.db() as db:
            for table in ('overlap_regions','overlap_tracks','overlap_track_regions','result_changes'):
                assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
