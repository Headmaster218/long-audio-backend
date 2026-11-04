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
