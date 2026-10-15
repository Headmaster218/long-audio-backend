# Oyez cross-date speaker verification pilot

Research date: 2026-10-15. Sanitized aggregate research report.
This custom 18-identity experiment tests open-set speaker matching on
annotation-labeled English Supreme Court speech. It is not an official Oyez or
SCOTUS benchmark. Independent listening and browser-to-decoder time-origin
verification remain pending.

## Main result

On held-out identities, the unchanged fixed policy recovered 2 of 12 known
queries. A policy selected using calibration only recovered 7 of 12. The
remaining known queries abstained. Neither policy made an observed wrong-known
assignment or accepted any of the six unknown queries. This small result does
not establish a population false-accept rate or product readiness. No product
threshold, model weight, template or product code changed.

Each role has a separate six-person gallery and three gallery-absent people.
Each person contributes two 10-second probe halves from one 20-second crop.
Calibration and held-out identities and case files are disjoint.

| Role and policy (cosine threshold, margin) | Known correct /12 | Known wrong /12 | Known abstain /12 | Unknown accepted /6 | Unknown rejected /6 |
|---|---:|---:|---:|---:|---:|
| Calibration fixed (0.75, 0.10) | 0 | 0 | 12 | 0 | 6 |
| Calibration selected (0.65, 0.15) | 5 | 0 | 7 | 0 | 6 |
| Held-out fixed (0.75, 0.10) | 2 | 0 | 10 | 0 | 6 |
| Held-out selected (0.65, 0.15) | 7 | 0 | 5 | 0 | 6 |

Held-out known recovery is 16.7% versus 58.3%; accepted coverage across all 18
queries is 11.1% versus 38.9%. False-person assignments are known-wrong plus
unknown-accepted, totaling 0/18 for each policy. Correct-person precision among
accepted held-out queries is 2/2 and 7/7. Overall abstention is 16/18 and 11/18;
rejecting unknowns is desirable, so this is not an error rate. Calibration
recovery is an in-sample optimization result.

The selected policy recovered both halves for two of the six known held-out
people, one half for three, and neither half for one. The fixed policy recovered
both halves for one person and neither half for five. These are descriptive
counts; two halves of the same recording are correlated.

## Method

1. Freeze a metadata-only selection of 18 opaque identities and 20 source case
   recordings before acquisition or model inference. Split into nine identities
   per role, with six known and three gallery-absent identities in each. Source
   recordings span 2014-10-08 through 2017-03-27. Paired sessions have official
   dates 27–694 days apart. Known identities use an earlier enrollment and a
   later probe from different cases. Earlier sessions for unknown identities
   provide provenance only and are excluded from the gallery and scoring.
2. Acquire complete source MP3s and decode from their beginning with FFmpeg to
   mono 16 kHz PCM16. Center 10-second enrollment and 20-second probe crops
   within preselected annotated turns using declared integer sample indices.
   The minimum annotation-interior margin is 5.1155 seconds. Exact decoded
   sample lengths and non-clipping waveforms do not prove speaker purity.
3. Retain all 30 planned crops: twelve enrollments and eighteen probes. Divide
   each probe into two nonoverlapping 10-second halves. No score-driven
   exclusion, replacement, movement or denominator reduction is allowed.
4. Use the frozen eres2net-base-zh-cn-3dspeaker-16k-v1 extractor on CPU, serially,
   with two extractor threads. Convert PCM16 to float32 divided by 32768;
   L2-normalize finite nonzero 512-dimensional embeddings. Compare by float32
   dot product. This produces 48 embedding calls over 480 seconds of input.
   Each role contributes 18 queries against six enrollment templates: 12
   genuine pairs, 60 wrong-known pairs and 36 unknown-to-gallery pairs, for
   108 pair scores per role and 216 overall.
5. Rank gallery scores descending. Accept only when the best score is at least
   tau and exceeds the second score by at least the margin. Exact top-score
   ties abstain. Keep the fixed policy at tau=0.75 and margin=0.10.
6. Fit the study policy on calibration only. Search tau in {0.55, 0.65, 0.75,
   0.85, 0.95} and margin in {0.05, 0.10, 0.15}, plus always-abstain. Minimize
   false-person assignments, then maximize known correct recoveries, then
   prefer larger tau and larger margin. Always-abstain wins if no zero-error
   candidate recovers a known query. Persist the selected (0.65, 0.15) policy
   after the 24 calibration calls and before any held-out embedding. Apply
   both policies once to an immutable, separate held-out gallery.

The policy freeze records zero held-out embeddings. All 48 planned embedding
calls and all 216 pair scores completed. Gallery hashes remained unchanged.
Independent arithmetic recomputation reconciled all 216 pair scores within
1e-6 absolute tolerance, every decision and aggregate, and all 16 calibration
candidates. It also reconciled the frozen inputs, unchanged galleries and
freeze-before-evaluation chronology. This numerical check does not replace the
pending acoustic checks. No model was trained or fine-tuned in this experiment.
Identity overlap with pretraining is unknown, even though the two experiment
roles are disjoint.

## Execution and limitations

The completed inference run took 17.50 seconds. Sampled peak RSS was 329,396,224
bytes over 172 samples, with a maximum sample gap of 0.13084 seconds. The sampler
joined and recorded no child process; the run exited with code 0. These are
sampled observations; brief
peaks and stalls can be missed. They exclude acquisition time and are not a
machine-wide memory or latency guarantee.

An initial multi-output FFmpeg acquisition failed under its memory constraint.
The completed continuation used one output per crop decode, retained verified
complete inputs, and excluded partial crop outputs. All 30 planned crops were
present for scoring; this acquisition issue did not reduce the denominators.

Official annotations supply identity labels and turn boundaries. Manual checks
of attributed-speaker consistency, audible speech, interruptions and overlap
have not been completed. Equivalence between browser playback timestamps and
the full-file decoded timeline is also unverified. Interior margins and exact
sample counts cannot establish independently verified single-speaker speech.

Six unknown query halves represent only three people in each role. Pair scores
share queries and enrollment templates. These dependencies and the small,
selected sample prevent a population FAR, low-error guarantee or reliable tail
estimate. No EER, demographic inference, broad speaker-recognition assurance,
ASR result, diarization result or production-readiness claim is made.

Keep these results separate from CN-Celeb. Language, enrollment duration,
recording conditions and sample composition differ; any difference cannot be
attributed solely to cross-date separation. The observed within-pilot change
compares two decision policies on the same frozen scores, not two models.

## Reuse and provenance

PROTOCOL.json records the aggregate design, threshold-selection contract,
counts and qualifications without sample mappings. It is a protocol
specification, not an executable replay of the private inference run.

The existing [CN-Celeb model-free policy reference](../cnceleb-verification-20261015/score_policy.py)
at checkpoint 7a6883098cf7ac41629000f0664583e8fee0eb97 documents the same
acceptance gates and calibration objective. Its seven-person-gallery and
20-query validation are CN-Celeb-specific, so it cannot directly consume this
six-person-gallery, 18-query design. That source is unchanged and is not
copied into this package.

Completed-result SHA-256:
98abfb3de07d91b4580b0a774cdf734dc8196c36c6832e969604be32e588fa72.
The package manifest records file sizes and hashes, excluding its own hash to
avoid self-reference. Exact sample mappings and raw trial artifacts are not
included, so this package supports inspection of the reported design and
aggregates rather than exact audio-level reproduction.

## Attribution and public references

Source material: [Oyez](https://www.oyez.org/), under its
[CC BY-NC 4.0 license notice](https://www.oyez.org/license). Research and
noncommercial use still requires appropriate attribution and respect for
underlying rights. The [Creative Commons license](https://creativecommons.org/licenses/by-nc/4.0/)
does not imply Oyez endorsement or resolve rights it does not license.
Audio was decoded, resampled and cropped for this pilot. This package
redistributes no audio, transcripts, embeddings, per-person scores or source
identity mappings.

The published [SCOTUS preparation recipe](https://github.com/cvqluu/MTL-Speaker-Embeddings/blob/main/scotus_data_prep/step3_prepdata.py)
is a public methodological reference for segmented speech preparation; this
custom pilot does not reproduce the published study protocol. See also
[FFmpeg documentation](https://ffmpeg.org/ffmpeg.html) for the decoding tool.
