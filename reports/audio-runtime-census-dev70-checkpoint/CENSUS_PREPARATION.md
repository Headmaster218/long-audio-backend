# Stage 2 rev03: independent-review corrections

SOURCE FROZEN, ROOT TEST EXECUTION PENDING. NO COMPLETE CENSUS CLAIM.
Stage 1 and stage 2 remain immutable. No tests/model/SQL execution by worker.

Three review corrections:

1. Optional platform dependency: guarded fcntl import allows the module/default
   Worker path to import without that transport dependency. Explicit sink creation
   rejects unsupported platform capabilities before creating files. This does not
   qualify Windows overall; the underlying baseline is Linux-qualified and Windows
   remains unverified. Bounded isolated-module tests load both the observer module
   and Worker with the simulated missing dependency, without replacing installed
   modules globally or spawning a subprocess.
2. Fork ownership: record creator PID and reject inherited-process append, invalidate,
   close and FD cleanup BEFORE acquiring the inherited mutex or performing sink I/O.
   The inherited object becomes locally invalid; it does not close/unlock/write the
   parent's descriptors or evidence. A fork child requires its own fresh sink.
   Synthetic PID-change tests assert zero sink I/O and zero mutex acquisition;
   no actual fork is performed. Existing flock is not treated as per-process proof.
3. Directory durability: initialize files and fsync the new directory, then fsync
   its parent directory before any successful initialization acknowledgement. A
   targeted parent-fsync fault keeps initialization invalid, propagates failure,
   closes creator-owned FDs and preserves the unacknowledged evidence directory.

Unchanged stage 2 limitations and full remaining implementation list are in
STAGE2_SCOPE.md. Those tests mostly exercise wrappers, not full Worker execution
or no-observer real-DB equivalence. Stage 3 must add actual integration coverage.
No transaction/maintenance implementation was added in rev03. Eligibility remains
UNKNOWN; terminal metadata remains deliberately UNQUALIFIED_STAGE1.

Root-only test command, not executed here:

    cd audio-sampling-census-stage2-rev03-20261022
    PYTHONPATH=. python -m pytest -q tests/test_trial_sampling_census_stage1.py tests/test_trial_sampling_census_stage2.py tests/test_trial_sampling_census_portability.py

Artifacts include patches from stage 2 and original baseline, full candidate source,
source hashes and SHA256SUMS. Apply to a fresh copy, preserving earlier evidence:

    cd <fresh-stage2-copy>
    git apply --check ../sampling-census-stage2-rev03-artifact-20261022/rev03-from-stage2.patch
    git apply ../sampling-census-stage2-rev03-artifact-20261022/rev03-from-stage2.patch
