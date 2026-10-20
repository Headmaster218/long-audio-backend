"""Finite synthetic fixtures for the reviewed-person bridge.

These helpers own a fresh real Store. Defaults remain metadata-only and never
create or read audio. Explicit pcm=True creates one four-second synthetic
PCM16 WAV only when a test executes. No helper loads a model, invokes a worker,
starts a server, or references an ambient database. Native admission uses an
exact fixture-owned capability, not evidence of acoustic correctness. Bridge
reads use no such capability. Each Fixture is bounded to six observations.
"""
from copy import deepcopy
import hashlib
import json
import struct
import threading
from types import SimpleNamespace
import uuid
import wave

import pytest

from audio_backend import change_feed, embedding_space, identity_canonical
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_primary_probe as primary
from audio_backend import recording_primary_provenance as provenance
from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_links as links
from audio_backend import reviewed_primary_persons as bridge
from audio_backend import speaker_support, transcript_contract, verified_identity
from audio_backend.native_owned_cell_store import Receipt
from audio_backend.native_owned_support_cache import ReadResponseBudget
from audio_backend.settings import Settings
from audio_backend.storage import Store


POLICY = {
    "schema_version": 1, "experimental": True, "single_threshold": .75,
    "centroid_threshold": .75, "margin": .1, "minimum_query_seconds": 2.,
    "minimum_enrollment_seconds": 2.,
}
GROUP = "explicit-human-bank"
RATE = 16000
FRAMES = 4 * RATE
CURRENT = "identity_primary_associations_v1"
RECEIPT_FIELDS = {
    "erasure_event_id", "target_event_id", "generation", "profile_revision", "status",
}


def sha(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def count(store, table, where="1", args=()):
    with store.db() as db:
        return db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


def rows(store, table):
    with store.db() as db:
        return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def immutable_snapshot(store):
    """Only tiny fixture tables; maximum six observations and twelve claims."""
    names = (
        "speakers", "identity_review_events", "identity_review_event_order",
        CURRENT, "identity_active_reviews", "identity_decisions",
        "identity_relation_events", "identity_group_policy",
    )
    return {name: rows(store, name) for name in names}


def activation_request(*, key="activate-bank", generation=None, revision=0):
    return {
        "expected_generation": generation, "expected_revision": revision,
        "idempotency_key": key, "mode": verified_identity.MODE,
        "policy_sha256": speaker_support.digest(speaker_support.canonical(POLICY)),
        "confirmed": True,
    }


class Fixture:
    def __init__(self, tmp_path):
        self.root = tmp_path
        policy_path = tmp_path / "synthetic-policy.json"
        policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
        self.settings = Settings(
            data_dir=tmp_path, start_worker=False, disk_reserve_bytes=0,
            recording_disk_reserve_bytes=0, reviewed_primary_persons_enabled=True,
            verified_identity_policy_file=policy_path,
        )
        (tmp_path / "uploads").mkdir()
        (tmp_path / "tmp").mkdir()
        self.store = Store(tmp_path / "jobs.sqlite3", self.settings)
        self.store._reviewed_person_response_budget = ReadResponseBudget()
        change_feed.initialize(self.store)
        self.history = history.HistoryStore(self.store)
        self.made = []
        self.admissions = {}
        self.owner = SimpleNamespace(
            _primary_admission_matches=lambda token, saved, node, invocation:
                self.admissions.get(token) == (history.encoded([saved, node]), invocation),
            _primary_success_matches=lambda reservation, node, vector, invocation:
                self.admissions.get(reservation["token"], (None, None))[1] == invocation,
            _history_source_quiescent=lambda version: True,
            cell_source=SimpleNamespace(owner=SimpleNamespace(
                # Retirement validates exact synthetic receipts, not any file.
                authority=SimpleNamespace(receipt_valid=self._receipt_valid),
                _publication_guard=threading.RLock(),
            )),
        )
        self.capability = self.history.bind_success_owner(self.owner)

    def _receipt_valid(self, receipt, budget):
        budget.check()
        return any(receipt == item.receipt for item in self.made)

    def activate(self, group=GROUP):
        return bridge.activate_bank(self.store, group, activation_request())

    def fence(self, key=None, group=GROUP):
        state = bridge.bank(self.store, group)
        return {
            "expected_generation": state["generation"],
            "expected_revision": state["profile_revision"],
            "idempotency_key": key or "request-" + uuid.uuid4().hex,
        }

    def request(self, item, *, person=None, name="Synthetic Person", key=None, group=GROUP):
        value = bridge.observation(self.store, item.sid, item.rid, item.observation_id)
        body = {
            **self.fence(key, group),
            "source_session_id": item.sid, "recording_id": item.rid,
            "observation_id": item.observation_id, "binding_sha256": value["binding_sha256"],
            "expected_source_authority_revision": value["source_authority_revision"],
            "expected_source_revision": value["source_revision"],
            "attestation": {
                "version": "human_exact_observation_person_claim_v1",
                "extent": "observed_crop_only", "confirmed": True,
            },
        }
        body["person_id" if person else "new_person_name"] = person or name
        return body

    def associate(self, item, *, person=None, name="Synthetic Person", group=GROUP):
        return bridge.mutate(self.store, group, self.request(item, person=person, name=name, group=group))

    def erase_request(self, *, group=GROUP):
        return {
            **self.fence(str(uuid.uuid4()), group),
            "scope": "all_claim_history_for_exact_observation", "confirmed": True,
        }

    def retract(self, event, *, group=GROUP):
        return bridge.mutate(self.store, group, self.fence(group=group),
                             previous=event["event_id"], retract=True)

    def relation(self, a, b, *, kind="same_person", group=GROUP):
        return bridge.relate(self.store, group, {
            **self.fence(group=group), "person_id": a, "other_person_id": b,
            "kind": kind, "confirmed": True,
        })

    def undo(self, event, *, group=GROUP):
        return bridge.relate(
            self.store, group, self.fence(group=group), previous=event["event_id"])

    def observe(self, *, parent=(.5, .625), physical=None, pending=False,
                vector=False, space=None, pcm=False, vector_values=None, group=None,
                packet_overlap=False):
        """At most six 4-second synthetic observations, one per recording.

        Unless pcm=True, source paths deliberately do not exist and hashes
        describe metadata-only fixture facts. PCM opt-in writes at most 128,044
        bytes per observation and binds real stat, file and exact-crop hashes.
        vector_values supplies an explicit synthetic original float32 vector.
        group opt-in requires an already activated verified bank; it never
        silently converts a legacy/default session. Real history admission and
        finalization create immutable certificates and indexed dependencies.
        packet_overlap adds one later, known overlapping label pair while the
        chosen two-second crop remains outside that pair's overlap.
        """
        assert len(self.made) < 6
        assert type(pcm) is bool
        assert vector_values is None or vector
        number = len(self.made) + 1
        session_args = {}
        if group is not None:
            with self.store.db() as db:
                policy = verified_identity.group_policy(db, group)
                assert policy and policy["mode"] == verified_identity.MODE
                assert policy["primary_associations_enabled"]
            session_args = {"identity_group": group, "identity_mode": verified_identity.MODE}
        sid = self.store.create_session(f"Synthetic source {number}", **session_args)["id"]
        rid, cid = f"{number:032x}", f"synthetic-source-{number}"
        stream = f"synthetic-stream-{number}"
        source_digest = sha(physical or f"source-{number}")
        path = self.root / "uploads" / f"{'synthetic' if pcm else 'absent'}-{number}.audio"
        assert not path.exists()
        owned_pcm = None
        if pcm:
            from audio_backend.native_owned_cell_capture import signature
            seed = int(sha(physical or f"source-{number}")[:8], 16)
            owned_pcm = b"".join(
                struct.pack("<h", (frame * 17 + seed) % 65536 - 32768)
                for frame in range(FRAMES))
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(RATE)
                out.writeframes(owned_pcm)
            source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            file_signature = signature(path.stat())
        else:
            file_signature = (1, number, 2 * FRAMES + 44, 3, 4)
        receipt = Receipt(sid, cid, source_digest, FRAMES, 44, 2 * FRAMES + 44,
                          file_signature, 0., stream)
        epoch = "a" * 32
        label = epoch + ":speaker_1"
        with self.store.db() as db:
            db.execute("""INSERT INTO recording_batches
                (id,session_id,capture_id,capture_origin_frame,state,created)
                VALUES(?,?,?,0,'completed',0)""", (rid, sid, f"capture-{number}"))
            db.execute("""INSERT INTO chunks
                (id,session_id,idempotency_key,digest,path,duration,timeline_start,
                 status,next_offset,created)
                VALUES(?,?,?,?,?,4.,0.,'completed',4.,0)""",
                (cid, sid, cid, source_digest, str(path)))
            db.execute("""INSERT INTO stream_chunks(chunk_id,session_id,stream_id,position)
                VALUES(?,?,?,0)""", (cid, sid, stream))
            db.execute("""INSERT INTO recording_work
                (chunk_id,batch_id,position,frames,bytes,capture_start_frame,
                 native_receipt_status,native_receipt,receipt_id,receipt_state,file_signature)
                VALUES(?,?,0,?,?,0,'durable',?,?,'durable',?)""",
                (cid, rid, FRAMES, receipt.size, receipt.encoded, receipt.receipt_id,
                 json.dumps(receipt.signature)))
            cursor = db.execute("""INSERT INTO segments
                (chunk_id,start,end,text,raw_text,asr_backend,timestamp_kind,
                 speaker_local_id,speaker_window_start,speaker_status)
                VALUES(?,?,?,'synthetic','synthetic','sensevoice','ctc',?,0.,'unknown')""",
                (cid, *parent, label))
            segment_id = cursor.lastrowid
            segment = dict(db.execute("SELECT * FROM segments WHERE id=?", (segment_id,)).fetchone())
        parent_ref = transcript_contract.primary_evidence(sid, segment)["source_ref"]
        commit = {
            "scope": {"session_id": sid, "kind": "stream", "key": stream,
                      "origin": (0.).hex(), "run": "1" * 32, "reset": 1,
                      "epoch": epoch, "index": 0},
            "part": {"session_id": sid, "chunk_id": cid, "receipt_sha256": receipt.receipt_id,
                     "source_sha256": source_digest, "source_frames": FRAMES,
                     "source_start": 0, "source_end": FRAMES,
                     "logical_start": 0, "logical_end": FRAMES},
            **{key: sha(key) for key in
               ("native_sha256", "cut_sha256", "evidence_sha256", "runtime_sha256")},
        }
        packet = {
            "schema": provenance.SCHEMA, "commit": commit,
            "query": [0, FRAMES, 0, FRAMES, FRAMES, 9, True],
            "owned_pcm16le_sha256": (
                hashlib.sha256(owned_pcm).hexdigest() if pcm else source_digest),
            "labels": [label],
            "turns": [[0, 0, 0, FRAMES, 0., 4.]],
            "units": [[0, 0, parent[0], parent[1], parent[0], 0, [0]]],
            "rows": [[0, [0]]], "refs": [[parent_ref, None]],
        }
        if packet_overlap:
            assert parent[0] < 3.095
            packet["labels"].append(epoch + ":speaker_2")
            packet["turns"].append([1, 1, 50000, 60000, 3.125, 3.75])
        raw = provenance.bounded(packet)
        digest, slot = hashlib.sha256(raw).hexdigest(), core.slot(commit)
        header = dict(sequence=number, sid=sid, rid=rid, cid=cid, core_start=0,
                      core_end=FRAMES, core_slot=slot, digest=digest)
        header["charge"] = provenance._charge(raw, sid, rid, cid, slot, digest)
        provenance.validate_packet(packet, header)
        with self.store.db() as db:
            db.execute(f"INSERT INTO {provenance.TABLE} VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (number, sid, rid, cid, 0, FRAMES, slot, digest, raw, header["charge"]))
        saved = {"header": header, "packet": packet}
        space = deepcopy(space or links.expected_space())
        node = primary.standalone_primary_metadata(saved, embedding_space.validate_space(space))
        crop = None
        if pcm:
            span = node["source_spans"][0]
            crop = owned_pcm[2 * span["source_start"]:2 * span["source_end"]]
            assert len(crop) == 64000
            node.update(pcm16le_sha256=hashlib.sha256(crop).hexdigest(),
                        float32le_sha256=primary.standalone_float_digest(crop))
        else:
            node.update(pcm16le_sha256=sha(physical or f"crop-{number}"),
                        float32le_sha256=sha("float-" + (physical or f"crop-{number}")))
        token, invocation = f"{number:032x}", f"{number + 100:032x}"
        self.admissions[token] = (history.encoded([saved, node]), invocation)
        old_policy = self.settings.recording_primary_observation_policy
        self.settings.recording_primary_observation_policy = history.POLICY
        try:
            reservation = self.history.claim_primary(
                sid, rid, token, saved, node, receipt.encoded, space=space,
                current_check=lambda db: db.execute(
                    "SELECT 1 FROM chunks WHERE id=? AND digest=? AND status='completed'",
                    (cid, source_digest)).fetchone() is not None,
                admission_capability=self.capability, invocation_id=invocation)
            if vector:
                self.history.append_primary(
                    reservation, node,
                    [1.] + [0.] * 511 if vector_values is None else vector_values,
                    invocation,
                    success_capability=self.capability, current_check=lambda db: True)
            elif not pending:
                self.history.finish_primary(reservation, "probe_failed")
        finally:
            self.settings.recording_primary_observation_policy = old_policy
        with self.store.db() as db:
            cert = dict(db.execute(f"SELECT * FROM {history.CERTIFICATES} WHERE attempt=?",
                                   (token,)).fetchone())
            source = dict(db.execute(f"SELECT * FROM {history.SOURCES} WHERE chunk_id=?",
                                     (cid,)).fetchone())
        item = SimpleNamespace(
            sid=sid, rid=rid, cid=cid, path=path, receipt=receipt, saved=saved, node=node,
            observation_id=cert["observation_id"], certificate=cert, source=source,
            segment_id=segment_id, parent_ref=parent_ref, reservation=reservation,
            pcm=owned_pcm, crop=crop,
        )
        self.made.append(item)
        return item

    def retire(self, item):
        prepared = self.history.prepare_retirement(item.sid, item.rid, item.source["version"])
        for _ in range(16):
            if self.history.retirement_page(prepared)["complete"]:
                break
        else:
            raise AssertionError("One-observation retirement exceeded its finite 16-page fixture bound")
        return self.history.commit_retirement(prepared)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "_AUTHORITY_UNKNOWN_DATABASES", {})
    monkeypatch.setattr(history, "_AUTHORITY_UNKNOWN_BYTES", 0)
    monkeypatch.setattr(history, "_AUTHORITY_UNKNOWN_OVERFLOW", False)
    value = Fixture(tmp_path)
    yield value
    budget = value.store._reviewed_person_response_budget
    assert budget._bridge_operation_count == budget._bridge_operation_bytes == 0
    assert budget._lease_count == budget._lease_bytes == 0


def assert_refused(call, *, status=None, code=None):
    with pytest.raises(bridge.BridgeError) as captured:
        call()
    if status is not None:
        assert captured.value.status == status
    if code is not None:
        assert captured.value.code == code
    return captured.value


def assert_source_free_receipt(store, receipt):
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt["status"] == "erased"
    with store.db() as db:
        row = dict(db.execute("SELECT * FROM identity_review_events WHERE id=?",
                              (receipt["erasure_event_id"],)).fetchone())
    assert row["action"] == "erase_primary"
    assert row["operator_context"] == "local_api_association_erasure"
    assert row["previous_event_id"] == receipt["target_event_id"]
    for key in ("person_id", "support_id", "vector_id", "evidence_key", "fingerprints",
                "attestation", "prior_prediction", "review_kind", "source_session_id",
                "source_recording_id", "primary_observation_id"):
        assert row[key] is None
    assert uuid.UUID(row["idempotency_key"]).version == 4
    assert str(uuid.UUID(row["idempotency_key"])) == row["idempotency_key"]
    assert len(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()) <= 1024
