"""In-process ASGI boundary tests. No listener, worker, model, audio or child."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient

from audio_backend import fragment_http, reviewed_primary_persons as bridge
from audio_backend.app import create_app
from reviewed_person_bridge_fixtures import GROUP, activation_request, count, env


def test_http_auth_private_cache_strict_shapes_and_exact_crop_navigation(env):
    item = env.observe()
    env.settings.api_key = "synthetic-local-api-key"
    headers = {"Authorization": "Bearer synthetic-local-api-key"}
    with TestClient(create_app(env.settings)) as client:
        prefix = f"/identity-groups/{GROUP}"
        assert client.get(prefix + "/reviewed-person-bank").status_code == 401
        created = client.put(prefix + "/reviewed-person-bank", json=activation_request(), headers=headers)
        assert created.status_code == 200, created.text
        assert "private" in created.headers["cache-control"] and "no-store" in created.headers["cache-control"]
        observation_url = f"/sessions/{item.sid}/recordings/{item.rid}/primary-observations"
        observed = client.get(observation_url + "/" + item.observation_id, headers=headers)
        assert observed.status_code == 200, observed.text
        assert observed.json()["review_eligible"] is True
        body = env.request(item)
        bad = client.post(prefix + "/primary-associations", json={**body, "vector": [1., 0.]}, headers=headers)
        assert bad.status_code in (400, 409, 422)
        assert count(env.store, "identity_primary_associations_v1") == 0
        result = client.post(prefix + "/primary-associations", json=body, headers=headers)
        assert result.status_code == 200, result.text
        person = result.json()["person_id"]
        page = client.get(prefix + f"/speakers/{person}/occurrences", headers=headers)
        assert page.status_code == 200, page.text
        occurrence = page.json()["items"][0]
        assert occurrence["extent"] == "observed_crop_only"
        assert occurrence["parent_evidence_url"].startswith(f"/sessions/{item.sid}/transcript/evidence/")
        assert "no-store" in page.headers["cache-control"]
        assert len(page.content) <= 128 * 1024
        assert str(item.path) not in page.text and "speaker_1" not in page.text


def test_http_manual_events_are_not_acoustic_reviews_and_receipts_are_not_history(env):
    env.activate()
    item = env.observe()
    event = env.associate(item)
    owner_sid = env.store.create_session("bank-scoped acoustic reader", GROUP)["id"]
    with TestClient(create_app(env.settings)) as client:
        original = client.get(f"/sessions/{owner_sid}/identity-reviews/{event['event_id']}")
        assert original.status_code == 404
        acoustic = client.get(f"/sessions/{owner_sid}/identity-reviews")
        assert acoustic.status_code == 200
        assert event["event_id"] not in acoustic.text
        body = env.erase_request()
        url = f"/identity-groups/{GROUP}/primary-associations/{event['event_id']}"
        deleted = client.request("DELETE", url, json=body)
        assert deleted.status_code == 200, deleted.text
        assert client.request("DELETE", url, json=body).json() == deleted.json()
        assert client.get(url).status_code == 404
        history = client.get(f"/identity-groups/{GROUP}/primary-associations")
        assert history.status_code == 200
        assert deleted.json()["erasure_event_id"] not in history.text
        assert item.observation_id not in history.text
        assert "no-store" in deleted.headers["cache-control"]


def test_http_disabled_routes_do_not_weaken_existing_fragment_guard(env):
    env.settings.reviewed_primary_persons_enabled = False
    with TestClient(create_app(env.settings)) as client:
        assert client.get(f"/identity-groups/{GROUP}/reviewed-person-bank").status_code == 404
        assert client.put(f"/identity-groups/{GROUP}/reviewed-person-bank",
                          json=activation_request()).status_code == 404
        # Ordinary session creation rejects global identity options before it
        # needs a fragment provider. No provider is installed by this fixture.
        response = client.post("/sessions", json={
            "name": "fragment", "identity_group": GROUP,
            "identity_mode": "verified_review_v1", "processing_mode": "fragment_pcm16_v1",
        })
        assert response.status_code in (400, 409, 422)
        sid = env.store.create_session("synthetic fragment guard")["id"]
        with env.store.db() as db:
            db.execute("UPDATE sessions SET processing_mode='fragment_pcm16_v1' WHERE id=?", (sid,))
        for suffix, body in (
            ("identity-reviews", {
                "expected_generation": "a" * 32, "expected_revision": 0,
                "idempotency_key": "fragment-refused", "kind": "positive",
                "new_person_name": "Never create", "support_id": "a" * 32,
                "vector_id": "b" * 32, "manifest_sha256": "c" * 64,
                "vector_sha256": "d" * 64, "space_id": "e" * 64,
                "attestation": {"version": "all_pooled_support_single_identified_voice_v1",
                                "reviewed_all_pooled_support": True,
                                "single_identified_voice": True,
                                "no_other_overlapping_voice": True},
            }),
            ("identity-relations", {
                "expected_generation": "a" * 32, "expected_revision": 0,
                "idempotency_key": "fragment-relation-refused",
                "person_id": "a" * 32, "other_person_id": "b" * 32,
                "kind": "same_person", "confirmed": True,
            }),
        ):
            response = client.post(f"/sessions/{sid}/{suffix}", json=body)
            assert response.status_code == 409, response.text
            assert "fragment_pcm16_v1" in response.text


def test_http_bad_cursor_limit_and_internal_storage_failure_are_typed(env, monkeypatch):
    env.activate()
    event = env.associate(env.observe())
    with TestClient(create_app(env.settings), raise_server_exceptions=False) as client:
        prefix = f"/identity-groups/{GROUP}/speakers/{event['person_id']}/occurrences"
        for query, reason in (
            ("?limit=33", "invalid_page_limit"),
            ("?limit=0", "invalid_query"),
            ("?cursor=not-a-cursor", "invalid_cursor"),
        ):
            response = client.get(prefix + query)
            assert response.status_code == 409, (query, response.status_code, response.text)
            assert response.json() == {"detail": reason}, (query, response.text)
        def failed(*args, **kwargs):
            raise bridge.BridgeError("storage_unavailable", 503)
        monkeypatch.setattr(bridge, "occurrences", failed)
        unavailable = client.get(prefix)
        assert unavailable.status_code == 503
        assert "storage_unavailable" in unavailable.text
        assert "Traceback" not in unavailable.text
