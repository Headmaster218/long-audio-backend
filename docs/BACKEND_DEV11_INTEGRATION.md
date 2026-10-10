# Backend dev11 integration candidate

Version `0.2.0rc2.dev11` combines four reviewed source contributions:

1. Canonical duration/EOS corrections, including legacy measured resampled ranges.
2. Private prospective failed-overlap diagnostic retention.
3. Bounded v2 event-candidate sample-grid ownership and sanitized numeric audit.
4. Git-only public history recovery.

This is a local integration candidate, not a release, production recommendation,
real-model quality result, or long-run acceptance. No models, thresholds, defaults,
public API contracts or new database schema are introduced by this integration.
The existing dev10 additive overlap diagnostic schema is preserved.

## Exact source composition

The genuine merge has these ordered parents:

- Repaired public dev10: `366797b5c0bba70975507b71db8b4596b6d9344f`,
  tree `dc6c9bd493238b4b6c41b087eb668a60f1726895`.
- Standalone numeric fix: `5cca518467e5d03eaced884f73798917542f79fd`,
  tree `e35afda019d60bb0590b7b65480f55dbc38f2ef6`.

Their exact merge base is `4bde1e8d2fd050a10a1168e4cb46f80a5db6d28c`.
`BACKEND_DEV11_INTEGRATION.json` pins both public commit structures, the exact
19-path delta from repaired dev10, prior inputs and reviewed runtime results.
Its own recursive digest is excluded; exact schema, serialization, scope and path
membership are tested. Every inherited byte outside that delta is unchanged.

`event_candidate_store.py` is the numeric branch's exact blob. `storage.py`
combines its single provenance-retention call with the existing overlap resource
checks, evidence persistence and transaction. `worker.py` combines numeric
ownership with the unchanged duration/EOS decoder changes. Neither file is
replaced wholesale by its older dev8-derived version. All other runtime files
are dev10's exact bytes, except the explicit package-version declaration.

The numeric rule retains its bounded guarantee. It does not establish invariance
under arbitrary cancellation/rebasing or global timestamp sample precision.
Excluded numeric intervals remain unverified model-window evidence; no duration
is invented. See [the original numeric contract](EVENT_CANDIDATE_NUMERIC_OWNERSHIP.md).

## Historical checks and current checks

`FRAGMENT_STAGING_IMPORTS.json`, `FRAGMENT_STAGING_LAYERS.json`,
`PUBLIC_HISTORY_MAP.json`, `PUBLIC_RECOVERY_FOLLOWUP.json`, the public-history
helper and prior recovery documentation remain byte-for-byte unchanged.
The historical layers' target remains dev10; the new integration manifest owns
dev11's current version. The standalone numeric manifest, documentation and
synthetic runtime test file also remain exact historical source artifacts.

The public map still resolves only its reviewed original-to-public entries.
Unknown objects fail closed. Historical dev8/dev9/dev10 checks run on their
immutable mapped public trees. The exact recovery delta and mode tests run on
`366797b...`; the standalone numeric delta and mutation tests run on `5cca518...`
against public dev8. They no longer mistake today's integrated worktree for one
of those historical trees. No old original commit object is imported.

The new current-tree gate independently verifies exact paths, inherited bytes,
versions, both runtime compositions, history preservation, modes and worktree/index
agreement. Before commit it requires the correct `HEAD` and `MERGE_HEAD`; after
commit it requires the exact ordered parents and committed-tree/index agreement.
A full public-only clone is required: no shallow history, private object fallback,
alternates, grafts or replacement refs. See the unchanged
[public history explanation](PUBLIC_REPRODUCIBILITY.md) for the earlier repair's
scope and historical validation results; those results are not dev11 acceptance.

## Explicit source-budget change

The planned tree has exactly 283 files: repaired dev10's 277, the numeric branch's
three additions, and this integration's three new artifacts. Exactly thirteen
inherited files change. The new policy allowlist adds precisely those six paths.

The measured union was already 3,238,414 bytes before policy/test resolution and
new integration artifacts, exceeding the historical 3,200,000-byte ceiling by
38,414 bytes. Therefore dev11 explicitly raises only its aggregate source ceiling
to 3,400,000 bytes. The final tree is measured against that ceiling; this document
does not substitute a predicted byte total for the measured result.

The per-file limit remains 250,000 bytes. Privacy exclusions, content/path checks
and `source_staging_guard.py` are unchanged. The old recovery and numeric gates
still enforce their original policies on their original trees. This budget
change neither permits private reports/media/models nor broadens eligible paths.

## Model-free verification

Use Python 3.12+ and the repository's existing test dependencies. Do not download
models or import private recovery inputs merely to run these checks. With no
real-data/model opt-in environment variables set, run:

```sh
python -m pytest -q tests/test_fragment_staging_sources.py tests/test_public_history.py tests/test_backend_dev11_integration.py
python -m pytest -q tests/test_event_candidate_numeric_projection.py tests/test_canonical_duration_eos.py tests/test_overlap_diagnostics.py tests/test_overlap_ownership.py tests/test_overlap_store.py tests/test_overlap_public_contract.py tests/test_moss_td_candidates.py
python -m pytest -q
```

The synthetic combined tests exercise numeric exclusions alongside failed overlap
evidence, durable main output, atomic rollback/retry/replay, resource rejection
and public privacy. Existing suites cover numeric endpoint sweeps, native EOS,
legacy decoding, individual budgets, provenance, lifecycle and schema invariance.

Run the complete default suite against the final exact tree. Report failures,
skips and the two existing strict historical q1 expected failures explicitly;
do not convert missing private fixtures into inferred passes. Build the wheel
from a Git-only tree export, then verify import and workers-disabled local
`/health`, `/openapi.json` and `/docs`, without loading model modules.
A command appearing here is not evidence that it has passed. Validation reports
must identify the exact tree and environment they actually tested.

No real-model smoke is included. A later separately authorized, resource-bounded
smoke must use fresh isolated state and must not modify any preserved run or
claim to recover historical lost diagnostics.

## Public test portability

The old boundary-ownership fixture now uses schema-valid `door` and `rain` score
rows with source labels. All interval/support and subthreshold-score assertions
remain; production candidate validation is unchanged.

Strict CPU-provider tests run their exact selected original nodes in one fresh
pytest subprocess, with original setup/call/teardown outcomes relayed to the main
suite. This preserves their module/session fixture behavior while separating the
attested provider closure from unrelated NumPy testing modules loaded by earlier
tests. In particular, NumPy's empty testing-package marker remains inadmissible
under the unchanged production policy. Child failure, missing reports, unexpected
exit and timeout fail closed; no tests are dropped or declared passed by inference.
The synthetic Qwen candidate-store module uses the same per-module isolation so
its unchanged 256 MiB caller-plus-child admission is not consumed by unrelated
earlier pytest allocations. Each module retains its exact selected nodes and
original fixture lifetimes; separate result caches are verified together through
the actual parent terminal and JUnit consumers. Resource limits and descendant
cleanup assertions remain unchanged.

Positive-event fake-model transport and failure tests use deterministic synthetic
PCM and explicitly synthetic selection-score metadata generated in temporary
storage. They retain their transport, ownership, budgeting and failure assertions.
The separate historical private-data selection check is still an actual historical
check and is explicitly skipped when its undistributed fixture is absent; synthetic
success never stands in for that evidence.

Use an interpreter with the declared dependencies installed, including soundfile:
some lifecycle tests deliberately replace PYTHONPATH for controlled fake-worker
injection. Merely supplying runtime dependencies through a parent's PYTHONPATH is
not sufficient for those child-process tests. No downloads or model setup are
needed for this matrix. For bounded local scratch, pytest's ordinary
`-o tmp_path_retention_policy=failed` option releases passing-test temporary files
while preserving failed cases. External resource enforcement still applies.

Synthetic decoded-source subprocess tests explicitly select one CPU from their
launcher's inherited affinity, using the existing psutil query when the OS query
is unavailable. Their backpressure, cancellation and resource-limit assertions
and the production decoder's default CPU remain unchanged. These Linux-oriented
process tests do not establish support for other operating systems.
