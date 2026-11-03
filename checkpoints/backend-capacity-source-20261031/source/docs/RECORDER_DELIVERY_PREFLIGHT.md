# Android recorder delivery transport preflight

This opt-in, read-only command checks a **local copy** of the recorder's three
FTPS delivery files. It does not contact FTPS or the backend, create a session,
start inference, join segments, delete originals, or change any default policy.

```sh
python -m audio_backend.recorder_delivery /path/to/downloaded/attempt-directory
```

The directory must contain `delivery.v1.txt`, `segment.v1.bin`, and `audio.wav`.
Exit 0 means only that their observed transport bytes match. Exit 2 refuses
malformed, changed, inaccessible, or oversized files. JSON output always has
`metadata_semantics_validated: false`, `immutable_publication_proven: false`, and
`strict_import_ready: false` on successful observation. Never use exit 0 alone as
an ingest, server-commit, remote durability, or deletion acknowledgement.

## What is checked

- Exact bounded ASCII marker, canonical source ordinal, destination revision,
  and attempt binding to source identity, revision, and WAV SHA-256.
- Java RFT1 transport envelope identity, length and SHA-256.
- Inner PCM1 metadata magic, v1/v2 schema number and trailing SHA-256 checksum.
  **The inner object is otherwise opaque.** Its fields, frame counts, hashes,
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
