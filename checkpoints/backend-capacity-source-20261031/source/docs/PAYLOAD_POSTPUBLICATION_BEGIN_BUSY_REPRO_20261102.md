# Generated payload receipt-admission regression (2026-11-02)

## Independently reproduced baseline mechanism

Baseline source `708d713e01ad6eec2659d8b3af40c012aa6d2c64` is preserved in public
checkpoint `b1c6fb1e6d793071645144f11dea8b7dfc2beeb7`. Four independent generated-data
cases established a classification gap. This proves a possible mechanism, not the
identity of any historical lock holder or the cause of a particular private run.

The tests use the existing generated two-second component fixture and its explicit
synthetic PayloadFrontier authority seam. Original Store, SQLite, budget, immutable
file publication and cleanup execute. No Worker thread, model, inference, original
run database or real recording is used. The original Worker maintenance method is
called without constructing its model/thread owners.

Both an exact failed-BEGIN fault and a real competing SQLite writer reproduced:
final file positively published/closed; published-receipt BEGIN cannot acquire;
original rollback/close succeed; old code converts the failure to PayloadBeginBusy;
Worker defers. This leaves a reserved row and final .unit, no .stage or published
hash/signature, unchanged hot bodies and no locators. A later ordinary maintenance
call then fails `payload_reserved_unit_pending` instead of reporting the first
post-publication failure.

## Narrow correction

Only the published-receipt transaction now opts into the existing native BEGIN
acquisition helper: at most 250 milliseconds, clipped to the SAME original
5-second PayloadBudget and unchanged SQL/read/metadata counters. It uses the same
original guarded connection/owner; the write path executes BEGIN IMMEDIATE and
never runs the helper's read-snapshot SELECT branch. There is no Python transaction
retry loop, file/link replay, SQL-body/COMMIT replay or inference retry.

That exact scope forbids ordinary busy deferral after irreversible file publication.
Persistent BEGIN contention preserves the original SQLite error and becomes terminal
immediately. The remembered original exception also receives a local nondeferrable
marker; Worker will not swallow even a Busy-shaped body/cleanup fault from that
scope. No exception wrapper replaces the first error or its cleanup-unknown state.
Before-mutation contention retains the prior deferral policy. Later placement
contention retains existing same-unit continuation only after a durable published
receipt actually exists.

Durable reservation remains before file publication. The correction does not move
that authority, hold a SQLite write transaction across copy/fsync/hash, increase a
quota/deadline, or adopt an unreceipted reserved file after process loss. Existing
SIGKILL crash-boundary tests must retain reserved-file fail-closed behavior.

## Verification scope

Current regression expectations differ intentionally from the baseline: persistent
receipt contention is the original immediate fatal error, not a defer; Worker keeps
that first error sticky without another maintenance call. A separate direct Store
call still refuses the unreceipted reservation. Transient real lock contention,
SQL body/COMMIT/close faults and original budget preservation are covered by the
new receipt-admission tests. No cleanup retries or model runs are used.

Use workspace-backed generated test data; do not lower the original 5.5 GiB disk
reserve floor merely because a small /tmp filesystem cannot satisfy it.

```sh
python -B -m pytest -q -p no:cacheprovider \
 --basetemp=../payload-receipt-generated-tests \
 tests/test_payload_postpublish_begin_busy_nov02.py \
 tests/test_payload_receipt_native_wait_nov02.py \
 tests/test_payload_receipt_faults_nov02.py \
 tests/test_payload_crash_boundary_nov02.py \
 tests/test_store_subprocess_reopen_nov01.py \
 tests/test_strict_payload_v2.py tests/test_strict_archive_v2.py
```

This source checkpoint does not repair, adopt or authorize restarting any previously
failed run. The immutable failed evidence remains separate. Any actual-model retry
or new qualification needs its own explicit decision after review and source backup.
