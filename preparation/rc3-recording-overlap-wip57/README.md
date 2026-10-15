# Finalized recording-overlap primary/deduplication checkpoint

This source checkpoint preserves a bounded finalized-batch milestone: exact
byte-equal recording overlap is represented once in canonical primary work,
while every original encoded recording and its source-to-work provenance remain
retained. It is not a release or complete audio-system acceptance.

## Verified historical result

- 20 new focused tests plus 12 original regression tests passed: 32 total,
  zero failures, errors or skips, 5.99 seconds. They use synthetic/fake providers;
  no model inference occurred during pytest. Per-process peak RSS was
  133,201,920 bytes, not a simultaneous process-family peak.
- One actual API/helper run exited zero in 12.32 seconds. Primary processing,
  exact retained-byte verification and native ownership checks passed. Positive
  shutdown found no known live descendants. Peak sampled process-tree RSS was
  683,499,520 bytes; sampling does not establish an instantaneous hard maximum.
- Three unique source uploads totaled 27 seconds. Their exact known-coordinate
  union was 21 seconds, processed as two ordinary adjacent 10.5-second chunks.
  The primary API returned 16 segments. Native metadata showed one 21-second
  input/processed epoch, with no replay and disjoint primary ownership.
- Same-key A retry preserved three original receipts; finish retry preserved
  both work identities. All seven original-to-derived mappings were verified.
  After shutdown, all three original WAVs and both derived WAVs matched exact
  bytes/digests; concatenated derived PCM matched the 21-second canonical input.
- Optional MOSS did not run. Initial native-ingestion statuses were
  receipt_pending for the first derived source and busy for the second. One
  first-source cell was retained; no eligible seam existed. The optional owner
  reported one dropped operation and source_ingestion_receipt_unavailable.
  Receipt handoff sequencing remains unresolved in this frozen checkpoint.

Primary success, original retention, optional failure and cleanup are separate
outcomes. Neither zero exit status nor health=ready makes the optional step pass.
The recorded text is not an accuracy, completeness or speaker-identity result.
Historical aggregate facts above do not claim execution of these public templates.

## Exact scope

The API accepts all sources before explicit finish. Each capture ID declares one
shared PCM sample-coordinate domain; coordinates are supplied by the client.
Only exact PCM equality is aliased. Unknown placement, gaps and differing samples
remain explicit blockers with original receipts retained; there is no guessed
alignment or silent source loss. See docs/RECORDING_OVERLAP_BATCHES.md for the
full API, bounds, persistence and deletion contracts.

Continuous capture, source-specific undo/erasure/revocation, late insertion,
placement correction, acoustic alignment across microphones, arbitrary overlap
recovery, lexical/speaker quality, long-duration endurance, Windows/GPU operation
and complete optional receipt/MOSS behavior remain open. There is no new repair
of the optional receipt sequencing in this packet. Keep original source files
independently before requesting destructive whole-session deletion.

## Source reconstruction without a second full product copy

Repository: Headmaster218/long-audio-backend.
Checkpoint parent: 442cf1f3a76902770d50254a6182f614c12d2b6e.
Public destination: preparation/rc3-recording-overlap-wip57.

Start a separate source tree from product commit
1fc066910ca78b2e57d7a28f79b91cafc06f5dd5. Preserve any existing originals.
Apply the six included feature paths at their matching product-relative paths.
Five Python files are byte-exact frozen candidate source; the feature document
has project-facing status wording updated. The frozen candidate manifest digest
is 8811cf711e0219587c295aaa04bc9ae34d5f3541680c42c64f65d61cead179b3.

The candidate also requires three already-preserved dependency overlays from the
checkpoint parent's preparation tree, copied at their product package paths:

- preparation/overlap-api-diagnostic-wip55/native_owned_cell_capture.py
  SHA-256 635a39a89bd408dd3f4a29a89991924f1ce7e1902fbe42a3ba713627ae3e81b8
- preparation/overlap-api-diagnostic-wip55/native_owned_cell_store.py
  SHA-256 bb3091a5236c2a62647ce3cd4d4b54475f9c8809e50fbc61f2ecbf13757e852d
- preparation/cell-deadline-recovery-wip52/candidate/audio_backend/native_owned_cell_jobs.py
  SHA-256 071112ba59dc8728b838013195eb3c3a15b67f205cdd97da9517b2f8e2cc647e

Use the v1 store above, not the newer owner-catch diagnostic store in WIP56.
The reconstruction/source/assets locks are already preserved under
preparation/minimal-recovery-wip53. Its source.lock.json SHA-256 is
4299d4d0e44fb79596770fac1809fc9d33b244a9b07fc8417e25339a4e46bb69.
Those preparation directories are source recipes, not bundled runtime/assets or
proof that a different deployment environment is qualified. They do not license
redistribution of third-party audio or models.

The complete assembled audio_backend inventory must contain 102 regular .py/.json
files and match the pinned helper digest in PUBLIC_MANIFEST.json. Base product
and reconstruction pins are references; the other 351-file source tree is not
copied into this packet. Static comparison found no additional candidate changes
beyond the six feature paths, three dependency overlays and candidate patch file.
The patch file and full private freeze manifest are intentionally omitted.

## Packet contents and verification

- audio_backend/{app,lifecycle,recording_overlap,storage}.py: four product modules.
- tests/test_recording_overlap.py: 20-case focused test source.
- docs/RECORDING_OVERLAP_BATCHES.md: finalized recording API contract.
- helper/smoke.py.in and helper/server_entry.py.in: sanitized actual-run templates.
- helper/README.md and CONFIGURATION_FIELDS.json: portable local binding contract.
- docs/TESTED_API_GUIDE_ZH.md: ordinary localhost API guide, with historical
  adjacent-upload results clearly separated from this finalized-batch milestone.
- PUBLIC_MANIFEST.json and this README: integrity and factual result summary.

Preparation only performed text transformation, source hashing, static parsing
and byte-exact inverse checks. No product/helper imports, tests, models, server,
database inspection, network access or publication occurred in preparation.
No audio, transcript, private run identifiers, raw receipt, runtime, local config,
database or execution log is included. The public packet is under 512 KiB.
