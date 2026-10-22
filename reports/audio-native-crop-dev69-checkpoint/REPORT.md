# Native crop query support: recovery and opt-in candidate

Status: candidate source checkpoint; complete focused qualification remains open. This
checkpoint does not change the repository's root product, merge a branch,
activate a bank, publish a release, or establish real-audio identity accuracy.

## Why this work resumed

The previous fixed-window dev68 experiment remains **inconclusive**. Its
reference coverage was insufficient, its query labels were not opened, and no
stage predictions were produced. Its fixed windows, criteria and result are
not being changed or retroactively repaired.

A fresh cloud filesystem no longer contained the historical runtime, models,
audio, databases or local-only experiment helpers. Published commit
`4131b4dcda46a761dd185ae071dc49b985f3144c` was recovered, and the nine dev66
and eight dev67 source overlays were hash-checked to reconstruct a separate
138-product-file baseline. Restored source is not a restored historical run.

Inspection found that the production query path, not only the previous test
adapter, rejects a crop whenever its entire source packet has any overlap
witness. The existing crop selector already subtracts competing native-label
turns and erodes crop boundaries. A separate opt-in policy now derives exact
crop-local support from that retained, immutable packet evidence.

## Candidate behavior and limits

- Existing schema-1/default whole-packet veto remains unchanged.
- Schema 2 explicitly selects `native_crop_support_v1` in a **new immutable
  identity bank**. Existing banks and pending/completed queries are not migrated.
- Crop support is derived in the same snapshot after packet, node, source,
  certificate and complete witness validation. Missing or invalid support
  refuses the query. Whole-packet audit evidence is retained.
- Target-label coverage must include the crop plus erosion margins; competing
  labels may not intersect that interval. Logical and physical coordinates
  must agree.
- Policy version/hash are retained and checked with schema-2 outcomes. Immutable
  bank policy and existing group/generation identity isolate cached jobs.
- Human association/enrollment authority, exact binding hashes, score
  thresholds, margin, negative evidence and gallery limits are unchanged.
  This feature neither enrolls references nor rewrites transcripts.

The result means **native metadata supports this crop**, not acoustic purity.
Allowing a crop to proceed to existing scoring is not a successful identity
match. Cross-day stable identity and self-learning acceptance remain open.

## Fresh finite verification history

These are new runs in a new environment, not requalification of the lost
historical runtime. Tests use synthetic fixtures without models or user audio.

1. Reconstructed dev67 startup-recovery module: **16 passed**, 3.04 seconds.
2. Initial crop candidate plus unchanged conservative overlap control:
   **30 passed**, 6.78 seconds.
3. Initial three-module regression: **172 passed, 5 failed**, 142.31 seconds.
   All five failures reported missing `soundfile` package metadata before the
   relevant assertion paths; this failure is retained rather than relabelled.
4. Revision 02 adds seven explicit schema-2 cases for negative evidence,
   capacity, conflicting manual claims, and missing/corrupt cached policy
   identity. After dependency installation, the four-module run completed:
   **211 passed, 2 failed**, 167.21 seconds. The two failures occurred in
   existing reviewed-person cases (`metadata_capacity` and a
   deadline-related `SQLITE_INTERRUPT`). All 36 new crop-policy controls passed,
   but the complete focused gate did not pass.
5. An isolated rerun of those same two failed nodes, with unchanged source and
   unchanged storage limits, produced **2 passed**, 3.29 seconds. This does not
   erase or convert the failed broad run. One broad failure recorded the
   existing 250 ms storage deadline at 2,300 VM steps; the first capacity
   refusal remains ambiguous. No resource limit was relaxed to obtain a pass.

The first candidate's incomplete `diff -ru` artifact omitted new-file bodies.
It is not a reconstruction artifact. This checkpoint instead includes every
changed/new file and a complete new-file-inclusive patch; see README.md.
The parent verified all eight payload hashes and successfully ran the patch
application check against the reconstructed dev67 baseline. This check does
not itself constitute a test run or a production installation.

## Reproduction and remaining qualification

Follow README.md's exact published-base overlay sequence. The authoritative
delta is `source/`, verified by `source-files.sha256`. Do not overlay directly
onto the older root product or overwrite archived reports.

The focused gate uses Python 3.12.14, pytest 9.1.1, disabled automatic plugins,
no bytecode/cache writes, a fresh temporary directory, and finite process
memory/time limits. Legacy provenance controls require `numpy`, `soundfile` and `sherpa-onnx`
package metadata (including the declared sherpa core dependency); the shorter
dependency note in the bundled preparation README is not exhaustive.
Runtime dependency versions must be recorded for each
reproduction; the historical locked environment is not claimed restored.

The old dev67 process-crash helper was not included in its historical source
checkpoint, so that old crash test is not independently restored here. No
package-install qualification, model inference, microphone endurance,
real-audio accuracy or production activation is claimed. A subsequent acoustic
trial needs a separate prospective sampling plan and opportunity-level census;
insufficient reference coverage must remain an explicit terminal result.

## Focused command and unresolved gate

The actual broad-gate command ran from the revision-02 candidate root, with
test/runtime packages in the sibling directories named below. For reproduction,
install the versions in FRESH_RUNTIME.json into equivalent isolated paths and
use a new temporary path each time:

```sh
ulimit -v 2097152
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=../audio-test-tools-20261022:../audio-recovery-deps-20261022:. \
timeout --signal=TERM --kill-after=10 240 python -m pytest -q \
  -p no:cacheprovider --basetemp=../audio-crop-rev02-gate-20261022/tmp \
  tests/test_primary_crop_support.py \
  tests/test_primary_voice_references.py \
  tests/test_reviewed_primary_persons.py \
  tests/test_primary_history_startup_recovery.py
```

The recorded broad run used a 2 GiB process virtual-address ceiling and a
240-second outer deadline (TERM, then KILL after 10 seconds). Those are test
execution bounds, not changed product resource limits. Its original process
exit was 1. The diagnostic rerun targeted exactly:

- `tests/test_reviewed_primary_persons.py::test_nested_exact_edge_undo_preserves_original_association_owners[undo-bc-first]`
- `tests/test_reviewed_primary_persons.py::test_different_people_blocks_union_and_exact_crop_alias_conflicts_after_undo`

The passing isolated rerun establishes only that these two cases passed in
isolation. The broader intermittent/resource-related failures still need
investigation before calling the full focused gate qualified.
