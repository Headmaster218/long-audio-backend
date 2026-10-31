# Backend capacity reconstruction — source checkpoint, 2026-10-31

This is a development backup, not a release or a successful 12-hour/48-hour qualification.
The `source/` directory is a self-contained source tree frozen from local commit
`e9e9bb62b99b0b8d54d498424994fb29a57a606d`. It includes the October 31 capacity reconstruction,
strict shared sources and quota changes, bounded hot-only payload accounting, and explicit
safe page-admission reconciliation in the preserved-capture test controller.
It is not recovery of the lost unpublished October 30 source bytes.

## Restore

Clone this repository and check out branch `development/backend-capacity-source-20261031`
(or pin this checkpoint's containing Git commit). From the repository root:

```sh
python3 checkpoints/backend-capacity-source-20261031/verify_source.py
mkdir ../backend-restored-20261031
cp -a checkpoints/backend-capacity-source-20261031/source/. ../backend-restored-20261031/
cd ../backend-restored-20261031
# Use Python 3.12+ and the included dependency lockfiles for the chosen runtime.
# Optional ordinary development install in a newly created virtual environment:
python -m pip install -e '.[test]'
```

No application-source overlay or older checkout is required: all 150 tracked product
files and the selected source/tests/docs/configuration are present under `source/`.
`SOURCE_MANIFEST.json` pins every included path, byte length, SHA-256 and Git blob SHA-1.
The parent public commit is `4e5712d998a429dddf2c349cedbaa9df6641ad40`; its tree and history
are preserved unchanged outside this new checkpoint. Existing public identical Git
blobs are reused, but restoration does not require assembling prior checkpoints.

## Deliberate exclusions

No recordings, real transcriptions, models, databases, credentials, hidden credential
directories, runtime logs or raw run reports are included. Historical `reports/`,
`docs/reports/`, and stored execution-summary JSON files are omitted. The source README
and older docs retain historical references; links into excluded reports will not
resolve inside this standalone tree. Synthetic test strings/fixtures remain source.
Model manifests and license notices are metadata, not model weights. Models, native
runtime binaries, exact-runtime qualification, supervisor locks and test input assets
must be supplied and independently validated before real inference. Do not copy an
old runtime attestation merely because a historical source document mentions it.

## Validation boundary at backup time

The October 31 actual-model short gate processed 588 seconds (56/56 chunks), followed
by stopped integrity/FK, registered-file hash, cold-read and reopen checks. This is a
limited short gate, not accuracy, overlap or global speaker-identity qualification.
The preserved-data, mixed-build, interrupted-and-resumed capacity attempt failed:
an unexpected Worker exit was missed while the old supervisor continued refreshing
its own heartbeat. That stalled interval is not processing time. The attempt was
subsequently stopped with positive owner exits; a clean stop does not turn the
attempt into a pass. No 12-hour/48-hour success or uninterrupted single-version
qualification is claimed.

This checkpoint adds an equivalent pending-root query using existing admission
indexes, enabled-worker health/failure observations, and separate startup,
processing, stopped-integrity and terminal monitoring phases. Supervisor heartbeat
is not a substitute for actual completed-count progress. Worker failure remains
fatal; the five-minute no-progress signal is an inspection alert, not permission
to kill legitimate active inference or verification. Phase-diagnostic write failures cannot skip shutdown; the first failure remains
preserved through cleanup. Model-free tests are separate
from actual-model capacity qualification.

Final selected model-free aggregate at this exact source commit: 146 passed,
1 deselected (an existing capture fixture pins an older source SHA), and one
dependency deprecation warning. This was a selected aggregate, not the full
repository suite or an actual-model long-run qualification.

The snapshot operation did not execute the complete test suite. It scanned common
credential patterns and selects only tracked UTF-8 source/config/docs/test material.
