# Strict archive v2 — bounded offline vertical slice

**Source-authored, not yet execution-qualified. Not 12-hour/48-hour ready.**

This fresh format is `strict-primary-archive-v2`. The default and original
`strict-primary-manual-v1` DDL remain unchanged. There is no migration, implicit
adoption of legacy data, capacity increase, or permission to delete producer
files. Only a fresh/empty database or an exactly matching v2 database is accepted.

## Scope and ownership

The three Store-only APIs are `archive_completed_chunk(cid, budget)`,
`open_archived_source(archive_id, object_id, budget)`, and
`retire_cached_source(cache_id, budget)`. V2 requires `offline=True`,
`start_worker=False`, the existing strict prerequisites, and exclusive admission
through the same Store. No HTTP, Worker, alias-reader or retry cold-routing path
is connected. A Python cleanup flag is not proof that arbitrary trusted threads
are absent; other writers/manual consumers are unsupported.

The archive collector reads actual retained Store rows and files, never a
caller-supplied proof dictionary. One complete completed chunk/page is collected
all-or-nothing. Original lexical bodies, outcome/compact/primary packets,
source/work audio and receipts/ranges, selected-support manifests and PCM, exact
vector bytes, embedding-space descriptors and normalization are retained.
Unknown or external dependency graphs are refused, not truncated or represented
by digest-only recipes. Pending native seam/external-cell graphs are unsupported. Retained packet refs
must cover every primary row exactly. `unknown_only` outcomes are refused in
this slice because they lack the durable per-row packet binding; this is not a
claim that the ordinary strict commit path rejects those outcomes. Native packet
turns bind clipped geometry, labels and multiplicity to SQL turns; remaining
turn columns are retained current-snapshot projections, not packet-authenticated
producer fields.

## Fixed bounds

The independent caller-owned `ArchiveBudget` is shared across all phases and DB
scopes: 5 seconds, 128 MiB controlled explicit file bytes plus SQL projected
payload reads, 4 MiB metadata/proof, 32 MiB retained payload, 1 MiB reusable I/O
buffer, and 150,000 SQL VM steps. The read cap does not account for all kernel
filesystem/page-cache or import-loader I/O. No phase renews it.
Tighter source-format limits still apply. One chunk is at most 168,000 frames
(10.5 seconds), with at most 4 contiguous outcomes, 512 primary rows and 256 turns.
Per-kind collector caps include 16 original sources, 32 ranges, 8 selected
supports, 8 exact vectors and 8 linked observations; 17 source objects and 40
objects total. The Store retains at most 16 archive checkpoints and 40 immutable
cache-generation rows. The archive directory has a 112-name census bound, counting
staged/orphan files as well as published names, plus a fixed apparent-byte
ceiling derived from those row limits. New publication reserves both staged and
final names/bytes and respects the original disk floors. All limits refuse
overflow; no prefix is success. Known failed staged files are not garbage-collected
and can consume this fixed quota until external teardown; unknown cleanup keeps
the exact owner and process fail-stop latch.

## Durable states and cold access

A positive source snapshot close precedes archive staging. Staged complete bytes
must be verified, fsynced and positively closed. Publication includes directory
fsync and close. The original Store then rechecks source/tip/proof/quiescence and
commits the immutable checkpoint. A caller acknowledgement lost **after known
COMMIT and positive original close** permits reconciliation against the exact
checkpoint, never primary interval replay. An exception from the original COMMIT
with unknown outcome is different: even positive rollback/close cannot clear the
original owner and process-global fail-stop latch.

Cold fetch validates the complete checkpoint and bundle before exposing a new
archive-owned duplicate and explicit lease. The logical source remains stable;
the fetched generation, generation-bound cache ID, pathname and stat incarnation
are new. A stale cache ID is refused once a new incarnation exists. Exact producer
signatures are never rewritten. Retirement commits a specific intent and may
unlink **only that exact new cache duplicate** after all leases/copies/owners are
positively closed. It then fsyncs/closes the directory and confirms absence.
Absence alone is not evidence of a completed retirement after interruption.

Unknown file/directory/database/lease close or unknown original COMMIT preserves
the original error, original owner and process-global fail-stop latch. A new same-process Store is not a substitute for supervisor
cleanup. Tests may reopen data only after every old DB context has positively
closed, connections and all operation/lease slots are empty, depth is zero, and
`strict_cleanup_confirmed()` remains true. Such data reopen is not a real service
restart qualification. Unknown owners stay in fixed retained test slots and are
not reclosed or replaced. Test-only monkeypatch isolation may restore the global
registration for later independent generated fixtures; it retains each exact old
owner/descriptor/connection in a fixed witness slot. Production has no reset API.

## Verification boundary

The source-authored seven-group suite contains 66 statically enumerated cases
(not yet executed). It uses fresh temporary SQLite plus actual generated tiny
canonical WAV/source/work files and original Store claim/commit functions. Its
nonempty positive fixture includes real full lexical bodies, matching canonical
receipts/ranges, selected-support PCM/manifests and original vector bytes. Native
observations, frontend/runtime provenance and embedding numbers are deliberately
synthetic numerical witnesses; no model/native/runtime/acoustic qualification is
claimed. No model assets, historical audio/database, network call, subprocess,
worker, or thread is required. Execution needs its separately reviewed command.
The 16-checkpoint/40-cache-generation lifetime ceilings and artificially corrupted
noncontiguous immutable outcomes receive source-guard review only; the 66 cases
do not dynamically exhaust those ceilings or bypass/rebuild immutable triggers.
The bounded directory census and its iterator-close-unknown branch are also
source-reviewed rather than independently fault-exercised by this fixed selection.

The lifecycle case keeps another unarchived manual tail in the same Store. Its
original retry obtains a new nonce and preserves the accepted prefix; this is not
Worker cold-tail integration.

## Explicitly deferred

- Retiring the existing 2,056 immutable hot outcome/compact rows.
- Original source/work eviction or rewriting `chunks.path`.
- Native cell/receipt limits and long-run index policy.
- Worker/alias/manual-retry cold routing and live-retirement qualification.
- Any real/model/long-run execution or 12-hour/48-hour readiness claim.

The original candidate04 and all v1 producer files/hot rows remain untouched.
