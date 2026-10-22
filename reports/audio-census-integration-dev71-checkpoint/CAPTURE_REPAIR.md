# Stage 3 rev02: observer capture noninterference repair

SOURCE FROZEN. ROOT EXECUTION REQUIRED. No product code, tests, SQL, model,
compile/parser or subprocess workloads were executed by this source worker.
No qualification or complete-census claim is made.

Candidate: audio-sampling-census-stage3-rev02-20261022.
Original failed candidate audio-sampling-census-stage3-20261022 is unchanged.

## Production delta and audit

Only recording_speaker_history.py and trial_sampling_census.py change.
Every history capture_transaction call now passes a synchronous producer. All
mapping subscription, max/addition and dictionary construction occur within the
existing exception-isolated capture helper, after its active/nonfailed check.
All seven capture_scope sites pass lazy extra fields; scope mapping reads are
also deferred under that same guard. The two observer-only counts.items loops
move into capture_counters, whose active/nonfailed check precedes iteration.
The helper stops on failed capture, including the existing 32-record cap.
No producer is retained: observed pre-state is copied before original mutation.
No SQL, hashes, JSON, filesystem or observer sink I/O is added inside transactions.
Inactive calls still allocate tiny closures/call helpers; they do not inspect
mappings, construct field dictionaries or enumerate counters. Zero overhead is
not claimed. Product operation arguments, order, SQL and results are unchanged.
Stage 3 manager binding capture was inspected: existing dict reads/assignments
are already inside BaseException isolation; no changes were needed there.

A producer error marks the trace invalid, suppresses snapshots/ACK on finish,
and leaves the original return value or exact original exception untouched.
No misleading acknowledged complete observation is emitted after failed capture.

## Test source added, not run

New test_trial_sampling_census_capture_isolation.py has 13 parameterized cases:
- inactive and already-failed traces never read throwing mappings/producers;
- empty, partial and malformed fact dictionaries invalidate only observation;
- throwing transaction/scope/scope-extra/counter reads are isolated;
- before/after counters are copied immediately, not after later mutation;
- unchanged minimal-fact cadence seam admits both with and without observer;
- exact original exception survives a failed capture.

The original live-window fixture is NOT edited. Root must rerun its two original
failures, these new cases, existing stage1/2/3 portability tests and wider suite.

## Separate terminal fixture patch

terminal-release-fixture-only.patch is NOT applied to this source or bundled in
the candidate fix patch. It adds primary_recovery_status='ready' to the existing
manager.history stub and nothing else. This explicitly scopes those terminal
cleanup tests to startup recovery already completed. Root should apply it only
to fresh copies of both baseline and candidate and compare; preserve unadapted
failures as evidence. No production behavior or test assertion is altered.

## Payload

rev02-from-stage3.patch contains the complete candidate delta, including the new
test. full-source.tar.gz contains the complete frozen candidate. SOURCE_SHA256SUMS
covers every candidate file. ORIGINAL_SOURCE_SHA256SUMS records the preserved
failed source. SHA256SUMS covers all artifact payloads including these manifests.
