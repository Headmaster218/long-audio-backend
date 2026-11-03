# Independent local inbox native audit

This saved-evidence verifier never launches a native service, makes an HTTP call,
or opens an original service database through SQLite. It is separate from the
qualification harness and uses a fresh private output directory for copied
jobs/native-receipt databases. Raw reports, copies, audio and transcripts must
remain outside source control.

The successful evidence is the closed `backend-local-inbox-loopback-native-smoke-rev02-20261103`
envelope. Its candidate was extracted from published source checkpoint
`3bd9e1c80d8aa381478d4d5d941ff0a02645a909`, with only the accepted runtime-qualification
patch applied. The verifier compares all candidate files to the immutable Git
checkpoint and saved manifests, so later source-only reports do not change the
executed baseline.

The first launcher exited before creating a run directory or starting a service:
an empty managed `.git` marker was incorrectly treated as a real repository by
the harness output guard. That guard was narrowly fixed and independently tested
against the actual workspace and real repository/worktree markers. The failed
candidate/envelope was preserved. The revised candidate had a new reviewed plan
and separate parent launch confirmation. There was one actual native run and no
native retry.

## Run on a closed envelope

Use the qualified interpreter and a fresh output outside the envelope, candidate,
source checkout and original fixture:

```sh
python -I -B audit_local_inbox.py \
  --envelope <closed-rev02-envelope> \
  --preflight <closed-rev02-envelope>/PRELAUNCH_REVIEW.json \
  --output <fresh-private-audit-directory>
```

The audit checks selected-path ledger hashes, ordered process phases and original
wait receipts, exact session/capture request targets, duplicate/conflict handling,
explicit operator isolation, snapshot/source PCM bytes, immutable uploader chains,
completed native database rows, source/core receipts, canonical ASR identity,
private export hashes/permissions, exact readable-text reconstruction, normal
cleanup, and original evidence hashes/mtimes/ctimes before and after inspection.
Nonempty WAL or rollback-journal evidence is refused rather than recovered in place.

Actual ASR receipt linkage is required. A nonempty string or harness success flag
alone does not establish success. The audit does not qualify recognition accuracy,
speaker identity, original hardware continuity, full-capture completeness, abrupt
crash recovery, a native-service restart, multiple pages, long recordings, FTPS
receipt, or deployment. Historical wait receipts are saved evidence, not a new
observation of the original waits. Resource fences are sampled/cooperative.

## Verifier controls

```sh
LOCAL_INBOX_AUDIT_EVIDENCE=<closed-rev02-envelope> \
  python -B -m pytest -q test_audit_local_inbox.py
```

The verified run has **37 passing controls**: 26 model-free chain, PCM, inventory,
phase and ledger controls, plus 11 closed-copy positive/negative checks. The latter
cover private permissions, artifact hash drift, mutating export requests, rehashed
foreign ASR text/work/timestamps, unsupported accuracy claims, readable-text drift,
ASR-skipped receipts and native source-receipt drift. All mutations target temporary
copies and the original envelope inventory is checked after the suite.

Without the environment variable, the 11 saved-evidence checks are skipped.
The same checks may explicitly adapt the prior public-speech envelope as a verifier
control fixture. That adaptation is not evidence of a new local inbox native run.
