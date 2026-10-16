"""Check published aggregates only; no model, audio, network or prediction replay."""
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
P = json.loads((HERE / "PROTOCOL.json").read_text())
R = json.loads((HERE / "RESULTS.json").read_text())
VIEWS = ("primary_median9", "diagnostic_raw")
LABELS = ("broad_door", "Knock", "Slam", "Door")
COUNTS = ("tp", "fp", "fn", "tn")
ADDITIVE = COUNTS + ("reference_count", "covered_references", "false_positive_seconds", "segments")


def rates(row):
    tp, fp, fn = (row[k] for k in ("tp", "fp", "fn"))
    return {"precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


class Aggregates(unittest.TestCase):
    def check_metric(self, row, seconds):
        for key in COUNTS + ("reference_count", "covered_references", "segments"):
            self.assertIs(type(row[key]), int)
            self.assertGreaterEqual(row[key], 0)
        self.assertEqual(sum(row[k] for k in COUNTS), seconds)
        self.assertEqual(row["segments"], seconds)
        self.assertLessEqual(row["covered_references"], row["reference_count"])
        self.assertGreaterEqual(row["false_positive_seconds"], 0)
        self.assertLessEqual(row["false_positive_seconds"], seconds)
        for key, value in rates(row).items():
            if value is None:
                self.assertIsNone(row[key])
            else:
                self.assertAlmostEqual(row[key], value, places=12)

    def test_per_file_and_pooled_arithmetic(self):
        self.assertEqual(len(R["per_file"]), 4)
        self.assertEqual([f["id"] for f in R["per_file"]], [f["id"] for f in P["fixtures"]])
        for file in R["per_file"]:
            self.assertEqual(file["duration_seconds"], 120)
            for view in VIEWS:
                for label in LABELS:
                    self.check_metric(file["views"][view][label], 120)
        for split, pooled in R["pooled"].items():
            files = [f for f in R["per_file"] if split == "all_four" or f["historical_split"] == split]
            self.assertEqual(len(files), 4 if split == "all_four" else 2)
            self.assertEqual(pooled["files"], len(files))
            self.assertEqual(pooled["audio_seconds"], 120 * len(files))
            for view in VIEWS:
                for label in LABELS:
                    aggregate = pooled["views"][view][label]
                    self.check_metric(aggregate, pooled["audio_seconds"])
                    for key in ADDITIVE:
                        self.assertAlmostEqual(aggregate[key], sum(f["views"][view][label][key] for f in files), places=12)

    def test_headline_and_prior_comparison(self):
        for view in VIEWS:
            heldout = R["pooled"]["heldout"]["views"][view]
            door = heldout["broad_door"]
            self.assertEqual(tuple(door[k] for k in COUNTS), (2, 0, 16, 222))
            self.assertEqual((door["covered_references"], door["reference_count"]), (1, 12))
            self.assertEqual(door["f1"], 0.2)
            self.assertEqual((heldout["Knock"]["covered_references"], heldout["Knock"]["reference_count"]), (1, 6))
            self.assertEqual((heldout["Slam"]["covered_references"], heldout["Slam"]["reference_count"]), (0, 6))
        for view, tp, covered in ((VIEWS[0], 3, 2), (VIEWS[1], 4, 3)):
            row = R["pooled"]["calibration"]["views"][view]["broad_door"]
            self.assertEqual((row["tp"], row["covered_references"], row["reference_count"]), (tp, covered, 12))
        old = R["prior_zipformer_comparison"]
        self.assertFalse(old["rerun"])
        self.assertTrue(old["different_model_threshold_and_timing_semantics"])
        for arms in old["splits"].values():
            for row in arms.values():
                self.check_metric(row, 240)
        for arm, counts, covered in (("baseline_5s_2.5s", (3, 10, 15, 212), 2),
                                     ("experimental_2s_1s", (4, 10, 14, 212), 3)):
            row = old["splits"]["heldout"][arm]
            self.assertEqual(tuple(row[k] for k in COUNTS), counts)
            self.assertEqual(row["covered_references"], covered)
            detail = old["heldout_reference_breakdown"][arm]
            self.assertEqual(detail["knock_covered"] + detail["doorslam_covered"], covered)
            self.assertEqual(detail["knock_total"] + detail["doorslam_total"], 12)
            self.assertEqual(detail["doorslam_covered"], 0)

    def test_fixed_protocol_and_call_accounting(self):
        e, d, m = P["execution"], P["decision"], P["model"]
        self.assertEqual(R["status"], "completed_comparative_pilot")
        self.assertEqual(R["model_loads"], e["model_loads"])
        self.assertEqual(R["model_loads"], 1)
        self.assertEqual(R["inference_calls_attempted"], 48)
        self.assertEqual(R["inference_calls_completed"], e["total_calls"])
        self.assertEqual(e["total_calls"], e["calls_per_file"] * e["files"])
        self.assertEqual(sum(f["inference_calls"] for f in R["per_file"]), 48)
        self.assertTrue(all(f["inference_calls"] == 12 for f in R["per_file"]))
        self.assertEqual(e["score_shape"], m["strong_output_shape_per_call"])
        self.assertEqual(e["score_shape"], [447, 250])
        self.assertEqual(e["score_float32_payload_bytes_per_call"], 447 * 250 * 4)
        self.assertEqual(e["samples_per_chunk"], 16000 * e["chunk_seconds"])
        self.assertEqual((e["chunk_seconds"], e["chunk_hop_seconds"]), (10, 10))
        self.assertEqual(e["frames_per_file"], 250 * 12)
        self.assertEqual(m["output_frame_seconds"], 10 / 250)
        self.assertFalse(e["padding_needed"])
        self.assertFalse(m["causal"])
        self.assertEqual(d["threshold"], 0.2)
        self.assertEqual(d["comparison"], "strictly greater than")
        self.assertTrue(d["uncalibrated_global_threshold"])
        self.assertEqual([d["views"][v]["median_frames"] for v in VIEWS], [9, 1])
        self.assertEqual(d["broad_door_label_indices"], {"Door": 134, "Knock": 228, "Slam": 333, "Sliding door": 335, "Cupboard open or close": 120})
        self.assertTrue(P["historical_split_names_only"])
        self.assertTrue(P["exposure"]["all_four_previously_exposed"])
        self.assertTrue(P["exposure"]["not_a_new_blind_test"])
        self.assertFalse(R["baseline_reexecuted"])
        self.assertFalse(R["production_defaults_changed"])

    def test_timing_resource_and_package_bounds(self):
        r, limits, e = R["resources"], P["declared_resource_limits"], P["execution"]
        self.assertEqual(r["rtf_audio_seconds"], sum(f["duration_seconds"] for f in R["per_file"]))
        self.assertEqual(r["rtf_audio_seconds"], 480)
        self.assertAlmostEqual(r["inference_compute_seconds"], sum(f["compute_seconds"] for f in R["per_file"]), places=12)
        self.assertAlmostEqual(r["inference_compute_only_rtf"], r["inference_compute_seconds"] / 480, places=15)
        self.assertLess(r["work_seconds"], r["elapsed_including_startup_cleanup_seconds"])
        self.assertLess(r["work_seconds"], limits["work_seconds"])
        self.assertLess(r["elapsed_including_startup_cleanup_seconds"], limits["outer_seconds"])
        self.assertGreater(r["maximum_work_phase_sample_gap_seconds"], 0)
        self.assertLess(r["maximum_work_phase_sample_gap_seconds"], r["work_seconds"])
        self.assertLess(r["peak_sampled_execution_plus_monitor_rss_bytes"], limits["sampled_execution_plus_monitor_rss_bytes"])
        self.assertLess(r["peak_sampled_execution_plus_monitor_rss_bytes"] + limits["reserved_other_memory_bytes"], limits["combined_ram_bytes"])
        self.assertLess(r["maximum_cumulative_new_disk_bytes"], limits["new_disk_bytes"])
        self.assertEqual(r["score_output_bytes"], 48 * e["score_serialized_bytes_per_call"])
        self.assertLess(r["score_output_bytes"], limits["score_and_report_output_bytes"])
        self.assertTrue(r["positive_exit_wait"])
        self.assertTrue(r["owned_cleanup_completed"])
        self.assertEqual(r["known_owned_processes_remaining"], 0)
        files = list(HERE.iterdir())
        self.assertEqual({f.name for f in files}, {"README.md", "PROTOCOL.json", "RESULTS.json", "test_aggregates.py"})
        self.assertTrue(all(f.is_file() and not f.is_symlink() for f in files))
        self.assertLessEqual(len(files), P["public_package"]["maximum_files"])
        self.assertLessEqual(sum(f.stat().st_size for f in files), P["public_package"]["maximum_total_bytes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
