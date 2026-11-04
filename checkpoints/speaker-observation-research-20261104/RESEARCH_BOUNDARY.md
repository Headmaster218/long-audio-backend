# Next research boundary after the frozen Chinese pilot

The completed C/T pilot is now development evidence, not a reusable holdout. Its threshold and outcomes are sealed at Git commit dfa31b3b9906d9b2be0dd4855488242eb5d47b67. This follow-on work must not select, tune or claim validation on those people/recordings again.

## Minimal improvement under investigation

Reuse the existing product's `verified_identity.centroid_score` (normalized f64 mean, maximum eight reviewed references), rather than introduce a different centroid or revive the old correction-memory experiment's thresholds. The real missing prerequisite is a reliable collection of multiple correctly attributed, adequately supported observations, with explicit source-frame lineage and session separation.

A pure manifest/trial planner is being implemented separately from production. It will reject leakage and duplicated weighting, retain excluded inputs in the audit denominator, and distinguish externally annotated reference observations from automatic predictions. It cannot prove acoustic purity or trustworthy identities from caller-supplied metadata. Its output cannot authorize enrollment or production writes.

## Prospective comparison, not yet an executable protocol

1. Select a new cohort before scores. Exclude every person ID and recording used in all prior project corpora where mapping is available; disclose unresolved cross-corpus aliases/model-training overlap.
2. Freeze development/calibration and held-out people, all derivative grouping, recording-event boundaries, source provenance, annotated single-speaker intervals, and resource caps. Do not substitute channel/device changes for new sessions.
3. Compare a fixed earliest eligible single reference against a fixed number of earlier, distinct-session verified references using the SAME centroid implementation and SAME later query set. Preserve missing-reference and unusable-query outcomes; no winner-selecting gallery construction.
4. Independently calibrate each arm only on the new calibration people under one prespecified open-set objective and a fixed margin/policy. Lock both policies before opening the new holdout. Neither 0.46 nor historical 0.55 is automatically transferable.
5. Count known correct, wrong-person emissions, refusal, unknown acceptance, unavailable inputs, per-person/per-session stability, and maximum wrong-person streaks. Count people/recording events separately from repeated frames or pairs. Report resource and annotation effort.
6. Keep clean-reference oracle performance separate from automatic segmentation/grouping performance. A gain in the oracle arm does not prove automatic collection, poisoning resistance, overlap robustness or low-intervention long-term use.

Exact roster, corpus, annotation evidence, gallery size, observation count, calibration rule and acquisition/inference budgets require a final frozen protocol after source feasibility. No new acquisition or model run is authorized by this draft alone.

## Safety and operational limits

No audio is deleted. No query prediction becomes its own reference or a descendant's anchor. No identity merge occurs. Ambiguous, overlapping, unsupported or conflicting evidence remains unresolved. This research does not weaken production support proofs, introduce a purity assertion from native diarization, or change the production threshold. Product 1,046-file source integrity was verified after the previous pilot; any later production changes need their own tests and evidence.

Practical low-intervention deployment still needs an independently evaluated means to obtain those reliable observations, durable bounded state/recovery, explicit retention, and safe unknown handling. A manifest validator alone does not deliver these properties.

## Confounds and resource reporting

A single two-second reference versus several two-second references changes both the observation count and total enrollment speech. Any gain would be attributable to that combined intervention, not averaging alone. Do not introduce a longer-single-reference arm without first validating the same encoder's variable-length contract and freezing that additional arm. Keep each arm's exact source frames and original audio unchanged; count encoder calls, retained bytes, reference annotation minutes, decision latency, and unknown-person exposure. Repeated clean observations from one recording event may help within-session recognition but cannot be reported as longitudinal or independent-session evidence.

For an initial implementation test, at most eight references per person reuses the existing product limit. This bound is a capacity choice, not a calibrated accuracy setting. The research planner is capped at 4 MiB JSON, 4,096 observations and 256 declared people; it performs no model inference or retention mutation. Actual download/inference ceilings belong in a later source-specific execution release, not this documentation draft.
