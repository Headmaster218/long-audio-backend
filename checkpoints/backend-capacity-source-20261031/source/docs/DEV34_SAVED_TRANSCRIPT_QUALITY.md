# dev34 saved transcript quality comparison

This comparison scores the saved `baseline_reading` and unified `reading` outputs
from `0.2.0rc3.dev34`. The unified reading improves both main metrics on all three
AMI occurrences. Only Chinese occurrence 2 improves; the other four readings and
scores are unchanged. Speaker-aware error rates remain poor. These are exposed
repeated-source diagnostics: five repeats of one 31.5-second Chinese source and
three repeats of one 20-second AMI source. They are not independent samples and
support no confidence interval, pooled-language result or general quality claim.

See [unified transcript semantics](UNIFIED_TRANSCRIPT.md) and
[copied-database API qualification](DEV34_COPIED_TRANSCRIPT_API.md). Schema and
API correctness alone do not establish transcript quality.

## Method and interpretation

Official MeetEval 0.4.3 supplied cpWER and approximate greedy DI-cpWER; the same
functions supplied cpCER and greedy DI-cpCER over normalized Chinese codepoints.
Existing normalization was unchanged: English uses NFKC, case folding and
word tokenization with normalized apostrophes; Chinese strips markup, applies
NFKC and case folding, then removes whitespace, punctuation and symbols. English
word and Chinese character denominators are kept separate.

Scoring used intact saved units, their order, scoped speaker streams and explicit
unknown content. Home-source units were selected once; page-reference copies
were excluded. Repeated lexical strings remained score-bearing. Competing
alternatives were never concatenated; there was no text repair, string clipping,
text deduplication or invented word/character timing. Job-local labels were not
merged into global identities or replaced using recording-level links.

There are 4–7 emitted scoped streams for two reference speakers. cp scoring keeps
those streams distinct; greedy DI permits different assignment and is approximate
and segment/order-dependent. The cp–DI gap measures assignment sensitivity, not
pure speaker-identity error or a causal attribution penalty. MOSS-generated times
remain unverified. An absent speaker-probe assessment is unassessed; incomplete
content and unavailable EOS do not certify completion.

## Main results

B means baseline and U means unified. Every cell shows B → U. Errors are
substitutions + deletions + insertions (S/D/I); rates are errors divided by the
reference denominator and can exceed 100%. Each Chinese occurrence has 126
reference characters; each AMI occurrence has 56 reference words.

| Source occurrence | cp errors | cp S/D/I | Greedy DI errors | Greedy DI S/D/I | Streams |
|---|---:|---|---:|---|---:|
| Chinese 1 | 156 → 156 | 12/85/59 → 12/85/59 | 48 → 48 | 20/27/1 → 20/27/1 | 5 → 5 |
| Chinese 2 | 173 → 142 | 18/88/67 → 16/69/57 | 47 → 42 | 26/21/0 → 26/14/2 | 6 → 7 |
| Chinese 3 | 150 → 150 | 19/78/53 → 19/78/53 | 50 → 50 | 21/27/2 → 21/27/2 | 5 → 5 |
| Chinese 4 | 155 → 155 | 18/81/56 → 18/81/56 | 48 → 48 | 21/26/1 → 21/26/1 | 6 → 6 |
| Chinese 5 | 157 → 157 | 14/84/59 → 14/84/59 | 45 → 45 | 18/26/1 → 18/26/1 | 6 → 6 |
| AMI 1 | 62 → 47 | 3/36/23 → 4/21/22 | 28 → 9 | 9/16/3 → 4/2/3 | 4 → 4 |
| AMI 2 | 60 → 48 | 10/34/16 → 7/20/21 | 26 → 15 | 4/20/2 → 8/3/4 | 6 → 7 |
| AMI 3 | 64 → 44 | 2/38/24 → 3/19/22 | 30 → 8 | 12/16/2 → 3/1/4 | 6 → 4 |

AMI unified cpWER remains 83.93%, 85.71% and 78.57%; greedy DI-cpWER is 16.07%,
26.79% and 14.29%. Chinese occurrence 2 changes from 137.30% to 112.70% cpCER
and 37.30% to 33.33% greedy DI-cpCER. The four unchanged Chinese occurrences
retain cpCER of 123.81%, 119.05%, 123.02% and 124.60%, and greedy DI-cpCER of
38.10%, 39.68%, 38.10% and 35.71%, respectively.

Chinese occurrence 2 replaces six primary units with three selected MOSS units.
The other four Chinese occurrences receive none. AMI replaces 16, 12 and 18
primary units and adds 8, 10 and 10 MOSS units, respectively. AMI retains 0, 3 and 0
primary units. All selections use existing saved output.

## Minority and overlap associated matches

Under the fixed official greedy-DI assignments, minority-speaker exact token
matches change as follows:

- AMI: 8 → 15, 6 → 15 and 8 → 16 out of 19 reference words per occurrence.
- Chinese occurrence 2: 4 → 9 out of 13 reference characters. Occurrences 1, 3,
  4 and 5 remain at 4, 4, 4 and 5 exact matches out of 13.

Counts condition on one assignment and optimal edit path. They do not verify
global speaker identity or time-local recall; tied paths can move token errors,
and insertions are unlocalized. Full S/D/I totals agree with the
official scores, and minority S/D/I was checked with official SISO scoring.

AMI has 27 reference words whose annotated intervals overlap another speaker's
annotated word interval. Greedy-DI exact matches on these words improve 14 → 23,
15 → 24 and 10 → 24. Nine are minority words, with exact matches 0 → 6, 0 → 8
and 0 → 7. For Chinese, every selected complete reference segment overlaps
another speaker somewhere. The whole overlap-containing-segment category covers
all 126 characters and cannot be interpreted as overlap-only CER.

## Boundary sensitivity

Main midpoint and any-positive-overlap selection contain exactly the same whole
units. Each main window has one crossing hypothesis unit in each reading.
Fully-contained selection removes that unit but retains the full reference
denominator; it can discard genuine in-window speech and is neither a preferred
policy nor an accuracy bound. Chinese right overhang is 0.100–0.149 seconds into
scheduled digital zero. AMI left context may contain speech, and its pinned
boundary-crossing reference word is retained.

The fully-contained comparison retains the main improvement direction:

| Source occurrence | Contained cp errors B → U | Contained greedy DI errors B → U |
|---|---:|---:|
| Chinese 1 | 155 → 155 | 49 → 49 |
| Chinese 2 | 172 → 141 | 48 → 43 |
| Chinese 3 | 150 → 150 | 51 → 51 |
| Chinese 4 | 154 → 154 | 49 → 49 |
| Chinese 5 | 156 → 156 | 46 → 46 |
| AMI 1 | 59 → 45 | 30 → 15 |
| AMI 2 | 56 → 44 | 27 → 16 |
| AMI 3 | 60 → 44 | 31 → 14 |

Boundary ambiguity remains material. In Chinese occurrence 2's adjacent
17-second diagnostic panel (79 reference characters), midpoint and contained
scores are unchanged, but any-positive-overlap worsens cp errors 106 → 110 and
greedy DI errors 41 → 47 because a different intact crossing unit is included.
The preceding 14.5-second panel improves under all three policies. These panels
are boundary diagnostics, excluded from the main conclusion; adjoining panels
must not be recombined by clipping their strings.

## Separate standalone comparators

Old standalone MOSS outputs have different support and context. Listed once each,
they remain separate, never concatenated or pooled with the main readings.

| Comparator | Reference denominator | cp and greedy DI errors | S/D/I for each metric | Rate |
|---|---:|---:|---|---:|
| AMI | 56 words | 5 | 3/2/0 | 8.93% |
| Chinese 17 seconds | 79 characters | 19 | 8/10/1 | 24.05% |
| Chinese 14.5 seconds | 47 characters | 12 | 9/2/1 | 25.53% |

## Execution and verification

One saved-output scoring run covered 95 cases. Exact ordered-input caching
reduced 190 requested cp/DI invocations to 112 unique calls; 24 official SISO and
48 alignment calls brought the total to 184 external evaluator/alignment calls.
Elapsed scoring time was 0.630 seconds. Peak sampled combined RSS was 84,557,824
bytes (80.64 MiB), sampled every 20 ms, within the fixed 60-second, 256-MiB
combined-RSS and 8-MiB fresh-artifact envelope. These scoring costs do not measure
model inference latency or continuous peak memory.

All 25 pinned data inputs and six evaluator/runtime pins were unchanged.
Independent read-only verification accepted the stored assignments, exact cache
keys, token and S/D/I conservation, minority checks and boundary findings without
rerunning metrics. No model/audio rerun, database work, threshold tuning,
projection tuning or product change occurred.
