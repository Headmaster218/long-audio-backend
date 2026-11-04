# Logical span workflow result, 2026-11-04

## Observed result

Three contiguous public-speech originals totalled 144,000 mono 16 kHz PCM frames
(9 seconds). The first 48,000-frame ROTATION original was admitted and explicitly
closed as a logical analysis span. The next original remained unmodified and
bound as successor. The first export completed, then the new advance command
created exactly one next session from the saved intent. The remaining 96,000 frames
were admitted and exported through the genuine final STOP.

Independent verification compares decoded native analysis WAV bodies, not only
reported durations: concatenating both completed native tasks equals the original
144,000-frame PCM exactly. Original delivery bytes and decoded producer metadata
are preserved. The first export records logical_analysis_boundary,
recording_complete=false and recognition_continuity_verified=false. The original
frame mapping ends at 48,000 and resumes at 48,000, ending at 144,000.

Advance issued one POST. Repeating advance after the next intake completed
validated the same link with zero HTTP requests/writes. Both exports contain
observed completed primary analysis; one and six nonempty text units respectively.
This count is not an accuracy score, and segmentation differs from the earlier
single-session 9-second sample. Recognition-context continuity is not established.

The native supervisor exited 143 after graceful SIGTERM, with no recorded child
processes remaining. Sampled native peak RSS was 777,695,232 bytes; this is an
observation, not a hard memory guarantee.

## What was fixed during the test

The first native export attempt exposed a missing logical policy propagation in
export recovery. The original completed analysis session was preserved. After
fixing that path and adding a real-ASGI recovery-export regression, the same job
was resumed and exported; no replacement capture was used to hide the failure.
Independent review also found and fixed authority anchoring/durability and
protected-export-destination gaps before accepting the final workflow.

The source-client commands used the installed Python 3.13.5 / dynamic SQLite 3.46.1
environment. The native service remained the previously verified installed
candidate; native processing code did not change in this round. Updated client
modules were loaded explicitly from source. This is not yet an all-new-wheel
installation proof. The final resume checked client source hashes before/after.

## Separate interrupted-task recovery

An earlier harness stopped another 3-second task while processing was pending.
Restart correctly showed failed/manual rather than claiming completion. One
explicit retry on that exact chunk in the same session completed native processing,
preserving original audio and 0–48,000-frame EOS identity. It saved one transcript
segment and shut down cleanly. See RECORDER_OPERATIONAL_RECOVERY_ZH.md for the
supported path and the tested cursor-zero limitation. This is separate evidence,
not automatic restart recovery or a substitute for the two-span result above.

## Scope that remains open

- One cloud host with a small public sample; no physical Android device or user's
  server was accessed or qualified.
- Logical successor currently must be in the same frozen inbox. Receiver routing
  is separately verified, but an automatic cross-generation analysis transition
  has not been implemented or accepted.
- Explicit bounded commands, not atomic rollover, hot routing, automatic retries,
  indefinite scheduling, storage reclamation or long-duration endurance.
- No claim of cross-speaker identity, overlapping-speech quality, stable semantics
  across inference-session boundaries or arbitrary-server deployability.

## Regression scope

The frozen source passed 230 related logical-boundary, next-session, initializer,
capacity, runner, status, export and export-recovery tests on the installed
Python 3.13 environment (one existing Starlette deprecation warning). A separate
source-level Python 3.12 run passed the same 230 controls. Independent review ran
121 focused controls and checked the native artifacts, export hashes, mappings
and session link separately. These are focused regressions, not a full repository
pass, a new installed-client wheel qualification or a long-audio quality result.

Ordinary direct logical export currently requires a canonical absolute job path;
use the documented absolute paths. Its early guard is less permissive than older
relative-path callers. No integrity check is relaxed to accommodate that case.
