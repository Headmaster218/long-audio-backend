# dev58 fixed AliMeeting full trial

Date: 2026-10-19. Result: **complete processing; identity conclusion remains inconclusive**.

This was one fixed, previously exposed AliMeeting Eval recording, R8001_M8004, physical channel 1. It is not a held-out evaluation or evidence of general speaker-identification accuracy. Corpus/acquisition context remains in [the existing provenance](DEV55_ALIMEETING_EVAL_PROVENANCE.json). No product, version, model, threshold, crop, input order or inference setting changed for this report.

## Scope and completion

The installed package was `0.2.0rc3.dev58`, from published commit `cce374ba5257506d4a2770816b9c60ad293a4187` (tree `4a6491cde8c89f3c2f1957c1a2b088e3c12d1646`). The source/package qualification remains separate from this factual run report.

The full 25,181,600-frame, 1573.85-second mono 16 kHz PCM16 input completed: six pages, sixteen sources and all 150 primary work rows. Uploaded/published input was tracked separately from completed primary work. Final coverage and durable invariants passed. There was one session creation and one actual invocation, with no automatic retry.

Processing used the existing primary-clean-history-v1 policy and unchanged 60-second observation interval. It produced 27 distinct natural observations and aliases, within the fixed maximum of 31: 22 vectors and five missing vectors. There were 27 admitted/claimed attempts, 22 successes, five failures, 131 skipped candidates, 63 witnesses and no remaining pending work or reservation/staging charge.

## Identity limits and missing vectors

The final durable comparison scope was `unknown_rival`. Public and fresh passive views agreed on `links_stale`, history `abstained`, reason `unknown_rival`, history revision 54, decision revision -1 and authority revision 0. There were zero durable decision rows. Authoritative published links and policy eligibility were unavailable. **Zero decisions is not a zero-false-merge result.**

All five missing-vector attempts retained `AdmissionError` / `optional_cancelled` descriptors:

- Four stopped at `dispatch_validation`, subtype `stale_generation`, with child dispatch `not_attempted`. Recorded sample ages were 2, 1, 1 and 1 ms.
- One stopped at `child_wait`, subtype `busy_flight`, with child dispatch `possible` and sample age 51 ms. “Possible” does not establish successful child work.
- All five recorded reservation held, no grace used, zero wait, and null sampling cause/phase.

These descriptors locate the observed boundaries; they do not prove one common underlying cause or establish that every historical refusal is fixed.

## Fixed sparse-reference accounting

The unchanged sparse-v2 scorer (SHA256 `4c8000c8a37b5874b03972061b4ffee09ef863f2d67b959966cbfaca58aab95d`) reported:

- 20 single-speaker crops, six overlap crops and one missing-coverage crop; no silence or partial-speech classifications. The saved parser retained 604 active intervals. The sole missing-coverage crop lies wholly beyond the reference’s declared global/tier domain; this is a coverage limitation, not parser unavailability.
- 351 enumerated independent pairs, of which 190 were reference-scorable. No pair was excluded for alias reuse, overlapping ranges or a different vector space.
- 47 natural same-speaker opportunities; 27 were vector-complete and 20 lacked a vector. Across all independent pairs, 120 lacked at least one vector.
- 63 distinct witness keys: 37 partially bound and 26 unbound, with no fully bound pair. Witnesses are not interchangeable with fully evaluable independent pairs.

Link precision, link rate, false-merge counts, policy-eligible denominators and cluster rates remain null because authoritative links/policy eligibility were unavailable. DER and CER were not run. Reference-derived opportunity counts do not repair that authority gap.

## Controls, cleanup and evidence

The original execution handle exited 0. The helper recorded positive waits for its original uploader and server handles, completed lifespan shutdown, a complete final sample and no remaining observed family. The separate passive reader also had a positive original wait and exit 0. Other descendants were confirmed absent; absence is not represented as a parent reap.

There were no monitor errors. Ten incomplete process samples included two populated pending-metadata cases, both resolved absent before their original deadlines. The original last-complete-plus-three-second bound was not renewed. One incomplete output sample was retained. The helper reported a maximum entry-time sample gap of 2.018693 seconds; the largest gap between serialized complete-sample timestamps was 2.027122 seconds. Both remained below three seconds.

Positive cleanup preceded the read-only snapshot and passive traversal. All-table/all-column/all-row conservation matched across backup and passive reads; the passive reader did not read reference labels. Two prediction seals preceded reference access, and their 420 and 481 members remained unchanged. All 2,531 pinned input/runtime/package files, fifteen symlinks and eleven frozen source/control files were verified unchanged.

The preflight evidence is split: the original invocation passed its first two methods but failed a cleanup-test wait expectation. The existing cleanup method passed separately after exactly two test-only corrections: the expired negative case requires one original wait, and existing receipt copying occurs before assertions. Runtime helper bytes were unchanged across both invocations and the actual trial. The original failed result remains preserved; its unresolved-case temporary receipt is still unavailable and was not reconstructed. All earlier partial actual runs and reports remain preserved.

## Resource accounting

The unchanged envelope was CPU 0-1 at nice 19, 6 GiB sampled charged memory, 512 MiB sampled output with a 384 MiB early stop, 3000 seconds live including at most 720 seconds drain, 3300 seconds normal work and one nonrenewed 120-second cleanup pool, with a 3420-second absolute bound.

Recorded total time was 2063.621422 seconds; live plus stop was 2059.864203 seconds and cleanup used 0.191017 seconds. Peak sampled RSS was 879,640,576 bytes; peak sampled charged memory was 2,423,144,448 bytes. Last sampled aggregate CPU was 1652.536498 seconds, not a complete post-exit CPU-usage total.

The receipt-recorded sampled output peak was 152,518,656 bytes. Post-exit retained actual output was 154,210,304 allocated bytes including directories, or 152,665,286 logical bytes across 483 files. These are different accounting points: the sampled value is stored before the terminal receipt is written. Whole-namespace bookkeeping is separate. Both actual-output figures stayed below the unchanged limits. Sampled memory/output observations are not continuous or kernel-enforced peaks.

This completes the fixed processing measurement. It does not qualify the missing-vector path, establish authoritative identity accuracy, authorize a replay, or replace any historical result.
