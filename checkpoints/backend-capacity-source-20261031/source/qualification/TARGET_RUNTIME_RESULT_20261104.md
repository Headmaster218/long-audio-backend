# Recovered target qualification workflow, 2026-11-04

The earlier unbacked portable patch was not present after cloud rollback. This is
newly reconstructed source based on backend commit 940787c, not a claim that the
lost patch or its results were recovered. Production source and runtime pins are
unchanged. Historical probes retain their exact-host default behavior.

## Fresh evidence

An explicit frozen target observation was generated with the current Linux
built-in-SQLite interpreter. Both generic SQLite and production-DDL CellStore
physical probes passed on the current cloud filesystem. Each used its original
bounded crash/recovery workload and returned fill/crash/recovery waits 0/-9/0.
The CellStore result reached 6144 4096-byte pages, 2048 synthetic sources, 55 cells,
and five SQLITE_FULL events. Saved rollback evidence contained 6044 journal
records reconstructing 168 changed pages. Recovery was byte-identical and matched
all six typed-table oracles. Maximum sampled target allocation was 49,975,296
bytes; sampled allocation is not an instantaneous kernel bound.

The parameterized independent byte auditor rechecked actual saved evidence,
current source/runtime inventory and frozen observation. A separate read-only
review confirmed its hashes, full-capacity results, unchanged evidence and saved
wait receipts. Raw databases, journals, observations and runtime reports remain
private and are not included in source backup. The generic probe has its own
passed supervisor outcome; no separate generic independent-byte audit is claimed.

This demonstrates the qualification workflow on one current host only. It does
not activate production, qualify another host or dynamic SQLite binding, prove
power-loss durability, recorder provenance, speech quality or long-duration
operation. Next gates are an evidence-bound experimental profile, real-verifier
refusal/ownership/restart regressions and independent limited acceptance before
strict native smoke testing.

## Fresh focused regression result

One combined selected run passed 209 tests, with no skips and two expected TLS 1.1
deprecation warnings from a rejection test. It included target-observation/audit
refusals, strict preflight/supervisor controls, receiver publication/completion,
real loopback FTPS and the actual Android Java transport using assembled Android
233ed0c source. This is a focused suite, not the entire repository test suite;
the Java transport runs on the cloud host, not on a physical Android device.

## Test-only runtime candidate controls

A later explicit-evidence run passed nine real-verifier CellStore controls under
two-CPU, 1 GiB address-space and CPU/wall limits. The test fixture rechecks the
frozen target, pinned audit and physical result, all saved evidence, and exact
product CellStore source hash. Only private test stores receive the candidate;
shipped production profiles remain unchanged and are asserted unchanged.
Schema replay/conflict/pin/restart behavior, five pre-disk runtime mismatches,
existing-store binding refusal and unresolved-owner lock-token retention passed.
Synthetic authority and lock tokens do not qualify OS lock integration or native
startup. The nine controls intentionally skip when no explicit evidence is given.
This is a separate run after the 209-test selection, not a combined 218-test run.
