"""Synthetic contract tests only: no recordings, models or C/T evaluation."""
import copy
import hashlib
import json
import random
import unittest
from unittest.mock import patch

import multireference_trials as contract


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def evidence(status=None, origin="external_review", verified=True):
    result = {"origin": origin, "evidence_id": "annotation-v1", "verified": verified}
    if status is not None:
        result["status"] = status
    if origin == "missing":
        result["evidence_id"] = None
        result["verified"] = False
    return result


def fixture():
    people = [{"person_id": name, "split": split, "enrollment": kind}
              for name, split, kind in (("c-known", "C", "known"), ("c-unknown", "C", "unknown"),
                                        ("t-known", "T", "known"), ("t-unknown", "T", "unknown"))]
    sessions = [{"session_id": "session-" + str(i), "acquisition_order": i,
                 "recorded_on": "2026-12-%02d" % (i + 1),
                 "declaration_origin": "external_review", "evidence_id": "session-log-v1"}
                for i in range(8)]
    sources = [{"content_sha256": digest("source" + str(i)), "session_id": s["session_id"],
                "sample_rate": 16000, "total_frames": 1600000} for i, s in enumerate(sessions)]
    observations = []
    for person in people:
        positions = (0, 1, 2, 3) if person["enrollment"] == "known" else (3,)
        # Distinct non-overlapping spans even when different people share a source.
        start = people.index(person) * 160000
        for i in positions:
            name = person["person_id"] + "-" + str(i)
            observations.append({"observation_id": name, "person_id": person["person_id"],
                "split": person["split"], "role": "query" if i == 3 else "reference",
                "space_id": "speaker-v1.frontend-v1", "source_content_sha256": sources[i + (4 if person["split"] == "T" else 0)]["content_sha256"],
                "start_frame": start, "end_frame": start + 64000, "crop_sha256": digest(name),
                "identity": evidence(), "speech": evidence("speech"), "overlap": evidence("single_speaker")})
    return {"schema_version": 1, "protocol_id": "prospective-multiref-v1", "cohort": "new_prospective",
            "space": {"model_id": "speaker-model-immutable-v1", "frontend_id": "frontend-immutable-v1",
                      "space_id": "speaker-v1.frontend-v1", "model_sha256": digest("model-weights"),
                      "frontend_config_sha256": digest("canonical-frontend-config")},
            "content_hash_scheme": "pcm_s16le_mono_v1",
            "reference_count": 2, "persons": people, "sessions": sessions, "sources": sources,
            "observations": observations}


def plan(value):
    return json.loads(contract.plan_trials(contract.freeze_manifest(json.dumps(value))))


def ledger(result, name):
    return next(o for o in result["observations"] if o["observation_id"] == name)


class TrialContractTests(unittest.TestCase):
    def setUp(self):
        self.m = fixture()

    def reject(self, expected=None):
        with self.assertRaisesRegex(ValueError, expected or "."):
            contract.freeze_manifest(json.dumps(self.m))

    def test_valid_fixed_paired_plan(self):
        result = plan(self.m)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(len(result["observations"]), len(self.m["observations"]))
        self.assertEqual(result["manifest_sha256"], digest(contract.freeze_manifest(json.dumps(self.m))))
        for row in result["enrollments"]:
            person = row["person_id"]
            self.assertEqual(row["status"], "ready")
            self.assertEqual(row["single_reference_ids"], [person + "-0"])
            self.assertEqual(row["multiple_reference_ids"], [person + "-0", person + "-1"])
            self.assertEqual(row["selected_session_ids"], ["session-0", "session-1"] if person == "c-known" else ["session-4", "session-5"])
        for row in result["denominators"]:
            self.assertEqual((row["total_queries"], row["known_queries"], row["unknown_queries"],
                              row["usable_queries"], row["unusable_queries"]), (2, 1, 1, 2, 0))
        self.assertEqual(ledger(result, "c-known-2")["reasons"], ["outside_fixed_reference_count"])

    def test_reordered_manifest_is_identical_freeze_and_plan(self):
        expected = contract.freeze_manifest(json.dumps(self.m))
        for seed in range(15):
            shuffled = copy.deepcopy(self.m)
            for key in ("persons", "sessions", "sources", "observations"):
                random.Random(seed).shuffle(shuffled[key])
            actual = contract.freeze_manifest(json.dumps(shuffled, sort_keys=bool(seed % 2)))
            self.assertEqual(actual, expected)
            self.assertEqual(contract.plan_trials(actual), contract.plan_trials(expected))

    def test_freeze_is_immutable_string_snapshot(self):
        frozen = contract.freeze_manifest(json.dumps(self.m))
        before = contract.plan_trials(frozen)
        self.m["observations"][0]["identity"]["verified"] = False
        self.assertEqual(before, contract.plan_trials(frozen))
        self.assertNotEqual(frozen, contract.freeze_manifest(json.dumps(self.m)))

    def test_plan_requires_canonical_freeze(self):
        with self.assertRaisesRegex(ValueError, "canonical"):
            contract.plan_trials(json.dumps(self.m))

    def test_duplicate_crop_hash(self):
        self.m["observations"][1]["crop_sha256"] = self.m["observations"][0]["crop_sha256"]
        self.reject("duplicate crop_sha256")

    def test_duplicate_observation_id(self):
        self.m["observations"][1]["observation_id"] = self.m["observations"][0]["observation_id"]
        self.reject("duplicate observation_id")

    def test_overlapping_spans_even_under_different_person_ids(self):
        self.m["observations"][5]["source_content_sha256"] = self.m["observations"][0]["source_content_sha256"]
        self.m["observations"][5]["start_frame"] = 100
        self.m["observations"][5]["end_frame"] = 65000
        self.reject("overlapping")

    def test_nested_overlap(self):
        extra = copy.deepcopy(self.m["observations"][0])
        extra.update(observation_id="nested", crop_sha256=digest("nested"), start_frame=1, end_frame=10)
        self.m["observations"].append(extra)
        self.reject("overlapping")

    def test_adjacent_spans_allowed(self):
        extra = copy.deepcopy(self.m["observations"][0])
        extra.update(observation_id="adjacent", crop_sha256=digest("adjacent"), start_frame=64000, end_frame=128000)
        self.m["observations"].append(extra)
        result = plan(self.m)
        self.assertEqual(ledger(result, "adjacent")["reasons"], ["same_reference_session_not_selected"])

    def test_content_alias_cannot_claim_new_session(self):
        alias = copy.deepcopy(self.m["sources"][0])
        alias["session_id"] = "session-1"
        self.m["sources"].append(alias)
        self.reject("duplicate content_sha256")

    def test_content_alias_cannot_change_sample_rate(self):
        alias = copy.deepcopy(self.m["sources"][0])
        alias["sample_rate"] = 48000
        self.m["sources"].append(alias)
        self.reject("duplicate content_sha256")

    def test_crop_repackaged_as_new_source_cannot_fill_reference_slot(self):
        a, b = self.m["observations"][:2]
        self.m["sources"][1].update(content_sha256=a["crop_sha256"],
                                     total_frames=a["end_frame"] - a["start_frame"])
        b.update(source_content_sha256=a["crop_sha256"], start_frame=0, end_frame=32000)
        self.reject("cross-namespace")

    def test_actual_pcm_crop_source_alias_rejected(self):
        def pcm_hash(pcm):
            header = ("pcm_s16le_mono_v1\n16000\n" + str(len(pcm) // 2) + "\n").encode()
            return hashlib.sha256(header + pcm).hexdigest()
        pcm = bytes(range(256)) * 10
        part = pcm[40:200]
        a, b = self.m["observations"][:2]
        self.m["sources"][0].update(content_sha256=pcm_hash(pcm), total_frames=len(pcm) // 2)
        a.update(source_content_sha256=pcm_hash(pcm), start_frame=20, end_frame=100,
                 crop_sha256=pcm_hash(part))
        self.m["sources"][1].update(content_sha256=pcm_hash(part), total_frames=len(part) // 2)
        b.update(source_content_sha256=pcm_hash(part), start_frame=0, end_frame=40,
                 crop_sha256=pcm_hash(part[:80]))
        self.reject("cross-namespace")

    def test_crop_repackaged_as_t_source_cannot_bypass_split_isolation(self):
        a = self.m["observations"][0]
        self.m["sources"][7].update(content_sha256=a["crop_sha256"], total_frames=64000)
        t_queries = [o for o in self.m["observations"] if o["split"] == "T" and o["role"] == "query"]
        for index, obs in enumerate(t_queries):
            obs.update(source_content_sha256=a["crop_sha256"], start_frame=index * 16000,
                       end_frame=(index + 1) * 16000)
        self.reject("cross-namespace")

    def test_self_consistent_full_source_crop_is_valid(self):
        self.m["sources"][0]["total_frames"] = 64000
        self.m["observations"][0]["crop_sha256"] = self.m["sources"][0]["content_sha256"]
        self.assertEqual(plan(self.m)["status"], "ready")

    def test_inconsistent_full_source_crop_hash_rejected(self):
        self.m["sources"][0]["total_frames"] = 64000
        self.reject("full-source crop hash")

    def test_partial_crop_cannot_claim_full_source_hash(self):
        self.m["observations"][0]["crop_sha256"] = self.m["sources"][0]["content_sha256"]
        self.reject("cross-namespace")

    def test_space_mismatch(self):
        self.m["observations"][0]["space_id"] = "different-frontend"
        self.reject("space mismatch")

    def test_unknown_enrollment_rejected(self):
        self.m["observations"][4]["role"] = "reference"
        self.reject("unknown people")

    def test_ct_identity_leakage(self):
        self.m["observations"][0]["split"] = "T"
        self.reject("identity leakage")

    def test_ct_person_duplication_rejected(self):
        person = copy.deepcopy(self.m["persons"][0])
        person["split"] = "T"
        self.m["persons"].append(person)
        self.reject("duplicate person_id")

    def test_boolean_is_not_integer(self):
        for container, key in ((self.m, "schema_version"), (self.m, "reference_count"),
                               (self.m["sessions"][0], "acquisition_order"),
                               (self.m["sources"][0], "sample_rate"),
                               (self.m["sources"][0], "total_frames"),
                               (self.m["observations"][0], "start_frame"),
                               (self.m["observations"][0], "end_frame")):
            original = container[key]
            container[key] = True
            self.reject("integer")
            container[key] = original

    def test_floats_not_accepted_as_integers(self):
        self.m["reference_count"] = 2.0
        self.reject("integer")

    def test_nonfinite_numbers(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            self.m["observations"][0]["start_frame"] = value
            self.reject("nonfinite")

    def test_malformed_json(self):
        for raw in ("{", "[]", "null", "", '{"schema_version":1,"schema_version":1}', "[" * 1200):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                contract.freeze_manifest(raw)

    def test_invalid_hash(self):
        self.m["observations"][0]["crop_sha256"] = "A" * 64
        self.reject("SHA-256")

    def test_absolute_path_and_unknown_fields_rejected(self):
        self.m["space"]["model_id"] = "/models/secret"
        self.reject("not a path")
        self.m = fixture()
        self.m["observations"][0]["score"] = .99
        self.reject("expected exactly")

    def test_non_boolean_verified_rejected(self):
        self.m["observations"][0]["identity"]["verified"] = 1
        self.reject("boolean")

    def test_source_frame_bounds(self):
        for start, end in ((-1, 10), (10, 10), (11, 10), (0, 1600001)):
            self.m["observations"][0].update(start_frame=start, end_frame=end)
            self.reject("integer")

    def test_count_boundaries(self):
        for count in (0, 1, 9, "2", None):
            self.m["reference_count"] = count
            self.reject("integer")
        for count in (2, 8):
            self.m["reference_count"] = count
            plan(self.m)

    def test_bounded_array_capacities(self):
        for constant in ("MAX_OBSERVATIONS", "MAX_PEOPLE", "MAX_SESSIONS", "MAX_SOURCES"):
            with patch.object(contract, constant, 1):
                self.reject("rows")

    def test_byte_capacity(self):
        with patch.object(contract, "MAX_BYTES", 10):
            self.reject("bounded JSON")

    def test_missing_clean_evidence_blocks_without_losing_denominator(self):
        for obs in self.m["observations"]:
            if obs["role"] == "reference" and obs["person_id"] == "c-known":
                obs["overlap"] = evidence("unknown", "missing")
        result = plan(self.m)
        self.assertIn("overlap_not_externally_verified", ledger(result, "c-known-0")["reasons"])
        c = result["denominators"][0]
        self.assertEqual((c["total_queries"], c["usable_queries"], c["unusable_queries"]), (2, 0, 2))
        self.assertEqual(len(result["manifest"]["observations"]), 10)

    def test_native_clean_predictions_do_not_create_references(self):
        for obs in self.m["observations"]:
            if obs["role"] == "reference":
                obs["speech"] = evidence("speech", "native_prediction", False)
                obs["overlap"] = evidence("single_speaker", "native_prediction", False)
        result = plan(self.m)
        self.assertTrue(all(not row["multiple_reference_ids"] for row in result["enrollments"]))
        self.assertIn("speech_not_externally_verified", ledger(result, "c-known-0")["reasons"])

    def test_native_identity_predictions_do_not_create_references(self):
        self.m["observations"][0]["identity"] = evidence(origin="native_prediction", verified=False)
        result = plan(self.m)
        self.assertEqual(result["enrollments"][0]["single_reference_ids"], ["c-known-1"])
        self.assertIn("identity_not_externally_verified", ledger(result, "c-known-0")["reasons"])

    def test_native_predictions_cannot_be_marked_verified(self):
        self.m["observations"][0]["speech"] = evidence("speech", "native_prediction", True)
        self.reject("not external verification")

    def test_oracle_annotations_are_explicit_and_allowed(self):
        self.m["observations"][0]["overlap"] = evidence("single_speaker", "oracle_benchmark")
        result = plan(self.m)
        self.assertEqual(result["enrollments"][0]["single_reference_ids"], ["c-known-0"])
        obs = next(o for o in result["manifest"]["observations"] if o["observation_id"] == "c-known-0")
        self.assertEqual(obs["overlap"]["origin"], "oracle_benchmark")
        self.assertIn("oracle_or_verified_reference_arm_is_not_automatic_grouping", result["limitations"])

    def test_non_speech_and_overlap_are_not_clean(self):
        self.m["observations"][0]["speech"]["status"] = "non_speech"
        self.m["observations"][0]["overlap"]["status"] = "overlap"
        result = plan(self.m)
        self.assertEqual(ledger(result, "c-known-0")["reasons"], ["speech_not_clean", "overlap_not_clean"])

    def test_insufficient_references_no_single_arm_fallback(self):
        self.m["reference_count"] = 4
        result = plan(self.m)
        for row in result["enrollments"]:
            self.assertEqual((row["status"], row["single_reference_ids"], row["multiple_reference_ids"]),
                             ("unusable", [], []))
        self.assertEqual(sum(d["total_queries"] for d in result["denominators"]), 4)

    def test_same_session_crops_cannot_fill_multiple_slots(self):
        for source in self.m["sources"][:3]:
            source["session_id"] = "session-0"
        result = plan(self.m)
        self.assertEqual(result["enrollments"][0]["eligible_distinct_session_count"], 1)
        self.assertEqual(result["enrollments"][0]["status"], "unusable")
        self.assertEqual(result["session_claim"], "declared_disjoint_sessions_not_statistical_independence")

    def test_reference_query_same_session_excluded_even_different_source(self):
        self.m["sources"][0]["session_id"] = "session-3"
        result = plan(self.m)
        self.assertIn("session_shared_with_query", ledger(result, "c-known-0")["reasons"])
        self.assertEqual(result["enrollments"][0]["single_reference_ids"], ["c-known-1"])

    def test_reference_after_earliest_query_excluded(self):
        self.m["sessions"][2].update(acquisition_order=9, recorded_on="2026-12-09")
        result = plan(self.m)
        self.assertIn("reference_not_before_earliest_query_date", ledger(result, "c-known-2")["reasons"])

    def test_same_day_order_does_not_prove_precedence(self):
        self.m["sessions"][2]["recorded_on"] = "2026-12-04"
        result = plan(self.m)
        self.assertIn("reference_not_before_earliest_query_date", ledger(result, "c-known-2")["reasons"])

    def test_missing_query_date_fails_closed(self):
        self.m["sessions"][3]["recorded_on"] = None
        result = plan(self.m)
        self.assertIn("reference_query_chronology_unverified", ledger(result, "c-known-0")["reasons"])
        self.assertEqual(result["denominators"][0]["total_queries"], 2)
        self.assertEqual(result["denominators"][0]["usable_queries"], 0)

    def test_missing_reference_date_fails_closed(self):
        self.m["sessions"][0]["recorded_on"] = None
        result = plan(self.m)
        self.assertIn("reference_query_chronology_unverified", ledger(result, "c-known-0")["reasons"])
        self.assertEqual(result["enrollments"][0]["single_reference_ids"], ["c-known-1"])

    def test_date_order_contradiction_rejected(self):
        self.m["sessions"][0]["recorded_on"] = "2026-12-09"
        self.reject("contradict")

    def test_bad_date_rejected(self):
        for value in ("2026-13-04", "20261201", 7, True):
            self.m["sessions"][0]["recorded_on"] = value
            self.reject("YYYY-MM-DD")

    def test_duplicate_session_and_order_rejected(self):
        self.m["sessions"][1]["acquisition_order"] = 0
        self.reject("duplicate acquisition_order")
        self.m = fixture()
        self.m["sessions"][1]["session_id"] = "session-0"
        self.reject("duplicate session_id")

    def test_unknown_source_session_person_rejected(self):
        for section, key in (("sources", "session_id"), ("observations", "person_id"),
                             ("observations", "source_content_sha256")):
            self.m = fixture()
            self.m[section][0][key] = digest("absent") if "sha256" in key else "absent"
            self.reject("unknown")

    def test_unverified_query_kept_not_promoted_or_dropped(self):
        self.m["observations"][3]["identity"] = evidence(origin="native_prediction", verified=False)
        result = plan(self.m)
        row = ledger(result, "c-known-3")
        self.assertEqual(row["role"], "query")
        self.assertEqual(row["selected_for"], [])
        self.assertEqual(result["denominators"][0]["total_queries"], 2)
        self.assertEqual(result["denominators"][0]["unusable_queries"], 1)

    def test_query_purity_does_not_filter_benchmark_denominator(self):
        self.m["observations"][3]["speech"] = evidence("unknown", "missing")
        self.m["observations"][3]["overlap"] = evidence("unknown", "missing")
        result = plan(self.m)
        row = ledger(result, "c-known-3")
        self.assertEqual(row["selected_for"], [])
        self.assertFalse(row["single_speaker_accuracy_usable"])
        self.assertEqual(row["query_condition"], "unverified_or_unknown")
        self.assertFalse(row["externally_verified_speech"])
        self.assertFalse(row["externally_verified_overlap"])
        self.assertEqual(result["denominators"][0]["total_queries"], 2)
        self.assertEqual(result["status"], "blocked")

    def test_ct_session_leakage_even_disjoint_person_and_source(self):
        self.m["sources"][4]["session_id"] = "session-0"
        self.reject("source-session leakage")

    def test_ct_session_leakage_even_nonoverlapping_frame_spans(self):
        self.m["observations"][5]["source_content_sha256"] = self.m["sources"][0]["content_sha256"]
        self.reject("source-session leakage")

    def test_exact_space_digests_required(self):
        for key in ("model_sha256", "frontend_config_sha256"):
            original = self.m["space"][key]
            self.m["space"][key] = "unpinned-model-latest"
            self.reject("SHA-256")
            self.m["space"][key] = original

    def test_container_byte_hash_scheme_is_not_accepted(self):
        self.m["content_hash_scheme"] = "wav_container_bytes"
        self.reject("content_hash_scheme")

    def test_missing_t_split_is_blocked(self):
        self.m["persons"] = [p for p in self.m["persons"] if p["split"] == "C"]
        self.m["observations"] = [o for o in self.m["observations"] if o["split"] == "C"]
        result = plan(self.m)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("T:no_usable_known_queries", result["reasons"])
        self.assertIn("T:no_usable_unknown_queries", result["reasons"])
        self.assertEqual(result["denominators"][1]["total_queries"], 0)

    def test_missing_unknown_query_is_blocked(self):
        self.m["observations"] = [o for o in self.m["observations"] if o["observation_id"] != "c-unknown-3"]
        result = plan(self.m)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("C:no_usable_unknown_queries", result["reasons"])

    def test_missing_known_query_is_blocked(self):
        self.m["observations"] = [o for o in self.m["observations"] if o["observation_id"] != "c-known-3"]
        result = plan(self.m)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("C:no_usable_known_queries", result["reasons"])

    def test_overlap_query_is_labelled_unusable_without_denominator_loss(self):
        self.m["observations"][3]["overlap"]["status"] = "overlap"
        result = plan(self.m)
        row = ledger(result, "c-known-3")
        self.assertEqual(row["query_condition"], "overlap")
        self.assertTrue(row["externally_verified_overlap"])
        self.assertFalse(row["single_speaker_accuracy_usable"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["denominators"][0]["total_queries"], 2)

    def test_earliest_query_cutoff_is_fixed_not_per_query(self):
        extra = copy.deepcopy(self.m["observations"][3])
        extra.update(observation_id="c-earlier-query", source_content_sha256=self.m["sources"][2]["content_sha256"],
                     start_frame=800000, end_frame=864000, crop_sha256=digest("earlier-query"))
        self.m["observations"].append(extra)
        result = plan(self.m)
        self.assertEqual(result["reference_cutoff_dates"]["C"], "2026-12-03")
        self.assertIn("reference_not_before_earliest_query_date", ledger(result, "c-known-2")["reasons"])
        self.assertEqual(result["enrollments"][0]["multiple_reference_ids"], ["c-known-0", "c-known-1"])
        self.assertEqual(result["denominators"][0]["total_queries"], 3)

    def test_planner_performs_no_file_io(self):
        raw = json.dumps(self.m)
        with patch("builtins.open", side_effect=AssertionError("unexpected file access")):
            frozen = contract.freeze_manifest(raw)
            result = json.loads(contract.plan_trials(frozen))
        self.assertEqual(result["status"], "ready")

    def test_missing_evidence_cannot_assert_known_status(self):
        self.m["observations"][0]["overlap"] = evidence("single_speaker", "missing")
        self.reject("unknown status")

    def test_evidence_mutation_changes_frozen_manifest_hash(self):
        before = plan(self.m)["manifest_sha256"]
        self.m["observations"][0]["identity"]["evidence_id"] = "different-review"
        self.assertNotEqual(before, plan(self.m)["manifest_sha256"])

    def test_legacy_cohort_rejected(self):
        self.m["cohort"] = "reused_current_T_queries"
        self.reject("cohort")

    def test_no_gallery_is_explicit(self):
        self.m["persons"] = [p for p in self.m["persons"] if p["enrollment"] == "unknown"]
        self.m["observations"] = [o for o in self.m["observations"] if "unknown" in o["person_id"]]
        result = plan(self.m)
        self.assertEqual(ledger(result, "c-unknown-3")["reasons"], ["no_known_enrollment_people"])
        self.assertEqual(sum(d["total_queries"] for d in result["denominators"]), 2)


if __name__ == "__main__":
    unittest.main()
