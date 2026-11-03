# Nine real Java-producer deliveries: bounded host qualification

## Scope and source

The recorder source was freshly restored from
`Headmaster218/long-audio-recorder-android` commit
`2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9`, with a clean detached checkout.
The pinned repository's own assembly helper applied its seven ordered overlays.
All 256 assembled files were independently compared with their exact committed
source paths, Git blob IDs, byte lengths and SHA-256 hashes. The assembly receipt
SHA-256 is `6434fa619850c576503ea442b3c679662b6fcd5720d4501cabd6d8c939d73a21`.
Previously changed build snapshots were not used as producer inputs.

`qualification/generate_nine_java_deliveries.py` compiled the 37 unchanged
production Java core files plus `qualification/NineProducerDeliveries.java` with
Java 8 source/target and warnings-as-errors. Runtime outputs record the hashes of
all 161 class files. Key production-source SHA-256 hashes:

- PcmSegmentWriter.java: `b23781f5469854abef2c74ff4bc55a2adaf905650275be4e7ea3129d7997f381`
- DirectorySpool.java: `a2bc786f90a09a51728bc30f0ec5486e6dab2b417cfed5ddc22aeeb519fdc27c`
- FtpsDelivery.java: `e92567ad1f62e2aa5a0435e5172d47a81b532c6dc644b86b17f1abe7e9f1d73c`
- SegmentMetadata.java: `f73d9e75ea2aab795b5ae6f5946711e5d0eb0c74db1e1be7622fb0f76e1a8427`

The harness feeds generated digital-silence PCM and synthetic read observations
through the production terminal-aware writer and filesystem spool. It generates
11 one-second segments, then selects the last nine. Production FtpsDelivery
encodes every transport envelope and marker. Python only reads/verifies these
outputs; it never generates, rebases or re-encodes the wire metadata.

## Producer result

The Java harness passed 78 assertions. The selected deliveries retain original
sequences 2 through 10, epoch 7, and original frame interval `[32000,176000)`.
Each has 16000 frames. The first eight close with ROTATION and the last with
INTERRUPTION. The preceding two real producer segments remain local and are
not submitted. Every envelope's local metadata is byte-identical to its original
spool manifest, and backend transport/metadata readers validate all nine.
A second independent compilation and generation reproduced all 161 class-file
hashes and all 27 selected-delivery file hashes exactly.

The selected nine seconds map to backend-local frames `[0,144000)`. No producer
START or STOP is invented; both outside-span gap durations remain unknown.

## Integration result

`qualification/qualify_nine_java_incremental_span.py` runs four separate
processes against the actual strict ASGI handlers and one private SQLite
namespace, without entering application lifespan or starting native workers:

1. Admit and retry the first four original deliveries locally.
2. Reopen the backend and intake journal, retry delivery four, then admit and
   retry the other five. Staging uses only GET and sends no automatic EOS.
3. Explicitly finalize up to the pre-seal boundary. Nine original source WAVs
   are actually admitted. One deliberately lost successful PUT response is
   reconciled through GET without repeating the write.
4. Reopen again, retry finalization with zero new PUTs, then directly replay all
   nine exact source requests against the real backend. All are recognized as
   duplicates with unchanged source identities. There remains one session, one
   open page, nine source rows and zero recording-work rows.

A separate, explicitly synthetic protocol peer exercises successful publication
and finalization, lost-PUT-response reconciliation, an interruption after a
durable piece midway through input five, fresh-uploader recovery, all nine exact
delivery retries, and repeated finalization. The completed v3 journal has nine
archived inputs, one archived page, and no active input/page or pending spool.

Finally, explicitly synthetic completed-work rows are inserted into a separate
test database, and the new observer reads them through actual ASGI GET routes.
It returns nine exact original-to-local mappings, INTERRUPTION with
`recorder_stop_observed=false`, unknown external gaps, and selected-span-only EOS.
Two observations are identical. Producer originals and intake journal contents,
mtimes and ctimes remain unchanged; backend database bytes also remain unchanged.

## Evidence and limitations

Local fixture report `run-01/FIXTURE.json` SHA-256:
`dbe7d41f903b0634e471e1ab7a1e0e2b54764f8468f426abed841e2662b2eabe`.
Combined final-code integration report `integration-03/RESULT.json` SHA-256:
`bf745a67ba0c8c31934202859bc70a38628be49caa7b671a6e19b17751c669aa`.
GET-only status report `integration-03/status.json` SHA-256:
`045e76ac846608d020d7400ae74d4ade96257c90373af11df51d2a339987916c`.
The report pins all backend Python source bytes and found no source changes
during this run. Later source changes require rerunning relevant checks.
The final status implementation includes the pure allocated-byte bound check;
its source SHA-256 is
`f70965a57c50d710b7c22fa8a92409f723becf3673dd6d9b6485f6ece38bb556`.
The qualification script SHA-256 is
`b90287d9bac4618f300d85ec1714362e5e7a04b5a2d7bf8112288045b500ee0b`.

This is actual production-Java execution on a host JVM, not an Android runtime,
microphone, real-device or physical-continuity test. Strict source admission is
real; completed analysis rows and publication success are explicitly synthetic.
No native inference, acoustic quality, live service, FTPS network, production
deployment or long-duration performance is qualified. Nine one-second inputs
fit one backend page; the run does not exercise multi-page native processing.

## Reproduction

Use a fresh checkout at the exact producer commit above, then run its committed
`scripts/assemble-source.py --ref <commit> --output <fresh-assembly>`.
From the backend source directory:

```sh
python qualification/generate_nine_java_deliveries.py \
  --producer-checkout <clean-pinned-checkout> \
  --producer-commit 2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9 \
  --recorder-source <fresh-assembly> --output <outside-source>/run-01

python qualification/qualify_nine_java_incremental_span.py \
  --fixture <outside-source>/run-01/FIXTURE.json \
  --output <outside-source>/integration-01
```

Use the existing test environment providing the backend dependencies and pytest
needed by its test helper modules. Each output path must be new. Java sources
and the two Python qualification scripts plus this report are the publication
allowlist. Audio, envelopes, class files, database/journals, logs, local receipts,
and generated reports remain outside the source tree and must not be committed.

A subsequent [short native completion qualification](NINE_JAVA_PRODUCER_NATIVE_COMPLETION_20261103.md)
uses these same nine producer deliveries with the isolated accepted runtime.
That separate run establishes native diarization and actual GET-only completion
status for nine seconds of silence; ASR remains explicitly skipped.
