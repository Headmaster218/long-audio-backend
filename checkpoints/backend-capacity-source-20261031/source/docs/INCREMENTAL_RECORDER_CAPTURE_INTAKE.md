# Explicit incremental recorder capture intake

Verification checkpoint: a separate 11-second incremental native fixture passed independent artifact audit. This is not long-duration qualification. Existing default runtime pins and deployment remain unchanged.

This command admits one immutable recorder delivery at a time into a known strict session. It reuses the existing v3 paged append uploader, rather than extending the fixed-batch journal or creating sessions automatically. Each invocation is explicit. No watcher, FTPS poller, server reservation, model/runtime activation or original-file deletion is added.

The frozen default capacity is **12 logical hours and 64 deliveries**, selectable at the first call up to 48 hours and 128 deliveries. These are finite software bounds, **not measured long-duration runtime qualification**. Mono16k PCM16 is required. Individual delivery validation retains its 512 MiB WAV bound, and this wrapper caps aggregate snapshot WAV bytes at 8 GiB. Per-page source slots can be reached earlier than these duration/count limits.

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

Missing uploader checkpoints, missing committed admissions, partial snapshots/receipts, unexpected files or changed metadata preserve state and refuse. A narrowly bounded explicit recovery is available only when the first complete admission exists and the uploader directory contains only its lock. See below; every other initial interruption still requires investigation. Do not replace the capture, delete the job or edit its records to bypass a refusal. No automatic cleanup or adoption is attempted.

## Explicit recovery before the initial uploader checkpoint

If the process exited after publishing the complete ordinal-zero admission but before creating any uploader checkpoint, repeat the exact original delivery/target/job call with `--recover-initial` instead of `--start`. This is an explicit operator decision, not automatic retry. Keep `--exclusive-writer` and all original arguments; capacity options must match if supplied.

Recovery requires an intact private job header and both original locks, exactly one complete ordinal-zero admission, the exact admitted original path and bytes, and a fully verified owned snapshot. Its uploader directory must contain **only** `uploader.lock`: an existing checkpoint (including an empty one), temporary file, history directory, spool, symlink or unknown entry refuses before HTTP. The backend recording contract must freshly report no capture and no chunks; the uploader independently checks empty session/capture/chunks again before initializing. All reads must succeed, and the original exclusive-writer precondition remains required because these observations are not a server-side reservation. This does not authenticate a backend incarnation or protect against a competing external writer.

Every bound snapshot and admission file is positively fsynced and reverified before initialization. The same session, capture, source ID, ordinal, input path, frame origin and capacity are retained. No previous remote request is replayed from guessed state. The existing uploader initializes its first local checkpoint and handles subsequent interruptions through ordinary exact-delivery retries. After that checkpoint exists, omit `--recover-initial`; the recovery option deliberately refuses an already started uploader, even after successful recovery. A nonempty or uncertain remote result does not authorize adoption.

Partial snapshot copies, missing/partial admission receipts, incomplete header/locks, extra admissions and lost checkpoints with remaining artifacts are still preserved and refused. Do not delete or rename evidence to satisfy the recovery preconditions. No session creation, watcher, automatic snapshot repair, EOS, inference retry, native runtime activation or cleanup is added.

The regression test starts an actual subprocess and exits with `os._exit(73)` immediately before the first uploader run, bypassing Python cleanup. The parent uses real strict ASGI/SQLite handlers for recovery, verifies exact source identity and frame accounting, retries without duplicate input, and appends the next ordinal. Remote uncertainty, nonempty sessions, altered originals/snapshots/receipts, substitute paths, partial admissions and any uploader artifacts remain refusal cases. These are model-free admission tests, not native inference or power-loss/filesystem durability qualification.

## Capacity and work bounds

- At most 128 admission records, each bounded to 16 KiB. Long paths can trigger that metadata bound.
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

Before selecting segment size and frozen limits, run the [read-only profile preflight](RECORDER_SEGMENT_PROFILE_PREFLIGHT.md). The Android source default of five-minute segments is source-slot safe but reaches the default 64-input limit around 5h20m. Roughly seven hours needs an explicitly larger input budget. Exact-boundary ROTATION-only endings still cannot imply STOP/EOS.
