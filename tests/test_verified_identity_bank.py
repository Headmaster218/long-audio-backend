"""Synthetic vectors/operator assertions only: no acoustic accuracy claims."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from audio_backend import embedding_space, speaker_support, support_vectors, verified_identity as bank
from audio_backend.storage import Store
from audio_backend import change_feed
from test_reviewable_support import setup, prepare

POLICY={'schema_version':1,'experimental':True,'single_threshold':.75,'centroid_threshold':.75,
        'margin':.1,'minimum_query_seconds':2.,'minimum_enrollment_seconds':2.}

@pytest.fixture
def proven(monkeypatch):
    binding=copy.deepcopy(next(iter(embedding_space._AUDITED_BINDINGS.values())))
    binding['model'].update(sha256='a'*64,dimension=8,version='test-v1')
    key=embedding_space._digest(binding)
    monkeypatch.setitem(embedding_space._AUDITED_BINDINGS,key,binding)
    return embedding_space._descriptor(key,SimpleNamespace(provider='cpu',num_threads=2))


def configured(tmp_path):
    settings,store=setup(tmp_path)
    settings.verified_identity_policy=POLICY
    change_feed.initialize(store)
    sid=store.create_session('verified','verified',bank.MODE)['id']
    return settings,store,sid


def captured(store,sid,proven,seed=0,vector=None):
    sid,chunk,result,wav,samples,lineage,generation=prepare(store,sid=sid,seed=seed)
    result['metadata']['speaker_embedding_space']=proven
    result['metadata']['speaker_embedding_space_id']=embedding_space.validate_space(proven)
    if vector:
        for row in result['segments']+result['speaker_turns']:row['speaker_embedding']=vector
        result['_speaker_support']=speaker_support.prepare_window(result,wav,lineage,generation,core_start=0,analyzer_start=0,settings=store.settings)
    assert store.commit_window(chunk,0,chunk['duration'],result)
    with store.db() as db:
        support=db.execute('SELECT * FROM speaker_support WHERE chunk_id=?',(chunk['id'],)).fetchone()
        raw=db.execute('SELECT * FROM speaker_support_vectors WHERE support_id=?',(support['id'],)).fetchone()
        return chunk,dict(support),dict(raw) if raw else None


def request(store,sid,support,raw,**kwargs):
    view=bank.bank(store,sid)
    return {'expected_generation':view['generation'],'expected_revision':view['profile_revision'],
            'idempotency_key':str(view['profile_revision']), 'kind':'positive', 'new_person_name':'Reviewed Person',
            'support_id':support['id'],'vector_id':raw['id'],'manifest_sha256':support['manifest_sha256'],
            'vector_sha256':raw['vector_sha256'],'space_id':raw['space_id'],
            'attestation':{'version':bank.ATTESTATION,'reviewed_all_pooled_support':True,
                           'single_identified_voice':True,'no_other_overlapping_voice':True},**kwargs}


def test_original_vectors_immutable_restart_and_no_legacy_backfill(tmp_path,proven):
    settings,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    assert speaker_support.digest(raw['raw_vector'])==json.loads(support['manifest'])['embedding_sha256']
    with store.db() as db:
        db.execute('DELETE FROM speaker_observations')
    reloaded=Store(store.path,settings)
    with reloaded.db() as db:
        assert support_vectors.read(db,support['id'])[0]['raw_vector']==raw['raw_vector']
        db.execute('DELETE FROM speaker_support_vectors')
    with pytest.raises(ValueError,match='vector_proof_unavailable'):
        bank.mutate(store,sid,request(store,sid,support,raw))


def test_assertion_retry_conflicts_retraction_and_replace(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    req=request(store,sid,support,raw)
    bad=copy.deepcopy(req);bad['attestation']['single_identified_voice']=False
    with pytest.raises(ValueError,match='Mixed or uncertain'):bank.mutate(store,sid,bad)
    event=bank.mutate(store,sid,req)
    assert bank.mutate(store,sid,req)==event
    bad=dict(req,new_person_name='Other')
    with pytest.raises(ValueError,match='idempotency_conflict'):bank.mutate(store,sid,bad)
    assert speaker_support.metadata(store,sid,support['id'])['human_verified'] is False
    req2=request(store,sid,support,raw,new_person_name='Corrected')
    newer=bank.mutate(store,sid,req2,previous=event['event_id'])
    assert newer['person_id']!=event['person_id']
    r={'expected_generation':req['expected_generation'],'expected_revision':2,'idempotency_key':'retract'}
    bank.mutate(store,sid,r,previous=newer['event_id'],retract=True)
    assert bank.bank(store,sid)['active_counts']=={}


def test_capacity_reserves_retraction_and_replace_rolls_back(tmp_path,proven,monkeypatch):
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    monkeypatch.setattr(bank,'MAX_EVENTS',2)
    first=bank.mutate(store,sid,request(store,sid,support,raw))
    with pytest.raises(OverflowError):bank.mutate(store,sid,request(store,sid,support,raw),previous=first['event_id'])
    assert bank.bank(store,sid)['active_counts']=={'positive':1}
    r={'expected_generation':first['generation'],'expected_revision':1,'idempotency_key':'remove-at-cap'}
    bank.mutate(store,sid,r,previous=first['event_id'],retract=True)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM identity_active_reviews').fetchone()[0]==0


def test_concurrent_claims_one_winner_and_rollback(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    req=request(store,sid,support,raw)
    def run(i):
        try:return bank.mutate(store,sid,dict(req,idempotency_key=str(i),new_person_name=str(i)))
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert sum(x is not None for x in results)==1
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_active_reviews').fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM speakers WHERE state='reviewed_reference'").fetchone()[0]==1


def test_source_expiry_and_reset_remove_trust(tmp_path,proven):
    from audio_backend.identity_reset import reset_group
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    req=request(store,sid,support,raw);bank.mutate(store,sid,req)
    with store.db() as db:db.execute('DELETE FROM speaker_support WHERE id=?',(support['id'],))
    assert bank.bank(store,sid)['active_counts']=={}
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_support_vectors').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM identity_review_events WHERE action='source_expired'").fetchone()[0]==1
    reset_group(store,'verified')
    assert bank.bank(store,sid)['generation']!=req['expected_generation']
    with pytest.raises(ValueError,match='stale_generation'):bank.mutate(store,sid,req)
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0]==0


def test_combined_budget_does_not_allow_vector_overflow(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    store.settings.speaker_support_total_pcm_bytes=3*16000*4
    _,support,raw=captured(store,sid,proven)
    assert support['status']=='available' and raw is None
    with store.db() as db:assert support_vectors.retained_bytes(db)==3*16000*4


def test_duplicate_audio_cannot_add_weight_or_conflicting_person(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    first=bank.mutate(store,sid,request(store,sid,support,raw))
    _,same,same_raw=captured(store,sid,proven)
    assert same_raw['evidence_key']==raw['evidence_key']
    for extra in ({'new_person_name':'Other'}, {'new_person_name':None,'person_id':first['person_id']}):
        with pytest.raises(ValueError,match='duplicate_or_conflicting'):
            bank.mutate(store,sid,request(store,sid,same,same_raw,**extra))
    with pytest.raises(ValueError,match='duplicate_or_contradictory'):
        bank.mutate(store,sid,request(store,sid,support,raw,kind='negative',new_person_name=None,person_id=first['person_id']))


def test_eight_positive_limit_and_vector_space_binding(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    person=None
    for seed in range(9):
        _,support,raw=captured(store,sid,proven,seed=seed)
        req=request(store,sid,support,raw,**({'new_person_name':None,'person_id':person} if person else {}))
        if seed==8:
            with pytest.raises(OverflowError,match='eight'):bank.mutate(store,sid,req)
        else:person=bank.mutate(store,sid,req)['person_id']
    assert bank.bank(store,sid)['active_counts']=={'positive':8}
    req['space_id']='0'*64
    with pytest.raises(ValueError,match='binding mismatch'):bank.mutate(store,sid,req)


def test_cross_session_group_and_mutation_failure_atomic(tmp_path,proven,monkeypatch):
    _,store,sid=configured(tmp_path)
    _,support,raw=captured(store,sid,proven)
    request1=request(store,sid,support,raw)
    other=store.create_session('same group','verified')['id']
    with pytest.raises(KeyError):bank.mutate(store,other,request1)
    before=change_feed.changes(store,sid)['high_watermark']
    def broken(*args):raise RuntimeError('transaction failure injection')
    monkeypatch.setattr(bank,'_invalidate',broken)
    with pytest.raises(RuntimeError):bank.mutate(store,sid,request1)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM speakers WHERE state='reviewed_reference'").fetchone()[0]==0
    assert change_feed.changes(store,sid)['high_watermark']==before


def test_query_proof_rejects_raw_mismatch_and_unknown_space(tmp_path,proven):
    _,store,sid=configured(tmp_path)
    _,chunk,result,*_=prepare(store,sid=sid)
    value=result['_speaker_support']['A']
    result['metadata'].update(speaker_embedding_space=proven,speaker_embedding_space_id=embedding_space.validate_space(proven))
    assert support_vectors.query_proof(value['raw_vector'],result['metadata'],value)['space_id']
    with pytest.raises(ValueError,match='fingerprint mismatch'):
        support_vectors.query_proof([.9]+[0.]*7,result['metadata'],value)
    changed=copy.deepcopy(proven);changed['frontend']['snip_edges']=True
    result['metadata']['speaker_embedding_space']=changed
    with pytest.raises(ValueError):support_vectors.query_proof(value['raw_vector'],result['metadata'],value)


def test_policy_read_is_bounded_before_loading(tmp_path,monkeypatch):
    from pathlib import Path
    path=tmp_path/'too-large.json';path.write_bytes(b'x'*8193)
    def unexpected(*args,**kwargs):raise AssertionError('oversized policy must not be opened')
    monkeypatch.setattr(Path,'open',unexpected)
    with pytest.raises(ValueError,match='8 KiB'):bank.policy_file(path)


def test_genuine_legacy_observation_eviction_does_not_replace_original_vector(tmp_path,proven):
    _,store=setup(tmp_path,max_matched_speaker_observations_per_group=1)
    sid=store.create_session('Legacy capture')['id']
    _,first,original=captured(store,sid,proven,seed=0)
    assert first['observation_id'] is not None
    captured(store,sid,proven,seed=1)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]==1
        assert db.execute('SELECT 1 FROM speaker_observations WHERE id=?',(first['observation_id'],)).fetchone() is None
        retained,_=support_vectors.read(db,first['id'])
        assert retained['raw_vector']==original['raw_vector']
        assert retained['vector_sha256']==original['vector_sha256']
