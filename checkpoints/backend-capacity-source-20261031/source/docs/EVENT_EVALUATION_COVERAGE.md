# Environmental event evaluation coverage

This model-free benchmark contract does not change production labels, thresholds,
models, APIs, timestamps or the legacy event matching algorithm.

## Compatibility and historical diagnostics

`scripts/benchmarks/score_predictions.py:event_score` and the `score()` result's
`events` field are unchanged. They retain the exact raw, same-string greedy IoU
>=0.3 diagnostic, including zero values and its original denominator behavior.
Existing frozen evaluations must never be rewritten. V03's AMI-only 0 matches /
972 references /22 hypotheses remains that diagnostic. Its references are vocal
noise tags and have no overlap with the six environmental evaluation targets.
AliMeeting's missing event annotations do not imply environmental negatives.

The new sibling `environmental_events` field always reports coverage, even when
an input has no `events` key. For disjoint or absent target reference coverage:

- `available=false`, `reason=no_target_reference_coverage`
- `precision`, `recall` and `f1` are null, not zero-percent environmental accuracy
- the six `unsupported_targets` and exact label-count histograms remain visible

Partial annotation sets expose `covered_targets`, `unsupported_targets`,
`all_targets_covered=false`, and `metric_scope=covered_target_subset`. Numeric
results live only under `per_target`; no aggregate turns incomplete coverage
into an overall six-target percentage. Exact positive annotations support recall
for that target, even when predictions are empty. They do not establish negative
coverage or allow unmatched hypotheses to be called false positives.

## Optional explicit negative-coverage contract

Gold input may add the following field, only when its annotation protocol really
establishes exhaustive labeling for that specific target and time support:

```json
{
  "event_annotation_coverage": {
    "schema_version": 1,
    "exhaustive_intervals": {
      "rain": [{"start": 0, "end": 30}]
    }
  }
}
```

An empty list supplies no coverage. Labels must be exact evaluation targets:
rain, driving, subway, door, music, silence. This declaration is not inferred from
filenames, source titles, empty references, other classes, or all-recording
silence. It must come from an explicit annotation protocol. Intervals must be
finite, nonnegative, ordered and positive-duration. Touching/overlapping declared
supports are unioned; gaps stay unknown. Interval units and origin must match the
original event timestamps. There is no automatic clock correction.

Per-target recall matches all explicitly annotated positives against original
same-label predictions. Precision considers only predictions wholly inside the
label's explicit exhaustive support and matches them against the known positive
references. A boundary-crossing prediction is excluded from precision, not
clipped or silently treated as a false alarm. Every excluded hypothesis is
counted. Separate recall/precision matching avoids consuming an in-scope match
with an unscored hypothesis. F1 is defined only when all positive and predicted
supports for that target are inside exhaustive coverage and both component
ratios have denominators. Otherwise it is null. `precision_identifiable` denotes
presence of exhaustive support; with zero scoped predictions the precision ratio
is still null. No-positive reference recall is null even for known negative
support. False-positive counts are null without exhaustive support.

The matching rule remains greedy descending IoU, not maximum-cardinality
matching or an acoustic onset benchmark. Zero-duration records are omitted from
the new coverage-aware result; the raw diagnostic preserves its historical
handling. New evaluation timestamps are validated as finite, nonnegative and
ordered. Coverage validation errors fail explicitly rather than inventing labels.

## Semantic boundary

No alias, ancestor or physical-source mapping is inferred: Train is neither
`driving` nor `subway`; Creak is not Door; Humming is not Music. Exact casing is
required. Production's existing broad Vehicle/Engine-to-driving mapping remains
unchanged here, but is ambiguous for a user's road-driving/subway distinction.
It must not be expanded by this evaluation fix.

The separate metadata-only rail_transport replay remains research evidence only.
Its explicit Train-family candidates are not inserted into production output,
renamed driving, or counted as accurate Subway detections. Cached weak-source
mixtures and generated controls do not certify natural-audio precision, human
audibility or independent generalization.

## Verification

Run the model-free focused tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tests/test_event_evaluation_coverage.py tests/test_lexical_metrics_fast.py
```

A read-only V03 check can load one existing source gold/prediction JSON pair at a
time, compare `event_score` byte-for-value with that source's frozen `events`
diagnostic, and call `environmental_event_score` without running ASR, reading
audio, loading models or recomputing lexical/diarization metrics. Write the new
compatibility summary outside tracked/frozen report paths. Do not mutate frozen
metadata or promote the research rail candidates.
