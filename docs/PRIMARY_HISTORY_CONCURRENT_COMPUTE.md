# Concurrent comparison source checkpoint

## Status

This is an unexecuted development correction over checkpoint 04A. Do not deploy it, open production databases with it, or enable its policy. The additive schema and old-reader refusal marker run during Store initialization even with the policy off. The earlier 34 focused mixed-history and two integrated passes belong to the earlier exact source; they do not qualify these changes. No actual model has run.

## Existing task and resource ownership

Comparison remains one tracked task and retained score prefix, with one 128 MiB reservation and one separately reported CPU lane. Its CPU grant no longer requires primary, optional inference, probe or handoff demand to disappear. It never obtains a native/model execution permit. Ordinary inference ownership and nonpreemptive cleanup are unchanged.

A provisional comparison owner is charged before resource sampling. Sampling runs outside both the permit condition and manager lock; an exact owner/generation/configuration recheck precedes the run grant. A short maintenance claim excludes competing cleanup or restart callers without holding those locks through the census. Positive thread death, buffer disposal and reservation release remain distinct. A validated primary sample can revoke an over-budget comparison while primary continues.

The real comparison thread configures only its own Linux native thread ID to nice 19 and a single allowed CPU, then verifies the readback. Unsupported platforms, a single-CPU inherited mask or unverified settings leave comparison pending. This is a time-shared placement policy, not a dedicated-core guarantee. If inference threads share the allowed mask, they may use the same CPU. This checkpoint does not establish a separate core for the earlier actual-model CPU 0-1 profile.

Compute runs outside database snapshots and manager/publication locks. Each bounded compute quantum is followed by scalar authority/currentness checks. The existing cooperative limits are 50 ms compute and a separate bounded SQL phase of up to 250 ms, plus scheduler/GIL overrun; these are not hard real-time guarantees. Configuration changes, resource failures and cancellation retain ownership through cleanup. Exact complete publication still requires the original revision-current compare-and-swap.

## Admission and revision races

A building comparison no longer by itself blocks a naturally due observation claim. The normal authenticated claim path atomically retains every admitted candidate and dependency, advances history revision and clears the current public result. A pending candidate blocks positive publication. A completed late candidate causes the old task to rebase with its valid internal score prefix, retaining every rival under the same original 180-second task deadline. Pending or continuously changing history may leave bounded lag or an incomplete result; a rebase cannot refresh the deadline or discard admitted evidence.

An old negative terminal outcome owns cleanup of its old projection only. It may latch an unknown-rival refusal only when its snapshot remains current; newer evidence stays pending, and invalid authority retains its invalid state. Cadence-only skips update high-water, bucket and skipped-count bookkeeping without changing comparison state or evidence revisions. A clean published scope therefore does not rebuild solely because a source opportunity was skipped.

## Pending finite qualification

New source fixtures cover real queued Worker cohorts, concurrent admitted appends, cancellation and revocation, unknown authority, thread/resource ownership, cadence stability and negative-result revision races. They have not run in this checkpoint. The earlier positive A/A/B and two-observation abstention cases also require rechecking on these product bytes.

The separately approved model-free measurement envelope is one cumulative 360 seconds wall and 300 aggregate CPU-seconds, CPUs 3 and 4 at nice 19, 384 MiB combined sampled RSS and 1 GiB address space per process. Its new retained test family is bounded to 64 MiB, with the ten preserved history evidence trees also counted under the existing 256 MiB aggregate artifact bound. These measurement limits do not enlarge the product's 128 MiB reservation or any 180-second task deadline. Exact commands and source pins require a separate gate before execution.

The proposed paired measurement pins the synthetic primary lane to CPU 3 and the comparison thread to CPU 4, recording actual masks and nice values, primary latency/progress, cancellation tail, comparison work, and retained resources. Full-size timing uses synthetic providers and projections; small real-store cases exercise admission and publication fences. Attributable comparison RSS above its reservation must be reported, even if the enclosing measurement stays below its limit. Python GIL and synthetic-driver results cannot establish real-model latency, full-size production SQL/authority throughput, dedicated physical CPU capacity or worst-case multi-round performance.

No scoring thresholds, model policy, history pruning, schema, legacy 32-node path or dense upstream provenance limits are changed by this correction. The known baseline VM-budget failure remains separately unqualified.
