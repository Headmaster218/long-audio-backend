# Untimed native-support alternatives

This fresh reconstruction starts at the published dev13 source. It does not restore the lost later development history or qualify a runtime. The new module is pure standard-library code and is not imported by the application, providers, storage or scheduler.

A SourceSupport binds a declared source generation, native plan/runtime/core hashes, exact 16 kHz integer frame bounds, and PCM16LE/float32LE hashes. The support is inside one core and source, and is at most 320,000 frames. Its ID is the SHA256 of canonical support JSON. Source frames may reach the exact JSON integer limit (2^53-1); booleans, fractions and floating-point values are rejected. Support bounds describe submitted input, not generated word times.

An Alternative binds one named provider (qwen or moss), model/runtime/config hashes, the same support, original text, outcome/native status/EOS/truncation and bounded raw bytes. Serialization records the exact raw length/hash, without including raw content. Raw bytes are immutable and retained by the caller. This layer does not parse native raw output or prove that a caller's status/text faithfully describes it. A future trusted adapter must independently verify the source, actual submitted material, runtime, raw parsing, session semantics, ownership, cancellation/deadline and cleanup. Hash-shaped declarations alone are not live attestation.

Complete content requires returned status, integer native status 0, EOS true, truncation false, nonempty text and nonempty retained raw bytes. Native18, other failure codes, unknown/failed outcomes, partial or uncertain EOS/truncation, empty text and empty raw remain diagnostics. They cannot be promoted by supplying complete_content=true. Complete content describes only the lexical envelope; it is not proof of accuracy or authority to publish, route, overwrite primary text or update a speaker profile.

Every projection has timing_scope=input-support-only, word_times=null, global_speaker_id=null, speaker_identity_valid=false and profile_update_allowed=false. Extra fields are rejected. Existing strict-timed MOSS tracks remain a separate contract: this module supplies no conversion, inferred timing, speaker identity, normalization, ranking or winner.

Distinct named providers and distinct immutable provider profiles may share one support. A bundle rejects mixed supports and duplicate provider/model/runtime/config keys. Caller order is retained. No session authorization, cross-session reuse, no-replay ledger or persistence policy is implied.

## Bounds

- One support: 4,096 canonical JSON bytes; at most20 seconds
- Text: 65,536 UTF-8 bytes; raw return: 131,072 bytes
- One projected alternative: 196,608 canonical bytes, including escaped control characters
- One bundle: at most8 alternatives and262,144 serialized bytes
- A conservative256-byte bundle framing allowance can reject a near-limit bundle early
- An at-most8-item bundle may reference up to1MiB raw data held by its caller; serialized bundle limits do not authorize that persistence cost

Strict parsing checks types and field bounds before avoidable serialization. Null, unknown, truncated and failed results remain visible. No diagnostic is silently replaced with successful empty text.

## Verification scope

Focused standard-library tests cover material/provenance mismatches, exact geometry, raw hash/size, strict types, invalid or unbounded fields, complete/diagnostic outcomes, coexistence/duplicate rules and serialized budgets. The separate fresh source gate checks the reviewed dev13 delta and preserves the old verifier against its historical tree. This checkpoint has no model, acoustic, API, storage, full-suite, platform or production-readiness claim. Further integration requires a separately reviewed implementation and resource/ownership evidence. Reviewed source must be remotely read back before the next expansion.
