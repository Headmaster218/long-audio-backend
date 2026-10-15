"""Synthetic, standard-library tests. No private files, audio, model or network."""
from dataclasses import FrozenInstanceError, replace
import unittest

from score_policy import BASELINE, GRID, Policy, Probe, accept, compare, select_policy, summarize, validate_role


def fixture(role, prefix, known_score=.70, unknown_score=.20):
    """Invented scores and opaque IDs; never a reconstruction of pilot data."""
    gallery = tuple(f"{prefix}-k{i}" for i in range(7))
    rows = []
    for identity in gallery + tuple(f"{prefix}-u{i}" for i in range(3)):
        for query in range(2):
            scores = tuple((person, known_score if person == identity else
                            unknown_score if identity not in gallery and person == gallery[0] else .10)
                           for person in gallery)
            rows.append(Probe(role, f"{identity}-q{query}", identity, scores))
    return tuple(rows)


def change_scores(row, values):
    return replace(row, scores=tuple((label, values.get(label, value)) for label, value in row.scores))


class ScorePolicyTests(unittest.TestCase):
    def test_inclusive_threshold_and_margin(self):
        row = fixture("calibration", "c")[0]
        row = change_scores(row, {"c-k0": .75, "c-k1": .625})
        self.assertEqual(accept(row, Policy(.75, .125)), "c-k0")
        self.assertIsNone(accept(row, Policy(.750001, .125)))
        self.assertIsNone(accept(row, Policy(.75, .125001)))

    def test_exact_tie_abstains_even_with_zero_margin(self):
        row = change_scores(fixture("calibration", "c")[0], {"c-k0": .8, "c-k1": .8})
        self.assertEqual(row.ranking[0][0], "c-k0")
        self.assertIsNone(accept(row, Policy(.5, 0)))
        self.assertIsNone(accept(replace(row, scores=tuple(reversed(row.scores))), Policy(.5, 0)))

    def test_bad_scores_and_mutable_pairs_are_rejected(self):
        row = fixture("calibration", "c")[0]
        for invalid in (float("nan"), float("inf"), -1.1, 1.1):
            with self.assertRaises(ValueError):
                change_scores(row, {"c-k0": invalid})
        with self.assertRaises(ValueError):
            replace(row, scores=list(row.scores))
        with self.assertRaises(ValueError):
            replace(row, scores=(row.scores[0],) * 7)

    def test_known_wrong_and_unknown_accept_are_separate(self):
        rows = fixture("calibration", "c", .80, .80)
        wrong = change_scores(rows[0], {"c-k0": .20, "c-k1": .90})
        report = summarize((wrong, rows[-1]), BASELINE)
        self.assertEqual(report["counts"]["known_wrong"], 1)
        self.assertEqual(report["counts"]["unknown_false_accept"], 1)
        self.assertEqual(report["false_person_assignments"], 2)
        self.assertEqual(report["denominators"], {"known": 1, "unknown": 1, "total": 2})

    def test_zero_acceptance_precision_is_null(self):
        report = summarize(fixture("calibration", "c"), None)
        self.assertEqual(report["accepted"], 0)
        self.assertIsNone(report["rates"]["correct_person_precision"])
        self.assertEqual(report["counts"]["known_abstain"], 14)
        self.assertEqual(report["counts"]["unknown_abstain"], 6)

    def test_grid_and_conservative_tiebreak(self):
        self.assertEqual(len(GRID), 15)
        self.assertEqual(select_policy(fixture("calibration", "c")), Policy(.65, .15))

    def test_false_person_priority_precedes_recovery(self):
        rows = list(fixture("calibration", "c", .80, .60))
        rows[0] = change_scores(rows[0], {"c-k0": .60})
        rows[1] = change_scores(rows[1], {"c-k0": .60})
        policy = select_policy(rows)
        self.assertEqual(policy, Policy(.75, .15))
        self.assertEqual(summarize(rows, policy)["counts"]["known_correct"], 12)
        self.assertEqual(summarize(rows, Policy(.55, .15))["false_person_assignments"], 6)

    def test_always_abstain_when_no_safe_recovery(self):
        self.assertIsNone(select_policy(fixture("calibration", "c", .30, .20)))
        self.assertIsNone(select_policy(fixture("calibration", "c", .60, .80)))

    def test_selection_rejects_evaluation_rows(self):
        with self.assertRaisesRegex(ValueError, "same-role"):
            select_policy(fixture("evaluation", "e"))

    def test_role_geometry_and_gallery_validation(self):
        rows = fixture("calibration", "c")
        for invalid in (rows[:-1], rows[:-1] + (rows[0],),
                        rows[:-1] + (replace(rows[-1], truth_id="c-extra"),)):
            with self.assertRaises(ValueError):
                validate_role(invalid, "calibration")
        changed = replace(rows[-1], scores=tuple(("c-extra" if i == 0 else label, score)
                                                for i, (label, score) in enumerate(rows[-1].scores)))
        with self.assertRaisesRegex(ValueError, "Gallery changed"):
            validate_role(rows[:-1] + (changed,), "calibration")

    def test_freeze_precedes_evaluation_and_loader_called_once(self):
        events = []
        def freeze(payload):
            events.append(("freeze", payload["selected"]))
            return "synthetic-receipt"
        def evaluation():
            events.append(("evaluation", None))
            return fixture("evaluation", "e")
        report = compare(fixture("calibration", "c"), evaluation, freeze)
        self.assertEqual(events, [("freeze", [.65, .15]), ("evaluation", None)])
        self.assertEqual(report["roles"]["evaluation"]["calibrated_study_only"]["counts"]["known_correct"], 14)

    def test_failed_or_missing_freeze_blocks_evaluation(self):
        called = []
        def evaluation():
            called.append(True)
            return fixture("evaluation", "e")
        for receipt in (None, "", " ", False):
            with self.assertRaisesRegex(ValueError, "freeze receipt"):
                compare(fixture("calibration", "c"), evaluation, lambda payload: receipt)
        def failing_freeze(payload):
            raise OSError("Synthetic persistence failure")
        with self.assertRaises(OSError):
            compare(fixture("calibration", "c"), evaluation, failing_freeze)
        self.assertEqual(called, [])

    def test_identity_and_query_leakage_are_rejected(self):
        calibration = fixture("calibration", "c")
        with self.assertRaisesRegex(ValueError, "Identity leakage"):
            compare(calibration, lambda: fixture("evaluation", "c"), lambda payload: "receipt")
        evaluation = list(fixture("evaluation", "e"))
        evaluation[0] = replace(evaluation[0], query_id=calibration[0].query_id)
        with self.assertRaisesRegex(ValueError, "Query leakage"):
            compare(calibration, lambda: evaluation, lambda payload: "receipt")

    def test_evaluation_scores_do_not_retune_policy(self):
        calibration = fixture("calibration", "c")
        good = compare(calibration, lambda: fixture("evaluation", "e", .80, .20), lambda payload: "receipt")
        poor = compare(calibration, lambda: fixture("evaluation", "e", .30, .80), lambda payload: "receipt")
        self.assertEqual(good["freeze"], poor["freeze"])
        self.assertNotEqual(good["roles"]["evaluation"], poor["roles"]["evaluation"])

    def test_callback_cannot_change_selected_policy(self):
        def mutate_payload(payload):
            payload["selected"][:] = [.95, .15]
            return "receipt"
        report = compare(fixture("calibration", "c"), lambda: fixture("evaluation", "e"), mutate_payload)
        self.assertEqual(report["freeze"]["selected"], [.65, .15])
        self.assertEqual(report["roles"]["evaluation"]["calibrated_study_only"]["counts"]["known_correct"], 14)
        with self.assertRaises(FrozenInstanceError):
            BASELINE.tau = .55

    def test_calibration_digest_is_order_invariant(self):
        rows = fixture("calibration", "c")
        one = compare(rows, lambda: fixture("evaluation", "e"), lambda payload: "receipt")
        reordered = tuple(replace(row, scores=tuple(reversed(row.scores))) for row in reversed(rows))
        two = compare(reordered, lambda: fixture("evaluation", "e"), lambda payload: "receipt")
        self.assertEqual(one["freeze"], two["freeze"])


if __name__ == "__main__":
    unittest.main()
