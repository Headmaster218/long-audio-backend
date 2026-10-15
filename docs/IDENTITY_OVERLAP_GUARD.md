# Reviewed identity overlap guard

Development source candidate based on productdev9 commit
`8c1431ff6b570f693b1a5431fab8e4536dfcbc03`. This change is not a live deployment.

## Scope and behavior

Only the explicitly selected `verified_review_v1` identity mode changes.
All local-speaker decisions are prepared against one transactional gallery
snapshot before immutable decision history or transcript projections are saved.

If distinct local speaker IDs have positive-duration intersecting valid turns
and independently match the same canonical reviewed person, both abstain with
`unknown_overlap_identity_conflict`. Conflicts are computed before modifying any
assignment, so input order cannot choose a winner. Candidate identity, score,
margin and reference evidence remain in the decision snapshot; the accepted
identity is null. `overlap_conflicting_local_ids` records the window-local IDs.
Overlap-flagged transcript segments retain the explicit conflict reason.

There is no runner-up promotion, automatic enrollment, anchor update, new
permanent different-person constraint or model execution. Existing canonical
aliases, negative vetoes, review/retraction and integrity checks remain intact.
Support capture, final decisions and transcript writes share one transaction.

Same-ID duplicate fragments and touching endpoints do not create conflicts.
Local IDs are never treated as stable across windows, recordings or days.
Invalid interval rows do not establish overlap evidence. Distinct accepted
people and already-abstaining endpoints are unchanged.

The guard admits at most 4096 turn rows and 64 local identities. Overflow
preserves transcript text but abstains accepted identities with
`unknown_overlap_identity_capacity`. Unioning same-ID intervals bounds the
sweep's active set at 64, work at O(T log T + T*K), and conflict storage at K².
These bounds cover the new guard, not all pre-existing window-processing work.

Legacy identity behavior is unchanged. Reviewed matching remains centroid-based
with its existing policy and thresholds. No default mode or policy is switched.

## Validation and limits

Synthetic model-free checks on the final bounded candidate: 144 passed, with one
existing real-decoder test deliberately deselected, in 25.90 seconds. Sampled
aggregate test-process-tree plus monitor RSS peaked at 165,371,904 bytes. Limits
were CPU 3, nice 19, 256 MiB sampled RSS, 2 GiB virtual address space, and 120
seconds. An independent fresh run of all 15 new guard cases passed in 1.90
seconds, with sampled child RSS of 84,656 KiB. Independent source review accepted
this isolated candidate scope.

Coverage includes order-independent collisions, aliases, duplicate fragments,
nonoverlap, unknown/different-person endpoints, capacity boundaries and overflow,
explicit persisted reasons, transcript retention, restart, cross-recording ID
reuse, no permanent relation creation and transaction rollback. Existing
reviewed matching, canonical, integrity, lifecycle, projection, review-bank and
legacy API/storage tests were included in the 144-case pure subset.

Earlier pre-bound revision results are retained as limitations, not erased:
- Expanded run: 140 passed, one existing `test_fake_worker_runs_real_ffmpeg`
  failed with subprocess exit 245; its cause was not independently established.
- A rerun under an incorrectly restrictive 256 MiB virtual-address-space limit
  produced 29 passes, 100 failures and 11 errors, with one deselection. The first
  failure was MemoryError, followed by SQLite disk-I/O errors. Correcting the
  test resource configuration did not require a product workaround.

The passing pure subset is not a passing full decoder suite or a real-audio
accuracy result. Diarization errors can cause conservative abstention; wrong
identities without simultaneous contradictory tracks can remain undetected.
Long-term physical identity, cross-day stability, cold-voice matching and zero
false merges are not qualified by this change. Multi-anchor scoring, automatic
prototype adaptation and contiguous-span embedding selection are separate work.
