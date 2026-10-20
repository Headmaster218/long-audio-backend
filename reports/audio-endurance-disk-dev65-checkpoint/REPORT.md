# Shared disk-census audio endurance recovery checkpoint

Status as of 2026-10-20 13:51 UTC:

- **Original uninterrupted endurance: FAILED.** Subsequent recovery cannot change that verdict.
- **Recovered processing: IN PROGRESS, NOT PASSED.** The execution owner reported the disk-corrected continuation running at the authenticated partial tenth input. This report does not establish completion or a new live result count.
- **Last qualified stopped baseline: 400 completed chunks and 2,412 result rows.** All 373 results from the preceding baseline were preserved. The stopped snapshot had no failed chunks, nine clean inputs and the same partial tenth input.

## Established failure branch and remaining uncertainty

The preceding actual attempt stopped with “Incomplete disk admission census.” In the old resource walker, the first `lstat` `FileNotFoundError` caused `Budget.census()` to return `None`; an admission caller then required an immediately complete census and refused. That branch is established by the retained error and source. The walker did not retain the triggering path or stage, so the exact missing filename and its lifetime cannot be reconstructed. Normal concurrent creation or deletion is consistent with the failure, but the report does not claim a specific file was responsible.

The audit also found independent live stdout/stderr `rglob`/`stat` sums with the same enumeration-to-read race. The old `os.walk` lacked an error callback, allowing some directory-enumeration errors to be suppressed. These were addressed together rather than by ignoring one missing file.

The failed actual attempt remains preserved. Its server and uploader original process handles were positively waited, no survivors were reported, the final stopped readback conserved the prior results, and lock release was authorized only after durable terminal reporting. This positive cleanup applies to that attempt, not to an earlier lost executor session whose cleanup remains **UNKNOWN**.

## One bounded, descriptor-relative resource census

The correction uses a shared file-descriptor-relative, no-follow census for total run allocation, logical stdout/stderr usage and historical evidence-ledger allocation. It replaces the separate resource walks. Directory descriptors are opened and checked against their parent-relative identities; root and ancestor bindings are verified. Enumeration has entry, depth and duration bounds.

Allocation is charged conservatively using the greater of logical size and allocated blocks. Across a transient gap, accounting retains the prior aggregate, outstanding admission reservations and positive newly measured or partially measured identity increments, and never charges less than the current readable aggregate. A replacement identity cannot erase the retained charge of the earlier identity. A subsequent complete census must pass the retained-charge checks before the episode is cleared.

The normal sampling cadence remains five seconds. The existing fifteen-second last-clean freshness limit remains in force: a gap does not refresh the clean timestamp, and repeated churn cannot grant a new deadline. An incomplete initial census without a clean baseline is refused. Positively confirmed transient absence or regular-file replacement is recorded as a sampled gap; permission errors, I/O errors, symlinks, special files, device drift and directory or ancestor binding drift remain refusals.

The run ceiling remains 8 GiB, the free-space floor remains 5.5 GiB, and the normal terminal headroom remains 64 MiB. The shared logical stdout/stderr cap remains 256 MiB. The independently checked free-space floor is not replaced by the named-file sum.

This is sampled filesystem accounting, not an atomic disk-usage or historical-peak guarantee. Files that appear and disappear between observations, or unlinked files still held open elsewhere, can leave unmeasured usage. Reported gaps and conservative retained charges do not prove a bound on all such historical bytes.

## Durable diagnostics without admission recursion

A separate, exclusively created and identity-checked diagnostic descriptor records bounded gap, refusal and recovery evidence. It is capped at 8 MiB, with bounded record count and record size, and is flushed with `fsync`. It does not call the ordinary ledger admission path, avoiding recursive census/admission failure while recording a census refusal.

The diagnostic carve-out is contained within the normal 64 MiB headroom, not added on top of it. At least the diagnostic reserve remains protected even when another caller requests zero discretionary reserve. Direct diagnostic writes still check the hard ceiling and free-space floor. Terminal summaries distinguish currently observed bytes, retained charged bytes, outstanding reservations, last-clean time, peaks, gaps and diagnostic-write failures. A vanished entry with no readable measurement is identified as unmeasured rather than silently assigned a proven zero size.

## Recovery and immutable proofs are unchanged

The resource sampler does not relax immutable source validation. Exact input identities, source-file sets, hashes, stopped-copy conservation and post-positive-wait receipt or helper-output checks remain strict. The supervisor's existing instance and uploader locks, role-specific inherited descriptors, original process handles and positive waits remain the basis of ownership and cleanup.

The stopped journal retained nine clean inputs and partial input 010 with 6,144,000 frames consumed in that input, 75,264,000 frames emitted overall and 70,560,000 published. These are stopped-baseline coordinates, not a live-progress claim. Recovery uses the same Store, session, journal and immutable input identity. The formerly failed primary work was already completed; no retry POST is authorized or required by this continuation.

Known recovered-segment observation usage carries forward at **100 read-only GETs and 739,409 decoded bytes**. The existing bounded allowance is not reset. The 128 previously recorded namespace exit gaps also carry forward. Historical global request accounting remains unknown. The outer wrapper was shortened to 69,000 seconds; original absolute TERM/KILL deadlines, the original schedule and the remaining resource and cleanup limits were not extended.

## Finite qualification, including the failed fixture attempt

The first disk-correction control run completed **197 passing tests and one failure**. The failure was a stale test assertion looking for a literal prior baseline count of 373 while the qualified current baseline was 400. It is preserved as a failed control attempt, not relabeled as a pass. No lifecycle or actual continuation was launched on that failed qualification.

The narrow follow-up changed the fixture, not the disk sampler or product runtime. The terminal-ownership test now loads the expected count from the actual candidate's pinned stopped baseline and exercises real terminal reporting with a real evidence ledger. It checks successful conservation and release, conservation mismatch retaining ownership, and an injected terminal `fsync` failure that must not close the supervisor's ownership descriptors or remove their retained anchor.

The revised suite passed **198 finite controls**, including 41 focused disk-census controls, four disk-adapter controls and the retained 22 actual-shaped resume-run regressions. A separate two-generation synthetic lifecycle also passed with positive original stops, lifecycle completion, no survivors or reported errors, retained ownership on an injected helper failure, and locks checked through final receipt persistence.

These are finite source/control qualifications. They are not proof that the twelve-hour recovery has completed, that every runtime race is eliminated, or that the repeated workload has independent acoustic accuracy. The already qualified namespace-sampled memory protocol remains separate and unchanged by this disk correction.

## Completion and publication boundaries

Recovered completion can pass only after the original 90-input geometry, all 4,115 expected completed chunks, prior-result conservation, no failed work, sampled-resource checks and positive cleanup are established. Original uninterrupted success remains false regardless of a later recovered outcome. The workload repeats one real eight-minute recording prefix ninety times; it tests accumulation, recovery and bounded operation rather than acoustic diversity.

This checkpoint is public-safe inspection source and qualification documentation, not an executable deployment or approval bundle. Private release configuration, filesystem locators, internal session and chunk identifiers, recordings, model weights, databases, journals, voiceprints and raw runtime logs are excluded. Any source transformations and original hashes are recorded in package provenance. Finite-control results apply to the reviewed original bundle; transformed publication files are not represented as a separately tested deployment.
