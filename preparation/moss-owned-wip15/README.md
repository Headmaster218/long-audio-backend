# MOSS owned-controller source checkpoint 15

This source-only checkpoint preserves the corrected frozen product/profile files and three focused test files before their execution. The Python 3.12 -S runtime-site correction has received static acceptance; complete source/test and ownership qualification remain pending. This is not a release or an execution-ready distribution.

## Correction since checkpoint 14

Python 3.12 -S can leave sys.prefix pointing at the base interpreter. The corrected source derives selected virtual-environment sites from the lexical interpreter path and bounded pyvenv.cfg, explicitly rejects system-site inclusion and missing/noncanonical selected roots, and checks runtime metadata/provider identity against those selected roots. The runner uses that same closed selection before the existing wheel-member verification. These are source-level changes, not a claim that execution has passed.

Checkpoint 14 remains preserved as superseded, unqualified historical source. It has not been rewritten or retroactively corrected. The default app, API and scheduler remain unchanged; this work is an explicit local developer-facing experimental controller.

## Exact source contents

- audio_backend/profiles/moss_handy_q8_0.json
- audio_backend/moss_handy_profile.py
- audio_backend/moss_handy_adapter.py
- audio_backend/moss_handy_runner.py
- audio_backend/moss_handy_controller.py
- tests/test_moss_handy_profile.py
- tests/test_moss_handy_adapter.py
- tests/test_moss_handy_controller.py
- README.md

The test sources cover profile/runtime selection, raw-first projection, single-call failure handling, ownership/cancellation/cleanup boundaries and bounded synthetic-worker wiring. Their inclusion does not mean they ran or passed. The controller test source contains an explicit tiny standard-library fake-child path; no child was started for this backup. Synthetic phrases, sentinel hashes and synthetic paths in fixtures are not captured user or model output.

All eight source files are byte-exact copies checked against the frozen source manifest, with file identity and hashes stable before and after the complete copy. This is a limited recovery snapshot; dependencies, full source gates and any authorized runtime qualification must be supplied separately.

No test, project/native import, model/audio read, network request or Git operation was performed to prepare this checkpoint. Private source seals, indexes, contracts, receipts, copy records, runtime/model/audio data, raw transcripts, credentials and captured environment values are excluded. Public model/revision pins, the ordinary product runtime-manifest integrity pin, generic system paths and static execution settings remain source constants. No release or gate result is claimed.
