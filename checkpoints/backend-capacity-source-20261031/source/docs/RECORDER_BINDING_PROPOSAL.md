# Disabled session/source binding proposal journal

This is a **local proposal**, not an ingest adapter or server reservation. It does
not authenticate a server, check/create a backend session, send HTTP requests,
modify strict primary state, publish pages, execute models or retire source files.
Every proposal has `activation: disabled`, `target_identity_verified: false`,
`server_reservation_proven: false` and `strict_import_ready: false`.

## Explicit target and default read-only operation

```sh
python -m audio_backend.recorder_binding /private/snapshot-a /private/snapshot-b \
  --origin http://127.0.0.1:8765 --backend-id operator-chosen-instance-label \
  --session-id intended-session --capture-id intended-capture
```

The origin must be HTTP(S), with HTTPS outside recognized loopback, and no
credentials, path, query or fragment. Host case, default ports and IPv6 spelling
are canonicalized. The backend label, session and capture identifiers are explicit
operator declarations, **not authenticated target identity**. No credentials are
read, printed or stored. A different DNS alias is not automatically recognized as
the same server. Do not interpret this format validation as a connectivity check.

Only one capture's complete supplied prefix from sequence/frame zero is accepted.
Identical content repeats are collapsed; conflicting ordinals, missing prefix,
unresolved edges, epoch transitions, gaps/overlaps and incompatible observations
refuse the proposal. Only canonical PCM16 mono 16 kHz is currently supported.
Stereo or other rates require a separately designed normalization identity and
are refused rather than silently resampled. Captures may have an unknown future
suffix; this fixed proposal does not claim the whole capture is complete.

`--capture-origin-frame` is an explicit proposed backend coordinate, default zero.
Integer arithmetic maps the accepted canonical PCM frame extents from that origin;
it does not align physical clocks or settle unknown elapsed time. Warnings remain
visible and do not turn declarations into physical-continuity proof.

## Stable source keys and conflicting bodies

For each source, the proposal stores its original source identity, inner metadata
SHA-256, WAV/PCM hashes, ordinal/epoch and proposed frame extent. Its deterministic
`source_key` binds the proposal schema, canonical origin, backend label, session,
capture and source identity. **Content hashes and frame-origin values are excluded
from the key**: changing the body for the same source must not silently mint a
fresh retry key and duplicate work. The frozen journal binds the exact body.

These are future adapter keys, not claims that existing backend endpoints support
them. They are not automatically passed to the current uploader or HTTP APIs.
Different operator-chosen backend labels/aliases can identify the same actual
server incorrectly; resolving that requires authenticated server identity in the
future adapter. Source-key determinism alone is not a server-side uniqueness
constraint or exactly-once guarantee.

## Optional durable local record

Add `--journal /private/parent/new-binding` to explicitly record the proposal.
The parent must be exclusively controlled by the operator, owned by the effective
user and not group/other writable. The same-UID mkdir/open substitution limitation
from the owned-snapshot tool still applies; this is not an adversarial sandbox.

A new journal uses a private exclusive directory, bounded canonical checksummed
`binding.pending.json`, positive flush/fsync/close, non-overwriting hard link to
`binding.json`, and directory/parent fsync and close. It returns
`created_and_fsynced` only after that local sequence completes. The receipt is at
most 64 KiB. Its checksum provides consistency, not authenticity or server proof.

An existing directory is never rewritten. Inputs are reverified and an exact
matching completed journal returns `existing_exact_observation` with
`publication_durability_proven: false`. That is read-only reconciliation, not
retroactive proof that an interrupted creator succeeded. Incomplete, changed,
extra-file, malformed or conflicting journals refuse and remain untouched.
Failure at write/fsync/link/close preserves state; no automatic cleanup or retry
under another key occurs.

The journal is a fixed prefix, not an appendable queue. Adding later sources,
changing target coordinates or changing content requires a new design decision;
this implementation refuses to extend an existing journal. Do not create parallel
journals to bypass a conflict. Multiple directories do not constitute a global
registry, lock or uniqueness service.

The inherited 32-snapshot/512 MiB observed-input bounds and one cooperative
120-second budget apply. Blocking filesystem operations are not forcibly timed
out. A later adapter must revalidate the same content again; today's proposal is
not an enduring lease on mutable source bytes.

## Required before any ingestion activation

1. Authenticate the actual backend instance and bind to a verified session with
   the expected strict policy and capture state, rather than an operator label.
2. Establish server-supported idempotency semantics and exact body digests for
   each operation. Reconcile uncertain outcomes using the same identities; never
   mint a replacement key after an unknown commit.
3. Hold the exact owned source bytes for the operation's full lifetime and preserve
   originals through confirmed completion. Implement durable per-operation states,
   restart ownership, bounded backpressure and explicit cancellation semantics.
4. Keep unresolved epoch/gap/format cases separate until an explicit policy exists.
   Do not fabricate silence, clock alignment or source continuity.
5. Test the real strict API lifecycle with synthetic inputs and interrupted requests
   before enabling any ingest path. The local proposal code does not satisfy this.

## Validation, 2026-11-03

51 new binding tests cover deterministic/reordered inputs, content-preserving
repeat, same-key changed-body conflicts, coordinates/target changes, disabled
activation, unsupported format/boundaries, origin validation, bounds, journal
publication failures, three abrupt child exits, exact read-only reconciliation,
parent/symlink restrictions, close uncertainty and network-free operation.
The selected aggregate is 255 tests including existing recorder/capacity controls.
No full-suite, real backend request, server identity/idempotency behavior, model,
network or physical-power-loss qualification is claimed.

Warnings are collected from every supplied source, including single-snapshot
proposals with no adjacency edges. Matching stereo/48 kHz snapshot fixtures
explicitly assert the format-policy refusal, rather than failing earlier integrity.

An actual Java-generated mono 16 kHz fixture also produced a local journal and
an unchanged exact-repeat observation. The Java rotation scenario uses stereo
48 kHz, so this restricted first binding path correctly refused it; no format
policy was relaxed or audio silently converted to make that example pass.
