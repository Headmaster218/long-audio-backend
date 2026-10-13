# Optional two-commit seam alternatives

Status: SOURCE-ONLY UNQUALIFIED. The implementation is a bounded, default-OFF
extension to native-owned cells. This document is a source contract, not a
source seal, test report, SQLite qualification, genuine-overlap observation,
acoustic benchmark, service run, or inference authorization. Fresh independent
source/model-free and finite exact-runtime SQLite-policy acceptance is required
before any separately authorized materialization or actual run. Existing dev7
successes do not establish dev8 acceptance.

## Opt-in and compatibility

`Settings.native_owned_seam_cells_enabled` defaults to `False`, accepts only a
boolean, and requires `native_owned_cells_enabled=True`. The corresponding CLI
option is `--native-owned-seam-cells`, requiring `--native-owned-cells`. Existing
legacy-worker and qualified-runtime admission remains mandatory. No model,
service, native request, job, extra thread, process, or queue is enabled by this
flag alone.

The flag controls new composite production only. Ordinary v1 cell construction,
canonical bytes, identities, ownership, policy and request/response meanings
remain unchanged. An admitted composite-capable store with seam production OFF
may retain and read v1/v2 cells and accept separately explicit v2 diagnostic
requests when the existing cells/jobs flags allow. With the entire cells feature
OFF, public feature-disabled behavior and bounded owner cleanup remain in force.

Store profiles are explicit and immutable:

- Legacy profile: the original v1 cell policy hash. Seam opt-in does not migrate
  it or change `store_meta`; composite publication declines with
  `incompatible_legacy_cell_policy` while ordinary v1 behavior is preserved.
- Composite profile: the hash of canonical
  `{"schema":"native-owned-cell-store-policy-v2","accepted_cell_policies":sorted([P1,P2])}`.
  It accepts only the exact known v1/v2 proof kinds with unchanged runtime,
  allocation and qualification identity checks.
- Unknown profiles reject. No implicit namespace rebind or policy rewrite.

P1 is `8385e071e9700dced5d766c80f7c41a676481a796b74a06848204f561d1ea0b5`.
P2 is `b8480ec44a75c456a6e036cfa8d28d2b03829e2769f438178f4b29b14f2425b1`.
The composite store profile hash is
`fece6df7a046b4ac0b94e608e002f580f5df8a42c2f90ccd17f6a950b1184e64`.

## Exactly two commits and two source receipts

For adjacent original primary cores A=[a,s), B=[s,b), with rate 16000:

- left=min(160000,s-a), right=min(160000,b-s)
- selected candidate=[s-left,s+right)
- both sides are positive; the two-core envelope b-a is at most 960000 frames
- A is the preceding upload's terminal committed core; B is its contiguous,
  explicitly registered same-stream successor's first committed core
- both original `Store.commit_window` returns must be True, both rows must be
  completed, and both current `next_offset` values must exactly match their
  captured terminal local end and source duration

There is no padding, third-core absorption, historic commitment recovery,
general stitcher or primary re-execution. Each contribution has its own current
immutable ingestion receipt and clipped relative-source/logical mapping. The
current original primary metadata for A and B must still match the two separate
native states and actual core-cut values. Revalidate both commitments and both
sources at publication and every later list/detail/dispatch/result/raw-source
access; either source deletion, replacement, receipt loss, tombstone or
commitment drift makes the composite unavailable.

The production candidate policy chooses positive half-open overlap between two
different native epoch-local labels in one observed response. It enumerates
(witness index, first turn index, second turn index) lexicographically and keeps
the first qualifying pair. Same-label duplicates, touching endpoints and overlap
obtained only by unioning separate responses cannot select a cell. Turns retain
their original observed query-clipped intervals and order; there is no label
merging, cross-epoch identity or global-unclipped claim. The separate actual gate
would additionally require a same-response distinct-label overlap straddling s;
that stronger observation is not claimed by this source change.

## Scope and observable continuity

Both original observations require the same session, explicit stream and origin,
native epoch ID/index, validated native runtime provenance and monotonically
observed reset generation. Capture increments its reset generation before
validating every observed state/provenance reset. Invalid or overflowing resets
disable capture. Reusing an epoch ID after a reset cannot reuse a tail. This
serial survives resets of the rolling witness's internal action count.

Capture sees synchronous chunk/state/provenance callbacks, not native process
objects. Its continuity claim relies on the unchanged Worker/NativeCoordinator
reset-before-input bracketing for each new original native process/epoch. Direct
Popen/original-process identity belongs to the separate qualification harness;
missed callbacks, hostile producers or replacement paths are outside this
source-invariant claim. No extra query or native process hook is introduced.

A retained reset serial records historical continuity only; it is not live read
authority after restart. Retained cells revalidate current sources and commits.
Restart neither reconstructs a tail nor requires an in-memory old serial.

## Closed v2 proof and the B publication anchor

The proof schema is `native-owned-seam-composite-v2`. Its exact top-level keys
are schema, policy, scope, geometry, support, dependencies, commits,
native_witnesses, selection, publication_anchor, selected and coverage. All
nested objects have closed key sets. Reject missing/extra keys, wrong array
lengths, bool-as-integer, non-finite values, noncanonical hashes and malformed
identifiers. Integers are in [0,2^53-1], hashes are lowercase 64-hex and epoch IDs
lowercase 32-hex. Identifiers retain the existing 128-UTF-8-byte/no-NUL rule.
Canonical JSON is sorted-key, ensure_ascii=True, compact separators and
allow_nan=False. Exact proof bytes must equal the canonical parsed object and
fit in 65536 bytes including all nested material. Aggregate turns across the
two native witnesses are at most 256.

The support uses the unchanged `SourceSupport` shape, but its
`core_start_frame=a` and `core_end_frame=b` explicitly describe the
`two_committed_core_envelope`. They are not a single actual primary core or a
third synthetic commitment. `source_frames=b` is a bounded frontier, not the
recording's total length. The selected cell range is the candidate, at most
320000 frames. Existing 20-second and sample/hash/mapping checks still apply.

The commits array preserves exactly two ordered original A/B commitments. Each
binds chunk ID, source-origin frame, local and logical half-open bounds, original
native prepared state, original `core_cut`, and canonical `evidence_sha256`.
B's local start is zero; A's logical end equals B's logical start. Preparation is
provisional; an arbitrary claimed boolean is never a substitute for the
producer's actual successful commit observation.

Each of two native witnesses preserves its commit index, query and analyzer
intervals, full analyzer PCM digest, piece PCM digest, observed native state,
validated provenance, action count, actual-finish EOS observation and bounded
original turns. Capture compares each contributed original-receipt PCM piece
with both the native-ring piece and actual analyzer piece. Concatenated exact
pieces determine PCM16 and float32 identities. Hashes alone are not attestation;
independent actual reconstruction is a separate gate.

`publication_anchor` is exactly B's original `{chunk_id,start,end,sha256}`
commitment. Internal `Cell.chunk_id`, commit start/end/hash and `cells.cid` keep
that original B anchor. It is separate from the two-core support envelope and
composition identity. V2 validators must distinguish these roles explicitly;
v1 single-core checks remain intact.

## Evidence identities and stable coordinate ownership

Let C denote the unchanged canonical cell JSON encoding and H(x)=SHA256(C(x)).
Let `contents` be the unchanged content fields of the two ordered SourceSpans.

- source_generation=H({schema:'immutable-source-cell-generation-v1',sources:contents})
- plan_sha256=H({schema:'bounded-cell-mapping-v1',parts:contents})
- native_runtime_sha256=H(the common validated native provenance)
- core_sha256=H({schema:'native-owned-seam-composition-v2',scope,geometry,commits,native_witnesses})
- cell_id=H({policy:P2,support:support.to_dict()})
- support_id retains the unchanged SourceSupport algorithm
- proof_sha256 is SHA256 of the exact complete canonical proof bytes

The stable committed ownership key is:

H({schema:'two-commit-seam-ownership-v2',session_id,stream_id,epoch_id,
   commits:[[A.chunk_id,A.local_start,A.local_end],
            [B.chunk_id,B.local_start,B.local_end]],geometry,policy:P2})

Ownership deliberately excludes `evidence_sha256` and all other evidence-derived
values. Unchanged replay resolves the same ownership and cell identity, creating
no second ownership row. Altered evidence at the same ordered coordinates and
geometry still resolves the same ownership; existing
`conflicting_committed_ownership` and proof validation must reject it. Changed
core/cell/proof evidence identity cannot allocate around a coordinate conflict.

## Overlapping diagnostic API meaning

Every v2 list/detail/token/job binding/summary/result carries the exact
proof-derived coverage object with these fields:

- kind='seam_overlap_alternative'
- scope_sha256: canonical hash of the native-audio-coverage-scope-v1 object
  binding session, stream, origin_seconds_hex, epoch ID/index and runtime hash
- frame_range=[candidate start,candidate end]
- source_support_core_semantics='two_committed_core_envelope'
- coverage_envelope_frames=[a,b]
- primary_commit_refs: exactly the two original A/B `{chunk_id,start,end,sha256}`
  anchors, in that order
- adds_primary_audio=false; automatic_dispatch=false

Reset generation is a continuity witness, not a new recording coordinate domain.
Compare intervals only within the same verified coverage scope. Relate a v2
alternative to v1 evidence through the original primary commit references.
Never assume independent uploads share coordinates, sum v1+v2 durations, append
both diagnostic transcripts as additional primary speech, or map diagnostic
speaker labels into primary identities. Raw result bytes remain unchanged;
the enclosing binding carries coverage meaning.

A v2 POST uses the existing exact six request keys plus
`coverage_intent='overlapping_diagnostic'`, still within 1024 bytes. The parser
accepts only the old exact shape or the v2 extended exact shape. The adapter
rejects missing/wrong v2 intent and rejects a v2-only intent on a v1 target.
Rejected requests accept zero jobs and call no provider. If validation follows
pin acquisition, existing positive pin cleanup is required; a rejection does
not promise that no pin was acquired.

The unchanged manager hashes the complete request with origin/session/target.
An exact key/request/jobs-epoch repeat returns the same job without reacquisition
or redispatch. Changed binding/intent or a v1 target on that key conflicts.
Different explicitly requested keys may represent different diagnostics.
Volatile jobs/keys vanish on restart; durable retention is not durable
exactly-once inference. No scheduler, automatic dispatch or regeneration exists.

## Tail, working reservations and cleanup

At most one capture-owned pending tail is retained, capped at 768 KiB including
at most 320000 PCM bytes plus its bounded evidence. A.prepare creates only a
provisional tail; A.committed can promote it after the actual successful primary
commit, even if no ordinary A cell was selected or retained. B.prepare consumes
only the exact adjacent tail and observed prefix. B.committed validates both
completed authoritative rows before using the existing one-slot owner handoff.

The additional assembler working reservation is 4 MiB above the existing
64 MiB optional reservation. This covers the current candidate and transient
assembly; those lifetimes must be charged separately from the pending tail and
existing handoff. Check sums before allocation and decline oversized valid
responses rather than widening caps. These are payload/accounting reservations,
not a measured Python object-memory bound or hard RSS guarantee.

Ordinary cells retain precedence. At most one composite joins them inside the
unchanged three-cell batch and 2359296-byte handoff. A full ordinary batch
causes composite decline, with no displacement or fourth cell. Busy/unavailable
publication has no retry queue or automatic primary re-execution. Once accepted,
the existing publish operation/store/pin lifecycle owns full composite PCM;
the capture releases its tail reference.

Scope/epoch/reset discontinuity, gap, unexpected core, false/unknown commit,
interrupted preparation, capacity failure, revocation and shutdown invalidate
the tail. Check generation before and after unlocked work and before handoff.
If no immediate existing hook can clean up interrupted work, unusable material
remains charged until the next hook/close and cannot be replaced by a second
slot. Delete/close invalidates tail generation and owner admission. Positive
owner/pin cleanup acknowledgements remain required before resource reuse.

## Preserved storage envelope and source acceptance

The four-table schema/indexes, SQL write strings, qualified runtime tuple/build
identity, pragmas, zero busy timeout, write transaction/recovery boundaries,
6144-page limit and 4096-byte page size remain unchanged. The database family
reserves 24 MiB DB + 32 MiB journal + 2 MiB overhead = 58 MiB within 64 MiB.
Other limits remain 1024 cells, 4096 receipts, 128 source parts, 640000 PCM bytes,
65536 combined proof/dependencies bytes, two copied pins at 768 KiB, 500 ms
operation/5 ms lock/150000 SQL steps/256 KiB projection/40 MiB source reads and
64 KiB transfers. The finite seam profile would retain at most three cells and
two receipts; it is not a new fill/crash or long-recording claim.

`CellStore._open` has an explicitly reviewed known-policy admission exception;
there is no blanket assertion that its old AST is unchanged. `_schema`,
`_transaction`, `_receipt`, `_publish`, `_insert_cell`, `_expire` and `_sweep`
retain their exact source bytes, writes and limits. Changed parsing, source
validation and admissible row semantics need new independent review and finite
real-SQLite/model-free cases. Reopen qualification if the frozen write closure,
pragmas, maxima or transaction order changes.

The release slice is exactly 20 paths: five added, 15 changed, no deleted files,
351 total source files, at most 5 MiB total and 250000 bytes per file. Source plus
Git is capped at 12 MiB subject to fresh measured admission; preparation metadata
16 MiB and test artifacts 256 MiB are independent ceilings. The dev7 historical
adapter changes only its docstring/current snapshot reader and pins published
commit ebc7a02fb4420c0089f2a146ec6bf9dcc23137b4, tree
41a091d97438238c88491a4c543002b47e647c72 and sole parent
9e0a135bda2c37641fb0a641655835a0c6a92feb, retaining 346 files/4487300 bytes.
Earlier source gates/manifests and all prior test IDs, including the historical
458-case selection, stay preserved. New tests require a fresh exact discovered
inventory before execution; no new passing denominator is asserted here.

Independent preparation must produce fresh source freeze, external index/seal,
handoff, test plan, storage parity, source review and resource admission records.
These are separate evidence held outside the product source. Pending hashes or
review fields are not acceptance. Tests, native/product imports, audio,
services and inference are distinct gated actions; this document authorizes none.
