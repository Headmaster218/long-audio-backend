# Optional evidence retention and compact reclamation

The dev16 candidate adds conservative retention telemetry and an explicitly enabled,
compact-only maintenance path. The normal primary transcript is independent of
this optional evidence store. No retained cell, source receipt, PCM, recording
outbox, primary transcript, or MOSS result is a reclamation target.

## Read-only health

The optional-cell health response includes a fixed-size `retention` object.
Counts are last-observed measurements, not a transactionally consistent snapshot
of both databases. A bounded idle-owner refresh observes cell, source-receipt,
and compact-row occupancy. Reading the new retention and compact-GC projections
performs no SQL or filesystem scan. The surrounding `/health` endpoint retains
its existing process, disk and configured-readiness checks.

All retained cells are roots, including cells whose PCM was explicitly released.
Source and compact reference counts are lower bounds. Unknown producer,
pending-proof, recording-outbox and history references stay protected. The
response distinguishes an unavailable or stale observation from an observed
capacity condition. `blocked_referenced_capacity` means the measured capacity
is occupied by established roots; unknown protection is reported separately.
A collector page cannot establish the root state of the whole store.

Refresh runs only while the owner is idle, after ordinary handoffs and
reconciliation. Its existing cooperative work budget is unchanged. A query
already in progress can consume part of a newly arriving operation's deadline;
maintenance is not hard-preemptive or a zero-latency guarantee.

## Opt-in compact-only maintenance

The `--native-owned-compact-gc` switch defaults off and requires
`--native-owned-cells` and `--native-owned-core-seams`, including their existing
runtime qualification and compatibility requirements. It is supported only with
the exact application-bound single `Worker.loop` instance. A missing, changed,
or mismatched worker/store/capture binding makes reclamation unavailable; it is
never interpreted as an idle producer. Calling `Worker.process` or capture
preparation directly does not establish this maintenance fence.

One idle attempt inspects at most eight indexed compact IDs and at most eight
frontier bodies. All body reads count toward a 32 KiB local projection ceiling
and the unchanged shared 256 KiB operation projection budget. The original
2-second owner work deadline and 150,000 SQL-step limit remain. The primary
SQLite busy timeout is 5 ms per lock wait, not a 5 ms bound on the whole attempt.
Attempts are spaced from completion by at least one
second. No models or source audio are read.

Only a completed source chunk in a verified continuous stream can qualify. A
different, later-position completed chunk must supply the exact maximal durable
compact frontier in the same scope. Indexed membership, immutable coordinates,
current offsets and exact compact bytes are rechecked. The current predecessor,
capture tail and provisional roots remain protected. The first candidate does
not generalize this proof to standalone uploads or a still-active long upload.

The owner reserves its existing single operation slot. A primary
`BEGIN IMMEDIATE` transaction checks that no chunk is running and that the exact
bound worker has no current chunk. A running row covers the ordinary
claim-assignment gap; the bound current chunk covers the
completed-but-still-publishing interval. If an API pauses a claimed row before
the worker assigns its current chunk, safety additionally depends on the
unchanged `Worker.loop` interruption check before preparation and protection of
the latest predecessor. The two idle checks alone do not establish that subcase.
Pending core proofs,
publication flights, unreleased recording receipts and any retained-cell
dependency on a source protect its compact rows. This deliberately retains more
rows than a complete per-proof graph might require.

Deletion is confined to exact compact IDs and bytes in that one primary
transaction. There is no general SQL write retry. A failed or uncertain commit
is reported explicitly; later absence alone is not counted as an attributable
successful reclamation. The optional-cell database and every source receipt
remain unchanged. Existing read-only authority routines remain read-only.

## Capacity and retained output limits

These are instance-wide caps, not per-session allowances:

- 4,096 source receipts
- 1,024 retained cells, including metadata-only cells
- 2,056 compact commitments
- At most four pending core proofs, each bounded to 128 KiB

Compact reclamation can help a continuous sequence of quiet completed chunks.
One retained cell protects all compacts from each source it depends on, so the
collector is intentionally conservative. It cannot retire genuinely referenced
history to make space.

At one source and one compact per full 10.5-second chunk, the original compact
cap corresponds to 5 h 59 min 48 s, while the unchanged source-receipt cap
corresponds to 11 h 56 min 48 s. Partial chunks, retained cells or byte limits can
exhaust capacity sooner. Primary long-append support does not establish 48-hour
optional evidence capacity. A single active upload also does not gain the
completed-source reclamation proof.

No age-based eviction, automatic source deletion, automatic transcript deletion
or `VACUUM` is added. A full protected store reports unavailable optional
capacity. Primary work can continue with an explicit optional gap; reclamation
does not guarantee that every future overlap will have retained evidence.

MOSS structured output still uses the existing volatile execution manager in
this candidate. TTL expiry or accepting a later job can retire earlier volatile
result bytes. Compact reclamation does not solve result durability. Durable
accepted structured text/provenance storage requires a separate bounded design
and qualification; do not treat a proof hash as a complete archived proof.

## Qualification status

On 2026-10-16 the frozen collector source passed 73 focused tests, followed by
280 selected regression cases and 44 subtests. The regression includes the
earlier telemetry tests; do not add the preceding telemetry run's 88 + 192 cases
again. Coverage includes the actual 2,056-compact / 4,096-receipt metadata
fixture within the original budgets, primary-claim exclusion, the paused-claim
gap, pending proof sides, retained dependencies, uncertain commit recovery,
default-off settings/CLI, and a fake-acoustic future seam using the retained
predecessor. The collector product source stayed fixed during these runs.

Two earlier failed attempts are preserved separately: a synthetic CLI
prerequisite setup error, and a fixture looking for a private file path in a
public chunk view. Their fixture corrections do not turn those attempts into
successful runs or count them as product failures.

The focused run took 19.99 seconds, with sampled peak RSS 96,190,464 bytes and
sampled aggregate storage 40,390,656 bytes. The selected regression took 84.45
seconds, with sampled peak RSS 144,650,240 bytes and sampled aggregate storage
130,031,616 bytes. Both original processes were positively waited; no observed
owned processes remained. These are sampled test observations, not hard resource
peaks, throughput, or latency guarantees.

Tests used fresh synthetic state and simulated storage qualification, without
models or historical/user databases. They do not establish general VFS,
power-loss, acoustic-quality, long-recording, or 48-hour optional-history
qualification. The dev16 packaging copy changed only version and documentation
after those source tests.

A subsequent nine-stage dev16 package audit passed 62 source packaging/CLI
tests, a standard offline wheel build and fresh target installation, and seven
installed-package tests including the future-seam fixture. All 117 package
files matched the staged source, wheel and installed target byte for byte.
Default-off installed imports and application construction with the worker
disabled passed without Torch or model/checkpoint reads; no analyzer was
constructed or server started. Both backend and uploader CLI help checks passed.
Existing qualified dependencies were reused: this was not a newly provisioned,
dependency-complete environment or a model-inference run.

Across those package stages, the largest sampled RSS was 116,674,560 bytes and
the largest sampled aggregate artifact footprint was 23,908,352 bytes. These
sampled maxima do not establish hard peaks or long-uptime performance. This
final documentation refresh followed execution; production package files,
tests and build configuration remain the checked bytes, and the wheel was not
rebuilt for the documentation refresh. Chinese API guidance is in
[the dev16 guide](DEV16_OPTIONAL_RETENTION_API_ZH.md).
