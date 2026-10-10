# Native overlap discovery: eight-input evidence

## Measured discovery result

The unchanged native diarization path processed all eight independently finalized, annotation-selected overlap-positive inputs to exact EOF: **120.515 seconds**, including **33.19 seconds of annotated overlap** and **87.325 seconds of nonoverlap**. Seven inputs triggered the production overlap candidate planner; one did not. There were zero failed inputs.

Binary overlap was scored on exact 16 kHz integer-sample unions, with zero collar and at least two distinct native labels active:

| Quantity | Samples | Seconds |
|---|---:|---:|
| True-positive overlap | 307,248 | 19.203 |
| False-positive overlap | 63,696 | 3.981 |
| False-negative overlap | 223,792 | 13.987 |
| Reference overlap | 531,040 | 33.190 |
| Predicted overlap | 370,944 | 23.184 |

Time precision is **82.8287%**; time recall is **57.8578%**. These are sample-level overlap-time metrics, not word or speaker-identity accuracy. All eight input clips are positives, so **input-level false-positive rate/specificity is unmeasured**. Their nonoverlap time still supports the false-positive-time and precision calculations above. Predicted boundaries include the frozen postprocessing onset/offset padding; no collar, threshold or timing adjustment was tuned after results.

Of the 13.987 seconds of false-negative overlap, 13.429 seconds have one active native label and 0.558 seconds have none. This does not isolate a cause such as cold state, thresholds, postprocessing, or representation/model error; it does not justify tuning on these eight outputs.

## Cached composition is a separate layer

**Zero new MOSS generations were made.** The native planner selected a whole-input support for seven inputs, totaling 109.465 seconds. Each passed the exact decoded-sample/support/model/runtime/prompt/budget equivalence gate, permitting reuse of its unchanged historical MOSS generation. The one untriggered input was unavailable to this overlap candidate path.

All three paired cached views (raw canonical diagnostic, strict projection, strict-plus-existing-partial recovery) score **188/686 = 27.4052%** under full-denominator availability accounting:

- Seven available cached inputs: 117 errors / 615 reference characters
- One unavailable input: its complete 71-character reference is retained as an empty-candidate penalty
- Historical oracle-input baseline: 127/686; that unavailable input originally contributed 10 errors
- Exact difference: **127 − 10 + 71 = 188**, an increase of 61 from candidate availability

This is not a new MOSS transcription-quality decline. On the available subset the old outputs remain unchanged, with conditional error rate 117/615 = 19.0244%; this conditional figure must not replace the full 686-character result. Forty-five cached phrases remain strictly admitted; no new partial-recovery benefit is demonstrated. Ordinary ASR was not run, so candidate-path unavailability does not mean an ordinary ASR path would omit these words.

Per-input sample counts, availability states, frame coverage, cached errors and sample-identity receipts are in `results.json`. Per-input oracle permutation removes label naming differences only. It does not establish persistent identity or resolve within-input speaker merging/switching. An unmatched reference track is not proof its words are absent from all emitted tracks.

## What actually ran

The native model/runtime path used unchanged production NativeCoordinator and NativeProcess with reset → push → finish → turns → close, followed by pinned production overlap detection, trigger and support planning. Each input had independent origin/offset zero, exact sample-count duration, and finalization. No reference turns, reference text, desired speaker counts or reference identities were sent to the native worker. Every observed core and extraction endpoint matched the complete input, including fractional-second EOF; no tail was silently dropped or extended.

The native model was Nemotron-3-Diarization Q8_0; runtime was NeMo-Speech.cpp 0.2.0 on CPU. Profile, postprocessing, capacity, planner defaults, provenance and asset digests are in `policy.json` and `provenance.json`. The geometry record's 80 ms value is **not** the scoring grid or asserted live output cadence. The live runtime cadence was represented as 160 samples/10 ms, while postprocessed boundaries and scoring use exact 16 kHz samples. Native provenance's requested four threads and the wrapper's requested two threads/CPU affinity are separate layers, preserved as observed.

Cached candidates were projected through the actual production parser/recovery/partial-track validation/public projection using actual native regions and plans. Full database/API execution, ordinary ASR, event models, embeddings and new MOSS inference were not run. This is not an end-to-end full-meeting or live-stream evaluation, and no production default was changed.

## Cache provenance and container distinction

Reuse required exactly one complete-input plan, complete native core/extraction, exact integer sample bounds, exact decoded float32 hash, and unchanged MOSS model/runtime/prompt/precision/backend/adaptive generation budget. Changed support, multiple scopes, missing trigger, failure or mismatched samples/pins would refuse reuse; no transcript clipping, label joining or replacement decode was allowed.

The cached generation actually consumed the original PCM16 WAV and retains that encoded-file hash. A normal adapter could write a FLOAT WAV, but this phase **did not materialize or consume that container**. Its encoded hash remains null/unobserved. Exact decoded float equivalence permits only the stated cached composition; it is not evidence that two different encoded containers have identical bytes.

## Portable checks and reproducibility limits

Run the model-free tests:

    python -B -m unittest -v test_evidence.py

`portable_metrics.py` provides validated integer-sample unions, distinct-label overlap, recording-to-input coordinate shifting, zero-collar TP/FP/FN, and full-denominator cached-availability accounting. Tests include all 65,536 pairs of eight-sample binary masks, duplicate-label edge cases, half-open boundaries, missing/failure accounting, all-input denominator retention, exact observed sample-count arithmetic, all-three-view 188/686 parity, and the original-PCM/unobserved-FLOAT distinction.

These helpers reproduce arithmetic, **not the full live harness** or native model. They do not independently enforce the complete production cache gate; that gate was separately audited. Raw reference/native timelines, audio, corpus text, generated text, machine paths, process IDs and binaries are omitted. Therefore published summaries alone cannot independently regenerate the observed detection intersections or transcript-level CER. The independent postrun audit replayed actual plans and recomputed the underlying metrics before sanitization; its digest is recorded. Prior data selection and 33-candidate/72-exclusion evidence remain in `../overlap/` at the historical parent snapshot.

AliMeeting-derived metadata retain CC BY-SA 4.0 attribution and licensing; see `ATTRIBUTION.md`. Model/runtime notices remain separately scoped under `LICENSES/`. Dataset licensing does not relicense project code or models. No new project-code license is granted.

## Execution verification and boundaries

The phase completed in 17.802 seconds. All eight worker groups and the separate scorer have cleanup receipts, and the original execution owner recorded terminal exit zero. Peak sampled owned-plus-supervisor RSS was 199,036,928 bytes; peak visible-namespace RSS was 213,405,696 bytes. These are namespace-limited sampled observations, not whole-host physical memory or instantaneous hard-cap proof. Limits, sampled memory/disk minima and source/runtime/model verification are recorded in the metadata.

All scopes used the same previously frozen public-corpus inputs. No training-unseen or all-historical-benchmark disjointness claim is made. The result identifies discovery/availability loss on these isolated positives; broader continuous-stream coverage, negative-input behavior, full API integration, and global identity remain untested. This package does not authorize further model runs.
