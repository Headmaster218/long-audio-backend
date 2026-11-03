# Recorder span: short real native publication smoke, 2026-11-03

## Scope and observed result

A fresh isolated strict-primary-payload-v2 service accepted a selected recorder
span, published its EOS page, and completed both actual native work chunks. The
input was **11 seconds of generated digital silence**, not microphone audio.
This establishes this short integration path, not transcription accuracy,
physical continuity, recorder provenance, capture completeness, device behavior,
or long-duration/capacity performance. Nothing was deployed and no existing
service or database was modified.

The fixture has three PCM16 mono 16 kHz deliveries:

- Original sequences 11, 12, 13, epoch 7, original start frame 100003.
- Lengths 64000, 64000, 48000 frames; original end frame 276003.
- Original close reasons ROTATION, ROTATION, INTERRUPTION.
- Backend span-local starts 0, 64000, 128000 and end 176000.
- EOS retains `selected_span_only`; `recorder_stop_observed` is false.
  Before/after gaps remain unknown and hardware frame mapping remains
  UNESTABLISHED. No STOP or missing prefix was manufactured.

The real service published one EOS page with exactly three sources. Native
chunks of 168000 and 8000 frames (10.5 and 0.5 seconds) both reached `completed`
before shutdown. Both native receipts report
`native_diarization_only_asr_skipped`: generated silence exercised native
diarization; speech recognition was skipped. This is not evidence of ASR
execution or word accuracy. One successful published-seal response was deliberately lost;
the existing uploader reconciled the frozen intent. Two completed programmatic intake calls with `resume=True`
returned exactly the original result and added no sources. They
replayed the **same frozen seal** once each, retaining its path, body and key.
This is not a zero-HTTP-mutation claim: the existing uploader intentionally
replays the seal because GET does not expose its idempotency key and revision.

Original generated WAVs, delivery markers and metadata remained byte-identical,
as did the frozen source pins. The importer still reports
`analysis_completion_verified: false`; native completion above was established
separately by the smoke's completed-chunk observation, not by changing the
importer's meaning.

## Runtime, lifetime and resource boundaries

The service used the existing recovered SenseVoice/Nemotron configuration,
English decode language and original model/runtime pins. A direct-workspace
isolated candidate contained only the already accepted two-literal runtime
qualification patch from `qualification/accepted_candidate_20261103.patch`.
It binds `ACCEPTED_RUNTIME_BASIS_20261103.json`, SHA256
`27e9f806302f01da31d2f3bd2be0de327f195ccc63d0fd93c6b18ccb760a5589`.
The production profile, selectors, verifier, schema, model settings, budgets and
feature defaults were not changed. The earlier experimental-only patch was not
used. All 2157 pinned runtime/model/library/interpreter assets, 733515621 bytes,
matched, and the accepted runtime fingerprint/build/device-27 checks passed.

Independent prelaunch review accepted the exact candidate and bounded launch.
The existing Gate retained two-CPU affinity, a 300-second cooperative work
budget, 6 GiB sampled aggregate RSS cap, 5.5 GiB free-disk floor, normal shutdown
wait and descendant checks. The outer timeout was 480 seconds plus a 120-second
TERM grace; it did not fire.

Observed successful run:

- Elapsed 9.465 seconds; user CPU 6.833 seconds; system CPU 0.928 seconds.
- Peak sampled aggregate RSS 366813184 bytes (about 349.8 MiB).
- Post-run service evidence allocation 3379200 bytes across 39 files.
- Original outer wait and original service Popen wait both returned zero.
- Worker stopped, instance lock closed, permit clear, store safe, all five
  shutdown predicates true, no shutdown exceptions and no remaining descendant
  accepted by the existing Gate.

These resource checks are sampled/cooperative, not instantaneous kernel quotas.
The saved receipts support original wait/stop history; an independent later
saved-byte audit cannot re-observe those historical process waits.

## Failure retained before the successful run

The first isolated attempt stopped after an overly strong **test-harness**
assertion demanded no mutating HTTP requests on completed resume. All three
results matched and the selected sources/page had published, but the existing
uploader legitimately sent exact frozen-seal replay requests. The harness was
corrected to allow only the exact saved seal and to refuse new PUTs, new pages,
changed keys, paths or bodies. The product uploader was not changed.

That first attempt exited 1 and stopped/reaped the service cleanly with service
exit 0. Its early shutdown interrupted the 10.5-second chunk while the 0.5-second
chunk was queued. **It is not native-completion evidence.** Its complete local
artifacts and independent failure audit were retained (failure audit SHA256
`2f0831d21dd7625d2100a60ac5b2774b74d09df0917877b17f5bd0c4ce010c90`). No inference was retried
in that session. The successful attempt used a new isolated candidate, output,
database and explicit smoke session after a second independent launch review.

The initial unit-test invocation also retained a test-bootstrap failure: 63
passed and two subprocess fixtures could not import pytest. Pointing those test
children at the already restored test-tools directory resolved it without
changing the runtime or product. All 66 focused tests then passed, including
new exact-seal replay negative controls. Native execution used `-I -B` and
explicitly unset PYTHONPATH/PYTHONHOME.

## Reproduction and evidence

Source-only helpers:

- `qualification/recorder_span_synthetic_fixture.py` creates the fixed generated
  fixture under a new private directory, with hashes for every original file.
- `tools/strict_uploader_smoke.py --recorder-span-delivery PATH` accepts repeated
  explicit selected inputs totaling at most 30 seconds. Other input modes are
  mutually exclusive. Existing service/model/resource/cleanup gates remain.
- `tests/test_strict_uploader_smoke.py` covers fixture declarations, input-mode
  refusal and exact frozen-seal replay guards; `tests/test_recorder_span_intake.py`
  retains the admission, resume, refusal and process-exit controls.

Before native launch, restore the exact accepted runtime/model assets, verify
all pins and selectors, and use a fresh isolated candidate located directly
under the workspace expected by `capacity_config.py`. Apply only the accepted
candidate patch there, never to the working product profile. Generate the fixed
fixture, freeze candidate/asset manifests, independently review the bounded
launch, and invoke the existing smoke with its three delivery directories and
asset manifest. Session creation is one explicit isolated **smoke-harness** POST;
the product importer does not create sessions or retry session creation.

Successful result SHA256:
`7bb439aef5ce784efafd3d721df5a8675a9fba9bc7c2e1f7b7e196c67d59dae5`.
Successful terminal receipt SHA256:
`5e67902edec46f5447ca7dac6ee53c387f7a1af6d1d44d268d8c55febdc56099`.
Frozen audio_backend/tools source-pin manifest SHA256:
`2d138631d806f07f7884f27241c923e76cfa0597700205044ad01757830b6ea9`.

Independent final saved-byte audit passed. It inspected only a separate SQLite
copy and independently checked unchanged originals, metadata/snapshots, saved
source identity and exact PCM reconstruction across all three sources and both
native chunks, full range coverage, two exact seal replays, runtime/assets/source
pins, and consistent cleanup receipts. Its own nine focused audit-control tests
also passed. Audit source is
`qualification/independent_recorder_span_audit_20261103.py` (SHA256
`032b8fa7f9760e32efe386655d435b0ae57a9cc48988a874c3258e825fbed55e`).
Final audit report SHA256:
`65f3ce6645e8db9491a064b13ec6f3a4bcfbab3d9bcd8e04a48fb3db1ce55fc5`.
All generated audio, SQLite files, model/runtime assets, raw logs and raw run
reports stay outside source control. Only reusable source/tests and this bounded
result summary are intended for code backup.
