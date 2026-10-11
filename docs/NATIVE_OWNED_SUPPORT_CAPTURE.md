# Experimental volatile native-owned support

`--native-owned-support` enables an optional read-only bridge on the ordinary
legacy Worker. It is OFF by default and excluded from inference configuration.
It does not change the primary transcript, primary scheduling, model arguments,
SQL schema, or source-control behavior. There is no lexical candidate dispatch,
automatic winner, word alignment, speaker identity assignment, or enrollment.
The existing optional shared Bearer token applies when configured.

## Current scope

Only a newly claimed, isolated original PCM16 mono 16 kHz RIFF upload with zero
timeline origin and zero processing offset is eligible. The physical source
must contain 1–320,000 frames (at most20 seconds). The actual native coordinator
must produce one entire finalized core whose processed and stable frontiers
reach the exact source EOF. Streamed, normalized, resumed, non-native, longer,
multiple-core, fractional-cut and incomplete-frontier inputs are unavailable to
this bridge. Ordinary primary long-recording processing remains available.

This is an experimental short-input bridge, not completion of long-recording
support. Native overlap detection can miss speech. `no_native_overlap` means no
selected overlap in the observed native result, not acoustic silence.

## What the evidence means

The bridge observes the existing reset, push, finish and turns returns. It
validates the producer's observed provenance and fingerprint, and compares both
the native-push WAV and analyzer WAV against the exact retained original PCM
slice at their existing extraction seams. It performs no additional decode or
model call. The unchanged coordinator's raw floating-point-to-prepared-frame
rounding is recorded; source, support and committed bounds remain strict
integral frames. A genuine EOF deficit is not repaired.

A whole-core support is selected only when at least two distinct native-local
labels have positive half-open temporal overlap. The core is at most20 seconds.
Adjacent touches and duplicate turns from one label do not select a support.
The immutable SourceSupport contract carries content/geometry, PCM and float32
hashes. Source generation excludes session/path aliases; authorization remains
bound to the actual session, chunk, application epoch and primary change feed.
Stored historical producer evidence does not attest a currently installed
runtime. Public projections contain no private paths or raw producer details.

Publication follows a successful primary commit and a fresh bounded read of the
existing primary change feed, source metadata and source bytes. Pause/resume,
retry, source changes, missing triggers, extra relevant control records,
deletions and ambiguous evidence reject publication or invalidate reads.
Optional validation failures do not replace original native results/errors.

## Read API

- GET `/sessions/{sid}/native-owned-supports`
- GET `/sessions/{sid}/native-owned-supports/{cid}`
- GET `/sessions/{sid}/native-owned-supports/{cid}/audio`

List accepts canonical decimal `after` (exclusive, initially0) and `limit`
(1–20), plus `expected_capture_epoch`. Continuation requires the current epoch.
Detail/audio optionally accept exact `expected_capture_epoch` and
`expected_revision`. Raw queries are limited to1024 bytes and four fields;
duplicate, unknown and noncanonical values are rejected. All success/error
responses are private/no-store. Missing/deleted resources return404; stale
source/control/revision and disabled/unsupported capture return409; corrupt or
unavailable required evidence and exhausted response capacity return503.

List pages are bounded by rows and128 KiB serialized bytes; a cursor never
silently skips an eligible result. Detail is at most16 KiB. Audio is a canonical
PCM16 WAV (at most640,044 bytes) sent as a44-byte header and the retained
immutable PCM, without a second full WAV buffer, resampling or temporary files.
`lexical_dispatch_available` is always false.

## Lifetime and bounds

The process-local cache holds at most four supports and64 bounded markers within
4 MiB including a32 KiB marker reserve. Supports expire after900 monotonic
seconds. One producer may stage a support. Restart creates a new empty capture
epoch; there is no backfill, pending-record adoption or disk persistence.
Shutdown closes the epoch before stopping the existing Worker. Deletion purges
optional memory after the existing primary transaction; a late pre-delete
producer cannot republish it. Failed bounded invalidation makes the cache
unavailable and reports optional cleanup pending. Switching OFF does not hide
previously enabled cleanup obligations.

Exactly two global read leases share1.5 MiB for simultaneously live response
buffers. List/audio reserve768 KiB and detail128 KiB before copying or retaining
response-owned material. Leases last through outer ASGI completion, disconnect
or send failure. Already authorized responses may finish after a later delete;
this is not forensic memory erasure. Server/kernel transport buffers are a
separate part of whole-process memory measurement.

Each read has one cooperative500 ms budget across all page targets:1024 global
feed rows,256 KiB projected SQL,150,000 SQLite VM steps,40 MiB of source-byte
work, zero SQL busy timeout and at most5 ms per cache-lock acquisition. Limits
do not reset per result. Files are opened as bounded regular files without
following links or blocking on FIFOs. Slow OS operations can delay detection;
a late result is rejected rather than represented as a hard real-time bound.

## Next extensions

The core evidence and SourceSupport contract can be reused by durable per-cell
storage. Longer/streamed inputs require bounded multi-source mappings, each
actual native safe-cut/core/EOF, and a paged persistent inventory. Restart
persistence additionally requires an independently qualified physical quota,
owner lifetime and crash-cleanup policy. Explicit Qwen and MOSS jobs can later
consume the same support while preserving primary and both alternatives. These
extensions are not enabled here. Focused fake-provider tests establish control
and evidence behavior only; positive real overlap needs separate qualification.
