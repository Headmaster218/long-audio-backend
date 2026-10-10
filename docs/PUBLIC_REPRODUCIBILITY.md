# Public Git-only provenance checks

This tooling/docs follow-up starts at public dev10 commit
`0a27197979c8e96fa560432f7a15f49c9e1965fb`. Runtime code, prior feature
blobs, package version `0.2.0rc2.dev10`, and historical import/layer manifests
remain unchanged. This is source validation, not model-quality acceptance.

Use a full public clone of `Headmaster218/long-audio-backend`, then check out
the reviewed follow-up revision containing this document. Fetch all public
branches; a shallow or single-branch clone may lack required historical objects:

```sh
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
python -m pip install -e '.[test]'
python -m pytest -q tests/test_public_history.py tests/test_fragment_staging_sources.py
python -m pytest -q tests/test_cli.py tests/test_candidate_ledger_api.py
```

Python 3.12+ and Git are required. These focused checks use no models. An
editable install prepares dependencies; it does not download model weights.
The CLI/API smoke tests do not prove inference readiness or full-suite success.

## Why a map is necessary

Historical manifests retain original commit IDs. The public connector history
uses different commit IDs, while preserving the reviewed source bytes. An
unmodified public dev10 audit produced **82 failures, 16 setup errors, and 31
passes** in the historical staging tests; CLI/API smoke tests passed. Those
failures remain the pre-repair result, not evidence of model failure.

`PUBLIC_HISTORY_MAP.json` declares exactly 15 original-to-public lookups plus
the immutable dev10 baseline. Each public SHA, tree and ordered parent list is
pinned. Before this repair, these mappings were checked against 124 existing
file/blob/absence pins, four composition merge bases, dev9 ancestry, and dev10
parent order. The test suite independently checks those historical manifests.
The `74b51d...` projection reference is metadata only; it is intentionally not
mapped to an invented public object.

`scripts/public_history.py` verifies the map digest and commit structure before
reading files. Unknown revisions, missing commits/trees/blobs, and unexpected
graph results fail closed. A missing file is recognized only in a verified tree.
There is no private-object, branch-name or approximate-match fallback.

`PUBLIC_RECOVERY_FOLLOWUP.json` pins the exact reviewed tooling/test/docs delta.
Its own digest is excluded to avoid circular self-pinning; its exact schema,
canonical serialization, fixed scope and path set are independently tested.
Every other inherited byte must equal immutable public dev10. The source guard,
privacy exclusions and 3,200,000-byte total / 250,000-byte per-file caps remain.

Do not import private recovery bundles, models, databases, reports or audio to
make these tests pass. Public-only object-store checks require the 15 original
objects to remain absent. Historical materials mentioned in README are not
published by this follow-up. The standalone numeric-grid branch still needs
its own provenance repair; this change does not claim to fix that branch.

## Validation progress (2026-10-10)

Focused provenance: 185 passed (162.63s, 110.2 MiB peak process-tree RSS).
CLI/API: 15 passed. Wheel build, import and `/health`, `/openapi.json`, `/docs`
passed without models, workers disabled. CPU 0/nice 19; all resource limits held.
The initial 90-second cutoff was incomplete; a later 180-case run passed before
mode/type hardening. These focused checks do not establish full-suite or
inference/model acceptance.
