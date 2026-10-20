# dev65 inherited lock adoption: limited qualification

Date: 2026-10-20. Product version: `0.2.0rc3.dev65`.

## Product change

Explicit Linux inherited-descriptor adoption is added for server `instance.lock`
and uploader `uploader.lock`. Admission precedes Store/journal/directory mutation
only when this mode is selected. It verifies canonical single-link regular-file
identity, read-only access, the descriptor's preheld whole-file exclusive flock,
and contention from an independent same-inode open. Ownership is duplicated on
the same open file description with CLOEXEC. Incoming child fds are consumed;
cleanup is close-only, never `LOCK_UN`. Unresolved cleanup remains strongly
referenced, including interrupted cleanup and discarded caller objects.

Default acquisition timing and existing inference/recording policies are
unchanged. See [the interface contract](../docs/INHERITED_LOCK_ADOPTION.md).

## Recorded finite results

- 79 selected model-free synthetic checks passed, with zero failures, errors,
  or skips: 34 lock-admission/ownership controls, 24 constructor/lifespan/CLI
  controls, 13 existing CLI controls, and 8 separate supervisor-ownership
  controls. The eight supervisor controls are not included in this product-only
  publication. The published product test selection therefore accounts for 71
  of the 79 checks, not an independently reproducible 79-test product suite.
- A separate offline wheel build and fresh installation completed successfully.
  An independent audit found all 137 product payload files byte-identical across
  the qualified source, wheel, and installed tree. Installer metadata is not
  part of that 137-file equality claim.
- One installed synthetic migration/reopen/sampling/old-writer-refusal smoke
  passed. The historical dev62 child script and its pinned hash were unchanged;
  only the test's current expected version/name advanced to dev65.
- The finite execution owners recorded positive waits of the original process
  handles. Resource bounds were sampled, not a hard whole-system proof.

These results belong to separate invocations and boundaries. They are not
summed into a full-suite result. An earlier finite attempt had 76 passes and
one directory-descriptor rejection failure: file wrapping raised
`IsADirectoryError` before explicit type admission. The corrected source checks
the raw duplicate before wrapping it and adds two file-wrapper failure controls.
The strict directory `ValueError` assertion was not weakened. The earlier
failed source and raw evidence are retained privately and not overwritten or
included in this public source payload.

## Publication provenance

All published product implementation and test files are byte-identical to the
qualified revision. README and interface-document status paragraphs were
updated after qualification, and this result summary was added; those prose-only
changes were not inputs to the package run and do not change product code.
The publication manifest supplies relative filenames, base hashes and final
hashes, with the qualified hashes recorded separately for those prose updates.

The wheel verified in the offline audit had SHA256:
`7d773c1bed2e081eade15cddcda02d39df1c2f9bc155ad341171af300b60fb94`.
The wheel itself, private runtime configuration, raw logs and execution receipts
are intentionally not included in this source-only publication.

## What this does not establish

No actual recording continuation, model execution, listening service, uploader
HTTP flow, real-data mutation, endurance, production restart, acoustic accuracy,
or production deployment is qualified by these results. The model-free package
smoke uses synthetic data and does not validate real retained databases.

These are cooperative advisory locks, not protection against actors that ignore
flock, rename control files, or unlock a shared open description. Linux procfs
and the target filesystem's flock behavior remain prerequisites. Actual
supervisor lifetime, descendant cleanup, recovery and original-handle waits
require their own evidence before any real continuation.
