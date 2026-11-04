# Independent calibration pilot: prospective source backup

Status 2026-11-04 UTC: preparation only. The single bounded official SMIIP-TV acquisition is underway; the complete frozen 108-input bundle does not yet exist. No real pilot model constructor, threshold fit or holdout evaluation has run. This checkpoint is not launch approval or a production-ready release.

The prior frontend comparison remains a negative practical result. Its adapter passed but all18 known and6 unknown queries were refused; no threshold change followed. See ../frontend-compatibility-research-20261104/RESULT_REPORT.md. Threshold provenance and the broader design are in ../threshold-calibration-design-20261104/.

This new pilot proposes32 speaker IDs, split into C/T with6 known and10 unknown each. References and queries use different days; C uses May2020 and T June2020. Only C primary queries may select one scalar threshold, with .10 rival margin fixed and .75 baseline retained. Extra queries are descriptive only. If no useful candidate exists, T stays unexecuted. At most108 distinct crops plus8 fixed repeat encodings are planned. Production defaults and all banks remain unchanged.

SMIIP-TV is Mandarin mobile prompted/free-text speech, not a meeting or automatic-diarization dataset. Official author code identifies the date fields. Public metadata do not prove cross-corpus real-person, household/device/domain or original model-training disjointness. No prompt text is supplied to verify text separation. All these boundaries remain explicit.

Acquisition has one cumulative8GiB compressed-transfer cap including the prior4MiB probe,30-minute total wall limit,1200CPU-second limit and128MiB retained-source cap. The full49,951,256,989-byte archive is not downloaded or digest-verified. Only predeclared candidates are retained; fixed format/length checks select the earliest qualifying candidate before any scores. Cap or unresolved earlier-candidate failures stop rather than silently changing the cohort.

Model-free controls currently pass:38 scoring/orchestration and26 native-runner tests independently checked; one additional synthetic preparation fixture checks all108 input bindings and duplicate-history/source rejection. These are software controls, not real recognition evidence. Final source/data pins and release review remain necessary before any pilot inference.

Code uses the prior same-host pinned runtime/helpers and explicit input files. It is a recoverable research source checkpoint, not a portable-runtime claim. The final protocol must pin every input, run template and source. C must finish and seal before selection; a successful immutable choice lock must exist before T is released. T predictions seal before its truth file is summarized. Identity metadata are visible during preparation, so this is not a human-blind claim.

The official corpus source is https://www.openslr.org/156/ and the archive is https://openslr.trmal.net/resources/156/SMIIP-TV_v2.0.tar.gz. Follow the official attribution and CC BY-SA3.0 license. No audio, models, transcripts, per-person vectors, age or gender tables are included here.
