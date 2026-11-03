# Bounded speech-core diagnostics, 2026-11-03

## Outcome and scope

This checkpoint improves experiment configuration and failure diagnostics. It does
**not** claim to fix transcription accuracy or speaker identity. Production models,
capacity configuration, minimum embedding duration, strict primary-only admission,
retention/qualification profiles, cut geometry, and publication gates are unchanged.
No decoding suppression, automatic language change or model promotion is introduced.

The historical strict public Chinese smoke was backed up at
`02cf677dffcc1ee50a6fd5d52cd70d7bd03c8272`. Its source report says English was forced
and the short second core emitted extra English text with unknown speaker and
`embedding_dimension_mismatch`. That historical service database/run was not
available in this restored workspace, so this investigation does not re-audit it
or claim an end-to-end reproduction. Current source basis is
`ec300eb625555dd81389d024332b115cd605b893`.

## Decoder evidence, not reference accuracy

Using the existing SHA-pinned int8 SenseVoice release and sherpa-onnx 1.13.8 CPU,
a predeclared matrix compares English, automatic and matching-language decoding
for Chinese (the diagnostic sample) plus Japanese, Korean and Cantonese public
samples. Each is decoded whole and in independent 5.25-second cores. There are
30 decoder calls per matrix. Japanese/Korean/Cantonese supplement the exposed
Chinese sample; these few short release examples are not a representative held-out
accuracy benchmark. Their upstream output is not independently annotated truth.

- The Chinese recording has 89,472 frames: 84,000 plus a 5,472-frame (0.342s) tail.
  Both English and automatic decoding produce ASCII letters in that isolated tail;
  matching Chinese produces punctuation only. Whole-file/first-core text hashes
  match across these language choices. **Automatic language alone is not a fix.**
- The Japanese recording has a 31,200-frame (1.95s) second core. Forced English
  produces English-like text there; automatic/matching Japanese produce Japanese.
  This demonstrates language-setting sensitivity, not a measured accuracy gain.
- Korean and Cantonese clips fit within one 5.25-second core. Their full/core
  outputs match across these choices; they do not test short final-core behavior.

The short context and language setting are experimentally implicated; this does
not prove the tail is silence or establish a general safe text-removal rule. No
new duration threshold was selected. Raw outputs remain local. The portable
`sensevoice_core_language_diagnostic_20261103.py` writes hashes/counts only, checks
hardcoded model/token/sample hashes, and uses the exact verified file paths.
The final reviewed script completed all 30 calls in 13.313 seconds; local
hash/count summary SHA-256 is
`9d8b4c9cf9667c4ce5d82ec960235c03d72065c976d57893fcc90f3561decddc`.
Run under a timeout and resource limit. It does not start the strict service or
prove its native diarization/publication behavior.

Public source and example documentation:
- https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
- https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html

## Bounded source changes

1. `tools/strict_uploader_smoke.py --language {auto,zh,en,ja,ko,yue}` makes the
   language explicit for a new experiment and records effective/explicit language
   in its result. Omission preserves the capacity profile's existing English
   default. The shared capacity configuration is unchanged; invalid languages
   are rejected before output/service creation.
2. `speaker_support.prepare_window` reports `missing_embedding` when no vector
   exists, rather than calling absence a dimension mismatch. Malformed vectors
   retain `embedding_dimension_mismatch`. Both paths still return no PCM/manifest
   and abstain from speaker identity. Missing does not assert a specific cause.
   Existing saved rows keep their historical status; no database migration occurs.
3. Synthetic boundary controls show the original two-second minimum abstains at
   5,472/31,200/31,999 frames and decodes at 32,000. Thus a short core can have no
   embedding without any dimensional defect. This source-level explanation is not
   proof of the exact unavailable historical speaker-runtime state.

## Verification and failures

- Final focused suite: **101 passed**, one existing Starlette/httpx deprecation
  warning. Files: `test_strict_uploader_smoke.py`, `test_reviewable_support.py`,
  `test_speaker_support_lineage.py`, `test_short_embedding_abstention.py`.
- An initial new test incorrectly expected the private support-status column in
  public segment JSON: 74 passed, 1 failed. The test now checks that column in the
  database and checks the public support endpoint separately. Product behavior
  was not changed to satisfy that incorrect expectation.
- Extended seven-file candidate suite: **201 passed, 11 failed, 14 setup errors**.
  Independent baseline: **189 passed, the same 11 failures and 14 errors**, with
  identical failed/error node IDs. Twelve newly added cases pass. This extended
  run preceded the four additional short-duration controls above.
- Independent final extended rerun including the four duration controls: **205
  passed, 11 failed, 14 errors**, with the same 25 unsuccessful baseline node IDs.
  Independent review found no remaining correctness blocker in the bounded change.
- Baseline issues: nine frozen bounded-core hash/proof expectations, two cold
  reopen disk-reserve errors, fourteen primary-crop fixture storage-availability
  setup errors. They are not repaired or reclassified as passing here.
- This is not a full repository pass, service requalification, crash/endurance
  run, or accuracy acceptance. The new smoke language option has configuration
  and refusal tests, not a fresh full strict-service integration run.

Only source/tests and this summary belong in Git. Do not upload audio, transcripts,
raw run reports, databases, models or credentials. A future quality improvement
needs independently annotated short speech/non-speech and boundary examples,
including legitimate short utterances, before choosing context or rejection rules.
