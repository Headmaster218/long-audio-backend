# Concurrent history qualification result

## Result

All 27 selected model-free functional cases passed on the exact final candidate. The production modules are byte-identical to development checkpoint 05. Two test-only corrections followed that checkpoint: checkpoint-boundary cancellation timing properties, and a bounded witness-page reader in a two-attempt fixture.

This is a focused functional result. The full-size paired comparison measurement did not launch in the same run: its third-stage admission required 230 work seconds remaining, and only 225.813038 seconds remained. Its CPU and artifact admission floors passed. The original refusal is preserved separately from the successful functional result.

## Covered paths

The cases exercise:

- The real owned-copy, primary commit and normal cleanup path, followed by a synthetic fresh probe, positive cleanup, retained observation and passive transcript annotation. Three admitted A/A/B observations across five primary cohorts produce the conservative positive A/A link and separate competitor. The two-observation case abstains.
- Real comparison-thread progress before EOF while subsequent synthetic Worker cohorts remain queued. Authenticated late admissions retain every candidate, clear current publication, and rebase the valid score prefix under the original task deadline.
- Cadence-only skips without rebuilding an unchanged complete result, and preservation of pending, invalid and unknown-rival states. An old unknown-rival result cannot latch over newer resolving-alias or unresolved-rival evidence.
- Cancellation, source revocation, revocation at ready output, and configuration changes. Authority uncertainty and bounded-read failure pause further compute. Complete stale output cannot publish.
- Unsupported thread placement, affinity/nice readback refusal, a manual caller that cannot alter OS placement, retained ownership until positive thread death and disposal, competing maintenance callers, resource revocation, and stop during unlocked resource census or cached cleanup.

No actual inference ran. This gate does not establish full MOSS manager lifecycle coverage, full-size production SQL/authority throughput, acoustic correctness or real-model latency.

## Observed cancellation timing

The five fixtures deliberately hold a completed quantum at a checkpoint. They measure the request or successful mutation, barrier release, positive thread join, and cache/token release. These are observed checkpoint-boundary tails rather than arbitrary in-quantum worst-case latency.

| Action | Request to positive join | Barrier release to positive join | Request to token release |
| --- | ---: | ---: | ---: |
| Cancel | 4.967 ms | 4.942 ms | 11.960 ms |
| Revoke | 16.815 ms | 8.140 ms | 22.808 ms |
| Revoke ready output | 16.714 ms | 7.653 ms | 23.409 ms |
| Disable links | 5.765 ms | 5.743 ms | 12.587 ms |
| Disable history policy | 5.223 ms | 5.216 ms | 12.560 ms |

## Conservation and resources

The first concurrency attempt stopped at a fixture's incorrect one-page witness assumption after three passes and one failure. The corrected helper follows the store's basis-boundary pagination contract, requires strict cursor/count conservation, and feeds both pages without changing product limits or substantive assertions. That failure and its synthetic fixture remain retained.

Across both concurrency invocations, there were 31 test executions: 30 passes including three duplicate passes, and the one preserved fixture failure. The corrected final-source gate covers 27 unique passing cases. Cumulative supervised use was 119.300792079 wall seconds and 66.413593 aggregate CPU-seconds. Peak combined sampled RSS was 142,458,880 bytes. Peak allocated artifacts in the concurrency family were 37,203,968 bytes; after exact success-only fixture cleanup, the terminal family retained 2,555,904 bytes in 49 entries. The ten older history evidence trees remained unchanged at 42,102,784 bytes in 255 entries.

The earlier 34 mixed-history plus two integration passes remain a separate, earlier-source result. They are not a blanket regression claim for this candidate. The unchanged baseline VM-budget failure and dense upstream provenance quotas remain separate limitations.

## Paired measurement attempts

The first standalone representative attempt completed its five-cohort baseline, then failed when the fixture tried to nest a second live application. The production single-owner registry correctly rejected it before runtime qualification. The fixture was corrected to use sequential lifespans with positive baseline thread, manager and instance-lock cleanup, unchanged database identity and the same bounded scalar read token. The failed setup, its baseline and its 15.235629791 wall seconds / 9.068587 CPU-seconds remain retained.

The corrected standalone attempt reached real concurrent compute and failed substantively on the first primary core. The existing capture Budget exhausted its cumulative 0.5-second active-phase wall allowance, detecting the refusal at `source_row`: 0.501837 seconds, 12,900 of 150,000 VM steps, 2,534,144 of 41,943,040 source-read bytes, and 1,972 of 262,144 SQL projection bytes. The resulting SQLITE_INTERRUPT propagated through required automatic capture before primary commit. The first concurrent chunk remained at offset zero with no transcript segment or native commit; the remaining four chunks stayed queued.

This is a primary-progress regression in the tested concurrent-thread configuration. The 0.501837 seconds is cumulative capture work detected at that read, not the duration of that single query. Successful baseline capture subphase timings and the failed lane's partial per-core timing row were not retained. The evidence therefore does not distinguish GIL contention from other scheduling, SQL or I/O effects. Separate CPU affinity alone cannot prove a GIL cause.

The byte-matched baseline completed all five primary cohorts. Its first core reached acknowledged commit in 0.289726 seconds and normal cleanup in 0.293137 seconds, using 0.278831 primary-thread CPU-seconds. Across the five queued baseline cohorts, median arrival-to-cleanup was 0.635559 seconds and maximum was 1.246172 seconds; these include queue wait and are not isolated core service times. Baseline primary-thread placement was recorded as CPU 3 at nice 19. The concurrent lane passed the same CPU 3/nice 19 setup assertions before capture, but its failure prevented final identity/timing-row serialization.

The comparison thread's recorded placement was CPU 4 at nice 19. Before cancellation it loaded all 2,880 fixed vectors, reached 39,067 of 4,145,760 cold dot products and 280 score-prefix rows, and executed 31 compute quanta. The observed comparison call lasted 2.135284 seconds with 1.956662 thread CPU-seconds. Its maximum step was 51.092864 ms; every step had a new scalar currentness poll. The build’s full-work logical forecast was 125,834,676 bytes within the unchanged 134,217,728-byte reservation. Early attributable sampled RSS delta was only 2,629,632 bytes because almost all score work remained unfinished; this does not qualify the eventual allocation peak.

Both primary lanes were joined before application teardown, comparison cleanup completed, the original scalar reservation was released, and the sole pytest child was positively reaped without force kill. The corrected attempt used 26.696852307 wall seconds and 14.584958 aggregate CPU-seconds. Its peak combined sampled RSS was 146,325,504 bytes. Neither the cold full comparison nor the four-row append stage completed. There is no large-history throughput, safe coexistence, complete-memory, real-model or acoustic-quality verdict.

Cumulative concurrency-family execution through all four invocations was 161.233274177 wall seconds and 90.067138 aggregate CPU-seconds. There were 33 test executions: 30 passes (27 unique final-source functional cases) and three preserved failures (the witness paginator fixture, nested application setup, and substantive concurrent capture deadline). The final concurrency evidence family retained 18,739,200 bytes in 147 entries. Together with the unchanged older history trees, the terminal allocation was 60,841,984 bytes in 402 entries. The peak across the family remained 37,203,968 bytes, or 79,306,752 bytes including older history evidence.

The primary deadline, SQL/read caps, scoring thresholds and product behavior were not weakened after the failure. The next step requires a separately reviewed causal diagnostic before any compute-isolation change. The earlier baseline VM-step failure at 150,100/150,000 steps is distinct from this new wall-time failure at 12,900 steps.

## Reproducibility

The final source manifest is SHA-256 ca57f7af90eb080a72e6fe452a5073a6f747fdf7a24dedb37f014d1e67566862, covering 533 files. The independent functional terminal review is SHA-256 998c855b5274821c80d8069481f2a1a421a8c790962bbfadb7aa68c464816101. The substantive concurrent-failure terminal review is SHA-256 8c173a5ac16fb87ed885e0b1fe44d14658dd92fb9544eb1bbd125d9ff1a9fa8e. No model, scoring threshold, schema, history pruning or legacy 32-node policy changed in this concurrency correction.
