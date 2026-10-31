# Exclusive-primary cache retirement: finite source qualification

Date: 2026-10-19 UTC. Status: source-only WIP recovery checkpoint, not a packaged release.

## Result and exact attribution

All 93 unique, explicitly selected synthetic cases have passing source-bound evidence.
This is not a claim that all 93 were rerun on the final tree. The accepted disjoint
credit sets are 34 unaffected historical A cases, 34 corrected-collector A/B/scope
cases, 11 first-C cases, C2, 11 corrected-history/ACK/legacy cases, and the final two
legacy cases. All ten attempts, including seven failed attempts, remain retained
and charged. No automatic retries occurred; each correction and continuation had
an independent source/control review and a serial execution release.

The final tested implementation tree is `1676fac0690b0f9ed195d5add4b8c93f68702a6f`: 576 files,
10,466,337 bytes. Its manifest SHA-256 is
`d14e40529a943b8f926524d78dbf34f5001e46ca2f4960f97a469481aeabcf82`.
The final two cases ran on this tree. Earlier coverage is explicitly tied to its
original source, inputs, exact node lists and retained execution receipts.

This checkpoint's sole parent is `dedaf4664b3421ce2b775ee20c84be9431e23323` (tree `38fd58cd83e949b4d79067754b7879b366b87272`).
It preserves the integration checkpoint report and every earlier protected report
unchanged. Only one product module and three test files differ from that parent;
this status report is the sole added path. Across the entire feature, product
changes remain limited to the eight approved existing modules in the accepted
plan, SHA-256 `aabc0df10c585b07f635eb09d2aff00e506fee11e7dac89946f0ba46da3cf80e`.

## Implemented contract

- Only newly created, exclusively primary-only recordings can receive the immutable
  `primary-cache-retirement-v1` marker. A verified current CellStore identity is
  bound atomically with actual page publication, using the real CellJobsSource
  adapter's owner/store relationship.
- A marked recording costs 512 logical accounting bytes under existing quotas.
  This is a real accounting reservation, not a physical SQLite allocation claim.
  A known optional marker-capacity refusal leaves both marker fields NULL forever
  and preserves primary recording creation. Genuine schema/accounting failures and
  uncertain writes remain hard failures. Existing caps are not increased.
- Exact SQLite schema migration/serialization, trigger/index validation, immutable
  binding, schema-cookie invalidation and predecessor-reader refusal are retained.
  Anti-mixing checks cover admission before leases, restarted work, direct SQL
  guards, publication preflight and its transactional final recheck.
- Every actual compact/receipt deletion candidate still requires durable receipt
  proof, current physical source identity, exact canonical membership, verified
  owner/store qualification and current roots. Qualified completed gap rows can
  advance a prefix without inventing durable receipt authority for those gaps.
- Every held primary copy remains a root until its exact drop, including provisional,
  ready/running, cancelled/terminal and unknown-ACK states. Lock/shape/owner
  uncertainty defers collection. Legacy, mixed and unqualified domains remain
  conservatively retained.
- Existing collectors, eight-candidate batches, keyset progression, time/projection/
  SQL budgets and unknown-COMMIT suppression remain. No history/comparator/packet-body
  change, new inference permission, MOSS overlap fabrication or audio unlinking is
  introduced.

## Real-cap rolling and bounded collector observations

The corrected-source cap tests filled the actual 2,056 compact and 4,096 receipt
limits, then performed four collection/replenishment waves for each. At least 16
eligible entries were retired and replaced with novel sources per test, cumulative
admission exceeded the original cap, and occupancy returned to the unchanged cap
after every wave. Unrelated cold rows were byte-preserved. The receipt-capacity gap
remained unqualified and receipt-absent while later qualified prefix progress
continued. These are metadata/cache mechanism observations, not hours of audio.

The first dense held-root fixture exceeded the existing 150,000-step limit at
150,100 steps. It was not accepted or rerun with a raised limit. The narrow product
correction reused the existing optional `list_read_scope` connection context in
both collector wrappers. Each authority read still opens a fresh BEGIN/ROLLBACK
snapshot; no authority values are cached. Nested/exception cleanup and a real
separate-writer authority change were then tested.

The corrected dense evidence contains 452 collector operations: 258 compact and
194 receipt operations. Each operation used at most one authority connection.
Compact maxima: 64 fresh snapshots, 88,100 total VM steps, 6,000 authority VM steps
and 139,753 projected bytes. Receipt maxima: 32 fresh snapshots, 41,400 total VM
steps, 3,900 authority VM steps and 68,142 projected bytes. All recorded snapshots
balanced and all connections closed once. These are prospective measurements on
the corrected source; the failed historical run had no exact connection counter.

## Evidence conservation and separate authority contracts

The real app/Worker fixture activates and binds the fresh marker. Synthetic
streaming responses expose only the supplied frames as stable and notify once;
the real coordinator, permits, commit and page lifecycle remain in use. The finite
tests cover marker refusal without lost primary creation; actual SQLite migration;
restart and anti-mixing; held roots; exact-ACK rereads; unknown COMMITs; source
replacement/revocation; packet, transcript-ref, history and audio conservation.
One fresh interpreter ran the exact frozen predecessor Store initializer with a
populated migrated synthetic database, proving old startup refusal and unchanged
logical schema/table/packet/charge/audio snapshots. It ran no app worker or model.
Other restart cases are same-interpreter close/reopen checks; they are not
represented as fresh-process startup qualification.

Live producer authority and durable history evidence are distinct. A completed and
dropped producer cannot be recreated by passing `admitted=True`; fresh standalone
mapping can correctly refuse a retired ingestion receipt. Existing held roots keep
actual unresolved producers protected. Durable HistoryStore evidence is instead
validated through its real bounded begin/page/revalidate/finish metadata APIs,
retained receipt objects and exact live source facts, without a comparator child.

After both caches are absent, history receipts and source facts still validate.
Cancellation, transcript revision and digest mutations invalidate the genuine
history build; physical source replacement fails retained-receipt validation.
An owner-generation change rejects the exact old live producer fence while valid
admitted durable history remains conserved. Ordinary owner rotation/restart is
not misreported as durable source deletion.

The C2 unknown-rival case cancels the exact positively admitted second bootstrap
core, verifies its terminal candidate-missing certificate/attempt, consumes the
already-requested real Worker handoff, and later proves that core's own compact and
receipt are gone while its exact missing certificate and conservative transcript
abstention remain. Sampling uses stream-relative frames: cores seven/eight start
at 63/73.5 seconds and are not due after the six bootstrap claims. An earlier
absolute-origin diagnostic was corrected; it is not the final cadence explanation.

## Retained attempts and resource accounting

| Attempt | Passed calls | Work s | Total s | CPU s | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| gate-a | 0 | 5.666501 | 6.259004 | 7.242520 | A1 fixture path error |
| gate-a-recheck | 1 (A1) | 7.219481 | 7.968469 | 8.878523 | A1 pass; open-stream fixture error |
| gate-a-continuation | 43 | 30.727276 | 31.374710 | 30.616205 | A43 passed |
| gate-b-current | 2 historical only | 10.012070 | 10.694813 | 11.438650 | Two cap passes; dense SQL-budget refusal |
| gate-b-scope-continuation | 34 | 39.148745 | 39.860706 | 39.347346 | A10 + B21 + 3 scope cases passed |
| gate-c-current | 11 | 18.317740 | 19.038026 | 19.356649 | C11 passed; non-due cancellation fixture error |
| gate-c-continuation | 0 | 131.684159 | 132.445305 | 50.433380 | Controlled work deadline; no completed node |
| gate-c-handoff | 1 (C2) | 14.018693 | 14.830280 | 15.600787 | C2 passed; producer/history test-contract error |
| gate-c-history | 11 | 20.712120 | 21.503203 | 22.079933 | C11 passed; legacy notification fixture error |
| gate-c-legacy-final | 2 | 2.387655 | 3.153819 | 4.136139 | Final two legacy cases passed |

All-ten-attempt totals: **279.894440 s work,
287.128335 s total,
209.130132 CPU-s**, below the
approved cumulative 540/600/420 limits. Maximum sampled aggregate RSS was
251,404,288 bytes (limit 402,653,184),
and allocated artifacts peaked at
138,567,680 bytes (limit 167,772,160).
These are sampled bounds with the documented census policy, not universal hardware
or allocation guarantees. CPU affinity 3/nice 19, per-process address-space limits,
zero undeclared children, original-handle waits and strict final source/input/prior
evidence checks were enforced. Disposable temporary/WAL disappearance can make a
sample incomplete; complete-sample age remains bounded and final census is strict.

The stalled C14 attempt hit its cooperative work deadline about 0.0019 seconds
beyond the threshold, received TERM and was positively waited. It received zero
pass credit. Its `bounds_observed` field excludes work time and must not be read as
an all-limits pass. Root separately approved the subsequent finite allocations;
the exhausted original C allowance was never silently reset. No forced kill or
cleanup error was recorded in any attempt; all observed descendants were absent
at each terminal check.

All failed sources, raw reports, traceback/stdout/stderr, fixtures, origins and
receipts remain local evidence. The initial fixture path read, open-stream stable
frontier, non-due cancellation and missing manual handoff were corrected only in
their scoped test contracts. C3's fabricated post-drop producer authority was
replaced with the genuine durable-history APIs. The final isolated legacy fixture
already lacked the predecessor-required store/notification seam; its one-method
sentinel/mock now asserts exactly one notification while preserving the real third
publication validation and exact `source_changed_after_publication_commit` reason.
No product guard was weakened to make a test pass.

## Recovery evidence identities

The evidence summary lists all 93 exact nodes and their nonoverlapping source-bound
credit, ten receipts and cumulative limits. Its SHA-256 is
`1b303ded903a6461f4c6df20633eba6d5bba26ca9a72e7d0d942646b77b557ca` (`qualification-evidence-summary.json`).
The independently reconstructed 93-node source-attribution map is
`633f0fdad6e614cc31d4720d02f2a0fa15e7562765d8d84ae238cd4377965e76`
(`independent-final-source-attribution.json`).

Execution receipts:

- `gate-a/execution-receipt.json`: `b4cd46306aaf7c26cd8aa332b6f3427e4d2d08fbef282d687e020f4c826ad31a`
- `gate-a-recheck/execution-receipt.json`: `102decc68f3090c298e40d774b9b5c673734fce9482dd9bfea51d32958b5c194`
- `gate-a-continuation/execution-receipt.json`: `d86d72927654d0995d1ac1e73dba404eb88d57682b9d1495fd3626ee370382de`
- `gate-b-current/execution-receipt.json`: `54ca93c9ee90bafcc0d292003a7db9a110fb4d8449e5f8f8f026a60d82517195`
- `gate-b-scope-continuation/execution-receipt.json`: `a6f4c3bb28466d6c34b8a9a4ba8308b57b399affc246db30074e347e03935fb1`
- `gate-c-current/execution-receipt.json`: `0e185af1fd7afe0d30e6dd4437dec7bb20e0894f7528a953aa273d6a3289c81f`
- `gate-c-continuation/execution-receipt.json`: `4b39b1011c6b9c45dbceee7bce3b1b9b5863f1c412056a04d492c08f883d173c`
- `gate-c-handoff/execution-receipt.json`: `0e3f84ece81aa3ed09fd3a38c5e7f5f3010bdfe4d7740968478fd87cf5957e85`
- `gate-c-history/execution-receipt.json`: `898c3de9640b1fee005e559df14304b1b1108d4aeb7c9738bc239f8f619e4bd3`
- `gate-c-legacy-final/execution-receipt.json`: `25ec1a2ea70babe21acbc5080068ab54b9a7d17ceac87fbc87a7b8c788be0392`

Independent result/stall review receipts:

- `independent-A-result-review.json`: `e7ed015d3ee10869e19219f13e945b4b7f5be66a371e7ca82e38890971204c40`
- `independent-B-scope-result-review.json`: `3cea37bd003a712885e852bbf1f5e90dcf117ab6c0d077dd1eb4525789df7d60`
- `independent-C-failure-review-v2.json`: `1c13af97528d05c454b851a48a4f0986014661af8b99fc64e0c56558efca9f8b`
- `independent-C-continuation-stall-review.json`: `45ac3bdd5c81a37a4d734880384c5d466aac156769dff9de277077b8df0523e4`
- `independent-C-handoff-result-review.json`: `d9c0d7e8fde324fe6b6ce63f63035bf55e1fd29bde83d09d5672a57cc6e55666`
- `independent-C-history-result-review.json`: `b81f77e07aa4502ea05e5b4e21530418b24c0ca823ce779b45c3006bc2fbb276`
- `independent-final-result-review.json`: `396e46346bd0af61d148cb3753ac5b1b6c7f4ab9261b87108a384729490a22ca`

## Limits and next gate

This qualifies only the selected synthetic source contracts. It does not execute
the full repository suite, prove power-loss durability, qualify arbitrary SQLite
filesystems/runtimes, or establish actual long-duration speaker identity/ASR
quality. It does not establish 12–48 hour acoustic endurance or audio-retention
capacity. Existing PCM/provenance quotas and the independent audio retirement
protocol remain; this slice never unlinks audio. Removing these cache ceilings as
the immediate rolling blocker does not remove those other limits.

No dependency installation, network/model call, version change, wheel build,
deployment or new package runtime qualification is part of this checkpoint.
Any subsequent version/docs/package gate needs separately approved exact source,
artifact parity, commands, process topology and bounded resources.
