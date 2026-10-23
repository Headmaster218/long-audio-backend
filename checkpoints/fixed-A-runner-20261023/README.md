# Fixed A runner source checkpoint

This directory preserves the 31 Python implementation and test files of runner
revision 06. It is a development source checkpoint, not a release, deployment,
self-contained execution environment, or full-suite qualification.

## Backend dependency

Use Headmaster218/long-audio-backend commit
`4ddb2937edad332a1c5582ecc04fa618539e3611` and follow
`checkpoints/recovered-source-20261023/README.md` to reconstruct the published
dev71 source overlays and apply the recovered backend source delta. Do not run
against that commit's older root product without the reconstruction. This runner
checkpoint adds no backend changes and does not replace the prior checkpoint.

## Scope

Revision 06 adds the fixed AMI A input geometry and uploader, A dispatch in
manifest preparation and lifecycle supervision, and corresponding controls.
The fixed window is 720 seconds, divided into three pages and eight sources,
with 69 work chunks. Public input identity constants remain validation code;
no audio bytes are included. Explicit observer-off operation, existing resource
limits, exact frame checks, mutation non-retry, and original process wait and
cleanup gates are preserved.

One fixed 12-minute operational run completed after finite controls. This does
not establish transcript or speaker accuracy, cross-day identity acceptance,
long-recording acceptance, or resolution of the earlier A10 failure. Earlier
failures remain failures and the lost A11 outcome remains unknown. Runtime
evidence and results are intentionally not bundled here.

## Restoration prerequisites

- Reconstruct and verify the backend first. Review and rebind
  prepare_manifest.py's source basename and source-index identity to the restored
  complete tree; do not bypass verification or assume the old whole-tree index
  was reproduced by a source-only restore.
- Independently prepare local settings, runtime and model assets, then generate
  fresh verified bindings for the exact restored paths and fresh output. Actual
  settings JSON, manifests, inventories, models, databases and logs are omitted.
- Acquire permitted public input audio independently. a_input.py and
  short_input.py validate fixed identities; test_actual_A_input.py requires the
  external original recording. No labels or transcript fixtures are bundled.
- Review fixed workspace, product and interpreter paths before relocation.
  test_process_lifecycle.py requires the named reconstructed baseline sibling.
- Settings-comparison tests in test_A_upload.py and test_short_upload.py require
  intentionally omitted local settings files. They cannot run from this source
  checkpoint alone.
- test_child_environment.py compares bindings.py, uploader_cli.py and
  short_input.py with a rev04 sibling. These unchanged reference bytes are also
  available in the earlier published runner source companion. Materialize any
  reconstructed reference explicitly; do not claim historical runtime recovery.
- runner.py intentionally refuses direct launch. The actual orchestration entry
  is lifecycle_cli.py with explicit mode and newly validated bindings. Its A mode
  starts real processing; restoring these files alone does not authorize a run.

No actual settings, measured environment or model inventory, audio, transcript,
database, execution log, credential, or internal recovery note is included.
