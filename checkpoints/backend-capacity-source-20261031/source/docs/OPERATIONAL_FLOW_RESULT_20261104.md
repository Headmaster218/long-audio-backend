# Operational flow result, 2026-11-04

## Product changes

`recorder_ftps_receiver configure` derives the exact existing publication binding,
validates provisioned inputs and creates a new private configuration. No manual
binding-hash Python snippet, hand-written JSON, credential creation or listener is
needed. It refuses overwrite, unsafe paths, contradictory input and uncertain
writes; interruption is nonzero and preserves the file for review.

`recorder_inbox_runner initialize` explicitly creates one session and binds one
selected complete delivery. Durable intent precedes exactly one non-idempotent
POST. Uncertain creation never retries. Confirmed-session resume rechecks identity
and emptiness with GET-only requests. Contradictory partial local state refuses
before any HTTP. Existing configure remains local-only; initialize does not upload.

`recorder_receiver_status` reports stopped-receiver retained counts, configured
remaining budgets and capacity blockers. Its nonblocking existing locks prevent
misrepresenting active transfers as a stable snapshot. No content, deletion,
free-disk or successful future-admission claim is made.

The Chinese `RECORDER_OPERATIONAL_START_ZH.md` joins configuration, foreground
startup, first delivery, initialization, bounded watch, export and shutdown.

## Actual first end-to-end execution

New source clients ran against the previously reviewed installed Python 3.13 /
dynamic SQLite native server. This is not yet a newly packaged complete release.
Actual Android Java core writer/spool/envelope and production FTPS transport were
compiled from the verified 263-file assembly at
`233ed0ce5efd2d7864bfd3fbf4ced8f52a910fcd`. Fifty-six producer/client source pins
were retained. The private host fixture selected nine seconds of public AMI
EN2002a Mix-Headset PCM at frames [26672000,26816000); timestamps are synthetic,
not microphone evidence. Source: https://groups.inf.ed.ac.uk/ami/corpus/;
license: https://creativecommons.org/licenses/by/4.0/.

The actual sequence succeeded:
1. Configure publication and generate receiver config using the new CLI.
2. Check config/capacity, start receiver and reviewed strict backend on loopback.
3. Upload the genuine writer-produced STOP delivery using Android Java FTPS.
4. Initialize one new session using exactly one POST, then bind the received copy.
5. Run bounded watch, finalize only the genuine terminal input, wait for native
   processing and commit the selected primary export.
6. Stop runner/receiver/native and observe capacity without deleting any data.

Independent audit verified exact producer/raw/inbox/WAV delivery hashes, one
persisted session, one completed 144,000-frame chunk, five nonempty canonical ASR
units matching the stopped database, two native packets bound to PCM/source
receipts/outcomes, and both committed export artifact hashes. DB integrity/FKs
were clean and all 176 installed server files still matched its reviewed receipt.
Historical wait codes were runner 0, receiver 0, native 143, with completed shutdown,
no forced cleanup and no recorded surviving children. Sampled native RSS peaked
at 792,637,440 bytes; this is not an instantaneous hard bound.

The watch summary retains conservative `analysis_completion_verified:false` and
its last waiting status `pending`; independent committed export evidence supports
selected primary export success, not complete recording or all analysis quality.
The fixture's printed `/fixture` remote-directory text is stale; the actual
profile uses `/` and the real receiver accepted only its configured root.
No transcript, raw audio, database, credential, private key or model is in Git.

The related combined source regression run passed 209 tests with three existing
library/TLS-test deprecation warnings. This is scoped regression evidence, not a
full repository green claim; historical exact-runtime failures remain documented.

## Remaining delivery gaps

- The default strict runtime still matches a historical exact target. Dynamic
  recognition plus reviewed candidate materialization is not automatic approval
  of a new server. Model provisioning and per-target acceptance remain required.
- This run used new clients and an earlier installed candidate server, on one
  cloud host. It did not prove a fresh whole-release installation or another host.
- It used one terminal span, not an indefinite live stream. Receiver 256 attempts /
  8 GiB raw and publication limits, runner 192 inputs/default 12 hours and explicit
  capture/run/epoch boundaries remain real operational limits.
- There is no demonstrated lossless unattended generation rollover/retention.
  Existing recordings, raw originals, pending receipts and reconnect readback
  cannot be discarded to create capacity. No limits were raised for this work.
- Physical Android capture, remote/private-network interoperability, acoustic
  accuracy, cross-person speaker identity, overlapping speech and long-duration
  stability remain unqualified. A short nonempty ASR result does not establish
  any of these goals.


## Follow-up: fresh complete installation and arriving rotations

A separate fresh Python 3.13 environment now contains the complete newly built
product, native runtime dependencies and FTPS receiver dependencies. All 32 final
dependency versions match the combined hash lock; pip-check passes before and
after product installation. The explicit pycparser 3.1→3.12 resolution is recorded,
not represented as equality with the historical runtime-only lock. pyftpdlib was
built from hash-verified official source. An optional secondary registry metadata
lookup was blocked and was not retried; successful official-index download logs,
actual wheel hashes and installation receipts remain the provenance evidence.

The exact 1,016-file source was independently reviewed after fresh physical crash
recovery (95 changed pages,6,044 journal records) and nine real-verifier controls.
Only reviewed runtime/evidence literals were materialized. All 178 installed
product source files / 4,210,484 bytes matched before and after execution. Both
receiver/runner clients and strict server ran from that one installed environment
with -I -B, without source-path injection.

Actual Android core code produced three contiguous 48,000-frame deliveries using
its terminal-aware writer: ROTATION, ROTATION, STOP, for 144,000 total frames.
The first published delivery initialized one session. The next two actual FTPS
transfers arrived while watch was running; both observations were recorded true.
Watch processed all three inputs, authorized terminal closure only at STOP and
committed the selected primary export. This is a short continuous-arrival check,
not long-duration endurance or physical recording evidence.

Historical receipts show runner 0, receiver 0, native 143, no recorded remaining
native children or forced cleanup; sampled native RSS 796,082,176 bytes.
Stopped capacity observation reports three retained attempts and three published
deliveries, 292,073 logical file bytes in each raw/publication accounting, 253 remaining
attempt/entry slots. It makes no deletion-safety or future-admission guarantee.
The same 209 product regressions also passed on this Python 3.13 dependency set.

- Combined lock: b1247c91baebe92b39be9250723822cf27ff6b217692755160f7b7632fa6845c
- Frozen source manifest: 91f4e815c10b1eed6020c4dd885bd24fee41a17f4bc9159d06c881249ec1caa3
- Reviewed basis: 724380448a23dd5dbc8cb3505b184118c4b8921eab5a59f057df70c59d95d8c6
- Materialization receipt: ef476e53e390f4acd2f10ccca75a59cd20d57403bbae666cae1bbc024268025c
- Installed wheel: b4fe91f4e89f6b38698be77f0a3a387f240e124011e61448703fac49ca60d614

This closes the earlier mixed source-client/old-installed-server packaging gap for
this exact same-host candidate. Default shipped admission remains unchanged.
The repository's default main branch was independently observed at cf1541d;
operational work remains on development/backend-capacity-source-20261031 and its
explicit restoration entrypoint. No default-branch promotion, deployment to the
user's server, general runtime activation or production release is implied.

Independent stopped audit of this installed three-arrival run confirmed exact
producer/raw/inbox and runner selections, one session creation, concatenated PCM
matching the completed nine-second backend chunk, five nonempty canonical ASR
units matching the database, both export artifact hashes, two native packets and
source/strict-outcome bindings. Integrity/FKs were clean. Stored transcripts are
not reproduced here. This is not an acoustic-quality assessment.
