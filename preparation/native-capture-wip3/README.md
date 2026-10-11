# Native-capture preparation WIP3: installation and installed-symbol checks

Source-only checkpoint prepared for branch preparation/rc3-dev3-native-capture-wip3, based on WIP2 commit 4758f37a91166addbf6ed557866c6048bd6a89bb (tree 415e4c9982826fe1aefffb8804e0dc3d5b6af6ff). The qualified runtime remains 2c841d84a9b3204e510628f4aef17fe90334e76c; runtime source, defaults and version are unchanged.

## Preserved source and recorded status

- Eight byte-exact Python sources preserve shared installation checks, the guarded preparation driver, fixed offline installation/build substeps, closed offline completion, the 14-module import probe, SDK descriptor/wrapper compatibility, seven fake offline-correction cases and four mocked category-boundary cases.
- Prior guarded work passed offline installation/build and file verification, the 14-module installed import check, and installed SDK symbol compatibility with descriptor restoration. These checks requested no model construction or inference. The import probe did load extension code.
- The current common.py includes the later category redistribution: 1 MiB moved from the original retained-build allowance to bookkeeping, leaving 15 MiB build and 7 MiB bookkeeping, with the 512 MiB category cap unchanged. Its four exact/one-over boundary cases passed. The seven fake offline-correction cases passed before that later common.py change.
- Earlier installation, import and SDK passes used the prior common.py identity. They are historical results, not a rerun of this assembled WIP3 snapshot. Packaging only read and hashed source and inspected metadata; it ran no source imports, tests, network requests, installations or models.

Physical SQLite durability, native C ABI behavior, actual returned stream types and inference remain unqualified. This checkpoint does not establish real native-capture readiness or completion.

## Reference-source prerequisites

SOURCE_INVENTORY.json records all eight source paths, byte sizes and hashes, plus this README. Its own hash is sealed in the local publication allowlist.

This is reference material, not a standalone runnable recovery package. Relative layout bindings, exact preparation-run identifiers, content pins and terminal-state dependencies are preserved verbatim. Their referenced run directories, private execution specs, receipts, host inventory and exact design inputs are unavailable dependencies in this payload. Restoring approved inputs and reviewing all pins, guards and execution authority are required before execution; never bypass them to make this snapshot run.

Unchanged controller/guard sources and public input/distribution metadata remain in [WIP1](../native-capture-wip1/README.md). Corrected acquisition and continuation sources remain in [WIP2](../native-capture-wip2/README.md). Those sources are intentionally not duplicated. Moving storage-proof work, the remaining adapter and older installation fixtures with superseded category assumptions are omitted.

No runtime checkout, installed environment, execution spec, approval or execution receipt, machine-absolute private path, captured environment/proxy value, transcript, audio, model, archive or wheel is included. Relative provenance constants and generic program defaults remain part of the byte-exact source. Only the exact reviewed source/documentation allowlist may be published by the authorized publisher after rehashing every file. Preparing this package did not publish a branch, commit or PR.
