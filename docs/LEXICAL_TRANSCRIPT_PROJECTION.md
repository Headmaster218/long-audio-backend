# Opt-in estimated-boundary lexical transcript projection

This experimental display view is requested explicitly:

`GET /sessions/{sid}/transcript?projection=lexical-estimated-v1`

Omitting the mode, or using `whole-primary-v1`, preserves the existing policy and
cursor identity. Unknown modes, duplicate parameters, and projection parameters
on evidence or binding-audit routes are rejected. A cursor is bound to its mode,
session, block and current transcript revision; a cross-mode cursor is 409.
Retention remains default-off. This view does not enable retention, inference,
speaker learning or historical reconstruction.

## What it can show

A current primary segment with validated, primary-bound CTC inventory can supply
whole interior lexical groups to an exact-adjacent selected MOSS support. The
original prefix and suffix survive as exact substrings. Original full `p.*`
parents remain in `baseline_reading` and affected alternatives; alternatives also
identify replaced lexical spans. The active reading never combines a full parent
with its fragments. Complete MOSS phrases retain their own order, repetitions,
punctuation, model timestamps and source-local speaker scopes.

Every original primary character is owned exactly once by the kept-or-replaced
partition, independently in raw and display strings. Standalone punctuation,
empty and whitespace contributions attach to the preceding lexical group;
leading decoration attaches to the first. Joined ASCII words are indivisible.
Adjacent equal-native-anchor groups merge. No tokenization, OpenCC, normalization,
spacing rewrite, time sorting or cross-recognizer punctuation deduplication is
performed. Unsupported/mixed-track/classification-disagreeing inventories remain
opaque full parents. Historical NULL inventories are never reconstructed.

Selection uses the existing whole-primary discovery closure, support-frame DAG,
maximum-support ranking and stable candidate tie-breaks. All intersecting groups
and opaque simultaneous parents must fit the run's exact-adjacent support union.
A crossing group rejects the run; an internal join may bisect a group only when
the whole run union contains it. A support in an internal estimated gap cannot
insert a candidate without replacing a lexical-bearing group.

Canonical kept spans and replaced spans per run are built globally before page
filtering. Each lexical span has its own envelope-start home block, with full
text there and references on other intersecting pages. Full parents keep their
original homes. Numeric unit indices define ordinal ordering.

## Timing limitations

CTC starts are stored post-adapter context-shifted, millisecond-rounded native
anchors. Ends are estimates from the next anchor/decode end and may have been
clamped or shortened at speaker turns. Unit coordinates use the original order:

`origin + speaker_window_start + (unit_time - core_start)`

Display envelopes use the existing outward `primary_frame` conversion. Nothing
is clamped to candidate support, core, source chunk or page. This is an estimated
boundary display preference, not forced alignment or certified word timing.
`acoustic_coverage` is `unproven`, `word_times` is null, `complete_content` is false,
and no new person identity is claimed. Complete phoneme containment and ASR
quality improvement have not been established by this view.

## Exact evidence ranges

The authenticated existing evidence route accepts a bounded reference:

`l.<segment-id>.<inventory-sha256>.<lexical-policy-sha256>.<first>-<exclusive-end>`

Indices address complete stored units with `0 <= first < end <= 512`; the actual
inventory length is checked. Any contiguous stored-unit range can be retrieved,
even if it is not group-aligned or selection-eligible. Its response explicitly
says `range_semantics: stored_complete_unit_range`, `selection_eligibility:
not_asserted` and `selection_status: not_asserted`. The policy hash binds range
interpretation, not a claim that a range was selected.

The response contains exact original units, independent raw/display spans and
substrings, original timing, derived session times using the arithmetic above,
parent evidence reference, inventory hash and bounded stored provenance. All
contributor source rows and primary/inventory binding are revalidated in the same
snapshot. This is not fresh PCM attestation. Missing parent/NULL inventory is
404. Malformed/stale content, hash/policy mismatch or invalid range is 409.
Database, dependency, deadline and VM errors remain failures.

## Admission and fallback

Existing limits remain 128 primary rows, 16 result bodies, 1,024 bindings,
128 charged chunk IDs, 256 KiB response, 0.75-second snapshot and 250,000 VM steps.
Additional limits are 512 admitted validated lexical units, 128 lexical groups
and 128 total projection atoms across the closed inputs. Primary payload bytes
and all loaded inventory bytes share the existing 256 KiB primary-read budget.
Each inventory is length-admitted at 64 KiB maximum, then validated one at a
time. Malformed JSON can materialize more small entries before schema rejection;
this is a raw-byte staging bound. Source lookups use a snapshot-local cache of
exact source fields sharing closure's charged IDs, including descriptor aliases.
Each unseen source ID is charged before its indexed SELECT.

The admission set includes every parent with an eligible candidate's outer
endpoint strictly inside its envelope, in stable numeric parent-ID order. New
span associations share the existing binding cap. No fragment copies the full
parent canonical content or inventory blob.

Individual missing/stale/unsupported inventories remain opaque. Explicit optional
admission or serialization overflow discards the entire lexical/MOSS overlay:
`reading == baseline_reading`, empty selected runs and alternatives, incomplete
`lexical_projection`, and false `selection_performed`/`alternatives_complete`.
It does not retry whole-primary MOSS selection. Per-item optional notices collapse
to one bounded aggregate with the observed notice count and incomplete evidence;
that count does not claim a complete history census. Baseline evidence links survive;
optional speaker annotations cannot erase fallback uncertainty. An over-cap
baseline still receives the existing refusal.

## Qualification status

Version 0.2.0rc3.dev36's behavior passed one independently reviewed, bounded,
synthetic, model-free invocation: 224 cases (94 new lexical cases and 130 existing
projection/component/HTTP/current-inventory regressions), without exclusions.
Pytest took 9.57 seconds; the complete guarded stage took 10.47 seconds. Sampled
combined process-tree RSS peaked at 80,572,416 bytes, fresh fixtures at 1,298,432
bytes, and all accounted artifacts at 18,931,712 bytes. CPU 3/nice 19, the 256 MiB
RSS/1 GiB address-space/16 MiB fixture limits, and the 120-second total envelope
held. The owned child was positively waited, no observed process remained, and
fresh fixtures were removed. Source/control hashes were unchanged before/after.

The matrix covers independent raw/display conservation and punctuation ownership,
exact/one-frame boundaries, adjacent unions and gaps, simultaneous opaque parents,
nonzero origins/overhang, actual two-page reads in both orders, stale references,
restart, cumulative budgets, near-64-KiB malformed JSON staging, charged descriptor
aliases, hard read errors, and dense-notice/optional-speaker response fallback.
No model, audio, historical database or native inference runtime was used. This
establishes the display/evidence contract on synthetic inputs; actual-model
Chinese overlap integration and recognition quality still require a separate
approved trial. Estimated ends never establish certified phoneme containment.
