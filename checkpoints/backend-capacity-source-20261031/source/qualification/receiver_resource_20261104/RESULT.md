# Receiver resource result, 2026-11-04

## Scope and reproducibility

PASS for the bounded synthetic owned-receiver scenario below, not end-to-end ASR.
Base backend commit `bda0ad6e87779b884a872cd4aedbde739976d829`; all 962 checkpoint
files were verified against SOURCE_MANIFEST before testing. No product file, bound,
verifier or test expectation was changed. Raw runtime evidence remains private.
The portable harness differs from the executed harness only by taking source/output
roots from environment variables instead of embedded local paths; its one-segment
smoke also exited successfully. Source checksum receipts are in the summary.

## Actual workload and observations

- 256 valid synthetic segments, 120 s each, mono 16 kHz PCM16LE: 30,720 seconds
  (8 h 32 min) represented audio. This is not 8 h 32 min elapsed operation.
- Two fresh processes: 128 segments each, with normal close and successful exit
  between them. Combined execution 595.467 s; first phase 186.057 s, second 409.410 s.
- Production writer callbacks published each delivery; full production inventory
  validated all retained deliveries after every arrival. After filling, 32 more
  full inventories exercised a fixed retained workload.
- 494 best-effort resource samples; maximum sampled RSS 38.29 MiB. The fixed-size
  observations ranged from 34.703 to 34.730 MiB, increasing only 28 KiB from first
  to last. This bounded window does not prove indefinite absence of leaks.
- Operation-end descriptors were 5 or 6, including observer activity; normal close
  returned to 4. Independent final audit also ended at 4 descriptors.
- Maximum sampled allocated output was 1.836 GiB. Disk grows intentionally with
  retained originals and their independent published copies. It does not plateau.
- Fresh receiver construction took 0.0106 s initially and 0.0448 s with 128 retained
  attempts. That second process successfully continued with new deliveries.
- Attempt 257 refused with `completion_raw_attempt_capacity`, without changing the
  default 256-attempt limit. A third process confirmed the same refusal after reopen.
- Final audit checked all 768 raw/published file pairs: byte-identical SHA-256,
  independent inodes, 256 valid published deliveries. Each tree held 983,247,648
  logical file bytes. No safety threshold, unexpected exception, or failed product
  assertion occurred in this receiver workload.

## Practical bottleneck

Full inventory median grew from 0.214 s for arrivals 1–32 to 2.785 s for arrivals
225–256. The 32 fixed-size scans had median 2.329 s, p95 3.030 s and maximum 3.551 s.
Publication median was 0.140 s in the first block and 0.155 s in the last block.
These are cloud observations with competing activity and monitor overhead, not a
controlled benchmark or a promise for a local server.

Production inventory deliberately revalidates retained WAV content. The 256 arrival
inventories plus 32 fixed inventories traverse about 147 GiB of WAV bytes in total,
excluding publication and final-audit reads. Per-scan work is linear in retained
bytes; repeated arrival scanning accumulates quadratic work. Do not remove integrity
checks to hide this cost. A future retention/index design needs its own integrity,
ownership, reconciliation and restart tests.

The production raw-attempt and inbox caps remain 256, with 8 GiB byte budgets. Android
currently defaults to 300-second segments; 256 such segments are 21 h 20 min, not an
unlimited recording service. This study deliberately uses shorter segments for more
stages per represented hour. Retention/rotation must be explicit before these limits.

## Limits

This exercises a local server-neutral library with synthetic valid v1 metadata. It
does not start FTPS, authenticate clients, test phone capture, intake or inference,
evaluate transcription/speaker quality, interrupt an in-flight upload, perform
power-loss testing, or establish whole-recording physical continuity. Reopening after
a normal completed phase is not crash recovery. Native inference is a separate result.

The intended conclusion is narrow: this many-segment receiver path showed bounded
sampled memory and no descriptor accumulation, preserved completed bytes across a
normal process restart, and enforced admission limits. Retained disk and repeated
hashing still grow with the amount retained.
