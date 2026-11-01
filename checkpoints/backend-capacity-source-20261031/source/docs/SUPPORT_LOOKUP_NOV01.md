# New Nov1 exact support lookup repair

This is newly implemented and measured work, not restoration of lost Oct31
commit 46d674ca or its real-data/cold-API qualification. Baseline source was
restored from public commit f5105d48bb588a8f25c1e0b2e5ac646edc4dee9e, whose
checkpoint pins source e9e9bb62b99b0b8d54d498424994fb29a57a606d. All 711 baseline
source files passed the public size/SHA256/Git-blob manifest before editing.

The only product change is `INDEXED BY support_session` on the existing exact
support-row SELECT. The existing index is `(session_id,id)`; no DDL, budget,
deadline, projections, authority predicates, payload resolution, retries or
failure-handling changes are introduced. An absent required index refuses;
there is no fallback to an unbounded group scan.

## Fresh synthetic evidence

Python 3.12.14 / SQLite 3.53.1; in-memory synthetic database, same-session/group
histories of 674, 8461 and 32916 rows, existing session/group/generation indexes.
The unhinted planner selects `support_group(identity_group=?)`, consuming
6803/84673/329223 VM instructions for metadata lookup (four more with PCM).
The hinted query selects `support_session(session_id=? AND id=?)`, consuming
67 instructions at all three sizes (71 with PCM). Returned rows are identical.
Raw synthetic plan/counter evidence is in `evidence/SUPPORT_LOOKUP_NOV01_SYNTHETIC.json`.
These counts are observations on this fixture/runtime, not universal bounds.

Before the change: six new bounded-work tests failed, nine authority/resolver
checks passed. Afterwards: all 15 new tests passed. Combined with existing
speaker-support lineage and lifetime-quota checks: 46 passed. Six existing cold
integrity/revocation component cases also passed with temporary data on the
workspace filesystem; 12 unrelated cases were not selected.

Initial wider test collection was blocked by missing optional `soundfile`.
Initial cold component checks on `/tmp` failed the existing disk-reserve check
(4.9 GiB tmpfs), identically on the unmodified public baseline. Repeating them
on the workspace's 58 GiB-free filesystem passed without altering checks or
budgets. Failed logs are retained outside the source tree; no failed result is
silently counted as passed. No packages/models were downloaded.

No inference, long-duration capacity run, real recording, quality evaluation or
lost-data recovery occurred. This new local Git checkpoint has not been uploaded.
