# Reviewed fragment extensions: isolated source staging

This is local development staging, version `0.2.0rc2.dev4`, not a release or
promotion. The isolated branch preserves active develop `6835ec41` ancestry and adds
source-only imports; the original experimental repositories retain their graphs
separately. No private experimental ancestry is fetched or merged. No reports, private history,
recordings, models, vectors, operational state are imported. No dependency changes, new HTTP interfaces, date or upload glue.

## Scope remains narrow and default off

- Real-provider bridges are explicit offline/internal experimental entrypoints.
  The prior eight-arm real base matrix and separate two-support MOSS extension
  reported empty base event arrays: their event result is negative-event
  invariance only. It is not evidence for positive event coverage.
- Positive-event seams are a separate selected synthetic development experiment.
  Prior four-arm / 64 Zipformer calls reported equality and nonempty coverage at
  a 30-second boundary. That finite selected development result does not imply
  general event recall, production acceptance or full speech/diarization quality.
- Normalization is finalized, single-island PCM16 only. It is a standalone
  experimental class, not wired into the runtime/model bridge or HTTP APIs.
  Do not infer streaming, multi-island or compressed-input support.
- The independently admitted native boundary-probe and split-only continuation
  harnesses are benchmark-only. P1 passed. The original P2 whole arm completed
  processing but failed its report-size cap; that failure remains preserved.
  A separately budgeted split-only remainder later matched the old whole arm's
  exact PCM and all anonymous speaker intervals. This was not an uninterrupted
  P2 pass. Combined native input was 808.80025 seconds within 822.400375 seconds.
  The 300-second receipt was not EOS; ten commit IDs survived the epoch-boundary
  restart, and the final one-sample core had no invented speaker activity.
  This establishes no ASR quality, global identity or arbitrary crash recovery.

`FRAGMENT_STAGING_IMPORTS.json` records every reviewed source import's exact
commit, path, byte length and SHA256. Imports preserve bytes; any later change
requires a distinct integration-fix commit and new tests. Existing active-only
portability setup, private-input opt-ins and two strict historical rejected-q1
expected failures are retained. Unexpected passes remain failures.

## Source guard proposal for this isolated tree

The raw imports add 18 files and 248,902 bytes to the 211-file / 1,974,168-byte
baseline, yielding 229 files / 2,223,070 bytes. The later admitted native benchmark adds three source files; staging
documentation, provenance, guard and tests are separately audited additions. The proposed bounded total is
2,500,000 bytes, with the existing 250-file and 250,000-byte per-file limits.
The exact allowlist and existing privacy exclusions are retained in
`FRAGMENT_STAGING_POLICY.json`. This does not change active or public policies.
The older bridge allowlist is frozen source provenance, not this tree's policy.

`scripts/source_staging_guard.py` is the unchanged original guard implementation.
Its library API is used by the staging regression test with this tree's explicit
policy; its standalone CLI retains its original public-policy defaults.
Privacy scanning is heuristic and complements source review, not proof of privacy.
The source map tests check reviewed bytes and enforce absence of accidental
normalizer/runtime coupling. Generated regression evidence is kept outside
tracked source. Tests use explicit temporary directories and CPU6, no neural
inference or downloads, with own process-tree RSS <=512 MiB, generated disk
<=180 MiB, available host RAM >=2 GiB and free disk >=5.5 GiB. Failed evidence
is retained; only completed generated fixtures are cleaned.
