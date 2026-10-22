# Reconstructing this source checkpoint

## Product source

1. Retrieve commit `5470f08d568da759d3087c2bb2cb907227e8ff9a` from
   `Headmaster218/long-audio-backend`. Its root is still the older published
   product. Follow `reports/audio-native-crop-dev69-checkpoint/README.md` to
   reconstruct the dev65 → dev66 → dev67 → dev69 revision-02 candidate in a
   separate tree. Do not mutate archived reports.
2. Compare each existing file with SOURCE_DELTA.json's before_sha256; a null
   value requires the path to be absent. Copy all seven `source/` files to the
   same relative paths in a new candidate copy and verify after hashes.
3. Alternatively, from that fresh dev69 candidate root, run
   `git apply --check /path/to/census-from-dev69.patch`, then apply that patch.
   Source payloads are authoritative. Do not apply directly to the older root.
4. Current finite controls are:

   python -m pytest -q tests/test_trial_sampling_census_stage1.py tests/test_trial_sampling_census_stage2.py tests/test_trial_sampling_census_portability.py

   Use a fresh temporary directory and the parent's finite execution envelope.
   These tests do not complete the integration gaps described in
   CENSUS_REMAINING_SCOPE.md. Preparation documents retain their original
   not-executed statements; REPORT.md records later root-run outcomes.

## Restored assets and smoke paths

`runtime/RESTORE_INVENTORY.json` supplies official download URLs, sizes, hashes
and license references. Its six download rows sum to 427,331,627 bytes.
`runtime/download-receipt.json` and `runtime/expanded-receipt.json` preserve the
observed verified download and extraction identities. The exact helper files
are included, but intentionally keep their historical relative paths:

- Place RESTORE_INVENTORY.json at prospective-audio-plan-20261022/RESTORE_INVENTORY.json.
- Place download.py and extract.py under audio-model-restore-20261022/.
- Use fresh asset destinations; the downloader rejects existing destinations.
- Reproduce NumPy 2.5.3 from the normal PyPI registry, checking the exact wheel
  filename, size and SHA256 in runtime/NUMPY_WHEEL.json before installation into
  audio-audited-runtime-20261022. No wheel is included here.

Do not silently reuse an unverified download, execute an unexpected binary, or
widen runtime bindings to match a different dependency. Follow required source,
license and execution approvals in the environment where reproduction runs.

The exact successful smoke files belong at
`audio-restored-smoke-plan-rev03-20261022/`. The frozen manifest records the
expected sibling source, runtime, model and bundled WAV paths and all byte pins.
It uses dev69 source at `audio-crop-policy-candidate-rev02-20261022`, not the new
census source. Recreate that tree independently. The launch script owns its
child process and requires a fresh `audio-restored-smoke-run03-20261022` output.
If an interpreter, package or other pinned artifact differs, stop and prepare a
separate prospective revision; do not change the archived frozen files.

The successful root run checked FROZEN.json's child/launch/manifest identities
before and after, with positive original-child wait. Read result and wait
receipt together. Model hardlinks in the output layout reference already
counted model storage; they were excluded from the generated-output cap.

`smoke-first-recovered/` is historical evidence only. Its exact old files and
receipts document the first run's unavailable attestation and preparation race;
they are not a recommended launch plan. No old raw audio or private database is
needed for source reconstruction. No general acoustic qualification follows
from the short bundled development smoke.
