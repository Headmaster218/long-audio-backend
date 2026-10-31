# dev56 AliMeeting lineage partial trial

Date: 2026-10-19 UTC. Product remains dev56 at `4d94a1a467e8781839b6b441faad54462d0c9f61`.

## Outcome and fixed scope

The lineage-observer attempt exited with status 1 after 512.136 seconds. Independent review accepts the preserved result as `partial_measurement` with live status `live_failed`. Bounded post-stop reporting completed; the meeting evaluation did not. No retry was performed.

The fixed exposed input remains AliMeeting Eval meeting R8001_M8004, physical channel 1: 25,181,600 frames, 1,573.85 seconds, 16 kHz mono PCM. The intended extent was six pages, 16 sources and 150 work rows, with at most 31 natural observations. This is not held-out evaluation. Public acquisition history, source links and dataset licensing remain in [the acquisition report](DEV55_ALIMEETING_EVAL_ACQUISITION.md) and [provenance](DEV55_ALIMEETING_EVAL_PROVENANCE.json).

## Preserved partial progress

- 54 of 150 work rows completed: 9,072,000 frames, or 567 seconds. A further queued row reached a 5.48-second offset; it is not completed coverage. Summing all offsets gives 572.48 seconds, not 572.48 completed seconds.
- 56 primary packets and 339 primary segments were retained. The 112 materialized work rows comprise 54 completed, 30 queued and 28 staging rows.
- 12 of 16 sources were uploaded and acknowledged, totaling 1,176 seconds. Three pages were published and released, covering 882 seconds; page four remained pending receipts. Clean EOF and capture EOS were not reached.
- 13 natural observations yielded 11 vectors and two `candidate_missing` results, with 25 input witnesses and zero durable decisions or decision rows. Published links and policy eligibility are unavailable because there is no bounded final snapshot; null metrics are not a measured zero-link result.

Both missing-candidate descriptors record `optional_cancelled` / `ownership_changed`, one at `child_wait` and one at `child_start`. Both retain reservation-held and `child_dispatch=possible`. Their terminal-transition timing is unknown; neither shutdown causation nor safe replay is established. Observed process samples are not a complete dispatch census.

Separately, saved pre-stop responses show `unknown_rival`, `result_storage_unavailable`, and `process_sample_unavailable` / `comparison_resource_observation_unavailable`. These are distinct comparison-coverage limits, with no established causal link to the missing candidates or monitor stop.

## Observer stop and limits

The fail-closed observer refused a first-seen child after its executable lookup returned an empty value. A fresh, birth-checked direct lineage to the original server was observed. The saved earlier kernel state was `R`; a later same-identity check established non-`Z`, but its exact state was not retained. Neither continuous lifecycle state nor the child's semantic role or launch cause is established.

The helper skipped its later explicit argument read. However, the pinned psutil executable lookup can internally attempt an argument fallback and swallow `AccessDenied`; those internal outcomes were not retained. An absent helper-visible permission exception does not establish denial-free lookup or that every argument read was unattempted.

This refusal remains distinct from the earlier [dev56 monitor-limited attempt](DEV56_ALIMEETING_MONITOR_LIMITED_TRIAL.md). No common historical cause is inferred.

## Reference interpretation and identity limits

All 13 crops intersect retained labeled speech, and all 13 original-channel mappings independently match. The saved reference parse contains four tiers and 604 intervals, all active. Nevertheless, the frozen evaluator requires explicit coverage from all four tiers for each crop and treats gaps as unknown. It classified all 13 observations as `missing_coverage`, leaving zero reference-scorable independent pairs.

This is a coverage-availability result under that evaluator, not evidence of absent labeled speech or an origin mismatch. No gap reinterpretation or rescore was performed. Identity remains inconclusive; zero durable decisions supports no accuracy or zero-false-merge claim. DER and CER were not run.

## Cleanup and evidence integrity

Original server and uploader handles were positively waited, ordered server lifespan shutdown completed, and the passive reader was positively waited. Cleanup took about 0.537 seconds; no observed family member remained. Known-child and refused-child numeric disappearance receipts do not establish original-parent reaps or absence of every never-observed descendant. The accepted helper's stop receipt requires a fresh complete final inventory; this is not a separately serialized raw census.

Independent review verified all 2,521 input-file pins and 15 symlinks, both prediction seals with 210 and 250 entries, all 252 output-inventory entries and 105 evidence-ledger records, with no blocking integrity findings. Runtime evidence was sealed before reference interpretation. Stopped/snapshot conservation and passive-read conservation matched. Passive traversal remained incomplete; preserved reporting does not change the failed live result.

## Resources and preservation

Peak sampled RSS was 856,858,624 bytes; peak sampled charged memory, including reserves, was 2,400,362,496 bytes. Peak sampled output allocation was 106,135,552 bytes. The final inventory contained 105,924,738 logical bytes and 106,795,008 allocated bytes including directories. Final allocation is distinct from the sampled peak. The largest retained sample gap was about 1.699 seconds. These are sampled observations, not continuous maxima or kernel-enforced hard caps.

This source-only report preserves all earlier reports and changes no product, version, settings or historical result. It authorizes no new execution, retry or reference reinterpretation.
