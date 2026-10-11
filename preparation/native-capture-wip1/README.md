# Native-capture preparation: source-only WIP

This is a review and recovery checkpoint for real native-capture preparation beside rc3.dev3. It is not a runtime release, a complete runnable harness, or a claim that real native capture works.

Base: Headmaster218/long-audio-backend at 2c841d84a9b3204e510628f4aef17fe90334e76c. Proposed branch: preparation/rc3-dev3-native-capture-wip1. Proposed repository location: preparation/native-capture-wip1/. No version, default setting, production source, tag, merge, or deployment is changed by this package.

## Contents

- reference-source/preparation/acquisition: superseded acquisition v4 reference source, synthetic fixtures and fixture identifiers. Corrected timeout/bounded-redirect helper preparation is outside this snapshot.
- reference-source/preparation/guard: current sampled-resource guard, network launcher, pin-spec helper, finite fixtures, the older snapshot needed by guard-delta parity checks, and the pre-network guard needed by network-environment parity checks.
- reference-source/preparation/controller: current frozen real-capture controller preparation and 29 listed model-free fixture identifiers.
- reference-source/preparation/storage-proof: draft finite-producer admission with 19 passing pure fixtures, plus an unexecuted physical Store geometry proof.
- reference-source/preparation/controller-readiness: source-only runner and 48-ID list defining the fake-controller/pure-admission fixture union.
- metadata: strict public-field projections of asset, distribution and fixed-input metadata.
- SOURCE_MAP.json, METADATA_PROJECTIONS.json, ALLOWLIST.json and FILE_INVENTORY.json: exact permitted contents, source/output hashes, byte sizes and transformations.
- STATIC_REVIEW.json and PRIVACY_REVIEW.json: limited packaging-time checks, not runtime/test receipts.

All copied Python and fixture-identifier files are byte-for-byte source snapshots. No source code was rewritten for portability. Source roots in SOURCE_MAP are logical roles, so machine-absolute paths are not published.

## Current validation scope

This checkpoint was prepared using bounded reads and text copies, JSON parsing, static Python AST parsing, SHA-256 calculation and privacy-oriented text/URL inspection. No packaged script was executed or imported; no test suite, installer, provider, audio path, native ABI, model, acquisition, server or Store proof ran as part of packaging. No prior local run receipts are included. Prior validation of the base or other predecessor files is not inherited as qualification of this combined snapshot.

Controller provenance is the exact a04c65220445aacab00c0dd37833a32c548f75b04913f67cc564b085afab2a32 preparation freeze. Its 29 fake-controller cases and 19 pure admission cases passed as an exact guarded 48-case union on source hashes matching this snapshot, before this packaging report was finalized. Packaging did not rerun those tests. The source-only runner and identifier list are included; the machine-specific freeze, launch spec and result receipts are excluded. This does not establish installed SDK compatibility, the physical SQLite envelope, portable-layout execution or real native capture.

The storage admission remains a draft validated only by pure fixtures; the physical Store proof is unexecuted. A finite admitted shape does not prove a universal SQLite disk limit, storage safety, production validation success, or crash/power-loss behavior.

## Superseded acquisition v4 caveat

The included acquisition v4 is superseded and must not be used as actual acquisition-readiness authority. Its inherited urllib redirect-body drain had no byte bound, despite a socket timeout, and its initial 5-second network timeout proved too short. Corrected timeout and bounded-redirect helper preparation is outside this source snapshot. These limitations apply even though source copies are byte-exact and some earlier fixtures passed.

## Reconstruction prerequisites and portability limits

The relative directory layout groups related source; it does not make this package directly runnable:

1. Source retains its original workspace-relative root labels and safety checks. Its sibling runtime/design/preparation roots, charged history roots and recovery-note requirement are not supplied. Do not bypass these guards to make the snapshot run. A future portable implementation needs separately reviewed layout changes.
2. Acquisition requires the original exact approved design/addendum/README pins and full acquisition manifest. Those original inputs are omitted. The public JSON projections have different schemas/hashes and must never be substituted for those pinned inputs.
3. Guard operation needs Linux process identity facilities, a separately reviewed immutable launch spec, exact source/runtime inventories, deadlines, STOP handling and resource admission. No ready-to-run spec or authority is included.
4. Network tests need the included history/network-v1 snapshot; guard-delta tests need the included qualified_guard.snapshot.py and guard-test-ids.json. Those filenames describe lineage, not current checkpoint qualification.
5. Controller operation additionally needs the exact runtime, provider assets, fixed input, installed-file pins, readiness proofs, positive owned-process proof, posthash budget and explicit actual-run authority.
6. prove_store.py imports preparation/installation/common.py. Installation files are intentionally omitted while their source is changing. A future reconstruction must supply stable, reviewed installation source and the exact verified installed runtime first.
7. Fixtures are source only. Running even model-free fixtures is a separate step requiring the accepted bounded guard; this document intentionally provides no launch command.

Public metadata preserves useful package/asset URLs, declared hashes, sizes, licenses and the historical fixed 19.19-second input selection. It excludes host marker values, local package observations, raw transcript text, speaker-reference identities, cached model outputs and historical execution records. It does not prove downloaded bytes, target compatibility, license acceptance, accuracy or a future positive native outcome.

## Privacy and publication boundary

Only ALLOWLIST.json entries are candidates for publication. Exclude every unlisted sibling, runtime file, run directory, model/audio/archive/wheel, host inventory, original spec, original freeze, raw receipt and internal note. Source contains environment variable names and conspicuously synthetic redaction-test sentinels, but no captured environment values or real credentials. Generic Linux paths and localhost interfaces are program literals, not a host inventory.

FILE_INVENTORY.json seals all other files. Its own hash/size are supplied separately to the reviewer because a manifest cannot contain its own SHA-256. Publication requires independent allowlist/privacy review and a sole authorized publisher. This preparation step did not create a branch, commit, PR or remote update.
