# Explicit bounded export recovery

This is a separate operator-invoked, GET-only command. It does not change watch
behavior, launch a server, replay native inference, upload data, resume an old
partial export, or infer the body of the earlier live HTTP 409. The original
failed run remains a negative result. A later successful read-only export is a
separate result for the same completed job/session/capture.

Run `python -m audio_backend.recorder_export_recovery` with the original
`--job-dir`, `--api`, `--session-id`, `--capture-id`, an entirely new absolute
`--output-dir`, and `--exclusive-writer`. The latter acknowledges the unchanged
exporter's exclusive-writer precondition. The API must already provide safely
available production read projections; this command does not initialize it.
Do not start the full application on original evidence or on cloned databases
that retain original filesystem paths: startup can recover or delete files.

Before any output write, the existing production local-status validators run
under the existing read-only locks and validate canonical job and receipt source
paths. Their finalized local observation, checkpoint, archive identities, original
source files, snapshots, control files and directory/lock identities are pinned.
The same observation is revalidated at attempt boundaries, including the boundary
after a revision conflict. Cheap identity checks also run through the unchanged
exporter guards before publication. A coherent replacement of the same-path job
is a refusal, never permission to export a different job. No stronger concurrent
writer guarantee than the existing explicit exclusive-writer assumption is made.
Local validation retains existing source byte/frame/file limits and the shared
deadline; the number of validation passes is bounded by the three-attempt ceiling.

The new root contains separate `attempt-01`, `attempt-02`, optionally `attempt-03`
directories. Every attempt calls the original exporter from the beginning,
including completed-job validation, all status rechecks, both full transcript
passes, local file identity/hash checks and last-manifest publication. A conflict
never combines pages or copies old output. Partial failed attempts remain intact.
Existing output roots are refused before network access. No prior-output path is
provided or validated: the operator must choose a new root outside all previous
attempt directories. Back up and externally hash preserved evidence before invocation.

Only a body exactly shaped as `{"detail":"transcript_revision_changed"}` from
HTTP 409 on this session's whole-primary cursor GET is eligible for restart.
Duplicate JSON keys, missing/extra fields, textual hints, non-transcript routes,
other status codes, network failures, same-revision changed text, incomplete
work status, invalid content and arbitrary HTTP 409 stop immediately. Error bodies
are recognized before the existing exception's human string truncation. No raw
error body or credentials are written to recovery audit records.

Default maximum is two fresh attempts, hard ceiling three. A shared deadline,
cancellation guard and GET request counter span every attempt. Before every GET,
the wrapper reserves the underlying bounded HTTP transport's full 512 KiB JSON
response allowance against one aggregate 256 MiB default network budget. This
conservative reservation includes unsuccessful responses and is never reset; it
may intentionally stop a large export earlier than actual byte counting would.
Default aggregate transcript output allowance is 64 MiB, divided evenly between
maximum attempts. The audit adds at most 16 KiB. Existing per-attempt page/unit
and request/content bounds also remain intact. Smaller budgets are available to
Python callers through RecoveryLimits/ExportLimits. Injected transports are for
validated read-only adapters/tests and must honor the same underlying response
cap; arbitrary adapters are not a security boundary.

A fsynced `recovery.jsonl.partial` records identity, bounds and attempt transitions.
It becomes `recovery.jsonl` only after a child exporter succeeds. A failed audit
write or cancellation can leave only the last durable transition. The only export
completion authority is the successful child directory's standard `complete.json`,
not the root audit. Uncertain publication is not retried. The exporter truthfully
retains `observation_atomic=false`; recovery introduces no stronger atomicity,
accuracy, speaker identity, native inference or whole-capture claim.
