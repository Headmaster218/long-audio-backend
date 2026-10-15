# Owner-catch diagnostics and model-free publication replay

This seven-file source checkpoint contains one byte-exact diagnostic store,
three reversible templates, local-binding documentation and a public manifest.
It preserves aggregate outcomes from completed historical tests. The public
templates have not been executed. No retained database, upload, audio,
transcript, raw report, local configuration or operational identity is included.

## Diagnostic change

`native_owned_cell_store.py` extends the existing fixed-field diagnostic helper
with `stage=owner` and a bounded operation vocabulary, then calls it at the
existing generic owner-operation catch. The subsequent reason, status, result
and dropped-operation handling are unchanged. The shared 32-record limit,
1,024-character record cap, nonblocking count lock, sanitized exception fields
and best-effort logger remain. Exception messages and tracebacks are not logged.
The delta changes no budget, SQL, transaction, proof, receipt, retry or failure
semantics. It is an observability change, not a budget fix.

## Historical results and their limits

- Six injected-owner tests passed. They exercised the real owner loop while
  injecting namespace opening, scans, publication policy and selected failures,
  with fresh in-memory SQLite. They compared prior v1 behavior and diagnostic
  behavior, including results, counters, slot release, shutdown, safe fields,
  caps, logger errors and count-lock contention. They did not perform a faithful
  retained-data publication.
- A separate faithful quiescent replay passed all six operations: two receipt
  submissions, first-cell publication, the ordered second-cell plus composite
  publication, list and detail. It retained three cells, with zero drops and no
  diagnostic warnings. Its real owner Budget and authority validation paths were
  unchanged; the primary database copy had no concurrent writer.
- The nominal 100 ms writer-hold case passed, with a measured 101.744153 ms hold.
  One real empty primary claim held its transaction before normal commit/close.
- The corrected immediate-submit commit-close case passed, releasing the writer
  at the first final authority validation after a measured 173.149858 ms hold.
  Its acceptance window remained 50-200 ms and its hold expiry remained 200 ms.
- One earlier commit-close attempt missed the requested schedule: its measured
  201.850636 ms hold expired before final-validation entry. It retained
  `schedule_not_reached`, despite successful operations and cleanup. That result
  is not counted as a concurrency-schedule pass.

Each accepted replay completed the same six operations, retained three cells,
and reported zero drops and no diagnostic warnings. Both accepted concurrency
cases confirmed closed owner/writer threads and connections and then checked
three retained committed rows with matching expected identities and proof/PCM
hashes. Originals were reported unchanged. These statements are aggregate
historical results, not claims that this public packet has been executed.

The original publication exception remains unidentified. Neither quiescent
success nor these two controlled schedules recreates the historical timing or
proves production robustness. No production installation, native inference,
budget fix or repair of the earlier warning is claimed. The comparison candidate
was loaded as an in-process test overlay; installed product files were unchanged.

## Files and local bindings

- `native_owned_cell_store.py`: exact tested owner-catch diagnostic source.
- `test_owner_diagnostic.py.in`: six-test comparison template. It requires the
  prior exact v1 store, SHA-256
  `bb3091a5236c2a62647ce3cd4d4b54475f9c8809e50fbc61f2ecbf13757e852d`,
  plus that package's supporting modules. Keep the included candidate beside
  this template. A v2-overwritten package is not the v1 comparison baseline.
- `replay.py.in`: quiescent retained-data replay template.
- `concurrency.py.in`: corrected immediate-submit concurrency template with
  `writer-held` and `commit-close` cases. The historical writer-held result used
  the earlier script; this corrected source retains that branch's behavior.
- `CONFIGURATION_FIELDS.json`: every path and retained-database binding.
- `PUBLIC_MANIFEST.json`: public file integrity and aggregate validation status.

Resolve every placeholder before any execution. The two database hashes and byte
lengths must bind to the exact locally reviewed inputs. Product/runtime pins and
all validation, limit and scheduling logic remain unchanged. Numeric database
size literals are represented by `int('{{...}}')` bindings; restoring the private
values reverses all template substitutions byte-for-byte.

For replay and concurrency, ROOT is the parent of the script directory. Use
ROOT-relative source, candidate and distinct fresh output locations. The scripts
refuse an existing output leaf. Original uploads must still have their authentic
paths and receipt signatures. This is not a fixture relocation tool: do not
rewrite receipts, proofs or namespace bindings to bypass a mismatch.

A separately provisioned qualified runtime and supporting package are required;
this packet is not a standalone product installation. After local resolution,
replay selects execution with `--run`; concurrency uses
`--case writer-held --run` or `--case commit-close --run`. There is no dry-run mode
for either replay script. Do not import them to inspect them: their module-level
execution path includes setup and cleanup. Inspect their text or syntax instead.
The comparison test also performs imports at module load.

The scripts read original databases as bytes for verified copies; SQLite opens
only fresh copies or the new output namespace. Authentic upload reads remain
read-only. The copied optional database is extraction-only and is removed after
its bounded rows are loaded; the new optional store receives truthful fresh
namespace bindings. The real owner submits once per operation, without retries.
A terminal unknown result does not establish absence of committed state.

## Bounds and checkpoint validation

Replay bounds remain 120 wall seconds, including a 10-second cleanup reserve,
CPU affinity 3, nice 19, 1 GiB address space, a reported 256 MiB RSS target,
4 MiB inclusive output leaf and 64 KiB result. These bounds depend on the stated
runtime and cooperative checks; they are not a general production guarantee.

All four public source files passed syntax parsing and in-memory compilation
without executing their code. Exact inverse substitutions reconstruct the four
frozen inputs; the product module needs no substitutions. Source/evidence pins,
original namespaces, public file set and privacy checks were verified before
freezing this packet. No public template, model, service or replay was executed
in checkpoint preparation.
