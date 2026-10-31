# Dev28 mixed-stream execution: fixed-deadline incomplete result

## Scope

This 2026-10-16 CPU run exercised the dev28 source checkpoint on the unchanged, predeclared 30-minute logical workload: four ordered recording scopes comprising Chinese overlapping speech, AMI overlapping speech, environmental events and a Chinese demo. The workload deliberately repeated public clips and contained substantial digital silence. It is an operational integration test, not an accuracy benchmark, uninterrupted 12-hour qualification, or full-speech throughput test.

Primary analysis, automatic MOSS selection, durable results, temporal events and optional recording-speaker probes were enabled. No manual MOSS job request was sent. The source checkpoint is f4d806b433924acadce99c7e85195d14e774634e. Installed-package parity and model-free tests were checked separately; this actual run used the frozen source tree.

## Outcome

The original helper exited 1 at its unchanged 2,520-second work cutoff. Elapsed time was 2,520.390 seconds. The backend remained alive and was processing primary work at cutoff; the helper then performed an orderly owned shutdown. This run is incomplete and has no overall acceptance claim.

- API accepted 1,735.5 of the intended 1,800 audio seconds.
- 56 released pages covered 1,704 seconds. Their saved page responses all reported durable receipt-ready status, zero pending receipts and zero receipt gaps.
- The last saved progress observation reported 1,683 primary committed seconds. At that observation, 31.5 accepted seconds had not been released and 21 released seconds had not committed. Another 64.5 seconds had never been admitted. The last telemetry was at 2,518.734 seconds, shortly before the 2,520.390-second deadline error; this is not an exact post-shutdown database census.
- Maximum observed accepted-minus-committed backlog was 85.76 seconds; last observed backlog was 52.5 seconds.
- End-to-end elapsed time divided by primary committed audio was 1.49756. This includes pacing, startup, optional processing and cleanup; it is not isolated model compute time or a completed-workload throughput estimate.

## Automatic overlap results

The saved evidence binds 50 committed result snapshots to their exact requests, proofs, capture identities and validated one-construction/one-session/one-run native receipts. They include 21 ordinary-cell and 29 core-seam results. Cleanup and source-pin release acknowledgements were verified.

The disjoint stored parsing statuses are 42 schema-clean results, seven with invalid model timestamps and one with unparsed text. Schema-clean does not establish transcription accuracy, speaker correctness, full content recovery or trustworthy word timing. All results remain model-generated and unverified.

Two additional candidates ended with no result. Their bounded durable outcomes both record start-phase ReadError with reason cell_owner_unavailable. Invocation was possible, no result receipt validated, and native invocation counts remain unknown. Both retain positive owned cleanup acknowledgements. The recorded wait return codes, minus nine and unavailable, do not establish an out-of-memory event, a particular SQL failure or zero native calls. A fresh copied-database indexed read verified these records without opening or changing the original database through SQLite.

## Availability, resource and shutdown observations

All 59 retained HTTP 503 responses were classified: 35 cell-owner unavailable, ten result-storage unavailable, eight jobs-lock and six primary-gate unavailable. Three observed probe-storage pending episodes subsequently cleared. Their initial storage causes are not established. There was no terminal probe-storage pending or ownership failure in the final telemetry.

One bounded fresh full-census retry established later current resource accounting. It supplied no retrospective proof about unseen historical descendants. Original process-handle ownership and cleanup requirements remained in force.

Peak sampled process-tree RSS was 1,912,549,376 bytes; including the explicit 1,342,177,280-byte reserves, the reported total was 3,254,726,656 bytes. There were 9,964 samples with a maximum observed gap of 0.342 seconds. Peak charged output was 163,446,784 bytes across 1,403 entries. These are sampled observations, not guaranteed instantaneous memory peaks. No resource violation or discarded server-log bytes were reported.

The owned server received intentional SIGTERM, produced shutdown-complete markers and was positively waited with return code minus fifteen. The original helper was positively waited with exit 1. No known live owned children remained. Final models-disabled history, full retained-audio verification, complete input admission, discovery and drain checks were not reached. Unfilled final helper fields such as primary/automatic not_run or committed_candidates zero must not be read as negating the independently verified 50 saved snapshots.

## Remaining work

The two start-gate failures need targeted authority-handoff investigation. Their common high-level category does not prove a shared underlying exception. Any later copied-state history check or targeted fix is separate evidence and cannot turn this original run into a pass. A future batch test should use a measured, explicitly bounded duration appropriate to the observed processing speed, retaining fixed resource limits and honest overlap-result completeness checks.
