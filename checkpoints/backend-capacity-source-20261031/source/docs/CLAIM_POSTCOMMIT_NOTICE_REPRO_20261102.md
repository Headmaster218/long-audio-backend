# Strict claim post-COMMIT notice-budget reproduction

Source baseline: `27a1c45292d0ccd551fb39469293c6d95a1517d2`.
The original reproduction is preserved in local commit
`164a180c3dafaf2220c1b844ff761ed0f3f2bfbd`. This document separates that baseline
mechanism from the narrowly reviewed correction below.
No prior run database, recording, model, inference or production worker thread is
used. Generated two-second PCM and the existing strict Store fixture supply the
real recording schema and queued work.

## Exact observed boundary

A sqlite3.Connection subclass forwards the real claim transaction and COMMIT.
Only after COMMIT returns positively, a separate read-only connection confirms
that the chunk is durably `running`. The test then advances the existing native
Budget's elapsed accounting by 0.533518 seconds. No deadline/cap is increased,
SQL body replaced, exception fabricated, or operation retried.

Original Store.db subsequently sets sql_outcome to `committed` and executes its
post-COMMIT budget.check. That check raises the original
`Unavailable('bounded_planning_capacity')`; the return prepared by Store.claim
never reaches its caller. Original Worker.loop likewise propagates the refusal
before worker.process is called, publishing terminal class `Unavailable`.

The test also establishes a narrower accounting defect: the exception handler
calls rollback after COMMIT has already ended the transaction. This no-op
rollback returns positively, and Store.db changes the known `committed` outcome
to `rolled_back`. The generated database still contains a running chunk and an
active strict control. Actual SQLite integrity, foreign keys and original
connection cleanup remain positive. Because claim_running_write is True, the
claim wrapper does **not** incorrectly treat this as a safe pre-claim defer.

The original three cases passed: direct claim, original synchronous Worker.loop, and the same
generated strict claim with no injected accounting event. The original worker
constructor starts no thread or model here. The baseline control returns the
claim normally.

## Interpretation and limits

Failing closed after an already-committed running transition is not authorization
to retry, requeue, adopt, or describe the transaction as rolled back. Preserving
`committed` in the diagnostic outcome is independently justified. A policy for
late committed delivery would require separate owner/permit/cleanup review.

The injected event occurs **after** COMMIT. It demonstrates loss of the return
at that boundary, not a SQL planner regression or the cause of any historical
wall-clock delay. Test SQL steps remain below the existing 150,000 notice cap;
this does not prove whether real elapsed time came from query planning, storage
latency or scheduling. No budget increase or performance-cause attribution follows from this reproduction.

## Running

Use workspace-backed temporary data, retaining all original reserve guards:

```sh
mkdir -p ../backend-claim-postcommit-repro-validation-20261102
python -B -m pytest -q -p no:cacheprovider \
 --basetemp=../backend-claim-postcommit-repro-validation-20261102/generated \
 tests/test_claim_postcommit_notice_nov02.py
```

## Narrow committed-late ownership transfer

The current correction preserves a positively committed SQL outcome even when a
subsequent rollback is a no-op. A generic claim caller still receives the original
deadline refusal. Only the original Worker, inside its existing primary claim
probe, may receive a one-shot authenticated committed-late ownership receipt.
The original COMMIT and original connection close must both have returned
positively. The receipt binds the same Store, Worker, thread, original probe-entry token, permit, exact row,
source, offset and strict nonce; it is not a new claim or permission to replay.
No SELECT, nonce generation, transaction body or COMMIT is repeated to deliver it.
Pre-commit/body failures and uncertain COMMIT or close never qualify.

The original 0.5-second notice refusal remains diagnosed as late, not reported as
within-budget success. The Worker records a distinct committed-late count, also included as a non-sensitive
integer in the qualification service health and terminal receipts. Its
first claim acquires the original primary intent before releasing the probe; a
fair second claim retains its previously acquired intent. Normal native execution
admission and cancellation remain separate from ownership delivery.

For a late claim only, a separate execution-admission read uses the existing
0.5-second Budget and Store database helper with query-only enabled and one explicit read snapshot. Existing
strict_recording.active verifies the original row, nonce, source, offset and
durable tip before native/model preparation. The original claim budget is never
reset or reused as a fresh allowance. Stop is checked before and after this read;
a prior cancellation or nonce replacement refuses execution. This does not claim
to eliminate all future cancellation races; original execution polling remains.

The tests use generated fixtures, real SQLite and original permit/Worker control
paths with explicitly bounded model-free process substitutes where required.
They are not model accuracy, wall-clock capacity or durability qualification.
No previously failed database is repaired, adopted, resumed or reinferred.

## Regression limits

Broader pre-existing ownership tests are not all green: one selected ownership
group has 89 passes and 13 failures; a strict-worker group has 41 passes and
3 failures. Both groups were independently repeated on the unchanged baseline
27a1c452 with the exact same failing test IDs. This checkpoint does not relabel
those failures as passes or claim full-suite qualification. Current focused
receipt, crash, reopen and capacity-harness checks are reported separately.
