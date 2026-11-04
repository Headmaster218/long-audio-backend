# Fixed frontend compatibility result, 2026-11-04

## Decision

The fixed test-source-derived candidate did **not improve usable identity decisions** on this independent small fixture. Keep production unchanged. The adapter compatibility prerequisite passed, so this negative decision result is not explained by an obviously mismatched comparison adapter within the predeclared tolerances. It does not establish the encoder's exact training frontend or prove a general model limitation.

Both frontends abstained on all 18 known-person queries and rejected all 6 withheld-person queries. Neither emitted an identity. Thus known-person recall and total emission coverage were both zero; emission precision is undefined. Wrong known-person emissions and unknown false accepts were zero because nothing was emitted. Actual identity merges were not performed or evaluated. Zero of six unknown accepts does not demonstrate a low rare-error rate.

Both frontends ranked the correct person first for all 18 known queries. This shows useful relative separation in this small read-English fixture, despite failing the fixed absolute acceptance threshold. It does not authorize lowering that threshold.

## Frozen experiment

The prospective implementation and sample plan were backed up before inference at Git commit 594b0e44861886dd66c94e87e4fdd37367975dca. There was one complete run, measurement-01, with no retry, parameter scan, sample replacement, alternate reference selection, or threshold change.

Independence means previously unused in the bounded prior-project inventory, not proven exclusion from model training or cross-corpus physical-person identity. Eight LibriSpeech test-clean speakers supplied 36 distinct utterances, each a deterministic central two-second crop. Six known speakers each had two references and three queries; two withheld speakers had three queries each. Primary decisions used only the first reference per known speaker: 24 queries and 144 dependent reference-query comparisons, including 18 same-person comparisons. The second references were diagnostic only: 288 total dependent comparisons, including 36 same-person comparisons. No query labels were supplied to the predictor; metadata was visible to the experiment designers, so no human-blinding claim is made.

The same exact 512-D ERes2Net ONNX and native ORT bytes were used for all stages:

- A: actual native Sherpa frontend, snip_edges=False and high_freq=-400 (7600 Hz).
- A-prime: explicit KNF frontend with the same options, through the fixed C adapter and exact native ORT library. This was only an adapter prerequisite.
- B: explicit KNF candidate, snip_edges=True and high_freq=0 (8000 Hz).

All other declared settings stayed fixed. The decision threshold remained 0.75 and rival margin 0.10. The primary contrast changes two frontend options together; it does not isolate which caused any change. Feature-only cutoff contrasts used matched grids. Native 200-frame and candidate 198-frame matrices were not truncated or interpolated into a false alignment.

## Compatibility and completeness

The 36 native raw-feature matrices and explicit same-profile matrices were exactly equal numerically, with maximum absolute difference zero. The normalized A/A-prime embedding maximum absolute difference was 3.371969796717167e-7 and maximum cosine gap was 2.3544499683225695e-12, within the prospectively frozen bounds. Their fixed winners and decisions agreed for all 24 queries. All repeatability gates passed. Candidate execution occurred only after the bridge gates passed.

All three stages completed 36 first encodings plus four fixed repeat encodings: 120 attempted and 120 completed calls, three model constructions. There were no unavailable query outputs. The 144 explicit feature-only computations covered the four predeclared option profiles. Saved predictions were sealed before labeled summary generation.

The run ended normally after 14.15 seconds and 20.58 aggregate child CPU seconds. Sampled peak owned RSS was 189,460,480 bytes, and allocated output was 12,832,768 bytes. These are sampled/recorded measurements, not a continuous kernel resource proof. All three original process handles were positively waited, exited zero, required no termination, and had absent owned process groups at cleanup in their execution namespace. This is not an independent host-wide audit of retired PID namespaces.

## Descriptive changes, not calibrated improvement

Across 18 dependent known-query comparisons, B minus A true-person score had median +0.0289503, increasing in 14/18. True-versus-best-rival margin had median +0.0159075, increasing in 11/18. However, the best score for withheld people also increased, by median +0.0353703 across six queries. Score increases alone therefore cannot establish safer or better recognition. All 24 fixed decisions remained below-threshold abstentions in both arms.

No ROC, EER, tuned threshold, selected best reference, multireference decision arm, or extra encoder was added after observing these results.

## What this says about the original failure

The earlier AMI known-speaker miss was not sufficient to identify a cause. This separate clean read-English fixture again gives complete abstention, even though the correct person ranks first. The two frontend changes do not solve that fixed-policy coverage problem. Annotation-overlap pollution is therefore not a necessary explanation for every abstention, but duration, phonetic content, recording conditions, reference variability, model/language domain and acceptance-policy calibration remain confounded. The experiment does not prove all crops are pure speech and does not isolate a short-duration effect.

The historical training-recipe audit gives a rationale for B, but exact installed weights remain unbound to a training commit/configuration/environment. B is not established as the correct training frontend. No production vector-space migration or bank mixing is justified.

## Remaining work and stopping boundary

This bounded question is answered. Do not repeat threshold or frontend scans on these same 24 queries. A separate prospective calibration/evaluation design would need disjoint speakers, enough unknown-person impostor trials, representative meeting/Chinese/audio conditions, and explicit false-accept risk targets. Conservative quality gating and bounded multiple-reference evidence remain candidates for a separately frozen test, not demonstrated fixes here.

Unattended operation still lacks integrated next-session scheduling, safe boundary/drain/hot-switch handling, clear recovery for uncertain writes and failed inference, storage/old-attempt lifecycle policy and unified status. Cross-boundary ASR context and speaker identity continuity, independent-server installation, Android device behavior, Chinese/noisy/cross-talk accuracy and long-duration operation remain unqualified. This research run is same-host only.

Product source and production defaults were not changed. All 1,046 original frozen product-source files were rehashed unchanged after the run. Original runtime files and frozen input bytes remain covered by the terminal integrity audit.

Preparation failures were retained: initial unknown-label contract handling, resource-test fixture reserve failure, cleanup/admission/array-completeness and signed-zero reporting defects were corrected before the freeze. Final model-free controls passed 15 execution and 10 metric tests. Those are harness checks, not recognition-quality samples.

## Evidence hashes

- Frozen experiment: fbe31b4d31c8bb43af745618f93bda75ccda9e6e11e7de6254471e3a23b5bc3f
- Frozen source: 4c324be6150b02d513cbbd867acfd1910e3126586ab719e7ec8e5a277e4a7ed7
- Terminal receipt: fca43b46d413cce36e550bacbfd38c13282529b98080192d6217699549851f54
- Summary: b863f6dd4e61a7492ca516dc6f6460c10df8b59d6a87aa1e2f4b0fcaf0ef879e
- Prediction seal: 537ea627eca87a36d1d019c95b14175c51ea37a50a2e9b38457875de193fad90
- Bridge gate: 9920e6a5559c2e701068945419d0790208c87e32686f9881519272fecbfa590a
- All 23 output-file manifest: d4ca2e2b4e26a83d690fb10ffe0107a5625c2517c36f2259ad3019c80f4a6479

Raw audio, transcripts, model weights, runtime binaries and per-person vectors remain outside the Git checkpoint. The public sample reproducibility manifest provides original corpus attribution, identifiers, crop ranges and hashes.
