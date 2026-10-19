# Primary-only observation coordinator: experimental source checkpoint

This is a source-only WIP over `a85433614557217a1c5e8d3fa08a2e4bcb4dca9c`.
The finite model-free qualification is complete; no new package, version, model,
acoustic, sustained-runtime or deployment qualification is implied. Use only an
isolated experimental copy, retaining the existing schema/downgrade safeguards.

## Material lifetime limits

New primary-only compact and source-receipt records have no MOSS batch/ordinal/
retention-store qualification. Existing conservative GC therefore does not retire
them. Skipped seam drains also leave `CoreSeams.work_pending` true, independently
preventing receipt GC. No retention policy or production cap was changed.

- The shared global compact cap is **2,056 rows**: approximately **5 h 59 m 48 s**
  at one 10.5-second core per row from an empty store. Existing rows consume the
  same capacity, so new observations may stop earlier.
- The shared source-receipt cap is **4,096 rows**: approximately **11 h 56 m 48 s**
  at that cadence from an empty store. The earlier compact limit already prevents
  new observations. Existing upload/derived-PCM quotas can bind earlier still.
- Direct finite tests filled each actual, unchanged cap with explicitly synthetic
  metadata. Compact refusal and actual receipt admission/outbox terminal-gap
  publication both preserved committed primary text, completed status and exact
  next offset, without creating new provenance or observation attempts for the
  refused path. Existing filler rows remained unchanged. This is capacity
  conservation evidence, not a long-duration measurement.

Independent, safe primary receipt/compact retirement is mandatory next work
before claiming 12–48-hour usefulness. It must preserve admitted certificates,
history, unknown-rival abstention and revocation. This checkpoint does not solve
that lifetime limitation and does not introduce cell PCM retention.

## Implemented scope

Twelve production modules add an exact opt-in, startup-bound primary-only mode
using the existing owner, permit, bounded capture/commit and observation
coordinator. It requires the approved primary-history policy and native/lexical
prerequisites, with both MOSS flags off and no MOSS model path. Two CLI switches
expose existing prerequisite settings; default behavior remains off.

In this startup mode, MOSS HTTP/manager/adapter dispatch, candidate planning,
legacy seam publication and MOSS queue service remain closed, including after
flag changes. Primary text commits retain optional observation refusal, while
SQL failures, consumed budgets, rollback uncertainty and unknown commit
acknowledgement remain hard boundaries. Maintenance and cleanup keep the exact
owner reachable without inference replay. Existing combined/default callbacks
and thresholds, schemas, scheduling policy and runtime caps were not changed.

## Exact finite qualification and attribution

Executed final operational source tree:
`539e1dc3c1866bb0720775e2ecd119da69770076` (572 files; 10,314,229 bytes).
This report is a post-test documentation overlay only; the checkpoint tree also
contains it. The twelve product files were unchanged after tree
`4a63dcffa523f060cec086e47cb3636d827b94bb`.

The accepted coverage is **48 unique application nodes** (A24, B12, C12), plus
one separate SQLite-lifecycle monitor control. There were **86 selected
application-node slots across invocations**, including failed/interrupted attempts and affected
reruns: 56 fully reported case occurrences (53 passed, three assertion failures)
and 30 slots in aborted invocations with no completion credit. The single
monitor-control selection is separate. These are not 86 passes.

- A: 18 unaffected passes on `bf9154f5bfe9bd4648dcf0abdbd18002db6230cc`,
  carried forward by independent call-path review; six affected callback/owner
  cases passed on `4a63dcffa523f060cec086e47cb3636d827b94bb`. The later changes
  were isolated to the new pipeline test helper and mixed-history test body.
- B: eleven complete passes on `804dfa6b0694abd698bf141e4cb9b41911b4ca6b`,
  including both actual-capacity refusal paths; the corrected mixed-history node
  passed on `539e1dc3c1866bb0720775e2ecd119da69770076`. Independent review found
  the later test-body changes did not affect those eleven passes.
- C: all twelve unchanged compatibility nodes passed on the final operational
  tree. They include genuine-profile synthetic ownership/restart behavior,
  combined MOSS coexistence, a fresh passive reopen, exact acknowledgement/
  cleanup boundaries and the existing tiny isolated comparator.

The mixed-history case uses the existing one-second fake MOSS phrase. It
produces genuine MOSS history admission with native-primary duplicate evidence
and authenticated witnesses, then preserves that history, a cancelled-primary
unknown rival and dormant queues through primary-only reopen. It **does not
qualify an independently vectorized MOSS phrase**.

## Retained failures and corrections

All original raw failures, interrupted output, source attribution and cleanup
receipts remain retained; no aborted attempt gains pass credit from progress
characters alone.

1. Initial A: 23 passed, one failed because replacement of the registered
   authority reached its `owner` attribute before the new primary-only permit
   fence. A narrow guard-order correction preserved the old combined branch.
   The first six-case recheck hit its overly tight 5.5 CPU-second ceiling before
   complete qualification output, receiving zero credit. A reviewed six-case
   continuation completed; the other eighteen cases were statically unaffected.
2. Two B invocations were interrupted by disposable-file allocation-census
   races, with no complete JUnit/origin record and zero credit. The first raw
   `F` remains unattributed. Static review separately corrected a missing
   test-local `BEGIN` required by `read_exact`; this did not change production.
   The conventional monitor now records transient incomplete disposable samples,
   requires a complete initial/final census and a complete sample at most one
   second old, while retaining strict protected-path/identity/permission checks.
3. Completed B then passed eleven nodes and failed the mixed-history setup.
   A single diagnostic run preserved its three-second phrase and all assertions:
   binding and candidate capture returned, then `moss_staging_bound` refused with
   `history_staging_capacity` before admission. That reason covers byte,
   graph-count and depth guards; no exact fired threshold or peak was measured.
   No production guard was weakened. The reviewed test-only correction reused
   the established one-second phrase, retaining every history/rival/reopen
   assertion and explicitly narrowing the vector-coverage claim above.

The separate real SQLite WAL-lifecycle monitor control passed but observed no
incomplete sample itself. The later complete B run did observe two incomplete
samples, followed by complete bounded samples; its maximum complete-sample age
was 0.122390 seconds. No new recorder framework was introduced.

## Resource and evidence boundary

All ten retained supervised invocations, including failures and the monitor
control, consumed 152.480263 seconds work, 155.457487 seconds total and
114.837689 aggregate CPU-seconds including ten conservative one-second outer
wrapper allowances. The final approved aggregate envelope was 175 seconds work
plus 40 cleanup / 145 CPU-seconds. The small failed A recheck exceeded its local
CPU subdivision and remains marked as a bounds failure.

Peak sampled owned-family RSS including the wrapper allowance was 229,572,608
bytes; peak sampled cumulative fresh allocation was 44,818,432 bytes, below the
384 MiB / 64 MiB guards. These are sampled observations, not continuous kernel
aggregate caps or a complete transient-process census. All original waits and
observed-family cleanup completed; no forced kill, cleanup error or observed
survivor remained. Source/input pins matched at each terminal check.

Supervisors and non-comparator pytest ran on CPU 3 at nice 19; comparator-bearing
pytest used CPUs 3/4 and the existing child settled on CPU 4. Each pytest address
space was capped at 1 GiB. No real model inference/runtime import, network,
download, dependency install or external audio decoder ran. Synthetic PCM and fake model
edges exercised real app, Worker, owner, permit, SQLite and tiny comparator
paths. Dependencies were already-pinned local bytes; minimal absent-only local
materialization of four public test dependencies is not a historical-runtime
parity claim. The qualified SQLite filesystem was the workspace, not `/tmp`.

Evidence identity pins (SHA256):

- Final frozen controls: `13eb388f00af4e907f5a2fe86d7d98ba5559579445b46ae80f70dc7fe3f98a8a`
- Complete terminal evidence manifest: `cd95094883da013ca3d49f7f25dc6c710e62312d5cac720f14ff4123f8a0a5af`
- B correction receipt: `8a6ecc7b25f3c75903eb31dbd0c94da63f5805c16fcd03fad36a5fdd98d46a26`
- C receipt: `6614580d3373a23d61023e06ad82dad1de3b21268780164598c7f887e0c96473`

Existing historical reports, failures, acoustic-inconclusive results and accepted
rivals remain intact. Packaging/version changes and sustained retirement work
require separate review and qualification.
