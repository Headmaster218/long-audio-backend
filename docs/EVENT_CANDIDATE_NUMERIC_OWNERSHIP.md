# Event candidate numeric ownership

This is a standalone dev8-derived source fix, not a release or a dev10 merge.
It changes only candidate ownership and its diagnostic persistence. The event
detector, ASR, speaker models, thresholds and database schema are unchanged.

## Contract

- Intersections are checked on the detector's 16 kHz PCM grid with an
  uncertainty-aware half-up tie convention. A positive floating-point sliver
  between integer-aligned detector supports and touching core boundaries can
  have no sample support. Such an intersection is excluded from the candidate
  table and receives `numeric_degenerate_sample_grid_intersection` diagnostics.
- Integer-frame supports and half-frame endpoint constructions within the stated
  roundoff bound survive at either parity. Surviving float times are retained;
  this is not a minimum-duration filter or a timestamp snapping operation.
- If session-timeline addition cannot represent a positive owned interval or
  support, the row receives `numeric_degenerate_absolute_projection` diagnostics
  instead of an invented duration or a failing SQLite positive-interval check.
  An unrepresentably large origin can therefore exclude even a genuine sample.
- Candidate validation still runs before numeric exclusion. Candidates remain
  unverified model-window evidence. Exclusion never promotes or relabels an event.

## Bounded tie convention and limits

For endpoint `t`, use `x = t * 16000`, `k = floor(x)` and the half-frame midpoint
`k + 0.5`. The uncertainty band in frame units is
`(3 * ulp(t) * 16000 + ulp(x)) / 2`. Values within that band below the midpoint
resolve upward, as does an exact tie. This recovers half-frame division roundoff
without a fixed time epsilon. The three input-rounding terms cover two rounded
nonnegative terms and their rounded sum, followed by the scale multiplication.
For example, tests cover independently divided frame endpoints and an endpoint
formed as `start + 1 / 16000` throughout the supported 0..120-second input range.

This convention is not strict half-up rounding of every exact supplied float,
and it does not bound arbitrary arithmetic histories. Cancellation/rebasing can
carry errors larger than the ULP of the final local time. A synthetic subtraction
counterexample is retained as an explicit limitation test; generic rebase
invariance and global timestamp sample precision are not established.

Both sides of a shared boundary use the same deterministic endpoint map.
Sub-sample spans inside one quantization cell are excluded, while a sub-sample
span crossing a transition can own a frame index. Its original fractional float
duration is preserved, never expanded to a full sample. Diagnostics record the
actual input intersection and resulting frame indices, including exclusions
outside the supported error bound.

## Durable diagnostics

`chunks.provenance.event_candidate_projection_diagnostics` holds a total count,
retained records and an explicit `truncated` flag. Retention is limited to 64
records and a 64 KiB audit budget, accounting for storage's Unicode escaping and
wrapper overhead. Oversized records can be omitted; later records that fit may
still be retained. The count includes omitted records.

Each retained record includes the reason, candidate index, raw timing, sanitized
candidate evidence and a SHA-256 hash of the original candidate JSON. The existing
public-label filter removes private voice labels, and arbitrary candidate fields
are not copied into this new durable evidence. This is bounded sanitized evidence,
not retention of the entire arbitrary raw payload. Original candidate metadata is
left unchanged in the existing last-window metadata field.

Diagnostics survive later empty or silence windows independently of stable model
provenance. They commit in the same transaction as the owned core. A rollback
preserves retry input; a rejected replay does not duplicate rows or audit counts.

## Model-free verification

Run `python -m unittest discover -s tests -p test_event_candidate_numeric_projection.py -v`.
The suite also supports pytest. It generates all inputs from simple constants,
16 kHz frame positions and `math.nextafter`; it loads no audio, weights or captured
model outputs. Coverage includes ULP slivers, adjacent cores, one-sample ties,
representable and unrepresentable origins, evidence filtering, Unicode byte caps,
rollback/replay, unchanged schema, and unaffected main/event/speaker ownership.
The endpoint sweep is exhaustive across 1,920,000 frame positions below 120
seconds; full ownership/store tests cover selected cases, including formerly
collapsed ties and the 2.5-second detector-hop touching boundary.

These are numeric and transactional checks. They do not establish transcription
quality, event recognition accuracy, or whole-recording completion.
