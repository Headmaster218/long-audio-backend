# Portable runtime recipe: frozen v2 source checkpoint

This is a source-only recovery checkpoint, not a runtime installation or an execution release. It adds the exact frozen ten-file v2 packet to the WIP40 source lineage (parent commit `ffa47c2bfc924107cf71cd84e6031f20a7767839`). The additional README records status without changing the packet.

## Integrity and scope

All ten packet files are byte-for-byte copies; no path substitutions or other source transformations were needed. `PACKET_MANIFEST.json` is 1,448 bytes with SHA-256 `d6c8884741c4a9d281d5102fca0df9227d273a6da68366f5ed636de660ec7804`. Its nine file entries pin the remaining source files. Preserve the relative layout and verify those bytes before any separately approved use.

The packet contains the bootstrap, guarded phase driver, acquisition supervisor, closed configuration schema, runtime installer/auditor, host/import probe, synthetic fixture source, runtime lock, and source map. It includes the root `.dist-info` metadata correction, bounded adjacent-PT_LOAD ELF string-table mapping correction, and bounded audit progress markers. The v2 derivative preserves the Guard algorithm and all 89 fixture source cases.

Private configurations, approvals, process identities, receipts, asset payloads, host inventories, and runtime outputs are not included. Synthetic fixture identities are test data, not live authority. The manifest and source comments retain their original preparation-time flags; they have not been rewritten as execution evidence.

## Status at checkpoint

- The original v1 fixture run passed 89 cases. Static host inventory passed.
- The focused v2 derivative check passed: 8 paths and 68 bounded progress events, with unchanged fixture source bytes and Guard algorithm. This was not a new whole-runtime qualification.
- Two combined wheel/archive audit attempts remain UNKNOWN after their original tool handles were lost. Neither has an accepted completed audit receipt or plan. The later available log records end markers for the wheels and three small archives; the corpus audit is incomplete. Progress markers do not establish successful completion.
- All 38 assets were freshly downloaded and independently accepted: zero reused, 38 fresh, 5,150,756,491 bytes total. Acquisition acceptance does not qualify runtime execution, and no assets are included here.
- Runtime installation, runtime import qualification, extraction, actual provider execution, genuine upload API compatibility, and end-to-end CellStore qualification remain unqualified by this checkpoint.

## Recovery prerequisites

Recover the pinned product source and assets separately. Product source is pinned in the packet to commit `1fc066910ca78b2e57d7a28f79b91cafc06f5dd5`. Generate and independently review fresh private configuration, roots, acquisition evidence, and a one-shot execution release. Use the closed phase contracts in `runtime/runtime.lock.json` and `config.schema.json`; this README grants no execution permission.

Complete and independently accept the required host, whole wheel/archive, and allocation audits before releasing dependent installation or extraction. Each phase needs its original supported-tool successful terminal, Guard cleanup/reap, and accepted outputs. Do not reconstruct success from logs, reuse old process identity, bypass refusals, or widen the frozen limits.

Checkpoint preparation only read/hashed/parsed source as data and copied it. It did not import recipe modules, run fixtures, read assets, execute runtime/provider code, or publish to a remote.
