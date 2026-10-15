# Finalized recording overlap batches, candidate v2

Status: receipt-outbox source checkpoint with partial integration results.
Historical tests passed 35 focused cases and 12 original regressions. The actual
API case preserved the 21-second union and made both source receipts durable,
but optional capture logged SQLITE_BUSY_RECOVERY (261), so no MOSS was dispatched
and intended full integration failed. This snapshot contains no later retry fix.
The primary-database outbox adds genuine observation freezing and owner-thread
exact-row reconciliation; optional schema, pragmas and budgets stay unchanged.

## Purpose and boundaries

This optional API accepts all overlapping source recordings before any work is
queued. It removes only PCM samples proven byte-equal at explicitly declared
shared sample coordinates. Original encoded WAV bytes and their individual
receipts remain retained, including equal duplicates, unknown placements, and
conflicting audio. It does not compare words, match waveforms approximately,
infer a microphone identity, align by wall clock, trim silence, normalize,
resample, downmix, replace channels, or pass work to the experimental fragment
backend. The existing strict PCM inspector is reused only as a WAV validation
utility. Actual processing uses ordinary legacy chunks, the existing Worker,
existing native capture hooks, and existing proof-gated MOSS routing.

This is a finite finalized batch, not continuous capture or a 12–48-hour buffer.
One recording capture reserves one fresh empty legacy session. Existing sessions
that never opt into recordings retain the normal non-overlap upload API. Once
reserved, ordinary chunk insertion into that session is rejected. A new domain
requires another fresh session. No source-specific erasure, undo, revocation,
late insertion, placement correction, rebuilding incomplete finalization, or
source-file download route is claimed in this slice. Whole-session deletion
retains its existing supported lifecycle.

## Exact API

1. Create an ordinary session using POST /sessions (processing_mode legacy).
2. PUT /sessions/{sid}/recordings/{capture_id}/sources with Idempotency-Key and
   an original PCM16 little-endian, 16,000 Hz, mono WAV as the raw HTTP body.
   The optional source_start_frame query is an exact nonnegative integer in the
   capture coordinate domain. Omission is retained as unknown placement.
3. GET /sessions/{sid}/recordings/{capture_id} to inspect source receipts and the
   current revision.
4. POST /sessions/{sid}/recordings/{capture_id}/finish with a strict JSON body:
   {"expected_revision": 2, "idempotency_key": "finish-request-1"}.
5. If needed, POST /sessions/{sid}/recordings/{capture_id}/cancel while the
   batch is pending_receipts. It retains originals, maps and payloads, marks
   staged chunks cancelled, and never reopens or queues that batch.

capture_id contains 1–128 ASCII letters/digits/underscore/dot/hyphen. Supplying
one capture_id explicitly declares one shared PCM sample-coordinate domain; it
is not merely a human name for several microphones. Separate microphone domains
must use separate capture IDs and sessions. Their audio is never compared or
aliased, even when their timestamps or bytes happen to match. If placement is
unknown, omit source_start_frame; the server does not guess. There is no
recorded_at alignment parameter.

Source Idempotency-Key is scoped to the capture and binds original encoded-byte
SHA-256, format and declared placement. A repeated identical receipt returns its
original source ID without increasing revision. Changed bytes or placement with
the same key are rejected. Finish idempotency binds the exact source revision,
policy and fixed timeline placement; replay returns the original work IDs and
current status, including after restart, without creating more chunks.

The first valid finish request atomically freezes the entire accepted source
snapshot before file reads/planning/copying. No later source is accepted, even
when the result is unresolved or interrupted. Exact duplicate receipt retries
remain allowed. A stale revision does not freeze the batch. To reset a frozen
batch in this first slice, delete its session and start a fresh session; export
or otherwise retain originals independently before requesting deletion.

PUT/finish return the batch summary; PUT adds source_id and duplicate.
GET returns private/no-store JSON. The summary has:
- capture_id, coordinate_domain, format, policy, revision, state
- capture_origin_frame, capture_end_frame, stream_id
- sources: id, seq, idempotency_key, digest, frames, bytes,
  source_start_frame, format, created
- work_chunks: chunk_id, position, frames, bytes, capture_start_frame,
  native_receipt_status, receipt_state, receipt_id, receipt_attempts,
  status, digest, timeline_start, duration
- ranges: source_id, chunk_id, source_start_frame, capture_start_frame,
  work_start_frame, frames, relation (owner or exact_alias)
- optional_receipts: pending, durable, gaps, ready
- blockers and limits

Paths and raw receipt payloads are never returned by these endpoints. GET uses
one read transaction, so batch/work/count fields describe one coherent snapshot.
receipt_state is pending, durable, gap or cancelled. receipt_id is the immutable
SHA-256 of the actually observed canonical receipt, or null where observation was
unavailable. receipt_attempts is a saturating diagnostic submission count, not a
claim of repeated inference. optional_receipts.ready requires every work receipt
to be durable; gaps excludes intentionally disabled capture. No such flag proves
that native cells or MOSS jobs exist: their existing proof-gated APIs remain
separately authoritative. queued means ordinary work is published; individual
chunk status shows queued/running/completed/etc. A queued batch may have explicit
optional_receipt_gap blockers. pending_receipts work is always nonclaimable
staging, and finish replay returns the same work/map/receipt IDs and current state.

## Coordinates, overlap and output

Sources are sorted by integer source_start_frame, then durable receipt order.
For each interval, every overlapped sample is compared byte-for-byte against the
already selected owner ranges, in at most 32,768-frame / 65,536-byte blocks.
An equal prefix, suffix or fully contained source maps back to the same retained
work interval as an exact_alias. A late prefix is sorted correctly because
nothing is queued before finish. Different encoded WAV headers do not by
themselves imply different PCM; encoded-byte digests remain in original receipts.

Any unknown placement, gap, differing PCM sample, missing/changed original, or
capture-duration capacity failure leaves all sources inspectable, the batch
unresolved, no aliases published and zero ASR work. No conflicting source is
silently dropped in favor of an owner. All metadata in these unresolved receipts
remains bound to its original source and coordinate declaration.

A successful union has one immutable persisted capture origin: the minimum
source coordinate. That origin maps to session timeline frame zero. The union
is streamed directly into ordinary canonical WAV pieces of 168,000 frames
(10.5 seconds), with only the final piece allowed to be shorter. The internal
stream is recording.{batch_id}; all pieces are adjacent, finality is explicit,
and the normal stream/chunk registration semantics are used. The source bytes
are never collected into one all-capture array or giant joined file.

Each original frame has a complete durable source-local → capture → work-local
range map, including aliases. The original source receipt is not relabeled as a
derived source. Derived chunks carry their own hashes and ordinary receipt IDs;
existing native-cell proof lineage therefore terminates at those actual derived
work files. The separate durable map carries the original recording provenance.

## Finite admission

The stricter of the following defaults and applicable existing settings wins:
- 16 original sources per batch
- 120 seconds / 1,920,000 frames per source
- Per-source encoded bytes: 3,905,536, also bounded by max_upload_bytes
- 300 seconds / 4,800,000 unique capture frames, also max_audio_seconds
- At most 32 pieces; 300 seconds normally requires at most 29 pieces
- 64 retained batches globally; one per session
- 32 MiB per-batch copy budget and 128 MiB global recording copy budget
- Fixed 512 KiB per-batch metadata projection reservation, including at most
  32 canonical receipt BLOBs of at most 1,024 bytes each
- The configured filesystem disk_reserve_bytes and existing process/host checks

Copy accounting includes allocated bytes (at least st_size, or st_blocks*512
when larger) of all feature-owned originals, staged files, derived work, orphan
copies and failed-cleanup remnants. It adds metadata reservations and next-write
allocation rounding. Existing unrelated ordinary upload files are excluded from
this feature's quota but still affect the normal filesystem free-space floor.
HTTP admission/freeze/staging uses the existing single upload/finalization
lock. Async publication and recording cancellation share one nonblocking
maintenance lock and primary BEGIN IMMEDIATE transaction authority. Capacity is checked before accepting bytes,
before each bounded output piece and again before the database stage commit.
The source/map/manifest projection has an additional finite size/count check
before materialization. Metadata reservation is a bounded logical projection;
it is not a claim about total primary SQLite/WAL allocated bytes, which also
contain unrelated legacy data and remain subject to the disk floor.

Range comparison memory, hash buffers and copying use fixed blocks. Sources,
owner spans, map rows and pieces are explicitly count-bounded. A directory scan
for physical quota uses constant-memory iteration rather than collecting paths.
No huge fixture is required to exercise the admission boundaries.

## Transaction, crash and lifecycle behavior

1. Source files are fsynced, renamed into owned uploads/*.audio, and the directory
   is fsynced before their receipt transaction commits. An ordinary exception
   before acceptance removes only the unaccepted copy. A crash-orphan copy has no
   receipt and is removed by existing startup cleanup.
2. Finish first commits preparing, the request key/revision and the immutable
   original snapshot. This is the acceptance fence before comparisons or I/O.
3. Original hashes/geometry are validated, exact overlaps checked, and every
   bounded derived WAV is written/fsynced. Its actual open .part descriptor is
   passed through streamed_descriptor and prepare_upload when native capture is
   available. No backfilled observation or simulated production receipt is used.
4. Inside one primary transaction, actual staged chunk IDs are allocated using
   the existing chunk registration hook. observe_recording_receipt consumes each
   genuine open-descriptor token, verifies rename continuity and freezes its
   canonical Receipt. It does not pretend the uncommitted chunk is already
   primary authority. All nonclaimable staging chunks, complete maps, exact file
   signatures, source/work manifest and canonical receipt BLOBs commit together
   as pending_receipts. Open tokens then close; no observation is fabricated on
   restart. Missing observation is an explicit optional gap.
5. The existing Worker.loop performs at most one receipt admission or retirement
   per maintenance tick, with a one-second retry floor and no blocking wait,
   background thread, new service, model call or unbounded queue. A currently
   occupied owner slot leaves the exact payload pending. A racing refusal also
   leaves it intact. Submitted operations are retained until slot_released,
   including the last operation before primary release. Unrelated ordinary
   sessions remain claimable while the recording batch is pending.
6. The existing owner validates current committed primary/path authority before
   receipt acceptance. A source SQL commit followed by failure before cache/ack
   is reconciled by exact CID, length-bounded canonical bytes, current authority
   and generation on the owner thread. Matching replay is idempotent; conflict
   is rejected. Recording maintenance never opens the optional database.
7. Acknowledgements require the exact currently retained owner receipt and a
   stable owner generation around primary authority validation. Busy, deadline,
   uncertain I/O and temporary owner shutdown/unavailability remain retryable
   pending, even when the whole batch takes longer than two seconds. The
   existing per-operation two-second budget is unchanged and never enlarged.
   A definite source_receipt_capacity refusal is a terminal optional gap with
   the original observed payload retained. Disabled/no-observation gaps are
   also explicit; none of these gaps invent native proof or block primary.
8. Once every observed receipt is durable or an explicit terminal gap, current
   owner receipt/generation, the complete source/work/map/stream manifest and
   original/derived file identities and full hashes are checked before one
   atomic staging-to-queued batch flip. File/map/source authority corruption
   interrupts primary publication rather than silently becoming optional loss.

A crash in preparing or old incomplete staging becomes interrupted on restart,
with zero newly queued work and no reconstructed observations. Only an atomically
complete pending_receipts manifest resumes: recovery changes previously durable
acknowledgements back to pending for current-owner reattestation of the exact
persisted payload. Derived staged files survive via ordinary chunk ownership;
originals survive via recording_sources and the startup path guard. Unreferenced
incomplete derived .part/.audio files remain subject to existing orphan cleanup.
A pending batch with a persistently unavailable owner remains visibly pending,
not falsely ready, until owner recovery or recording cancellation/session deletion.
This can delay this batch's ASR; it does not stall unrelated primary sessions.
A frozen incomplete batch cannot reopen by finish retry, chunk resume or upload.

A crash after the final transaction leaves the same queued work IDs. Existing
worker recovery may replay an in-flight inference call; this feature does not
promise exactly-once model invocation. It promises no duplicate batch/work IDs
or already-committed ASR rows from a finish/source replay. Normal cancel/pause/
retry operates on published ordinary chunks, and the existing stream semantics
handle their descendants. Staging is not a public resumable chunk state. The recording-specific pending
cancel route changes batch/chunks/outbox state atomically. An already accepted
receipt may retire afterward, but no late acknowledgement can queue cancelled
or deleted work. Session deletion revokes current native authority as before.

Whole-session deletion inserts cleanup intents for originals as well as ordinary
work files before logically erasing all batch/source/work/range rows. Existing
native-cell, support and MOSS deletion notifications still apply. Failed unlink
intents survive and are retried by the existing lifecycle cleanup. Unrelated
sessions and streams are not touched. Retained paths stay within the same owned
uploads/*.audio namespace; cleanup authority is not broadened.

## Test coverage and execution boundary

The focused original-style pytest source has 35 cases including parameter
expansions. Existing overlap/original retention/API/Worker cases remain, and the
new real observation/authority/owner fixtures cover two receipts whose total
processing exceeds two seconds; initial busy and a competing admission refusal;
a real unchanged owner deadline followed by retry; complete staged process death;
shutdown/restart with previous durable acknowledgement revalidation; SQL commit
before acknowledgement; pending cancel/delete/revoke; unrelated Worker claim
progress while a receipt is genuinely pending; explicit terminal capacity gap;
conflicting durable receipt; generation revocation during acknowledgement and
owner reconciliation; and contained reporting failure. Threads and child exit
are positively waited. Native qualification is explicitly simulated only in
model-free fixtures; descriptor, primary storage and owner mechanisms are real.

Synthetic fixtures use short PCM (largest unique fixture 336,001 frames, under
0.65 MiB). Quota negatives lower thresholds. The completed model-free run passed
35 focused cases in 9.82 seconds and 12 original regressions in 4.06 seconds,
using separate 32 MiB and 16 MiB output leaves, respectively. Both reported no
remaining child processes or threads. Separate per-process RSS maxima do not
establish aggregate peak memory. No model loading or download occurred in those
tests. This public packet was only statically parsed and integrity-checked; its
templates were not executed.

## Established mechanisms consulted

- Python 3.12's standard wave interface supplies frame seeking, bounded
  readframes, writeframesraw and final header correction:
  https://docs.python.org/3.12/library/wave.html
- GStreamer's stream design distinguishes source/stream time, running time and
  EOS. This candidate similarly keeps original capture coordinates separate
  from its explicitly finalized internal work timeline:
  https://gstreamer.freedesktop.org/documentation/additional/design/streams.html
- Whisper-Streaming addresses stable transcript commitment and bounded context;
  those mechanisms do not establish equality of two original audio recordings.
  This candidate keeps the existing lexical tail/commit behavior unchanged and
  performs exact PCM overlap checks independently:
  https://github.com/ufal/whisper_streaming

- The established transactional-outbox pattern motivates committing primary work
  and delivery payload together, with exact idempotent replay after uncertainty:
  https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html
- SQLite separate connections see only committed writes, hence descriptor
  observation is frozen inside the primary transaction while owner authority
  validation happens only after that transaction commits:
  https://www.sqlite.org/isolation.html
