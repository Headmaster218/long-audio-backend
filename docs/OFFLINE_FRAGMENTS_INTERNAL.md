# Internal offline fragment APIs (default OFF)

Version `0.2.0rc2.dev3` adds experimental import-only modules. Nothing registers
these modules in the public API, worker, queue, production database, native
adapter or identity pipeline. Existing production processing remains unchanged.
This is development groundwork, not a release or production acceptance.

## Exact-PCM planning

`fragment_core.inspect_pcm16_source`, `BoundedPCM16Reader`, `build_pcm16_plan`,
`SourcePlan`, `core_windows` and `PlanCheckpoint` inspect original sources,
construct bounded logical plans and retain source ownership, redundant aliases,
integer-frame mappings and immutable accepted prefixes. The caller supplies
original-file bindings and independent `fragment_matcher.Continuity` assertions.
Only same-format native PCM16 WAV, mono/stereo, 8–48 kHz is supported. Reads are
bounded to 60 seconds; seam searches use at most 30 seconds per side. Plans are
limited to 4,096 sources and 8 MiB serialized metadata. There is no codec
expansion, resampling, padding inference or full-record concatenation.

The original `exact-pcm16-v1` policy remains the low-level default. The historical
`exact-pcm16-v2q1` policy and its documented failures remain reproducible. The
separate `exact-pcm16-v2q1p1` policy is the only independently accepted quiet-
matching candidate in this bounded offline evaluation; q1 was rejected. p1 must
be selected explicitly through
`build_pcm16_plan(..., policy='exact-pcm16-v2q1p1')` or
`fragment_matcher_p1.match_boundary_p1`. Policy identities are pinned in evidence
and checkpoints; changing policies cannot reinterpret accepted history.

p1 adds a bounded native-sample periodicity veto to the quantized-information
screen. Reviewed mechanics include exact 0.5-second and 2-second quiet duplicate
cases and frozen synthetic controls. This does not establish universal periodicity
detection, unseen-speech performance, ASR quality or a probability of common
source. Unknown capture relationships, replay possibility, ambiguity, insufficient
information and unsupported transformations cannot justify a trim.

Capture identity and exclusion of real replay are trusted input declarations.
Equal PCM cannot prove either. A false replay-exclusion declaration can cause
real new-observation frames to be removed; the retained private diagnostic
explicitly demonstrates that limitation. No false-provenance guarantee is made.

## Injected-provider runtime

`fragment_runtime.LogicalPCMReader`, `LogicalRunner`,
`fragment_runtime_contracts.RuntimePolicy` and
`fragment_runtime_store.RuntimeStore` consume an already-decided plan. They do
not match seams. Admission is narrower: original 16 kHz mono PCM16 WAV and one
continuous island. Providers are injected, side-effect-free test contracts;
there is no accepted real-model or public API integration.

The logical grid is 30 seconds, native epochs are 300 seconds, and default
analyzer context is 2 seconds. A separate SQLite ledger commits output/cursor/EOS
state atomically and supports bounded restart replay. Exactly-once publication
does not imply exactly-once inference. Each native lexical unit retains its full
original support interval and original logical anchor. A separate interior
routing anchor chooses ownership, including support-end anchors routed left;
routing never replaces or clips native evidence. See
[the detailed runtime contracts](FRAGMENT_RUNTIME_INTERNAL.md) for limits,
provider fingerprints, endpoint handling, cancellation and finalization.

## Verification and evidence boundaries

The ordinary suite uses synthetic PCM and fake providers. Real-PCM matrix tests
remain opt-in through explicitly reviewed local source and manifest bindings;
a skipped real matrix is not a successful real-data run. Private raw reports,
audio, vectors and operational state are not public-source deliverables.

These modules were imported as exact reviewed source blobs from separate local
feature histories. Original histories, frozen expectations and failure evidence
remain separately recoverable. Public synchronization requires a source-only
allowlist review; neither private branch ancestry nor recovery bundles belong
in a public repository.
