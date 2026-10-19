# Primary cache retirement integration: partial, unqualified WIP

Snapshot date: 2026-10-19 UTC. This source-only recovery checkpoint is not a release or deployment recommendation. It preserves the integrated implementation and finite synthetic test sources while qualification is incomplete. No package or version change is included.

## Ancestry and scope

The sole intended parent is schema WIP commit `e5ddabe54ae2ee5c137cde20654ae4edb2fc1e62`, tree `51e411aae0bff05514428e704c9e9ccaf19f2a65` (574 files, 10,357,701 bytes), itself a child of `5deca15f9b37eb28fcb912a7fabc0163812942c7`. Every parent path is preserved unless expressly modified by this checkpoint. The 26 previously listed report/status paths and the schema WIP status report are all preserved byte-for-byte.

The accepted implementation plan has SHA-256 `aabc0df10c585b07f635eb09d2aff00e506fee11e7dac89946f0ba46da3cf80e`. The full feature changes only the eight approved existing product modules: recording overlap, primary provenance, storage, owned-core seams, cell capture, cell store, MOSS jobs, and automatic MOSS coordination. Relative to the schema WIP, storage is already unchanged. This checkpoint also adds two narrowly scoped retirement test modules and updates the existing synthetic CellStore authority fixture with an explicit false-marked method. No permissive production authority fallback is added.

The active implementation and test tree at this snapshot is `5c3a64d02fe21eb0d220af715da85ed982a014fa` (576 files, 10,450,517 bytes), before addition of this report. Source manifest SHA-256: `a3b0a8d6cc26e6fbc210e6d8b6188e06b42820247460f77cf17158107a8f0f22`.

## Implemented boundaries, still requiring complete qualification

- Fresh exclusive-primary batches may acquire the optional immutable policy marker; existing or mixed batches are never adopted. A known optional marker-capacity refusal leaves both marker fields permanently NULL and preserves primary creation. Genuine schema, accounting, and uncertain-write errors remain hard failures.
- Additive SQLite migration preserves old rows and exact contracts, charges 512 bytes per marked batch, and fences the old initializer through an exact-schema mismatch. Store binding is verified against the real Capture/CellJobsSource ownership and immutable CellStore key.
- MOSS admission and transactional guards reject work in marked domains before lease/reservation acquisition, at claim and before-window checks, and at final publication/commit checks. Declared and canonical chunk-session membership are covered.
- Existing collectors retain their caps, bounded projections, progress budgets, and durable-receipt requirements. Qualified completed gaps may advance a prefix, but every actual deletion candidate must retain durable receipt proof. Held/unresolved primary probe copies remain roots until their explicit release.
- Publication reconciliation retains immutable binding/charge and exact affected page/packet identity while allowing unrelated later batch progress. Current accounting and authority are separately rechecked.
- No audio unlinking, mixed/legacy reclamation, history/comparator changes, packet-body changes, or quota/cap increases are included.

## Retained execution record and precise pass credit

The approved finite selection contains 90 synthetic cases: A44 (schema, activation, qualification and bounded adversaries), B21 (collector/replenishment and rollover), and C25 (pipeline roots, restart/revocation, unknown acknowledgments and six existing authority compatibility cases). Tests use standard pytest and existing bounded controls. Models, external audio, network access and dependency installation are outside this gate.

1. Initial A attempt, source tree `afd87f9adb015681e0a6aac976ee86514cad8e1f`, stopped on its first test with `KeyError: 'path'`. The test mistakenly expected the public Store.chunk projection to expose its internal audio path. No migration or predecessor child was reached, and this attempt has zero pass credit. The correction uses a bounded exact chunk ID/session query and validates fixture-root containment. Execution receipt SHA-256: `b4cd46306aaf7c26cd8aa332b6f3427e4d2d08fbef282d687e020f4c826ad31a`.

2. Corrected A attempt, source tree `63e688cc9792b82b76b1c638605aade0227f489c`, passed exactly `tests/test_primary_cache_retirement.py::test_A1_exact_migration_and_old_usage_reader_refusal`. It exercised actual additive SQLite serialization and genuine predecessor Store initialization in one bounded, positively waited fresh interpreter, including logical schema/table/packet/charge and unchanged audio conservation. The exact test and product bytes remain unchanged in this checkpoint. Execution receipt SHA-256: `102decc68f3090c298e40d774b9b5c673734fce9482dd9bfea51d32958b5c194`. Old-reader evidence SHA-256: `0be1074d275fc883f82f772e83becabdc29047e6121785ab8b38e97e802e687f`.

3. That second attempt then stopped in the production-entry marker test. Marker creation, all-gap unbound publication, and the first durable store binding had been reached, but the shared open-stream fake reported zero stable frames. The real Worker correctly retained the first chunk as waiting_input at offset zero. These partial assertions are not counted as a passed case. The retained fixture diagnosis and all terminal evidence remain available; no product lifecycle change was justified.

4. The scoped retirement pipeline fixture now reports a non-final synthetic streaming frontier bounded by actual supplied WAV frames, with exactly one response-observer notification. The completion helper performs one justified cohort for each fresh 10.5-second chunk and asserts completed status, exact duration offset, and absence of a held primary copy. It does not poll or artificially drain. At this report snapshot, this correction and its remaining A43 invocation have not executed. B21 and C25 also remain unexecuted.

Only the one source-bound A1 pass is accepted from this work. Both failed attempts are retained and charged. Their cumulative work/total/CPU usage is 12.885981901 / 14.227473385 / 16.121043 seconds. Remaining A limits are 147.114018099 seconds work, 165.772526615 seconds total, and 103.878957 aggregate CPU seconds. The remaining A43 topology has no child process. All later execution requires independent frozen-source/argv review and the coordinator's serial release; failures do not authorize retries.

## Limits and next gates

The overall approved envelope remains 540 seconds work, 600 seconds total, 420 aggregate CPU seconds, CPU 3 at nice 19, 384 MiB aggregate RSS, 1 GiB pytest address space, and 160 MiB cumulative artifacts. Both completed attempts exited with positive cleanup and retained source/input/resource evidence. No current test or model process is active at this snapshot.

Next steps are the independently reviewed remaining A43, then separately released B21 and C25 with all prior costs charged. The rollover cases must cross both the existing 2,056 compact and 4,096 receipt limits without raising either limit, using qualified retirement and replenishment. They are not yet passed. Restart, source revocation, unresolved roots, dirty-domain denial, delayed/unknown acknowledgments, optional-capacity fallback, and conservation assertions remain pending beyond the one migration case described above.

Removing a metadata cache wall does not establish 12–48-hour actual acoustic endurance or audio-retention capacity. The existing PCM/provenance quotas and retained audio remain unchanged. Historical reports, including the prior source-bound synthetic primary-only qualification and its failures, are preserved without being relabeled as this feature's qualification.
