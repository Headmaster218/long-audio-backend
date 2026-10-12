# Native-owned selected cells: rc3.dev6 finite retention qualification

**Default OFF. One exact runtime A profile has a finite conditional SQLite/unix
VFS allocation and process-crash recovery qualification. UNQUALIFIED outside
this exact profile.** The registry binds the accepted evidence digest below;
the source activation still requires its separate enabled-retention check and
refrozen source review. No model, acoustic, service RSS, long-recording or
power-loss qualification is claimed. Explicitly enabling on an unqualified
runtime produces optional-unavailable status while the ordinary primary
pipeline continues. No extra model execution or provider is constructed.

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

The conditional disk envelope is an application/schema/transaction envelope for
the independently qualified pinned SQLite build, local unix VFS, journal
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

For every source change: freeze exact source bytes, complete independent privacy
and source closure review, admit the finite test/resource inventory, and run only
separately authorized model-free cases. The required cases cover geometry,
seams/epoch transforms, 48-hour integer coordinates, quota/hot-journal behavior,
one-slot pressure, source authority, deletion at unlocked boundaries, restart,
held pins and shutdown uncertainty. Historical test results do not qualify dev6.

Future genuine tests are separately gated: A is observable in-flight existing
slice cancellation/preemption with safe original-handle cleanup; B follows a
separately reviewed jobs adapter and covers preselected long, real upload-seam
and sparse epoch-boundary diagnostics. Neither is authorized by this document.

### Exact accepted runtime profile

The single accepted runtime record binds SQLite source ID and version, the
canonical compile-options digest, actual interpreter content and realpath,
sqlite3 Python/bytecode package closure, the explicit observed built-in _sqlite3
origin and interpreter container, and the no-separate-libsqlite3-mapping
observation. Separate extension/library artifact fields are null for this form,
not fabricated hashes. The record also binds Linux/unix VFS, filesystem device,
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

The independently accepted finite sequence filled the exact schema to 6,143
pages under its 6,144-page ceiling and observed SQLITE_FULL using conservative
schema-valid storage shapes. It retained a genuinely interrupted hot DELETE
journal and recovered a separate copy to the exact pretransaction database
bytes and complete logical oracle. The journal audit verified 3,067 records
and checksums, one header and a 512-byte sector. Fill, immutable crash original,
and recovery copy remain separate closed namespaces. These storage shapes are
not authentic producer evidence.

The record binds SQLite 3.53.1, its exact source/build and observed built-in
interpreter/package identity, the accepted runtime A realpath digest, filesystem
device 27, explicit unix VFS, and this terminal evidence SHA256:
`7097319b7e75d603dd0730033e29847b76631d71ae1bf8215f2052fc01377f55`.
The accepted journal bound retains the conservative 65,536-byte sector maximum,
one header and 2 MiB allocated slack. Accounting charges max(logical size,
allocated blocks times 512), including directories. Runtime relocation or a
changed package, build, device or namespace requires separate qualification.
The Q interpreter's equal content hash does not admit its different realpath.

This proof is one bounded process-crash case under the pinned application and
VFS assumptions. Synchronous FULL is configured; independent fsync completion
and power-loss durability were not established. Sampled memory is not a hard
RSS ceiling. It does not establish a platform-wide filesystem quota or hostile
same-UID namespace safety. Source activation changes only the registry tuple
and module status wording; all proof-bound store functions and schema remain
unchanged. The genuine enabled-retention selected case and refreshed 398-case
model-free denominator remain pending for this activation snapshot.

Most model-free storage fixtures inject a test-only RuntimeQualification subclass
through the private store constructor. SQLite source/build identity and pragma
readbacks remain real, while the injected binary and allocation identities are
explicitly SIMULATED. A separate fixture exercises the real binary verifier
and rejects mismatch before any disk-sidecar connection; passing simulated
fixtures cannot qualify allocation. Their zero-header journal case tests
accounting only. The separate selected AdmissionTests case uses the actual
production record and real verifier: runtime A must publish one tiny synthetic
cell, read it, close, restart and read the exact retained proof/PCM; runtime Q
must reject the A profile before any disk connection. Four real-verifier
mismatches cover interpreter realpath, SQLite source/build, package and device.

The implemented identity form is the explicitly observed Linux built-in
_sqlite3 module, bound to the exact interpreter content, size and realpath,
the running /proc/self/exe container, and a bounded sqlite3 Python/bytecode
closure. It requires no separately named libsqlite3 mapping. That absence is
an observation, not a proof of static linkage. No generic dynamic resolver or
fallback is provided. Individual artifacts have a 64 MiB streamed ceiling;
all identity reads share the 40 MiB/500 ms cooperative operation budget and
64 KiB transfer blocks. A runtime profile is admissible only after the exact
observed identity and separate finite allocation/VFS evidence are accepted.

Source-file opens walk at most 32 directory components descriptor-relative
from the filesystem root with O_DIRECTORY and O_NOFOLLOW, then open/stat the
basename relative to the bound parent descriptor. The final parent descriptor
and bounded ancestor device/inode identities remain attached to the read or
upload observation. A fresh no-follow chain comparison closes each unlocked
read/rename boundary; changed parents fail optional capture. Both retained
descriptors close together, including failure paths. No source-parent alias
is accepted merely because the final basename is a regular file.

SQLite uses a finite trusted private-namespace contract. Before the first disk
connection and around owner operations/recovery, a bounded no-follow ancestor
walk verifies persistent device/inode bindings and root-or-current-user owners;
group/other-writable ancestors are rejected (there is no sticky-directory
exception in this checkpoint). The data directory is current-user owned and
the cell namespace must remain private. Python SQLite still opens its absolute
filename non-atomically with these checks. Hostile concurrent same-UID namespace
mutation is outside this finite qualification; a post-open identity check does
not prevent a pre-open recovery race. No custom VFS or descriptor URI workaround
is claimed. Optional unregister exceptions retain ownership and their internal
diagnostic while primary worker/jobs shutdown still runs.
