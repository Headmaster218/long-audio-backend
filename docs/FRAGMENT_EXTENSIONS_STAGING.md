# Reviewed fragment and canonical-person composition: isolated dev6 staging

This is local development staging, not a release or active/public promotion.
This candidate preserves both reviewed histories: finalized PCM16 CPU/builder
`4ab61e7d54b2365195c0e5c2cc3ad3e0ba841efb` and verified canonical-person
`429de02166fd18e77088489763abc233b33426f0`, sharing base
`867a215a0578bb23885d175623a36ee3ed04d238`. Earlier dev4/dev5 import facts
remain intact. Private evidence, recordings, model assets, vectors and
operational state are not source imports. No dependencies are installed.

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
bytes per file. The exact dev6 union contains 256 already reviewed source paths,
with a 2,850,000-byte ceiling and the same 250,000-byte per-file maximum. Original
import/layer records and privacy exclusions remain unchanged.
Additional work requires its own audited delta. The older bridge allowlist is historical provenance, not the current
staging policy. The unchanged source guard remains a heuristic review aid.

Historical rejected-q1 assertions retain strict expected failures; unexpected
passes still fail. Private-input tests remain opt-in and explicit. Regression
for this composition use CPU8, nice19, explicit temporary roots and one file per
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
The dev6 union policy is local to the new composition worktree. Shared policies
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
