# Explicit incremental selected-span intake

`audio_backend.recorder_incremental_span_intake` accepts one original delivery at a time into a known empty strict-primary-payload-v2 session. This removes the fixed span adapter's eight-input limit for a single declaration-contiguous selected span. It uses the existing bounded v3 uploader and its 192-input capacity, rather than changing the fixed-span or whole-capture journal formats.

A selected span may begin at a nonzero original sequence, epoch and frame origin. Those declarations and every original source ID remain unchanged in the retained snapshot/admission. Only the backend timeline is span-local: its origin is zero, local ordinal is original sequence minus the frozen first sequence, and each local frame start is the sum of the proven preceding selected inputs. Missing predecessors are not fabricated, and the first producer declaration is never rewritten to sequence zero or START.

## Explicit start, append and finalize

Use a private 0700 parent directory, an operator-selected capture ID and an existing empty strict session. The operator must control the target session/capture and local job exclusively. API/authentication and HTTPS rules are the same as the other intake commands; HTTP is limited to loopback.

```sh
python -m audio_backend.recorder_incremental_span_intake \
  --start --delivery /private/received/original-segment-23 \
  --job-dir /private/intake-jobs/selected-span-001 \
  --api https://your-backend.example --session-id EXISTING_SESSION_ID \
  --capture-id SELECTED_SPAN_CAPTURE_ID --exclusive-writer \
  --acknowledge-span-local-closure
```

Append each next complete delivery by repeating the command with its exact path and without `--start`. Every invocation, including retries, recovery and finalization, requires both acknowledgments. The closure acknowledgment means that any eventual EOS closes **only the selected span**, not a complete recorder capture or an inferred missing prefix/suffix.

The first input can be ROTATION, STOP, INTERRUPTION or a singleton RECOVERED_TAIL. Each subsequent input must retain the capture/run/epoch/configuration declaration, be the next original sequence, and pass the existing declared-frame, trusted-seam and anchor checks with no unresolved boundary reasons. Its predecessor must be ROTATION. A recovered uncertain tail cannot be joined to any other input.

STOP and INTERRUPTION are terminal declarations, but appending them still sends **no implicit EOS**. After the terminal input is completely staged, finalize explicitly:

```sh
python -m audio_backend.recorder_incremental_span_intake \
  --finalize --job-dir /private/intake-jobs/selected-span-001 \
  --api https://your-backend.example --session-id EXISTING_SESSION_ID \
  --capture-id SELECTED_SPAN_CAPTURE_ID --exclusive-writer \
  --acknowledge-span-local-closure
```

Finalization requires STOP, INTERRUPTION or singleton RECOVERED_TAIL. A latest ROTATION cannot finalize. No new input can follow a terminal declaration. Before finalization begins, only the exact latest-delivery retry is permitted; after it begins, repeat `--finalize` to reconcile the saved request. No gap filling, padding, epoch joining, automatic session creation, inference retry, watcher or deployment is introduced.

## Provenance, results and status

The immutable `capture.json` schema is `explicit-incremental-recorder-span-intake-v1`. In addition to the frozen target and caps, it records both acknowledgments and the selected original capture/run identity hashes, epoch, first sequence, epoch frame start and configuration. Durable `initial-intent.json` binds that header, the exact first original path and its verified report before snapshot or uploader effects. Admission records use the same distinct span schema and preserve full producer delivery reports unchanged.

The shared engine receives an immutable per-invocation policy. It does not mutate module globals, dispatch from an untrusted stored schema, migrate old jobs or reinterpret one adapter's job as another. Whole-capture and fixed-span commands continue to reject this job format, and this command rejects their formats. New span bindings use canonical JSON equality, so booleans and floating-point values cannot impersonate integer identities, ordinals or receipt fields.

Append results separate `published_frames` from `staged_frames`; short inputs may remain entirely in local spool. `accepted_with_eos` means the selected-span upload was finalized, not that downstream analysis succeeded. Results retain `analysis_completion_verified: false`, `capture_completeness_proven: false`, `physical_continuity_proven: false` and `hardware_frame_mapping: UNESTABLISHED`. Original terminal reason, recorder STOP observation and recovered-tail uncertainty remain distinct.

The earlier `recorder_span_status` command supports the fixed v2 span job only. It does not support this v3 layout. Use the separate [incremental span status command](RECORDER_INCREMENTAL_SPAN_STATUS.md) for a bounded read-only observation of a finalized v3 job. Status observation is not inference retry, a native accuracy claim or an atomic server snapshot.

## Recovery and frozen bounds

The underlying [incremental capture admission/recovery rules](INCREMENTAL_RECORDER_CAPTURE_INTAKE.md) continue to apply:

- The intake lock and actual uploader lock remain held through registry verification, remote reconciliation, admission and final checks.
- The registry and authenticated uploader state must describe the same exact prefix, with at most one pending admission. Resume a pending input using the exact latest original path/content; older retries and changed identities refuse.
- Every complete admission is positively fsynced again before registration or upload. An incomplete active spool piece may be reconstructed from its exact unchanged snapshot.
- Explicit `--recover-initial` is limited to a durable, complete span intent, matching original bytes and origin, exact recoverable local prefixes, a pristine uploader and an observed empty strict session. It never infers a missing intent or repairs anything after an uploader checkpoint exists. Missing/changed origin data, conflicting prefixes and ambiguous state are preserved and refused. Recovery retains original inodes/prefixes rather than deleting or replacing evidence.
- Default capacity is 12 logical hours and 192 inputs. At initial start only, `--max-hours` can select 1–48 hours and `--max-inputs` can select 1–192. Later calls may omit those options or repeat their exact frozen values, never upgrade them.
- Aggregate original snapshot WAV bytes remain capped at 8 GiB. Per-delivery WAV validation is bounded at 512 MiB. Each admission remains bounded at 16 KiB. The 32 MiB uploader metadata, 48 MiB owned-state and 12 MiB working-spool limits are unchanged.
- Existing page source-slot reservations may limit tiny segments before the input-count cap. The command refuses rather than sending early EOS to escape that limit.
- Disk reserve checks and cooperative invocation/inspection deadlines remain enforced. Historical original, snapshot and journal validation grows with retained history; append is bounded, not constant-time.

These are finite software limits, not measured 12/48-hour runtime qualification. Neither successful empty-state GETs nor local locks reserve the server against another account/host writer or authenticate a backend incarnation.

## Test evidence and its limits

Tests separately cover nine tiny original files with nonzero sequence/epoch/frame origin through actual strict ASGI/SQLite source admission, including lost successful PUT response reconciliation. They stop before seal/native work and verify no duplicate source or premature EOS. A separate synthetic protocol peer covers complete finalization, lost committed PUT and seal responses, exact finalized replay and truthful selected-span-only output.

A metadata-only case simulates nine 300-second declarations, totaling 45 minutes, to check original-coordinate mapping and capacity arithmetic. It does not ingest 45 minutes of audio. A separate 192-input case uses real tiny files and journals with explicitly scaled synthetic page geometry to exercise the maximum bounded registry. Neither case establishes long-duration/native processing or physical-device continuity.

Adversarial tests cover missing/changed origins, canonical type aliases, cross-schema jobs, terminal fencing, source-slot exhaustion, frozen caps and lock exclusion. Real subprocess `os._exit` cases cover six initial recovery stages and interrupted active inputs after multiple admissions, including exact snapshot-based spool rebuilding on a fresh process restart. These are controlled software crash tests, not filesystem power-loss qualification.
