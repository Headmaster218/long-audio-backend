# Opt-in runner export revision recovery

Default `run` and `watch` behavior is unchanged: a transcript revision conflict
stops the invocation and preserves its incomplete output. No recovery is silently
enabled by configuration or by an earlier successful invocation.

For a fresh export, explicitly add `--export --export-recovery-attempts 2` (or3)
to the existing runner `run`/`watch` command and retain its required writer and
span-closure acknowledgments. Python callers use `export=True` and
`export_recovery_attempts=2`. Value0 is the default;1 and values above3 are invalid.
The configured output directory must not exist. This mode uses it as a recovery
root from the first attempt, with `attempt-01`, optionally `attempt-02`/`attempt-03`,
and a bounded recovery audit. Failed partial files remain beside the successful
attempt. Nothing replaces, resumes, mixes or silently discards an old partial.

The exact recovery mode and attempt bound are durably bound in `export.intent.json`
before effects. A different invocation cannot reinterpret this intent. If any
output exists but `export.done.json` is absent, the runner stops with publication
uncertain even when a child has a complete manifest. It never automatically
adopts that output, repeats inference, or starts a fresh output tree on restart.
An operator can inspect preserved evidence and use the separate explicit recovery
workflow with a distinct destination when appropriate.

Only an exact typed HTTP409 `transcript_revision_changed` on this session's
whole-primary transcript cursor GET can trigger another attempt, and only during
the scoped recovery helper. All other conflicts, malformed errors, transport
uncertainty, source changes, work regressions and cancellation remain fatal.
Every attempt delegates the unchanged exporter with both transcript validation
passes and complete local source/control checks. The existing exclusive-writer
assumption remains; this is not hostile concurrent-writer atomicity.

The same outer runner request count, wall deadline and cancellation apply to all
attempts and all earlier intake/status actions. During recovery, every response,
including a failed409, conservatively reserves512KiB from the same network budget;
reported byte accounting explicitly notes these reservations. The recovery
wrapper additionally retains its aggregate response/output budgets and maximum
three-attempt bound. GET-only is enforced at both wrapper and runner phase levels.
The scoped allowance never authorizes intake/finalize/worker retries.

A successful durable runner receipt binds the selected child's normal completion
manifest and a digest of every bounded audit/attempt file, including failed
partials. Later receipt replay verifies the entire tree without HTTP, regardless
of current CLI recovery flags. A successful result reports `export_attempts`,
`revision_conflicts`, and `selected_attempt`, so a recovered conflict is visible.
A changed/missing audit, failed partial, successful artifact or unexpected member
invalidates the receipt. There is no new accuracy, speaker identity, whole-capture
completion, native retry or deployment claim.
