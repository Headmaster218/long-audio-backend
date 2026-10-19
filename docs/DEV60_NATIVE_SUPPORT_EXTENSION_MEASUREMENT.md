# Dev60 native support extension measurement

Extending each retained two-second speaker input to its full, previously selected native-predicted clean component produced a mixed development result. Same-speaker scores increased on the small subset scorable in both arms, while reference-scorable coverage fell. Five newly unscorable supports contain annotation gaps; those gaps do not establish silence or speaker contamination. The measurement identifies no deployable winner and establishes no safe-link improvement. Product dev60 remains `6326733db5984d95546f52bd0de5653504af2ee0`.

## Frozen comparison and controls

The two arms retain the same 13 original observations: four in development rank 3 and nine in rank 4. D2 reproduces each exact original two-second input. DF uses the entire same selected, eroded connected component, spanning 3.429–10.119 seconds. Selection preceded reference interpretation; there was no gold-selected position, concatenation, padding, replacement observation or adaptive duration choice. The contrast changes both duration and speech content, so it measures support extension rather than a causal duration-only effect.

One original invocation completed 26 encodes, representing 13 matched observations rather than 26 independent samples. Total encoded waveform duration was 98.126 seconds, including 26 seconds of D2 controls and 72.126 seconds of DF support. All 13 original PCM and float-input hashes, normalized-vector byte comparisons, and complete saved baseline comparator rows reproduced exactly before DF inference. Both arms were sealed before full-support reference scoring. The data were already exposed development data; this ordering does not create retrospective blinding.

The native CPU/two-thread Sherpa path, existing 512-dimensional ONNX model, waveform conversion, feature settings, global-mean operation and float32 normalization were unchanged. No model, frontend, threshold or calibration variant was introduced. The [official export contract](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/export-onnx.py#L87-L134) permits dynamic time length. The audited difference between native extraction and the [official test harness](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/test-onnx.py#L41-L89) remains unresolved; these results are conditional on the retained native frontend.

## Coverage and annotation evidence

All 13 observations have vectors in both arms. Their full natural-pair populations remain six and 36. Source alias and physical-overlap checks exclude no pairs; this source-independence check does not make pairs sharing an observation statistically independent. Original constraints and unscorable observations remain retained.

| Development rank | Single-speaker observations D2 → DF | Overlap observations D2 → DF | Missing-coverage observations D2 → DF | Scorable pairs D2 → DF | DF unscorable pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3 | 3/4 → 2/4 | 1 → 1 | 0 → 1 | 3/6 → 1/6 | 5/6 |
| 4 | 9/9 → 5/9 | 0 → 0 | 0 → 4 | 36/36 → 10/36 | 26/36 |

The frozen sparse scorer classifies all five newly unscorable supports as unlabeled inside the declared annotation domain. Their unknown durations total 0.559 seconds in rank 3 and 0.648 seconds in rank 4. None has annotated overlap or annotated activity from a speaker other than its D2 speaker. This is absence of annotation coverage, not proof that the unannotated audio is clean, silent or contaminated. No gap was filled, relabeled or removed to recover a favorable cohort.

Across all DF inputs, rank 3 has 24.643 seconds of support and 24.084 seconds of labeled speech union; rank 4 has 47.483 and 46.835 seconds. Explicit nonactivity and out-of-domain measures are zero. Separately, the existing rank-3 overlapping observation contains 0.660 seconds of annotated overlap under DF, up from 0.1305 seconds under D2. That is positive reference evidence of overlap within native-predicted clean support. Rank 4 has no annotated overlap.

## Scores and selection limitation

Paired differences are DF minus D2, restricted to pairs source-valid and reference-pure in both arms with the same reference relationship. They are descriptive, uncalibrated score changes.

| Development rank and common cohort | Pairs | Minimum delta | Median delta | Maximum delta |
| --- | ---: | ---: | ---: | ---: |
| 3 same speaker | 1 | +0.142719 | +0.142719 | +0.142719 |
| 3 different speaker | 0 | null | null | null |
| 4 same speaker | 4 | +0.089596 | +0.123239 | +0.175077 |
| 4 different speaker | 6 | −0.119711 | −0.045821 | +0.074800 |

Every common same-speaker score rose. Different-speaker changes have both signs despite their negative median. Rank 3's common pair changes from 0.547932 to 0.690651. It offers no different-speaker comparison.

For completeness, the full scorable cohorts have these min/median/max scores:

| Cohort | D2 | DF |
| --- | --- | --- |
| Rank 3 same speaker | 3 pairs: .547932/.693945/.734395 | 1 pair: .690651/.690651/.690651 |
| Rank 4 same speaker | 16 pairs: .478580/.592383/.742385 | 4 pairs: .745250/.761158/.780558 |
| Rank 4 different speaker | 20 pairs: .043163/.219275/.407377 | 6 pairs: .214576/.274418/.297173 |

These changing cohorts cannot support a causal comparison of unmatched medians. Rank 4's different-speaker median rises across full scorable cohorts even though the common-pair median delta is negative. Its descriptive AUROC is 1.0 in each arm; the minimum-same minus maximum-different gap is .071203 and .448077 on different cohorts. Neither establishes general improvement. Only one of six rank-3 pairs and ten of 36 rank-4 pairs enter the paired comparison; coverage loss and common-subset selection are central limitations.

## Policy boundary and disposition

DF is research representation evidence without product admission or source authority. Policy eligibility, published links, false-merged pairs/clusters, precision and link coverage are null, not zero. D2 reproduces the existing authoritative empty-link outcome. No DF policy replay, grouping, threshold sweep, enrollment or persistent profile was performed. The 32/25 historical skips retain unknown causes. Reserved ranks 1–2 and all confirmation recordings remain untouched.

Raw Bash wall time was 5.343 seconds; qualified CPU charge was 9.696 seconds including the accepted allowance. Qualified sampled RSS was 277,221,376 bytes. The 86 result files contain 743,105 bytes and occupy 1,007,616 allocated bytes. These are bounded-run measurements, not continuous host-wide containment or deployment throughput estimates. Lifecycle evidence is direct original-process exit 0 plus the source-verified childless adapter. No separate final process census was collected.

Independent terminal review accepted the completed measurement as conditional development-only representation evidence, with the lifecycle qualifications above. No further inference follows this result. Generalization, calibration, safe coverage and speaker identity validity remain unestablished.

## Evidence pins

SHA-256 pins identify retained evidence without publishing raw inputs or representations:

- Frozen design: `ca2f3aff5b14c0a39ab1248818235f9c6657ad8c9888f1ac08acc64f6246d5b6`
- Input manifest: `92697c861d15d0d3f1021d5160f21d203c11e81172037891bb588fcbd4ba64a3`
- Both-arm representation seal: `0094a791f943bb5746b0156289fc5d979eca62a95bacfdc2c5d541ad6921b6f9`
- Owner terminal receipt: `995425d381ac822c21448b86bc689b64ed77ff949ce33fd5b6c7e97556b3f030`
- Score outputs: `4dff524e6c0ae8c4fb5d8314a86214c1cb841cca1cd1e0249fa2d61b3db1ebe9`
- Coverage diagnosis: `822255a2d81b0bab6bbe1afb0264feaef28c2c42f7649f35721e7103ce0b726d`
- Independent terminal review: `e8aca3eb361bf8c09c04cdb7682612706c6e9902c1334ec0e6d461d2692cba08`
