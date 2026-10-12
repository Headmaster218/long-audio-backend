# Explicit volatile Handy MOSS jobs (rc3.dev5)

This source integration is OFF by default. It lets an operator explicitly run
one experimental diagnostic against an already-current selected native-owned
support. It does not replace the primary transcript, rank alternatives, assign
global speaker identity, enroll a speaker, align words or complete a long input.
Preparation of this source does not establish test execution or publication.

## Opt-in and supported configuration

Enable native-owned support capture and set both `--moss-jobs` and
`--moss-jobs-model` to the explicit local model file. The corresponding settings
are `moss_jobs_enabled=False` and `moss_jobs_model_path=None` by default. Both are
excluded from inference configuration. Supplying only a model does not enable
jobs, and an enabled configuration without the explicit model is invalid.

Enabled MOSS execution is Linux x86-64 CPU only. This integration requires the
ordinary legacy Worker, `start_worker=True`, native-owned support, Nemotron
native diarization and `overlap_backend=none`.
A configured fragment provider factory is rejected. This is not a bridge between
fragment provider ownership and MOSS ownership. Post-start factory replacement
is unsupported and cannot silently authorize optional work.

The fixed profile is `handy-moss-q8_0-cpu-experimental-v1`, described in
[MOSS_HANDY_EXPERIMENTAL.md](MOSS_HANDY_EXPERIMENTAL.md). No HTTP parameter can
select a model, profile override, path, executable, prompt, language, runtime or
thread count. Configuration creates no model, installs nothing and downloads
nothing. The separate child verifies the fixed profile, runtime and complete
local model before its one possible invocation. Existing MOSS profiles and
native provider algorithms are unchanged.

One eligible support is original PCM16 mono 16 kHz, at most 320,000 frames,
20 seconds and 640,000 PCM bytes. It must already exist as a current selected
support in the cache with an exact capture epoch, revision and support ID.
There is no re-decode, extra primary native call or arbitrary audio upload to a
MOSS route. The primary long-recording pipeline remains unrestricted by this
optional short-input limit.

## One job and explicit epochs

There is one occupied execution/storage slot, one optional service thread and
zero backlog. Submission reserves the shared execution permit synchronously;
primary intent, uncertain ownership, resources, a busy slot or stale evidence
can reject it without queueing or invoking a model. An accepted job belongs to
the manager even if its submitting HTTP request disconnects.

A cleaned terminal record can be retired by the next fully validated submit.
Live or uncertain ownership cannot be evicted. Each accepted job expires at
900 monotonic seconds from acceptance. Reads and long runs do not extend that
deadline. Expiry hides result/raw and invalidates publication, but cannot remove
an unresolved claim, release the execution permit or erase cleanup obligations.

Discover `jobs_epoch` with list before submitting. Mutation requests require the
current process-local epoch. An `Idempotency-Key` has 1–64 ASCII letters, digits,
underscores or hyphens. A key binds its session and canonical request. Repeating
the same key/request returns its job; changing the request gives 409. A retired
or expired accepted key gives 410 and is never replayed. At most 64 accepted
keys, including the live record, exist per epoch in a bounded tombstone ledger.
The 65th distinct admission gives 429 `epoch_capacity`; keys are never silently
evicted. Rejections consume no accepted-key slot. A restart creates a new epoch
and loses volatile artifacts; it does not adopt or resume an old job.

## API

The existing shared Bearer middleware applies. Every response in this route
family, including authentication, parser, routing and capacity errors, is
`private, no-store`. Error bodies contain bounded reason codes, never server
paths, process IDs, native transcript excerpts or arbitrary exception text.

- `POST /sessions/{sid}/native-owned-supports/{cid}/moss-jobs`: exact JSON fields
  `jobs_epoch`, `expected_capture_epoch`, `expected_revision`, `support_id` and
  `profile`; required `Idempotency-Key` header. 202 means reservation, slot and
  accepted-key ownership were handed to the manager. It does not mean a model
  succeeded or that output is complete.
- `GET /sessions/{sid}/moss-jobs`: bounded metadata/tombstones, with canonical
  decimal `after` (exclusive, initially 0), `limit` (1–20, default 20) and
  `expected_jobs_epoch`. Continuation requires the exact epoch. At most 64 total
  entries and 32 KiB per page; no transcript/raw content appears in summaries.
- `GET /sessions/{sid}/moss-jobs/{job_id}`: at most 16 KiB of whitelisted state,
  binding, retention, cancellation and cleanup metadata.
- `GET /sessions/{sid}/moss-jobs/{job_id}/result`: at most 256 KiB of immutable
  diagnostic JSON after positive cleanup and fresh current-source authorization.
- `GET /sessions/{sid}/moss-jobs/{job_id}/raw`: at most 128 KiB of exact retained
  `native.raw_text` UTF-8 bytes, after cleanup and current-source authorization.
- `POST /sessions/{sid}/moss-jobs/{job_id}/cancel`: JSON containing only the
  required `jobs_epoch`. Cancellation invalidates publication immediately. If
  cleanup is blocked, it may request one serialized cleanup-only retry; it never
  retries inference and reports pending until positive cleanup is established.

Mutation bodies are bounded to 1,024 bytes before parsing, one concurrent parser
and a cooperative two-second receive timeout. Content type is exact JSON;
compression, duplicate/unknown fields, nonfinite values, nesting, booleans used
as integer revisions and noncanonical identifiers are rejected. Query bytes are
at most 1,024 with four fields; escapes, non-ASCII, duplicates, unknown keys and
noncanonical decimals are rejected before source/controller use.

Status meanings: 200 read/repeat; 202 accepted or cancellation pending; 404
unknown/deleted session or job; 409 disabled, unsupported, busy, mismatch,
not-terminal or raw unavailable; 410 known expired/retired job/key; 429 epoch or
mutation capacity; 503 unavailable evidence, resource sampling, cleanup or
response capacity. There is no discard, queue-control, install or general
execution endpoint.

## Exact output and source authorization

Raw is the exact native text encoded as UTF-8, not a reconstructed SDK envelope.
It has `text/plain; charset=utf-8`, exact Content-Length, nosniff, raw-kind and
SHA256 metadata. The endpoint adds no BOM or newline, performs no decode/reencode
and never describes a truncated prefix as exact. Missing, invalid-Unicode,
oversize and unavailable native output remain explicit. Exact empty output is
distinct from unavailable. A failed diagnostic may retain exact raw only after
cleanup and fresh authorization; cancellation/source invalidation suppresses
publication even if a late native return looks successful.

EOS, generated token count and native completion remain unknown. Even a normal
return has `complete_content=false`; model-local phrase times are unverified.
Historical source/profile/support identities describe that invocation, not the
currently installed primary runtime or a continuously current source.

The internal DispatchLease pins one immutable support and matching PCM with a
separate maximum 768 KiB charge. Acquisition, post-reservation dispatch,
post-cleanup publication and result/raw reads each require fresh bounded source
and change-feed validation. One phase shares the existing 500 ms cooperative
budget, 1,024 feed rows, 256 KiB projected SQL, 150,000 VM steps, 40 MiB source
reads and 5 ms lock acquisition limits. The first SELECT is the database
authorization point, followed by final cache identity/expiry checks. No database
transaction or cache lock spans model work, thread joins or cleanup.

Deletion, source controls/change, expiry and epoch closure invalidate publication
and cancel a live dispatch. A retained pin remains charged until released; PCM
is released after writing child input. Direct deletion and another Store for the
same database notify the same registered jobs owner after the unchanged primary
commit. A pending optional obligation is reported as `moss_jobs_cleanup_pending`
when applicable. Process cleanup proof and pending retained-memory cleanup are
reported separately. A cleaned deleted record is discarded only after its pin
has released; bounded lock or purge uncertainty remains pending. Source-support
expiry also expires cleaned content. Already authorized responses may finish
under their ASGI lease after a later delete; this is not a forensic memory-erasure guarantee.

## Primary precedence and ownership

The permit covers a full claimed legacy chunk, including analyzer lifetime,
native requests, extraction, commit and original cleanup. It also covers upload
ffprobe; nested extraction reuses the enclosing span. Primary-primary concurrency
and original primary claim/SQL remain unchanged. A protected claim probe prevents
optional admission during the existing claim; an empty poll does not cancel a
running optional job. A real claim publishes primary intent before process
creation, cancels optional publication and waits without marking the chunk failed
merely because it is waiting. Pause/cancel/stop can withdraw it before execution.

Transient primary handles need positive original-handle exit and no known live
observed descendants. A native child may remain resident only with the same
current Worker/coordinator ownership, identity, completed request and idle state.
A normal permissive primary close return is not proof. Uncertain primary
observation makes optional admission unavailable while later primary work can
continue. Original primary cleanup is not replaced, and MOSS cannot signal a
resident or otherwise unowned primary child.

MOSS cleanup is stricter: original-handle wait/reap and no known live owned
descendants are required before releasing its permit. Unknown or failed cleanup
retains the slot, permit and owner marker, so waiting primary work cannot spawn.
Cancel permits bounded cleanup retry only. No timeout, daemon disappearance,
expiry or lack of output converts uncertainty into execution permission. The
receipt remains observed-owned-only with `discovery_complete=false`, not a proof
of exhaustive native descendant discovery.

Preemption is cooperative at driver polls, normally no more than 50 ms apart
once the wait loop is running, followed by the existing finite cleanup phase.
Slow filesystem/OS calls can delay detection; neither 50 ms nor the 60-second
cleanup budget is a hard handoff guarantee. Compute remains capped at 300 seconds
with no model retry/replay.

## Resource and startup fences

Keep the existing MOSS caller-plus-owned 3.5 GiB cap, 2 GiB host floor, 5.5 GiB
disk floor and 2 MiB scratch cap. A strict shared sampler also accounts for the
caller and every observed service descendant, including resident native memory,
under the configured whole-service limit. Each sample is bounded to 4,096 process
rows, 128 identities and a cooperative 250 ms. Denied scans, identity uncertainty
or overflow fail optional admission/cancel optional work.

Admission conservatively reserves resident primary descendants plus a 3.5 GiB
caller/candidate envelope and 16 MiB wrapper reserve. Host available memory must
cover the unallocated envelope plus 2 GiB; shared disk reserve is at least the
larger existing reserve or 5.5 GiB. These are ceilings, not measured model demand.
Checks occur at reservation, before spawn, before child admission and driver
polls. Sampling failure during cleanup cannot abort ownership cleanup or justify
release. There is no new global process supervisor or kernel containment claim.

A fixed private mode-0700 namespace outside tmp contains one slot and the dev4
five-file allowlist. Logical and allocated scratch remain at most 2 MiB. The
bounded owner context includes job/capture epoch bindings. No per-job directory
backlog or growing log is created. A fresh controller never adopts a nonempty
slot; normal shutdown discards only positively cleaned, identity-checked files.
Abandoned claims are not automatically deleted and unknown PIDs are never killed.

Even with jobs OFF, startup acquires InstanceLock and then performs an
authoritative bounded no-follow ownership check before any configured fragment
factory initializer, tmp cleanup, recovery or Worker startup. A preliminary
preconstruction check is diagnostic only. Unresolved ownership refuses startup
without destroying evidence. Never-enabled ordinary legacy startup creates no
optional namespace/thread/model and adds no provider call or SQL.

On a platform without descriptor-relative no-follow primitives, including the
mocked Windows branch, OFF startup may pass only a bounded, noncreating absence
check. Existing ancestors must be directories without symlink/reparse evidence;
any existing or uncertain MOSS namespace refuses startup and calls for inspection
on a supported Linux host. It never adopts, scans, deletes or signals old optional
ownership on that fallback. Enabled jobs reject non-Linux/missing-primitives
configurations before creating a namespace. Mocked-platform fixtures do not
establish actual Windows qualification.

There is one deliberate OFF ordering exception for a configured fragment factory:
create_app defers its initialize hook until after InstanceLock and the ownership
fence. The original hook and schema SQL execute once with later timing. Direct
FragmentHTTP construction retains immediate initialization by default. Success
is initialize-once; failure preserves the exception and is not retried. A failure from the retained original shipped CPUFactory initializer can release
the lock when the other cleanup conditions hold. Caller-supplied initializer
exceptions provide no process-cleanup proof; uncertain ownership stays blocked. Existing fragment processing/cleanup
algorithms and constructor SQL bodies are unchanged.

Shutdown closes admission and invalidates optional publication before stopping
primary work. The old Worker join timeout is not evidence of optional cleanup;
unresolved optional ownership keeps the InstanceLock referenced and reports
`shutdown_blocked`.

## Memory and qualification boundary

The existing two-response/1.5 MiB pool is shared across support and jobs routes.
Jobs result/raw/detail/list reserve 768/256/128/256 KiB respectively before
copying or retaining response material; mutations reserve a reply lease before
acceptance. Outer ASGI completion, disconnect or send failure releases exactly
once, without transferring accepted-job ownership to the request.

One terminal/controller projection is at most 256 KiB, raw 128 KiB, immutable
result serialization 256 KiB and bounded metadata/ledger 64 KiB, under a 1 MiB
logical retained-result cap separate from the dispatch pin and existing 4 MiB
support cache. Byte accounting is not Python RSS; whole-service sampled RSS is
the real observed limit. The incremental wrapper envelope is at most 16 MiB.

The current source gate pins the exact 26-path composed inventory, version-only
metadata, immutable published dev4 ancestry, original primary SQL, unchanged
provider/cleanup/profile boundaries and narrow deferred-initialization seam.
The dev4 gate is an immutable historical proof, not a validation of this tree.
External source/index/seal verification, test results and publication must each
be separately reported against exact frozen bytes. No acoustic, live native
MOSS service concurrency, long optional recording, full-suite, Windows/Vulkan
or production-deployment qualification follows from model-free fixtures.
