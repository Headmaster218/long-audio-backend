# Qualified local-original results

Frozen checkpoint for publication review, 2026-10-15. Public-template qualification:
UNCONFIGURED / NOT EXECUTED. Local-original paced real-model result:
PRIMARY OPERATIONAL PASS, with optional-path warnings and no quality qualification.

## Evidence scope and provenance

These findings summarize existing completion records, small top-level summaries,
and bounded audit/telemetry files from the local originals. This preparation did
not re-run either helper or inspect the active real30 run. Original source and
source-manifest fingerprints are in MANIFEST.json. Historical preparation labels
inside the original READMEs/manifests predate the later executions; they are not
used as current execution-status statements.

The working product Python inventory for the acceptance runs was 99 files,
1,671,402 bytes, with aggregate SHA-256
06a6b81a8f2e8c31e98a04b52601aa03cce1ec69d40f20d0eb189f3bb140bee7.
This content pin is the tested-product provenance. The proposed Git parent is a
publication baseline; source equality to that parent has not been established by
this draft. Other regression suites are outside this evidence review.

## Focused infrastructure: passed locally

- 128 one-second receipts completed; the 129th admission was rejected without
  moving the timeline or consuming its idempotency key.
- Exact retry preserved identity. Changed bytes, final metadata, explicit
  offsets, gaps, and overlaps were rejected. Repeated explicit finish was safe.
- Paginated public results/changes reconciled with read-only SQLite counts.
- The disposable session's sources and related rows were removed; no cleanup
  intent remained. SQLite file shrinkage was not required.
- Wall time: 243.968 seconds. Peak sampled tree RSS: 152,412,160 bytes;
  maximum dense RSS sample gap: 0.432 seconds.
- Original process completion was positively observed, with clean product
  lifespan completion and owned-child cleanup.

## Accelerated 12-minute prefix: passed locally

Twelve 60-second chunks covered 720 logical seconds in 18.902 total wall seconds.
The fake-analyzer active-window SIGKILL/restart preserved the committed prefix,
kept checkpoints non-regressing, and preserved the exact-retry identity.
Recovery to the API was 0.992 seconds. Peak sampled tree RSS was 152,625,152 bytes,
with a maximum dense sample gap of 0.410 seconds. Cleanup was verified.

The prefix's 1,120.037-second projection for 720 chunks was an estimate made
before the full logical run. The measured full result below supersedes it.

## Accelerated 12 logical hours: passed locally

Label: FAKE_ANALYSIS / ACCELERATED_LOGICAL_12H /
NO_QUALITY_OR_WALL_SOAK_CLAIM.

- 720 completed chunks x 60 seconds = 43,200 logical seconds, or 12 logical hours.
- 691,200,000 canonical frames at 16 kHz; 1,382,400,000 PCM16 payload bytes and
  31,680 WAV-header bytes.
- Total wall time: 926.359 seconds, approximately 15 minutes 26 seconds.
- Maximum unfinished receipts: 4; maximum uncommitted input: 240 seconds.
- Final audit: 8,639 segment rows, 1,441 event rows, 1,441 speaker-turn rows,
  15,129 change rows, and 720 stream/chunk rows. Pagination counts reconciled;
  no duplicate committed natural keys were found.
- One controlled SIGKILL during a positively observed fake-analyzer active
  precommit window, followed by restart on the same database. Previously
  committed row IDs/content were preserved; checkpoints did not regress;
  exact upload retry preserved its receipt identity. API recovery: 1.325 seconds.
- The active chunk's pre-crash checkpoint was 0.0 seconds. This establishes
  preservation of the global committed history, not resumption from a nonzero
  within-chunk offset. Native/model crash recovery was not exercised.
- Positive original completion and owned-child cleanup were recorded. The
  deliberate crash required no clean-lifespan marker; normal shutdown required
  its process-bound marker and positive wait.

### Memory and telemetry are different sampling streams

Dense in-memory RSS monitoring recorded 3,640 checks and a peak aggregate tree
RSS of 204,713,984 bytes. Its maximum observed sample gap was 0.505 seconds.
The persisted resource ledger retained 92 approximately ten-second records;
its maximum adjacent elapsed-time gap was 10.249 seconds, and its highest
persisted RSS value was 171,360,256 bytes. The dense peak is not reconstructed
from that lower-frequency ledger. Neither series guarantees an instantaneous
memory maximum between observations.

### Storage observations

The summary's final filesystem charge was 1,396,416,512 bytes. The supplied
post-summary retained-charge audit is 1,396,420,608 bytes, which includes later
small evidence writes. These are different snapshots; neither is a live scan
performed while preparing this draft.

The final category measurements included 1,383,190,528 charged upload bytes,
13,004,800 database bytes, zero WAL/SHM bytes, zero optional-cell bytes, and a
4,096-byte temporary directory charge. The recorded peak run charge was
1,397,501,952 bytes. Filesystem charges include allocation/directory overhead
and therefore differ from the exact PCM payload. Fake output density is not a
forecast of real-speech database growth. Original input retention is linear;
rolling-retention behavior was not qualified.

## real30 source-function check: passed locally, model-free only

The existing local-original --check completion reported exit success and:
42 scheduled receipts, 28,800,000 frames, a simulated two-second wait, preserved
observer return values and exceptions, and exact analyzer-only argument rewrite.
It explicitly reported real_models_run=false and real_pacing_run=false.
No model throughput, actual pacing, restart, or 30-minute completion follows
from this check.

## Actual paced real-model run: primary operational pass locally

Label: REAL_MODELS / PACED_30_MINUTES / MOSTLY_DIGITAL_SILENCE / NO_QUALITY_SCORE.
The existing terminal completion and independent audit accepted the primary
operational run. No audio, transcript, database, or active run tree was opened
for this checkpoint preparation; only the bounded completion record was added
to the reviewed evidence.

### Input, pacing, and primary completion

- 42 completed receipts and 28,800,000 canonical frames covered 1,800 real
  monotonic seconds of paced input. Total wall time was 1,812.652 seconds.
- Four isolated session/stream/identity scopes kept unrelated recordings apart.
  Three recordings were used once: 70.470625 seconds of demo Chinese, 45.1
  seconds of AISHELL channel 0, and 21 seconds of AMI. Total distinct real
  recording duration was 136.570625 seconds. The fourth scope supplied
  1,663.429375 seconds of digital silence.
- Final primary rows: 71 segments, 47 speaker turns, and 83 events. These are
  operational output counts, not accuracy or diarization-quality scores.
- Maximum actual send lateness was 0.005004325 seconds. The last HTTP
  acknowledgement arrived at pacing time 1,800.358675 seconds. The summary's
  last-ack field was measured after explicit finish, so it must not be mistaken
  for that final HTTP acknowledgement time. Drain after pacing was 9.683117
  seconds, within the separate 300-second allowance.
- Maximum unfinished receipts were 4; maximum uncommitted audio was 79.84
  seconds. No MOSS job was accepted or submitted by the test.
- CPU SenseVoice and native Nemotron were used with overlap backend none.
  Model dispatch ownership for MOSS remained enabled for profile parity;
  that does not constitute a MOSS execution test.

### Restart and cleanup

The planned restart began at pacing time approximately 1,100.000124 seconds and
took 9.178020 seconds. It was a graceful idle open-tail restart after 940.16
committed seconds in the silence scope and native epoch index 3. Committed row
prefixes were unchanged and checkpoints did not regress. This is not an
in-flight native crash-recovery result.

The outer run exited successfully and was positively waited. Both owned server
lifetimes were positively waited with clean product lifespan completion and
owned-child cleanup. No captured process identifiers are included here.

### Literal model accounting

Numeric accounting recorded 101 successful analyzer windows, 39 successful ASR
decode calls, 83 quiet windows, 83 zero-decode windows, and 112 successful native
pushes across two native process lifetimes. There were 79 paired analyzer
starts/exits, with zero failed or incomplete observer calls. Window decode sums
reconciled to the 39 actual decodes. These categories are not disjoint input
segments and should not be added together to estimate audio duration.

Observed native input frames include replay, and decoder frames can include
context; neither is additional distinct recorded speech. The mostly silent
schedule establishes bounded operation and idle/restart behavior, not sustained
speech-driven ASR, embeddings, or event-model throughput.

### Memory, telemetry, and storage

Dense in-memory monitoring recorded 7,153 RSS checks, a peak aggregate tree RSS
of 827,674,624 bytes, and a maximum sample gap of 1.254 seconds. The separately
persisted ledger contained 180 records, peaked at 705,564,672 bytes, and had a
maximum gap of 10.249 seconds. The persisted series is lower-frequency evidence;
neither series guarantees an instantaneous peak between observations.

Peak run charge was 62,918,656 bytes. The accepted post-run inventory charged
62,271,488 bytes for 57 regular files and seven directories. These filesystem
charges differ from logical file bytes. Retention remains linear, and this quiet
workload's storage growth cannot be projected as sustained-speech database growth.

### Optional-path caveat: not qualified

49 Unavailable warnings occurred: 3 in capture and 46 in seam_prepare. Their
historical reason and timestamp were not logged, so the precise causes and their
positions relative to restart are unknown. There is no evidenced SQLite error
code/name explaining them. Do not infer historical causes from later health.

The current post-restart optional health snapshot was ready, with accepted=12,
dropped=0, durable=0, no active/pending handoffs, and no cleanup pending. Its last
capture reason was no_native_overlap and its last seam reason was
seam_tail_promoted. These are point-in-time post-restart values, not whole-run
counters, and they do not explain or negate the 49 historical warnings.

The result therefore qualifies the primary operational path only. It does not
qualify optional-capture reliability, durable optional-cell output, MOSS dispatch,
or a speech-quality score. A representative sustained-speech workload, 12-hour
wall-clock soak, rolling retention, and continuous overlapping-source
sealed-prefix deduplication remain separate qualifications.

## New paced 12-hour source: result pending

real12h.py.in is a source-only template derived from the newly prepared local
helper. The local original passed its bounded model-free source-function check, including
schedule, numeric bounds, observer preservation, and bounded diagnostics. No models
or actual pacing ran in that check; the 12-hour result remains pending. Static byte reconstruction and AST parsing establish provenance,
not execution or acceptance. The already audited 30-minute findings above are
unchanged.

The design schedules 732 receipts over 43,200 monotonic seconds: the same
136.570625 seconds of distinct real recordings once and 43,063.429375 seconds of
silence, across four isolated scopes. Its idle graceful restart is scheduled at
21,620 seconds. Startup/drain/cleanup remain bounded at 90/300/120 seconds; maximum
combined phase time is 43,710 seconds. These are planned bounds, not observations.

The additional optional diagnostic records only validated bounded reason codes
for exact Unavailable exceptions while preserving original returns/exceptions.
This does not retrospectively explain the 49 real30 warnings. Any future result
must retain REAL_MODELS / PACED_12_HOURS / MOSTLY_DIGITAL_SILENCE / NO_QUALITY_SCORE
and separately report optional reliability, actual calls, sampling gaps, retained
storage, and owned cleanup. No sustained-speech or quality qualification is
implied by source preparation or the mostly silent workload.
