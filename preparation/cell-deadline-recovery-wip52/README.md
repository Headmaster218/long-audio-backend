# Cell owner deadline recovery candidate v1

Candidate ID: `cell-owner-deadline-recovery-v1-20261014`

This is a source-only reconstruction based on immutable product dev8:
[Headmaster218/long-audio-backend, 1fc066910ca78b2e57d7a28f79b91cafc06f5dd5](https://github.com/Headmaster218/long-audio-backend/tree/1fc066910ca78b2e57d7a28f79b91cafc06f5dd5).

## Scope

- Passive `Budget()` keeps its 0.5-second cooperative active-work limit.
- `Budget.for_owner(operation=None)` uses a fixed two-second active-work limit.
  Submitted work also retains its original admission deadline. Queue time cannot
  restart or extend that deadline.
- Every operation is admitted with a two-second deadline. Both wait paths default
  to two seconds and clip their wait to remaining admission time.
- Owner startup, startup receipt pages, owner operations and `Pin.read()` use the
  owner budget. CellJobsSource read and dispatch acquisition wait for up to the
  same deadline.
- Reconciliation yields to the existing occupied handoff slot after completing
  the current cell or receipt, including its cursor update. There is no added
  queue, retry mechanism or skipped item.
- Lock waits stay at 0.005 seconds. Close and pin-release waits stay at 0.5 seconds.
  SQL steps (150,000), SQL projection (262,144 bytes), source reads (41,943,040
  bytes), schema, SQLite busy timeout and all other storage limits are unchanged.

These are cooperative limits; they do not impose hard bounds on individual OS
calls or commits. A late commit can still have an unknown outcome. This change
does not extend runtime qualification, durability claims or working-memory claims.

## Provenance and contents

`base/` contains exact dev8 copies of the two modified product modules. Their
Git blob hashes match the immutable repository response:

- store: `c4aadddd3306e2a19c41364897be402113dd781b`
- jobs: `848d28874b018ab79cc12a977bc9116cd0209bb4`

`candidate/audio_backend/` contains the reconstructed modules.
`candidate/tests/test_cell_owner_deadline_recovery.py` contains 21 new focused
standard-unittest methods. The patch contains the two module changes and an
explicit update to the existing slow-owner test: the blocking gate becomes
3.0 seconds and the timeout delay becomes 2.01 seconds. Its passive-budget cases
are unchanged. The new test file is a separate overlay, not embedded in the patch.

The reconstructed jobs file exactly matches the historical 13,502-byte candidate
hash, `071112ba59dc8728b838013195eb3c3a15b67f205cdd97da9517b2f8e2cc647e`.
The store is a new 68,405-byte reconstruction,
`7ea280d9b5e2f1784daefa308d8dfce728f1d7b8a8554f2a587aedb6ccffd592`.
It does not match the historical store candidate and inherits no prior test
result. Exact byte counts, file hashes and ceilings are in `INVENTORY.json`.

## Verification status

Performed: immutable-source retrieval, byte/blob hashing, source review, patch
syntax inspection and non-mutating applicability checking of all three changed
files against exact dev8 source. The legacy test's removed lines were also checked
against its exact immutable source.

Not performed: product imports, unit/integration tests, database creation, model
loading, native runtime execution, timing measurement, publication or deployment.
No full-suite pass or runtime qualification is claimed.

## Future regression recipe

Run only after execution is separately authorized, in a clean disposable checkout
at the pinned dev8 commit with the project's normal test dependencies available.
Set `RECOVERY_BUNDLE` to this bundle and work from that checkout's root. No
production import path or runtime selector is changed.

```sh
test "$(git rev-parse HEAD)" = 1fc066910ca78b2e57d7a28f79b91cafc06f5dd5
git apply --check "$RECOVERY_BUNDLE/owner-deadline-recovery.patch"
git apply "$RECOVERY_BUNDLE/owner-deadline-recovery.patch"
cp "$RECOVERY_BUNDLE/candidate/tests/test_cell_owner_deadline_recovery.py" tests/
cmp audio_backend/native_owned_cell_store.py \
    "$RECOVERY_BUNDLE/candidate/audio_backend/native_owned_cell_store.py"
cmp audio_backend/native_owned_cell_jobs.py \
    "$RECOVERY_BUNDLE/candidate/audio_backend/native_owned_cell_jobs.py"
TEST_TMPDIR="$(mktemp -d)"
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$TEST_TMPDIR" PYTHONPATH=.:tests \
  timeout -k 5s 120s python -m unittest -v test_cell_owner_deadline_recovery
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$TEST_TMPDIR" PYTHONPATH=.:tests \
  timeout -k 5s 120s python -m unittest -v \
  test_native_owned_cell_store.StoreFixture \
  test_native_owned_cell_jobs.CellJobsTests
```

The second command group uses existing synthetic fixtures with simulated
qualification and real temporary SQLite mechanics; it is not an exact-runtime
qualification gate. Keep all fixture data in the temporary directory and do not
point tests at production data. Neither group invokes a native model by design.
Record the exact source hashes, command, interpreter and results when execution
is authorized; do not substitute historical results.

Further real-owner 1/3/20-item mixed workloads, queue-delay measurements and
maximum SQL-step observations require fresh execution and their own recorded
results. They have not been reproduced by this source-only candidate.
