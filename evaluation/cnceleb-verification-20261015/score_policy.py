"""Pure score-policy reference; synthetic tests only, no audio/model or file I/O.

This module accepts already-computed cosine scores under opaque labels. It does
not acquire data, extract embeddings, replay the private pilot, or update models.
Python 3.10+; standard library only.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Callable, Iterable


ROLES = ("calibration", "evaluation")
GRID = tuple((t, m) for t in (.55, .65, .75, .85, .95) for m in (.05, .10, .15))
COUNTS = ("known_correct", "known_wrong", "known_abstain",
          "unknown_false_accept", "unknown_abstain")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def opaque(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


@dataclass(frozen=True)
class Policy:
    tau: float
    margin: float

    def __post_init__(self):
        require(math.isfinite(self.tau) and -1 <= self.tau <= 1, "Invalid threshold")
        require(math.isfinite(self.margin) and 0 <= self.margin <= 2, "Invalid margin")


BASELINE = Policy(.75, .10)


@dataclass(frozen=True)
class Probe:
    role: str
    query_id: str
    truth_id: str
    scores: tuple[tuple[str, float], ...]

    def __post_init__(self):
        require(self.role in ROLES, "Invalid role")
        require(opaque(self.query_id) and opaque(self.truth_id), "Opaque IDs required")
        require(type(self.scores) is tuple and len(self.scores) == 7, "Seven immutable scores required")
        require(all(type(pair) is tuple and len(pair) == 2 for pair in self.scores), "Immutable score pairs required")
        require(all(opaque(label) and isinstance(value, (int, float)) and
                    math.isfinite(value) and -1.0001 <= value <= 1.0001
                    for label, value in self.scores), "Invalid cosine scores")
        require(len({label for label, _ in self.scores}) == 7, "Duplicate gallery identity")

    @property
    def gallery(self):
        return frozenset(label for label, _ in self.scores)

    @property
    def known(self):
        return self.truth_id in self.gallery

    @property
    def ranking(self):
        return tuple(sorted(self.scores, key=lambda pair: (-pair[1], pair[0])))


def accept(row: Probe, policy: Policy | None):
    """Inclusive threshold/margin gates; exact top-score ties always abstain."""
    if policy is None:
        return None
    first, second = row.ranking[:2]
    margin = first[1] - second[1]
    return first[0] if margin > 0 and first[1] >= policy.tau and margin >= policy.margin else None


def summarize(rows: Iterable[Probe], policy: Policy | None):
    counts = dict.fromkeys(COUNTS, 0)
    for row in rows:
        accepted = accept(row, policy)
        if row.known:
            outcome = "known_abstain" if accepted is None else \
                "known_correct" if accepted == row.truth_id else "known_wrong"
        else:
            outcome = "unknown_abstain" if accepted is None else "unknown_false_accept"
        counts[outcome] += 1
    known = sum(counts[k] for k in ("known_correct", "known_wrong", "known_abstain"))
    unknown = counts["unknown_false_accept"] + counts["unknown_abstain"]
    total = known + unknown
    accepted = total - counts["known_abstain"] - counts["unknown_abstain"]
    divide = lambda n, d: n / d if d else None
    return {"counts": counts, "denominators": {"known": known, "unknown": unknown, "total": total},
            "false_person_assignments": counts["known_wrong"] + counts["unknown_false_accept"],
            "accepted": accepted,
            "rates": {"known_recovery": divide(counts["known_correct"], known),
                      "known_wrong": divide(counts["known_wrong"], known),
                      "unknown_false_accept": divide(counts["unknown_false_accept"], unknown),
                      "known_abstention": divide(counts["known_abstain"], known),
                      "unknown_rejection": divide(counts["unknown_abstain"], unknown),
                      "false_person": divide(counts["known_wrong"] + counts["unknown_false_accept"], total),
                      "accepted_coverage": divide(accepted, total),
                      "overall_abstention": divide(total - accepted, total),
                      "correct_person_precision": divide(counts["known_correct"], accepted)}}


def validate_role(rows: Iterable[Probe], role: str):
    rows = tuple(rows)
    require(role in ROLES and len(rows) == 20 and all(row.role == role for row in rows),
            "Exactly 20 same-role probes required")
    require(len({row.query_id for row in rows}) == 20, "Duplicate query")
    gallery = rows[0].gallery
    require(all(row.gallery == gallery for row in rows), "Gallery changed within role")
    frequencies = {}
    for row in rows:
        frequencies[row.truth_id] = frequencies.get(row.truth_id, 0) + 1
    identities = set(frequencies)
    require(len(identities) == 10 and set(frequencies.values()) == {2}, "Two probes per identity required")
    require(identities & gallery == gallery and len(identities - gallery) == 3,
            "Seven known and three unknown identities required")
    return rows


def select_policy(calibration_rows: Iterable[Probe]):
    """No evaluation input is accepted. None is the always-abstain candidate."""
    rows = validate_role(calibration_rows, "calibration")
    candidates = tuple(Policy(t, m) for t, m in GRID) + (None,)

    def objective(policy):
        report = summarize(rows, policy)
        return (report["false_person_assignments"], -report["counts"]["known_correct"],
                -(policy.tau if policy is not None else 2),
                -(policy.margin if policy is not None else 2))

    return min(candidates, key=objective)


@dataclass(frozen=True)
class Freeze:
    selected: Policy | None
    calibration_digest: str
    calibration_identities: tuple[str, ...]
    calibration_queries: tuple[str, ...]

    def payload(self):
        """Fresh aggregate metadata for a caller's durable freeze callback."""
        return {"schema": "score-policy-freeze-v1",
                "selected": None if self.selected is None else [self.selected.tau, self.selected.margin],
                "calibration_digest": self.calibration_digest,
                "calibration_identity_count": len(self.calibration_identities),
                "calibration_probe_count": len(self.calibration_queries),
                "candidate_count": len(GRID) + 1}


def compare(calibration_rows: Iterable[Probe],
            load_evaluation: Callable[[], Iterable[Probe]],
            persist_freeze: Callable[[dict], str]):
    """Freeze first, then invoke the lazy evaluation loader exactly once.

    The caller must durably persist payload and return a nonempty receipt. This
    pure helper verifies call ordering and disjoint labels; it cannot prove a
    callback wrote to disk, a caller never saw evaluation data, or labels denote
    distinct real people. The private experiment supplied its own event evidence.
    """
    calibration = validate_role(calibration_rows, "calibration")
    selected = select_policy(calibration)
    canonical = [[row.role, row.query_id, row.truth_id, sorted(row.scores)]
                 for row in sorted(calibration, key=lambda row: row.query_id)]
    digest = hashlib.sha256(json.dumps(canonical, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    frozen = Freeze(selected, digest, tuple(sorted({row.truth_id for row in calibration})),
                    tuple(sorted(row.query_id for row in calibration)))
    receipt = persist_freeze(frozen.payload())
    require(isinstance(receipt, str) and bool(receipt.strip()), "Missing durable freeze receipt")
    evaluation = validate_role(load_evaluation(), "evaluation")
    require(not set(frozen.calibration_identities) & {row.truth_id for row in evaluation},
            "Identity leakage across roles")
    require(not set(frozen.calibration_queries) & {row.query_id for row in evaluation},
            "Query leakage across roles")
    reports = {role: {"fixed_primary": summarize(rows, BASELINE),
                      "calibrated_study_only": summarize(rows, frozen.selected)}
               for role, rows in zip(ROLES, (calibration, evaluation))}
    return {"freeze": frozen.payload(), "freeze_receipt": receipt, "roles": reports}
