# Read-only interruption and epoch partition report

```sh
python -m audio_backend.recorder_boundary_report /private/snapshot-a /private/snapshot-b
```

Supply explicit, previously created owned snapshot directories, in any order.
The command verifies every snapshot and its producer metadata using the existing
sequence verifier before emitting one JSON report. Invalid, incomplete, changed
or malformed inputs refuse the entire request with exit status 2. There is no
partial-success subset. Successful observation returns exit status 0, which does
not authorize ingestion. The Python entry point is `report_snapshots(paths,
deadline_seconds=120)`.

## What this adds

The existing sequence report lists pairwise declaration relations. This report
groups unambiguous observed audio into separately identified declaration-contiguous
spans and shows exactly where each group stops. It makes interrupted captures
reviewable without losing the audio before or after an unresolved boundary,
silently joining epochs, selecting a conflicting retry, or pretending the whole
capture is ingestible. A span is a description of verified supplied files, not a
new media file, repaired recording, safe-ingestion promise or operational import
plan. No source is copied, renamed, rewritten, deleted or retired.

## Reading the report

- Captures are grouped by the existing SHA-256 capture identity and sorted by
  declared global sequence. Every existing sequence-reconciler edge reason splits
  spans: missing ordinals, frame gaps/overlaps, epoch/run/format/input/target
  conflicts, seam/close problems and v2 anchor observation contradictions.
- Span IDs are a versioned hash of the original source identity at the span's
  first sequence. They do not depend on input order, path, selected content hash,
  transfer attempt, or span end. Adding a suffix keeps an existing span's ID;
  observing an earlier adjoining prefix changes its actual start and hence ID.
  An ID is a grouping label, not content authentication or an import/idempotency
  token. Content hashes remain separately visible.
- Each segment retains original sequence, epoch, epoch-local recorded-PCM frame
  start/end, format, boundary, close reason, declared seam flag, hashes and
  zero-based input indices. Input indices refer to CLI argument order and can
  change on a shuffled invocation. Raw capture/run names, device/input strings,
  anchor payloads and filesystem paths are not emitted. Run identity is hashed;
  these hashes are correlation labels, not a guarantee of anonymization.
- Exact repeats of inner metadata hash plus WAV hash at the same capture/ordinal
  count once as audio; all input indices are retained. Repeat validation still
  consumes the input-count, byte and time budgets. `verified_input_wav_bytes`
  describes this verification workload, including repeats. Span audio totals and
  `unambiguous_observed_wav_bytes` exclude repeats and every conflicted ordinal.
- If variants disagree at one ordinal, **all variants are quarantined**. There is
  no selected content and no span member for that ordinal. Every variant is shown
  separately with its provenance and close state. The quarantine byte total counts
  distinct variant files, not trusted audio or a playable timeline. Boundaries
  touching a conflict do not borrow any variant's epoch or frame declaration.
- `INTERRUPTION` can terminate a span after declaration-adjacent rotations; the
  next segment starts another span. `RECOVERED_TAIL` splits from its predecessor
  and is retained as that exact close state, including when supplied alone.
  Neither state is converted to STOP or EOS. Final ROTATION and STOP also remain
  visible; neither establishes whole-capture completeness.
  Every affected segment and span has explicit `unsupported_close_reasons`, even
  when supplied alone. Each span also carries its unresolved `entry_reasons`.
- Every epoch transition or regression is unresolved. For a cross-epoch or
  conflicted boundary, `declared_frame_delta` is null. It is also null when the
  same epoch has conflicting run/format/input/boundary/target declarations, since
  those do not establish one comparable coordinate domain. For an otherwise
  unambiguous same-epoch boundary, it records only the next declared start minus the previous declared
  end, explicitly labeled `declaration_only`. It is not converted to elapsed time,
  silence duration or physical lost audio. `physical_gap_duration_seconds` is
  always null. There is no global timeline, inferred cross-epoch offset, clock
  repair, resampling, gap fill or overlap deduplication.
- Missing prefix, an unsupplied predecessor, nonzero initial frame/epoch, unusual
  initial boundary and initial recovered tail are reported even for a single
  supplied ordinal. Empty leading reasons do not prove that the capture is complete.

The report, captures, spans, quarantines and unresolved boundaries always retain
`physical_continuity_proven: false` and `strict_import_ready: false`. The top-level
report also retains `hardware_frame_mapping: UNESTABLISHED`,
`gap_repair_performed: false` and `epoch_ingestion_supported: false`.

## Bounds and read-only scope

The unchanged verifier accepts at most **32 snapshots**, **512 MiB aggregate WAV
bytes** and a shared **120-second cooperative deadline** (callers may shorten it).
The emitted JSON is additionally capped at **256 KiB**, including its newline.
WAVs are streamed by the existing bounded verifier, never loaded wholesale.
The input-byte precheck is not a hard global I/O meter against concurrent same-user
mutation; verification rereads files. Blocking filesystem calls are not forcibly
interrupted. Snapshots must stay under the operator's control and unchanged during
inspection. Snapshot validation establishes observed consistency, not microphone
authenticity, crash durability or hardware-frame alignment.

This command performs no networking, session creation, uploader mutation, database
write or model dispatch. All current intake guards remain unchanged. JSON output
goes only to stdout; writing it to a file requires the caller's explicit redirection.

## Why this is not epoch ingestion support

The strict backend still binds one capture to a session. Current explicit recorder
intake still requires the original sequence 0 / epoch 0 / START declaration and
explicit STOP-based finalization. It refuses interruptions, recovered tails,
missing segments and epoch changes. This report does not relax those rules and
does not create or recommend automatic session targets for spans.

A future separately reviewed span importer would need a new known-empty session,
preserved original ordinals/epoch/source hashes, and explicit span-local coordinates
with durable provenance and idempotency. It must not rewrite producer metadata to
make a later span look like original sequence 0. Mapping, lifecycle, finalization
and uncertain physical gaps still need an explicit design and tests. None of that
ingestion work is implemented or authorized by a successful report.

## Verification scope

Focused synthetic tests cover shuffled inputs and stable IDs, exact repeats,
quarantined conflict variants, interruptions/recovered tails, restarts and epoch
regression, gaps/overlaps, missing prefixes, declaration/anchor mismatches, privacy
allowlisting, count/byte/deadline/output bounds, malformed snapshots, CLI behavior
and byte/mtime/mode preservation of original deliveries and owned snapshots.
These are model-free observation tests, not real-microphone continuity, acoustic
accuracy, runtime qualification, ingestion success or full-repository qualification.
