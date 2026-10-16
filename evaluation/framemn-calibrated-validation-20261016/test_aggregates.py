"""Model-free checks of the published aggregate report; no empirical inference test."""
import json
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
PROTOCOL = json.loads((ROOT / "PROTOCOL.json").read_text(encoding="utf-8"))
RESULTS = json.loads((ROOT / "RESULTS.json").read_text(encoding="utf-8"))
COUNT_KEYS = ("tp", "fp", "fn", "tn", "reference_count", "covered_references")
ADDITIVE_KEYS = COUNT_KEYS + ("false_positive_centiseconds", "segments")


def rates(tp, fp, fn):
    return {
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


class AggregateChecks(unittest.TestCase):
    def assert_metric(self, metric):
        for key in ADDITIVE_KEYS:
            self.assertIs(type(metric[key]), int)
            self.assertGreaterEqual(metric[key], 0)
        self.assertEqual(metric["segments"], sum(metric[k] for k in ("tp", "fp", "fn", "tn")))
        self.assertLessEqual(metric["covered_references"], metric["reference_count"])
        self.assertAlmostEqual(metric["false_positive_seconds"], metric["false_positive_centiseconds"] / 100)
        for key, expected in rates(metric["tp"], metric["fp"], metric["fn"]).items():
            if expected is None:
                self.assertIsNone(metric[key])
            else:
                self.assertTrue(math.isfinite(metric[key]))
                self.assertAlmostEqual(metric[key], expected, places=12)

    def assert_pool(self, pooled, rows):
        self.assert_metric(pooled)
        for row in rows:
            self.assert_metric(row)
        for key in ADDITIVE_KEYS:
            self.assertEqual(pooled[key], sum(r[key] for r in rows), key)

    def test_rate_edge_cases(self):
        self.assertEqual(rates(0, 0, 0), {"precision": None, "recall": None, "f1": None})
        self.assertEqual(rates(0, 0, 1), {"precision": None, "recall": 0, "f1": 0})
        self.assertEqual(rates(0, 1, 0), {"precision": 0, "recall": None, "f1": 0})
        self.assertEqual(rates(1, 0, 0), {"precision": 1, "recall": 1, "f1": 1})

    def test_calibration_arithmetic(self):
        cal = RESULTS["calibration"]
        self.assertEqual((cal["file_count"], cal["audio_seconds"], cal["new_model_calls"]), (2, 240, 0))
        self.assertEqual(cal["eligible_threshold_count"], 50)
        for view in cal["views"].values():
            self.assert_pool(view["pooled_broad_door"], [r["broad_door"] for r in view["per_file"]])
        selected = cal["views"]["selected_001"]
        self.assertEqual([selected["pooled_broad_door"][k] for k in COUNT_KEYS], [13, 7, 5, 215, 12, 10])
        self.assertEqual([r["broad_door"]["false_positive_centiseconds"] for r in selected["per_file"]], [57, 161])
        self.assertEqual(selected["broad_union_reference_breakdown"], {"knock": {"covered": 6, "total": 6}, "doorslam": {"covered": 4, "total": 6}})

    def test_new_mixture_pooling(self):
        new = RESULTS["new_mixtures"]
        for view, pooled in new["pooled"].items():
            for label, metric in pooled["metrics"].items():
                self.assert_pool(metric, [r["views"][view]["metrics"][label] for r in new["per_file"]])
                self.assertEqual(metric["segments"], new["audio_seconds"])
            for label, breakdown in pooled["broad_union_reference_breakdown"].items():
                for key in ("covered", "total"):
                    self.assertEqual(breakdown[key], sum(r["views"][view]["broad_union_reference_breakdown"][label][key] for r in new["per_file"]))
            broad = pooled["metrics"]["broad_door"]
            self.assertEqual(broad["covered_references"], sum(r["covered"] for r in pooled["broad_union_reference_breakdown"].values()))
            self.assertEqual(broad["reference_count"], sum(r["total"] for r in pooled["broad_union_reference_breakdown"].values()))
        p = new["pooled"]["primary_median9_001"]
        d = new["pooled"]["diagnostic_median9_020"]
        self.assertEqual([p["metrics"]["broad_door"][k] for k in COUNT_KEYS], [16, 6, 18, 440, 24, 11])
        self.assertEqual([d["metrics"]["broad_door"][k] for k in COUNT_KEYS], [2, 0, 32, 446, 24, 1])
        self.assertEqual(p["broad_union_reference_breakdown"], {"knock": {"covered": 11, "total": 12}, "doorslam": {"covered": 0, "total": 12}})

    def test_false_positive_budgets(self):
        rows = RESULTS["new_mixtures"]["per_file"]
        primary = [r["views"]["primary_median9_001"] for r in rows]
        self.assertEqual([r["metrics"]["broad_door"]["false_positive_centiseconds"] for r in primary], [39, 20, 158, 85])
        for view in RESULTS["new_mixtures"]["pooled"]:
            observed = []
            for row in rows:
                v = row["views"][view]
                passes = v["metrics"]["broad_door"]["false_positive_centiseconds"] <= 200
                self.assertEqual(v["research_fp_budget_pass"], passes)
                observed.append(passes)
            self.assertEqual(RESULTS["new_mixtures"]["pooled"][view]["all_files_pass_research_fp_budget"], all(observed))

    def test_zipformer_summary_consistency(self):
        zipformer = RESULTS["zipformer_saved_calibration"]
        self.assertEqual(zipformer["new_model_calls"], 0)
        for arm, floor in (("baseline_5s_2.5s", 4.37), ("experimental_2s_1s", 2.78)):
            result = zipformer["arms"][arm]
            self.assertIsNone(result["selected_threshold"])
            self.assertEqual(result["eligible_threshold_count"], 0)
            self.assertEqual(result["minimum_observed_maximum_per_file_fp_seconds"], floor)
            self.assertGreater(floor, 2)
            endpoint = result["grid_endpoint_diagnostic"]
            self.assertEqual(endpoint["threshold"], 0.99)
            self.assertEqual(max(endpoint["false_positive_seconds_by_file"]), floor)
            self.assert_metric(endpoint["pooled_broad_door"])
            saved = result["saved_025_diagnostic"]
            self.assert_pool(saved["pooled_broad_door"], [r["broad_door"] for r in saved["per_file"]])
        # Report checks cannot independently establish an exhaustive curve's infeasibility.

    def test_frozen_protocol_and_fixture_counts(self):
        decision = PROTOCOL["decision"]
        self.assertEqual((decision["primary_threshold"], decision["diagnostic_threshold"], decision["median_frames"]), (0.01, 0.20, 9))
        self.assertEqual(decision["broad_door_label_indices"], {"Door": 134, "Knock": 228, "Slam": 333, "Sliding door": 335, "Cupboard open or close": 120})
        self.assertEqual(PROTOCOL["calibration"]["threshold_grid"], {"start": 0.01, "stop_inclusive": 0.50, "step": 0.01, "count": 50})
        self.assertTrue(PROTOCOL["calibration"]["selection_fixed_before_new_mixture_outputs"])
        ids = [f"test_{n}_ebr_0_nec_{nec}_poly_{poly}" for n in (2, 3) for nec, poly in ((2, 0), (4, 1))]
        self.assertEqual([r["id"] for r in PROTOCOL["fixtures"]], ids)
        self.assertEqual([r["id"] for r in RESULTS["new_mixtures"]["per_file"]], ids)
        self.assertEqual(sum(r["duration_seconds"] for r in PROTOCOL["fixtures"]), 480)
        for key in ("physical_knock_references", "physical_doorslam_references"):
            self.assertEqual(sum(r[key] for r in PROTOCOL["fixtures"]), 12)

    def test_reported_execution_and_resources(self):
        new = RESULTS["new_mixtures"]
        self.assertEqual((new["model_loads"], new["inference_calls_attempted"], new["inference_calls_completed"]), (1, 48, 48))
        self.assertFalse(new["zipformer_reexecuted"])
        self.assertFalse(new["production_defaults_changed"])
        resource = RESULTS["resources"]
        limits = PROTOCOL["declared_resource_limits"]
        self.assertEqual(resource["original_exit_code"], 0)
        self.assertTrue(resource["owned_cleanup_completed"])
        self.assertEqual(resource["score_output_bytes"], 48 * 447128)
        self.assertAlmostEqual(resource["inference_compute_only_rtf"], resource["inference_compute_seconds"] / resource["rtf_audio_seconds"], places=12)
        for observed, bound in (("elapsed_including_startup_cleanup_seconds", "outer_seconds"), ("work_seconds", "work_seconds"), ("peak_sampled_execution_plus_monitor_rss_bytes", "sampled_execution_plus_monitor_rss_bytes"), ("maximum_cumulative_new_disk_bytes", "new_disk_bytes"), ("score_output_bytes", "score_and_report_output_bytes")):
            self.assertLessEqual(resource[observed], limits[bound])
        # These checks validate reported values, not continuous peak usage or latency.

    def test_package_bounds(self):
        files = sorted(ROOT.iterdir())
        self.assertEqual([p.name for p in files], ["PROTOCOL.json", "README.md", "RESULTS.json", "test_aggregates.py"])
        self.assertTrue(all(p.is_file() and not p.is_symlink() for p in files))
        self.assertLessEqual(len(files), PROTOCOL["public_package"]["maximum_files"])
        self.assertLessEqual(sum(p.stat().st_size for p in files), PROTOCOL["public_package"]["maximum_total_bytes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
