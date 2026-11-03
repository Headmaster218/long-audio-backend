# Explicit local delivery-to-publication experiment

`tools/recorder_delivery_smoke.py DELIVERY NEW_PRIVATE_OUTPUT --assets ASSET_MANIFEST`

This opt-in test connects the actual producer delivery bytes to existing backend
components. It does not enable an intake daemon, change any readiness flag, or
establish production recorder session ownership. The selected runtime must
already pass the unchanged strict qualification gates.

The test accepts one complete initial capture only: sequence/epoch/frame start
zero, START boundary, STOP close, no predecessor seam, PCM16 mono 16 kHz, at most
30 seconds. It validates the producer envelope and inner metadata, creates a
fresh exclusively controlled snapshot, verifies it, then passes its exact WAV
and hash to the existing bounded strict uploader smoke in a fresh service.
There is no resampling, concatenation, gap filling, epoch mapping, network FTPS,
production binding activation, or retry of session creation.

After clean shutdown it opens the jobs database read-only and verifies exactly
one source, batch, and page: session/capture identity, linked identifiers,
source hash and stored bytes, start/origin 0, frame extent and EOS publication.
It records these actual identifiers in the local result. Producer metadata
is not imported into application state: its source identity is joined to the
backend source only by this explicit experiment's observed byte/hash mapping.
The original delivery and owned snapshot are both reverified after processing.

The outer `input_kind=explicit_producer_delivery_experiment` is authoritative
for the experiment type. The nested reused smoke result's historical
`explicit_public_sample` label is only a legacy label, not a claim that the
fixture is real speech or has verified public provenance.

Success covers Android producer-codec synthetic delivery → local semantic
preflight → owned snapshot → strict uploader → backend publication. It does not
cover microphone acquisition, Android service/OEM behavior, network transport,
physical continuity, source authentication, general crash reconciliation,
durable recorder-session binding, speech quality, or automatic ingestion.
All generated WAVs, metadata, reports, transcripts and databases remain local;
only test/tool source and bounded findings are backed up.

## Observed integrated run, 2026-11-03

A new Android producer fixture used production DirectorySpool/PcmSegmentWriter
and FtpsDelivery, with 37 assertions for catalog/header/zero-PCM/inner-and-outer
hashes/readback. It produced one version 2 initial START→STOP delivery, 11 seconds,
176,000 frames, 16 kHz mono16-bit silence, 11 synthetic NOT_POLLED read observations.
WAV size 352,044 bytes; SHA-256
`1a092b23d9dfe64d53943d18262c0a49eaf32c5ab5f2b016cd8edd16ec9440dd`.

The exact delivery passed semantic preflight and a fresh private owned snapshot.
The already-qualified isolated runtime accepted those exact WAV bytes through
existing strict uploader/API code; one source and page yielded two completed
chunks of 10.5 and 0.5 seconds, published/released with EOS. The post-cleanup
read-only database mapping matched the source hash, 176,000 frames, zero origin,
and actual session/capture/batch/page/source identifiers. Producer original
and snapshot remained unchanged. Automatic ingestion remains disabled; metadata
was not imported as trusted recorder provenance.

The supervisor exited 0 with all cleanup predicates true, store safe, lock closed,
permit clear, worker stopped and no cleanup errors. Sampled peak RSS 390,418,432
bytes is a short observation, not endurance or memory-stability evidence.
Local integration result SHA-256:
`59652f31367a8b10d160f37d03f798f6e5deb4a68212111ebadea33dff9c7e7e`.
The focused producer-intake/uploader/contract suite passed 252 tests, with one
existing Starlette/httpx deprecation warning. This is not a whole-repository pass.

Producer source was independently reviewed and its remote tree matched tested
source at Android commit
[ed8855c7920e78259055e331a4a1b146eb1cea08](https://github.com/Headmaster218/long-audio-recorder-android/commit/ed8855c7920e78259055e331a4a1b146eb1cea08).
The producer's source-only fixture test is reproducible; generated files are not
committed. Existing producer suites additionally passed 184 delivery, 868 FTPS,
and 91 capture-anchor assertions. No SDK/device run is implied.

Independent saved-evidence audit accepted the local integration and verified
that concatenating both chunk PCM bodies exactly reproduces all source/snapshot
frames byte-for-byte. Three strict outcomes were empty, consistent with silence.
REPORT SHA-256 `83bd242a83089a277c50524342e3355b88bf7167c72edb8e073f961b849adfeb`;
audit source SHA-256 `f7160e19f03f2549b8ae0ad52f6e1fc092858a9627361f1021a5e0ea4498bdc2`.
Source is saved in `qualification/independent_recorder_integration_audit_20261103.py`.
It is a historical local-path audit that writes its report beside itself, not a
portable test to run within a manifest-verified source checkout. Raw evidence was
not changed. A fresh remote Android source assembly reproduced the three original
delivery files byte-for-byte with all 37 assertions passing again.
