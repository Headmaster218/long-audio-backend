# Explicit incremental recorder capture intake

This whole-capture command still requires original START at sequence/epoch/frame zero and a final STOP. For an explicitly selected uninterrupted declaration span that starts later or ends in INTERRUPTION, use the separate [incremental selected-span command](INCREMENTAL_RECORDER_SPAN_INTAKE.md). Its selected-span-only EOS and job schema are distinct.

Verification checkpoint: a separate 11-second incremental native fixture passed independent artifact audit. This is not long-duration qualification. Existing default runtime pins and deployment remain unchanged.

This command admits one immutable recorder delivery at a time into a known strict session. It reuses the existing v3 paged append uploader, rather than extending the fixed-batch journal or creating sessions automatically. Each invocation is explicit. No watcher, FTPS poller, server reservation, model/runtime activation or original-file deletion is added.

The frozen default capacity is **12 logical hours and 192 deliveries**, selectable at the first call up to 48 hours and 192 deliveries. These are finite software bounds, **not measured long-duration runtime qualification**. Mono16k PCM16 is required. Individual delivery validation retains its 512 MiB WAV bound, and this wrapper caps aggregate snapshot WAV bytes at 8 GiB. Per-page source slots can be reached earlier than these duration/count limits.

## Start, append, then explicitly finalize

Use an existing empty strict-primary-payload-v2 session and private 0700 parent. As with the other explicit intake commands, assert that only this operator writes the selected session/capture and local job. HTTPS and `AUDIO_API_KEY` behavior are unchanged; HTTP is permitted only on loopback.

```sh
python -m audio_backend.recorder_capture_intake \
  --start --delivery /private/received/segment-0 \
  --job-dir /private/intake-jobs/live-capture-001 \
  --api https://your-backend.example --session-id EXISTING_SESSION_ID \
  --capture-id OPERATOR_CHOSEN_CAPTURE_ID --exclusive-writer
```

For each next complete delivery, rerun without `--start`, changing only `--delivery`. On the first call only, `--max-hours` and `--max-inputs` can change the default frozen bounds. Later calls may omit those options or repeat their exact values.

Even a STOP delivery is appended **without automatically sending EOS**. After its append has succeeded, explicitly finalize:

```sh
python -m audio_backend.recorder_capture_intake \
  --finalize --job-dir /private/intake-jobs/live-capture-001 \
  --api https://your-backend.example --session-id EXISTING_SESSION_ID \
  --capture-id OPERATOR_CHOSEN_CAPTURE_ID --exclusive-writer
```

Finalization requires the latest admitted declaration to be STOP, and every admitted input to have matching completed uploader state. No next delivery is allowed after STOP. A user/process interruption does not imply recorder STOP or permission to close a still-open capture.

## Declared adjacency, not physical continuity

The first delivery must declare sequence 0, epoch 0, frame 0, START and no previous seam. Each following delivery must retain capture/run/configuration identity, follow a ROTATION, have the next ordinal and declared frame start, and carry compatible seam/anchor observations. Only ROTATION and STOP close reasons are supported here.

Missing ordinals, epoch changes, gaps, overlaps, uncertain seams, recording interruption/recovered-tail declarations and changed producer identity are refused visibly. Safe boundary reason codes are returned; no silence insertion, clock correction, epoch joining or implicit EOS is performed. Invalid next-delivery metadata is checked before creating an admission directory, so an ordinary boundary refusal does not corrupt an otherwise valid job.

The output explicitly retains `physical_continuity_proven: false`, `hardware_frame_mapping: UNESTABLISHED`, and unverified producer/backend identity. The operator-selected API origin/session is not an authenticated backend incarnation.

## What an append result means

An append returns `delivery_staged_without_eos`, with separate `published_frames` and `staged_frames`. Short deliveries may exist only in the local uploader spool. The uploader publishes full 294-second pages when subsequent decoding advances; a short pending page stays local until more input or explicit finalization. File EOF is not capture EOS and is not a server-acceptance or analysis-completion claim.

An explicit finalization returns `accepted_with_eos`. Both responses retain `analysis_completion_verified: false`; inspect downstream chunks/results separately for failed or pending analysis. No inference retry is automatic.

## Admission and crash recovery

The job has an immutable target/capacity header and one immutable ordinal admission record per delivery. Each record binds the original path, stable snapshot path, full delivery report, metadata/envelope/WAV hashes and transport revision/attempt. It is fsynced before the uploader is allowed to register or send that input. Every retry positively re-syncs the exact bound snapshot/receipt files and containing directories, then verifies them again; readable canonical bytes left by an earlier failed fsync are not treated as durability proof. The producer source ID remains the uploader input ID; changing retry content never creates a new retry identity.

There is no separately mutable central index. Bounded ordinal enumeration plus the authenticated v3 uploader registry must agree on an exact prefix, with at most one outstanding admission. Both the intake lock and the actual uploader lock remain held through registry inspection, append/finalize, remote reconciliation and final checks. Legacy v2 dispatch is explicitly disabled for this command.

After an interrupted append, repeat the exact latest `--delivery` call, without `--start`. A complete admission whose uploader registration did not finish can resume. An incomplete active input must resume before the next delivery; a missing owned spool piece can be rebuilt from its unchanged snapshot. Repeating an older delivery once a newer one has been admitted is refused. After finalization begins, use `--finalize` again to reconcile the saved EOS request.

Missing uploader checkpoints, missing committed admissions, unexpected files or changed metadata preserve state and refuse in ordinary retry mode. Explicit initial recovery is available within the boundaries below. New jobs carry durable initial intent, allowing exact prefix completion of interrupted bootstrap files. Legacy jobs without that intent still require a complete first admission. Do not replace the capture, delete the job or edit its records to bypass a refusal. No automatic cleanup or adoption is attempted.

## Explicit recovery before the initial uploader checkpoint

Use `--recover-initial` instead of `--start` with the exact original delivery, job,
API, session and capture. Keep `--exclusive-writer`; supplied capacity options must
match. This is an explicit operator decision, not a watcher or automatic retry.

New jobs write immutable `initial-intent.json` under the intake lock **before**
creating the capture header, uploader scaffold or snapshot. The intent binds the
absolute job and original delivery paths, complete verified delivery report,
stable source/capture identity and frozen capacity header. It is retained for the
job's lifetime. A partial/missing intent or missing intake lock is never guessed.
Legacy jobs without the new header marker retain the earlier complete-admission
recovery path; their incomplete header/snapshot/admission is not reconstructible.

For an intent-backed initial job, recovery first verifies that exact original and
all existing control/snapshot bytes. Each existing file must be an exact prefix of
the intended bytes with permitted private ownership, type and hardlink structure.
Only ordinal zero is allowed. Unknown entries, changed source bytes, mismatched
prefixes, symlinks, unexpected hardlinks and oversized files refuse. A published
snapshot receipt must already be complete and share its canonical pending inode;
recovery does not treat an impossible partially published snapshot as a valid
copy-in-progress. Existing bytes and inodes are preserved. Missing files may be
created and missing suffixes appended, but nothing is overwritten, truncated,
deleted, renamed or substituted. This is bounded reconciliation of known bytes,
not permission to replay arbitrary input.

Before repairing anything, the uploader directory must be absent, empty or contain
only its empty lock. An existing uploader checkpoint, temporary checkpoint,
history directory, media spool or other artifact refuses. Any existing uploader
lock is held before remote observation. The strict recording contract must report
no capture and no chunks. Missing/unavailable or nonempty remote state refuses.
All local candidates are preflighted before writes; the intent is positively
fsynced again, missing scaffold and the exact snapshot/receipt are completed and
synced, and the ordinary immutable registry and uploader checks run afterward.
The uploader independently checks session/capture/chunks before initializing.

Exclusive control remains a precondition: GET observations do not reserve the
session, authenticate its incarnation or protect against another OS/account
writer. The command retains the existing refusal when the session changes before
initialization. No server reservation, session creation or watcher is introduced.
Disk reserve, frozen frame/byte/input caps, cancellation and the enclosing deadline
remain enforced; snapshot recovery also has its own 120-second cooperative bound.

The same session, capture, source ID, ordinal, original/snapshot path, frame origin
and capacity survive recovery. After a valid uploader checkpoint exists, omit
`--recover-initial` and use the ordinary exact-delivery retry, even if recovery
itself was interrupted. Do not delete evidence to satisfy recovery preconditions.
No EOS, inference retry, native runtime activation or cleanup is implicit.

Tests use actual subprocess `os._exit` at six initial stages: after durable
intent, during header writing, after scaffold creation, during WAV copying, after
snapshot completion and during admission writing. A seventh exit interrupts
suffix completion during recovery itself. Tests verify preserved inode/prefixes,
exact frame/source identity, no duplicate input, next-ordinal append and fail-closed
conflicts. Separate helper tests cover short writes, fsync failures, immutable
publication, hardlinks and filesystem types. These are model-free admission tests,
not physical power-loss, native inference or filesystem durability qualification.

## Capacity and work bounds

- At most 192 admission records, each bounded to 16 KiB. Long paths can trigger that metadata bound.
- Snapshot WAV bytes are capped separately from uploader spool/history/state. They remain retained with originals, and are not part of the uploader's 12 MiB working spool.
- Before copying a new snapshot, free space must cover its bytes, the configured disk reserve, uploader spool and an additional 16 MiB margin. This is a preflight check, not an OS disk-space reservation against other processes.
- Known duration and byte limits are checked before admission, so an already known over-cap delivery is not partly accepted first.
- Existing pages have at most 16 source pieces. Fourteen tiny deliveries can exhaust a pending page's reservation; the next delivery then refuses with `capture_source_slots_exhausted_no_automatic_eos`. The command does not finalize early to escape this limit. A supported coalescing or operator-resolution workflow would be separate work.
- One effective frozen-cap guard and cooperative deadline covers each invocation; it is not accidentally reset to the legacy two-hour frame limit. Inspection operations also retain their 120-second per-operation bound. Startup and final verification scan history and retained originals, so work grows with retained bytes. This is bounded-history processing, not constant-time append.

## Evidence limits

Tests cover actual strict ASGI/SQLite admission without native lifespan, explicit STOP/EOS fencing, successful PUT response loss, exact outstanding-admission recovery, active spool reconstruction, invalid-boundary refusal without poisoning the job, actual uploader-lock exclusion, missing-checkpoint refusal, and source-slot exhaustion. A synthetic real-geometry 294-second input checks that a full page requests a **non-EOS** seal, stopping before native publication. A metadata-only three-hour capacity case tests arithmetic beyond two hours; it is not three hours of ingestion or resource evidence.

The bounded native smoke can separately append the producer's 4s+4s+3s fixture, verify no premature EOS, retry its latest input, explicitly finalize with one lost published-seal response, and replay completed finalization. This short silence experiment cannot establish long-duration stability, transcription accuracy, speaker identity, real device capture, physical continuity or FTPS transport of this fixture.


## Observed bounded incremental run, 2026-11-03

From restored GitHub source in the isolated accepted runtime candidate, three independent 4s/4s/3s append calls returned 64,000, 128,000 and 176,000 staged frames, all with zero published frames and no EOS. The latest-delivery retry equality check passed in the pinned harness. Explicit finalization then recovered one discarded successful published-seal response; a second finalization call returned the same result.

Independent audit verified all 177 pinned Python source files, one session/page, three source placements, exact original/snapshot/backend/chunk PCM, two completed chunks (168,000 and 8,000 frames), persisted EOS seal and one stream-final marker, empty final uploader spool, database integrity and clean shutdown. Lost-response count and the unsaved separate latest-delivery retry are supported by the frozen harness and completed result, not a retained full HTTP transcript. The run used local Android producer silence outputs, not a new FTPS/device recording or speech-accuracy test. Source backups exclude all media, databases, model assets and generated reports.

Before selecting segment size and frozen limits, run the [read-only profile preflight](RECORDER_SEGMENT_PROFILE_PREFLIGHT.md). New jobs default to 192 deliveries, enough for 144 five-minute segments across twelve logical hours. Terminal-aware Android can mark the exact-full last segment STOP; separate explicit finalization is still required. Older jobs keep their frozen 64/128 limits and cannot be upgraded by retry options. Legacy ROTATION-only endings still cannot imply STOP/EOS; choose the planner’s explicit legacy mode for those producers.


## Extended input-capacity evidence

The 192-input maximum is finite, not an unbounded recorder queue. The journal's
maximum authentication bytes are 24,960 across 588 page and 192 input receipts;
32 MiB metadata, 48 MiB owned state and 12 MiB spool limits are unchanged. At the
individual record-size limits, conservative allocation/reservation arithmetic is
26,628,096 metadata bytes and 39,211,008 bytes including spool. Runtime allocation
checks still refuse unsupported filesystem geometry or exhausted resources.
Intake may retain 192 bounded admission/decoded records; it revalidates historical
originals/snapshots and journal history on each invocation. Startup and replay work
is bounded by count/bytes but grows with history, including quadratic duplicate
checks. This is not constant-time append or a throughput guarantee.

Tests exercise real tiny original files and intake/uploader journals across inputs
129–144, explicit STOP/finalize, outstanding-admission retries, fresh object reloads
and a lost committed source response, using a scaled synthetic protocol peer.
Separate real-geometry metadata-only fixtures cover 144×300s/147 pages and maximum
192 padded inputs/588 pages, frozen old caps, restarts and late-history tampering.
Neither fixture processes twelve or forty-eight hours of real audio or qualifies
native inference. Existing strict-handler admission tests are separate evidence.
