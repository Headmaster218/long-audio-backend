# Dev60 held-out rank 1: partial run and saved-score recovery

As of 19 October 2026, the four-meeting pilot has **4 planned, 1 attempted, 0 accepted original complete measurements and 1 accepted saved-score supplement**. Rank 1 (`R8007_M8011`) completed live coverage but exited 1 before passive acceptance or reference scoring. The saved-only supplement recovers a descriptive result without upgrading the failed original. Ranks 2–4 remain unattempted and paused. The three confirmation meetings remain untouched by inference or reference interpretation.

## Fixed scope

The unchanged inference product is commit `6326733db5984d95546f52bd0de5653504af2ee0`. Selection, crops and decision rules remained unchanged; persistent-profile identity remained disabled; speaker_identity_valid stayed false. No automatic retry, replacement, threshold search or calibration occurred.

The [preregistration checkpoint](DEV60_HELDOUT_PREFIX_CHECKPOINT.md) and [provenance metadata](DEV60_HELDOUT_PREFIX_PROVENANCE.json) retain selection and corpus attribution: AliMeeting Eval, Alibaba Group, CC BY-SA 4.0. Rank 1 used channel 1/index 0, 16 kHz mono PCM, half-open [0,360) seconds / [0,5,760,000) frames.

## Original run: coverage, failure and qualified cleanup

All 35/35 rows covered 360 seconds. Ten natural admissions yielded 10 observations and 10 vectors, with zero candidate-missing or pending observations. One durable batch contained 14 decision rows: 10 annotations, 2 unscored merges and 2 round records.

The saved isolated passive result records 17 direct-method attempts, six traversed blocks, complete evidence/binding audit and unchanged logical conservation. The total 2,786,225 decoded bytes include 488,741 passive bytes. Retained output does not prove parent acceptance.

The raw live status was `live_finished`, but the original outer terminal exit was 1 with `TrialStop: live original executable/argv unavailable`. The audit localizes failure to parent polling after passive launch, before acceptance/scoring. An exit-transition sampling race is consistent with the record, but the historical kernel transition and failing native identity read are unknown. No exact race cause is proven.

Positive original waits are recorded for the uploader and stopped server, and live shutdown completed. The original passive numeric return code and explicit positive-wait field are absent. Reviewed `finally` source, no appended passive-cleanup row/error and normal outer exit support the original-handle terminal-poll/wait branch. This is a **qualified source-flow inference**, not a recorded direct wait or recovered return code. File presence or an empty current-sandbox census cannot supply either; the census is not host-wide.

Cached live comparison retained 201,326,592 bytes (192 MiB) and one reserved CPU lane through nominal drain; reservation does not mean active computation. After shutdown, stopped durable reserved/staging charges were zero. The runtime seal preceded labels; original pre-reference binding/sealing/scoring was not reached.

## Separately accepted saved-only supplement

One approved invocation bound once, sealed bindings and two links, then parsed one reference and called the unchanged scorer once. It exited 0 with a positive original outer terminal result and no children. No database/audio/model reopen, service/reader replay, new HTTP/session call, inference rerun or vector occurred. Review reopened no references and reran no scorer. Original receipts/failure remain immutable; historical database hashes are provenance, not fresh identity verification.

- Ten crops: nine single-speaker, one overlapping-reference crop; no missing-reference-coverage, silence, partial-speech or unreadable-reference classifications.
- Two scorable published pairs: **2 reference-correct, 0 false, 0 unscored**.
- Of 45 source-dependency-screened vector-complete pairs, 36 are reference-scorable; 12 are same-speaker. Only 2/12 are linked, with 10 unlinked. Policy eligibility is unavailable, so no policy-eligible denominator or recall is claimed.
- Eight clusters include two reference-scorable pure nontrivial clusters and six singletons, including the overlapping-reference crop. All 24 witness rows were retained: 15 partially bound, 9 unbound, zero fully bound/distinct bound pairs. Zero hard-witness-conflicted clusters does not establish broad cannot-link coverage.

The pair screen excludes shared aliases, overlapping source ranges and different spaces, not statistical dependence. This tiny shared-observation sample supports no calibration, persistent identity, general quality or speaker/room generalization claim. DER and CER remain unrun.

## Resource accounting and limits

Original helper elapsed was 1,041.082112492 seconds, including 1,039.017414752 seconds for live processing and stop. Last sampled aggregate CPU was 360.471419 seconds. Peak sampled RSS was 825,839,616 bytes; peak sampled charged memory was 2,349,424,640 bytes. Maximum sample gap was 1.461766741 seconds. Peak sampled output was 37,060,608 bytes; final allocated output was **37,523,456 bytes**. Cleanup-pool use was 0.222004423 seconds. Retained ceilings were 3,420 absolute seconds, 6 GiB sampled charged memory and 512 MiB sampled output; these are not continuous aggregate kernel caps or a hard disk quota.

The supplement's direct execution returned in 0.100382889 seconds; its persisted final driver sample reported 0.28407 aggregate CPU seconds including admission. Owner-reported final accounting was 44.647423534 cumulative wall seconds and 44.672646889 conservative CPU seconds, 47,972,352 bytes peak sampled RSS and 143,360 allocated namespace bytes. The CPU charge conservatively includes the entire single-CPU dispatch/tool-wait interval plus admission/bookkeeping; it is not measured scoring CPU and does not add raw driver CPU twice. Final values are owner-reported in the audit, not a separately retained raw tool transcript. Limits were 60 wall/45 CPU seconds, 384 MiB sampled RSS, 1 GiB per-process address space and 16 MiB fresh output. RSS/output were sampled; per-process limits and outer timeout are not continuous aggregate enforcement.

## Evidence and disposition

SHA256 evidence pins (retained records are not publication payloads):

- Original terminal audit: `d14ca1ecf5f035fd72769a800e019258a1eb673412f92b870f91bd13664efd60`
- Original owner terminal report: `f6edf81d830f46e7973eedc81cc5d0389ce97465efb23dfc551017feed06be7d`
- Saved-score terminal audit: `9b195451fb2967c008d8c068a04378a789125eb9cd89e154777494b04011ebd3`
- Saved-score driver source: `604bb097e0bc69e18db9a747f9cccf3bbe616d69cb44d407b4f9a8c80cf980c5`
- Unchanged scorer source: `5e63008ede56b664d139980b3f5f26c1f9d7902fc7a593c6eb374dac092eb156`

The publication base is `ae3f816f396c15161fc54675006dbec27c112e5c`, tree `fdfddeee8837431cf055acc092f7a5e095132420`, with all 608 files and prior reports preserved. The batch stays paused; helper amendments or further runs need separate review and release.
