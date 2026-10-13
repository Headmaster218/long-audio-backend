# Dev8 seam retry: source-recovery checkpoint

WIP34 preserves six frozen corrected harness sources and this README on WIP33 parent [ae9087a7bfb5bbbb470b7e582e86e54b2ca70fcb](https://github.com/Headmaster218/long-audio-backend/commit/ae9087a7bfb5bbbb470b7e582e86e54b2ca70fcb). These public transformed templates are unexecuted source backups. They are not a runnable distribution, execution approval, or actual seam/MOSS qualification.

## Status at this snapshot

- The initial actual run failed before the original commit operation because the harness incorrectly demanded context labels in the clipped core. A separate abrupt-server provenance gap was also recorded.
- Cleanup was confirmed, and a separately performed byte posthash succeeded. These outcomes do not turn the failed run into a pass or establish seam or MOSS qualification.
- The corrected private harness passed 81 model-free cases, and that terminal was independently accepted, as reported by the gate owner.
- The 12-case isolated-entry suite also passed, but its independent terminal audit remains pending at this snapshot. Entry acceptance, data-validation acceptance, and the actual retry remain pending. The actual retry requires separate authorization.
- Earlier fixture, isolated-entry, component-cap, and initial actual-run failures remain distinct historical failures. Historical files are preserved; this checkpoint does not replace or relabel them.
- Intended scope remains two 10.5-second uploads, one natural composite cell, one optional diagnostic job, replay, and grouped session deletion. No actual seam/MOSS qualification is claimed.

## Exact public files

- README.md
- controller/launch.py
- controller/server.py
- controller/capture.py
- controller/test_smoke.py
- entry/isolated_entry.py
- entry/test_isolated_entry.py

Private paths, run/evidence identifiers, nonpublic integrity pins, and private provenance constants use deterministic __PRIVATE_...__ placeholders. Existing literal mappings are reused where the original literal is unchanged. Numeric placeholders are unresolved names. Exact recovery requires the separately retained private mapping and original source pins; neither is public. Do not execute these templates or guess replacements.

Only source AST parsing, inverse byte-recovery checks, and unchanged-source hash checks were performed for this backup. No tests or application/native imports were run during checkpoint preparation. No raw audio, transcript, actual output, private receipt, credential, model payload, database, or metadata inventory dump is included. Public files are read-only copies, and later gate results or corrections do not silently alter this frozen snapshot.
