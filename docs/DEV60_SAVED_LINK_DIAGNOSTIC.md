# dev60 saved-link diagnostic

Date: 2026-10-19. **Low final linking coverage coincides with low same-speaker scores and competition from other observations of the same speaker.** This is a saved-only diagnosis of the already exposed [fixed dev60 trial](DEV60_ALIMEETING_FULL_FIXED_TRIAL.md), not a new recording run or a held-out quality result. Production behavior and thresholds are unchanged.

## Exact reconstruction

One cold replay of the unchanged [history comparator](../audio_backend/recording_speaker_history_compare.py) consumed the retained 26 vectors and 59 witness rows. All 30 reconstructed decision rows matched the saved rows canonically: 26 annotations, two merges and two rounds. The result retained 24 clusters, including two two-member provisional clusters. The 325 pair scores were newly derived from frozen vectors; there was no saved score array against which to claim score equality.

Prediction-only reconstruction and aggregate evidence were sealed before joining the existing saved reference labels. No original database, audio, TextGrid or model files were opened; no inference, scorer rerun, filtering, threshold sweep, source change or second comparator invocation occurred. Input/control hashes were conserved. The single original execution returned exit 0; its independently reviewed terminal evidence remains local.

The accepted envelope was one analysis process on CPU 3 at nice 19, 60 seconds total, 30 CPU seconds, 256 MiB conservative sampled aggregate RSS, 512 MiB address space and 8 MiB fresh output. No speed claim is made from differently scoped tool and process timing fields.

## Scores, ranking and margins

The retained sample has 325 pairs. Of these, 190 are reference-scorable: 47 same-speaker and 143 different-speaker pairs. The other 135 are unscorable, including pairs involving overlap or outside-reference crops.

- 39 of the 47 same-speaker pairs scored below the unchanged 0.55 threshold; eight reached it. Same-speaker median score was 0.406.
- Only two of those eight also met the 0.10 target-gap condition at both endpoints. These are the two published pairs. Six above-threshold same-speaker pairs remained unlinked.
- All 143 scorable different-speaker pairs were below 0.55; their maximum was 0.414 and median was 0.107. This is a small exposed-sample observation, not a general false-link guarantee.
- Four unscorable pairs also reached 0.55; none was linked. They cannot be relabeled correct or incorrect using this reference.

Here, a target's competition rank is one plus the number of strictly higher-scoring alternatives. Its target gap is its score minus the strongest other competitor's score. That gap is an actual winner margin only when the target wins. These descriptive quantities do not create official pair eligibility or per-pair rejection labels.

The 45 unlinked same-speaker pairs produce 90 directed endpoint-to-target comparisons:

- 75 target gaps were negative: the strongest alternative was another same-speaker observation in 73 cases and unscorable in two.
- Nine were positive but below 0.10: four strongest alternatives were same-speaker and five unscorable.
- Six reached 0.10: five strongest alternatives were same-speaker and one unscorable.

Across those 90 directions, the strongest alternative was same-speaker 82 times and unscorable eight times, never a scorable different speaker. **These are not 82 low-margin errors.** Most targets simply ranked below another observation, often of the same person. Counts concern pair directions, not distinct people or independent rejection events.

The saved round counters remain cluster-level facts: round 0 had 10 below-threshold, six low-margin and six nonreciprocal outcomes; round 1 had 12, six and six respectively. They may concern the same clusters and must not be summed or assigned directly to the 45 unlinked pairs.

## Grouping and evidence diversity

Complete-link grouping lowered some final cross-cluster scores, but produced no additional below-0.55 crossing among pairs originally at or above 0.55. This final-group comparison does not isolate grouping's possible ranking or margin effects and is not a counterfactual replay.

Both two-member linked clusters represent the same one of four reference speakers and remain separate. Four same-speaker observation pairs lie across those two clusters. Two correct links therefore do not represent two distinct people or broad reference-speaker coverage.

The 26 crops total 52 seconds, with 26 retained packets/work chunks/native scopes, 16 uploads and six pages. Median consecutive crop-start spacing is 61.699 seconds; minimum is 17.905 seconds and maximum is 125.9605 seconds. The two linked pairs include one within the same upload/page and one across uploads/pages, with inter-crop gaps in the 10–60 second and at-least-300-second bins. Upload/page diversity within one recording does not establish different sessions, microphones or acoustic environments.

## Current policy and limits

`primary-clean-history-v1` requires a best candidate and a distinct runner-up, fixed score/margin checks, reciprocal proposals, authenticated alias handling, cannot-link and source-overlap safeguards, and complete-link grouping. It does not require three same-person observations. Two observations can form a provisional cluster when another independent competitor supplies the runner-up comparison. No new temporal or upload-diversity requirement is inferred.

After merges, the existing comparator ranks clusters and recomputes complete-link scores and margins. The pair-level descriptive evidence above does not mean the production algorithm stays pair-only.

The final same-speaker link rate remains descriptive: 2/47, with 45 unlinked opportunities. The policy-eligible denominator is unavailable; this is not official policy recall. The two late snapshots roughly 15 seconds apart do not establish all links ever published, first-link latency or cross-append stability. Persistent-profile identity, profile updates and automatic enrollment remain disabled; recording links are provisional and reversible.

This diagnosis identifies score and competition patterns on one exposed fixed recording. It does not establish a unique cause for every unlinked pair, isolate embedding-model or crop-quality causes, justify threshold changes, or establish general identity quality. Independent held-out evaluation is required before quality or calibration claims.
