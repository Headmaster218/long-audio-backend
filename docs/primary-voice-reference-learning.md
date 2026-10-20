# Reviewed primary voice references (experimental)

This checkpoint adds explicit enrollment of an already manually associated,
original primary crop. A manual label alone never enrolls. No model is trained
or replaced, no thresholds are changed, and no transcript speaker is relabeled.
Synthetic model-free tests are software checks, not evidence of real speaker
recognition accuracy or long-duration acceptance.

## Explicit review and enrollment

With the existing reviewed-primary-persons feature enabled and an activated
verified identity bank:

1. Read the exact observation and create its human person association as before.
2. GET `/sessions/{sid}/recordings/{rid}/primary-observations/{observation_id}/review-audio`.
   The bounded JSON reply contains mono 16 kHz PCM16LE as base64, its actual frame
   count and exact binding hash. It uses the receipt-bound retained source, a
   descriptor-safe file open, exact RIFF extent, crop PCM hash and float-conversion
   hash. No filesystem paths are returned. The endpoint supports at most 32,000
   frames; it never pads or truncates a certificate's exact crop to that ceiling.
3. POST `/identity-groups/{group}/primary-voice-references` with the existing
   generation/revision/idempotency fence plus `association_event_id`,
   `binding_sha256`, `certificate_id`, `vector_sha256`, `space_id`, and attestation:
   `version: reviewed_exact_primary_voice_v1`, `reviewed_exact_crop: true`,
   `single_identified_voice: true`, `no_other_overlapping_voice: true`.
   All three flags must be actual booleans. The operator must listen to the whole
   exact crop and identify only the associated person. This remains a human
   assertion, not automatic proof of purity.
4. POST `/identity-groups/{group}/primary-voice-references/{event_id}/retract`
   with a current fence to remove the acoustic reference without removing the
   human association.

Enrollment requires currently reviewable audio, a valid original 2,048-byte /
512-float32 certificate, finite nonzero normalizable contents, the exact immutable
vector hash and embedding-space descriptor, and policy minimum enrollment duration.
The native certificate determines the exact crop geometry. The current native
profile produces a two-second crop; a bank requiring a longer enrollment cannot
silently enroll it.

## Future crop suggestions

Successful original primary certificate transactions enqueue optional, durable
queries only when the source session explicitly belongs to an enabled verified
bank. An idle hook drains one pending query after primary handles/permits and
pending acknowledgement recovery are released. No audio/model invocation is
needed for matching. GET the observation's `/identity-suggestion` endpoint for
pending, unavailable, stale, abstaining or experimental matched state.

Matching combines compatible legacy and typed primary references using the
existing normalization, centroid, score and margin rules. Exact existing negative
acoustic evidence can veto a candidate. One query considers at most 64 active
positive references across the bank, including incompatible-space entries; larger galleries abstain before row materialization. This deliberately conservative total-bank ceiling can reduce coverage. At most 4,096 query records are
retained globally, reclaiming oldest completed records when needed. Capacity
exhaustion never blocks primary transcript/certificate completion.

Any native different-speaker overlap witness anywhere in the query packet causes
abstention, even when the selected crop itself is clean. Incomplete or invalid
proof also abstains. This conservative packet-wide rule reduces coverage. Absence
of a witness does not prove clean speech. Conflicting exact-crop manual claims
also abstain. Suggestions are crop-only and never become new references.

## Lifecycle and compatibility

The existing immutable review ledger and request idempotency are reused. Typed
primary references are not fabricated legacy supports. Global positive capacity,
canonical per-person capacity and future revocation-event reservations include
both reference types. Replacing/retracting an association revokes its reference.
Ordinary PCM retirement leaves an already enrolled, intact original certificate
usable; source revocation invalidates it. Explicit source/association erasure and
bank reset remove the corresponding source-bearing reference history and queries.
Old requests do not recreate a retracted/revoked reference. Generation, bank,
availability and source revisions fence suggestion reads; canonical relation
changes stale earlier results.

The migration modifies one recognized history dependency guard. Older dev61
writers reject the new exact guard rather than operating without shared-capacity
or erasure awareness. Disablement does not downgrade or delete retained schema.
The feature uses the existing bearer/local API boundary; identity groups are not
new ACLs. It does not establish whole-parent, whole-session or cross-device time
identity, and does not change legacy transcript identity decisions.
