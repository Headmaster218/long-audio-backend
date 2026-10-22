# Trial sampling census stage 2: optional synchronous hooks

Status: SOURCE FROZEN, ROOT TEST EXECUTION PENDING. NOT A COMPLETE CENSUS.
No worker-side tests, models, SQL, or native execution was performed. Stage 1
and its original artifact remain unchanged; this is a new separate source copy.

## Changes

Worker accepts an optional keyword-only trial_observer. A synchronous observation
scope starts after final core-cut geometry is known and immediately before native
prepare. It binds session/chunk/local/logical frame information; source mapping
and revision are explicitly UNKNOWN. It observes prepare completion, commit intent,
exact True vs non-True return, commit exception as UNKNOWN, and primary callback
invocation/returned/exception/absent. Original exceptions, returns, abort cleanup,
publication and callback catch behavior are retained. Early analyzer/core-cut or
preflight failures occurring BEFORE this scope are not represented yet.

Native capture records wrapper completion/failure, missing analyzer/native, original
commit evidence digest after its budget scope, no jobs, and swallowed primary-copy
exceptions. The digest is commit evidence, NOT asserted to be a mapping digest.

MossJobs records preparation outcome AFTER the original method and original read
transaction return, including absent handle with UNKNOWN reason. It also records
the original due result after primary_due's DB context returned, without a second
query. Scalar sampling state from that same DB snapshot is NOT exposed yet.
No observation was added inside a product DB transaction. No product DB schema,
threshold, eligibility or authority change was made. Logging may change timing;
this is decision-rule preservation, not a counterfactual sampling equivalence claim.

Observation is explicitly synchronous and context-local; background maintenance
threads do NOT inherit it. No observer is installed by default. Sink failures are
sticky through ObserverPort; throwing observers are contained. Collection control
must still watch health and stop the product orderly. Failure is NOT a product skip.

All stage 1 bounds, append/fsync behavior and no-reopen restriction remain.
Terminal metadata remains UNQUALIFIED_STAGE1, deliberately unchanged: the stage
label signifies an unqualified transport seal, not full integration version.
There is no COMPLETE-producing API. classify_window always returns eligibility
UNKNOWN, even when commit and callback have positive observations.

## Root-only gate commands, not executed by worker

    cd audio-sampling-census-stage2-20261022
    PYTHONPATH=. python -m pytest -q tests/test_trial_sampling_census_stage1.py tests/test_trial_sampling_census_stage2.py

Then run existing worker/capture/Moss/history regression suites and the root's
focused dev69 gate. New synthetic tests cover actual wrapper entrypoints, exact
single product invocation, return/exception identity, missing callback UNKNOWN,
observer exceptions, context restoration, no-observer no sink I/O, and real native
early absence. They do not exercise complete real worker/DB/model execution.
Stage 1's unchanged-source control now excludes exactly the three integrated
product modules; all other baseline product Python files must remain identical.

## Full remaining list before acoustic qualification

1. Earlier window lifecycle/preflight/analyzer failures before final core geometry.
2. Actual same-snapshot source revision/mapping digest and recording identity in
   opportunity keys; null identity causes cannot be resolved by invented hashes.
3. Same-snapshot due scalar state from HistoryStore, with no new DB reads.
4. Buffered claim/skip transaction pre/post/recheck/counter/reservation outcomes;
   emit only after original transaction exit, and preserve unknown commit acks.
5. Specific absent-handle reasons (history disabled, recovery, owner, recording);
   current generic absent is UNKNOWN and cannot count as an eligibility decision.
6. Background maintenance retry/claim-absent/hard-close/terminal success/failure;
   bind delayed outcomes to ORIGINAL opportunity, never latest highwater.
7. Root stopped-snapshot exact core tiling/cursor/packet/callback/claim/terminal
   reconciliation, with no counter/attempt backfill for missing decisions.
8. Collector health monitoring and orderly stop, 5.5 GiB floor, model/process/output
   controls, immutable final snapshot linkage, terminal validation/seal statuses.
9. Qualified restart ownership/new epoch if needed. Reopening evidence is prohibited
   here. SQLite/sidecar crash atomicity is NOT provided; any gap remains UNKNOWN.
10. Root integration regression, real DB absent-observer decision/state equivalence,
    all original design synthetic races/crash/failure/boundary cases and an end-to-end
    collector dry run. Unit tests alone do not qualify a new acoustic trial.

## Artifact application

stage2-from-stage1.patch applies to a fresh stage 1 copy.
stage2-from-baseline.patch applies to a fresh original dev69 rev02 candidate.
Both are additive/targeted source patches; full-source.tar.gz is the complete new
candidate. SHA256SUMS and SOURCE_SHA256SUMS freeze the deliverable identities.

    cd <fresh-stage1-copy>
    git apply --check ../sampling-census-stage2-artifact-20261022/stage2-from-stage1.patch
    git apply ../sampling-census-stage2-artifact-20261022/stage2-from-stage1.patch

No readiness claim should be inferred from source preparation. Root retains the
original broad-gate failures independently of isolated passes. Historical dev68
remains INCONCLUSIVE; this instrumentation does not revise it.
