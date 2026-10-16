# Automatic timestamped-recording MOSS dispatch (dev25)

This release adds a finite, default-off automatic dispatch
path. It retains eligible overlap intents during primary processing and gives
them a bounded publication/admission opportunity at an eligible committed-core
handoff or when the ordinary Worker is idle. There is no per-job POST for automatic dispatch. The source qualification
and installed-package checks, with their separate limits, are recorded below.

Primary transcription and MOSS diagnostics remain separate outputs. The separate read-only unified transcript is provisional; see
[UNIFIED_TRANSCRIPT.md](UNIFIED_TRANSCRIPT.md). Automatic
dispatch does not establish complete overlap recovery, word accuracy, global
speaker identity, EOS or complete model content.

## Opt-in and input boundary

`moss_automatic_enabled` defaults to `False` and must be a boolean. The CLI
switch is `--moss-automatic`. It requires all of:

- `--moss-jobs` and an explicit absolute local `--moss-jobs-model` file;
- `--native-owned-support --native-owned-cells --native-owned-core-seams`;
- CPU inference, Nemotron native diarization with its existing local model and
  library, and `--overlap-backend none`;
- the ordinary Worker with `start_worker=True`, without a fragment provider;
- the existing qualified runtime, SQLite/VFS, filesystem and fixed MOSS profile.

For an otherwise qualified ordinary-worker configuration, add these flags:

```text
--device cpu --diarization-backend nemotron --overlap-backend none
--native-owned-support --native-owned-cells --native-owned-core-seams
--moss-jobs --moss-jobs-model /absolute/local/qualified-moss-model.gguf
--moss-automatic
```

This is an addition to an existing valid local configuration, not a complete
deployment command. It does not install a runtime or download a model. The
fixed profile and manual job configuration remain described in
[MOSS_HANDY_JOBS.md](MOSS_HANDY_JOBS.md).

The first guaranteed input scope is timestamped `recording_work` backed by durable source
receipts, retained exact PCM and a validated device-timeline mapping. A timestamp
alone is insufficient. The first slice includes ordinary selected cells and
strictly validated adjacent-core or reset-replay seam cells. It explicitly
rejects automatic mode combined with `native_owned_seam_cells_enabled` /
`--native-owned-seam-cells`: that legacy upload-seam path is excluded. There is
no historical metadata backfill or automatic conversion of an old diagnostic
into new authority.

## Durable intent and atomic core commit

`moss_automatic_intents_v1` and `moss_automatic_usage_v1` live in the existing
primary SQLite database and WAL. No new database, temporary sort, model worker,
PCM pool or inference framework is introduced.

Initialization applies an additive primary-database schema migration containing
two tables, eight indexes and one cancellation trigger, even while automatic
dispatch is disabled. Existing metadata is not backfilled. Incompatible schema
is strictly rejected rather than silently adopted or repaired. Downgrade to
pre-dev19 code against a migrated database is unqualified.

Each intent binds a stable candidate key, fixed policies, exact source-frame
ownership, at most two compact slots/source roots, exact proof and proof hash,
and the cell/result identities. Candidate keys exclude the process epoch. A
conflicting proof cannot replace the first binding. Exact proof is retained
until the intent becomes terminal; it is not discarded because an attempt is
temporarily blocked.

A committed core produces at most three ordinary intents and one core/reset
seam intent. Before processing the next core, admission requires room for four
rows, 344,064 charged bytes, 262,144 proof bytes and one compact. Primary text,
compact authority, every eligible intent for that core and `next_offset` commit
atomically. Unexpected enqueue failure rolls back the whole automatic core;
the primary offset must not advance past missing automatic evidence.

## Fixed finite budgets and backpressure

The v1 limits are fixed:

| Resource | Global | Per session |
| --- | ---: | ---: |
| Retained intent rows | 4,096 | 1,024 |
| Charged intent bytes | 64 MiB | 16 MiB |
| Nonterminal intents | 256 | 64 |
| Retained proof bytes | 16 MiB | 4 MiB |

One row charges at most 86,016 bytes: 16 KiB accounting, up to 64 KiB exact
proof and up to 4 KiB binding. These are admission/accounting bounds, not an
assertion about physical SQLite or WAL allocation. Existing MOSS-result quotas
remain separate and continue to apply. The existing 8 MiB result-write allowance,
2 MiB scratch allowance and disk free-space floor are unchanged.

At capacity, primary processing yields at the unchanged core offset. Automatic
work can drain on committed-core handoffs and Worker idle opportunities. A missing PCM source, full cell
store, busy shared slot or other temporary admission blocker leaves the intent
pending and preserves its proof; it is never represented as inferred success.
Automatic publication does not insert a metadata-only cell on a PCM-capacity
refusal or `SQLITE_FULL`; the exact intent stays queued for a later bounded
opportunity. A pre-existing exact cell whose PCM was explicitly retired stays
visibly blocked; this slice does not silently restore retired PCM.

Freeing pending/proof capacity does not free every retained-row charge. Terminal
intent rows are not evicted automatically, so retained-row capacity eventually
requires explicit session erasure or an operator change to the applicable
deployment conditions. This release offers no unbounded-retention or
arbitrary-duration guarantee, and no CLI flag raises these fixed intent caps.

Queries use bounded, length-first projections and indexed scalar usage/root
lookups. The existing SQLite operation deadlines, VM-step budgets, source-read
limits, owner handoff limits, copied-pin budgets and native-process concurrency
are unchanged. Optional compact/receipt reclamation cannot erase an intent's
pending or accepted authority to manufacture space.

## Fair handoff and the shared execution slot

Dev24 distinguishes queued primary requests, a granted waiting cohort, and
executing owners. A due automatic turn freezes the current granted cohort
(at most one Worker and one probe). New primary requests queue for the next
cohort and do not extend the current one. After positive completion and cleanup,
one selected automatic candidate gets its bounded opportunity before the next
queued cohort is granted. An executing or already-granted primary always fences
optional admission. The Worker leaves its database row queued while waiting;
only after grant does it repeat eligibility checks and claim the current row.
It holds no database transaction or manager lock during that wait.

The primary unit is one safely committed core, with durable next_offset, complete
receipt publication and ordinary analyzer cleanup. No live analyzer is carried
across MOSS. When the automatic queue is empty, disabled or globally blocked,
the existing per-input analyzer path continues. A last-moment admission or source
race can still cause an unproductive reload. Reload and native context replay
costs require real-model measurement; synthetic continuity is not a speed claim.

Selection uses an immutable RAM snapshot of at most 256 queued/published keys,
at most 64 per session, read through the existing indexed metadata projection.
Sessions rotate after each selected key; all captured keys are visited or
boundedly skipped before a new snapshot. New arrivals wait for the next snapshot.
Deleted, stale or blocked keys consume their visit but keep their durable state
unless an existing authoritative transition changes it. A continuously eligible
captured key is visited within 256 opportunities; a new arrival within 512,
conditional on healthy opportunities. These are selection bounds, not time or
successful-inference guarantees. Accepted/unknown attempts are never replayed.

A selected queued key may continue publication, exact acknowledgement and
admission in the same turn, using the original 2-second publication-operation
deadline. An unknown flight is retained without spinning, resubmitting or
refreshing its budget. Acceptance keeps the existing atomic result reservation
and accepted-intent link; result commit marks that intent committed atomically.

Automatic and manual requests share the existing single MOSS execution/storage
slot, permit and native cleanup obligations. One admitted automatic text owner
is protected from queued primary arrivals through its existing 300-second
execution and 60-second cleanup limits and bounded first terminal publication.
Setup, validation and publication retain their separate existing bounds; 360
seconds is not a total primary-wait SLA. Explicit cancellation, shutdown,
revocation, disablement, resource/deadline failure and ownership uncertainty
still stop or fence work. Manual optional policy is unchanged. A post-text
speaker probe is a separate, preemptible owner and yields to primary requests.

There is no new model thread, database table, retry ledger or persistent scheduler.
The RAM grant/snapshot state starts empty on restart; durable accepted/unknown
work remains governed by the existing no-replay rule. Manual 64-key acceptance
ledger semantics and all quotas remain unchanged.

## Read-contention pre-window barrier (dev25)

Before preparing a native window, automatic mode checks the existing recording,
intent and compact capacity through a read-only result-store connection. Only a
numeric SQLite BUSY/LOCKED at the read begin/body phase may produce the private
pre-window deferral, and only after that connection closes successfully. The
ordinary public error mapping remains HTTP503/result_storage_unavailable.

The Worker keeps the durable offset and existing proofs, uses the existing
conditional queued transition and ordinary process cleanup, then waits on its
stop event for 100 ms before rechecking. It does not execute optional work on
that deferred iteration. No native window is skipped and no accepted attempt
is replayed. Capacity/configuration backpressure retains its existing behavior.
A fixed callsite/operation/phase/reason/numeric-code record identifies this
specific deferral; log sink failure cannot change the outcome.

Deadline/VM exhaustion, I/O, changed database identity, schema errors, failed
connection closure, writes and uncertain commits are not reclassified. There
are no SQL retries inside the database context, higher busy timeout, larger
budget, or uncertain-publication relaxation. Repeated contention may continue
to delay progress; this is not a finite recovery-time guarantee. See
[DEV25_PREFLIGHT_DEFERRAL_ZH.md](DEV25_PREFLIGHT_DEFERRAL_ZH.md) for qualification.

## State, restart and save fences

The normal lifecycle is `queued` to `published` to `accepted`, then `committed`,
`no_result` or `interrupted`; cancellation is terminal. A durable queue entry is
not evidence that inference ran. An accepted record without an exact committed
result after restart projects `interrupted_or_unknown`; the service never
automatically replays inference for that old acceptance or infers a successful
result from it.

Source-generation and permit-publication fences guard automatic saves. Current
primary cancellation/deletion predicates veto a save. The guard order is manager
gate, source publication-generation fence, permit condition, then primary/result
database transaction. No owner handoff, native call, thread join or source/config
callback occurs while these guards are held. This does not retroactively change
historical manual-save semantics. The exact automatic text owner remains held
through the first bounded publication attempt. A lost commit acknowledgement or
generic storage/readback failure retains the computed immutable snapshot and
blocks ownership release, even if cancellation also arrives. Existing exact
committed-result readback or a positively acknowledged terminal outcome may
resolve it; an authoritative deletion/source veto releases only an already
cleaned exact owner with no uncertain probe. The Worker scheduling pump does not
automatically resolve retained uncertain text commits. Cleanup or publication
uncertainty can therefore block primary indefinitely pending explicit recovery;
no timeout silently clears that state.

Pending and accepted intent rows are indexed roots for both compact and receipt
GC, including both sides of a seam. Session deletion removes its intents in the
primary deletion transaction. Restart does not turn incomplete evidence or
uncertain native ownership into permission to publish.

## Read APIs and results

- `GET /sessions/{sid}/moss-automatic`: bounded automatic-intent status listing.
- `GET /sessions/{sid}/moss-automatic/{candidate_key}`: one stable candidate's
  state and bound diagnostic identity.
- `GET /sessions/{sid}/moss-jobs/history/{job_id}/result`: the existing retained
  structured result for a committed job linked by the intent.

The automatic routes are observational; reading them does not enqueue a job,
publish a cell, repeat inference or clear a blocker. Retained structured results
continue to follow the existing
[durable-history contract](MOSS_HANDY_JOBS.md#durable-structured-history).
An automatic `committed` result remains a diagnostic snapshot, not a replacement
for the primary transcript or proof of reconciled content. Consult state and
explicit blocker information rather than treating primary completion, a listed
candidate or HTTP success on a read as MOSS completion. The capacity projection
includes candidate/pending counts, oldest queue age, a blocker reason, in-flight
publication, and `discovery_complete`. Failed unfinished recording work keeps
discovery incomplete, including an explicit cross-run boundary refusal.

## Historical dev19 qualification (not dev24 acceptance)

The frozen functional source passed 92 focused model-free cases and, in a
separate run, 634 existing regression cases with 46 subtests. The runs used
CPU 3, nice 19 and recorded 1 GiB RSS / 2 GiB address-space limits; no models
were imported. These limits are not measured peak-memory results.

The real-model source observations total eight automatic native attempts,
eight committed results and zero manual MOSS mutations:

- AMI, 21 seconds from two adjacent 10.5-second sources: three automatically
  committed results, three native calls and zero manual job POSTs. The original
  helper failed solely on listener-port reuse during its models-disabled restart.
  A separate GET-only history supplement passed without rerunning a model;
  this does not turn the original helper into a clean pass.
- Chinese, 17-second whole input: three calls and three committed results.
  History and cleanup were independently accepted.
- Chinese, 14.5-second whole input: two calls and two committed results.
  The final audit was accepted. The second ordinary core correctly selected no
  native overlap: the witnessed overlap ended at frame 167,024, before that
  core's start at 168,000; the broader seam retained the boundary overlap.

All three models-disabled history reads were byte-exact, all schema-error lists
were empty and all database connections were closed.

Dev19 passed standard offline wheel construction, a fresh pip target installation
and exact source/wheel/installed comparison of all 119 package files. Dependency
declarations and third-party license files were unchanged. Default-off application
construction, CLI help, four prerequisite-rejection checks and five installed
model-free tests passed. A newly seeded synthetic dev18 database upgraded with
all existing table rows, schema and committed history unchanged; automatic
intents stayed empty and reopening was idempotent. This does not qualify upgrade
of an existing user database or downgrade compatibility.

Packaging reused the existing qualified dependencies, downloaded no assets and
ran no models; it was not a fresh dependency-environment qualification. Only this
guide and README received factual packaging updates afterward. Package files,
tests and build configuration retain the checked bytes, so the wheel was not
rebuilt for that documentation-only update.

The real-model observations above remain source qualification; models were not
rerun against the installed dev19 distribution. Previous dev18 and earlier test,
install and model records remain historical facts for their own snapshots. These
short checks do not establish transcription quality, EOS, global speaker identity,
complete overlap recovery, throughput, crash durability or long-recording
qualification.

## Current restart and bounded revocation

Already-durable unadmitted proofs can resume publication/admission after restart;
committed structured history is readable with models disabled. New primary
capture may use the separate restart-replay proof only when a fresh response
covers the exact retained support under its strict source/run/runtime checks;
see [RESTART_REPLAY_CORE_SEAM_CANDIDATE.md](RESTART_REPLAY_CORE_SEAM_CANDIDATE.md).
Insufficient replay is explicitly refused. Native IDs do not continue across
process runs, and arbitrary seamless restart recovery is not guaranteed. Accepted attempts without an exact live owner are shown as
`interrupted_or_unknown`, including same-process uncertain admission.

Source revocation/close attempts the publication fence for at most the existing
5 ms lock limit. On contention, cleanup remains visibly pending and new
publication/admission is refused. The existing owner drains that pending
transition. An already-guarded result transaction linearizes first; a completed
generation revocation is not claimed until the guard is acquired. Close cannot
report success while that transition remains pending.

Automatic structured output is provided through durable history. Its volatile
live-result and raw endpoints report `automatic_history_only`; availability
flags are false after proof retirement. No current-source authority is inferred
from a historical result.

## Dev24 qualification

The scheduling source, ownership diagnostic and packaging checks have separate
receipts. See [DEV24_FAIR_SCHEDULING_ZH.md](DEV24_FAIR_SCHEDULING_ZH.md). Historical
model observations above do not qualify the new scheduling policy or its real
reload cost. The first-failure ownership diagnostic records only fixed phase,
reason and exception categories plus already-observed scalar facts. It does not
clear the sticky fail-closed latch or infer a historical failure cause.
