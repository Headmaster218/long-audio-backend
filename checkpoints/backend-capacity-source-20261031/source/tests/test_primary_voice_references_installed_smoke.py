"""One installed dev62 ASGI smoke, no listener or model/worker execution.

Two fresh synthetic observations, one retained four-second PCM16 WAV, two
passive app lifespans, and bounded direct model-free suggestion maintenance.
This checks API/lifecycle behavior, not real-speaker accuracy or crash recovery.
"""
import base64
from copy import deepcopy
import os
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

import audio_backend
from audio_backend.app import create_app
from audio_backend import primary_voice_references as voice, transcript_contract
from audio_backend.worker import Worker
from reviewed_person_bridge_fixtures import GROUP, activation_request, count, env


def test_installed_dev62_exact_crop_enrollment_suggestion_correction_retraction_and_erasure(env, monkeypatch, tmp_path):
    install_root = Path(os.environ["BRIDGE_INSTALL_ROOT"]).resolve()
    assert Path(audio_backend.__file__).resolve().is_relative_to(install_root)
    assert audio_backend.__version__ == "0.2.0rc3.dev62"
    assert env.settings.start_worker is False
    source = env.observe(pcm=True, vector=True)
    prefix = f"/identity-groups/{GROUP}"
    reference_url = prefix + "/primary-voice-references"
    source_url = f"/sessions/{source.sid}/recordings/{source.rid}/primary-observations/{source.observation_id}"
    audio_url = source_url + "/review-audio"
    env.settings.api_key = "synthetic-dev62-installed-smoke"
    headers = {"Authorization": "Bearer synthetic-dev62-installed-smoke"}

    def forbidden(*args, **kwargs):
        raise AssertionError("Passive installed smoke attempted to start a model worker")
    monkeypatch.setattr(Worker, "start", forbidden)

    def ok(client, method, path, *, body=None, private=True):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = client.request(method, path, **kwargs)
        assert response.status_code == 200, (path, response.status_code, response.text)
        if private:
            cache = response.headers.get("cache-control", "")
            assert "private" in cache and "no-store" in cache, (path, cache)
        return response

    def enrollment(claim):
        observed = env.request(source)
        certificate = source.certificate
        return {
            **env.fence(), "association_event_id": claim["event_id"],
            "binding_sha256": observed["binding_sha256"],
            "certificate_id": certificate["certificate_id"],
            "vector_sha256": certificate["vector_sha256"],
            "space_id": source.node["space_id"],
            "attestation": deepcopy(voice.ATTESTATION),
        }

    revision_evidence = []
    def revision_basis(sid, document):
        assert not env.settings.recording_speaker_links_enabled
        assert not env.settings.recording_speaker_history_read_enabled
        with env.store.db() as db:
            db.execute("BEGIN")
            session = db.execute("SELECT transcript_revision,timeline_end FROM sessions WHERE id=?", (sid,)).fetchone()
            change = db.execute("SELECT MAX(cursor) FROM result_changes WHERE session_id=?", (sid,)).fetchone()[0]
        basis = {"policy": document["policy_sha256"], "session": sid,
                 "primary_change_max": change or 0,
                 "transcript_revision": session[0], "timeline_end": session[1]}
        assert document["revision"] == transcript_contract.digest(basis)
        return basis

    def unchanged_content_with_bank_revision(client, sid, before, initial_basis, stage, expected_reason):
        after = ok(client, "GET", f"/sessions/{sid}/transcript", private=False).json()
        current_basis = revision_basis(sid, after)
        with env.store.db() as db:
            db.execute("BEGIN")
            changes = [dict(row) for row in db.execute(
                "SELECT cursor,entity,operation,data FROM result_changes WHERE session_id=? AND cursor>? ORDER BY cursor",
                (sid, initial_basis["primary_change_max"]))]
            policy = dict(db.execute("SELECT revision,availability FROM identity_group_policy WHERE identity_group=?", (GROUP,)).fetchone())
        record = {"stage": stage, "before": before, "after": after,
                  "before_revision_basis": initial_basis, "after_revision_basis": current_basis,
                  "causal_changes": changes, "bank_policy": policy,
                  "allowed_changed_fields": ["revision"]}
        revision_evidence.append(record)
        (tmp_path / "transcript-revision-evidence.json").write_text(json.dumps(revision_evidence, indent=2)+"\n")
        assert before["next_cursor"] is after["next_cursor"] is None
        assert {key for key in before if before[key] != after[key]} == {"revision"}
        assert dict(after, revision=before["revision"]) == before
        assert {key: value for key, value in current_basis.items() if key != "primary_change_max"} == {
            key: value for key, value in initial_basis.items() if key != "primary_change_max"}
        assert current_basis["primary_change_max"] > initial_basis["primary_change_max"]
        assert changes and all(row["entity"] in {"speakers", "identity_bank"} for row in changes)
        assert all(row["operation"] == "upsert" for row in changes)
        latest = changes[-1]
        assert latest["cursor"] == current_basis["primary_change_max"] and latest["entity"] == "identity_bank"
        event = json.loads(latest["data"])
        assert event["reason"] == expected_reason
        assert event["profile_revision"] == policy["revision"]
        assert event["availability_sequence"] == policy["availability"]

    with TestClient(create_app(env.settings)) as client:
        assert client.get(audio_url).status_code == 401
        assert client.post(reference_url, json={}).status_code == 401
        initialized = ok(
            client, "PUT", prefix + "/reviewed-person-bank",
            body=activation_request()).json()
        generation = initialized["generation"]
        before_source = ok(
            client, "GET", f"/sessions/{source.sid}/transcript", private=False).content
        claim = ok(
            client, "POST", prefix + "/primary-associations",
            body=env.request(source)).json()
        reviewed = ok(client, "GET", audio_url).json()
        assert reviewed["authority"] == "exact_crop_review_only"
        assert (reviewed["sample_rate"], reviewed["channels"], reviewed["frames"]) == (16000, 1, 32000)
        assert base64.b64decode(reviewed["audio"], validate=True) == source.crop
        assert reviewed["pcm16le_sha256"] == source.node["pcm16le_sha256"]
        assert count(env.store, voice.TABLE) == 0
        invalid = enrollment(claim)
        invalid["attestation"]["single_identified_voice"] = False
        refused = client.post(reference_url, json=invalid, headers=headers)
        assert refused.status_code == 409 and "no-store" in refused.headers["cache-control"]
        reference = ok(client, "POST", reference_url, body=enrollment(claim)).json()
        assert reference["active"] is True and reference["automatic_enrollment"] is False
        retract_url = reference_url + f"/{reference['event_id']}/retract"
        assert client.post(retract_url, json=env.fence()).status_code == 401

        future = env.observe(group=GROUP, vector=True)
        suggestion_url = (
            f"/sessions/{future.sid}/recordings/{future.rid}/primary-observations/"
            f"{future.observation_id}/identity-suggestion")
        assert client.get(suggestion_url).status_code == 401
        before_future = ok(
            client, "GET", f"/sessions/{future.sid}/transcript", private=False).json()
        future_basis = revision_basis(future.sid, before_future)
        assert ok(client, "GET", suggestion_url).json()["status"] == "pending"
        assert voice.process_one(client.app.state.store)["status"] == "experimental_identity_match"
        suggested = ok(client, "GET", suggestion_url).json()
        assert suggested["person_id"] == claim["person_id"]
        assert suggested["reference_event_ids"] == [reference["event_id"]]
        assert suggested["authority"] == "automatic_suggestion"
        assert suggested["extent"] == "observed_crop_only"
        assert suggested["automatic_enrollment"] is False
        assert count(env.store, voice.TABLE) == 1

        corrected = ok(
            client, "POST", prefix + f"/primary-associations/{claim['event_id']}/replace",
            body=env.request(source, name="Corrected synthetic person")).json()
        assert corrected["person_id"] != claim["person_id"]
        assert count(env.store, voice.TABLE) == 0
        assert ok(client, "GET", suggestion_url).json()["status"] == "stale"
        corrected_reference = ok(
            client, "POST", reference_url, body=enrollment(corrected)).json()
        with env.store.db() as db:
            db.execute("BEGIN")
            voice.enqueue(db, future.sid, future.rid, future.observation_id)
        assert voice.process_one(client.app.state.store)["status"] == "experimental_identity_match"
        assert ok(client, "GET", suggestion_url).json()["person_id"] == corrected["person_id"]
        assert ok(client, "GET", f"/sessions/{source.sid}/transcript", private=False).content == before_source
        unchanged_content_with_bank_revision(client, future.sid, before_future, future_basis,
            "after_correction_and_reenrollment", "primary_voice_reference_changed")

    # Persisted bank/reference state must not depend on the startup policy file.
    env.settings.verified_identity_policy = None
    env.settings.verified_identity_policy_file = None
    with TestClient(create_app(env.settings)) as restarted:
        assert ok(restarted, "GET", prefix + "/reviewed-person-bank").json()["generation"] == generation
        assert ok(restarted, "GET", suggestion_url).json()["person_id"] == corrected["person_id"]
        retracted = ok(
            restarted, "POST",
            reference_url + f"/{corrected_reference['event_id']}/retract",
            body=env.fence()).json()
        assert retracted["active"] is False
        stale = ok(restarted, "GET", suggestion_url).json()
        assert stale["status"] == "stale" and stale["person_id"] is None
        assert count(env.store, voice.TABLE) == 0
        ok(restarted, "POST", reference_url, body=enrollment(corrected))
        assert count(env.store, voice.TABLE) == 1
        ok(restarted, "DELETE", f"/sessions/{source.sid}", private=False)
        assert count(env.store, voice.TABLE) == 0
        assert count(env.store, "identity_review_events", "source_session_id=?", (source.sid,)) == 0
        assert restarted.get(audio_url, headers=headers).status_code == 404
        assert ok(restarted, "GET", suggestion_url).json()["person_id"] is None
        unchanged_content_with_bank_revision(restarted, future.sid, before_future, future_basis,
            "after_retraction_reenrollment_and_source_erasure", "primary_association_source_erased")

    origins = {
        name: Path(module.__file__).resolve()
        for name, module in sys.modules.items()
        if (name == "audio_backend" or name.startswith("audio_backend."))
        and getattr(module, "__file__", None)
    }
    assert origins and all(path.is_relative_to(install_root) for path in origins.values()), origins
