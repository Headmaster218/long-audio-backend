# Reviewed identity collision guard on dev12

This source candidate integrates the reviewed guard from commit
`8003918284bc068147d069ace23bfa7044c5a5f7` into dev12 product commit
`13bd20980fddea511a9f35ebd90aa81eebbc56c3`. It is not a live deployment.
There is no package-version, database-schema, policy or matching-threshold change.

## Activation and behavior

The guard applies only to the existing explicit `verified_review_v1` opt-in.
Legacy/default identity behavior remains unchanged. Use the existing
[reviewed-group activation and human-reviewed anchor API](VERIFIED_IDENTITY.md);
there is no new enrollment path or automatic review.

Within one analyzer window, all reviewed identity assignments are prepared
against the same transaction before immutable decisions or projections are
written. If distinct local IDs have valid positive-duration overlapping turns
and independently match the same canonical reviewed person, both accepted IDs
are cleared with `unknown_overlap_identity_conflict`. All conflicts are found
before any decision changes, so input order cannot select a winner. Decisions
retain the candidate, score, margin and reviewed evidence, together with
`overlap_conflicting_local_ids`. A runner-up is never promoted.

Transcript text and turn/segment time coverage are preserved. Overlap-flagged
segments retain the explicit abstention reason. Same-ID duplicates, touching
endpoints, invalid intervals, different accepted people and already-abstaining
endpoints do not create a collision. Canonical aliases are compared as one
person; exact negative vetoes, retraction and integrity checks remain in force.
Conflicts create no permanent person relation, negative fact, new person,
reviewed anchor or automatic update. Local IDs do not carry across windows.

The guard accepts at most 4096 turn rows and 64 local IDs; overflow clears
accepted IDs with `unknown_overlap_identity_capacity`, preserving the transcript.
Same-ID interval union bounds the new sweep to O(T log T + T*K) work and K²
conflict storage. These limits bound the new guard only, not all pre-existing
window-processing work.

## dev12 compatibility and validation boundary

Only the reviewed decision preparation/finalization and two `commit_window`
identity hunks change production code. Existing optional-cell and core receipt
schemas, caps, transactional outbox, replay/retry and deliberate fresh-store
migration behavior remain unchanged. Final identity decisions, support and
primary projections stay in the existing transaction with the core receipt.
An optional outbox refusal still preserves primary text and identity abstention;
a raised late transaction failure rolls back all of them.

The original 15-case reviewed guard suite is retained byte-for-byte. Additional
model-free tests cover all turn/draft orders, invalid intervals, unchanged legacy
assignment, exact negative evidence, retracted anchors, primary coverage,
core receipt coexistence, restart/idempotent retry, optional capacity rejection
and late-transaction rollback. All public fixtures use invented geometry and
synthetic assignments. A separate private timing-crossover check uses saved
real turn timings and counterfactual assignments; it is not observed identity
prediction or accuracy evidence.

The dev12 candidate's selected 21-module regression passed **405 tests and
103 subtests**, with no failures, skips or deselections. Pytest took 109.95 seconds;
observed process wall time was 111.73 seconds. This includes the existing
real-FFmpeg fake-analyzer test, all 15 original guard cases and all 23 additional
compatibility cases. The source was unchanged during testing; only this result
documentation was refreshed afterward.

Validation used standard pytest, synthetic vectors and fresh temporary
SQLite/WAV fixtures with an existing read-only runtime. No model was loaded,
downloaded or constructed. The tested process ran on CPU 3 at nice 19, with a
2 GiB hard address-space cap, a 512 MiB sampled RSS target and a 160 MiB temporary
output ceiling. Sampled process-group plus monitor RSS peaked at 195,846,144
bytes; sampled charged output peaked at 123,592,704 bytes. These sampled figures
are not instantaneous hard RSS or disk bounds. The run exited 0 within its
240-second work / 300-second total envelope, with no monitor stop condition or
remaining sampled process-group RSS. Thirteen existing temporary fixture files
were retained; this is not an all-files-deleted claim.

Long-timeline fixtures cover logical reset boundaries at 300 and 3600 seconds.
The two-upload case totals **3644.5 seconds (3600 + 44.5)** across the one-hour
upload boundary. Native/ASR activity is synthetic, and these cases execute much
faster than their logical timelines. They are not wall-duration soaks or
real-acoustic accuracy evidence.

An earlier 38-case focused run passed before two saved-timing crossover fixtures
were replaced by invented public geometry. The 405-test run covers the revised
synthetic fixtures; the earlier result remains separate evidence. The historical
reviewed guard also had 144 pure-subset passes and a separate 15-case independent
pass, which are not dev12 integration results. Historical decoder and incorrectly
restrictive-address-space failures remain historical evidence; the decoder case
passed in the new dev12 run without deselection or a product workaround.

This selected regression is not the full repository suite. No new wheel/install
validation, live API/model smoke or deployment was performed for this candidate.

This is conservative window-local abstention, not all-mode merge prevention.
Diarization mistakes can cause abstention, and wrong identities without an
overlapping contradictory track can remain undetected. Cross-day identity,
cold-voice robustness, real-audio accuracy, zero false merges and long-run
reliability are not qualified. Multi-anchor scoring and automatic adaptation
remain separate work.
