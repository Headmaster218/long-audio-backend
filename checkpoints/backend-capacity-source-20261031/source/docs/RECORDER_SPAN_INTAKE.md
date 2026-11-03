# Explicit bounded recorder span intake

For more than eight deliveries or one-at-a-time admission of a selected span, use the separate [incremental span intake](INCREMENTAL_RECORDER_SPAN_INTAKE.md). It preserves original provenance with a distinct v3 job schema and requires explicit finalization; this fixed adapter and its existing jobs remain unchanged.

`python -m audio_backend.recorder_span_intake` accepts one explicitly selected,
already available declaration-contiguous span into a **separate, existing,
known-empty strict-primary-payload-v2 session**. It does not create sessions or
choose/split spans. It leaves the original single, batch and incremental capture
intake guards unchanged.

A successful EOS means **only that the selected span ends**. It does not mean
the recorder stopped, the whole recorder capture is complete, missing data was
recovered, or a physical timeline has been established. The backend has ordinary
session-local EOS semantics; the local manifest and returned span description
carry the narrower recorder provenance. Consumers must retain that distinction.

## Explicit operator invocation

Use a private 0700 parent and a new job directory. Supply 1–8 directories in
original sequence order, totaling at most 512 MiB of WAV and two hours of mono
16 kHz PCM16. Configured uploader limits can be lower. No resampling, silence
padding, gap filling, inference retry or automatic session creation occurs.

```sh
python -m audio_backend.recorder_span_intake \
  --delivery /private/received/segment-41 \
  --delivery /private/received/segment-42 \
  --job-dir /private/intake-jobs/span-41-42 \
  --api https://your-backend.example \
  --session-id EXISTING_EMPTY_SESSION_ID \
  --capture-id OPERATOR_CHOSEN_SPAN_CAPTURE_ID \
  --exclusive-writer \
  --acknowledge-span-local-closure
```

Both acknowledgments are required on every invocation. Exclusive ownership
covers the session/capture and local job. The second acknowledgment accepts the
meaning of a separate span-local EOS, including when the recorder close reason
is INTERRUPTION or RECOVERED_TAIL. Do not reuse an occupied session, even when
its capture ID matches. An incompatible contract, existing capture or chunks
refuses new intake before media mutation.

## Selection and closure

The first input may have an arbitrary original sequence, epoch and epoch-local
frame start. An epoch restart or a missing predecessor can therefore begin a
separate selection. Original coordinates are preserved as evidence; the new
server capture always starts at span-local frame 0. Subsequent inputs start at
the sum of selected preceding frame lengths, never at their original epoch
coordinates. This is a local translation within one declaration-contiguous span,
not a mapping between epochs or to a microphone clock.

Every input must have the same recorder capture identity. Original ordinals
must be consecutive and supplied in increasing order. Every existing sequence
reconciler edge must have no refusal reasons. Missing ordinals, duplicate or
conflicting ordinals, gaps, overlaps, epoch changes, run/configuration conflicts,
untrusted seams and contradictory anchors refuse the selection before HTTP.
No boundary reason is suppressed. Validation covers only the supplied selection;
it cannot discover conflicting deliveries that were not supplied. The unselected
prefix/suffix and their gap
durations remain unknown; the adapter never declares them to be zero.

The final close reason must be:

- **STOP:** the selected terminal segment declares recorder STOP. This is still
  not proof that the complete capture or its prefix has been supplied.
- **INTERRUPTION:** the selected span ends at its declared interruption. It is
  never rewritten as STOP.
- **RECOVERED_TAIL:** allowed only as a singleton, retained explicitly as uncertain
  evidence. No adjacent segment is joined to it and no STOP is manufactured.

A terminal ROTATION is refused. It is not independently closed by this adapter.
Nonterminal inputs must satisfy the existing rotation/adjacency checks. Warnings
such as absent anchors are retained and do not become hardware-clock proof.

## Durable identity and exact recovery

Before any network effect the adapter snapshots and verifies all deliveries,
checks selection boundaries and source-slot capacity, then exclusively writes
and fsyncs a canonical manifest. It binds:

- Normalized API origin, existing session, selected capture and local origin 0.
- Ordered input paths, owned, byte-verified snapshot paths and local starting frames.
- Original source IDs, capture/run identity hashes, original sequence, epoch,
  frame start, frame length, close reason, WAV/PCM/metadata hashes and declarations.
- Both acknowledgments, selected-span-only EOS, terminal closure reason,
  unproven physical continuity/capture completeness and unknown before/after gaps.

Original WAVs, markers and metadata are not edited. The inherited 16 KiB manifest
limit remains unchanged; unusually long paths can refuse even a valid eight-input
selection. The command is bounded fixed selection, not an incremental watcher.

The existing v2 bounded append uploader owns piece keys, exact bytes, publication
and EOS intents. It retains original source IDs as input identities while piece
idempotency keys belong to the frozen uploader journal. One shared upload guard
bounds all appends and finalization. A private outer lock and the real uploader
lock are held across journal inspection and all uploader invocations. Preflight
and final reinspection each share a 120-second cooperative deadline. These are
not hard OS limits or physical resource measurements.

After interruption, preserve the entire job and all originals and rerun the
**same arguments** with `--resume`. A changed selection, order, path, target,
closure declaration, snapshot or acknowledgment is refused. Resume requires the
exact intact canonical manifest, snapshots and uploader journal. Missing or
partial initial state is never repaired or adopted, even if the target appears
empty. Investigate it manually; do not create a replacement job/capture to bypass
an uncertain effect. A valid active journal can replay its exact input from the
unchanged snapshot, using existing piece identities. Successful-but-lost PUT
responses are reconciled against the saved journal and remote sources, not
resent under new keys.

Success returns `selected_span_accepted_with_eos`, the preserved original span
coordinates/closure, original source IDs, warnings, source/page/frame counts and
`analysis_completion_verified: false`. It also explicitly reports
`capture_completeness_proven: false`, `physical_continuity_proven: false`,
`hardware_frame_mapping: UNESTABLISHED` and unknown before/after gap lengths.
Failures remain unresolved and preserve possible prior effects. No automatic
inference retry, deployment, producer authentication or global timeline claim
is implied.

## Test scope

`tests/test_recorder_span_intake.py` has separate layers:

- Actual strict ASGI/SQLite source admission, without native lifespan, exercises
  nonzero original coordinates, separate restart spans, STOP/INTERRUPTION/recovered
  singletons, loss of the second PUT response, interrupted/resumed exact source
  admission, frozen EOS intent, occupied targets and changed selection refusal.
  These tests deliberately stop before seal/native work. They do not establish
  actual strict/native publication or inference completion.
- The existing synthetic protocol peer exercises full client return, span-local
  EOS and repeated exact completion resume with no additional sources. Its
  immediate page publication is simulated, not a native processing result.
- Separate interpreter `os._exit` tests interrupt after the durable manifest
  before any uploader journal, and after an active piece journal save. The former
  is explicitly refused and preserved; the latter resumes the exact journal.
  This covers process exit/restart, not power-loss durability or filesystem crash
  consistency.
- Refusal, bounded-size arithmetic, sparse oversized-file precheck, eight-input
  canonical-manifest capacity and shared guard tests cover client safeguards.
  Tiny fixtures and arithmetic checks are not two-hour runtime qualification.

A separate [short real native smoke](RECORDER_SPAN_NATIVE_SMOKE_20261103.md)
completed two native chunks from 11 seconds of generated silence with nonzero
original coordinates and an INTERRUPTION close. It verified exact completed
resume without new sources while preserving span-local EOS. That narrow result
is separate from these model-free tests and does not qualify acoustic accuracy,
recorder hardware/provenance, continuity, capture completeness or long duration.

## Observe downstream completion without replay

After this fixed span job has finalized, use the separate
[read-only span status command](RECORDER_SPAN_STATUS.md) to compare its intact
local provenance and journal with the current backend page/source/chunk state.
It does not resume intake, repeat the seal, retry inference or change the job.
The importer still reports `analysis_completion_verified: false`; the status
command provides a separate bounded observation, not an atomic future guarantee.
