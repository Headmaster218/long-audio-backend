# Bounded qualification and limitations

## Result

The narrow storage defect is qualified: after loss of the prior process, a
primary-history reservation can be closed conservatively as missing evidence
under a positively verified new instance owner. This restores scope-specific
comparison discovery without replaying inference or inventing a success.

Root-executed finite evidence consists of:

- 131 distinct source cases: the 58-case crash/startup/regression gate, four
  authenticated-admission boundary cases and 69 additional unchanged targeted
  regressions. The earlier 16-case stage is included in the 58, not counted again.
- 57 installed-package cases, all passing with no skips or errors. These repeat
  selected source cases against installed bytes; they are not 57 additional
  distinct cases, and the total must not be called 188 distinct tests.
- A disposable snapshot-copy experiment with four full audits: before recovery,
  after recovery, same-owner idempotence and fresh-owner idempotence.

The copy changed from 272 successes / 21 failures / 1 reservation to
272 successes / 22 failures / 0 reservations. The original snapshot remains
272 / 21 / 1 and was not repaired or replaced. Only the copy was mutated.

Independent review verified all 69 tables and 159,259 rows: exact permitted
changes in three history tables, unchanged unrelated data, and identical full
raw logical audits for both idempotence passes. All 4,115 completed chunks and
25,004 combined result rows were conserved. The latter count is the total across
segments, speaker turns and events, not a count of segments alone.

The successful copy run used two genuine instance-lock acquisitions, two
bounded ten-page drains, positive lock cleanup and three serial original-process
exit-zero waits. Final preservation verified unchanged original, mirror and
retained failed-working-copy file families. Root enforced the same experiment's
2 GiB cumulative limit, including retained earlier copies, and the established
5.5 GiB filesystem free-space floor. Original-family reads used no-atime,
no-follow, read-only descriptors; no SQL connection opened the original.
Resource accounting sampled lifetime inode maxima and retained the preparation
charge across the wrapper restart; it does not prove the absence of unseen
transient peaks. Original numeric waits and complete post-wait resource checks
remain part of the evidence.

## Implemented boundary

- A real `InstanceLock`, exact descriptor/inode ownership proof and namespace
  `ownership_fence` establish recovery authority. A UUID difference is not
  authority.
- New primary-history admissions stay closed until bounded startup recovery
  completes. The keyset traversal examines at most 32 attempt rows per batch.
- Only reserved primary attempts with the exact retained basis/accounting and
  pending evidence are eligible. Existing terminal outcomes remain immutable.
- Unknown commit outcomes are re-observed under the qualified lock; they never
  authorize inference replay. Lock or ownership ambiguity fails closed.
- Missing-evidence closure releases reservation/staging charges while retaining
  authenticated evidence and preserving withdrawal/deletion constraints.
- Recovery is bound and drained before the real application's success owner is
  installed. Maintenance continues a pending drain; shutdown revokes authority.
- No automatic-template policy, source garbage collection or model logic changed.

Linux descriptor-lock proof is deliberately required. If `/proc` lock evidence
is unavailable, primary-history admission fails closed, including for empty
stores. Ordinary primary transcription remains available. This qualification
does not establish primary-history admission on unsupported platforms.

## Installed qualification adaptations

The offline package gate used three serial original children: build, install
and installed tests. All were positively waited with exit zero, as was their
original root process. Source/wheel/installed parity and loaded origins were
checked, with no dependency download or permanent runtime mutation.

This is explicitly a plugin-disabled qualification: optional Pydantic plugin
autoload and pytest plugin autoload were disabled. Normal schema validation
remained active. It is not acceptance of arbitrary plugin-enabled deployment.

One unchanged fixture's explicit AST source-root constant was routed to the
verified installed payload. Its loader/function bodies and test assertions
were unchanged; helper identity and five installed AST input hashes were
checked. No model-heavy module import was used. This is a disclosed fixture-root
adaptation, not a claim that the fixture environment was wholly unmodified.

The installed selection excludes only the separately qualified three-process
old-source crash test, whose helper layout is source-specific. Its genuine
crash, old-source negative control and recovery evidence remain separate.

## Preserved failures and corrections

All earlier outputs and limitations remain preserved in private qualification
evidence. None was overwritten, silently omitted or promoted into a pass.

1. The initial runner guard omitted SQLite connection-target validation. Review
   blocked execution until disposable-only connection checks were added.
2. Initial pytest logging opened the default null-device write target and was
   denied. The new revision explicitly routed logs inside disposable output.
3. The first process-crash fixture attempted a null-device open before child
   creation. A new revision used a pipe/EOF and saved exact original-child waits.
   The other 57 controls in that failed run remained recorded, not erased.
4. Two external root-monitor failures interrupted the four-case source gate:
   pytest's current-directory symlinks, then a transient deleted-file census
   race. Neither partial run was claimed as a completed suite. The first wait
   lacked a saved numeric child exit; that limitation remains explicit. The
   second original process was positively waited with a termination result.
   Monitoring was revised to no-follow inode accounting, conservative lifetime
   charges and a bounded clean-census deadline; application guards were unchanged.
5. Packaging initially inherited a read-only disposable build-root directory.
   Only that verified new root gained owner-write; originals and file payloads
   remained unchanged.
6. Offline install metadata attempted external distribution-info helper probes.
   Install-only documented metadata-source configuration disabled those probes;
   subprocess and null-device write denials were not relaxed.
7. Installed application import triggered optional Pydantic plugin discovery
   through legacy distribution metadata. Explicit plugin-autoload disabling
   replaced that discovery; legacy-product reads stayed denied.
8. One installed run recorded 54 failures and three passes, all 54 from the same
   source-relative AST fixture read. The verified installed-root fixture routing
   described above corrected the harness without weakening test assertions.
9. The first copy audit assumed a zero absolute capture origin. It failed during
   the baseline audit, before any lock acquisition or recovery mutation. A
   separate read-only verifier proved preservation and an empty working WAL.
   A fresh working main was then copied from the verified mirror, retaining the
   failed copy. The revised audit checks exact supplied absolute frame values
   and the unchanged duration separately, rather than normalizing the origin.
10. After successful fresh-copy preparation, the root wrapper expected the old
    receipt filename and stopped before application launch. Root verified the
    actual fresh-copy receipt and continued only the remaining stages. Preparation
    was not repeated and no additional full copy was created for that error.

## Claims not established

No actual comparison was executed in the copy experiment. Making comparison
work discoverable does not prove speaker matching, correct merging, stable
identity learning or acoustic accuracy. No real model replay or new success was
used to repair storage. Recognition quality, plugin-enabled deployment,
production-scale performance, power-loss durability and release/deployment
acceptance remain outside this checkpoint. V2 remains NO_GO.

The copied-data storage result is accepted within its finite reviewed envelope.
This sanitized checkpoint itself remains pending final review and publication
permission. It contains no private database/audio or raw diagnostic evidence.
