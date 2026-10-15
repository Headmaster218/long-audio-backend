# Finalized recording batches: exact PCM overlap

This opt-in API accepts all source recordings before processing. One capture ID
declares one shared sample-coordinate domain in one fresh, empty `legacy`
session. Only byte-equal PCM at explicitly declared coordinates is aliased.
Original encoded WAV bytes and receipts remain retained, including duplicates,
unknown positions and conflicting sources. No waveform alignment, resampling,
downmixing, normalization, word matching or clock-based placement is performed.

For commands, see [the Chinese API guide](TESTED_API_GUIDE_ZH.md). Installation,
schema changes and rollback cautions are in [the installation guide](INSTALL_DEV9_ZH.md).

## API and coordinates

1. `POST /sessions` with `processing_mode: legacy` creates a fresh session.
2. `PUT /sessions/{sid}/recordings/{capture_id}/sources` accepts a raw PCM16
   little-endian, 16,000 Hz, mono WAV body and an `Idempotency-Key` header.
   `source_start_frame` is an exact nonnegative integer, at most `2^53-1`, in
   this capture's coordinate domain. Omitting it retains unknown placement.
3. `GET /sessions/{sid}/recordings/{capture_id}` returns original receipts,
   revision, batch state, work, maps, optional receipt state and blockers.
4. `POST /sessions/{sid}/recordings/{capture_id}/finish` accepts strict JSON,
   for example `{"expected_revision":3,"idempotency_key":"finish-1"}`.
5. `POST /sessions/{sid}/recordings/{capture_id}/cancel` cancels a
   `pending_receipts` batch while preserving originals, maps and payloads.

`capture_id` is 1–128 ASCII letters, digits, underscores, dots or hyphens. It is
a coordinate declaration, not an inferred microphone identity. Different or
uncertain microphone domains require different captures and sessions; matching
timestamps or bytes alone do not establish a shared domain. There is no
`recorded_at` alignment parameter on these routes.

The first source reserves the empty session. Ordinary chunk insertion then
fails; use another session for ordinary uploads. A source retry uses the same
capture, key, original encoded bytes, format and declared position. An exact
retry returns the original source ID without increasing revision; changed
content or placement conflicts. Finish binds its key, revision, fixed policy
and timeline placement. Exact replay returns the same work/map IDs and current
state, including after restart, without creating another batch of chunks.

The first valid finish freezes the source snapshot before comparison or copying.
A stale revision does not freeze it. After freezing, no new source or placement
correction is accepted, including for an unresolved or interrupted batch;
original exact receipt retries remain valid. Preserve that batch for inspection
and use a fresh session for corrected input. Cancellation does not reopen it.

## Exact union and provenance

Sources are ordered by declared coordinate, then receipt order. Every overlapped
sample is compared byte-for-byte in blocks of at most 32,768 frames. Equal
prefixes, suffixes and contained sources become `exact_alias` ranges; otherwise
the selected interval is an `owner` range. Different WAV headers can still hold
equal PCM; original encoded-byte digests are always retained separately.

Unknown placement, a coordinate gap, different PCM, a missing/changed original
or excess capture duration leaves the batch `unresolved`, with zero ASR work
and no published alias map. No conflict is silently resolved in favor of one
source. The minimum source coordinate becomes the immutable capture origin,
mapped to session timeline frame zero. Each work chunk has at most 168,000
frames (10.5 seconds); only the last may be shorter. The internal stream is
explicitly finalized. Neither a giant joined file nor an all-capture PCM array
is built.

The durable range map distinguishes source-local, capture and work-local frame
coordinates. Every original frame, including aliases, maps back to its original
receipt. Derived work has its own byte digest and genuine native receipt;
native proof refers to the derived file, while the separate recording map
preserves original provenance.

## States, receipts and polling

`open` accepts sources. Finish moves through `preparing` and can return
`pending_receipts`, `queued` or `unresolved`; interrupted preparation becomes
`interrupted`. Pending cancellation yields `cancelled`. HTTP 202 is acceptance,
not completion. A `queued` batch still requires polling each ordinary chunk to
`completed`; failed/paused/cancelled work must be handled explicitly.

GET is private/no-store and uses one read transaction. Returned fields include:

- `capture_id`, `coordinate_domain`, `format`, `policy`, `revision`, `state`,
  `capture_origin_frame`, `capture_end_frame`, `stream_id`;
- `sources`: IDs, order, idempotency key, encoded-byte digest/length, frame count,
  declared position, format and creation time;
- `work_chunks`: chunk IDs, position, frame/byte counts, capture offset, digest,
  timeline/duration, ordinary status, `native_receipt_status`, `receipt_state`,
  `receipt_id` and `receipt_attempts`;
- `ranges`: source/chunk IDs, all three frame offsets, count and relation;
- `optional_receipts`: `pending`, `durable`, `gaps`, `ready`; `blockers`, `limits`.

File paths and raw receipt payloads are not exposed. Receipt state is `pending`,
`durable`, `gap` or `cancelled`. `receipt_id` hashes the actually observed
canonical receipt, or is null if observation was unavailable. Attempts are a
saturating diagnostic count, not an inference count. `ready` requires all work
receipts to be durable; intentionally disabled capture is excluded from `gaps`
but does not become durable. None of these fields proves cells or MOSS results
exist. Their separate proof-gated APIs remain authoritative.

Pending work is nonclaimable staging. The Worker maintenance loop performs at
most one receipt admission or retirement per tick, with a one-second retry
floor. Owner busy, temporary unavailability and uncertain acknowledgements keep
the exact payload pending. Other sessions can still progress. A batch may wait
longer than two seconds; each individual owner operation retains its original
two-second deadline and bounded work counters.

A definite receipt-capacity refusal or missing observation is an explicit
optional gap. Primary work can become queued with a gap blocker; clients must
report it separately. A persistently unavailable owner may leave a genuinely
observed receipt pending until owner recovery or explicit pending cancellation.
Polling timeout does not prove failure and must not create another finish key.

## Finite admission

The stricter of these defaults and configured service limits applies:

- 16 original sources per batch; one batch per session; 64 retained batches.
- 120 seconds / 1,920,000 frames and 3,905,536 encoded bytes per source.
- 300 seconds / 4,800,000 unique capture frames; at most 32 derived chunks.
- 32 MiB per-batch and 128 MiB global recording-copy budget.
- Fixed 512 KiB metadata reservation per batch, including at most 32 canonical
  receipt BLOBs of at most 1,024 bytes each.
- Existing upload/audio limits, process checks and filesystem free-space floor.

Copy accounting uses at least logical size or allocated blocks, whichever is
larger, and includes feature-owned originals, staging, work, orphans, failed
cleanup remnants, metadata reservations and allocation rounding. Unrelated
ordinary uploads are outside this feature quota but consume filesystem space.
The reservation is not a bound on the entire primary database/WAL allocation.
Comparison, hashing and copying are block-bounded; sources, spans, maps and
pieces are count-bounded. These bounds do not establish a 48-hour buffer.

## Durability and bounded contention

Source files are flushed, renamed and directory-synced before receipt commit.
Finish first commits the frozen input snapshot. Derived files are bounded WAV
pieces. Genuine open-descriptor observations are frozen into canonical receipt
payloads, not reconstructed from paths after restart. Staging rows, complete
maps, file identities, manifest and payloads commit atomically in the primary
database before owner delivery.

The existing owner validates committed primary/path authority before accepting
a receipt. An exact already-committed receipt can reconcile a lost acknowledgement
on the owner thread; conflicts, generation changes and invalid authority are
rejected. Recording maintenance never opens the optional database. Before the
atomic staging-to-queued flip, the complete manifest, source/work hashes,
file identities and current owner acknowledgement are rechecked. Corruption
interrupts publication rather than silently becoming optional loss.

Only complete `pending_receipts` manifests resume after restart. Previously
durable acknowledgements in a pending batch return to pending for current-owner
reattestation. Incomplete preparation becomes interrupted; no observation or
proof is invented. A crash after queue publication retains the same work IDs.
Ordinary recovery may repeat an in-flight inference call; exactly-once model
invocation is not promised. Committed ASR rows and batch/work IDs are not
duplicated by an exact upload/finish replay.

PrimaryAuthority's six materialized reads use fresh read-only snapshots with
zero SQLite busy timeout. Only integer error codes `SQLITE_BUSY` (5) and
`SQLITE_BUSY_RECOVERY` (261) permit at most three attempts and two 5 ms sleeps.
Failed connections close before retry, and original deadlines, elapsed time,
counters and authority checks remain charged. Writes, uncertain commits and
model work are never retried by this mechanism. Other errors propagate. Fixed
phase labels, attempt, sanitized exception class and SQLite code/name are
diagnosed without SQL, exception text, source paths, IDs or audio.

## Cancellation, retention and limits

Pending cancel atomically cancels staging and pending outbox state while
retaining original files, maps and payloads. A previously accepted owner receipt
may retire, but a late acknowledgement cannot queue cancelled work. Once queued,
use ordinary per-chunk controls and their stream semantics; the pending-cancel
route is not an undo of completed work.

Existing whole-session deletion remains destructive: it records cleanup intents
for originals and derived files, erases the associated recording rows, and uses
existing cell/support/job revocation and cleanup. It is not required to resolve
or retry a frozen batch; keeping the old session and creating a fresh one avoids
discarding evidence. There is no source-specific delete, undo, revocation, late
insert, source download or incomplete-finalization rebuild API in this release.

This is finalized known-coordinate exact-PCM processing, not continuous capture,
sealed-prefix streaming, approximate alignment, full-content MOSS recovery,
global identity validation or long-duration endurance qualification. See the
[release notes](DEV9_RELEASE_NOTES_ZH.md) for fixed-case results and historical
failures, reported separately.
