# Bounded receiver-generation and analysis transition

`audio_backend.recorder_generation_transition` composes the existing logical
boundary, bounded runner and exact next-session initializer. It does not install
a daemon or introduce another progress database. It never starts, stops, restarts
or changes the FTPS receiver.

## Prerequisites

Prepare a fresh stable-endpoint routing configuration using
`recorder_receiver_routing`. Retain every prior generation, keep the same account,
TLS identity, host, port and directory, and explicitly stop/restart the receiver
outside this command when changing its active generation. Verify the receiver
operation separately. Reading a valid route file cannot establish that a live
receiver uses it.

The old runner must have a frozen receiver-publication binding and a fully
admitted, nonempty prefix ending in ROTATION. Its output destination must already
be configured. The exact successor must already be completely published in the
route configuration's selected active generation, with matching retained raw and
published files. The old generation must remain in the route list and differ
from the active generation. Normal sequence, frame, epoch and seam checks apply.
No pending original, missing original, partial upload or unknown attempt is
adopted or repaired.

## Explicit command

    python -m audio_backend.recorder_generation_transition \
      --state-dir /private/runner/span0 \
      --route-config /private/routing-next.json \
      --successor-attempt r-EXACT-PUBLISHED-ATTEMPT \
      --next-state-dir /private/runner/span1 \
      --next-job-dir /private/jobs/span1 \
      --next-output-dir /private/exports/span1 \
      --next-capture-id analysis-span-1 \
      --next-session-name "Next bounded analysis span" \
      --acknowledge-logical-analysis-boundary --finalize --export \
      --acknowledge-create-next-session --exclusive-writer \
      --acknowledge-span-local-closure \
      --deadline-seconds 120 --max-actions 256 --max-polls 256 --poll-seconds 5

Replace the attempt placeholder with the exact published directory name. The
coordinator derives the next inbox and publication binding from the declared
active generation. It does not accept a second independently chosen destination.

The v2 logical-boundary receipt pins the route file's canonical path and exact
byte hash, the exact old/new publication pairs, the successor files and directory
identities, and the next-session plan. The existing independent job and runner
anchors bind that whole receipt. Keep the selected routing file unchanged and
available for recovery; a replacement file must not overwrite it.

The command then finishes and exports only the old selected analysis span. Only
after the old export is verified does it create or verify the exact next session
through the existing initializer. It does not admit or analyze the next original.
Run or watch the newly configured runner explicitly afterward. The final
physical STOP span continues to use the ordinary runner finalization workflow.

## Bounds and recovery

The invocation has one monotonic deadline. Boundary preparation and next-session
creation are each limited to at most 120 seconds within that deadline. Old-span
processing also has explicit action, poll, request, write and response-byte
bounds. `--max-requests`, `--max-network-writes` and `--max-network-bytes` apply to
that finishing phase; the initializer adds at most six requests and one POST.
Optional `--export-recovery-attempts 2` or `3` enables only the existing bounded,
GET-only export revision-conflict recovery. Its default is zero.

Exit codes:

- 0: `transition_complete`. Old export verified and exact next session linked.
- 3: `transition_paused`. A bounded phase stopped without completing the link.
- 2: Refusal, interruption or uncertainty. Preserve all existing files and inspect
  the exact saved operation. A failure does not prove that no remote effect occurred.

For a deliberate bounded pause, use `--max-actions 1`. Repeat the exact command
with a sufficient action/deadline budget to continue. Changed successor, routing
file, next paths, capture ID or session label is refused. Budgets are not part of
the immutable plan and may be changed on a later explicit invocation.

There is no automatic retry. Complete anchored preparation is verified rather
than recreated, so an existing finalization/export intent can resume. Partial
preparation follows the existing exact creation-order protocol. Finalization and
export reuse their existing receipts. A confirmed next-session creation can
finish its local binding without another POST; an uncertain POST is never sent
again. An already completed next initialization verifies locally even after next
intake begins. Do not remove controls, invent a replacement session, or overwrite
partial export output to force progress.

## Meaning of success

Every result explicitly reports `receiver_runtime_verified=false`,
`receiver_process_changed=false`, `atomic_rollover=false`,
`recording_complete=false` and `recognition_continuity_verified=false`.
This is a recoverable bounded analysis transition based on declared routing and
verified originals, not an atomic receiver switch, physical recording STOP,
uninterrupted recognition guarantee, device proof or indefinite storage policy.
Originals, old generations and prior analysis artifacts remain untouched.
