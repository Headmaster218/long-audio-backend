"""Focused, finite v2 bridge contracts; execution requires a separate review gate.

No external fixtures, real audio, model, download, worker, server, or child
process. Each node owns one fresh result DB, at most six source recordings and
observations, four people, twelve manual/relation events, and two threads only
in the explicit CAS race. Capacity tests lower logical constants rather than
constructing a maximum-size gallery. The companion old-startup node is the sole
separately gated child-process exception.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import uuid

import pytest

from audio_backend import identity_canonical, lifecycle, speaker_support
from audio_backend import recording_primary_provenance as provenance
from audio_backend import recording_speaker_history as history
from audio_backend import reviewed_primary_persons as bridge
from audio_backend import support_vectors, verified_identity
from audio_backend.identity_reset import reset_group
from audio_backend.settings import Settings, primary_only_enabled, coordinator_enabled
from audio_backend.storage import Store
from reviewed_person_bridge_fixtures import (
    CURRENT, GROUP, activation_request, assert_refused, assert_source_free_receipt,
    count, env, immutable_snapshot, rows,
)


def test_defaults_do_not_enable_models_or_change_fragment_identity_guard(tmp_path):
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    assert settings.reviewed_primary_persons_enabled is False
    assert settings.speaker_support_enabled is False
    assert settings.recording_speaker_links_enabled is False
    assert not primary_only_enabled(settings) and not coordinator_enabled(settings)
    store = Store(tmp_path / "plain.sqlite3", settings)
    with store.db() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (CURRENT,)).fetchone() is None
        frozen = db.execute("SELECT sql FROM sqlite_master WHERE name=?",
                            (history.CERTIFICATES + "_erase_only",)).fetchone()[0]
    assert frozen == history.DDL[history.CERTIFICATES + "_erase_only"]
    # The normal creation path still cannot create a model-free acoustic bank.
    with pytest.raises(ValueError):
        store.create_session("ordinary", "ordinary", verified_identity.MODE)


def test_explicit_model_off_bank_keeps_source_session_unchanged(env):
    item = env.observe()
    before = env.store.session(item.sid)
    created = env.activate()
    state = bridge.bank(env.store, GROUP)
    assert created["generation"] == state["generation"]
    assert state["automatic_enrollment"] is False
    assert state["counts"]["persons"] == state["counts"]["active_associations"] == 0
    assert state["counts"]["active_positives"] == state["counts"]["active_negatives"] == 0
    assert env.store.session(item.sid) == before
    assert env.settings.start_worker is False and env.settings.speaker_support_enabled is False
    assert not primary_only_enabled(env.settings) and not coordinator_enabled(env.settings)
    assert count(env.store, "speaker_support") == count(env.store, "identity_active_reviews") == 0


def test_group_identifier_preserves_existing_dot_and_hundred_character_contract(env):
    group = "bank." + "a" * 95
    assert len(group) == 100
    created = env.activate(group)
    assert bridge.bank(env.store, group)["generation"] == created["generation"]
    before = immutable_snapshot(env.store)
    assert_refused(lambda: env.activate(group + "b"))
    assert immutable_snapshot(env.store) == before


@pytest.mark.parametrize("case", ["missing-policy", "wrong-digest", "legacy-collision", "unknown-field"])
def test_bank_initialization_refuses_invalid_inputs_without_partial_state(env, case):
    body = activation_request()
    if case == "missing-policy":
        env.settings.verified_identity_policy = None
    elif case == "wrong-digest":
        body["policy_sha256"] = "0" * 64
    elif case == "legacy-collision":
        env.store.create_session("legacy", GROUP)
    else:
        body["vector"] = [1., 0.]
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.activate_bank(env.store, GROUP, body))
    assert immutable_snapshot(env.store) == before


def test_activation_lost_ack_is_exact_and_reset_invalidates_old_generation(env):
    body = activation_request()
    first = bridge.activate_bank(env.store, GROUP, body)
    assert bridge.activate_bank(env.store, GROUP, body) == first
    assert count(env.store, "identity_review_events") == 1
    changed = dict(body, policy_sha256="0" * 64)
    assert_refused(lambda: bridge.activate_bank(env.store, GROUP, changed), status=409)
    reset_group(env.store, GROUP)
    assert bridge.bank(env.store, GROUP)["generation"] != first["generation"]
    assert_refused(lambda: bridge.activate_bank(env.store, GROUP, body), status=409)
    assert count(env.store, "identity_review_events") == 0


def test_zero_exemplar_uuid_person_and_failed_new_claim_are_atomic(env):
    env.activate()
    item = env.observe()
    bad = env.request(item, name="Do not retain this name")
    bad["binding_sha256"] = "0" * 64
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.mutate(env.store, GROUP, bad), status=409)
    assert immutable_snapshot(env.store) == before
    event = env.associate(item)
    person = next(row for row in rows(env.store, "speakers") if row["id"] == event["person_id"])
    assert uuid.UUID(hex=person["id"]).version == 4
    assert person["state"] == "reviewed_person" and person["session_id"] == ""
    assert person["embedding"] is person["embedding_evidence"] is None
    assert event["active_positive_count"] == 0
    assert event["acoustic_reference_status"] == "none"
    assert event["acoustic_support_certified"] is False
    assert count(env.store, "identity_active_reviews") == count(env.store, "speaker_examples") == 0


def test_existing_person_and_same_names_preserve_independent_original_ids(env):
    env.activate()
    one, two, three = [env.observe() for _ in range(3)]
    a = env.associate(one, name="Same display name")
    b = env.associate(two, name="Same display name")
    c = env.associate(three, person=a["person_id"])
    assert a["person_id"] != b["person_id"]
    assert c["person_id"] == a["person_id"]
    assert count(env.store, "speakers", "identity_group=?", (GROUP,)) == 2
    assert {row["observation_id"] for row in bridge.occurrences(
        env.store, GROUP, a["person_id"])["items"]} == {one.observation_id, three.observation_id}


@pytest.mark.parametrize("field,value", [
    ("source_session_id", "missing-source"),
    ("recording_id", "f" * 32),
    ("observation_id", "f" * 64),
    ("binding_sha256", "f" * 64),
    ("expected_source_authority_revision", 999),
    ("expected_source_revision", 999),
], ids=["session", "recording", "observation", "binding", "authority", "source-revision"])
def test_each_request_binding_fence_rejects_atomically(env, field, value):
    env.activate()
    item = env.observe()
    body = env.request(item)
    body[field] = value
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.mutate(env.store, GROUP, body))
    assert immutable_snapshot(env.store) == before


@pytest.mark.parametrize("parent", [(0., 4.), (.5, 2.), (0., .5)],
                         ids=["crop-inside", "crop-extends-outside", "crop-disjoint"])
def test_exact_crop_geometry_never_expands_to_parent_or_words(env, parent):
    env.activate()
    item = env.observe(parent=parent)
    before = env.store.results(item.sid)
    observed = bridge.observation(env.store, item.sid, item.rid, item.observation_id)
    event = env.associate(item)
    detail = bridge.event_detail(env.store, GROUP, event["event_id"])
    assert detail["binding"] == observed["binding"]
    assert detail["binding"]["parent_ref"] == item.parent_ref
    crop = detail["binding"]["physical_crop"]
    assert (crop["source_start_frame"], crop["source_end_frame"]) == (
        item.node["source_spans"][0]["source_start"], item.node["source_spans"][0]["source_end"])
    assert crop["source_end_frame"] - crop["source_start_frame"] == 32000
    assert detail["extent"] == "observed_crop_only"
    assert detail["authority"] == "human_observation_claim"
    assert detail["acoustic_support_certified"] is detail["enrollment_allowed"] is False
    assert env.store.results(item.sid) == before


def test_candidate_missing_is_eligible_but_pending_is_not(env):
    env.activate()
    complete, pending = env.observe(), env.observe(pending=True)
    view = bridge.observation(env.store, complete.sid, complete.rid, complete.observation_id)
    assert view["review_eligible"] is True
    assert view["binding"]["evidence_kind"] == "candidate_missing"
    assert view["binding"]["certificate_id"] is None
    assert bridge.observation(env.store, pending.sid, pending.rid,
                              pending.observation_id)["review_eligible"] is False
    assert_refused(lambda: bridge.mutate(env.store, GROUP, env.request(pending)))


def test_one_observation_one_claim_and_exact_physical_alias_conflict(env):
    env.activate()
    one, alias = env.observe(physical="same-physical-crop"), env.observe(physical="same-physical-crop")
    a = env.associate(one)
    assert_refused(lambda: env.associate(one, person=a["person_id"]), status=409)
    before = immutable_snapshot(env.store)
    assert_refused(lambda: env.associate(alias, name="Contradictory owner"), status=409)
    assert immutable_snapshot(env.store) == before
    b = env.associate(alias, person=a["person_id"])
    assert b["person_id"] == a["person_id"]
    assert count(env.store, CURRENT) == 2 and count(env.store, "identity_active_reviews") == 0


def test_one_manual_person_can_span_recorded_model_spaces_without_acoustic_import(env, monkeypatch):
    from types import SimpleNamespace
    from audio_backend import embedding_space
    env.activate()
    first = env.observe()
    claimed = env.associate(first)
    synthetic_binding = deepcopy(next(iter(embedding_space._AUDITED_BINDINGS.values())))
    synthetic_binding["model"]["sha256"] = "f" * 64
    binding_id = embedding_space._digest(synthetic_binding)
    monkeypatch.setitem(embedding_space._AUDITED_BINDINGS, binding_id, synthetic_binding)
    second_space = embedding_space._descriptor(binding_id, SimpleNamespace(provider="cpu", num_threads=2))
    second = env.observe(space=second_space)
    added = env.associate(second, person=claimed["person_id"])
    assert added["binding"]["space_id"] != claimed["binding"]["space_id"]
    env.settings.recording_speaker_model_path = str(env.root / "never-read-new-current-model")
    assert bridge.event_detail(env.store, GROUP, claimed["event_id"])["binding"]["space_id"] == claimed["binding"]["space_id"]
    assert count(env.store, "identity_active_reviews") == count(env.store, "speaker_support_vectors") == 0


def test_manual_acoustic_and_relation_actions_share_idempotency_namespace(env):
    env.activate()
    a, b = [env.associate(env.observe()) for _ in range(2)]
    assert_refused(lambda: bridge.relate(env.store, GROUP, {
            **env.fence(key="activate-bank"), "person_id": a["person_id"],
            "other_person_id": b["person_id"], "kind": "same_person", "confirmed": True,
        }), status=409, code="idempotency_conflict")
    merged = env.relation(a["person_id"], b["person_id"])
    relation_row = next(row for row in rows(env.store, "identity_relation_events")
                        if row["id"] == merged["event_id"])
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.rename(env.store, GROUP, a["person_id"], {
        **env.fence(key=relation_row["idempotency_key"]), "name": "Do not rename",
    }), status=409)
    acoustic_sid = env.store.create_session("existing acoustic route", GROUP)["id"]
    with pytest.raises(ValueError, match="idempotency_conflict"):
        verified_identity.mutate(env.store, acoustic_sid, {
            **env.fence(key="activate-bank"), "kind": "positive",
            "new_person_name": "No acoustic import", "support_id": "a" * 32,
            "vector_id": "b" * 32, "manifest_sha256": "c" * 64,
            "vector_sha256": "d" * 64, "space_id": "e" * 64,
        })
    assert immutable_snapshot(env.store) == before


@pytest.mark.parametrize("extra", [
    {"vector": [1., 0.]}, {"support_id": "fake-anchor"},
    {"attestation": {"version": "human_exact_observation_person_claim_v1",
                     "extent": "whole_parent", "confirmed": True}},
], ids=["client-vector", "client-anchor", "whole-parent"])
def test_no_vector_anchor_or_parent_wide_import(env, extra):
    env.activate()
    item = env.observe()
    body = env.request(item)
    body.update(extra)
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.mutate(env.store, GROUP, body))
    assert immutable_snapshot(env.store) == before


def test_replace_retract_idempotence_and_failed_replacement_preserve_current(env):
    env.activate()
    one, other = env.observe(), env.observe()
    original_request = env.request(one)
    first = bridge.mutate(env.store, GROUP, original_request)
    assert bridge.mutate(env.store, GROUP, original_request) == first
    assert_refused(lambda: bridge.mutate(
        env.store, GROUP, dict(original_request, new_person_name="Changed")), status=409)
    bad = env.request(other)
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.mutate(env.store, GROUP, bad, previous=first["event_id"]), status=409)
    assert immutable_snapshot(env.store) == before
    corrected_body = env.request(one, name="Corrected owner")
    second = bridge.mutate(env.store, GROUP, corrected_body, previous=first["event_id"])
    assert bridge.mutate(env.store, GROUP, corrected_body, previous=first["event_id"]) == second
    assert not bridge.event_detail(env.store, GROUP, first["event_id"])["active"]
    retract = env.fence()
    result = bridge.mutate(env.store, GROUP, retract, previous=second["event_id"], retract=True)
    assert bridge.mutate(env.store, GROUP, retract, previous=second["event_id"], retract=True) == result
    assert bridge.mutate(env.store, GROUP, original_request) == first  # historical exact retry
    assert count(env.store, CURRENT) == 0


def test_concurrent_stale_cas_has_one_winner_without_orphan_person(env):
    env.activate()
    item = env.observe()
    original = env.request(item)
    barrier = __import__("threading").Barrier(2)
    def claim(index):
        barrier.wait(timeout=5)
        try:
            return bridge.mutate(env.store, GROUP, dict(
                original, idempotency_key=f"concurrent-{index}", new_person_name=f"Owner {index}"))
        except bridge.BridgeError as exc:
            # Serialized metadata admission may refuse a simultaneous caller
            # before it reaches CAS. Its identical retry must then lose CAS.
            assert exc.status in (409, 503)
            return (index, exc.status)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(claim, range(2)))
    assert sum(isinstance(value, dict) for value in answers) == 1
    loser = next(value for value in answers if isinstance(value, tuple))[0]
    assert_refused(lambda: bridge.mutate(env.store, GROUP, dict(
        original, idempotency_key=f"concurrent-{loser}", new_person_name=f"Owner {loser}")), status=409)
    assert count(env.store, CURRENT) == count(env.store, "speakers", "identity_group=?", (GROUP,)) == 1


def test_fenced_rename_preserves_uuid_and_invalidates_person_cursor(env):
    env.activate()
    a, b = [env.associate(env.observe(), name="Name") for _ in range(2)]
    page = bridge.persons(env.store, GROUP, limit=1)
    assert page["has_more"] and page["next_cursor"]
    # A bank-owned session is optional; the old session-scoped rename must fence.
    sid = env.store.create_session("bank owner", GROUP)["id"]
    with pytest.raises(ValueError, match="reviewed_person_revision_required"):
        env.store.correct_speaker(sid, a["person_id"], name="Unfenced")
    result = bridge.rename(env.store, GROUP, a["person_id"], {**env.fence(), "name": "Renamed"})
    assert result["person_id"] == a["person_id"]
    assert_refused(lambda: bridge.persons(env.store, GROUP, cursor=page["next_cursor"], limit=1), status=409)
    assert {row["person_id"] for row in bridge.persons(env.store, GROUP)["items"]} == {
        a["person_id"], b["person_id"]}


@pytest.mark.parametrize("undo_first", [0, 1], ids=["undo-ab-first", "undo-bc-first"])
def test_nested_exact_edge_undo_preserves_original_association_owners(env, undo_first):
    env.activate()
    claims = [env.associate(env.observe(), name=f"Owner {n}") for n in range(3)]
    a, b, c = [event["person_id"] for event in claims]
    ab, bc = env.relation(a, b), env.relation(b, c)
    before = rows(env.store, CURRENT)
    sequence = [ab, bc] if undo_first == 0 else [bc, ab]
    env.undo(sequence[0])
    expected = {a: a, b: c, c: c} if undo_first == 0 else {a: b, b: b, c: c}
    for event in claims:
        detail = bridge.event_detail(env.store, GROUP, event["event_id"])
        assert detail["person_id"] == event["person_id"]
        assert detail["canonical_person_id"] == expected[event["person_id"]]
    env.undo(sequence[1])
    assert rows(env.store, CURRENT) == before
    for event in claims:
        assert bridge.event_detail(env.store, GROUP, event["event_id"])["canonical_person_id"] == event["person_id"]


def test_different_people_blocks_union_and_exact_crop_alias_conflicts_after_undo(env):
    env.activate()
    one, seed, alias = env.observe(physical="alias"), env.observe(), env.observe(physical="alias")
    a, b = env.associate(one), env.associate(seed)
    constraint = env.relation(a["person_id"], b["person_id"], kind="different_people")
    assert_refused(lambda: env.relation(a["person_id"], b["person_id"]), status=409)
    env.undo(constraint)
    merged = env.relation(a["person_id"], b["person_id"])
    later = env.associate(alias, person=b["person_id"])
    env.undo(merged)
    for event in (a, later):
        detail = bridge.event_detail(env.store, GROUP, event["event_id"])
        assert detail["status"] == "conflicting_exact_crop_claims"
        assert detail["effective_person_id"] is None and detail["active"]
        assert detail["person_id"] == event["person_id"]


def test_passive_restart_reads_trap_audio_vector_comparison_and_live_authority(env, monkeypatch):
    env.activate()
    item = env.observe(vector=True)
    event = env.associate(item)
    recorded_space = bridge.event_detail(env.store, GROUP, event["event_id"])["binding"]["space_id"]
    def forbidden(*args, **kwargs):
        raise AssertionError("A passive manual read touched an acoustic/live-authority path")
    monkeypatch.setattr(provenance, "current", forbidden)
    monkeypatch.setattr(history.HistoryStore, "observation_page", forbidden)
    monkeypatch.setattr(speaker_support, "audio", forbidden)
    monkeypatch.setattr(support_vectors, "read", forbidden)
    env.settings.verified_identity_policy = None
    restarted = Store(env.store.path, env.settings)
    # Passive reads remain usable with all model/producer/worker flags off.
    detail = bridge.event_detail(restarted, GROUP, event["event_id"])
    assert detail["binding"]["space_id"] == recorded_space
    assert detail["effective_person_id"] == event["person_id"]
    assert len(bridge.occurrences(restarted, GROUP, event["person_id"])["items"]) == 1
    assert not item.path.exists()


def test_valid_retirement_keeps_attribution_and_revocation_suppresses_it(env):
    env.activate()
    item = env.observe()
    event = env.associate(item)
    binding = bridge.observation(env.store, item.sid, item.rid, item.observation_id)["binding_sha256"]
    before = bridge.bank(env.store, GROUP)["profile_revision"]
    env.retire(item)
    retired = bridge.event_detail(env.store, GROUP, event["event_id"])
    assert retired["status"] == "source_retired"
    assert retired["effective_person_id"] == event["person_id"]
    assert retired["binding_sha256"] == binding
    assert bridge.bank(env.store, GROUP)["profile_revision"] == before + 1
    assert len(bridge.occurrences(env.store, GROUP, event["person_id"])["items"]) == 1
    with env.store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        history.invalidate_sources(db, [item.cid], "source_changed")
    revoked = bridge.event_detail(env.store, GROUP, event["event_id"])
    assert revoked["effective_person_id"] is None
    assert revoked["status"] in ("source_revoked", "binding_stale")
    assert bridge.occurrences(env.store, GROUP, event["person_id"])["items"] == []
    diagnostic = bridge.occurrences(env.store, GROUP, event["person_id"], include_unavailable=True)
    assert len(diagnostic["items"]) == 1 and diagnostic["items"][0]["effective_person_id"] is None


def test_retracted_only_history_source_state_is_response_diagnostic(env):
    env.activate()
    item = env.observe()
    claim = env.associate(item)
    env.retract(claim)
    before = bridge.event_detail(env.store, GROUP, claim["event_id"])
    assert before["source_diagnostic_snapshot"] == "response_only"
    with env.store.db() as db:
        db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (item.cid,))
    after = bridge.event_detail(env.store, GROUP, claim["event_id"])
    assert after["source_diagnostic_snapshot"] == "response_only"
    assert after["active"] is False and after["effective_person_id"] is None


@pytest.mark.parametrize("changed", ["parent", "mapping", "cancelled"])
def test_parent_mapping_and_cancellation_invalidate_saved_claim_fences(env, changed):
    env.activate()
    item = env.observe()
    event = env.associate(item)
    request = env.request(item)
    before_revision = bridge.bank(env.store, GROUP)["profile_revision"]
    with env.store.db() as db:
        if changed == "parent":
            db.execute("UPDATE segments SET text='changed parent' WHERE id=?", (item.segment_id,))
        elif changed == "mapping":
            db.execute("UPDATE recording_work SET capture_start_frame=16000 WHERE chunk_id=?", (item.cid,))
        else:
            db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (item.cid,))
    detail = bridge.event_detail(env.store, GROUP, event["event_id"])
    assert detail["effective_person_id"] is None
    assert detail["status"] in ("source_revoked", "binding_stale")
    assert bridge.bank(env.store, GROUP)["profile_revision"] > before_revision
    assert_refused(lambda: bridge.mutate(env.store, GROUP, request), status=409)


@pytest.mark.parametrize("corruption", ["missing", "wrong-owner", "extra"], ids=["missing-row", "altered-row", "injected-row"])
def test_current_projection_integrity_checks_missing_and_unreturned_rows(env, corruption):
    env.activate()
    one, two = env.observe(), env.observe()
    a, b = env.associate(one), env.associate(two)
    with env.store.db() as db:
        if corruption == "missing":
            db.execute(f"DELETE FROM {CURRENT} WHERE event_id=?", (b["event_id"],))
        elif corruption == "wrong-owner":
            db.execute(f"UPDATE {CURRENT} SET person_id=? WHERE event_id=?", (a["person_id"], b["event_id"]))
        else:
            # Inject a stale projection for an otherwise valid, retracted event.
            saved = dict(db.execute(f"SELECT * FROM {CURRENT} WHERE event_id=?", (b["event_id"],)).fetchone())
    if corruption == "extra":
        env.retract(b)
        with env.store.db() as db:
            db.execute(f"INSERT INTO {CURRENT} ({','.join(saved)}) VALUES ({','.join('?' for _ in saved)})",
                       tuple(saved.values()))
    before = rows(env.store, CURRENT)
    # Querying A must detect damage to B, not claim a complete healthy page.
    assert_refused(lambda: bridge.occurrences(env.store, GROUP, a["person_id"]), status=503)
    assert rows(env.store, CURRENT) == before  # no lazy GET repair


def test_cross_recording_keyset_pages_and_source_change_invalidate_cursor(env):
    env.activate()
    one, two, three = [env.observe() for _ in range(3)]
    first = env.associate(one)
    env.associate(two, person=first["person_id"])
    env.associate(three, person=first["person_id"])
    page = bridge.occurrences(env.store, GROUP, first["person_id"], limit=1)
    assert len(page["items"]) == 1 and page["has_more"] and page["next_cursor"]
    following = bridge.occurrences(env.store, GROUP, first["person_id"], cursor=page["next_cursor"], limit=1)
    assert following["items"][0]["observation_id"] != page["items"][0]["observation_id"]
    with env.store.db() as db:
        db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (three.cid,))
    assert_refused(lambda: bridge.occurrences(
        env.store, GROUP, first["person_id"], cursor=page["next_cursor"], limit=1), status=409)
    assert len(json.dumps(following).encode()) <= 128 * 1024


def test_occurrence_and_exact_erase_query_plans_use_partition_indexes(env):
    env.activate()
    item = env.observe()
    event = env.associate(item)
    with env.store.db() as db:
        current_plan = db.execute(f"""EXPLAIN QUERY PLAN SELECT event_id FROM {CURRENT}
            WHERE identity_group=? AND generation=? AND person_id=? AND observation_id>?
            ORDER BY observation_id LIMIT 32""",
            (GROUP, event["generation"], event["person_id"], "")).fetchall()
        erase_plan = db.execute("""EXPLAIN QUERY PLAN SELECT id FROM identity_review_events
            WHERE identity_group=? AND generation=? AND primary_observation_id=?""",
            (GROUP, event["generation"], item.observation_id)).fetchall()
    for plan in (current_plan, erase_plan):
        detail = " ".join(str(row[3]) for row in plan).upper()
        assert "SEARCH" in detail and "INDEX" in detail
        assert "SCAN IDENTITY_" not in detail


def test_shared_event_reservation_protects_retract_and_net_erase_at_capacity(env, monkeypatch):
    env.activate()
    item = env.observe()
    monkeypatch.setattr(verified_identity, "MAX_EVENTS", 3)
    first = env.associate(item)  # activation + claim + one reserved retraction = 3
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.rename(
        env.store, GROUP, first["person_id"], {**env.fence(), "name": "No spare slot"}), status=429)
    assert immutable_snapshot(env.store) == before
    retracted = env.retract(first)
    assert count(env.store, "identity_review_events") == 3
    receipt = bridge.erase(env.store, GROUP, retracted["event_id"], env.erase_request())
    assert_source_free_receipt(env.store, receipt)
    assert count(env.store, "identity_review_events") == 2


def test_combined_reviewed_person_count_has_no_human_state_bypass(env, monkeypatch):
    env.activate()
    one, two = env.observe(), env.observe()
    first = env.associate(one)
    with env.store.db() as db:
        db.execute("""INSERT INTO speakers(id,session_id,identity_group,name,embedding,state,created)
            VALUES(?,?,?,'existing acoustic person',NULL,'reviewed_reference',0)""",
            (uuid.uuid4().hex, "", GROUP))
    monkeypatch.setattr(verified_identity, "MAX_PERSONS", 2)
    before = immutable_snapshot(env.store)
    assert_refused(lambda: env.associate(two, name="Over combined cap"), status=429)
    assert immutable_snapshot(env.store) == before
    assert env.associate(two, person=first["person_id"])["person_id"] == first["person_id"]


@pytest.mark.parametrize("target", ["active", "replaced", "retracted"])
def test_exact_erase_covers_all_branches_prior_owners_and_preserves_other_scopes(env, target):
    env.activate()
    item, alias, unrelated = env.observe(physical="same"), env.observe(physical="same"), env.observe()
    first = env.associate(item, name="Earlier owner")
    second = bridge.mutate(env.store, GROUP, env.request(item, name="Later owner"), previous=first["event_id"])
    retraction = env.retract(second)
    current = env.associate(item, person=first["person_id"])
    alias_claim = env.associate(alias, person=first["person_id"])
    other = env.associate(unrelated, name="Other observation")
    env.activate("other-bank")
    other_bank = env.associate(item, name="Other bank owner", group="other-bank")
    before_people = rows(env.store, "speakers")
    independent = {name: rows(env.store, name) for name in
                   ("identity_active_reviews", "identity_decisions", "identity_relation_events")}
    chosen = {"active": current, "replaced": first, "retracted": retraction}[target]
    receipt = bridge.erase(env.store, GROUP, chosen["event_id"], env.erase_request())
    assert_source_free_receipt(env.store, receipt)
    assert count(env.store, "identity_review_events",
                 "identity_group=? AND primary_observation_id=?", (GROUP, item.observation_id)) == 0
    erased_ids = {first["event_id"], second["event_id"], retraction["event_id"], current["event_id"]}
    assert not erased_ids & {row["event_id"] for row in rows(env.store, "identity_review_event_order")}
    assert rows(env.store, "speakers") == before_people
    assert {name: rows(env.store, name) for name in independent} == independent
    assert bridge.event_detail(env.store, GROUP, alias_claim["event_id"])["active"]
    assert bridge.event_detail(env.store, GROUP, other["event_id"])["active"]
    assert bridge.event_detail(env.store, "other-bank", other_bank["event_id"])["active"]
    for event_id in erased_ids:
        assert_refused(lambda event_id=event_id: bridge.event_detail(env.store, GROUP, event_id), status=404)


def test_delete_retry_is_one_source_free_receipt_and_rejects_changed_or_missing_target(env, monkeypatch):
    from types import SimpleNamespace
    group = "receipt." + "g" * 92
    assert len(group) == 100
    env.activate(group)
    item = env.observe()
    original_request = env.request(item, key="private-original-key", group=group)
    first = bridge.mutate(env.store, group, original_request)
    # Nineteen-digit revisions and the longest current-width timestamp exercise
    # the full 22-column receipt at its current-clock worst case.
    with env.store.db() as db:
        db.execute("UPDATE identity_group_policy SET revision=? WHERE identity_group=?",
                   (10**18, group))
    body = env.erase_request(group=group)
    current_clock = 1792472400.1234567
    assert len(json.dumps(current_clock)) == 18
    with monkeypatch.context() as patch:
        patch.setattr(bridge, "time", SimpleNamespace(
            time=lambda: current_clock, monotonic=bridge.time.monotonic))
        receipt = bridge.erase(env.store, group, first["event_id"], body)
    before = immutable_snapshot(env.store)
    assert bridge.erase(env.store, group, first["event_id"], body) == receipt
    assert immutable_snapshot(env.store) == before
    assert_source_free_receipt(env.store, receipt)
    stored = next(row for row in rows(env.store, "identity_review_events")
                  if row["id"] == receipt["erasure_event_id"])
    assert len(stored) == 22 and stored["created"] == current_clock
    complete_size = len(json.dumps(stored, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode())
    assert complete_size == 866 + len(group) + 2 * len(str(stored["sequence"])) + 18 == 1022
    assert_refused(lambda: bridge.erase(env.store, group, uuid.uuid4().hex, body), status=409)
    assert_refused(lambda: bridge.erase(env.store, group, first["event_id"],
                                      env.erase_request(group=group)), status=404)
    assert_refused(lambda: bridge.erase(env.store, group, receipt["erasure_event_id"],
                                      env.erase_request(group=group)), status=404)
    encoded = json.dumps(rows(env.store, "identity_review_events"), sort_keys=True)
    for forbidden in (item.sid, item.rid, item.observation_id, original_request["binding_sha256"],
                      "private-original-key", first["person_id"]):
        assert forbidden not in encoded
    page = bridge.events(env.store, group)
    assert not any(row["action"] == "erase_primary" for row in page["items"])


@pytest.mark.parametrize("change", ["non-uuid", "uuid-v1", "source-field", "operator-context", "wrong-scope", "unconfirmed"])
def test_erase_request_has_strict_opaque_safe_shape(env, change):
    env.activate()
    event = env.associate(env.observe())
    body = env.erase_request()
    if change == "non-uuid":
        body["idempotency_key"] = "contains-personal-text"
    elif change == "uuid-v1":
        body["idempotency_key"] = "00000000-0000-1000-8000-000000000000"
    elif change == "source-field":
        body["source_session_id"] = "forbidden"
    elif change == "operator-context":
        body["operator_context"] = "forbidden free text"
    elif change == "wrong-scope":
        body["scope"] = "all_people"
    else:
        body["confirmed"] = False
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.erase(env.store, GROUP, event["event_id"], body))
    assert immutable_snapshot(env.store) == before


def test_erased_post_cannot_replay_and_old_delete_retry_protects_new_claim(env):
    env.activate()
    item = env.observe()
    body = env.request(item)
    first = bridge.mutate(env.store, GROUP, body)
    deletion = env.erase_request()
    receipt = bridge.erase(env.store, GROUP, first["event_id"], deletion)
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.mutate(env.store, GROUP, body), status=409)
    assert immutable_snapshot(env.store) == before
    refreshed = env.request(item, person=first["person_id"], key=body["idempotency_key"])
    later = bridge.mutate(env.store, GROUP, refreshed)
    assert later["event_id"] != first["event_id"]
    before_retry = immutable_snapshot(env.store)
    assert bridge.erase(env.store, GROUP, first["event_id"], deletion) == receipt
    assert immutable_snapshot(env.store) == before_retry
    assert bridge.event_detail(env.store, GROUP, later["event_id"])["active"]


def test_scoped_erase_does_not_need_source_or_canonical_availability(env):
    env.activate()
    item = env.observe()
    first = env.associate(item)
    with env.store.db() as db:
        db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (item.cid,))
        # A malformed relation head makes canonical attribution unavailable.
        db.execute("UPDATE identity_group_policy SET relation_head=? WHERE identity_group=?",
                   ("f" * 64, GROUP))
    receipt = bridge.erase(env.store, GROUP, first["event_id"], env.erase_request())
    assert_source_free_receipt(env.store, receipt)
    assert count(env.store, CURRENT) == 0


def test_erase_failure_rolls_back_chain_order_projection_and_revision(env, monkeypatch):
    from types import SimpleNamespace
    group = "receipt." + "g" * 92
    env.activate(group)
    item = env.observe()
    first = env.associate(item, group=group)
    second = bridge.mutate(env.store, group, env.request(item, name="Replacement", group=group),
                           previous=first["event_id"])
    with env.store.db() as db:
        db.execute("UPDATE identity_group_policy SET revision=? WHERE identity_group=?",
                   (10**18, group))
    body = env.erase_request(group=group)
    before = immutable_snapshot(env.store)
    with env.store.db() as db:
        db.execute("""CREATE TRIGGER fixture_erase_receipt_failure BEFORE INSERT
            ON identity_review_events WHEN NEW.action='erase_primary'
            BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END""")
    assert_refused(lambda: bridge.erase(env.store, group, second["event_id"], body), status=503)
    assert immutable_snapshot(env.store) == before
    with env.store.db() as db:
        db.execute("DROP TRIGGER fixture_erase_receipt_failure")
    def complete_database_state():
        # This single bounded fixture's full schema and logical SQLite contents,
        # including history, provenance, change-feed and sqlite_sequence rows.
        with env.store.db() as db:
            schema = tuple(tuple(row) for row in db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
            dump = tuple(db.iterdump())
            assert len(schema) <= 512 and sum(len(line.encode()) for line in dump) <= 2 * 1024**2
            metadata = tuple((name, db.execute("PRAGMA " + name).fetchone()[0]) for name in
                             ("application_id", "user_version", "schema_version", "journal_mode"))
        return schema, dump, metadata
    before_full = complete_database_state()
    exotic_clock = -1.2345678901234567e+100
    assert len(json.dumps(exotic_clock, allow_nan=False)) == 24
    assert 866 + len(group) + 2 * len(str(body["expected_revision"] + 1)) + 24 == 1028
    with monkeypatch.context() as patch:
        patch.setattr(bridge, "time", SimpleNamespace(
            time=lambda: exotic_clock, monotonic=bridge.time.monotonic))
        assert_refused(lambda: bridge.erase(env.store, group, second["event_id"], body),
                       status=429, code="metadata_capacity")
    assert complete_database_state() == before_full
    assert bridge.bank(env.store, group)["profile_revision"] == body["expected_revision"]
    assert count(env.store, "identity_review_events", "id IN (?,?)",
                 (first["event_id"], second["event_id"])) == 2
    assert count(env.store, CURRENT, "event_id=?", (second["event_id"],)) == 1


def test_source_deletion_erases_retracted_only_chains_and_keeps_retained_bank(env):
    env.activate()
    item, other = env.observe(), env.observe()
    original = env.request(item, key="source-private-retry")
    a = bridge.mutate(env.store, GROUP, original)
    replacement = bridge.mutate(env.store, GROUP, env.request(item, name="Prior second owner"),
                                previous=a["event_id"])
    env.retract(replacement)
    b = env.associate(other, person=a["person_id"])
    relation = env.relation(a["person_id"], replacement["person_id"])
    before_people = rows(env.store, "speakers")
    generation = bridge.bank(env.store, GROUP)["generation"]
    before_revision = bridge.bank(env.store, GROUP)["profile_revision"]
    lifecycle.delete_session(env.store, item.sid, delete_file=lambda path: None)
    assert bridge.bank(env.store, GROUP)["generation"] == generation
    assert bridge.bank(env.store, GROUP)["profile_revision"] == before_revision + 1
    assert rows(env.store, "speakers") == before_people
    assert count(env.store, "identity_relation_events", "id=?", (relation["event_id"],)) == 1
    assert count(env.store, "identity_review_events", "source_session_id=?", (item.sid,)) == 0
    assert_refused(lambda: bridge.mutate(env.store, GROUP, original))
    for event in (a, replacement):
        assert_refused(lambda event=event: bridge.event_detail(env.store, GROUP, event["event_id"]), status=404)
    assert bridge.event_detail(env.store, GROUP, b["event_id"])["active"]
    assert item.sid not in json.dumps(bridge.events(env.store, GROUP))
    lifecycle.delete_session(env.store, other.sid, delete_file=lambda path: None)
    assert bridge.bank(env.store, GROUP)["generation"] == generation
    assert count(env.store, CURRENT) == 0


def test_source_delete_scrubs_older_creation_pointer_and_surviving_bank_feed(env):
    env.activate()
    item = env.observe()
    first = env.associate(item)
    owner_sid = env.store.create_session("surviving bank session", GROUP)["id"]
    with env.store.db() as db:
        db.execute("UPDATE speakers SET session_id=?,state='reviewed_reference' WHERE id=?",
                   (item.sid, first["person_id"]))
    lifecycle.delete_session(env.store, item.sid, delete_file=lambda path: None)
    with env.store.db() as db:
        assert db.execute("SELECT session_id FROM speakers WHERE id=?",
                          (first["person_id"],)).fetchone()[0] == ""
        snapshots = [row[0] for row in db.execute(
            "SELECT data FROM result_changes WHERE session_id=? AND entity='speakers' AND data IS NOT NULL",
            (owner_sid,))]
    assert all(item.sid not in snapshot for snapshot in snapshots)
    assert env.store.session(owner_sid)["deleted"] == 0


def test_source_delete_keeps_prior_receipt_opaque_and_exact_retry_works(env):
    env.activate()
    item = env.observe()
    first = env.associate(item)
    body = env.erase_request()
    receipt = bridge.erase(env.store, GROUP, first["event_id"], body)
    lifecycle.delete_session(env.store, item.sid, delete_file=lambda path: None)
    assert_source_free_receipt(env.store, receipt)
    assert bridge.erase(env.store, GROUP, first["event_id"], body) == receipt
    assert item.sid not in json.dumps(rows(env.store, "identity_review_events"))


def test_selected_delete_revision_cas_never_silently_rebases_other_selection(env):
    env.activate()
    one, two = [env.associate(env.observe()) for _ in range(2)]
    first_delete, second_delete = env.erase_request(), env.erase_request()
    bridge.erase(env.store, GROUP, one["event_id"], first_delete)
    before = immutable_snapshot(env.store)
    assert_refused(lambda: bridge.erase(env.store, GROUP, two["event_id"], second_delete), status=409)
    assert immutable_snapshot(env.store) == before
    assert bridge.event_detail(env.store, GROUP, two["event_id"])["active"]


def test_full_reset_with_no_bank_sessions_clears_receipts_and_rotates_generation(env):
    env.activate()
    item = env.observe()
    first = env.associate(item)
    delete_body = env.erase_request()
    receipt = bridge.erase(env.store, GROUP, first["event_id"], delete_body)
    assert count(env.store, "sessions", "identity_group=?", (GROUP,)) == 0
    with env.store.db() as db:
        evidence_before = db.execute(f"SELECT observation_id,header FROM {history.CERTIFICATES}").fetchall()
    reset_group(env.store, GROUP)
    assert bridge.bank(env.store, GROUP)["generation"] != first["generation"]
    assert count(env.store, CURRENT) == 0
    assert count(env.store, "speakers", "identity_group=?", (GROUP,)) == 0
    assert count(env.store, "identity_review_events", "identity_group=?", (GROUP,)) == 0
    assert count(env.store, "identity_relation_events", "identity_group=?", (GROUP,)) == 0
    with env.store.db() as db:
        evidence_after = db.execute(f"SELECT observation_id,header FROM {history.CERTIFICATES}").fetchall()
    assert [tuple(row) for row in evidence_after] == [tuple(row) for row in evidence_before]
    assert_refused(lambda: bridge.erase(env.store, GROUP, first["event_id"], delete_body), status=409)
    assert_refused(lambda: bridge.event_detail(env.store, GROUP, receipt["erasure_event_id"]), status=404)


def test_partial_or_unknown_bridge_schema_is_not_normalized_on_restart(env):
    env.activate()
    before = rows(env.store, "identity_group_policy")
    with env.store.db() as db:
        db.execute("DROP TRIGGER " + history.CERTIFICATES + "_erase_only")
        db.execute(f"""CREATE TRIGGER {history.CERTIFICATES}_erase_only BEFORE DELETE
            ON {history.CERTIFICATES} BEGIN SELECT RAISE(ABORT,'unknown future guard'); END""")
    with pytest.raises((ValueError, RuntimeError), match="[Ii]ncompatible|[Ss]chema"):
        Store(env.store.path, env.settings)
    assert rows(env.store, "identity_group_policy") == before
    with env.store.db() as db:
        assert "unknown future guard" in db.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (history.CERTIFICATES + "_erase_only",)).fetchone()[0]


def test_old_shape_to_bridge_migration_failure_rolls_back_all_additions(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0)
    store = Store(tmp_path / "migration.sqlite3", settings)
    with store.db() as db:
        old_guard = db.execute("SELECT sql FROM sqlite_master WHERE name=?",
                               (history.CERTIFICATES + "_erase_only",)).fetchone()[0]
        before_policy = tuple(row["name"] for row in db.execute("PRAGMA table_info(identity_group_policy)"))
        before_events = tuple(row["name"] for row in db.execute("PRAGMA table_info(identity_review_events)"))
    def fail_guard_install(db):
        raise RuntimeError("synthetic migration fault")
    monkeypatch.setattr(history, "install_bridge_guard", fail_guard_install)
    settings.reviewed_primary_persons_enabled = True
    with pytest.raises(RuntimeError, match="synthetic migration fault"):
        Store(store.path, settings)
    with store.db() as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (CURRENT,)).fetchone() is None
        assert tuple(row["name"] for row in db.execute("PRAGMA table_info(identity_group_policy)")) == before_policy
        assert tuple(row["name"] for row in db.execute("PRAGMA table_info(identity_review_events)")) == before_events
        assert db.execute("SELECT sql FROM sqlite_master WHERE name=?",
                          (history.CERTIFICATES + "_erase_only",)).fetchone()[0] == old_guard


@pytest.mark.parametrize("part", ["header", "packet", "space", "certificate"])
def test_corrupt_retained_metadata_is_never_accepted_or_repaired(env, part):
    """Corruption is injected only into this node's fresh synthetic database."""
    env.activate()
    item = env.observe(vector=part == "certificate")
    body = env.request(item)
    with env.store.db() as db:
        if part == "header":
            db.execute(f"DROP TRIGGER {history.CERTIFICATES}_immutable")
            changed = json.loads(item.certificate["header"])
            changed["space_id"] = "f" * 64
            raw = history.encoded(changed)
            db.execute(f"UPDATE {history.CERTIFICATES} SET header=?,charge=? WHERE observation_id=?",
                       (raw, len(raw) + 1024, item.observation_id))
        elif part == "packet":
            db.execute("DROP TRIGGER recording_primary_immutable_v1")
            db.execute(f"UPDATE {provenance.TABLE} SET digest=? WHERE sequence=?",
                       ("f" * 64, item.saved["header"]["sequence"]))
        elif part == "space":
            db.execute("DROP TRIGGER recording_history_scope_policy_v1")
            db.execute(f"UPDATE {history.SCOPES} SET space_id=? WHERE scope_seq=?",
                       ("f" * 64, item.source["scope_seq"]))
        else:
            db.execute(f"DROP TRIGGER {history.CERTIFICATES}_immutable")
            db.execute(f"UPDATE {history.CERTIFICATES} SET certificate_id=? WHERE observation_id=?",
                       ("f" * 64, item.observation_id))
    before = immutable_snapshot(env.store)
    diagnostic = bridge.observation(env.store, item.sid, item.rid, item.observation_id)
    assert diagnostic["review_eligible"] is False and diagnostic["binding"] is None
    assert_refused(lambda: bridge.mutate(env.store, GROUP, body))
    assert immutable_snapshot(env.store) == before


@pytest.mark.parametrize("refusal", ["disk", "vm", "cancelled", "read-gate", "binding-bytes"])
def test_bounded_resource_refusals_leave_no_partial_person_or_claim(env, monkeypatch, refusal):
    from types import SimpleNamespace
    import threading
    env.activate()
    item = env.observe()
    body = env.request(item)
    before = immutable_snapshot(env.store)
    token = None
    locked = False
    try:
        if refusal == "disk":
            monkeypatch.setattr(bridge.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
        elif refusal == "vm":
            monkeypatch.setattr(bridge, "MAX_VM_STEPS", 0)
        elif refusal == "cancelled":
            cancelled = threading.Event()
            cancelled.set()
            token = bridge.CONTEXT.set(cancelled)
        elif refusal == "read-gate":
            assert bridge._GATE.acquire(blocking=False)
            locked = True
        else:
            monkeypatch.setattr(bridge, "MAX_BINDING", 1)
        assert_refused(lambda: bridge.mutate(env.store, GROUP, body))
    finally:
        if token is not None:
            bridge.CONTEXT.reset(token)
        if locked:
            bridge._GATE.release()
    assert immutable_snapshot(env.store) == before


def test_feature_off_restart_still_erases_cross_group_links(env):
    env.activate()
    item = env.observe()
    first = env.associate(item)
    env.settings.reviewed_primary_persons_enabled = False
    restarted = Store(env.store.path, env.settings)
    lifecycle.delete_session(restarted, item.sid, delete_file=lambda path: None)
    assert count(restarted, CURRENT) == 0
    assert count(restarted, "identity_review_events", "source_session_id=?", (item.sid,)) == 0
    assert count(restarted, "speakers", "id=?", (first["person_id"],)) == 1
    with restarted.db() as db:
        generation = db.execute("SELECT generation FROM speaker_identity_generations WHERE identity_group=?",
                                (GROUP,)).fetchone()[0]
    assert generation == first["generation"]


@pytest.mark.parametrize("corruption", [
    "policy", "generation-nul-suffix", "retracted-source", "retry-response",
    "retry-hash-nul-suffix", "relation-sequence", "relation-created",
])
def test_wide_or_wrong_typed_metadata_refuses_before_materialized_decode(env, monkeypatch, corruption):
    """Seven finite corruption cases; every injected database is newly created."""
    env.activate()
    item = env.observe()
    request = env.request(item)
    event = bridge.mutate(env.store, GROUP, request)
    relation = None
    if corruption.startswith("relation-"):
        other = env.associate(env.observe())
        relation = env.relation(event["person_id"], other["person_id"])
    elif corruption == "retracted-source":
        env.retract(event)
    wide = "x" * 8193
    with env.store.db() as db:
        if corruption == "policy":
            db.execute("DROP TRIGGER identity_policy_immutable")
            db.execute("UPDATE identity_group_policy SET policy=? WHERE identity_group=?", (wide, GROUP))
        elif corruption == "generation-nul-suffix":
            db.execute("UPDATE speaker_identity_generations SET generation=? WHERE identity_group=?",
                       (event["generation"] + "\0" + wide, GROUP))
        elif corruption.startswith("relation-"):
            db.execute("DROP TRIGGER relation_events_immutable")
            column = "sequence" if corruption == "relation-sequence" else "created"
            db.execute(f"UPDATE identity_relation_events SET {column}=? WHERE id=?",
                       (wide, relation["event_id"]))
            assert db.execute(f"SELECT typeof({column}) FROM identity_relation_events WHERE id=?",
                              (relation["event_id"],)).fetchone()[0] == "text"
        else:
            db.execute("DROP TRIGGER review_events_immutable")
            column, value = {
                "retracted-source": ("source_session_id", wide),
                "retry-response": ("response", wide),
                "retry-hash-nul-suffix": ("request_sha256", "a" * 64 + "\0" + wide),
            }[corruption]
            db.execute(f"UPDATE identity_review_events SET {column}=? WHERE id=?",
                       (value, event["event_id"]))
    before = immutable_snapshot(env.store)
    def materialization_must_not_run(*args, **kwargs):
        raise AssertionError("Corrupt scalar reached the materialized canonical loader")
    monkeypatch.setattr(identity_canonical, "load", materialization_must_not_run)
    if corruption.startswith("retry-"):
        operation = lambda: bridge.mutate(env.store, GROUP, request)
        expected = "retry_metadata_unavailable"
    elif corruption.startswith("relation-"):
        operation = lambda: bridge.persons(env.store, GROUP)
        expected = "canonical_integrity_unavailable"
    else:
        operation = lambda: bridge.bank(env.store, GROUP)
        expected = {
            "policy": "identity_policy_unavailable",
            "generation-nul-suffix": "identity_generation_unavailable",
            "retracted-source": "association_integrity_unavailable",
        }[corruption]
    assert_refused(operation, status=503, code=expected)
    assert immutable_snapshot(env.store) == before
