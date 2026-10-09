"""Synthetic relation/provenance contracts, not acoustic accuracy measurements."""
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from audio_backend import change_feed, identity_canonical as relations, verified_identity as bank
from audio_backend.app import create_app
from audio_backend.identity_reset import reset_group
from audio_backend.storage import Store
from test_verified_identity_bank import configured, captured, proven, request
from test_verified_identity_matching import enroll, query


def mutation(store, sid, key):
    state = bank.bank(store, sid)
    return dict(expected_generation=state['generation'], expected_revision=state['profile_revision'],
                idempotency_key=key)


def relation(store, sid, source, target, kind='same_person', key=None):
    body = mutation(store, sid, key or f'relation-{bank.bank(store,sid)["profile_revision"]}')
    body.update(person_id=source, other_person_id=target, kind=kind, confirmed=True)
    return body


def merge(store, sid, a, b):
    return relations.mutate(store, sid, relation(store, sid, a, b))


def undo(store, sid, event):
    return relations.mutate(store, sid, mutation(store, sid, 'undo-'+event['event_id']), previous=event['event_id'])


def roster(store, sid):
    return {row['id']: row['canonical_person_id'] for row in store.speakers(sid)}


def people(store, sid, proven, count=2):
    return [enroll(store, sid, proven, seed=i, new_person_name=f'Person {i}')[0]['person_id'] for i in range(count)]


def immutable(store):
    with store.db() as db:
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in ('speakers', 'speaker_observations', 'speaker_support_vectors',
                              'identity_review_events', 'identity_active_reviews', 'identity_decisions')}


def all_changes(store, sid, after=0):
    rows = []
    while True:
        page = change_feed.changes(store, sid, after, 100)
        rows.extend(page['changes'])
        if not page['has_more']:
            return rows
        after = page['next_cursor']


def test_merge_preserves_evidence_and_compares_distinct_canonical_people(tmp_path, proven):
    settings, store, sid = configured(tmp_path)
    a, b = people(store, sid, proven)
    _, before_query = query(store, sid, proven, seed=2)
    assert before_query['speaker_status'] == 'unknown_ambiguous'
    before = immutable(store)
    cursor = change_feed.changes(store, sid)['high_watermark']
    event = merge(store, sid, a, b)
    assert immutable(store) == before
    assert roster(store, sid) == {a: b, b: b}
    assert not event['acoustic_support_certified']
    delta = all_changes(store, sid, cursor)
    assert len(delta) == 1 and delta[0]['entity'] == 'identity_bank'
    assert delta[0]['data']['refresh_canonical_persons'] is True
    _, current = query(store, sid, proven, seed=3)
    assert current['speaker_id'] == b and current['speaker_status'] == 'experimental_identity_match'
    decision = bank.decision(store, sid, current['identity_decision_id'])
    assert len(decision['reference_event_ids']) == 2
    assert decision['margin'] == 2 and not decision['automatic_query_is_human_verified']
    assert bank.bank(store, sid)['active_counts'] == {'positive': 2}
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0] == 0
        assert all(r[0] is None for r in db.execute('SELECT merged_into FROM speakers'))
    reloaded = Store(store.path, settings)
    change_feed.initialize(reloaded)
    assert roster(reloaded, sid) == roster(store, sid)
    assert relations.events(reloaded, sid) == relations.events(store, sid)


@pytest.mark.parametrize('first_undo', [0, 1])
def test_nested_undo_preserves_exact_owners_of_later_reviews(tmp_path, proven, first_undo):
    _, store, sid = configured(tmp_path)
    a, b, c = people(store, sid, proven, 3)
    ab = merge(store, sid, a, b)
    bc = merge(store, sid, b, c)
    _, support, raw = captured(store, sid, proven, seed=4)
    added = bank.mutate(store, sid, request(store, sid, support, raw, person_id=a, new_person_name=None))
    # A request addressed to the canonical root remains owned by that root too.
    _, support2, raw2 = captured(store, sid, proven, seed=5)
    root_review = bank.mutate(store, sid, request(store, sid, support2, raw2, person_id=c, new_person_name=None))
    before = immutable(store)
    sequence = [ab, bc] if first_undo == 0 else [bc, ab]
    undo(store, sid, sequence[0])
    expected = {a: a, b: c, c: c} if first_undo == 0 else {a: b, b: b, c: c}
    assert roster(store, sid) == expected
    undo(store, sid, sequence[1])
    assert roster(store, sid) == {a: a, b: b, c: c}
    assert immutable(store) == before
    assert bank.event_detail(store, sid, added['event_id'])['person_id'] == a
    assert bank.event_detail(store, sid, root_review['event_id'])['person_id'] == c


def test_constraints_are_symmetric_nontransitive_and_retractable(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    a, b, c = people(store, sid, proven, 3)
    constraint = relations.mutate(store, sid, relation(store, sid, a, b, 'different_people'))
    with pytest.raises(ValueError, match='duplicate_different'):
        relations.mutate(store, sid, relation(store, sid, b, a, 'different_people'))
    # A!=B and B!=C say nothing about A versus C.
    relations.mutate(store, sid, relation(store, sid, b, c, 'different_people'))
    merge(store, sid, a, c)
    with pytest.raises(ValueError, match='conflicting_people'):
        merge(store, sid, b, c)
    with pytest.raises(ValueError, match='distinct canonical'):
        relations.mutate(store, sid, relation(store, sid, a, c, 'different_people'))
    undo(store, sid, constraint)
    with pytest.raises(ValueError, match='conflicting_people'):
        merge(store, sid, c, b)  # The other explicit constraint still applies.


def test_positive_negative_conflict_blocks_merge_and_later_alias_review(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    a, _, support_a, raw_a = enroll(store, sid, proven)
    b, _, _, _ = enroll(store, sid, proven, seed=1)
    a, b = a['person_id'], b['person_id']
    negative = bank.mutate(store, sid, request(store, sid, support_a, raw_a,
                         kind='negative', person_id=b, new_person_name=None))
    before = bank.bank(store, sid)
    with pytest.raises(ValueError, match='contradictory_review'):
        merge(store, sid, a, b)
    assert bank.bank(store, sid) == before and roster(store, sid) == {a: a, b: b}
    bank.mutate(store, sid, mutation(store, sid, 'remove-negative'), previous=negative['event_id'], retract=True)
    merge(store, sid, a, b)
    with pytest.raises(ValueError, match='contradictory_review'):
        bank.mutate(store, sid, request(store, sid, support_a, raw_a,
                    kind='negative', person_id=b, new_person_name=None))


def test_alias_negative_veto_never_promotes_passing_runner_up(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    a, b = people(store, sid, proven)
    c, _, _, _ = enroll(store, sid, proven, seed=2, vector=[.8,.6]+[0.]*6)
    merge(store, sid, a, b)
    _, support, raw = captured(store, sid, proven, seed=3)
    negative = bank.mutate(store, sid, request(store, sid, support, raw,
                          kind='negative', person_id=a, new_person_name=None))
    _, output = query(store, sid, proven, seed=3)
    assert output['speaker_id'] is None and output['speaker_status'] == 'unknown_exact_negative'
    decision = bank.decision(store, sid, output['identity_decision_id'])
    assert decision['candidate_person_id'] == b and decision['negative_event_id'] == negative['event_id']
    assert decision['margin'] == pytest.approx(.2)
    _, unrelated = query(store, sid, proven, seed=4)
    assert unrelated['speaker_id'] == b and unrelated['speaker_id'] != c['person_id']
    assert bank.bank(store, sid)['active_counts'] == {'positive': 3, 'negative': 1}


def test_eight_positive_component_cap_on_merge_add_and_replace(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    a, b = people(store, sid, proven)
    first = None
    for seed in range(2, 8):
        _, support, raw = captured(store, sid, proven, seed=seed)
        event = bank.mutate(store, sid, request(store, sid, support, raw,
                            person_id=a if seed < 5 else b, new_person_name=None))
        first = first or event
    merge(store, sid, a, b)
    _, support, raw = captured(store, sid, proven, seed=8)
    extra = request(store, sid, support, raw, person_id=a, new_person_name=None)
    with pytest.raises(OverflowError, match='eight'):
        bank.mutate(store, sid, extra)
    # Atomic one-for-one replacement preserves the component bound and owner.
    replacement = bank.mutate(store, sid, extra, previous=first['event_id'])
    assert replacement['person_id'] == a
    c, _, _, _ = enroll(store, sid, proven, seed=9)
    before = immutable(store)
    with pytest.raises(OverflowError, match='explicitly retract'):
        merge(store, sid, b, c['person_id'])
    assert immutable(store) == before


def test_get_feed_human_projection_and_stale_automatic_history(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    a, b = people(store, sid, proven)
    _, row = query(store, sid, proven, seed=2)
    store.assign_segment(sid, row['id'], a)
    before = immutable(store)
    event = merge(store, sid, a, b)
    for source in (store.results(sid)['segments'],
                   [c['data'] for c in all_changes(store, sid) if c['entity']=='segments']):
        human = [r for r in source if r['id']==row['id'] and r['speaker_status']=='human_corrected'][-1]
        assert human['speaker_id'] == b and human['original_speaker_id'] == a
        assert human['identity_state'] == 'stale' and human['canonical_revision'] == event['profile_revision']
    for record in (c['data'] for c in all_changes(store, sid) if c['entity']=='speakers'):
        assert record['canonical_person_id'] == b and record['canonical_state'] == 'current'
    _, auto = query(store, sid, proven, seed=3)
    prediction = bank.decision(store, sid, auto['identity_decision_id'])
    undo(store, sid, event)
    output = {r['id']:r for r in store.results(sid)['segments']}
    assert output[row['id']]['speaker_id'] == a
    assert output[auto['id']]['speaker_id'] is None and output[auto['id']]['original_speaker_id'] == b
    assert bank.decision(store, sid, auto['identity_decision_id'])['original_person_id'] == prediction['original_person_id']
    assert all(t['speaker_id'] is None for t in store.speaker_turns(sid)['speaker_turns'])
    assert immutable(store)['identity_review_events'] == before['identity_review_events']


@pytest.mark.parametrize('identical', [True, False])
def test_concurrent_cas_and_identical_retry_after_undo(tmp_path, proven, identical):
    _, store, sid = configured(tmp_path)
    a, b = people(store, sid, proven)
    first = relation(store, sid, a, b, key='concurrent-merge')
    second = copy.deepcopy(first)
    if not identical:
        second.update(kind='different_people', idempotency_key='concurrent-conflict')
    def attempt(body):
        try:
            return relations.mutate(store, sid, body)
        except ValueError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [first, second]))
    successes = [result for result in outcomes if isinstance(result, dict)]
    assert len(successes) == (2 if identical else 1)
    if identical:
        assert successes[0] == successes[1]
        undo(store, sid, successes[0])
        assert relations.mutate(store, sid, first) == successes[0]
        assert roster(store, sid) == {a: a, b: b}
    else:
        assert 'stale_profile_revision' in str(next(r for r in outcomes if isinstance(r, ValueError)))


def test_feed_failure_rolls_back_entire_relation_and_revision(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    a, b = people(store, sid, proven)
    with store.db() as db:
        db.execute("""CREATE TRIGGER fail_relation_feed BEFORE INSERT ON result_changes
            WHEN NEW.entity='identity_bank' BEGIN SELECT RAISE(ABORT,'injected relation failure'); END""")
    view = bank.bank(store, sid)
    cursor = change_feed.changes(store, sid)['high_watermark']
    with pytest.raises(sqlite3.IntegrityError, match='injected relation'):
        merge(store, sid, a, b)
    assert bank.bank(store, sid) == view
    assert relations.events(store, sid)['events'] == []
    assert roster(store, sid) == {a: a, b: b}
    assert change_feed.changes(store, sid)['high_watermark'] == cursor


def test_reserved_relation_capacity_allows_undo_and_constraint_retraction(tmp_path, proven, monkeypatch):
    _, store, sid = configured(tmp_path)
    a, b, c = people(store, sid, proven, 3)
    monkeypatch.setattr(relations, 'MAX_EVENTS', 4)
    ab = merge(store, sid, a, b)
    constraint = relations.mutate(store, sid, relation(store, sid, a, c, 'different_people'))
    with pytest.raises(OverflowError, match='capacity'):
        relations.mutate(store, sid, relation(store, sid, b, c, 'different_people'))
    undo(store, sid, ab)
    undo(store, sid, constraint)
    assert len(relations.events(store, sid)['events']) == 4
    assert roster(store, sid) == {a: a, b: b, c: c}


@pytest.mark.parametrize('damage', ['delete', 'move', 'generation', 'hash', 'head', 'count', 'person', 'legacy_alias'])
def test_relation_damage_never_hides_conflict_or_promotes_runnerup(tmp_path, proven, damage):
    _, store, sid = configured(tmp_path)
    a, b = people(store, sid, proven)
    event = relations.mutate(store, sid, relation(store, sid, a, b, 'different_people'))
    with store.db() as db:
        if damage == 'delete':
            db.execute('DELETE FROM identity_relation_events WHERE id=?', (event['event_id'],))
        elif damage in ('move','generation','hash'):
            db.execute('DROP TRIGGER relation_events_immutable')
            field = {'move':'identity_group','generation':'generation','hash':'event_sha256'}[damage]
            db.execute(f'UPDATE identity_relation_events SET {field}=? WHERE id=?', ('0'*32,event['event_id']))
        elif damage == 'head':
            db.execute("UPDATE identity_group_policy SET relation_head='' WHERE identity_group='verified'")
        elif damage == 'count':
            db.execute("UPDATE identity_group_policy SET relation_count=0 WHERE identity_group='verified'")
        elif damage == 'person':
            db.execute('DELETE FROM speakers WHERE id=?',(a,))
        else:
            db.execute('UPDATE speakers SET merged_into=? WHERE id=?',(b,a))
    _, row = query(store, sid, proven, seed=2)
    assert row['speaker_id'] is None and row['speaker_status'] == 'unknown_reference_integrity'
    assert row['canonical_state'] == 'unavailable'
    with pytest.raises(ValueError, match='integrity'):
        merge(store, sid, a, b)


def test_source_deletion_preserves_relation_then_reset_erases_it(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    a = enroll(store, sid, proven)[0]['person_id']
    survivor = store.create_session('Surviving recording','verified')['id']
    b = enroll(store, survivor, proven, seed=1)[0]['person_id']
    body = relation(store, sid, a, b)
    event = relations.mutate(store, sid, body)
    store.delete_session(sid)
    assert bank.bank(store, survivor)['active_counts'] == {'positive':1}
    assert roster(store, survivor) == {a:b,b:b}
    undo(store, survivor, event)
    assert roster(store, survivor) == {a:a,b:b}
    reset_group(store, 'verified')
    assert relations.events(store, survivor)['events'] == [] and roster(store, survivor) == {}
    with store.db() as db:
        assert tuple(db.execute("SELECT relation_count,relation_head FROM identity_group_policy WHERE identity_group='verified'").fetchone()) == (0,'')
    with pytest.raises(ValueError, match='stale_generation'):
        relations.mutate(store, survivor, body)
    assert a not in json.dumps([c['data'] for c in all_changes(store,survivor)])


def test_api_auth_strict_scope_pagination_and_no_audio_certification(tmp_path, proven):
    settings, store, sid = configured(tmp_path)
    a, b = people(store, sid, proven)
    settings.api_key = 'synthetic-relation-token'
    route = f'/sessions/{sid}/identity-relations'
    with TestClient(create_app(settings)) as client:
        body = relation(store, sid, a, b)
        assert client.post(route, json=body).status_code == 401
        client.headers['Authorization'] = 'Bearer '+settings.api_key
        assert client.post(route,json=dict(body,confirmed='yes')).status_code == 422
        assert client.post(route,json=dict(body,vector=[1])).status_code == 422
        assert client.post(route,json=dict(body,confirmed=False)).status_code == 409
        event = client.post(route,json=body)
        assert event.status_code == 200 and event.headers['cache-control'] == 'private, no-store'
        assert not event.json()['acoustic_support_certified']
        assert client.post(route,json=body).json() == event.json()
        assert client.post(route,json=dict(body,kind='different_people')).status_code == 409
        other = store.create_session('Other','other',bank.MODE)['id']
        assert client.post(f'/sessions/{other}/identity-relations',json=relation(store,other,a,b)).status_code == 404
        assert client.get(f'/sessions/{other}/identity-relations').json()['events'] == []
        current = mutation(store,sid,'http-undo')
        undone = client.post(route+'/'+event.json()['event_id']+'/retract',json=current)
        assert undone.status_code == 200 and undone.json()['action'] == 'undo_merge'
        first = client.get(route+'?limit=1').json()
        second = client.get(route+f'?limit=1&after={first["next_cursor"]}').json()
        assert len(first['events']) == len(second['events']) == 1
        assert not first['events'][0]['active']
        encoded = json.dumps([first,second,store.speakers(sid),all_changes(store,sid)])
        for private in ('raw_vector','request_sha256','event_sha256','relation_head',str(tmp_path),settings.api_key):
            assert private not in encoded


def test_ambiguous_alias_merge_rejected_and_idempotency_namespaces_shared(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    a, b, c = people(store, sid, proven, 3)
    with pytest.raises(ValueError, match='idempotency_conflict'):
        relations.mutate(store,sid,relation(store,sid,a,b,key='0'))
    event = merge(store,sid,a,b)
    with pytest.raises(ValueError, match='canonical roots'):
        merge(store,sid,a,c)
    _, support, raw = captured(store,sid,proven,seed=3)
    with store.db() as db:
        key = db.execute('SELECT idempotency_key FROM identity_relation_events WHERE id=?',(event['event_id'],)).fetchone()[0]
    with pytest.raises(ValueError, match='idempotency_conflict'):
        bank.mutate(store,sid,request(store,sid,support,raw,idempotency_key=key))
