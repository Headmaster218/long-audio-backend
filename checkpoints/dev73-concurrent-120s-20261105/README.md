# Six-minute concurrent ingestion and analysis

Using the existing freshly qualified installed dev73 and generated-bundle features, one finite-plan invocation processed three contiguous 120-second segments from the already acquired CC-BY-4.0 AMI ES2014a Mix-Headset public recording. The selected interval was 0–360 seconds before inference, without repetition or resampling. The receiver remained running during analysis. B publication bootstrapped A finalization; C was uploaded only while A native work was running. There were no product changes or old-state adoption.

## Outcome and evidence distinction

The primary plan completed two handoffs and produced three exports. The original harness recorded failure because a short-fixture assertion incorrectly expected one native chunk per span; this outcome is preserved. A separately reviewed read-only validator, executed after clean shutdown, verified 36 completed ordered native chunks (12 per span), all three exports and 5760000 PCM frames byte-for-byte against originals. Independent terminal audit cleared this separate validation. No retry, resume, replacement run or exact-repeat test was performed.

C's actual FTPS transfer lasted 1.811 seconds. Within that transfer interval, 36 samples observed A running and native CPU time increased by 0.72 seconds. This establishes one actual ingestion/analysis overlap; it does not claim B overlapped native work. Two real typed GET503 responses were followed by successful GET200 responses on identical paths. The final watcher reports one busy-read retry in its own scope; this demonstrates bounded read recovery only, never mutation retries.

## Sampled resources

The 360-second source completed in 323.758 seconds including initialization/export overhead. Native cumulative CPU was about 280 seconds. Conservative primary peak native RSS was 907038720 bytes (about 865 MiB); stage telemetry's separate peak was 906473472 bytes. Stage peaks were 894070784, 896860160 and 906473472 bytes. Sampled artifact peak was112072330 bytes (about107 MiB). Retained data grew during ingestion as expected. These three finite stages do not establish a memory-leak bound, instantaneous resource maximum, real-time deadline guarantee or twelve-hour endurance.

All 188 installed product files, 1832 dependency files, eight model assets and original spool/raw/publication data remained unchanged. Native exit143 and receiver exit0 were observed; no tracked or matching service processes remained. The original failed RESULT hash, separate VALIDATION hash and full retained telemetry hash are pinned in SUMMARY.json.

## Scope and reproduction

Source product remains 80d9399efa148c1321702e3c25447844181a8302; deployment and qualification evidence precede this checkpoint. The fixture uses offline-prepared public speech, not live microphone capture, phone hardware or a user server. Recognition/speaker accuracy and continuity are not evaluated. The original harness and Java instrumentation are preserved as evidence, including the known one-chunk assertion; use the separate validator to understand the actual terminal outcome, not an edited historical pass.

Only code, hashes, counts and resource summaries are published. Audio, transcripts, models, databases, credentials and binaries remain excluded. Original run data and uncertain/failure evidence are preserved locally.
