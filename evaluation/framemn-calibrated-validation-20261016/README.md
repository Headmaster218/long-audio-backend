# FrameMN10: calibration and fixed new-mixture validation

## Main result

The calibration-selected threshold **0.01** with unchanged median-nine smoothing covers **11/24 physical door references** on four new DCASE mixtures: **11/12 knocks and 0/12 door slams**. Pooled one-second TP/FP/FN/TN are **16/6/18/440**; precision/recall/F1 are **0.7273 / 0.4706 / 0.5714**. Every file meets the prespecified false-positive-support budget of at most 2 seconds per 120-second recording.

That budget success does not establish general door readiness. All twelve slam references are missed. No production threshold/default, API integration, or deployment acceptance follows from this result.

The four files, in fixed reporting order:

| New mixture | Broad reference coverage | One-second F1 | FP support |
|---|---:|---:|---:|
| test_2_ebr_0_nec_2_poly_0 | 2/4 | 0.6000 | 0.39 s |
| test_2_ebr_0_nec_4_poly_1 | 3/8 | 0.6250 | 0.20 s |
| test_3_ebr_0_nec_2_poly_0 | 2/4 | 0.5455 | 1.58 s |
| test_3_ebr_0_nec_4_poly_1 | 4/8 | 0.5263 | 0.85 s |

The unchanged **0.20 diagnostic**, computed from the same 48 saved outputs, covers **1/24**, with TP/FP/FN/TN **2/0/32/446**, F1 **0.1111**, and no observed false-positive support. It is a prespecified descriptive comparator; there was no test-set threshold search or extra inference.

## Calibration was a separate stage

Only the two declared development-calibration files were used to select the threshold. The predeclared grid was 0.01–0.50 in 0.01 increments, with median-nine smoothing fixed. Eligibility required each file to have at most 2 seconds of false-positive support. Selection maximized covered physical references, then pooled one-second F1, then the higher threshold.

The selected 0.01 covers **10/12** calibration references (6/6 knocks and 4/6 slams), with TP/FP/FN/TN **13/7/5/215**, F1 **0.6842**, and file-level FP support **0.57/1.61 seconds**. At 0.20, calibration coverage is 2/12 and F1 0.2857. All 50 grid points were eligible. The selected point is the lower grid boundary, so this is a within-grid selection rather than a global optimum or probability calibration. The grid was not extended.

Calibration read saved outputs only. It did not read prior held-out outputs or run a model. The selection and new-mixture protocol were frozen before new-mixture outputs were examined. The earlier fixed-threshold result remains separate and unchanged at commit `cd81ec9e65c83fac9ec29c557f2e9de0f9a50a24`: its prior test_1 partition covered only 1/12 references, with F1 0.20. See the [original fixed report](../framemn-comparison-20261016/README.md); these later results do not retrospectively replace it.

## What the metrics mean

The fixed broad-door prediction is the union of **Door, Knock, Slam, Sliding door, and Cupboard open or close**. Physical references contain **knock and doorslam only**. Drawer remains a separate negative/confuser. A broad-union hit does not prove correct fine-class recognition.

- One-second confusion counts use half-open bins and any positive overlap. Counts are pooled before computing precision, recall and F1; per-file rates are not averaged.
- Reference coverage counts each physical reference once if it has any positive-duration overlap with predicted union support. This is lenient coverage, not one-to-one event matching, event-count accuracy, an IoU criterion, or precise onset/offset accuracy.
- False-positive support is the duration of predicted union outside reference union. It is not a false-alarm-count budget. Partial extensions can consume FP time even when they do not create a false-positive one-second bin.
- Separate fine-label metrics in RESULTS.json compare Knock to knock, Slam to doorslam, and Door alone to the combined physical references. On new mixtures, Knock has pooled one-second F1 0.80 and 11/12 reference coverage; Slam has F1 0 and 0/12 coverage. Door alone covers one knock reference and no slam. These descriptive views do not change the frozen union.

The 40 ms output spacing is not 40 ms latency. Ten-second model context and smoothing over the complete recording are noncausal. Scores use the strong 447-label ordering, a single sigmoid, strict `>` thresholding, and nine-frame half-sample-reflect smoothing per class after full-file concatenation. Input is mono 16 kHz float32 via a fixed FFmpeg/SWR conversion. The official model frontend is retained, but bit-exact parity with the upstream librosa resampler was not established.

## Saved-score investigation of missed slams

Source and saved-output consistency checks found no demonstrated label-index, class/time-axis, chunk-order or timing-convention bug. Across all 12 annotated slam intervals, Mechanisms ranks first by per-class maximum, and median-smoothed Generic impact sounds ranks 2–5. Every fixed door-class median maximum remains below 0.01; raw outputs show only one small above-threshold Knock excursion. Smoothing alone therefore cannot explain the other 11 misses. Generic-impact peaks near annotated onset argue against a gross offset but do not prove acoustic alignment or that the response is caused by the slam.

The [AudioSet Slam definition](https://research.google.com/audioset/ontology/slam_1.html) includes violent door or hinged-cover closing. Generic Mechanisms/impact labels do not identify a door and must not be substituted into the fixed union. Manual acoustic QC and numerical resampler parity remain unresolved. This was a descriptive saved-score investigation, with no model rerun, test-threshold change or revision of the validation results.

## Zipformer: matched-budget calibration, no eligible selection

Saved Zipformer calibration outputs were evaluated at 0.01–0.99 in 0.01 increments under the same physical references, per-file FP budget and coverage/F1/higher-threshold rule. **Neither arm has an eligible grid point**:

- 5-second windows / 2.5-second hop: minimum observed maximum-per-file FP support **4.37 seconds**, attained at 0.97–0.99. At 0.99 it covers 1/12, F1 0.1667.
- 2-second windows / 1-second hop: corresponding minimum **2.78 seconds**, attained at 0.91–0.99. At 0.99 it covers 2/12, F1 0.3636.

These floors are diagnostics, not selected operating points. No outside-grid threshold or all-negative fallback was substituted. Zipformer retains `>=`, release ratio 1, full accepted-window support, original merging, and maximum-five-second splitting. FrameMN's frame support and smoothing differ. The budget alignment does not remove these confounds, and there is no fair selected-Zipformer new-mixture comparison. **No new Zipformer calls were made.**

## Data scope and rights

This uses four previously unprocessed 120-second synthetic office mixtures from [DCASE 2016 Task 2](https://dcase.community/challenge2016/task-sound-event-detection-in-synthetic-audio): test_2 and test_3 at literal EBR 0, NEC 2/poly 0 and NEC 4/poly 1. DCASE documents an evaluation source pool independent of train/development. Per-mixture source IDs are unavailable, so independence from previously exposed test_1, between these new mixtures, and from model pretraining is unproven. They are not four established independent replications. No manual listening/acoustic QC was performed; annotations alone cannot establish the cause of missed slams.

Model source is [PretrainedSED at 1aa47e482f7e89904cba2338999345025d8b4e36](https://github.com/fschmid56/PretrainedSED/tree/1aa47e482f7e89904cba2338999345025d8b4e36), using `frame_mn10_strong_1.pt`. The [source code license is MIT](https://github.com/fschmid56/PretrainedSED/blob/1aa47e482f7e89904cba2338999345025d8b4e36/LICENSE). A separate checkpoint-weight license was not verified. Its SHA-256 identifies the local first-seen bytes; it is not a publisher-authenticated digest.

The [official-linked evaluation provider](https://archive.org/details/dcase2016_task2_test_public) declares [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), while the [DCASE datalist](https://dcase-repo.github.io/dcase_datalist/datasets/sounds/dcase2016_task2_eval.html) says CC BY 3.0. The package README does not resolve this discrepancy. Attribution: DCASE 2016 Task 2; test-package credits Grégoire Lafay, IRCCYN, Ecole Centrale de Nantes, France. Evaluation remains internal research use; this checkpoint redistributes no audio or weights and grants no rights to them. PROTOCOL.json records public model/data hashes and versions.

## Execution and verification scope

One original CPU run completed **48/48 calls and one model load**, exiting successfully; known owned execution cleanup completed. Elapsed time including startup/cleanup was **11.5093 s**; work-phase sampled execution-plus-monitor RSS peaked at **480,124,928 bytes**, with maximum sample gap **0.412860 s**. Sampling does not establish a continuous peak.

The measured compute region was **2.749356 s / 480 s = 0.00572783 RTF**. It includes the mel frontend, model forward, finite-logit check, sigmoid and output conversion. It excludes loading (**6.200914 s**), full-file resampling (**0.552282 s**), score serialization, event decoding, startup and cleanup. These timings are environment-specific and are not end-to-end latency guarantees or disjoint components to sum mechanically.

This is a sanitized report with model-free aggregate checks, not a standalone inference reproduction package. From this directory:

```sh
timeout 10s python -I -B test_aggregates.py
```

The standard-library tests verify arithmetic, pooling, fixed protocol, package bounds and reported resource limits only. They do not rerun inference, read audio/weights/scores, certify frontend parity or establish empirical accuracy. No audio, transcripts, biometric vectors, full score matrices, private execution receipts or machine-specific paths are included. There is no API persistence, Windows, Vulkan, natural-quiet, music, driving, subway or device-generalization claim.
