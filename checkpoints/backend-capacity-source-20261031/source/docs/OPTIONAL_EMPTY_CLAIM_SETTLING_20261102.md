# Optional execution: bounded empty-claim settling (2026-11-02)

## Scope and cause

This is a new model-free repair based on public checkpoint
`48a8b4e0ba6ce88f0c02c11f6d10e006b8be3f17`. An existing preemptible optional
reservation could be cancelled with `optional_ownership_changed` when an idle
Worker's empty `claim_probe()` overlapped the unlocked resource census. The
fingerprint compared `_probe=False` with `_probe=True` despite no primary intent.
The deterministic regression uses the real permit/condition/claim protocol,
controlled process samples and generated fixtures, not an actual model.

## Contract preserved

- Reservation admission still refuses active probes. Existing reservations settle
  probes before both fingerprint snapshots; **all** ownership fields remain checked.
- The two waits and exactly one census share a single absolute 250 ms maximum,
  clipped to the original accepted-job and, once constructed, driver deadlines.
  No model replay, retry census, renewed grace or larger resource cap is introduced.
- Before input publication no driver exists; that gate uses the accepted-job
  deadline. Before spawn/admission and while running the original driver deadline
  and cancellation event are passed through. Legacy custom gate signatures remain.
- Condition waits release the lock; no census/Store operation holds it. Real queued
  primary intent, shutdown, cancellation, configuration and unavailable state veto
  promptly. The optional reservation remains held until positive cleanup proof.
- A caller-clipped census deadline is not global observer exhaustion. Genuine
  natural wall, row and identity failures retain their original recovery semantics.
- Durable-cell publication authority, revocation and readiness are checked again
  after the wait/sample, followed by original cancellation/deadline checks.
- Automatic text and automatic primary-probe policy paths are not changed.

## Synthetic verification

Three new test modules cover 45 cases: both empty-probe barriers, a probe wholly
inside sampling, probe-body exceptions, real queued intent, cancellation/shutdown,
full budget shared across barriers, no reset, late samples, reentrant callbacks,
positive-cleanup gating, real service-sampler error classification, durable source
revocation, original job/driver deadlines and actual controller call-site wiring.
No model/runtime download or real inference is used.

Independent final review passed: the focused selection passed 179 tests plus
4 subtests. Additional automatic-probe/comparison coverage passed 186 tests,
with 12 failures and 1 setup error reproduced as the exact same IDs on the
unchanged public baseline. These are explicitly not all-green suites.

Affected aggregate: **213 passed, 36 failed, 27 subtests passed**. The exact same
36 failure IDs were independently reproduced on the unmodified public baseline:
35 older jobs fixtures lack `recording_primary_usage_v1` schema before reaching the
changed gate; one existing owned-process wiring test does not launch its fixture.
This is NOT a full-suite pass. Those baseline failures were not hidden, deselected,
or repaired by weakening production schema checks. New/focused permit tests are
reported separately. Independent review is recorded in the checkpoint README.

Reproduce (with the repository's test/runtime dependencies installed):

```sh
python -B -m pytest -q -p no:cacheprovider \
 tests/test_optional_claim_probe_settle.py \
 tests/test_optional_claim_gate_budget.py \
 tests/test_optional_claim_sample_budget.py \
 tests/test_native_execution_permit.py \
 tests/test_optional_running_sample_busy.py \
 tests/test_moss_handy_controller.py tests/test_moss_handy_jobs.py \
 tests/test_primary_probe_sampling_diagnostic.py \
 tests/test_primary_sampling_recovery.py
```

## Remaining limits

This checkpoint is not a model-quality, device, long-duration, crash-recovery or
full-capacity qualification. No actual-model rerun is part of this repair. A probe
that remains active for the entire bounded envelope still fails safely, preserving
its reservation for cleanup; the fix does not guarantee success during persistent
queue discovery contention. It prevents cancellation solely due to a transient,
settled empty probe while preserving real primary preemption.
