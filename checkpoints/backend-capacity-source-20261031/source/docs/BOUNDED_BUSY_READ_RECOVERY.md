# Bounded recorder busy-read recovery

This option is off by default. Omitting it preserves watch's fail-closed response
to a 503. It does not change strict-store locking, native runtime behavior,
generation/policy checks, or permission to resume an interrupted operation.

`recorder_inbox_runner watch --busy-read-retries N` and
`recorder_generation_transition --busy-read-retries N` accept integers 0 through
3. The allowance is shared across the whole watch invocation, not renewed per
helper, page, poll, or endpoint. The transition forwards that single allowance
to its old-span watch. Coordinator preflight identity reads and next-session
initialization are outside this retry scope and still stop on failure.

An extra request is permitted only when all of these hold:

- The failed operation is an exact GET with no request kwargs/body.
- Its bounded 503 JSON has exactly `detail` and `admission_reason`, the existing
  fixed human detail, and one of `strict_archive_busy`,
  `strict_publication_busy`, or `strict_mutation_busy`.
- The target is the configured session, recording contract, exact capture,
  canonical numbered page, supported canonical chunk query, or an explicitly
  bound reviewed-identity session/bank read.
- It is outside export recovery, and an invocation-wide extra-read slot remains.

The same GET leaf is repeated. No helper or write is restarted. PUT, POST,
timeouts, uncertain outcomes, untyped/unknown 503s, unsupported targets, and
transcript reads remain nonrecoverable here. Export recovery retains its separate
exact transcript-revision conflict contract and never consumes this allowance.
An endpoint that translates busy into another error shape remains nonrecoverable.

Every attempt rechecks the original request, byte, deadline, cancellation,
target, and local-state guards. Existing identity checks still reject a changed
bank generation or policy. The pause is cancellable within the original deadline.
With busy recovery enabled, each failed request conservatively retains one maximum JSON response reservation
(512 KiB); valid successful responses are charged their encoded size. Export
recovery continues reserving the maximum for every response. Runner counters
represent calls dispatched to its transport boundary, not helper invocations.
`busy_read_retries_used` counts consumed extra-attempt slots at that boundary; a
downstream coordinator guard can refuse such an attempt before a network call.
Default watch's legacy failed-response accounting is unchanged.

The coordinator also counts every actual underlying request, including nested
identity reads and failed-response reservations, under its outer network limits.
Its `network_requests` and `network_response_bytes` report underlying calls and
charges, including when a runner attempt was refused before dispatch.
Before initializing the next session it reserves the existing maximum bootstrap
(six requests, or eleven for reviewed-identity preservation, and one POST), so a
known budget refusal does not strand a new POST intent. This strengthens ordinary
non-identity transition accounting as well as identity-mode accounting.

Paused transition results retain safe nested stop details: status, fixed reason
and category, error type, HTTP status, recognized admission reason, and retry
counts when enabled. Raw response bodies, paths, source data, and credentials are
not copied into public failures. `possible_remote_effects` remains true: failed
mutex admission for the current read does not undo any earlier request effects.
`automatic_retry: false` on a paused transition means there is no automatic
whole-transition restart or resume; it does not deny already-counted opt-in leaf
retries within that invocation.

Model-free qualification is in `tests/test_recorder_busy_read_recovery.py` and the
existing watch, export-recovery, transition, uploader, and identity-handoff tests.
These tests do not rerun or qualify acoustic models, live strict inference, or
any previously blocked real-speech study.
