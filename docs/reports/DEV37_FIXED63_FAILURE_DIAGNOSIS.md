# dev37 fixed 63 second trial failure and diagnosis

Date: 2026-10-17

## Outcome and source

The bounded multicore functional objective was not demonstrated. The latest published source for this trial is dev37, commit b93a328b2406757b7f776b61d3d8a7f8d4c0cba3. The single trial completed primary processing for all 63 seconds but produced no qualifying automatic replacement. The functional gate returned exit 1, which remains a failure result. There was no model rerun.

The fixed input was two uploads: 31.5 seconds of previously exposed contiguous AISHELL-4 speech, followed by 31.5 seconds of declared digital zeros. No new speech selection, mixing, model, threshold or forced candidate was introduced. The speech-to-zero boundary can test functional provenance; it cannot establish conversational cross-file accuracy.

## Actual trial evidence

- All six primary work chunks completed and both uploads drained.
- Three durable v2 intents closed as no_result: two with unresolved_commit_horizon and one with unresolved_end_of_source.
- Zero automatic MOSS admissions, published jobs or completed results were retained.
- Zero demonstrated third-native-root contributions, cross-upload witnesses or combined intended-path witnesses. Partial-parent selection was also absent and is a separate outcome.
- A qualifying witness would require 3-4 distinct native commitments with exact PCM lineage, positive third-root frame contribution, both original uploads, and a completed automatic result. Digital-zero frames could count as positive frame contribution. Native commitment count is distinct from original upload count.
- The primary record retained 14 segments, 107 lexical units and 37,254 bytes of inventory evidence, with no missing inventory. Primary text, source data and default, legacy and lexical readbacks were preserved through a models-disabled restart. No partial-parent text partition was exercised.
- Both server lifecycles shut down cleanly, were positively waited, and left no owned survivors. No forced kill was required.

Elapsed trial time was 58.77 seconds. Peak observed aggregate RSS was 690,388,992 bytes; adding the fixed unknown and secondary reserves gave 2,032,566,272 bytes, below the 6 GiB budget. Peak fresh output allocation was 11,599,872 bytes, including 1,032,192 bytes of evidence, below the 64 MiB total and 8 MiB evidence limits. The fixed 900-second work and 120-second cleanup budgets were not exhausted. These observations do not imply an accuracy or long-run capacity qualification.

## Intermediate refusals and terminal reasons

Saved intermediate notices reported serialization_capacity at core preparations near 10.5, 21 and 31.5 seconds. At 42 seconds, bounded enqueue reported a serialization_capacity savepoint refusal while preserving owned targets; lexical capture was unavailable there. The later durable horizon and end-of-source reasons describe how the unresolved intents finally closed. They must not be treated as the original serialization cause, or as evidence that no overlap target existed.

The three stored binding, target and original-proof sizes were respectively 640/10,552/7,625; 660/10,616/7,659; and 655/11,604/7,870 bytes. These retained objects do not expose the complete transient query or identify the exact historical throwing object.

## Saved final-state inventory reproduction

A model-free, saved-data-only measurement applied the unchanged pure inventory constructor and canonical serializer to seven fixed inventory-derived bands. It used whole intersecting primary rows from the final saved state and all six chunk-source rows. Exact historical transaction membership is unknown. These are diagnostic preparation windows, some wider than the 20-second audio-support cap, not proposed MOSS supports or reconstructed historical proofs.

The canonical inventory limit was 65,536 bytes:

| Band in seconds | Canonical bytes | Constructor result |
| --- | ---: | --- |
| 0-10.5 | 29,940 | Passed |
| 0-19.12 | 61,349 | Passed |
| 0-21 | 77,661 | serialization_capacity |
| 0-31.5 | 122,460 | serialization_capacity |
| 10.5-42 | 100,696 | serialization_capacity |
| 19.12-52.5 | 64,853 | Passed |
| 21-63 | 53,700 | Passed |

In the three refused bands, atom descriptors persisted in both per-parent and top-level arrays consumed 46,827, 73,218 and 57,407 bytes combined. Escaped retained inventory strings contributed another 24,363, 39,686 and 34,562 bytes within the parent rows. The six-source array was 1,224 bytes in each band. This measures serialization amplification in the saved final-state representation; it does not prove the exact historical refusal size or substitute a successful certificate for a rejected one.

The scalar measurement preserved the serializer's three refusals. Source and input bytes remained unchanged; the copied-state checks also preserved original and copied database bytes. No model, audio decode or API replay was used for this diagnosis.

## Status and limits

The dev37 functional failure is preserved. No compact-representation fix has succeeded in this actual trial, and this report does not validate or approve a future fix. A later source checkpoint or trial would require its own checks. Same-epoch experimental functionality, long-run behavior, global speaker identity and transcription accuracy remain distinct qualifications.
