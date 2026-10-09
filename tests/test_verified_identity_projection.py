"""Additional independent synthetic review; no model inference or production data."""
from test_verified_identity_bank import configured, captured, proven, request
from test_verified_identity_matching import query
from audio_backend import verified_identity as bank
import pytest


@pytest.mark.parametrize('field', ['space_id', 'identity_group'])
def test_active_projection_corruption_cannot_hide_winning_person(tmp_path, proven, field):
    _, store, sid = configured(tmp_path)
    _, support, raw = captured(store, sid, proven)
    alice = bank.mutate(store, sid, request(store, sid, support, raw))
    _, support, raw = captured(store, sid, proven, seed=1, vector=[.8, .6] + [0.] * 6)
    bob = bank.mutate(store, sid, request(store, sid, support, raw, new_person_name='Bob'))
    _, before = query(store, sid, proven, seed=2)
    assert before['speaker_id'] == alice['person_id']
    with store.db() as db:
        db.execute(f'UPDATE identity_active_reviews SET {field}=? WHERE event_id=?',
                   ('0' * 64, alice['event_id']))
    _, after = query(store, sid, proven, seed=3)
    assert after['speaker_id'] is None, (after['speaker_status'], after['speaker_id'] == bob['person_id'])
    assert after['speaker_status'] == 'unknown_reference_integrity'


import pytest

@pytest.mark.parametrize('field,value',[
    ('identity_group','elsewhere'),('generation','0'*32),('kind','negative'),
    ('person_id','0'*32),('evidence_key','0'*64),('space_id','0'*64),
])
def test_all_mutable_eligibility_fields_fail_before_gallery_filter(tmp_path,proven,field,value):
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    first=bank.mutate(store,sid,request(store,sid,support,raw))
    _,support,raw=captured(store,sid,proven,seed=1,vector=[.8,.6]+[0.]*6)
    bank.mutate(store,sid,request(store,sid,support,raw,new_person_name='Runner up'))
    with store.db() as db:
        db.execute(f'UPDATE identity_active_reviews SET {field}=? WHERE event_id=?',(value,first['event_id']))
    _,row=query(store,sid,proven,seed=2)
    assert row['speaker_id'] is None and row['speaker_status']=='unknown_reference_integrity'


@pytest.mark.parametrize('mutation',['missing_active','missing_person','merged_person','moved_person','missing_vector'])
def test_missing_or_filtered_competitor_never_promotes_runner_up(tmp_path,proven,mutation):
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    first=bank.mutate(store,sid,request(store,sid,support,raw))
    _,support,raw2=captured(store,sid,proven,seed=1,vector=[.8,.6]+[0.]*6)
    bank.mutate(store,sid,request(store,sid,support,raw2,new_person_name='Runner up'))
    with store.db() as db:
        if mutation=='missing_active':db.execute('DELETE FROM identity_active_reviews WHERE event_id=?',(first['event_id'],))
        elif mutation=='missing_person':db.execute('DELETE FROM speakers WHERE id=?',(first['person_id'],))
        elif mutation=='merged_person':db.execute('UPDATE speakers SET merged_into=? WHERE id=?',('0'*32,first['person_id']))
        elif mutation=='moved_person':db.execute('UPDATE speakers SET identity_group=? WHERE id=?',('elsewhere',first['person_id']))
        else:db.execute('DELETE FROM speaker_support_vectors WHERE id=?',(raw['id'],))
    _,row=query(store,sid,proven,seed=2)
    assert row['speaker_id'] is None and row['speaker_status']=='unknown_reference_integrity'


from audio_backend import change_feed
import uuid

def test_many_distinct_decisions_do_not_require_eager_invalidation(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, support, raw = captured(store, sid, proven)
    event = bank.mutate(store, sid, request(store, sid, support, raw))
    _, row = query(store, sid, proven, seed=1)
    final_id = None
    with store.db() as db:
        for i in range(2048):
            final_id = uuid.uuid4().hex
            db.execute('''INSERT INTO identity_decisions
                SELECT ?,identity_group,generation,chunk_id,window_start,?,snapshot
                FROM identity_decisions WHERE id=?''',
                (final_id, f'fixture-{i}', row['identity_decision_id']))
            db.execute('INSERT INTO identity_decision_state(decision_id) VALUES(?)', (final_id,))
        db.execute('''CREATE TRIGGER prohibit_eager_decision_invalidation
            BEFORE UPDATE ON identity_decision_state
            BEGIN SELECT RAISE(ABORT,'eager decision history update'); END''')
    cursor = change_feed.changes(store, sid)['high_watermark']
    state = bank.bank(store, sid)
    bank.mutate(store, sid, dict(expected_generation=state['generation'],
        expected_revision=state['profile_revision'], idempotency_key='distinct-decision-retract'),
        previous=event['event_id'], retract=True)
    delta = change_feed.changes(store, sid, cursor)
    assert len(delta['changes']) <= 3 and not delta['has_more']
    assert bank.decision(store, sid, final_id)['state'] == 'stale'
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_decision_state WHERE stale!=0').fetchone()[0] == 0


def test_group_projection_validation_does_not_scan_unrelated_ledger(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    event=bank.mutate(store,sid,request(store,sid,support,raw))
    def work(db):
        counts=[0]
        def progress():counts[0]+=1;return 0
        db.set_progress_handler(progress,1)
        try:assert bank.projection_intact(db,'verified',event['generation'])
        finally:db.set_progress_handler(None,0)
        return counts[0]
    with store.db() as db:
        before=work(db)
        for i in range(2048):
            db.execute('''INSERT INTO identity_review_events
                (id,identity_group,generation,sequence,action,operator_context,response,created)
                VALUES(?,?,'irrelevant',1,'retract','fixture','{}',0)''',
                (uuid.uuid4().hex,f'unrelated-{i}'))
        after=work(db)
    # The exact VM count is not an API promise, but unrelated groups cannot
    # cause a linear full-ledger scan. Small B-tree depth changes are allowed.
    assert after <= before + 100


def test_event_detail_resolves_audio_after_restart_and_reports_source_erasure(tmp_path,proven):
    from audio_backend.storage import Store
    from audio_backend import speaker_support
    settings,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    event=bank.mutate(store,sid,request(store,sid,support,raw))
    sibling=store.create_session('Same group query','verified')['id']
    _,row=query(store,sibling,proven,seed=1)
    reference=bank.decision(store,sibling,row['identity_decision_id'])['reference_event_ids'][0]
    reloaded=Store(store.path,settings)
    detail=bank.event_detail(reloaded,sibling,reference)
    assert detail['source']['source_session_id']==sid
    assert detail['source']['support_id']==support['id'] and detail['source']['audio_available']
    assert speaker_support.audio(store,sid,support['id'])
    assert bank.events(store,sibling)['events'][0]['detail_url'].endswith(reference)
    other=store.create_session('Other group','other',bank.MODE)['id']
    with pytest.raises(KeyError):bank.event_detail(store,other,reference)
    store.delete_session(sid)
    detail=bank.event_detail(store,sibling,reference)
    assert not detail['active'] and not detail['source']['audio_available']
    assert detail['source']['reason']=='support_revoked_or_removed'
    assert 'audio_url' not in detail['source']


def test_event_detail_http_auth_and_private_allowlist(tmp_path,proven):
    from fastapi.testclient import TestClient
    from audio_backend.app import create_app
    import json
    settings,store,sid=configured(tmp_path)
    settings.api_key='local-test-token'
    _,support,raw=captured(store,sid,proven)
    event=bank.mutate(store,sid,request(store,sid,support,raw))
    route=f'/sessions/{sid}/identity-reviews/{event["event_id"]}'
    with TestClient(create_app(settings)) as client:
        assert client.get(route).status_code==401
        client.headers['Authorization']='Bearer local-test-token'
        response=client.get(route)
        assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
        assert client.get(response.json()['source']['audio_url']).status_code==200
        encoded=json.dumps(response.json())
        for private in ('raw_vector','normalized_sha256','vector_sha256',str(tmp_path),'"pcm"','"embedding"'):
            assert private not in encoded
