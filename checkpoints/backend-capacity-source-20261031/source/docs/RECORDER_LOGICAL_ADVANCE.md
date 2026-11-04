# Advance one explicitly bound logical span

After the old logical span is finalized and its primary export is locally verified, this opt-in command creates exactly one next session using the successor and next-session intent frozen in the logical-boundary receipt. It does not choose a different destination, delivery, session label, capacity or output path.

```sh
python -B -m audio_backend.recorder_logical_advance \
  --state-dir /private/span-a/runner \
  --acknowledge-create-next-session \
  --exclusive-writer \
  --acknowledge-span-local-closure
```

The old logical receipt, both hash anchors, original/successor bytes, completed export receipt and artifact hashes must validate. The export manifest must explicitly bind the same logical boundary. Missing analysis/export completion stops the command; it never assumes a pending span completed.

The existing initializer performs at most one non-idempotent session POST, with durable intent before it. A lost response with no confirmed creation receipt refuses future automatic POSTs. Confirmed creation with unfinished local setup can resume through the initializer's exact existing recovery rules. A returned session identity equal to the old session is refused, never accepted as a new link.

If the next initialization is already complete, the command independently validates its exact request/template, first original selection and confirmed receipts without HTTP. This also works after next-span intake has started. It does not call initialization again against an already-running job or make a new session because an old response was lost.

The link consists of the old immutable boundary's next-session intent and the exact confirmed next initializer receipts. No old original, result, header or admission is moved or overwritten. The next session starts at local frame zero, anchored to the verified successor's producer identity, sequence and frame start. The command prepares the next runner but does not upload it; continue the existing bounded run/watch flow against that exact next state directory.

This is a crash-resumable, serialized, one-transition operation, **not an atomic cross-session transaction or an unattended continuous scheduler**. It preserves the same-inbox restriction of the logical-boundary command. Stable FTPS routing and receiver-generation management are separate concerns. Original PCM frame accounting can be checked across linked spans; speaker identity, recognition accuracy and transcription context across sessions remain unverified. `recording_complete=false`, `recognition_continuity_verified=false`, and `atomic_rollover=false` remain explicit.

Normal CLI output contains hashes, counts and semantic flags, not private paths or session labels. Keep all private initialization, boundary and export artifacts. Do not delete uncertain state to force another POST.
