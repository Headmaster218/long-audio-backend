# Read-only recorder span completion status

`python -m audio_backend.recorder_span_status` observes the exact existing job
created by `recorder_span_intake`. It answers whether the selected span's work
was observed completed in both page and session projections, without resuming
intake or changing any local job files or remote state.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m audio_backend.recorder_span_status \
  --job-dir /private/intake-jobs/span-41-42 \
  --api https://your-backend.example \
  --session-id EXISTING_SPAN_SESSION_ID \
  --capture-id ORIGINAL_SPAN_CAPTURE_ID \
  --exclusive-writer \
  --deadline-seconds 120
```

Supply the original API origin, session and capture explicitly. The command
compares them with both durable local records before making any request; a
manifest cannot silently redirect this invocation. Credentials, if needed, use
the existing `AUDIO_API_KEY` transport header. They are not printed or persisted.
The private parent/job directories, original deliveries, owned snapshots,
manifest, intake lock, uploader lock and finalized v2 uploader journal must all
still exist. Do not copy a journal into a replacement job or edit it to make
status succeed. The original input paths are read from the verified manifest;
there is no new selection, target adoption or migration.

## Results and next steps

The command performs one finite observation, not a polling service:

- `completed`: every expected work chunk was `completed` in both GET projections,
  with exact identity/geometry/coverage and terminal session progress. Both
  `analysis_completion_observed` and its compatibility field
  `analysis_completion_verified` are true under the assumptions below.
- `pending`: all identities and histories reconcile, but one or both projections
  have unfinished work or disagree about completion. Run this same read-only
  command again later if another observation is useful.
- `blocked`: either projection shows failed, paused or cancelled work, or the
  capture is aborted. The report identifies the affected work IDs/positions and
  statuses without exposing server error text. Inspect that existing job and
  server. No analysis retry or repair is performed.
- `not_ready`: the existing v2 journal is not locally finalized. This is not a
  partial success. Preserve the job; use the documented exact-job intake recovery
  workflow only if resumption is intended and authorized.
- `refused`: missing, malformed, changed or incompatible local/remote history,
  unexpected chunks, target mismatch, lock contention, authentication/transport
  errors, cancellation or a resource limit prevented verification. No successful
  completion claim is returned, and HTTP errors are not retried.

Exit status is 0 for an observed `completed`, `pending` or `blocked` result, and
2 for `not_ready` or `refused`. Parse the JSON `status` rather than treating exit
0 as proof of completion. Refusal JSON is on stderr. The Python `run` API raises
on refusal; `NotReady` distinguishes an unfinished journal.

`optional_receipts` separately reports the page API's pending/durable/gaps/ready
receipt counters. It does not gate primary completion and does not mean optional
analysis is complete. Durable means the original acknowledgment was recorded;
it does not promise perpetual retention of optional metadata.

## What is checked

The command holds both existing locks using read-only descriptors throughout.
It never creates a lock, directory, snapshot or journal and never calls uploader
save, cleanup, resume, seal, finish, cancellation or inference retry paths.
All network requests are body-free GETs of the existing recording contract,
capture, each frozen page and the finite session chunk listing.

Before HTTP, original and snapshot bytes/metadata are revalidated, sequence and
closure declarations are reconciled, and the original manifest is reconstructed
exactly, including cumulative span offsets. Pure v2 journal validation verifies
local lineage and frontiers. Each frozen source piece's PCM/WAV hashes are
recomputed from its snapshot. Source identities and local control files are
checked again before a successful observation returns.

Remote checks include:

- The strict-primary-payload-v2 contract, exact capture, EOS and publication
  frontiers, with no pending or additional pages.
- Frozen page/server IDs, current page revisions, release state and exact source
  IDs, idempotency keys, digests, lengths, formats and span-local coordinates.
- Work IDs, global positions, byte/frame lengths, timeline starts and durations
  covering the selected span exactly, without duplicates or foreign work.
- A cursor-checked session scan through an explicit empty terminal response.
  Its complete ID set and geometry must equal the page work set. Global rowids
  can be sparse; the count, not the numeric cursor, is bounded.
- Finite nonnegative session progress. A `completed` chunk must reach its full
  duration within a sub-frame floating-point tolerance, never one missing frame.
- A final capture/contract re-read and local identity reinspection. Missing or
  inconsistent history refuses rather than returning a partial success.

A span ending exactly at a full page can have a saved capture-level finish and
last-page `eos=false`. This valid finalized form is supported without replaying
the finish. Ordinary final short pages retain their frozen page-local EOS.

## Bounds and limitations

The original limits remain: 1–8 inputs, at most 512 MiB of input WAVs and two
hours of mono 16 kHz PCM16, at most 25 pages and 700 work chunks (two hours
actually needs at most 686). Lower supplied input/frame limits are honored.
Session pages request 16 rows to accommodate bounded Unicode error fields.
Each HTTP response is capped at 512 KiB, aggregate decoded response bytes at
32 MiB, and requests at a fixed finite count. One shared cooperative guard
covers cancellation, elapsed time and RSS, using the existing bounded HTTP
transport and deadline. These are software limits, not measured production
performance or hard OS interruption guarantees. No native workers start.

**This is a non-atomic observation under an explicit exclusive-writer assumption.**
The local locks do not lock the server or prevent another operator, direct API
client or analysis retry from changing it. Page/chunk status may change between
GETs, and the server provides no transaction token covering all projections.
The result does not guarantee simultaneous completion or safety against other
writers. GET also omits frozen seal/finish request keys, so
`remote_request_identity_verified=false` even when the current page revision
and exposed history match. Exact request replay is deliberately not attempted.

EOS still means **only the selected span ends**. Original STOP, INTERRUPTION or
RECOVERED_TAIL evidence is preserved; before/after gap durations remain unknown.
The report does not prove full recorder-capture completeness, physical acoustic
continuity, a hardware timeline, transcript accuracy or speaker accuracy. A
successful synthetic test is not native inference or production qualification.

## Verification

`tests/test_recorder_span_status.py` exercises real ASGI GET routing and SQLite
projections with explicitly synthetic publication/analysis records and no native
lifespan. It covers completion, unfinished/blocked states, projection disagreement,
real multi-page geometry and full-page finish, identity/coverage tampering,
malformed cursors, sparse pagination, bounded Unicode responses, local byte/mtime
preservation, locks, cancellation and limits. These fixtures do not qualify
actual native publication, inference, restart or audio accuracy.
