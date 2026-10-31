# Finalized PCM16 HTTP experiment

Status: opt-in experimental integration with a pinned local CPU launcher.
One independently audited 12-second real CPU loopback HTTP smoke at commit
`0de25be7` verified exact input receipts, one atomic final core/true EOS, duplicate
upload/finish idempotency, paginated output and positive provider cleanup. It
produced 22 lexical rows, four native turns and no public events. The original
private harness exited 1 because it required service exit 0; Uvicorn 0.54.0
gracefully shut down and replayed the planned SIGTERM, yielding retained wait
status -15. That failed terminal is preserved; a separately reviewed, model-free
classification accepts only the recorded complete shutdown and positive cleanup.
No neural rerun was used. This short check does not establish accuracy, positive
events, overlapping-word quality, endurance or other-platform support. The
legacy worker and default launcher remain unchanged.

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
launcher is described below; its actual HTTP evidence is limited to the short
audited smoke described above.

## Reproducible model-free checks

`python -m pytest -q tests/test_fragment_http.py` uses synthetic providers through
the actual AnalyzerBridge and FastAPI TestClient. It covers idempotency, true
EOS edges, immutable sealing, raw scores and attribution, overlapping native
turns, whole/fragment PCM read hashes, fault rollback, source mutation, controls,
300-second restart/replay, pagination and erasure races. Passing these checks
establishes software behavior only, not neural accuracy or operational readiness.

Unmanaged injected factories must clean partially constructed providers and
bound their own blocked calls. Managed CPU contexts add `generation`, `stop()`
and an ownership scope; controls stop the transport and kill/reap owned children.
Neither interruption nor process exit implies native EOS.

## Explicit CPU launcher and recovery

### Build a local manifest without inference

The supported initial profile is Linux x86_64, Python 3.12, SenseVoice/native
Nemotron/ERes2Net/Zipformer CPU with no extra separation. Prepare the models and
native CPU installation explicitly using the existing setup documentation,
including the model licenses. The builder never installs, downloads, changes a
dependency, starts a server/child or constructs a model. It does import the
trusted inference wrappers to check their actual local object/data closure.

In an explicitly prepared environment, the supported dependency recipe is:

```sh
python -m pip install -e '.[inference]' numpy==2.5.3 sherpa-onnx==1.13.8 \
  sherpa-onnx-core==1.13.8 soundfile==0.14.0 opencc-python-reimplemented==0.1.7 \
  psutil==7.2.2 cffi==2.1.1
```

This is a separate, user-invoked installation step, not something the builder
performs. Other dependency versions fail with an actionable error; broad legacy
dependency ranges are not evidence of support for this frozen CPU profile.

Create a limits JSON with every field below. These are explicit example ceilings,
not automatic host admission or measured requirements. Size them for the host,
other workloads and recording length before serving. The builder prints derived
maximum call/frame debits without reserving resources or increasing any ceiling.
The one-hour example can be changed; there is no fixed 330-second product limit.

```json
{
  "max_source_frames": 57600000,
  "max_run_attempts": 2,
  "max_core_attempts": 2,
  "handshake_seconds": 120,
  "request_seconds": 180,
  "run_seconds": 7200,
  "tree_rss_bytes": 4294967296,
  "host_reserve_bytes": 2147483648,
  "disk_reserve_bytes": 5368709120,
  "scratch_bytes": 16777216,
  "retained_bytes": 268435456
}
```

Save it as `cpu-limits.json`, then build/check the environment-specific manifest:

```sh
python scripts/build_fragment_cpu_manifest.py --asset-root . --model-dir ./models \
  --limits cpu-limits.json --output cpu-provider.json > cpu-provider-build.json
SHA=$(python -c 'import json; print(json.load(open("cpu-provider-build.json"))["sha256"])')
python -m audio_backend --fragment-cpu-manifest cpu-provider.json \
  --fragment-cpu-sha256 "$SHA" --fragment-cpu-check
python -m audio_backend --fragment-cpu-manifest cpu-provider.json \
  --fragment-cpu-sha256 "$SHA" --data-dir ./fragment-data --host 127.0.0.1 --port 8765
```

`--asset-root` may point to a separate existing prepared installation; its public
model manifests must match this source checkout, and its relative native receipt
is verified/rebased rather than reusing its old absolute settings. Runtime
companions, model/license bytes, local interpreter, source and fixed dependency
package roots are hashed. Actual lazy imports, OpenCC's precise dictionary chain
and selected libsndfile/framework objects are checked without model creation.
Expected native metadata comes from the same pure function used after the live
provider's actual checks; no prior inference receipt or private freeze is needed.
The manifest is fsynced and published without overwriting an existing file, then
its exact file SHA256 is printed. If publication fails, inspect the output before
retrying; a directory-fsync error may leave a valid but unacknowledged file.

Move source/assets or change dependencies? Rebuild into a new output file and
rerun check-only. Never edit/rebase paths in a frozen manifest. A generated pin
records local bytes, not a new model-quality or platform certification. The
generation command requires its installed wheel file inventories; incomplete or
unsupported runtime closure fails closed. Keep generated manifests and receipts
private because they contain local paths.

For an already built manifest, use `python -m audio_backend --fragment-cpu-manifest FILE
--fragment-cpu-sha256 SHA256 --fragment-cpu-check` first. Check-only performs
local hashing and validation without models, children, a database or listener.
Omit `--fragment-cpu-check` to serve; `--data-dir`, `--host` and `--port` remain
available. Server startup performs no setup, download or implicit asset discovery.

The bounded JSON manifest uses schema `fragment-cpu-provider-v1` and exactly
`config`, `files`, `limits`, `native_provenance`. `Manifest` in
`audio_backend/fragment_cpu_provider.py` defines the strict field contract.
`files` maps canonical paths to byte counts/SHA256 for every actual consumed
model, runtime companion, imported wrapper/dependency and interpreter. Supply
the approved local configuration and expected native provenance explicitly.
Before any model constructor, child handshake preloads and attests the actual
lazy inference modules. OpenCC conversion data and actual libsndfile/framework
objects are pinned; native ABI companions are checked after library mapping and
before model creation. Ambient LD_LIBRARY_PATH, LD_PRELOAD and LD_AUDIT overrides
are rejected. Interpreter/OS system libraries remain the trusted platform
boundary. Parent projection/runtime imports are pinned separately; check-only
performs local-file validation, while actual loaded-object attestation requires
the child handshake. Public manifest views are defensive copies.
Settings match the evaluated SenseVoice, Nemotron, ERes2Net and Zipformer CPU
path. Extra separation/MOSS is disabled; independent native overlap tracks are
preserved. Mixture words are not asserted to resolve every overlapping voice.

Finite source-duration, run/core-attempt, wall/request, RSS, host/disk reserve,
scratch and retained-evidence ceilings are explicit per deployment. Total call,
audio and replay ceilings derive from the sealed recording length and attempts;
there is no fixed 330-second recording limit. Reservations commit before model
construction/compute and survive pause/retry/restart without refund. Metadata
counters are bounded logical byte accounting, not a filesystem quota. RSS/disk
polling is not a hard OS limit; deployment admission must allow safety headroom.

Linux inherited sockets require no bind/listen. Both trusted child processes
use dedicated groups, inherited CPU affinity capped at two CPUs, thread caps and
nice19. Stdout/stderr are discarded; bounded error/call evidence is durable in
SQLite. Admitted sherpa/ctypes engines create threads, not helpers. Arbitrary
plugins, daemonization and other backends are prohibited. Observed unexpected
descendants leave ownership pending; this is not a hostile-code sandbox.

Lease scratch is outside generic `tmp` cleanup. Shutdown keeps instance ownership
when the worker or cleanup remains unresolved; health reports the blocker.
While the API is stopped, use the same manifest options with
`--fragment-cpu-inspect-lease LEASE`; reconciliation additionally requires
`--fragment-cpu-reconcile INSPECTION_SHA256`. The command takes the instance lock,
rechecks the exact lease/manifest/generation, original boot/PID namespace, known
identities and empty groups, and refuses unknown/live ownership. Gone/reused
identities are termination observations, never fabricated historical reaping.
A crash between durable spawn intent and PID registration is recoverable only
when the original owner is positively gone in the same boot/PID namespace and a
complete bounded process scan finds no admitted child or ambiguous candidate.
The scan binds the persisted exact command, unique process token, parent identity
and trusted no-daemonizing topology; inaccessible or incomplete scans remain
blocked. Observed live ownership must terminate through its original retained
owner or a separately authorized identity-bound operator action, then be
reinspected. There is no force-clear action or bare-PID kill. Reconciliation
never resets attempt budgets.

Provider projection v2 composes the independently reviewed `74b51d2` whitespace
inventory with the existing normalized-source branches. Public projection
recomputes exact lexical/nonlexical accounting and ordered raw reconstruction;
whitespace is not classified as silence. Original v1 evidence remains readable.
