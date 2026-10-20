# Twelve-hour audio endurance checkpoint

Status at 2026-10-20 09:54 UTC: **IN PROGRESS, NOT PASSED**. The fresh retry server reached readiness. The first scheduled input had not yet arrived at this checkpoint. No twelve-hour completion, restart-conservation, sustained throughput or acoustic-accuracy result is claimed.

## Completed controls

- The revision03 finite suite passed **35/35 controls** with a positive original-process wait and exit code 0. The suite includes metadata/SQLite conservation, journal locking and bounded resource checks, plus ten real localhost HTTP controls.
- HTTP controls cover Connection: close with exact and zero Content-Length, EOF-delimited and chunked responses, truncated length/chunk framing, body-size refusal, slow-drip headers, slow-drip bodies and watchdog-thread retirement. The drip controls use a 250 ms whole-request deadline and a 1.25 s observed upper assertion.
- A separate synthetic ownership lifecycle completed with exit code 0 in approximately 1.98 seconds. It exercised three child launch/wait/retire cycles, retained an intentionally timed-out original for cleanup, and reported positive stop, lifecycle completion, no survivors and no monitor errors. This is synthetic process-control evidence, not a real backend endurance result.
- Earlier evidence is retained outside this public-safe package, including the initial failed journal-lock conservation control and its read-only-lock correction. That failure is not relabeled as passing.

## Startup failure and narrow correction

The first actual endurance attempt failed before session creation or audio ingestion. HTTP response consumption closed the final socket reference for a Connection: close response, but the observation loop attempted to reset its timeout again, raising BadFD. The backend shutdown was orderly; the failure was preserved rather than counted as a successful run.

The revision03 transport detects response completion before further reads, validates declared response length, and always closes response/connection resources. A retained duplicate socket is used only by a bounded deadline watchdog to interrupt the endpoint. This enforces one deadline over headers and body, including slow-drip headers that per-read socket timeouts do not bound. The watchdog is canceled and joined before its duplicate descriptor is closed. Existing request/body/aggregate quotas are unchanged.

## Endurance design and limitations

The run uses ninety distinct immutable copies of the same fixed eight-minute real recording prefix. This is explicitly **repeated real material**, not ninety independent recordings or independent acoustic-accuracy evidence. Inputs are scheduled every eight minutes into one append-v3 capture/session/journal; one orderly restart is planned after committed input 45, and finalization only after input 90. Completed results and capture frontier must survive the restart and final stopped readback.

The plan allows twelve hours of paced capture plus bounded drain under a twenty-four-hour outer ceiling. Limits remain two CPU cores at nice 19, 6 GiB sampled charged aggregate memory, 8 GiB accounted run disk and a 5.5 GiB free-disk floor. The product capture/recording caps remain 4096/6144 MiB. Sampled accounting is not a kernel-enforced aggregate memory guarantee. Late append rescan latency and backlog are measured; no real-time guarantee is claimed.

## Package boundaries

This is a source/control review checkpoint, **not a runnable deployment bundle**. Deployment entry points, release/configuration documents, approval state, private filesystem locators, raw recordings, model files, databases, voiceprints and raw run logs are excluded. The source still requires the separately reviewed process monitor and installed product; those dependencies and a local lifecycle configuration are intentionally not bundled. No downloads or dependency installation occur from this package.

The `source` directory contains the final transport/runner/control implementation with documented sanitization. `history` contains only the historical pre-fix adapter for review, not execution. `provenance.json` records original source hashes and transformations. `manifest.json` hashes the public-safe files. The archive has not been published.
