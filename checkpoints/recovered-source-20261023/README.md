# Recovered development source checkpoint

This is a source-only development checkpoint, not a release, merge, deployment,
full-test pass, model-quality claim, or long-recording acceptance result.

## Reconstruct the public base

Use Headmaster218/long-audio-backend commit
`9c91788cf6c5a785b91b49e1c21d3ab187077343`. Its root product is older than the
candidate described by its checkpoint reports. Work in a fresh copy.

Overlay the complete `source/` payloads from these published checkpoint
folders onto matching root-relative paths, in this order, verifying their
published before/after identities and bundle manifests:

1. `reports/audio-endurance-root-reattestation-dev66-checkpoint/` (9 files).
2. `reports/audio-primary-history-recovery-dev67-checkpoint/` (8 files).
3. `reports/audio-native-crop-dev69-checkpoint/` (8 files).
4. `reports/audio-runtime-census-dev70-checkpoint/` (7 files).
5. `reports/audio-census-integration-dev71-checkpoint/` (7 files).

Follow the checkpoint READMEs and REPRODUCE instructions for hash verification.
Do not apply `terminal-release-fixture-only.patch`, which was a separate
comparison-fixture adaptation. Historical patch application is not claimed:
the verified base reconstruction used authoritative source payload overlays.

## Apply this delta

Check the ten source-file preconditions in SOURCE_DELTA.json. An absent before
hash means the file must not exist. Then apply
`dev71-to-recovered-diagnostics.patch` from the reconstructed candidate root,
not directly from the older published root. Verify every after hash.

The patch modifies five package files and adds one package file plus four tests.
It restores scoped authority read reuse, bounded exact float32 digest conversion,
authenticated initial bounded planning refusal handling, and bounded primary
commit failure/stage diagnostics. Original budgets, SQL, primary commit failure
semantics and independent validation boundaries are not relaxed.

All unchanged package assets, profiles, fixtures, dependency declarations and
licenses remain inherited from the public base. The distribution version is
unchanged. This payload does not include measured runtime/model inventories,
models, audio, transcripts, databases, execution logs or credentials.

## Verification scope

The new patch was application-checked in a fresh reconstructed base; complete
package and test directory bytes matched the source candidate afterward.
Selected new controls passed in the reconstruction environment: 46 three-fix
controls and 36 commit-diagnostic controls. Separate generated-PCM primary-only
support tests also passed. Those test helpers and execution environment are
not included in this ten-path patch. These are limited, new verification results,
not a full-suite or real long-audio acceptance claim. Earlier failed runs remain
failures; the interrupted A11 run has no recovered final outcome.

Runtime setup, actual-model validation, complete long-recording trials and
persistent cross-day identity acceptance remain separate unfinished work.
