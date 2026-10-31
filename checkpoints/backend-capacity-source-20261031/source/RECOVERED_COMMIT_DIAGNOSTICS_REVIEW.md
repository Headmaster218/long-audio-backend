# Reconstructed bounded primary commit diagnostics

New source identity, not recovered historical bytes. Base is the frozen
audio-recovered-threefixes-rev01-20261023 candidate (manifest SHA256
1443a319773e3c01d61d7fc0b92f5a4914aae07d3c8b39c5b9603a8996c73bf5).
Root independently collected and passed 46 three-fix controls; the frozen base
review's predicted 44 was incorrect and is retained unchanged. No historical
A06-A10 outcome is converted into a new result; A11 terminal/cleanup UNKNOWN.

## Scope and fields

Only new primary_commit_cost.py, Budget.progress best-effort observations and
Store transaction/stage notes are added. No old global phase observer is needed.
No SQL, progress-handler identity/return, budget amount/check/reset, retry,
optional fallback, model, receipt or proof semantics are changed.

Each bounded primary transaction gets a local observer after sqlite.connect.
The prior marker is saved, cleared before observer construction and restored
best-effort after the original close attempt, before emitting. Startup, timing,
marker-restoration and logging errors must not replace original exceptions.
The original removal/rollback/close order and exception priorities are retained:
removal failure still skips rollback; SQLite rollback error remains swallowed
by original logic; close failure can still replace the body error. The observer
does not repair, retry or reinterpret these outcomes.

primary_commit_cost (schema primary_commit_cost_v1) is failure-only, at most
eight records per process, each <=1024 UTF-8 bytes. It carries fixed fields for
stage body/commit, SQLite integer code, wall/thread CPU milliseconds, cumulative
Budget elapsed/limits/counters, current-token callback count/refusal, successful
Store callback installation/removal, rollback and close result, and observed
postrollback transaction state. No error message, SQL, path, transcript, audio,
vector, row ID or user identifier is logged. Every interrupt attribution remains
unproven: same-Budget callback observations do not establish its connection.
Earlier sticky Budget refusal is not read into the new marker. No-body-error
close-only failures are not newly classified. Connect failure uses only the
published pre-existing diagnostic. Absence of a record proves nothing.

primary_commit_stages (schema primary_commit_stages_v1) is a <=2048-byte
failure-only companion sharing the SAME eight-failure admission, never a new
cap and never consumed by successes. It is emitted only after the original
Store.db context has exited and outer context flag has been restored. A
pre-yield db failure has no captured observer and therefore no companion.
Nonblocking cap contention drops records; logger failures may leave a partial
pair. No additional fsync or durable per-event file is introduced.

There are 12 fixed stages: begin_guards, lexical_prepare, identity_prepare,
turn_rows, segment_rows, lexical_persist, events_candidates, core_compact,
optional_enqueue, primary_packet, chunk_update, commit_exit. Ten notes are in
commit_window, one commit_exit note in Store.db, and begin_guards is implicit.
Rows are cumulative endpoint samples, not stage self-time. Columns are stage,
cumulative_wall_ms, cumulative_thread_cpu_ms, budget_steps,
budget_projection_bytes, budget_read_bytes. Fixed clock tolerance is 1 ms.
Samples require the same thread, monotonic finite nonnegative clocks/counters,
and a strictly advancing fixed stage index. Invalid sampling hides all rows.
Connection open and final cleanup are excluded. Counters are explicitly shared
Budget projections, not all SQL bytes or a per-connection attribution. Nested
reads may contribute. begin_guards aggregates post-connect PRAGMA, BEGIN,
current-row/cache/schema checks; stages do not identify exact inner functions.

Instrumentation/logging consumes time within the existing phase and may perturb
deadline-boundary outcomes; no byte-for-byte timing-equivalence claim. Companion
serialization is outside the original db context, but synchronous observer and
logger overhead is not zero and does not identify host scheduling causes.

## Fresh controls and independent gates

test_recovered_commit_diagnostics.py uses fake connections and test-only clocks
around real Store contexts and real Budget: 12 failure-stage endpoints, current
deadline/VM versus external/stale marker, exact callback identity, original
body/commit/removal/rollback/close exception precedence, startup/observer/logger
faults, prior-token restoration and a restoration-setattr fault, success/cap
contention/overflow, pre-yield exclusion, invalid clocks/thread/counters/order,
and maximum counter values with 12-row output (not maximal clock fields).
Root must determine actual collection count.
No source author executed imports, tests, SQL, models or workloads.

The separately reconstructed primary-only support gate must bind this final
manifest after review. It uses actual primary-only settings context2, sampling
v2, support enabled and generated PCM with fake inference only. That gate,
published integration regressions and a new finite real run remain root-owned;
fake controls cannot establish A10 causation, performance or qualification.
