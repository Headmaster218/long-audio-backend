# FAIL: continuous fixed-20-second ASR screen (2026-10-10)

The candidate failed the original quality gate. Preserve this negative result
alongside the inherited positive staged-clip screens; it is not a replacement
score or a default promotion.

Eight continuous AliMeeting channel-0 excerpts, 733.385 seconds per arm,
4,182 reference characters, 41 fixed windows per arm, 82 calls total:

- SenseVoice int8: 1,360 / 4,182 errors, 32.5203% CER
  (220 substitutions, 1,107 deletions, 33 insertions)
- Qwen3-ASR-0.6B Q5_K_M: 1,637 / 4,182 errors, 39.1440% CER
  (683 substitutions, 711 deletions, 243 insertions)
- Only 5/8 meetings were nonregressing; the gate required at least 6/8
- Qwen returned 39 successful windows and two truncated windows
- Both truncated windows also triggered repetition suspicion, in the same
  meeting: period 7 repeated 46 times, and period 2 repeated 150 times

The process completed correctly while recognition quality failed. All 82 calls
were accounted for, original model and supervisor handles exited with code 0
and were waited/reaped, and bound pre/postflight verification passed. Independent
terminal review recomputed meeting/total counts and the repetition findings.
Private audit receipts are not distributed; these are reported verification
facts rather than independently verifiable public execution attestations.

This result stays unchanged. A later status-triggered split diagnostic, if run,
is a separate experiment: it must not replace, silently repair, or mix with these
original scores. This failed gate earned no permission to advance or promote.

## What this measures

Annotation-assisted outer edges retain complete reference turns. Internal
20-second cuts are fixed, not gold-aligned, and can cut speech. All windows,
including final short windows and silence, are retained. Complete excerpt
hypotheses concatenate the window outputs in order and are compared with the
chronologically serialized complete gold turns. Overlapping speakers make this
whole-excerpt CER order-sensitive. No error allocation to individual windows or
utterances is claimed. This is not a production-adapter/end-to-end, timing,
speaker identity, or diarization evaluation.

Outer-edge selection was revised for annotation feasibility before model outputs:
±15-second search admitted only 2/8 meetings, ±180 seconds admitted 7/8, and the
explicitly selected ±300-second rule admitted all eight. This is transparent
post-hoc protocol feasibility work, not output-based selection, and it limits
representativeness. No model-output-dependent excerpt substitutions were made.
See PROTOCOL.md for the deterministic targets, gap rules, tie-breaks and scoring.

## Source-only evidence

- `selection.json`: eight excerpts and 41 window sample bounds, no text
- `results.json`: full original aggregate/meeting counts, statuses and FAIL gate
- `pins.json`: links to inherited exact backend/model/corpus/runtime pins
- `score.py`: bounded offline whole-excerpt character-count helper
- `test_snapshot.py`: synthetic scoring and public snapshot consistency tests
- `ATTRIBUTION.md`: corpus attribution and license scope

From the snapshot root, run:
`python -B -m unittest discover -s continuous -p 'test_*.py'`

The importable `score_excerpt(reference, records)` helper accepts local reference
text and ordered records containing status/text/truncated. It produces counts
only; it neither runs a recognizer nor validates full execution provenance.
Acquisition, annotation selection and inference reproduction are not automated.
The inherited English and Chinese files are unchanged and remain separate
experiments. No audio, transcripts, prediction text, private artifact IDs/hashes,
credentials, machine paths, request headers or internal process logs are shipped.
