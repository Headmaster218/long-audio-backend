"""Synthetic API round trip only; this never executes any audio model."""
from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend.settings import Settings
from test_framemn_candidate_projection import candidate,owned


def test_existing_api_and_change_feed_return_discriminated_temporal_payload(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store=client.app.state.store;sid=store.create_session('temporal API fixture')['id']
        store.add_chunk(sid,'one','one',tmp_path/'unused.audio',10,offset=100)
        chunk=store.claim();result=owned([candidate(runs=[[249,250]])],0,10)
        assert store.commit_window(chunk,0,10,result)
        response=client.get(f'/sessions/{sid}/event-candidates');assert response.status_code==200
        row=response.json()['event_candidates'][0]
        assert row['payload_kind']=='framemn_temporal_v1'
        assert row['temporal']['frame_runs']==[[249,250]]
        assert row['temporal']['owned_sample_runs']==[[159360,160000]]
        assert row['temporal_time_axis']=='tile_relative'
        assert (row['start'],row['end'])==(100,110)
        assert client.get(f'/sessions/{sid}/events').json()['events']==[]
        changes=client.get(f'/sessions/{sid}/changes').json()['changes']
        entries=[r['data'] for r in changes if r['entity']=='event_candidates']
        assert len(entries)==1 and entries[0]==row
        cursor=response.json()['next_cursor']
        assert client.get(f'/sessions/{sid}/event-candidates?after={cursor}').json()['event_candidates']==[]
