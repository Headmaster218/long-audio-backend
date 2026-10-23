# Runner source companion

These27 Python implementation/test files preserve the reviewed runner source.
They are source backup, not a self-contained execution environment or a release.
No actual settings, runtime/model inventory, model/audio file, database, log or
credential is included. Original code bytes and validation constraints are kept.

Before executing a restored copy:
- Restore the backend using the parent checkpoint README and source delta.
- Review and rebind prepare_manifest.py's source basename/index to the newly
  verified complete source tree. The old full-tree index is not reconstructed
  by this source-only checkpoint; do not simply bypass verification.
- Regenerate local settings and closed-runtime/model bindings, and acquire the
  external short audio independently. short_input.py validates its exact identity.
- Review fixed workspace/candidate/interpreter paths before relocation.
- test_process_lifecycle.py needs the named reconstructed baseline sibling.
- test_short_upload.py includes a comparison requiring the intentionally omitted
  settings JSON files; regenerate them before claiming that gate can run.
- test_child_environment.py compares six rev04 sibling reference files. They were
  byte-identical to these rev05 files: launch_binding.py, prepare_manifest.py,
  lifecycle_cli.py, bindings.py, uploader_cli.py and short_input.py. A restored
  reference can use these same bytes but must be labeled reconstructed, not new
  evidence that the historical runtime was recovered.

runner.py intentionally refuses launch; lifecycle_cli.py is the actual
lifecycle/short entrypoint after new manifest validation. Preserve explicit
observer-off selection, original process wait/lock/cleanup and resource gates.
A successful module-discovery check is not successful model inference.
