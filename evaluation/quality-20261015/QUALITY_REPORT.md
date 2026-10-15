# Quality findings

Status: three completed experiments. Historical aggregate results have been checked; no new model or metric run was performed for this source checkpoint. The configuration templates must be configured locally and remain unexecuted. The current product default is unchanged.

## 1. AMI English reference evaluation

The primary comparison is the common 20-second interval [511, 531) of AMI meeting ES2002a. Official AMI manual 1.6.2 lexical reference words were selected by forced-aligned word midpoint in this half-open interval, producing 56 normalized reference tokens in two separate speaker streams. The reference text is manual; the word timestamps are forced-aligned and are not independent manually verified timing ground truth.

Official MeetEval 0.4.3 was used without a custom metric implementation. Lower values are better.

| Saved output | cpWER | cpWER S / D / I | Greedy DI-cpWER | DI S / D / I |
| --- | ---: | ---: | ---: | ---: |
| Primary pipeline | 87.50% (49 / 56) | 6 / 28 / 15 | 50.00% (28 / 56) | 9 / 16 / 3 |
| MOSS diagnostic | 8.93% (5 / 56) | 3 / 2 / 0 | 8.93% (5 / 56) | 3 / 2 / 0 |

S, D, and I denote word substitutions, deletions, and insertions. The primary hypothesis contains 43 normalized tokens, including 12 tokens whose missing speaker labels remain one explicit unknown stream. MOSS contains 54 normalized tokens.

### What the two metrics mean

cpWER permits one optimal permutation of complete hypothesis streams against separate reference speaker streams for the entire scored window. It does not use separate mappings on either side of a processing seam.

The companion greedy DI-cpWER permits reassignment of intact original hypothesis segments among those fixed reference speaker streams. It retains each original primary segment or MOSS phrase as an indivisible unit, keeps chronological segment order, and does not split, merge, deduplicate, or repair words using the reference. Reference speaker streams stay separate and internally ordered. Concurrent reference speakers are never arbitrarily concatenated into a single reference transcript.

The primary error count falls from 49 to 28 under segment reassignment: a difference of 21 errors, or 37.50 percentage points. This quantifies sensitivity to attribution and segmentation under this scoring setup. It is not an isolated unknown-label penalty or a causal estimate of recognition failure. The greedy solver is not guaranteed to find a global optimum; its residual error mixes recognition, segmentation, ordering, and boundary-selection effects. Different original segmentations can also affect comparisons.

The MOSS diagnostic has fewer errors on this particular saved English excerpt under both metrics. This observation is not a production-default recommendation and does not generalize to Chinese, other acoustic conditions, longer recordings, or population-level performance. Metric assignments are evaluation alignments, not verified personal or global speaker identities.

### Boundary and coverage sensitivity

Reference words use their forced-aligned midpoint. Hypotheses are selected as whole segments by segment/model midpoint; no word timing or proportional text trimming is fabricated. Available native CTC start anchors do not cover the first outer-boundary primary segment, so they do not remove the whole-segment edge ambiguity.

For the common 20-second interval:

| Hypothesis edge policy | Primary cpWER | Primary greedy DI-cpWER | MOSS, both metrics |
| --- | ---: | ---: | ---: |
| Midpoint in interval | 87.50% | 50.00% | 8.93% |
| Any positive overlap | 87.50% | 50.00% | 8.93% |
| Fully contained | 83.93% | 53.57% | 8.93% |

The 21-second interval [510.5, 531.5) has 58 reference tokens and gives primary cpWER 82.76%, primary greedy DI-cpWER 46.55%, and MOSS 12.07% under both metrics for the midpoint case. This is unequal-coverage sensitivity only: MOSS heard [511, 531), not the full 21 seconds.

MOSS recorded `complete_content=false`, `eos=null`, and `truncated=false`. These fields do not certify completion and do not establish that actual truncation occurred. No timestamp-accuracy assessment was performed.

### Normalization

The same fixed word normalization was applied to both reference and hypotheses: Unicode NFKC, case folding, conversion of curly apostrophes to ASCII, preservation of Unicode alphanumerics and internal apostrophes, replacement of other punctuation with spaces, whitespace splitting, and removal of edge apostrophes and empty tokens. Fillers, repetitions, lexical fragments, and non-English recognized alphanumeric tokens remain error-bearing tokens. There is no contraction expansion, semantic substitution, language filtering, or character-token scoring.

## 2. Official Chinese crop/context ablation

The fixture is a 70.470625-second official Chinese example. No trusted transcript is available, so no CER, accuracy ranking, or quality recommendation is reported.

The paired experiment used the same seven contextual reads, fixed native turns and owned cores, identical production alignment and core-projection logic, and independent boundary-tail state. One shared model initialization served both arms with fresh streams, CPU execution with two threads, language auto, ITN enabled, and simplified-Chinese projection.

| Arm | Recognition calls | Submitted audio exposure | Unsubmitted owned-core duration |
| --- | ---: | ---: | ---: |
| A: native turn crops | 21 | 93.673625 s | 0.6360625 s, summed per core |
| B: full contextual reads | 7 | 94.470625 s | 0 s |

Exposure is summed across calls and includes repeated contextual audio; it is not unique fixture duration. Arm A reproduced the historical transcript exactly, including formatting. This is reproducibility evidence, not accuracy evidence. The arm B text differed, but without a trusted reference that difference cannot establish improvement or degradation.

The A coverage gap is 0.6360625 seconds when summing each window's owned-core coverage. The union of all actual A submissions mapped to the complete fixture leaves approximately 0.636 seconds uncovered. These are different accounting scopes. A geometric audio-coverage gap does not by itself identify omitted words. This experiment changes crop exposure and context together; it does not isolate an energy gate or prove a quiet-speech failure.

### Observed cost and timing caveats

- Total bounded run elapsed time: 12.748 seconds.
- One shared model initialization: 1.182677 seconds.
- Decode wall totals: A 5.288294 seconds; B 5.110035 seconds.
- Peak sampled summed process-tree RSS: 502,562,816 bytes.
- 125 resource samples; maximum observed sample gap: 0.105607 seconds.

RSS is sampled, so the recorded maximum is not a guaranteed instantaneous peak. Initialization is shared rather than independently charged to each arm. Each read ran A before B with no counterbalancing, warmup, or repeated trials; initialization, cache, and execution-order effects prevent a controlled speed comparison. Resource and timing observations describe this run only.

## 3. AMI one-call full-context control

One additional SenseVoice decode used the exact 20-second [511, 531) audio submitted to the saved MOSS diagnostic. Both PCM16 and float32 input identities were checked against that saved support. There was one model initialization and one recognition call. Existing primary and MOSS baseline scores were reused without rescoring or regrouping.

The same 56-word reference, normalization, common-window midpoint policy, and official MeetEval 0.4.3 metrics were used:

| Common 20-second output | Hypothesis words | cpWER | cpWER S / D / I | Greedy DI-cpWER | DI S / D / I |
| --- | ---: | ---: | ---: | ---: | ---: |
| Prior primary pipeline | 43 | 87.50% (49 / 56) | 6 / 28 / 15 | 50.00% (28 / 56) | 9 / 16 / 3 |
| SenseVoice one-call full context | 39 | 83.93% (47 / 56) | 14 / 25 / 8 | 41.07% (23 / 56) | 4 / 18 / 1 |
| Saved MOSS diagnostic | 54 | 8.93% (5 / 56) | 3 / 2 / 0 | 8.93% (5 / 56) | 3 / 2 / 0 |

The full-context control has two fewer cpWER errors and five fewer greedy DI-cpWER errors than the prior primary output. Its 39 normalized words include 13 in the explicit unknown stream. This is a small, fixture-specific numerical change, not a general accuracy claim. The full-context DI result has 18 deletions, versus 16 in the prior primary result; its lower total error count does not by itself demonstrate recovery of previously missing speech.

### Projection is also part of the comparison

Retained native turns were reconstructed from the original two read/core geometries and checked against the saved canonical turns. The saved registry mapping was applied before one common-window alignment and lexical ownership projection. This produces 13 indivisible scored segments, compared with 16 selected segments in the original primary baseline. It is not the same segmentation/projection path with only crop duration changed. In particular, the greedy DI-cpWER difference cannot be attributed solely to longer recognition context.

No new reference-guided boundaries, word splitting, text repair, arbitrary reference-speaker concatenation, or global speaker-identity inference was introduced. This local control does not establish API-level equivalence, timing accuracy, or a production-default recommendation. It also does not support extrapolation to Chinese.

### Observed resources

The single decode, projection, and official scoring completed in 3.867 seconds total. Peak sampled combined process RSS was 469,585,920 bytes. The model and scoring stages completed successfully. These are single-run sampled observations; they are not a controlled performance comparison with the multi-call Chinese experiment or a guaranteed instantaneous memory ceiling.

## Conclusion and scope

The completed work establishes a reproducible Chinese crop/context comparison, an explicit reference-based English diagnostic, and a one-call English control with a disclosed projection difference. It provides no Chinese quality ranking, no causal quiet-gate diagnosis, no global speaker-identity verification, and no basis for changing the current product default. Other experiments are outside this checkpoint.

## Source references

- [Official AMI manual annotations 1.6.2](https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip)
- [AMI transcription documentation](https://groups.inf.ed.ac.uk/ami/corpus/transcription.shtml)
- [MeetEval 0.4.3 release metadata](https://pypi.org/pypi/meeteval/0.4.3/json)
- [Official cpWER implementation](https://raw.githubusercontent.com/fgnt/meeteval/v0.4.3/meeteval/wer/wer/cp.py)
- [Official greedy DI-cpWER implementation](https://raw.githubusercontent.com/fgnt/meeteval/v0.4.3/meeteval/wer/wer/di_cp.py)
