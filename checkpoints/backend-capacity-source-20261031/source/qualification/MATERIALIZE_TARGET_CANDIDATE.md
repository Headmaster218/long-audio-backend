# Reviewed isolated source materialization

`materialize_target_candidate.py` copies manifest-listed source into a fresh
isolated directory and changes only runtime identity/evidence literals inside the
single product qualification record. It does not edit the original source, launch
anything, install packages, mutate an existing store, or enable general rollout.

The approval boundary is an explicitly reviewed JSON basis supplied with its exact
SHA-256. This tool never generates a basis or infers approval from successful
observation, audit, or tests. Its boolean/schema checks cannot authenticate a human
reviewer: the caller must trust the independently reviewed artifact and digest.
Do not fabricate a basis merely to make materialization pass.

The exact basis keys are:

```
{
  "schema": "reviewed-isolated-target-materialization-v1",
  "accepted_for_fresh_bounded_smoke": true,
  "general_production_rollout_approved": false,
  "existing_store_migration_authorized": false,
  "source_manifest_sha256": "<exact manifest hash>",
  "source_module_sha256": "<original native_owned_cell_store.py hash>",
  "target_sha256": "<frozen observation hash>",
  "audit_sha256": "<independent audit report hash>",
  "physical_result_sha256": "<physical RESULT.json hash>",
  "candidate_tests_sha256": "<candidate test receipt hash>"
}
```

Candidate-test receipts must have schema `target-candidate-regression-receipt-v1`,
integer `tests_passed` at least 9, integer `exit_code` zero,
`production_profile_activated` false, and matching `source_manifest_sha256`,
`source_module_sha256`, `target_sha256`, `audit_sha256` and
`physical_result_sha256`. Additional receipt fields may bind original logs, JUnit
or execution limits. The caller is responsible for generating honest receipts
from completed runs, not planned or inferred results.

The source manifest uses `standalone-source-checkpoint-v1`, with a `files` array
of relative `path`, integer `size` and `sha256`. Optional Git blob `sha` values are
also verified. All physical-probe source-manifest entries must still match.
Only manifest-listed source is copied. Symlinks, traversal, duplicate paths,
Git metadata, bytecode, audio, databases, model/library binaries, unsupported
suffixes and oversized input are refused. Current supported source suffixes are
`.py`, `.java`, `.json`, `.md`, `.patch`, `.sql`, `.toml`, `.txt`, or no suffix.
An input basis for an older manifest is not silently upgraded to a newer tree.

```
python -I -B qualification/materialize_target_candidate.py \
  --source /absolute/frozen/source \
  --manifest /absolute/SOURCE_MANIFEST.json \
  --basis /absolute/REVIEWED_BASIS.json --basis-sha256 EXACT_REVIEWED_DIGEST \
  --target /absolute/target.json --audit /absolute/audit.json \
  --physical /absolute/physical/RESULT.json \
  --candidate-tests /absolute/candidate-tests.json \
  --output /absolute/new-candidate
```

Output contains `source/`, `candidate.patch` and a final `RECEIPT.json` with source,
patch and candidate hashes. Receipt completion means only source materialization.
An I/O interruption can leave a partial directory without a completion receipt;
never treat that directory as complete or rerun into it. The tool refuses existing
outputs. Retain the reviewed basis and input manifest with the evidence separately.

All shipped allocation bounds and verifier/selector implementation remain intact.
Candidate startup must still perform actual runtime verification, on a fresh
private data directory, using the independently approved bounded launch plan.
Materialization is not proof that the current execution host matches the target.
Neither a relocated executable with shared standard library nor this utility
establishes fresh-host installation, production deployment, power-loss durability,
real-device behavior or acoustic accuracy.
