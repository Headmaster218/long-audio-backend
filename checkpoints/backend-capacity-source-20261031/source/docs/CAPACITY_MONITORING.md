# Capacity-test liveness observations

A supervisor heartbeat proves only that the supervisor is running. It must not
stand in for completion progress or a live transcription worker. An earlier
capacity controller missed a dead worker while refreshing its own heartbeat;
that attempt remains failed, and its stalled interval is not processing time.

The local service now emits an atomic runtime worker observation every 0.5 s.
The controller checks that observation, worker failure flags, and uncaught
`audio-worker` thread errors, independently of its own heartbeat. An unexpected
worker exit/failure, missing observation after startup, or stale observation
causes an orderly service stop and a retained failed attempt receipt. It never
retries failed inference.

Progress records separately retain the last observed completed-counter advance.
Five minutes without a completed advance raises a soft inspection alert; it
must not kill legitimate active inference or final validation merely for taking
time. The supervising operator must inspect worker state, errors, stage, and
counts on each bounded check, returning control between checks rather than
waiting indefinitely for a percentage milestone.

Startup, active processing, stopped integrity verification, and terminal
cleanup are distinct phases. Clean shutdown does not erase an earlier worker
failure, and a healthy administrative service with its worker intentionally
disabled is not an unexpected worker exit.
