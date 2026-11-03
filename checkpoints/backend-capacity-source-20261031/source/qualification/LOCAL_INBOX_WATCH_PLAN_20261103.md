# Finite local inbox watch: bounded native qualification plan

This is a proposed short qualification, not a result. Freeze a published source
checkpoint and exact fresh candidate/launch plan, obtain independent review and
explicit parent confirmation before launching native work. Never retry a native
failure without a new reviewed plan and confirmation.

## Input and scenario

Use the same fixed nine-second public AMI PCM described in
`PUBLIC_SPEECH_EXPORT_FIXTURE_20261103.md`. The generator now has an explicit
`--terminal-reason STOP` option, invoking unchanged `PcmSegmentWriter.stop()`
after eleven full one-second segments. Select the last nine unchanged deliveries:
epoch 7, sequences 2–10, original frames 32,000–176,000, eight ROTATIONs and final
genuine STOP. Verify every assembled Java source against clean producer commit
`2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9`, all exact PCM, original manifests,
envelope bytes and fixture receipt hashes. No serialized close reason is edited.

The harness creates one isolated fresh empty session, never the runner. Configure
from the first fully valid selected delivery, then run one foreground watch with
explicit exclusive writer, selected-span closure, finalize and export permission.
After admission of the first input, place only WAV and envelope for the next,
unselected input. A harness observer calls the unchanged unpublished validator
and records its successful return twice across a normal watch pause. Require
identical request counts, zero writes and unchanged runner/intake file inventory.
Only then publish the marker. Add each remaining genuine delivery after the
previous admission, including the final genuine STOP. Never modify a frozen input.
Prepare each copied directory outside the inbox and atomically rename it into
place using Linux renameat2 with RENAME_NOREPLACE. Publish the withheld marker by
the same exact-byte no-overwrite staging/rename pattern,
so the only deliberate incomplete shape is a stable marker-absent directory.
This controlled local publication is not ordinary FTPS STOR behavior.

The watch must automatically stage all nine inputs, finalize only their genuine
terminal span, observe native completion and export privately in that same
process. The observer records actual HTTP request start/return times and exact
export-call request boundaries without replacing transport results or product
checks. Independently observe status and repeat export in fresh GET-only children.
Require exact original mappings, STOP observed, unknown gaps retained, nonempty
primary text, correct artifact hashes/lengths and byte-identical export files.

## Resource, lifetime and evidence limits

Use the existing unchanged 300-second Gate, two CPU affinity slots, 6 GiB sampled
aggregate RSS cap, 5.5 GiB free-disk floor and 16.25 GiB sampled artifact cap.
Keep the outer `timeout --signal=TERM --kill-after=120s 480s`, existing 90-second
normal service stop and original-Popen waits for every child. Exceptional watcher
cleanup sends TERM, waits five seconds, then KILL and waits five seconds. Watch
SIGINT/TERM sets its cancellation callback. No service restart or old DB reuse.

The watch shares one 120-second deadline, at most 512 polls at 0.1-second spacing,
256 actions, 1,024 requests, 512 writes and 256 MiB response-byte budget. The
harness itself bounds marker observations and event files. No daemon is installed.
Candidate source is the exact checkpoint plus only
`accepted_candidate_20261103.patch`. Production runtime pins, models, settings,
lifecycle and admission guards remain unchanged. Pin every candidate file and
reverify unchanged afterward. All audio, transcripts, model files, databases,
classes, fixture receipts and logs stay outside Git.

Retain ordered monotonic arrival/watch events, request counts, raw request trace,
marker hold proof, original waits, independent export/status evidence, candidate
and producer pins, service terminal predicates and descendant cleanup. Export
observers are harness-only wrappers that call and restore the original function.

## Boundaries

This is controlled local publication, not FTPS/device/Android/microphone evidence,
deployment, an installed service, native restart, long endurance, abrupt crash,
ASR accuracy, word alignment, speaker identity or whole-capture completeness.
Genuine STOP is a producer declaration and EOS still closes only this selected
span. Synthetic host observation times do not establish hardware continuity.
