# Explicit local recorder snapshots

The opt-in snapshot tool creates a bounded local copy of one validated Android
recorder delivery. It is not wired into an uploader, database, strict recording
API, background watcher, or source-retirement path. It never deletes originals.

```sh
python -m audio_backend.recorder_snapshot create /local/delivery /private/parent/new-snapshot
python -m audio_backend.recorder_snapshot inspect /private/parent/new-snapshot
```

The destination must not exist. Empty, interrupted and completed destinations
all refuse a second create; they are never resumed or overwritten. A failed
create preserves every generated file for review. Do not automatically retry by
editing or clearing that directory. The inspect command is always read-only.

## Required trust and ownership boundary

Use a parent directory exclusively controlled by this operator for the entire
operation. The tool refuses a parent not owned by the effective user or writable
by group/others; it keeps a parent directory descriptor through creation and
persistence. New directories/files use private creation modes. Parent path
components otherwise resolve normally.

**Those checks do not stop another process with the same user ID.** POSIX mkdir
followed by opening the new directory is not atomic against an external rename.
A concurrent same-user actor could substitute an existing directory between those
steps. Concurrent modification/renaming of the destination parent or directory is
therefore unsupported. This is not an adversarial shared-filesystem sandbox.
The no-overwrite guarantee assumes this explicit exclusive-parent precondition.
No chmod-based immutable claim or permanent filesystem access control is imposed.

## Creation protocol and bounds

1. Validate the complete source declarations and observed WAV using the optional
   metadata mode, then hash all three bounded source files.
2. Check free space for the expected copies, receipt and 16 MiB headroom. This is
   a point-in-time check, not a disk reservation against unrelated writers.
3. Exclusively create the new destination. Stream each source through 64 KiB
   buffers into an exclusively created regular file; check exact expected
   lengths/hashes and source identity, then flush, fsync and close the output.
4. Revalidate the copied producer declarations, WAV, identities and all hashes.
5. Write canonical checksummed `snapshot.pending.json`, fsync and close it. The
   receipt records observations, not a future durability promise.
6. Create the non-overwriting hard link `snapshot.json` to those exact closed
   receipt bytes, then fsync/close the directory and its original parent. Retain
   the pending link; no cleanup deletion is required.
7. Return `created_and_fsynced: true` only after all original directory/source
   descriptors have positively closed.

Limits are inherited: 512 MiB WAV, 8.5 KiB envelope, 1 KiB marker; the receipt is
at most 8 KiB. Reads stream, and source files are hashed more than once. One
cooperative 120-second budget covers the entire operation; blocking filesystem
I/O, fsync and close do not have a hard interrupt timer. This Linux/POSIX protocol
requires directory fsync, hard links and ordinary regular-file semantics. It has
not been qualified for network mounts, Windows or Android filesystems.

## Interrupted publication and reconciliation

Before the final receipt link exists, inspect refuses an incomplete snapshot.
It never fills missing bytes, invents an acknowledgement, replays source work,
updates a receipt or removes a partial file. Preserve incomplete state and use an
explicitly new destination only after reviewing the failure and available space.

If the final link exists but the creator lost an acknowledgement or failed during
a later fsync/close, a later inspect may verify all observed files and receipt.
That yields `observed_snapshot_verified: true` with
`publication_durability_proven: false`. It does not retrospectively claim that the
failed creator succeeded or that bytes survived a physical power failure.
Unknown close outcomes likewise are not transformed into successful creation.

Inspection checks exactly five expected entries, bounded canonical receipt JSON,
receipt checksum, pending-receipt bytes, all file hashes, full metadata/WAV
validation, and repeat byte inventories. It checks directory identity around
path-based semantic reads. These are observations, not an authenticity signature
or a guarantee against later file modification. A same-user actor who can rewrite
every file and receipt can construct a different consistent snapshot.

`strict_import_ready` remains false in every result. Snapshot creation does not
establish hardware-frame mapping, continuity across capture epochs, or cross-chunk
speaker identity. It does not authorize primary inference or MOSS dispatch.
Sequence/gap mapping and durable API idempotency remain separate work.

## Validation, 2026-11-03

159 selected tests passed: 29 new snapshot tests, 104 metadata/transport checks,
and 26 existing capacity CLI/page checks. Fault coverage includes all six fsync
positions, lost directory-close acknowledgement, link failure, source mutation,
postpublication tampering, directory replacement, existing destinations,
noncanonical receipt, shared-parent refusal and disk headroom refusal.

Four tests run a real child process that exits abruptly before selected fsyncs,
then inspect from a fresh process. They verify incomplete refusal or read-only
observed-byte reconciliation without changing source/snapshot bytes. These are
process-exit controls, **not power-cut or storage hardware durability tests**.

All nine current Java-generated producer deliveries also passed local copy,
revalidation and inspection. No generated WAVs or receipts are committed.
Independent review found the mkdir/open substitution race and prompted the explicit
exclusive-private-parent scope above. One initial close-fault test targeted the
preflight close rather than snapshot close; that fixture was corrected to target
the actual destination descriptor. No product failure was relabeled as a pass.
No full-suite, real recording, FTPS network, strict API, model or deployment
qualification is claimed.
