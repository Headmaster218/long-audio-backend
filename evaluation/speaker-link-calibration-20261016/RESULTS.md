# Anonymous speaker linking: whole utterances versus central 2-second crops

16 October 2026

## Main finding

On retrospective, identity-disjoint evaluation, the selected policy linked 8/10 same-speaker pairs using whole utterances and 5/10 using central 2-second crops. Both had 0/180 observed different-speaker threshold passes and 0/180 final false merges. Short crops linked fewer same-speaker pairs in this sample. Zero observed errors does not establish a deployment error rate.

## Data and separation

- A fixed CN-Celeb speech subset supplied 40 test utterances from 20 people, with exactly two utterances per person. All 14 retained enrollment utterances were excluded.
- Calibration and evaluation each contained 20 utterances from 10 people. Their identity sets were disjoint; no cross-split scores were computed. The same split and utterances were preserved across duration conditions.
- Each split supplied 190 unordered pairs: 10 same-speaker positives and 180 different-speaker negatives. The negatives represent only 45 identity dyads and share utterances and people; they are not 180 independent trials.
- Seven identities per split were formerly “known” and three “unknown” in a gallery experiment. These are now reporting strata only: all identities are anonymous, and formerly-unknown same-speaker pairs remain valid positives. No gallery assignment is performed.
- Whole-utterance durations were 2.5078125–36.91975 seconds. Each short crop contained exactly 32,000 frames at 16 kHz. For an utterance with N frames, start=floor((N−32000)/2), end=start+32000, using half-open original-frame coordinates. No quality-based shifting, padding, replacement, denoising or concatenation was permitted.
- Earlier whole-utterance and gallery outcomes were already exposed. These are retrospective studies, not pristine untouched holdouts. Reusing the same people and recordings also makes the duration conditions dependent.

## Reproducible scoring and linking policy

The fixed CPU extractor was ERes2Net base, Chinese, 3D-Speaker, 16 kHz, with 512-dimensional embeddings and verified runtime/frontend configuration. PCM16 samples were converted to float32 by division by 32768; outputs received float32 L2 normalization.

Both pairwise studies preserved normalized float32 components without renormalization and scored with deterministic float64 math.fsum of their products. This is the actual computation, rather than the older gallery experiment’s NumPy float32-dot convention.

Nodes began as singletons. Merge scores were the minimum over every original-vector cross-pair, with no centroids or template learning. A hard cannot-link would prohibit the merge; this corpus lacks native simultaneous-speaker evidence, so its constraint set was empty. Ground-truth identities were used for calibration selection and reporting, never as clustering inputs.

Each round ranked all constraint-compatible clusters, including below-threshold candidates. Merges required reciprocal unique-best choices, score >= tau, and best-minus-runner-up margin >= m on both sides. Exact ties or missing runner-ups caused abstention. Disjoint reciprocal merges were synchronous, canonically ordered, with cross-pair constraints rechecked. Processing stopped at no merges, bounded by 20 rounds. Four initial orders gave identical partitions, an implementation check rather than evidence of candidate-population robustness.

Calibration searched tau={0.55, 0.65, 0.75, 0.85, 0.95}, m={0.05, 0.10, 0.15}, plus always-abstain. Eligible candidates required zero raw different-speaker threshold passes. Selection maximized true final links, then larger tau, then larger m. No useful eligible candidate would mean always-abstain and zero yield. Each choice was frozen before evaluation without test-based retuning. The whole-utterance policy was separately applied to short crops as a prespecified diagnostic.

## Results

“True links” counts same-speaker pairs in a final shared cluster. “Raw false passes” counts different-speaker pairs passing tau before ambiguity checks. “False merges” counts different-speaker pairs in a final shared cluster. Linked nodes exclude singletons.

| Input / policy | Split | True links | Raw false passes | False merges | Linked nodes | Singletons |
|---|---|---:|---:|---:|---:|---:|
| Whole, selected 0.55 / 0.15 | Calibration | 7/10 | 0/180 | 0/180 | 14/20 | 6/20 |
| Whole, selected 0.55 / 0.15 | Evaluation | 8/10 | 0/180 | 0/180 | 16/20 | 4/20 |
| 2 s, selected 0.55 / 0.10 | Calibration | 4/10 | 0/180 | 0/180 | 8/20 | 12/20 |
| 2 s, selected 0.55 / 0.10 | Evaluation | 5/10 | 0/180 | 0/180 | 10/20 | 10/20 |
| 2 s, fixed whole-policy diagnostic 0.55 / 0.15 | Calibration | 3/10 | 0/180 | 0/180 | 6/20 | 14/20 |
| 2 s, fixed whole-policy diagnostic 0.55 / 0.15 | Evaluation | 5/10 | 0/180 | 0/180 | 10/20 | 10/20 |

Raw same-speaker passes equaled final links for selected policies. The whole-policy diagnostic passed four short-crop calibration positives but linked three. Both margins recovered 5/10 evaluation positives; the smaller margin did not improve evaluation yield.

Formerly-known recovery was 5/7 then 6/7 for whole utterances, and 3/7 then 4/7 for short crops, in calibration/evaluation order. Formerly-unknown recovery was 2/3 in both whole-utterance splits and 1/3 in both short-crop splits. Different-speaker strata contained 84 known/known, 84 known/unknown and 12 unknown/unknown pairs per split; all had zero raw false passes and final false merges for the reported policies.

Under the selected policies, 3/10 and 2/10 identities remained fragmented with whole utterances; 6/10 and 5/10 remained fragmented with short crops. These produced 13, 12, 16 and 15 final clusters respectively. All observed clusters were pure and the largest had two nodes. Pair precision among actual links was 7/7, 8/8, 4/4 and 5/5 respectively; singleton-heavy partitions can have perfect purity despite poor recovery. No complete-link invariant or tested order-stability violation was observed.

## Execution and verification

Whole-utterance scoring reused saved embeddings with no new model calls. Short-duration extraction used one serial extractor for 40 successful calls and 80 seconds of crop input. Worker time was 4.135738 seconds; peak RSS was 170,061,824 bytes (162.18 MiB). Extractor release and process completion were confirmed.

Bounds were a 120-second worker alarm, 180-second outer envelope, 2 GiB hard address space, 1 GiB observed RSS guard and 4 MiB output. RSS checks used stage-boundary high-water readings, not a kernel-enforced ceiling. Sources were processed one at a time. Independent verification covered source/PCM/crop hashes, 380 short-duration scores, 16 candidates, freeze ordering and model-space consistency.

## Interpretation and limits

These descriptive results support further research, with explicit abstention and substantial short-duration fragmentation. They establish neither a production threshold nor a useful statistical false-merge bound. Calibration’s zero-error condition is a selection criterion.

Two samples per person cannot validate genuine clusters of three or more spans. The corpus does not test simultaneous-speaker constraints, overlap or actual same-recording linking. Central crops differ from native-exclusive, phrase-selected, guarded meeting crops in phonetics, noise, context and selection. Tie and missing-runner-up behavior was tested mechanically but not meaningfully exercised by selected-policy corpus results.

Dataset session groups do not establish independence of recording dates or devices; checkpoint pretraining overlap remains unverified. CN-Celeb research/noncommercial restrictions remain applicable. Separate positive-only feasibility probes were excluded from calibration and evaluation; a low positive-pair score alone does not establish different people.

No production default, persistent or global identity, verified-person assignment, automatic enrollment, or cross-recording link was created or authorized by these studies. Unresolved speech remains unlinked; any later practical links must remain provisional and recording-scoped.
