"""Independent model-free lifecycle checks; synthetic assertions are not accuracy evidence.

Only scratch databases and generated three-second waveforms are used. These tests
exercise public projections plus transaction boundaries, rather than a model or
an inference provider. A policy file is always explicitly selected.
"""
import copy
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from audio_backend import change_feed, embedding_space, speaker_support, support_vectors
from audio_backend import verified_identity as bank
from audio_backend.app import create_app
from audio_backend.identity_reset import reset_group
from audio_backend.storage import Store, ident
from test_reviewable_support import prepare, setup
from test_verified_identity_bank import POLICY, captured, proven, request


VECTOR = [1.0] + [0.0] * 7
TRANSACTION_TABLES = (
    'identity_group_policy', 'speaker_identity_generations', 'identity_feed_suppression',
    'identity_review_events', 'identity_review_event_order',
    'identity_active_reviews', 'identity_decisions', 'identity_decision_state',
    'identity_decision_sources', 'speaker_support', 'speaker_support_vectors', 'speaker_support_sources', 'chunks',
    'speakers', 'segments', 'speaker_turns', 'result_changes',
)


def configured(tmp_path, **settings_kwargs):
    policy_path = tmp_path / 'explicit-experimental-policy.json'
    policy_path.write_text(json.dumps(POLICY))
    settings, store = setup(tmp_path, verified_identity_policy_file=policy_path,
                            **settings_kwargs)
    change_feed.initialize(store)
    sid = store.create_session('Review source', 'verified', bank.MODE)['id']
    return settings, store, sid


def recording(store, sid, space, *, seed=0, vector=None, extra_source=False):
    """Capture even when retention is refused; query proof is independent."""
    _, chunk, result, wav, _, lineage, generation = prepare(store, sid=sid, seed=seed)
    result['metadata'].update(speaker_embedding_space=space,
                             speaker_embedding_space_id=embedding_space.validate_space(space))
    for row in result['segments'] + result['speaker_turns']:
        row['speaker_embedding'] = list(VECTOR if vector is None else vector)
    dependency = None
    if extra_source:
        extra = store.settings.data_dir / 'uploads' / f'{ident()}.audio'
        extra.write_bytes(wav.read_bytes())
        dependency, _ = store.add_chunk(sid, ident(), chunk['digest'], extra, 3)
        with store.db() as db:
            db.execute("UPDATE chunks SET status='completed',next_offset=duration WHERE id=?", (dependency,))
        first = lineage['parts'][0]
        first.update(analyzer_frames=[0, 16000], requested_seconds=1.0)
        lineage['parts'].append(dict(first, chunk_id=dependency,
                                    analyzer_frames=[16000, 48000], requested_seconds=2.0))
    result['_speaker_support'] = speaker_support.prepare_window(
        result, wav, lineage, generation, core_start=0, analyzer_start=0,
        settings=store.settings)
    assert result['_speaker_support']['A']['status'] == 'available'
    assert store.commit_window(chunk, 0, chunk['duration'], result)
    segment = next(row for row in store.results(sid)['segments'] if row['chunk_id'] == chunk['id'])
    return chunk, segment, dependency


def review(store, sid, space, *, seed=0, vector=None, name='Reviewed Alice', person=None):
    chunk, support, raw = captured(store, sid, space, seed=seed, vector=vector)
    event = bank.mutate(store, sid, request(
        store, sid, support, raw, new_person_name=None if person else name,
        **({'person_id': person} if person else {})))
    return chunk, support, raw, event


def retract_request(store, sid, key='retract'):
    view = bank.bank(store, sid)
    return dict(expected_generation=view['generation'], expected_revision=view['profile_revision'],
                idempotency_key=key)


def snapshot(store):
    with store.db() as db:
        return {table: sorted((tuple(row) for row in db.execute(f'SELECT * FROM {table}')), key=repr)
                for table in TRANSACTION_TABLES}


def all_changes(store, sid, after=0):
    rows = []
    while True:
        page = change_feed.changes(store, sid, after=after, limit=7)
        rows.extend(page['changes'])
        if not page['has_more']:
            return rows
        assert page['next_cursor'] > after
        after = page['next_cursor']


def assert_stale_change(store, sid, segment, after, *, human_person=None):
    changes = all_changes(store, sid, after)
    invalidations = [row['data'] for row in changes
                     if row['entity'] == 'identity_bank' and row['operation'] == 'upsert']
    assert invalidations, 'Caught-up clients need a transactional compact invalidation event'
    latest = invalidations[-1]
    assert latest['invalidates_automatic_assignments'] is True
    assert latest['invalidate_before_revision'] > int(segment['identity_profile_revision'])
    assert latest['generation'] == bank.bank(store, sid)['generation']
    # New clients replaying older feed entries must see the same safe projection
    # as a current GET, while original predictions stay in immutable decisions.
    historical = [row['data'] for row in all_changes(store, sid)
                  if row['entity'] == 'segments' and row['entity_id'] == str(segment['id'])
                  and row['operation'] == 'upsert']
    assert historical
    current = next(row for row in store.results(sid)['segments'] if row['id'] == segment['id'])
    for row in (historical[-1], current):
        assert row['identity_state'] == 'stale'
        assert row['speaker_id'] == human_person
        if human_person is None:
            assert row['speaker_status'] == 'identity_stale'
        else:
            assert row['speaker_status'] == 'human_corrected'


def immutable_part(decision):
    return {key: value for key, value in decision.items() if key not in ('state', 'stale_reason')}


def assert_public_json(value, root):
    forbidden = {'raw_vector', 'embedding', 'speaker_embedding', 'pcm', 'path', 'normalized_vector'}
    def walk(node):
        if isinstance(node, dict):
            assert not forbidden.intersection(node), node.keys()
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    assert str(root) not in json.dumps(value)


@pytest.mark.parametrize('identical', [False, True])
def test_concurrent_claims_and_idempotent_retries_commit_once(tmp_path, proven, identical):
    _, store, sid = configured(tmp_path)
    _, support, raw = captured(store, sid, proven)
    first = request(store, sid, support, raw, idempotency_key='racing-claim')
    second = copy.deepcopy(first)
    if not identical:
        second.update(idempotency_key='competing-claim', new_person_name='Competing Bob')
    gate = threading.Barrier(2)
    def attempt(body):
        gate.wait(timeout=10)
        try:
            return bank.mutate(store, sid, body)
        except ValueError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [first, second]))
    successes = [value for value in outcomes if isinstance(value, dict)]
    assert len(successes) == (2 if identical else 1)
    if identical:
        assert successes[0] == successes[1]
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM identity_active_reviews').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM speakers').fetchone()[0] == 1
    assert bank.bank(store, sid)['profile_revision'] == 1


def test_changed_idempotency_payload_never_changes_committed_state(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, support, raw = captured(store, sid, proven)
    body = request(store, sid, support, raw, idempotency_key='one-request')
    event = bank.mutate(store, sid, body)
    before = snapshot(store)
    with pytest.raises(ValueError, match='idempotency_conflict'):
        bank.mutate(store, sid, dict(body, new_person_name='Conflicting label'))
    assert snapshot(store) == before
    assert bank.mutate(store, sid, body) == event
    assert snapshot(store) == before


@pytest.mark.parametrize('failure', ['capacity', 'evidence', 'feed'])
def test_failed_replacement_preserves_original_bank_results_and_feed(tmp_path, proven, monkeypatch, failure):
    _, store, sid = configured(tmp_path)
    _, _, _, first = review(store, sid, proven)
    _, query, _ = recording(store, sid, proven, seed=1)
    assert query['speaker_id'] == first['person_id']
    _, new_support, new_raw = captured(store, sid, proven, seed=2)
    body = request(store, sid, new_support, new_raw, new_person_name='Replacement Bob')
    if failure == 'capacity':
        monkeypatch.setattr(bank, 'MAX_EVENTS', 2)
        expected_error = OverflowError
    elif failure == 'evidence':
        body['vector_sha256'] = '0' * 64
        expected_error = ValueError
    else:
        with store.db() as db:
            db.execute("""CREATE TRIGGER fail_bank_publication BEFORE INSERT ON result_changes
                WHEN NEW.entity='identity_bank' BEGIN SELECT RAISE(ABORT,'injected feed failure'); END""")
        expected_error = sqlite3.IntegrityError
    before = snapshot(store)
    with pytest.raises(expected_error):
        bank.mutate(store, sid, body, previous=first['event_id'])
    assert snapshot(store) == before
    assert bank.decision(store, sid, query['identity_decision_id'])['state'] == 'current'


def test_retraction_at_reserved_capacity_invalidates_without_erasing_decision(tmp_path, proven, monkeypatch):
    _, store, sid = configured(tmp_path)
    monkeypatch.setattr(bank, 'MAX_EVENTS', 2)
    _, _, _, event = review(store, sid, proven)
    _, query, _ = recording(store, sid, proven, seed=1)
    original = bank.decision(store, sid, query['identity_decision_id'])
    cursor = change_feed.changes(store, sid)['high_watermark']
    body = retract_request(store, sid)
    removed = bank.mutate(store, sid, body, previous=event['event_id'], retract=True)
    assert bank.mutate(store, sid, body, previous=event['event_id'], retract=True) == removed
    assert bank.bank(store, sid)['active_counts'] == {}
    current = bank.decision(store, sid, query['identity_decision_id'])
    assert current['state'] == 'stale'
    assert immutable_part(current) == immutable_part(original)
    assert current['original_person_id'] == event['person_id']
    assert_stale_change(store, sid, query, cursor)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0] == 2


def test_competitor_review_invalidates_gallery_decisions_but_preserves_human_labels(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, _, _, alice = review(store, sid, proven)
    query_sid = store.create_session('Separate query recording', 'verified')['id']
    _, auto, _ = recording(store, query_sid, proven, seed=1)
    _, human, _ = recording(store, query_sid, proven, seed=2)
    store.assign_segment(query_sid, human['id'], alice['person_id'])
    original = {row['id']: bank.decision(store, query_sid, row['identity_decision_id']) for row in (auto, human)}
    cursor = change_feed.changes(store, query_sid)['high_watermark']
    review(store, sid, proven, seed=3, vector=[0.0, 1.0] + [0.0] * 6, name='Reviewed Bob')
    rows = {row['id']: row for row in store.results(query_sid)['segments']}
    assert rows[auto['id']]['speaker_id'] is None
    assert rows[human['id']]['speaker_id'] == alice['person_id']
    assert rows[human['id']]['speaker_status'] == 'human_corrected'
    for row in (auto, human):
        current = bank.decision(store, query_sid, row['identity_decision_id'])
        assert current['state'] == 'stale'
        assert immutable_part(current) == immutable_part(original[row['id']])
    assert_stale_change(store, query_sid, auto, cursor)
    assert_stale_change(store, query_sid, human, cursor, human_person=alice['person_id'])
    for turn in store.speaker_turns(query_sid)['speaker_turns']:
        assert turn['identity_state'] == 'stale' and turn['speaker_id'] is None


def test_original_nonunit_vector_binding_survives_restart_and_cannot_be_substituted(tmp_path, proven):
    settings, store, sid = configured(tmp_path)
    vector = [0.6000000000000001, 0.8000000000000002] + [0.0] * 6
    _, support, raw = captured(store, sid, proven, vector=vector)
    expected = json.dumps(vector, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    assert raw['raw_vector'] == expected
    assert raw['vector_sha256'] == hashlib.sha256(expected).hexdigest()
    reloaded = Store(store.path, settings)
    with reloaded.db() as db:
        assert support_vectors.read(db, support['id'])[0]['raw_vector'] == expected
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('UPDATE speaker_support_vectors SET raw_vector=? WHERE id=?', (expected, raw['id']))
    normalized, _ = support_vectors.normalized_vector(vector)
    assert speaker_support.canonical(normalized) != expected
    forged = request(store, sid, support, raw,
                     vector_sha256=hashlib.sha256(speaker_support.canonical(normalized)).hexdigest())
    with pytest.raises(ValueError, match='binding mismatch'):
        bank.mutate(store, sid, forged)
    with store.db() as db:
        db.execute('DELETE FROM speaker_support_vectors WHERE id=?', (raw['id'],))
    with pytest.raises(ValueError, match='vector_proof_unavailable'):
        bank.mutate(store, sid, request(store, sid, support, raw))
    assert bank.bank(store, sid)['active_counts'] == {}


@pytest.mark.parametrize('different_frontend', ['high_freq', 'snip_edges'])
def test_same_model_different_proven_frontend_neither_matches_nor_enrolls(tmp_path, proven, monkeypatch, different_frontend):
    _, store, sid = configured(tmp_path)
    review(store, sid, proven)
    altered_binding = copy.deepcopy(embedding_space._AUDITED_BINDINGS[proven['binding_id']])
    if different_frontend == 'high_freq':
        altered_binding['frontend'].update(high_freq=-200.0, high_freq_hz=7800.0)
    else:
        altered_binding['frontend']['snip_edges'] = True
    binding_id = embedding_space._digest(altered_binding)
    monkeypatch.setitem(embedding_space._AUDITED_BINDINGS, binding_id, altered_binding)
    other_space = embedding_space._descriptor(binding_id, SimpleNamespace(provider='cpu', num_threads=2))
    assert other_space['model'] == proven['model']
    assert embedding_space.validate_space(other_space) != embedding_space.validate_space(proven)
    chunk, support, raw = captured(store, sid, other_space, seed=1)
    query = next(row for row in store.results(sid)['segments'] if row['chunk_id'] == chunk['id'])
    assert query['speaker_id'] is None
    decision = bank.decision(store, sid, query['identity_decision_id'])
    assert decision['status'] == 'unknown_embedding_space'
    with pytest.raises(ValueError, match='incompatible_embedding_space'):
        bank.mutate(store, sid, request(store, sid, support, raw))
    assert bank.bank(store, sid)['active_counts'] == {'positive': 1}


def test_label_actions_and_legacy_embeddings_never_create_trust(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    injected_person = ident()
    # Simulate pre-existing application identity state without a reviewed anchor.
    with store.db() as db:
        db.execute("""INSERT INTO speakers(id,session_id,identity_group,name,embedding,state,created,embedding_version)
            VALUES(?,?,?,?,?,'confirmed',0,'test-v1')""",
            (injected_person, sid, 'verified', 'Legacy label', json.dumps(VECTOR)))
    store.correct_speaker(sid, injected_person, name='Renamed label')
    _, first, _ = recording(store, sid, proven)
    assert first['speaker_id'] is None
    assert not first['enrollment_available'] and not first['profile_example_available']
    store.assign_segment(sid, first['id'], injected_person)
    for action in (
        lambda: store.enroll_speaker(sid, 'Unsafe enrollment', first['id']),
        lambda: store.add_speaker_example(sid, injected_person, first['id']),
    ):
        with pytest.raises(ValueError, match='review_required'):
            action()
    with pytest.raises(ValueError, match='verified_merge_unsupported'):
        store.correct_speaker(sid, injected_person, undo_merge=True)
    _, second, _ = recording(store, sid, proven, seed=1)
    assert second['speaker_id'] is None
    assert bank.bank(store, sid)['active_counts'] == {}
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speakers').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0] == 0


@pytest.mark.parametrize('quota', ['rows', 'bytes'])
def test_long_query_sequence_after_retention_quota_keeps_matching_without_learning(tmp_path, proven, quota):
    _, store, sid = configured(tmp_path)
    _, _, _, event = review(store, sid, proven)
    with store.db() as db:
        retained_bytes = support_vectors.retained_bytes(db)
    if quota == 'rows':
        store.settings.speaker_support_max_records = 1
    else:
        store.settings.speaker_support_total_pcm_bytes = retained_bytes
    for seed in range(1, 17):
        _, query, _ = recording(store, sid, proven, seed=seed)
        assert query['speaker_id'] == event['person_id']
        decision = bank.decision(store, sid, query['identity_decision_id'])
        assert decision['status'] == 'experimental_identity_match'
        assert decision['review_availability'] != 'available'
        assert decision['reference_event_ids'] == [event['event_id']]
        assert decision['automatic_query_is_human_verified'] is False
        assert not speaker_support.metadata(store, sid, segment_id=query['id'])['verified_review_eligible']
    with store.db() as db:
        assert support_vectors.retained_bytes(db) == retained_bytes
        assert db.execute('SELECT COUNT(*) FROM speaker_support_vectors').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0] == 1
    assert bank.bank(store, sid)['active_counts'] == {'positive': 1}
    assert bank.bank(store, sid)['profile_revision'] == 1


def test_exact_negative_veto_abstains_even_when_runner_up_would_pass(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, _, _, alice = review(store, sid, proven)
    _, _, _, bob = review(store, sid, proven, seed=1, vector=[0.8, 0.6] + [0.0] * 6, name='Bob')
    _, support, raw = captured(store, sid, proven, seed=2)
    bank.mutate(store, sid, request(store, sid, support, raw, kind='negative',
                new_person_name=None, person_id=alice['person_id']))
    _, vetoed, _ = recording(store, sid, proven, seed=2)
    veto = bank.decision(store, sid, vetoed['identity_decision_id'])
    assert vetoed['speaker_id'] is None and veto['original_person_id'] is None
    assert veto['status'] == 'unknown_exact_negative'
    assert veto['score'] == pytest.approx(1.0)
    assert veto['margin'] == pytest.approx(0.2)
    assert bob['person_id'] != vetoed['speaker_id']
    _, distinct, _ = recording(store, sid, proven, seed=3)
    assert distinct['speaker_id'] == alice['person_id'], 'Negative memory must not generalize by similarity'


@pytest.mark.parametrize('retain_query', [True, False])
def test_nonowner_query_source_deletion_invalidates_without_review_retention(tmp_path, proven, retain_query):
    _, store, sid = configured(tmp_path)
    _, _, _, event = review(store, sid, proven)
    if not retain_query:
        store.settings.speaker_support_max_records = 1
    chunk, query, dependency = recording(store, sid, proven, seed=1, extra_source=True)
    before = bank.decision(store, sid, query['identity_decision_id'])
    assert query['speaker_id'] == event['person_id']
    assert bool(before['support_id']) is retain_query
    cursor = change_feed.changes(store, sid)['high_watermark']
    with store.db() as db:
        db.execute('DELETE FROM chunks WHERE id=?', (dependency,))
    assert store.chunk(chunk['id'])['id'] == chunk['id']
    decision = bank.decision(store, sid, query['identity_decision_id'])
    assert decision['state'] == 'stale'
    assert immutable_part(decision) == immutable_part(before)
    assert bank.bank(store, sid)['active_counts'] == {'positive': 1}
    assert_stale_change(store, sid, query, cursor)


def test_nonowner_anchor_source_deletion_releases_trust_at_full_capacity(tmp_path, proven, monkeypatch):
    _, store, sid = configured(tmp_path)
    monkeypatch.setattr(bank, 'MAX_EVENTS', 2)
    chunk, source, dependency = recording(store, sid, proven, extra_source=True)
    with store.db() as db:
        support = dict(db.execute('SELECT * FROM speaker_support WHERE chunk_id=?', (chunk['id'],)).fetchone())
        raw = dict(db.execute('SELECT * FROM speaker_support_vectors WHERE support_id=?', (support['id'],)).fetchone())
    event = bank.mutate(store, sid, request(store, sid, support, raw))
    query_sid = store.create_session('Dependent prediction', 'verified')['id']
    _, query, _ = recording(store, query_sid, proven, seed=1)
    original = bank.decision(store, query_sid, query['identity_decision_id'])
    cursor = change_feed.changes(store, query_sid)['high_watermark']
    with store.db() as db:
        db.execute('DELETE FROM chunks WHERE id=?', (dependency,))
    assert bank.bank(store, sid)['active_counts'] == {}
    with store.db() as db:
        assert not db.execute('SELECT 1 FROM speaker_support_vectors WHERE id=?', (raw['id'],)).fetchone()
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0] == 2
        assert db.execute("SELECT previous_event_id FROM identity_review_events WHERE action='source_expired'").fetchone()[0] == event['event_id']
    with pytest.raises(KeyError):
        speaker_support.audio(store, sid, support['id'])
    current = bank.decision(store, query_sid, query['identity_decision_id'])
    assert current['state'] == 'stale' and immutable_part(current) == immutable_part(original)
    assert_stale_change(store, query_sid, query, cursor)


def test_source_erasure_and_feed_failure_roll_back_together(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    chunk, _, _, _ = review(store, sid, proven)
    recording(store, sid, proven, seed=1)
    with store.db() as db:
        db.execute("""CREATE TRIGGER fail_expiry_publication BEFORE INSERT ON result_changes
            WHEN NEW.entity='identity_bank' BEGIN SELECT RAISE(ABORT,'injected expiry failure'); END""")
    before = snapshot(store)
    with pytest.raises(sqlite3.IntegrityError, match='injected expiry failure'):
        with store.db() as db:
            db.execute('DELETE FROM chunks WHERE id=?', (chunk['id'],))
    assert snapshot(store) == before
    assert store.chunk(chunk['id'])['id'] == chunk['id']


def test_restart_preserves_policy_active_reviews_and_stale_decision_history(tmp_path, proven):
    settings, store, sid = configured(tmp_path)
    _, _, _, first = review(store, sid, proven)
    _, stale, _ = recording(store, sid, proven, seed=1)
    review(store, sid, proven, seed=2, person=first['person_id'])
    _, current, _ = recording(store, sid, proven, seed=3)
    expected_bank = bank.bank(store, sid)
    expected = {row['identity_decision_id']: bank.decision(store, sid, row['identity_decision_id'])
                for row in (stale, current)}
    expected_feed = all_changes(store, sid)
    reloaded = Store(store.path, settings)
    change_feed.initialize(reloaded)
    assert bank.bank(reloaded, sid) == expected_bank
    for decision_id, value in expected.items():
        assert bank.decision(reloaded, sid, decision_id) == value
    assert all_changes(reloaded, sid) == expected_feed
    _, next_query, _ = recording(reloaded, sid, proven, seed=4)
    assert next_query['speaker_id'] == first['person_id']


def test_reset_redacts_identity_history_rotates_generation_and_keeps_transcript_audio(tmp_path, proven):
    settings, store, sid = configured(tmp_path)
    chunk, support, raw, event = review(store, sid, proven, name='PRIVATE_LABEL_FOR_RESET')
    sibling = store.create_session('Same group second recording', 'verified')['id']
    _, query, _ = recording(store, sibling, proven, seed=1)
    store.assign_segment(sibling, query['id'], event['person_id'])
    old_bank = bank.bank(store, sid)
    old_request = request(store, sid, support, raw, new_person_name='PRIVATE_LABEL_FOR_RESET',
                          expected_revision=0, idempotency_key='0')
    assert bank.mutate(store, sid, old_request) == event
    with store.db() as db:
        sources = [row[0] for row in db.execute('SELECT path FROM chunks WHERE session_id=?', (sibling,))]
    cursors = {session: change_feed.changes(store, session)['high_watermark'] for session in (sid, sibling)}
    secrets = [event['person_id'], event['event_id'], support['id'], raw['id'], old_bank['generation'],
               query['identity_decision_id'], 'PRIVATE_LABEL_FOR_RESET']
    reset_group(store, 'verified')
    new_bank = bank.bank(store, sid)
    assert new_bank['identity_mode'] == bank.MODE
    assert new_bank['policy_sha256'] == old_bank['policy_sha256']
    assert new_bank['generation'] != old_bank['generation']
    assert new_bank['active_counts'] == {} and new_bank['space_id'] is None
    for session in (sid, sibling):
        current = store.results(session)
        assert current['segments'][0]['text'] == 'unchanged'
        assert all(row['speaker_id'] is None for row in current['segments'])
        historical = all_changes(store, session)
        # Opaque entity IDs remain in DELETE tombstones so clients can erase
        # prior state; none of their old identity-bearing payloads may remain.
        encoded = json.dumps([current, [row['data'] for row in historical], bank.events(store, session)])
        assert all(secret not in encoded for secret in secrets)
        updates = all_changes(store, session, cursors[session])
        resets = [row['data'] for row in updates
                  if row['entity'] == 'identity_bank' and row['operation'] == 'upsert']
        assert resets and resets[-1]['reason'] == 'identity_reset'
        assert resets[-1]['generation'] == new_bank['generation']
        assert resets[-1]['invalidate_before_revision'] > old_bank['profile_revision']
    with store.db() as db:
        for table in ('identity_review_events', 'identity_active_reviews', 'identity_decisions',
                      'identity_decision_state', 'identity_decision_sources', 'speaker_support_vectors'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        assert all(secret not in json.dumps([tuple(row) for row in db.execute('SELECT detail FROM corrections')]) for secret in secrets)
    from pathlib import Path
    assert all(Path(path).exists() for path in sources)
    with pytest.raises(KeyError):
        bank.decision(store, sibling, query['identity_decision_id'])
    with pytest.raises(ValueError, match='stale_generation'):
        bank.mutate(store, sid, old_request)
    reloaded = Store(store.path, settings)
    assert bank.bank(reloaded, sid)['generation'] == new_bank['generation']
    _, fresh, _ = recording(reloaded, sid, proven, seed=2)
    assert fresh['speaker_id'] is None
    assert store.speakers(sid) == []


def test_api_scope_auth_and_public_provenance_never_expose_credentials_or_vectors(tmp_path, proven):
    settings, _, sid = configured(tmp_path, api_key='synthetic-token-not-a-real-secret')
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        _, support, raw = captured(store, sid, proven)
        same = store.create_session('Sibling session', 'verified')['id']
        other = store.create_session('Other group', 'other', bank.MODE)['id']
        body = request(store, sid, support, raw)
        route = f'/sessions/{sid}/identity-reviews'
        assert client.post(route, json=body).status_code == 401
        assert client.get(f'/sessions/{sid}/identity-bank').status_code == 401
        client.headers['Authorization'] = f'Bearer {settings.api_key}'
        for wrong_session in (same, other):
            wrong_body = request(store, wrong_session, support, raw)
            assert client.post(f'/sessions/{wrong_session}/identity-reviews', json=wrong_body).status_code == 404
        response = client.post(route, json=body)
        assert response.status_code == 200, response.text
        event = response.json()
        assert client.post(route, json=body).json() == event
        assert client.post(route, json=dict(body, vector=VECTOR)).status_code == 422
        _, other_support, other_raw = captured(store, other, proven, seed=2)
        foreign_person = request(store, other, other_support, other_raw,
                                 new_person_name=None, person_id=event['person_id'])
        assert client.post(f'/sessions/{other}/identity-reviews', json=foreign_person).status_code == 404
        _, query, _ = recording(store, sid, proven, seed=1)
        decision_route = f'/sessions/{sid}/identity-decisions/{query["identity_decision_id"]}'
        for wrong_session in (same, other):
            assert client.get(f'/sessions/{wrong_session}/identity-decisions/{query["identity_decision_id"]}').status_code == 404
        assert client.post(f'/sessions/{other}/identity-reviews/{event["event_id"]}/retract',
                           json=retract_request(store, other)).status_code == 409
        payloads = []
        for url in (route, decision_route, f'/sessions/{sid}/identity-bank'):
            response = client.get(url)
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'private, no-store'
            payloads.append(response.json())
        payloads.extend([store.results(sid), store.speaker_turns(sid), all_changes(store, sid)])
        assert_public_json(payloads, tmp_path)
        assert settings.api_key not in json.dumps(payloads)
        with store.db() as db:
            assert settings.api_key not in '\n'.join(db.iterdump())
            actor = db.execute('SELECT operator_context FROM identity_review_events').fetchone()[0]
            assert actor and actor != settings.api_key and 'Bearer ' not in actor


def test_deleted_source_session_invalidates_survivor_and_keeps_verified_mode(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, support, raw, _ = review(store, sid, proven)
    survivor = store.create_session('Surviving session', 'verified')['id']
    _, query, _ = recording(store, survivor, proven, seed=1)
    cursor = change_feed.changes(store, survivor)['high_watermark']
    store.delete_session(sid)
    assert bank.bank(store, survivor)['identity_mode'] == bank.MODE
    assert bank.bank(store, survivor)['active_counts'] == {}
    assert bank.decision(store, survivor, query['identity_decision_id'])['state'] == 'stale'
    assert_stale_change(store, survivor, query, cursor)
    with store.db() as db:
        assert not db.execute('SELECT 1 FROM speaker_support_vectors WHERE id=?', (raw['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM result_changes WHERE session_id=?', (sid,)).fetchone()
    with pytest.raises(KeyError):
        speaker_support.audio(store, sid, support['id'])
    _, unknown, _ = recording(store, survivor, proven, seed=2)
    assert unknown['speaker_id'] is None


def test_old_generation_transient_query_cannot_match_after_reset_at_quota(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, pending, result, _, _, _, old_generation = prepare(store, sid=sid)
    result['metadata'].update(speaker_embedding_space=proven,
                             speaker_embedding_space_id=embedding_space.validate_space(proven))
    assert result['_speaker_support']['A']['generation'] == old_generation
    store.set_state(pending['id'], 'pause')
    reset_group(store, 'verified')
    _, _, _, new_anchor = review(store, sid, proven, seed=1)
    store.settings.speaker_support_max_records = 1
    assert bank.bank(store, sid)['generation'] != old_generation
    assert store.commit_window(pending, 0, pending['duration'], result)
    query = next(row for row in store.results(sid)['segments'] if row['chunk_id'] == pending['id'])
    decision = bank.decision(store, sid, query['identity_decision_id'])
    assert query['speaker_id'] is None and decision['original_person_id'] is None
    assert decision['status'] == 'unknown_query_proof'
    assert decision['reference_event_ids'] == []
    assert decision['review_availability'] != 'available'
    assert bank.bank(store, sid)['active_counts'] == {'positive': 1}
    assert store.speakers(sid)[0]['id'] == new_anchor['person_id']


def test_verified_overlap_conditioning_cannot_copy_untracked_person_assignments(tmp_path, proven):
    from test_overlap_store import payload
    _, store, sid = configured(tmp_path)
    _, _, _, event = review(store, sid, proven)
    _, chunk, result, _, _, _, _ = prepare(store, sid=sid, seed=1)
    result['metadata'].update(speaker_embedding_space=proven,
                             speaker_embedding_space_id=embedding_space.validate_space(proven))
    candidates = payload()
    candidates['regions'][0]['detected_speaker_local_ids'] = ['A', 'B']
    candidates['regions'][0]['conditioning_evidence'] = [
        {'conditioning_local_id': 'A', 'reason_code': 'fixture', 'enrollment_seconds': 3.0}]
    candidates['tracks'][0]['conditioning_local_id'] = 'A'
    candidates['tracks'][0]['provenance']['conditioning_local_id'] = 'A'
    result['overlap_candidates'] = candidates
    assert store.commit_window(chunk, 0, chunk['duration'], result)
    query = next(row for row in store.results(sid)['segments'] if row['chunk_id'] == chunk['id'])
    assert query['speaker_id'] == event['person_id']
    for phase in ('current', 'stale'):
        if phase == 'stale':
            bank.mutate(store, sid, retract_request(store, sid), previous=event['event_id'], retract=True)
        overlaps = store.overlap_candidates(sid)
        assert overlaps['tracks'] and overlaps['regions']
        for track in overlaps['tracks']:
            assert track['speaker_id'] is None and track['conditioned_on_speaker_id'] is None
        assert event['person_id'] not in json.dumps(overlaps)
        overlap_feed = [row for row in all_changes(store, sid)
                        if row['entity'] in ('overlap_regions', 'overlap_tracks')]
        assert overlap_feed
        assert event['person_id'] not in json.dumps(overlap_feed)


def test_large_history_bank_mutation_emits_bounded_compact_invalidation(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, _, _, alice = review(store, sid, proven)
    _, query, _ = recording(store, sid, proven, seed=1)
    # Many transcript rows can share one local-voice decision. Grow only a
    # synthetic scratch history, never the frozen evaluation corpus.
    with store.db() as db:
        for _ in range(2048):
            db.execute('''INSERT INTO segments(chunk_id,start,end,text,speaker_id,speaker_local_id,
                speaker_match_score,speaker_status,speaker_window_start,identity_decision_id,
                identity_profile_revision,identity_state)
                SELECT chunk_id,start,end,text,speaker_id,speaker_local_id,speaker_match_score,
                speaker_status,speaker_window_start,identity_decision_id,identity_profile_revision,
                identity_state FROM segments WHERE id=?''', (query['id'],))
        history_count = db.execute('SELECT COUNT(*) FROM segments').fetchone()[0]
        # The contract is bounded invalidation, not eager rewriting of every
        # historical result. This abort would catch a hidden per-row rewrite
        # even if its feed trigger were temporarily disabled.
        db.execute('''CREATE TRIGGER disallow_eager_history_invalidation BEFORE UPDATE ON segments
            WHEN OLD.identity_decision_id IS NOT NULL AND NEW.identity_state IS NOT OLD.identity_state
            BEGIN SELECT RAISE(ABORT,'bank mutation rewrote result history'); END''')
    assert history_count > 2048
    cursor = change_feed.changes(store, sid)['high_watermark']
    bank.mutate(store, sid, retract_request(store, sid), previous=alice['event_id'], retract=True)
    delta = all_changes(store, sid, cursor)
    assert len(delta) <= 3
    assert not any(row['entity'] in ('segments', 'speaker_turns') for row in delta)
    assert_stale_change(store, sid, query, cursor)
    for row in store.results(sid, limit=500)['segments']:
        assert row['identity_state'] == 'stale' and row['speaker_id'] is None


def test_failed_reset_rolls_back_generation_privacy_rewrites_and_feed_suppression(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    review(store, sid, proven)
    _, query, _ = recording(store, sid, proven, seed=1)
    old_bank = bank.bank(store, sid)
    with store.db() as db:
        db.execute("""CREATE TRIGGER fail_reset_publication BEFORE INSERT ON result_changes
            WHEN NEW.entity='identity_bank' BEGIN SELECT RAISE(ABORT,'injected reset failure'); END""")
    before = snapshot(store)
    with pytest.raises(sqlite3.IntegrityError, match='injected reset failure'):
        reset_group(store, 'verified')
    assert snapshot(store) == before
    assert bank.bank(store, sid) == old_bank
    assert bank.decision(store, sid, query['identity_decision_id'])['state'] == 'current'
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_feed_suppression').fetchone()[0] == 0
