"""Verified-only matching synthetic contract, not real audio accuracy."""
import copy
import hashlib
import json
import math
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend import verified_identity as bank, embedding_space, speaker_support, change_feed
from audio_backend.settings import Settings
from audio_backend.storage import Store, ident
from test_verified_identity_bank import proven, configured, captured, request, POLICY
from test_reviewable_support import prepare, write_wave, result_for


def last(store,sid):return store.results(sid)['segments'][-1]


def query(store,sid,proven,seed=1,vector=None):
    wav=store.settings.data_dir/'uploads'/f'{ident()}.audio'
    samples=write_wave(wav,seed=seed)
    cid,_=store.add_chunk(sid,ident(),hashlib.sha256(wav.read_bytes()).hexdigest(),wav,3)
    chunk=store.claim();assert chunk['id']==cid
    result=result_for(samples)
    lineage={'status':'complete','decoder_sha256':'d'*64,'analyzer_frame_count':len(samples),
        'parts':[{'chunk_id':cid,'source_sha256':chunk['digest'],'seek_seconds':0.,
                  'requested_seconds':3.,'analyzer_frames':[0,len(samples)]}]}
    generation=speaker_support.capture_generation(store,chunk)
    result['metadata'].update(speaker_embedding_space=proven,speaker_embedding_space_id=embedding_space.validate_space(proven))
    if vector:
        for row in result['segments']+result['speaker_turns']:row['speaker_embedding']=vector
    result['_speaker_support']=speaker_support.prepare_window(result,wav,lineage,generation,core_start=0,analyzer_start=0,settings=store.settings)
    assert store.commit_window(chunk,0,chunk['duration'],result)
    return chunk,last(store,sid)


def enroll(store,sid,proven,seed=0,vector=None,**kwargs):
    chunk,support,raw=captured(store,sid,proven,seed,vector)
    return bank.mutate(store,sid,request(store,sid,support,raw,**kwargs)),chunk,support,raw


def test_no_auto_person_no_legacy_fallback_and_matching_restart(tmp_path,proven):
    settings,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    row=last(store,sid)
    assert row['speaker_id'] is None and row['speaker_status']=='unknown_no_reviewed_references'
    assert store.speakers(sid)==[]
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==0
    event=bank.mutate(store,sid,request(store,sid,support,raw))
    other=store.create_session('later','verified')['id']
    _,row=query(store,other,proven)
    assert row['speaker_id']==event['person_id'] and row['speaker_status']=='experimental_identity_match'
    assert row['identity_profile_revision']=='1' or row['identity_profile_revision']==1
    dec=bank.decision(store,other,row['identity_decision_id'])
    assert dec['reference_event_ids']==[event['event_id']] and not dec['automatic_query_is_human_verified']
    reloaded=Store(store.path,settings)
    assert bank.decision(reloaded,other,row['identity_decision_id'])==dec
    assert bank.bank(store,sid)['active_counts']=={'positive':1}
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM speakers').fetchone()[0]==1


@pytest.mark.parametrize('quota',['bytes','rows','per_record','window'])
def test_later_queries_match_after_review_quota_is_full(tmp_path,proven,quota):
    _,store,sid=configured(tmp_path)
    event,_,support,raw=enroll(store,sid,proven)
    if quota=='bytes':store.settings.speaker_support_total_pcm_bytes=support['pcm_bytes']+raw['retained_bytes']
    elif quota=='rows':store.settings.speaker_support_max_records=1
    elif quota=='per_record':store.settings.speaker_support_max_pcm_bytes=0
    else:store.settings.speaker_support_window_pcm_bytes=0
    for seed in range(1,26):
        _,row=query(store,sid,proven,seed=seed)
        assert row['speaker_id']==event['person_id']
        dec=bank.decision(store,sid,row['identity_decision_id'])
        assert dec['review_availability']!='available'
    assert bank.bank(store,sid)['active_counts']=={'positive':1}
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_support_vectors').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM identity_decisions').fetchone()[0]==26


def test_exact_negative_veto_never_redirects_runnerup(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    a,_,_,_=enroll(store,sid,proven,0)
    b,_,_,_=enroll(store,sid,proven,1,vector=[.8,.6]+[0.]*6,new_person_name='B')
    _,support,raw=captured(store,sid,proven,seed=2)
    bank.mutate(store,sid,request(store,sid,support,raw,kind='negative',new_person_name=None,person_id=a['person_id']))
    _,row=query(store,sid,proven,seed=2)
    assert row['speaker_id'] is None and row['speaker_status']=='unknown_exact_negative'
    # A different support receives no similarity-generalized veto.
    _,row=query(store,sid,proven,seed=3)
    assert row['speaker_id']==a['person_id'] and row['speaker_id']!=b['person_id']


def test_space_change_and_unknown_proof_abstain(tmp_path,proven,monkeypatch):
    _,store,sid=configured(tmp_path)
    enroll(store,sid,proven)
    changed=copy.deepcopy(embedding_space._AUDITED_BINDINGS[proven['binding_id']])
    changed['frontend']['high_freq']=-200.
    key=embedding_space._digest(changed);monkeypatch.setitem(embedding_space._AUDITED_BINDINGS,key,changed)
    alternate=embedding_space._descriptor(key,type('Config',(),{'provider':'cpu','num_threads':2})())
    _,row=query(store,sid,alternate)
    assert row['speaker_id'] is None and row['speaker_status']=='unknown_embedding_space'
    _,chunk,result,*_=prepare(store,sid=sid,seed=9)
    assert store.commit_window(chunk,0,3,result)
    assert last(store,sid)['speaker_status']=='unknown_query_proof'


def test_public_api_explicit_mode_assertion_privacy_and_legacy_blocks(tmp_path,proven):
    file=tmp_path/'policy.json';file.write_text(json.dumps(POLICY))
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0,speaker_support_enabled=True,
                      verified_identity_policy_file=file,api_key='secret-fixture')
    with TestClient(create_app(settings)) as client:
        client.headers['Authorization']='Bearer secret-fixture'
        sid=client.post('/sessions',json={'name':'new','identity_group':'new','identity_mode':bank.MODE}).json()['id']
        store=client.app.state.store
        _,support,raw=captured(store,sid,proven)
        metadata=client.get(f'/sessions/{sid}/speaker-support/{support["id"]}').json()
        assert metadata['verified_review_eligible'] and metadata['vector_id']==raw['id']
        req=request(store,sid,support,raw)
        response=client.post(f'/sessions/{sid}/identity-reviews',json=req)
        assert response.status_code==200,response.text
        event=response.json()
        assert client.post(f'/sessions/{sid}/identity-reviews',json=req).json()==event
        pid=event['person_id'];segment=last(store,sid)['id']
        assert client.post('/sessions',json={'identity_group':'new','identity_mode':'legacy'}).status_code==409
        with pytest.raises(ValueError,match='review_required'):store.enroll_speaker(sid,'wrong',segment)
        with pytest.raises(ValueError,match='review_required'):store.add_speaker_example(sid,pid,segment)
        with pytest.raises(ValueError,match='unsupported'):store.correct_speaker(sid,pid,undo_merge=True)
        before=bank.bank(store,sid);store.correct_speaker(sid,pid,name='Only label')
        assert bank.bank(store,sid)==before and store.speakers(sid)[0]['state']=='reviewed_reference'
        _,row=query(store,sid,proven,seed=8)
        url=f'/sessions/{sid}/identity-decisions/{row["identity_decision_id"]}'
        data=client.get(url);assert data.status_code==200 and data.headers['cache-control']=='private, no-store'
        public=json.dumps(data.json())+json.dumps(client.get(f'/sessions/{sid}/changes').json())+json.dumps(metadata)
        for forbidden in ('raw_vector','normalized_sha256',str(tmp_path),'"pcm"','"embedding"'):
            assert forbidden not in public
        assert json.loads(raw['raw_vector']) not in [data.json().get('vector')]
        other=client.post('/sessions',json={'name':'separate','identity_group':'separate'}).json()['id']
        assert client.get(url.replace(sid,other)).status_code==404
        client.headers.clear();assert client.get(url).status_code==401


def test_legacy_mode_cannot_convert_and_requires_explicit_policy(tmp_path):
    settings=Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0)
    store=Store(tmp_path/'jobs.sqlite3',settings)
    store.create_session('legacy','x')
    with pytest.raises(ValueError,match='immutable'):store.create_session('bad','x',bank.MODE)
    with pytest.raises(ValueError,match='requires'):store.create_session('bad','new',bank.MODE)


def test_mutable_active_projection_cannot_change_immutable_review(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    event,_,_,_=enroll(store,sid,proven)
    with store.db() as db:
        db.execute('UPDATE identity_active_reviews SET evidence_key=? WHERE event_id=?',('0'*64,event['event_id']))
    _,row=query(store,sid,proven,seed=8)
    assert row['speaker_id'] is None and row['speaker_status']=='unknown_reference_integrity'


def test_replace_records_kind_for_rebuildable_ledger(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    event,_,support,raw=enroll(store,sid,proven)
    replacement=bank.mutate(store,sid,request(store,sid,support,raw,kind='negative',
        new_person_name=None,person_id=event['person_id']),previous=event['event_id'])
    with store.db() as db:
        row=db.execute('SELECT action,review_kind,previous_event_id FROM identity_review_events WHERE id=?',(replacement['event_id'],)).fetchone()
        assert tuple(row)==('replace','negative',event['event_id'])
        assert bank.projection_intact(db,'verified',event['generation'])
