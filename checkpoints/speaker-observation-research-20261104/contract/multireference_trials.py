"""Research-only, pure JSON trial planner. No audio, embeddings, scores or I/O.

A canonical JSON string is the immutable freeze boundary. Metadata is an
externally supplied claim; accepting it does not prove speech or identity purity.
"""
import hashlib
import json
import re
from datetime import date

MAX_BYTES = 4 * 1024 * 1024
MAX_OBSERVATIONS = 4096
MAX_PEOPLE = 256
MAX_SESSIONS = 4096
MAX_SOURCES = 4096
MAX_FRAME = (1 << 53) - 1
MAX_REFERENCES = 8
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
EXTERNAL_ORIGINS = {"external_review", "oracle_benchmark"}
ORIGINS = EXTERNAL_ORIGINS | {"native_prediction", "missing"}


def _fail(message):
    raise ValueError(message)


def _keys(value, required, where):
    if type(value) is not dict or set(value) != set(required.split()):
        _fail(f"{where}: expected exactly {required}")


def _token(value, where):
    if type(value) is not str or not IDENTIFIER.fullmatch(value):
        _fail(f"{where}: expected a bounded opaque identifier, not a path")


def _hash(value, where):
    if type(value) is not str or not HASH.fullmatch(value):
        _fail(f"{where}: expected lowercase SHA-256")


def _integer(value, lower, upper, where):
    if type(value) is not int or not lower <= value <= upper:
        _fail(f"{where}: expected integer in [{lower}, {upper}]")


def _enum(value, allowed, where):
    if type(value) is not str or value not in allowed:
        _fail(f"{where}: invalid value")


def _rows(value, maximum, where):
    if type(value) is not list or not 1 <= len(value) <= maximum:
        _fail(f"{where}: expected 1..{maximum} rows")


def _unique(rows, key, where):
    result = {}
    for row in rows:
        if row[key] in result:
            _fail(f"{where}: duplicate {key}")
        result[row[key]] = row
    return result


def _evidence(value, statuses, where):
    required = "origin evidence_id verified" + (" status" if statuses else "")
    _keys(value, required, where)
    _enum(value["origin"], ORIGINS, where + ".origin")
    if type(value["verified"]) is not bool:
        _fail(where + ".verified: expected boolean")
    if statuses:
        _enum(value["status"], statuses, where + ".status")
    if value["origin"] == "missing":
        if value["verified"] or value["evidence_id"] is not None:
            _fail(where + ": missing evidence cannot be verified or have an evidence ID")
        if statuses and value["status"] != "unknown":
            _fail(where + ": missing evidence must have unknown status")
    else:
        _token(value["evidence_id"], where + ".evidence_id")
    if value["origin"] == "native_prediction" and value["verified"]:
        _fail(where + ": native predictions are not external verification")


def _external(evidence):
    return evidence["origin"] in EXTERNAL_ORIGINS and evidence["verified"]


def _parse(raw):
    if type(raw) is not str or len(raw) > MAX_BYTES:
        _fail("manifest: expected bounded JSON text")
    try:
        if len(raw.encode("utf-8")) > MAX_BYTES:
            _fail("manifest: exceeds byte capacity")
    except UnicodeEncodeError as exc:
        raise ValueError("manifest: invalid Unicode") from exc

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                _fail("manifest: duplicate JSON key " + key)
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda value: _fail("nonfinite number: " + value))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("manifest: malformed JSON") from exc


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def freeze_manifest(raw):
    """Validate and return an immutable canonical JSON snapshot.

    Input has no paths, predictions to promote, scores or learned parameters.
    Unknown keys fail closed. Lists are canonically sorted before freezing.
    """
    m = _parse(raw)
    _keys(m, "schema_version protocol_id cohort content_hash_scheme space reference_count persons sessions sources observations", "manifest")
    _integer(m["schema_version"], 1, 1, "schema_version")
    _token(m["protocol_id"], "protocol_id")
    _enum(m["cohort"], {"new_prospective"}, "cohort")
    _enum(m["content_hash_scheme"], {"pcm_s16le_mono_v1"}, "content_hash_scheme")
    _keys(m["space"], "model_id frontend_id space_id model_sha256 frontend_config_sha256", "space")
    for key in ("model_id", "frontend_id", "space_id"):
        _token(m["space"][key], "space." + key)
    for key in ("model_sha256", "frontend_config_sha256"):
        _hash(m["space"][key], "space." + key)
    _integer(m["reference_count"], 2, MAX_REFERENCES, "reference_count")
    for key, maximum in (("persons", MAX_PEOPLE), ("sessions", MAX_SESSIONS),
                         ("sources", MAX_SOURCES), ("observations", MAX_OBSERVATIONS)):
        _rows(m[key], maximum, key)
    for person in m["persons"]:
        _keys(person, "person_id split enrollment", "person")
        _token(person["person_id"], "person_id")
        _enum(person["split"], {"C", "T"}, "person.split")
        _enum(person["enrollment"], {"known", "unknown"}, "person.enrollment")
    people = _unique(m["persons"], "person_id", "persons")
    for session in m["sessions"]:
        _keys(session, "session_id acquisition_order recorded_on declaration_origin evidence_id", "session")
        _token(session["session_id"], "session_id")
        _integer(session["acquisition_order"], 0, MAX_FRAME, "acquisition_order")
        _enum(session["declaration_origin"], EXTERNAL_ORIGINS, "session.declaration_origin")
        _token(session["evidence_id"], "session.evidence_id")
        recorded = session["recorded_on"]
        if recorded is not None:
            try:
                if type(recorded) is not str or date.fromisoformat(recorded).isoformat() != recorded:
                    _fail("recorded_on: expected YYYY-MM-DD or null")
            except (ValueError, TypeError) as exc:
                raise ValueError("recorded_on: expected YYYY-MM-DD or null") from exc
    sessions = _unique(m["sessions"], "session_id", "sessions")
    _unique(m["sessions"], "acquisition_order", "sessions")
    dated = [s["recorded_on"] for s in sorted(m["sessions"], key=lambda s: s["acquisition_order"])
             if s["recorded_on"] is not None]
    if dated != sorted(dated):
        _fail("recorded_on: supplied dates contradict declared chronology")
    for source in m["sources"]:
        _keys(source, "content_sha256 session_id sample_rate total_frames", "source")
        _hash(source["content_sha256"], "source.content_sha256")
        _token(source["session_id"], "source.session_id")
        if source["session_id"] not in sessions:
            _fail("source: unknown session")
        _integer(source["sample_rate"], 8000, 192000, "source.sample_rate")
        _integer(source["total_frames"], 1, MAX_FRAME, "source.total_frames")
    # Hash is the source identity: relabeling the same bytes as a new session
    # cannot evade overlap or query/reference separation checks.
    sources = _unique(m["sources"], "content_sha256", "sources")
    for obs in m["observations"]:
        _keys(obs, "observation_id person_id split role space_id source_content_sha256 start_frame end_frame crop_sha256 identity speech overlap", "observation")
        for key in ("observation_id", "person_id", "space_id"):
            _token(obs[key], "observation." + key)
        _enum(obs["split"], {"C", "T"}, "observation.split")
        _enum(obs["role"], {"reference", "query"}, "observation.role")
        if obs["person_id"] not in people:
            _fail("observation: unknown person")
        person = people[obs["person_id"]]
        if obs["split"] != person["split"]:
            _fail("observation: C/T person identity leakage")
        if obs["role"] == "reference" and person["enrollment"] == "unknown":
            _fail("observation: unknown people cannot provide enrollment")
        if obs["space_id"] != m["space"]["space_id"]:
            _fail("observation: model/frontend space mismatch")
        _hash(obs["source_content_sha256"], "observation.source_content_sha256")
        _hash(obs["crop_sha256"], "observation.crop_sha256")
        if obs["source_content_sha256"] not in sources:
            _fail("observation: unknown content source")
        source = sources[obs["source_content_sha256"]]
        _integer(obs["start_frame"], 0, source["total_frames"] - 1, "start_frame")
        _integer(obs["end_frame"], obs["start_frame"] + 1, source["total_frames"], "end_frame")
        if (obs["start_frame"] == 0 and obs["end_frame"] == source["total_frames"]
                and obs["crop_sha256"] != obs["source_content_sha256"]):
            _fail("observation: full-source crop hash must equal source hash")
        _evidence(obs["identity"], None, "identity")
        _evidence(obs["speech"], {"speech", "non_speech", "unknown"}, "speech")
        _evidence(obs["overlap"], {"single_speaker", "overlap", "unknown"}, "overlap")
    _unique(m["observations"], "observation_id", "observations")
    _unique(m["observations"], "crop_sha256", "observations")
    for obs in m["observations"]:
        if obs["crop_sha256"] in sources:
            source = sources[obs["source_content_sha256"]]
            if (obs["crop_sha256"] != obs["source_content_sha256"] or obs["start_frame"] != 0
                    or obs["end_frame"] != source["total_frames"]):
                _fail("observations: cross-namespace source/crop content alias")
    intervals = {}
    for obs in m["observations"]:
        intervals.setdefault(obs["source_content_sha256"], []).append((obs["start_frame"], obs["end_frame"]))
    for spans in intervals.values():
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
            _fail("observations: overlapping source frame spans")
    session_splits = {}
    for obs in m["observations"]:
        sid = sources[obs["source_content_sha256"]]["session_id"]
        session_splits.setdefault(sid, set()).add(obs["split"])
    if any(len(splits) > 1 for splits in session_splits.values()):
        _fail("observations: C/T source-session leakage")
    for key, identity in (("persons", "person_id"), ("sessions", "session_id"),
                          ("sources", "content_sha256"), ("observations", "observation_id")):
        m[key].sort(key=lambda row: row[identity])
    return _canonical(m)


def plan_trials(frozen_manifest):
    """Return an immutable JSON plan; never mutate a bank or compute a score.

    Requires freeze_manifest's exact canonical result. Every observation is
    retained in the embedded manifest and ledger; every query remains counted.
    """
    canonical = freeze_manifest(frozen_manifest)
    if canonical != frozen_manifest:
        _fail("plan_trials requires the canonical freeze_manifest result")
    m = json.loads(canonical)
    sources = {s["content_sha256"]: s for s in m["sources"]}
    sessions = {s["session_id"]: s for s in m["sessions"]}
    people = {p["person_id"]: p for p in m["persons"]}
    observations = m["observations"]

    def session_id(obs):
        return sources[obs["source_content_sha256"]]["session_id"]

    def chronology(obs):
        source = sources[obs["source_content_sha256"]]
        # Frame positions compare only within one content source. Cross-source
        # same-session ties are deterministic, not claims of capture chronology.
        return (sessions[source["session_id"]]["acquisition_order"],
                source["content_sha256"], obs["start_frame"], obs["end_frame"], obs["observation_id"])

    query_sessions = {session_id(o) for o in observations if o["role"] == "query"}
    cutoffs = {}
    for split in ("C", "T"):
        query_dates = [sessions[session_id(o)]["recorded_on"] for o in observations
                       if o["role"] == "query" and o["split"] == split]
        cutoffs[split] = min(query_dates) if query_dates and all(query_dates) else None
    ledger = {}
    for obs in observations:
        reasons = []
        if not _external(obs["identity"]):
            reasons.append("identity_not_externally_verified")
        for kind, clean in (("speech", "speech"), ("overlap", "single_speaker")):
            if not _external(obs[kind]):
                reasons.append(kind + "_not_externally_verified")
            if obs[kind]["status"] != clean:
                reasons.append(kind + "_not_clean")
        if obs["role"] == "reference":
            reference_date = sessions[session_id(obs)]["recorded_on"]
            cutoff = cutoffs[obs["split"]]
            if reference_date is None or cutoff is None:
                reasons.append("reference_query_chronology_unverified")
            elif reference_date >= cutoff:
                reasons.append("reference_not_before_earliest_query_date")
            if session_id(obs) in query_sessions:
                reasons.append("session_shared_with_query")
        ledger[obs["observation_id"]] = {
            "observation_id": obs["observation_id"], "role": obs["role"],
            "person_id": obs["person_id"], "split": obs["split"],
            "session_id": session_id(obs), "selected_for": [], "reasons": reasons,
            "externally_verified_speech": _external(obs["speech"]),
            "externally_verified_overlap": _external(obs["overlap"]),
            "query_condition": ("not_a_query" if obs["role"] != "query" else
                "non_speech" if obs["speech"]["status"] == "non_speech" else
                "overlap" if obs["overlap"]["status"] == "overlap" else
                "declared_single_speaker_speech" if _external(obs["speech"]) and _external(obs["overlap"])
                    and obs["speech"]["status"] == "speech" and obs["overlap"]["status"] == "single_speaker"
                else "unverified_or_unknown")}
    enrollments = []
    for person in m["persons"]:
        if person["enrollment"] != "known":
            continue
        candidates = sorted((o for o in observations if o["role"] == "reference"
                             and o["person_id"] == person["person_id"]
                             and not ledger[o["observation_id"]]["reasons"]), key=chronology)
        distinct = []
        seen_sessions = set()
        for obs in candidates:
            if session_id(obs) in seen_sessions:
                ledger[obs["observation_id"]]["reasons"].append("same_reference_session_not_selected")
            else:
                distinct.append(obs)
                seen_sessions.add(session_id(obs))
        ready = len(distinct) >= m["reference_count"]
        selected = distinct[:m["reference_count"]] if ready else []
        for index, obs in enumerate(distinct):
            row = ledger[obs["observation_id"]]
            if not ready:
                row["reasons"].append("insufficient_clean_disjoint_reference_sessions")
            elif index >= m["reference_count"]:
                row["reasons"].append("outside_fixed_reference_count")
            else:
                row["selected_for"] = ["single", "multiple"] if index == 0 else ["multiple"]
        enrollments.append({
            "person_id": person["person_id"], "split": person["split"],
            "status": "ready" if ready else "unusable",
            "reasons": [] if ready else ["insufficient_clean_disjoint_reference_sessions"],
            "eligible_distinct_session_count": len(distinct),
            "single_reference_ids": [selected[0]["observation_id"]] if ready else [],
            "multiple_reference_ids": [o["observation_id"] for o in selected],
            "selected_session_ids": [session_id(o) for o in selected]})
    denominators = []
    for split in ("C", "T"):
        gallery = [p for p in enrollments if p["split"] == split]
        queries = [o for o in observations if o["role"] == "query" and o["split"] == split]
        for obs in queries:
            row = ledger[obs["observation_id"]]
            if not gallery:
                row["reasons"].append("no_known_enrollment_people")
            elif any(p["status"] != "ready" for p in gallery):
                row["reasons"].append("split_gallery_incomplete")
            if not row["reasons"]:
                row["selected_for"] = ["single", "multiple"]
        usable_known = sum(not ledger[o["observation_id"]]["reasons"] and
                           people[o["person_id"]]["enrollment"] == "known" for o in queries)
        usable_unknown = sum(not ledger[o["observation_id"]]["reasons"] and
                             people[o["person_id"]]["enrollment"] == "unknown" for o in queries)
        reasons = []
        if not gallery:
            reasons.append("no_known_enrollment_people")
        elif any(p["status"] != "ready" for p in gallery):
            reasons.append("split_gallery_incomplete")
        if not usable_known:
            reasons.append("no_usable_known_queries")
        if not usable_unknown:
            reasons.append("no_usable_unknown_queries")
        if any(ledger[o["observation_id"]]["reasons"] for o in queries):
            reasons.append("scheduled_queries_unusable_for_single_speaker_accuracy")
        for obs in queries:
            ledger[obs["observation_id"]]["single_speaker_accuracy_usable"] = not ledger[obs["observation_id"]]["reasons"]
        denominators.append({"split": split, "status": "blocked" if reasons else "ready", "reasons": reasons,
                             "total_queries": len(queries),
                             "usable_known_queries": usable_known, "usable_unknown_queries": usable_unknown,
                             "known_queries": sum(people[o["person_id"]]["enrollment"] == "known" for o in queries),
                             "unknown_queries": sum(people[o["person_id"]]["enrollment"] == "unknown" for o in queries),
                             "usable_queries": sum(not ledger[o["observation_id"]]["reasons"] for o in queries),
                             "unusable_queries": sum(bool(ledger[o["observation_id"]]["reasons"]) for o in queries)})
    return _canonical({
        "plan_schema_version": 1, "trial_design": "D_declared_disjoint_sessions",
        "status": "ready" if all(d["status"] == "ready" for d in denominators) else "blocked",
        "reasons": [d["split"] + ":" + reason for d in denominators for reason in d["reasons"]],
        "manifest_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "manifest": m, "selection_rule": "earliest_declared_session_one_observation_per_session_v1",
        "session_claim": "declared_disjoint_sessions_not_statistical_independence",
        "reference_cutoff_dates": cutoffs,
        "enrollments": enrollments, "observations": list(ledger.values()),
        "denominators": denominators,
        "limitations": ["metadata_does_not_prove_identity_or_speech_purity",
                        "source_and_crop_hashes_are_declared_not_recomputed",
                        "person_and_session_aliases_require_external_adjudication",
                        "missing_or_same_day_dates_cannot_establish_reference_before_query",
                        "same_session_source_hash_order_is_a_tiebreaker_not_chronology",
                        "new_prospective_cohort_is_a_declaration_not_verified_history",
                        "oracle_or_verified_reference_arm_is_not_automatic_grouping"]})
