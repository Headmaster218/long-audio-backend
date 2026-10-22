# Sampling census stage 3: bounded transaction and maintenance observations

SOURCE FROZEN. ROOT EXECUTION PENDING. NOT QUALIFIED FOR A COMPLETE CENSUS.
No tests, model, SQL or native execution was performed by this source worker.
Earlier candidates/artifacts remain immutable.

## Integration

HistoryStore primary_due, note_primary_skip, claim_primary, resolve_primary,
finish_primary, append_primary and acknowledge_primary_append have outer wrappers.
DECISION_INTENT precedes the original method invocation. The original method runs
exactly once. DECISION_ACK is emitted only after it completely returns, including
all original DB contexts, post-commit checks and connection close. Exceptions are
recorded as UNKNOWN and the original exception propagates unchanged. A returned
False is distinguished from a thrown exception. None of this grants execution.

Inside original transactions, hooks copy fixed scalar fields into a local buffer
of at most 32 records, each using the fixed scalar schema and bounded integers/text.
No sidecar I/O, hashing, JSON encoding, extra SELECT or deadline/budget/schema change
was introduced inside the database. Buffer overflow/failure marks the observer
invalid after exit and cannot change the product result. Observer-off calls retain
original calls and SQL; the extra scalar hook calls find no active transaction.

observed_pre values come from existing scope/row/due/counter reads. intended_post
values are existing SQL assignment inputs or in-memory counter changes, NOT a
claim to have reread committed post-state. Missing original post-query values stay
unknown. Existing _source_fact version/mapping digest and packet digest are copied,
not recomputed as hypothetical source proof. Existing-attempt lookup explicitly
records that due recheck was not evaluated. Per-call operation IDs link intention,
flushed snapshots and method acknowledgement; snapshots on a failed method are
speculative observations and do not prove committed state.

Manager handles retain a non-authoritative original observation binding. The
primary callback, worker thread, maintenance and append-maintenance use that exact
binding rather than the current/newest window. Every actual history method call
gets its own operation record, including retries. Delayed hard-close retains the
original frame. Missing bindings are not guessed or backfilled. State-only manager
terminal events explicitly are not durable terminal proof; actual history returns
remain separate, including success/failure/absence/unresolved status.

Previous bounded sink, creator-PID fence, parent-directory fsync and guarded platform
import controls are retained. No migration, changed threshold, changed retry rule,
new model invocation or automatic observer installation was added. Logging can
change timing; identical counterfactual sample timing is not claimed.

## Root-only commands, not executed here

    cd audio-sampling-census-stage3-20261022
    PYTHONPATH=. python -m pytest -q tests/test_trial_sampling_census_stage1.py tests/test_trial_sampling_census_stage2.py tests/test_trial_sampling_census_portability.py tests/test_trial_sampling_census_stage3.py

Then execute the existing primary history/admission/recovery/sampling/manager and
worker regression suites under the root's qualified environment. New tests reuse
bounded real HistoryStore SQLite fixtures and real InstanceLock; synthetic PCM is
limited by the existing fixture and never invokes a model.

New controls cover snapshot flush outside DB, original return and exception paths,
observed_pre/intended_post distinction, no-observer state/SELECT-count equivalence,
commit followed by lost method acknowledgement, precheck true/recheck false,
existing attempt (no new recheck), real success/failure, missing binding, delayed
hard-close preserving old binding/highwater, observer exception isolation and buffer
bounds/no encoding/hash inside capture. These tests are not an end-to-end Worker
or acoustic collector trial, and do not establish all interleavings or crash modes.

## Remaining before prospective acoustic qualification

1. Root execution and independent review of these source changes; fuller original
   suites and true Worker+manager+DB integration under absent/throwing observers.
2. Earlier preflight/analyzer/core-cut failures before the current WINDOW_BEGIN.
3. Exact source mapping/revision and recording identity in original opportunity
   keys. This stage links source facts at claim time but does not manufacture them
   for no-claim windows. Generic missing-handle reasons remain UNKNOWN.
4. Final immutable stopped-snapshot reconciliation: complete interval tiling/cursor,
   unique packet/window matching, every callback and decision, and all tokens and
   actual terminal certificates. No counter or attempt row may backfill lost events.
5. Collector health monitoring/stop, disk floor, process/model/output bounds and
   final snapshot identity. A healthy transport alone is not a healthy collector.
6. Terminal schema/validation and separate COMPLETE/UNKNOWN/INTERRUPTED/FAILED/
   OPERATIONAL_INCOMPLETE statuses. Current terminal remains UNQUALIFIED_STAGE1;
   classify_window unconditionally reports eligibility UNKNOWN, COMPLETE false.
7. Full crash/fault/retry/thread-state matrix. Root tests here simulate post-context
   lost acknowledgement, not every low-level COMMIT/close/crash fault.
8. Restart/epoch continuation if required. Reopen remains prohibited. A fork child
   cannot reuse the sink. Sidecar/SQLite atomicity is still NOT provided.

## Artifacts

stage3-from-rev03.patch applies to a fresh rev03 candidate.
stage3-from-baseline.patch applies to the original dev69 rev02 candidate.
full-source.tar.gz contains the complete new source tree. SOURCE_SHA256SUMS and
SHA256SUMS freeze the source and deliverables. No post-READY source edits planned.

    cd <fresh-rev03-copy>
    git apply --check ../sampling-census-stage3-artifact-20261022/stage3-from-rev03.patch
    git apply ../sampling-census-stage3-artifact-20261022/stage3-from-rev03.patch
