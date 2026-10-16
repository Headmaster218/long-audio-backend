# FrameMN10 event comparison, 2026-10-16

**Recall remains poor.** On the historical heldout portion, the fixed FrameMN10 primary configuration covered only **1/12** physical-door references, with one-second segment **F1 0.200000** and recall **11.11%**. Knock coverage was **1/6**, and Slam-versus-doorslam coverage was **0/6**. The raw diagnostic view gave the same heldout result. Zero observed false positives does not offset the missed references or establish generalizable precision. **No quality, production-default, API, Windows or Vulkan acceptance is supported.**

## Data and frozen comparison

This reuses exactly the same four complete 120-second DCASE 2016 Task 2 mixtures already exposed in the prior Zipformer comparison: dev/test sample 1, EBR token 0, NEC2/POLY0 and NEC4/POLY1. The names “calibration” and “heldout” are historical partition names, **not a new blind test**. Independence between the selected mixtures and absence of pretrained-model exposure are not established. All files ran under the fixed policy, without a quality gate, substitution, threshold search or retuning.

| Historical portion / FrameMN view | TP / FP / FN / TN | Precision | Recall | F1 | Covered references | FP support |
| --- | --- | --- | --- | --- | --- | --- |
| Calibration, primary median9 | 3 / 0 / 15 / 222 | 1.000000 | 0.166667 | 0.285714 | 2/12 | 0s |
| Calibration, raw diagnostic | 4 / 0 / 14 / 222 | 1.000000 | 0.222222 | 0.363636 | 3/12 | 0s |
| Heldout, primary median9 | 2 / 0 / 16 / 222 | 1.000000 | 0.111111 | 0.200000 | 1/12 | 0s |
| Heldout, raw diagnostic | 2 / 0 / 16 / 222 | 1.000000 | 0.111111 | 0.200000 | 1/12 | 0s |
| All four, primary median9 | 5 / 0 / 31 / 444 | 1.000000 | 0.138889 | 0.243902 | 3/24 | 0s |
| All four, raw diagnostic | 6 / 0 / 30 / 444 | 1.000000 | 0.166667 | 0.285714 | 4/24 | 0s |

Precision here is descriptive arithmetic on this tiny exposed sample. The median9 primary view was fixed in advance; raw is diagnostic, not a selected replacement.

## Model and temporal meaning

[PretrainedSED FrameMN10](https://github.com/fschmid56/PretrainedSED/tree/1aa47e482f7e89904cba2338999345025d8b4e36) ran in float32 CPU eval/inference mode, batch one, with its unchanged official 16 kHz frontend. **One model load, 48 calls**: twelve nonoverlapping 10-second chunks per recording. Each call returns 447 classes by 250 frames; sigmoid is applied once. The 40 ms grid is output spacing, **not 40 ms latency or demonstrated physical onset/offset accuracy**. Inputs have 10-second context and the pipeline is noncausal.

The global, uncalibrated threshold is strict **score > 0.2**, predeclared from the middle official demo value. Primary postprocessing is a 9-frame/360 ms median, independently per class with reflect boundaries over the full concatenated 3000-frame file. It crosses 10-second chunk joins and is applied before thresholding and before unioning classes. Raw uses the same scores and threshold without median. There is no padding in these full-length inputs.

PCM resampling deliberately reuses the prior fixed FFmpeg conversion. The upstream demo uses librosa; this does not claim equivalence between those resamplers. Frontend details, fixture hashes and version pins are in [PROTOCOL.json](PROTOCOL.json).

## Taxonomy and metrics

Predicted broad-door support is the union of **Door, Knock, Slam, Sliding door, and Cupboard open or close**. Physical-door reference truth is only annotated **knock + doorslam**. These are different targets: false-positive support is relative to the narrower physical reference. Knock is also scored against knock, Slam against doorslam, and Door alone against physical-door truth. Slam-to-doorslam is a cross-taxonomy mapping; Slam is not a dedicated “Door slam” class.

One-second bins and event supports are half-open. Any strictly positive overlap counts; endpoint contact alone does not. Confusion counts are pooled before computing rates. Each annotated reference can be covered once by any positive overlap with the prediction union. This lenient coverage diagnostic is not one-to-one event accuracy, and predicted supports are not physical event counts. Door-negative time can contain other sounds or valid broader-category sounds. No precise onset/offset, natural-quiet or representative six-target accuracy was measured.

## Prior Zipformer context, without reexecution

The [previously published comparison](../event-window-comparison-20261015/README.md) reported historical heldout F1 **0.193548**, coverage **2/12**, FP support **11.18s** for 5-second windows / 2.5-second hop, and F1 **0.250000**, coverage **3/12**, FP support **12.25s** for experimental 2-second windows / 1-second hop. Both used threshold 0.25. Knock coverage was 2/6 and 3/6 respectively; doorslam coverage was 0/6 in both.

FrameMN’s 0.200000 F1 falls between those values but covers fewer references than either. Models, label spaces, thresholds, postprocessing and timing semantics differ. This is descriptive pilot context, not a controlled model-only, threshold-only or window-only ablation. Zero FrameMN false positives does not establish a quality advantage when 11/12 references remain uncovered. No production defaults changed.

## Timing and bounded execution

Owner-observed elapsed time, including startup and cleanup: **11.237747s**. Model load: **5.624450s**. Full-file resampling: **0.525862s**. Inference/compute-only time: **3.140059s** across **480s** of audio, giving compute-only RTF **0.006541789**. That timed compute region includes the official mel frontend, model forward, finite-logit check, sigmoid and output conversion. It excludes loading, resampling, serialization, event decoding, startup and cleanup; it is not end-to-end latency. Timing components are not a disjoint decomposition of the total.

Peak sampled execution-plus-monitor RSS: **481,603,584 bytes**; maximum work-phase sample gap: **0.466973s**. The run exited successfully, was positively waited, and completed owned-process cleanup with no known owned processes remaining. Sampling covers the work phase and cannot guarantee the continuous memory peak. [RESULTS.json](RESULTS.json) records exact timing/resource aggregates and per-file, partition and all-four metrics.

## Source-only checks and licensing

Run the standard-library-only checks with:

```sh
python -I -B test_aggregates.py
```

These verify published arithmetic, pooling, call accounting, frozen settings and reported resource/package bounds. They do not run inference, reconstruct predictions, validate raw score tensors, prove frontend parity or independently reproduce the empirical evaluation. This package contains no audio, transcripts, model binaries, full class traces or score matrices.

The checkpoint is [frame_mn10_strong_1.pt](https://github.com/fschmid56/PretrainedSED/releases/download/v0.0.1/frame_mn10_strong_1.pt), **15,537,114 bytes**, SHA-256 **4a0fe320d5369987b772394c51881fb20a602967e6842f72f3e5c8181065ece7**. That digest is local first-seen byte identity, not an upstream-signed checksum. Upstream code is [MIT](https://github.com/fschmid56/PretrainedSED/blob/1aa47e482f7e89904cba2338999345025d8b4e36/LICENSE). No separate explicit checkpoint-weight license statement was verified; evaluation was internal research only, and weights are not bundled or relicensed.

The [official DCASE task](https://dcase.community/challenge2016/task-sound-event-detection-in-synthetic-audio) links an [evaluation archive listing CC BY 4.0](https://archive.org/details/dcase2016_task2_test_public), while the [DCASE datalist says CC BY 3.0](https://dcase-repo.github.io/dcase_datalist/datasets/sounds/dcase2016_task2_eval.html). The packaged attribution credits Grégoire Lafay, IRCCYN, Ecole Centrale de Nantes. This licensing ambiguity remains unresolved; no audio is redistributed or relicensed.
