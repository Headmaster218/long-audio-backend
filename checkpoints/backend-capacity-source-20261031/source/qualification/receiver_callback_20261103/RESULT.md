# Automatic receiver completion: real FTPS callback qualification

**The first authorized live run passed, independently audited.** A receiver integration can now publish a completed recording automatically from its successful upload lifecycle callback, while holding incomplete, failed, repeated, and restart-uncertain attempts out of the owned inbox. The harness did not call publication after the client exited.

This qualifies the server-neutral product adapter bound to a real loopback TLS server fixture. It does not install a production FTPS service or qualify native ASR, a physical Android device, deployment, finalize, or export. The backend protocol peer is model-free.

The original [prepared plan](PLAN.md) and prior `receiver_wire_20261103` evidence remain unchanged.

## Sources and preflight

- Android production source: `2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9`. The unchanged production `FtpsNetwork`, core classes, and existing host fixtures compiled from 55 Java sources with source/target 8 and `-Werror`.
- Accepted backend baseline: `90cbacbcea68e8944ad80e864719632f1aa3ac17`. The combined reviewed adapter/harness backup was `e716403eb6d7b8a03ec8096699cfde07440015ff`, verified on the remote before launch. A separate restore contained 928 files, 14,641,903 bytes; every file matched its Git blob, SHA-256, and executable mode.
- Product adapter SHA-256: `be093da17069898f2be09652de406c28353c01b0618f78444cb82470bfb399f2`.
- Qualification runner SHA-256: `295ff9e884b4e7bb27dbb88fbed154158736e0e4f487b96e13a5c8e8a1b0b450`.
- Callback server SHA-256: `3e2986251469e898fac80dc456ee3dd572f8f21a3e2a3a8861c9e191eafc63e4`.
- Helper SHA-256: `f023a759b96f4347903b7c17faf457608975c705ce87f21e6e728744f9b09238`; source-only safety tests SHA-256: `b65eb3d3d1c590c88d7b702ea6c969da36b14bd2621dcab94832552d3cb75094`.
- Python 3.12.14 used the exact previously validated interpreter, with `PYTHONOPTIMIZE` unset, `-B`, and explicit `PYTHONPATH` pointing to the existing test-tools directory. All 1,304 test-tool files, totaling 17,116,775 bytes, matched their prior unchanged manifest.
- Full execute-phase import-only preflight recorded 364 imported modules and started no server or key generation. Independent prelaunch review checked all 528 source/dependency pins, every imported module, all restored files, and all test-tool hashes without mismatch.
- All 96 copied fixture dependency files matched the previous qualified fixture. Versions remained pyftpdlib 2.2.0, pyOpenSSL 26.4.0, pyasyncore 1.0.5, and pyasynchat 1.0.5. No download or install was needed. Runtime setuptools remained 84.2.1, the previously disclosed difference from historical 84.0.0.
- The source-only safety suite passed 7/7, independently repeated: mismatched pins prevent execution; injected pre-server exception and the installed SIGTERM handler unwind and preserve a failure report; owned children are reaped; disk, deadline, and output bounds refuse unsafe progress. Two initial test cases encountered the small `/tmp` disk floor before reaching their intended checks. Their test-only disk mocks were corrected; production guards were not relaxed and no service was started by those tests.

## Actual lifecycle and admission observations

1. The fixed synthetic authenticated account mapped to one private staging root, separate from the published inbox. The server's filesystem hooks used the adapter to create a new attempt and obtain each exclusive-create bounded stream before opening a STOR destination. No previously existing attempt was adopted.
2. The production Java client uploaded ordinary STOR data over actual control/data TLS sockets. Audio and metadata closed successfully first. The marker was held after 32 bytes had been flushed, with its actual writer handle still open. Across two scans by the same production watch, the inbox remained empty, no peer calls occurred, and runner/inbox/job contents and identities were unchanged.
3. Releasing the marker produced its actual successful closed-file callback. Only after all three successful member callbacks, zero live handles, and zero pending writers did the product adapter publish automatically. A repeated callback carrying the same original writer object returned the exact same published result without another publication attempt.
4. That same watch admitted exactly one source, with one action and four polls, and stopped at `awaiting_finalize`. The model-free peer received 13 reads and zero writes. Analysis completion, capture completeness, and physical continuity remained unproven.
5. A later ordinary STOR targeting the sealed attempt was rejected before opening or truncating its audio file. Raw, published, runner, and admitted state remained unchanged.
6. A second production Java upload was deliberately interrupted after its marker's first 32 bytes. The actual incomplete closed-file callback returned `failed` with `completion_transfer_failed`, with zero pending/live writers. Its repeated failed callback was exact and idempotent. The marker bytes were retained in raw staging, never published. The Java client performed only its planned read-only reconciliation, which failed; no retransmission occurred and its local source remained unchanged.
7. A separate local crash probe used the adapter to close audio and metadata successfully, then wrote and fsynced a structurally valid complete marker while its handle remained open. It exited without invoking the marker's success callback or adapter cleanup. Complete-looking files did not publish. A fresh process refused all three old attempts, published, failed, and crash-uncertain, with `completion_existing_attempt_not_adopted`. This is lifecycle/crash-fence qualification, not an additional wire transfer. Fresh runner state still reported one input, `awaiting_finalize`, zero actions, and zero requests.

The first authorized live launch succeeded and was not retried. The deliberate interruption and crash were planned negative cases, not hidden failed launches.

## Exact selected input and identity

The selected Java producer WAV, raw staging WAV, published WAV, and admission snapshot WAV all contained 16,428 identical bytes, representing 8,192 mono 16 kHz frames with `STOP` closure:

- WAV SHA-256: `b060690a88c5afb4b32056d1bb3086d3ea694b8f682e733896e24fc642c6980a`.
- The 419-byte transport metadata was identical in raw, published, and admission snapshot locations: SHA-256 `00d30bd5587fd86d2ab47e20c86dd979098963550a16fa76fc08382c6b497df4`.
- Its embedded 312-byte manifest exactly matched the Java producer's original manifest: SHA-256 `7df83701dd1eb98796817f1b9a4f64d9376678c5834cc31d9ae2e9183104c42d`.
- The 358-byte marker was identical in raw, published, and admission snapshot locations: SHA-256 `beceebea917d50b410e6e2ca226c67d139ad9c3d82cc3a3acd7ae6b4c853bba1`.
- Each of the nine raw/published/snapshot member files had link count 1. For each member, all three inode identities were distinct. The published and admitted copies were not hard links to a mutable raw file.
- The runner's selected published path, admission ordinal 0, delivery description, and snapshot identity agreed. Duplicate callbacks, refused STOR, incomplete upload, crash probe, and process restart did not change that selected input.

These checks establish byte and producer-declaration consistency, not trusted hardware provenance or authentication. `authentication_proven` remains false; the trusted receiver must authenticate and map its account before using the adapter.

## Bounds and cleanup

- Exit 0, elapsed 12.714 seconds. CPU affinity was CPU 3, nice 19. The outer launch used TERM at 585 seconds and KILL 15 seconds later; server/client caps remained 60/30 seconds.
- Peak process-tree RSS: 164,896,768 bytes, below 384 MiB. Original run output before the separate audit: 856,916 bytes, below 8 MiB. The 5 GiB free-disk floor held.
- Four control TLS sessions, 17 data TLS channels, 102,220 application wire bytes, and seven STOR commands: six admitted members and one pre-open refusal. No FTP delete, rename, append, or resume commands were used.
- All seven owned subprocesses were reaped with exit 0. The watch thread closed and the server had zero live writer handles. All 19 recorded loopback listener ports closed; their cleanup records were independently checked.
- The newly generated synthetic CA/server key and per-process Java trust directory were removed. No permanent credential, system/JDK trust change, daemon, or deployed service was created.
- Final source verification was unchanged. Raw logs, transferred bytes, control transcript, and private run state remain outside Git. Only this sanitized report and reviewed source tools/tests are eligible for backup.

## Private evidence hashes

- Run report: `80a4bd08884a6ec3a7c503920807c633040e56690fd11de687573d3ec15f6283`.
- Server callback/command transcript: `a5f6537ce8282cb9940483636f5bade8bf292ca3968731a388522fe20188c806`.
- Frozen source/dependency pins: `c27d857759e813829b02ee62b672550256c38353bd42f64c8e92cca8f9399397`.
- Full import preflight: `7968f4ad58cb9d7ec05d5076c738b1fbd454eb5ee439317a04a467b9e43dbbb2`.
- Restored-source verification: `a62cab410db023ed56f89b26437fa536c4c24630c8b8b643962b27b52e0b8309`.
- Existing test-tool manifest: `b2d9b5180a9376738f66067e64e8feee0ba7b7704727f9da6e26cca4c623a121`.

Independent terminal audit passed. It rechecked callback order, exact selected copies, refusal/restart behavior, all 528 pins, 364 imported modules, 1,304 test-tool files, all 928 restored Git-blob identities, and cleanup. Final independent audit SHA-256: `f01b7c372dc597ac45fb26bbee242f58b160686a362a0537de2d63d4d02e74ac`.

## Remaining boundary

The automatic callback path is real within the reviewed TLS fixture. The product module is server-neutral: an actual deployed receiver must bind every writable open and real closed-file callback, retain exclusive ownership, supply trusted account scope, and preserve the separate raw/published roots. The adapter never adopts or automatically retries old attempts. The receiver/operator must inspect uncertain publication before any explicitly authorized retransmission; existing strict publisher reconciliation remains separate.

This result does not qualify a running production receiver installation, actual backend HTTP service, native inference, ASR/transcript quality, real Android routing/trust-provider/lifecycle behavior, microphone capture, physical device, finalize, export, or deployment.
