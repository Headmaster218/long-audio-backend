# Fixed-panel automatic selector geometry

This is a posthoc check on the same previously exposed recordings, using saved source-bound native turns and no new model calls. It is source-only read-only validation, not installed HTTP/API qualification or recognition evidence. The earlier quality results remain unchanged.

The fixed selector uses positive intersections of distinct native labels within an accepted core, adds one second on each side clipped to that core, unions touching time supports, splits at 20 seconds, and takes the first eight chronological ranges. No parameter or range was changed using the reference results.

## Detection versus selected context

- **English overlap 1:** Native detection intersects 0.539 of 1.120 seconds of annotated overlap, 48.125% recall. It misses 0.581 seconds and detects 0.199 seconds outside the annotated overlap. The selected 2.738-second support covers all annotated overlap and includes 1.618 seconds of annotated non-overlap/context.
- **English overlap 2:** Native detection intersects 2.029 of 2.090 seconds, 97.081% recall. It misses 0.061 seconds and detects 0.209 seconds outside the annotated overlap. The selected 4.238-second support covers all annotated overlap and includes 2.148 seconds of non-overlap/context.
- **Mandarin overlap:** Native detection intersects 0.989 of 1.700 seconds, 58.176% recall. It misses 0.711 seconds and detects 0.785 seconds outside the annotated overlap. Three selected supports total 6.925 seconds, cover all annotated overlap, and include 5.225 seconds of non-overlap/context.

Context coverage is therefore better than raw detection recall on these examples. It does not establish detection accuracy or recognition quality. English references use forced-aligned lexical-word support; Mandarin references use human utterance support. Their rates should not be pooled into a single headline accuracy.

Both solo controls and the exact digital-zero control have no detections and no selected ranges. Their overlap recall and precision denominators are undefined, not 100%. A skipped candidate is not a model silence or solo-recognition pass. The unavailable second Mandarin overlap slot remains unavailable.

## Selection cost and boundary limits

Five ranges select 13.901 seconds of source audio in total. There are no duplicated dispatch frames, omitted budget ranges, or selected ranges longer than 20 seconds. This measures selected audio and call count, not measured speed: the Mandarin excerpt changes from one full-clip candidate call to three shorter support calls.

All five selected supports cross a reference boundary. The two English supports cut word intervals. Each of the three Mandarin supports cuts utterance intervals; fully contained Mandarin utterance counts are zero, one and zero. No selected support has a complete reliable whole-window reference. Each must retain an explicit `unscorable_boundary` status for whole-window WER/CER. Do not use the earlier full-clip error rates, apportion Mandarin characters by time, discard boundary output using gold, or expand these ranges using annotations.

The next separately authorized installed-path run can verify GET/export/source binding, deterministic selection, bounded candidate execution and raw-output preservation. It cannot establish a new complete-window recognition error rate on these supports.

## Evidence

`PUBLIC_AUTOMATIC_SELECTOR_GEOMETRY01.json` contains per-case interval sets, exact frame counts, misses, selected non-overlap, context costs, event coverage and boundary diagnostics. `score_saved_selector_geometry.py` independently reconstructs the fixed native intersections and supports without importing model or product code. All 118 saved-file hashes and 187 source-module hashes were rechecked. Actual source hashes and PCM coordinates match the frozen crop bindings.

The report contains no transcripts, original speaker/session/chunk labels, audio or private paths. No candidate calls were made and the original quality qualification was not revised.
