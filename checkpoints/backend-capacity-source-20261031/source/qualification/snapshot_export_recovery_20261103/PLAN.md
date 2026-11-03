# Snapshot-only, same-job read recovery qualification

This is an explicit new read-only recovery experiment, not another native run and
not a restart of the original production service. The stopped run, partial export,
source files and immutable job identity remain unchanged. Any resulting artifact
is a projection of the closed database snapshot, not proof about a current remote
service or concurrent live system.

Do not use `create_app(start_worker=False)`: its construction/lifespan invokes
Store initialization, cleanup, recovery and other lifecycle actions independent of
the worker flag. Retained absolute source paths make that especially unsuitable.

## Source and read implementation

`snapshot_http.py` verifies the complete frozen source manifest, then extracts exact
AST nodes for five production GET endpoints and the native read-lease middleware.
It never invokes the app factory. The two passive Store methods are extracted as
unchanged methods on a read-only holder. Production RecordingOverlap read methods
are bound without constructing that class. Production transcript routes,
TranscriptStore and ReadResponseBudget are used unchanged. No fabricated HTTP
responses, forced success, prerecorded response substitution, inference, mutation
endpoint, model initialization or schema migration is involved.

The host is a qualification-only local HTTP projection. This assembly is not the
full production application's routing, authentication or lifecycle. The host must
bind only 127.0.0.1 at the exact port in the original immutable job origin, or stop
if that port is unavailable. Never rewrite the original job API or session IDs.
Its outermost middleware refuses every non-GET method before dispatch. Disable
OpenAPI/docs. Source identity and SQLite query-only/immutable restrictions fail
closed. Response leases use the exact production handoff and release middleware.

## State, guards and bounded launch requirements

Before launch, verify original shutdown predicates and absence of old processes.
Snapshot only the closed jobs database to a new private directory. Preserve the
old WAL/SHM/files untouched; require closed/checkpointed state before copying.
Verify complete original-tree bytes, lengths, mtimes and ctimes before and after.
Do not copy or migrate the native cell store merely to serve GETs. Keep original
absolute paths in snapshot rows; passive projection handlers never open media.

The SQLite guard confines all database connections to the one copied file using
`mode=ro&immutable=1`, enables query_only and installs a deny-by-default authorizer.
Production transcript readers use the same guard. Existing snapshots are never
mutated, repaired or initialized. Original job/source reads remain separately
protected by inventory and a write-denial audit guard in the outer supervisor.

Host limits must be frozen before execution: one CPU, finite request/body/output
budgets, <=384 MiB sampled process-tree RSS, <=120-second wall time and a normal
bounded stop with original Popen wait. No optional background jobs or native child
processes. The host should log request method/path/status without transcript text.
The exporter retains its own bounds, consistency checks and lease protections.

The recovery client must use the original session/capture/job and a fresh private
output directory. It must never reuse/delete the old partial output or rerun
admission/finalize/upload/session creation. A narrowly typed read-conflict retry
may perform only bounded GETs, with fresh uncommitted output and unchanged source
identity; it must not retry arbitrary409 or infer a retryable reason from a string
substring. That client is reviewed separately. All artifacts and completion
manifests must disclose snapshot provenance and be independently checked against
canonical rows and native packet/source receipts. This cannot turn the original
failed run into a successful original watch run.

This document/source does not authorize a launch. Independent review, source-only
backup, frozen plan/spec and authorized bounded recovery scope are required first.

## Reviewed host implementation limits

The CLI implements 120 seconds, one CPU/nice19, 384 MiB sampled RSS, no child
processes, 256 requests, 64 MiB response bodies, 8 MiB sampled host output and a
5.5 GiB free-disk floor. Query strings are ASCII and at most1,024 bytes; decoded
paths at most2,048 bytes. Trace rows retain method, bounded relative target including
query, and status only, so transcript cursor evidence is preserved without text.
Budget violations latch failure, request server stop, and cannot produce a passing
host receipt even if the ASGI server catches the request exception.

Protected roots must include the original run and frozen source tree; the reviewed
spec also includes the producer tree. All root/output separation checks occur
before creating output. The Python audit guard resolves event dir_fds and integer
FDs; explicit wrappers cover os.open's otherwise missing dir_fd audit field and
Python FD write APIs. Inherited writable protected FDs cause refusal. This is
cooperative-process defense in depth, not protection against hostile native code
or a replacement for a kernel sandbox.

The original uvicorn task is awaited. Timeout triggers bounded cancellation and a
failed/unconfirmed receipt, never a positive cleanup claim. Database/path guards
remain installed until process exit even after shutdown failure. The parent must
kill and positively wait for an unresponsive host. Active requests, outstanding
response leases, traffic failure, changed originals/snapshot or unconfirmed task
completion prevent a passing receipt. Parent separately verifies original trees
and the full frozen source manifest after the original child wait.

## Recovery wrapper and clients

Place qualification files together at
`candidate/qualification/snapshot_export_recovery_20261103/`. Both host and clients
use the same explicitly hashed host specification and complete final source
manifest. The wrapper and client refuse execution from any other source location.
Clients install original/source/snapshot mutation guards before importing product
code and obtain API/session/capture/job identity only from that verified spec.

The outer wrapper has a 140-second active-work deadline, 512 MiB sampled aggregate
RSS ceiling, 256 MiB sampled output ceiling and 5.5 GiB free-disk floor. It selects
one CPU and nice19 before spawning, inherited by both 90-second recovery clients.
Host-specific 120-second/384 MiB limits remain unchanged. Cleanup adds bounded
TERM/KILL waits after the work deadline. Original Popen objects are positively
waited; observed descendants are identified by PID plus creation time, cleaned up
on failure, and any descendant surviving original waits prevents acceptance.
The parent must still enforce an outer termination-and-wait backstop.

Acceptance checks exact recovery/export schemas, a fresh attempt directory,
144,000 frames, nonempty primary text, artifact roster/hashes, JSON unit counts,
and byte-identical repeat artifacts. Independent canonical snapshot/native-receipt
comparison remains required separately; wrapper success alone is not that audit.
Snapshot provenance is recorded in the wrapper RESULT alongside unchanged exporter
artifacts. It must accompany them; exporter complete.json is not relabeled as live
production evidence.

`test_qualify_recovery.py` uses only fabricated temporary files and mocks, with no
sockets or original evidence. Retained-evidence ASGI tests in test_snapshot_http.py
require explicit `AUDIO_RECOVERY_EVIDENCE_ROOT` pointing to the workspace; absent
that variable, those tests skip rather than guessing a path from final placement.

`launch_recovery.py` is the outer owner: it verifies the same source/spec bindings,
installs protected-root guards, and invokes `/usr/bin/timeout --signal=TERM
--kill-after=15s 160s` on the native interpreter with `-I -B`. No child creates a
new session, so timeout's group contains the wrapper, host and clients. The launch
owner positively waits for the original timeout Popen, records monotonic duration,
child CPU time and sampled RSS, and independently inventories all protected roots
plus the snapshot after wait, including hard-timeout outcomes. It does not declare
success if descendants survive the original wait or require cleanup. A 180-second
outer-owner fallback kills the verified timeout process group and still waits.

The outer owner independently enforces the same 512 MiB sampled total-family RSS
(including itself), 5.5 GiB free-disk floor and 256 MiB output cap. A resource-check
failure immediately kills the timeout-owned group, then follows the same positive
wait and post-wait inventory path; a stalled inner wrapper cannot disable these
outer sampled limits.
