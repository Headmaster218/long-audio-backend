# Explicit logical analysis boundaries

A recorder ROTATION is not a recording STOP. This opt-in operation closes only a
selected backend analysis span at a complete admitted original-file boundary.
It preserves the original WAV and metadata, and does not assert that recording
has finished or that recognition context continues unchanged across sessions.

## Prepare a boundary

First admit at least one complete original through the normal runner. Every
selected input must be fully admitted; no pending selection, unfinished admission
or prior finalization/export intent may remain. The final original must still say
ROTATION. The exact next original must already be published in the SAME frozen
inbox and pass the existing sequence/frame/seam checks. Duplicate attempts require
an explicit choice. Cross-generation successor selection is not implemented here.

    python -m audio_backend.recorder_logical_boundary \
      --state-dir /private/runner/span0 \
      --successor-attempt r-EXACT-PUBLISHED-ATTEMPT \
      --next-state-dir /private/runner/span1 \
      --next-job-dir /private/jobs/span1 \
      --next-output-dir /private/exports/span1 \
      --next-capture-id analysis-span-1 \
      --acknowledge-logical-analysis-boundary

The successor argument is the actual published attempt directory name, not the
literal placeholder above. The next paths must be fresh, canonical and disjoint
from the old data. Preparation does not create a session, upload audio or send
HTTP. It saves independent private job and runner intent anchors, then an immutable
logical-boundary.json receipt in the old job. The anchors pin the complete
receipt hash before it grants authority.
The receipt binds the original admission prefix, exact frame count, runner config
and selection ledger, successor content and bounded next-session intent.

Preparing the receipt fences the old span against further intake. Ordinary
ROTATION finalization remains refused without a valid receipt. A missing, altered or partial authority control is fenced and refused before
network effects. Exact preparation resumes only a valid saved creation-order
prefix and re-fsyncs the authority controls and their parents. A failed fsync
does not become consent on retry. Altered arguments are not accepted.

## Finalize and export

Use the existing runner's explicit --finalize and --export authorizations. The
stored logical consent does not silently request either operation. The uploader's
existing selected-span EOS flushes the exact positive tail, including a tail that
is not a full analysis chunk; no padding, truncation or invented producer STOP is
needed. Resume, status and export independently revalidate the boundary.

The status and export retain the original frame mapping and explicitly report
logical_analysis_boundary, recording_complete=false and
recognition_continuity_verified=false. The text export also labels the logical
boundary. Keep the receipt, original deliveries, runner state, job and export
artifacts together. This is not an authorization to delete anything.

## Advance to the exact next session

After the old selected span has completed analysis and its export is verified:

    python -m audio_backend.recorder_logical_advance \
      --state-dir /private/runner/span0 \
      --acknowledge-create-next-session --exclusive-writer \
      --acknowledge-span-local-closure

This command uses only the successor, next paths, session name and capture ID
saved by the anchored boundary. It validates the old export before creating a
session through the existing single-attempt initializer. It does not upload or
start processing the next original. Run/watch the new runner explicitly afterward.

A confirmed creation can resume from its saved initialization receipts. An
uncertain POST must stop without another POST; inspect the existing operation,
not a replacement session. Once the next initialization is complete, an exact
repeat validates its saved link with zero HTTP, including after next intake has
started. A changed or foreign initialization is refused. This is an explicit,
recoverable two-span workflow, not an atomic transaction or an automatic scheduler.

## Limits

These controls assume one trusted owner and exclusive writer. They detect
individual missing/altered authority controls, not coordinated consistent rollback
of every independent control and its surrounding state. Do not treat them as a
security boundary against the account owner or an arbitrary concurrent writer.


This mechanism does not by itself schedule repeated cuts, change receiver routing,
clear capacity, ensure enough storage for backlog, or provide an indefinite daemon.
The next original must be available before the cut can be bound. Existing partial
or uncertain inputs are preserved for explicit recovery instead of being moved to
a new session. Independent analysis sessions can produce different boundary words
or speaker identities even when concatenated PCM is exact. Long-duration,
overlapping-speaker and recognition-quality acceptance remain separate work.

## Authority durability and trust boundary

The receipt is independently hash-bound by immutable job `logical-boundary.intent.json` and runner `logical-cut.intent.json` markers, written before it. An incomplete preparation fences append/finalize instead of reverting to physical-only behavior. Exact preparation retries can finish only the expected creation-order prefix; unknown partial controls remain refused. Successful retries explicitly fsync all authority files and parent directories. Effectful intake revalidates and fsyncs the pinned authority before every HTTP write.

Each individually missing or altered receipt/anchor is refused. Runner's independent marker also detects removal of both job controls. This remains a trusted-owner local protocol, not protection against coordinated wholesale rollback or replacement of all independent state. Do not delete authority controls as a recovery strategy.

Exports and full status reports are private provenance artifacts: they can contain original/successor paths and the next-session label. Do not commit or publish them. Normal preparation CLI output exposes only semantic flags and the receipt hash.
