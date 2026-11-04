# Prospective multi-reference trial contract

Research-only planning adapter, standard library only. It reads JSON text and
returns JSON text. It does not read recordings, load models, compute embeddings,
score trials, select thresholds, modify a bank, delete raw recordings, or access
Git. No product files are modified. The existing product already implements an
f64 normalized-mean centroid over up to eight reviewed references in
`audio_backend/verified_identity.py:centroid_score`; this is not another centroid
implementation.

This contract supports only the **D declared-disjoint-session design**. It does
not implement the M design of multiple turns from one meeting/session; those
turns cannot fill separate reference slots here. This is an **oracle / externally
verified clean-reference research arm**, not a
solution for unattended automatic speaker grouping. External review supplies the
identity and speech/overlap evidence that the automatic system does not yet
reliably supply. A verified metadata claim does not prove actual speaker purity.

## Use and freeze boundary

```python
from multireference_trials import freeze_manifest, plan_trials

frozen_json = freeze_manifest(input_json_text)
plan_json = plan_trials(frozen_json)
```

Both results are immutable strings. The plan includes the complete canonical
manifest plus its SHA-256, all observation decisions, and both split denominators.
Input list order does not change the freeze or plan. The caller must preserve the
frozen JSON and its hash out of band **before** computing model outputs. The hash
is not a signature or an append-only storage service. Editing, relabeling, or
refreezing inputs changes the snapshot; this module cannot prevent a caller from
discarding an older freeze.

Run only the synthetic tests:

```sh
python -m unittest discover -s backend-speaker-aggregation-research-20261104/contract -v
```

`test_multireference_trials.fixture()` is a complete synthetic manifest example.
Its hashes name invented data. No examples claim to correspond to real recordings.

## Fields and why they exist

Every object has exactly the listed fields. Unknown keys, duplicate JSON keys,
duplicate row IDs, nonfinite numbers, and malformed types fail validation.
Identifiers are bounded opaque tokens, never file paths.

| Object | Fields | Purpose |
| --- | --- | --- |
| Root | `schema_version`, `protocol_id`, `cohort` | Freeze schema and prospective protocol identity. Only `new_prospective` is admitted; this is a declaration, not a verified historical claim. |
| Root | `content_hash_scheme` | Must be `pcm_s16le_mono_v1`, defined below. Prevents treating a container-file digest as a canonical audio-content digest. |
| Root | `reference_count` | Fixed 2–8 references per known person for the entire protocol. No adaptive count or single-reference fallback. |
| Root | `space` | `model_id`, `frontend_id`, `space_id`, exact `model_sha256`, exact `frontend_config_sha256`. Pins declared model and frontend artifacts; IDs alone are insufficient. |
| Root | `persons`, `sessions`, `sources`, `observations` | Explicit bounded registries; no hidden source lookup or discovery. |
| Person | `person_id`, `split`, `enrollment` | One externally reconciled canonical person ID belongs to exactly C or T and is `known` or `unknown`. Unknown people never supply reference observations. |
| Session | `session_id`, `acquisition_order`, `recorded_on` | Declared capture-event identity, unique global order, and ISO `YYYY-MM-DD` date or null. Supplied dates must agree with order. Different files from one event must retain the same session ID. |
| Session | `declaration_origin`, `evidence_id` | External review or oracle benchmark provenance for the session declaration. Different labels do not establish independence. |
| Source | `content_sha256`, `session_id`, `sample_rate`, `total_frames` | Canonical content identity, event provenance, frame coordinate system, and hard crop bounds. One content hash cannot be aliased as another session or sample rate. |
| Observation | `observation_id`, `person_id`, `split`, `role` | Immutable observation identity, ground-truth identity/split assignment, and a prospectively declared `reference` or `query` role. |
| Observation | `space_id` | Must match the pinned model/frontend space. This is a declared binding for a future executor to verify. |
| Observation | `source_content_sha256`, `start_frame`, `end_frame`, `crop_sha256` | Binds the half-open frame interval `[start_frame, end_frame)` to source and crop content. Bounds are integers, not seconds or floats. |
| Observation | `identity` | `origin`, `evidence_id`, `verified`: independently supplied identity evidence, never a model prediction promoted into truth. |
| Observation | `speech`, `overlap` | Each has the same provenance fields plus `status`. Speech: `speech`, `non_speech`, `unknown`. Overlap: `single_speaker`, `overlap`, `unknown`. Separate evidence prevents identity review alone from asserting clean audio. |

Evidence origins are `external_review`, `oracle_benchmark`, `native_prediction`,
or `missing`. Native predictions cannot have `verified=true`. Missing evidence
requires `verified=false`, a null evidence ID, and an unknown status. Oracle and
reviewed evidence remain distinguishable in the frozen manifest; they are never
relabeled as native system performance. Evidence IDs identify external records;
the adapter does not retrieve or authenticate those records.

### Canonical content hash definition

For this version, externally prepared canonical audio is signed 16-bit
little-endian **mono** PCM. Its hash is SHA-256 over:

```text
ASCII("pcm_s16le_mono_v1\n" + decimal_sample_rate + "\n" + decimal_frame_count + "\n")
followed immediately by the raw PCM bytes
```

Decimals have no leading zeros. The crop hash uses the source sample rate,
`end_frame - start_frame` as frame count, and the corresponding source slice.
The producer's frozen frontend configuration must specify deterministic decoding,
channel selection/downmixing, and resampling, including implementations/versions.
The source digest is **not** a WAV/MP3/container-file digest. Original source
recordings should remain preserved externally; canonical PCM is a research view,
not permission to replace or delete originals.

This module checks declared hash syntax and collisions, not actual bytes. A later
executor must recompute all model/frontend/source/crop hashes and verify crop
bounds before embedding. Identical canonical content cannot be relabeled into a
second source/session. Different encodings, lossy transforms, resampling, edits,
or false hashes can still hide semantic duplicates. No claim is made that all
re-encoded or correlated audio has been detected.

## Fixed selection and eligibility

1. Reject any person shared between C and T under the same canonical ID. Reject
   any declared source session containing observations from both splits, even
   for different people, different files, or disjoint frame ranges.
2. Reject globally repeated crop hashes and any overlapping frame spans on the
   same canonical source. Adjacent spans are valid; they still do not constitute
   distinct sessions. A source hash matching any observation's crop hash is a
   known content alias and is rejected unless that observation is a self-consistent
   full-source crop of its own source. A full-source crop must have exactly its
   source hash. This prevents a known crop from being repackaged as a new source
   and then subcropped to evade session/split isolation.
3. A reference needs external/oracle verified identity, speech, and single-speaker
   evidence. Missing evidence fails closed with explicit ledger reasons. Native
   predictions cannot generate, relabel, or qualify references.
4. Freeze a gallery before the **earliest scheduled query date in that split**.
   Every reference date must be strictly earlier. Missing reference/query dates
   and same-day date-only records cannot establish precedence and are unusable.
   Any session containing a query is excluded from reference selection globally.
5. For each known person, select the first eligible observation per declared
   session, then the first fixed number of sessions in declared acquisition order.
   Within a session, the deterministic tie-break is source hash, start frame,
   end frame, observation ID. Cross-source hash order is not a claim of within-
   session capture chronology. Embeddings and scores never affect selection.
6. The single-reference arm uses the first observation in that exact selected
   multi-reference set. Insufficient distinct eligible sessions block both arms;
   two crops from one session cannot fill two slots. Extra or same-session
   observations remain in the ledger with reasons.
7. Compare every scheduled query against the same split's complete known-person
   gallery. Unknown-person queries retain their unknown target. This is a compact
   gallery/query plan, not an expanded pair matrix.

These are **declared disjoint sessions**, not statistical independence. Session
truth, identity alias reconciliation, chronology, provenance authenticity, and
source completeness remain the external data curator's responsibilities.

## Query conditions and denominator preservation

This compares clean references. Queries may come from noisy operational data;
noise does not become a claim of clean speech. Every query has a condition label
and explicit externally-verified speech/overlap flags. For this version's
single-speaker identity-accuracy evaluation, overlapping, non-speech, unknown, or
unverified queries are `single_speaker_accuracy_usable=false` with reasons. They
remain in total known/unknown query denominators and in the complete manifest.
They are not silently counted as valid single-speaker targets or dropped.

Each C/T split reports total, known, unknown, usable, and unusable query counts,
including usable-known and usable-unknown counts. The whole plan is `ready` only
when **both** splits have complete galleries, at least one usable known query and
one usable unknown query, and every scheduled query is usable. Otherwise it is
`blocked` with reasons. A blocked plan is a diagnostic deliverable, not a completed
experiment or permission to remove inconvenient observations. `selected_for` records
planned arm membership, never execution; the top-level blocked status takes
precedence. Readiness is only
contract completeness, not evidence of statistical power or model quality.

## Experiment boundary and limits

- Use entirely new prospective C/T data. Do not convert current T queries into
  references, rerun on the same holdout, or tune reference selection after scores.
  `cohort` and the frozen hash cannot independently prove historical non-reuse.
- Before running new data, freeze the single-vs-multiple protocol, sample size,
  reference count, metrics, and handling/reporting of unusable observations.
- In a separately authorized executor, calibrate each arm independently on new C,
  freeze its policy, then evaluate once on new untouched T. Do not automatically
  inherit historical research thresholds 0.46 or 0.55. There is no calibration,
  threshold selection, evaluation, or model execution in this adapter.
- The existing production threshold of 0.75 is unchanged by this work and remains
  uncalibrated here; retaining it is not an accuracy or qualification claim.
- The existing centroid implementation, not new aggregation math here, remains
  the future scoring dependency. This work makes no accuracy-improvement claim.
- Integer capacities: at most 256 people, 4096 sessions/sources/observations each,
  4 MiB JSON input, 2–8 references, sample rates 8000–192000, and frame/order values
  no greater than `2**53 - 1`. Booleans are never accepted as integers.
