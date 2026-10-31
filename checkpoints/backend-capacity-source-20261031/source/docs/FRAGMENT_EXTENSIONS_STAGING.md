# Reviewed private overlap-retention composition: isolated dev10 staging

This is local development staging, not a release or active/public promotion.
The earlier dev6 composition preserved both reviewed histories: finalized PCM16 CPU/builder
`4ab61e7d54b2365195c0e5c2cc3ad3e0ba841efb` and verified canonical-person
`429de02166fd18e77088489763abc233b33426f0`, sharing base
`867a215a0578bb23885d175623a36ee3ed04d238`. Earlier dev4/dev5 import facts
remain intact. Private evidence, recordings, model assets, vectors and
operational state are not source imports. No dependencies are installed.

The historical dev7 composition retains dev6
`7e2ce6cea70c622f6979fe5b9c517ff87dfa11ff` and the independently reviewed ASR
coverage candidate `9b44a401143ed339ff2e21e3063201e5d044d232` as separate merge
parents, with merge base `4ab61e7d54b2365195c0e5c2cc3ad3e0ba841efb`. The new
`coverage_composition` layer records actual composed blobs and both parent inputs;
the historical dev6 `composition` and both local-followup records stay unchanged.
Historical blob verification reads immutable parent trees, while the new layer
binds the current worktree/index. Inherited paths must match the selected parent
exactly. The historical dev7 source union has 259 paths; its local budget is at most 260
files / 2,950,000 bytes, with the unchanged 250,000-byte per-file ceiling.

The coverage ledger records actual integer ASR input and separates invocation
started from confirmed completion. It reports successful core support and its
complement, without repairing recognition or automatically restoring words.
Unsubmitted input is not silence. See [the coverage contract](ASR_INPUT_COVERAGE.md).
The canonical/fragment identity guards and default-off modes are unchanged.

## Current dev10 private overlap-retention composition

This source-only composition preserves the two reviewed branch histories with
merge `117e8414c4f524477db83886ab3731a5c3afc0b4`: dev9 first parent
`3b7f5a9dcee0d626348d5354531d1a8bad6bed7b`, retention feature second parent
`9ba755a87f599c1b3743388594da6ae431e82a64`, and immutable dev8 merge base
`5ec6c9d5bd3febf2c30695120b969e6b70a1c0d2`. The disjoint pure union contains
272 paths and 3,149,704 bytes, without conflict resolutions or feature edits.

The `overlap_diagnostic_retention_composition` record pins the actual ordered
merge parents, merge base and merge commit, all eleven retention feature blobs
with dev8/dev9 predecessor or absence pins, and five non-manifest integration
blobs. Current version, policy, staging documentation and source tests are the
only six provenance-integration paths. The layered manifest is not self-hashed;
exact structure, immutable historical records and worktree/index equality
constrain it. Every dev9/historical record stays unchanged except the explicit
current target version; historical tests continue checking immutable source trees.
Both the duration/EOS and retention feature blobs stay byte-identical to their
reviewed commits. See [private retention semantics](OVERLAP_DIAGNOSTIC_RETENTION.md).

Retention is prospective and bounded. Failed raw text, schema details and opaque
model phrase evidence remain private, with truthful missing/truncated/redacted
labels. Invalid evidence never becomes usable text or identity/profile state.
This does not recover historical output that was never persisted. Ordinary public
API shapes, original-mixture text and identity isolation remain unchanged. A live
or frozen run is not upgraded merely because this local source composition exists.

The exact current allowlist has 272 paths: the immutable dev9 inventory plus the
four reviewed retention module/document/synthetic-fixture/test additions. The
3,200,000-byte aggregate limit, 250,000-byte per-file limit, common source checker
and all privacy exclusions remain unchanged. Provenance-only growth is capped
at 30,000 bytes relative to the pure merge.

Focused model-free feature/provenance tests and independent review establish only
their checked source behavior. Prior failing review cases remain in their original
evidence; corrections do not rewrite those outcomes. No complete-suite, real-model,
recognition-quality, speaker-quality or long-run acceptance transfers from dev8,
dev9 or historical endurance results to dev10. No model call, active-run migration
or public deployment is part of this composition.

## Historical dev9 duration/EOS follow-up

This source-only integration starts from immutable dev8
`5ec6c9d5bd3febf2c30695120b969e6b70a1c0d2` and pins the five-path corrective
feature at `bbf46874885423cf998426d4c7cb3f451939e595`. The separate
`canonical_duration_eos_followup` record identifies each feature blob, its
original dev8 predecessor or absence, and the five non-manifest integration
blobs. The layered manifest itself is checked by exact structure, historical
value preservation and worktree/index equality, rather than a circular self-pin.
All historical imports and composition records remain unchanged and are verified
against their immutable source trees. The current tree is exactly the dev8
inventory plus `tests/test_canonical_duration_eos.py`; every other byte change
must belong to the five feature paths or six integration paths.

The feature uses exact same-rate PCM/FLAC frame metadata when eligible and
source-relative native frame scheduling. Stateful native terminal decoding
rejects duration mismatches; legacy windows retain measured resampled ranges.
Decoder errors fail both backends. Generic AAC/edit-list/resampling reconciliation,
old accumulated rounded-receipt stream drift and whole-file sequential integrity
remain unresolved. See [the duration and EOS contract](API.md).

The earlier `a58732810e629aada258c4ae37394ecd871afd2b` full model-free baseline
retained five failures: four current-provenance gaps and one genuine legacy
44.1 kHz resampling regression. The corrective commit fixes only that legacy
guard scope, with independent focused review. This provenance integration does
not erase those results or itself establish a complete-suite pass. Long-run,
ASR and speaker-quality acceptance remain unestablished for the changed source;
no historical 12-hour or V03 result transfers to this native implementation.

The exact dev9 allowlist has 268 paths. The 3,200,000-byte aggregate limit,
250,000-byte per-file limit, source checker and all privacy exclusions remain
unchanged. Version and current provenance are separate from feature behavior.

## Historical dev8 composition and limits

This isolated composition retains event-coverage commit
`440701a24bbef0e7053b2aa90cd2e3b03259938c` and corrected Qwen candidate commit
`9585cd5d9afbb512e7e70f8c43c97987c066000b` as its two parent histories, with frozen
dev7 `1cbd6e62da69b7abc0ecac6425b46401f76fc294` as their merge base. The historical
`candidate_evaluation_composition` record binds the dev8 composed blobs and every
unchanged candidate import. All prior `composition`, `coverage_composition`,
import and local-followup records remain unchanged and are tested against their
immutable historical trees. The self-referential layered manifest is excluded
from its own blob list; structural/history tests verify it separately.

The 267-path exact source union has a dev8-local ceiling of 267 files and
3,200,000 bytes, retaining the 250,000-byte per-file limit and every privacy
exclusion. Frozen dev7 policies and reproduction pins are not rewritten.

- Qwen is an explicit opt-in offline candidate CLI and review ledger. It does
  not become default ASR, HTTP integration, automatic transcript repair, or
  evidence of 12–48-hour validation. The corrective recovery/process-fencing
  history is preserved. Separately authorized real-model smoke results remain
  external evidence; this model-free integration does not establish quality.
- The event change only adds annotation-aware evaluation coverage. The exact
  legacy diagnostic remains separate; incompatible references cannot become a
  zero-percent six-target accuracy claim. Production labels, thresholds, models
  and default behavior are unchanged. Research rail candidates are not inserted.
  See [the evaluation contract](EVENT_EVALUATION_COVERAGE.md).

Dev8 model-free checks use CPU0, nice19, an owned RSS ceiling of 512 MiB, a
5 GiB free-disk floor and 2 GiB host-available floor. Ordinary files retain a
64 MiB worktree/scratch cap. The two unchanged decoded-source process-control
nodes retain their explicit CPU7 synthetic-child default as a narrow exception;
their test owner stays CPU0/nice19. No product default is changed for the harness.

The single unchanged 10k/100k-row queue comparison cannot be split internally.
Its initial 64 MiB planning block and failed 112 MiB attempt remain evidence.
The one separately authorized retry has a 256 MiB aggregate artifact allowance,
192 MiB early warning and 224 MiB sampled stop, admitted only with at least
5 GiB + 256 MiB free. The runtime 5 GiB floor remains enforced. Sampling requests
20 ms intervals; it is not an instantaneous hard quota. Per-file maxima and
final SQLite page/WAL observations preserve the checkpoint-overlap explanation.
Tests retain original cases, parameters and assertions. Temporary test roots are
removed only after their results and any failure metadata are recorded.
Resource-blocked cases remain explicitly incomplete until an authorized unchanged
rerun succeeds. The complete default collection is executed in bounded file/case
runs, not claimed as one whole-process invocation. Original failed attempts,
skips and strict xfails remain evidence. No inference, download, publication or
release is part of this composition.

## Current scope: explicit internal experiments, default off

- The explicitly configured finalized-original-PCM16 HTTP mode has an offline
  CPU manifest builder and logical-runtime worker route. It retains independent
  native overlap tracks, atomically publishes evidence-backed rows/cursors/EOS,
  and disables global identity enrollment, review and canonical-relation
  mutations. Verified legacy groups remain separate and usable in the same app.
  See [the exact HTTP/profile/quickstart scope](FRAGMENT_HTTP_EXPERIMENT.md).
- The finalized normalized runtime adapter explicitly connects the reviewed
  PCM16 normalizer to internal logical scheduling and provider bridge classes.
  This supersedes the earlier dev4 statement that the normalizer had no internal
  runtime adapter. The exact-PCM default store/reader remain restricted to
  original 16 kHz mono PCM16. Normalized admission is explicit: finalized,
  single continuous island, same-rate 44.1/48 kHz PCM16 WAV, mono/stereo and the
  existing minimum length. No HTTP/upload/production-worker integration.
- Normalized scheduler/model timing remains canonical 16 kHz; original native
  source coordinates and conservative many-to-many filter support remain
  distinct. No fabricated one-to-one native sample map or global identity claim.
  The adapter launches a separate bounded FFmpeg process per <=0.5-second read;
  model-free correctness tests do not establish throughput or real-model quality.
- Decoded-source receipts and the rolling emitted-PCM reader are a separate
  Linux-only, read-only, explicit default-off experiment. They are NOT admitted
  into SourcePlan, Alias, auto-trimming, normalized runtime, HTTP or workers.
  The compressed file bytes, decode-policy receipt and actually emitted PCM
  hashes remain distinct. No automatic compressed ingestion is added.
- AAC endpoint accuracy remains unresolved. Rolling chunks are provisional until
  their own session completes. One active rolling session per process is
  supported; no 48-hour, arbitrary seeking or general streaming claim follows.
- Prior real-provider base matrix event arrays were empty: only negative-event
  invariance was established. Positive-event seams are separately selected
  synthetic development equality/nonempty 30-second boundary coverage, not
  general event recall or production acceptance.
- Native-boundary evidence stays limited: P1 passed; original P2 whole processing
  completed but report-size failed. A separately admitted split-only remainder
  matched retained whole PCM and anonymous intervals. This was not uninterrupted
  P2 success. The 300-second receipt was not EOS; ten commit IDs survived the
  epoch-boundary restart; final one-sample core had no invented speaker activity.
  No ASR/global identity or arbitrary crash recovery quality is established.

## Exact source lineage and bounded packaging

`FRAGMENT_STAGING_IMPORTS.json` remains the unchanged original dev4 flat import
manifest. `FRAGMENT_STAGING_LAYERS.json` pins that manifest's exact SHA256 and
records ordered normalized-runtime, codec, finalized-HTTP and CPU updates.
The builder's separate local-followup pins are retained unchanged. A distinct
composition record binds both parent inputs and each actual composed blob;
combined code is never labelled as an unchanged parent import. Every overwritten path
names its old source commit/hash and newly reviewed source commit/hash. Tests
reject missing paths, duplicate overrides, stale lineage/version and unreviewed
bytes. Original imported docs describe their historical experiment; this scope
page and the layered manifest describe the current integrated source tree.

Historical reviewed source lineage is preserved. Integration provenance,
allowlist, cross-mode tests and current version/docs describe the new composition.
The original dev5 staging budget was 2,500,000 bytes, 250 files and 250,000
bytes per file. The historical dev6 union contained 256 already reviewed source paths,
with a 2,850,000-byte ceiling and the same 250,000-byte per-file maximum. Original
import/layer records and privacy exclusions remain unchanged.
Additional work requires its own audited delta. The older bridge allowlist is historical provenance, not the current
staging policy. The unchanged source guard remains a heuristic review aid.

Historical rejected-q1 assertions retain strict expected failures; unexpected
passes still fail. Private-input tests remain opt-in and explicit. The historical dev7 regressions
used CPU8, nice19, explicit temporary roots and one file per
bounded run: own process-tree RSS <=384 MiB and new generated disk <=96 MiB.
The single 100,003-row queue-scaling test has a separately authorized 256 MiB
disk budget. Each run starts only with at least 5 GiB plus its disk budget free.
Temporary fixtures are cleaned after recording each result; failed logs remain
private evidence. Default model-free collection is inspected before execution;
optional real-input gates remain off.
No neural inference, downloads or frozen-longrun changes are part of this work.

## Corrected codec resource evidence

The earlier independent approximately 35 MB claim measured pytest only, and
an initial rolling sampler measured only its supervisor. Neither is full-tree
RSS. A corrected separate 63-test review measured 86,331,392 bytes for supervisor,
pytest and live decoder children. That is sampled, not OS-hard, peak memory.
The full correction remains in private review evidence with its byte hash;
reports are excluded from source ancestry. These earlier measurements do not
substitute for this staging tree's own monitored regression results.

## Explicit verified canonical-person candidate

The reviewed standalone canonical candidate added exactly `audio_backend/identity_canonical.py` and
`tests/test_verified_identity_canonical.py` to the reviewed source allowlist. Its
exact source inventory was 249 files, with a 2,600,000-byte ceiling; its 250-file
maximum, 250,000-byte per-file maximum and all privacy exclusions are unchanged.
Each union policy is local to its respective composition worktree. Shared policies
and source-guard implementations are unchanged; both prior branches remain intact.

The delta updates the existing verified-review bank, Store, API, change feed and
API documentation to support explicit revision-checked person relations. It
preserves original evidence owners, requires explicit same-person/different-people
claims, and makes merge undo reversible without reassigning later reviews.
Verified mode remains opt-in, with no new anchor admission or automatic learning
from merges. This does not integrate identity into experimental fragment HTTP
processing, calibrate speaker accuracy or establish long-recording quality.
See [the relation and invalidation contract](VERIFIED_IDENTITY.md).

The standalone canonical verification was model-free with its own smaller
resource limits. The composition requires the complete default model-free suite,
explicit fragment/verified cross-mode checks, independent review and the exact
union/provenance/privacy gate before candidate acceptance. This is not publication
or mainline promotion. The prior 12-second HTTP check established infrastructure
behavior only. ASR/global-identity quality acceptance remains false; neither
canonical bookkeeping nor successful long-run completion establishes accuracy.
