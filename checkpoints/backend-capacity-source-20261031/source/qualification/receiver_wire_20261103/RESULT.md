# Receiver publication: actual FTPS wire result

**Passed, independently audited.** This is a bounded local synthetic transport and admission result, not native ASR, Android-device, or deployment qualification. The original prepared-only [plan](PLAN.md) is retained unchanged as historical evidence.

## Sources and reproducibility

- Android production source: `2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9`. All 256 assembled files matched their manifest and the exact clean pinned Git blobs. The run compiled 55 unchanged production/core and existing host-fixture Java sources with source/target 8 and `-Werror`.
- Backend baseline: `8204e1523c3119c65a82d0e3b94efac78257f799`; reviewed receiver-publication checkpoint: `91b9f8125bf4d4bac43188e180d8d46b7fefb998`. All 168 restored backend/helper Python files matched that checkpoint's exact Git blobs. The run used the fresh restored source, not a mutable working checkout.
- Reviewed harness backup: `9109bf4a7625f05a7f6890c2a6706b5b724157fa`.
- Main harness SHA-256: `1c74a73d4458fe651baae045758224f46ea14156c9285421fad966c936920dd1`.
- Server fixture SHA-256: `e49c1c003d33c9fbb23f098d885cd3dd1723144d4bf26af059ae79ea6b1d27a4`.
- Original plan SHA-256: `f79d7472a3dc6c1f5f3c18d406961a29a6e4a3ea85743c70c1892d9b1850c2b3`.
- Python: 3.12.14, `/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python`.
- Official hash-pinned fixture packages: pyftpdlib 2.2.0, pyOpenSSL 26.4.0, pyasyncore 1.0.5, pyasynchat 1.0.5. Downloaded 263,282 bytes; initially installed 1,194,207 bytes into the isolated fixture target only. No system packages or native profiles changed.
- Reused runtime: cryptography 50.0.0, cffi 2.1.1, typing_extensions 4.16.0, wheel 0.48.0. setuptools was 84.2.1 instead of the historical dependency manifest's 84.0.0; this build-tool difference was disclosed before execution.

The first approved launch stopped on `ModuleNotFoundError: No module named 'pytest'` before creating a run directory, key, subprocess, or socket. The unchanged model-free peer lives in a test module and imports pytest. That failure was preserved; it was not a wire attempt or an uncertain publication.

For the separately reviewed corrected launch, the existing test-tools directory was explicitly added to `PYTHONPATH`:

`/workspace/scratch/12ec3121e93f/audio-recovery-test-tools-20261023b`

This supplied pytest 9.1.1. Its `pytest/__init__.py` SHA-256 was `ee37885c9583f843390684dfb496907f87a0905128e7a3cf4ad227f49ff34c2d`. Import-only preflight verified every execute-phase import, including the peer and TLS modules, without starting a server. It recorded 158 local module origins/hashes. A separate SHA-256 manifest pinned all 1,304 existing test-tool files, totaling 17,116,775 bytes; manifest SHA-256 was `b2d9b5180a9376738f66067e64e8feee0ba7b7704727f9da6e26cca4c623a121`.

The successful launcher first ran `sha256sum --quiet -c` against that complete manifest, then used:

```sh
env -u PYTHONOPTIMIZE \
  PYTHONPATH=/workspace/scratch/12ec3121e93f/audio-recovery-test-tools-20261023b \
  timeout --signal=TERM --kill-after=15s 585s \
  taskset -c 3 nice -n 19 python -B \
  "$HARNESS/qualify_receiver_wire.py" \
  --android "$ANDROID_SOURCE" \
  --backend "$RESTORED_BACKEND_SOURCE" \
  --pins "$REVIEWED_PINS" \
  --output "$NEW_PRIVATE_RUN_DIRECTORY" \
  --execute-reviewed-fixture
```

All path variables are explicit operator-selected inputs. For a different restored location, run the harness's non-executing `--record-pins` preparation and review the resulting canonical-path/hash manifest before execution. The FTPS server module is resolved relative to the harness, so the restored source tools have no dependency on the original harness directory. `PYTHONOPTIMIZE` must remain unset so fixture assertion guards are enabled. SIGTERM unwinds into cleanup, with 15 seconds reserved inside the 600-second outer limit.

## Observed behavior

1. **A genuinely partial ordinary STOR stayed isolated.** The unchanged production `FtpsNetwork` sent the WAV, metadata, and marker over real TLS/control/data sockets. The server held the marker after 32 bytes were written and flushed, with its file handle still open. Across two completed scans, the same production watch saw an empty published inbox, made zero peer calls, and changed no runner/inbox/job file contents or identities.
2. **Transfer completion alone caused no admission.** After releasing the marker, all STOR handles closed and the Java client successfully verified readback. The same watch remained running with no published input or peer calls.
3. **Publication coordinated with a live scan.** The fixture observer held an actual shared publication lock during inventory. The publisher's exclusive lock request was observed waiting. The inbox remained empty until that inventory was released. Original production validation and atomic publication then proceeded, without replacing production return values or relaxing checks.
4. **That same watch admitted exactly one source.** Final status was `awaiting_finalize`: one input, one helper/action, eight polls. The local model-free peer answered 13 reads and received zero writes. No finalize or export was requested. `analysis_completion_verified`, capture-completeness, and physical-continuity flags remained false.
5. **Exact selected identity was preserved.** The original Java-produced WAV was 16,428 bytes, 8,192 mono 16 kHz frames, with `STOP` closure. Independent audit matched the original local producer WAV and embedded manifest to raw staging, published input, and the admission snapshot. All three files were byte-exact across raw, published, and admission locations, with independent file inodes and the exact selected admission target.
6. **Duplicate and restart behavior was idempotent.** A repeat returned `already_published` without changing frozen inbox/runner/job contents or identities. A fresh Python process reopened both receiver and runner state, returned `already_published` and `awaiting_finalize` with one input, and made zero actions or requests.
7. **Later raw STOR writes could not mutate the selected input.** A truncated audio re-STOR was refused with `wav_size`. A second valid same-attempt delivery with a changed producer run declaration, a recomputed metadata checksum, and matching marker hash was refused with `receiver_existing_attempt_conflict`. The selected published input and admission state remained unchanged. Exact original raw files were restored through STOR.
8. **Existing empty targets were not replaced.** A separate private collision fixture preserved the exact existing empty destination directory and refused publication. The product's independent subprocess review separately covers racing collisions, interrupted publication, source mutation, and lock contention.

## Bounds and cleanup

- Exit status: 0. Elapsed: 9.493 seconds.
- Peak process-tree RSS: 152,481,792 bytes, approximately 145.4 MiB, below 384 MiB.
- Four TLS sessions, ten genuine STOR commands, sixteen data TLS channels, and 86,153 application wire bytes. No FTP delete or rename commands were used.
- Run output before this aggregate report: 634,584 bytes, below 8 MiB. CPU affinity was CPU 3 with nice 19. The 5 GiB free-disk floor held.
- All five owned subprocesses were reaped with exit 0. Both worker threads closed. All seventeen recorded listener ports were closed; independent review also found them absent from the kernel TCP listener tables.
- The freshly generated ephemeral key and trust directory was removed. No persistent credentials, system/JDK trust configuration, service, daemon, or deployment was created.
- All 226 pinned source files, 158 imported local modules, and 1,304 existing test-tool files remained unchanged.

## Independent audit and evidence hashes

Independent review passed with no blockers. Private raw logs, transcripts, generated data, and any key material are deliberately excluded from this source report.

- Run report SHA-256: `69492cb16b30d1a662ff64c97a244a12e967f9bb115bfd788312b591122d7677`.
- Server transcript SHA-256: `0084f2b67a473a33b0dca9a1bb96e7f3632c1cc06fc99b584f22f9c782f97129`.
- Full import-preflight SHA-256: `9a097406430c20268e3e6d19c37b2a5e316988a92dcc428d0f1e2caff68f2e7f`.
- Independent result-review SHA-256: `fd30d0d25eaa3fb37a1abc3b7b308cec4acc1000309f38c3e1549b756efef1aa`.

## Remaining boundary

This qualifies the opt-in completed-delivery publication primitive and its coordination with the production watch using actual loopback FTPS writes. The harness explicitly invokes publication after its server's closed-handle notification. **It does not install or deploy an automatic FTPS server callback.** A deployed receiver must retain ownership of the separate raw staging and published inbox, and invoke publication only after its upload handles close.

The backend peer was model-free. No actual backend HTTP service, native inference, ASR, transcript output, physical Android device, microphone, Android routing/trust-provider/lifecycle behavior, or production deployment was exercised. This result must not be described as end-to-end speech processing.
