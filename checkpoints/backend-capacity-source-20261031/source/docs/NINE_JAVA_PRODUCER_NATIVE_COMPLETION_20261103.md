# Nine Java-producer inputs: actual native completion and v3 status

## Scope and result

A fresh isolated strict-primary-payload-v2 service accepted all nine original
host-Java recorder deliveries through incremental selected-span intake,
published one EOS page with nine sources, and completed one actual native
9-second work chunk. Two fresh GET-only v3 observer processes then reported
`completed`, with nine exact original-to-local mappings and no blocked work.
No completed-work rows, publication replies or native result were simulated.

The native receipt reports `native_diarization_only_asr_skipped`: Nemotron-3
diarization ran and finished through 9.0 seconds; silence bypassed ASR, speaker
embeddings and the event model. This is not evidence of speech-recognition
execution, transcript quality or speaker-identity accuracy.

The input was generated digital silence emitted by the unchanged production
Java recorder core, not microphone audio or an Android-device recording. The
producer checkout, assembly verification and fixture generation are documented
in [the host qualification](NINE_JAVA_PRODUCER_SPAN_QUALIFICATION_20261103.md).
The same immutable fixture was used without regenerating or rebasing metadata:

- Recorder commit: `2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9`.
- Original sequences 2 through 10, epoch 7, interval `[32000,176000)`.
- Nine inputs of 16000 frames each; backend interval `[0,144000)`.
- Eight ROTATION closes followed by INTERRUPTION, with no invented STOP.
- EOS remains `selected_span_only`; before/after gaps remain unknown,
  `recorder_stop_observed=false`, hardware mapping UNESTABLISHED, and capture
  completeness and physical continuity remain unproven.

This is a short, single-page integration result. It does not qualify Android
runtime behavior, a microphone, physical continuity, acoustic quality,
long-duration/capacity performance, multiple pages, FTPS network transport,
deployment or production runtime activation.

## Real intake, replay and observation

The source-only smoke extension keeps the existing fixed-span eight-input
limit unchanged. Its separate incremental-span probe accepts 2 through 32
explicit inputs totaling less than 10.5 seconds. The existing strict service,
model configuration and resource guards remain in control.

1. A first intake process staged the first four deliveries, replaying each exact
   latest delivery. It made 125 GET requests and no remote mutations.
2. That process exited normally. A fresh process replayed delivery four, then
   staged the remaining five and replayed each latest delivery. It made 176 GET
   requests and no remote mutations. This is clean intake-process restart,
   not an abrupt-crash or native-service restart test.
3. A third process explicitly finalized, admitted exactly nine source PUTs and
   published the real EOS page. The helper deliberately discarded one successful
   published-seal response, after the service had acted, to exercise uncertain
   response reconciliation. Its 11 seal calls all used the same frozen intent.
4. A fourth process repeated finalization. Its result matched exactly, no source
   PUT was sent, and its sole mutation was the same frozen seal with unchanged
   path, body and key. This is not a zero-mutation finalization-retry claim.
5. After actual native work completed, two further processes each made exactly
   seven GET requests through the real HTTP service. Their v3 status results
   matched exactly and reported one completed work chunk and one durable optional
   receipt. They changed no original or intake file bytes, mtimes or ctimes.

The completed append-v3 journal has nine archived inputs and one archived page,
144000 emitted/published/completed frames, no active input or page, and no
pending spool. The intake result still has `analysis_completion_verified=false`;
completion was established separately by the actual work observation and v3
status, without changing what intake claims.

## Runtime, resources and cleanup

The direct-workspace candidate differs from production source only by the
accepted two-literal patch in `qualification/accepted_candidate_20261103.patch`.
Its acceptance basis SHA-256 is
`27e9f806302f01da31d2f3bd2be0de327f195ccc63d0fd93c6b18ccb760a5589`.
Production `native_owned_cell_store.py` remains unchanged at SHA-256
`efbd83e985482d47d3337e7290cddfacc680ca77c49452c904c18457211393d8`.
All 2157 pinned interpreter/runtime/model/library assets, 733515621 bytes,
matched before launch. Current runtime fingerprints and filesystem device 27
matched the accepted qualification. No experimental candidate patch was used.

Independent prelaunch review accepted the exact candidate and launch plan.
The unchanged Gate used two-CPU affinity, a 300-second cooperative work budget,
6 GiB sampled aggregate RSS cap, 5.5 GiB free-disk floor and normal service stop
with original-process wait and descendant checks. The outer timeout remained
480 seconds plus 120 seconds TERM grace and did not fire. All six intake/status
children were polled under the same Gate and positively waited.

- Elapsed: 12.092 seconds; user CPU: 9.553 seconds; system CPU: 1.496 seconds.
- Peak sampled aggregate RSS: 381988864 bytes, approximately 364.3 MiB.
- Closed run allocation: 3465216 bytes across 121 files.
- Outer original wait, service original wait and all six child waits returned 0.
- Worker stopped; lock closed; permit clear; store safe; all five shutdown
  predicates true; no shutdown exceptions or surviving descendants accepted
  by the existing Gate.

These are sampled/cooperative bounds, not instantaneous kernel quotas. Saved
receipts support the original wait/stop history; a later saved-byte audit cannot
independently re-observe those historical process waits.

An independent closed-run audit passed. It verified all nine original,
snapshot, archive and admitted-source identities, exact PCM reconstruction of
the 144000-frame native work, nine exact owner ranges, native execution mode,
both GET-only observations and cleanup receipts. SQLite was examined only on
an audit copy. The closed original contained an empty WAL and a 32768-byte
shared-memory sidecar; both were retained and hashed, not removed or treated as
evidence of unfinished work. No original evidence was changed by the audit.
Reusable saved-byte verification is in
`qualification/nine_native_audit_20261103/audit_nine.py`, SHA-256
`443d344b06d53bf67bb9204974cc8369bbdde4492b1413f80e49fa90d9f24d67`.
Its companion `test_audit_nine.py` passed 11 valid/negative audit controls.

## Source tests and reproduction

The focused harness/intake/status suite passed 82 tests. After a final
test-helper wait-race hardening, all 32 final smoke-control tests passed,
including the added race regression. The broader run's only warning was an
existing Starlette/httpx deprecation. These are focused checks, not a full
repository test-suite claim.

Before reproducing, restore the exact accepted runtime/assets and the immutable
Java fixture, use a fresh candidate directly under the expected workspace,
apply only the accepted candidate patch, verify all source/asset/fixture pins,
and independently review the finite launch. Invoke:

```sh
python -I -B tools/strict_uploader_smoke.py <new-outside-source-run> \
  --assets <verified-asset-manifest> \
  --recorder-incremental-span-delivery <original-delivery-0> \
  --recorder-incremental-span-delivery <original-delivery-1> \
  ... \
  --recorder-incremental-span-delivery <original-delivery-8>
```

Use the pinned runtime and the same outer 480-second timeout plus 120-second
TERM grace. Session creation is one explicit smoke-harness POST, never retried;
the product importer does not create sessions. Original audio, metadata,
transport envelopes, SQLite files, journals, models, raw logs and generated
reports stay outside source control. Only reusable tools/tests and this bounded
summary are intended for code backup.

## Local evidence hashes

- Fixture: `dbe7d41f903b0634e471e1ab7a1e0e2b54764f8468f426abed841e2662b2eabe`.
- Launch plan: `f920a1944a03c4d27c2c5e0275104b6356ee85a8999bf19239f0817a8637ae98`.
- Prelaunch review: `d81552261e18baf170c88dd192697ac19850bb5877b1b9dac3fed42a12724fc4`.
- Actual result: `f2229ac262ec53ada539c9e9212766a4be88a660fbd210162ad0dcea3c6baac7`.
- Terminal receipt: `5e67902edec46f5447ca7dac6ee53c387f7a1af6d1d44d268d8c55febdc56099`.
- Run source pins: `fba8e749f3965268439e3201aa67bc29628e58cb1ae1ee4ad77be946b5a5dfb6`.
- First GET-only status phase: `93ed2f75795a49756413e83a2aa6248af27d51bff9b69f5cebb95d32770010bb`.
- Repeated GET-only status phase: `8544af0b7ee0c3aaddc3cb538636b9667179f732d37c97181dccaaa13d0ae502`.
- Independent closed-run audit: `5129d4b50b7e7d80d07e336cb9ad84fce8037d179c0592ae0a2e2614b5ced554`.

The status phase hashes differ because their process IDs differ; their returned
status values are identical.
