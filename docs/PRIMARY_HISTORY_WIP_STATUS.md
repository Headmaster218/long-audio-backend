# Primary history integration checkpoint 06

Focused functional qualification and a preserved concurrency regression over 52fb2d47cf484cfd2aa40da68fc78c35ab755e08. Production code is unchanged from checkpoint 05. This backup contains two tested fixture corrections and the exact result documentation.

Do not deploy this checkpoint, open production databases with it, or enable its policy. Store initialization applies the additive schema and old-reader refusal marker even when the producer policy is off. Legacy tables are not rebuilt or backfilled.

All 27 selected final-source model-free functional cases passed, including the useful A/A/B identity path, two-observation abstention, real queued-cohort progress, concurrent admission/currentness fences, cancellation and ownership negatives. These focused passes do not establish the full suite or full MOSS mixed-route manager lifecycle.

The paired full-size measurement failed on the first concurrent primary core. Its existing cumulative 0.5-second capture wall deadline was reached at 0.501837 seconds, before primary commit, while the separate comparison thread was computing. The SQL work count was 12,900 of 150,000 steps. The primary-only baseline completed all five cohorts. GIL contention is plausible but not causally established by the saved data. Both threads, the scalar reservation and the original child closed positively; no cap or threshold was changed. Neither full cold nor appended comparison completed, so there is no safe-coexistence, complete-memory or large-history throughput claim. See PRIMARY_HISTORY_GATE03.md.

The earlier 34 mixed-history plus two integration passes remain separately attributed to their earlier exact source. Their original 174-node census had 138 unexecuted nodes. Earlier ingestion, pending-count and subsequent concurrency fixture failures remain preserved. No actual model has run.

A causal diagnostic and any subsequent compute-isolation architecture are separate, unimplemented work. The product's 128 MiB reservation and each 180-second comparison-task deadline remain unchanged. Production SQL scalability, real-model latency, acoustic quality, adverse multi-round comparison, the dense upstream provenance quota limitation and the separate baseline VM-budget failure remain unqualified.
