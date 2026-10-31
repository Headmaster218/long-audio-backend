# Backend capacity reconstruction — source checkpoint, 2026-10-31

This is a development backup, not a release or a successful 12-hour/48-hour qualification.
The `source/` directory is a self-contained source tree frozen from local commit
`9e6117f77b2c75a1dd37781cd86b2c163c7c2b10`. It includes the October 31 capacity reconstruction,
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
The parent public commit is `9008770887e3c19323fc397bb8ffabe4b2e1ac10`; its tree and history
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
A preserved-data, mixed-build, interrupted-and-resumed 43,218-second audio-volume run
is still in progress. No 12-hour/48-hour success or uninterrupted single-version run
is claimed. This backup neither stops nor mutates that run. Old source documents may
describe earlier stages; they do not override these limits.

The snapshot operation did not execute the complete test suite. It scanned common
credential patterns and selects only tracked UTF-8 source/config/docs/test material.
