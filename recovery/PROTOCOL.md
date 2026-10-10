# Two distinct bounded recovery experiments

## A: post-hoc known-failure diagnostic

Keep the original eight-excerpt continuous selection, original 4,182-character
reference denominator, original raw parent outputs/statuses, and original FAIL.
Only the two original native-status-18, explicitly truncated full 20-second Qwen
parents are eligible. Their identities were known from the completed failed run.
Each is split into exactly two contiguous 10-second children, with fresh sessions
and unchanged model/language/context/cap settings. No recursion or other retries.
Use left-then-right child text only when both children succeed and are untruncated;
otherwise preserve unresolved parent output/status. Never mix parent and child
text, choose by reference accuracy, or discard original results. Report the
whole-cohort counterfactual separately from the original failed screen.

## B: prospectively frozen new-support screen

Same eight official AliMeeting far-field sources, unchanged channel index 0,
16 kHz signed PCM16. Choose new excerpts disjoint from every prior continuous
excerpt and staged Chinese oracle clip in that same source; exact half-open
sample interval intersection rejects a candidate. These are new time supports,
not new meetings or a demonstrated unseen-training set.

With source duration T seconds, desired start is 30 * floor(2*T/90) seconds,
and desired end is start + 90 seconds. This targets roughly two-thirds through
the source. Candidate outer edges are integer-frame midpoints of internal
all-speaker annotation gaps at least 0.5 seconds long, each within ±300 seconds
of its target. Require duration 60..120 seconds and complete intersecting gold
turns. Rank by total endpoint displacement, absolute duration difference from
90 seconds, then start frame, then end frame. No automatic relaxation or source
replacement. All data admission rules and policy were frozen before outputs.

Retain silence, overlap and final short windows. Tile each chosen excerpt into
fixed disjoint 20-second windows plus its final short window: 40 base calls per
arm, of which 32 are full 20 seconds. Predeclare two contiguous 10-second children
for each full parent (64 potential children), but execute children only on a
native status 18 plus was_truncated=true full-length parent. A truncated final
short window stays unresolved. Maximum depth is one; never expand the cap.

Only both native-status-0, untruncated children permit replacement, in left/right
order. Child repetition is a quality failure, never a text-selection filter.
No references, scores, language labels or output repetition detection trigger a
retry. No mixing of parent and child text, no history/cache/context carryover.
Keep first-pass and recovered results and all actual-call counts. Potential
children whose parents do not trigger are unattempted, not failed calls.

Prospective execution used the actual pure recovery module with material input,
source/configuration binding and distinct observed fresh native sessions; source
pins use the equivalent public qualified tree in pins.json. This is distinct
from production-adapter/end-to-end integration. The baseline runtime API and
model configurations are unchanged.

## Scoring and immutable outcomes

Use inherited continuous protocol normalization: NFKC, lowercase, remove Unicode
P/S and whitespace, character Levenshtein unit costs, diagonal/deletion/insertion
tie order. Concatenate predictions in time order and complete reference turns
by start/end/tier/interval. Compare at whole-excerpt and pooled levels, with fixed
denominators per experiment. Overlap makes chronological text CER order-sensitive;
no character-error attribution to individual windows is asserted.

The prospective gate requires recovered pooled Qwen CER no worse than SenseVoice,
at least 6/8 nonregressing meetings, resolved final windows, no final truncation
or repetition flags, and successful bound execution. It failed meeting coverage
(4/8), despite overall gain and clean final statuses. No advancement permission
is earned. The post-hoc diagnostic has no clean first-pass eligibility regardless
of descriptive checks. The earlier continuous gate remains immutable FAIL.
