# Experimental provisional rolling decode

This is isolated and default OFF. It adds no runtime, SourcePlan, Alias, normalizer,
HTTP, or source-ownership admission. It uses the existing frozen receipt contract;
AAC presentation remains unresolved. No source files are changed.

## Verification is session-wide and explicit

A RollingDecodedReader first verifies compressed identity, native format, and the
pinned decoder recipe. That identity check is exposed separately and does not
mean the decoded stream is already verified. Each read returns a
ProvisionalPCMWindow with a unique session ID and an explicit provisional label.

Only a CompletedDecode from the same session, after the entire emitted count and
SHA256 match at EOS, establishes full stream verification. finish() drains the
remaining stream and returns that completion. Decoding error, source mutation,
resource failure, timeout, cancellation, or early close invalidates all provisional
chunks from that session and never produces a completed receipt. Previously
returned window objects deliberately retain their provisional label; a consumer
must pair them with the exact session's completed receipt. Such pairing is not
automatic ownership or timeline admission. Caller-constructed structural labels
remain unauthenticated, as in the baseline reader.

Use reader.is_verified_window(window, completion) for in-process pairing. It
requires that reader's actual stored completed-receipt object, matching session
identity, and a bounded per-window integrity token covering bytes/coordinates.
Copied or caller-constructed completion objects, another session's completion,
and changed windows return false. No issued-window ledger grows with duration.
Serialized receipts alone are not authority for this pairing API. This guards
incorrect pairing, not hostile code with direct access to reader internals.

## Bounded, monotonic consumption

One decoder process serves forward reads and cache hits. The native emitted PCM
ring holds at most two seconds, and reads return at most one second. A backward
request older than the ring raises ReplayRequired without starting another decoder
or seeking. The caller may explicitly create a new replay session if appropriate.
There is no compressed input seeking, fixed-latency arbitrary access, or 48-hour
validation claim. Forward consumption is O(total consumed output), plus initial
and final compressed-file hashes; repeated full-file decoding per window is gone.

Internal PCM buffering is at most the two-second ring plus two 64 KiB chunks:
one fixed readv buffer and one queued/consumer-held handoff. The one-item handoff
slot is released only after the consumer copies it into the ring. Returned window
bytes (at most one second) are caller-owned and additional. OS pipes, decoder
internal memory, <=8 KiB diagnostics, bounded metadata, and interpreter overhead
are also additional and covered by the sampled process-tree RSS guard. A consumer
that retains many windows can itself use unbounded memory; this reader does not
retain a history of returned windows.

A producer services the bounded pipes and polls cancellation/resources even while
the consumer pauses. Backpressure stops stdout reads when the handoff slot is
occupied. cancel() and close() kill/reap the child and join the producer. An idle
lease (default five seconds) also invalidates/reaps a paused session without a new
read call. Use a context manager or explicit close; this is not a persistent daemon.

## Limits and environment

The baseline Limits apply. Default whole-session wall budget is 30 seconds,
including setup and consumer pauses, and decoder CPU is 20 seconds per subprocess,
not aggregate parent/child CPU. The default emitted cap is 28,800,000 frames
(10 minutes at 48 kHz, about 10m53s at 44.1 kHz). Compressed cap is 64 MiB, CPU 7,
one thread, sampled own process-tree RSS <=256 MiB, >=2 GiB available host RAM, and
>=5.5 GiB free disk. This compact profile does not admit 48-hour material.

At most one rolling session may be active per process. A second active session
is rejected explicitly; the lease is released after constructor failure, final
verification, cancellation, close, or failure/reaping. Completed cached sessions
can coexist. This keeps the own-child resource guard's scope explicit.

Existing native taskset/prlimit wrappers apply affinity/resource limits without
Python preexec_fn during either startup verification/probing or the producer.
Decoder binaries/build are pinned;
wrappers/shared libraries remain part of the trusted immutable Linux environment.
No installation, model loads, paid services, downloads, or generated audio are used.

## Compact validation

Tests use only the frozen four-second AAC/Opus assets, exact sequential baseline
PCM, and tiny controlled failure subprocesses. They cover forward/overlapping/final
ranges, one decoder invocation, provisional/final distinction, bounded ring/handoff,
cache hits/explicit misses, pause/resume, idle/explicit/external cancellation,
resource and wall failures, source replacement, full-hash mismatch, diagnostics,
partial frames, and reaping. Larger-duration validation remains a separate task.


This implementation is Linux-specific (/proc, readv, taskset, prlimit, pinned
Linux decoder build). There is no Windows implementation or portability claim.
The older synchronous baseline uses Python preexec_fn and is limited to controlled
single-threaded invocation; the rolling helper does not inherit that startup path.

## Independent result and measurement correction

The final independent combined pass passed 63/63 (37 baseline and 26 rolling,
including 10 additional rolling review tests). Wall time was 26.158708 seconds,
aggregate CPU 23.077213 seconds, and correctly sampled complete-tree RSS
86,331,392 bytes. The PPid traversal observed supervisor, pytest, and live children.
Available host RAM stayed above 5,592,346,624 bytes and free disk above
6,618,861,568 bytes. Research/review footprint stayed below 464 KB.

Read `research/MEASUREMENT_CORRECTION.md` before comparing historical memory
numbers: the earlier independent 35 MB baseline number excluded descendants,
and an earlier 10.5 MB rolling number covered only the supervisor. Neither is a
full-tree peak. Original baseline records/archive remain unchanged. The separate
implementation-side 81.5 MB restored-baseline monitor used verified PPid-based
psutil enumeration and retains its sampled full-tree scope. Sampling is not an
OS-hard memory proof. This finalization changes documentation/evidence only after
the reviewed code passed; it does not claim another unchanged test run.
