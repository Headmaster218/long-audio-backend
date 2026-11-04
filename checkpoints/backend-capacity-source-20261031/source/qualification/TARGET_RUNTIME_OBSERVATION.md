# Explicit target-runtime evidence path

This experimental path records identity and gathers finite physical evidence. It
never activates a production profile, weakens the product verifier, migrates a
store, or qualifies a platform merely by observing it. Existing probe invocations
without target arguments retain their historical exact-host identity checks.

Use the intended built-in-SQLite Linux interpreter with `-I -B`. First create a
fresh private mode-0700 parent directory on the intended data filesystem. Run:

```
python -I -B qualification/target_runtime_observation.py \
  --data-dir /absolute/private-parent --output /absolute/private-parent/target.json
```

Keep the printed SHA-256 separately. Review the observation and harness before
running any workload. Unsupported bindings remain refused; observation does not
install or modify an interpreter or SQLite package. Existing bytecode is included
in the fingerprint; `-B` avoids writing new bytecode, not reading existing caches.

After review, an explicitly authorized physical probe can use:

```
python -I -B qualification/cellstore_crash_probe.py \
  /absolute/private-parent/new-evidence \
  --target-observation /absolute/private-parent/target.json \
  --target-sha256 EXACT_PRINTED_SHA256
```

The generic `sqlite_crash_probe.py` accepts the same options, but generic evidence
alone does not establish application-schema allocation behavior. Output must not
already exist. The parent and all experiment directories must be private and on
the observed filesystem. Baseline, attempt and recovery directories are explicitly
mode 0700. The target file is read through a bounded, no-follow regular-file check;
its exact digest is verified before creating output and is passed unchanged to
every child. Every child verifies actual runtime identity before database work.
Runtime and target identity are rechecked at completion. Package leaf inventory
is retained and checked against its aggregate fingerprint for independent audit.

The physical workload retains its original resource, deadline, signal, cleanup,
source-immutability and recovery controls. Limits are sampled/cooperative, not
kernel filesystem quotas or power-loss guarantees. Independent byte audit,
reviewed experimental candidate profile, real-verifier refusal/ownership tests,
and explicit limited acceptance remain necessary before a strict service smoke.
Neither these tools nor their output change the production allowlist.

The independent saved-byte audit is explicit and writes a new report outside the
evidence directory:

```
python -I -B qualification/audit_target_cellstore_evidence.py \
  --evidence /absolute/private-parent/new-evidence --source /absolute/source \
  --report /absolute/private-parent/new-audit.json \
  --target-observation /absolute/private-parent/target.json \
  --target-sha256 EXACT_PRINTED_SHA256
```

Assertions must remain enabled. The auditor rejects existing report destinations,
symlinks/special evidence, oversized inventories and source paths outside the
selected source root. It opens only the clean recovered database read-only; it
never opens the interrupted pair with SQLite. It checks recorded lifecycle
receipts but cannot independently re-observe past process events. See
`TARGET_RUNTIME_RESULT_20261104.md` for the fresh cloud-only scope.

## Test-only real-verifier candidate controls

`test_target_candidate_cellstore.py` is explicitly opt-in and never updates
`QUALIFIED_RUNTIMES`. Set all six inputs before invoking pytest:

- `TARGET_OBSERVATION`: absolute frozen observation JSON path.
- `TARGET_SHA256`: independently recorded observation digest.
- `TARGET_AUDIT`: absolute independent saved-byte audit report path.
- `TARGET_AUDIT_SHA256`: independently recorded audit report digest.
- `TARGET_EVIDENCE`: absolute physical evidence directory.
- `TARGET_RESULT_SHA256`: independently recorded physical RESULT.json digest.

Without inputs the nine controls skip; partial inputs fail. The session fixture
checks current identity, hash-pinned audit/result, and the audit's full saved-file
inventory. It creates a test-only qualification using the observed identity,
unchanged shipped allocation bounds and a digest binding the three evidence
inputs. Audit success is not production acceptance or permission for service smoke.
The private `CellStore` constructor alone receives this profile; the real runtime
verifier remains enabled. Every test checks that the product allowlist is unchanged.

Put pytest's fresh `--basetemp` on the observed filesystem. Include the source root
and `tests` directory on the test process's import path alongside test dependencies;
keep this test-only import path out of service launches. Bound the run externally
(e.g. two allowed CPUs, 1 GiB address space, 90/100 CPU seconds, 120-second alarm).
Controls cover schema publish/replay/conflict, pins, restart, five mismatches before
disk open, existing-binding refusal without namespace mutation, unresolved-owner
lock-token retention, and nonactivation of the product selector. Authority and lock
tokens are synthetic; this does not establish native service, actual OS lock,
recorder provenance, acoustic accuracy, migration, or deployment readiness.
