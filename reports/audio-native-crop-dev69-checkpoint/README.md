# Reconstructable crop-policy candidate, revision 02

This is a source checkpoint, not an installed release or a test-pass claim.
All eight changed/new files are included under `source/`, including fixture,
tests and documentation. Nothing in this delta depends on a private local
helper. It does not restore the historical runtime, models or data.

## Base

Use published commit `4131b4dcda46a761dd185ae071dc49b985f3144c` of
`Headmaster218/long-audio-backend`, whose root product is dev65. In a separate
working source tree, first apply the nine dev66 overlays listed in
`reports/audio-endurance-root-reattestation-dev66-checkpoint/source-delta.json`,
verifying every before/after size and SHA256. Next overlay the eight source
files from `reports/audio-primary-history-recovery-dev67-checkpoint/source/`,
verified against that checkpoint's `files.sha256`. This produces the reviewed
138-product-file dev67 baseline (distribution version remains dev66).

Do not apply this delta directly to the older dev65 root. Do not overwrite the
archived checkpoint reports. Do not call restored source a restored historical
execution environment.

## Applying this delta

From this bundle directory, `sha256sum -c source-files.sha256` verifies every
included source payload. Copy each `source/` path to its identical relative
path in a **new copy** of the reconstructed dev67 baseline.

Alternatively, `candidate-full.patch` is a Git full-body binary-capable diff,
including complete new-file bodies. From the reconstructed baseline's root,
first run `git apply --check -p2 /absolute/path/candidate-full.patch`, then
`git apply -p2 /absolute/path/candidate-full.patch`. No patch application has
been executed by this preparation worker; the parent must verify it before
relying on this alternate reconstruction route. The included source payload
is the authoritative delta.

`candidate-files.sha256` records the full resulting candidate source tree using
workspace-relative paths. `source-files.sha256` uses bundle-relative paths.

## Root-only finite verification

First run `tests/test_primary_crop_support.py` and the unchanged legacy control
`tests/test_primary_voice_references.py::test_packet_overlap_abstains_even_when_the_selected_crop_is_clean`.
Then run the full modules:

- `tests/test_primary_voice_references.py`
- `tests/test_reviewed_primary_persons.py`
- `tests/test_primary_history_startup_recovery.py`

Use the root's fresh temporary directory, disabled automatic pytest plugins,
no bytecode writes and finite memory/time limits. Mixed legacy-support controls
need the declared soundfile/numpy dependency closure. Keep dependency errors
distinct from assertion failures, and retain all prior run logs.

Revision 02 adds explicit schema-2 negative-evidence, gallery-capacity,
contradictory-manual-claim and corrupted outcome policy-identity controls.
Production source is identical to revision 01. This worker executed no tests,
models, servers, database experiments or publication operations.

The revision-01 `diff -ru` review artifact omitted new-file bodies and was not
an applyable full patch. It is historical review evidence only; this bundle
supersedes it for reconstruction without deleting it.
