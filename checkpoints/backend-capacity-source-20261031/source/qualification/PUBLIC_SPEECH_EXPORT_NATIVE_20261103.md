# Public-speech completed-span export qualification, 2026-11-03

## Result

One independently reviewed and explicitly confirmed native launch passed. A fixed
nine-second public AMI speech span went through the unchanged host Java recorder
core, incremental selected-span intake, real native analysis, completed-status
verification, and two fresh GET-only transcript-export processes. The service
produced one completed 144,000-frame work chunk and **five nonempty primary ASR
units**. Both private JSON/TXT exports and completion manifests are byte-identical.
This establishes usable local export of actual recognized public speech, not just
a successful silent-input lifecycle.

The executed source was checkpoint `3a3bcfc8dd792ae549c33931a3baf55e40b1803a`.
The candidate was extracted directly from that published source tree: 888 files,
14,017,676 bytes. Its sole difference is the already accepted two-literal runtime
patch in `native_owned_cell_store.py`. The production runtime pin, main branch,
models, model settings, admission gates and normal service lifecycle are unchanged.
No existing service database was reused and there was no native retry.

## Speech, ASR and output usability

The fixed input and license/provenance are described in
`PUBLIC_SPEECH_EXPORT_FIXTURE_20261103.md`. It is AMI Consortium / University of
Edinburgh EN2002a Mix-Headset, 1667 through 1676 seconds, under CC BY 4.0. This is
public audio replay with genuine host-producer metadata and synthetic observation
timestamps, not Android, microphone, hardware-timing or original AMI recorder
metadata evidence. Nine one-second deliveries retain original producer sequences
2 through 10, epoch 7, frames 32,000 through 176,000 and final `INTERRUPTION`.

The real ASR backend is **SenseVoiceSmall int8 on CPU**, with the unchanged English
capacity profile and `ctc_speaker_turns` strategy. Native Nemotron-3 Q8 diarization
also ran. The speech was not suppressed as nonspeech. Two retained native primary
packets/core outcomes cover the actual source PCM, and all five exported primary
units bind exactly to current canonical ASR database rows and those receipts.
There is no synthetic text injection or optional MOSS transcript substitution.

Local run outputs are retained outside the source tree under
`backend-public-speech-export-native-smoke-20261103/run/export/`:

- `transcript.txt`: 1,167 bytes, readable timed primary transcript with uncertainty
  and speaker-scope notices. It contains five nonempty text units.
- Independent verifier source: `701b351dc772a35c39add64ec6dd07829b9b23afb0e2afeac307e1577693585e`.
- Independent verifier controls: `417c7cfb45b2861f20f52da73380be7eaba031b04df7b67709436858bd4f033a`.
- `export.json`: 10,534 bytes, exact original ASR content, canonical identity and
  hashes, span-relative times, conservative frame envelopes, selected-span input
  mapping, explicit completion observation and uncertainty fields.
- `complete.json`: completion manifest that commits the artifact pair and hashes.
  Consumers must require this manifest before treating a newly written pair as
  complete. Directories are mode 0700 and files are mode 0600.

The repeat export is in sibling `export-repeat/` and matches all three files
exactly. Each export process made 14 GET requests and no mutations. It observed
one revision-bound transcript page and verified completed status before and after.
The remote read is bounded and repeatable in this run, not an atomic database
snapshot or a guarantee against future remote changes.

Times are relative to the selected nine-second span. Native CTC start anchors
are retained; interval ends are estimated, not forced aligned. Frame envelopes
round outward around those original timestamps, and additional printed decimals
do not establish additional measurement precision. Word-level timestamps are not
invented. Local speaker labels remain scoped to their original chunk/window;
they are not person identities. Two units have no speaker assignment.

`complete_content=false`, `accuracy_verified=false`, `speaker_identity_valid=false`
and unavailable word alignment remain explicit. A completed native chunk does not
prove every spoken word was recovered. Unknown gaps outside the selected span
remain unknown; selected-span EOS does not prove whole-capture completeness.
The fixture's fourteen human-reference words and one outer-boundary-crossing
annotation do not constitute an accuracy score. No raw hypothesis or reference
text is included in this Git report.

## Independent checks and resources

The independent saved-byte audit opened both the jobs and native-receipt SQLite
stores only on copies after the real service stopped. Original database and
sidecar byte hashes, sizes, mtimes and ctimes remained unchanged. It verified
canonical ASR identity, retained primary packets, exact source/core PCM coverage,
source receipt linkage, original delivery inventories and exact reconstruction
of the readable TXT from the JSON. It did not merely trust the harness success
flag. Historical wait receipts are saved evidence, not an independent re-execution
of the original waits.

All eight child phases have successful original-Popen wait receipts: first,
second, finalize, repeat, status, status-repeat, export and export-repeat.
The native service and outer launcher exited 0. Every shutdown predicate was true;
the worker stopped, stores were safe, the lock was closed, permits were clear,
and no cleanup or late committed-claim error remained. Intake exercised a clean
process exit/reopen and exact delivery/seal replay. It did not test an abrupt
crash or restart of the native service.

Measured launch wall time: **15.829 seconds**. User CPU: 15.242 seconds; system CPU:
1.682 seconds; total CPU: 16.924 seconds. Peak sampled aggregate RSS was
**844,361,728 bytes**. The recorded post-run envelope, including focused test
artifacts, occupied 10,633,216 allocated bytes; free space was 59,735,465,984 bytes.
These are observations, not instantaneous kernel quotas.

Unchanged limits were: 300-second service Gate, two CPU affinity slots, 6 GiB
sampled RSS cap, 5.5 GiB free-disk floor, 16.25 GiB sampled artifact cap, 480-second
outer timeout with 120-second TERM grace, and the existing 90-second normal
service shutdown wait. All 2,157 pinned model/runtime assets (733,515,621 bytes)
were rehashed before launch with no mismatches. Actual runtime fingerprints and
filesystem device 27 matched. All 888 candidate source pins remained unchanged.

Source-only qualification included 43 focused candidate controls, the previously
reported 328 affected aggregate checks, 103 independent exporter review cases and
146 fresh published-source checks. These overlapping suites are separate runs,
not an additive test count or a full repository pass. The final independent
verifier also passed 22 focused positive/negative controls (11 base and 11
closed-copy audit cases). Reusable verifier and tests are in
`qualification/public_speech_native_audit_20261103/`; raw audit JSON and database
copies are deliberately excluded from source. Candidate comparison is against
the pinned published Git baseline, so later documentation changes are not
misrepresented as changes to the executed candidate.

## Reproduction

Use a new candidate directory directly under the compatible workspace and a new
run/data directory. Extract the checkpoint's
`checkpoints/backend-capacity-source-20261031/source` tree and apply only
`qualification/accepted_candidate_20261103.patch`. Verify the exact candidate,
runtime, asset and fixture pins and retain independent plan approval before a
native launch. Do not adapt the runtime qualification to a different environment
or widen limits to force success.

Prepare the public fixture with `generate_public_speech_java_deliveries.py --help`:
use the pinned recorder assembly/checkpoint, the full EN2002a WAV hash in the
fixture report, `--source-start-frame 26640000`, official AMI attribution and the
CC BY 4.0 URL. Generation uses a fresh output directory, eleven seconds of source
PCM and nine selected deliveries. It must not rewrite producer metadata.

Run the bounded smoke with its existing `--assets` manifest and all nine
`--recorder-incremental-span-delivery` paths, adding `--export-transcript`.
Use the unchanged outer `timeout --signal=TERM --kill-after=120s 480s` wrapper.
The exact reviewed argv and launch script are retained locally in the launch
envelope. The smoke requires nonempty real primary text for this qualification;
a structurally successful empty export is not reported as speech success.

For a separately running compatible service and completed v3 span intake journal,
the exporter CLI is:

```text
python -m audio_backend.recorder_incremental_span_export \
  --job-dir <completed-intake-directory> --api <service-url> \
  --session-id <explicit-session> --capture-id <explicit-capture> \
  --output-dir <fresh-private-directory> --exclusive-writer \
  --deadline-seconds 120
```

This is read-only against the service and produces a private local artifact pair;
it does not create sessions, upload audio, rerun ASR or silently resume intake.
The qualification service has already stopped, so its finished private exports
remain usable without restarting it or reopening its original database.

## Exact evidence hashes

- Fixture receipt: `735895855f8de14f84992216ee7b28fb7359ce74fb30a86aed6ed5e93e80dd56`.
- Frozen launch plan: `16b76fa1e5546f845cf84f668d29d76546ee6487d2d8af13a070b6ee3e3b9bf8`.
- Independent prelaunch review: `f5955f5c94b740b34074349191b87e184d2f0e7bd712a1cff3063fad430224c1`.
- Original outer-wait receipt: `9fc3fe0eb95585fb9971ccfae1fbba74289c70b116994eae8b312d8e3b14d3a4`.
- First export wait: `2d2a1a49f8fb9f030f8f84939b86a8b8397e703af1f913050855a4419facbf29`.
- Repeated export wait: `e4eeaafc2badaa1f3ed9d0c5dae27c994a1db4dd8ada3ae133d7fc556cb2e9ee`.
- Terminal cleanup: `5e67902edec46f5447ca7dac6ee53c387f7a1af6d1d44d268d8c55febdc56099`.
- Native run result: `88ad4f9d3771b95be279b877d8d015eff3e84d0fb6a833422e972d6a04e24e92`.
- Resource summary: `b22f280ce91c8d16a51fcd0b3323147970a3cddcbea17320441b3d959761fd09`.
- Timing receipt: `f0e439de6855c2d4dbf280775d880ae15b8ca8cebbc344600d4f109b70a5818d`.
- Independent closed-copy audit: `420ad2a6a15b044dc41969ec1f3dad2d385ec86b31bddf08338b1821913c307e`.
- Independent verifier source: `701b351dc772a35c39add64ec6dd07829b9b23afb0e2afeac307e1577693585e`.
- Independent verifier controls: `417c7cfb45b2861f20f52da73380be7eaba031b04df7b67709436858bd4f033a`.
- `export.json`: `7086e298fd150d9836a43be858d84fe0a5d8398c42902b538dd1d96b81257c3f`.
- `transcript.txt`: `9440731a17d8ae26d4baf6fba39129c1b01d6bea5b6b1dbb88ca60906c2997ce`.
- `complete.json`: `8613a53b8685242efc9d1cd54a16cd5fcf654cd59f8e1189bbf7212dd777f7ff`.

Only source, tests and aggregate evidence are eligible for backup. Public audio,
reference text, raw transcripts, models, databases, local receipts and raw logs
remain outside Git. This finite single-page result does not qualify acoustic
accuracy, speaker identity, missing-word recovery, physical continuity, long
recordings, multiple pages, production rollout or a model/default change.
