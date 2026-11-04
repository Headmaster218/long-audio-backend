# Independent prospective D trial-contract audit

Date: 2026-11-04 (UTC). Scope: read-only inspection of the research planner, tests and documentation; synthetic in-memory checks only. No production edits, real recordings, model calls, scoring, threshold fitting or Git operations. The only auditor-created deliverable is this report.

## Initial result: 59 tests passed, one concrete leakage bug found

The initial independent command was:

`PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s backend-speaker-aggregation-research-20261104/contract -v`

Result: 59 tests passed in 0.074 seconds. The initial source SHA-256 was `2c24c965b5f35fe31f5e6d1370813585c64cd58be8af867beec329b6788bc5cc`; test SHA-256 was `85ae8c61e9215d33022aac64c65118ba75379fc934a3de346ece922b98f164a2`.

### Finding: exact crop-to-source aliases bypassed source separation

Separate uniqueness checks on source-content hashes and crop hashes did not compare those namespaces. Source B could equal the entire canonical content of observation A's crop. An observation on a strict subcrop of B then had a different crop hash and different declared session, evading both repeated-crop and same-source-overlap checks. This is a detectable equality of declared hashes, not merely a hypothetical curator fabricating unrelated hashes.

Minimal reproduction against `test_multireference_trials.fixture()`:

```python
m = fixture()
a, b = m['observations'][:2]
sb = m['sources'][1]
sb['content_sha256'] = a['crop_sha256']
sb['total_frames'] = a['end_frame'] - a['start_frame']
b['source_content_sha256'] = sb['content_sha256']
b['start_frame'] = 0
b['end_frame'] = 32000
r = plan(m)
```

Observed: `r['status'] == 'ready'`, with C-person references `['c-known-0', 'c-known-1']`, despite the latter being a subcrop of the former's audio. A variant placing this aliased source under T query observations likewise returned `ready`.

The issue was additionally reproduced with actual *synthetic bytes generated in memory*, using the documented canonical PCM hash envelope: a 1,280-frame source, its 80-frame crop (frames 20:100), that crop used as an 80-frame second source, and the second source's first 40 frames as its observation. All source/crop hashes were computed honestly from these bytes, and the planner still selected both references. No recording was loaded or model run.

Recommended fix: fail closed when an observation crop hash equals a registered source hash other than its own valid full-source crop. A full-source crop must have the same hash as its source under this canonical hash scheme. Keep the valid own-source full-span case accepted. The writer and parent were notified immediately; post-fix verification is recorded below when completed.

## Other checks

Ten additional independent synthetic checks passed before remediation:

- Native query evidence blocks eligibility without losing denominator rows.
- Missing query dates block the affected split.
- Same-day dates cannot establish reference precedence.
- Noninteger large numeric values are rejected.
- Score keys are rejected.
- Verified native identity assertions are rejected.
- Empty observation registries are rejected.
- Declared C/T source-session aliases are rejected.
- Actual 4,097-observation input exceeds the capacity.
- A 4,096-observation input retains all 4,090 scheduled queries and completes without pair-matrix expansion.

The capacity check used 2,762,891 input bytes, produced 3,885,318 output bytes, and completed freeze plus planning in approximately 0.189 seconds in this environment. This is a bounded smoke check, not a production performance benchmark.

Inspection supports canonical immutable string snapshots, stable ordering, fixed reference count, single/multiple paired reference subsets, global query-session exclusion, earliest-query cutoff per split, source-frame bounds, strict typed input, and fail-closed complete-gallery/split denominators. A blocked plan retains all declared observations; readiness is not acoustic correctness or statistical validity.

The final documentation explicitly describes D declared-disjoint-session design only and excludes M within-meeting references from filling separate slots. It correctly states that hashes, provenance, session/person identity and historical cohort novelty are externally supplied declarations, not runtime-verified facts. It disclaims acoustic purity, automatic grouping and accuracy improvement, and prohibits prior C/T reuse for tuning or validation. Production 0.75 is retained but not calibrated by this work. No independent claim about the production centroid implementation or prior pilot results was made by this audit.

## Post-fix verification

The writer applied the minimal repair, added six regressions, and preserved the old reproduction. Independent rerun: **65 tests passed** in 0.066 seconds. Six additional direct rechecks against the final source confirmed:

1. Original exact crop-to-source alias: rejected.
2. Honest in-memory synthetic PCM alias: rejected.
3. C-reference-to-T-query cross-split alias: rejected.
4. Valid own-source, full-span crop with equal hash: accepted; plan remained ready.
5. Full-span crop claiming a different hash: rejected.
6. Partial crop claiming its own full-source hash: rejected.

Final independently checked hashes:

- Planner: `cab7e69215943cf107656ddfd2e85b4e45e657313096de203b32cff2afcc392a`
- Tests: `b43828c616d8903dc6148dce3c0b06ba0b63d79df9118ad3abc4ad2eb69b2b14`
- README: `61b44de69561993882d045ae75e2444f6f982dbfd66bc2efe752b53a1595cabe`

**Disposition:** The concrete detectable-alias issue is fixed. No other material issue was identified within this bounded independent audit. The planner remains a declared-metadata, synthetic-tested D research contract; this is not acoustic evaluation, permission to run models, or evidence of production accuracy. Arbitrary edits/re-encodings, unreconciled aliases, false declarations and historical cohort reuse remain explicitly external curation responsibilities.
