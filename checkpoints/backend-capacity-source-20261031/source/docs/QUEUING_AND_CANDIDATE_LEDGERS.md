# Bounded work units and durable candidate evidence

## Queue admission

The legacy `Settings.max_pending_chunks` name now limits **work units**:

- One independent upload is one work unit.
- All unfinished receipts of one explicit `(session_id, stream_id)` continuous stream are one work unit.
- Each explicit stream has a hard limit of 128 unfinished receipts. Queued, running, paused, waiting-for-input, and recoverable failed stream receipts count toward this bound. Failed independent chunks do not occupy a running-work slot.
- Nonfinal stream fragments must contain at least 0.5 seconds of real audio. The check occurs after media duration is known but before durable admission. Rejection rolls back the receipt and timeline and does not consume an idempotency key.
- Tiny explicit final fragments are allowed. `/sessions/{sid}/streams/{stream_id}/finish` can finalize an already uploaded stream without adding a receipt, even when its buffer is full.
- Retrying an existing failed stream receipt does not consume an additional fragment slot. Cancelling a predecessor cancels its pending descendants and closes that continuity scope; completed results remain intact.

Thus a queue limit of one admits one stream's three 10-second pieces but rejects a second unrelated stream. A full 128-piece buffer contains at least about 64 seconds of nonfinal source audio, enough for the current bounded native processing horizon. Processing can free slots; a failed/paused predecessor must be retried/resumed/cancelled, not hidden as a mysterious full queue. Future adapters must not silently require an unbounded horizon.

For new admitted work, total unfinished receipts are bounded by `128 × max_pending_chunks`; most workloads use much less. `/health.queue_limits` exposes these effective units and limits. Disk-reserve checks remain independent. Completed retained recordings still require storage and intentional retention/deletion; this is a bounded-memory queue, not unlimited disk.

Chunk collision admission uses an indexed predecessor lookup under the existing nonoverlapping-upload invariant. This does not forbid simultaneous **speaker turns inside one recording**.

## Event candidates

`GET /sessions/{sid}/event-candidates?after=0&limit=500` is a distinct, durable evidence ledger. It retains uncertain AudioSet classifier alternatives even when they do not become primary events:

- At most 10 safe raw labels and six mapped-family scores per support window
- At most 64 support windows per analysis commit
- At most 500 rows per page; payload ≤8 KiB and event provenance ≤4 KiB per row
- Immutable first-write keys `(chunk_id, analysis_offset, candidate_index)`
- Explicit `unverified_model_candidate`, uncalibrated scores, and `window_support` timing; onset precision may be unknown/null

`start/end` describe the owned core intersection on the session timeline. `support_start/support_end` preserve the original classifier support on that timeline, including context outside the core. These are not invented event onsets. The worker must rebase analyzer-input coordinates before storage; blindly adding the chunk offset to old metadata would shift context timestamps.

Candidates do not promote subthreshold labels into `/events`, do not change a speaker profile, and do not establish that a person was driving or that a specific object caused a generic sound. Voice demographic subclasses are excluded. Primary `/events` rows explicitly identify unverified model/rule inference, not human-verified facts.

## Overlap candidates

`GET /sessions/{sid}/overlap-candidates` is separate from the ordinary transcript and speaker bank. Candidate regions/tracks preserve original support, scoped source slots, unresolved identity, and unverified quality. Group/track IDs are namespaced by chunk and analysis window. An anonymous separator slot is not a persistent person.

The region cursor keeps all associated source tracks with the region. Responses have a bounded soft byte cap, with a complete single region retained. Candidate provenance is allowlisted; raw voice vectors and audio filesystem paths are not public fields. Separation-disabled, insufficient-conditioning, failed, and capacity-limited states remain explicit; original mixture data is not discarded by creating candidates.

## Replay, correction, and deletion

`GET /sessions/{sid}/changes` includes speaker turns, primary event inference fields, event candidates, and overlap regions/tracks through explicit safe field allowlists. JSON candidate payloads/provenance are decoded for clients. Schema upgrades backfill changed public tables once; replay remains cursor-paginated.

Candidate rows are immutable analysis evidence. Speaker identity reset clears transcript/turn person assignments and private templates; it does not erase unrelated acoustic evidence or turn anonymous overlap candidates into identities. Session deletion removes candidate ledgers with their source chunks and erases that session's historical public-feed payloads.

Logical deletion and private file-cleanup intents commit before any file is unlinked. Interrupted/failed cleanup is retryable and reported as pending, rather than leaving live rows pointing to partially deleted files. Application deletion is not a promise of forensic secure erasure of disks or backups.

## Event policy and artifact lineage

New analyzer results and persisted event-candidate provenance include
`event_model_sha256` and `event_labelmap_sha256`, computed from the actual local
model and AudioSet CSV bytes, not a manifest or model display name. Their stable
fingerprint also includes `event_policy_revision`, 5-second classification
support, 2.5-second hop, 5-second maximum output interval, and the full effective
policy: `event_threshold`, resolved per-family `event_thresholds`,
`event_release_ratio`, and `event_silence_peak`. The silence acceptance threshold
is always 1.0, including when a different silence override was supplied. No
threshold, label mapping, score, or event timing behavior changes in this update.

The same fields survive candidate SQLite storage, `/event-candidates`, and the
`/changes` snapshots. Chunk stable provenance retains the artifact identities as
well. Existing immutable evidence is not rewritten or backfilled with guessed
identities. Missing historical hashes or execution flags mean unknown.

Hashing reads bounded 1 MiB blocks and caches at most the two event artifacts per
analyzer. Unchanged windows only inspect filesystem metadata. Size, nanosecond
mtime/ctime, inode/device, and resolved-path changes invalidate that entry;
missing/unstable artifacts fail rather than reuse an old identity. Event tagging
currently loads a fresh model and labelmap on each call. A file change across
that call rejects the analysis result, so output is not assigned the identity of
new bytes after loading old bytes. This is a local change detector, not protection
against an adversary able to restore all filesystem metadata. Model directories
should remain stable during analysis. Paths and cache metadata are not public.

Artifact identities describe the configured files, not proof of neural
execution. The whole-window silence fast path does not read or require unused
event artifacts: their hashes are omitted and
`event_artifact_identity_status: not_checked_silence` is explicit. Constructor
model-presence validation is unchanged. Non-silent analysis still requires both
artifacts and reports `event_artifact_identity_status: hashed_local_files`. Each
new candidate's `model_inference_executed` records whether that
support window actually called the tagger. Silence-rule windows report false,
including within mixed silent/non-silent analysis windows. The whole-window
silence fast path continues to report `models_executed: []`,
`model_execution: not_run_silence`, and now
`event_model_inference_executed: false`; it does not create a non-silence stable
provenance variant.
