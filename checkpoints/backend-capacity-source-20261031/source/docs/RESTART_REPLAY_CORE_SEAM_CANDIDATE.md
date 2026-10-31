# Restart-replay seam contract

The dev23 source checkpoint extends dev22 (`9ec48015843b19ab1708690e4dc235f1b2a056ab`). A fixed 42-second real-model case produced a validated restart seam and seven retained automatic results. Its original helper exited 1 on a history-pagination assertion; a separate models-disabled GET-only check verified all seven historical results. This is bounded restart evidence, not continuous-run or speaker-identity qualification. See [dev23 scope](DEV23_STREAM_SAFETY_ZH.md).

## Contract

`native-owned-restart-replay-core-seam-v1` is an additional proof policy. The historical left primary compact stays byte-for-byte unchanged. The right compact belongs to a different, freshly observed Capture run. Session, source scope, origin and runtime must agree; committed ranges must be adjacent. Epoch indices must be equal or consecutive. Equal indices retain the deterministic native epoch ID; consecutive indices require distinct epoch IDs and the exact 300-second boundary.

Within one epoch, each side contributes its actual available support, capped at ten seconds. This includes partial-core restart: `[10.5,19.12)` and `[19.12,21)` give `[10.5,21)`, with no invented padding. Across consecutive epochs, each committed side must contain ten seconds, preserving the previous short-reset-side refusal.

NativeCoordinator's existing bounded replay performs all pushes. CoreSeams then makes exactly one extra current `turns` request over the exact support interval. It does not push, finish, run ASR, or adopt a historical turn response. The observer must record that response as the next action without a reset, frontier change or owner revocation. Current replay bytes must match immutable source PCM; replay, input/processed/stable coverage and runtime are validated by the unchanged strict witness validator. Query PCM and support PCM hashes must agree.

The new query records `capture_run`, and each label is `capture_run:epoch_id:speaker_N`. Validation binds that namespace to the right compact before validating the original epoch-local producer label. Recording-speaker links use a separate run-aware native query scope for this policy. No old label is inferred from a prior response. This does not retroactively reinterpret ordinary or historical proof label domains or establish a person's identity. NativeCoordinator's deterministic identity and optional permit handoffs are unchanged.

## Persistence and compatibility

The existing compact transaction, optional outbox, CellStore writer, automatic ledger and job manager own all work. There is no additional worker, journal, table, primary-schema change or backfill. The automatic public `kind` remains the existing `core_seam` family; `selection_policy` identifies the restart contract and is part of its ownership binding. A right primary commit and automatic intentions retain their original atomic transaction. Completed history is immutable; accepted-unknown attempts follow existing interruption reconciliation and are never instructions to rerun inference.

The optional store uses a new explicit policy hash, `native-owned-cell-store-policy-restart-replay-v1`, accepting the new proof schema plus the previously supported proof schemas. This does not accept an old store-policy identity. A store with the old reset-replay policy is refused with `optional_primary_binding_mismatch`; it is not silently adopted or migrated. Keep old stores and their paired primary databases untouched. This policy requires newly initialized optional state; do not overwrite or silently adopt an existing optional store. Preserve the complete old data directory and its paired primary database before choosing a fresh deployment.

Historical v3 same-epoch and reset-replay contracts remain strict and continue to reject cross-run proofs when named explicitly. A physical restart selects the new contract rather than relaxing either old policy. Missing replay, mismatched runtime or source bytes, unobserved queries, source deletion, source revocation, missing receipts and altered authority prevent proof acceptance/publication. In required automatic mode, a failed preparation cannot advance the primary offset.

## Verification

`tests/test_restart_replay_core_seams.py` uses the existing real Worker, SQLite, source receipts, owner thread, outbox and automatic application harnesses with fake acoustic providers. It covers same-epoch/300-second restart, the nonfinal partial-core flow, current-run label isolation, proof mutations, preparation/publication refusals, transaction and owner-acknowledgement loss, optional-store rejection, automatic unknown admissions, and preservation of completed history.

The model-free tests use fresh databases and fake acoustic providers. Run the relevant files with ordinary pytest after installing the project's test dependencies. Tests do not require historical databases or model inference. Test failures and later corrections are counted separately in the development notes; no failed run is relabeled as a pass.

An initial automatic smoke case exposed a pre-existing fake provider returning turns beyond the requested interval. Only the integration fake was corrected to honor query bounds; the strict product validator was retained. Two later test-assertion mistakes (counting ordinary cells alongside the new seam and naming `metadata` instead of `store_meta`) were corrected without a product change. Exact pass counts and final source hashes are in the accompanying candidate report and manifest.
