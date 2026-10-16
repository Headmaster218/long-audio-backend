# dev20 unified transcript: qualification and limits

The unified reading is a useful provisional display, not a recognition-quality
or speaker-identity guarantee. It removes duplicated selected source coverage;
intrinsic model repetitions and simultaneous equal-text speakers remain intact.
The original primary view and complete linked alternatives remain available.

## Implementation checks

- Final 16-result page limit: 92 model-free focused tests passed. These include
  normal 10.5-second source geometry across three fixed 60-second pages, stable
  continuation, all boundary candidate references, exact evidence, stale
  revision, deletion, model-disabled routes, lease cleanup, payload/response
  quotas, 17-result refusal and 16-by-128 alternative-link refusal.
- The bounded interval-DP test uses 16 adjacent supports with both one and 128
  intersecting whole primary units. It visits exactly 136 reachable endpoint
  pairs, stays below 0.75 CPU seconds, and retains a response within 256 KiB.
  This is a bounded synthetic check, not a production latency guarantee.
- Before the count-only increase from eight to 16, the same selection algorithm
  and store implementation passed 726 existing regression tests and 46 subtests.
  That complete regression group was not rerun after the count-only change.
  The final focused run and saved-response parity below qualify that change.
- The focused and regression checks ran on one CPU with nice 19, a 1 GiB sampled
  process-tree RSS limit and a 2 GiB address-space limit. Their supervised elapsed
  times were 2.04 and 192.36 seconds; sampled RSS peaks were approximately 76.3
  and 304.5 MB. Resource limits are not typical deployment requirements or hard
  peak-memory guarantees. No model runtime was imported.

Three previously saved short result sets were then read through the real
application's registered authenticated GET and evidence routes, with models and
workers disabled. Only disposable copies of closed database main files were
opened by SQLite. The check intentionally did not enter application lifespan;
it qualifies route/authentication/response-lifetime behavior, not server startup
or relocation of historical audio paths. All three final response bodies were
byte-identical to the earlier eight-result-cap bodies, including selected refs,
text, speaker scopes, alternatives and revisions. Exact primary and retained
MOSS evidence checks passed. No new inference or audio access occurred.

The read API adds no database schema, migration, persistent projection index,
background worker or dependency. It does not provide new crash, power-loss,
long-running throughput or arbitrary-database compatibility qualification.

## Saved-output diagnostic comparison

The fixed coordinate-only selection policy was frozen before comparison. The
three exposed examples are diagnostics, not held-out acceptance data. English
uses word tokens and Mandarin uses character tokens; rates are not pooled across
languages. The baseline retains its saved API speaker assignments. Unified MOSS
streams use the actual product's anonymous job/result/local-label namespace.

| Example and reference size | cpWER / cpCER, primary → unified | Greedy DI counterpart, primary → unified |
|---|---:|---:|
| English AMI common midpoint, 56 words | 87.50% → 83.93% | 50.00% → 16.07% |
| Mandarin 17 seconds, 79 characters | 40.51% → 87.34% | 36.71% → 27.85% |
| Mandarin 14.5 seconds, 47 characters | 42.55% → 25.53% | 38.30% → 25.53% |

The Mandarin 17-second cpCER regression is substantial: **40.51% to 87.34%**.
The greedy-DI improvement does not cancel that regression. Its gap from cp is
compatible with cross-job speaker-label fragmentation, but does not isolate a
causal recognition error or prove correct identity. Equal label strings across
jobs must not be merged; one English example aligns repeated label strings to
opposite reference roles in different jobs.

Official MeetEval 0.4.3 computed the metrics once. Greedy DI reassigns intact
hypothesis units and is approximate, segmentation/order-dependent, and not a
guaranteed optimum. The selected support runs use five jobs and replace all 29
primary units in these examples. Their full readings contain 18 intact MOSS
phrases; the English common-midpoint comparison excludes one whole edge phrase.
Six linked alternative groups remain. No alternative text was stitched into a
competing score, and no primary units survived this particular fixed selection.

All MOSS timestamps remain model-generated and unverified; word times are
unavailable, complete content is unasserted and EOS is unknown. These examples
cannot establish minority-speaker recall, timestamp accuracy, global identity,
full overlap recovery or generalization. No score was used to tune the fixed
selection policy, choose a default or promote an accuracy claim.

## Capacity and evidence boundaries

The fixed page width is 60 seconds. Sixteen intersecting committed results is a
count ceiling, not a promise that every such page fits: cumulative result bytes
remain 2 MiB, primary units 128, alternative links 1,024, and the whole response
256 KiB. A dense page can explicitly refuse rather than silently truncate.
There is no smaller-window retry parameter.

The 1,024 automatic-binding descriptor / 4 MiB binding ceiling applies to the
**entire session** on every page. Pagination cannot bypass it. The count increase
helps ordinary dense pages; arbitrarily long automatic sessions may require the
existing primary and result-history endpoints. Pending, unknown, cancelled,
unmapped or block-crossing evidence remains explicit and is not guessed into
current authority.

Primary evidence is exact-current only: changed hashes return 409 and missing
units return 404; historical primary replay is not provided. Retained MOSS
result references are historical-only and do not certify current source validity.
See the [full contract](UNIFIED_TRANSCRIPT.md) and
[Chinese API examples](TESTED_API_GUIDE_ZH.md).

## Package status

Version `0.2.0rc3.dev20` passed standard offline wheel construction and a fresh
pip target installation. All 123 package files matched the source, wheel and
installed target byte-for-byte. Dependency declarations and the bundled
third-party license/adaptation files were unchanged. Default-off application
construction, both transcript routes, CLI help and existing prerequisite guards
passed. The installed package passed 97 model-free tests: the 92 transcript
cases and five retained automatic-scheduling/receipt-GC checks.

The three bounded stages all completed with positive process waits, unchanged
source pins and no remaining owned process group. They took approximately 0.89,
0.45 and 3.25 seconds; the largest sampled RSS was 81.3 MB and aggregate working
artifacts were 32.7 MB. Checks reused qualified existing dependencies; this is
not a clean environment with newly installed dependencies. No download, model
execution or new schema migration was involved.

The wheel is `long_audio_backend-0.2.0rc3.dev20-py3-none-any.whl` (617,773 bytes),
SHA-256 `57cb74d16554c6be5a10647d954dd08fa9ecf84e7d3797177e2e98797a210622`.
After these checks, only this qualification document was updated with the
observed package results. Package code, tests, build configuration and wheel
remain the checked bytes; the wheel was not rebuilt for that documentation-only
update. Earlier real-model observations remain observations of their original
source versions, not new dev20 installed-model qualification.
