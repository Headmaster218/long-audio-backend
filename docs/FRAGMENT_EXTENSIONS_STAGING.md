# Reviewed fragment extensions: isolated dev5 staging

This is local development staging, not a release or active/public promotion.
The branch preserves active dev4 `732f37698b58f00750d76ba99e4cf7599c1b97f7`
ancestry and adds exact source-only imports. Original experimental graphs and
private review/evidence remain separate. No experimental history, reports,
recordings, model assets, vectors or operational state are imported. No new
HTTP interfaces, date/upload glue, production-worker or dependency changes.

## Current scope: explicit internal experiments, default off

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
records ordered normalized-runtime and codec updates. Every overwritten path
names its old source commit/hash and newly reviewed source commit/hash. Tests
reject missing paths, duplicate overrides, stale lineage/version and unreviewed
bytes. Original imported docs describe their historical experiment; this scope
page and the layered manifest describe the current integrated source tree.

All reviewed source/test/docs bytes are preserved. Integration provenance,
allowlist and tests are separately committed; version/docs changes are separate.
The original dev5 staging budget was 2,500,000 bytes, 250 files and 250,000
bytes per file. This branch adds the separately approved canonical-person delta
below. Original import/layer records and privacy exclusions remain unchanged.
Additional work requires its own audited delta. The older bridge allowlist is historical provenance, not the current
staging policy. The unchanged source guard remains a heuristic review aid.

Historical rejected-q1 assertions retain strict expected failures; unexpected
passes still fail. Private-input tests remain opt-in and explicit. Regression
runs use CPU8, explicit temporary roots and per-module cleanup only on success:
own process-tree RSS <=256 MiB, generated disk <=128 MiB, available host RAM
>=2 GiB and free disk >=5.5 GiB. Failed attempts remain private evidence.
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

This isolated candidate adds exactly `audio_backend/identity_canonical.py` and
`tests/test_verified_identity_canonical.py` to the reviewed source allowlist. Its
exact source inventory is 249 files, with a 2,600,000-byte ceiling; the 250-file
maximum, 250,000-byte per-file maximum and all privacy exclusions are unchanged.
The candidate policy is local to this worktree. Shared/public policies, source
guard implementations and original import/layer manifests are unchanged.

The delta updates the existing verified-review bank, Store, API, change feed and
API documentation to support explicit revision-checked person relations. It
preserves original evidence owners, requires explicit same-person/different-people
claims, and makes merge undo reversible without reassigning later reviews.
Verified mode remains opt-in, with no new anchor admission or automatic learning
from merges. This does not integrate identity into experimental fragment HTTP
processing, calibrate speaker accuracy or establish long-recording quality.
See [the relation and invalidation contract](VERIFIED_IDENTITY.md).

Verification is model-free and file-by-file on one reserved CPU, with sampled
process-tree RSS limited to 256 MiB and this candidate's generated worktree disk
limited to 32 MiB. Independent review and the exact allowlist/privacy gate are
required before a local source commit; this is not publication or mainline merge.
