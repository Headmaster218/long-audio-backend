# dev67 orphan-history recovery checkpoint

Status: sanitized source and qualification checkpoint, prepared for review.
Not published, deployed or promoted to a release. No database, audio, private
recording identifiers, raw runtime logs or local execution paths are included.

## Contents

- `dev67-recovery.patch`: repository-relative unified diff against the qualified
  dev66 source, covering exactly three production files, one existing test
  fixture file and four added test modules.
- `source/`: the exact qualified bytes of those eight files, not a complete
  checkout. Existing unchanged repository files and fixtures remain required.
- `QUALIFICATION.md`: bounded results, failure history and acceptance limits.
- `SOURCE_IDENTITY.md`: production identity and package-version disclosure.
- `files.sha256`: hashes of every delivered file except the manifest itself.

Production and test bodies were copied without adaptation. No source build,
import, test, model, helper or database operation was executed while assembling
this checkpoint. The results described here belong to the separately preserved,
root-executed qualification runs, not a new run of this delivery directory.

## Test portability boundary

The genuine process-crash test in `test_primary_history_recovery_stage2.py`
expects its separately reviewed `crash_control_child.py` adjacent to the
qualification source directory. That helper and its fixed old-source/runtime
layout are intentionally not bundled as a general-purpose public launcher.
The exact test is retained as qualified evidence; it must not be described as
standalone runnable from this partial checkpoint. No test is silently skipped,
rewritten or represented as independently requalified here. A portable crash
harness would need its own source review and finite execution qualification.

The remaining modules depend on unchanged baseline fixtures. This checkpoint
is a reviewable patch and exact payload, not a self-contained test environment.
Publication and deployment require separate review of the final checkpoint and
release identity. Automatic-template policy remains unchanged; identity-quality
acceptance has not been established by these storage tests.
