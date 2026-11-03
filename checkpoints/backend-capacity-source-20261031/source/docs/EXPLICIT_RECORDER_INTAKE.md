# Explicit recorder delivery intake

This opt-in command imports one verified, complete local recorder delivery into an **already existing strict session**. It is not an automatic FTPS watcher, session creator, server-side producer identity registry, or recorder hardware qualification. The default service and runtime pins are unchanged. The separate experimental session-request patches remain unapplied.

## Supported first slice

- One complete initial START → STOP delivery: sequence 0, epoch 0, frame start 0, no trusted previous seam.
- PCM16 mono 16 kHz, at most two hours, with verified envelope, marker, WAV/PCM hashes and semantic metadata.
- An explicitly chosen existing empty strict-primary-payload-v2 session. The internal processing mode remains `legacy`, even though strict policy is required.
- An exclusively controlled local job and one exclusive operator/writer for the target session/capture. There is no server reservation to enforce this assumption.
- HTTPS, or HTTP only on loopback. An API key may be supplied through `AUDIO_API_KEY`; it is not written into the intake manifest. A target URL/session is not an authenticated backend incarnation ID.

Other formats, multiple recorder segments, gaps, recovered tails and epoch changes are refused. In particular, this command does not resample 48 kHz or stereo recordings or invent an original-frame to normalized-frame mapping.

## Run

Create a private parent directory (mode 0700), retain all original delivery files, and use a new job directory inside that parent:

```sh
python -m audio_backend.recorder_intake \
  --delivery /private/received/r-delivery \
  --job-dir /private/intake-jobs/job-001 \
  --api https://your-backend.example \
  --session-id EXISTING_SESSION_ID \
  --capture-id OPERATOR_CHOSEN_CAPTURE_ID \
  --exclusive-writer
```

`--exclusive-writer` asserts that no other operator or process writes this session/capture or job directory. Do not use it when that is not true. The private parent and job must not be symlink paths or readable/writable by other users. The source itself remains in place and is revalidated on each invocation.

The command creates a verified local snapshot, a non-overwriting source/target manifest and a separate existing uploader journal before sending media. It uses the uploader's bounded deterministic source pieces and saved seal/EOS requests. It never retries or creates a session. It does not send recorder metadata as trusted hardware provenance.

The normal disk reserve is 5.5 GiB, the maximum uploader working spool is 12 MiB, and the uploader deadline defaults to 12 hours (`--deadline-seconds` can lower it). These are refusal limits, not measured worst-case guarantees. Snapshot validation has its own finite 120-second deadline. A long-lived failed/paused worker is not automatically retried by this command.

## Completion and unresolved outcomes

Exit 0 reports `status: accepted_with_eos`. This means the journal and remote capture reconcile, uploaded frames match, and original/snapshot bytes still verify. It deliberately reports `analysis_completion_verified: false`. It does not promise accurate transcription, known speakers, physical continuity or successful downstream analysis.

Exit 2 reports `status: unresolved`, a bounded reason and safe error category, and conservatively marks possible remote effects. Categories distinguish HTTP authentication/access/conflict/limit errors, failed/paused/cancelled workers, deadline with pending-or-uncertain work, and transport/local-I/O uncertainty. HTTP status codes are retained without server bodies. A network loss, interrupted local fsync or failed verification can happen **after** server effects. Preserve the original delivery and the entire job directory. Inspect the target's page/chunk status and any blocked worker before taking further action. The CLI never prints server error bodies or API keys.

For an intact job and unchanged source/target, rerun exactly the same command with `--resume`. Resume rechecks the immutable binding, snapshot and journal, and rejects foreign pages, extra capture history or changed source keys. Lost HTTP source responses are reconciled through the existing deterministic journal keys. Rejected remote identity is not written into the journal.

If the manifest, snapshot or uploader journal is missing/incomplete, resume refuses. There is no automatic repair or adoption of an existing capture. Do not solve this by creating another capture or deleting state; first establish what the original target committed. An interruption before the first uploader journal exists may therefore need explicit operator investigation even if no request was sent.

The service's page-creation API has no unique per-request key. A persisted create intent and matching page can recover a lost response, but cannot distinguish a concurrent foreign writer creating that exact empty page. Exclusive ownership is required, not proven.

## Evidence boundaries

`tests/test_recorder_intake.py` exercises actual authenticated strict ASGI admission and SQLite without starting native inference. It tests exact resume, lost successful PUT responses, target/source/snapshot/journal refusal and rejection without additional writes. It does not simulate publication and call it a successful service run.

The existing bounded native smoke additionally accepts `--recorder-delivery` for a complete delivery of at most 30 seconds. It starts an isolated genuinely qualified service, creates one test session without retry, exercises the actual intake function and its completed resume, then independently checks native chunk completion, EOS publication, and clean shutdown. Generated media, state, reports and databases remain local and must not be uploaded with source.

## Observed bounded integration, 2026-11-03

The actual FTPS/TLS loopback receiver's 11-second Android-produced silence delivery was passed through this command's implementation against the previously accepted isolated runtime candidate, without changing the default runtime pins. The initial run and completed-job resume returned the same one-source/one-page acceptance; the service separately completed 10.5-second and 0.5-second chunks and published EOS. Both source/snapshot verification and clean shutdown succeeded. The experiment is silence, not transcription accuracy, and JVM host API fixtures are not an Android device qualification.

Normal source, default native runtime pins and main remain unchanged except for this opt-in client, its test harness extension, tests and documentation. The candidate session-idempotency and atomic-initialization patches are still unapplied.
