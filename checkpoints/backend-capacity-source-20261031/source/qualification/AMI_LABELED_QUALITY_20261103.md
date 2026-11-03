# AMI human-reference context and activity diagnostic, 2026-11-03

## Decision

Keep the backend unchanged. On this small preselected labeled set, two seconds of
context increases lexical errors and decreases low-share-speaker word matches.
This is stronger evidence than whole-input agreement, but still only four short
clips from two related AMI sessions. No model, default, rejection threshold,
primary-only gate, speaker policy or user data is modified.

## Source and frozen selection

The official AMI documentation describes human transcription with multiple review
passes, and automatically forced-aligned word times:
https://groups.inf.ed.ac.uk/ami/corpus/transcription.shtml

The official archive fetch through the web tool returned HTTP 403. The existing
public mirror was downloaded without authentication from its pinned revision:
https://huggingface.co/datasets/FluidInference/ami-corpus-mirror/tree/722d8891643e1e4dc62cfd0d198fa05a1646c3cc

Its repackaged `annotations/ami_public_manual_1.6.2.zip` is 22,887,865 bytes,
SHA-256 `56e29112cd89cfba76fbf8f10215eb245cc89b0d3d27355ba14b406a7ea7776c`.
The mirror declares identical annotation content and CC BY 4.0 attribution to the
AMI Consortium/University of Edinburgh. Archive byte identity with the unavailable
upstream download was not independently established. Individual XML member hashes
are recorded locally; reference text is never included in the Git deliverable.
Corpus and license source: https://groups.inf.ed.ac.uk/ami/download/

Label-only selection was frozen before model output. Four 26.25-second clips start
at EN2002a 1260/1667 seconds and EN2002b 1078/1119 seconds. They are disjoint from
earlier 60/180-second crops. Each contains exactly five 5.25-second scored cores.
Zero and two-second symmetric context use the same scored audio and reference.
The annotation intervals show no reference word crossing an outer crop boundary;
this is not a claim of perfect acoustic boundary timing.

The clips contain 88 / 60 / 90 / 91 normalized reference words: **329 total**. They are
fresh clips from related recordings, not independent-speaker validation. All
speakers individually having at least ten words and 10–30% of that clip's words
are included in the low-share stratum, yielding 45 / 24 / 11 / 31 words: **111 total**.
Pooling several qualifying speakers may exceed 30% of a clip; the criterion is
per speaker. No selection or thresholds were changed after observing predictions.

Frozen plan SHA-256:
`553bdcc540a62e626acc9b726ca2dd027f49349dedac39d61dd31ae67f1c1d1d18`.
The actual ASR run's plan is byte-identical to this pre-inference plan.

## Human-reference lexical results

Same pinned SenseVoice int8 model, CPU, automatic language, real adapter and lexical
ownership code in both arms. Synthetic all-active turns isolate recognition and
ownership; this is not a real diarization/ASR service integration.

For zero-context→two-second-context:

- EN2002a@1260: errors 37→36 of 88; low-share correct 27→28 of 45.
- EN2002a@1667: errors 13→18 of 60; low-share correct 15→10 of 24.
- EN2002b@1078: errors 18→17 of 90; low-share correct 8→7 of 11.
- EN2002b@1119: errors 29→37 of 91; low-share correct 18→9 of 31.

Aggregate conventional time-sorted-reference WER is **97/329 (29.48%)→108/329
(32.83%)**. Zero-context operations: 33 substitutions, 64 deletions, 0 insertions;
two-second-context: 24 substitutions, 82 deletions, 2 insertions. Low-share reference
word matches fall **68/111→54/111**. This is lexical matching, not speaker recovery.

Near interior core boundaries, reference words with a forced-aligned midpoint
within 0.5s have correct matches 30/52→32/52. Using word start instead produces
30/51→31/51. This changes the boundary stratum only, not transcript membership,
ordering or full WER. Slightly better boundary matching does not compensate for
worse overall or low-share results. It is not ground-truth timestamp accuracy.

Scoring is unit-cost Levenshtein over NFKC/casefolded ASCII letter/digit tokens,
retaining internal apostrophes and lexical fillers, with deterministic diagonal,
deletion, insertion tie priority. Numbers are not expanded, so written-number
conventions can contribute errors. ASCII-only normalization is English-specific;
all saved decode-window languages were English. It is not unrestricted multilingual
WER. Punctuation and non-word/transformerror XML elements are excluded and counted.

115 of 329 reference words have interval overlap with another speaker. Time-sorting
overlapping speech is order-sensitive. The remaining 214 reference words match
171→164 times, but this is secondary: their C/S/D assignments still come from the
same global alignment influenced by overlap. A common word spoken by another
speaker can match a low-share reference word. No speaker-specific insertions, DER,
identity accuracy or independent clean-speech score is claimed.

## Actual native activity check, separate from ASR

The original pinned Nemotron-3 Q8 native provider and its verified runtime were
run on each same isolated clip, with fresh epochs, explicit finalization and
positive close. Models and postprocessing are unchanged. No persistent identity
or cross-clip state is learned. This is a standalone provider check, not the strict
service lifecycle or a long-running streaming evaluation.

Native cluster counts are 4/3/2/3, equal to the reference speaker counts in these
clips. Of 111 low-share reference-word midpoints:

- Any native activity exists at 109.
- An **optimistically fitted same-clip one-to-one** reference-speaker/cluster
  permutation covers 101: 39/45, 22/24, 11/11 and 29/31 by clip.
- 40 midpoints have multiple predicted active clusters.

**These are coverage-only counts, not native diarization accuracy or recovered
speakers.** The oracle mapping uses the evaluation labels themselves. The metric
does not penalize extra active clusters or speech predicted during silence: keeping
all clusters active continuously could score perfect coverage. A synthetic test
explicitly exhibits that limitation. Forced-aligned midpoint uncertainty also
applies. The separate ASR and native summaries cannot establish a causal diagnosis
of missing words or end-to-end speaker-attributed transcription success.

## Verification and limits

- 40 real ASR adapter calls, 8 paired rows; local summary SHA-256:
  `9798be1019ae83332dbef2423ae1437685e5b587b87d7325873921c9dd105a64`.
- Four actual native finalized clips, provider closed, original command exit 0;
  local summary SHA-256:
  `b6c23eecb1ed0615577789aeb395f2bae7b8ae1528562ecfcdba05432b2b7bd7`.
- Sixteen new model-free scorer/parser/mapping tests plus existing diagnostic and
  boundary tests: **58 passed**. Not a full repository pass.
- Independent review verified frozen plan, source hashes, interval bounds,
  arithmetic, model/runtime provenance fields and interpretation limits.
- Source frame ranges and hashes remain bound; all saved output intervals remain
  in the actual decoded source window. This does not qualify durable service
  provenance packets, crash/restart, false alarms, longer recordings or identity.

Reproducible tools are `ami_labeled_context_diagnostic_20261103.py` and
`ami_native_midpoint_diagnostic_20261103.py`. Use `--help`; first run ASR
`--plan-only`, preserve that plan, then run ASR and pass that frozen plan to the
native diagnostic. Execute under external memory/CPU/wall limits, with existing
pinned local assets. The native diagnostic also checks a 1.5 GiB host-memory reserve.
The recorded runs used two OpenMP/BLAS threads, 4 GiB address-space limits and
260s/200s wall deadlines respectively.

Only tools, tests and this aggregate report are authorized for Git backup. Raw
annotation archives, reference text, audio, hypotheses, models, databases and
local run outputs stay outside the source checkpoint. The next improvement needs
more independently labeled speakers/conditions and real-service evaluation of
word retention, false positives and attribution, not tuning to these four clips.
