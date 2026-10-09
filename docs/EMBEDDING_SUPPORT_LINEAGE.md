# Forward-only acoustic support review (Phase A)

A transcript correction, speaker rename, or merge is not a review of all audio
used by an embedding. The extractor can concatenate predicted-clean fragments
from a whole analyzer window, including context before/after a displayed line.
Diarization can miss another voice. Byte integrity is not speaker purity.

This additive phase can retain exact support for review when explicitly enabled.
Capture is **off by default**. Start the service with `--speaker-support-review`
or set `Settings(speaker_support_enabled=True)` to opt in. Default mode skips
review-specific source hashing, provenance hashing and PCM artifact copying;
the pre-existing lightweight pooled-support metadata is unchanged.

This phase does **not** enable
identity learning, verified anchors, promotion, human certification, or automatic
template updates. Existing matching, enrollment, examples, rename, merge and
assignment semantics remain legacy behavior. In particular, the existing
`state=confirmed` display state is not an acoustic purity certificate.

## Exact bytes and actual source lineage

`clean_speaker_audio` still produces the same pooled waveform. It records exact
half-open selected sample intervals, the actual selected frame count, and SHA256
of concatenated mono IEEE754 float32 little-endian PCM (no container header).
The interval inventory is capped at 512. Incomplete inventories are explicitly
ineligible; no prefix is offered as the full support.

The worker measures actual output frames from each decoded source part, rather
than inferring part lengths from requested timestamps. For each ordered part,
the private support manifest records:

- original chunk ID and verified source-content SHA256;
- source seek/duration **requests** and actual concatenated decoder-output frame
  range;
- selected sample ranges within that part's actual decoder output;
- embedding version, weight hash, dimension and raw analyzer-vector fingerprint;
- frontend, normalization/runtime/configuration and selection fingerprints;
- decoder executable and decoding-contract fingerprints;
- full analyzer-input PCM hash, exact selected PCM hash and identity generation.

Source hashes are streamed with constant-sized reads. A 128-entry LRU cache uses
path, device, inode, size, mtime and ctime; unchanged files are not rehashed every
window. Cold sources require one extra sequential file read. Source signatures
are checked after hashing and decoding. This detects local file changes; it is
not an attestation against a malicious filesystem. Decoder executable hashes
bind the executable, not every system shared library. Each immutable manifest retains bounded frontend runtime/configuration and
selection objects with recomputable canonical hashes, plus actual diarization
model/runtime settings and, for native diarization, epoch/replay scope.
The retained exact PCM,
not an assumption of deterministic later decoding, is the review truth.

The analyzer hashes the actual float32 input. The worker independently converts
the exact analyzed PCM16 WAV using signed int16 / 32768, verifies that input hash,
and verifies the full pooled PCM hash before retaining an artifact. The support
keeps pre/post-core context, including negative projected coordinates. Requested
source seek times are **not** exact sample coordinates in the original compressed
file; only actual decoder-output ranges have that interpretation.

## Read-only API

All routes use the existing bearer-token middleware when `AUDIO_API_KEY` is set.
Keep this single-user service on localhost unless it has appropriate external
TLS/access controls. Session IDs and identity groups are not tenant boundaries.

- `GET /sessions/{sid}/segments/{segment_id}/speaker-support`
- `GET /sessions/{sid}/speaker-turns/{turn_id}/speaker-support`
- `GET /sessions/{sid}/speaker-support/{support_id}`
- `GET /sessions/{sid}/speaker-support/{support_id}/audio`

Disabled capture is reported explicitly as `capture_not_enabled` for new
results; enabling it later does not backfill old audio. A result-scoped request returns the immutable support ID, generation, safe
manifest, explicit availability/reason and complete-support status, or a clear
unavailable reason for legacy/non-retained evidence. A support-ID request is
scoped to the owning live session. Metadata and audio use `Cache-Control:
private, no-store`. Neither vectors, absolute paths, arbitrary producer metadata
nor PCM BLOBs appear in JSON. Existing segments/turns/changes do not leak the new
private database pointers.

`review_eligible` means that complete exact bytes are retained for acoustic
review. `human_verified` is always false; purity remains
`predicted_from_diarization_unverified`; `identity_learning_available` is false.
Clients must disclose that review includes **all** pooled fragments and context,
not just one corrected transcript line. On every audio request, length, encoding,
manifest integrity and full PCM SHA256 are checked before producing any body.
The response is a lossless mono 16 kHz IEEE float WAV. Re-decoding a deleted or
changed source never substitutes different bytes under an old support ID. If a
source file changes after capture, the original hash-bound retained PCM remains
reviewable; deleting a contributing chunk through the service revokes it.
Responses explicitly label these as original captured bytes. They do not claim
that a current source file still matches its historical source-content hash.

## Private persistence, quotas and lifecycle

Exact PCM artifacts are private SQLite BLOBs, committed atomically with their
support manifest and result references. Support has an independent lifetime from
the legacy observation ring; evicting an observation does not erase its support.
The original embedding vector may disappear with observation eviction;
`embedding_sha256` is a fingerprint, not a retained vector. Future anchor work
must separately retain an authorized vector or create versioned re-embedding
evidence with actual model/frontend provenance. There are no separate filesystem
artifacts to orphan after a crash. A database
trigger prevents mutation under an existing support ID.

The CLI opt-in is `--speaker-support-review`. Quotas use Python `Settings`:

| Setting | Recommended opt-in default | Hard upper bound |
| --- | ---: | ---: |
| `speaker_support_max_records` | 4096 | 8192 |
| `speaker_support_max_pcm_bytes` | 1,280,000 (20 seconds) | 1,280,000 |
| `speaker_support_total_pcm_bytes` | 64 MiB | 256 MiB |
| `speaker_support_window_pcm_bytes` | 8 MiB | 8 MiB |

A manifest is at most 128 KiB, with at most 128 source parts, 512 selected
intervals and 64 candidate supports processed per window. Admission is serialized
inside the existing SQLite write transaction. It checks the live row/byte totals
and the existing disk/RAM guards, reserving space for temporary SQLite copies.
Audio retrieval checks database BLOB length before a bounded read. Setting the
record limit to zero disables review capture.

There is deliberately **no automatic review-support eviction** in Phase A. At
byte capacity, new rows may retain metadata with `total_pcm_quota` and no audio.
At row capacity, new result references record `support_row_quota` without making
another support row. New review availability resumes after reset/deletion frees
capacity. This is bounded admission, not rolling retention or a promise to keep
future reviewed anchors pinned. SQLite/WAL files may retain their high-water
size after logical deletion, and live-byte quotas do not replace disk guards.

Group reset atomically rotates the generation, deletes all group support and
clears both turn and segment pointers. Session deletion removes its support and
PCM. A dependency trigger revokes the entire support if **any** contributing
chunk is deleted, including cross-chunk context. Restart cannot regenerate old
support from current chunk metadata. Erasure is application-level deletion, not
forensic secure erase or removal from external backups.

Malformed/missing lineage, source-content or model/frontend mismatches, input or
pooled-hash mismatches, quota/resource denial, and incomplete selection disable
review support only. Ordinary transcript/diarization and current matching continue.
Historical rows are not backfilled from a chunk's latest metadata.

## Verification boundary

Synthetic/model-free tests cover exact float bytes, cross-chunk decoding,
overlap exclusion, out-of-core context, >512 intervals, restart/migration,
observation eviction, quotas/concurrent admission, reset/source deletion,
transaction rollback, corruption rejection and API privacy/authentication.
These prove covered program behavior, not acoustic purity or model quality.
The frozen active long-run and earlier evaluation study are unchanged.
