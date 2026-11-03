# Bounded English-language control on overlapping AMI speech

## Decision

Do not change defaults. Explicit English decoding offered no useful improvement
on this finite comparison. Automatic detection already returned English for all
eight windows. Stop this line of experiments rather than select more examples
until a favorable result appears. This does not establish that language control
is ineffective for all multilingual recordings or all models.

No product files, model selection, primary-only policy, strict qualification,
speaker gates, or runtime defaults were changed. The tool is an offline diagnostic
and does not dispatch through or qualify the strict service.

## Frozen comparison

Two fresh 21-second clips were selected from public AMI labels before inference:
EN2002a at 350 seconds and EN2002b at 400 seconds. They do not overlap prior
60/180, 1260/1667, 1078/1119, 1551 or 565-second experiments. They come from the
same related meeting sessions and are **not independent held-out speakers**.

The selection rule was the first qualifying integer start in [300,500) for each
meeting: no outer reference-word cut, at least ten overlap-reference words, and
at least one speaker contributing ten or more words and 10–30% of the clip's
reference words. [800,1000) was also searched and yielded no qualifying clip.
Selection used labels only, not hypotheses. Multiple qualifying low-share
speakers are pooled, so the pooled share can exceed 30%.

The clips contain 102 and 81 reference words, with low-share denominators 45 and
32 and overlap denominators 47 and 48. Each clip is divided into exactly four
5.25-second cores, no context. Both arms use identical source PCM, timing,
SenseVoiceSmall int8 assets, two CPU threads, ITN, lexical ownership and scoring.
Only recognizer language differs: `auto` versus `en`. The recognizer is released
and recreated between arms because its language is fixed at construction.

The model and token files, source audio and annotation archive must match the
existing pinned SHA-256 values. PLAN records selected clips, annotation member
hashes, fixed arms and source hash. SUMMARY records five implementation hashes,
model/audio hashes, runtime version, source-frame and clip-frame spans, per-window
PCM hashes and owned clip-local intervals. Actual hypothesis/reference text is
not saved in either file. Synthetic all-active turns isolate ASR, so this study
measures lexical retention, not speaker attribution or activity coverage.

Frozen PLAN SHA-256:
`fececef190ed1716fbfed16348c23ec40df609238b6b39350c024b3097c927ce`.
The plan-only and actual-run PLAN files were byte-identical. The actual run
completed 16 decodes and four rows, exited successfully, and used sherpa-onnx
1.13.8. SUMMARY SHA-256:
`6f6ea7cf8336ac1d0e2e3eed95a77f4694a648ca994a9378021fac23ae397b20`.

## Results

- EN2002a: both arms 50 errors / 102 reference words. Low-share correct words
  decreased from 17/45 (auto) to 16/45 (English). Overlap correct remained 12/47.
- EN2002b: both arms 44 errors / 81 reference words. Low-share correct remained
  14/32; overlap correct remained 11/48.
- Combined: both arms 94/183 errors, or 51.37% time-start-ordered WER. Low-share
  correct decreased from 31/77 to 30/77. Overlap correct stayed 23/95.
- Automatic detection returned `en` on all eight windows. Equal total errors do
  not mean identical outputs or error composition.

These samples show substantial overlap/minority loss but no evidence that
language selection is the useful remedy. Forcing a language would additionally
risk legitimate code-switching, which this English-only study does not test.
No language policy is recommended from this result.

## Reference and metric limitations

The public AMI manual transcripts contain human-reviewed lexical content, but
word timestamps are automatically forced aligned. Overlap tags here mean
intersection of different-speaker word intervals; they are not independently
manual overlap labels. Timing errors and long aligned intervals can affect them.

The fixed scorer uses NFKC/casefold, ASCII apostrophes, ASCII alphanumeric words
with internal apostrophes, retained fillers and no number expansion. References
are ordered by word start, speaker, index. Unit-cost Levenshtein ties prefer
diagonal, deletion, insertion. Simultaneous speech has no unique serialization,
and common-word alignment can shift speaker strata. Low-share and overlap
counts are therefore descriptive reference-word retention measures, not
speaker identity, DER, or overlap source-separation accuracy. There is no
insertion attribution by speaker. Tiny samples do not establish broad accuracy.

Reference background: https://groups.inf.ed.ac.uk/ami/corpus/transcription.shtml
and https://groups.inf.ed.ac.uk/ami/download/ . Annotation bytes came from the
public FluidInference mirror pinned at revision
`722d8891643e1e4dc62cfd0d198fa05a1646c3cc`, archive SHA-256
`56e29112cd89cfba76fbf8f10215eb245cc89b0d3d27355ba14b406a7ea7776c`.
The original official archive could not be downloaded in the earlier acquisition;
byte identity against it was not independently established. No fresh external
assets or packages were installed for this experiment.

## Reproduction and tests

Run `qualification/ami_language_control_diagnostic_20261103.py` with explicit
`--asset-root`, `--ami-root`, `--annotations`, and a new local `--output`
directory. Use `--plan-only` first; the subsequent run must produce the same PLAN.
Use the already verified cloud runtime. The executable has a 180-second
between-decode wall bound; this run additionally used an external 240-second
timeout. There are exactly two clips, two arms and sixteen decode calls, no
retries or adaptive post-result selection.

The model-free suite passed 23 tests:

- tests/test_ami_language_control_diagnostic.py (five new tests)
- tests/test_ami_labeled_context_diagnostic.py
- tests/test_sensevoice_context_diagnostic.py

New guards cover frozen arms/window geometry, outer reference cuts, missing
minority denominator, missing overlap denominator and empty-hypothesis failure.
Tests use invented words only. This is not a complete product regression run.

Only the diagnostic source, synthetic tests and this report are authorized for
backup. Raw audio, reference text, transcripts, outputs, models, secrets and
runtime assets remain excluded. Main remains untouched.
