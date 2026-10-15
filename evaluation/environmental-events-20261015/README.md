# Environmental-event pilot, 2026-10-15

This fixed development pilot completed, but **broad-door temporal performance was poor**. The results are retained without changing thresholds, taxonomy or model weights. This is an exploratory checkpoint, not a held-out accuracy claim or a release-quality acceptance pass.

## Results

- **DCASE broad door, pooled 1-second bins:** TP 11, FP 38, FN 18, TN 293. Precision **22.45%**, recall **37.93%**, F1 **28.21%**. The 360 bins comprise 29 positive and 331 negative bins, derived from 18 annotated knock/doorslam intervals in three complete 120-second mixtures.
- **ESC-50 positive-only clip hits:** rain 6/6, door knock 3/3, door creak 2/3. These are presence-hit counts, not precision or six-target accuracy. The unchanged detector taxonomy excludes the model class `Creak` even though creaking doors are valid broad-door references.
- **Supplemental unchanged greedy IoU >= 0.3:** all three DCASE mixtures returned door precision, recall and F1 of 0. There were 0 matched reference intervals among 18 references and 11 predicted supports. This is a coarse-support diagnostic, not a physical-event count or onset-accuracy result.
- **Digital-zero controls:** 5 seconds and 120 seconds both passed: only silence covering the complete recording, with zero model calls. This does not establish accuracy on natural quiet recordings.
- **Execution:** 29 fixtures, 605 seconds of input, one Zipformer model load, 165 attempted and completed model calls. Single-run elapsed time was 20.554 seconds, including preprocessing and monitoring. Sampled process-group-plus-monitor RSS peaked at 332,115,968 bytes, with a maximum work-phase sample gap of 0.105331 seconds. These are observations, not continuous peak-memory or model-only speed guarantees.

## What was scored

DCASE knock and doorslam annotations map to broad `door`. Full recordings provide exhaustive door truth: a half-open 1-second bin is positive if a reference interval has any strictly positive overlap. Predictions use the same rule, with duplicate/overlapping supports unioned. Pooled TP/FP/FN/TN determine the reported precision, recall and F1. A door-negative bin can contain other office sounds; it does not imply natural silence or absence of every target.

ESC-50 supplies weak clip-level labels. Rain, knock and creak clips support positive-only hit measurements. Engine, train, pouring water, wind, keyboard typing and clapping are diagnostic confuser clips, not verified negatives. Engine/train labels do not establish driving/subway positives. No all-target false-positive or accuracy score is inferred from these weak tags.

## Reproduction and provenance

`PROTOCOL.json` contains the exact fixture IDs and deterministic selection rules, source URLs, licensing references, checksums, unchanged inference policy, conversion settings, per-mixture counts and aggregate results. `metrics.py` contains only the four frozen pure helpers: `segments`, `rates`, `segment_score`, and `weak_hit`. Run the synthetic, model-free checks from this directory with:

```
python -B -m unittest -v test_metrics
```

The evaluated product is [commit 76175df](https://github.com/Headmaster218/long-audio-backend/commit/76175dfce9475fe1d04799b6cf2d7aeea2045de7); the [event detector](https://github.com/Headmaster218/long-audio-backend/blob/76175dfce9475fe1d04799b6cf2d7aeea2045de7/audio_backend/events/detector.py) and other evaluated source files are also SHA-256-pinned in the protocol. The [Zipformer-small release](https://github.com/k2-fsa/sherpa-onnx/releases/download/audio-tagging-models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15.tar.bz2) and actual ONNX/label-map bytes are identified separately. Hashes establish byte identity, not whether evaluation material appeared in model training.

This package contains aggregate observations, public-source selection metadata and scoring helpers. It is not a standalone inference harness and includes no recordings, transcripts, model binaries or per-window score vectors.

## Limits and licensing

- The three DCASE development mixtures are correlated: 99 source appearances use 90 unique isolated training samples. Density, polyphony and filename EBR change together, so the mixtures are not controlled contrasts or source-disjoint samples. Filename EBR tokens are preserved literally because the upstream README's subsequent dB-range prose is inconsistent.
- ESC-50 selection was fixed before inference: categories in the stated order, lexicographically sorted filenames, globally unique source IDs. It yields 22 fold-1 and two fold-2 clips, not a representative stratified sample.
- Detector support windows are 5 seconds with a 2.5-second hop. Merged/chunked supports do not establish precise event onset/offset times.
- Music, driving, subway and natural quiet/silence remain unvalidated. Model-training overlap is unknown. There was no threshold calibration or model change.
- [ESC-50](https://github.com/karolpiczak/ESC-50/tree/33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6) is **CC BY-NC 3.0**; this pilot's use is noncommercial research-only. Follow the pinned upstream [license and source attributions](https://github.com/karolpiczak/ESC-50/blob/33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6/LICENSE) when obtaining the data. [DCASE 2016 Task 2](https://dcase.community/challenge2016/task-sound-event-detection-in-synthetic-audio) is **CC BY 3.0**. Dataset licenses remain applicable; this report does not relicense them.
