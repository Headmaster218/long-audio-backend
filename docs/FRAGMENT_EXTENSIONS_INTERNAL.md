# Offline fragment extensions, 0.2.0rc2.dev4

Default OFF and experimental. This public source projection adds offline model
bridge contracts, a standalone PCM normalizer and benchmark-only event/native
boundary tools. It adds no HTTP routes, date/upload glue, automatic enrollment
or end-to-end product acceptance. Tests use synthetic PCM and fake providers.
The source files come from immutable staging commit
`732f37698b58f00750d76ba99e4cf7599c1b97f7`; original private experiment ancestry,
audio, vectors, model assets and raw reports remain excluded.

## Precisely what the source adds

- [Offline model bridges](FRAGMENT_MODEL_BRIDGE_INTERNAL.md) wrap existing
  analyzers/native processing through explicit bounded experiment admission,
  attempt accounting, private evidence and source/runtime fingerprint checks.
  The native process gains a no-op-by-default response observer and opt-in
  import attestation/response bounds. These are experimental harness hooks,
  not a new production API. The default seam matcher remains v1; experiments
  explicitly select p1 with trusted continuity/replay declarations.
- [Finalized PCM normalization](FRAGMENT_NORMALIZATION_EXPERIMENT.md) remains
  a standalone import-only class. It supports a narrow finalized single-island
  PCM16 scope with a pinned FFmpeg build. It is not connected to the logical
  runtime, model bridge, HTTP APIs or compressed inputs.
- [Positive-event seam protocol](POSITIVE_EVENT_SEAMS.md) and native boundary
  probes are benchmark tools. Merely importing source or passing fake tests
  does not authorize inference, retries or broader experiments.

## Historical evidence, with its limitations

The immutable component documents preserve their original protocol-stage
wording, including statements that inference had not yet been admitted. This
section distinguishes the later bounded findings; it does not rewrite those
frozen source documents or turn them into runtime permission.

- The earlier eight-arm real base comparison and separate two-support MOSS
  extension produced empty base event arrays. Their event result establishes
  negative-event invariance only, not positive-event coverage.
- A separate selected synthetic-development four-arm event experiment
  reported equality and nonempty 30-second seam coverage using 64 Zipformer
  calls. Selection used already-exposed model-positive development material.
  This is not a blind test, acoustic ground truth, general event recall,
  complete speech/diarization quality or production acceptance.
- Native P1 passed its bounded check. The original P2 whole arm finished
  processing but failed its report-size cap; that original failure remains
  preserved. A separately admitted split-only remainder later matched the
  retained whole PCM and anonymous interval evidence. This was not an
  uninterrupted P2 success. The 300-second receipt was not EOS, ten commit
  IDs survived the epoch-boundary restart, and the final one-sample core
  contained no invented speaker activity. No global identity, ASR quality
  or arbitrary crash-recovery claim follows.

These are historical, separately scoped findings. This public export's own
checks are reported only in [TEST_STATUS.md](../TEST_STATUS.md). Private study
results and the earlier staging suite are not substituted for public-tree tests.

## Public source controls

[FRAGMENT_STAGING_IMPORTS.json](FRAGMENT_STAGING_IMPORTS.json) retains the exact
component source commits, paths, byte lengths and hashes. All 30 reviewed
component files retain their bytes. The older bridge allowlist is frozen
provenance for its original source tree; it is not this repository's policy.
[PUBLIC_POLICY.json](../PUBLIC_POLICY.json) is the sole public path allowlist.

The duplicate staging scanner and private-tree staging policy are omitted.
The eight adapted source/privacy regression cases use the existing unchanged public
gate and portable exported files rather than a live staging Git index.
One separate model-free case tests private-output admission and exclusion through
the real public gate. Seven exact positive-event tests require private development
PCM/cache bindings and explicit opt-in; one exact bridge test needs the excluded
private packager. These eight named cases are explicit skips when unavailable;
the original component test bytes remain unchanged.
Public-specific dependency-pin and temporary-storage test adaptations remain
intact. Two strict rejected-q1 historical expected failures and explicit real-PCM
opt-ins stay separate from passing tests. The total public source budget is
250 files / 2,500,000 bytes; the 250,000-byte per-file cap, private exclusions,
secret checks, structured-vector checks, hooks and exact-path review remain.
