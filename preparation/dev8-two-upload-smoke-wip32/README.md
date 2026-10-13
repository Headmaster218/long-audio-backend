# Two-upload qualification harness: source-recovery checkpoint

This is an unqualified, unexecuted snapshot of substituted source-recovery templates. It is a source backup, not a runnable distribution, an execution approval, or evidence that the pending two-upload harness passed.

## Status and scope

- The already-qualified dev8 product is separately published at [commit 1fc066910ca78b2e57d7a28f79b91cafc06f5dd5](https://github.com/Headmaster218/long-audio-backend/commit/1fc066910ca78b2e57d7a28f79b91cafc06f5dd5). That product status does not qualify this pending actual harness.
- The reviewed public scope is two 10.5-second uploads, one natural composite cell, one optional diagnostic job, replay, and grouped session deletion.
- 81 model-free fixture cases and 12 isolated-entry cases are planned but have not yet been run for this snapshot. Their counts were checked by source AST inspection only.
- Actual targets remain absent at checkpoint time. Genuine two-upload inference is unqualified, and the actual run remains separately gated.
- Passive-observer corrections were identified after this snapshot and remain pending. This checkpoint preserves the frozen source through reversible substitutions; it does not include those later corrections.
- No tests, application imports, native/provider imports, services, model inference, audio materialization, or database operations were performed to create this checkpoint. Existing fixture examples are explicitly synthetic.

## What is included

Exactly these eleven files are in the public payload:

- README.md
- controller/ownership.py
- controller/instrumentation.py
- controller/common.py
- controller/bootstrap.py
- controller/server.py
- controller/capture.py
- controller/launch.py
- controller/test_smoke.py
- entry/isolated_entry.py
- entry/test_isolated_entry.py

Private workspace/component paths, run and evidence identifiers, nonpublic integrity pins, and private recording/provenance constants have deterministic __PRIVATE_...__ placeholders. Numeric placeholders are deliberately unresolved Python names. No replacement is a usable pin, authority, path, or approval. Generic public interfaces, runtime/module names, and the public product commit remain readable.

The substituted Python files are AST-parseable templates only. They cannot establish runtime correctness, readiness, ownership cleanup, acoustic quality, or authorization. Do not execute them or replace the placeholders with guesses. Missing models, inputs, runtime assets, authority/readiness documents, and receipts are not supplied here. Reconstructing the original source from this template requires a separately retained private literal mapping and original source pins; neither is published.

No raw audio, transcript, private receipt, API key, model payload, database, or metadata inventory dump is included. Source-only byte-for-byte recovery and unchanged-original checks were performed in memory; these checks are not fixture or actual-run qualification. The private checkpoint manifest and seal are excluded from this public allowlist.

This snapshot stays fixed if live sources are corrected later. A later correction requires a separately identified checkpoint and separate review.
