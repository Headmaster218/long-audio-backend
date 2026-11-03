# Independent read-only runtime preflight, 2026-11-03

## Scope and conclusion

Current restored source and the existing recovered runtime were observed directly.
No service, model inference, database migration, runtime installation, or production
source edit was performed. Module imports below did not instantiate applications,
workers, native libraries, or models. This is preflight evidence, not a successful
combined-chain integration result or new crash/allocation qualification.

The exact accepted two-literal isolated runtime patch remains compatible with the
observed interpreter, SQLite build/package and filesystem device. The unpatched
source retains the older package pin and intentionally does not match. Fresh
bounded integration still requires reviewed candidate, input and launch guards.

## Source and accepted patch

All 935 files in the restored SOURCE_MANIFEST match their byte lengths, SHA-256,
Git blob SHA-1 and the current repository tree at
`checkpoints/backend-capacity-source-20261031/source/` for commit
`6828077bb327c378ecd9c67154d034066a687500`.
The historical watch used 907 files and another checkpoint. Its success must not
be presented as execution of this current checkpoint.

A generated unmanifested qualification bytecode file was observed. Copy only the
manifest-listed source into the candidate; use `-I -B` and do not include caches.

- Original cellstore SHA-256: `efbd83e985482d47d3337e7290cddfacc680ca77c49452c904c18457211393d8`.
- Accepted patch SHA-256: `3a97845bf13b00e402ce73cb9990ce72f2696b9d82254cf442fbfde18a46afe8`.
- Accepted basis file/evidence literal: `27e9f806302f01da31d2f3bd2be0de327f195ccc63d0fd93c6b18ccb760a5589`.
- Expected patched cellstore SHA-256: `323bb5528ad656baa0699131154990a6dc5184cbf14a6b0591ba81482cf4fd77`.

## Direct runtime observations

- Interpreter length: 30,894,944 bytes.
- Interpreter SHA-256: `fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.
- Interpreter resolved-path SHA-256: `65fe300dfb28a7ca3f663634ae0aac5615189445bde833a7409891e1a273b22b`.
- SQLite package SHA-256: `6ac9a15ee2c393bd994e6b3784ed3f5fb97c9b4f986cbce03dccbad6226778df`.
- SQLite version: `3.53.1`.
- SQLite source ID: `2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9`.
- Sorted canonical compile-options SHA-256: `600e922c0614cd07a1640976eb2e283b43072322af46cbeb0df7a998990095cb`.
- Binding: `observed-built-in-no-separate-sqlite-mapping-v1`; extension, library and library-realpath pins are null. Mapping absence is not a static-link proof.
- Platform Linux; destination filesystem device 27. Explicit `vfs=unix` in-memory connection succeeds; the unchanged production store selects unix VFS explicitly. No fresh physical allocation/crash experiment was run.
- Accepted limits remain sector maximum 65,536 bytes, one journal header, and 2,097,152 bytes allocation slack.
- Nine CPU affinity slots available; observed free disk 61,214,932,992 bytes. These observations must be checked again before launch.

## Fresh dependency inventories, not historical equality

The named historical physical result, independent audit and regression result were
not found at their old locations. Their committed accepted summary remains pinned,
but those raw receipts were not freshly reverified. The historical 2,157-file,
733,515,621-byte model/runtime inventory was not located. It is not valid to label
the following new inventory as equality with that historical manifest.

Fresh private inventories hash all regular files beneath the existing recovered
Python environment and test-tools directory separately, excluding bytecode and
`__pycache__`. Symlinks are recorded separately and are not followed as directory
inventories; the interpreter target is covered by the exact runtime fingerprint.
These inventories contain file hashes only, not runtime data backups. Base-system
libraries outside these roots are not a complete OS snapshot.

- python-environment: 2122 regular files, 195,867,956 bytes, 4 symlinks; private inventory SHA-256 `3aef61300d1c4b99edf38dc50597b452bf07c6e4986715806d58051eb7283c0b`.
- test-tools: 683 regular files, 8,618,071 bytes, 0 symlinks; private inventory SHA-256 `c8172545afd46a8d05e3a3e5dfe92e23dae41d3e61760077890ec4394e81c2b3`.

All 27 exact package versions in `requirements-runtime-dev9.lock.txt` match the
qualified environment, including the transcribe packages. This is installed-version
comparison plus fresh file inventory, not revalidation against original wheel
archive hashes. Inference requirements match except build tool cmake 4.4.4 is absent.
No installation or runtime change was made.

The full development lock is not identical: test packages live in the separately
inventoried test-tools directory; certifi is 2026.10.19 rather than 2026.7.22 and
pluggy is 1.7.0 rather than 1.6.0. Cmake is absent. Build requirements setuptools
84.0.0 and wheel 0.48.0 are absent; packaging 26.3 is test-tools-only. Metrics
Cython, kaldialign, meeteval and scipy are absent. Optional DiCoW, MOSS Transformers
and overlap profiles require absent packages and/or older conflicting numpy,
soundfile and psutil versions. These separate profiles are not authorized by this
native SenseVoice/Nemotron preflight and must not be silently enabled. No accuracy
metrics or build-environment completeness is claimed.

Imports in the qualified environment, without service/model execution:
- `audio_backend.app`: ok.
- `audio_backend.native_owned_cell_store`: ok.
- `audio_backend.recorder_inbox_runner`: ok.
- `audio_backend.recorder_incremental_span_export`: ok.
- `audio_backend.recorder_intake`: ok.
- `audio_backend.recording_uploader`: ok.
- `audio_backend.settings`: ok.
- `audio_backend.transcript_http`: ok.
- `audio_backend.worker`: ok.
- `uvicorn`: ok.

## Asset content observations

The existing `ASSET_EXPECTATIONS.json` from the gate10 recovered asset basis was
read, with its `sample` entry excluded. All 32 nonsample files below matched lengths
and SHA-256, totaling 506,746,590 bytes. This checks aliases separately and does not
claim every listed library is loaded. Names below are relative asset locations,
not private output destinations.

- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_asr_c.so.1`: 68208 bytes; `b16719ac6c8614e43a9bed224b21dd17b9bfb59b5215cc7fe29482712090dc52`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_asr_c.so`: 68208 bytes; `b16719ac6c8614e43a9bed224b21dd17b9bfb59b5215cc7fe29482712090dc52`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libllama.so`: 3865744 bytes; `7fa0e98ff09bb87a3ecdf68d509de96d4014d54a7b89ee570f18084c99a3f5b2`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml-cpu.so.0.25.1`: 1186072 bytes; `468fa2631a621dd7d61118c3ddede6b75cf16f9d63c8602420c5a3a5e4570019`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_text_normalization.so`: 3650200 bytes; `39c3d410a0481b0d58a1bb9d3d5a2cb5d7fb9250b3406a291b154cb34b2a0ebf`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml-base.so`: 863168 bytes; `79c5512818e2a6936cdeba03b5d0ecaf1b27fb0671afff8f8d22a4d5903f1062`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml.so`: 47864 bytes; `dc4a83f10816da67ccbf0b108d03e8da090bfa52f8da2feffb81d900672560d4`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libllama.so.0`: 3865744 bytes; `7fa0e98ff09bb87a3ecdf68d509de96d4014d54a7b89ee570f18084c99a3f5b2`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml-base.so.0`: 863168 bytes; `79c5512818e2a6936cdeba03b5d0ecaf1b27fb0671afff8f8d22a4d5903f1062`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml.so.0`: 47864 bytes; `dc4a83f10816da67ccbf0b108d03e8da090bfa52f8da2feffb81d900672560d4`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml.so.0.25.1`: 47864 bytes; `dc4a83f10816da67ccbf0b108d03e8da090bfa52f8da2feffb81d900672560d4`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_asr.so`: 2265256 bytes; `596c0e0dd3da78e959e850ed3e90911524a6051d8f4405324d70a570c427e246`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_tts.so`: 1114264 bytes; `799cde968a0bb97d98c8c78851fc5794ab7eda785d9631a1c6179403821bebce`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libgcc_s.so.1`: 104984 bytes; `489803ded8a4d803e2c792763a2767c12185f5764b7bba080742d14ef2452b27`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libgomp.so.1`: 265856 bytes; `98b21ff32bb07b53f1fb266d887d2db0086ea66c8ad03696a1db53f65321cd94`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml-base.so.0.25.1`: 863168 bytes; `79c5512818e2a6936cdeba03b5d0ecaf1b27fb0671afff8f8d22a4d5903f1062`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_nmt_c.so.1`: 22928 bytes; `b7c4a1e055084460fa614864bbf8bfb37a8c0c4636194abb8a0c61e430cc07ae`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml-cpu.so.0`: 1186072 bytes; `468fa2631a621dd7d61118c3ddede6b75cf16f9d63c8602420c5a3a5e4570019`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libstdc++.so.6`: 1956992 bytes; `26916e7389a561537a0510661fc75538cea236139346f3dbe21bc64ba65605bf`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_nmt_c.so`: 22928 bytes; `b7c4a1e055084460fa614864bbf8bfb37a8c0c4636194abb8a0c61e430cc07ae`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libatomic.so.1`: 30968 bytes; `2bd070e46f561a6ff36e4e358f5b0eaf04fdbe37992db38223b13c760a4721ab`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libllama.so.0.5.0`: 3865744 bytes; `7fa0e98ff09bb87a3ecdf68d509de96d4014d54a7b89ee570f18084c99a3f5b2`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libggml-cpu.so`: 1186072 bytes; `468fa2631a621dd7d61118c3ddede6b75cf16f9d63c8602420c5a3a5e4570019`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_tts.so.1`: 1114264 bytes; `799cde968a0bb97d98c8c78851fc5794ab7eda785d9631a1c6179403821bebce`.
- `audio-model-recovery-20261023b/expanded/native/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_nmt.so`: 64520 bytes; `c05886ee5bbeec3e0f13c9544b60cafac3c14cb2fc403b84326cd1806ed29112`.
- `audio-recovery-model-layout-20261023b/sensevoice/model.int8.onnx`: 239233841 bytes; `c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51`.
- `audio-recovery-model-layout-20261023b/sensevoice/tokens.txt`: 315894 bytes; `f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc`.
- `audio-recovery-model-layout-20261023b/sensevoice/UPSTREAM_MODEL_LICENSE`: 5306 bytes; `7dba975a2069691db4992b0592d70828b330d2f8a30a71450f4e152a554e84f8`.
- `audio-recovery-model-layout-20261023b/speaker.onnx`: 39593761 bytes; `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b`.
- `audio-recovery-model-layout-20261023b/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx`: 91932865 bytes; `e61bdfaab92b48bc51209c0da91621fb8d89d1564bb5bb38be6e4a4380c1ed34`.
- `audio-recovery-model-layout-20261023b/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv`: 14675 bytes; `cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429`.
- `audio-model-recovery-20261023b/models/native-diarization/Nemotron-3-Diarization.q8_0.gguf`: 107012128 bytes; `08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1`.

All six configured layout assets also matched their independent layout pins:

- `speaker.onnx`: 39593761 bytes; `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b`.
- `sensevoice/model.int8.onnx`: 239233841 bytes; `c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51`.
- `sensevoice/tokens.txt`: 315894 bytes; `f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc`.
- `sensevoice/UPSTREAM_MODEL_LICENSE`: 5306 bytes; `7dba975a2069691db4992b0592d70828b330d2f8a30a71450f4e152a554e84f8`.
- `sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx`: 91932865 bytes; `e61bdfaab92b48bc51209c0da91621fb8d89d1564bb5bb38be6e4a4380c1ed34`.
- `sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv`: 14675 bytes; `cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429`.

## Candidate mapping and mandatory launch guards

Place the fresh manifest-only candidate beside the existing recovered model
layout and model-recovery roots so unchanged `configuration(REPO.parent, data)`
resolves the verified assets. Use the recovered environment interpreter with
`-I -B`. Apply exactly the accepted two-literal patch in the isolated candidate;
never edit production, weaken selectors, force verifier success, migrate an old
store, or alter model/settings defaults to make the test pass.

Before launch, independently verify the complete candidate manifest and patch,
all fresh runtime/assets hashes, explicit public-input provenance and exact PCM,
current focused regressions, fresh private empty data directory/device, source
immutability, and absence of conflicting candidate processes. Keep test-tools
separate from service imports. Do not treat this report as permission to omit a
missing guard or as a substitute for the combined harness review.

Preserve the 300-second Gate, two CPU affinity slots, 6 GiB sampled aggregate RSS,
5.5 GiB free-disk floor, 16.25 GiB sampled artifact cap, 480-second outer timeout,
120-second TERM grace and normal 90-second service shutdown. Keep original Popen
objects, record terminal request outcomes, and verify worker/store/lock/permit
cleanup plus descendant exit. These sampled resource bounds are not kernel quotas.
Successful future integration would still not establish acoustic accuracy,
physical Android capture, external FTPS transport, or whole-recording completeness.
