# Dev27 mixed-stream operational test: incomplete

Source: `a61f5075fd4a12939d4aa178797717227b4c0878` (`0.2.0rc3.dev27`).

## Fixed scope

The test planned 1,800 seconds across separate Chinese speech, AMI speech, environmental-event and demo-recording scopes. Inputs were already available public research clips, with declared repetition and digital silence. It exercised real primary models, automatic MOSS admission, retained results and optional speaker probes. No manual MOSS job request was used. This was an operational test, not a transcription-accuracy evaluation or a full-speech endurance test.

## Observed result

The original test ended with exit code 1 after 1,170.698 seconds. It did not complete the fixed input, drain, final primary verification or restart/history comparison.

- Accepted audio: 631.5 seconds; published pages: 600 seconds.
- Last observed primary committed coverage: 567 seconds.
- Largest observed accepted-versus-committed backlog: 85.76 seconds.
- 33 automatic MOSS results were durably committed, each with a validated one-construction, one-session, one-run receipt.
- Three automatic candidates ended without a result. Two retained outcomes establish failure before invocation, with all native-attempt counts zero. The third has possible invocation and no validated native receipt; its counts remain unknown. These candidates prevent complete overlap acceptance.
- The third failed candidate retained an original child wait status of -9 and positive observed-owned cleanup. That status alone does not establish OOM, model execution, or the initiating failure.
- Some model timing remains unqualified; durable text is not a claim of accurate aligned overlap or global speaker identity.

## Failure and maintenance evidence

The immediate stopping condition was a primary ownership observation failure: `NoSuchProcess` during the `selected_identity` phase. The retained diagnostic does not identify the affected PID or distinguish creation-time lookup from parent lookup. The event remains indeterminate; it has not been relabeled as a confirmed ordinary child exit.

Dev27 probe-storage maintenance made observable progress. Nine sampled pending observations formed three episodes; the longest seven-sample episode spanned 12.474 seconds. Subsequent observations cleared staging in every episode, including terminal health. Terminal health retained `ReadError`, phase `reserve`, reason `result_storage_unavailable`; the underlying initiating exception remains unknown.

All 22 retained HTTP 503 responses were classified: five `jobs_lock_unavailable`, four `result_storage_unavailable`, and thirteen `cell_owner_unavailable`. No actual `speaker_probe_storage_pending` response occurred, so the helper's specific pending-response deferral retains model-free test coverage only.

The two pre-invocation failures and one start-phase failure retained the category `other`. Later bounded source analysis and a separate model-free gate-contention reproduction identify possible mechanisms, not the historical exceptions.

## Resources and shutdown

Peak sampled process-tree RSS was 1,905,721,344 bytes; including the declared 1,342,177,280-byte reserves gives 3,247,898,624 bytes. There were 4,642 samples with a maximum observed gap of 0.3383 seconds. Peak output accounting was 71,507,968 bytes across 726 entries. These are sampled observations, not continuous peak guarantees.

The original helper was positively waited. The owned server completed orderly shutdown, received the intentional termination signal and was positively waited with status -15. No known surviving descendants or resource violation was reported. Saved source, partial results and failure evidence were retained.

This result does not qualify the planned 30-minute operational test, 12–48-hour operation, Windows/Vulkan execution, transcription accuracy, or cross-day speaker identity.
