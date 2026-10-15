# Reproduction checklist

These files preserve source structure and the completed experiments' methodology. They are not a turnkey benchmark distribution. The `.py.in` templates have unfilled required configuration and were not run. The historical results belong to the original executed-source hashes listed in `SOURCE_MANIFEST.json`. The parameter-free `normalization.py` is byte-identical to the original helper.

## Before any execution

1. Work in new, private experiment directories. Do not reuse or overwrite an existing attempt. Keep generated transcripts, word-level references, model outputs, initialization settings, and input receipts private.
2. Confirm the rights to use the official fixtures, annotations, and model assets. Obtain them separately through their official sources. This checkpoint contains no data or model weights.
3. Replace each required placeholder listed in the source manifest using locally verified paths, expected mapping, and protocol hash. Copy each configured template to a `.py` filename in its experiment directory. Copy the unchanged normalizer beside the AMI helpers. Do not treat placeholder-containing templates as executable scripts.
4. Review the configured source and record its SHA-256 separately from the original and template hashes. Regenerate all dependent local source/input pins consistently; do not weaken assertions simply to pass them. These configuration templates have not been validated against another runtime or dataset.
5. Verify the already-provisioned dependencies, runtime imports, fixed fixture geometry, and local input contracts. This checkpoint does not install packages or reproduce runtime build steps.

## Chinese crop/context experiment

The original helper uses the unchanged production `AudioAnalyzer._transcribe_turns` and `_transcribe_sensevoice`, plus `align_tokens` and `lexical_core`. Its import guard requires the configured production adapter in the selected Python 3.12 runtime. The template preserves the original Linux affinity, resource-limit, subprocess, and process-monitoring requirements; porting those platform assumptions would be a separate change.

Required inputs, kept private:

- An existing Python 3.12 runtime with the production `audio_backend` code and the dependencies used by the helper, including NumPy, soundfile, OpenCC, sherpa-onnx, and psutil.
- Verified official model assets in the directory structure expected by the production analyzer.
- The official Chinese WAV: mono PCM16, 16 kHz, exactly 1,127,530 frames.
- Historical `chunks-terminal.json` containing seven retained windows, their metadata, native diarization turns, fixed/final owned cores, and expected per-window crop counts 3, 2, 5, 3, 3, 3, 2.
- Historical `primary-transcript.txt` for the exact-reproduction check. It is not a trusted quality reference.
- A locally prepared `sources.sha256` that binds all required source, fixture, model, and historical input files. Paths are relative to the experiment directory's parent. The source pin manifest itself is not distributed here.

The helper's `check` mode exercises exact planning and production crop arithmetic without model calls; `run` requires successful checks with unchanged pins. The fixed real-call inventory is A 21 and B 7. There is one shared lazy initialization, fresh streams, CPU2, language auto, ITN true, and simplified-Chinese projection. Do not rerun merely to obtain a favorable outcome, and do not claim CER without a trusted reference.

The preserved per-attempt constraints are 240 seconds of work plus up to 60 seconds cleanup, CPU affinity 0–1, nice 19, sampled summed RSS 1 GiB, per-process address space 2 GiB, and an inclusive 4 MiB experiment-file budget. The helper prevents an existing attempt directory from being overwritten. Its resource sampling is not a hard instantaneous RSS bound.

## AMI reference and scoring experiment

Prepare a separate private evaluation directory with the configured reference/scoring helpers and unchanged normalizer. The selected fixture is ES2002a; the common source interval is [511, 531), and the declared unequal-coverage sensitivity interval is [510.5, 531.5).

Required input structure:

- `downloads/ami_public_manual_1.6.2.zip`: official annotation archive, obtained and verified separately.
- `output/SCORING_PROTOCOL.json`: frozen reference selection, normalization, window, whole-segment edge, unknown-stream, and single-window mapping rules matching the report.
- `output/BUILD_RESULT.json`: evidence that the intended existing MeetEval 0.4.3 environment passed its documented smoke examples; the cpWER adapter requires `documented_smoke_examples_passed` to be true. Do not fabricate this evidence.
- A configured directory with the original private `segments-00.json` primary output and `moss-result.json` diagnostic output. The helper reads primary `segments[].text`, not `raw_text`; it reads MOSS `diagnostic_phrases[].raw_text`.
- The primary timestamps are crop-relative plus 510.5 seconds. The MOSS binding is sample rate 16 kHz with support frames [8000, 328000), producing source offset 511 seconds and 20 seconds of support. Do not silently change these fixed assumptions.

Run the configured reference parser only after checking its official meeting-to-reference speaker mapping. It selectively extracts the four ES2002a word XMLs, meeting metadata, and archive license/readme, and writes private reference windows and provenance. Word midpoints define half-open inclusion; invalid lexical times are audited rather than repaired.

The cpWER adapter uses the official `meeteval.wer.wer.cp.cp_word_error_rate`. It scores two windows, three whole-segment edge policies, and two systems: 12 frozen cases. It preserves missing primary speaker labels as one nonempty unknown stream and never invents word alignment or deduplication. Its generated scoring inputs contain private text and must not be copied into this source checkpoint.

Before the DI companion, create and freeze `output/DI_SCORING_PROTOCOL.json`, binding the locally generated `SCORING_PROTOCOL.json`, `SCORES.json`, `SCORE_INPUTS_PRIVATE.json`, `REFERENCE_WINDOWS.json`, and `INPUT_MAPPING.json`, plus the unchanged normalizer hash. Set the companion's explicit protocol-hash placeholder to that verified file's SHA-256. Preserve the protocol's original segment grouping, chronological ordering, separate reference streams, and interpretation. The required metric is `meeteval.wer.wer.di_cp.greedy_di_cp_word_error_rate` from MeetEval 0.4.3.

The DI companion first checks the documented synthetic zero-error example, then reuses all 12 frozen input cases. It supplies no invented word times to the metric and uses `reference_sort=False` and `hypothesis_sort=False`. All original hypothesis segments remain indivisible. The scorer's assignments must not be presented as real speaker identification.

## AMI one-call full-context control

Configure `ami_full_context.py.in` in a third new private experiment directory. It requires the existing production Python 3.12 runtime, official model directory, retained primary/MOSS outputs, frozen AMI evaluator directory, and a verified 21-second source WAV. Replace all five path placeholders and preserve the existing source/import and input-identity checks.

The source WAV must be mono PCM16 at 16 kHz with 336,000 frames. Extracting frames [8000, 328000) produces exactly 320,000 samples for source interval [511, 531). The input's PCM16 and float32 identities must equal the saved MOSS support identities. The helper permits only one stream, one decode, and one model initialization, with CPU2, language auto, ITN true, and the same production conversion settings.

### Frozen protocol synopsis

Prepare a private `PROTOCOL.json` and `sources.sha256` binding the local inputs, executed source, and frozen evaluation. The original private protocol and its configuration pin are not distributed here. A locally configured repeat needs its own frozen protocol and matching source/input pins.

- Reconstruct the original two 10.5-second owned cores from contextual reads [0, 12.5) and [8.5, 21), matching the 14 saved canonical turns.
- Apply the saved chunk-local-to-registry mapping before alignment, then retain the common 20-second interval with its seam at common-relative t = 10 seconds.
- Use one coordinate system, one production alignment, and one 20-second lexical projection. Preserve every lexical unit exactly once and reject out-of-range units instead of clamping or resplitting them.
- Complete the model-free edge/seam conservation checks before the one-call run, with unchanged pins.
- Reuse the existing common/midpoint reference and baseline score cases without rescoring or regrouping them. Keep reference speakers separate; keep original projected hypothesis segments indivisible.
- Score the new output with official MeetEval 0.4.3 cpWER and greedy DI-cpWER and the unchanged normalizer. Retain unknown labels explicitly. Do not alter text or assignments using the reference.
- Disclose that the observed control has 13 scored segments versus 16 in the baseline. The control changes projection and segmentation as well as recognition context, so the DI difference does not isolate crop duration.
- Keep generated native output, adapter/projection text, score-input payloads, private mapping, and all receipt/configuration data outside the public checkpoint.

The preserved control runs its existing model and scoring environments sequentially under one bounded supervision period. Its safeguards include a 240-second work budget, up to 60 seconds cleanup, CPU affinity 0–1, nice 19, 2 GiB per-process address space, a 1 GiB sampled combined RSS limit, and an inclusive 4 MiB experiment-file cap. No new native diarization or speaker-recognition result is established by this control.

## Validate and report

- Check the exact input/source identities and the standard scorer version.
- Verify every S + D + I total, denominator, and hypothesis-length identity; ensure the common 20-second case is primary and the full 21 seconds is labeled unequal coverage.
- Keep the primary unknown tokens; keep reference speakers separate. Do not construct an arbitrary concurrent-speaker reference concatenation.
- Report the edge-policy sensitivity and the greedy algorithm's limitations.
- Separate reproducibility from quality. The Chinese sample has no trusted transcript, and the English result is a single-fixture diagnostic.
- Publish only reviewed aggregate findings and source. Do not include raw audio, text payloads, private configuration/receipt files, or local run paths.

No new model or metric execution was performed to prepare this checkpoint. Configuration portability, runtime compatibility, and a repeat of the recorded experiment remain untested here.
