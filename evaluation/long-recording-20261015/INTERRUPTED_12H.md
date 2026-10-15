# Interrupted 12-hour operational test

Status on 2026-10-15: **incomplete**. The retained evidence does not establish a
12-hour pass or final process cleanup. The execution platform no longer exposed
the original process handle, and the test did not produce its terminal summary
or final audit. The cause of process termination, if any, is unknown.

## Workload and retained progress

The unchanged dev9 backend ran with actual native diarization and SenseVoice,
fixed real-time upload pacing, and one scheduled graceful service restart.
The planned workload was 12 hours: 136.570625 seconds of distinct real audio in
three separate recordings, followed by digital silence. This tests mostly-quiet
operation; it does not measure sustained speech load or recognition quality.

The saved admission ledger verifies 572 unique, consecutive upload receipts,
537,865,130 canonical 16 kHz frames, and 33,616.570625 seconds of input
(9 hours, 20 minutes, 16.57 seconds). Maximum actual send lateness was 16.242 ms.
These are admitted-input measurements, not a claim that every admitted chunk
finished processing.

The latest of 280 retained resource samples is earlier than the final two
receipts. At that sample, 569 chunks were completed and one was waiting for more
input. The primary result counts were 71 segments and 47 speaker turns.
Measured run storage was 1,090,576,384 bytes and sampled process-tree RSS was
310,972,416 bytes. The maximum RSS among the retained 120-second samples was
504,344,576 bytes. The denser sampler's final high-water report was not written,
so that sparse maximum must not be presented as the full-run peak.

The numerical trace contains 18,036 complete records, including 1,691 analyzer
windows, 39 decodes, 3,730 native calls and 1,138 paired analyzer starts/exits.
No recorded call failed or remained unmatched, and no observer-failure record
was present. Balanced recorded calls do not prove final service or native-child
cleanup.

## Restart and optional capture

The first service instance saved its clean lifespan, stopped-worker and released
lock marker. A new service instance subsequently processed native calls and
accepted the next fixed-time uploads. The frozen control flow resumes admission
only after its committed-prefix and checkpoint assertions. However, the detailed
restart frontier, duration, prefix values and original-process wait results were
held for the missing terminal report. Their final independent closure is absent.

The trace retains 1,110 optional capture refusals:

- Three capture-stage `fractional_frame_coordinate` failures.
- 1,107 `seam_terminal_core_required` refusals during nonterminal quiet streaming.

The separate frame-origin candidate has 13 passing synthetic tests, but was not
installed in this run. Exact live failing coordinates were not logged. Optional
capture reliability remains a separate limitation.

## Evidence boundaries and timing correction

Original files were preserved. Review used saved records and source; it did not
reopen the original databases, rehash audio, run models or restart the test.
The final summary, final audit and second-service cleanup marker are missing.
No reconstructed exit code, cleanup success or replacement-run result is claimed.

The recipe's 43,710-second phase sum is a nominal budget, not an absolute promise
of final process termination. Its existing cleanup can add up to 35 seconds of
escalation plus final bookkeeping without a separate internal wall-clock bound.
The launched outer timeout requested TERM
at 43,770 seconds and KILL five seconds later. That timeout bound covers the
helper; it does not independently prove cleanup of a detached service process.

This result does not extend the previously completed 30-minute paced test into
a 12-hour acceptance. A future long-duration acceptance needs durable process
hosting and recoverable terminal evidence, rather than treating a disconnected
control session as success.

## Published source

- Product: `8c1431ff6b570f693b1a5431fab8e4536dfcbc03`
- Endurance recipe and earlier completed results:
  `9e4c9a58e3d9e441b861381aaa7d1d1d0c2cac06`
- Isolated frame-origin candidate:
  `186ac59f253bba5c1ac599ac4ba60325283a362a`

No audio, transcripts, private configurations or raw process records are included
in this report.
