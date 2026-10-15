"""Synthetic assignment contracts only; no acoustic accuracy claims."""
import copy
import pytest
from audio_backend import verified_identity as bank, speaker_support, embedding_space
from audio_backend.speaker_evidence import clean_speaker_audio
from audio_backend.storage import Store
from test_verified_identity_bank import configured, proven
from test_verified_identity_matching import enroll, query
from test_reviewable_support import prepare, result_for


def turn(local, start, end):
    return dict(speaker_local_id=local, start=start, end=end)


def draft(person):
    return dict(original_person_id=person, candidate_person_id=person,
                score=.9, status='experimental_identity_match')


@pytest.mark.parametrize('reverse', [False, True])
def test_symmetric_collisions_do_not_promote_or_choose_winner(reverse):
    drafts={key:draft('person') for key in 'ABC'}
    turns=[turn('A',0,2),turn('B',1,3),turn('C',2.5,4)]
    bank.exclude_overlap_collisions(drafts, list(reversed(turns)) if reverse else turns)
    assert all(d['original_person_id'] is None for d in drafts.values())
    assert all(d['candidate_person_id']=='person' and d['score']==.9 for d in drafts.values())
    assert drafts['A']['overlap_conflicting_local_ids']==['B']
    assert drafts['B']['overlap_conflicting_local_ids']==['A','C']
    assert drafts['C']['overlap_conflicting_local_ids']==['B']


@pytest.mark.parametrize('turns,people', [
    ([turn('A',0,1),turn('B',1,2)],['person','person']),
    ([turn('A',0,2),turn('A',1,3)],['person','person']),
    ([turn('A',0,2),turn('B',1,3)],['person','other']),
    ([turn('A',0,2),turn('B',1,3)],['person',None]),
    ([turn('A',0,2),turn('B',float('nan'),3)],['person','person']),
])
def test_only_positive_overlap_of_distinct_accepted_local_ids_is_conflict(turns,people):
    drafts=dict(zip('AB',map(draft,people)));before=copy.deepcopy(drafts)
    bank.exclude_overlap_collisions(drafts,turns)
    assert drafts==before


def collision_window(store,sid,proven,reverse=False):
    _,chunk,_,wav,samples,lineage,generation=prepare(store,sid=sid,seconds=7,seed=11)
    turns=[turn('A',0.,4.),turn('B',3.,7.)]
    if reverse:turns.reverse()
    result=result_for(samples,turns)
    result['segments']=[dict(start=0.,end=1.,text='a',speaker_local_id='A'),
                        dict(start=5.,end=6.,text='b',speaker_local_id='B')]
    for local_id in 'AB':
        result['metadata']['speaker_embedding_evidence'][local_id]=clean_speaker_audio(samples,turns,local_id)[1]
    result['metadata'].update(speaker_embedding_space=proven,
        speaker_embedding_space_id=embedding_space.validate_space(proven))
    result['_speaker_support']=speaker_support.prepare_window(result,wav,lineage,generation,
        core_start=0,analyzer_start=0,settings=store.settings)
    assert all(result['_speaker_support'][key]['status']=='available' for key in 'AB')
    return chunk,result


@pytest.mark.parametrize('reverse',[False,True])
def test_store_persists_only_final_abstention_and_restart(tmp_path,proven,reverse):
    settings,store,sid=configured(tmp_path)
    event,*_=enroll(store,sid,proven)
    chunk,result=collision_window(store,sid,proven,reverse)
    assert store.commit_window(chunk,0,7,result)
    with store.db() as db:
        rows=list(db.execute('SELECT * FROM segments WHERE chunk_id=?',(chunk['id'],)))
        turns=list(db.execute('SELECT * FROM speaker_turns WHERE chunk_id=?',(chunk['id'],)))
        assert db.execute('SELECT COUNT(*) FROM identity_decisions WHERE chunk_id=?',(chunk['id'],)).fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM identity_relation_events').fetchone()[0]==0
    assert len(rows)==len(turns)==2
    reloaded=Store(store.path,settings)
    for row in rows+turns:
        assert row['speaker_id'] is None
        assert row['speaker_status']=='unknown_overlap_identity_conflict'
        decision=bank.decision(reloaded,sid,row['identity_decision_id'])
        assert decision['original_person_id'] is None
        assert decision['candidate_person_id']==event['person_id']
        assert decision['status']=='unknown_overlap_identity_conflict'
        assert decision['overlap_conflicting_local_ids'] in (['A'],['B'])
    # Same local ID in another recording/window does not inherit this conflict.
    other=store.create_session('another recording','verified')['id']
    _,row=query(store,other,proven,seed=17)
    assert row['speaker_id']==event['person_id']


def test_failure_rolls_back_all_prepared_support_decisions_and_projections(tmp_path,proven,monkeypatch):
    _,store,sid=configured(tmp_path);enroll(store,sid,proven)
    chunk,result=collision_window(store,sid,proven)
    original=bank.persist_decision
    calls=[]
    def fail_second(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise RuntimeError('injected second decision failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(bank,'persist_decision',fail_second)
    with pytest.raises(RuntimeError,match='injected'):
        store.commit_window(chunk,0,7,result)
    with store.db() as db:
        for table in ('identity_decisions','segments','speaker_turns','speaker_support'):
            assert db.execute(f'SELECT COUNT(*) FROM {table} WHERE chunk_id=?',(chunk['id'],)).fetchone()[0]==0
        assert db.execute('SELECT next_offset FROM chunks WHERE id=?',(chunk['id'],)).fetchone()[0]==0


def test_canonical_alias_collision_and_overlap_segment_reason(tmp_path,proven):
    from test_verified_identity_canonical import people, merge
    _,store,sid=configured(tmp_path)
    a,b=people(store,sid,proven)
    merge(store,sid,a,b)
    chunk,result=collision_window(store,sid,proven)
    result['segments'][0]['overlap']=True
    assert store.commit_window(chunk,0,7,result)
    with store.db() as db:
        rows=list(db.execute('SELECT * FROM segments WHERE chunk_id=?',(chunk['id'],)))
    for row in rows:
        assert row['speaker_id'] is None
        assert row['speaker_status']=='unknown_overlap_identity_conflict'
        decision=bank.decision(store,sid,row['identity_decision_id'])
        assert decision['candidate_person_id']==b
        assert len(decision['reference_event_ids'])==2


@pytest.mark.parametrize('overflow', ['turns','locals'])
def test_capacity_abstains_accepted_only_without_promoting_unknown(overflow):
    drafts={'A':draft('person'),'B':draft(None)}
    turns=[turn('A',0,1)]
    if overflow=='turns':turns*=bank.MAX_OVERLAP_TURNS+1
    else:drafts.update({str(i):draft('person') for i in range(bank.MAX_OVERLAP_LOCAL_IDS)})
    bank.exclude_overlap_collisions(drafts,turns)
    for local_id,snapshot in drafts.items():
        assert snapshot['original_person_id'] is None
        assert snapshot['status']==('experimental_identity_match' if local_id=='B' else 'unknown_overlap_identity_capacity')
        assert 'overlap_conflicting_local_ids' not in snapshot


def test_capacity_boundary_and_duplicate_turn_union():
    drafts={str(i):draft('person') for i in range(bank.MAX_OVERLAP_LOCAL_IDS)}
    turns=[turn(str(i % bank.MAX_OVERLAP_LOCAL_IDS),0,1) for i in range(bank.MAX_OVERLAP_TURNS)]
    bank.exclude_overlap_collisions(drafts,turns)
    assert all(d['status']=='unknown_overlap_identity_conflict' for d in drafts.values())
    assert all(len(d['overlap_conflicting_local_ids'])==bank.MAX_OVERLAP_LOCAL_IDS-1 for d in drafts.values())


def test_store_capacity_preserves_transcript_and_immutable_reason(tmp_path,proven,monkeypatch):
    _,store,sid=configured(tmp_path);enroll(store,sid,proven)
    chunk,result=collision_window(store,sid,proven)
    monkeypatch.setattr(bank,'MAX_OVERLAP_TURNS',1)
    assert store.commit_window(chunk,0,7,result)
    with store.db() as db:
        rows=list(db.execute('SELECT * FROM segments WHERE chunk_id=? ORDER BY start',(chunk['id'],)))
    assert [row['text'] for row in rows]==['a','b']
    for row in rows:
        assert row['speaker_id'] is None
        assert row['speaker_status']=='unknown_overlap_identity_capacity'
        assert bank.decision(store,sid,row['identity_decision_id'])['status']=='unknown_overlap_identity_capacity'
