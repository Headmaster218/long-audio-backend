# Recording receipt-outbox checkpoint

This source snapshot preserves the next bounded finalized-recording milestone:
two durable native source receipts are delivered from a primary-database outbox
before their canonical primary chunks become claimable. Original recordings,
exact overlap deduplication and source-to-work provenance remain preserved.
The intended full API/MOSS integration failed in the historical run below.

## Verified historical results

- 35 focused model-free cases and 12 original regressions passed in separate
  runs: 9.82 and 4.06 seconds, respectively. Both reported no remaining children
  or threads. The helper's original in-memory self-test also passed. These facts
  are not claims that the public templates have run, or that models were tested
  by pytest. Per-process resource maxima are not aggregate peak measurements.
- The actual API/helper run completed in 15.82 seconds and exited 1. Three
  unique original sources totaled 27 seconds and formed the exact 21-second
  PCM union, processed as two ordinary 10.5-second primary chunks. It returned
  16 primary segments. Native metadata verified one 21-second epoch, no replay
  and disjoint primary ownership. Exact source/finish retries kept identities.
- Both derived-source ingestion receipts were durable in one attempt each;
  optional_receipts reported durable=2, pending=0, gaps=0, ready=true. The owner
  reported dropped=0. All seven source/work mappings and all three original
  plus two derived files were verified after positive shutdown; the derived
  concatenation matched the canonical input PCM.
- Optional capture logged OperationalError with sqlite_code=261 and
  sqlite_name=SQLITE_BUSY_RECOVERY. The failing query was not recorded. One
  cell was retained, no eligible seam was found, and MOSS dispatch and accepted
  job counts were both zero. Intended full integration therefore failed.
- Cleanup positively waited the server and found no known live descendants.
  Peak sampled process-tree RSS was 684,707,840 bytes. Sampling cannot prove an
  instantaneous hard memory maximum.

These are separate outcomes: primary/deduplication, original retention, durable
receipt delivery, optional capture failure, no MOSS dispatch and verified cleanup.
Zero owner drops and ready receipts do not establish successful optional capture.
No capture-retry fix, complete MOSS result, accuracy, completeness or speaker
identity acceptance is included in this snapshot.

## Implementation scope

The primary database stores a bounded receipt outbox together with nonclaimable
staging chunks and exact maps. Descriptor observations are frozen under current
primary authority. Receipt delivery and exact owner-row reconciliation occur
after commit, serially through the existing owner. Busy/deadline uncertainty is
kept pending; terminal capacity gaps stay explicit. Work becomes claimable only
after the batch's staged receipt decisions are resolved. Restart revalidates
prior acknowledgements rather than blindly accepting stale authority.

The seven changed files are app.py, recording_overlap.py, worker.py,
native_owned_cell_capture.py, native_owned_cell_store.py, the focused test and
the recording API document. Native changes are observation freezing and exact
owner-thread reconciliation. Optional schema, pragmas, operation budgets and
storage budgets remain unchanged. There is no later SQLite capture-retry repair
in this frozen source. The native receipt outbox and optional capture are distinct
stages; successful delivery cannot establish that a later capture succeeded.

The explicit known-coordinate, exact-PCM, finalized-batch limits still apply.
Continuous capture, source-specific undo/revocation/erasure, unknown-coordinate
acoustic alignment, arbitrary simultaneous-speech recovery, quality, endurance
and other deployment platforms remain unqualified. Preserve original files
independently before requesting destructive whole-session deletion.

## Reconstruct from the preceding public source

Repository: Headmaster218/long-audio-backend.
Checkpoint parent: 3038c536872d46e9466c045182e05c413a6db9c4.
Public destination: preparation/rc3-recording-outbox-wip58.

Start with a separate source tree assembled exactly as described in
preparation/rc3-recording-overlap-wip57/README.md at the parent commit. That recipe
binds product 1fc066910ca78b2e57d7a28f79b91cafc06f5dd5, its reconstruction locks,
three earlier native overlays and the six v1 feature paths. Overlay this packet's
seven feature paths at the same product-relative paths. This replaces the native
capture/store files with v2; storage.py, lifecycle.py and native_owned_cell_jobs.py
remain the preceding candidate's exact versions. The other product files are
referenced rather than copied again. Preserve the earlier source and evidence.

Frozen v2 candidate manifest SHA-256:
b3fb4d62595f0208372d6efab0b41e769a98bcc878b70b26064599ca342986dd.
Six included Python source/test files are byte-exact; the included API document
has only project-facing status/test-result prose updated. The assembled package
must contain 102 regular .py/.json files and match helper inventory SHA-256
0aea123b5c046adba8ee766914cf12b3c347deddb4e9c8af3e07a4fa18ba03fe.
Use the exact helper traversal/canonical-JSON algorithm described in the manifest.

The existing ordinary localhost guide remains at
preparation/rc3-recording-overlap-wip57/docs/TESTED_API_GUIDE_ZH.md. Its historical
AMI MOSS success belongs to an earlier adjacent-upload case, not this v2 case.
For new recording receipt states and guarantees use this packet's
[recording API contract](docs/RECORDING_OVERLAP_BATCHES.md) and
[helper guide](helper/README.md). The helper verifies the installed baseline
separately and injects the assembled candidate root through its explicit entry;
this snapshot does not overwrite an installed runtime or contain runtime assets.

## Packet and preparation checks

The packet contains the seven feature paths, helper/smoke.py.in,
helper/server_entry.py.in, helper/README.md, CONFIGURATION_FIELDS.json,
PUBLIC_MANIFEST.json and this README. Resolve local bindings before execution.
All package, fixture, interpreter and source-lock pins remain fixed; entry and
root-layout pins must be calculated after local relocation. Do not weaken pins
to accept a different package.

Preparation performed source hashing, static Python parsing, reversible template
checks and privacy/inventory checks only. No product/helper imports, tests,
models, server execution, database opening, network access or publication took
place. No audio, transcript, raw receipts, database, runtime, operational identity,
local configuration, execution log or private inverse mapping is distributed.
The public packet is below 400 KiB; earlier public source/evidence stays unchanged.
