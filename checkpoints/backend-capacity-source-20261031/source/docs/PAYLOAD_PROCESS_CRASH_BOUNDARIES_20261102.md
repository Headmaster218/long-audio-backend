# Generated payload process-crash boundaries (2026-11-02)

This test-only milestone builds on public backend checkpoint
`e47746cff340f09089446cabd300f83d7068cbc4`. No production source, storage policy,
quota, model runtime or recovery algorithm is changed.

## Real operations and deliberately limited seams

Each case uses generated two-second PCM and existing component fixtures. The real
Store, SQLite WAL transactions, immutable payload writer, locks, schema admission,
publication checks and cold readers run. The existing `PayloadFrontier.guard`
component seam supplies the tiny fixture frontier; this is not a Worker/service
integration test. A test forwarding connection proxy blocks exactly the original
placement commit, and a module-local OS forwarding proxy blocks the original
stage-to-final hard link. No production hooks or fake Store/lock/reader are added.

The parent waits for an atomic test-evidence acknowledgement outside the Store
namespace. It checks the expected boundary, per-case nonce and its own Popen PID,
then sends SIGKILL only to that still-unreaped child. It positively reaps exit -9
before launching a separate reader exec. There are no child descendants, process
name searches, process-group signals or signalling of unrelated owners. A missing
barrier, wrong nonce or premature exit remains a failure; cleanup reaps the same
owned child, and any unknown exit is explicit rather than masking the first error.

## Three expected states

| Kill boundary | State after genuine new Store admission | Required result |
|---|---|---|
| Before final hard link, after durable reservation and closed/fsynced stage | reserved + hot bodies, no final or locators | Exact existing maintenance refuses `payload_reserved_unit_pending`; same reservation count/sequence/bytes and stage content remain. No automatic link, erase, replacement or quota relief. |
| Before original placement COMMIT, after actual locator/hot-body SQL | published + hot bodies, no locators | SQLite rolls back unfinished placement. Original maintenance resumes the same unit (`resumed=True`), preserving file hash/signature/sequence and installing exactly the previously planned locators. |
| After original placement COMMIT returns, before close/ack | committed + cold bodies | Original cold readers recover exact PCM and vector bytes. No repeat placement or fabricated reconciliation is needed. |

All cases reacquire the genuine instance lock in the new reader process and check
that another actual lock contender is excluded. They verify SQLite integrity and
foreign keys, exact state and row counts, immutable archive bytes, generated audio
hashes and source-file provenance. Cold states also use an explicit original
PayloadBudget to read PCM/vector, require positive SQL/read charges and unchanged
deadline, and verify exhausted-budget refusal without resetting it.

## Running and scope

```sh
python -B -m pytest -q -p no:cacheprovider \
 tests/test_payload_crash_boundary_nov02.py \
 tests/test_store_subprocess_reopen_nov01.py \
 tests/test_strict_payload_v2.py tests/test_strict_archive_v2.py
```

The new module contains three crash/reopen cases and three parent failure/cleanup
controls. All data are generated. Test receipts, temporary databases, stage/unit
files and runtime output are not part of the public source backup.

A SIGKILL is **not** a clean writer exit, simulated power loss, hardware/filesystem
failure or proof of fsync durability. The result establishes only these bounded
component process-crash states on the tested host/runtime. It does not qualify
whole-service recovery, real-model accuracy, devices or long-duration capacity.
The earlier broad-suite baseline failures remain outside this test-only scope.
