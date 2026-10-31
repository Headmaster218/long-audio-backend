"""Synthetic identity-erasure contract; no inference or production data."""
import json

import pytest
from fastapi.testclient import TestClient

from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.storage import ident
from test_overlap_public_contract import candidate_payload


def seed(store, group, label, monkeypatch, *, sid=None):
    sid = sid or store.create_session('retained recording', group)['id']
    pid = ident()
    with store.db() as db:
        db.execute('''INSERT INTO speakers(id,session_id,identity_group,name,embedding,state,created)
            VALUES(?,?,?,?,?,?,?)''', (pid, sid, group, label, json.dumps([1.] * 8), 'confirmed', 0.))
    monkeypatch.setattr(store, '_window_identity',
        lambda db, chunk, offset, metadata, group, local_id, embedding:
        (pid, .8, 'matched', None) if local_id == 'A' else (None, None, 'unknown', None))
    source = store.settings.data_dir / 'uploads' / (ident() + '.audio')
    source.write_bytes(b'retained original acoustic source')
    cid, _ = store.add_chunk(sid, ident(), ident(), source, 2)
    chunk = store.claim()
    candidates = candidate_payload()
    candidates['regions'][1]['conditioning_evidence'] = [
        {'conditioning_local_id': 'A', 'enrollment_seconds': 2.}]
    assert store.commit_window(chunk, 0, 2, {
        'segments': [{'start': 0., 'end': 2., 'text': 'retained words', 'speaker_local_id': 'A'}],
        'events': [], 'speaker_turns': [
            {'start': 0., 'end': 2., 'speaker_local_id': 'A'},
            {'start': 0., 'end': 2., 'speaker_local_id': 'B'}],
        'overlap_candidates': candidates, 'metadata': {}})
    store.correct_speaker(sid, pid, name=label)
    return sid, pid, source


def snapshot(client, sid):
    return {name: client.get(f'/sessions/{sid}/{name}').json() for name in (
        'speakers', 'segments', 'speaker-turns', 'overlap-candidates', 'changes')}


def test_reset_anonymizes_current_history_and_shared_group_after_restart(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        sid, pid, source = seed(store, 'family', 'Private Alice', monkeypatch)
        other_sid, other_pid, other_source = seed(store, 'family', 'Private Bob', monkeypatch)
        unrelated, _, _ = seed(store, 'separate', 'Keep Carol', monkeypatch)
        before = {s: snapshot(client, s) for s in (sid, other_sid, unrelated)}
        cursors = {s: before[s]['changes']['next_cursor'] for s in (sid, other_sid)}
        assert client.delete('/identity-groups/family/speakers').status_code == 200
        after = {s: snapshot(client, s) for s in (sid, other_sid)}
        assert snapshot(client, unrelated) == before[unrelated]
        for s, forgotten in ((sid, pid), (other_sid, other_pid)):
            current = after[s]
            assert current['speakers']['speakers'] == []
            assert current['segments']['segments'][0]['speaker_id'] is None
            assert current['segments']['segments'][0]['text'] == 'retained words'
            assert all(t['speaker_id'] is None for t in current['speaker-turns']['speaker_turns'])
            assert all(t['overlap'] for t in current['speaker-turns']['speaker_turns'])
            candidates = current['overlap-candidates']
            track = candidates['tracks'][0]
            old_track = before[s]['overlap-candidates']['tracks'][0]
            assert track['conditioned_on_speaker_id'] is None
            assert track['conditioning_evidence']['conditioned_on_speaker_id'] is None
            assert track['conditioning_evidence']['raw_diarization_speaker_status'] == 'identity_reset'
            assert track['conditioning_evidence']['speaker_turn_ids'] == old_track['conditioning_evidence']['speaker_turn_ids']
            assert track['conditioning_evidence']['conditioning_local_id'] == 'A'
            assert track['text'] == old_track['text'] and track['provenance'] == old_track['provenance']
            hint = candidates['regions'][1]['conditioning_evidence'][0]
            assert hint['conditioned_on_speaker_id'] is None and hint['conditioning_local_id'] == 'A'
            assert 'Private Alice' not in json.dumps(current) and 'Private Bob' not in json.dumps(current)
            feed = current['changes']['changes']
            # Historical speaker entity IDs remain solely to support deletion replay.
            assert all(row['operation'] == 'delete' and row['data'] is None
                       for row in feed if row['entity'] == 'speakers')
            assert all(forgotten not in json.dumps(row['data']) for row in feed)
            assert [r['cursor'] for r in feed[:len(before[s]['changes']['changes'])]] == [
                r['cursor'] for r in before[s]['changes']['changes']]
            new = client.get(f'/sessions/{s}/changes', params={'after': cursors[s]}).json()['changes']
            assert any(row['entity'] == 'speakers' and row['entity_id'] == forgotten
                       and row['operation'] == 'delete' for row in new)
            assert [r['data'] for r in new if r['entity'] == 'overlap_tracks'] == [candidates['tracks'][0]]
            assert [r['data'] for r in new if r['entity'] == 'overlap_regions'] == [candidates['regions'][1]]
        assert source.exists() and other_source.exists()
        with store.db() as db:
            details = ' '.join(r[0] for r in db.execute('SELECT detail FROM corrections'))
            assert 'Private Alice' not in details and 'Private Bob' not in details
            assert 'Keep Carol' in details
        # Repeating erasure is idempotent, including feed sequence.
        assert client.delete('/identity-groups/family/speakers').status_code == 200
        assert {s: snapshot(client, s) for s in (sid, other_sid)} == after
    with TestClient(create_app(settings)) as client:
        assert {s: snapshot(client, s) for s in (sid, other_sid)} == after


def test_reset_transaction_rolls_back_if_candidate_redaction_fails(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        sid, _, source = seed(store, 'family', 'Keep Until Success', monkeypatch)
        before = snapshot(client, sid)
        with store.db() as db:
            db.execute("CREATE TRIGGER fail_reset BEFORE UPDATE ON overlap_tracks BEGIN SELECT RAISE(ABORT,'injected reset failure'); END")
        with pytest.raises(Exception, match='injected reset failure'):
            client.delete('/identity-groups/family/speakers')
        assert snapshot(client, sid) == before and source.exists()


def test_reset_repairs_old_missing_speaker_hints_and_labels(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        sid, _, _ = seed(store, 'legacy', 'Forgotten By Old Version', monkeypatch)
        # Simulate the old route: live speakers gone, stale candidate/history remains.
        with store.db() as db:
            db.execute("DELETE FROM speakers WHERE identity_group='legacy'")
        assert client.delete('/identity-groups/legacy/speakers').status_code == 200
        result = snapshot(client, sid)
        assert result['overlap-candidates']['tracks'][0]['conditioned_on_speaker_id'] is None
        assert 'Forgotten By Old Version' not in json.dumps(result)


def test_reset_rejects_running_work_without_mutation(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        sid, _, _ = seed(store, 'family', 'Not Yet Forgotten', monkeypatch)
        store.add_chunk(sid, ident(), ident(), tmp_path / 'queued.audio', 1)
        store.claim()
        before = snapshot(client, sid)
        assert client.delete('/identity-groups/family/speakers').status_code == 409
        assert snapshot(client, sid) == before
