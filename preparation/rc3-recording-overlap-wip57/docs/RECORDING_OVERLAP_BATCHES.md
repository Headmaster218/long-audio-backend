# Finalized recording overlap batches, candidate v1

Status: finalized-batch primary/deduplication milestone. Historical verification
passed 20 focused and 12 original tests plus one real API case. Optional receipt
sequencing remains unresolved; see the checkpoint README for exact outcomes.
The native-owned-cell store/capture/jobs dependencies remain pinned and unchanged.
This source packet includes no model, audio, retained database or runtime.

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
late insertion, placement correction, resumable interrupted finalization, or
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
  native_receipt_status, status, digest, timeline_start, duration
- ranges: source_id, chunk_id, source_start_frame, capture_start_frame,
  work_start_frame, frames, relation (owner or exact_alias)
- blockers and limits

Paths are never returned by these endpoints. Native receipt status is an
admission-time snapshot (including receipt_pending), not a claim that a durable
cell proof or MOSS job exists. Actual cell/MOSS read APIs remain authoritative.
The batch state queued means ordinary work was published; the individual chunk
status fields show whether processing is queued/running/completed/etc.

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
- Fixed 512 KiB per-batch metadata projection reservation
- The configured filesystem disk_reserve_bytes and existing process/host checks

Copy accounting includes allocated bytes (at least st_size, or st_blocks*512
when larger) of all feature-owned originals, staged files, derived work, orphan
copies and failed-cleanup remnants. It adds metadata reservations and next-write
allocation rounding. Existing unrelated ordinary upload files are excluded from
this feature's quota but still affect the normal filesystem free-space floor.
All admission, freeze, staging and publication occurs under the existing one
active upload/finalization lock. Capacity is checked before accepting bytes,
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
4. All ordinary chunk rows are committed together as nonclaimable staging,
   alongside complete source/work mappings and the frozen manifest. Native
   finish_upload receives the valid original token, renamed file and real stored
   chunk. Optional refusal/pending is retained without replacing primary ASR.
5. Before queueing, the stored manifest, entire expected work-ID/count/state set,
   source-to-work map, explicit stream geometry and each derived file's identity
   and full digest are rechecked. A single transaction changes the complete
   batch from staging to queued. A failure keeps it nonclaimable/interrupted.

A crash in preparing or staging becomes interrupted on restart, with zero
newly queued work and no automatic reconstruction of upload observations. Files
already referenced by ordinary staging rows survive startup. Original files are
protected by recording_sources lookup and the startup path relocation guard.
Unreferenced incomplete derived .part/.audio files are safely orphan-cleaned.
A frozen failed batch cannot be reopened by retry, resume or a late upload.
The retained interrupted batch can be inspected and whole-session deleted.

A crash after the final transaction leaves the same queued work IDs. Existing
worker recovery may replay an in-flight inference call; this feature does not
promise exactly-once model invocation. It promises no duplicate batch/work IDs
or already-committed ASR rows from a finish/source replay. Normal cancel/pause/
retry operates on published ordinary chunks, and the existing stream semantics
handle their descendants. Staging is not a public resumable chunk state.

Whole-session deletion inserts cleanup intents for originals as well as ordinary
work files before logically erasing all batch/source/work/range rows. Existing
native-cell, support and MOSS deletion notifications still apply. Failed unlink
intents survive and are retried by the existing lifecycle cleanup. Unrelated
sessions and streams are not touched. Retained paths stay within the same owned
uploads/*.audio namespace; cleanup authority is not broadened.

## Test coverage and execution boundary

The focused original-style pytest source has 20 cases, including parameter
expansions: exact prefix/suffix/contained/late-prefix geometry; one-sample
conflicts; unknown/microphone domains; gaps; one-frame final piece; metadata and
revision idempotency; changed originals; caps and disk floor; rollback retaining
an immutable finish fence; positively waited real child process death after
stage; restart preservation/no ASR; erasure retry; unchanged ordinary APIs;
normal Worker replay without duplicate committed ASR; optional native refusal;
the actual native ingestion descriptor contract with simulated test-only runtime
qualification; and mutation of staged files/maps before publication.

Synthetic fixtures are short PCM (the largest unique fixture is 336,001 frames,
under 0.65 MiB). Quota-negative tests override small constants rather than
allocating production-sized batches. Expected new-test retained payload is below
12 MiB, with a 32 MiB fresh output-leaf ceiling including DB/WAL and reports.
The completed model-free grouped run passed all 32 cases in 5.99 seconds, with
zero failures, errors or skips. Kernel per-process peak RSS was 133,201,920 bytes;
this is not a simultaneous process-family peak. No models ran during those tests.
The public source packet has only static syntax and integrity checks.

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
