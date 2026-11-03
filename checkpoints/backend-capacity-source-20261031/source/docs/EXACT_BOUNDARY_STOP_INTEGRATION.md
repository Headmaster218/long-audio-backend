# Exact-boundary STOP integration

The terminal-aware Android producer fixture has three 4-second, 16 kHz mono PCM16 segments: 64,000 frames each, sequence 0/1/2, declared starts 0/64,000/128,000, and close reasons ROTATION/ROTATION/STOP. The final STOP is exactly the configured target size. No empty segment or rewritten published metadata is needed.

The existing backend already accepts this shape. No ingestion module, strict gate, runtime pin, default, EOS policy or wire schema was changed for this checkpoint.

## Checks

`tests/test_recorder_exact_stop.py` covers synthetic exact-full STOP and optionally the actual local Java producer output. Set `RECORDER_EXACT_STOP_FIXTURE` to that output directory to run all five checks. Without the fixture, two cross-language checks explicitly skip rather than claiming the Java path ran.

- Fixed batch snapshots all three deliveries, admits their exact frames through real strict ASGI handlers, and records EOS intent. Repeat after a lost successful PUT response does not duplicate source uploads.
- Incremental calls stage the three segments without EOS. Before STOP, explicit finalize refuses. After full STOP, explicit finalize reaches the native seal boundary; replay does not duplicate sources.
- Historical ROTATION-only batch refuses before HTTP. Merely exhausting supplied files is never a terminal declaration.

These test handlers deliberately stop before native sealing. Separate existing `tools/strict_uploader_smoke.py --recorder-incremental-delivery ...` ran the actual Java output through the unchanged qualified isolated candidate and real native startup, snapshots, uploader, seal, publication and cleanup. It staged 64,000/128,000/192,000 frames over three calls; explicit finalize published one EOS page and three sources. Two native chunks completed (10.5 and 1.5 seconds). One successful published-seal response was deliberately lost and reconciled, and a completed-job retry returned the same result. Source and original input hashes stayed unchanged; shutdown exited zero with owners stopped, lock closed and permit clear.

This is 12 seconds of synthetic digital silence. It is not Android-device, microphone, physical continuity, authenticated producer, FTPS transport, transcription accuracy, global speaker identity or long-duration resource qualification. The earlier candidate acceptance remains narrowly scoped to fresh isolated stores. No old-store migration or production activation occurred. Generated audio, state, reports, models and secrets remain local and are not part of source backup.

The profile planner still uses the conservative legacy eager-rotation model. Its extra-tail reservation is not needed for a terminal-aware exact STOP, but remains valid as conservative planning until producer mode is explicitly selected in that planner. This checkpoint does not infer deployed phone behavior from development source.

The producer source checkpoint is [Android 9a2c04b](https://github.com/Headmaster218/long-audio-recorder-android/commit/9a2c04bf9062548eb2107f7a87353c41180dfcd0). Its terminal-aware constructor is selected by the new CaptureEngine overlay; legacy clients retain the eager behavior.

Independent review passed seven additional actual-fixture checks and a retained-artifact audit. The audit verified 177 source pins, intact v3 history, identical 192,000 PCM frames through originals, snapshots, server sources and derived native work (168,000 + 24,000 frames), SQLite integrity and unchanged original media/database. The repeatable read-only audit is `qualification/independent_exact_stop_audit_20261103.py`. Lost-response and latest-append retry claims rely on the pinned harness assertions and saved result, not a complete retained uploader HTTP transcript. Local RESULT SHA-256: `b46c4dbf92bc5171bc5bbc6b8d00fcb7d6265fa2b8904219991e8fe523460133`.
