# dev60 fixed AliMeeting full trial

Date: 2026-10-19. **Complete processing with two reference-correct provisional links; general identity quality remains unestablished.** Independent review accepted the single fixed measurement, positive cleanup and conserved evidence. The input is already exposed, not held out; both links concern one reference speaker.

## Fixed input and controls

Package `0.2.0rc3.dev60` came from published commit `6326733db5984d95546f52bd0de5653504af2ee0`, tree `c37ead48b684ba935e1a7b3ce9950ad3a467e513`. No corpus revision or reselection occurred. Source: [OpenSLR 119 AliMeeting Eval](https://www.openslr.org/119/), archive `Eval_Ali.tar.gz`, SHA256 `dc47343b2474b5ebcf458927e878155f6ddeb59c85e685b3645c32a1f9578d92`. [Provenance](DEV55_ALIMEETING_EVAL_PROVENANCE.json) retains the official archive URL, selection and license. Hashes do not prove independent authenticity or historical equality.

`R8001_M8004_MS801.wav` used physical channel 1 (index 0), exactly retained as mono 16 kHz PCM16: 25,181,600 frames / 1,573.85 seconds. Mono file SHA256: `7312be8c5c34e2cfc5b53812010449023a8c5648565e1908080d481216710c9e`; PCM SHA256: `9557fb6c4289d5884209a1036ed4f1d4bf91ac4d42702a261d023bb22969981e`.

Primary-only mode, order, models, inference settings, thresholds, crop rules, `primary-clean-history-v1` and 60-second observation intervals stayed unchanged. The fixed trial, uploader and sparse-v2 scorer stayed byte-identical; helper adaptation was limited to approved paths/hashes. Scorer SHA256: `4c8000c8a37b5874b03972061b4ffee09ef863f2d67b959966cbfaca58aab95d`. One session and one actual invocation occurred, without retry or replay.

## Completion and current provisional links

All six input pages, sixteen sources and 150/150 primary work rows completed over the full input. Uploaded/published input is distinct from completed work. Coverage and durable invariants passed.

All 26 naturally admitted observations succeeded with vectors: zero missing or pending, within the maximum of 31; the saved skipped counter was 131. Work rows are not observations. Dev59 had 27 observations, so membership is not paired identical.

The final live snapshot was complete/current at history/decision revision 52. One decision batch contained 30 rows: 26 annotations, two merge rows and two round rows. Annotations were 22 `source_scoped` and four `provisional_linked`. Complete current production annotations published two anonymous provisional recording-link pairs. `speaker_identity_valid=false`; profile updates and automatic gallery enrollment remained disabled.

Round 0: 10 `below_tau`, six `low_margin` and six `not_reciprocal` candidate outcomes, with two merges. Round 1: 12, six and six respectively, with zero further merges. These per-cluster/per-round tallies can repeat clusters; they are not eligibility counts or explanations for the 45 unlinked reference pairs. Raw `unknown_rival_ownership_clear=false`; this does not make every unlinked pair a correct abstention.

## Sparse-reference result and limits

The unchanged scorer evaluated the final current pair set. Two late snapshots, primary-complete and final-quiescence, agreed roughly 15 seconds apart. This is not an inventory of all links ever published: first-link latency and cross-append identity stability are unmeasured. Zero false links applies only to the two final scored pairs. Results:

- 20 single-speaker crops, five overlap and one missing-coverage crop; zero partial-speech, silence or unreadable-reference crops. The missing-coverage crop is two seconds wholly beyond declared reference coverage and is unscored, not silence.
- 325 enumerated independent observation pairs, all with distinct aliases, a common embedding space and disjoint saved audio ranges. Of these, 190 were reference-scorable; none lacked a vector.
- 47 reference same-speaker opportunities, all vector-complete: two linked and 45 unlinked. The linked fraction is 2/47 (about 4.26%). Policy-eligible denominators and rates remain null, so the 45 are not proven policy-eligible misses.
- Two unique, independent, scored published links: two reference-correct, zero false and zero unscored. Both concern one reference speaker. Observed 2/2 link precision is a tiny selected sample, not broad accuracy or an identity-valid result.

Scoring invariants passed. DER and CER were not run. General acoustic, diarization and multi-speaker identity quality remain unestablished.

## Quiescence, cleanup and conserved evidence

Final comparison state/owner were null, with zero reservation, reserved/active lanes, child RSS and parent charges; no primary work was queued, granted or executing. Current settled snapshots reached final quiescence. Raw `cleanup_confirmed=false` is a separate reset status field, not a substitute for positive process-cleanup evidence or a stronger per-comparator clean-ACK claim.

Original outer, uploader and passive-reader waits were positive with exit 0. The original server wait returned -15 after owner SIGTERM and completed lifespan shutdown. Complete final census found no remaining observed family within visible scope. This is not host-wide absence; descendant absence is not an original-parent reap.

Positive cleanup preceded stopped read-only backup and passive traversal. All-table/all-column/all-row conservation matched exactly. The stopped fresh-reader projection was `historical` / `links_stale` at decision revision 52, while the saved final live snapshot had been current. No service/model restart or live revalidation occurred. Both prediction seals preceded reference access and remained intact; all input/runtime/package pins, controls, helpers and prior evidence were unchanged. The owner rehashed all contents; the reviewer checked opaque audio/database entries through recorded hashes and metadata, without opening them.

No monitor errors or child-refusal records were recorded; 88 SQLite result-database diagnostic refusals remain: 45 `SQLITE_BUSY_RECOVERY` and 43 `SQLITE_BUSY`. Six transient incomplete process-sample records also remain, with zero incomplete stop-entry or output samples. Eventual completion does not erase these operational refusals or imply continuous observation.

## Bounds and descriptive comparison

The fixed envelope was CPU 0-1 at nice 19, 6 GiB sampled charged memory, 512 MiB sampled output with a 384 MiB early stop, 3,000 seconds live, 3,300 seconds normal work, one nonrenewed 120-second cleanup pool and a 3,420-second absolute bound. The 720-second drain value was explicitly a nominal stop-check, not a strict elapsed cap; no strict 720 or 720+15 second qualification is claimed.

Recorded total time was 1,359.150732 seconds; live plus stop was 1,354.865549 seconds. Positive cleanup took 0.292461 seconds; the one cleanup pool used 0.292471 seconds. Sampled peaks were 2,430,300,160 charged-memory bytes (2.26 GiB), 886,796,288 RSS bytes and 152,322,048 output bytes (145.27 MiB). Terminal output occupied 153,972,736 allocated bytes. Last sampled aggregate CPU was 1,571.235029 seconds, not a complete post-exit wait4 total; helper-reported sample-entry gap was 2.424838 seconds; consecutive complete-sample timestamp gap was 2.426096 seconds, including current sampling cost. Sampled peaks are not continuous/kernel-enforced caps, and terminal writes separate output accounting points.

For the same fixed input, [dev59](DEV59_ALIMEETING_FULL_FIXED_TRIAL.md) completed 150 rows / 1,573.85 seconds with 27 attempts, 23 vectors, four missing vectors and zero decision batches/rows. Its authoritative-link metrics were unavailable/null, not an authoritative zero-link or zero-false-link result. It retained `unknown_rival`, an idle 192 MiB comparison reservation and one reserved lane, with total time 2,064.000604 seconds and its documented drain qualification exception. Dev60's counts and elapsed time are descriptive operational observations only: natural crops/reference opportunities differ, historical failure causes remain unestablished, and these counts do not prove causal speedup or general quality improvement. Earlier raw failures and limits remain preserved. No rerun or tuning occurred.
