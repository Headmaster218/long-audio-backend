# Context-aware short-core investigation, 2026-11-03

## Decision

**Do not change production context, language, text suppression or models from this
evidence.** Two seconds of existing context often improves agreement with a
whole-input adapter output, but the effect is not uniform and that output is not
human ground truth. No backend behavior, strict gate, primary-only rule, threshold,
speaker assignment or persisted data is changed by this checkpoint. The deliverable
is a reproducible diagnostic and model-free ownership tests.

## Predeclared comparisons and public sources

The same pinned SenseVoice int8 model, CPU runtime and automatic language setting
were used throughout. Core size is 84,000 frames (5.25s); the only compared setting
is zero versus 32,000 frames (2s) of symmetric context. Existing real
`_transcribe_sensevoice`, `align_tokens` and `lexical_core` code is used. All-active
synthetic turns deliberately isolate ASR/ownership; they are not actual diarization.

1. Five existing public Chinese, English, Japanese, Korean and Cantonese sample
   recordings, each placed after fixed zero/0.5/2-second digital-silence prefixes.
   These are five recordings and correlated placements, not 15 independent samples.
   They include previously exposed diagnostic samples; no held-out accuracy claim.
2. Four fixed 29.25-second crops, starting at 60 and 180 seconds in each of
   `EN2002a.Mix-Headset.wav` and `EN2002b.Mix-Headset.wav`. These are two related AMI
   meeting recordings, not four independent meetings or speakers. They were chosen
   before viewing crop output and not adjusted afterward.

Sample/model/token/original-recording SHA-256 values are hardcoded in the diagnostic
and verified before inference. No new assets were downloaded. Sources:
- https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2
- https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
- AMI Meeting Corpus, publicly released under CC BY 4.0:
  https://groups.inf.ed.ac.uk/ami/download/

## Findings and important confounds

The comparison distance is character edit distance after NFKC normalization,
case-folding and removal of non-alphanumeric characters. It measures agreement,
not CER against truth, and hides punctuation/spacing differences.

- In the short-release study, context improves that proxy in ten placements,
  ties in four, and worsens it in one English placement. Total distance is 24→6.
  In the original zero-prefix examples, at least one context window contains the
  whole recording. This explains part of the agreement and prevents interpreting
  it as independent evidence of robust streaming improvement.
- AMI context windows do not contain the entire crop. For a@60, a@180, b@60 and
  b@180 respectively, zero-context→two-second-context distances are **15→6,
  87→63, 16→11 and 41→49**. Aggregate 159→129 masks the last crop's worse agreement.
- The reference is whole-input **adapter** output. For 29.25-second AMI crops,
  that adapter itself uses its existing 28-second cores and overlapping context.
  It is not a single unchunked native decode, nor an independent reference.
- Whole-input output can itself be wrong. A proxy disagreement increase is not
  proof of an accuracy regression; a decrease is not proof of accuracy improvement.
- The first reported reference language and each external window's detected
  language are recorded. The reference's complete internal language sequence is
  not recorded; this is not complete language provenance.

No duration threshold or suppression rule is justified. In particular, deleting
all brief tail outputs could delete legitimate speech. The experiment does not
establish a safe rule that removes hallucinations while retaining short utterances.

## Timing and provenance boundaries

Each row keeps the original-file hash, exact original crop frames, added prefix
frames, each core's frames and exact decode-source frames, float32 PCM hash, and
emitted intervals. The hash named `float32_pcm_sha256` is of normalized float32
samples, not the PCM16 WAV container passed to inference. The original file hash
and deterministic crop/prefix geometry identify the input transformation.

Emitted intervals are crop/prefix-local and remain within their actual decode
window, retaining whole lexical units rather than clipping them to a core. To map
back to original-recording seconds, add source_crop_frames[0]/16000 and subtract
prefix_silence_frames/16000. Artificial prefix silence has no original audio source.
These are model-derived timestamps, not verified word boundaries.

The diagnostic refuses nonempty output lacking lexical ownership evidence instead
of applying an incorrect fallback time shift. That latent diagnostic-only fallback
was found and corrected during independent review; the successful initial run used
tokenized output, so its executed path was not affected. Empty output remains empty.
No synthetic labels are written into the real backend or any user database.

## Verification and reproducibility

- Final diagnostic runs 121 adapter calls and writes 38 comparison rows across
  both studies. It writes hashes/counts only by default. Optional local transcripts
  must stay outside source and must never be included in Git backup.
- Final local summary SHA-256:
  `5d12085bcc83d95f0f804bfe4c6fda21c17f31fe312f88fa5c77d9a01fe95891`.
  All 38 result rows match the preceding reviewed run exactly.
- Seven new model-free checks cover empty/no-evidence behavior, source/core time
  projection and distance calculation. Combined with existing boundaries,
  punctuation and stream-boundary checks: **42 passed**. No full-repository claim.
- Independent review checked source binding, source-window interval bounds,
  original AMI crop offsets, the corrected failure path and comparison limits.
  It supports backing up this diagnostic, not changing product defaults.

Example (existing local assets and runtime required):

    timeout -s TERM -k 5 320 prlimit --as=4294967296 --cpu=300 -- \
      python -I -B qualification/sensevoice_context_diagnostic_20261103.py \
      --asset-root EXISTING_MODEL_ASSET_ROOT --ami-root EXISTING_AMI_ROOT \
      --output NEW_PRIVATE_OUTPUT_DIRECTORY

Use OMP_NUM_THREADS=2 and OPENBLAS_NUM_THREADS=2 for the recorded run's settings.
The source/analyzer/boundaries hashes and runtime version are included in the local
summary. No service, database, provenance packet, crash, endurance, overlap or
speaker-identity qualification is performed. Next evidence needs independent
human references and actual speech/non-speech/overlap annotation, with real-service
source ownership checked, before making an acoustic behavior change.
