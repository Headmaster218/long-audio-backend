"""Explicit larger, model-free resource nodes; never part of an implicit suite.

The two full-capacity nodes each own one fresh Store with exactly 1,024 people
and at most 4,096 valid relation events. They contain no recordings, observations,
audio, vectors, model runtime, subprocess, or external database. Other nodes in
this module use the ordinary small fixture and at most one actual ASGI worker
thread plus the event-loop thread. Execution needs the separately reviewed
resource proposal; authoring/AST inspection is not qualification.
"""
from copy import deepcopy
import asyncio
import json
import threading
import time
import uuid

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from audio_backend import identity_canonical, speaker_support, verified_identity
from audio_backend import reviewed_primary_persons as bridge
from audio_backend import reviewed_primary_persons_http as http
from audio_backend import native_owned_support_cache as leases
from audio_backend.app import _NativeSupportReadLifetime, _assign_native_read_lease
from reviewed_person_bridge_fixtures import GROUP, env


def _full_relation_ledger(env, *, deep_component):
    """Build legal synthetic rows directly, without 4,096 repeated full reloads.

    Every row has the exact production hash, request hash, response, generation
    and chain format. Max-length accepted unicode keys/operator contexts and
    names exercise materialized strings. The unchanged loader validates the
    complete ledger in the measured operation below.
    """
    activated = env.activate()
    generation = activated["generation"]
    people = [uuid.uuid4().hex for _ in range(1024)]
    if deep_component:
        actions = [("merge", people[0], people[1]), ("undo_merge", people[0], people[1])] * 1025
        actions += [("merge", people[index], people[index + 1]) for index in range(1023)]
        expected_rows, expected_active = 3073, 1023
    else:
        actions = [("merge", people[0], people[1]), ("undo_merge", people[0], people[1])] * 2048
        expected_rows, expected_active = 4096, 0
    assert len(actions) == expected_rows
    assert expected_rows + expected_active == identity_canonical.MAX_EVENTS == 4096
    assert verified_identity.MAX_PERSONS == len(people) == 1024
    head = ""
    prior = None
    with env.store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.executemany("""INSERT INTO speakers
            (id,session_id,identity_group,name,embedding,state,created)
            VALUES(?,'',?,?,NULL,'reviewed_person',0)""",
            ((person, GROUP, "\U0001f9ea" * 200) for person in people))
        for index, (action, source, target) in enumerate(actions):
            event_id = uuid.uuid4().hex
            sequence = index + activated["profile_revision"] + 1
            previous = prior if action == "undo_merge" else None
            key = chr(0x10000 + index) + "\U0001f9ea" * 127
            request = {"expected_generation": generation, "expected_revision": sequence - 1,
                       "idempotency_key": key}
            if action == "merge":
                request.update(kind="same_person", person_id=source,
                               other_person_id=target, confirmed=True)
            response = {
                "event_id": event_id, "action": action, "person_id": source,
                "other_person_id": target, "previous_event_id": previous,
                "generation": generation, "profile_revision": sequence,
                "operator_assertion_is_claim": True, "acoustic_support_certified": False,
            }
            event = {
                "id": event_id, "identity_group": GROUP, "generation": generation,
                "sequence": sequence, "action": action, "person_id": source,
                "other_person_id": target, "previous_event_id": previous,
                "idempotency_key": key,
                "request_sha256": speaker_support.digest(speaker_support.canonical(
                    {"request": request, "previous": previous})),
                "operator_context": "\U0001f9ea" * 128,
                "response": speaker_support.canonical(response).decode(),
                "created": float(index), "previous_sha256": head,
            }
            head = identity_canonical._hash(event)
            db.execute(f"""INSERT INTO identity_relation_events
                ({','.join(identity_canonical.FIELDS)},event_sha256)
                VALUES({','.join('?' for _ in range(len(identity_canonical.FIELDS) + 1))})""",
                (*[event[key] for key in identity_canonical.FIELDS], head))
            prior = event_id
        db.execute("""UPDATE identity_group_policy SET revision=?,relation_count=?,relation_head=?
            WHERE identity_group=?""", (sequence, expected_rows, head, GROUP))
    return people, generation, expected_rows, expected_active


def _exercise_real_materialized_loader(env, record_property, *, deep_component):
    people, generation, expected_rows, expected_active = _full_relation_ledger(
        env, deep_component=deep_component)
    budget = env.store._reviewed_person_response_budget
    assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
    assert budget._lease_count == budget._lease_bytes == 0
    assert bridge.READ_SECONDS == 0.75 and bridge.MAX_VM_STEPS == 250000
    record_property("relation_rows", expected_rows)
    record_property("reserved_active_relations", expected_active)
    record_property("vm_step_limit", bridge.MAX_VM_STEPS)
    record_property("vm_steps_observed", "not exposed by production budget")
    started = time.monotonic()
    try:
        # A real bridge operation owns the whole loader, SQL, validation and
        # serializer, without tracemalloc, spies, or changed product limits.
        # The separately admitted supervisor samples process-tree RSS.
        page = bridge.persons(env.store, GROUP, limit=32)
    except bridge.BridgeError as exc:
        record_property("refusal_code", exc.code)
        record_property("refusal_status", exc.status)
        raise
    finally:
        record_property("api_wall_seconds", time.monotonic() - started)
        assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
        assert budget._lease_count == budget._lease_bytes == 0
    assert len(page["items"]) == 32
    assert page["has_more"] and page["next_cursor"]
    assert len(json.dumps(page).encode()) <= 128 * 1024
    assert all(item["generation"] == generation for item in page["items"])
    if deep_component:
        assert {item["canonical_person_id"] for item in page["items"]} == {people[-1]}
    else:
        assert all(item["canonical_person_id"] == item["person_id"] for item in page["items"])


def test_full_1024_person_4096_relation_loader_fits_unchanged_capacity(env, record_property):
    _exercise_real_materialized_loader(env, record_property, deep_component=False)


def test_full_reserved_1024_person_deep_component_loader_fits_unchanged_capacity(env, record_property):
    _exercise_real_materialized_loader(env, record_property, deep_component=True)


def _endpoint(env, path="/identity-groups/{group}/speakers"):
    app = FastAPI()
    budget = env.store._reviewed_person_response_budget
    http.register_routes(app, env.store, _assign_native_read_lease, lambda: budget)
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def _scope(path, *, method="GET", query=b""):
    return {
        "type": "http", "http_version": "1.1", "method": method,
        "path": path, "raw_path": path.encode(), "query_string": query,
        "headers": [], "scheme": "http", "server": ("synthetic", 80),
        "client": ("synthetic", 1),
    }


def test_operation_lease_is_dynamic_nonblocking_and_separate_from_response_pool(env):
    budget = env.store._reviewed_person_response_budget
    assert leases.BRIDGE_OPERATION_BYTES == 32 * 1024**2
    assert leases.MAX_READ_BYTES <= 1536 * 1024
    assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
    operation = budget.reserve_bridge_operation()
    responses = []
    try:
        assert operation.reserved == 32 * 1024**2
        assert budget._bridge_operation_count == 1
        with pytest.raises(leases.ReadError) as unavailable:
            budget.reserve_bridge_operation()
        assert unavailable.value.status_code == 503
        assert unavailable.value.reason == "read_capacity"
        responses = [budget.reserve_jobs_response("result") for _ in range(2)]
        assert budget._lease_count == 2
        assert 0 < budget._lease_bytes <= leases.MAX_READ_BYTES
        assert budget._bridge_operation_bytes == 32 * 1024**2
        operation.release()
        operation.release()
        assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
        assert budget._lease_count == 2
    finally:
        operation.release()
        for response in responses:
            response.release()
    assert budget._lease_count == budget._lease_bytes == 0


def test_request_cancellation_keeps_operation_charged_until_real_worker_finishes(env, monkeypatch):
    env.activate()
    env.associate(env.observe())
    endpoint = _endpoint(env)
    budget = env.store._reviewed_person_response_budget
    release_worker = threading.Event()
    original_load = identity_canonical.load
    original_release = leases.BridgeOperationLease.release

    async def scenario():
        loop = asyncio.get_running_loop()
        entered, finished = asyncio.Event(), asyncio.Event()
        loop_thread = threading.get_ident()
        def blocked_load(*args, **kwargs):
            assert threading.get_ident() != loop_thread
            assert budget._bridge_operation_count == 1
            loop.call_soon_threadsafe(entered.set)
            assert release_worker.wait(2), "Finite synthetic worker hold expired"
            return original_load(*args, **kwargs)
        def observed_release(lease):
            original_release(lease)
            if lease.owner is budget:
                loop.call_soon_threadsafe(finished.set)
        monkeypatch.setattr(identity_canonical, "load", blocked_load)
        monkeypatch.setattr(leases.BridgeOperationLease, "release", observed_release)
        scope = _scope(f"/identity-groups/{GROUP}/speakers")
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            raise AssertionError("Cancelled request must not finish a response")
        async def inner(scope, receive, send):
            response = await endpoint(Request(scope, receive), GROUP)
            await response(scope, receive, send)
        task = asyncio.create_task(_NativeSupportReadLifetime(inner)(scope, receive, send))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert budget._bridge_operation_bytes == 32 * 1024**2
            assert budget._lease_count == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert scope["_native_support_read_done"].is_set()
            assert budget._lease_count == budget._lease_bytes == 0
            assert budget._bridge_operation_count == 1
            assert budget._bridge_operation_bytes == 32 * 1024**2
            with pytest.raises(leases.ReadError) as refused:
                budget.reserve_bridge_operation()
            assert refused.value.status_code == 503
        finally:
            release_worker.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await asyncio.wait_for(finished.wait(), timeout=2)
        assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
        assert budget._lease_count == budget._lease_bytes == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("send_failure", [False, True], ids=["completed-send", "failed-send"])
def test_metadata_charge_covers_serializer_response_charge_covers_send(env, monkeypatch, send_failure):
    env.activate()
    env.associate(env.observe())
    endpoint = _endpoint(env)
    budget = env.store._reviewed_person_response_budget
    serialized = []
    original_encode = bridge._encode
    def observed_encode(value, maximum=bridge.MAX_REQUEST):
        if isinstance(value, dict) and "items" in value:
            assert budget._bridge_operation_count == 1
            assert budget._bridge_operation_bytes == 32 * 1024**2
            assert budget._lease_count == 1
            serialized.append(True)
        return original_encode(value, maximum)
    monkeypatch.setattr(bridge, "_encode", observed_encode)
    async def scenario():
        scope = _scope(f"/identity-groups/{GROUP}/speakers")
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
            assert budget._lease_count == 1
            assert 0 < budget._lease_bytes <= leases.MAX_READ_BYTES
            if send_failure:
                raise ConnectionError("synthetic transport closed")
        async def inner(scope, receive, send):
            response = await endpoint(Request(scope, receive), GROUP)
            assert response.status_code == 200
            await response(scope, receive, send)
        if send_failure:
            with pytest.raises(ConnectionError):
                await _NativeSupportReadLifetime(inner)(scope, receive, send)
        else:
            await _NativeSupportReadLifetime(inner)(scope, receive, send)
        assert serialized
        assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
        assert budget._lease_count == budget._lease_bytes == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("error", ["query", "response-capacity", "worker", "serialization"])
def test_operation_and_response_leases_release_on_each_error_path(env, monkeypatch, error):
    env.activate()
    budget = env.store._reviewed_person_response_budget
    held_responses = []
    if error == "response-capacity":
        held_responses = [budget.reserve_jobs_response("result") for _ in range(2)]
    if error == "worker":
        def failed(*args, **kwargs):
            raise bridge.BridgeError("synthetic_worker_unavailable", 503)
        monkeypatch.setattr(bridge, "persons", failed)
    if error == "serialization":
        original = bridge._encode
        def failed(value, maximum=bridge.MAX_REQUEST):
            if isinstance(value, dict) and "items" in value:
                raise bridge.BridgeError("synthetic_serialization_unavailable", 503)
            return original(value, maximum)
        monkeypatch.setattr(bridge, "_encode", failed)
    endpoint = _endpoint(env)
    async def scenario():
        scope = _scope(f"/identity-groups/{GROUP}/speakers",
                       query=b"unknown=1" if error == "query" else b"")
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            pass
        async def inner(scope, receive, send):
            response = await endpoint(Request(scope, receive), GROUP)
            assert response.status_code in (409, 503)
            await response(scope, receive, send)
        await _NativeSupportReadLifetime(inner)(scope, receive, send)
        assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
        assert budget._lease_count == len(held_responses)
    try:
        asyncio.run(scenario())
    finally:
        for response in held_responses:
            response.release()
    assert budget._lease_count == budget._lease_bytes == 0
