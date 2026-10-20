# Audio endurance recovery checkpoint

Status at 2026-10-20 11:01 UTC:

- **Uninterrupted endurance: FAILED.** The interrupted attempt must not later be relabeled as a successful uninterrupted run.
- **Recovered processing: IN PROGRESS, NOT PASSED.** The restarted backend preserved the mandatory 195 completed results before starting input 006 in the same session, capture, journal and Store. No twelve-hour completion is claimed.

## Conserved progress and cleanup qualification

Five inputs had completed ingestion before the launch-monitor refusal, representing 38,400,000 frames of repeated real material. The authenticated stopped journal verified five clean inputs, no active input, nine pages and a published frontier of 37,632,000 frames. The stopped result baseline contained 195 completed chunks and 1,165 primary result rows. These results must remain unchanged through recovery and final verification.

The failed attempt's retained original processes were positively waited and no survivors were reported. The backend logged orderly application shutdown. Nevertheless, the monitor's overall positive-stop qualification was **false** because an original helper binding was incomplete and the shutdown census reported an error. Recovery preserves that distinction rather than rewriting the historical cleanup verdict.

## Original-launch observation failure and correction

The refusal happened while launching the passive journal helper after successful input 005, at the exact argv/executable binding assertion. The actual mismatching metadata was not retained, so a transient observation race remains a hypothesis, not an established root cause. Returning from process creation does not itself prove a pre-exec race.

The revised binding permits at most five observations within 100 milliseconds. The original process handle remains retained throughout. Kernel birth, parent, group, session and UID continuity are checked; admission still requires exact expected argv and resolved executable. PID reuse, lineage change, exit before proof and exhausted deadlines remain refusals. Bounded diagnostic hashes and lengths are recorded without dumping argv. No mismatched original is admitted. Cleanup responsibility now starts before launch/binding, preventing a not-yet-running shortcut from being mistaken for a positive stop.

## Stopped baseline correction

The first stopped-baseline attempt failed its strict source-conservation comparison. A readonly SQLite connection can create WAL/shared-memory sidecars. A zero-byte WAL and 32 KiB shared-memory file appeared at the baseline timestamp, consistent with that effect. However, the failed collector did not retain its before/after census, so the complete historical mutation set is not proven. That failed attempt remains preserved.

The successful replacement copied the stopped database and any WAL as ordinary readonly byte streams into exclusive scratch storage, then opened SQLite only on the copies. It did not ignore a nonempty WAL or exclude shared-memory files from source conservation. Before/after hashes and identities matched for all 19 source evidence files, with no changed entries. The copied snapshot supplied the mandatory completed-result baseline. Failure paths now retain both censuses and changed-file diagnostics.

## Controls completed

The full revision04 suite passed **53/53 controls**, including the earlier HTTP deadline/framing tests, eight original-binding controls and ten recovery controls. Binding controls include 30 rapid real helper processes with positive original waits. Recovery controls cover unchanged completed results, frontier regression refusal, source mutation refusal, no new session, skipping only the five committed inputs, retained schedule/restart/finalization, original-deadline refusal, environment preservation and cleanup after binding failure.

A separate synthetic ownership lifecycle also passed with positive stop, lifecycle completion, retained original ownership on intentional failure, and no survivors or monitor errors. These are bounded control results, not proof that the long run has finished or that the specific production refusal was reproduced.

## Timing, accounting and verdicts

The original monotonic origin was not explicitly serialized. Recovery therefore uses a documented bounded reconstruction, including a one-second allowance for integer boot-time quantization. The interval width is below **2.047 seconds**. The later bound is used for paced arrivals; no exact-origin or uninterrupted-wall-time claim is made. Original absolute TERM/KILL deadlines remain authoritative, and the recovery wrapper expires earlier rather than granting a fresh twenty-four hours.

Request counts, decoded bytes, ledger/log usage, resource peaks and the mandatory result baseline are carried forward. The stopped/unobserved interval is explicitly reported rather than hidden inside a claim of continuous short-gap sampling. Original two-core/nice-19, sampled-memory and disk limits remain unchanged. Overall uninterrupted success stays false; recovered completion can pass only its separately labeled final conservation and coverage checks.

The workload remains ninety distinct immutable copies of one fixed eight-minute real recording prefix. It tests repeated-material ingestion, accumulation, recovery and bounded operation, **not independent acoustic accuracy**.

## Package boundaries

This is a public-safe source/control checkpoint, **not a runnable deployment bundle**. It excludes deployment entry points, release and approval configuration, private filesystem locators, raw recordings, models, databases, voiceprints and raw run logs. External reviewed monitor/product dependencies and local lifecycle configuration are intentionally absent. The source transformations and original hashes are recorded in `provenance.json`; `manifest.json` hashes this package. The earlier public checkpoint is referenced by digest. Nothing in this package launches or resumes the actual run.
