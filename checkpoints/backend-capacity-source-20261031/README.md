# Backend capacity reconstruction — source checkpoint, 2026-10-31

This is a development backup, not a release or a successful 12-hour/48-hour qualification.
The `source/` directory is a self-contained source tree based on local commit
`4e910ce57b1f976cdf78b00224bbc94db5d05964`, with the Nov3 transport-preflight
addition described below. The containing remote commit and per-file manifest
pin the current snapshot. It includes the October 31 capacity reconstruction,
strict shared sources and quota changes, bounded hot-only payload accounting, and explicit
safe page-admission reconciliation in the preserved-capture test controller.
It is not recovery of the lost unpublished October 30 source bytes.

## Restore

Clone this repository and check out branch `development/backend-capacity-source-20261031`
(or pin this checkpoint's containing Git commit). From the repository root:

```sh
python3 checkpoints/backend-capacity-source-20261031/verify_source.py
mkdir ../backend-restored-20261031
cp -a checkpoints/backend-capacity-source-20261031/source/. ../backend-restored-20261031/
cd ../backend-restored-20261031
# Use Python 3.12+ and the included dependency lockfiles for the chosen runtime.
# Optional ordinary development install in a newly created virtual environment:
python -m pip install -e '.[test]'
```

No application-source overlay or older checkout is required: all 152 tracked product
files and the selected source/tests/docs/configuration are present under `source/`.
`SOURCE_MANIFEST.json` pins every included path, byte length, SHA-256 and Git blob SHA-1.
The parent public commit is `b33172a7f584a0b60b76a58eefd0ca3ba2ab0a6a`; its tree and history
are preserved unchanged outside this new checkpoint. Existing public identical Git
blobs are reused, but restoration does not require assembling prior checkpoints.

## Deliberate exclusions

No recordings, real transcriptions, models, databases, credentials, hidden credential
directories, runtime logs or raw run reports are included. Historical `reports/`,
`docs/reports/`, and stored execution-summary JSON files are omitted. The source README
and older docs retain historical references; links into excluded reports will not
resolve inside this standalone tree. Synthetic test strings/fixtures remain source.
Model manifests and license notices are metadata, not model weights. Models, native
runtime binaries, exact-runtime qualification, supervisor locks and test input assets
must be supplied and independently validated before real inference. Do not copy an
old runtime attestation merely because a historical source document mentions it.

## Validation boundary at backup time

The October 31 actual-model short gate processed 588 seconds (56/56 chunks), followed
by stopped integrity/FK, registered-file hash, cold-read and reopen checks. This is a
limited short gate, not accuracy, overlap or global speaker-identity qualification.
The preserved-data, mixed-build, interrupted-and-resumed capacity attempt failed:
an unexpected Worker exit was missed while the old supervisor continued refreshing
its own heartbeat. That stalled interval is not processing time. The attempt was
subsequently stopped with positive owner exits; a clean stop does not turn the
attempt into a pass. No 12-hour/48-hour success or uninterrupted single-version
qualification is claimed.

This checkpoint adds an equivalent pending-root query using existing admission
indexes, enabled-worker health/failure observations, and separate startup,
processing, stopped-integrity and terminal monitoring phases. Supervisor heartbeat
is not a substitute for actual completed-count progress. Worker failure remains
fatal; the five-minute no-progress signal is an inspection alert, not permission
to kill legitimate active inference or verification. Phase-diagnostic write failures cannot skip shutdown; the first failure remains
preserved through cleanup. Model-free tests are separate
from actual-model capacity qualification.

Historical Oct31 source checkpoint selected aggregate: 146 passed,
1 deselected (an existing capture fixture pins an older source SHA), and one
dependency deprecation warning. This was a selected aggregate, not the full
repository suite or an actual-model long-run qualification.

The snapshot operation did not execute the complete test suite. It scanned common
credential patterns and selects only tracked UTF-8 source/config/docs/test material.

## Fresh Nov1 reconstruction: not recovered historical evidence

Source was freshly restored from public f5105d and verified against its manifest.
The Nov1 product change is a one-line explicit use of the existing support_session
index for exact support reads. No DDL, authority predicate, deadline, or budget
was changed. Newly generated synthetic in-memory SQLite evidence and tests are
included; these are not restored Oct31 measurements or real user/audio data.

At 674/8461/32916 synthetic support rows, metadata lookup consumed 67 VM steps
instead of 6803/84673/329223; with PCM, 71 instead of 6807/84677/329227. Results
matched. Final selected new/existing checks: 52 passed, 12 unselected. Initial
missing-soundfile collection and temporary-filesystem disk-reserve failures are
recorded in docs/SUPPORT_LOOKUP_NOV01.md, not converted into passes.

No models, inference, capacity run, real recordings, transcriptions or long-run
results were restored or executed for this change. All historical qualification
statements above describe earlier work, not current-environment qualification.
This source checkpoint does not restore the lost unpublished Oct31 fixes or
private 12-hour run data and makes no new real-data/endurance/accuracy claim.

## Reviewed regression contracts, Nov1 subsequent milestone

This milestone changes tests, synthetic fixtures and documentation only. The
historical default DDL is compared against 14 independently extracted historical
SQL strings, including exact bounds on permitted nondefault threshold changes.
Capture source provenance remains checked, with independently historical AST pins
for its unchanged predicate/frontier and narrow packetless/priming behavior tests.
See source/docs/REVIEWED_ARCHIVE_CAPTURE_CONTRACTS_NOV01.md for full scope.

Final selected checks: 141 passed; one Worker-thread handoff case not selected.
The prior two stale source-pin failures were reviewed, not suppressed or counted
as passes. Additional Store cold-read reopen tests use generated component data;
no model, inference, real recording, process-restart or long-run claim follows.

## Subsequent bounded process-reopen validation

Tests/protocol documentation only; no production changes. Original model-free
Worker handoff is now included rather than unselected. Two sequential generated-
data writer/reader child processes positively exit and re-open original Store
cold data across an actual exec boundary, with real locks, source/policy binding,
SQLite integrity, exact bytes and unchanged budget/deadline refusal. Owned-child
SIGTERM and kill/reap timeout branches are separately checked. See
source/docs/SYNTHETIC_PROCESS_REOPEN_NOV01.md for independent review and limits.

The final selected aggregate is 145 passed (142 existing including Worker handoff,
plus three subprocess/watchdog cases), no deselections. This remains synthetic
component/connected-thread validation, not real-model, mobile-device, crash,
full-service restart, accuracy or long-duration qualification.

## November 2 bounded empty-claim race repair

The current source adds a reviewed manual optional-check repair: an empty Worker
claim probe must settle at both ownership snapshots within one original <=250 ms
envelope, clipped by accepted-job and existing driver deadlines. Real queued
primary work still cancels promptly and waits for positive optional cleanup.
Full fingerprints, automatic paths and observer budget failure policy remain.
See `source/docs/OPTIONAL_EMPTY_CLAIM_SETTLING_20261102.md` for exact scope.

New synthetic tests: 45 passed. Focused independently repeated selection: 179
passed plus 4 subtests. Broader affected aggregate: 213 passed, 36 failed, 27
subtests passed. The same 36 failures were independently reproduced on the
unmodified public parent, not masked or deselected. This is not a full-suite pass.
No actual model inference or long-duration qualification ran for this repair.

Restore remains a standalone full source tree; no patch overlays are required.
Only seven source/test/docs files and this README/manifest change from the public
parent. All unchanged source blobs and prior Git history are preserved.

Additional independent automatic-probe/comparison tests: 186 passed, 12 failed,
1 setup error; exact same failing IDs on the public baseline. No new regression
was observed; this does not establish a full-suite pass.

## November 2 generated process-crash boundaries

This current increment adds only three test/helper/documentation files. Production
source is unchanged from e47746c. Three real owned-child SIGKILL boundaries test
reserved-before-link fail-closed behavior, published-before-placement-commit
rollback plus exact same-unit resume, and committed-before-ack cold reading.
Real new-process Store/instance locks, immutable archive bytes, exact PCM/vector
reads and original budget refusal are verified; no production hook is added.

Independent code review and six new cases passed. Related archive/payload/process
regression: 97 passed. The final nonce-control atomic-write refinement was followed
by another six-case pass; remote restoration reruns the same complete97 selection.
Generated receipts, databases/audio/artifacts and runtime logs are excluded.
This establishes only bounded component process-crash behavior, not power loss,
fsync durability, whole-service recovery, real-model quality or long capacity.
Details: `source/docs/PAYLOAD_PROCESS_CRASH_BOUNDARIES_20261102.md`.

## November 2 capacity harness clean pause

Current increment: six source/test/docs files, plus this README and manifest.
Production backend files are unchanged. A separate PAUSE marker drains already
started page publication exactly once, stops new pages and waits for completed+
released work under one <=900-second deadline clipped to the original attempt.
Original shutdown remains separately bounded. Clean-paused is never a capacity pass.
Stopped proof binds exact source/config/assets, full data-file roster and immutable
page/chunk/source state under a fixed1800-second envelope and actual inactive lock.
Explicit same-build resume consumes its checkpoint once and preserves old receipts;
STOP/unknown requests/errors cannot be hidden. Zero-page pause is not resumable.

Independent exact-source review: 142 model-free capacity tests passed. Tests cover
original main orchestration with an explicit non-service double, real empty Store
schema, generated nonzero SQLite/control fixtures and fail-closed proof mutations.
No actual service/model pause or12h run was performed for this source milestone.
The existing broader-suite historical failures remain outside this focused scope.
See `source/docs/CAPACITY_CLEAN_PAUSE_20261102.md` for exact timing, commands and
limitations. Private checkpoints/data/runtime logs are deliberately not included.

## 2026-11-02: published receipt admission

Generated-only SQLite contention tests cover a bounded native BEGIN wait after
file publication and fatal original-error preservation rather than Worker deferral.
The original 250 ms acquisition cap and 5 second budget remain unchanged.
No SQL-body, COMMIT, file or inference replay; no adoption of reserved artifacts.
Independent reviews passed. Selected local regression: 267 tests passed, including
crash boundaries, subprocess reopen, archive/payload and capacity harness coverage.
This is not actual-model or long-duration qualification; failed run data is neither
modified nor included. See source/docs/PAYLOAD_POSTPUBLICATION_BEGIN_BUSY_REPRO_20261102.md.

## 2026-11-02: authenticated committed-late claim delivery

A positive COMMIT is no longer misreported as rollback. Only the original Worker
inside the same original probe can consume an exact one-shot committed-late claim
receipt after positive original close. No claim/SQL body/nonce is replayed. The
original 0.5-second refusal remains visible, including a late count in qualification
health/terminal metadata. A separate bounded read snapshot verifies the original
strict lease before model preparation; stop/cancel/nonce mismatch refuse execution.

Focused final model-free regression: 325 passed; independent review: 58 passed.
Broader selected groups retain 16 failures also reproduced on the unchanged prior
baseline (89 pass/13 fail and 41 pass/3 fail); this is not full-suite green.
No actual-model rerun or long-duration qualification is claimed. All fixtures are
generated; prior failed runs, databases and runtime logs are excluded and untouched.
See source/docs/CLAIM_POSTCOMMIT_NOTICE_REPRO_20261102.md for exact scope.

## Nov3 isolated resume CLI portability

The standalone source was restored and all 743 predecessor files verified against
the public manifest and Git tree. The resume harness now adds its resolved tools
directory before importing sibling modules, so direct `python -I -B .../capacity_resume.py
--help` works outside the repository. Product code and recovery policies are unchanged.
Three new CLI/launch contract tests and existing capacity harness tests pass:
148 selected model-free tests. See `source/docs/CAPACITY_RESUME_CLI_PORTABILITY_20261103.md`.
No lost runtime data or receipts are reconstructed or included. No long-run pass is claimed.

## Nov3 recorder transport preflight

Added an opt-in read-only local delivery preflight and Java/Python fixture checker.
58 selected model-free tests passed; three generated Java producer fixtures matched.
Independent review reran the 32 new tests. Only marker/envelope/checksum and WAV
transport integrity are checked. Inner metadata semantics, immutable publication,
strict ingestion and speaker identity are explicitly not qualified. No audio,
models, runtime databases, credentials or raw run data are included. See
source/docs/RECORDER_DELIVERY_PREFLIGHT.md for bounds and the remaining intake contract.

## Nov3 opt-in recorder metadata declaration validation

Added --validate-metadata to the read-only delivery checker. It validates bounded
Java PCM1 v1/v2 declarations, source identity and observed WAV/PCM bindings.
130 selected tests passed; nine actual Java-generated synthetic deliveries passed
cross-language checks. Strict ingestion remains disabled; no immutable snapshot,
clock mapping, sequence-gap repair or physical-continuity claim is introduced.
Default transport behavior remains unchanged. Details and deliberate canonical
text/surrogate restrictions are in source/docs/RECORDER_DELIVERY_PREFLIGHT.md.
