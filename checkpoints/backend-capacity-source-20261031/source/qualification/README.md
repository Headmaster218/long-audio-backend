# Finite SQLite evidence probe (not profile activation)

`sqlite_crash_probe.py` is an opt-in Linux-only experiment on a new private
synthetic namespace. It does not modify production settings, qualification
records, installed runtime/cache files, models or existing databases.

It pins the currently observed package explicitly as an experimental input,
requires all other shipped runtime fingerprint/build/device fields unchanged,
and records the exact package leaf inventory plus source-file hashes. This does
not call an altered production verifier or treat fingerprint equality as evidence
of physical correctness.

The fixed generic fixture fills a 4096-byte-page database until actual SQLITE_FULL
with exactly 6144 pages. It uses the existing DELETE/FULL/unix-VFS/no-spill/cache
settings. The supervisor observes an owned child stopped during commit, requires
actual changed database pages, checks a single complete journal segment and every
record checksum, and reconstructs the baseline exactly. Unsupported/malformed
journals fail. Clear timing misses may use up to four fresh attempts.

The stopped child is killed and reaped. Recovery runs on a separate copy; original
interrupted database/journal and separate captured bytes remain for independent
audit. A fresh process must recover exact bytes and all expected logical rows,
confirm integrity_check and close. No power-loss, physical fsync completion,
adversarial mutation, unobserved peak or acoustic accuracy claim follows.

Supervisor: 1 GiB address-space limit, two allowed CPUs, cooperative 240-second
work deadline and 256 MiB artifact allowance. Children: 1 GiB address-space,
25/30-second CPU soft/hard limits, 45-second alarm and 32 MiB per-file limit.
Allocation limits are sampled/cooperative, not kernel filesystem quotas. All
synthetic evidence stays local. The supervisor only signals its own child PID;
it masks SIGINT/SIGTERM during launch handoff and cleanup. Children arm Linux
parent-death SIGKILL before doing SQLite work and verify supervisor identity.
External SIGKILL may prevent final reporting; missing output remains unknown.

This generic physical probe is only one input to qualification. Production
CellStore schema, ownership/refusal controls, allocation reasoning and candidate
regressions still need separate independent acceptance before any profile change.
The probe never edits QUALIFIED_RUNTIMES and cannot itself authorize activation.

Journal layout/checksum reference: [SQLite file format](https://www.sqlite.org/fileformat2.html#the_rollback_journal).

## Fresh observed result, 2026-11-03

The first physical run exited zero. It reached SQLITE_FULL with 6144 database
pages and 6128 rows. Capture attempt 1, stopped sample 4, contained 103 changed
pages and a single 6129-record rollback journal with a 512-byte sector field.
Every original page/checksum passed and reconstructed the exact baseline hash.
Original child waits were fill 0, captured writer -9, recovery 0. The recovered
copy matched the baseline byte-for-byte and all expected rows; the interrupted
pair remained unchanged for independent audit.

Observed database+journal allocation was 50,323,456 bytes, including 3704 bytes
of allocation slack. Baseline/recovered database size was 25,165,824 bytes. This
is a finite observed case, not an instantaneous quota or universal VFS proof.
The shipped profile still rejects the current runtime; it has not been changed.
Independent post-run audit and production-schema/owner regressions are separate
requirements before any proposed profile activation.

Independent saved-byte audit accepted this run as generic process-crash evidence.
Its separate parser verified all 6129 checksums/original pages and every changed
page, all 6128 recovered rows, unchanged raw evidence, runtime inventory and
source hashes. Signal/wait history is supported by original supervisor receipts,
not independently re-observed afterward. Audit source is preserved verbatim as
`independent_saved_audit_20261103.py`; it is a historical, local-path-specific
script, not a general CLI. It writes its report beside itself and must not be run
in a source checkout expecting no modifications.

Original physical result SHA256:
`cac203b1b63256a811033f19d831698c9087a06b185ddecb258a14db250ec4a8`.
Original independent audit script SHA256:
`69f14c4bc514ea5cd4a8723fc9cd0273a740cb05ab59e7b0e3f3e232ab636654`.
Raw synthetic database/journal evidence is retained locally and excluded from
source backup. This acceptance does not activate or replace the shipped profile.

## Isolated production-schema candidate regressions

A separate source copy of checkpoint d64aa5e3991ca94780a43887131f84816e5e7b2c
was prepared. Its only production-code differences are the two literals in
`isolated_candidate_20261103.patch`. The working backend profile remains unchanged.
`EXPERIMENTAL_PROFILE_BASIS.json` explicitly does not approve activation and binds
the actual physical result and independent audit hashes. Candidate module SHA256:
`1422352f2b0b88ddfeb6c50ca10ac824ef9899057f5fdc19762cf23d532cb2b1`.

Nine isolated tests passed: the eight cases in `test_candidate_cellstore.py` plus
the unchanged existing
`AdmissionTests.test_seam_synthetic_real_runtime_a_profile_restart_dispatch_and_off`.
Checks include actual schema creation, publish/replay/conflicting-proof ownership,
pinned read/release, restart, real runtime/build/device refusal before disk access,
existing qualification binding refusal without changing namespace bytes, and
unresolved-owner lock-token retention. The real production verifier and SQLite
are used. Primary authority and instance-lock token are synthetic, so this is not
real OS/factory lock, recorder provenance, native inference or service evidence.

The original first run failed because default /tmp is on a different filesystem:
the unchanged selector correctly returned no profile. That failure was preserved.
The second run used a fresh workspace directory for both pytest basetemp and
TemporaryDirectory, asserting device 27 before proceeding. It passed 9 tests in
1.08 seconds with exit zero. It verified exact candidate import path, actual
success branch rather than alternate runtime refusal, unchanged full source
manifest, evidence hashes and the exact two-literal diff. Work was bounded by
120-second alarm, 90/100 CPU seconds and 1 GiB address-space limit, on two CPUs.

To reproduce, restore the cited source checkpoint into a separate candidate,
add the experimental basis/test file, apply the patch only there, use the original
isolated runtime with -I -B and required test dependencies, and put all temporary
files on the qualified filesystem. Explicitly run the qualification test file
and the named unchanged seam test. The repository's default testpaths remain
`tests`; experimental candidate tests are opt-in and must not run against the
unchanged production profile expecting success.

Independent result review accepted these nine tests as isolated candidate
schema/ownership/refusal evidence. It verified saved results, all candidate pins,
unchanged production module and exact two-literal candidate diff. Production
activation is still a separate decision. Original regression result SHA256:
`56004ce3fb32499f96234962870884c931389d9a65be88db5802a0917f9df361`.

## Production-DDL full-capacity physical evidence

An independent acceptance review correctly declined to activate from the generic
physical run and tiny schema regressions alone: the documented allocation envelope
is application/schema/transaction-specific. That decision is preserved locally.
The separate `cellstore_crash_probe.py` closes this experimental evidence gap with
unchanged `CellStore._schema`, all production indexes/PRAGMAs/SQLite limits,
positive-length synthetic cell spans and source/dependency receipt relationships.
Opaque synthetic proofs are structural fixtures, not authentic primary evidence.

The fresh run reached actual SQLITE_FULL five times during progressively smaller
atomic cell/dependency insertions, with exactly 6144 pages, 2048 sources and
55 cells/dependencies. It captured 177 changed pages and a single 6044-record
journal during commit. Original child waits were 0/-9/0. Recovery used a separate
copy and matched both exact baseline bytes and the complete typed six-table oracle
(store_meta, sources, cells, dependencies, sqlite_master and sqlite_sequence),
including all column names, schema SQL/rootpages, integrity and reference checks.

Production charging, max(logical size, allocated blocks) plus directory, observed
49,975,296 bytes with 4384 bytes slack for the interrupted namespace. Baseline and
recovery each charged 25,169,920 bytes. Limits remain sampled/cooperative and do
not prove power-loss behavior or instantaneous global quotas. Original interrupted
files remain untouched for independent audit. Production qualification is still
unchanged pending explicit independent acceptance of the combined evidence.

## Explicit limited acceptance and real strict-service smoke

Independent acceptance decision SHA256
`f329d6403f2b3c2ef6267e054530aadb897c349bc7752c34d638017552e07156`
accepted the combined production-schema physical evidence and nine real-owner
regressions for the exact fresh bounded service candidate. Independent physical
audit SHA256 is
`b75df7a97d927825a360b108d62f370e2b6b5b78c9f67605070b202d0fb101bc`.
Physical result SHA256 is
`1d8971d14719c7ddecdedd245184270b8a18b64e9f05ff04420785e0402f4f41`.

`ACCEPTED_RUNTIME_BASIS_20261103.json` binds that acceptance and its evidence;
SHA256 `27e9f806302f01da31d2f3bd2be0de327f195ccc63d0fd93c6b18ccb760a5589`.
The accepted patch again changes only package/evidence literals in a separate
source copy. All selectors, verifier methods, schema, bounds and feature defaults
remain unchanged. The working production copy and main are not updated.
General production rollout and old-store migration are not approved by this
limited acceptance. The accepted basis replaces the earlier generic-only
experimental basis for this new candidate, without rewriting historical evidence.

Nine regressions passed again against the accepted evidence digest (0.94 seconds).
`test_accepted_candidate_cellstore.py` preserves those opt-in tests. The real
unchanged strict service then started with its inherited lock, real owner/Worker,
and all normal gates. The existing bounded uploader submitted 176000 frames of
synthetic digital silence. Two chunks completed (10.5 and 0.5 seconds); the page
was published and released with EOS. Startup, uploader, processing and graceful
shutdown completed with original process exit zero. Shutdown confirmed worker
stopped, lock closed, Store safe and permit clear, with no shutdown errors.

This is eleven-second functional integration evidence, not resource endurance,
real-recorder provenance, cross-chunk speaker identity or speech accuracy. No
claim about those follows from silence. Raw generated WAV/SQLite/runtime assets
remain local. Historical audit/decision scripts are preserved verbatim and use
fixed local paths; do not execute them in a source checkout expecting no writes.

Saved-database inspection further narrows the smoke: its three strict outcomes
were empty, with zero retained cells, zero primary packets and zero MOSS
intents/results. This qualifies the silent processing/publication path only;
nonempty speech retention and transcription remain untested here. The strict
primary-only MOSS-dispatch boundary is preserved.

Independent saved-smoke audit accepted this narrow result. It rehashed 32 assets,
verified the exact two-literal candidate change, configuration/source/CellStore
binding, read-only database outcome and consistent cleanup receipts. Audit report
SHA256: `49aa66a020c004b3a5426caa4f5f9866432240525c0b022df4fcba820a086e9f`.
It confirms no gate or MOSS-dispatch change and no nonempty-speech claim.

## Bounded nonempty public-speech smoke, 2026-11-03

The optional `tools/strict_uploader_smoke.py --input PATH --input-sha256 SHA`
accepts a hash-pinned existing PCM16 mono 16 kHz WAV of at most 30 seconds and
checks its bytes before starting the service. It uses Python's WAV-compatible
parser, not a general canonical-RIFF validator. Default input remains synthetic
silence. Chunk expectations derive from input frames; startup, native ownership,
asset verification, single-shot session creation, and cleanup gates are unchanged.
The historical result schema/capture label still contains `synthetic`; explicit
samples are distinguished by `input_kind` and `input_sha256`.

One fresh isolated accepted-candidate run used the existing public release's
`test_wavs/zh.wav`, not the earlier exposed English sample. Its 178,988 bytes and
89,472 frames (5.592 seconds) matched SHA-256
`b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373`.
The original archived release was rehashed against the historical inventory:
163,002,883 bytes, SHA-256
`7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e`;
the tar member independently matched the sample bytes.
Source: https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2

The actual supervisor exited 0; strict native startup was ready, one uploaded
source became one completed chunk, and the page was published/released with EOS.
Two nonempty text segments and two retained primary provenance packets/outcomes
were durable. The owned-cell database had zero PCM cells: this is intentional
primary-only compact-observation behavior, not evidence of retained audio cells.
No MOSS intents/results were created. Cleanup reported worker stopped, cells/jobs
stopped, store safe, lock closed, and permit clear without cleanup errors.
The input and executed source were unchanged. The sampled peak RSS was
849,182,720 bytes; this short sample is not an endurance/stability measurement.
The local RESULT.json SHA-256 is
`7dc130d32f209a66c17837ef74caf8feff76762ee47c91fe4f87fb38f00a6155`.

Accuracy remains unqualified. The unchanged test configuration selected English
for a Chinese sample. Its first segment resembles the upstream int8 example
(normalization aside), but the short final core emitted an extra English tail
relative to that example. Tail speaker support was unknown with
`embedding_dimension_mismatch`; it must not be described as complete speaker
success. The upstream example is model output, not independently verified ground
truth: https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
No parameters, cut rules, or expected text were tuned after observing the sample.
This proves bounded nonempty strict publication, not accuracy, recorder linkage,
physical continuity, cross-chunk identity, PCM retention, migration, or rollout.
Audio, transcripts, raw databases, model assets, and raw run reports stay local;
only source/tests and this hash/count/limitation summary are backed up.

The independent saved-evidence audit accepted this narrow nonempty primary path:
REPORT SHA-256 `27b1422b290da6b25c7dfbfa87c8e4190bcfee8c682c9f9e67f993bc07914e92`.
Its source is preserved as `independent_speech_audit_20261103.py`; like the earlier
historical audit scripts it binds local evidence paths and writes a report beside
itself, so it is not a portable test to run inside a verified source snapshot.
It independently checked packet digests/links, two core compacts, binding and
exact two-literal candidate module delta, assets, lifecycle receipts, and evidence
unchanged. The updated focused source suite passed 32 tests, with one existing
Starlette/httpx deprecation warning; this was not a full-repository test run.
