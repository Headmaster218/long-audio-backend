# Finite local inbox watch: genuine STOP native qualification

## Result

One independently reviewed, explicitly confirmed short native run passed. A single
foreground `recorder_inbox_runner.watch` invocation admitted nine genuine host-Java
recorder deliveries over real loopback HTTP, observed a stable missing-marker
publication twice, finalized their genuine STOP, waited for native processing and
exported five nonempty primary ASR units. Independent status and a second fresh
GET-only export passed; JSON, TXT and completion manifest match byte for byte.

This extends the earlier one-shot reconciler qualification with one finite watch,
not an installed service or unbounded daemon. The explicit test harness created
one fresh isolated empty session; the runner created none. No deployment, remote
provisioning, device access, credentials changes or FTPS receiver was involved.

## Genuine producer provenance

The input is the same fixed public AMI Consortium / University of Edinburgh
EN2002a Mix-Headset PCM interval documented in
`PUBLIC_SPEECH_EXPORT_FIXTURE_20261103.md`, under CC BY 4.0. The source interval and
PCM selection were not changed for this run. Selected PCM SHA-256 remains
`824962e469c1b5c3969804b2846b1428fe4a194a1744d5599aa595ae7f69af73`.

The qualification generator's explicit STOP option calls the unchanged production
`PcmSegmentWriter.stop()` after its last full segment. All 256 assembled source
entries were verified against clean producer commit
`2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9` before compilation. The eleven genuine
one-second outputs include two preceding segments; the last nine are selected.
Every selected WAV retains exact public PCM, and every embedded envelope metadata
record equals its original producer spool manifest. No close reason, envelope,
ordinal, epoch or timestamp was rewritten after production.

New STOP fixture receipt SHA-256:
`a95146cfa3ee9b01c70f1a031352297bd5a79f9de290fabe5f58e6296f5d73e0`.
The nine selected inputs preserve epoch 7, sequences 2–10, original frames
32,000–176,000, eight ROTATION declarations and final STOP. Total duration is
144,000 mono 16 kHz frames, or nine seconds. Host observation timestamps remain
synthetic; this is not Android/microphone or hardware timing evidence.

## What the finite watch actually exercised

- Configuration used the first fully valid selected delivery and known fresh
  session. Configuration performed no HTTP or intake. Eight subsequent genuine
  deliveries arrived after watch startup.
- The second input arrived as a stable directory containing WAV and envelope,
  without its publication marker. The unchanged validator returned successfully
  twice across a normal watch pause, about 0.104 seconds apart. Both snapshots
  show exactly 13 HTTP requests and zero writes; full before/after runner and
  intake inventories, including bytes, hashes, mtimes and ctimes, are identical.
- Only after those observations did the harness publish the final marker. Every
  subsequent input was supplied after the preceding admission. Controlled
  publication stages exact bytes outside the inbox, then uses Linux
  renameat2(RENAME_NOREPLACE) for atomic, non-overwriting directory or marker
  publication. This is not ordinary FTPS STOR behavior.
- The same watch process admitted all nine inputs and invoked explicit authorized
  finalize only after the genuine STOP was admitted. The finalize event records
  count 9 and the unchanged last delivery, sequence 10, epoch 7 and STOP.
- Native processing completed and the same watch invocation exported privately.
  Its bounded run used 51 polls, 41 helper/actions, 432 requests, 18 writes and
  478,301 response bytes. The mutations were one page POST, nine source PUTs and
  eight frozen seal POSTs. The watcher never issued POST /sessions.
- Ordered request events include one terminal return/error for every request;
  the 57 expected errors were GET 404 observations, not lost or retried writes.
  Export events isolate its final 14 GET requests from earlier finalization.
- Independent fresh status/export children were GET-only and did not alter
  original delivery or intake files. All selected original bytes and paths remain
  unchanged. The three child processes and the launcher were successfully reaped
  using their original Popen objects.

The observer wrappers are qualification-only: they call the unchanged marker
validator, intake and export functions, return their original results, rethrow
errors, and restore the functions in finally. They do not bypass product checks,
change HTTP replies, insert transcripts or replace a native model. Focused tests
cover passthrough, restoration, error trace and no-overwrite publication.

## Native output and independent evidence

Real SenseVoice primary ASR and native Nemotron diarization ran under the existing
model configuration. The retained private export contains five nonempty primary
units. TXT is 1,167 bytes, JSON 10,521 bytes and completion manifest 718 bytes;
all files are private and their lengths and SHA-256 hashes agree with the manifest.
All three repeat-export files are byte-identical.

The independent audit reverified producer/source/runtime pins, ordered arrival and
watch events, the exact selected-path ledger, all 453 child requests, the 18
finalize-only writes and the safe normal shutdown evidence. It bound all five
exported primary units to canonical database rows and two retained native packets
and core outcomes, including exact source/core PCM and source receipt linkage.
It opened only closed database copies and verified that original evidence,
database and sidecar bytes, sizes, mtimes and ctimes remained unchanged. Saved
original wait receipts are historical evidence, not independent re-execution of
those waits.

All 30 independent verifier controls passed: 19 event/bounds tamper controls and
11 native/export controls. Their scope is separate from the candidate's 38
focused harness controls. No raw text, media, models or databases are in Git.
Reusable verifier sources and controls are in
[`native_inbox_watch_audit_20261103/`](native_inbox_watch_audit_20261103/).

- Independent audit JSON SHA-256:
  `b8dafc05dbf78fac2e7cb0d564b9b1f850db1556f615ea55d37b787ed16c7331`.
- Audit controls log SHA-256:
  `0d82aa40fba42b0887ac758dfa2b20f20356c49d87bc5d27dce11cc4a5e93407`.
- Executed independent verifier SHA-256:
  `9a6d6e1b18e5d0a668cce4a6fdda63c9263c3286f824271157959ef6ff264f6e`.

ASR text was observed, not accuracy-scored. Complete content, word alignment,
global speaker identity and whole-capture completeness remain unverified. The
watch's conservative top-level analysis verification flag is not changed; the
independent completed-status and export documents carry completion evidence.
STOP is a producer declaration, not proof of physical continuity. Backend time
starts at zero for this explicitly selected span; original gaps remain unknown
and EOS means selected-span closure only.

## Frozen source, lifecycle and resources

Executed source checkpoint: `1b2537d60e2a1f7afe69bcc66eef35a043d340b7`.
The fresh candidate has 907 pinned source files, 14,339,537 bytes, with only the
already accepted two-literal isolated runtime patch applied. All 907 candidate
files were rehashed unchanged after the run. Production runtime pins, models,
settings, service lifecycle and admission guards remain unchanged. No old database
was reused and there was no additional native run or native-service restart.

- Frozen launch plan SHA-256:
  `0086afe8aa0d1ac918019a090469b4bb8a328a5945c9a686920a0c82e2df45e9`.
- Launcher SHA-256:
  `844c7c18c63eb4b53158faef98d84f2bf54062837c44170fba512635c3a2c1bd`.
- Independent prelaunch review SHA-256:
  `a91b6ea0debf213c910c94295220c5da0d6810768377fe78e213c6b67df4ac39`.
- All-file candidate pins manifest SHA-256:
  `a6afb76c35d120580e76ed6e125e21bfba0b19e2d997cce5d65a25ff7ff551b2`.
- Executed harness SHA-256:
  `608819db228249bb5d8da88a8dc6caa572bed109c841a530f031acbe838f5861`.
- Accepted runtime patch SHA-256:
  `3a97845bf13b00e402ce73cb9990ce72f2696b9d82254cf442fbfde18a46afe8`.

All 2,157 model/runtime asset files, 733,515,621 bytes, were reverified. Runtime
fingerprints and filesystem device 27 matched the accepted qualification. The
candidate's 38 focused model-free controls passed. This was not a full-repository
regression or long-duration test.

Unchanged limits: 300-second Gate, two CPU affinity slots, 6 GiB sampled aggregate
RSS, 5.5 GiB free-disk floor, 16.25 GiB sampled artifact cap, outer 480-second timeout
with 120-second TERM grace, and normal 90-second service shutdown. The watch used
one 120-second deadline, at most 512 polls at 0.1-second spacing, 256 actions,
1,024 requests, 512 writes and 256 MiB response bytes. SIGINT/TERM cancellation and
exceptional child cleanup remained bounded; those cancellation paths were not
exercised by this successful native run.

Observed wall time was 20.913 seconds; user CPU 21.772 seconds and system CPU
3.368 seconds. Peak sampled aggregate RSS was 907,423,744 bytes. Before the
closed-copy audit the envelope occupied 8,949,760 allocated bytes with
59,634,290,688 bytes free. These are sampled observations, not kernel quotas.
All launcher, service and child waits exited zero. Every shutdown predicate
passed: worker and stores stopped safely, lock closed and permit cleared.
The separate process-absence check found no remaining candidate Python process.

Raw audio, reference text, transcripts, databases, compiled classes, models and
local run/fixture evidence remain outside Git. The reusable scenario and limits
are in `LOCAL_INBOX_WATCH_PLAN_20261103.md`; reproduction requires a fresh exact
candidate and independent plan review plus explicit launch confirmation.
