# Private offline real-provider bridge

Implementation only. Tests use synthetic PCM and fake native/analyzer results.
Neither a passing contract suite nor a freeze receipt authorizes model execution.
No HTTP routes, legacy result tables, enrollment, identity bank or event taxonomy
are changed. The default matcher remains v1; this experiment opts into p1.

## Reviewed dependencies

- Runtime: `39f34e494952595c3882ca0b74a6c6a0bfcee9ab`.
- Matcher source: `febde6fa9bba49d433bf1a238f717909001f3403`.
- Matcher evidence: `8dd40c04c17d8ea29e54fee81502d9fef9ff3011`.
- Fixture manifest: `b1440539eadc526966440b14b1dee74d548fd35b4b359f6e4d271a9ab6afe702`.
- Opt-in policy: `exact-pcm16-v2q1p1`; continuity declarations remain required.
  Exact equality cannot distinguish a true duplicate from deliberately replayed
  audio under false provenance. Gold ranges never enter the matcher.

## Driver and actual providers

`scripts/benchmarks/compare_fragment_models.py` defaults to model-free `protocol`.
`freeze` hashes existing files without model construction, extraction or copies.
A freeze requires committed source and binds the actual interpreter, transitive
installed inference dependencies, original/alias sources, model bytes, native
runtime companions, exact wrapper sources, config and reviewed base commits.
It records actual imported paths and refuses another checkout. Per-call checks
use inode/device/size/mtime/ctime; final reporting requires a full rehash. This is
local change detection, not protection against an adversarial filesystem owner.

`base` and `moss` require a separate explicit parent approval receipt containing
its authorization reference, exact freeze digest, selected arm list, stage,
per-category call/audio limits, CPU affinity, RSS cap and resource-observation
contract. A file is an operational receipt, not independent authority. No retry,
replay, native probe, full eight-arm matrix or MOSS stage is implicitly admitted.
Existing output cannot be silently resumed with a fresh zeroed budget. A separate
MOSS attempt is durably fenced before any model call; failed/interrupted attempts
cannot rerun silently. All SQLite connections close explicitly, independently of GC.

- NativeBridge wraps the existing NativeProcess, never NativeCoordinator.
  Contiguous input is acknowledged exactly in integer frames. Input/stable and
  quantized activity frontiers remain separate. All simultaneous tracks are kept.
  Finish requires the exact durable control, terminal epoch and final frame.
  A factory owns closure across arm completion, rollover, exceptions and replay.
- NativeProcess has a no-op-by-default response observer so even `_error` JSON
  survives its exception translation. Native runner adds opt-in actual-import
  attestation and a smaller response limit only with private harness config.
  The ordinary service protocol is otherwise unchanged.
- AnalyzerBridge uses explicit immutable scheduler context, original native
  provenance and read-relative native turns with `diarization_backend=nemotron`.
  Ordinary lexical reconciliation uses a stable mixture input track, never a
  person. Raw local speaker/null assignments, confidence, embeddings, raw text,
  event candidates, source labels, overlap metadata and errors remain private.
- SenseVoice storage category `native_word` does not strengthen its timing:
  starts are native CTC anchors; ends are estimates; Latin pieces may be joined;
  no forced alignment or Chinese character-time interpolation is claimed.
- Unsupported empty lexical units fail with retained raw evidence. Punctuation
  remains supported. Native support and original anchor survive separately from
  routing anchors; the final raw blob records reconciliation and publication
  indexes, including identical-raw-index ambiguity instead of guessing.

## Durable evidence

The only extra logical core field is optional `evidence_ref`:
`{schema:"fragment-real-evidence-v1",sha256:<64hex>,bytes:<1..2097152>}`.
The fixed experiment root resolves only hash-named JSON, never supplied paths.
Canonical bounded bytes are fsynced, renamed and directory-fsynced before SQL.
A lifetime exclusive evidence lease protects file-before-transaction ordering
against concurrent cleanup. References are checked inside the same authoritative
snapshot-fenced transaction as output/cursor/tail/checkpoint/EOS acknowledgement.
Resume/read rejects missing, changed, symlinked or differently bound evidence.
A crash before SQL may leave an orphan. Cleanup verifies all core and separate
candidate/failure roots first and removes only unreferenced recognized files.
Creating a stage ledger durably marks the evidence root as requiring a comprehensive
external-reference provider. Generic core-only cleanup then refuses, including after
process restart, until that complete inventory is supplied.

All real evidence and hash freezes are admitted only under an explicit `reports/private_*`
subtree inside the checkout; the existing source packager excludes that subtree.
Private candidate/failure stages use an additional experiment-only ledger,
separate from logical core publication. MOSS is not silently integrated into the
logical durable candidate contract. Failed raw batches are retained even when
candidate parsing produces zero tracks.

## Actual-call accounting

Attempts are reserved before execution and never refunded. Actual submodel
compute/decode hooks preserve the original analyzer scheduling and models.

| Category | Full-matrix maximum |
|---|---:|
| Runs / analyzer calls | 8 / 12 |
| Native reset / push / finish / turn query | 8 / 8 / 8 / 16 |
| Native input | 4,423,360 frames = 276.46 s |
| Analyzer reads | 4,679,360 frames = 292.46 s |
| SenseVoice decodes | 384 / 720 s; each analyzer <=32 decodes / 60 s |
| ERes2Net compute | 96 / 1,920 s; <=8 slots per request; <=20 s per pool |
| Zipformer compute | 108 / 540 padded inference seconds |
| MOSS | <=2 B supports / 22.46 s / one model load |

Every compute input is hashed as actual little-endian float32 samples. ERes2Net
calls also capture the existing clean_speaker_audio selection before clipping,
validate its hash/count against the actual compute input, and map complete
selected intervals through exact logical read/source owners. A >512-interval
truncated inventory is explicitly unavailable as a complete map. This does not
reuse or claim the older physical-upload ffmpeg decode contract, certify purity,
or enroll a person. Event internal padding is recorded as inference padding.

MOSS remains a separate C++ CPU Q8 0.2.3 stage, only B native_overlap supports,
never a gold trigger. Supports are scoped by arm and window. Parse failures,
raw text, diagnostic phrases, truncation and provider metadata remain in raw
batch evidence. Candidate speakers stay anonymous, unresolved and profile
ineligible. Actual token counts remain unknown; only <=512/support is asserted.

## Resource observation and stopping

Implementation tests use CPU7, one math thread, <=512 MiB own RSS and <=64 MiB
for clone plus reports/temp. Every stage keeps host available RAM >=2 GiB and
free disk >=5.5 GiB after projected writes. Processes are bound by PID plus
creation time and PPid scans, not `/proc/task/.../children`. Short-lived exits
and zombies are normal; missing unobserved project roots fail closed.

Real base-stage initial approval is limited to <=1 GiB own RSS, subject to actual
preflight. MOSS admission is separate after base resource evidence. A final
backend target of 6 GiB (maximum 10 GiB) is not a second allowance for concurrent
experiments. Same-namespace aggregate project RSS is capped at 6 GiB.

An alternative, explicitly approved cross-namespace contract can combine the
own-tree sample with an existing read-only endurance feed. Metrics and progress
must both be <=15 s old. A missing optional progress wall field is legitimate;
partial append uses the previous still-fresh sample. Initial transition retry is
bounded to one second. Feed PIDs are never signaled. This sum is an asynchronous
estimate, not verified instantaneous aggregate RSS or an aggregate peak claim.
Every additional concurrent project must be identified by the approved roster.

The standalone experiment watchdog samples at 50 ms, enforces operation and
outer deadlines, and terminates only its own identified tree on failure. Process exit remains
guaranteed even if diagnostic writes or child cleanup fail; existing child
parent-death watchers are independently fault-tested. This is
sampled enforcement, not a kernel-hard memory guarantee. Limits or impossible
frontiers produce explicit partial coverage; there is no automatic inference
retry. Source bytes, active service, running long run and model installations
remain untouched.

## Comparison claims

PCM/read hashes and exact ownership are independent of recognition quality.
English diagnostics disclose partial edge exclusions, native-gold midpoint
seam neighborhoods and whole-excerpt edit alignment. Ambiguous boundary
insertions are surfaced rather than forced into a local WER. Chinese speaker
metrics use one anonymous overlap-optimal assignment with zero-collar,
overlap-aware DER. Candidate CER includes all reference characters, missing
speakers as deletions and unmatched text as insertions; repeat substrings use
full-reference alignment, not invented character times. F laughter quality is
`unsupported_label`; raw top-label mentions are diagnostics only. Equal model
outputs do not imply correct recognition, person identity or acoustic purity.
