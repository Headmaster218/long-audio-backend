# Speaker observation research after the frozen calibration pilot

## What this checkpoint adds

- Read-only failure diagnosis with exact original source/crop and sealed-score checks. It does not generate new predictions on the exposed holdout.
- Version-pinned open-source comparisons and two separate future study designs: within-meeting segmentation/observations (M) and cross-date identity (D).
- A small executable, model-free **D-domain prospective trial planner**, with synthetic adversarial tests. It preserves the complete input ledger and denominators, rejects duplicate weighting/leakage and marks incomplete trials blocked.

The product already supports up to eight reviewed references via its normalized float64 centroid. The missing prerequisite is a trustworthy collection of correctly attributed, adequately supported observations. This checkpoint does not replace the centroid, change production defaults, add automatic enrollment, or demonstrate improved speaker accuracy.

## Findings and limits

The frozen T set remains 4/12 known queries correct and 8/12 refused. Refused crops were not generally quieter or more silent; geometry varies across people and sessions. Some cases motivate studying reference representativeness, but others are inconsistent even across queries. No single cause or aggregation advantage has been established.

The next real experiment needs new data, a frozen source-specific protocol, verified annotation/identity boundaries, and resource release. AliMeeting's raw speaker labels warrant further provenance checking; per-meeting RTTM numbering is not sufficient for cross-meeting identity. A 3.67 GB official Eval archive was verified by HEAD only, not downloaded. The remaining official SMIIP evaluation roster has at most 26 unused candidate IDs after the earlier 32, before any further exclusions; the previously suggested 64-person plan is not available from that roster.

The D planner deliberately refuses same-session and same-date reference/query reuse. It cannot run M's within-meeting experiment. M needs a separately specified within-meeting contract; relabeling turns as sessions is not an acceptable workaround. Declared hashes and annotations are not cryptographic provenance or acoustic purity proof, and contract readiness is not experimental success.

## Reproducible components

- `diagnosis/`: public report, aggregate geometry/signal diagnostics, integrity hashes, reproducible script and five synthetic tests. Original waveform/vector artifacts are not included.
- `contract/`: pure JSON planner, schema documentation and synthetic tests. It has no model, scoring or filesystem APIs.
- `methods/`: report, future protocol drafts and exact source revisions. Third-party source copies remain outside this backup; links and hashes are provided instead.
- `RESEARCH_BOUNDARY.md`: reuse prohibition, confounds, resource considerations and production limits.

No raw audio, embeddings, models, transcripts, database, keys or demographic tables are in this checkpoint. No deletion or identity merge was performed. Unattended lifecycle orchestration and cross-boundary ASR/context remain separate unfinished product work.

## Validation

65 contract tests and five diagnosis tests pass. Independent audit found an exact crop-to-source alias leak in the initial 59-test version; the repair and original failure evidence are retained. Six independent alias/self-crop regressions passed after repair. The 1,046 original product files rehash unchanged. These are model-free engineering checks, not a new speech-quality result.
