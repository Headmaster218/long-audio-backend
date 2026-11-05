# Finite automatic analysis plan

`audio_backend.recorder_analysis_plan` runs a finite, predeclared chain of up to
seven analysis handoffs over at most eight prepared receiver generations. It
composes the same discovery, logical-boundary, export and exact initialization
receipts as the standalone one-handoff command. That command retains its public
arguments and behavior.

An intermediate edge stops at a verified next-session link. The following edge
then drains that session, without waiting for an artificial terminal event or
requiring the operator to start its runner separately. The last runner drains
available contiguous input and either exports its real STOP or reports that the
plan is exhausted at a ROTATION. No producer marker is rewritten.

## Private plan

Initialize the first runner and prepare the receiver generations and immutable
routing files beforehand. Each edge's route must select the immediately next
generation, with the same ordered retained generation list and endpoint settings
across every route. The receiver itself remains separately controlled. Files do
not prove which route a live receiver uses.

Create a private plan file (mode 0600), outside all work trees. Example:

```json
{
  "schema": "recorder-finite-analysis-plan-v1",
  "state_dir": "/private/runners/span0",
  "edges": [
    {
      "route_config": "/private/route-1.json",
      "next_state_dir": "/private/runners/span1",
      "next_job_dir": "/private/jobs/span1",
      "next_output_dir": "/private/exports/span1",
      "next_capture_id": "analysis-span-1",
      "next_session_name": "Analysis span 1"
    },
    {
      "route_config": "/private/route-2.json",
      "next_state_dir": "/private/runners/span2",
      "next_job_dir": "/private/jobs/span2",
      "next_output_dir": "/private/exports/span2",
      "next_capture_id": "analysis-span-2",
      "next_session_name": "Analysis span 2"
    }
  ]
}
```

The initial runner must have a receiver-publication binding and export directory.
Future state/job/export paths must be unused for a new plan. Their parents must
already be private directories. The initial and future work trees, all retained
raw/inbox/publication-state trees, and the plan control directory must be mutually
disjoint, canonical, private and outside Git. Plan and route files cannot lie
inside these work trees. The control directory must be separately precreated,
private (0700), and empty before its first use.

```sh
python -m audio_backend.recorder_analysis_plan \
  --plan-file /private/analysis-plan.json \
  --control-dir /private/analysis-plan-control \
  --acknowledge-finite-plan --exclusive-writer \
  --acknowledge-span-local-closure \
  --deadline-seconds 600 --phase-max-actions 64 --phase-max-polls 64 \
  --max-total-actions 32768 --max-total-polls 4096 \
  --poll-seconds 1 --busy-read-retries 3 --export-recovery-attempts 3
```

Those two retry options are optional and default to zero. They retain the
existing typed busy-GET and GET-only export revision recovery rules. They never
permit mutation retries. No service is installed or started by this command.

An optional `identity` object may contain exactly these three fields:
`preserve_reviewed_identity_group`, `identity_generation`, and
`identity_policy_sha256`. This pins the already reviewed identity bank through
each existing initializer. It does not enroll a speaker, change a threshold or
prove acoustic continuity. Without this object, no identity preservation is
claimed.

## Validation and recovery

The entire structural plan is validated before creating its durable intent or
performing any chain HTTP effect: all routes, retained bindings, forward adjacent
selections, endpoint settings, future configuration templates and global path
separation. Future audio need not have arrived yet. Current/selected-next audio
is inspected at its edge; partial or ambiguous publication is not permission to
skip to a later generation. A unique candidate is selected once, then its exact
immutable identity is pinned by the existing boundary receipts.

A private control lock is held during orchestration. The plan intent pins the
plan file's exact bytes, initial configuration and every route file. These pins,
control/lock identity and planned destination configuration fields are checked
across waits and before/after delegated work, including zero-network repeats.
Do not reformat the plan, overwrite a pinned route, remove controls or change
existing destinations to force progress.

There is no authoritative mutable hop cursor. An explicit later invocation
starts from the initial runner and verifies existing boundary, export and session
initialization receipts. A fully completed link can be verified without another
POST. A confirmed creation with unfinished local binding follows the existing
initializer recovery rules. An uncertain creation intent is refused before any
chain HTTP; it is not deleted or replayed. Unknown mutation outcomes latch off
further mutation dispatch for that invocation. No unresolved or uncertain phase
is automatically resumed by this coordinator.

Reuse the same plan file and control directory for explicit recovery. Budgets
may change on that invocation; the immutable plan cannot. A new plan can start
at another already initialized runner, but cannot adopt occupied future trees
from a different plan. Existing originals and earlier exports remain untouched.

## Outcomes

- `plan_complete` (exit 0): all used spans exported. A physical STOP before the
  last planned edge is exported and `unused_edges` reports the unused suffix.
- `plan_exhausted` (exit 3): the final selected prefix ends in ROTATION, there is
  no contiguous current-generation candidate at the final observation, and no
  further edge is declared. No STOP, closure or new generation is invented.
- `plan_paused` (exit 3): a bound, missing input or bounded terminal phase stopped
  the plan. Inspect its nested result; this is not completion.
- `plan_refused` (exit 2): changed authority, ambiguity, cancellation, uncertainty
  or another safety refusal. Preserve all files and inspect the exact operation.

`completed_edges` counts verified links in this invocation, not a recovery
permission. `terminal_stop_verified` describes the selected original's real
STOP, not full physical capture coverage. `recording_complete` and
`recognition_continuity_verified` remain false. An externally introduced logical
boundary beyond the final plan is refused rather than reported as physical STOP.

## Global bounds

One monotonic deadline covers validation and every edge. Delegated calls retain
an additional 120-second cap. Request, mutation-request and response-byte limits
are global; each nested operation receives only the remaining allowance. Failed
responses retain conservative byte reservations, and initializer bootstrap
reservations cannot reuse an earlier edge's budget.

Action and poll limits are conservative global reservations. Before each runner
or transition watch, the coordinator reserves its entire permitted phase action
and poll allowance. Unused reservations are not refunded. This includes nested
transition watches, early-STOP exports and final draining/terminal watches.
Results label these as `reserved_action_allowance` and
`reserved_poll_allowance`, not actual action counts. A reservation that would
exceed `max_total_actions` or `max_total_polls` stops before dispatch. Setting
large per-phase allowances with small totals can therefore pause early.

Clean final draining uses bounded one-poll passes. Once the last available
ROTATION is established, the command reports exhaustion without waiting out the
whole deadline. A real STOP receives one bounded finalize/export watch; a paused
terminal watch requires an explicit later invocation.

This is a finite orchestration primitive, not indefinite storage, automatic
provisioning, a system service, a phone delivery guarantee or long-duration
recognition qualification. It does not start/restart the receiver, remove data,
repair partial uploads, or relax existing primary-analysis qualification gates.

## Optional native-turn evidence export

Pass `--native-turns` (Python API `native_turns=True`) to opt in for every
export in this finite plan. The default remains the existing two-artifact
baseline. Native mode exports exactly `export.json`, `transcript.txt`, and
`native-turns.json`, plus `complete.json` committing their individual sizes
and SHA-256 hashes. Native source/content validation and paired stable reads
remain the direct exporter's responsibility. This evidence describes native
turn detection; it does not establish recognition accuracy or global speaker
identity, schedule overlap candidates, run an extra model, or replace primary
transcripts.

The finite plan's immutable `plan.intent.json` records
`export_policy: "native-turns-v1"` only when enabled, before any phase effects.
Each span's `export.intent.json` records the same policy before publication.
Omitting the flag on a native plan resume, or adding it to a baseline plan
resume, refuses before network activity. Exact completed repeats verify the
same artifact set and hashes without exporting again. Existing baseline
configs, plan intents, export intents, and receipts retain their old shapes;
there is no false-valued field or receipt migration.

The same default-off flag is available on standalone handoff, generation
transition, and runner `run`/`watch`. A saved export intent pins their choice and
an incompatible invocation is refused before effects. Unlike the finite plan,
these standalone commands do not have an outer policy intent: a pause before
an export intent exists does not pin the option. Use the finite plan when the
choice must be immutable across earlier intake/finalization pauses.

Native mode currently cannot be combined with `--export-recovery-attempts`.
That combination is refused before filesystem or network effects, including
at nested entry points. The direct export-recovery command also explicitly
refuses `--native-turns`; its baseline partial-file validation is unchanged.
On any native export failure, preserve all output and receipts. An incomplete
or uncertain publication is never adopted, overwritten, or upgraded in place.

Completed baseline recovery receipts retain their recorded request policy. The
reader accepts only the two previously published exact ceilings, 41050 and
73978, and checks every recorded request count against that receipt's own
ceiling. All other audit-header, source, file, hash and budget checks remain
exact. This read-only compatibility does not rewrite receipts, expand an old
budget, accept arbitrary ceilings, or resume an uncertain recovery attempt.
