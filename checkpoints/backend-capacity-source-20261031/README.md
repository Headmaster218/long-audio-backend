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

No application-source overlay or older checkout is required: all 155 tracked product
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

## Nov3 explicit owned local snapshots

Added a separate operator-invoked snapshot tool with exclusive creation, bounded
copy/revalidation, fsync/close and non-overwriting receipt publication. 159 selected
tests pass including four abrupt-process-exit/fresh-process inspection cases;
nine generated Java deliveries copied and inspected. Existing or interrupted
destinations are not overwritten/resumed. Requires an exclusively controlled
private parent; no same-UID adversarial rename protection, physical power-loss
qualification or strict ingestion is claimed. Details: source/docs/RECORDER_OWNED_SNAPSHOT.md.

## Nov3 read-only sequence and gap reconciliation

Added bounded reporting over explicit verified snapshots: duplicate content,
ordinal conflicts, missing sequences, frame/epoch/run/format/input boundaries and
cross-segment observed-read contradictions. 203 selected tests passed, with
Java rotation and reused-ordinal conflict controls. Matching declarations never
prove physical continuity, hardware mapping or whole-capture completeness.
No ingestion, gap fill, source deletion or MOSS dispatch is enabled. Details:
source/docs/RECORDER_SEQUENCE_RECONCILIATION.md.

## Nov3 disabled local target/source binding proposals

Added explicit local proposal keys and optional private frozen journals. Exact
retries are read-only observations; changed bodies/coordinates/targets conflict.
Only resolved canonical mono16k prefixes are proposed. Target labels are unverified,
activation stays disabled, and no server request or strict ingestion occurs.
255 selected tests pass, including three abrupt-exit journal controls. Singleton
anchor warnings are retained. See source/docs/RECORDER_BINDING_PROPOSAL.md.

## Nov3 actual recording-session admission contract

Added a narrow read-only session recording-contract endpoint reporting durable
strict policy/capture state and existing source limits/idempotency scope. Real
ASGI handlers and strict SQLite checks confirm non-idempotent session creation,
source replay and changed-body/placement refusal. 266 selected checks pass with
one dependency deprecation warning. No native lifespan, model execution or new
uploader activation occurs. See source/docs/RECORDING_SESSION_CONTRACT.md.

## Strict uploader admission checkpoint (2026-11-03)

Existing uploader tested against strict ASGI admission with two deterministic pieces, lost successful PUT response, and fresh-invocation resume. Stops before seal/native work. Source-only checkpoint; 764 manifest files.

## Native smoke diagnostic checkpoint (2026-11-03)

Real strict startup refused current SQLite package fingerprint. Added bounded synthetic smoke harness and immediate read-only refusal preflight, with two refusal tests. No runtime pin/cache changed; no actual source/seal/publication occurred. Source-only checkpoint: 766 manifest files.

## Startup refusal cleanup checkpoint (2026-11-03)

Bounded graceful service task settlement before terminal receipt, with four asynchronous tests. Fresh actual runtime refusal exits failed but confirms normal worker/Store/lock/permit cleanup with no descendants. Exact original runtime package restoration remains blocked; no qualification pin or cache modified. Source-only checkpoint: 767 manifest files.

## Finite SQLite evidence checkpoint (2026-11-03)

Source-only generic physical probe and12 parser controls. Actual SQLITE_FULL6144pages, confirmed changed-page rollback-journal capture, original child kill/reap and separate-copy exact recovery observed. Production qualification unchanged; not acoustic or power-loss evidence. No generated database/journal or runtime/cache asset included. 771 manifest files. Independent saved-byte audit accepted generic evidence only; audit source preserved.

## Isolated CellStore candidate checkpoint (2026-11-03)

Nine actual verifier/SQLite schema, ownership, restart and refusal tests passed on an isolated two-literal candidate; independent result review accepted this limited evidence. Initial wrong-device failure retained. Production profile remains unchanged. Experimental patch/test/evidence basis only; no databases/runtime/cache assets included. 774 manifest files.

## Production-schema physical evidence checkpoint (2026-11-03)

Unchanged CellStore DDL/indexes, production limits/accounting,6144page actualSQLITE_FULL,177changed-page crash capture and exact separate recovery with complete typed six-table oracle observed. Independent saved-byte audit passed. Production profile unchanged. No generated DB/journal assets included. 781 manifest files. Independent limited runtime acceptance and accepted-candidate patch preserved; real strict11second silence startup/upload/seal/publication and clean shutdown passed. Working production profile remains unchanged.

2026-11-03: bounded public speech smoke and independent audit source added;32 focused tests passed. Two nonempty primary packets, no owned PCM cells, short-tail speaker issue remains. No audio/transcripts/databases/models uploaded; production profile/main unchanged.

2026-11-03: added explicit local recorder-delivery integration experiment and audit. Android producer bytes pass semantic preflight/snapshot, strict upload and publication; exact source/chunk PCM verified. 252 focused tests passed. No microphone/FTPS/continuity/importer-activation claim; generated data excluded.

2026-11-03: actual loopback FTPS/JVM TLS received delivery passed strict backend publication. Independent audit matches remote, snapshot, source and chunk PCM. Added audit source and clarified current versus historical session integration status. No device/automatic-intake claim; source-only backup.

2026-11-03: isolated server session-request idempotency experiment backed up as unapplied patch, test source, design and hash basis. 33 focused tests passed; independent review reran22 candidate cases. Working application/native pins unchanged. No activation, migration, primary-store allocation or crash qualification.

2026-11-03: backed up finite session-request process-kill/allocation probe and independent audit source. Precommit rollback and committed lost-response replay verified; actual strict lifetime1000 boundary respected. Initializer interruption remains unrecovered fail-closed. No activation or midcommit/power-loss claim; raw database artifacts excluded.

2026-11-03: isolated atomic strict/feature schema placement backed up as second unapplied patch with25passing tests and two killed-process checkpoints. Independent audit confirms both namespaces rollback, earlier base/WAL persists and strict adoption still refuses. No activation; prioritize known-session intake rather than broader bootstrap work.

2026-11-03: opt-in explicit known-session intake command added with private stable snapshot/journal, strict contract checks and safe unresolved categories. 46 focused intake/adversarial tests and actual FTPS-received 11-second native publication plus exact completed resume independently passed. Source-only backup; no watcher/session creation/device/accuracy claim, original runtime pins and main untouched.

2026-11-03: explicit fixed2-8segment intake now verifies whole same-epoch declared-adjacent batch before media writes, preserves missing/epoch/gap reasons, and resumes the active incomplete input safely. 321focused tests pass. Actual Android4s+4s+3s producer batch published through isolated native service, with one lost successful seal response and duplicate-free completed resume independently audited. Source-only backup; no FTPS for this new batch, device/accuracy/continuity or unattended-intake claim.

2026-11-03: incremental intake source safety checkpoint before native execution. Existingv3 default12h/64input bounded history, explicit append versus finalization, immutable admission binding and both locks added. Independent review caught and resolved a failed-fsync retry durability gap;23focused tests pass. Metadata-only4.9h case is not media/runtime evidence. Native incremental run pending; productionpins/main unchanged.

2026-11-03: independent11s native incremental audit passed from restored source: three separate appends remained local/noEOS, explicit finalization recovered a lost successful seal response, finalization replay matched, exactPCM/3sourceplacements/2completedchunks and cleanup verified. 344focused tests pass. Audit source and scope documentation backed up; no generated media/DB/report included and no long-duration or quality claim.

2026-11-03: added read-only segment profile planner and independent arithmetic oracle. Android source defaults to 300-second segments; 64 inputs limit the capture to about 5h20m, while tiny profiles can exhaust 16 page slots. Explicitly allowing 128 inputs accommodates roughly seven hours. Exact-boundary missing STOP declarations remain unresolved. 24 planner tests pass; the independent 86,400-profile oracle matched. No intake/default/device change or native/resource qualification claim.

## Exact-full STOP compatibility

Actual Android terminal-aware output (three full four-second segments, final STOP) passes batch and incremental strict admission. A short isolated native run published explicit EOS and reconciled retry, with independent source/PCM/history/cleanup audit. Historical ROTATION-only remains nonterminal. No backend ingestion, production pin or default change. See source/docs/EXACT_BOUNDARY_STOP_INTEGRATION.md. Source/tests/docs only; generated media and state remain local.

## Explicit receiver workflow

A bounded read-only inbox command reports validated attempts and ordinal gaps without choosing retries or invoking ingestion. Complete multi-segment linkage now exercises actual three-session FTPS loopback receipts through one strict incremental capture and explicit native EOS, with saved artifact audit. Source/tests/docs only; no receiver deployment, credentials, media or databases. See source/docs/RECORDER_RECEIVER_WORKFLOW.md.

## Explicit initial admission recovery and speech diagnostics

A new opt-in `--recover-initial` reconciles exactly one complete initial recorder
admission before any uploader checkpoint exists. Exact original/snapshot identity,
lock-only uploader state, positive remote emptiness and renewed fsync checks are
required. Hard process exit and retry tests retain the same source/capture/frame
identities without implicit EOS. Partial snapshots/receipts, unexpected artifacts,
foreign or uncertain remote state still preserve and refuse. Session autocreation
and watchers remain disabled. See source/docs/INCREMENTAL_RECORDER_CAPTURE_INTAKE.md.

Independent speech work adds explicit per-experiment language selection (omission
keeps English), correct missing-versus-malformed embedding diagnostics, and a
hash/count-only bounded SenseVoice experiment. It does not claim improved acoustic
accuracy, activate new models/pins, or change the two-second embedding threshold.
See source/qualification/SPEECH_CORE_DIAGNOSTICS_20261103.md for evidence and the
unchanged baseline extended-suite failures. Combined focused verification: 142
passed with one existing dependency deprecation warning. Source/tests/docs only;
no audio, transcripts, model weights, databases or raw experiment outputs included.

Selected recorder/uploader regression: 560 passed, 9 optional Java-producer fixture
skips, 2 deselected, one existing dependency warning. The two deselected real-HTTP
logical-hour tests were attempted and failed before server startup because this
restored test runtime lacks `sitecustomize`; their processing was not exercised.
This is a selected model-free regression, not a full-suite or native long-run pass.

## Durable initial intent and partial-prefix recovery

New incremental jobs now persist exact initial intent before header/scaffold or
snapshot creation. Explicit `--recover-initial` can complete missing/partial initial
header, snapshot and admission bytes only when every existing byte is a verified
prefix of the bound original/control content, the uploader is pristine, and the
known strict session is positively empty. Existing bytes/inodes are retained;
no truncate, overwrite, rename, deletion or blind replay is used. Recovery itself
can be interrupted and reconciled again. Partial/missing intent, missing intake
lock, conflicting data and legacy partial jobs remain preserved refusals.

Seven real subprocess hard-exit scenarios cover initial admission and interruption
during repair; the snapshot helper separately tests short writes, fsync failures,
publication hardlinks and hostile filesystem types. Independent review approved
the scoped protocol. Selected recorder/uploader aggregate: 612 passed, 9 optional
Java-fixture skips, 2 previously blocked real-HTTP tests deselected, one dependency
warning. This is not a power-loss test, native inference qualification or full
repository pass. No session autocreation, watcher, new server reservation, model
promotion or production runtime pin change. See the updated incremental intake
and receiver workflow documents for exact operator steps and remaining limits.

## Context-aware speech diagnostic only

The source-only context study compares zero and two-second context through the
existing SenseVoice/lexical ownership adapter on fixed public release samples and
AMI crops. Whole-input agreement is a proxy, not human-reference accuracy; one
AMI crop worsens despite aggregate improvement. No production context, language,
model, threshold, suppression, speaker assignment or strict gate is changed.
The diagnostic defaults to hash/count output; all raw outputs, recordings and
model assets remain outside this backup. See
source/qualification/SPEECH_CONTEXT_DIAGNOSTICS_20261103.md for the predeclared
comparison, confounds, provenance and independent review scope.

## Labeled AMI diagnostic, no product change

Five source/test/report files add fixed human-reference lexical scoring and a
separate native-activity coverage diagnostic. On four preselected short AMI clips,
two-second context worsens aggregate WER and low-share-speaker word matches, so
no production defaults are changed. Native oracle midpoint coverage is explicitly
not diarization accuracy and does not penalize false alarms. Annotation provenance,
forced-alignment uncertainty, overlap/scoring limitations and independent review
are documented in source/qualification/AMI_LABELED_QUALITY_20261103.md.
Only reproducible tools, synthetic model-free tests and aggregate documentation
are included; annotation archives/text, audio, hypotheses, models, databases and
raw run summaries remain outside the source checkpoint.

## Bounded Handy labeled comparison, no promotion

Three diagnostic source/test/report files compare unchanged primary chunked,
primary whole-window and explicit Handy Q8 candidate output on two fresh labeled
AMI clips. Handy does not improve aggregate lexical errors or resolve low-share
speech retention; no model promotion, service dispatch or safety gate changes.
Candidate EOS/token completeness remains unknown. Exact profile, frozen plan,
cleanup evidence, scoring limits and the baseline /tmp disk-reserve fixture issue
are documented in source/qualification/AMI_HANDY_CANDIDATE_20261103.md.
Only source, synthetic tests and aggregate documentation are included. No audio,
annotations, hypotheses, weights, candidate scratch or raw results are backed up.

## Twelve-hour delivery-count capacity and terminal-aware planner

Fresh v3 uploader and incremental-intake jobs now use a finite 192-input default
and maximum, allowing 144 five-minute deliveries within the unchanged twelve-hour
duration default. Existing 64/128-input v3 jobs keep their frozen caps; legacy v2
stays at 32. The planner models current terminal-aware full-sized STOP, with
explicit legacy rotation-only compatibility and no inferred EOS.

Authentication is bounded to 24,960 bytes. Existing 32 MiB metadata, 48 MiB total
owned state, 12 MiB spool, source-slot, byte/duration and disk checks are unchanged.
Real journal/archive tests cover 144 × 300-second geometry with 147 pages, and 192
padded inputs across 588 pages, with restarts and late-record tamper refusals. A
separate fixture runs 144 tiny real input files through intake/uploader/replay and
explicit finish using scaled geometry and a synthetic protocol peer. Neither is
a twelve-hour native/audio runtime test. Independent review approved the scoped
changes; the planner oracle checked 259,200 profiles. No server quota, model,
primary gate, watcher, session creation or production pin change.

Final selected recorder/uploader aggregate: 628 passed, 9 optional Java-fixture
skips, 2 previously blocked real-HTTP tests deselected, one dependency warning.
Separate tiny-file intake suite: 3 passed, including old-cap preservation. Initial
test-only expectations for the old default/maximum and exact-full-page EOS were
corrected; existing full-page capture finish behavior was not changed. These are
selected tests, not a full-repository or actual long-duration qualification.

## Explicit interruption and epoch boundary observations

The bounded read-only recorder boundary report validates owned snapshots, splits
unresolved boundaries, preserves epoch-local coordinates and close reasons, and
quarantines all conflicting variants without selecting a winner. Exact repeats
are not counted as extra audio. Physical gap durations remain unknown; mismatched
coordinate declarations are not subtracted. Stable span identifiers do not grant
upload authority or establish physical continuity.

This is a diagnostic path, not cross-epoch ingestion. Existing strict intake,
server one-capture-per-session semantics and STOP/finalize requirements stay
unchanged. A future span-local importer requires separate explicit known-empty
session targets and honest interruption closure, preserving producer declarations.
No source mutation, network operation, model, watcher, session creation or
production activation is added. See source/docs/RECORDER_BOUNDARY_REPORT.md.

## Explicit bounded span-local ingestion

A separate opt-in importer now accepts one fixed declaration-contiguous closed
span into an explicitly supplied known-empty strict session. It retains original
source identities, epoch/ordinal/frame coordinates and interruption/recovered-tail
closure in a durable local manifest, while the server timeline starts at local
frame zero. Both exclusive ownership and selected-span-only EOS acknowledgments
are mandatory. Existing whole-capture intake and boundary-report guards remain
unchanged; the report does not invoke the importer.

The path is limited to 1–8 inputs, 512 MiB and two hours; recovered tails are
singletons and terminal ROTATION is refused. It never crosses an unresolved edge,
creates sessions, claims missing gap lengths, changes metadata or retries failed
inference. Exact intact journals resume; ambiguous missing initial journals stay
preserved/refused. Strict-handler admission tests stop before native publication;
full completion/repeat is separately tested against a synthetic protocol peer.
True subprocess exits cover pre-journal refusal and active-piece exact restart.
No deployment, raw-data backup, model or production pin changes are included.

## Short native span-publication qualification

An isolated accepted-runtime candidate completed an 11-second generated-silence
span with epoch7, original ordinals11–13 and INTERRUPTION closure. Real source
publication and both native diarization chunks completed, exact resume added no
sources, and clean shutdown was independently audited. ASR was skipped for
silence; this does not qualify speech accuracy, devices or long-duration behavior.

The first attempt's overstrict no-mutation test assertion stopped inference early;
its evidence is retained and not counted as native completion. The corrected
harness permits only exact saved seal replay, never new sources/pages. Production
runtime/model gates remain unchanged. Only reusable fixture/harness/audit code,
synthetic tests and the bounded report are backed up. See
source/docs/RECORDER_SPAN_NATIVE_SMOKE_20261103.md for evidence hashes and limits.

## Read-only finalized span completion status

A separate explicit status command now checks the exact finalized span job and
matching target using bounded GETs only. It verifies local provenance/PCM and
remote source/page/work identities, finite chunk pagination and full coverage,
then distinguishes completed, pending and blocked primary work. Optional receipt
status remains separate. Missing or incompatible history is refused without
repair, seal replay, inference retry, new sessions or local journal writes.

This is a non-atomic observation under exclusive-writer assumptions, not proof of
transcript accuracy or simultaneous immutable server state. GET cannot verify
hidden seal/finish request identity, and unknown span gaps remain unknown. Tests
use actual ASGI GET projections with explicitly synthetic publication/analysis
records; they are not additional native or endurance qualification. See
source/docs/RECORDER_SPAN_STATUS.md and the updated manual receiver workflow.


## Incremental selected-span ingestion beyond eight deliveries

A distinct explicit v3 span command now reuses the bounded 192-input capture
machinery while preserving original sequence, epoch and frame declarations.
It maps only the selected proven span onto backend-local coordinates, requires
an existing empty session and explicit span-closure acknowledgment, and refuses
epoch joins, ambiguous retries and inferred STOP. Durable initial recovery,
mid-input process restarts and frozen capacity checks remain enforced.

A separate GET-only v3 status observer validates authenticated history,
provenance, allocated metadata/state bounds and exact remote coverage without
repair or inference retry. Nine unchanged Java-producer deliveries from pinned
Android source were exercised through real ASGI source admission with separate
handler-process restarts and exact retries. Completion/status projections in
that qualification are explicitly synthetic; it is not native inference,
physical-device, 45-minute audio or 12-hour endurance evidence.

Only source, synthetic tests and bounded qualification reports are included.
See source/docs/INCREMENTAL_RECORDER_SPAN_INTAKE.md and
source/docs/RECORDER_INCREMENTAL_SPAN_STATUS.md for commands and limits.

## Nine-delivery native span qualification

The bounded isolated candidate now also completed nine unchanged Java-produced
one-second silence deliveries through incremental span intake. Two clean intake
processes staged the inputs, lost-seal reconciliation and exact finalize replay
added no sources, and one actual nine-second native diarization chunk completed.
Two fresh GET-only status processes independently observed the same completed
span and preserved original epoch/sequence mapping, INTERRUPTION and unknown
gaps. ASR was skipped for silence; this is not speech accuracy, device or
long-duration qualification. Independent saved-byte/DB-copy audit passed and
clean shutdown receipts were verified. Runtime guards and production pins stay
unchanged. Only reusable harness/tests and the bounded report are backed up.

## Explicit private selected-span transcript export

A bounded GET-only exporter now binds primary-baseline transcript text and hashes
to the exact completed selected-span work chunks while holding the existing local
locks. It rechecks transcript pages and status before publishing private JSON and
timestamped text into an explicit fresh output directory. Existing destinations,
symlinks and real source checkouts refuse; completion uses a manifest over the
artifact pair. Optional transcript substitutions and person identities are not
exported. Unknown gaps, source-local speaker uncertainty and unverified segment
timing/recognition remain explicit; empty text is not proof of silence.

This checkpoint includes model-free adversarial validation and a pinned public
AMI replay fixture generator. The subsequent bounded public-speech qualification
below used a separately reviewed isolated candidate with unchanged guards.
No raw audio, reference text, exported transcript, model or DB is included.
See source/docs/RECORDER_INCREMENTAL_SPAN_EXPORT.md for commands and limits.


## Public-speech native transcript/export qualification

The exact published exporter source completed a nine-second public AMI replay
through genuine Java producer deliveries, real native diarization and SenseVoice
CPU ASR. Two private GET-only exports produced identical JSON/text with five
nonempty primary units. Independent audit matched every text/time/hash to copied
DB rows and retained native PCM/core/source receipts, and reconstructed readable
text exactly. Both observations preserved original span mapping, INTERRUPTION,
unknown gaps and anonymous source-local speaker limits. All child waits and
cleanup receipts passed. This proves the bounded transcription/export path, not
recognition accuracy, person identity, complete speech capture, devices or
endurance. Source-only verifier and aggregate report are included at
source/qualification/PUBLIC_SPEECH_EXPORT_NATIVE_20261103.md.
