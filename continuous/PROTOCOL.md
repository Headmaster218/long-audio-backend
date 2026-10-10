# Continuous fixed20 protocol v1

## Cohort and pre-output feasibility revision

Use the same eight pinned AliMeeting far-field recordings in selection.json.
Original PCM is 16 kHz, signed 16-bit, eight channels; extract channel index 0
unchanged. No gain, resampling, denoising, silence deletion, overlap filtering,
utterance selection, context prompt, or forced language is introduced.

For each source of N frames, the desired start is
30 * floor(N / (16000 * 90)) seconds; desired end is start + 90 seconds.
Parse nonempty official TextGrid intervals, outward rounding to sample frames.
Union all speakers' annotated speech intervals. Candidate outer edges are
integer-frame midpoints of internal all-speaker gaps at least 0.5 seconds long.
Choose a start/end pair each within 300 seconds of its corresponding target,
with excerpt duration 60 through 120 seconds inclusive. Minimize, in order:
(1) sum of absolute endpoint displacements, (2) absolute duration difference from
90 seconds, (3) start frame, (4) end frame. No clipping or partial outer turns.

The initial ±15-second feasibility rule admitted 2/8 meetings; ±180 admitted 7/8.
The ±300 rule was explicitly selected and frozen before inference. These were
post-hoc annotation-feasibility revisions, not model-output screening. The same
eight source meetings remained fixed. Selected edges preserve every intersecting
gold turn completely; no acoustic boundary-gold claim follows from annotation.

## Fixed calls and normalization

Tile each excerpt from its selected start in disjoint 320,000-frame (20-second)
windows, retaining the last short window. Internal cuts do not use reference
boundaries. Cover all 11,734,160 frames exactly once per arm: 41 calls each.
Runtime/model versions and settings are inherited from ../runtime.json and
../assets.json: both arms automatic language, fresh session/stream per window,
Qwen native generation cap 256 IDs including EOS; no cap increase for this run.

Serialize complete reference turns by start, end, tier, interval. Concatenate
window predictions in chronological order with no inserted separator. Failed,
aborted, unknown or not-attempted calls contribute empty prediction text while
keeping their failure status. Truncated predictions retain emitted text and
truncation status. Do not treat exit code 0 as recognition success.

Apply Unicode NFKC, lowercase, remove punctuation/symbol categories P/S, remove
split whitespace, then character Levenshtein unit costs. Ties prefer diagonal
(match/substitution), then deletion, then insertion. Report whole-excerpt and
pooled S/D/I and fixed 4,182-character denominator. No per-window edit allocation
without alignment. Overlap serialization is order-sensitive, not permutation
invariant. Annotated overlap burden is separate from error attribution.

Repetition suspicion: first normalized-character span with minimal period 1..8,
at least eight copies and at least 32 characters, extended by complete copies.
This is a diagnostic flag, not a proof of why generation repeated. Public
metadata retain positions/period/counts, never the repeated content.

## Original gate, immutable FAIL

Require candidate pooled CER no worse than baseline, at least six of eight
meetings nonregressing, no candidate failures, no truncations, no repetition
suspicions, and successful bound execution/provenance checks. Observed quality
fails pooled CER, meeting coverage, truncation and repetition requirements;
execution verification passes. No advancement permission was earned.

Any later status-triggered splitting/retry diagnostic is a separately frozen
experiment. It must preserve this original FAIL and its scores, denominators,
selection and statuses. It cannot retroactively change this protocol or gate.
Historical training exposure is unknown. These excerpts are not an unseen,
representative or population-level noninferiority test, nor production evidence.
