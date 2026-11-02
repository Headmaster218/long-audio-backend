# Prospective context-zero two-source control

Use ALL exactly-two-active cores from the frozen native predictions, existing
planner with overlap_context_seconds=0 and overlap_max_seconds=12. Keep the
three-active core capacity-exceeded; do not change production's default context,
support-capacity checks, onset/offset thresholds or choose using gold. Four
cores, 5.806 seconds. Keep official_2s separator mode and its scope-permutation
uncertainty behavior. No stream/channel winner selection.

Runtime security update before any loading: official stable torch2.10.0+cpu,
CPU only, minimal dependencies in an isolated environment. Historical2.6 is
unusable due GHSA-63cw-57p8-fm3p. Verify exact official CPU wheel and all narrow
source/checkpoint hashes. Only weights_only=True,mmap=True extraction of pinned
state; bit-exact tensor verification. No unsafe-load fallback, arbitrary pickle
allowlist, remote-code trust, CUDA or full ClearVoice installation. If incompatible,
stop rather than fall back to vulnerable Torch. Preparation may load tensor
state but constructs no model or forward pass.

After independent preflight and source backup, one existing separator-runner
batch, all cores sequentially: two CPU,6GiB AS/4GiB outer sampledRSS, existing
runner's stricter2.5GiB RSS guard unchanged,300s compute+60cleanup/external365s.
No auto retry/guard increase. Freeze separator outputs and verify finite FLOAT
mono16k waveforms, metadata/source scopes and observed cleanup before ASR.
Each returned source scope is decoded exactly once with existing SenseVoice
CPU2, auto language, ITN/as-is. Separate fresh ASR processes,4GiBAS/3GiBRSS,
300s+60cleanup/external365s each; all streams retained. Original mixture and
all previous results stay unchanged. Stop on first error or uncertain cleanup.

No gold is an inference input. Freeze all outputs before content inspection.
Report all source scopes, detected language, unsupported/partial-reference
regions and exact lexical presence conservatively. No concatenated superior
CER, no source-slot/person identity claim, no enrollment or production promotion.
This tests whether this specific existing separator restores omitted mixture
content; failure does not establish that all separation approaches fail.
