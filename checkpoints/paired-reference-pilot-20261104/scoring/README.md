# Paired reference scoring, research only

`paired_scoring.py` is executable without audio or model inference. It consumes future, separately validated saved native vectors and compares one frozen reference against all three references for each enrolled person. It never edits product code or the earlier calibration/holdout utilities.

## Fixed comparison

- Predictor: `split`, `gallery`, `queries`, `primary_queries` only. Each gallery row is `{person_id, reference_ids:[first, second, third]}`. Lists are frozen and order-sensitive. At least two gallery people are required.
- Both arms use exactly the same gallery people, queries and primary subset. `single` uses the first reference; `multi3` uses all three. A missing/invalid reference, including one unused by `single`, makes **both entire galleries unavailable**. No reduced gallery or dropped query denominator.
- Each finite 512-dimensional vector is normalized by the exact product `support_vectors.normalized_vector`. Both scores invoke the exact product `verified_identity.centroid_score`; there is no reimplementation of centroid mathematics.
- Calibration independently selects each arm on the prespecified one-query-per-speaker primary subset: thresholds 0 through 1 inclusive in .01 increments; margin .10; zero observed known-wrong and unknown-accepted outputs; maximize known-correct; ties prefer higher thresholds. At least one correct known output is required. If either arm lacks a useful candidate, neither arm is released to T.
- The original .46 threshold is not used. There is no fixed-threshold comparison in the T report. The legacy selection helper internally computes .75 validation counts, but these counts do not choose thresholds or enter the paired comparison.
- One atomic, exclusive paired lock holds both thresholds. It binds current/imported source bytes, protocol, both complete predictor populations, C saved vectors, sealed C predictions and C selection report. Before T, C scores and selections are recomputed from those sealed inputs. An externally retained lock SHA is required.
- T predictions and locked-threshold decisions are atomically sealed before parsing T truth for reporting. T cannot create another lock or choose another threshold. Primary and all-query diagnostic counts are separate; all-query includes primary and is not an extra independent sample.
- Holdout reports include primary-only one-sided 95% Clopper-Pearson calculations solely as hypothetical conditional-iid reference calculations. They are explicitly **not formal population or safety confidence intervals**, do not establish independence, and provide no paired-effect interval. C selected-point intervals and all-query intervals are omitted.

## Source and truth schemas

Truth maps every query ID to exactly:

```json
{"kind":"known", "person_id":"gallery-person-ID", "speaker_id":"official-speaker-ID", "session_id":"meeting-ID"}
```

For unknowns, `kind` is `unknown` and `person_id` is null. One primary per source speaker is required; known primaries cover the entire gallery exactly once. All query speakers have a primary. Unresolved truth blocks reporting.

`source-evidence.json` has `schema:1` and `records`, with one record per frozen reference or query. Additional fields are allowed. Required record fields:

```json
{"id":"sample-ID", "split":"C", "role":"reference", "kind":"known", "person_id":"gallery-person-ID", "speaker_id":"official-speaker-ID", "session_id":"meeting-ID", "recording_day_label":"raw-XML-day-label", "meeting_phase":"a", "turn_id":"annotation-turn-ID"}
```

For queries, `role` is `query` and `meeting_phase` is `d`. All references are known and phase `a`; their person/source speaker must match the known queries. Three reference turns must be distinct within one meeting. The known phase-d query meeting is different but has the same raw day label. C and T share no sample IDs, gallery person IDs, or source speaker IDs, including unknowns. Truth is checked against these records.

**Phase a to d is scenario-phase ordering, not proven wall-clock chronology.** Raw day labels are compared for equality, never parsed into guessed dates. Three turns in one phase-a meeting are **not** the strict D3 three-session contract. The source metadata supplies identity/role claims; this code does not certify physical identity or listening-confirmed purity.

## Native-vector and upstream responsibilities

Supported vectors:

- JSON object of sample ID to 512-number array, useful for synthetic tests.
- NPZ with `ids` (unique string vector), `normalized` (N by 512 floating-point array), and `valid` (N-length boolean mask), matching the unchanged native runner. Additional raw/repeat arrays are ignored. Invalid masked rows remain unavailable. Duplicate JSON keys, extra vector IDs and malformed NPZ dimensions/types fail closed.

Before calling the scorer on real inputs, the parent must separately verify the complete bounded native run: frozen manifest/plan, source/runtime/model pins, actual waveform/crop and source annotation bindings, positive process termination receipt, call/resource limits, full native result/journal/vector seals, and repeat checks. This scorer does not run that process or attest that a vector came from a particular audio file/model. Include the null-protocol C/T native-plan-template pins and source/acquisition proof in the frozen source-evidence file. Freeze the protocol before C inference; then substitute its hash into the actual C manifest. Release the actual T manifest only after retaining the paired-lock pin. No protocol hash cycle is needed.

Known incomplete primary predictions block a lock. Missing diagnostic query vectors retain their rows but cannot tune the primary threshold. A complete native-run gate must reject incomplete real runs upstream.

## Commands

Use the existing environment, without installation:

```sh
PY=backend-frontend-comparison-20261104/research-runtime/bin/python
S=backend-paired-reference-pilot-20261104/scoring/paired_scoring.py
$PY -B "$S" freeze \
  --c-predictor C.predictor.json --c-truth C.truth.json \
  --t-predictor T.predictor.json --t-truth T.truth.json \
  --source-evidence source-evidence.json --output PROTOCOL.json
```

Retain the printed protocol SHA outside the file. After separately validating C native completion:

```sh
$PY -B "$S" score --protocol PROTOCOL.json --protocol-sha256 "$PROTOCOL_SHA" \
  --split C --vectors C/native/vectors.npz --vectors-sha256 "$C_VECTOR_SHA" \
  --output C.predictions.json
$PY -B "$S" report --protocol PROTOCOL.json --protocol-sha256 "$PROTOCOL_SHA" \
  --predictions C.predictions.json --predictions-sha256 "$C_PREDICTIONS_SHA" \
  --output C.report.json --output-lock PAIRED_CANDIDATE_LOCK.json
```

If status is `stop_before_T`, stop. No lock is produced. If both candidates lock, retain the printed lock SHA and release T under the native manifest contract. After separately validating T native completion:

```sh
$PY -B "$S" score --protocol PROTOCOL.json --protocol-sha256 "$PROTOCOL_SHA" \
  --split T --vectors T/native/vectors.npz --vectors-sha256 "$T_VECTOR_SHA" \
  --lock PAIRED_CANDIDATE_LOCK.json --lock-sha256 "$LOCK_SHA" \
  --output T.predictions.json
$PY -B "$S" report --protocol PROTOCOL.json --protocol-sha256 "$PROTOCOL_SHA" \
  --predictions T.predictions.json --predictions-sha256 "$T_PREDICTIONS_SHA" \
  --output T.report.json
```

All output creation is exclusive and atomic. Never change a frozen input in place and regenerate a pin after looking at its scores. A failed run requires an explicit new protocol/version, not silent replacement. Hashes are audit/sequence guards, not an adversarial security boundary. Source-role metadata contains labels, so this is not a label-blinded experiment.

## Synthetic preflight

```sh
backend-frontend-comparison-20261104/research-runtime/bin/python -B -m unittest discover \
  -s backend-paired-reference-pilot-20261104/scoring -v
```

This directory's tests use only synthetic vectors and metadata in temporary directories. No old C/T data, real recordings, model loading, downloads, network or Git operations are needed.
