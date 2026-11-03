# Local completed-delivery publication

This opt-in local receiver boundary makes ordinary growing FTPS `STOR` files
compatible with the bounded inbox runner. It installs no service, exposes no
listener, creates no backend session, and performs no HTTP or inference. It does
not change the Android producer or its existing WAV/envelope/marker wire format.

## Ownership and completion contract

Use three disjoint canonical private directories outside Git:

- `raw`: the FTPS server's writable staging root, whose immediate children are
  original `r-...` attempt directories. Live audio, envelope and even partially
  written marker files remain here. Raw attempts are never watched by intake.
- `inbox`: an initially empty receiver-owned publication directory. Only this
  publisher writes it. The FTPS account must not have access to it. The runner
  reads these original stable paths for the entire job lifetime.
- `receiver`: new immutable configuration, two lock files, and a private
  `prepare` directory. Receiver state and inbox must share a Linux filesystem
  supporting `renameat2(RENAME_NOREPLACE)` and directory `fsync`.

Parents and roots must be owned by the process user and private (0700). The
receiver must have exclusive control of these namespaces. Advisory locks do not
prevent arbitrary writers from modifying files. The commands fail on observed
replacement or mutation; they do not grant authenticated producer identity or
prove that an uncooperative writer cannot race them.

The receiver, not the Android uploader, invokes publication only after it knows
that all upload handles for this attempt have closed successfully. A completed
marker `STOR` callback is sufficient only when the receiver also knows the WAV
and envelope uploads completed and no other upload handle for that attempt is
live. A marker becoming visible, a parseable marker, a quiet time interval,
transport EOF on a failed upload, or a retry from a client is not that completion
signal. The publisher takes an explicit `completed_delivery=True` assertion; it
cannot establish the truth of the receiver's callback or authenticate it.

Concurrent uploads to other raw attempt directories are allowed. A source file
or attempt-directory mutation observed during snapshotting or while waiting to
publish refuses this invocation. It is never silently retried or treated as a
benign partial input. Once published, later raw writes cannot alter the separate
owned copy. The original raw attempt is never renamed, deleted, or modified, so
existing FTPS readback/reconciliation remains available.

## Trusted lifecycle adapter

The optional [server-neutral completion adapter](RECORDER_RECEIVER_COMPLETION.md)
owns new attempt creation and bounded stream opens, records the real receiver
transfer outcomes, and automatically invokes this publisher only after all three
exact streams close successfully. It adds process-lifetime ownership and raw
retention bounds without changing this lower-level API. It must be wired into
the actual receiver before any write-open; a final callback alone is insufficient.
It installs no service and does not adopt/replay old attempts after restart.

## Configure, publish, bind a runner

Create the raw/inbox roots and their private parent first. Choose an absent
receiver state directory. Configuration refuses nonempty inboxes and never adopts
a preexisting publication inbox.

```sh
python -m audio_backend.recorder_receiver_publication configure \
  --state-dir /private/receiver \
  --staging-dir /private/raw \
  --inbox-dir /private/inbox --exclusive-receiver

# Only from the receiver's completed-attempt handling:
python -m audio_backend.recorder_receiver_publication publish-completed \
  --state-dir /private/receiver \
  --delivery /private/raw/ORIGINAL_ATTEMPT \
  --completed-delivery --deadline-seconds 120
```

Equivalent Python interfaces are `configure(state_dir=..., staging_dir=...,
inbox_dir=..., exclusive_receiver=True)` and `publish_completed(state_dir=...,
delivery=..., completed_delivery=True, deadline_seconds=120)` in
`audio_backend.recorder_receiver_publication`. Neither discovers attempts or
selects the newest retry. A server integration must explicitly call the API from
its own completed-attempt boundary; this module alone does not wire up an FTPS
server callback.

After the first completed publication, configure the existing runner with its
usual explicit first delivery, target and capacity, plus:

```sh
python -m audio_backend.recorder_inbox_runner configure \
  --state-dir /private/runner --inbox-dir /private/inbox \
  --receiver-state-dir /private/receiver \
  --first-delivery /private/inbox/ORIGINAL_ATTEMPT \
  --job-dir /private/job --output-dir /private/export \
  --api http://127.0.0.1:8765 --session-id EXISTING_EMPTY_SESSION \
  --capture-id EXPLICIT_CAPTURE
```

The receiver binding is frozen in the runner configuration. Existing configured
runners are not edited or silently upgraded. The ordinary `run` and finite
`watch` commands then coordinate every inventory with this receiver. Keep the
usual exclusive-writer/span-closure acknowledgments and separate finalize/export
permissions. `--wait-for-marker-last` is not needed for this boundary; it remains
only the original narrow legacy option and does not make growing raw uploads
safe.

The read-only programmatic observation API is
`inventory(binding, directory, deadline_seconds=120, check=None)` using `bind(state_dir)`. The optional `check`
callback is called during lock waits; the runner supplies its cancellation-aware
guard, so waiting on publication does not delay cancellation until the deadline.
It opens preexisting locks only. The standalone legacy `recorder_inbox.inventory`
and its CLI remain unchanged and uncoordinated; do not use them to claim a
coherent observation while this publisher is active.

## Publication, contention and restart

The publisher boundedly reads exactly the three ordinary single-link regular
files, validates all transport hashes and producer metadata semantics, and copies
them into a new private preparation. It validates the copied bytes again,
including the exact marker bytes, and fsyncs files and directories. It preserves
attempt, source, revision, capture/run/epoch/sequence, audio, envelope and marker
bytes. Published directories contain exactly those three files; there are no
extra receipts inside the inbox.

A separate `preparation.lock` serializes snapshot operations. The
`publication.lock` is shared by inventory and exclusive only for the final
no-replace rename and directory syncs. Hashing/copying takes place outside this
publication critical section. Inventory keeps its original root-mutation and
file-corruption checks. The runner never holds this lock while doing HTTP,
intake, analysis status, or poll sleeps. The invocation-long `runner.lock` stays
separate, so publication does not wait for an entire watch invocation to end.

All lock waits, validation and copy work share one finite deadline, at most 120
seconds. Each file has the existing format bounds (WAV at most 512 MiB). There
are at most 256 inbox entries and at most 256 retained preparations, with a hard
8 GiB aggregate file-byte cap across both trees. Bounded stat-only scans include
partial preparations before allocating another copy. Disk headroom is also
checked before copying. The existing inventory aggregate 8 GiB WAV budget still
applies. A receiver operator must select retention and capacity before reaching
these limits; this command never cleans up originals or
published paths.

- Exact same-attempt replay validates all raw and published bytes, returns
  `already_published`, and re-fsyncs the files, published directory and both
  namespace parents. It does not replace the existing original or change paths.
- Different content at the same attempt is refused. A different transport nonce
  remains a separate attempt, preserving the runner's explicit duplicate
  selection and conflicting-ordinal checks.
- Interruption before rename leaves an unexposed `pending-*` preparation.
  Explicit re-invocation makes a fresh bounded preparation; it never repairs or
  deletes that evidence. Reaching the preparation bound requires investigation.
- Interruption after rename may leave a complete visible final even if the call
  reported failure. Re-invoke against the same exact source/state to validate
  and re-fsync that outcome. Do not remove, replace, or rebind the published path.
- Existing destinations, including empty directories and symlinks, are never
  overwritten. Unsupported atomic no-replace publication is a hard refusal,
  with no check-then-rename fallback.

Successful local publication is not permission to ingest, proof of physical
continuity, proof of whole-capture completion, or an ASR quality result. The
existing runner/intake validation and authorization remain responsible for those
separate stages. Unit/protocol tests here use local fixtures and model-free
peers, not a deployed receiver, real phone or native inference qualification.

## Separate actual FTPS qualification

The [bounded live-wire result](../qualification/receiver_wire_20261103/RESULT.md) passed with independent audit: a real partial marker remained isolated during the same running watch, then completed publication admitted one exact source. Later actual STOR writes, same-attempt conflict and fresh-process replay were verified. The backend peer was model-free; this is not native ASR, device, automatic callback installation or deployment evidence.
