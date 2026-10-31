# Dev29 longer mixed-workload result

## Outcome

The 2026-10-16 CPU run failed with helper exit 1 after 1,884.835 seconds. It did not finish the planned 1,800-second, 60-page workload. The frozen dev29 product ran throughout; the later dev30 timestamp diagnostics were not present.

The workload used previously tested Chinese, AMI, environmental-event and Chinese demo clips with declared repetition and digital padding. At the last saved observation, 1,200 seconds had been accepted, 1,183.5 seconds released and 1,131 seconds committed by the primary worker. Chinese and AMI scopes were released through their ends; the event scope was incomplete and the demo scope had not started. These are last-observed counters, not a new post-shutdown database census.

The 39 released pages all had ready source receipts with no receipt gaps. Another accepted page had not been released. Maximum observed accepted-minus-committed backlog was 85.76 seconds. Inclusive elapsed time divided by the last observed committed duration was 1.6665. It includes pacing, startup, optional processing and cleanup, and is not a completed-run or full-speech throughput measure.

## Two distinct failures

The terminal error was reported as `primary ownership unavailable`. Its retained diagnostic was an accounting observation failure: `observe_primary`, phase `process_enumeration`, reason `process_sample_budget`. The primary worker was alive and executing. The sampler uses a shared 250 ms deadline and a 4,096-row enumeration ceiling; the saved record cannot distinguish which ceiling ended this observation. It does not establish a memory overrun, identity mismatch or lost process. The implementation made this observation failure sticky, and the helper stopped on that state before the 3,600-second work deadline.

Earlier, one automatic core-seam candidate reached `no_result`. After confirmed shutdown, one indexed query against a fresh immutable read-only copy retrieved its durable outcome. The exact category was outer `before_spawn` authority submission busy because the one handoff slot was occupied. The read was not dispatched. The durable outcome records `invocation_possible=false`, `count_basis=not_invoked` and zero construction, session and run attempts, with cleanup and copied-source-pin release acknowledged.

The outer prelaunch loop recognized a closed SQLite-busy marker, while the immediate no-dispatch owner-slot marker was handled only by later inner driver gates. This candidate failed before reaching those inner gates. That specific coverage gap is separate from the later sampling-budget failure. The result does not identify a SQL error or authorize replay of an uncertain attempt.

The original database family was not opened through SQLite by the diagnostic. Its files and the copied files were hashed, and the originals retained their recorded identities and bytes. The raw helper summary's unknown-count entry remains unchanged; the separate durable-outcome check establishes the zero-call result for that one candidate.

## Retained actual results

There are 51 committed automatic results: 22 ordinary supports and 29 core seams. Each saved request, proof, result and native receipt was matched. Every committed receipt records exactly one construction, one session and one run, with model/session closure, process cleanup and copied-source-pin release acknowledged. No manual MOSS job POST was made.

Parsing yielded 42 schema-clean results, eight `invalid_model_timestamps` results and one `unparsed_text` result. All remain `model_generated_unverified`, with no assertion of complete content or valid global speaker identity. Schema-clean does not establish transcription or timing accuracy. This parser discarded rejected numeric timestamp pairs, so this run cannot recover their exact values.

Five observed speaker-probe storage-pending episodes cleared. Forty-two retained GET 503 responses comprised 30 cell-owner, seven jobs-lock, three result-store and two primary-gate responses. The lower-level cause of the historical probe-storage error remains unknown. No speaker-probe-pending 503 was observed in this run.

## Resource and shutdown evidence

The envelope remained two CPU cores, 8 GiB address space, 6 GiB sampled aggregate memory including 1.25 GiB explicit reserves, and 1 GiB total output. Peak sampled process-tree RSS was 1,916,174,336 bytes, or 3,258,351,616 bytes with reserves. The sampler recorded 7,462 observations with a maximum observed gap of 0.4983 seconds. Peak charged output was 121,454,592 bytes. No helper resource-limit violation or discarded server-log bytes was reported. Sampled peaks are not instantaneous guarantees.

The owned server received intentional SIGTERM, emitted orderly shutdown markers and was positively waited with return code minus fifteen. The original helper was positively waited with exit 1. No known surviving owned children remained.

The final full-input checks, final drain, retained-audio verification, models-disabled history restart and final transcript/evidence parity were not reached. Earlier passing short-run persistence evidence remains separate. Neither this run nor its diagnostic supplement establishes 30-minute completion, 12–48-hour endurance, Windows/Vulkan support or acoustic quality acceptance. No source change or model retry was performed as part of this review.
