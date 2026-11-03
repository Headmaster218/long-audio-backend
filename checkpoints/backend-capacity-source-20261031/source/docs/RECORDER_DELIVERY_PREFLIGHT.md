# Android recorder delivery transport preflight

This opt-in, read-only command checks a **local copy** of the recorder's three
FTPS delivery files. It does not contact FTPS or the backend, create a session,
start inference, join segments, delete originals, or change any default policy.

```sh
python -m audio_backend.recorder_delivery /path/to/downloaded/attempt-directory
```

The directory must contain `delivery.v1.txt`, `segment.v1.bin`, and `audio.wav`.
Exit 0 means only that their observed transport bytes match. Exit 2 refuses
malformed, changed, inaccessible, or oversized files. Default JSON output has
`metadata_semantics_validated: false`, `immutable_publication_proven: false`, and
`strict_import_ready: false` on successful observation. Never use exit 0 alone as
an ingest, server-commit, remote durability, or deletion acknowledgement.

## What is checked

- Exact bounded ASCII marker, canonical source ordinal, destination revision,
  and attempt binding to source identity, revision, and WAV SHA-256.
- Java RFT1 transport envelope identity, length and SHA-256.
- Inner PCM1 metadata magic, v1/v2 schema number and trailing SHA-256 checksum.
  **In default transport mode, the inner object is otherwise opaque.** Its fields, frame counts, hashes,
  source identity and capture-anchor semantics are not decoded or cross-checked.
  Even an internally invalid metadata object can pass transport preflight when
  its checksum and outer hashes match. This is intentional separation of layers,
  not complete recorder metadata validation.
- Canonical 44-byte RIFF PCM16 WAV geometry, full payload length and WAV SHA-256.
  Mono/stereo and other producer-supported channel counts are observed without
  resampling. The computed PCM hash is diagnostic, not provenance authority.
- Three held regular-file descriptors, no final-component symlinks/FIFOs, stable
  device/inode/size/mode/timestamps before and after reads, and matching directory
  entries at the final observation. No file is opened for writing.

Limits: 1 KiB marker, 8.5 KiB envelope, 512 MiB WAV, 64 KiB streaming buffer.
The 120-second deadline is cooperative between file operations, **not a hard
wall-clock bound** on slow filesystem I/O or close. This is a local POSIX tool;
parent path components are resolved normally. It is not a sandbox for untrusted
mounts or proof that a file will stay unchanged after the result is returned.

Hashes establish consistency, not authenticity. An actor able to rewrite every
file can generate a different internally consistent delivery. Neither the FTPS
marker nor this output is an immutable server publication receipt.

## Integration boundary

The producer's metadata v2 retains observational BOOTTIME information and an
explicit UNESTABLISHED hardware-frame mapping. This command does not turn those
observations into a continuous clock, normalized capture coordinates, trusted
seams, cross-segment speaker identities, or primary evidence. The existing append
uploader cannot consume this preflight output as authorization to concatenate
recordings. A future intake must fully decode/validate metadata, reconcile
capture/epoch/sequence/gaps, establish an owned immutable source snapshot and
persist exact ingestion identities before handing off to strict recording APIs.
Strict primary-only routing and MOSS dispatch restrictions are unchanged.

## Validation, 2026-11-03

32 new synthetic pytest cases plus 26 existing capacity resume CLI/page
reconciliation checks passed together (58 total). The new cases cover altered
bytes, malformed markers, envelope mismatches, forged matching outer hashes,
unsupported schemas, canonical WAV restrictions, symlinks, FIFO, oversized sparse
files, pathname replacement, deadline refusal and JSON CLI behavior. A deliberately
invalid inner metadata fixture checks that success never claims semantic validation.

Three separately generated **actual Java producer** fixtures passed the Python
checker: metadata v1 mono 16 kHz, v2 mono 16 kHz and v2 Unicode/stereo 48 kHz.
Each contains only three synthetic frames; generated audio is not checked in.
Reproduce using the recorder repository's `scripts/test-ftps-delivery.sh` with a
fresh output directory, then run:

```sh
python scripts/check_recorder_delivery_fixtures.py /path/to/generated-fixture-root
```

No real recording, network FTPS round trip, strict HTTP ingest, model execution,
acoustic accuracy, cross-chunk identity, long-run capacity, or deployment was
qualified by these checks. The repository-wide test suite was not run.


## Optional producer metadata validation

```sh
python -m audio_backend.recorder_delivery /path/to/delivery --validate-metadata
```

This separate option completely decodes the bounded PCM1 v1/v2 declaration and
checks its geometry, frame/ordinal ranges, close/seam rules, RIFF bounds, source
identity, declared PCM hash and WAV hash against the actual observed file. It
validates v2 anchor summaries using the producer's constructor invariants. It does
not add evidence to them: BOOTTIME values can regress, raw AVAILABLE framePosition
and nanoTime can be negative, and the mapping remains UNESTABLISHED. These values
are preserved as observations, not silently corrected or promoted into a clock.

On success, `metadata_semantics_validated` is true with explicit scope
`producer-declarations-and-observed-wav`. `strict_import_ready` and
`immutable_publication_proven` remain false. The JSON includes hashed capture/run
identities, epoch/sequence/recorded frame start, boundary, close/seam declarations
and aggregate anchor flags; it does not echo raw capture/run names or all timing
observations. A forged but internally consistent declaration can still pass.

The consumer implements canonical Java modified UTF-8 and counts at most 512
UTF-16 code units per string, including supplementary characters. Java trim
semantics (only code units <= U+0020) are preserved. Unlike permissive Java decode,
noncanonical modified UTF-8, noncanonical booleans and unpaired surrogates are
refused. The latter avoids Java's replacement-based UTF-8 identity hashing. This
is an explicit narrower acceptance policy, not byte-for-byte acceptance of every
possible Java String.

Final selected validation for this stage: 130 tests passed, including the 32 prior
transport cases, 72 metadata/opt-in cases, and 26 existing capacity CLI/page checks.
Initial development had one erroneous negative-test expectation: maximal signed
sample rate with mono still fits the unsigned RIFF byte-rate field. The fixture
was corrected to stereo overflow and a positive mono-boundary test was added;
product limits were not loosened to make the test pass. Independent review checked
producer invariants and exercised successful v1/v2 CLI cases.

Nine generated Java deliveries across seven scenarios passed both transport and
semantic checks: original three fixtures plus NOT_POLLED, UNAVAILABLE,
QUERY_EXCEPTION and three 4+4+2-frame rotation segments. Run the fixture checker
with `--expanded` for these cases. These remain synthetic declarations, not device
or network tests. No generated audio files are included in the source backup.

## Remaining owned-snapshot and sequence-gap design

No owned snapshot is created by either mode, and no multi-file importer is enabled.
A future importer must pin/read the exact accepted bytes into exclusively created,
bounded, importer-owned files; fsync and positively close them before publishing a
journal entry; then revalidate those owned bytes and persist source/idempotency
identities. Unknown copy/close/publication outcomes must stop for reconciliation.
Neither a filename nor a successful preflight from a prior invocation is authority.

Sequence and raw frame positions are declarations within a capture/epoch, not
normalized 16 kHz capture coordinates. Candidate adjacency requires at minimum
matching capture/run/epoch/format, exact consecutive sequence and frame extent,
and compatible rotation/seam declarations. Restart, route/format change,
interruption, recovered tail, unknown gap, missing or conflicting ordinal, or a
format resampling boundary must not be silently concatenated or filled with
silence. V2 observations do not settle those gaps. Even the generated three-piece
rotation example only demonstrates matching declarations, not physical continuity.
This design review is not an implementation or qualification of snapshot ownership,
crash recovery, strict API ingestion or global speaker identity.
