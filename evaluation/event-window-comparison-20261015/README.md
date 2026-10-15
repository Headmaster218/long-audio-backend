# Environmental-event window comparison, 2026-10-15

The fixed 2-second-window experiment improved heldout physical-door segment F1 only modestly, **0.193548 → 0.250000**. Recall remains poor: **3/12** annotated knock/doorslam events had any predicted overlap, versus 2/12 at baseline. False-positive support increased **11.18 → 12.25 seconds**. **No production default, threshold, taxonomy or model change is supported.** Execution completed; independent score-receipt checks, event reconstruction and execution audit passed.

## Fixed comparison

Baseline: 5-second windows / 2.5-second hop. Experimental: actual 2-second windows / 1-second hop. Both use the same Zipformer-small model, all 527 labels, category maxima and postprocessing, threshold 0.25 and release ratio 1.0. Two-second input acceptance does not establish a documented 2-second minimum or quality guarantee.

Four complete 120-second DCASE 2016 Task 2 mixtures were frozen before predictions: two calibration and two official-test files, all filename EBR token 0. The official test source pool is described as independent of training/development. Calibration sources overlap the earlier pilot. Independence between the two heldout mixtures and absence of pretrained-model exposure are not established.

| Split / arm | TP / FP / FN / TN | Precision | Recall | F1 | Covered references | FP support |
| --- | --- | --- | --- | --- | --- | --- |
| Calibration, 5s | 6 / 33 / 12 / 189 | 0.153846 | 0.333333 | 0.210526 | 4/12 | 35.55s |
| Calibration, 2s | 11 / 12 / 7 / 210 | 0.478261 | 0.611111 | 0.536585 | 7/12 | 19.35s |
| Heldout, 5s | 3 / 10 / 15 / 212 | 0.230769 | 0.166667 | 0.193548 | 2/12 | 11.18s |
| Heldout, 2s | 4 / 10 / 14 / 212 | 0.285714 | 0.222222 | 0.250000 | 3/12 | 12.25s |

Each row pools 240 half-open 1-second segments. The frozen calibration gate required higher F1 and no fewer covered references. It passed after 332 score calls, before heldout inference, with no retuning. The heldout gain is much smaller: 2-second segment recall is **22.22%**, and **9/12** references remain uncovered.

Heldout knock coverage rose **2/6 → 3/6**; doorslam coverage stayed **0/6** in both arms. Drawer-associated false-positive support rose 2.24 → 3.57 seconds on calibration and 0 → 0.82 seconds on heldout, excluding true-door overlap. The heldout drawer-only supports came from raw **Door/Knock**, not Cupboard scores. This is descriptive score/annotation evidence, not acoustic causal proof.

## Metric scope

A 1-second bin is positive for any strictly positive overlap with annotated or predicted support. Overlapping predictions are unioned. Confusion counts are pooled before calculating rates. A reference is covered once for any positive predicted overlap; this lenient coverage diagnostic is not one-to-one event accuracy.

Reference truth is **physical door = knock + doorslam**. The product category also includes Door, Sliding door, Slam, Knock and Cupboard open or close. False-positive time is relative to the narrower reference target. This mismatch cannot explain away zero doorslam coverage or the observed Door/Knock drawer confusions. Door-negative time may contain other sounds; it is not natural silence or all-target negative truth.

**Precise onset/offset accuracy was not measured.** Classifier supports are not physical event counts. Four synthetic mixtures at one EBR token do not establish representative six-target accuracy, device generalization, or music/driving/subway/natural-quiet performance.

## Execution history

The original execution failed a prediction-row serialization bound after both arms of the first calibration file completed. Its historical INCOMPLETE status is preserved. All **166 valid score calls** were retained; a separate continuation reconstructed them without decoding audio or replaying the model, then completed **498 new calls**. Total: **664 calls, two model loads**. The reconstructed baseline matched its saved events and metrics exactly; unavailable RMS/peak fields were omitted.

The continuation exited successfully, was positively waited, and left no known owned processes. Elapsed time: **34.003752 seconds**; sampled execution-group-plus-monitor RSS: **350,281,728 bytes**; maximum work-phase sampling gap: **0.126565 seconds**. The original's **12.158347 seconds** is separate, not a combined wall-time claim. Neither is model-only latency; sampled RSS is not a continuous peak guarantee. Both 120-second digital-zero controls returned only silence with zero model calls, without testing natural quiet.

## Separate actual API persistence check

Two previously exposed 5-second positive clips ran in separate sessions, one classifier inference window each:

- Rain: **Rain on surface**
- Door: **Knock**

HTTP event/candidate fields matched post-cleanup read-only SQLite rows, including scores, source labels, category scores, support timing and model provenance. Change-feed scores matched the SQLite JSON projection; the observed numeric representation difference was approximately 4.44e-16. Elapsed time: **6.665 seconds**. This verifies persistence on two examples only, with uncalibrated scores and 0–5-second classifier support. It adds no accuracy, generalization or precise-onset evidence.

## Files and licensing

[PROTOCOL.json](PROTOCOL.json) contains fixture/model hashes, source links, preprocessing, frozen policy, metric definitions and limitations. [RESULTS.json](RESULTS.json) contains per-file and pooled observations. Check aggregate arithmetic, pooling and call accounting without inference:

```
python -I -B test_aggregates.py
```

The checks reuse the preceding pilot's unchanged rate formulas; they do not independently verify underlying predictions. No recordings, transcripts, model binaries or per-call score matrices are included.

The [DCASE official task](https://dcase.community/challenge2016/task-sound-event-detection-in-synthetic-audio) links the [evaluation provider](https://archive.org/details/dcase2016_task2_test_public), declaring **CC BY 4.0**; the [DCASE datalist](https://dcase-repo.github.io/dcase_datalist/datasets/sounds/dcase2016_task2_eval.html) says **CC BY 3.0**. The packaged README credits Grégoire Lafay but does not resolve the discrepancy. The API inputs are [ESC-50](https://github.com/karolpiczak/ESC-50/tree/33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6), under [CC BY-NC 3.0 with source attribution](https://github.com/karolpiczak/ESC-50/blob/33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6/LICENSE). Use here is research-only and noncommercial; no audio is redistributed or relicensed.
