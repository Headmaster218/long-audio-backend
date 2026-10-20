# dev61 reviewed-primary person bridge: bounded qualification

Date: 2026-10-20. Version: 0.2.0rc3.dev61.

## Verified

This feature is an explicit manual association bridge, disabled by default. It
links retained primary observations to immutable person UUIDs across recordings.
It does not train a voice model, add acoustic exemplars, rewrite transcript speaker
labels or satisfy the separate automatic-learning requirement.

The final focused continuation passed 25/25 nodes: 14 affected/boundary checks,
2 unchanged full-capacity checks, 8 lease checks and 1 old-writer refusal check.
Capacity checks completed in approximately 161 ms and 150 ms. Canonical capacity
remains 1,024 people / 4,096 relation events with the unchanged 250,000 SQLite VM
step and 0.75-second operation limits. Streaming metadata validation preserves
bounded text/type checks and restores the temporary SQLite length limit even
when reading or closing the cursor fails.

The dev61 wheel built and installed offline without dependency installation.
All 135 Python package payload files matched source, wheel and installed bytes.
The installed-package ASGI smoke passed 1/1, checking two synthetic recordings,
explicit shared person UUID, resolving evidence URLs, byte-unchanged transcripts,
a separate app/Store reopen with models off, and one-source deletion retaining
the other source's attribution. Every loaded audio_backend module came from the
installed target. This was not a new OS process, reboot or live listener.

Build/install/smoke total: 3.730 seconds wall, 3.537 seconds aggregate CPU;
sampled RSS including a conservative 32 MiB allowance: 138,395,648 bytes;
sampled fresh allocation: 34,652,160 bytes. Envelope: 180 seconds wall,
120 seconds CPU, 512 MiB sampled aggregate RSS, 256 MiB fresh allocation.
Sampling is not a continuous kernel-enforced aggregate-resource guarantee.

## Preserved failure history

The original gate passed 84/86 ordinary tests; two failures and 14 unrun cases
were recorded; those 84 passes lacked a completed origin export. A subsequent continuation passed 4 affected and 3 compatibility
cases, then both full-capacity cases failed with metadata_capacity; lease and
old-writer stages did not run. The heavy SQL canonical preflight was replaced by
bounded streaming validation without increasing budgets or reducing capacity.
An attempted continuation stopped before any child/test because its gate-root
mode was 0755 rather than the controller-required 0700. A fresh corrected
continuation produced the 25/25 result above. Earlier failures remain historical
failures, not retrospectively passing tests; this is not a fresh full-suite pass.

## Remaining scope and migration caution

No models, real recordings, acoustic accuracy measurements, automatic identity
learning, physical-phone behavior, live network server or 12–48-hour endurance
were exercised. The bridge still depends on explicit human association.
Opening Store with the feature enabled installs a schema fence before bank
activation. Older unmodified binaries refuse that schema; use a backup taken
before feature-enabled Store opening for downgrade unless a separately reviewed
migration exists. Application deletion is not forensic disk/WAL/backup erasure.

## Evidence hashes and snapshot distinction

Wheel SHA-256: e3a0dd42a3fcee393a52e605664cd753240a3881abc3106cf7821c0e4e751f5c

Package execution receipt SHA-256:
453be5e0a1519f1497a8c8571f1adad02e98129f852f95dff666b52bac4883e1

The final source checkpoint changes only documentation after the accepted package
run: schema timing, validation status and this report. Runtime and test bytes
remain identical to the tested source. The preserved wheel was not rebuilt to
include these final documentation changes and is not distributed by this report.
