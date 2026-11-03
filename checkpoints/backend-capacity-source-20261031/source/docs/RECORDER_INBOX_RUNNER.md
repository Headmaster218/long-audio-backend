# Opt-in bounded local inbox runner

`audio_backend.recorder_inbox_runner` composes the existing incremental selected-span
intake, read-only status and private primary export. It handles **one explicitly
bound selected span and one operator-provided, initially empty strict session**.
It does not create sessions, discover a target, join epochs, infer gaps, retry
analysis, delete deliveries, choose the newest attempt, or run a daemon.
Importing the module and configuring a binding send no network requests.

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

Repeat manually after further delivery arrivals or to resume an interrupted
invocation. No installed service, automatic polling, deployment or permanent
process is introduced. `waiting_for_input` leaves a missing next sequence
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
