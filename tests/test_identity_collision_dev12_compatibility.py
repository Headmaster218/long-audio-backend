"""Model-free dev12 integration; synthetic identities are not acoustic truth."""
from copy import deepcopy
from itertools import permutations
import pytest

from audio_backend import native_owned_core_seams as core
from audio_backend import verified_identity as bank
from audio_backend.storage import Store
from test_reviewable_support import setup
from test_verified_identity_bank import configured, proven, request
from test_verified_identity_matching import enroll
from test_verified_identity_overlap_guard import collision_window, draft, turn


@pytest.mark.parametrize('start,end', [
    (None, 2), (0, None), ('0', 2), (0, '2'), (False, 2), (0, True),
    (float('nan'), 2), (0, float('nan')), (float('-inf'), 2),
    (0, float('inf')), (1, 1), (2, 1),
])
def test_invalid_intervals_never_establish_collision(start, end):
    drafts = {'A': draft('person'), 'B': draft('person')}
    before = deepcopy(drafts)
    bank.exclude_overlap_collisions(drafts, [turn('A', 0, 3), turn('B', start, end)])
    assert drafts == before


def test_all_turn_and_draft_orders_have_identical_conflict_graph():
    spans = [turn('A', 0, 2), turn('B', 1, 3), turn('C', 2.5, 4)]
    expected = None
    for order in permutations('ABC'):
        for turns in permutations(spans):
            drafts = {key: draft('person') for key in order}
            bank.exclude_overlap_collisions(drafts, turns)
            if expected is None:
                expected = drafts
            assert drafts == expected
            assert all(row['status'] == 'unknown_overlap_identity_conflict'
                       for row in drafts.values())


# Invented multi-fragment geometry only; labels and identities are synthetic.
@pytest.mark.parametrize('spans', [
    [('A', 1., 3.), ('A', 5., 6.), ('B', 7., 8.),
     ('B', 9., 12.), ('A', 11., 13.), ('B', 12.5, 15.), ('A', 14., 15.)],
    [('B', 0., 2.), ('A', 0., .5), ('A', 1., 1.5),
     ('B', 3., 8.), ('A', 5., 6.), ('A', 8.5, 9.5), ('B', 9., 10.)],
])
def test_synthetic_multifragment_collision_and_distinct_person_control(spans):
    turns = [turn(*span) for span in spans]
    before = deepcopy(turns)
    for ordered in (turns, list(reversed(turns))):
        shared = {key: draft('synthetic-person') for key in 'AB'}
        distinct = {key: draft('synthetic-person-' + key) for key in 'AB'}
        distinct_before = deepcopy(distinct)
        bank.exclude_overlap_collisions(shared, ordered)
        bank.exclude_overlap_collisions(distinct, ordered)
        assert all(row['original_person_id'] is None for row in shared.values())
        assert all(row['status'] == 'unknown_overlap_identity_conflict' for row in shared.values())
        assert distinct == distinct_before
        assert turns == before


def rows_for(store, chunk, table='segments'):
    with store.db() as db:
        return [dict(row) for row in db.execute(
            f'SELECT * FROM {table} WHERE chunk_id=? ORDER BY speaker_local_id',
            (chunk['id'],))]


def assert_collision(store, sid, chunk):
    for row in rows_for(store, chunk) + rows_for(store, chunk, 'speaker_turns'):
        assert row['speaker_id'] is None
        assert row['speaker_status'] == 'unknown_overlap_identity_conflict'
        decision = bank.decision(store, sid, row['identity_decision_id'])
        assert decision['original_person_id'] is None
        assert decision['status'] == row['speaker_status']


def test_legacy_still_assigns_independently_and_never_calls_reviewed_guard(tmp_path, proven, monkeypatch):
    _, store = setup(tmp_path)
    sid = store.create_session('legacy collision fixture')['id']
    def forbidden(*args, **kwargs):
        raise AssertionError('Reviewed guard must not execute in legacy mode')
    monkeypatch.setattr(bank, 'prepare_decision', forbidden)
    monkeypatch.setattr(bank, 'exclude_overlap_collisions', forbidden)
    chunk, result = collision_window(store, sid, proven)
    assert store.commit_window(chunk, 0, 7, result)
    rows = rows_for(store, chunk, 'speaker_turns')
    assert len(rows) == 2 and rows[0]['speaker_id'] is not None
    assert rows[0]['speaker_id'] == rows[1]['speaker_id']
    assert all(not row['speaker_status'].startswith('unknown_overlap_identity') for row in rows)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_decisions').fetchone()[0] == 0


def test_exact_negative_endpoint_keeps_veto_and_does_not_suppress_other(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    person, *_ = enroll(store, sid, proven)
    runner_up, *_ = enroll(store, sid, proven, seed=1,
        vector=[.8, .6] + [0.] * 6, new_person_name='Runner up')
    first, result = collision_window(store, sid, proven)
    assert store.commit_window(first, 0, 7, result)
    with store.db() as db:
        support = dict(db.execute('SELECT * FROM speaker_support WHERE chunk_id=? AND local_speaker_id=?',
                                 (first['id'], 'A')).fetchone())
        raw = dict(db.execute('SELECT * FROM speaker_support_vectors WHERE support_id=?',
                             (support['id'],)).fetchone())
    negative = bank.mutate(store, sid, request(store, sid, support, raw,
        kind='negative', new_person_name=None, person_id=person['person_id']))
    chunk, result = collision_window(store, sid, proven)
    assert store.commit_window(chunk, 0, 7, result)
    a, b = rows_for(store, chunk)
    assert a['speaker_id'] is None and a['speaker_status'] == 'unknown_exact_negative'
    assert b['speaker_id'] == person['person_id'] != runner_up['person_id']
    assert b['speaker_status'] == 'experimental_identity_match'
    decision = bank.decision(store, sid, a['identity_decision_id'])
    assert decision['negative_event_id'] == negative['event_id']
    assert decision['candidate_person_id'] == person['person_id']
    assert 'overlap_conflicting_local_ids' not in decision


def test_retracted_reference_cannot_create_collision_or_new_person(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    event, *_ = enroll(store, sid, proven)
    view = bank.bank(store, sid)
    bank.mutate(store, sid, dict(expected_generation=view['generation'],
        expected_revision=view['profile_revision'], idempotency_key='retract-fixture'),
        previous=event['event_id'], retract=True)
    chunk, result = collision_window(store, sid, proven)
    assert store.commit_window(chunk, 0, 7, result)
    for row in rows_for(store, chunk):
        assert row['speaker_id'] is None
        assert row['speaker_status'] == 'unknown_no_reviewed_references'
        decision = bank.decision(store, sid, row['identity_decision_id'])
        assert decision['reference_event_ids'] == []
        assert 'overlap_conflicting_local_ids' not in decision
    assert bank.bank(store, sid)['active_counts'] == {}
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM speakers').fetchone()[0] == 1


def immutable_bank(store):
    tables = ('identity_group_policy', 'identity_review_events', 'identity_active_reviews',
              'identity_relation_events', 'speakers', 'speaker_examples')
    with store.db() as db:
        return {table: sorted(map(tuple, db.execute(f'SELECT * FROM {table}')), key=repr)
                for table in tables}


def core_packet(chunk, result):
    """A compact receipt fixture, not native qualification or publication proof."""
    frames = round(chunk['duration'] * core.RATE)
    native = {'fixture': 'synthetic-current-response'}
    cut = {'nominal': chunk['duration'], 'actual': chunk['duration'], 'reason': 'fixed_or_final'}
    result['metadata'].update(native_diarization=native, core_cut=cut)
    evidence = {'chunk_id': chunk['id'], 'start': 0, 'end': frames,
                'native': native, 'core_cut': cut}
    commit = {'scope': {'session_id': chunk['session_id'], 'kind': 'upload',
        'key': chunk['id'], 'origin': float(chunk['timeline_start']).hex(),
        'run': '1' * 32, 'reset': 1, 'epoch': '2' * 32, 'index': 0},
        'part': {'session_id': chunk['session_id'], 'chunk_id': chunk['id'],
            'receipt_sha256': '3' * 64, 'source_sha256': chunk['digest'],
            'source_frames': frames, 'source_start': 0, 'source_end': frames,
            'logical_start': 0, 'logical_end': frames},
        'native_sha256': core.sha(native), 'cut_sha256': core.sha(cut),
        'evidence_sha256': core.sha(evidence), 'runtime_sha256': '4' * 64}
    core.validate_commit(commit)
    result['_native_core_seam'] = {'commit': commit, 'proof': None, 'reason': 'no_prior_core'}
    return commit


def configured_core(tmp_path, proven):
    settings, store, sid = configured(tmp_path)
    settings.native_owned_core_seams_enabled = True
    store = Store(store.path, settings)
    enroll(store, sid, proven)
    chunk, result = collision_window(store, sid, proven)
    commit = core_packet(chunk, result)
    return settings, store, sid, chunk, result, commit


def test_collision_and_dev12_core_receipt_commit_together_restart_and_retry(tmp_path, proven):
    settings, store, sid, chunk, result, commit = configured_core(tmp_path, proven)
    before = immutable_bank(store)
    with store.db() as db:
        schema = list(map(tuple, db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'native_core_%' ORDER BY name")))
    assert store.commit_window(chunk, 0, 7, result)
    assert_collision(store, sid, chunk)
    for table, key in (('segments', 'segments'), ('speaker_turns', 'speaker_turns')):
        rows = rows_for(store, chunk, table)
        expected = sorted(result[key], key=lambda row: row['speaker_local_id'])
        assert [(r['speaker_local_id'], r['start'], r['end']) for r in rows] == [
            (r['speaker_local_id'], chunk['timeline_start'] + r['start'],
             chunk['timeline_start'] + r['end']) for r in expected]
        if table == 'segments':
            assert [r['text'] for r in rows] == [r['text'] for r in expected]
    assert immutable_bank(store) == before
    reloaded = Store(store.path, settings)
    assert not reloaded.commit_window(chunk, 0, 7, result)
    assert_collision(reloaded, sid, chunk)
    with reloaded.db() as db:
        rows = list(db.execute('SELECT * FROM native_core_commits'))
        assert len(rows) == 1
        assert rows[0]['body'] == core.canonical(commit)
        assert rows[0]['state'] == 'none' and rows[0]['pending'] is None
        assert schema == list(map(tuple, db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'native_core_%' ORDER BY name")))
        assert db.execute('SELECT COUNT(*) FROM identity_decisions WHERE chunk_id=?',
                          (chunk['id'],)).fetchone()[0] == 2


@pytest.mark.parametrize('failure,reason,receipts', [
    ('capacity', 'core_receipt_row_capacity', 0),
    ('proof_size', 'core_pending_payload_capacity', 1),
    ('binding', 'core_commit_transaction_binding', 0),
])
def test_optional_core_rejection_preserves_identity_abstention_and_transcript(
        tmp_path, proven, monkeypatch, failure, reason, receipts):
    _, store, sid, chunk, result, _ = configured_core(tmp_path, proven)
    if failure == 'capacity':
        monkeypatch.setattr(core, 'MAX_COMPACTS', 0)
    elif failure == 'proof_size':
        result['_native_core_seam']['proof'] = b'x' * (core.PENDING_BYTES + 1)
    else:
        result['_native_core_seam']['commit']['native_sha256'] = '5' * 64
    assert store.commit_window(chunk, 0, 7, result)
    assert result['_native_core_seam_status'] == reason
    assert_collision(store, sid, chunk)
    assert [row['text'] for row in rows_for(store, chunk)] == ['a', 'b']
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0] == receipts
        assert db.execute('SELECT COUNT(*) FROM native_core_commits WHERE pending IS NOT NULL').fetchone()[0] == 0
        assert db.execute('SELECT next_offset FROM chunks WHERE id=?', (chunk['id'],)).fetchone()[0] == 7


def test_failure_after_core_receipt_rolls_back_identity_support_and_receipt_then_retries(
        tmp_path, proven, monkeypatch):
    _, store, sid, chunk, result, _ = configured_core(tmp_path, proven)
    original = core.transaction
    def fail_after_insert(*args, **kwargs):
        assert original(*args, **kwargs) == 'no_prior_core'
        raise RuntimeError('injected after optional receipt')
    monkeypatch.setattr(core, 'transaction', fail_after_insert)
    with pytest.raises(RuntimeError, match='injected after optional receipt'):
        store.commit_window(chunk, 0, 7, result)
    with store.db() as db:
        for table in ('identity_decisions', 'segments', 'speaker_turns', 'speaker_support'):
            assert db.execute(f'SELECT COUNT(*) FROM {table} WHERE chunk_id=?',
                              (chunk['id'],)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0] == 0
        assert db.execute('SELECT next_offset FROM chunks WHERE id=?', (chunk['id'],)).fetchone()[0] == 0
    monkeypatch.setattr(core, 'transaction', original)
    assert store.commit_window(chunk, 0, 7, result)
    assert_collision(store, sid, chunk)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0] == 1
