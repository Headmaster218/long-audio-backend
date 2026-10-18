# Concurrent comparison source checkpoint

## Status

Checkpoint 06 preserves unchanged checkpoint-05 product code, 27 passing selected functional cases, and a substantive concurrent primary-budget failure. Do not deploy it, open production databases with it, or enable its policy. The additive schema and old-reader refusal marker run during Store initialization even with the policy off. The useful small functional paths passed, but the representative comparison interfered with the first primary core's existing cumulative 0.5-second capture deadline. Large-history throughput and safe coexistence remain unqualified. No actual model has run. See PRIMARY_HISTORY_GATE03.md.

## Existing task and resource ownership

Comparison remains one tracked task and retained score prefix, with one 128 MiB reservation and one separately reported CPU lane. Its CPU grant no longer requires primary, optional inference, probe or handoff demand to disappear. It never obtains a native/model execution permit. Ordinary inference ownership and nonpreemptive cleanup are unchanged.

A provisional comparison owner is charged before resource sampling. Sampling runs outside both the permit condition and manager lock; an exact owner/generation/configuration recheck precedes the run grant. A short maintenance claim excludes competing cleanup or restart callers without holding those locks through the census. Positive thread death, buffer disposal and reservation release remain distinct. A validated primary sample can revoke an over-budget comparison while primary continues.

The real comparison thread configures only its own Linux native thread ID to nice 19 and a single allowed CPU, then verifies the readback. Unsupported platforms, a single-CPU inherited mask or unverified settings leave comparison pending. This is a time-shared placement policy, not a dedicated-core guarantee. If inference threads share the allowed mask, they may use the same CPU. This checkpoint does not establish a separate core for the earlier actual-model CPU 0-1 profile.

Compute runs outside database snapshots and manager/publication locks. Each bounded compute quantum is followed by scalar authority/currentness checks. The existing cooperative limits are 50 ms compute and a separate bounded SQL phase of up to 250 ms, plus scheduler/GIL overrun; these are not hard real-time guarantees. Configuration changes, resource failures and cancellation retain ownership through cleanup. Exact complete publication still requires the original revision-current compare-and-swap.

## Admission and revision races

A building comparison no longer by itself blocks a naturally due observation claim. The normal authenticated claim path atomically retains every admitted candidate and dependency, advances history revision and clears the current public result. A pending candidate blocks positive publication. A completed late candidate causes the old task to rebase with its valid internal score prefix, retaining every rival under the same original 180-second task deadline. Pending or continuously changing history may leave bounded lag or an incomplete result; a rebase cannot refresh the deadline or discard admitted evidence.

An old negative terminal outcome owns cleanup of its old projection only. It may latch an unknown-rival refusal only when its snapshot remains current; newer evidence stays pending, and invalid authority retains its invalid state. Cadence-only skips update high-water, bucket and skipped-count bookkeeping without changing comparison state or evidence revisions. A clean published scope therefore does not rebuild solely because a source opportunity was skipped.

## Focused qualification and blocked representative measurement

The 27 selected functional cases passed on the final source. They cover queued real Worker synthetic cohorts, admitted appends, cancellation and revocation, uncertain authority, thread/resource ownership, cadence stability and negative-result revision races, as well as the positive A/A/B and two-observation abstention rechecks.

The full-size paired measurement used synthetic comparison transport/projection, real manager/permit/comparator work and a separate bounded scalar SQL timing scope. The primary-only baseline passed. With the comparison thread on CPU 4 and the primary lane configured on CPU 3, the first concurrent primary core hit the unchanged cumulative capture wall deadline before commit. Only 39,067 of 4,145,760 cold dot products completed before cancellation. No warm append result or complete allocation peak was obtained. The saved metrics are compatible with shared-interpreter contention, but missing failure-phase CPU/query timing prevents a causal GIL verdict.

The tested configuration therefore remains blocked for deployment. A separately reviewed fixed causal diagnostic must precede a compute-isolation decision. No process architecture has been added in this checkpoint. The product's 128 MiB reservation and each 180-second task deadline are unchanged; the test supervisor's larger memory envelope is not a product allowance. Actual-model CPU 0-1 placement, full-size production SQL/authority throughput and adverse multi-round performance remain unqualified.

No scoring thresholds, model policy, history pruning, schema, legacy 32-node path or dense upstream provenance limits are changed by this correction. The known baseline VM-budget failure remains separately unqualified.
