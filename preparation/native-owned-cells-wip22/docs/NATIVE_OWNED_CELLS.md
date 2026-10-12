# Native-owned selected cells: rc3.dev6 source checkpoint

**UNQUALIFIED. Default OFF. No dev6 model, service, audio, model-free runtime, or
resource qualification is claimed by this source snapshot.** The production
SQLite/VFS qualification registry is empty. Explicitly enabling this feature on
an unqualified runtime produces optional-unavailable status while the ordinary
primary pipeline continues. No extra model execution or provider is constructed.

This checkpoint passively observes ordinary native request responses and PCM,
then partitions each successful actual safe-cut primary core into deterministic
half-open cells of at most 320,000 frames. Only positive simultaneous overlap
between distinct native epoch-local labels selects a cell. Native epochs remain
300 seconds, push/preroll remains 21.2 seconds, primary windows are unchanged,
and only an observed native finish establishes true EOF. Same-label duplicate
turns and touching endpoints do not select. Native original intervals and cell
intersections remain distinct. There is no speaker merge or lexical winner.

## Source continuity and ownership

Only fresh successful uploads observed while enabled can receive immutable
bounded ingestion receipts. The existing upload stream hash is bound to the
original descriptor, final rename identity, primary session/chunk, exact PCM16
mono 16 kHz RIFF geometry and file signature. Receipt-less historical uploads are
not backfilled. A duplicate can only reuse an independently matching durable
receipt. A crash or busy optional owner can leave a perfectly valid primary
upload ineligible for cells.

Cell mappings contain at most 128 exact contributing spans, never the whole
growing source plan. Original/native/analyzer PCM must match. Content identity
uses immutable digest/geometry/mapping; session and path authorization remain
separate. Original files and primary chunk rows retain their existing lifetime.
No retirement operation, regeneration execution, public cell route, jobs
adapter, scheduler or primary SQL schema change is introduced. Regeneration
eligibility is only an internal report while exact authorized source material
and mapping remain available. Expired PCM does not imply available originals.

The existing single-chunk native-owned-support API/cache and MOSS API, manager,
controller, permits and provider algorithms remain unchanged. A cell edge does
not release a whole-chunk primary permit or make a MOSS job schedulable.

## Finite optional component

The dedicated private namespace is `native-owned-cells/cells.sqlite3`. SQLite
uses DELETE rollback journaling, synchronous FULL, zero busy timeout, memory
temporaries and cache spill OFF. WAL/SHM are counted if present and fail closed.
No ATTACH, VACUUM, backup, disk temporary DB, conversion or uncertain-journal
removal is performed.

- Main DB: 4,096-byte pages, at most 6,144 pages (24 MiB)
- Rollback journal reservation: 32 MiB
- Allocated-file/control overhead reservation: 2 MiB
- Reserved envelope: 58 MiB, with 6 MiB below the 64 MiB total component ceiling
- At most 1,024 cell rows and 4,096 source receipts (each at most 1 KiB)
- Each cell: at most 640,000 PCM bytes and 64 KiB combined proof/dependencies
- One total active-or-pending owner handoff, shared between source receipts and
  publication batches of at most three cells, reserved at 2.25 MiB
- One 96-second native PCM witness ring (3,072,000 bytes), one bounded epoch
  summary and bounded turns; no growing response log or full-recording waveform
- Two copied read pins, reserving 768 KiB each; no SQL transaction spans a lease
- Read/list/detail projections: at most 20 rows and 64 KiB per page/detail
- Transfers/headers: at most 64 KiB; source-byte work at most 40 MiB per phase
- Cooperative prepare/commit and owner-operation budgets: 500 ms, lock attempts
  at most 5 ms, SQL at most 150,000 VM steps and 256 KiB projected material

Slow OS syscalls can exceed cooperative deadlines. Late optional results are
rejected; these numbers are not hard real-time guarantees. All sidecar fsync,
rollback and recovery runs on the one owner thread, never Worker/upload.
Primary work continues on optional busy, quota, proof or source failure.

The proposed disk envelope is an application/schema/transaction envelope for an
independently qualified pinned SQLite build, local unix VFS, journal
sector/header behavior and allocated-size method. It is not a universal
filesystem hard quota. Every new retained file remains charged, including hot
journals and allocated high-water after deletion. The 64 MiB optional working
reservation is explicitly **unmeasured**, not a hard RSS claim. Existing stricter
whole-service and host limits remain authoritative.

## Deletion, retention and recovery

Selection and complete proof precede retention. Invalid/unselected input cannot
evict anything. First-selected storage does not automatically evict earlier
cells. At byte capacity, bounded metadata-only mappings may fit; otherwise the
caller gets an explicit non-retention status. No unlimited omitted-cell ledger
or guarantee that all worst-case manifests fit is made.

Every publication/read uses current primary session and source authority, then
rechecks after unlocked I/O and before publication/use. Sidecar rows never
authorize access. Primary deletion closes publication generations, cancels pins
and schedules bounded reconciliation; tombstones/missing chunk rows also defeat
late publication after a crash. File replacement/removal makes sources
unavailable. Logical deletion is not forensic media erasure.

Startup examines an existing namespace, including feature-OFF cleanup mode,
under the existing instance ownership lock and after the MOSS fence. A
never-enabled absent feature creates no namespace, optional SQL or thread.
Shutdown closes admission and cancels handoffs/pins. If the original owner is
still unresolved after the bounded wait, its ownership and instance lock stay
referenced and shutdown is pending, never reported clean.

## Release gates

The implementation is restricted to the separately reviewed 18-path inventory
(9 additions, 9 changes), a fresh 340-file maximum source root, at most 4,600,000
aggregate source bytes and 250,000 bytes per file. Dev5 is preserved. Historical
dev5 source validation binds its published commit/tree and sole dev4 parent;
local historical HEAD is not a substitute.

Before qualification: freeze exact source bytes, complete independent privacy
and source closure review, admit the finite test/resource inventory, and run only
separately authorized model-free cases. The required cases cover geometry,
seams/epoch transforms, 48-hour integer coordinates, quota/hot-journal behavior,
one-slot pressure, source authority, deletion at unlocked boundaries, restart,
held pins and shutdown uncertainty. Historical test results do not qualify dev6.

Future genuine tests are separately gated: A is observable in-flight existing
slice cancellation/preemption with safe original-handle cleanup; B follows a
separately reviewed jobs adapter and covers preselected long, real upload-seam
and sparse epoch-boundary diagnostics. Neither is authorized by this document.

### Exact runtime profile still required

A future accepted runtime record must bind SQLite source ID and version, the
canonical compile-options digest, actual interpreter content and realpath,
sqlite3 package closure, the _sqlite3 extension, resolved loaded libsqlite3
content and realpath, Linux/unix VFS, the filesystem device,
maximum sector bytes, maximum rollback-journal header count, maximum allocated
slack (including the namespace directory), and the digest of the accepted
finite evidence. Current metadata persists that profile digest plus exact
primary, namespace and sidecar-file identities. Changed bindings fail closed.
Build and binary identity checks use a bounded in-memory SQLite connection
before opening any on-disk sidecar or recovering its journal. No CLI,
environment variable or HTTP request supplies qualification.

The bound to justify is 6,144 × (4,096 + 8) bytes of journal page records plus
sector_bytes_max × journal_headers_max, no more than 32 MiB. The accepted
allocated slack must fit 2 MiB and the component envelope 58 MiB. Cache spill is
OFF; neither cache_size nor journal_size_limit establishes this proof alone.

The separate qualification sequence must fill the exact schema to its physical
ceiling, exercise the largest admitted cell/proof/dependency transaction and
SQLITE_FULL, retain a genuine interrupted hot DELETE journal, and verify
recovery with readbacks and allocated-file accounting. Preserve three distinct
closed namespaces for the fill evidence, immutable crash DB+journal, and
recovery copy. This sequence is distinct from the focused model-free fixtures;
their injected profile is explicitly simulated and their zero-header journal
case tests accounting only. Neither passing those fixtures nor interpreter
identity can activate production retention. The registry remains empty until
actual evidence is independently accepted and the changed source is refrozen.

The first model-free fixtures inject a test-only RuntimeQualification subclass
through the private store constructor. SQLite source/build identity and pragma
readbacks remain real, while binary and allocation identities are explicitly
SIMULATED. They do not execute the production binary verifier. The current
production verifier supports only Linux with a distinct loaded `_sqlite3`
extension and exactly one mapped `libsqlite3.so` family, with each binary at most
64 MiB and all identity reads charged to the 40 MiB/500 ms cooperative budget.
Statically embedded SQLite or a larger interpreter is unavailable under this
profile, including an interpreter that otherwise passes the mechanics fixtures.
Supporting such a runtime needs an independently reviewed exact identity
binding and a new source freeze before enabled allocation qualification.
