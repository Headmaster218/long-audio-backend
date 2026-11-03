# Read-only incremental recorder span status

`python -m audio_backend.recorder_incremental_span_status` observes an existing,
finalized job created by `recorder_incremental_span_intake`. It verifies the
complete selected span against current page and session GET projections. It
never resumes ingestion, replays seal/finish, retries analysis, creates a session,
repairs a journal, or changes original deliveries, snapshots or local job files.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m audio_backend.recorder_incremental_span_status \
  --job-dir /private/intake-jobs/span-41-onward \
  --api https://your-backend.example \
  --session-id EXISTING_SPAN_SESSION_ID \
  --capture-id ORIGINAL_SPAN_CAPTURE_ID \
  --exclusive-writer \
  --deadline-seconds 120
```

Supply the exact API origin, session and capture explicitly. The command binds
these to the immutable span header, initial intent, all admissions and the v3
checkpoint before making any request. There is no target adoption or discovery.
Credentials use the existing transport mechanism and are not printed or saved.

The private parent/job directories, both existing locks, `capture.json`,
`initial-intent.json`, original delivery paths, all admitted snapshots and complete
`uploader.history` chains must still exist. Missing state is refused, not rebuilt.
The separate `recorder_span_status` command retains its original fixed-v2 1–8
input schema and limits. Neither command falls back to the other format.

## Results

The JSON schema is `recorder-incremental-span-status-v1`:

- `completed`: all expected work chunks have exact identities and coverage and
  are `completed` in both page and session projections, with full terminal
  progress. `analysis_completion_observed` and `analysis_completion_verified`
  are true subject to the non-atomic/exclusive-writer limitations below.
- `pending`: all histories and identities agree, but work is unfinished or the
  projections disagree on completion. Nothing is retried or changed.
- `blocked`: either projection reports failed, paused or cancelled work, or the
  capture is aborted. The report contains work IDs, positions and statuses, never
  private server error text. Inspect the existing job; this command cannot retry.
- `not_ready`: the journal is not finalized. No GET is issued and no partial
  completion claim is returned. Exact-job intake recovery is a separate action.
- `refused`: malformed/missing/changed history, incompatible target, unexpected
  work, lock contention, resource limit, cancellation or a transport error prevents
  verification. HTTP errors, including 429, are not retried.

Exit 0 means a completed, pending or blocked observation; inspect `status` rather
than treating exit 0 as completion. Refusal/not-ready JSON is on stderr and exits
2. The Python API raises instead; `NotReady` distinguishes unfinished intake.

`input_mapping` explicitly maps every immutable original sequence, epoch and
original frame interval to its cumulative zero-based span interval. Original
producer metadata is never rewritten. The `span` report retains original closure,
start/end/epoch/sequence and run identity. STOP is distinguished from INTERRUPTION
and the uncertain RECOVERED_TAIL singleton. ROTATION is not terminal closure.
Before/after gap durations remain unknown and EOS means `selected_span_only`.

`optional_receipts` is a separate pending/durable/gaps/ready projection. It does not
gate primary completion or assert optional-analysis completion or indefinite
receipt retention.

## Validation and read-only boundary

Both existing private locks are held through read-only descriptors. No uploader
or journal lifecycle constructor is called. The observer uses only detached,
pure v3 checkpoint validators, complete chain binding and authenticated archive
readers. It never calls journal load/save/recover/cleanup/census/budget or uploader
run/reconcile. No directory, lock, receipt or temporary is created.

Before the first GET, the observer:

1. Validates the exact immutable header/policy, target, initial intent and full
   bounded admission inventory.
2. Revalidates current original delivery bytes/metadata and every owned snapshot,
   with canonical type-sensitive receipt comparison and declaration adjacency.
   The original first sequence/epoch/frame origin is bound to the saved origin.
3. Requires complete, clean-EOF input history, all released pages, no active
   input/page/archive/retirement transition or spool, and matching final frontiers.
4. Authenticates both entire v3 chains against their checkpoint heads before using
   any archived binding. History is never accepted from a directory listing alone.
5. Checks original-to-normalized piece lineage and recomputes every piece PCM/WAV
   digest from its exact snapshot, one source and one page at a time. A valid
   chain containing a changed piece digest is not sufficient.
6. Requires actual terminal producer closure and saved terminal page EOS or the
   exact full-page capture-level finish form, without replaying either request.

GET-only remote checks cover the strict contract, exact capture/end/EOS, all
published pages, frozen page revisions and exposed source identities/keys/digests,
complete work geometry and contiguous span coverage. The session scan uses sparse
cursor validation through an explicit empty terminal response and must contain
exactly the same work IDs. Completed progress cannot be one frame short.
Capture and contract are reread; local controls, deliveries, snapshot identities,
history and piece hashes are checked again before returning any observation.

## Finite limits and caveats

The command supports the immutable job's limits up to 192 inputs, 48 hours of
mono 16 kHz PCM16, 8 GiB aggregate source WAV bytes, 588 pages and 16,464 work
chunks. New span intake defaults to 12 hours. Individual recorder delivery WAVs
retain their existing 512 MiB maximum. Explicitly supplied lower Python
`Limits.input_bytes`/`output_frames` limits are honored, not silently expanded.
The default status frame ceiling is 48 hours so a 12-hour job is not implicitly
reduced to the old two-hour observation limit.

Historical page records and piece PCM are streamed. At most 192 bounded admission
records and 16,464 work/chunk observation entries are retained, plus the v3
24,960-byte maximum digest-binding cache. There is no claim of constant total
memory. A pure read-only allocation census enforces the existing 32 MiB metadata
and 48 MiB state ceilings, including the uploader metadata allowance, without
requiring free disk for writes that status never makes. Each HTTP JSON response
remains capped at 512 KiB. Requests are bounded
by twice the frozen page cap plus the work cap plus five; decoded aggregate
responses are capped at 256 MiB. A larger response history refuses rather than
being truncated or labeled partially completed. Session pages request 16 rows.

One shared cooperative elapsed-time/cancellation/RSS guard covers the observation;
individual inspectors also share its remaining time and retain their 120-second
per-inspection maximum. Status rehashes media multiple times, and full history
validation includes bounded quadratic input comparisons. The 120-second default
is not a promise that an 8 GiB or 48-hour job finishes within it. Increase the
explicit finite deadline if needed. Filesystem syscalls are not forcibly
interrupted, and these controls are not hard OS-level I/O/RSS guarantees. Capacity
arithmetic and tiny/scaled fixtures do not qualify maximum-size runtime.

**This is a non-atomic observation under an explicit exclusive-writer assumption.**
Local locks cannot exclude direct API writers or a different operator. The server
has no transaction token spanning all GET projections. Status can change between
requests even when the final capture/contract reread matches. Therefore
`observation_atomic=false` and `concurrent_writer_safety_guaranteed=false`.
Frozen remote seal/finish keys are not exposed by GET, so
`remote_request_identity_verified=false`; status never replays them to infer an
identity. Target/producer authenticity, full-capture completeness, hardware or
physical continuity, and transcript/speaker accuracy are not established.

## Verification scope

`tests/test_recorder_incremental_span_status.py` uses individually admitted valid
Python-produced delivery files, real v3 immutable histories and real ASGI GET/DB
projections. Publication and worker completion rows are explicitly synthetic.
Coverage includes nine inputs, nonzero original coordinates, all terminal kinds,
full-page finish, multiple real-size pages, pending/blocked states, exact repeat,
local byte/time preservation, forbidden mutation-capable paths, changed chains,
missing state, type substitutions, malformed remote coverage/progress/cursors,
late local changes, locks and limits. The old v2 suite remains separate.
These focused fixtures do not qualify native inference, Android hardware,
long-duration runtime, or production deployment. Actual producer/worker
integration evidence, when available, must identify its own fixture and scope.
