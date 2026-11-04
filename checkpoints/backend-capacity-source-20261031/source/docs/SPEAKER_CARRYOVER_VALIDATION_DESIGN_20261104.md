# Prospective validation design for reliable low-intervention speaker carryover

Status: design proposal only. No new inference, collection, enrollment, source separation, threshold calibration or policy change is authorized by this document. Before a future launch, freeze exact corpus identifiers, input hashes, chronological spans, development/confirmation split, code/model/frontend pins, resource envelope and decision rules. This template is not a completed preregistration until those fields are filled.

## Objective and ordering

Maximize useful automatic carryover while treating a wrong identity merge as more costly than a temporary unknown. Measure manual interventions rather than assuming that a cleaner manually chosen reference is an acceptable solution. Preserve the present negative result and all previously exposed data as development evidence only.

1. Resolve the existing frontend-equivalence question with a static source/configuration audit first. If a concrete discrepancy is found, define one isolated correction and a reproducible technical compatibility test. Do not treat an unexplained low score as proof of a frontend bug.
2. Test evidence sufficiency and contamination handling on a newly declared development population before selecting another encoder. Existing longer-support and V2 reports already limit the case for an unqualified duration increase or model swap.
3. Only the chosen, frozen candidate proceeds to a genuinely held-out confirmation population. Changes after seeing confirmation outcomes require a new future confirmation set, not another pass over spent data.

## Data and complete denominators

- Use multiple meetings, speakers, sessions and recording conditions selected by a published deterministic manifest, not by vector yield or scores. Reserve genuinely unseen speakers/meetings for confirmation; explicitly disclose uncertainty about model-training overlap.
- For cross-session identity evaluation, require actual person-level labels that remain valid across sessions. Local diarization labels alone must not be treated as persistent people.
- Use fixed chronological enrollment and query intervals. Withhold designated identities before inference. Keep the original stream, every natural observation, all vetoes and no-output cases in every arm. Do not extend a window until enough favorable references appear.
- Freeze separate adequacy floors for represented known people, independently recorded reference/query opportunities, unknown identities and competing-person galleries. A useful pilot floor is at least 20 represented known people across 10 sessions, with at least three scorable later queries per known person and at least 20 withheld identities; these are exploratory information floors, not proof of a low error rate. If the fixed population fails the floors, stop as insufficient evidence.
- Annotation purity is evaluator-only. It must not select reference crops or suppress queries in the candidate algorithm. Uncovered/overlapped reference outcomes remain explicit, even if technically eligible.

## Limited paired diagnostic arms

Predeclare the complete arms, not an open-ended search. Begin with the same pinned encoder and frontend so other changes are interpretable.

- A: unchanged two-second input, earliest single automatically eligible reference, unchanged decision policy.
- B: same two-second input and same admission population; bounded automatic evidence accumulation using the earliest qualifying observations by a declared chronological rule, up to three distinct non-overlapping source observations. Never select the best-scoring reference for a query. No feedback from predictions into reference admission. Evaluate this as an anonymous identity-memory proposal, not a license to silently assert a real person's name.
- C, only if separately approved: a fixed native-support extension rule on the same observation anchors, with a predeclared upper duration cap and no padding, annotation-selected crop, or replacement when support is insufficient. Record content change as a confound; it is not a pure duration intervention. The earlier extension result requires explicit purity/coverage checks and makes it inappropriate to assume longer is better.

Do not combine B and C before their separate effects are understood. If the source audit justifies a frontend correction, substitute one expressly declared frontend arm rather than multiplying a large search. A later model alternative requires a new bounded comparison and its own score-space/calibration treatment. ERes2NetV2 is not automatically reconsidered after its retained NO_GO.

## Conservative automatic behavior

- Keep a quarantine/unknown state for inadequate, conflicting or contaminated evidence. Do not turn a low score into a new identity merge or repeatedly request user labeling for every unknown.
- Preserve original overlap/cannot-link constraints, source-independence checks, all competitors and real runner-up availability. Test one-person-gallery behavior separately; the existing sentinel margin cannot certify separation from alternatives.
- If automatic aggregation is studied, freeze the evidence admission, decay, capacity and revocation rules in advance. Prefer independent recurring observations, and prevent one mistaken suggestion from recursively enrolling itself or joining transitive clusters.
- Identity names and production enrollment authority stay distinct from anonymous acoustic continuity. Manual labeling can be evaluated as a separate user-triggered option, but is not hidden in the primary low-intervention arm.

## Metrics and pass/fail rules

For every arm and every chronological prefix, report:

- All natural inputs; unavailable, vetoed, unscorable and abstained counts; time to first useful stable identity; fraction remaining unknown.
- Correct/wrong known-person suggestions, withheld false accepts, unscorable emissions and real runner-up conflicts. Zero emissions has undefined precision.
- Identity switches, mixed-person clusters, reference contamination/drift, retractions, false-merge event duration and persistence. A later correction does not erase an earlier unsafe prefix.
- User prompts/interventions per recording hour and per known person, plus compute and latency. Ground-truth annotation labor is evaluation work, never disguised as product manual-intervention cost.
- Cluster/session-aware uncertainty, because pairs and prefixes sharing observations are dependent. Do not inflate the sample size by counting all pair combinations as independent.

Freeze a safety budget and a minimum useful-coverage gain before confirmation, based on the intended application. Promote only if the candidate meets both; coverage gains cannot compensate for failing the safety bound. For scale, zero errors in one withheld query has a one-sided 95% binomial upper bound of 95%. To bound a rate below 0.1% with zero errors would require at least 2,995 genuinely independent trials under the simple binomial assumptions, and dependent speaker/session data need an appropriate clustered design instead. This illustration is not a proposed claim that pairwise counts satisfy that requirement.

Keep threshold calibration, if ever authorized, confined to separate development data with its objective fixed in advance. Never lower 0.75 to accommodate these three exposed queries. Confirm once on the frozen held-out population; retain every adverse result and stop on technical failures without swapping samples.

## Stopping and scope

The next approved study ends after its fixed complete population and predeclared analyses, or on an operational stop. Underpowered, unchanged or negative results are valid outcomes, not permission to extend the corpus adaptively. Until an independently reviewed study establishes both safety and usefulness, the production policy and model remain unchanged.
