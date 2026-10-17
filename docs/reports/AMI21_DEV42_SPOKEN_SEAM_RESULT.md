# Fixed AMI21 spoken-seam functionality result

## Outcome

The single fixed AMI trial completed all 21 seconds of primary processing and passed models-disabled restart/readback, but the spoken cross-upload objective was not demonstrated. The original command exited 1 after 338.633210 seconds. There was no automatic MOSS job, structured MOSS result, authenticated cross-upload support, three-positive-root witness or partial-parent replacement. No actual retry was performed.

This is a retained waiting/refusal outcome, not evidence that the recording contains no overlap. Saved-only baseline scoring used the fixed 56-word common window: official cpWER 62/56 (110.71%; 3 substitutions, 36 deletions, 23 insertions) and greedy DI-cpWER 28/56 (50.00%; 9/16/3). Both selected assignment-conditioned alignments matched 8/19 minority-speaker words and 0/9 annotation-overlap-associated minority words. All three readings were identical, with no replacement-improvement claim. DI is approximate; fixed full-window and boundary sensitivities remain qualified.

## Fixed source and configuration

The exposed English input is the continuous AMI ES2002a Array1-01 interval [510.5,531.5) seconds: 336,000 mono PCM16 frames at 16 kHz. The existing Oct15 pre-model provenance fixed the split at local 10.5 seconds, source 521.0 seconds. The two contiguous halves were uploaded in their original order, without added silence, mixing or target-dependent tuning. Preparation verified exact source/PCM hashes and byte-for-byte rejoin, and preserved the original.

The pre-existing RTTM overlap [519.67,522.57) crosses that boundary. This annotation establishes the intended speech context only. Annotation timing and channel-audibility caveats remain; the separate word-level scoring reference also has forced-alignment caveats. Numeric PCM crossing would not by itself establish conversational quality or accurate words.

Product behavior remained the published dev42 commit f8a2d54fe5bd3681d9f10afbed57e1426fa2f1b9. The preceding report-only checkpoint was 180bda2b106514096c12904c2cccc13d67fcd5ce. Model artifacts, prompt, inference settings and semantic selection were unchanged. The cumulative optional-primary budget remained 1.0 second, as in the preceding trial: planning 0.4 seconds, finalization 0.98 seconds and notice 1.0 second; the product default is still 0.75 seconds and owner publication uses a separate budget.

A natural two-root crossing was eligible for its separately labeled historical route. Three positive native roots and partial-parent selection were reported independently, not imposed as spoken-crossing completion requirements. All 78 helper checks passed before the single dispatch.

## Observed waiting and terminal reason

The first chunk, ending at 10.5 seconds, registered one target and bound none, reporting waiting / bounded_support_pending. The final chunk, ending at 21 seconds, reported waiting / bounded_local_selection_changed with owned_targets_preserved=true. Its saved stream_final flag is true; both chunks' native metadata report finished=true and processed/input/stable frontiers at 21 seconds.

The one evidence-v2 ordinary intent later closed as no_result / unresolved_wait_deadline, 300.059970 seconds after creation. It had no job identifier or structured-result URL. Final queued, waiting, pending and published counters were zero; the unpublished proof read returned HTTP 404.

The final notice identifies a localization-parity refusal category. The exact historical comparison that failed is not retained in the public proof response. Saved observations showed an idle/open inference permit, a ready native owner and no pending handoff, storage or cleanup blockage. They do not justify attributing the earlier wait to resource scarcity or reconstructing an unavailable selection/query certificate.

## Preserved text and restart

Both 10.5-second work chunks completed. The first saved observation showing all primary work complete was at 33.526 seconds; this is an observation time, not an exact completion timestamp. Sixteen primary rows retained 44 lexical inventory units, totaling 24,440 inventory bytes, with no missing inventory row. Primary baseline, whole-primary reading and lexical reading were exactly object-equal: each contained the same 16 whole-primary units and no selected replacement.

Three transcript pages, 16 linked evidence objects, the durable snapshot, projection checks, binding audit and empty job history matched exactly after the fresh-port, models-disabled restart: 23 equal saved pairs. Whole-primary readback succeeded; granular retained/replaced partition behavior was not exercised in this trial.

Both original servers were positively waited after SIGTERM with clean lifecycle markers, no forced kill, no extra descendant cleanup and no surviving owned process. The original run and all evidence were preserved.

## Bounds and qualifications

The unchanged envelope was 900 seconds of work plus 120 seconds of cleanup, 6 GiB aggregate memory including 768 MiB unknown and 512 MiB secondary reserves, 64 MiB total fresh output and 8 MiB evidence. Peak sampled owned RSS was 670,208,000 bytes; including reserves it was 2,012,385,280 bytes. Peak fresh allocation was 7,557,120 bytes and evidence allocation 1,232,896 bytes. No resource error occurred.

The run used 162 HTTP calls, with zero retained 503 responses, terminal GET retries, manual MOSS posts or inference reruns. No MOSS invocation/result was validated; the native primary invocation count is not inferred from the two completed chunks.

The trial does not qualify spoken-boundary recovery, partial-parent gain, model-output completeness, acoustic coverage, word timing, global speaker identity or population accuracy. Its useful result is complete primary/readback preservation alongside an explicit final selection refusal followed by wall-clock wait expiry. Any lifecycle repair or later actual trial requires separate qualification; this result does not approve or establish such a repair.
