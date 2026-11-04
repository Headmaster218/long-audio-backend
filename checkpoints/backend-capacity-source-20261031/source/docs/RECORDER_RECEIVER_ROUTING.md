# Stable-endpoint receiver generations

This opt-in routing layer keeps one FTPS host, port, username and directory while
retaining several bounded receiver generations. It does not move or delete data,
repair partial uploads, change Android profiles, create analysis sessions, or
provide unattended hot rollover.

## What changes

A routing configuration contains one to eight existing generation bindings and
an explicitly selected active generation. A new, unique MKD attempt goes to the
active generation. An existing attempt's CWD and RETR resolve to its original
raw/publication namespace. Each attempt name must be unique across all retained
generations. The service holds every generation's receiver lease before binding
its socket; an unavailable lease, collision or unowned published attempt refuses
startup. A reconnect does not acquire permission to rewrite old objects.

The current account, TLS identity, listening address and port remain stable.
Switching active generation requires a clean stop and restart. It is not a
zero-interruption switch. This version has no automatic connection-drain
coordination: stopping during STOR can preserve a partial attempt that Android
will not repair. Only perform a controlled switch after uploads have settled;
this tool does not establish that condition for you. Preservation of partial
bytes is not proof of eventual delivery. Keep the same full retained generation
list on the restart: omitting an old generation makes its attempts inaccessible and can
permanently block Android readback. Do not revoke old credentials or rename paths.
Old QUEUED items that have not started an attempt may create new unique attempts
in the active generation through the same endpoint. Existing partially uploaded
attempts retain the original read-only reconciliation behavior; this change does
not fill missing objects or reset Android BLOCKED rows.

## Configuration

Keep the original single-generation service.json files and their private secret
references. Prepare an additional inactive generation using
RECORDER_RECEIVER_GENERATION.md before building the routing configuration.
The routing configuration's optional `generations` list has entries with exactly
`binding` and `inbox_dir`. Its top-level binding/inbox_dir pair must exactly match
one entry and selects the active generation. All data trees must be disjoint.
The endpoint-level limits apply separately to each generation; no limit is raised.

Create a fresh private routing configuration with the setup-only helper:

    python -m audio_backend.recorder_receiver_routing \
      --endpoint-config /private/receiver/service.json \
      --generation-config /private/receiver/service.json \
      --generation-config /private/next-generation/service.json \
      --active-generation 1 --output /private/routing-next.json

The zero-based active index selects which generation receives fresh attempts.
The endpoint configuration supplies the unchanged listening/TLS/account policy.
All generation configurations must use the exact same username, password-file,
certificate and key references. Their unused per-generation listening ports do
not override the stable endpoint. The command rejects recursive routing configs,
unknown/overlapping trees, existing output and output inside a data tree.
An uncertain partial output is retained for review, not automatically overwritten.

After stopping the current receiver, run `recorder_ftps_receiver check` and then
`serve` with `--config /private/routing-next.json`. Keep prior routing files and
all original per-generation files for explicit restart and review.

Do not run the unused per-generation service at the same time: all listed raw
namespaces are exclusively owned by the routing receiver. `check` validates the
configuration but does not claim leases, prove TLS/listener availability, or
confirm recording continuity.

## Remaining limits

- Analysis runners still have their own input, duration, byte and source-slot
  limits. Receiver routing alone does not finalize a ROTATION span or connect
  separate analysis sessions.
- Raw originals and published copies are retained. Multiple generations consume
  actual disk space. Eight generations is a bounded registry, not infinite storage.
- Session switching can change recognition context. Neither byte preservation nor
  routing success proves cross-speaker quality, long-duration stability, or
  uninterrupted semantic transcription.
- This first implementation has no automatic active-generation switch, retention
  policy, garbage collection or device-level delivery guarantee.

## Cloud verification, 2026-11-04

The final implementation passed an actual loopback TLS test using the unchanged
Android production FTPS transfer/network classes and three contiguous public
speech originals. The first original went to generation A. After a clean restart
at the same endpoint with B active, the client read back A's original attempt,
uploaded two fresh attempts to B, then read A again. All three WAVs and the decoded
original metadata matched byte-for-byte; A's ten files kept their content hashes.
Both services exited normally. The setup helper produced the tested configs.

An independent review found and fixed a setup-only omission bug before publication:
the helper now requires retaining every endpoint namespace, including the full
list of a routed endpoint. Missing old generations are refused before writing.
The independent routing/FTPS/completion suite passed 117 tests. Additional controls
confirmed old partial attempts stay read-only, published-only orphan attempts
refuse startup, and failed startup releases all acquired leases.

This is source-client testing with pinned installed dependencies on one cloud
host, not a physical Android device or an independent destination server. It did
not run native inference, qualify hot switching, or prove indefinite recording.
