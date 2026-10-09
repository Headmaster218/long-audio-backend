# Finalized normalized runtime adapter (internal, default OFF)

This adapter connects the already reviewed `FinalizedPCM16Normalizer` to the
internal logical scheduler and provider bridge classes. It does not connect to
HTTP, uploads, production workers, calendar/date grouping, identity banks, or
codec ingestion. No model execution is required by the synthetic tests.

## Explicit entry points

1. Call `prepare_normalized_plan(source_plan, paths, temp_root=..., enabled=True)`.
   This verifies immutable originals, compares aliases in native all-channel PCM,
   and measures exact canonical EOS with the pinned normalizer.
2. Create `NormalizedRuntimeStore(path, enabled=True)` and call `create(run_id,
   normalized_plan)`. Custom provider fingerprints require a `RuntimePolicy`
   whose `input_fingerprint` is exactly `INPUT_FINGERPRINT`.
3. Use `NormalizedLogicalRunner(..., temp_root=..., enabled=True)`. The ordinary
   store and reader continue to admit original 16 kHz mono PCM16 only.

Admission retains the normalizer's finalized, single continuous island,
same-rate 44.1/48 kHz PCM16 WAV, mono/stereo, and minimum 123 native-frame limits.
Missing files, live tails, mixed formats, gaps, unknown/ambiguous seams, changed
sources, unsupported builds, and unverified exact output counts fail closed.
The supported storage/runtime remains controlled immutable Linux/POSIX storage.
No new portability or adversarial-storage guarantee is implied.

## Two coordinate systems

`NormalizedPlan.source_plan` is the unchanged native SourcePlan: source frames,
owners, aliases, seam decisions, and hashes all stay native. It has no fabricated
canonical source spans. Its separately stored receipt binds native plan SHA256,
rate/channels/end, exact canonical 16 kHz end, normalizer fingerprint, and adapter
fingerprint. The normalized plan hash includes both records. They enter the
existing SQLite plan transaction together with true-EOS intent.

Scheduler cursors, model PCM, words, turns, events, epochs and finish receipts use
canonical 16 kHz coordinates. Model output timing is preserved; native-input
source coordinates are never substituted for model timestamps. The exact final
canonical sample count is measured, not rounded from native duration or padded.
Reopening rehashes originals, rechecks aliases and reproduces canonical EOS before
any provider receives audio. A receipt with a different exact EOS fails then.
A completed run remains a historical receipt; checking its source files again
requires opening a new reader, rather than silently changing past commitments.

The input-transform fingerprint is included in policy/window/control identity
and native instance scope. Normalized native keys use a tagged JSON object
for the run scope, structurally distinct from every admitted legacy string ID.
Legacy policies with no input fingerprint retain
their original hashes and native keys. Input prefix hashes for this finalized
path bind the entire native plan/normalizer plus a canonical prefix end; they do
not claim a one-to-one native prefix. Appending to finalized runs is unsupported.

## Bounded PCM and honest provenance

An existing runtime read of up to 60 seconds is assembled from at most 120
normalizer reads of at most 8,000 canonical samples. Each keeps the existing
25,465 native-frame context limit. There is no whole-recording PCM cache. Runtime
PCM output is at most 1,920,000 bytes; the bytearray/bytes conversion can briefly
hold two bounded copies. Native support metadata is capped at 128 owners and
128 aliases per chunk, 512 total support records per runtime read, and the
existing 1 MiB envelope bound. Overflow rejects the read without truncation.

`PCMWindow.mapping` uses the explicit `native-filter-support-v1` schema in this
mode. Each canonical chunk has native context bounds, retained owners, complete
intersecting alias records, and its immutable normalization receipt. These are
conservative filter-support records, not exact native sample contribution weights
and not one-to-one source ownership of canonical samples. Commit validation
reconstructs the expected support from the accepted native plan. A correctly
hashed but different owner map is rejected.

The bridge classes accept this explicitly tagged map while retaining the exact
canonical PCM16 hash for every actual pushed/analyzed input. Embedding evidence
preserves its actual pooled float32 input receipt and hash. Its canonical sample
selection and native filter support are separate fields, tagged
`canonical_selection_with_native_filter_support`; it deliberately does not
produce the old `exact_complete`/`source_mapping` form. A consumer requiring an
exact native source sample map must reject that status. No embedding enrollment
or profile write is introduced.

## Durability and performance boundaries

The existing transaction fences, output uniqueness, checkpoints, tail ownership,
EOS intent and acknowledgements are reused. Restart can replay model calls but
cannot duplicate a committed core. A fresh reader verifies the same normalized
receipt before replay. Synthetic tests cover whole/fragmented PCM and copied
native overlaps across both rates/channel counts, tiny final physical fragments,
seams inside synthetic words, final partial sample support, repeated/random
reads, create/commit/finish crashes, forged/mutated inputs, and the actual bridge
classes with fake providers and persisted raw evidence. A MeteredEngine fake
embedding compute receives actual canonical PCM16 decoded to float32; its
measured selected-input hash is checked through the persisted bridge evidence
for selections crossing both support-chunk and physical-fragment boundaries.

This first correctness adapter incurs a separate FFmpeg process per <=0.5s
canonical read, plus endpoint verification and repeated reads for native/analyzer
input and bounded restart. That can mean hundreds of launches for one short
recording. Synthetic elapsed time is diagnostic, not real-model or long-duration
throughput acceptance. A future streaming/persistent resampler needs an independent
phase/context/EOS/restart equivalence review; replacing the reviewed resampler
inside this adapter is outside scope.

The earlier frozen source-import and publication allowlist manifests are not
rewritten by this prototype. Promotion requires a separately reviewed source
delta and refreshed execution closure covering the adapter and changed runtime/
bridge modules; the prior real-model closure does not establish this new path.
