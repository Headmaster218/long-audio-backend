# Independent frozen AMI metadata audit

Status: **PASS_WITH_EXPLICIT_LIMITATIONS**

Frozen manifest SHA-256: `fc297e67fbee68481809c600068ffc159df0742c395f00a1b51f767a13a16e52`

## Verified

- 36 unique two-second coordinates: 12 references for four known people; 24 queries for all eight people.
- Each reference/query group has slots 1, 2, 3 from distinct original segments, with starts at least 30 seconds apart.
- References use scenario a; queries use scenario d on the same date. C/T official global IDs are disjoint.
- Every crop is inside its original segment with 100 ms erosion; its support words are linked children of that segment and continuously cover the crop with 100 ms chain erosion.
- No crop overlaps any other speaker segment expanded by 100 ms or any own positive-duration nonlexical event expanded by 100 ms. Extra check: no other-speaker positive-duration word-layer event overlaps after the same expansion.
- Independent interval union/complement reconstruction reproduced every earliest coordinate and qualifying interval endpoint under the frozen greedy selection rule.
- All 32 original annotation member hashes match. Retained meetings.xml is byte-identical to the copy in the official annotation archive.
- Four retained official Mix-Headset header records decode as PCM mono, 16 kHz, 16 bit, with data at byte 44. Every crop byte range and file-size bound matches.
- Selector code only uses meeting metadata, manual annotations, and retained header metadata. It does not read audio, model embeddings, or scores.

## Exact claim limits

- All claims concern annotation time support, not physical acoustic purity, listening certification, or speech quality.
- Lexical means positive-duration <w> without punc=true. It includes filled pauses and can have imperfect forced-alignment timing; words may be truncated at crop boundaries.
- Zero-duration nonlexical annotations are not positive-duration events and are not excluded by the frozen rule. Per-row IDs are reported.
- The official metadata has empty startTime for all four meetings. Earlier/later refers to scenario a/d ordering, not independently established wall-clock timestamps.
- C and T have disjoint official global IDs within this experiment. Historical exposure remains unknown because old AMI20/full-holdout inventories are missing; parent accepted this limitation.
- ES2015a/d are marked seen/training in official AMI metadata. This audit does not establish freedom from model pretraining or earlier experiment exposure.
- The retained HTTP 44-byte responses prove recorded header geometry only, not current server state or full-source audio hashes. No WAV body was read by this audit.
- The manifest internal status says prospective because the original is preserved. ACQUISITION_RELEASE.json separately freezes its exact SHA256.
- This is English same-day separate-scenario-meeting evidence. It is not Chinese, cross-day, long-duration, or final acceptance evidence.

## Reproduction

Run `python backend-paired-reference-pilot-20261104/metadata-audit/audit_frozen_metadata.py` from the workspace. It only reads metadata inputs and writes this audit directory.

`metadata_audit.json` includes original input/member hashes, per-row word IDs and support text, per-group start spacings, and explicit failures (if any).

Failures: 0
