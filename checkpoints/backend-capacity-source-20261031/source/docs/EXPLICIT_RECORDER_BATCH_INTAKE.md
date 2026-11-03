# Explicit fixed-batch recorder intake

`python -m audio_backend.recorder_batch_intake` extends the explicit known-session path to **2–8 already available sequential deliveries**, at most two hours total. This is a fixed complete batch, not an incremental watcher or arbitrary long-recording claim. The single-delivery command remains unchanged.

Use a private 0700 parent, a NEW job directory and an existing empty strict-primary-payload-v2 session. Assert exclusive operator ownership of the session/capture and local job, as required by [single-delivery intake](EXPLICIT_RECORDER_INTAKE.md). API origin and authentication rules, default disk reserve and no session creation also remain the same.

```sh
python -m audio_backend.recorder_batch_intake \
  --delivery /private/received/segment-0 \
  --delivery /private/received/segment-1 \
  --delivery /private/received/segment-2 \
  --job-dir /private/intake-jobs/complete-batch-001 \
  --api https://your-backend.example \
  --session-id EXISTING_SESSION_ID \
  --capture-id OPERATOR_CHOSEN_CAPTURE_ID \
  --exclusive-writer
```

## Required declarations

Supply the whole batch in sequence order, beginning with sequence 0, epoch 0, frame 0, START and no previous seam. Every nonfinal segment must end in ROTATION; the last must end in STOP. All must describe one capture, the same run/epoch/configuration and mono16k PCM16 format. Subsequent declarations must be frame-adjacent with trusted-previous-seam declarations and consistent anchors. These checked declarations **do not prove physical continuity or a microphone hardware clock mapping**.

All snapshots and metadata are verified before any media is sent. Missing ordinals, gaps, overlaps, epoch changes, recovered tails, uncertain seams, anchor contradictions and conflicting or repeated ordinals refuse the batch. Error JSON retains safe boundary reason codes, such as `missing_ordinals`, `declared_frame_gap`, and `epoch_boundary_unmapped`. No silence is inserted and no epochs are joined automatically. Warning codes, such as absent anchors, remain visible even when declared adjacency is accepted.

## Bounds and recovery

The fixed manifest binds every original path, verified delivery, stable snapshot path, target and order. Inputs cannot be added, removed, reordered or substituted during resume. The source-slot reservation plan is checked before remote mutation against the existing append uploader's 16-slot page limit. Aggregate WAV input is capped at 512 MiB and the configured uploader frame/byte limits also apply. The manifest has a 16 KiB bound; unusually long paths can therefore be refused even for eight deliveries.

Preflight shares a 120-second cooperative deadline across delivery inspection, snapshots and sequence checks. Upload uses one shared deadline across all inputs and finalization, rather than resetting it per segment. Final verification has another shared 120-second deadline. These cooperative limits are not hard OS deadlines or resource-usage measurements.

The existing append journal owns deterministic pieces, page publication and EOS. A resume verifies its exact manifest prefix and resumes the last incomplete input first, including reconstruction of a missing owned spool piece from its unchanged snapshot. Earlier clean inputs are not unnecessarily replayed before that active input. Every uploader invocation still verifies registered originals and remote capture history.

If finalization was requested, resume goes directly to its saved EOS intent. It never invents another capture or retries session creation. A lost successful source or seal response is reconciled with the same persisted keys and requests. The default service, runtime qualification pins and experimental session-idempotency patches are unchanged.

Successful output says `accepted_with_eos` and `analysis_completion_verified: false`, plus the segment count, frame count, source/page counts and declaration warnings. Errors remain unresolved with possible prior effects. Preserve the whole job and all originals, inspect blocked worker/remote state, then use the same arguments with `--resume`. Missing or incomplete initial manifest/journal requires manual investigation; there is no automatic repair/adoption. No automatic inference retry is performed.

## Test scope

Tests use actual strict ASGI admission/SQLite without native lifespan to check source response loss, fixed-manifest refusal, interrupted active-input spool reconstruction, source-slot bounds and visible boundary refusal before HTTP. The native smoke's repeated `--recorder-batch-delivery` options additionally support a total of at most 30 seconds. That isolated experiment uses real qualified native processing, discards one actual successful published-seal response, resumes the same complete job, and separately checks completed chunks/EOS/cleanup. Generated recordings, database state, journals and reports must remain local, not in source backups.

## Observed bounded native run, 2026-11-03

A separate Android producer fixture generated 4-second, 4-second and 3-second silence segments through the production spool/writer/metadata codec. This new batch was passed locally to the explicit intake command implementation; unlike the earlier single-delivery experiment, this batch was not itself sent over FTPS. Same-epoch sequence/frame/read-anchor declarations were checked, not promoted to hardware continuity proof.

The isolated accepted runtime processed all 176,000 frames as three sources in one session/page, completed 10.5-second and 0.5-second native work chunks, and published EOS. One real successful published-seal response was deliberately discarded; the saved seal replay recovered. A subsequent complete-job resume returned an identical acceptance result. Originals remained unchanged and the service exited cleanly. Silence is not accuracy or speaker-identity evidence, and no production rollout is implied.
