"""Independent model-free review tests for immutable B1 commit 84c4bbb."""
import copy
import json
from pathlib import Path

import pytest

from audio_backend import speaker_support, support_vectors, verified_identity as bank
from audio_backend.identity_reset import reset_group
from test_verified_identity_bank import configured, captured, request, proven


def test_oversized_policy_is_rejected_before_unbounded_read(tmp_path, monkeypatch):
    path = tmp_path / 'oversized-policy.json'
    path.write_bytes(b' ' * 8193)
    original = Path.read_bytes
    def forbid_unbounded(target):
        if target == path:
            pytest.fail('Policy size limit must be checked before unbounded read_bytes')
        return original(target)
    monkeypatch.setattr(Path, 'read_bytes', forbid_unbounded)
    with pytest.raises(ValueError):
        bank.policy_file(path)


def test_source_expiry_uses_reserved_capacity_for_all_active_claims(tmp_path, proven, monkeypatch):
    _, store, sid = configured(tmp_path)
    _, support_a, raw_a = captured(store, sid, proven, seed=0)
    positive_a = bank.mutate(store, sid, request(store, sid, support_a, raw_a))
    _, support_b, raw_b = captured(store, sid, proven, seed=1)
    positive_b = bank.mutate(store, sid, request(store, sid, support_b, raw_b))
    bank.mutate(store, sid, request(store, sid, support_a, raw_a,
        kind='negative', new_person_name=None, person_id=positive_b['person_id']))
    monkeypatch.setattr(bank, 'MAX_EVENTS', 6)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM identity_active_reviews').fetchone()[0] == 3
        db.execute('DELETE FROM speaker_support WHERE id=?', (support_a['id'],))
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0] == 5
        assert db.execute('SELECT COUNT(*) FROM identity_active_reviews').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM speaker_support_vectors').fetchone()[0] == 1
    view = bank.bank(store, sid)
    bank.mutate(store, sid, {'expected_generation': view['generation'],
        'expected_revision': view['profile_revision'], 'idempotency_key': 'last-retraction'},
        previous=positive_b['event_id'], retract=True)
    assert bank.bank(store, sid)['active_counts'] == {}
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM identity_review_events').fetchone()[0] == 6


def test_cross_session_source_deletion_retains_unrelated_group_references(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, support_a, raw_a = captured(store, sid, proven, seed=0)
    first = bank.mutate(store, sid, request(store, sid, support_a, raw_a))
    other = store.create_session('other recording', 'verified')['id']
    _, support_b, raw_b = captured(store, other, proven, seed=1)
    bank.mutate(store, other, request(store, other, support_b, raw_b,
        new_person_name=None, person_id=first['person_id']))
    store.delete_session(sid)
    assert bank.bank(store, other)['active_counts'] == {'positive': 1}
    with store.db() as db:
        assert db.execute('SELECT support_id FROM identity_active_reviews').fetchone()[0] == support_b['id']
        assert db.execute('SELECT COUNT(*) FROM speaker_support_vectors').fetchone()[0] == 1


def test_reset_removes_raw_vectors_and_old_idempotency_cannot_restore(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, support, raw = captured(store, sid, proven)
    req = request(store, sid, support, raw)
    bank.mutate(store, sid, req)
    reset_group(store, 'verified')
    with store.db() as db:
        for table in ('speaker_support_vectors', 'identity_active_reviews', 'identity_review_events'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    with pytest.raises(ValueError, match='stale_generation'):
        bank.mutate(store, sid, req)


def test_corrupted_exact_evidence_key_is_not_trusted_for_new_review(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, support, raw = captured(store, sid, proven)
    with store.db() as db:
        db.execute('DROP TRIGGER support_vectors_immutable')
        db.execute('UPDATE speaker_support_vectors SET evidence_key=? WHERE id=?', ('0' * 64, raw['id']))
    # Caller-visible bindings are unchanged; consumption must recompute the
    # exact evidence key from the support PCM identity and proven space.
    with pytest.raises(ValueError):
        bank.mutate(store, sid, request(store, sid, support, raw))


def test_corrupted_vector_group_cannot_be_enrolled_under_another_group(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, support, raw = captured(store, sid, proven)
    with store.db() as db:
        db.execute('DROP TRIGGER support_vectors_immutable')
        db.execute('UPDATE speaker_support_vectors SET identity_group=? WHERE id=?', ('other', raw['id']))
    with pytest.raises(ValueError):
        bank.mutate(store, sid, request(store, sid, support, raw))


def test_review_rejects_pcm_length_inconsistent_with_coherent_manifest(tmp_path, proven):
    _, store, sid = configured(tmp_path)
    _, support, raw = captured(store, sid, proven)
    manifest = json.loads(support['manifest'])
    # Keep all 3 seconds of stored PCM, but claim a complete 2-second selection.
    selected = manifest['support']
    selected['intervals_samples'] = [[0, 32000]]
    selected['pooled_sample_count'] = 32000
    selected = speaker_support.project_review_support(selected,
        core_start_seconds=0, analyzer_chunk_start_seconds=0)
    manifest['support'] = selected
    manifest['source_parts'][0]['selected_decoded_frames'] = [[0, 32000]]
    encoded = speaker_support.canonical(manifest)
    digest = speaker_support.digest(encoded)
    with store.db() as db:
        db.execute('DROP TRIGGER speaker_support_immutable')
        db.execute('UPDATE speaker_support SET manifest=?,manifest_sha256=? WHERE id=?',
                   (encoded.decode(), digest, support['id']))
    claimed = dict(support, manifest_sha256=digest)
    with pytest.raises(ValueError):
        bank.mutate(store, sid, request(store, sid, claimed, raw))
