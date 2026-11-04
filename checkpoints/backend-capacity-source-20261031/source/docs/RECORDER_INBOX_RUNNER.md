# Opt-in bounded local inbox runner

`audio_backend.recorder_inbox_runner` composes the existing incremental selected-span
intake, read-only status and private primary export. It handles **one explicitly
bound selected span and one initially empty strict session**. The session can be
operator-provided through local-only `configure`, or created by the separate,
explicit `initialize` command below. Runs never create sessions implicitly.
It does not discover a target, join epochs, infer gaps, retry
analysis, delete deliveries, choose the newest attempt, or run a daemon. The default `run` remains one-shot; `watch` is a separate,
explicitly bounded foreground command.
Importing the module and configuring a binding send no network requests.

## First use: explicitly create and bind one session

When no session exists yet, first publish one complete operator-selected delivery
into the private inbox, then use `initialize`. It creates exactly one session and
saves its ID in the private binding, so no API call or ID copy is needed by hand:

```sh
python -m audio_backend.recorder_inbox_runner initialize \
  --state-dir /private/runner/span-23 \
  --inbox-dir /private/received \
  --job-dir /private/intake/span-23 \
  --first-delivery /private/received/r-EXACT_VALID_ATTEMPT_NAME \
  --api http://127.0.0.1:8765 \
  --capture-id OPERATOR_SELECTED_CAPTURE \
  --output-dir /private/exports/span-23-first \
  --exclusive-writer --acknowledge-span-local-closure \
  --deadline-seconds 30
```

All path/privacy, complete-delivery and selected-span rules below apply. An
optional `--session-name` is sent to the chosen API and retained only in private
initialization evidence; it is not echoed in the command result. Authentication
uses `AUDIO_API_KEY` only in memory/headers. Non-loopback origins require HTTPS.
No uploads, intake, EOS, export, background watch or daemon are started by this
command. After `status: configured`, use the existing explicit `run` or `watch`.

Before the single `POST /sessions`, initialization validates local paths and the
exact first delivery, checks API health, and fsyncs an immutable request intent
binding endpoint, session name, capture/span and selected delivery. The current
API exposes the strict contract only after a session exists: initialization then
verifies the returned session identity, strict recording contract, no existing
chunks/capture, and unchanged selected input before completing local binding.
Health alone is not strict-contract or native-inference qualification. An
incompatible post-creation contract leaves a confirmed but unused session, never
an upload or silently accepted nonstrict session.

A bounded invocation makes at most six HTTP attempts and one mutation, with no
HTTP retries. The deadline defaults to 30 seconds (allowed 1–120), is shared
across initialization, and is checked before effects and between existing bounded
local validations. Individual filesystem/network calls remain cooperative, not
an OS kill guarantee. The runner lock excludes concurrent initialize/run/watch.

If interrupted, preserve the state and repeat the **same initialize arguments**
with `--resume`. A confirmed saved session response permits GET-only revalidation
and completion of a partial local binding, without creating another session.
Even an already-completed binding rechecks session identity and emptiness with
GET-only requests on explicit resume. Before any upload, ordinary intake still performs its own empty-session
checks. Once intake has started, use `run`/`watch`, not initialization.

If the POST might have been sent but its response was not durably saved, resume
refuses with `runner_session_creation_uncertain_no_retry` **without HTTP**. A crash
between the durable attempt marker and the actual send is conservatively included.
Do not delete/edit receipts or rerun into a fresh directory to disguise that state.
The operator must investigate server-side outcome separately; this tool does not
search for sessions, adopt an unconfirmed one, retry non-idempotent creation,
remove an orphan session or roll back a server mutation. Failed health checks
before the POST marker can be explicitly resumed safely.

`configure` remains the network-free alternative when the operator already has
an exact empty strict session ID.

## Set up one binding, then explicitly run

Keep the inbox, runner state, intake job and optional export in disjoint trees.
Their parents must already exist and be owner-only private directories. The inbox
must also be private. State/job/export must be outside a Git checkout, so private
originals and exported text cannot accidentally enter a source backup. Choose
fresh state/job/export paths; existing jobs are not adopted.

The first delivery below is an **operator-selected exact attempt directory**.
It must already contain the complete validated three-file delivery. Setup derives
the producer capture/run hashes, epoch, first sequence, frame origin and format
configuration from that delivery. It does not search for an initial attempt or
rewrite producer declarations.

```sh
python -m audio_backend.recorder_inbox_runner configure \
  --state-dir /private/runner/span-23 \
  --inbox-dir /private/received \
  --job-dir /private/intake/span-23 \
  --first-delivery /private/received/r-EXACT_VALID_ATTEMPT_NAME \
  --api http://127.0.0.1:8765 \
  --session-id EXISTING_EMPTY_STRICT_SESSION \
  --capture-id OPERATOR_SELECTED_CAPTURE \
  --output-dir /private/exports/span-23-first
```

The name in this example is a placeholder: a real directory name must match the
recorder's `r-<64 lowercase hex>-<32 lowercase hex>` contract. Configuration
creates only a private immutable binding and the exact first selection intent.
It performs no intake and does not prove that the server session is empty; the
existing intake verifies emptiness before initial admission. The operator must
control the session/capture, runner and intake job exclusively. Authentication
continues to use `AUDIO_API_KEY` in memory through the existing transport, never
inside configuration or receipts. Non-loopback origins require HTTPS.

Each invocation does a finite reconciliation and exits:

```sh
python -m audio_backend.recorder_inbox_runner run \
  --state-dir /private/runner/span-23 \
  --exclusive-writer --acknowledge-span-local-closure \
  --max-actions 8 --deadline-seconds 120
```

Repeat `run` manually after further delivery arrivals or to resume an interrupted
invocation. One-shot runs do not poll automatically. No command installs a
service, deploys anything, or starts a permanent process. `waiting_for_input` leaves a missing next sequence
unfilled. `action_limit` means the bounded batch stopped, not that analysis is
complete. `awaiting_finalize` means a genuine terminal producer input is staged
but no EOS was authorized.

Finalize and export require explicit per-run flags:

```sh
python -m audio_backend.recorder_inbox_runner run \
  --state-dir /private/runner/span-23 \
  --exclusive-writer --acknowledge-span-local-closure \
  --finalize --export --max-actions 8 --deadline-seconds 120
```

`--finalize` authorizes EOS for this selected span only, and only after STOP,
INTERRUPTION or a singleton RECOVERED_TAIL. A latest ROTATION cannot finalize.
`--export` authorizes the one configured fresh private output destination after
verified primary completion. Pending/blocked status is returned without an export
attempt. A run after finalization without `--export` performs bounded GET-only
status observation. A run with both flags can stage incoming inputs, finalize,
observe status and export if the finite action budget permits; it does not wait
indefinitely for future inputs or analysis. A pending finalization must be resumed
with `--finalize` again, never by an implicit unapproved EOS.

## Optional finite foreground watch

To process arrivals and observe pending primary analysis within **one finite
invocation**, explicitly choose `watch` instead of `run`:

```sh
python -m audio_backend.recorder_inbox_runner watch \
  --state-dir /private/runner/span-23 \
  --exclusive-writer --acknowledge-span-local-closure \
  --poll-seconds 5 --max-polls 256 --max-actions 256 \
  --max-requests 1024 --max-network-writes 512 --deadline-seconds 600
```

This command holds the **same runner lock** for its whole lifetime, including
sleep, so another compliant one-shot run or watch cannot interleave. The original
configuration, session/capture identity, exact paths, state directory and lock
identity remain checked across waits. There is no target discovery, new session,
background installation, automatic restart, or change to the configured span.
The operator must still exclude direct API writers and standalone intake tools.

Only `waiting_for_input` and verified `pending` primary status repeat by default.
A poll is one inventory/reconciliation pass, including an immediate productive
pass. A clean wait sleeps before the next pass. Each intake, finalization, status
observation **and export** consumes a separate action in watch mode. All polls,
actions, attempted requests, network mutations and decoded JSON response bytes
share one invocation-wide budget and one monotonic deadline; none reset after a
sleep. An export needs a remaining action after its completion observation.

`--finalize` and `--export` are optional, frozen permissions for this invocation,
with exactly the same genuine-terminal and fixed-destination restrictions as
`run`. Without `--finalize`, terminal input stops at `awaiting_finalize`. Without
`--export`, verified primary completion stops at `completed`. A gap or latest
ROTATION never grants closure. A completed export is revalidated, not regenerated.

Every conflict, quarantine, changed original/configuration, blocked worker,
authorization gap and uncertain transport result stops the watch for explicit
operator investigation and a new invocation. Watch does **not** repeat failed
HTTP requests inside an intake helper either: failed/uncertain requests stop it
immediately, except an expected GET 404 that the existing reconciliation uses to
identify an absent resource. This stricter behavior is watch-only; one-shot exact
reconciliation remains unchanged. A crashed process never restarts itself.

### Explicit marker-last waiting

The default remains conservative: even a new partial delivery stops watch.
Operators whose publisher writes the completion marker last may explicitly add
`--wait-for-marker-last`. It permits a narrower `waiting_for_publication` state:
only an **unselected**, marker-absent attempt directory containing a subset of
`audio.wav` and `segment.v1.bin`, both regular bounded files, may wait. Empty
unpublished directories are allowed. Extra files, symlinks, excessive sizes,
observed replacement/mutation, a present invalid marker, or a missing/changed
selected original remain fatal. Complete same-ordinal conflicts and all frozen
originals are validated before deciding to wait.

If any such unpublished attempt exists, the **entire pass performs no HTTP,
intake, finalization or export**, even when other complete arrivals are ready.
The same poll/deadline budgets apply. Later publication is fully validated before
selection. This is not a proof of publisher identity or arbitrary FTPS robustness;
active uploads that change during inspection may still require explicit resume.
Do not enable it for an untrusted shared inbox.

### Watch limits, cancellation and exit states

- Defaults: 256 polls, 256 helper actions, 1,024 HTTP requests, 512 HTTP mutations,
  256 MiB decoded JSON response bytes, five-second intervals and a 120-second
  deadline. All are per **whole watch**, not per poll.
- Hard maxima: 4,096 polls/actions, 65,536 requests, 8,192 mutations, 256 MiB decoded
  response bytes, 3,600-second intervals and an 86,400-second deadline. Minimum
  interval is 0.1 seconds. The existing 256-entry/8-GiB inventory cap applies to
  each pass and also bounds complete plus unpublished WAV sizes in marker mode.
- `--max-network-bytes` bounds successfully decoded JSON responses, not outbound
  WAV data, error bodies, headers or total wire traffic. Before each request watch
  reserves a full 512-KiB response allowance, so it may stop early rather than
  exceed that decoded-response budget. Outgoing traffic retains the request,
  mutation, source and spool limits of the existing intake.
- SIGINT/SIGTERM request cooperative cancellation; guarded sleeps check at least
  every 0.1 seconds. In-flight bounded network/media operations and individual
  filesystem syscalls may take longer to return. The deadline is cooperative,
  not an OS process-kill guarantee. Evidence is retained for explicit resume.
  Cancellation stops only this foreground watch; it does not cancel or undo
  already accepted backend work.
- `poll_limit`/`action_limit` exit 0 and report `last_status`; they are not
  completion. `awaiting_finalize`, `input_capacity_reached`, `blocked`, `completed`
  and `exported` stop immediately. Inspect `status`, not just the exit code.
- Errors return `unresolved`; time exhaustion returns `deadline_reached`; both
  exit 2. Cooperative cancellation returns `cancelled`; CLI SIGINT exits 130 and
  SIGTERM exits 143. Results include cumulative `polls`, `actions`,
  `helper_actions`, `network_requests`, `network_writes` and
  `network_response_bytes` when the run context has initialized.
- Watch reports `automatic_intake_enabled: true`,
  `continuous_polling_enabled: true`, `invocation_bounded: true` and
  `daemon_installed: false`. Those describe this explicit foreground invocation,
  not a persistent permission or service. Budgets are not renewed automatically.

## Selection, duplicates and quarantine

- Only the exact next original sequence is admitted. Full origin/configuration,
  declared adjacency and trusted-seam rules remain those of incremental span
  intake. Nonzero sequences and frame origins are retained. New runs or epochs
  are not concatenated.
- The first complete unique candidate can be selected for the next sequence.
  If multiple unselected attempts have identical content, selection requires
  `--select-attempt EXACT_ATTEMPT_DIRECTORY_NAME`. No lexical, timestamp or
  newest-attempt preference is used. CLI setup already selects its explicit first
  path; the Python `configure(config=...)` API can instead wait for the first
  arrival with an explicitly supplied full `span_origin`.
- Duplicate identity includes the source, revision, original metadata and media
  hashes, frame geometry and all validated declarations. Only the transport
  attempt nonce is excluded. After selection, equal-content alternate attempts
  are ignored without new admissions, but the **original selected path must
  remain available and unchanged**. An alternate copy never replaces it.
- Differing content at the same selected-or-later original ordinal blocks the
  entire invocation, even if an operator names an attempt. Incomplete, changed,
  inaccessible or invalid attempt directories also block it because their
  capture identity cannot be trusted. They are logically quarantined, not moved
  or removed. The operator may investigate and separately retain an alternative
  outside the active inbox, then repeat; the runner never performs that action.
- A gap is not terminal closure. Inputs after a terminal declaration are not
  admitted. Valid unrelated captures and original sequences before the explicit
  selected start are not automatically imported.

Inventory is a bounded non-atomic observation. Files can arrive after it, and a
read does not make a publisher immutable. The original path is revalidated before
intake; every pending input is rechecked before HTTP, and every outbound mutation
checks the exact frozen local admission prefix. Existing intake revalidates its
snapshots and journal. A subsequent observed conflict stops further progress.
There is no guarantee against a malicious same-user process racing every syscall
or a direct API writer outside these local locks.

## Durable state and restart

Runner state uses immutable canonical private files, not an overwritten cursor.
Each exact path/report intent is fsynced before intake, and its small completion
receipt is saved only after intake's complete exact-job validation returns. The
initial intent is bound to the immutable full configuration. Existing evidence is
positively fsynced again before a retry. The runner lock serializes local runs;
the existing intake/status/export helpers retain their own two locks.

A crash after a successful intake but before its runner receipt resumes the exact
original input through the existing adapter. A pending first input uses `--start`
only if the job is absent; a durable incomplete initial job uses the existing
narrow initial recovery path only when no uploader checkpoint exists. Existing
checkpoints use the original latest-input reconciliation, including lost PUT/seal
responses. A missing checkpoint after completed admission is refused, not rebuilt.
Initial crashes before sufficient durable intake intent, partial append snapshots,
missing origins, malformed controls, unknown ledger entries, a changed target and
external unjournaled intake progress remain preserved and refused. This runner
does not broaden the existing engine's recovery authority.

Finalization has its own durable intent before calling the existing exact finalize
path. No new input is selected once finalization begins. Lost completion receipts
reconcile that exact finalization instead of starting another capture.

Export has one durable intent and one fixed fresh destination. If a crash leaves
**any existing output but no runner export receipt**, the next run reports
`runner_export_publication_uncertain_preserve_output`. Even an apparently complete
artifact pair is not automatically adopted, overwritten, deleted or re-exported.
Inspect it separately with the documented export manifest rules. Choosing a new
export attempt is a separate explicit use of the standalone exporter; the runner
never silently rotates destinations. If there is a completed runner receipt,
repeat runs check the private manifest and both bounded artifact hashes before
reporting `exported`; that is verification of the retained local publication, not
an observation that the server can never change.

## Bounds and results

- Frozen span caps: 12 hours / 192 inputs by default; setup may explicitly select
  1–48 hours and 1–192 inputs. Existing source-slot, spool, disk reserve and snapshot
  limits still apply. These are software capacities, not long-duration runtime
  qualification.
- Each inventory counts **all** directory entries and caps them at 256, avoiding
  the inventory tool's default 128-entry limit for a 192-input span. Duplicate and
  ignored entries consume this capacity too. It reads at most 8 GiB of WAV data
  per inventory; each individual delivery remains capped at 512 MiB. Inventory
  is repeated before each action, so total hashing is not constant-time.
- Each run defaults to 1 action, 1,024 HTTP requests, 512 network mutations and a
  120-second shared deadline. Explicit maxima are 195 actions, 65,536 requests,
  8,192 mutations and 86,400 seconds. An action is one intake, one finalization,
  or one completed-job observation with optional export. A failed request consumes
  its attempt budget. Status/export add no HTTP retry behavior.
- One shared transport caps decoded responses at 256 MiB total and 512 KiB per
  response. Existing helper-specific limits also apply. Status and export can
  require a larger explicit finite request/deadline budget for large jobs.
- Metadata remains bounded to 16 KiB per immutable runner file and at most two
  files per input plus six controls. Underlying local writes remain bounded by
  the existing intake/uploader capacities; the mutation counter counts HTTP
  mutations, not filesystem syscalls. The shared cooperative guard covers media
  inspection, helpers and completed-export rehashing. It cannot forcibly interrupt
  individual filesystem syscalls.

Exit 0 can mean configured, waiting, action-limited, pending or blocked. Inspect
`status`, not just the exit code. Errors exit 2 with a redacted reason and a
preserve-evidence instruction. Primary completion is reported separately as
`primary_completion_observed`; no result claims capture completeness, physical
continuity, recognition accuracy, global speaker identity or native qualification.
Artifacts remain the existing **primary-baseline-v1** export, with timestamps and
all its original caveats. Nothing is uploaded to Git, shared or deployed.

## Verification scope

Model-free tests cover local-only setup, bounded batches, exact first and later
selection, duplicates, conflicts, missing arrivals, private state, limits,
original preservation, explicit EOS, a fresh subprocess `os._exit` after intake
before the runner receipt, and interrupted output preservation. Independent
review tests exercise the actual strict ASGI/SQLite source admission routes,
including a lost successful PUT response and restart at the seal boundary; they
stop before native execution. Unit publication stubs are explicitly synthetic
and do not qualify transcript generation. Any actual producer/native/loopback
qualification must be reported separately with its fixture and stopping limits.


Watch tests separately exercise delayed arrivals, marker-last waiting/refusal,
shared cumulative budgets, terminal statuses, uncertain-request stop, exact crash
resume, exclusive locks and real subprocess SIGINT/SIGTERM cancellation. These
are model-free orchestration tests, not an additional native or deployment
qualification.

A prior one-shot [public-speech native loopback qualification](../qualification/LOCAL_INBOX_LOOPBACK_NATIVE_20261103.md)
passed for the bounded one-span path: nine unchanged host-Java deliveries,
controlled incremental local arrivals, clean runner reopen, duplicate/conflict
refusals, explicit terminal finalization, five real primary ASR units and two
identical private exports over actual loopback HTTP. The independent closed-copy
audit verifies source/native/export linkage and clean process shutdown. This is
not a deployed or always-on receiver, Android/FTPS test, accuracy score,
whole-capture proof or native-service restart qualification.


A separate [finite-watch native qualification](../qualification/LOCAL_INBOX_WATCH_NATIVE_20261103.md)
passed in one foreground process: nine genuine host-Java public-speech deliveries,
actual marker-absent observations with zero HTTP activity, final producer STOP,
real native completion, five nonempty primary ASR units and exact repeated private
exports. Its controlled atomic local publication is not ordinary FTPS STOR.
The independent closed-copy audit verifies original producer/native/export
linkage, request boundaries and safe shutdown. It covers one short selected span,
not an installed daemon, deployment, long endurance, recognition accuracy,
speaker identity or whole-capture completeness.

## Receiver-owned publication boundary

For ordinary live FTPS writes, use the opt-in [local completed-delivery publisher](RECORDER_RECEIVER_PUBLICATION.md) with a separate raw staging tree and a frozen runner receiver binding. Its shared inventory/publication lock preserves the existing strict inventory checks while allowing arrivals during a watch. Marker-last alone is not a live-transfer completion protocol. This is local library/CLI support, not an installed FTPS receiver.
