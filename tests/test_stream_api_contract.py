"""HTTP/durability contract tests, not recognition-accuracy evidence."""
import io
import wave
import pytest
from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend.settings import Settings


def wav():
    buf=io.BytesIO()
    with wave.open(buf,'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(b'\0'*32000)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False))) as c:
        yield c


def upload(client,sid,key,query=''):
    return client.put(f'/sessions/{sid}/chunks{query}',headers={'Idempotency-Key':key},content=wav())


def test_independent_default_and_explicit_stream_contract(client):
    sid=client.post('/sessions',json={}).json()['id']
    a=upload(client,sid,'independent').json()['chunk']
    b=upload(client,sid,'start','?stream_id=meeting').json()['chunk']
    assert b['timeline_start']==a['duration']
    bad=upload(client,sid,'gap','?stream_id=meeting&timeline_start=20')
    assert bad.status_code==409,bad.text
    c=upload(client,sid,'end','?stream_id=meeting&stream_final=true')
    assert c.status_code==202,c.text
    assert upload(client,sid,'after','?stream_id=meeting').status_code==409
    assert upload(client,sid,'invalid','?stream_final=true').status_code==422


def test_retry_contract_and_finish_are_idempotent(client):
    sid=client.post('/sessions',json={}).json()['id']
    url='?stream_id=meeting&recorded_at=2026-01-01T00:00:00Z'
    a=upload(client,sid,'first',url); assert a.status_code==202,a.text
    assert upload(client,sid,'first',url).json()['duplicate']
    assert upload(client,sid,'first','?stream_id=other').status_code==409
    assert upload(client,sid,'first',url+'&stream_final=true').status_code==409
    assert upload(client,sid,'first',url+'&timeline_start=9').status_code==409
    finish=f'/sessions/{sid}/streams/meeting/finish'
    assert client.post(finish).status_code==200
    assert client.post(finish).status_code==200
    assert upload(client,sid,'first',url).json()['duplicate']
    assert client.post(f'/sessions/{sid}/streams/missing/finish').status_code==404


def test_change_feed_transactional_replay_and_corrections(client):
    store=client.app.state.store
    sid=client.post('/sessions',json={}).json()['id']
    cid=upload(client,sid,'first').json()['chunk']['id']
    claimed=store.claim()
    result={'segments':[{'start':0,'end':1,'text':'example','speaker_local_id':'s1','speaker_embedding':[1.0]+[0.0]*7}], 'events':[]}
    store.commit_window(claimed,0,1,result)
    feed=f'/sessions/{sid}/changes'
    initial=client.get(feed).json()
    text_changes=[r for r in initial['changes'] if r['entity']=='segments']
    assert len(text_changes)==1
    segment=text_changes[0]['data']
    cursor=initial['next_cursor']
    assert client.patch(f"/sessions/{sid}/segments/{segment['id']}",json={'speaker_id':None}).status_code==200
    updated=client.get(feed,params={'after':cursor}).json()
    corrected=[r for r in updated['changes'] if r['entity']=='segments']
    assert corrected[-1]['data']['speaker_id'] is None
    assert updated['next_cursor']>cursor
    assert 'embedding' not in str(initial) and '/uploads/' not in str(initial)
    with pytest.raises(RuntimeError):
        with store.db() as db:
            db.execute("UPDATE segments SET text='rolled back' WHERE id=?",(segment['id'],))
            raise RuntimeError('abort')
    assert not client.get(feed,params={'after':updated['next_cursor']}).json()['changes']
    page=client.get(feed,params={'limit':1}).json()
    assert len(page['changes'])==1 and page['has_more']
    with store.db() as db:
        before=db.execute('SELECT COUNT(*) FROM result_changes WHERE session_id=?',(sid,)).fetchone()[0]
    assert before
    assert client.delete(f'/sessions/{sid}').status_code==200
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM result_changes WHERE session_id=?',(sid,)).fetchone()[0]==0


def test_change_feed_reopen_and_stream_receipt_visibility(client):
    from audio_backend import change_feed
    store=client.app.state.store
    sid=client.post('/sessions',json={}).json()['id']
    upload(client,sid,'one','?stream_id=meeting')
    first=client.get(f'/sessions/{sid}/changes').json()
    receipt=[x for x in first['changes'] if x['entity']=='stream_chunks'][-1]
    assert receipt['data']['stream_final']==0
    change_feed.initialize(store)
    assert not client.get(f'/sessions/{sid}/changes',params={'after':first['next_cursor']}).json()['changes']
    client.post(f'/sessions/{sid}/streams/meeting/finish')
    updated=client.get(f'/sessions/{sid}/changes',params={'after':first['next_cursor']}).json()
    receipt=[x for x in updated['changes'] if x['entity']=='stream_chunks'][-1]
    assert receipt['data']['stream_final']==1 and receipt['data']['upload_stream_final']==0
    # Backdated insertion is still replayed in cursor order, retaining time axis.
    other=client.post('/sessions',json={}).json()['id']
    upload(client,other,'future','?timeline_start=86400')
    upload(client,other,'past','?timeline_start=0')
    rows=client.get(f'/sessions/{other}/changes').json()['changes']
    times=[x['data']['timeline_start'] for x in rows if x['entity']=='chunks']
    assert times==[86400,0]


def test_identity_changes_reach_all_existing_group_sessions(client):
    store=client.app.state.store
    sid=client.post('/sessions',json={'identity_group':'family'}).json()['id']
    other=client.post('/sessions',json={'identity_group':'family'}).json()['id']
    isolated=client.post('/sessions',json={'identity_group':'separate'}).json()['id']
    upload(client,sid,'one')
    c=store.claim()
    store.commit_window(c,0,1,{'segments':[{'start':0,'end':1,'text':'example',
        'speaker_local_id':'one','speaker_embedding':[1.0]+[0.0]*7}], 'events':[]})
    pid=store.speakers(sid)[0]['id']
    assert client.patch(f'/sessions/{sid}/speakers/{pid}',json={'name':'Alice'}).status_code==200
    for target in (sid,other):
        feed=client.get(f'/sessions/{target}/changes').json()['changes']
        records=[x for x in feed if x['entity']=='speakers']
        assert records[-1]['data']['name']=='Alice'
    assert not client.get(f'/sessions/{isolated}/changes').json()['changes']
    cursor=client.get(f'/sessions/{other}/changes').json()['next_cursor']
    assert client.delete('/identity-groups/family/speakers').status_code==200
    records=client.get(f'/sessions/{other}/changes',params={'after':cursor}).json()['changes']
    assert any(x['entity']=='speakers' and x['entity_id']==pid and x['operation']=='delete' for x in records)
