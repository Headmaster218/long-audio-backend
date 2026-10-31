# Dev60 four meeting saved link diagnostic

Unchanged comparator outcomes are reproduced. Unlinked same-speaker pairs show below-floor scores and same-speaker competitors, without an above-floor same-speaker pair crossing below .55 through final-group attenuation. These associations establish neither rejection causes nor a reason to change thresholds.

Independent terminal review accepted the sole invocation with the lifecycle and resource qualifications below.

## Scope and reconstruction

The [baseline pilot report](DEV60_FOUR_MEETING_BASELINE_PILOT.md) retains provenance: three accepted originals, rank 1 failed original and its separate accepted saved-only supplement. Product `6326733db5984d95546f52bd0de5653504af2ee0` retains the .55 floor, .10 margin, reciprocal complete-link rules, hard rivals, abstentions and crop selection.

Four sequential cold builds used 33 saved observations, 32 vectors and 53 witnesses, newly deriving 123 scores among 132 natural pairs. No saved score array existed. All predictions were sealed before saved-label interpretation, without original database/audio/reference/model reopens, cross-meeting comparisons or inference.

| Partition | Rank | Observations / vectors | Measured / natural pairs | Exact reproduced outcome |
| --- | ---: | ---: | ---: | --- |
| Calibration reserved | 1 | 10 / 10 | 45 / 45 | 14 rows; 2 linked pairs |
| Calibration reserved | 2 | 10 / 9 | 36 / 45 | unknown_rival; projection unavailable |
| Development | 3 | 4 / 4 | 6 / 6 | 5 rows; authoritative empty links |
| Development | 4 | 9 / 9 | 36 / 36 | 10 rows; authoritative empty links |

Workload totals are accounting only. No quality statistics are pooled. Ranks 1–2 remain unfitted; confirmation ranks 5–7 remain untouched.

## Calibration reserved scores

Scores use four decimals and nearest-rank medians. Same-speaker denominators are measured reference-scorable pairs; policy eligibility is unknown.

| Rank | Same-speaker pairs | Minimum / median / maximum | Below .55 | At least .55 and both target gaps at least .10 | Final links |
| --- | ---: | --- | ---: | ---: | --- |
| 1 | 12 | .3167 / .5195 / .6421 | 7 | 2 | 2 linked; 10 unlinked |
| 2 | 11 | .3756 / .4398 / .6027 | 7 | Unavailable | Unavailable |

Rank 1's two links are correct. Its ten unlinked same-speaker pairs include seven below .55 and three above; none meets both .10 gaps. All 24 different-speaker pairs are below .55 (median .0946, maximum .1826), as are nine unscorable pairs.

Rank 2 has seventeen measured different-speaker pairs below .55 (median .2040, maximum .3009) and eight unscorable pairs, two above .55. Nine missing-vector pairs comprise one same-speaker, seven different-speaker and one unscorable opportunity. All ten observations and twelve natural same-speaker opportunities remain in scope. Full-population ranks, gaps, competitors, attenuation, clusters and links remain null; the measured subset does not replace them.

## Development scores

| Rank | Same-speaker pairs | Minimum / median / maximum | Below .55 | At least .55 and both target gaps at least .10 | Final links |
| --- | ---: | --- | ---: | ---: | --- |
| 3 | 3 | .5479 / .6939 / .7344 | 1 | 0 | 0 linked; 3 unlinked |
| 4 | 16 | .4786 / .5866 / .7424 | 5 | 0 | 0 linked; 16 unlinked |

Rank 3 has no different-speaker separation evidence; its different-speaker cohort is empty and three unscorable pairs fall below .55. Rank 4's twenty different-speaker pairs are below .55 (median .2162, maximum .4074), with no unscorable pairs. Zero-link precision is undefined. Policy eligibility is unavailable throughout; descriptive linked fractions are not policy recall.

## Same speaker alternatives and attenuation

A directed target gap subtracts the strongest other observation's score from the target score. It is a winner margin only when the target wins. Tied alternatives retain their relationships; no ready-cohort target-top ties occurred.

| Partition / rank | Unlinked directed same-speaker targets | Strongest other is same reference speaker | Negative gap | Positive gap below .10 | Gap at least .10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Calibration reserved / 1 | 20 | 20 | 15 | 4 | 1 |
| Development / 3 | 6 | 6 | 3 | 1 | 2 |
| Development / 4 | 32 | 32 | 23 | 8 | 1 |

Endpoint/round counts do not establish pair-level causes. Rank 2 competition remains unavailable.

Attenuation subtracts the minimum cross-pair score between distinct final groups from the pair score. It is exactly zero for all unlinked same-speaker pairs in ranks 1/3/4 (10/3/16 respectively), with no above-.55 pair crossing below .55. Rank 1 attenuation reaches .0641 for different-speaker and .0570 for unscorable pairs, all already below .55. Ranks 3–4 have singleton groups only. This final-group arithmetic is not a counterfactual replay.

## Time and page diversity

| Partition / rank | Crops / total seconds | Uploads / pages | Consecutive crop start spacing min / median / max seconds |
| --- | ---: | ---: | --- |
| Calibration reserved / 1 | 10 / 20 | 4 / 2 | 16.056 / 24.5395 / 59.8005 |
| Calibration reserved / 2 | 10 / 20 | 4 / 2 | 16.6245 / 23.3205 / 64.913 |
| Development / 3 | 4 / 8 | 3 / 1 | 17.890 / 98.1495 / 122.9905 |
| Development / 4 | 9 / 18 | 4 / 2 | 15.954 / 26.456 / 62.488 |

Measured same-speaker same-page counts by rank are 8/12, 10/11, 3/3 and 12/16; same-upload counts are 4/12, 4/11, 1/3 and 5/16. Crop-edge separation counts in [10,60) versus [60,300) seconds are 5/7, 2/9, 1/2 and 5/11. Both links share an upload/page and the shorter bin. Pages/uploads are not independent sessions; pairs share observations.

## Operational and resource qualifications

Rank 1's failed passive monitor and unknown historical cause/return code remain preserved. Rank 2 retains dispatch_validation / ReadError / other / child_dispatch not_attempted, with unknown deeper cause. Ranks 3/4 retained 4/9 observations and 32/25 skips, with unknown skip causes.

Limits were 60 seconds (55 to TERM plus five to KILL), 30 CPU seconds, 256 MiB qualified sampled RSS, 512 MiB interpreter address space and 8 MiB fresh output. Bash timing was .242 real/.192 user/.049 system seconds; approved shell/rounding allowances bring CPU charge to 1.243 seconds. Sampled self high-water plus timeout-parent RSS was 49,557,504 bytes; the fixed 16 MiB Bash allowance brings it to 66,334,720 bytes.

CPU/RSS acceptance is not continuous aggregate kernel enforcement; hard CPU/address-space limits apply to the interpreter. Before the later review receipt, the namespace held 221,749 logical/266,240 allocated bytes; neither is a disk quota. Timing excludes owner review/tool overhead. No final process census was collected. Lifecycle evidence is source-verified childless analysis and direct original timed-pipeline exit 0; no census or host-wide absence is claimed.

## Interpretation and evidence

No fitting, sweep, product/model change or inference retry occurred. Persistent profiles remain disabled, speaker_identity_valid false and links provisional/reversible. Sparse witnesses and filename-token disjointness establish no person/acoustic independence. General accuracy, calibration validity, all-ever linkage and cross-append stability remain unestablished; DER/CER unrun.

Retained local evidence is excluded from publication. SHA256 pins:

- Manifest: `29026128b23c962d695fbd677312c4f639b6721c3fd0c815b8fa759dd7fe2696`
- Prediction seal: `a5b82bea29128145a49f4ae2889d3d34238b2b346fd3075f35596d5f4493103e`
- Aggregates: `d6cc0a424e03ff236121ea01eaf7ae90afe19bed6ad45c15851259c2e9c232a4`
- Independent terminal review: `9ffdab32d8b301aefabdef4bc9fc2e137a66de807eb5c2926c5db9651ac3fe86`

This report-only addition preserves prior reports/product/runtime and grants no execution, tuning, confirmation or publication authority.
