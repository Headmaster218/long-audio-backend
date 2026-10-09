# Offline fragment extensions, 0.2.0rc2.dev5

Default OFF and experimental. This source-only public projection adds an explicit
normalized internal runtime adapter and standalone compressed-source receipts /
rolling emitted-PCM reads. Source commit:
`867a215a0578bb23885d175623a36ee3ed04d238`. No original private experiment ancestry,
recordings, fixture assets, models, vectors or raw reports are imported.
No new HTTP routes, upload/date glue or production-worker integration is added.

## Current normalized internal path

The [normalized runtime adapter](FRAGMENT_NORMALIZED_RUNTIME_INTERNAL.md) now
connects the reviewed [PCM normalizer](FRAGMENT_NORMALIZATION_EXPERIMENT.md) to
internal logical scheduling and provider bridges. This supersedes the historical
dev4 statement that the normalizer had no internal runtime connection.
Admission is explicit and limited to finalized, single continuous islands of
same-rate 44.1/48 kHz mono/stereo PCM16 WAV with the existing minimum length.
Ordinary exact-PCM stores/readers still admit original 16 kHz mono PCM16 only.

Scheduler and model timestamps remain canonical 16 kHz. The native SourcePlan,
source coordinates and conservative many-to-many filter support remain separate.
No invented one-to-one native sample ownership, global identity or model-quality
claim is made. Each <=0.5-second normalization read launches a separate bounded
FFmpeg process; synthetic correctness tests do not establish throughput.
The provider bridges remain explicit offline/internal experiments; no HTTP or
end-to-end product acceptance is implied.

## Separate compressed-source experiments

[Decoded-source receipts](DECODED_SOURCE_RECEIPTS.md) bind compressed bytes,
pinned decoder/build and policy, then count/hash the actual emitted PCM.
Container presentation and emitted coordinates remain distinct; original
pre-encoding frame count and capture continuity are unproven. AAC endpoints
remain unresolved. The baseline range reader replays the complete source.

The separate [rolling reader](ROLLING_DECODE_READER.md) reuses one sequential
decoder with bounded forward buffering. Windows are provisional until their
own session completes its full count/hash check; pairing requires that exact
completed session receipt. Older windows are not silently upgraded. Cancellation,
early close, errors, source mutation or mismatched output invalidate the session.
There is one active session per process. No arbitrary seeking, 48-hour endurance
or general streaming acceptance is claimed.

Both readers remain outside SourcePlan, Alias, automatic trimming, the normalized
runtime, HTTP and production workers. The source includes no automatic compressed
ingestion. Linux/pinned-tool and trusted immutable-storage assumptions remain.

## Historical documents and evidence limits

Original imported documents retain their reviewed bytes, including historical
protocol-stage wording and references to private research reports that are not
distributed. This current scope and the layered manifest describe the integrated
tree. In particular, the rolling reader now exists as a separate experiment,
although the older baseline document describes it as future work.

- The original real-provider base matrix and separate MOSS extension produced
  empty event arrays, establishing negative-event invariance only.
- A separate four-arm selected synthetic-development event experiment reported
  equality and nonempty 30-second seam coverage. It is not blind accuracy,
  general recall or full speech/diarization acceptance.
- Native P1 passed its bounded check. P2 whole processing finished but report
  size failed; a separately admitted split-only remainder later matched the
  retained whole PCM/anonymous intervals. This was not uninterrupted P2 success.
  The 300-second receipt was not EOS, ten commits survived its epoch restart,
  and the final one-sample core had no invented activity.
- Historical codec memory measurements that counted pytest or a supervisor
  alone are not full-tree RSS. The imported documents explicitly preserve the
  correction and the scope of later sampled parent/child measurements.

None of those private findings substitutes for this export's own results in
[TEST_STATUS.md](../TEST_STATUS.md). A skipped frozen-fixture test is not a pass.

## Exact layered lineage and public controls

The original [flat import map](FRAGMENT_STAGING_IMPORTS.json) remains byte-for-byte
unchanged. [FRAGMENT_STAGING_LAYERS.json](FRAGMENT_STAGING_LAYERS.json) pins that
map's hash and the reviewed update order. Each overridden path names its previous
commit/hash and new reviewed commit/hash. Tests reject duplicate/missing paths,
stale lineage/version and changed or absent source bytes.

The public provenance tests use the existing unchanged public gate and exported
files, preserving public-specific private-input gating and exclusion coverage.
The old bridge allowlist and private staging policy are not public authority.
[PUBLIC_POLICY.json](../PUBLIC_POLICY.json) remains the only public path policy:
250 files / 2,500,000 bytes, 250,000 bytes per file, unchanged private exclusions,
secret/vector checks, hook and exact reviewed allowlist. No cap increases.
Two historical q1 controls remain strict expected failures; p1 is ordinary tests.
