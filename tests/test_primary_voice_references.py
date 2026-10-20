"""Finite, model-free primary reference contracts, not acoustic accuracy tests.

Execution requires the separate review gate. Each integration node owns a fresh
database, at most six four-second synthetic WAVs and six original 512-vectors.
Mixed-path nodes add one two-second WAV and one original legacy support/vector,
using real support preparation and review admission, never injected trust rows.
No models, downloads, worker/server threads, subprocesses or ambient stores are
used. The fair-handoff node instantiates real manager/Worker objects without
starting them and fakes only configuration/model dispatch eligibility.
The tiny mixed-capacity unit schema exercises admission accounting only, not
certificate validity. Capacity cases lower constants rather than fill galleries.
"""
import base64
from copy import deepcopy
import hashlib
import sqlite3
import struct
from types import SimpleNamespace
import uuid
import wave

import pytest

from audio_backend import embedding_space, identity_canonical, lifecycle, speaker_support, support_vectors
from audio_backend import primary_voice_references as voices
from audio_backend import recording_primary_probe as primary
from audio_backend import recording_speaker_links as links
from audio_backend import recording_speaker_history as history
from audio_backend import reviewed_primary_persons as bridge
from audio_backend import verified_identity as bank
from audio_backend.identity_reset import reset_group
from audio_backend.native_owned_cell_capture import signature
from audio_backend.storage import Store
from reviewed_person_bridge_fixtures import (
    CURRENT, GROUP, POLICY, activation_request, assert_refused,
    assert_source_free_receipt, count, env, immutable_snapshot, rows,
)


def original_vector(*components):
    """Exactly representable original float32 values, never model output."""
    return [float(value) for value in components] + [0.] * (512 - len(components))


def enrollment_request(fixture, item, association, *, key=None):
    bound = bridge.observation(fixture.store, item.sid, item.rid, item.observation_id)
    return {
        **fixture.fence(key),
        "association_event_id": association["event_id"],
        "binding_sha256": bound["binding_sha256"],
        **{field: bound["binding"][field]
           for field in ("certificate_id", "vector_sha256", "space_id")},
        "attestation": deepcopy(voices.ATTESTATION),
    }


def enroll(fixture, *, person=None, vector=None, physical=None):
    item = fixture.observe(
        pcm=True, vector=True, vector_values=vector, physical=physical)
    association = fixture.associate(item, person=person)
    request = enrollment_request(fixture, item, association)
    event = voices.enroll(fixture.store, GROUP, request)
    return item, association, request, event


def snapshot(fixture):
    with fixture.store.db() as db:
        db.execute("BEGIN")
        certificates = [dict(row) for row in db.execute(
            f"SELECT * FROM {history.CERTIFICATES} ORDER BY scope_seq,sequence")]
    return {
        "bridge": immutable_snapshot(fixture.store),
        "references": rows(fixture.store, voices.TABLE),
        "suggestions": rows(fixture.store, voices.SUGGESTIONS),
        "segments": rows(fixture.store, "segments"),
        "vectors": certificates,
    }


def overwrite_certificate(fixture, item, **columns):
    """Damage only the current test's original bytes, restoring the exact guard."""
    assert set(columns) <= {"vector", "vector_sha256", "certificate_id"}
    trigger = history.CERTIFICATES + "_immutable"
    with fixture.store.db() as db:
        db.execute("BEGIN")
        sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (trigger,)).fetchone()[0]
        db.execute("DROP TRIGGER " + trigger)
        db.execute(
            f"UPDATE {history.CERTIFICATES} SET " +
            ",".join(f"{key}=?" for key in columns) + " WHERE observation_id=?",
            (*columns.values(), item.observation_id))
        db.execute(sql)


def activate_policy(fixture, **overrides):
    policy = dict(POLICY, **overrides)
    fixture.settings.verified_identity_policy = policy
    request = activation_request()
    request["policy_sha256"] = speaker_support.digest(speaker_support.canonical(policy))
    return bridge.activate_bank(fixture.store, GROUP, request)


def query(fixture, vector):
    return fixture.observe(group=GROUP, vector=True, vector_values=vector)


def legacy_support_from_crop(fixture, item):
    """One real legacy support with exactly the primary crop's float PCM key."""
    import numpy as np
    from audio_backend.speaker_evidence import clean_speaker_audio
    assert item.crop is not None and len(item.crop) == 64000
    sid = fixture.store.create_session(
        "Synthetic legacy exact crop", GROUP, bank.MODE)["id"]
    path = fixture.root / "uploads" / (uuid.uuid4().hex + ".audio")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(item.crop)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cid, created = fixture.store.add_chunk(sid, "one-legacy-exact-crop", digest, path, 2.)
    assert created
    samples = np.frombuffer(item.crop, dtype="<i2").astype("<f4") / np.float32(32768)
    turns = [{"start": 0., "end": 2., "speaker_local_id": "A"}]
    _, evidence = clean_speaker_audio(samples, turns, "A")
    descriptor = links.expected_space()
    vector = original_vector(1)
    metadata = {
        "speaker_embedding_space": descriptor,
        "speaker_embedding_space_id": embedding_space.validate_space(descriptor),
        "speaker_embedding_sha256": descriptor["model"]["sha256"],
        "speaker_embedding_version": descriptor["model"]["version"],
        "speaker_embedding_dimension": 512,
        "speaker_embedding_evidence": {"A": evidence},
        "speaker_support_producer": speaker_support.producer_provenance(
            samples, turns, descriptor["model"]["sha256"], 2),
        "diarization_turns": turns, "diarization_backend": "pyannote",
        "diarization_model_sha256": "c" * 64,
        "diarization_runtime": "synthetic-model-free",
        "diarization_runtime_version": "test-v1",
        "effective_config": {"diarization_threshold": .7, "cpu_threads": 2},
    }
    result = {"metadata": metadata, "segments": [],
              "speaker_turns": [dict(turns[0], speaker_embedding=vector)]}
    generation = fixture.fence()["expected_generation"]
    lineage = {
        "status": "complete", "decoder_sha256": "d" * 64,
        "analyzer_frame_count": 32000,
        "parts": [{"chunk_id": cid, "source_sha256": digest,
                   "seek_seconds": 0., "requested_seconds": 2.,
                   "analyzer_frames": [0, 32000]}],
    }
    prepared = speaker_support.prepare_window(
        result, path, lineage, generation, core_start=0., analyzer_start=0.,
        settings=fixture.settings)["A"]
    assert prepared["status"] == "available", prepared["status"]
    assert prepared["manifest"]["support"]["pooled_pcm_sha256"] == item.node["float32le_sha256"]
    proof = support_vectors.query_proof(vector, metadata, prepared)
    old_enabled = fixture.settings.speaker_support_enabled
    fixture.settings.speaker_support_enabled = True
    try:
        with fixture.store.db() as db:
            db.execute("BEGIN")
            db.execute("UPDATE chunks SET status='completed',next_offset=2. WHERE id=?", (cid,))
            chunk = dict(db.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone())
            support_id, status = speaker_support.commit_support(
                fixture.store, db, chunk, 0., GROUP, "A", None, prepared)
            assert status == "available"
            vector_id = support_vectors.commit(fixture.store, db, support_id, GROUP, proof)
            assert vector_id is not None
            raw, _ = support_vectors.read(db, support_id)
            support = dict(db.execute(
                "SELECT * FROM speaker_support WHERE id=?", (support_id,)).fetchone())
    finally:
        fixture.settings.speaker_support_enabled = old_enabled
    return SimpleNamespace(sid=sid, support=support, vector=raw)


def legacy_request(fixture, legacy, person, *, kind="positive"):
    return {
        **fixture.fence(),
        "kind": kind, "person_id": person,
        "support_id": legacy.support["id"], "vector_id": legacy.vector["id"],
        "manifest_sha256": legacy.support["manifest_sha256"],
        "vector_sha256": legacy.vector["vector_sha256"],
        "space_id": legacy.vector["space_id"],
        "attestation": {
            "version": bank.ATTESTATION, "reviewed_all_pooled_support": True,
            "single_identified_voice": True, "no_other_overlapping_voice": True,
        },
    }


def test_fixture_pcm_is_opt_in_and_verified_group_is_explicit(env):
    absent = env.observe()
    assert not absent.path.exists() and absent.pcm is absent.crop is None
    assert env.store.session(absent.sid)["identity_group"] == "default"
    env.activate()
    actual = env.observe(pcm=True, vector=True, group=GROUP)
    assert actual.path.stat().st_size == 128044
    assert actual.receipt.signature == signature(actual.path.stat())
    assert actual.receipt.digest == hashlib.sha256(actual.path.read_bytes()).hexdigest()
    assert actual.saved["packet"]["owned_pcm16le_sha256"] == hashlib.sha256(actual.pcm).hexdigest()
    span = actual.node["source_spans"][0]
    assert actual.crop == actual.pcm[span["source_start"] * 2:span["source_end"] * 2]
    assert len(actual.crop) == 64000
    assert actual.node["pcm16le_sha256"] == hashlib.sha256(actual.crop).hexdigest()
    assert actual.node["float32le_sha256"] == primary.standalone_float_digest(actual.crop)
    assert env.store.session(actual.sid)["identity_group"] == GROUP
    assert env.settings.start_worker is False and env.settings.speaker_support_enabled is False


def test_exact_crop_audio_and_manual_association_are_not_automatic_enrollment(env):
    env.activate()
    item = env.observe(pcm=True, vector=True)
    before = rows(env.store, "segments")
    association = env.associate(item)
    reviewed = voices.review_audio(env.store, item.sid, item.rid, item.observation_id)
    assert reviewed["authority"] == "exact_crop_review_only"
    assert (reviewed["sample_rate"], reviewed["channels"], reviewed["frames"]) == (16000, 1, 32000)
    assert reviewed["encoding"] == "pcm_s16le_base64"
    assert base64.b64decode(reviewed["audio"], validate=True) == item.crop
    assert reviewed["pcm16le_sha256"] == item.node["pcm16le_sha256"]
    assert association["active_positive_count"] == 0
    assert count(env.store, voices.TABLE) == count(env.store, "identity_active_reviews") == 0
    request = enrollment_request(env, item, association)
    event = voices.enroll(env.store, GROUP, request)
    assert event["action"] == "enroll_primary_voice" and event["active"] is True
    assert event["automatic_enrollment"] is False
    assert event["operator_assertion_is_claim"] is True
    stored = rows(env.store, voices.TABLE)[0]
    assert stored["association_event_id"] == association["event_id"]
    assert stored["certificate_id"] == item.certificate["certificate_id"]
    assert stored["vector_sha256"] == hashlib.sha256(item.certificate["vector"]).hexdigest()
    assert count(env.store, "identity_active_reviews") == count(env.store, "speaker_support") == 0
    assert rows(env.store, "segments") == before
    with env.store.db() as db:
        db.execute("BEGIN")
        vector = voices.reference_vector(db, stored)
    assert vector == original_vector(1)


@pytest.mark.parametrize("case", [
    "missing", "old-manual-attestation", "wrong-version", "extra-field",
    "unreviewed", "mixed", "overlap", "integer-reviewed", "integer-single", "integer-overlap",
])
def test_enrollment_requires_exact_explicit_strict_boolean_attestation(env, case):
    env.activate()
    item = env.observe(pcm=True, vector=True)
    claim = env.associate(item)
    request = enrollment_request(env, item, claim)
    if case == "missing":
        request.pop("attestation")
    elif case == "old-manual-attestation":
        request["attestation"] = deepcopy(bridge.ATTESTATION)
    elif case == "wrong-version":
        request["attestation"]["version"] = "future-or-implicit"
    elif case == "extra-field":
        request["attestation"]["automatic"] = True
    else:
        field = {
            "unreviewed": "reviewed_exact_crop", "mixed": "single_identified_voice",
            "overlap": "no_other_overlapping_voice", "integer-reviewed": "reviewed_exact_crop",
            "integer-single": "single_identified_voice", "integer-overlap": "no_other_overlapping_voice",
        }[case]
        request["attestation"][field] = 1 if case.startswith("integer-") else False
    before = snapshot(env)
    assert_refused(lambda: voices.enroll(env.store, GROUP, request))
    assert snapshot(env) == before


@pytest.mark.parametrize("field", [
    "association_event_id", "binding_sha256", "certificate_id", "vector_sha256", "space_id",
])
def test_enrollment_fences_every_original_evidence_identifier(env, field):
    env.activate()
    item = env.observe(pcm=True, vector=True)
    claim = env.associate(item)
    request = enrollment_request(env, item, claim)
    request[field] = "f" * (32 if field == "association_event_id" else 64)
    before = snapshot(env)
    assert_refused(lambda: voices.enroll(env.store, GROUP, request))
    assert snapshot(env) == before


@pytest.mark.parametrize("case", ["missing-file", "replaced-file", "retired", "cancelled", "no-vector"])
def test_enrollment_requires_current_exact_retained_audio_and_original_vector(env, case):
    env.activate()
    item = env.observe(pcm=True, vector=case != "no-vector")
    claim = env.associate(item)
    request = enrollment_request(env, item, claim)
    if case == "missing-file":
        item.path.unlink()
    elif case == "replaced-file":
        replacement = item.path.with_suffix(".replacement")
        replacement.write_bytes(item.path.read_bytes())
        replacement.replace(item.path)
    elif case == "retired":
        assert env.retire(item)["acknowledged"] is True
    elif case == "cancelled":
        with env.store.db() as db:
            db.execute("BEGIN")
            db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (item.cid,))
    before = snapshot(env)
    assert_refused(lambda: voices.enroll(env.store, GROUP, request))
    assert snapshot(env) == before


@pytest.mark.parametrize("which_hash", ["pcm16le_sha256", "float32le_sha256"])
def test_audio_rechecks_exact_crop_digests_after_owned_file_validation(env, which_hash):
    env.activate()
    item = env.observe(pcm=True, vector=True)
    with env.store.db() as db:
        db.execute("BEGIN")
        bound = bridge._binding(db, item.sid, item.rid, item.observation_id)
        forged = deepcopy(bound)
        forged["binding"]["physical_crop"][which_hash] = "f" * 64
        assert_refused(
            lambda: voices._audio(env.store, db, forged),
            code="primary_audio_binding_changed")


def test_original_certificate_hash_is_checked_not_only_metadata_binding(env):
    env.activate()
    item = env.observe(pcm=True, vector=True)
    claim = env.associate(item)
    request = enrollment_request(env, item, claim)
    overwrite_certificate(env, item, vector=struct.pack("<512f", *original_vector(0, 1)))
    before = snapshot(env)
    assert_refused(
        lambda: voices.enroll(env.store, GROUP, request),
        status=503, code="primary_vector_integrity")
    assert snapshot(env) == before


@pytest.mark.parametrize("case", ["short", "long", "text", "nan", "infinity", "zero"])
def test_certificate_requires_blob_exactly_512_finite_nonzero_float32_values(env, case):
    env.activate()
    item = env.observe(pcm=True, vector=True)
    with env.store.db() as db:
        db.execute("BEGIN")
        bound = deepcopy(bridge._binding(db, item.sid, item.rid, item.observation_id))
    if case == "short":
        raw = struct.pack("<511f", *([1.] + [0.] * 510))
    elif case == "long":
        raw = struct.pack("<513f", *([1.] + [0.] * 512))
    elif case == "text":
        raw = "x" * 2048
    else:
        first = {"nan": float("nan"), "infinity": float("inf"), "zero": 0.}[case]
        raw = struct.pack("<512f", first, *([0.] * 511))
    digest = hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()
    # This unit isolates byte/type/numeric screening after a metadata binding.
    # Full enrollment above separately proves unchanged metadata cannot hide new bytes.
    bound["binding"]["vector_sha256"] = digest
    # Normal certificate CHECKs also reject bad type/length at write time.
    # Use a four-column malformed-state unit database, not disabled live guards.
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute(f"""CREATE TABLE {history.CERTIFICATES}
            (observation_id TEXT,vector BLOB,certificate_id TEXT,vector_sha256 TEXT)""")
        db.execute(f"INSERT INTO {history.CERTIFICATES} VALUES(?,?,?,?)",
                   (item.observation_id, raw, item.certificate["certificate_id"], digest))
        assert_refused(
            lambda: voices._certificate(db, bound),
            status=503, code="primary_vector_integrity")
    assert count(env.store, voices.TABLE) == 0


def test_two_second_crop_cannot_bypass_longer_enrollment_policy(env):
    activate_policy(env, minimum_enrollment_seconds=2.5)
    item = env.observe(pcm=True, vector=True)
    claim = env.associate(item)
    request = enrollment_request(env, item, claim)
    before = snapshot(env)
    assert_refused(
        lambda: voices.enroll(env.store, GROUP, request),
        code="insufficient_primary_enrollment_duration")
    assert snapshot(env) == before


def test_exact_policy_boundary_enrolls_and_physical_alias_cannot_add_weight(env):
    env.activate()
    item, claim, request, event = enroll(env, physical="one-identical-crop")
    alias = env.observe(pcm=True, vector=True, physical="one-identical-crop")
    second_claim = env.associate(alias, person=claim["person_id"])
    assert item.receipt.digest == alias.receipt.digest
    assert item.crop == alias.crop
    before = snapshot(env)
    assert_refused(
        lambda: voices.enroll(env.store, GROUP, enrollment_request(env, alias, second_claim)),
        code="duplicate_primary_voice_reference")
    assert snapshot(env) == before
    assert rows(env.store, voices.TABLE)[0]["event_id"] == event["event_id"]


def test_exact_retry_and_retract_retry_never_resurrect_reference(env):
    env.activate()
    item, claim, request, event = enroll(env)
    before = snapshot(env)
    assert voices.enroll(env.store, GROUP, request) == dict(event, idempotent_replay=True)
    assert snapshot(env) == before
    changed = dict(request, vector_sha256="f" * 64)
    assert_refused(lambda: voices.enroll(env.store, GROUP, changed))
    retract_request = env.fence("retract-voice")
    retracted = voices.retract(env.store, GROUP, event["event_id"], retract_request)
    assert retracted["active"] is False
    assert count(env.store, voices.TABLE) == 0
    before = snapshot(env)
    assert voices.retract(env.store, GROUP, event["event_id"], retract_request) == retracted
    assert voices.enroll(env.store, GROUP, request) == dict(
        event, active=False, idempotent_replay=True)
    assert snapshot(env) == before
    assert count(env.store, CURRENT) == 1
    with env.store.db() as db:
        db.execute("BEGIN")
        assert voices.projection_intact(db, GROUP, event["generation"])


@pytest.mark.parametrize("action", ["retract-association", "correct-association"])
def test_manual_claim_retraction_or_correction_revokes_voice_without_transferring_it(env, action):
    env.activate()
    item, claim, request, event = enroll(env)
    if action == "retract-association":
        env.retract(claim)
        assert count(env.store, CURRENT) == 0
    else:
        changed = bridge.mutate(
            env.store, GROUP, env.request(item, name="Corrected human identity"),
            previous=claim["event_id"])
        assert changed["person_id"] != claim["person_id"]
        assert changed["active_positive_count"] == 0
    assert count(env.store, voices.TABLE) == 0
    revocations = [
        row for row in rows(env.store, "identity_review_events")
        if row["action"] == "revoke_primary_voice"]
    assert len(revocations) == 1 and revocations[0]["previous_event_id"] == event["event_id"]
    before = snapshot(env)
    assert voices.enroll(env.store, GROUP, request) == dict(
        event, active=False, idempotent_replay=True)
    assert snapshot(env) == before
    with env.store.db() as db:
        db.execute("BEGIN")
        assert voices.projection_intact(db, GROUP, event["generation"])


def test_erasure_removes_reference_history_and_durable_queries_without_resurrection(env):
    env.activate()
    item = env.observe(pcm=True, vector=True, group=GROUP)
    claim = env.associate(item)
    request = enrollment_request(env, item, claim)
    event = voices.enroll(env.store, GROUP, request)
    assert count(env.store, voices.SUGGESTIONS) == 1
    erase_request = env.erase_request()
    receipt = bridge.erase(env.store, GROUP, claim["event_id"], erase_request)
    assert_source_free_receipt(env.store, receipt)
    assert count(env.store, voices.TABLE) == count(env.store, voices.SUGGESTIONS) == 0
    assert count(env.store, "identity_review_events", "primary_observation_id=?",
                 (item.observation_id,)) == 0
    before = snapshot(env)
    assert bridge.erase(env.store, GROUP, claim["event_id"], erase_request) == receipt
    assert_refused(lambda: voices.enroll(env.store, GROUP, request))
    assert snapshot(env) == before
    restarted = Store(env.store.path, env.settings)
    assert voices.process_one(restarted) == {"processed": False}
    with restarted.db() as db:
        db.execute("BEGIN")
        assert voices.projection_intact(db, GROUP, event["generation"])


def test_source_revocation_tombstones_reference_but_pcm_retirement_does_not(env):
    env.activate()
    item, claim, request, event = enroll(env)
    assert env.retire(item)["acknowledged"] is True
    assert count(env.store, voices.TABLE) == 1
    with env.store.db() as db:
        db.execute("BEGIN")
        assert voices.reference_vector(db, rows(env.store, voices.TABLE)[0]) == original_vector(1)
        history.invalidate_sources(db, [item.cid], "synthetic_source_revocation")
    assert count(env.store, voices.TABLE) == 0
    assert count(env.store, "identity_review_events",
                 "action='revoke_primary_voice' AND previous_event_id=?", (event["event_id"],)) == 1
    before = snapshot(env)
    assert voices.enroll(env.store, GROUP, request) == dict(
        event, active=False, idempotent_replay=True)
    assert snapshot(env) == before


def test_source_delete_and_group_reset_remove_primary_state_without_replay(env):
    env.activate()
    item, claim, request, event = enroll(env)
    pending = query(env, original_vector(1))
    lifecycle.delete_session(env.store, item.sid, delete_file=lambda path: None)
    assert count(env.store, voices.TABLE) == 0
    assert count(env.store, "identity_review_events", "source_session_id=?", (item.sid,)) == 0
    assert_refused(lambda: voices.enroll(env.store, GROUP, request))
    reset_group(env.store, GROUP)
    assert count(env.store, voices.TABLE) == count(env.store, voices.SUGGESTIONS) == 0
    assert bridge.bank(env.store, GROUP)["generation"] != event["generation"]
    assert_refused(lambda: voices.enroll(env.store, GROUP, request), code="stale_generation")
    assert voices.process_one(env.store) == {"processed": False}


def test_certificate_commit_enqueues_once_without_scoring_and_survives_restart(env, monkeypatch):
    env.activate()
    calls = []
    original_enqueue = voices.enqueue
    def checked_enqueue(db, sid, rid, observation_id):
        assert db.in_transaction
        assert db.execute(
            f"SELECT 1 FROM {history.CERTIFICATES} WHERE observation_id=? AND evidence_kind='vector'",
            (observation_id,)).fetchone()
        calls.append(observation_id)
        original_enqueue(db, sid, rid, observation_id)
    def no_scoring(*args, **kwargs):
        raise AssertionError("Certificate admission must not perform matching")
    monkeypatch.setattr(voices, "enqueue", checked_enqueue)
    monkeypatch.setattr(voices, "_compute", no_scoring)
    item = query(env, original_vector(1))
    assert calls == [item.observation_id]
    assert count(env.store, voices.SUGGESTIONS) == 1
    with env.store.db() as db:
        db.execute("BEGIN")
        voices.enqueue(db, item.sid, item.rid, item.observation_id)
    assert count(env.store, voices.SUGGESTIONS) == 1
    restarted = Store(env.store.path, env.settings)
    assert voices.suggestion(restarted, item.sid, item.rid, item.observation_id)["status"] == "pending"
    assert rows(env.store, voices.SUGGESTIONS)[0]["state"] == "pending"


def test_optional_enqueue_failure_does_not_rollback_original_certificate(env, monkeypatch):
    env.activate()
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("Synthetic optional-intent failure")
    monkeypatch.setattr(voices, "enqueue", unavailable)
    item = query(env, original_vector(1))
    assert item.certificate["evidence_kind"] == "vector"
    assert len(item.certificate["vector"]) == 2048
    assert count(env.store, voices.SUGGESTIONS) == 0
    assert voices.suggestion(env.store, item.sid, item.rid, item.observation_id)["status"] == "unavailable"


def test_successful_suggestion_is_only_a_crop_scoped_suggestion_not_enrollment(env):
    env.activate()
    _, claim, _, event = enroll(env)
    item = query(env, original_vector(1))
    before = snapshot(env)
    restarted = Store(env.store.path, env.settings)
    assert voices.process_one(restarted) == {
        "processed": True, "status": "experimental_identity_match"}
    result = voices.suggestion(restarted, item.sid, item.rid, item.observation_id)
    assert result["person_id"] == claim["person_id"]
    assert result["authority"] == "automatic_suggestion"
    assert result["extent"] == "observed_crop_only" and result["automatic_enrollment"] is False
    assert result["reference_event_ids"] == [event["event_id"]]
    assert result["score"] == pytest.approx(1.)
    assert result["margin"] == pytest.approx(2.)
    after = snapshot(env)
    assert {key: value for key, value in after.items() if key != "suggestions"} == {
        key: value for key, value in before.items() if key != "suggestions"}
    assert voices.process_one(restarted) == {"processed": False}
    with env.store.db() as db, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("BEGIN")
        db.execute(f"UPDATE {voices.SUGGESTIONS} SET state='pending'")


def test_same_person_relation_combines_references_without_competing_with_itself(env):
    env.activate()
    _, first, _, a = enroll(env, vector=original_vector(1, 0))
    _, second, _, b = enroll(env, vector=original_vector(1, 0))
    relation = env.relation(first["person_id"], second["person_id"])
    item = query(env, original_vector(1, 0))
    assert voices.process_one(env.store)["status"] == "experimental_identity_match"
    result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
    with env.store.db() as db:
        db.execute("BEGIN")
        policy = bank.group_policy(db, GROUP)
        canonical = identity_canonical.load(db, GROUP, relation["generation"], policy)
    assert result["person_id"] == canonical["mapping"][first["person_id"]]
    assert canonical["mapping"][first["person_id"]] == canonical["mapping"][second["person_id"]]
    assert set(result["reference_event_ids"]) == {a["event_id"], b["event_id"]}
    assert result["margin"] == pytest.approx(2.)


@pytest.mark.parametrize("second_vector,expected", [
    (original_vector(1, 0), "unknown_ambiguous"),
    (original_vector(0, 1), "experimental_identity_match"),
])
def test_explicit_different_people_remain_competitors_and_require_margin(env, second_vector, expected):
    env.activate()
    _, first, _, _ = enroll(env, vector=original_vector(1, 0))
    _, second, _, _ = enroll(env, vector=second_vector)
    env.relation(first["person_id"], second["person_id"], kind="different_people")
    item = query(env, original_vector(1, 0))
    assert voices.process_one(env.store)["status"] == expected
    result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
    if expected == "unknown_ambiguous":
        assert result["person_id"] is None and result["margin"] == pytest.approx(0.)
    else:
        assert result["person_id"] == first["person_id"]
        assert result["margin"] == pytest.approx(1.)
    assert count(env.store, voices.TABLE) == 2


def test_query_below_threshold_and_longer_query_policy_abstain(env):
    activate_policy(env, minimum_query_seconds=2.5, minimum_enrollment_seconds=2.5)
    item = query(env, original_vector(1))
    assert voices.process_one(env.store)["status"] == "unknown_insufficient_speech"
    assert voices.suggestion(env.store, item.sid, item.rid, item.observation_id)["person_id"] is None
    # Exercise the shared numeric rule separately without bypassing enrollment.
    selected = bank.select_reviewed_score([(.5, "synthetic-person", ["synthetic-reference"])], POLICY)
    assert selected["status"] == "unknown_below_threshold" and selected["person_id"] is None


def test_incompatible_embedding_space_is_not_compared_or_enrolled(env, monkeypatch):
    env.activate()
    enroll(env)
    binding = deepcopy(next(iter(embedding_space._AUDITED_BINDINGS.values())))
    binding["model"]["sha256"] = "f" * 64
    binding_id = embedding_space._digest(binding)
    monkeypatch.setitem(embedding_space._AUDITED_BINDINGS, binding_id, binding)
    other_space = embedding_space._descriptor(
        binding_id, SimpleNamespace(provider="cpu", num_threads=2))
    item = env.observe(group=GROUP, vector=True, space=other_space)
    assert voices.process_one(env.store)["status"] == "unknown_embedding_space"
    result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
    assert result["status"] == "unknown_embedding_space" and result["person_id"] is None
    assert count(env.store, voices.TABLE) == 1


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("change", ["enrollment", "retraction", "availability"])
def test_pending_and_completed_suggestions_hide_stale_bank_revisions(env, completed, change):
    env.activate()
    _, claim, _, event = enroll(env)
    item = query(env, original_vector(1))
    if completed:
        assert voices.process_one(env.store)["status"] == "experimental_identity_match"
    if change == "enrollment":
        enroll(env, person=claim["person_id"])
    elif change == "retraction":
        voices.retract(env.store, GROUP, event["event_id"], env.fence())
    else:
        with env.store.db() as db:
            db.execute("BEGIN")
            db.execute(
                "UPDATE identity_group_policy SET availability=availability+1 WHERE identity_group=?",
                (GROUP,))
    result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
    assert result["status"] == "stale" and result["person_id"] is None
    if not completed:
        assert voices.process_one(env.store) == {"processed": True, "status": "stale"}
    # A new current-revision intent may be added, but never mutates the old one.
    old = rows(env.store, voices.SUGGESTIONS)[0]
    with env.store.db() as db:
        db.execute("BEGIN")
        voices.enqueue(db, item.sid, item.rid, item.observation_id)
    current = rows(env.store, voices.SUGGESTIONS)
    assert len(current) == 2 and current[0] == old and current[1]["state"] == "pending"
    assert voices.suggestion(env.store, item.sid, item.rid, item.observation_id)["status"] == "pending"


def test_corrupt_reference_abstains_without_rewriting_transcription_or_trust(env):
    env.activate()
    source, claim, _, _ = enroll(env)
    item = query(env, original_vector(1))
    overwrite_certificate(env, source, vector=struct.pack("<512f", *original_vector(0, 1)))
    before = snapshot(env)
    assert voices.process_one(env.store)["status"] == "unknown_reference_integrity"
    result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
    assert result["person_id"] is None
    assert result["status"] == "unknown_reference_integrity"
    after = snapshot(env)
    assert after["bridge"] == before["bridge"]
    assert after["references"] == before["references"]
    assert after["segments"] == before["segments"]


def test_durable_intents_are_bounded_and_never_evict_pending_work(env, monkeypatch):
    env.activate()
    monkeypatch.setattr(voices, "MAX_SUGGESTIONS", 2)
    one, two, three = [query(env, original_vector(1)) for _ in range(3)]
    queued = rows(env.store, voices.SUGGESTIONS)
    assert [row["observation_id"] for row in queued] == [one.observation_id, two.observation_id]
    assert three.certificate["evidence_kind"] == "vector"
    assert voices.process_one(env.store)["status"] == "unknown_no_reviewed_references"
    with env.store.db() as db:
        db.execute("BEGIN")
        voices.enqueue(db, three.sid, three.rid, three.observation_id)
    queued = rows(env.store, voices.SUGGESTIONS)
    assert len(queued) == 2
    assert [row["observation_id"] for row in queued] == [two.observation_id, three.observation_id]
    assert all(row["state"] == "pending" for row in queued)


def test_packet_overlap_abstains_even_when_the_selected_crop_is_clean(env):
    env.activate()
    enroll(env)
    item = env.observe(group=GROUP, vector=True, packet_overlap=True)
    assert item.node["source_spans"][0]["source_end"] <= 50000
    with env.store.db() as db:
        db.execute("BEGIN")
        assert db.execute(f"SELECT witness_count FROM {history.ATTEMPTS} WHERE token=?",
                          (item.reservation["token"],)).fetchone()[0] == 1
    assert voices.process_one(env.store)["status"] == "unknown_packet_overlap"
    result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
    assert result["status"] == "unknown_packet_overlap" and result["person_id"] is None
    assert count(env.store, voices.TABLE) == 1


def test_oversized_gallery_finishes_each_job_without_materializing_or_starving(env, monkeypatch):
    env.activate()
    enroll(env)
    enroll(env)
    one, two = [query(env, original_vector(1)) for _ in range(2)]
    monkeypatch.setattr(voices, "MAX_QUERY_REFERENCES", 1)
    def no_gallery_materialization(*args, **kwargs):
        raise AssertionError("Count ceiling must precede gallery materialization")
    monkeypatch.setattr(bank, "active_reference_rows", no_gallery_materialization)
    for item in (one, two):
        assert voices.process_one(env.store) == {
            "processed": True, "status": "unknown_gallery_capacity"}
        result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
        assert result["status"] == "unknown_gallery_capacity" and result["person_id"] is None
    assert all(row["state"] == "complete" for row in rows(env.store, voices.SUGGESTIONS))
    assert voices.process_one(env.store) == {"processed": False}


@pytest.mark.parametrize("restart", [False, True])
def test_only_durable_suggestion_work_reaches_real_manager_worker_fair_handoff(env, monkeypatch, restart):
    from audio_backend.moss_automatic import Automatic
    from audio_backend.moss_handy_jobs import MossJobs
    from audio_backend.native_execution_permit import NativeExecutionPermit
    from audio_backend.worker import Worker
    env.activate()
    _, claim, _, _ = enroll(env)
    item = query(env, original_vector(1))
    store = Store(env.store.path, env.settings) if restart else env.store
    def no_model_or_thread(*args, **kwargs):
        raise AssertionError("This test must not dispatch a model, process or thread")
    permit = NativeExecutionPermit(env.settings, sampler=no_model_or_thread)
    env.settings._native_execution_permit = permit
    jobs = MossJobs(
        store, SimpleNamespace(), permit, result_store=SimpleNamespace(),
        _driver_factory=no_model_or_thread, _thread_factory=no_model_or_thread,
        _probe_factory=no_model_or_thread)
    automatic = Automatic(store, jobs.results, jobs=jobs)
    jobs.automatic = automatic
    # Exercise real scheduling/handoff, without enabling acoustic/text dispatch.
    automatic.primary_only = True
    monkeypatch.setattr(automatic, "coordinator_current", lambda: True)
    monkeypatch.setattr(automatic, "current", lambda: False)
    worker = Worker(store, env.settings, analyzer_factory=no_model_or_thread)
    worker.moss_automatic = automatic
    assert jobs._primary_pending is jobs._live is jobs._pending_record is None
    assert voices.pending_hint(store)
    assert jobs.automatic_maintenance_hint()
    assert automatic.primary_yield_due()
    worker._automatic_handoff(request=True)
    result = voices.suggestion(store, item.sid, item.rid, item.observation_id)
    assert result["status"] == "experimental_identity_match"
    assert result["person_id"] == claim["person_id"]
    assert not voices.pending_hint(store) and not jobs.automatic_maintenance_hint()
    assert not worker.thread.is_alive()
    assert permit._optional is None and not permit._primary


def test_person_list_counts_references_without_repeated_gallery_materialization(env, monkeypatch):
    env.activate()
    _, first, _, _ = enroll(env)
    _, second, _, _ = enroll(env)
    def no_global_gallery(*args, **kwargs):
        raise AssertionError("Person summaries must use indexed counts, not entire galleries")
    monkeypatch.setattr(bank, "active_reference_rows", no_global_gallery)
    listing = bridge.persons(env.store, GROUP, limit=2)
    assert {row["person_id"] for row in listing["items"]} == {
        first["person_id"], second["person_id"]}
    assert all(row["active_positive_count"] == 1 for row in listing["items"])


def test_reference_retraction_keeps_reserved_slot_at_event_capacity(env, monkeypatch):
    env.activate()
    _, _, _, event = enroll(env)
    with env.store.db() as db:
        db.execute("BEGIN")
        usage = count(env.store, "identity_review_events") + bank.active_reservations(db)
    monkeypatch.setattr(bank, "MAX_EVENTS", usage)
    retracted = voices.retract(env.store, GROUP, event["event_id"], env.fence())
    assert retracted["active"] is False
    assert count(env.store, voices.TABLE) == 0


@pytest.mark.parametrize("limit", ["per-person", "global"])
def test_legacy_and_primary_positive_references_share_one_capacity(limit, monkeypatch):
    """Two-row unit gallery only; no fabricated reference is used for scoring."""
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE identity_review_events(id TEXT)")
        db.execute("""CREATE TABLE identity_active_reviews
            (event_id TEXT,identity_group TEXT,kind TEXT,person_id TEXT,evidence_key TEXT)""")
        db.execute(voices.DDL[voices.TABLE])
        db.execute("INSERT INTO identity_review_events VALUES('legacy'),('primary')")
        db.execute("INSERT INTO identity_active_reviews VALUES('legacy',?,'positive','person','legacy-crop')",
                   (GROUP,))
        db.execute(f"INSERT INTO {voices.TABLE} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("primary", "association", GROUP, "generation", "person",
                    "source-session", "recording", "observation", "certificate", "vector",
                    "binding", "space", "primary-crop", "source-version", "primary-evidence"))
        canonical = {"identity_group": GROUP, "mapping": {"person": "person", "other": "other"}}
        references = bank.active_reference_rows(db, "positive", GROUP)
        assert {row["reference_type"] for row in references} == {"legacy", "primary"}
        assert bank.active_reservations(db) == 2
        monkeypatch.setattr(bank, "MAX_PER_PERSON" if limit == "per-person" else "MAX_POSITIVES", 2)
        target = "person" if limit == "per-person" else "other"
        with pytest.raises(OverflowError):
            bank._capacity(db, "positive", target, canonical=canonical)
        replacing = next(row for row in references if row["reference_type"] == "legacy")
        bank._capacity(db, "positive", "person", replacing=replacing, canonical=canonical)


def test_same_person_union_enforces_combined_primary_capacity(env, monkeypatch):
    env.activate()
    _, first, _, _ = enroll(env)
    _, second, _, _ = enroll(env)
    monkeypatch.setattr(bank, "MAX_PER_PERSON", 1)
    before = snapshot(env)
    assert_refused(lambda: env.relation(first["person_id"], second["person_id"]))
    assert snapshot(env) == before


@pytest.mark.parametrize("first,legacy_kind", [
    ("primary", "positive"), ("primary", "negative"),
    ("legacy", "positive"), ("legacy", "negative"),
])
def test_real_legacy_and_primary_enrollment_entry_paths_reject_shared_audio_conflicts(env, first, legacy_kind):
    env.activate()
    item = env.observe(pcm=True, vector=True)
    claim = env.associate(item)
    legacy = legacy_support_from_crop(env, item)
    with env.store.db() as db:
        db.execute("BEGIN")
        bound = bridge._binding(db, item.sid, item.rid, item.observation_id)
    assert voices.acoustic_key(bound) == legacy.vector["evidence_key"]
    if first == "primary":
        voices.enroll(env.store, GROUP, enrollment_request(env, item, claim))
        request = legacy_request(env, legacy, claim["person_id"], kind=legacy_kind)
        before = snapshot(env)
        with pytest.raises(ValueError, match="duplicate_or_conflicting|duplicate_or_contradictory"):
            bank.mutate(env.store, legacy.sid, request)
    else:
        bank.mutate(env.store, legacy.sid,
                    legacy_request(env, legacy, claim["person_id"], kind=legacy_kind))
        request = enrollment_request(env, item, claim)
        before = snapshot(env)
        assert_refused(lambda: voices.enroll(env.store, GROUP, request))
    assert snapshot(env) == before
    assert count(env.store, voices.TABLE) + count(env.store, "identity_active_reviews") == 1


def test_query_exact_legacy_negative_veto_is_not_overridden_by_high_positive_score(env):
    env.activate()
    _, claim, _, _ = enroll(env)
    item = env.observe(pcm=True, vector=True, group=GROUP)
    legacy = legacy_support_from_crop(env, item)
    negative = bank.mutate(
        env.store, legacy.sid, legacy_request(env, legacy, claim["person_id"], kind="negative"))
    with env.store.db() as db:
        db.execute("BEGIN")
        voices.enqueue(db, item.sid, item.rid, item.observation_id)
    assert voices.process_one(env.store)["status"] == "stale"
    assert voices.process_one(env.store)["status"] == "unknown_exact_negative"
    result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
    assert result["status"] == "unknown_exact_negative" and result["person_id"] is None
    assert result["candidate_person_id"] == claim["person_id"]
    assert result["score"] == pytest.approx(1.)
    assert result["negative_event_id"] == negative["event_id"]
    assert count(env.store, voices.TABLE) == count(env.store, "identity_active_reviews") == 1


def test_primary_reference_guard_is_distinct_from_old_dev61_shape_and_survives_restart(env):
    """Exact-schema fence only; not an unmodified old-binary startup test."""
    env.activate()
    enroll(env)
    with env.store.db() as db:
        db.execute("BEGIN")
        guard = db.execute("SELECT sql FROM sqlite_master WHERE name=?",
                           (history.BRIDGE_ERASE_GUARD,)).fetchone()[0]
        old_expected = {**history.DDL, **history.BRIDGE_DDL}
        actual = history._schema_sql(db, old_expected)
    assert guard == history.REFERENCE_BRIDGE_GUARD
    assert guard != history.BRIDGE_DDL[history.BRIDGE_ERASE_GUARD]
    assert not history._same_schema(actual, old_expected)
    restarted = Store(env.store.path, env.settings)
    with restarted.db() as db:
        db.execute("BEGIN")
        assert db.execute("SELECT sql FROM sqlite_master WHERE name=?",
                          (history.BRIDGE_ERASE_GUARD,)).fetchone()[0] == guard
        assert db.execute(f"SELECT COUNT(*) FROM {voices.TABLE}").fetchone()[0] == 1


@pytest.mark.parametrize("existing,kind,target,expected", [
    ("legacy-positive", "positive", "person", "duplicate_or_conflicting_positive_evidence"),
    ("legacy-positive", "positive", "other", "duplicate_or_conflicting_positive_evidence"),
    ("legacy-negative", "positive", "alias", "duplicate_or_contradictory_review"),
    ("primary-positive", "positive", "other", "duplicate_or_conflicting_positive_evidence"),
    ("primary-positive", "negative", "alias", "duplicate_or_contradictory_review"),
])
def test_shared_exact_acoustic_conflict_guard_covers_both_storage_orders(existing, kind, target, expected):
    """The common mutation guard, on three metadata fields rather than faux audio."""
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE identity_active_reviews
            (event_id TEXT,identity_group TEXT,person_id TEXT,kind TEXT,evidence_key TEXT)""")
        db.execute(f"""CREATE TABLE {voices.TABLE}
            (event_id TEXT,identity_group TEXT,person_id TEXT,evidence_key TEXT)""")
        if existing.startswith("legacy"):
            db.execute("INSERT INTO identity_active_reviews VALUES('existing',?,'person',?,'same-audio')",
                       (GROUP, existing.split("-")[1]))
        else:
            db.execute(f"INSERT INTO {voices.TABLE} VALUES('existing',?,'person','same-audio')", (GROUP,))
        canonical = {"identity_group": GROUP,
                     "mapping": {"person": "person", "alias": "person", "other": "other"}}
        with pytest.raises(ValueError, match=expected):
            bank.check_evidence_conflicts(db, GROUP, kind, target, "same-audio", canonical)
        # Neither another person's negative nor different PCM is a duplicate.
        bank.check_evidence_conflicts(db, GROUP, "negative", "other", "same-audio", canonical)
        bank.check_evidence_conflicts(db, GROUP, kind, target, "different-audio", canonical)
        bank.check_evidence_conflicts(
            db, GROUP, kind, target, "same-audio", canonical, old={"event_id": "existing"})


@pytest.mark.parametrize("completed", [False, True])
def test_suggestion_reads_retained_generation_without_creating_or_writing(env, monkeypatch, completed):
    env.activate()
    _, claim, _, _ = enroll(env)
    item = query(env, original_vector(1))
    if completed:
        assert voices.process_one(env.store)["status"] == "experimental_identity_match"
    def no_generation_writer(*args, **kwargs):
        raise AssertionError("Read endpoint must not invoke creating generation helper")
    monkeypatch.setattr(speaker_support, "generation", no_generation_writer)
    before = snapshot(env)
    result = voices.suggestion(env.store, item.sid, item.rid, item.observation_id)
    assert result["status"] == ("experimental_identity_match" if completed else "pending")
    if completed:
        assert result["person_id"] == claim["person_id"]
    assert snapshot(env) == before


@pytest.mark.parametrize("change", ["missing", "replaced"])
def test_suggestion_missing_or_changed_generation_is_stale_without_creation(env, monkeypatch, change):
    env.activate()
    _, _, _, _ = enroll(env)
    item = query(env, original_vector(1))
    with env.store.db() as db:
        db.execute("BEGIN")
        if change == "missing":
            db.execute("DELETE FROM speaker_identity_generations WHERE identity_group=?", (GROUP,))
        else:
            db.execute("UPDATE speaker_identity_generations SET generation=? WHERE identity_group=?", ("f" * 32, GROUP))
    def no_generation_writer(*args, **kwargs):
        raise AssertionError("Read endpoint must not recreate missing generation")
    monkeypatch.setattr(speaker_support, "generation", no_generation_writer)
    assert voices.suggestion(env.store, item.sid, item.rid, item.observation_id)["status"] == "stale"
    with env.store.db() as db:
        db.execute("BEGIN")
        row = db.execute("SELECT generation FROM speaker_identity_generations WHERE identity_group=?", (GROUP,)).fetchone()
    assert row is None if change == "missing" else row[0] == "f" * 32
