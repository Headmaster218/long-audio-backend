# Staged recording visibility prerequisite

This source snapshot is a model-free prerequisite for future paged recording ingestion. It is based on dev13 commit `1ee53f53d52cb26aca656aca37504b765fe0c822`; it does not replace the installed dev13 wheel or qualify continuous pages. Apply the seven files under `source/` to that source checkout to reproduce the candidate.

## Behavior

Recording reservations remain available for receipt attestation, but unpublished work is hidden from worker claims, audio context, stream end/final interpretation, native source mapping and predecessor wakeups. Processing status alone is insufficient: cancelled, unpublished recording work must remain hidden.

Release validates one complete contiguous reserved suffix through the raw stream tail. Chunk release, predecessor wake and the existing recording-batch transition share one SQLite transaction. A reader sees its previous snapshot or the released state. Receipt validation remains available before release, so the durable receipt outbox can complete. Ordinary upload and finalization behavior is preserved.

## Recorded checks

- 174 distinct selected model-free tests and 9 subtests passed in one deduplicated run.
- Four corresponding tests on the unchanged dev13 baseline reproduced early wake, context/end exposure, a waiting-input race and cancelled-unpublished exposure.
- The candidate tests include real SQLite transactions, rollback, concurrent reader snapshots, both wait/release orderings, full-tail validation, current-owner receipt attestation and idempotent committed output.
- Test time: 73.97 seconds; supervised elapsed: 75.60 seconds. Sampled peak RSS: 315,490,304 bytes. Peak charged source/test/output files: 9,965,568 bytes.
- Test limits: one CPU, nice 19, 180 seconds, 1 GiB sampled RSS threshold and 2 GiB address space. Sampled RSS is not a continuous peak guarantee.
- No schema, capacity, version or model change was part of this snapshot. No acoustic models or HTTP server were used by these checks.

Representative command, using the project's existing test dependencies:

```sh
python -m pytest -q --import-mode=importlib tests/test_staged_stream_visibility.py tests/test_stream_time_index.py tests/test_stream_boundaries.py tests/test_stream_api_contract.py tests/test_lifecycle_durability.py tests/test_api_storage.py tests/test_recording_overlap.py tests/test_native_owned_cell_capture.py tests/test_canonical_duration_eos.py
```

## Limits

The publication predicate uses the current whole-batch queued state. A future continuation feature needs permanent page-level release state; reopening a queued batch would incorrectly hide its published history. Public reservation metadata remains visible and must not be interpreted as worker completion or EOS.

These checks do not establish hour-file overlap ingestion, continuous-page processing, model-output equivalence, long-duration resource stability, Windows or device qualification. Existing recording limits and receipt-storage capacities still apply.
