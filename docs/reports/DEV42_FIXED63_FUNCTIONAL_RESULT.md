# Dev42 fixed 63-second bounded-support functionality trial

Date: 2026-10-17. Version: 0.2.0rc3.dev42.
Published source: f8a2d54fe5bd3681d9f10afbed57e1426fa2f1b9.

## Outcome

One fixed actual run exited 0. Saved-evidence audit accepted all 63 seconds of primary processing, a naturally produced single candidate with three positive-PCM native roots and positive contributions from both uploads, its completed automatic result and both transcript views, exact lexical conservation, and models-disabled restart.

This is an exposed, same-epoch functionality result. Upload 2 is declared digital silence. Crossing it demonstrates source/PCM provenance, not spoken-boundary recognition accuracy. Earlier dev37, dev38, dev39 and dev41 failures remain preserved; none was replaced by this result.

## Fixed input and configuration

The input was the existing contiguous 31.5-second AISHELL-4 speech excerpt, followed by 31.5 seconds of declared zeros, in two uploads. The speech comes from the previously exposed channel-0 interval [422.5,454.0) seconds. Both uploads contain 504,000 mono PCM16 frames at 16 kHz. Six work chunks completed; this is not a native-invocation count. No input, prompt, threshold, model or boundary tuning, or automatic retry, occurred.

The configured cumulative bounded-primary allowance was 1.0 second, as in dev41; the product default remains 0.75 second. Planning remains 0.4 second, finalization 0.98 second and notice 1.0 second, with a separate 2.0-second owner-publication budget. This does not qualify default-profile timing margins. The feature remains experimental, default-off and fresh-session only.

The unchanged experimental Q8_0 CPU model/profile/runtime pins were checked. Local and upstream-distribution artifact identities remain distinct; conversion provenance is unverified.

## One jointly qualifying candidate

The third durable candidate jointly satisfied both conditions:

- Support: [332480,505600) frames, or [20.78,31.60) seconds; 173,120 frames total.
- Actual native query: [330560,664000) frames, covering the selected support.
- Three distinct native commitments/slots contributed 3,520, 168,000 and 1,600 frames.
- Upload contributions were 171,520 frames from the speech upload and 1,600 frames from the zero upload. The latter is exactly 0.1 second of zeros, not additional speech.

The authenticated proof read returned HTTP 200. Canonical wire/header hashes, original and final source/commit dependencies, exact query/support PCM lineage, target/result binding, same epoch/scope and selection in both views were verified. The nested inventory-v3 uses selected-support-whole-parent-v1; its scan equals support, with whole intersecting parents preserved.

The complete proof is 50,991 bytes, including its 16,513-byte nested full target. Those lengths must not be added as though the target were an extra independent storage charge. The nested inventory is 20,835 bytes, containing seven parents, three source descriptors and 41 units.

## Automatic results and lexical conservation

Three candidates committed and all three structured results were validated. Each records one construction/session/run attempt, matching model/profile/runtime identities and verified cleanup. Native-diarization invocation count remains unavailable.

The results contain 3, 1 and 6 diagnostic phrases, with 2, 1 and 2 job-local labels respectively. Nine unchanged MOSS phrases appear in two selected reading runs; the unused overlapping result remains available as an alternative.

Partial-parent selection is a separate outcome from the joint crossing witness. One four-unit parent retains [0,3) and replaces [3,4); another checked granular parent retains [0,10). Exact raw/display substring partitions, including punctuation, conserve the checked parents without a full-parent-plus-fragments duplicate. Reading counts are four whole-primary, two primary-lexical and nine MOSS items. Fourteen primary rows/107 units remain inventoried (37,254 bytes), with no missing inventory.

## Defer, persistence and shutdown

The chunk ending at 31.5 seconds retained a waiting/bounded_planning_capacity notice with owned_targets_preserved. The following chunk, ending at 42 seconds, reported two bindings; primary processing continued to completion. No capacity subphase or byte metric was exposed for that notice. Its exact historical operation/capped object remains unknown, and this recovery does not retrospectively establish dev41's transaction outcome.

A fresh-port, models-disabled restart returned byte-identical six transcript pages, 30 evidence objects, all three results/histories and the durable snapshot. Default and explicit whole-primary views matched. Both original servers were positively waited after requested shutdown, with clean lifespan markers, no forced kill, no extra cleanup and no survivors.

Elapsed trial time was 122.428 seconds within the fixed 900-second work plus 120-second cleanup envelope. Peak sampled process-tree RSS was 1,869,545,472 bytes, or 3,211,722,752 bytes including the 768 MiB unknown and 512 MiB secondary reserves, below 6 GiB. Peak fresh allocation was 13,148,160 bytes and evidence allocation 1,794,048 bytes, below 64 MiB and 8 MiB. There was no resource error.

## Qualification limits

All three results use moss-handy-diagnostic-v1, with zero schema errors and zero timestamp-adjusted phrases. Timestamp policy is support-end-intersection-10ms-v1; model timing and estimated primary CTC acoustic boundaries remain unverified.

Raw-output endpoints returned 404, 404 and 409; reported native raw byte counts of 96, 23 and 262 are metadata only. Raw content was not independently retrieved. EOS and generated-token counts are unavailable; complete_content and speaker_identity_valid are false. Archive authority is a historical snapshot with source_current not checked and source_valid_at_commit not asserted; fresh proof and bindings were checked separately.

This run does not establish recognition accuracy, acoustic completeness, global speaker identity, a spoken cross-upload seam, long-run GC behavior or population-level quality.
