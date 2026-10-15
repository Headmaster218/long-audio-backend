# Quiet-seam continuity source checkpoint

This evaluation checkpoint contains a capture candidate and reversible source
templates. It is not an installation, runtime change, release qualification,
or a standalone runnable package. No evaluation is run by reading these files.

## Capture contract

`native_owned_cell_capture.py` is byte-identical to the tested capture candidate
(SHA-256 `b1113c7366e4677bf794458d1ccabbb829724f67ccd2d1d432fe8da2f3dd7494`).
Only `Capture.commit_seam` differs from its frame-origin predecessor.

When composition of A-B raises exactly
`Unavailable('no_same_response_native_overlap')`, a valid B terminal tail can
survive for the next B-C seam. Before promotion, the capture code validates
mapping, both witnesses, a distinct terminal-tail witness, provenance and
strictly increasing observation count. Current commit authority, hook identity,
owner/reset generation and revocation checks remain required. Malformed proofs,
coordinate errors, other exception types and nonexact reasons still fail closed.
The pure seam-selection/composition API still rejects a pair with no qualifying
same-response native overlap. Selected composite admission and handoff limits
retain their existing behavior.

## Reporting contract

The API helper preserves `last_capture_reason` as historical information.
`owner_work_pending` is true only for a positive valid integer
`handoffs_active_or_pending`, false for zero, and null for a missing or invalid
counter. The summary exposes `optional_owner_work_pending`. This is an owner-work
projection, not a per-publication terminal outcome. Warnings and idle checks are
unchanged.

## Files and configuration

- `native_owned_cell_capture.py`: exact product source, for inspection.
- `test_seam_tail_continuity.py.template`: 20 focused model-free regression cases.
- `smoke.py.template`: complete inherited API smoke helper with corrected health
  reporting. Its existing actual-run path can start model processes and write
  local output; it is not the 31.5-second validation harness summarized here.
- `test_reporting.py.template`: six reporting check groups using two separately
  supplied pinned health snapshots and synthetic counter/error variants.
- `RESULT.md`: observed outcomes and limits.
- `FIELDS.json`: bounded machine-readable outcome fields.
- `MANIFEST.json`: content identities, template requirements and target metadata.

Templates retain all logic and use literal `@NAME@` configuration tokens in place
of local paths, input/snapshot identities and run labels. They are deliberately
named `.template` and are not ready-to-run scripts. No fixture audio, transcript,
health snapshot, raw receipt or database is included.

For the continuity template, supply the pinned base and predecessor source trees
at `@BASE_SOURCE_DIR@` and `@PREDECESSOR_SOURCE_DIR@`, relative to the test's parent
directory. `@CANDIDATE_MANIFEST_FILE@` names a local manifest beside the configured
test with a `files` object mapping each checked filename to `bytes` and `sha256`.
Create that manifest for the configured files; this checkpoint's manifest has a
different purpose. The dependency pins and CPU/resource restrictions are retained.

For the smoke template, configure the workspace, output leaf, assets, runtime,
source lock and its digest, input file and digest, interpreter digest, identity
group, stream ID and idempotency prefix. Its inherited input geometry and source
pins remain fixed; the capture pin in that helper is not an installation recipe
for this candidate. Do not silently substitute different dependencies or inputs.

For the reporting template, supply two health-snapshot subdirectories and their
SHA-256 values, expected byte size and shared relative directory. Configure
`@CONFIGURED_SMOKE_SHA256@` to the digest of the configured `smoke.py`, located
beside the configured reporting test. Snapshot expectations in the test must
remain satisfied. This checkpoint does not include those private observations.

Results include the copied-state diagnostic completed on 2026-10-15 at 16:26 UTC.
No pagination optimization is included. See `RESULT.md` before drawing any
continuity, cell-proof or soak conclusion.
