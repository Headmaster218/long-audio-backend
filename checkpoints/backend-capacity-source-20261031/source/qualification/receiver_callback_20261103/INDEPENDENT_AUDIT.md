# Independent callback-run audit

The single approved loopback run passed independent read-only review on
2026-11-03. No server was rerun for this audit. The audit imports no backend,
server, native model, or database code and does not modify original evidence.
The detailed evidence remains private outside Git; this file contains only
aggregate outcomes and integrity anchors.

## Verified outcomes

- Six admitted writer opens correspond to six real closed-handle callbacks:
  five successful and one incomplete. Each open precedes its matching callback.
- A real 32-byte marker with one live writer remained outside intake through two
  empty polls of the same production watch. No peer request occurred during
  that hold. The final successful callback automatically published all three
  files, with no pending writers.
- Raw, published and admitted files are byte-identical and occupy three
  independent single-link inodes per file. The original published path remains
  bound into the runner intent and admission. Exactly one 8,192-frame input was
  selected; finalization and analysis completion were not asserted.
- Duplicate callbacks return identical cached results. A later same-attempt
  STOR was refused before opening/truncating the file. The failed marker is
  exactly 32 bytes, reports `completion_transfer_failed`, and was not published.
- The production Java failure flow performed read-only reconciliation and
  reported the expected failure. All client sockets and callback registrations
  were released.
- A separate process exited with valid complete-looking bytes but an open
  marker writer and no marker callback. Restart refused the published, failed
  and crash-interrupted attempts. Runner restart made zero actions or requests.
- Seven owned subprocesses exited and were reaped. All 19 observed listener
  checks recorded closure, the watch thread closed, and ephemeral keys were
  removed. Server error records are empty.

The run took **12.714 seconds**, with **164,896,768 bytes** peak process-tree RSS
and **102,220 application wire bytes**, within the reviewed bounds.

## Integrity and reproducibility

The reusable `audit_receiver_callback.py` checks the executed frozen copy,
not the mutable working checkout. It revalidated all **928 restored files**
(14,641,903 bytes), **528 pinned files**, **364 imported modules**, and
**1,304 test-tool files** (17,116,775 bytes). No mismatch, missing file or extra
restored file was found. SHA-256 and Git blob identities are checked against the
trusted restore manifest. The seven synthetic negative audit tests passed.

- Executed source checkpoint: `e716403eb6d7b8a03ec8096699cfde07440015ff`
- Original run report SHA-256:
  `80a4bd08884a6ec3a7c503920807c633040e56690fd11de687573d3ec15f6283`
- Reviewed pins SHA-256:
  `c27d857759e813829b02ee62b672550256c38353bd42f64c8e92cca8f9399397`
- Trusted restore manifest SHA-256:
  `a62cab410db023ed56f89b26437fa536c4c24630c8b8b643962b27b52e0b8309`
- Independent aggregate audit SHA-256:
  `f01b7c372dc597ac45fb26bbee242f58b160686a362a0537de2d63d4d02e74ac`

To repeat only the read-only audit, supply the private existing run, pins and
restore manifest to `audit_receiver_callback.py`, together with the expected
report and restore hashes. Use a fresh `--output` path outside the original run.
The audit refuses to overwrite its output. Do not invoke the qualification
runner merely to recheck the saved result.

## Limits

This is real local FTPS/TLS callback wiring with synthetic authentication and a
model-free backend peer. It is not a deployed receiver, a physical Android
device test, native ASR qualification, physical-continuity proof, or a claim of
analysis completion. Authentication and account-to-namespace authorization
remain the actual receiver integration's responsibility.
