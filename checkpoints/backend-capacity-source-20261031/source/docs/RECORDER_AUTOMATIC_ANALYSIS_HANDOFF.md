# Bounded automatic analysis handoff

`python -m audio_backend.recorder_analysis_handoff` is an opt-in coordinator for
one already prepared successor generation. It drains the current runner,
discovers a unique contiguous published successor, completes and exports the
old logical analysis span, creates or verifies its exact next session, and
starts the successor runner. It never writes a producer STOP or changes the
receiver. This removes the manual attempt-name selection, transition invocation,
and next-runner start for one handoff. It is not an indefinite daemon.

Prepare the receiver generations and their retained routing configurations first.
The running receiver can use `--auto-advance-generations` for capacity admission.
The handoff's separate immutable route file must select the intended successor
generation and retain the old generation. A file is not proof of live receiver
configuration. No file is modified to switch a live receiver. Do not remove old
namespaces or overwrite routing files that existing receipts pin.

Example, after initializing the first runner:

    python -m audio_backend.recorder_analysis_handoff \
      --state-dir /private/runners/span0 \
      --route-config /private/route-selecting-generation-1.json \
      --next-state-dir /private/runners/span1 \
      --next-job-dir /private/jobs/span1 \
      --next-output-dir /private/exports/span1 \
      --next-capture-id analysis-span-1 \
      --acknowledge-automatic-handoff --exclusive-writer \
      --acknowledge-span-local-closure \
      --deadline-seconds 300 --max-polls 64 --poll-seconds 1

The next paths, capture identity and optional session name are a fixed plan.
An existing boundary pins them and the exact original files. On an explicit
later invocation, supply the same plan. Confirmed work is verified from existing
receipts. A partial or uncertain operation is never discarded, repaired,
replaced or blindly retried. The command does not start another invocation
itself, including after a bounded pause.

## Selection and closure

Only fully published originals are eligible. Each discovery pass verifies the
old ledger and originals, and drains any remaining contiguous old-generation
input before considering another generation. A gap remains a wait, not a cut.
The old prefix must end in ROTATION. Physical STOP stays with the ordinary
runner closure path and is not converted into a logical boundary.

Discovery requires exactly one next-sequence candidate for the same capture.
Equivalent duplicates require review too. Unresolved inventory, conflicting
content, origin changes, invalid frame/epoch seams and namespace ambiguity stop
the command. Existing transition validation checks the raw/published copies,
normal sequence/frame continuity and the selected routing provenance again.
Discovery pins the config hash, route provenance and successor delivery hash
through transition before any transition HTTP effects. Uniqueness is checked at
selection time; after selection the exact immutable original is authoritative,
even if another equivalent attempt arrives later. It does not infer a new
selection from newest modification time or lexicographic order.

After the old export is verified, only the existing exact initializer may create
a session. Its durable POST intent prevents replay of an uncertain creation.
Successor intake begins automatically. If the successor span exports, the
command returns `handoff_complete` (exit 0). This includes physical STOP, or an
already pinned later logical closure on an exact repeat after further progress.
Otherwise `handoff_paused`
(exit 3) includes the phase and the nested bounded runner result; a linked next
session is reported separately. Refusal or uncertain effects return
`handoff_refused` (exit 2). None of these outcomes proves the physical recording
complete or speaker/recognition continuity.

## Budgets and recovery

One monotonic outer deadline covers all phases. Each delegated runner or
transition call is additionally capped at 120 seconds. `max_polls` bounds the
number of discovery/drain rounds and, separately, each of the transition and
successor watch phases. `max_actions` is a per-runner-phase bound, not a global
action count: the aggregate upper bound is `(max_polls + 2) * max_actions`.
The runner polling upper bound is `3 * max_polls`. These bounds are reported in
the result. Discovery waits are cancel-aware.

Requests, mutation requests and response-byte reservations are shared across
the entire invocation. Every phase receives the remaining allowance, so the
initializer's bootstrap reservation cannot consume budget already used by old
intake. Failed or interrupted responses retain their conservative charge. Clean
missing input and confirmed bounded drain progress may be observed again within
the discovery loop. Unresolved/uncertain mutations, a paused transition and a
paused successor watch stop the coordinator rather than triggering replay.

Optional `--export-recovery-attempts 2` or `3` enables existing GET-only export
revision-conflict recovery. `--busy-read-retries 1` through `3` enables the
existing typed busy-GET recovery per phase. Defaults are zero. Mutation retries
are not enabled. Shared network budgets include these reads.

Reviewed identity-bank preservation remains opt-in using the existing three
flags: `--preserve-reviewed-identity-group`, `--identity-generation`, and
`--identity-policy-sha256`. They pin the reviewed group and policy through the
existing transition initializer. Without them, no identity-bank continuity is
claimed. This command does not enroll speakers or alter matching thresholds.

## Limits

This implementation handles a single specified successor generation per
invocation. Provisioning further generations, storage retention, service startup
and scheduling additional handoffs remain operator responsibilities. Full
storage, partial uploads, uncertain session creation, gaps and failed analysis
remain visible blockers. The command does not silently reclaim data or retry
phone uploads. Transport and frame preservation are separate from recognition
quality, physical-device delivery and long-duration production qualification.

## Validation on 2026-11-05

Focused automated controls cover initialized-but-unadmitted old input, unique
selection, duplicates, route changes after discovery, bounded transition pause,
shared initialization budget exhaustion without a stranded POST intent, uncertain
creation without replay, missing acknowledgments and zero-network exact repeat.

A separate real loopback chain used the new source receiver and client modules
against the existing qualified installed dev67 native service. One FTPS process
admitted three prepared generations without a restart. Two bounded automatic
handoff invocations produced three native exports. The first invocation linked
and admitted B, then paused at B's ROTATION; the second linked C and exported its
real STOP. Each invocation was supplied its next paths and route in advance,
without supplying an attempt name or separately starting successor intake.

All 144,000 original PCM frames were conserved exactly, 48,000 per session.
Original, raw and published file bytes were unchanged, old attempts remained
readable, and both exact repeated handoffs made zero network requests and writes.
The receiver shut down with exit 0, the native service exited normally on SIGTERM,
and no recorded child remained. This short nine-second public-speech fixture is
an orchestration/frame-conservation test, not a long-duration or recognition
quality benchmark. The machine had no phone or external target server.

The first source-native launch was correctly refused because that source tree
had not been qualified for the interpreter. No qualification check was bypassed
or edited. The successful experiment explicitly kept the already qualified
native backend unchanged and exercised new clients through its real HTTP API.
It therefore does not establish installed identity or native qualification of a
new complete package. The validation JSON records exact source and evidence
hashes plus this limitation; later source changes need renewed verification.

The focused handoff/transition suite passed 41 tests. The related runner,
initializer, logical-boundary and transition regression passed 200 tests; a
separate reviewed-identity regression passed 32 tests. The latter two emitted
only the existing Starlette/httpx TestClient deprecation warning.
