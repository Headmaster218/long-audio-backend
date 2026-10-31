# Private prospective overlap diagnostics

This is an additive, local diagnostic-retention feature. It does not improve
recognition quality, repair model timing, recover missing historical output, or
change the public transcript/candidate API. Only synthetic/model-free validation
has been performed. No running service or existing live database is migrated by
creating this source change.

## What is retained

The MOSS adapter captures a version-1 envelope before temporary result cleanup.
Each safely associated support has one envelope, regardless of how many overlap
regions share it. The bounded result is scanned for all safely associated planned
supports even after an unexpected or duplicate ID; association errors still fail
the usable candidate batch. Its private captures contain:

- raw model text, if emitted;
- schema errors as opaque JSON text;
- diagnostic phrases and emitted phrases as separate opaque JSON text, including
  invalid/nonfinite/reversed timestamps;
- the runner's bounded error field, when present, with credential-pattern/path
  redaction.

These captures are never interpreted as usable tracks, main transcript text,
speaker labels to resolve, or enrollment/identity evidence. Usable phrase text,
raw_text and anonymous labels must be strict UTF-8 before projection, so malformed
Unicode cannot reach SQLite and roll back otherwise durable private diagnostics.
Invalid support
outputs still produce no usable candidate tracks. Existing anonymous valid-track
semantics are unchanged. Usable candidate counts and all remaining projection constraints are checked
before temporary cleanup. A later support parse or candidate-count failure cannot
discard earlier or later safely associated envelopes.

On an unassociated/malformed/missing/oversized result, a batch failure envelope
retains a bounded sanitized result/error-artifact prefix when available. If all
support envelopes were already associated, the batch record references that fact
instead of duplicating their raw output. Runtime logs, environment, config,
arbitrary metadata, and exception messages are not retained. Exception class and
safe reason code remain available. This intentionally limits failure detail to
avoid copying ambient credentials and local paths. Pattern redaction is defense
in depth, not a promise to recognize every possible secret in speech or text.

## Bounds and truthful labels

All budgets include serialized JSON overhead and escaped Unicode, measured as
UTF-8 bytes. The conservative ASCII JSON serialization is used for accounting.

- Per support/batch envelope: 128 KiB maximum
- Per window: 640 KiB and at most 5 envelopes (4 supports and 1 batch failure)
- Component JSON: raw 48 KiB, schema errors 12 KiB, each phrase list 24 KiB,
  failure/error artifact 4 KiB; batch result artifact 48 KiB
- Existing total candidate envelope: 2 MiB maximum, still enforced
- Private page: 768 KiB; diagnostic rows max 50, region references max 500

Malformed raw-text types are kept as opaque JSON with partial/invalid_field_type
status. Lone-surrogate raw strings are retained as escaped JSON, with
partial/invalid_unicode status and strict-UTF-8-safe exported text. For these
representations, counts and hashes describe the declared JSON serialization, not
nonexistent original UTF-8 text bytes.

Each capture records source and retained byte counts, a full-source SHA-256 only
when all source bytes were observed, complete/partial/unavailable status, and
explicit truncation/redaction/reason flags. A prefix read of an oversized file
has no full-source digest. If a bounded result was fully read before JSON parsing
failed, its full-source hash can still be retained with a truncated capture. Unicode is cut at codepoint boundaries; invalid UTF-8
artifact replacement is explicitly partial. Opaque JSON components preserve the
observed parsed value serialization, not the original result-file formatting.

“Complete” refers to a particular capture (or all listed captures in an envelope),
not the entire generator state, absent fields, recognition correctness, or the
full original result file. A missing optional field remains explicitly unavailable.
For a redacted capture, its digest describes the original fully observed bytes,
not the redacted text. A hash cannot reconstruct missing text. Absent raw_text/schema_errors fields do
not create an empty-content hash or zero count. Explicit empty string/list fields
do produce an observed empty hash/zero count; count and hash persist independently.

SQLite persistence checks the existing resource/disk reserve with additional
space for envelope data, pages, indexes and WAL. A candidate response over 2 MiB becomes an explicit failed candidate with
quarantined bounded evidence and no usable tracks. Invalid final envelopes or a
failed write abort the window transaction. The cursor cannot advance past undurable
evidence. Per-window bounds do not imply a fixed lifetime database size; retained
recordings/results continue to consume disk and remain subject to disk reserves
and normal session erasure.

## Storage, ownership, and private export

An additive migration creates overlap_support_diagnostics. The immutable logical
key is (chunk_id, window_start, diagnostic_key). Matching retries are idempotent;
a conflicting retry fails without overwriting original evidence. The existing
Store.commit_window transaction persists evidence, candidates and the cursor
atomically. Chunk/session deletion cascades to diagnostics. Application erasure
does not guarantee secure physical erasure or remove external backups.

Only trusted support bounds are translated from analyzer-window to core-relative
to session-absolute coordinates. Opaque model timestamps stay exactly as captured
and remain support-relative/unverified. A shared support is recorded once per
owning chunk/window, not once per linked overlap region.

Two explicit local Store methods support private maintenance/export:

- Store.overlap_diagnostics(session_id, after=0, limit=10): immutable diagnostic-ID
  pagination, with raw private envelopes. Follow next_cursor until the returned
  diagnostics list is empty.
- Store.overlap_diagnostic_status(session_id, after=0, limit=100): region-ID
  pagination with support/batch diagnostic references and availability labels.

These methods are deliberately not mounted as HTTP routes. Ordinary candidate
responses, changes feeds, transcripts and identity projections exclude private
captures and keep their previous shapes. Consumers of a private export must
reconcile region references and diagnostic row IDs separately; a support may
serve multiple regions. Missing support references may point only to a batch
failure, or have no evidence.

For historical rows, the private status projection reports hash_and_counts_only,
status_and_reason_only, or phrase_raw_text_only with unavailable full-generation
raw output/schema details/diagnostic phrases. It does not synthesize diagnostic
rows or claim that old hashes are recoverable text. Migration preserves existing
candidate rows and never backfills lost raw output.

## Verification boundary

Focused tests cover Unicode/control/surrogate byte budgets, invalid UTF-8 and
oversized/missing/malformed output, native-budget and invalid-timestamp evidence,
privacy redaction, shared-support associations, pagination, a synthetic empty
schema extracted from exact dev8 5ec6c9d, atomic rollback/retry, disk reserve failure,
public API/change-feed privacy, main transcript/identity invariance, and erasure.
They make no real-model accuracy, throughput, deployment, or long-run claim.
