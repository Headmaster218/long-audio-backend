# Finalized bounded PCM16 normalization experiment

Default OFF; import-only. No production, API, worker, provider, model or runtime
integration. This module consumes an already-decided `SourcePlan` and does not
match seams. Explicit `enabled=True`, `plan.final=True`, and an existing private
`temp_root` are mandatory. Missing next fragments are not EOS. This first scope
supports one continuous island, same-rate 44,100 or 48,000 Hz PCM16 WAV, one or
two channels, at least 123 logical native frames. Mixed formats, gaps, unresolved
islands, live tails, codecs and shorter standalone islands fail closed.

## API and immutable identity

`FinalizedPCM16Normalizer(plan, paths, temp_root=..., enabled=True)` streams
original file identity checks through `BoundedPCM16Reader`; no full-file hash
allocation or full-record concatenation is introduced. Reopening or restoring
`SourcePlan.from_json(...)` rehashes every original source. Every alias is compared
to retained native owner bytes in bounded chunks before any channel mixing.
Thus fabricated matching metadata cannot hide stereo cancellation differences.
All source signatures, including aliases and untouched sources, are checked
before/after reads and subprocess work. Controlled immutable storage is required;
stat checks do not authenticate adversarial mutable storage. Paths are bindings,
not identity. No source path enters plan or returned source identity.

`read(start, end)` accepts integer canonical frame intervals in `[0,canonical_end]`,
at most 8,000 frames, and returns `NormalizedWindow`. PCM length is exactly
`2*(end-start)` or the call fails; no padding, silence stripping or crossfade.
Empty reads return empty PCM and empty context/provenance at sentinel `[0,0)`.
Nonempty results include native context interval, native owners with original
source SHA256, intersecting original aliases with source SHA256, plan SHA256,
normalizer fingerprint, exact native end and canonical end. Owner/alias records
are native filter-support provenance, not invented one-to-one output-sample
ownership. Full intersecting alias records remain untrimmed. Random order,
repeated calls and serialized restart require no previous filter state.

## Pinned arithmetic and endpoint

Reduced `p/q = 16000/native_rate`. For canonical `[a,b)`, read native
`[floor(max(0,floor(a*q/p)-512)/q)*q, min(N,ceil(b*q/p)+512))`.
Resample retained owners with the exact filter string in the module and crop by
`anchor*p/q`. Stereo is explicitly `0.5*left + 0.5*right` only after native
ownership and alias checks. Physical file ends never reset logical filter phase.
Local flush occurs behind discarded right context, except true finalized EOS.

Exact native N is retained separately. Canonical M uses bounded-tail v2:
`A=floor(max(0,N-512)/q)*q`; M is `A*p/q` plus actual emitted tail frames for
native `[A,N)`. Check `abs(M*native_rate-N*16000)<=native_rate` using integers.
Do not replace M with per-fragment rounding or universal ceil. A final one-native-
frame physical receipt remains owned and uses preceding logical filter context.

Executable `/usr/bin/ffmpeg` must match SHA256
`e8a8d46f5225f3062cec7c07fb145d58ae73c603cb740dcd5bad34bfb54e455a`.
Full version-output SHA256 and filter/endpoint contract are separately pinned.
Executable metadata is rechecked each call. This shared-library FFmpeg build
requires immutable trusted installed libraries too: version text is not a
cryptographic attestation of every dynamic library. Different builds need a new
reviewed identity and evidence; callers cannot disable fingerprint checking.

## Bounds and limitations

- At most 8,000 canonical frames requested; conservative native replay hard cap
  25,465 frames, at most 101,860 interleaved input bytes.
- Alias validation uses the same native cap; total startup work scales with
  source/alias size but memory does not. Core limits remain 4,096 sources and
  8 MiB serialized plan metadata. No whole-record PCM cache.
- Per invocation output read bounded to 20,001 bytes and rejects above 20,000;
  generated files live only in unique directories under explicit temp_root.
- One-thread FFmpeg, 15-second wall timeout, 10-second child CPU hard limit,
  128 KiB child per-file size limit; temp usage sampled against 512 KiB.
- Process-tree RSS sampled at 10 ms against 256 MiB; host available RAM must stay
  at least 2 GiB and free disk at least 5.5 GiB. These sampled safeguards are not
  OS-hard RSS caps. Child is killed/reaped and owned temp directory removed on
  failure. No cross-instance/shared-root quota is claimed.
- Constructor hashing uses core 64 KiB streaming blocks. FFmpeg version output
  is captured only after executable hash acceptance. Trusted Linux/POSIX only;
  no portable sandbox or thread-safe subprocess pre-exec guarantee is claimed.
- Endpoint and waveform invariance have finite experimental evidence, not a
  universal theorem or speech/ASR quality claim. Caller continuity and true EOS
  declarations remain trusted; a false declaration can change observations.

## Evidence lineage

Source-only baseline is develop `6835ec41` (exact full hash in repository evidence).
No private git ancestry, model, audio corpus or raw private results were copied.
Study recipe SHA256: `5b4fe33343b155f33bd6de7ece9152177a1463acd155b70c8bf812bfa1b3526d`.
Bounded-tail v2 SHA256: `e549c19d2ee3e8b18bdedcad7854955cfa1797b0fc5511b04f40d7f2c7e61753`.
Independent accepted experimental study report SHA256:
`51f65b88987d0003c86422ded856c3f1e6fa718d15206948bc33d7b11790e9e4`.
The prior ceil-endpoint failures remain retained in the original study.

Synthetic tests compare whole pinned normalization against logical fragmentation,
nonaligned cuts, matcher-verified native overlap, final one-frame receipts,
seam/last-frame impulses, random/repeated/restored reads, minimum island length,
stereo cancellation attacks, missing/mutated/malformed sources and unsupported
formats/islands. Tiny adjacent seams are explicitly pre-decided continuity test
fixtures; no claim is made that the seam matcher infers a one-frame seam.

Source packaging limits/exclusions are unchanged. Adding this module to a public
release requires a separately reviewed source-guard size/allowlist delta.
