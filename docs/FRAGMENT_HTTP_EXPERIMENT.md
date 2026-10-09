# Finalized PCM16 HTTP experiment

Status: opt-in **integration slice**, model-free tested only. This is not a
real-provider launcher, model-quality acceptance, or a replacement for the
legacy streaming worker. No CLI flag enables it and no provider is supplied by
default. Neural resource budgets/supervision remain the provider's responsibility.

## Admission and API

An embedding application must explicitly pass `fragment_provider_factory` to
`create_app`. Only then may `POST /sessions` select
`{"processing_mode":"fragment_pcm16_v1"}`. Ordinary sessions remain `legacy`.
The experimental session has an isolated identity group; custom identity groups
and identity modes are rejected, and identity enrollment/correction/review writes
are disabled. The public projection does not create observations or identities.

This first slice accepts one stream per session, starting at logical frame zero:

- Upload original 16 kHz, mono, PCM16 WAV bytes through the existing chunks PUT.
- Supply `stream_id` to explicitly declare consecutive parts of the same capture.
  The server does **not** infer same-capture provenance from equal audio.
- No decoding, resampling, gap repair, overlap matching, or trimming occurs. Every
  supplied frame is retained, including a repeated fragment with a new key.
- Offsets must equal the exact preceding frame count / 16000. There are at most
  128 receipts, nonfinal receipts retain the existing 0.5-second minimum, and the
  configured total audio-duration limit applies to the complete stream.
- Uploads are server-hashed and their PCM format/frame count is verified. Files
  are reverified at sealing and by the logical reader on a fresh binding/restart.
- `stream_final=true` seals in the receipt transaction. Alternatively, the
  existing finish endpoint seals in one transaction. Retried finish creates no
  new plan/control. There is no processing until the immutable plan is sealed.
- Exact duplicate bytes and metadata retain the original receipt. A reused key
  with different bytes/metadata, or a new receipt after seal, is rejected.

The normal worker excludes experimental receipts. Its explicit experimental
adapter runs only `LogicalRunner`, with the original 30-second core scheduler,
300-second epochs, bounded replay and explicit native true-EOS acknowledgment.
A final sample is owned exactly; cancellation is never a substitute for EOS.

## Public output and uncertainty

Existing segments, speaker-turns, events and changes endpoints keep their cursor
and pagination contracts. They read indexed materialized public rows; no read
endpoint loads or scans the immutable core ledger. Experimental rows add:

- `projection_version`: `fragment-http-public-v1`
- `source_track_id`: evidence-scoped track, never a global identity
- `evidence_sha256`: durable private evidence content hash
- `evidence_item`: canonical JSON identifying corresponding raw item indexes

The first upload receipt is only the storage foreign-key anchor for public
rows. All start/end times are already on the logical session axis. They must
**not** be rebased by that receipt's duration or current upload position.

The projection requires the actual AnalyzerBridge raw evidence and matching
publication envelope. Event scores come from matching raw event items; missing,
invalid or changed scores fail publication. Lexical confidence is the raw unit
confidence or null. Identical raw units with disagreeing local attribution stay
unassigned. Local labels are epoch-scoped and have `speaker_id=null`; matching
and enrollment are disabled. Independent simultaneous native speaker turns are
retained separately and marked overlapping. Mixture ASR is not asserted to be
separated per-speaker transcription.

The `event-candidates` and `overlap-candidates` endpoints return an explicit 409
unsupported-capability response in this mode; an empty page would be misleading.
Raw event-candidate metadata is retained durably but is not projected in this
slice. Separate overlap-text candidates are not supported in this narrow slice. A
nonempty raw overlap-candidate collection fails closed rather than silently
omitting it. Existing legacy overlap behavior is unchanged. Raw nonlexical or
whitespace evidence remains in the provider evidence; the provider adapter must
supply its reviewed explicit accounting rather than silently discard speech.

## Atomicity, controls, and erasure

Public rows and their change-feed records are inserted in the same SQLite
transaction as the immutable core, lexical tail, cursor and terminal
acknowledgment. `Store.commit_window` is not called. The legacy identity and
chunk-relative timestamp machinery is therefore bypassed.

Raw evidence uses a session-scoped, content-addressed SQLite adapter implementing
`put`, `read`, and `verify_core`. It survives startup cleanup of `tmp`. Raw failed
calls can precede a successful core, but neither a blob nor a failed call creates
public output. Reads verify the original evidence hash and core binding.

Pause/resume/retry/cancel apply to the entire experimental stream, whichever
receipt is addressed. Every control changes a durable generation; work started
under an older generation cannot commit even after an immediate resume. Resume
of an unsealed paused stream returns it to collecting. A cancelled stream cannot
be finished or restarted. Shutdown/recovery reconstructs providers from durable
state once prior provider ownership has been released; it never persists live
native state as authoritative. Unexpected process loss leaves a durable
ownership blocker requiring independent cleanup verification before resumption.

Deletion requires the live worker/upload to release the session, as in the
existing API. Within the deletion transaction, a tombstone fences writes and
permits erasure of this session's otherwise immutable plans and core ledger.
All raw evidence, including failed-call/orphan blobs, controls and public rows
are erased together. Late evidence writes are refused. Original upload files
use the existing durable file-cleanup receipts. Application erasure does not
claim forensic SQLite/disk secure deletion or erase backups.

## Explicit provider factory contract

`factory.policy` must be a frozen `RuntimePolicy` whose fingerprints match the
actual providers. `factory(context)` returns `(analyzer, native_factory)`:

- `context.settings`: the application's resource/configuration settings
- `context.snapshot`: sealed run/plan/policy and the durable EOS control
- `context.paths`: server-owned original WAV transport bindings by source ID
- `context.evidence_store`: the scoped SQLiteEvidenceStore adapter
- `context.temp_root`: scratch-only provider input files

`analyzer` implements LogicalRunner's analyzer contract, including evidence
binding/finalization. Existing `AnalyzerBridge` is compatible. `native_factory`
implements the callable and closeable `NativeFactory` ownership contract and
returns a new native provider on replay/epoch changes. Both must close all child
processes when closed. Provider ownership is durably marked pending before factory construction and
released only after successful cleanup. Abrupt process loss or inability to
persist the release leaves the marker pending; startup does not assume that old
children died. Both cleanup attempts run even if one fails. A cleanup
failure is exposed in health/chunk errors and blocks all worker claims and affected-session erasure
until provider ownership is explicitly inspected and repaired. This durable
blocker survives application restart; this slice deliberately has no endpoint
that clears it or treats restart alone as proof of cleanup; it does not
kill the worker thread or revise already durable public output. A real NativeBridge should receive the snapshot's exact
EOS control ID, epoch and final frame; it must not infer EOS from a request to
stop, a pause, process exit, or an ordinary 300-second epoch seam.

The factory must independently bind/verify the approved local model/runtime
fingerprints, actual model-call budgets, CPU/thread/affinity limits and aggregate
process-tree resource supervisor. This module neither installs dependencies,
loads a default model, expands a budget, nor starts a paid API. A real-provider
launcher and neural HTTP acceptance remain explicitly unimplemented/unverified.

## Reproducible model-free checks

`python -m pytest -q tests/test_fragment_http.py` uses synthetic providers through
the actual AnalyzerBridge and FastAPI TestClient. It covers idempotency, true
EOS edges, immutable sealing, raw scores and attribution, overlapping native
turns, whole/fragment PCM read hashes, fault rollback, source mutation, controls,
300-second restart/replay, pagination and erasure races. Passing these checks
establishes software behavior only, not neural accuracy or operational readiness.

Factory construction must clean up every partially constructed provider if it
raises before returning the pair; the caller cannot close references it never
received. Controls fence evidence/publication atomically but do not preempt an
already blocked provider call. Until that call returns, deletion remains blocked.
Before a real launcher is admitted, every provider must have a bounded,
cancellable lifecycle with child-process ownership verified by its supervisor.
No timeout, wall-clock wait, or service restart is itself proof of native EOS or
successful child cleanup.
