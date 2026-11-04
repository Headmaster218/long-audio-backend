# Isolated target runtime smoke, 2026-11-04

## Candidate, not production activation

An independent review accepted a fresh isolated candidate for bounded startup and
short native testing, based on the current-target physical audit and nine real
verifier regressions. The source base is `86aa3768fa61a0bad0a5046440368dd66a16995f`.
Only the two runtime package/evidence literals in CellStore differ. The shipped
product source, verifier, supervisor and resource bounds are unchanged.

The exact two-literal patch is `isolated_target_smoke_20261104.patch`; the basis is
`ISOLATED_TARGET_SMOKE_BASIS_20261104.json`, SHA-256
`5bde13b4a586f53b9d28738bebd8915d3e3c2abf994ade33cef6acb72aaf922e`.
Candidate CellStore SHA-256 is
`5de8a2f51cf6e1b73651fd1758ca4c264a548c44cd7b5e64ea4c80e2b6ffcab6`.
These historical exact-target artifacts are not a generic installation override.
Do not apply them to a different interpreter, package, filesystem or old store.

## Startup and retained failure

The original startup harness expected exit zero after deliberately sending SIGTERM.
The supervisor correctly returned 143 under its documented signal policy, so that
harness result is retained as failed. A corrected harness used an independent fresh
data directory, observed exclusive instance-lock ownership while live, then verified
lock reacquisition after exit 143, application shutdown completion, no forced cleanup
message and no recorded surviving descendants. This startup-only run loaded no
inference and is not model qualification.

## Fresh actual native execution

A separate fresh-store run used the unchanged strict supervisor and strict uploader
against the official public SenseVoice `en.wav` sample. The upload/seal completed;
one 7.152-second chunk (114,432 mono 16 kHz frames) completed, with two nonempty
SenseVoice segments. The source sample SHA-256 was
`eb1eb008904465b74c304aad8342e8c7d3c6e61ffe9f66adcaca9cf0f76a93f4`.
Thirty-three existing native/model/sample files matched their recorded hashes before
launch; the copied model layout retained exact bytes and independent file inodes.

An independent read-only stopped audit checked database integrity and foreign keys,
actual retained packet hashes, native core commits, strict outcomes, source digest,
receipt hash/stat signature and PCM equality. Two primary packets cover contiguous
frames [0,84000) and [84000,114432); packet PCM matches uploaded slices, and uploaded
PCM equals the public sample. All 989 candidate source files remained unchanged
except the reviewed two-literal CellStore patch.

The native run's supervisor also returned expected 143 after intentional SIGTERM;
application shutdown was logged, no forced cleanup was logged, recorded descendants
were absent and the instance lock could be reacquired. These are retained harness
observations, not independently re-observed historical process events. Peak sampled
aggregate RSS was 805,326,848 bytes, not an instantaneous hard maximum.

The native result retains a conservative stale `native_inference_claim:false` field
from the startup template. It was not rewritten; persisted outcomes and the separate
independent audit establish actual processing. Health was captured before processing,
not as a final retention snapshot. Raw audio, recognition text, databases, model
weights, logs and private operational evidence are excluded from this source backup.

This is short cloud execution and exact provenance, not recognition accuracy,
speaker identity, overlap quality, long-duration endurance, power-loss, migration,
physical Android/server deployment or general runtime portability acceptance.
