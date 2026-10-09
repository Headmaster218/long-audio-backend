"""Model-free, OFFLINE-ONLY correction-memory experiment. Not wired into Store.

Verified whole-evidence anchors and automatic observations are separate. An
accepted automatic observation never becomes a verified anchor, and automatic
support cannot train its own descendants. Scores are cosine, not probabilities.
This module intentionally has no automatic enrollment or person merge API.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite, sqrt


def normalized(values):
    values = tuple(float(x) for x in values)
    if not values or any(not isfinite(x) for x in values):
        raise ValueError("Non-finite or empty embedding")
    scale = max(abs(x) for x in values)
    if scale == 0:
        raise ValueError("Zero embedding")
    scaled = tuple(x/scale for x in values)
    norm = sqrt(sum(x*x for x in scaled))
    return tuple(x/norm for x in scaled)


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError("Embedding dimension mismatch")
    return sum(x*y for x, y in zip(a, b))


@dataclass(frozen=True)
class Evidence:
    observation_id: str
    sequence: int
    vector: tuple[float, ...]
    model_version: str
    source_unit: str
    clean_seconds: float
    overlap_excluded: bool
    scope: str = "whole_embedding_support"


@dataclass(frozen=True)
class Decision:
    person_id: str | None
    status: str
    best_score: float | None
    anchor_score: float | None
    margin: float | None
    anchor_ids: tuple[str, ...] = ()
    automatic_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Correction:
    event_id: str
    sequence: int
    action: str
    person_id: str
    evidence: Evidence
    previous_prediction: str | None


class CorrectionMemory:
    """Bounded replay state, with explicit provenance and event retraction.

    Production needs transactional event persistence, group scoping, actual
    interval lineage and access control. This class supplies none implicitly.
    Caller persists exported events privately if durable replay is required.
    """
    def __init__(self, model_version, dimension, strategy="centroid", *,
                 max_people=256, max_anchors=8, max_automatic=4,
                 max_negative=8, max_events=4096):
        if strategy not in {"centroid", "verified_multi_anchor", "anchored_auto"}:
            raise ValueError("Unknown strategy")
        if not model_version or dimension < 1:
            raise ValueError("Explicit model binding required")
        if not 1 <= max_anchors <= 8 or not 0 <= max_automatic <= 4:
            raise ValueError("Memory limits exceed declared policy")
        if not 1 <= max_people <= 256 or not 1 <= max_negative <= 8 or not 1 <= max_events <= 4096:
            raise ValueError("Positive state bounds required")
        self.model_version, self.dimension, self.strategy = model_version, dimension, strategy
        self.max_people, self.max_anchors = max_people, max_anchors
        self.max_automatic, self.max_negative, self.max_events = max_automatic, max_negative, max_events
        self.anchors = {}
        self.automatic = {}
        self.negatives = {}
        self.events = []
        self.revoked = set()
        self.cannot_links = {}
        self.revoked_constraints = set()
        self.last_sequence = -1
        self.latest = None
        self.latest_decision = None

    def _validate(self, evidence):
        if evidence.model_version != self.model_version or len(evidence.vector) != self.dimension:
            raise ValueError("Embedding model/version/dimension mismatch")
        if (type(evidence.sequence) is not int or evidence.sequence < 0
                or not isinstance(evidence.observation_id, str) or not evidence.observation_id
                or not isinstance(evidence.source_unit, str) or not evidence.source_unit):
            raise ValueError("Missing observation provenance")
        vector = normalized(evidence.vector)
        return Evidence(evidence.observation_id, evidence.sequence, vector,
                        evidence.model_version, evidence.source_unit,
                        evidence.clean_seconds, evidence.overlap_excluded, evidence.scope)

    def _eligible(self, evidence):
        return (evidence.overlap_excluded is True
                and type(evidence.clean_seconds) in (int, float) and isfinite(evidence.clean_seconds)
                and evidence.clean_seconds >= 2.0)

    @staticmethod
    def _rank(scores):
        ranked = sorted(scores, key=lambda p: (-scores[p], p))
        if not ranked:
            return None, None, None
        best = ranked[0]
        margin = scores[best] - scores[ranked[1]] if len(ranked) > 1 else 2.0
        return best, scores[best], margin

    def _negative_veto(self, person, evidence, score):
        return any(n.observation_id == evidence.observation_id or
                   (cosine(n.vector, evidence.vector) >= .55 and
                    cosine(n.vector, evidence.vector) >= score - .10)
                   for n in self.negatives.get(person, []))

    def query(self, evidence):
        evidence = self._validate(evidence)
        if evidence.sequence <= self.last_sequence:
            raise ValueError("Queries must be strictly chronological")
        self.last_sequence = evidence.sequence
        self.latest = evidence
        self.latest_decision = self._decide(evidence)
        decision = self.latest_decision
        # Only a fresh, direct verified-anchor decision can be retained. No chain.
        if (self.strategy == "anchored_auto" and decision.status == "verified_anchor"
                and decision.anchor_score >= .65 and decision.margin >= .20
                and self.max_automatic):
            person = decision.person_id
            samples = self.automatic.setdefault(person, [])
            if (not any(e.source_unit == evidence.source_unit for e in samples)
                    and not any(e.source_unit == evidence.source_unit for e in self.anchors[person])):
                samples.append(evidence)
                del samples[:-self.max_automatic]
        return decision

    def _decide(self, evidence):
        if not self._eligible(evidence):
            return Decision(None, "ineligible_evidence", None, None, None)
        scores, closest = {}, {}
        for person, anchors in self.anchors.items():
            if not anchors:
                continue
            closest[person] = max(cosine(evidence.vector, a.vector) for a in anchors)
            if self.strategy == "centroid":
                try:
                    mean = normalized(sum(values) for values in zip(*(a.vector for a in anchors)))
                except ValueError:
                    return Decision(None, "inconsistent_verified_anchors", None, None, None)
                scores[person] = cosine(evidence.vector, mean)
            else:
                scores[person] = closest[person]
        best, score, margin = self._rank(scores)
        if best is None:
            return Decision(None, "no_verified_identity", None, None, None)
        threshold = .56 if self.strategy == "centroid" and len(self.anchors[best]) > 1 else .55
        aids = tuple(a.observation_id for a in self.anchors[best])
        if self._negative_veto(best, evidence, score):
            return Decision(None, "negative_evidence_veto", score, score, margin, aids)
        if score >= threshold and margin >= .10:
            return Decision(best, "verified_anchor", score, score, margin, aids)
        # Do not resolve anchor ambiguity using self-predicted observations.
        if self.strategy == "anchored_auto" and .45 <= score < .55 and margin >= .10:
            expanded = dict(scores)
            for person, samples in self.automatic.items():
                independent = [a for a in samples if a.source_unit != evidence.source_unit]
                if independent:
                    expanded[person] = max(expanded[person], max(cosine(evidence.vector, a.vector) for a in independent))
            winner, expanded_score, expanded_margin = self._rank(expanded)
            if winner == best and expanded_score >= .55 and expanded_margin >= .10:
                if self._negative_veto(best, evidence, expanded_score):
                    return Decision(None, "negative_evidence_veto", expanded_score, score, expanded_margin, aids)
                return Decision(best, "automatic_supported", expanded_score, score, expanded_margin, aids,
                                tuple(a.observation_id for a in self.automatic.get(best, [])))
        status = "ambiguous" if score >= threshold else "uncertain" if score >= .45 else "unrecognized"
        return Decision(None, status, score, score, margin, aids)

    def verify_latest(self, person_id, event_id, *, whole_evidence_verified=False):
        """A text-only correction is insufficient to certify a pooled embedding."""
        return self._correction(person_id, event_id, "verified_anchor", whole_evidence_verified)

    def reject_latest(self, person_id, event_id, *, whole_evidence_verified=False):
        return self._correction(person_id, event_id, "negative_example", whole_evidence_verified)

    def _correction(self, person, event_id, action, verified):
        if not person or not event_id or verified is not True:
            raise ValueError("Explicit whole-embedding evidence verification required")
        if (self.latest is None or not self._eligible(self.latest) or self.latest.scope not in
                {"whole_embedding_support", "oracle_clean_entire_clip"}):
            raise ValueError("No eligible current evidence")
        if len(self.events) >= self.max_events:
            raise OverflowError("Correction event capacity reached; archive explicitly")
        if any(e.event_id == event_id for e in self.events):
            raise ValueError("Duplicate correction event")
        if action == "verified_anchor":
            if person not in self.anchors and len(self.anchors) >= self.max_people:
                raise OverflowError("Identity capacity reached")
            if len(self.anchors.get(person, [])) >= self.max_anchors:
                raise OverflowError("Verified anchor capacity reached")
            for pid, anchors in self.anchors.items():
                if any(a.observation_id == self.latest.observation_id for a in anchors):
                    raise ValueError("Observation already verified; revoke its event before correcting")
            if any(n.observation_id == self.latest.observation_id for n in self.negatives.get(person, [])):
                raise ValueError("Observation explicitly excluded from this person")
        elif any(a.observation_id == self.latest.observation_id for a in self.anchors.get(person, [])):
            raise ValueError("Observation already verified for this person; retract first")
        elif len(self.negatives.get(person, [])) >= self.max_negative:
            raise OverflowError("Negative evidence capacity reached")
        event = Correction(event_id, self.last_sequence, action, person, self.latest,
                           self.latest_decision.person_id)
        self.events.append(event)
        self._rebuild()
        return event

    def retract(self, event_id):
        if event_id in self.revoked or not any(e.event_id == event_id for e in self.events):
            raise ValueError("No active correction event")
        self.revoked.add(event_id)
        self._rebuild()

    def _rebuild(self):
        self.anchors, self.negatives = {}, {}
        for event in self.events:
            if event.event_id not in self.revoked:
                target = self.anchors if event.action == "verified_anchor" else self.negatives
                target.setdefault(event.person_id, []).append(event.evidence)
        # Conservative invalidation: reference changes cannot preserve stale auto support.
        self.automatic.clear()

    def add_cannot_link(self, person_a, person_b, event_id):
        if person_a == person_b or person_a not in self.anchors or person_b not in self.anchors:
            raise ValueError("Cannot-link requires distinct existing verified people")
        if len(self.cannot_links) >= self.max_events:
            raise OverflowError("Constraint capacity reached")
        if not event_id or event_id in self.cannot_links:
            raise ValueError("Unique constraint event ID required")
        self.cannot_links[event_id] = (frozenset((person_a, person_b)), self.last_sequence)

    def retract_constraint(self, event_id):
        if event_id not in self.cannot_links or event_id in self.revoked_constraints:
            raise ValueError("No active constraint event")
        self.revoked_constraints.add(event_id)

    def assert_merge_allowed(self, person_a, person_b):
        # A guard only: this experiment never changes canonical identities itself.
        if any(pair == frozenset((person_a, person_b)) for eid, (pair, _)
               in self.cannot_links.items() if eid not in self.revoked_constraints):
            raise ValueError("Explicit different-person constraint forbids merge")
        raise ValueError("Identity merging is deliberately unavailable in this experiment")

    def summary(self):
        return {"strategy": self.strategy, "people": len(self.anchors),
                "verified_anchors": sum(map(len, self.anchors.values())),
                "automatic_observations": sum(map(len, self.automatic.values())),
                "negative_examples": sum(map(len, self.negatives.values())),
                "correction_events": len(self.events), "retracted_events": len(self.revoked),
                "cannot_links": len(self.cannot_links) - len(self.revoked_constraints), "last_sequence": self.last_sequence}
