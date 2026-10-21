# Complete-scope missing-evidence diagnostic

## Conclusion

Two bounded offline runs of the unchanged comparator reached the legitimate terminal refusal `unknown_rival` after loading the entire scope. This establishes that the missing-evidence guard was reached on the real persisted inputs. It is **not** a successful speaker comparison, an accuracy result, or approval for learning, publication of identities, deployment, or production use.

The aggregate explanation identified eight input-alias classes without a measured member. These unresolved competitors deliberately prevent the complete-scope projection. No candidates were removed, no replacement vectors were synthesized, and no thresholds or anti-merge controls were relaxed.

## Complete input coverage

Both completed runs included:

- 294 observations: 272 persisted measured vectors and 22 missing-vector candidates.
- All 113 retained witness occurrences, including their existing provenance.
- 36,856 measured-vector pair evaluations.
- 3,403 input-alias relationship pairs checked before refusal.

The measured-vector pairs were computed, but clustering did not proceed past the missing-rival guard. No speaker-link decisions were produced or published by these diagnostics.

## Why the scope refused

The unchanged comparator first groups observations by its authenticated input-alias rules, checking alias consistency and hard-witness/source-dependency relationships. A missing observation is not treated as absent evidence. A class can have usable measured evidence only when it already contains an authenticated measured member.

The resulting partition was:

- 83 physical-input alias classes in total.
- 63 classes containing measured observations only.
- 12 classes containing both measured and missing observations.
- 8 classes containing missing observations only.

Of the 22 missing observations, 14 belonged to classes with authenticated measured members. The remaining eight each occupied a missing-only class. The 14 observations were not remeasured or converted into vectors; their missing status and original provenance remained intact.

**Input-alias classes are not people or inferred speakers.** Neither 83 classes nor eight missing-only classes is a speaker-count estimate.

An unresolved competitor could change a proposed winner or its required margin. The current complete-scope policy therefore refuses the entire projection rather than silently improving the apparent certainty of measured matches by excluding missing competitors. The aggregate diagnostic only inspected the comparator's completed alias partition after its refusal; it did not replace alias logic or alter a decision.

## Execution and preservation

An earlier attempt stopped at a harness startup read guard, before staging or comparison. Its nonzero outcome remains a diagnostic failure, not a comparator refusal or success. The guard was subsequently corrected narrowly for the required read-only directory census; the failed attempt's evidence was preserved.

The two completed runs used isolated, model-free, offline execution with immutable, query-only SQLite reads. No production begin/save/publish path was invoked. The original database, corrected working copy, and qualified source contents were preserved. The aggregate run reused disposable staging without another database copy. An access-time-only exception was explicitly allowed for that staging file; no access-time change was observed, and its other metadata, bytes, and absence of sidecars were verified.

The supervising process positively waited for each completed original process. Both completed runs reported positive cleanup and preservation checks. A zero process exit here means the diagnostic completed and recorded its refusal, not that the comparison became ready.

## Resource scope

The runs retained the 128 MiB comparison-build allowance and 192 MiB owner ceiling, with a finite diagnostic deadline and supervising-process watchdog. Reported peak resident memory was approximately 23–24 MiB; reported diagnostic elapsed times were approximately 5.48 seconds and 4.15 seconds.

The aggregate run's retained files were charged against a combined 512 MiB disk envelope with a 5.5 GiB free-space floor. Filesystem use and free space were checked at program checkpoints and sampled by the supervisor. These observations are not continuous proofs of instantaneous usage, general throughput benchmarks, or guarantees for other scopes.

## What this does not establish

- No speaker accuracy, false-merge rate, recall, or cross-day identity reliability was measured.
- The source history contains repeated audio, so its duration does not make it an independent long-recording accuracy dataset.
- Full evidence loading and a correct safety refusal do not qualify production authority, identity persistence, learning, or deployment.
- The eight missing-only classes do not identify eight speakers, nor do they justify dropping any failed candidate.

## Recommended next evidence

The next separately reviewed evaluation should use an independently labeled, non-looped, provenance-valid recording, with multiple speakers and temporally separated examples. Keep its entire predeclared scope and every failed candidate; use labels only for evaluation under unchanged decision rules.

Include explicitly specified missing-evidence cases alongside complete-evidence cases. Report coverage and refusals together with false merges, same-speaker linkage recall, and abstentions. A refusal or zero merges must not be scored as an accuracy pass. The present result calls for that independent evaluation, not weaker refusal controls or further recovery changes to force this scope to produce identities.
