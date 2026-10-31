# dev56 AliMeeting sparse-reference coverage correction

Date: 2026-10-19 UTC. This report supplements the preserved [lineage partial-trial report](DEV56_ALIMEETING_LINEAGE_PARTIAL_TRIAL.md). It records a separately versioned evaluation correction and one saved-prediction rescore. The original trial remains `partial_measurement` / `live_failed`; its inputs, outputs and earlier reports are unchanged.

## Reference semantics and correction

The [official AliMeeting corpus](https://www.openslr.org/119) uses per-speaker TextGrid annotations. The [official reference converter at commit e64b8b8094ca5ea0889e121cb57d234c6a321e42](https://github.com/yufan-aslp/AliMeeting/blob/e64b8b8094ca5ea0889e121cb57d234c6a321e42/speaker/local/make_textgrid_rttm.py) emits speaker-activity segments for truthy interval marks, with speaker identity taken from the tier. It does not require every other tier to contain an explicit interval over the same segment. The converter's Git blob SHA-1 is `f83c572d893ad7a21f8c1387e99ce79b1f3f008f`.

This supports interpreting labeled intervals as annotated activity sets. It does not establish that omitted intervals guarantee acoustic silence or complete annotation. The frozen evaluator's requirement for explicit coverage from every speaker tier therefore withheld labels for sparse annotations: all 13 saved crops were classified as missing coverage, although each intersected labeled activity. With multiple entirely active tiers, that gate makes a single-speaker class structurally impossible.

The separate classifier splits each unchanged crop at exact clipped annotation boundaries. It reports labeled-union, per-speaker and overlap duration separately from unlabeled remainder and portions outside declared reference bounds. A single-reference-speaker label requires that one speaker cover the entire crop, no other speaker intersect it, and the crop remain inside global and tier-declared bounds. Unlabeled gaps stay unknown; silence requires explicit empty intervals from every tier. Overlap remains separately measured even if coverage is unavailable. No crop trimming, collar, boundary rounding, threshold tuning or inferred gap filling is introduced.

Executable parser behavior is unchanged. The existing restricted parser permits sparse gaps; this correction does not turn it into a general [Praat TextGrid validator](https://www.fon.hum.uva.nl/praat/manual/TextGrid_file_formats.html).

## Version and fixed synthetic check

Result schema: `alimeeting-natural-identity-sparse-v2`.
Classifier source SHA-256: `4c8000c8a37b5874b03972061b4ffee09ef863f2d67b959966cbfaca58aab95d`.

One fixed six-test synthetic gate passed, covering these groups:

1. Sparse activity: full one-speaker coverage, sequential speakers, overlap, unknown gaps, explicit empties and partial speech.
2. Exact boundaries: adjacency, half-open contact, disjoint crops, fractional endpoints and a one-frame gap.
3. Parser contract: supported encodings, mark handling and unchanged malformed-input rejection.
4. Pair, policy and witness regressions: independence exclusions, missing-vector denominators, duplicate witnesses and unavailable link/policy outputs.
5. Unavailable and empty inputs: no tiers, unreadable reference, empty observations and fixed input-size limits.
6. Source preservation and scope: unchanged parser/pair/policy logic, frozen original source and no public-API file reads in the synthetic fixture check.

These synthetic checks establish the tested classifier behavior, not real-world identity accuracy. Product code, product decisions, crop coordinates and identity thresholds were not changed. This publication contains the report only, not the candidate scorer or its tests.

## Saved-prediction result

The independently reviewed rescore used only the latest sealed 13 observations and pinned reference. It enumerated the same 78 unordered independent pairs. No model, inference, audio or database run occurred, and no earlier trial was added.

- The 13 fixed two-second crops total 26 seconds. Labeled activity covers all 26 seconds; overlap totals 0.635 seconds. Unlabeled, unknown and outside-declared-reference duration are zero within these selected crops.
- Ten crops have full single-reference-speaker support; three contain overlap and remain nonexclusive. No crop is classified as missing coverage, partial speech, silence or unreadable reference.
- Of 13 observations, 11 have vectors and two lack vectors. Both missing-vector observations are among the ten single-reference-speaker crops; all three overlap observations have vectors.
- Forty-five pairs have exclusive reference labels at both ends. Eight are natural same-reference-speaker opportunities; five have vectors at both ends. The two missing vectors remove three of those eight opportunities from the vector-complete subset. Across all 78 independent pairs, 23 involve a missing vector.
- The 25 retained witness rows remain unchanged: none fully bound, 15 partially bound and ten unbound.

The eight-versus-five comparison is an opportunity denominator, not a count of missed links, false merges or policy-eligible decisions. The 45 reference-scorable pairs are not 45 scored system links.

## What remains unavailable

There are zero durable decisions or decision rows and no bounded final authoritative policy/link snapshot. Published links, linked/unlinked counts, link precision, false merges, cluster measures and policy-eligible opportunity denominators remain unavailable (`null`), rather than measured zeros. DER and CER were not run.

This is fixed, already exposed reference evaluation, not held-out evaluation. Single-reference-speaker support describes annotation coverage, not proven acoustic purity. The correction recovers coverage and opportunity information only; it does not establish successful identity matching, a completed meeting trial or deployment readiness. The original failed result and all earlier reports remain preserved.
