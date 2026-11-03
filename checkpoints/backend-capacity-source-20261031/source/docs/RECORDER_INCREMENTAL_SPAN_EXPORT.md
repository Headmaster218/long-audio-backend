# Private primary transcript export for a completed selected span

`python -m audio_backend.recorder_incremental_span_export` reads one explicitly
identified, finalized incremental recorder job and exports its current **primary
ASR baseline**. It uses the existing authenticated GET APIs; it never creates a
session, uploads, seals, finishes, retries analysis, loads a model, or repairs an
intake job. The known local job, original deliveries and snapshots must still be
available for complete provenance verification.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m audio_backend.recorder_incremental_span_export \
  --job-dir /private/intake-jobs/span-41-onward \
  --api https://your-backend.example \
  --session-id EXISTING_SPAN_SESSION_ID \
  --capture-id ORIGINAL_SPAN_CAPTURE_ID \
  --output-dir /private/transcript-exports/span-41-first \
  --exclusive-writer \
  --deadline-seconds 120
```

Supply an **absolute fresh output directory** under an existing private,
owner-only parent outside a Git checkout and outside every input/job tree. The
command creates the directory as 0700 and files as 0600. Existing paths,
symlinks, public parents, and overwrites are refused. It does not upload or share
exports. Keep these private artifacts out of source backups. Authentication uses
`AUDIO_API_KEY` through the existing transport; credentials, local source paths,
server error bodies and transcript text are never printed to stdout/stderr.

## Artifacts and completion

- `export.json`: schema `recorder-incremental-span-export-v1`, original-to-span
  frame mapping, verified primary completion, exact canonical primary ASR units
  and hashes, unmodified original segment timestamps, conservative frame
  envelopes, per-work-chunk text counts, page fingerprints and transcript revision.
- `transcript.txt`: timestamped primary segment text, with anonymous source-local
  speaker scopes. Unicode words stay readable; control characters, embedded
  line breaks and bidirectional formatting characters are escaped inside quoted
  text so they cannot imitate headers or timestamps.
- `complete.json`: published last, containing artifact lengths, SHA-256 hashes,
  counts and explicit limitations. Consumers must require this regular private
  manifest and verify both artifact hashes before treating an attempt as complete.

The output has no wall-clock generation timestamp, making unchanged repeated
exports byte-identical. Segment timestamps are present in both formats. Repeating
requires a different fresh directory. A failed attempt can retain private
`.partial` files or an artifact pair without a completion manifest. Such an
attempt is **not a completed export** and is never automatically resumed,
removed, overwritten or shared. A final-sync failure attempts to remove only the
exact owned completion marker; if the filesystem also refuses that removal or
sync, publication is explicitly uncertain. A visible marker alone does not prove
power-loss durability. Do not reuse an uncertain output directory. Exit 0 means a committed artifact pair; exit 2
means refusal. Python callers receive the same metadata manifest or an exception.

## What the exported text means

This is `primary-baseline-v1`, obtained from `baseline_reading` of the existing
`whole-primary-v1` endpoint. It is not the unified optional MOSS display
preference or the lexical-estimated projection. Optional substitutions,
alternatives and recording-speaker links are intentionally omitted. No output
claims recognition accuracy, verified word alignment, a global person identity,
full recognized content, complete physical capture, or hardware continuity.

Every primary unit must recompute exactly from its canonical ASR fields and
content hash, belong to a work chunk bound by the full status verifier, retain
its exact source-local speaker scope, stay within the selected span and the
existing five-second ASR-context geometry, and have one unique segment ID and
source reference. Whole units crossing a 60-second block are exported once at
their home block; later references must match and intersect exactly. No text is
cropped, interpolated, gap-filled, deduplicated by words, or re-attributed.

`complete_content=false`, `eos=null`, and `speaker_identity_valid=false` remain
explicit. Intake EOS means **selected span only**. Unknown before/after capture
gaps stay null, and STOP, INTERRUPTION and uncertain RECOVERED_TAIL retain their
original distinctions. A successful export can contain no text:
`text_status=no_primary_text_observed` is explicit, with per-work text coverage.
Missing/empty text does not establish silence, ASR execution, or why recognition
was absent/skipped. Empty primary units are retained, not discarded.

## Read and publication safety

The existing incremental status implementation performs its full validation
first. A narrow internal completed-read continuation keeps both existing local
locks held throughout transcript retrieval. The continuation receives only work
identities already verified against exact local history and remote page/session
projections. Pending, blocked and unfinalized jobs cannot read transcript text.
The ordinary standalone status path retains its original request sequence.

All 60-second transcript pages must match the fixed policy, exact session,
integer geometry, one revision and the exact cursor sequence through the expected
terminal block. Response shape, canonical content, ordering and cross-block
references are validated. The exporter stages bounded local files, then the
observer rereads all work pages and session chunks. A second complete transcript
pass must reproduce every selected-baseline page fingerprint, even if a broken
peer reuses a revision. Finally capture/contract and local controls, admissions,
complete chains, snapshots and piece digests are revalidated before publication.

Private output uses anchored directory descriptors, no-follow exclusive file
creation, file readback hashes, no-overwrite links and final-link identity checks.
Artifact signatures are checked again before the completion manifest. These are
local filesystem safeguards, not a claim against a malicious same-user process
racing every syscall. Existing job files are never modified. The server read is
still **non-atomic under an explicit exclusive-writer assumption**; local locks
cannot exclude direct API writers, and there is no server-wide snapshot token.
The manifest commits a local artifact pair, not an atomic server observation.

## Finite bounds

All status and transcript requests share one elapsed-time/cancellation/RSS guard
and one aggregate GET-only transport budget; there are no retries, including
429, 409, or transient errors. Existing HTTP limits and authentication remain.
Defaults are 2,880 transcript pages (48 hours), 200,000 primary units, 64 MiB total
local output, 256 MiB aggregate decoded GET responses, and 41,050 total GETs.
Every transcript page is capped at 256 KiB, all other HTTP responses at 512 KiB.
Python `ExportLimits` can lower but not raise these ceilings. Existing `Limits`
can lower source/output-frame ceilings and control deadline/RSS. The default
120-second deadline includes all verification and publication, not just GETs.

Output is streamed, but bounded reference/segment-ID indexes and work coverage
are retained. No constant-memory or hard operating-system I/O interruption claim
is made. A legal 48-hour job may exceed output, response, runtime or memory
limits and is then refused, never truncated or reported as a partial success.
Capacity arithmetic and synthetic tests do not qualify maximum-size runtime.

## Verification and native smoke

Model-free suites use real v3 job histories and real ASGI transcript/status reads
with explicitly synthetic analysis rows. They test canonical text/timing/source
binding, empty results, nonzero original coordinates, terminal kinds, exact
repeat, privacy, locked read continuity, changed state, page/revision/reference
failures, output races, permissions, no-overwrite, finite budgets and CLI error
redaction. They do not establish inference or speech quality.

`tools/strict_uploader_smoke.py --export-transcript` is an opt-in extension of the
existing bounded incremental-span smoke. It requires the existing incremental
span input mode and changes no native/resource gates. After genuine worker
completion and status checks, two fresh child processes export through GET-only
transports. Their requests, original process wait receipts and private artifacts
are retained. The smoke requires exact repeated artifact bytes/hashes and at
least one nonempty native primary ASR unit, otherwise it explicitly fails speech
export qualification. Any actual fixture/run results must be reported separately;
this option alone is not evidence that a native run passed.

Native public-speech validation is pending at this source checkpoint. No deployment or runtime/model pin change is included.
