# dev64 bounded primary commit admission checkpoint

Status at 2026-10-20 11:34 UTC: **35 targeted controls passed; offline installed-package smoke passed. No actual dev64 endurance continuation has been qualified.** Earlier uninterrupted endurance remains failed. This is a bounded mitigation, not proof of the historical failure's exact SQL location or a claim that long-run stability is repaired.

## Observed failure

The previous recovered run stopped when an uploader encountered a primary work chunk marked failed. The stored worker error was `OperationalError: database is locked`. That chunk had a zero processing cursor and no committed segment, speaker-turn or event rows. The worker's generic exception handler had converted the processing exception into a permanent failed state. The precise SQL phase and extended SQLite error code were not retained, so they cannot be reconstructed with certainty.

This segment shut down positively with no monitor errors or survivors. Its stopped readback preserved 288 completed chunks and 1,750 primary result rows, including the previously conserved 195 completed chunks. Historical non-positive cleanup from the earlier interruption remains separately recorded.

The uploader journal had seven clean inputs and a partially consumed eighth input. Any authorized continuation must preserve the same session, capture, Store, journal and eighth input identity. It must not skip to input nine or treat the partial eighth input as completed.

## Narrow product change

Bounded primary result commits use a zero-timeout SQLite connection. Their `BEGIN IMMEDIATE` admission previously had no short contention retry. dev64 permits at most four admission attempts, with 10, 20 and 40 milliseconds of backoff, only when the numeric error belongs to the SQLITE_BUSY family and the connection proves that no transaction has been acquired.

All waits and checks consume the same original cumulative native handoff budget and absolute deadline. No timeout, SQL work allowance, model allowance or product quota is enlarged. There is no fresh budget per attempt.

SQLITE_LOCKED, invalid or string-only codes, other errors, an acquired transaction, SQL body failures and COMMIT failures do not authorize replay. The patch never replays the transaction body, COMMIT or model inference. Existing cursor checks and ownership cleanup remain in place. Exhaustion still fails closed and must not be described as stability repaired.

Bounded sanitized diagnostics now report the failing phase, numeric SQLite code, attempt and transaction/outcome state. They omit raw exception messages, SQL, identifiers, paths and result data. Diagnostic failures cannot replace the original database exception, and diagnostic output has a fixed process-wide record cap.

## Verification completed

- **35/35 targeted model-free controls passed.** These cover real SQLite contention and release, bounded exhaustion, malformed codes, LOCKED refusal, acquired-BEGIN refusal, deadline exhaustion, diagnostic failure isolation, existing claim behavior, same-handoff primary commit, no duplicate results, hard SQL/budget failures, uncertain commit acknowledgement and cursor-preserving retry behavior.
- The first control run had **34 passes and one failed test**. The test caught the intended deadline refusal inside the budget context, then incorrectly left the context's exit-time deadline check outside its assertion. The corrected revision changed only that test's assertion nesting. The original failure and source revision remain preserved; product budget enforcement was not relaxed.
- The offline wheel build, isolated installation and synthetic installed-package smoke all exited successfully with positive original-process waits. The installed smoke also retained the older-writer refusal contract.
- All **137 backend payload files** matched byte-for-byte between frozen source, wheel and installation. The full installed-file pin set contains 144 files. Package work took approximately 7.18 seconds, with sampled RSS plus its fixed charge below 200 MB and sampled package allocation below 68 MB.

These controls do not reproduce the historical exception's unknown location and do not establish twelve-hour completion, acoustic accuracy or a production performance guarantee.

## Continuation gates and package scope

A separately reviewed continuation must preserve all 288 completed results, explicitly reconcile the failed work item through the existing control API, and resume the same partially consumed input eight. Original absolute deadlines and the disclosed interrupted-run verdict remain authoritative. A recovered-completion verdict must be reported separately from uninterrupted endurance.

This checkpoint contains the seven changed source/test/metadata files, their before/after hashes and this report. It is a reviewable patch set, not a standalone deployment bundle. It excludes deployment configuration, approvals, private filesystem locations, recordings, model files, databases, voiceprints and raw operational logs. No publication or actual backend launch is performed by this package.
