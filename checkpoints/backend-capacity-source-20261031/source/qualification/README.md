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
