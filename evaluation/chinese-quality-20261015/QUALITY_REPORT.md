# AISHELL-4 two-window character evaluation

Status: two completed historical comparisons. Historical aggregate results and fixed-assignment arithmetic have been checked. No model or evaluator was run to prepare this checkpoint. The base source template and its derived variant remain unconfigured and unexecuted. The product default is unchanged.

## First window: [437, 454), 17 seconds

The experiment used channel 0 of official AISHELL-4 recording M_R003S01C01 over [437, 454): exactly 17 seconds at 16 kHz, or 272,000 samples. The reference contains seven complete manual segments, 79 normalized characters, two speaker streams, and approximately 2.026731 seconds of overlapping speech. No manual segment is cut by the selected interval. No character-level timing reference is available or fabricated.

The primary adapter retained its usual native turn/crop strategy, but its sole recognition crop in this run was the entire 272,000-frame interval. Both its PCM16 and float32 input identities matched MOSS. Thus this particular comparison has no input-duration or actual cropping difference. Architecture, alignment, output segmentation, and the systems' unchanged script treatment still differ.

## Character-error results

Official MeetEval 0.4.3 word-error functions were applied to individual normalized character tokens and labeled cpCER and greedy DI-cpCER. Lower values are better.

| System | cpCER | cpCER S / D / I | Greedy DI-cpCER | DI S / D / I |
| --- | ---: | ---: | ---: | ---: |
| Primary direct adapter | 39.24% (31 / 79) | 15 / 15 / 1 | 36.71% (29 / 79) | 13 / 15 / 1 |
| MOSS direct diagnostic | 24.05% (19 / 79) | 8 / 10 / 1 | 24.05% (19 / 79) | 8 / 10 / 1 |

S, D, and I denote character substitutions, deletions, and insertions. Primary output contains 65 normalized characters in eight segments, including nine characters in the explicit unknown stream. MOSS contains 70 normalized characters in six phrases and two local output labels.

MOSS has fewer character errors on this fixed sample under both metrics. This is a single-sample result, not a general accuracy estimate or a reason to change the current product default. It does not establish API-level equivalence or verify speaker identity.

### Metric interpretation

cpCER compares separate internally ordered reference speaker streams to whole hypothesis streams with a scorer-minimized permutation over the complete interval. Greedy DI-cpCER additionally permits assignment of intact hypothesis segments to those reference streams. It preserves the original segment boundaries and chronological (start, original-index) ordering, keeps the explicit unknown stream in the input, and never concatenates simultaneous reference speakers into one arbitrary transcript.

The primary's two-error reduction under DI reflects sensitivity to segment-to-stream reassignment; it does not isolate the cause of unknown labels or prove a recognition improvement. The greedy solver is not guaranteed globally optimal, and the systems have different original segmentation. Residual errors mix recognition, attribution, alignment, segmentation, ordering, and script-treatment effects. Metric assignments are evaluation alignments, not verified global or personal identities.

### Fixed cpCER assignment by reference stream

The following decomposition keeps each system's selected cpCER permutation and alignment fixed. The rows sum to the headline cpCER totals; they are not separately optimized per-stream scores or a DI breakdown.

| Reference stream | Characters | Primary errors | Primary S / D / I | MOSS errors | MOSS S / D / I |
| --- | ---: | ---: | ---: | ---: | ---: |
| Longer stream | 70 | 26 | 10 / 15 / 1 | 18 | 8 / 9 / 1 |
| Shorter stream | 9 | 5 | 5 / 0 / 0 | 1 | 0 / 1 / 0 |
| Total | 79 | 31 | 15 / 15 / 1 | 19 | 8 / 10 / 1 |

The shorter reference stream contains one six-character content segment and three one-character backchannels. Under this fixed alignment, MOSS matches all six content characters and two backchannels, with one backchannel deletion. Primary matches four content characters, substitutes two content characters, and substitutes all three backchannels.

Primary's unknown stream is aligned to that shorter reference stream. A three-character content prefix occurs once in each reference speaker stream, for two copies globally. Primary places both copies in the unknown stream, while MOSS places one in each output stream. This is mixed stream placement, not evidence of false or excess duplication. The chosen fixed alignment accounts for the relevant differences as substitutions rather than extra insertions; each system still has one metric insertion on the longer reference stream. These statements describe the fixed sequence alignment, not character-level timing, a unique semantic explanation, or verified speaker identity. No transcript or reference text is reproduced here.

### Normalization and script treatment

The fixed transform strips <...> tags, applies Unicode NFKC and case folding, removes Unicode punctuation/symbol code points and whitespace, then retains every other Unicode code point as one character token. Fillers, repetitions, digits, and non-Chinese characters remain. Reference strings must match their pre-existing normalized form exactly.

Primary output retains its production simplified-Chinese conversion. MOSS is scored from its returned phrase text without an added script conversion. Scoring does not add reference-guided repair, deduplication, invented character timing, or partial-segment trimming. Invalid/out-of-support output intervals are rejected rather than clipped; MOSS schema errors are rejected rather than selectively scoring surviving phrases.

## First-window calls and completion limits

The primary used one recognition crop and one SenseVoice decode. MOSS used one construction, one session, and one run, with context 816 and generation reserve 512. The exact same 17-second PCM was supplied without padding.

MOSS EOS and generated-token count are unavailable. Its complete-content flag is false, with no reported abort or truncation. These observations do not certify complete transcription and do not establish that an actual truncation occurred.

This is a direct-adapter experiment. The primary retained the existing native diarization, events/embedding behavior, crop strategy, auto-language/ITN settings, and simplified-script treatment; MOSS retained its existing profile and phrase processing. The result does not qualify a full API workflow.

## Held-out interval: [422.5, 437), 14.5 seconds

The second interval is acoustically disjoint from the first. It was selected before its recognition run using the fixed rule: take the earliest complete eligible remainder of the existing acquired extent, with real two-speaker overlap, using the reference intervals rather than recognition output. This is held out from the first diagnostic, not an independent recording or a claim about model-training data. It has the same speakers and recording conditions.

The interval contains three complete manual segments and 47 normalized reference characters: 43 in the longer stream and four in the shorter stream. Two overlap spans total approximately 1.107632 seconds. The annotated margins are 0.73575 seconds on the left and 0.03168 seconds on the right. The narrow right margin is a boundary limitation despite no annotated segment being cut.

| System | cpCER | cpCER S / D / I | Greedy DI-cpCER | DI S / D / I |
| --- | ---: | ---: | ---: | ---: |
| Primary direct adapter | 44.68% (21 / 47) | 10 / 10 / 1 | 40.43% (19 / 47) | 8 / 10 / 1 |
| MOSS direct diagnostic | 25.53% (12 / 47) | 9 / 2 / 1 | 25.53% (12 / 47) | 9 / 2 / 1 |

Primary returns 38 normalized characters in four segments, including three unknown-stream characters. MOSS returns 46 characters in three phrases and two local output labels. The same character transform, standard metrics, whole-segment ordering, unknown-label policy, and script treatment apply.

The top-level adapters receive the same selected 14.5-second interval, while actual recognition support differs slightly: primary's sole crop/decode covers relative frames [2896, 232000), or [0.181, 14.5), totaling 14.319 seconds. The omitted 0.181 seconds precede the first annotated manual segment at 0.73575 seconds. MOSS receives the complete 14.5 seconds without padding. This is not the identical recognition-input case observed in the first window, and the margin evidence is not character-level timing gold.

The held-out fixed cpCER decomposition also preserves the selected permutation and alignment:

| Reference stream | Characters | Primary errors | Primary S / D / I | MOSS errors | MOSS S / D / I |
| --- | ---: | ---: | ---: | ---: | ---: |
| Longer stream | 43 | 17 | 7 / 9 / 1 | 12 | 9 / 2 / 1 |
| Shorter stream | 4 | 4 | 3 / 1 / 0 | 0 | 0 / 0 / 0 |
| Total | 47 | 21 | 10 / 10 / 1 | 12 | 9 / 2 / 1 |

The shorter stream is one four-character content segment. Under the fixed alignment, primary's unknown stream contributes three substitutions and one deletion; MOSS matches all four characters. This is very small sequence-alignment evidence, not a stable per-speaker estimate, character-timing evidence, or proof of personal identity.

MOSS again used one construction, one session, and one run. MOSS uses duration-derived context 783, generation reserve 512, and the unchanged native adaptive ceiling of 492. The primary still uses one recognition crop/decode. The result is another fixed-window observation; four characters in the shorter reference stream are insufficient for a stable speaker-specific quality estimate.

MOSS has fewer measured character errors on both tested windows. Two intervals from one recording do not establish general accuracy, independence across speakers/acoustics, a general crop-duration explanation, or a product-default recommendation. No pooled benchmark score is claimed.

## Observed resources

| Observation | First 17-second window | Held-out 14.5-second window |
| --- | ---: | ---: |
| Total elapsed time | 28.264624 s | 26.544 s |
| Peak sampled combined RSS | 1,625,255,936 bytes | 1,625,432,064 bytes |
| Resource samples | 270 | 252 |
| Maximum sample gap | 0.108301456 s | 0.119032845 s |

Elapsed time covers the experiment's setup, native/primary processing, MOSS stage, and official scoring, rather than isolated model decode latency. RSS is a sampled observation, not a guaranteed instantaneous peak. The sequential stages and differing workloads do not provide a controlled cross-model speed comparison.

## Historical acquisition qualifications

The promised acquisition pacing was not enforced. The original metadata-writing invocation also failed after successful audio extraction. Those facts remain limitations of the historical acquisition; successful scoring must not be presented as proof that acquisition completed without exceptions.

The fixed source, reference selection, and input identities were retained and checked for this comparison. No audio or manual reference text is included in this public checkpoint. The dataset's official access and license terms must be checked separately before obtaining or redistributing its content.

## Scope

This checkpoint establishes explicit, fixed-reference Mandarin diagnostics on two short, disjoint overlapping-speech intervals from one recording. It does not establish population-level performance, a general crop-duration explanation, API behavior, character-timing accuracy, verified speaker identity, or a change to the product default.
