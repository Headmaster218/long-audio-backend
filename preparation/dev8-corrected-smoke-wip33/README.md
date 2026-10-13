# Corrected dev8 seam harness: source-recovery checkpoint

This six-file WIP33 overlay preserves five corrected harness sources and this README on WIP32 parent [e63882d64c5e7143447bf75b8f1ac7c7f11ac04f](https://github.com/Headmaster218/long-audio-backend/commit/e63882d64c5e7143447bf75b8f1ac7c7f11ac04f). These substituted public source-recovery templates remain unexecuted and are not a runnable distribution or an approval to execute.

## Status at this snapshot

- The current private-harness 81-case model-free fixture gate and 12-case isolated-entry gate passed and were independently accepted, as reported by the gate owner. No gate was run during preparation of this checkpoint, and none of these public substituted templates has been executed.
- Earlier fixture, isolated-entry, and component-cap attempts failed. Those are distinct historical failures; later accepted gates do not relabel the earlier attempts as passes.
- Corrections include passive-observer fault handling, isolated-entry refusal expectations, and component-cap accounting. The current component reservation is 544 MiB: 16 MiB source/metadata, 64 MiB model-free, 0 MiB startup, 448 MiB actual, and 16 MiB readiness/terminal.
- Genuine two-upload seam inference and the model phase remain unexecuted and unqualified. The actual run requires a separate GO. Reviewed scope remains two 10.5-second uploads, one natural composite cell, one optional diagnostic job, replay, and grouped session deletion.
- The separately qualified dev8 product at [1fc066910ca78b2e57d7a28f79b91cafc06f5dd5](https://github.com/Headmaster218/long-audio-backend/commit/1fc066910ca78b2e57d7a28f79b91cafc06f5dd5) does not establish qualification of this pending actual harness.

## Exact public files

- README.md
- controller/server.py
- controller/launch.py
- controller/test_smoke.py
- entry/isolated_entry.py
- entry/test_isolated_entry.py

Private paths, run/evidence identifiers, nonpublic integrity pins, and private provenance constants use deterministic __PRIVATE_...__ placeholders. WIP32 placeholder identities are reused where the original literal is unchanged; newly introduced private literals have WIP33-specific placeholders. Numeric placeholders are unresolved names. Recovery requires a separately retained private literal map and original source pins. Do not execute these templates or guess replacements.

Only source AST parsing, literal-recovery checks, and byte/hash verification were performed for this backup. No raw audio, transcript, actual result, private receipt, credential, model payload, database, or metadata inventory dump is included. Private acceptance evidence and all historical failure records remain outside the public payload. This frozen checkpoint is not silently refreshed when subsequent gates finish or sources change.
