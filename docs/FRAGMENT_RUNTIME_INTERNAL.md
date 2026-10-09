# Internal logical fragment runtime

This document records the dev3 internal contract. The default-OFF dev4 offline
extensions and their separate evidence limits are described in
[FRAGMENT_EXTENSIONS_INTERNAL.md](FRAGMENT_EXTENSIONS_INTERNAL.md).

This is isolated, model-free groundwork. It does not enable production fragment
processing. The existing worker, API, receipt/result tables, matcher, native
backend and identity banks are unchanged. No real-audio overlap-quality claim or
real-model accuracy claim follows from these tests.

## Accepted input

The caller supplies an already-decided, validated `SourcePlan` and original-file
bindings. The runtime never rematches a seam or derives a trim. Only original
16 kHz mono PCM16 WAV and one continuous island are admitted. Alias-only receipts
supply no PCM and create no core. Unknown relations, ambiguous overlaps, gaps,
mixed formats, resampling and codec expansion are rejected. The prototype's
4,096-source / 8 MiB serialized-plan bounds remain in force.

`LogicalPCMReader` provides exact integer-frame windows, original-source ranges,
source hashes, a mapping hash and a PCM hash. Each read is at most 60 seconds and
128 owner parts. Initial construction/restart hashes and inspects every binding;
append verifies only new sources and retains the accepted old identities. Path
relocation requires reopening the reader and hashing again. Before/after read
and before publication, file signatures must still agree. This assumes controlled
immutable storage; metadata signatures are not authentication against an
adversarial filesystem. There is no silent legacy-decoder fallback.

## Components and identities

- `fragment_runtime_contracts.py`: fixed sample/grid/epoch rules, bounded provider
  and output contracts, identities and defensive envelope validation.
- `fragment_runtime_store.py`: a separate SQLite database containing logical runs,
  immutable plan revisions, immutable core envelopes and a durable EOS control.
- `fragment_runtime.py`: bounded logical reader, injected-provider runner and
  native lexical/raw-interval ownership.
- `tests/test_fragment_runtime.py`: deterministic synthetic plans, fake providers
  and transaction/control fault injection; no matcher decisions or model loads.

The global grid is 30 seconds, the native epoch 300 seconds and default analyzer
context 2 seconds. File cuts never reset the grid. The native key contains run,
island, epoch, native fingerprint and canonical contract, not the changing plan
hash. Window IDs contain stable logical ownership bounds and policy. Native replay
starts at most 21.2 seconds before the owned epoch; pushes/reads are bounded to
60 seconds and total input per native epoch to 360 seconds. If native stability
lags beyond analyzer context, real available lookahead is consumed in bounded
increments. Neither file cuts nor ordinary epoch cuts call finish.

The analyzer receives one `PCMWindow` and a typed `NativeStatus`. Every original
native word/phrase is published with its own full interval, native unit kind,
original logical anchor, separate routing anchor and source scope. The analyzer
contract permits `start <= ownership_anchor <= end`. A native anchor equal to a
positive-duration support end routes just inside that support on the left; its
original anchor and interval remain unchanged. This applies before EOS as well,
so a live exact-grid/context-zero core retains its endpoint word before later
zero-sample finalization. An ordinary boundary anchor whose support extends
right remains right-owned. The frozen `native-support-end-left-routing-v1`
policy is included in the durable policy hash; incompatible older checkpoints
fail closed rather than silently adopting it.

Routing is computed once on the global axis. Sub-nanosecond integer-frame
representation drift may be snapped only in the separate routing point, which
stays inside the original support. Timestamp validation allows an absolute
1 ns representation tolerance, without clipping native evidence or changing
exact PCM-frame ownership. Existing
bounded lexical jitter reconciliation is reused and its logical tail is durable.
A repaired word may intentionally be published just left of the nominal core.

Analyzer source-track IDs must identify compatible source observations within an
epoch; they are not global speaker identities. IDs are epoch-scoped before
reconciliation. Candidate-window tracks and their provenance are not integrated.
Independent native speaker turns and events retain original support, instance
identity and independently intersected owned pieces. Multiple rows representing
one continuing event are not a new occurrence count.

## Durability and retries

`RuntimeStore.create` atomically inserts the initial plan and any EOS intent.
`append` requires an unchanged accepted source/seam/span/alias prefix, a revision
compare-and-set and an idempotent import operation ID. A true-final plan cannot
be extended. Waiting registration uses the same revision/cursor fence; alias-only
append and finalization wake waiters even when logical duration does not change.

A core publication transaction checks the complete offered snapshot against
SQL-held state, inserts one unique envelope, persists lexical tail/native receipt,
advances the cursor and, for the terminal core, acknowledges EOS atomically.
The envelope binds policy, source mapping, PCM hash, ownership bounds and native
scope/frontiers. Cancellation is independent and blocks later commits without
creating EOS. Hashes detect corruption; they are not cryptographic authentication
of an untrusted database administrator. There is no sidecar checkpoint authority.

A persisted EOS intent precedes any native finish request. It can release an
unstable final-epoch core before terminal ownership. Acknowledgement waits for the
last output commit. If audio was already completely owned before EOS arrived,
including an alias-only final append, a separate zero-output transaction records
the actual native finish response. Restart reconstructs the last real epoch,
including exact 300-second boundary targeting; no dummy audio or empty next epoch
is invented. Uncertain calls discard the in-memory provider and replay in a fresh
instance. This is exactly-once committed publication, not exactly-once inference.

`NativeStatus.consumed_until` acknowledges actual PCM, while `activity_until`
bounds supported observations and may be quantized. A one-sample final tail can
therefore be fully owned without inventing activity. This is an injected contract;
the production native backend's quantized final-sample behavior is unverified.

## Explicit remaining limits

- Real duplicate matching remains a separate gate; these plans are synthetic
  already-validated inputs, including synthetic alias evidence.
- An earlier epoch of a final plan that cannot stabilize within bounded real
  lookahead would require an additional drain contract. The runner raises an
  explicit error instead of issuing a false epoch-seam EOS or waiting forever.
- No discontinuity drain, safe-cut/partial-core policy, persistent provider process
  adapter, public queue/service, support artifact store, candidate storage,
  identity integration, deletion migration or public query/change-feed integration.
- No production results or source receipts are written. Providers are injected
  in-process test contracts and must not publish their own side effects. Actual
  process lifecycle/resource integration requires its own adapter and review.
- Runtime error reporting is synchronous; no operational retry daemon is enabled.
  The ledger inspection `commits` helper materializes rows for small reviews and
  is not a bounded pagination API for long recordings.
- A 3600-second EOS target is covered by metadata/control tests; real synthetic
  PCM replay is exercised across a 300-second epoch. This is not a long-run or
  real-model endurance validation.

Focused evidence is retained separately in the local runtime report directory.
Earlier checkpoints with review defects remain immutable and are not acceptance
candidates. Tests and source checks must be reported against the exact final
commit; passing this internal test suite is not production admission.
