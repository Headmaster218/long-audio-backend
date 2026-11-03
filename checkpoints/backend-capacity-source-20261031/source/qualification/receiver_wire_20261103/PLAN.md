# Receiver FTPS wire qualification plan

Status: prepared only. A parent-reviewed exact source checkpoint, reviewed harness, final hash manifest, and explicit start are required before launching the server.

## Scope and provenance

- Android source: 2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9. The copied assembled source manifest has 256 files; every byte/size/SHA-256 was checked against that manifest and the exact Git blob in the clean pinned checkout. Source is copied into the isolated workspace; historical fixtures/evidence stay untouched.
- Backend baseline: 8204e1523c3119c65a82d0e3b94efac78257f799. Reviewed receiver checkpoint: 91b9f8125bf4d4bac43188e180d8d46b7fefb998. Use the fresh restored source under /tmp/receiver-published-20261103; all 168 backend Python/helper files were matched byte-for-byte to that exact Git commit. Final source SHA-256 hashes are frozen before execution and checked on both success and failure.
- Production FtpsNetwork and core Java sources are compiled unchanged with the preexisting host Android API fixtures and WireClient. This is actual JVM TLS/control/data sockets, not Android routing, trust-provider, lifecycle, SDK, physical-device, deployment, or ASR qualification.
- Official PyPI fixture dependencies are separately installed into the isolated app/build target only. Archive sizes and SHA-256 pins are checked before install and before execution. Download: 263,282 bytes. Initial installed size: 1,194,207 bytes. No system or native-profile changes. Observed setuptools84.2.1 differs from historical manifest84.0.0; this build-tool difference is recorded alongside the actual matching fixture/runtime versions.
- The backend API peer is explicitly model-free. No native inference, transcript evaluation, finalize, export, or real service is performed. Runner outcome is one exact admitted source, awaiting explicit finalize.

## Phases

1. Generate one-day synthetic local CA/server key and per-process Java trust store. Start one pyftpdlib TLS server bound only to 127.0.0.1 on an ephemeral port.
2. The unchanged production Java FtpsNetwork uploads its synthetic 16,428-byte WAV plus metadata and marker using ordinary STOR. A fixture callback limits receives to 32 bytes and holds marker processing after the first bytes are flushed. The handle is still open; the marker is genuinely partial in raw staging.
3. Configure the receiver to a private raw staging root and a separate empty owned inbox. Configure the runner from the actual local Java-produced manifest, whose origin must later equal the fully validated published delivery origin.
4. Run one real production runner.watch invocation, with a model-free protocol peer. Across at least two observed scans of the empty published inbox, require no peer calls, intake actions, job, output, or runner/inbox file-content/identity changes. The ordinary raw partial STOR remains held and outside the inbox.
5. Release the marker, wait for all actual STOR handles to close and the original Java client to verify its readback. The same watch must still be running, with no calls or intake, because transfer completion alone is not publication.
6. A harness observer briefly pauses original inbox inventory while the receiver's shared publication lock is actually held. Start publish_completed in another thread and observe its exclusive publication-lock request blocking. The inbox stays empty and the same watch stays running. Release the observer: original validation/atomic publication and original scan continue. No validation or production return values are changed by the pacing observers.
7. Require the same watch to finish with status awaiting_finalize, inputs=1, actions=1, analysis_completion_verified=false. Verify all three published byte sequences and semantic report equal the actual wire delivery, and file inodes differ from raw staging.
8. Repeat publication: already_published with exact frozen inbox/runner/job inventory unchanged.
9. Perform a later ordinary TLS STOR truncating raw audio. Frozen selected input must remain unchanged and publication must refuse. Then ordinary TLS STOR restores audio and supplies valid changed metadata/run identity, recomputing metadata checksum and marker hash without changing the original attempt. Publication must refuse existing-attempt conflict and preserve the selected original.
10. Restore exact original raw files by TLS STOR. A fresh Python process reopens publication and runner state: already_published, awaiting_finalize, inputs=1, actions=0, network_requests=0. Require no frozen state or content changes.
11. In a separate private receiver setup, create an existing empty final target and require fail-closed preservation of its inode and empty contents. The independent product reviewer additionally covers racing empty collisions, subprocess contention, mutation, and interrupted publication.
12. Stop/reap server and all owned subprocesses, check recorded loopback listeners are closed, delete only the freshly generated key/trust directory, verify final source hashes, and write the raw private report. Failures are preserved; no automatic rerun.

## Bounds and cleanup

- CPU affinity exactly CPU 3; nice 19; process-tree RSS <=384 MiB.
- An outer timeout sends SIGTERM at585 seconds and SIGKILL15 seconds later (600 seconds total). The main process catches SIGTERM/SIGINT into its try/finally, which reaps every owned new-session child group and removes keys. Cleanup or final source verification failure produces a nonzero exit.
- Whole suite <=600 seconds; server <=60 seconds; Java client <=30 seconds; finite per-step and lock deadlines.
- <=5 loopback sessions (expected 4); <=256 FTP commands; <=2 MiB application bytes; <=8 MiB run output, excluding copied source/dependencies; >=5 GiB free disk.
- At most two simultaneous listeners, control and passive data, always 127.0.0.1. Expected 10 STOR commands. No delete or rename FTP commands.
- All generated synthetic credentials/key/trust material and raw logs/results live outside Git. Keys/trust are removed in finally, including failures. Remaining raw files are preserved as private evidence. Only source tools/tests and a sanitized aggregate report may enter Git after separate review.

## Run gate

Launch with PYTHONOPTIMIZE unset so fixture assert guards remain enabled. The harness refuses execution without --execute-reviewed-fixture and an exact --pins manifest matching all relevant current source hashes. --record-pins is read-only preparation and starts no server. The parent must provide explicit start after final checkpoint and review.
