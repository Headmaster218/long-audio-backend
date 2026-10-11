# Native-capture preparation WIP2: corrected acquisition source

Source-only checkpoint on proposed branch preparation/rc3-dev3-native-capture-wip2, based on WIP1 commit b1c11dc940d99e8fa5e427ed75d42b2650f1104e. The qualified runtime remains 2c841d84a9b3204e510628f4aef17fe90334e76c; runtime source, defaults and version are unchanged.

## Preserved correction and validation

- Acquisition v6: the network-operation timeout is capped at 30 seconds and remaining guard work time. Before urllib follows an admitted redirect, the exact supported HTTPResponse is closed and verified closed, preventing the inherited byte-unbounded body drain.
- The matching 49 acquisition synthetic cases passed.
- The continuation helper pins this corrected acquisition identity. Its matching 15 synthetic cases passed.
- These are byte-exact source copies. Packaging performed only metadata, hash, JSON, static syntax and privacy checks; it ran no tests, network requests, downloads, providers or models.

The 64 passing synthetic cases do not qualify successful live acquisition or a real native-capture run. At checkpoint preparation, live model acquisition is ongoing and remains unqualified; this backup is not a completion receipt. Socket timeouts alone are not a hard whole-operation timing proof: immutable outer guards and admission controls remain required.

Corpus-first has 18 prepared, unrun cases outside this payload. Its source family was omitted because byte-exact entry/launcher copies contain executor-specific path bindings. Installation and actual-guard work in progress, and the optional unexecuted SDK compatibility draft, are also omitted.

## Reference-source prerequisites

SOURCE_INVENTORY.json gives the exact seven source paths, byte sizes and hashes. It also inventories this README; its own hash is sealed by the publication review.

This is reference material, not a standalone runnable package. Existing workspace-relative layout assumptions, immutable design/manifest pins, terminal-state pins and guarded launch dependencies remain in source. Missing approved specs, receipts, full design inputs and runtime/asset inventories must be reconstructed and reviewed before any execution. Do not bypass checks or treat this snapshot as execution authority.

The unchanged guard and other earlier preparation references remain in [WIP1](../native-capture-wip1/README.md). Public asset/distribution/input metadata remains in [WIP1 metadata](../native-capture-wip1/metadata/); it is intentionally not copied again and does not substitute for exact execution inputs. WIP1 acquisition v4 stays superseded; use this checkpoint only as preservation of the reviewed correction.

No specs, approval or execution receipts, private paths, captured environment/proxy values, transcripts, audio, models, archives or wheels are included. Source has only conspicuous synthetic rejection/redaction sentinels and generic program defaults. Publication requires independent allowlist/privacy review; preparing this inventory did not publish a branch, commit or PR.
