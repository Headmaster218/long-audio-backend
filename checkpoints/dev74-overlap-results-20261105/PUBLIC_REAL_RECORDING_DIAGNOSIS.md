# Saved real-recording error diagnosis

This is a posthoc analysis of the five completed real-recording candidate outputs. It uses saved outputs only, changes no official score, and performs no new inference or tuning. The original run failed; its Chinese primary output was recovered through the one explicitly permitted formal retry. Overall qualification remains false, including the separately retained digital-zero failure.

## What failed in real speech

- **English overlap 1:** 15 reference words matched, with one insertion and no substitutions or deletions. The minority stream is exact under the frozen normalization. The insertion is a short interjection relative to the annotation; without an acoustic audit, it should not be called proven nonexistent speech.
- **English overlap 2:** 15 matches, three deletions in the minority-assigned stream, and two insertion words in an extra stream. Two of the three deleted reference words occur intact in that extra stream. This supports a channel-fragmentation diagnosis plus one omitted partial-word unit, rather than five wholly missing words. All three minority deletions intersect the other voice's forced-aligned word support. The official result remains five edits out of 18 words; no streams are merged or rescored.
- **Mandarin overlap:** 58 matched characters, seven substitutions and one insertion, with no deletions or extra stream. The minority reference contributes five substitutions and one insertion out of 28 characters; the other reference contributes two substitutions out of 37. The measured problem is primarily character substitution rather than omission. Utterance-level annotations do not locate individual errors in time, and the text does not establish an acoustic cause.
- **English solo:** 18 matches, one substitution on a partial-word unit, and one deletion near the end. There is no second reference speaker. These errors show that the remaining recognition problems are not confined to overlap.
- **Mandarin solo:** 73 matches, five substitutions, five deletions and one insertion in the assigned stream, plus one insertion character in an extra stream. One deleted reference character also occurs in that extra stream, consistent with possible fragmentation or relocation. The official result remains 12 edits out of 83 characters.

The extra streams are anonymous output channels, not proof of extra physical people. Exact-token presence in an extra stream is a descriptive diagnostic, not proof of its speaker or correct temporal location. Noise, masking, echo, annotation omissions and causal acoustic explanations remain unverified.

## Timing is a separate limitation

Under the unchanged oracle voice assignment, simultaneous predicted spans intersect the reference overlap regions for:

- English overlap 1: 0.47 of 1.12 seconds, 41.96%.
- English overlap 2: 1.05 of 2.09 seconds, 50.24%.
- Mandarin overlap: 0.94 of 1.70 seconds, 55.29%.

These are interval coverage diagnostics, not word accuracy. English overlap 1 matches every reference word yet covers less than half the annotated simultaneous activity in its generated timestamps. There were no malformed, adjusted, clipped, missing or out-of-range spans in the five real-recording cases, so syntax validity does not establish alignment accuracy.

English references have forced-aligned word times. Mandarin references have human utterance spans, which can include gaps within an utterance. The two coverage measures therefore have different annotation resolution and should not be pooled into one score. Individual Mandarin character errors cannot be assigned to specific overlap instants.

## Implications for the next work

1. Preserve lexical content while investigating fragmented local output channels. Do not automatically merge channels or promote them to persistent speaker identities.
2. Treat generated timestamps as unverified diagnostics. They are not a validated source for automatic interval entry or exact overlap boundaries.
3. Evaluate any automatic interval-entry mechanism separately on fixed, annotation-defined intervals with complete boundary context, without using these case scores to choose new examples.
4. Keep the candidate optional and reviewable. The small exposed panel and retained silence failure do not qualify automatic replacement of the primary transcript.

## Reproducible evidence

- `PUBLIC_REAL_RECORDING_ERROR_ANALYSIS.json`: exact per-voice edit counts, English error coordinates and extra-stream lexical overlap, without transcript tokens.
- `analyze_saved_lexical_errors.py`: uses the frozen scorer's deterministic alignment tie order. Its trace was checked against the scorer on all 961 pairs of binary-token sequences of length zero through four.
- `POSTHOC_TIMING_COVERAGE_RECOVERY01.json`: all reference and generated interval coordinates, anonymous ordinals and coverage counts.
- `posthoc_timing_coverage01.py`: saved-output interval analysis, independently cross-checked on a 16 kHz frame bitmap.
- `PUBLIC_SCORE_RECOVERY01.json` and `PUBLIC_RECOVERY01_OUTCOME.json`: the unchanged frozen official result and its failure, recovery, provenance and resource limits.

All reports remain transcript-free. Raw text and audio stay private.
