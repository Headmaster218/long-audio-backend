"""New standalone synthetic tests, separate from the original 26 pilot checks.

All numeric inputs below are generated fixtures; none are recorded embeddings.
"""

import hashlib
import math
import unittest

import protocol as p


def row(expected=None, best=None, score=1.0, margin=1.0, role="calibration"):
    return {
        "expected": p.SPEAKERS[0] if expected is None else expected,
        "best_speaker": p.SPEAKERS[0] if best is None else best,
        "best_score": score,
        "margin": margin,
        "role": role,
    }


def dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def basis(speaker):
    return tuple(float(speaker == candidate) for candidate in p.SPEAKERS)


class PolicyTests(unittest.TestCase):
    def test_fixed_policy_and_grid_are_unchanged(self):
        self.assertEqual(p.FIXED, (0.75, 0.10))
        self.assertEqual(p.GRID, tuple((t, m) for t in (0.55, 0.65, 0.75)
                                      for m in (0.05, 0.10, 0.15)))

    def test_acceptance_includes_both_boundaries(self):
        actual = p.decisions([row(score=0.75, margin=0.10)], p.FIXED)
        self.assertEqual(actual["counts"],
                         {"correct": 1, "wrong_person_merge": 0, "abstention": 0})

    def test_immediate_predecessor_of_either_boundary_abstains(self):
        for score, margin in ((math.nextafter(0.75, -math.inf), 0.10),
                              (0.75, math.nextafter(0.10, -math.inf))):
            with self.subTest(score=score, margin=margin):
                actual = p.decisions([row(score=score, margin=margin)], p.FIXED)
                self.assertEqual(actual["decisions"][0]["accepted"], None)
                self.assertEqual(actual["counts"]["abstention"], 1)

    def test_all_three_outcomes_are_counted(self):
        rows = [row(), row(expected=p.SPEAKERS[1]), row(score=0.5)]
        actual = p.decisions(rows, p.FIXED)
        self.assertEqual(actual["counts"],
                         {"correct": 1, "wrong_person_merge": 1, "abstention": 1})
        self.assertEqual([d["outcome"] for d in actual["decisions"]],
                         ["correct", "wrong_person_merge", "abstention"])

    def test_selector_rejects_wrong_row_count_or_evaluation_rows(self):
        cases = [[], [row()], [row(), row(), row()],
                 [row(), row(role="evaluation")],
                 [row(role="evaluation"), row(role="evaluation")]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                p.select_policy(rows)

    def test_selector_minimizes_wrong_merges_before_abstentions(self):
        rows = [row(expected=p.SPEAKERS[1], score=0.6),
                row(expected=p.SPEAKERS[1], best=p.SPEAKERS[1], score=0.6)]
        actual = p.select_policy(rows)
        self.assertEqual(len(actual["grid"]), 9)
        self.assertEqual(actual["selected"], {"tau": 0.75, "margin": 0.15})
        self.assertTrue(any(x["counts"]["wrong_person_merge"] == 1
                            and x["counts"]["abstention"] == 0
                            for x in actual["grid"]))

    def test_selector_minimizes_abstentions_then_raises_thresholds(self):
        rows = [row(expected=s, best=s, score=0.7, margin=0.12)
                for s in p.SPEAKERS]
        actual = p.select_policy(rows)
        self.assertEqual(actual["selected"], {"tau": 0.65, "margin": 0.10})
        self.assertEqual(actual["fixed"], p.decisions(rows, p.FIXED))

    def test_all_correct_tie_prefers_highest_tau_and_margin(self):
        rows = [row(expected=s, best=s) for s in p.SPEAKERS]
        actual = p.select_policy(rows)
        self.assertEqual(actual["selected"], {"tau": 0.75, "margin": 0.15})
        self.assertTrue(all(x["counts"]["correct"] == 2 for x in actual["grid"]))

    def test_all_abstention_tie_prefers_highest_tau_and_margin(self):
        rows = [row(expected=s, best=s, score=0.4) for s in p.SPEAKERS]
        actual = p.select_policy(rows)
        self.assertEqual(actual["selected"], {"tau": 0.75, "margin": 0.15})
        self.assertTrue(all(x["counts"]["abstention"] == 2 for x in actual["grid"]))


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.gallery = tuple((s, basis(s)) for s in p.SPEAKERS)

    def score(self, gallery=None, scorer=dot):
        return p.score_row(self.gallery if gallery is None else gallery,
                           basis(p.SPEAKERS[0]), p.SPEAKERS[0],
                           "contiguous", "calibration", scorer)

    def test_vector_checks_reject_nonfinite_wrong_size_and_small_norm(self):
        good = [float(i == 0) for i in range(512)]
        p.valid_vector(good)
        bad = [[0.0] * 512, [1e-12] * 512, [1.0] * 511,
               [math.nan] + [0.0] * 511, [math.inf] + [0.0] * 511]
        for values in bad:
            with self.subTest(first=values[0], length=len(values)):
                with self.assertRaises(ValueError):
                    p.valid_vector(values)

    def test_scoring_keeps_both_cosines_and_margin(self):
        actual = self.score()
        self.assertEqual(actual["scores"], dict(zip(p.SPEAKERS, (1.0, 0.0))))
        self.assertEqual(actual["best_speaker"], p.SPEAKERS[0])
        self.assertEqual(actual["best_score"], 1.0)
        self.assertEqual(actual["margin"], 1.0)

    def test_equal_cosines_use_lexical_speaker_tie_break(self):
        actual = self.score(tuple(reversed(self.gallery)), lambda *_: 0.5)
        self.assertEqual(actual["best_speaker"], min(p.SPEAKERS))
        self.assertEqual(actual["margin"], 0.0)
        self.assertEqual(p.decisions([actual], p.FIXED)["counts"]["abstention"], 1)

    def test_mutable_wrong_size_and_wrong_identity_galleries_rejected(self):
        invalid = [list(self.gallery), self.gallery[:1],
                   (self.gallery[0], self.gallery[0]),
                   (("synthetic-unknown", basis(p.SPEAKERS[0])), self.gallery[1])]
        for gallery in invalid:
            with self.subTest(gallery=gallery), self.assertRaises(ValueError):
                self.score(gallery)

    def test_nonfinite_and_out_of_range_cosines_rejected(self):
        for value in (math.nan, math.inf, -math.inf, 1.00011, -1.00011):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.score(scorer=lambda *_, value=value: value)
        for value in (-1.0001, 1.0001):
            with self.subTest(accepted_limit=value):
                self.assertEqual(self.score(scorer=lambda *_, value=value: value)
                                 ["best_score"], value)


class ProtocolTests(unittest.TestCase):
    def test_call_counts_freeze_order_and_policy_application(self):
        actions, events, cosine_calls = [], [], []

        def embed(speaker, role, arm):
            if role == "evaluation":
                self.assertIn("freeze", actions)
            actions.append((speaker, role, arm))
            return basis(speaker)

        def scorer(left, right):
            self.assertIs(type(left), tuple)
            cosine_calls.append((left, right))
            return dot(left, right)

        def freeze(calibration, rows):
            self.assertEqual(len(actions), 8)
            self.assertTrue(all(item[1] != "evaluation" for item in actions))
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(r["role"] == "calibration" for r in rows))
            self.assertEqual(set(calibration), set(p.ARMS))
            actions.append("freeze")
            return "synthetic-freeze-token"

        result = p.protocol(embed, scorer, freeze,
                            lambda kind, value: events.append((kind, value)))
        self.assertEqual(len(actions), 13)
        self.assertEqual(actions[8], "freeze")
        self.assertEqual(len(set(a for a in actions if isinstance(a, tuple))), 12)
        self.assertTrue(all(a[1] == "evaluation" for a in actions[9:]))
        self.assertEqual(len(cosine_calls), 16)
        self.assertEqual(result["cosine_score_count"], 16)
        self.assertEqual(len(result["all_scores"]), 8)
        self.assertEqual(len(events), 8)
        self.assertTrue(all(kind == "score" for kind, _ in events))
        self.assertEqual(result["frozen_thresholds_sha256"], "synthetic-freeze-token")
        self.assertEqual(result["gallery_sha256_before"], result["gallery_sha256_after"])
        for arm in p.ARMS:
            self.assertEqual(result["calibration"][arm]["selected"],
                             {"tau": 0.75, "margin": 0.15})
            for policy in ("fixed", "calibration_selected"):
                self.assertEqual(result["evaluation"][arm][policy]["counts"],
                                 {"correct": 2, "wrong_person_merge": 0, "abstention": 0})

    def test_failed_freeze_prevents_any_evaluation_embedding(self):
        calls = []

        def embed(speaker, role, arm):
            calls.append(role)
            return basis(speaker)

        with self.assertRaisesRegex(ValueError, "Threshold freeze failed"):
            p.protocol(embed, dot, lambda *_: None, lambda *_: None)
        self.assertEqual(len(calls), 8)
        self.assertNotIn("evaluation", calls)

    def test_enrollment_buffers_are_copied_into_immutable_galleries(self):
        enrollment_buffers = []

        def embed(speaker, role, arm):
            if role == "enrollment":
                values = list(basis(speaker))
                enrollment_buffers.append(values)
                return values
            for values in enrollment_buffers:
                values[:] = [99.0] * len(values)
            return basis(speaker)

        result = p.protocol(embed, dot, lambda *_: "synthetic-freeze-token", lambda *_: None)
        expected = {arm: tuple((s, basis(s)) for s in p.SPEAKERS) for arm in p.ARMS}
        expected_digest = hashlib.sha256(p.canonical(expected)).hexdigest()
        self.assertEqual(result["gallery_sha256_before"], expected_digest)
        self.assertEqual(result["gallery_sha256_after"], expected_digest)
        self.assertTrue(all(r["best_speaker"] == r["expected"] for r in result["all_scores"]))
        self.assertTrue(all(values == [99.0, 99.0] for values in enrollment_buffers))

    def test_adverse_evaluation_cannot_reselect_calibration_policy(self):
        frozen = []

        def embed(speaker, role, arm):
            if role == "evaluation":
                speaker = next(s for s in p.SPEAKERS if s != speaker)
            return basis(speaker)

        def freeze(calibration, rows):
            frozen.append(p.canonical(calibration))
            return "synthetic-freeze-token"

        result = p.protocol(embed, dot, freeze, lambda *_: None)
        self.assertEqual(p.canonical(result["calibration"]), frozen[0])
        for arm in p.ARMS:
            self.assertEqual(result["calibration"][arm]["selected"],
                             {"tau": 0.75, "margin": 0.15})
            self.assertEqual(result["evaluation"][arm]["calibration_selected"]["counts"],
                             {"correct": 0, "wrong_person_merge": 2, "abstention": 0})


if __name__ == "__main__":
    unittest.main()
