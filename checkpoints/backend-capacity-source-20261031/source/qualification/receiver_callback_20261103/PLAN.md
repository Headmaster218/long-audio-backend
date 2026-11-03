# Receiver lifecycle callback wire qualification

Prepared only. No server may start before independent source/pin review, a source backup, and the parent's explicit start.

## Scope

This is a new isolated qualification, leaving `receiver_wire_20261103` and its original private evidence unchanged. It uses unchanged Android production `FtpsNetwork` at `2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9`, the already verified official pinned TLS fixture dependencies, and the new server-neutral lifecycle completion adapter. Host Android fixtures do not qualify a real Android device. The backend protocol peer is model-free. No native ASR, deployment, finalize, export, real account, or permanent trust/credential changes are allowed.

## Required observations

1. An authenticated fixture owns one explicit staging subtree, a disjoint published inbox, and all writer starts/closes. Register a writer before the real server opens/truncates a STOR destination. Reject a new write before opening when the adapter refuses it.
2. Actual successful and incomplete pyftpdlib receive callbacks supply the original writer identity only after their file handle has closed. The completion adapter itself automatically publishes after all three successful members and zero live writers. The parent harness never manually publishes after a client exits.
3. Hold a real marker upload after 32 bytes are flushed, while the handle remains open. At least two scans by the same production watch must produce no input, job mutation, or peer requests.
4. Release that marker. All three successful lifecycle callbacks and zero live writers must precede automatic publication. The same watch must admit exactly one byte-exact independent snapshot with `awaiting_finalize` and no claim of analysis completion.
5. A repeated successful callback for the original writer is idempotent. A subsequent ordinary STOR for a sealed attempt must be refused before truncation, leaving raw and selected input bytes/inodes unchanged.
6. A second production Java upload suffers an actual marker transfer interruption after 32 bytes. Its incomplete callback must not publish, and its read-only reconciliation must fail. There is no successful-marker synthesis.
7. A separate local process uses the same adapter with a fresh nonce, reports two genuine successful closed members, writes and fsyncs a complete valid marker while leaving its handle open, and exits with `os._exit(0)` before any marker callback. A fresh adapter must refuse every existing published, failed, or uncertain attempt. The fresh production runner must preserve the selected input with zero actions or requests. This crash-fence probe is local lifecycle qualification, not a second wire-transport claim.
8. Record exact callback ordering, closed-handle facts, refusal reasons, wire transcript, source hashes, resource use, and cleanup. Raw evidence and generated keys are excluded from Git. Only reviewed source, tests, and sanitized results are eligible for backup.

## Bounds and launch gate

Exactly one allowed CPU, nice 19, process-tree RSS <=384 MiB, >=5 GiB free disk, <=8 MiB run output, <=2 MiB application wire traffic. The suite uses outer `timeout --signal=TERM --kill-after=15s 585s`, with all cleanup inside the 600-second bound. Server <=60 seconds, Java client <=30 seconds, finite waits, and loopback-only listeners. At most two simultaneous FTPS listeners, with every observed port checked closed at exit. Synthetic CA/server key and Java per-process trust store are removed in `finally`.

Use the exact previously validated Python 3.12.14 interpreter at `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python`, `PYTHONOPTIMIZE` unset, `-B`, and explicit `PYTHONPATH=/workspace/scratch/12ec3121e93f/audio-recovery-test-tools-20261023b`. Check all 1,304 test-tool files against the unchanged prior manifest. Perform a full execute-phase import-only preflight, including the model-free peer and server module, before any run. Record and independently review hashes of every relevant source, helper, Android manifest member, installed fixture dependency, and archive. Pin exact source again at exit.
