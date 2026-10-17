# dev38 fixed 63-second trial: failed gate and saved-state diagnosis

Date: 2026-10-17. Published version: `0.2.0rc3.dev38`, commit `2b01d756e34499e3169317cd38c5264a5f9d5208`.

## Scope and result

The unchanged exposed input comprised 31.5 seconds of previously used AISHELL-4 speech followed by 31.5 seconds of declared digital zeros, submitted as two uploads. The experimental local model/profile and the fixed resource limits were retained. This was a same-epoch functional trial, not an accuracy or global speaker-identity evaluation. Crossing speech into digital zeros would establish provenance, not conversational cross-file accuracy.

The actual run exited **1** after 74.549 seconds. Primary processing completed all six 10.5-second chunks, and admitted automatic work drained. Saved automatic history contained two v2 intents:

- One closed with `result_committed` and API state `committed`.
- One closed with `unresolved_commit_horizon` and state `no_result`.

The committed candidate's proof request returned HTTP **409**, with `cell_revoked`. The observer stopped before result-body validation, transcript-view checks and the models-disabled restart. A committed API state is therefore established; an independently validated MOSS result body or invocation count is not.

The required third-root and cross-upload objectives remain unqualified. The available binding declares three native commitments, but its selected support, frames `[121136,172976)`, has positive contribution from only two and stays within the first upload. Partial-parent selection was not evaluated. Restart was not run. No automatic rerun occurred.

Shutdown was clean and positively waited, with no surviving owned process or forced kill. There was no resource failure. Peak sampled process-tree RSS was 1,859,383,296 bytes, or 3,201,560,576 bytes including the fixed reserves, below the 6 GiB aggregate ceiling. Peak trial output was 12,316,672 bytes and evidence 1,105,920 bytes, below the 64 MiB and 8 MiB limits.

## Bounded copied-state findings

The closed original database namespace was preserved. An initial diagnostic stopped because an unused optional column was absent from the primary table. The corrected read removed that column, reused the verified copies and retained the first failure. Across both attempts, accounting recorded 42 reserved read slots, 41 actual statements, 90 returned rows and 299,462 projected bytes. All original bytes and copied database/WAL bytes remained unchanged; connections closed cleanly. No model, API server or inference replay was used.

Strict validation accepted the stored committed proof. Its owner-store and intent-store bytes were identical. The retained compact commitments, ordered dependencies, core hash and batch/store checks agreed. The proof measured 48,239 bytes; its inventory measured 24,528 bytes. Stored proof plus separately retained target measured 58,791 bytes.

Rebuilding the inventory for the stored scan from the final copied state produced 26,855 bytes and a different complete wire representation. The differences were exactly one added primary parent and one added source descriptor, both outside the selected support. No added, missing or changed parent/source intersected that support. Using the original target, commitments and stored query range with this current inventory selected exactly the original support geometry.

This reproduces a final-state, scan-wide inventory equality mismatch while preserving selected geometry. It does not replay the complete live authority check: live generation, original-file receipts and later mapping checks remain outside this diagnostic. It therefore does not prove the complete historical cause of the HTTP 409.

## Remaining serialization limitation

Saved notices for chunks ending at 31.5 and 42 seconds reported `serialization_capacity` with owned targets preserved. Later terminal horizon reasons are separate from those intermediate notices.

The two additional final-state preparation inventories measured 43,085 and 38,076 bytes. Both passed the unchanged 65,536-byte serializer limit. Their 31.5-second preparation windows are inventory-scan windows, not proposed audio supports; the audio-support cap remains 20 seconds.

These individual inventory measurements do not establish the size or membership of the failed historical combined object. The complete transient proof/query/handoff was not retained or reconstructed, so its exact failing size remains unknown.

The trial establishes a failed functional gate and the bounded diagnosis above. Transcript partition conservation, restart behavior and a future corrective change are not qualified by this result. No follow-up fix or additional actual run is claimed here.
