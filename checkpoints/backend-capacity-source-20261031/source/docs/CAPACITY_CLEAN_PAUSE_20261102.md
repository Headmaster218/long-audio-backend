# Capacity harness: explicit clean pause, not a capacity pass

This is harness-only engineering on the existing real-Worker protocol. No product
storage schema, quota, model adapter or inference behavior changes. Tests use
synthetic control/SQLite fixtures and an actual generated empty Store schema;
they do not launch a service or model.

## Commands after a reviewed source freeze

Run from a flat source checkout beside the existing verified asset directories:

```sh
../audio-recovery-runtime-20261023b/bin/python -B tools/capacity_run.py \
  /absolute/new-capacity-run --pages 147
# In a separate shell, request cooperative drain in the CURRENT attempt folder:
touch /absolute/new-capacity-run/PAUSE_REQUESTED
```

A pause marker is not itself a checkpoint. Wait for the original supervisor's
positive exit and final RESULT: `paused=true`, `passed=false`, no error fields,
unchanged source pins, and its matching `CLEAN_PAUSE.json` receipt. Inspect actual
completed counts, worker health, progress phase and cleanup; a live supervisor
heartbeat alone is not progress.

Explicit continuation of that exact frozen build:

```sh
../audio-recovery-runtime-20261023b/bin/python -B tools/capacity_resume.py \
  /absolute/new-capacity-run --attempt resume01 \
  --from-clean-pause /absolute/new-capacity-run/CLEAN_PAUSE.json
# A subsequent pause belongs to the new attempt, not the consumed old marker:
touch /absolute/new-capacity-run/resume01/PAUSE_REQUESTED
```

No `--reviewed-source-commit` or failed-inference retry plan is accepted for a
clean-pause continuation. The same source roster, configuration, target page count
and stopped data fingerprint must match. The original verified asset-manifest
bytes and every named model/library/sample content hash remain bound as well. A bounded full data-file roster includes
immutable artifacts and sidecar databases/WAL/journals, excluding only the instance
lock and known SQLite shared-memory files. Symlinks, extra/missing/changed files,
more than the fixed file-count bound or logical bytes above the existing data cap
fail closed; sparse oversized data is not silently accepted. The inactive real instance lock must be
acquired first. Validation precedes marker consumption and service launch. Both full resume
checks share one fixed 1,800-second preflight deadline; it is not reset between
the initial inspection and final pre-launch comparison.
The checkpoint also receives a one-use consumption record, so recreating an old
marker cannot authorize another attempt from the same checkpoint. The original pause marker is renamed to `PAUSE_CONSUMED.<attempt>` and a consumption
receipt retained; it is not erased, silently ignored or reusable. Any interruption
between consumption and launch remains explicit and needs inspection, not replay.
Prior clean-pause evidence cannot be downgraded to the older mixed-build resume
entry point by omitting `--from-clean-pause`.

## Drain and failure contract

- `PAUSE_REQUESTED` latches once. Its fixed deadline is the earlier of 900 seconds
  after observation and the attempt's original overall deadline; it never resets.
- If no page POST has been dispatched, no new page is created. Once publication
  started, its original PUTs/seal are each attempted once; ambiguity is an error,
  not permission to replay. No subsequent page starts after pause is latched.
- All already-published work must be completed and released. Page revisions,
  seals, source identities, ordered chunks and cursor are recorded and verified.
- Original SIGUSR1 shutdown and its existing 90-second limit remain separate from
  the drain budget. Stopped checkpoint verification then has one additional fixed
  1,800-second walk/hash/SQLite proof envelope, rather than a fresh limit per file.
  No claim is made that the whole pause returns within 900 seconds. No clean-pause receipt is usable without the final successful
  shutdown/result, unchanged pins and successful phase diagnostics.
- `STOP_REQUESTED` or SIGINT/SIGTERM remains an immediate emergency stop. STOP has
  priority during drain, checkpoint creation, validation and marker consumption.
  No resume code removes or hides it.
- Zero-page pause is valid but explicitly **not resumable**; start a separate fresh
  run rather than pretending a capture was already published.
- A clean pause exits successfully as an operator action but `passed` remains
  false. Failed drain, unknown HTTP result, cleanup, source, resource or monitoring
  errors cannot be transformed into a pause success.

Each explicitly requested resume remains a new attempt with the existing 16-hour
attempt budget. This is not a cumulative 16-hour guarantee across operator pauses,
nor a continuous-wall 12-hour qualification. The 147-page target is 43,218 seconds
of repeated speech, 4,116 chunks: audio-volume testing, not accuracy or a real
12-hour meeting. A two-page test retains its two-page target on continuation.

## Resource and persistence limits

The existing profile stays at two CPUs and 6 GiB aggregate memory. Core/receipt/
support limits remain 32,768/8,192/65,536, lexical 256 MiB, capture/global recording
4/8 GiB, native sidecar database 24 MiB, original shared-source quotas unchanged.
No automatic memory increase or data deletion is introduced. Source Git backups
exclude recordings, transcripts, databases, runtime logs and checkpoint receipts.

Abrupt process loss may leave an unsealed page; environment rollback may remove
local data entirely. This feature does not add partial-page replay, power-loss
recovery or durable remote data backup. Preserve all original run directories and
failure receipts. Do not claim a historical failed/mixed-build run became a pass.

Nonzero-page control tests use generated SQLite fixtures, not a live Worker.
The actual empty-Store schema test checks query compatibility only. Actual clean
service pause/continuation is not claimed until a separately authorized real run
produces and verifies its own positive shutdown and checkpoint evidence.
