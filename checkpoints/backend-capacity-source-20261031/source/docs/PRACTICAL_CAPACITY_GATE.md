# Fresh real-Worker capacity gate (Oct31)

`tools/capacity_run.py OUTPUT --pages 2` runs 588 seconds of repeated existing
English speech; `--pages 147` selects 43,218 seconds (12h18s) of audio volume.
Neither measures transcription accuracy or asserts 12 hours of wall-clock uptime.
The long mode requires a successful short run and a fresh disk forecast first.

Original application, Worker, inference, optional history comparison and shutdown
remain unchanged. No wrapper classifies optional exceptions as fatal or identity
success. Persisted optional dispositions are reported separately. Failed/paused
primary chunks stop admission; mutations are never automatically repeated.

The parent retains the instance lock through child positive exit and stopped
integrity/reopen checks. A failed graceful exit invokes identity-bound owned
session cleanup but remains FAIL. The monitor samples aggregate RSS with 64MiB
headroom against 6GiB, and all output allocation against 16.25GiB with a 5.5GiB
free-space floor. These are sampled bounds, not kernel aggregate memory quotas.
CPU affinity is two CPUs. Each source is produced separately; no 12-hour input
file or duplicate full-history JSON is constructed. GET journal rows keep only
hash/length/status; mutation responses remain available. Logs and journals have
finite monitored bounds. Original per-operation product budgets remain intact.

Stopped validation streams original, canonical, shared and archive file hashes;
checks SQLite integrity/FKs and exact completed geometry; verifies shared identity
references and reuse; reads three cold PCM objects through the original API after
Store reopen. This is not the lost Oct30 oracle or a crash/power-loss guarantee.

Source/model hashes are checked freshly. Runs preserve failed outputs and require
manual diagnosis. Local tests cover request no-replay, 147-page cursor progression,
and a real small owned-session cleanup; none replace actual inference results.

Read-only progress/page-release observations may be temporarily unavailable after
an individual three-second 503 retry window. They do not advance a cursor or
release a new page; the next normal polling opportunity stays inside the original
overall deadline. Required non-poll reads and all mutation errors remain fatal.
The supervisor reports skipped observations and preserves every 503 response.

For an orderly manual stop, create `OUTPUT/STOP_REQUESTED`; the original supervisor
then sends its owned service SIGUSR1 and waits for original lifespan cleanup.
SIGINT/SIGTERM set the same stop flag when delivered to the supervisor. A tool-level
namespace abort can bypass application receipts and must never be called a clean
application shutdown; preserve it as an interrupted run, not a product failure.
