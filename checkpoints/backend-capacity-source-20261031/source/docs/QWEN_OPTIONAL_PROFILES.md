# Optional offline Qwen model profile

The offline candidate runner defaults to the existing Qwen3-ASR 0.6B Q5_K_M CPU profile. An explicit freeze-time choice additionally admits one closed 1.7B profile. Primary ASR, HTTP, scheduling, candidate storage, recovery and the pure untimed alternatives contract are unchanged.

## Explicit selection

Use the existing `scripts/benchmarks/run_qwen_candidates.py freeze` command with its required source-plan, source-bindings, runtime-manifest, limits, source-scope and output arguments. Add:

```
--model-profile qwen3-asr-1.7b-q5-k-m-cpu-v1
```

Omitting that option preserves the seven-field `qwen-frozen-candidates-v1` envelope and implicit 0.6B selection. Explicit selection emits `qwen-frozen-candidates-v2`, adding `model_profile` and `model_profile_sha256`. The model-profile hash commits to the closed artifact/runtime/decode policy. Each request's existing `profile_sha256` still hashes its decode config. The run command takes the existing exact frozen hash and authorization receipt, and cannot override the selected model. This is an offline tool, not a service authentication mechanism.

Source/runtime/implementation inventories are still checked. New implementation bytes change the complete frozen digest even for the default profile. Existing manifests do not bypass that check. Model identity is never inferred from a filename. Changed or missing files fail closed; the tool does not download, convert or substitute assets.

## Exact provisioned artifacts

Both profiles require transcribe.cpp Python package version `0.2.3`, native commit `63a44d9239d610b3908e8a66b384924cd4a77217`, architecture `qwen3_asr`, and actual CPU backend/device. The runner checks the actual interpreter, imported wrapper/native-library inventory and bundled dynamic companions before sending readiness.

Default 0.6B artifact: 645,356,192 bytes, SHA256 `062a7bcb18675c2fe5ffd0e8b354eac6a1ceada9b2af9ef7168746ae16b54358`, actual variant `qwen3-asr-0.6b`.

Explicit 1.7B artifact: `Qwen3-ASR-1.7B-Q5_K_M.gguf`, 1,517,290,464 bytes, SHA256 `034c557fe92ff8fcd9a9c041cbdaad347be0a86a58d3a348f63cf3f0180879d0`, actual variant `qwen3-asr-1.7b`.

The explicit profile requires these exact runtime provenance values:
- model_id: `handy-computer/Qwen3-ASR-1.7B-gguf`
- model_revision: `3555bd238a8572bbace3ebf60d23b036dc0a5dbe`
- license: `apache-2.0`

The [converter's immutable revision metadata](https://huggingface.co/api/models/handy-computer/Qwen3-ASR-1.7B-gguf/revision/3555bd238a8572bbace3ebf60d23b036dc0a5dbe?blobs=true) supplies the exact LFS size/hash and Apache-2.0 model-card declaration. The [pinned runtime family documentation](https://github.com/handy-computer/transcribe.cpp/blob/63a44d9239d610b3908e8a66b384924cd4a77217/docs/models/qwen3-asr.md) documents both variants and untimed text output. The [upstream model card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) is a separate upstream source. Metadata verification does not establish that a local model has been downloaded, loaded or qualified.

## Unchanged decode and resource policy

Each window uses a fresh session: two CPU threads, 2048 context, F16 KV, automatic language, transcribe task, no history/context and no timestamps. The existing native generation cap is 256; it is not an observed generated-token count. Status 18, partial results, unknown truncation and failures remain explicit raw diagnostics and do not become complete candidates. No recovery or new retry policy is introduced.

The same finite caller-supplied job/call/frame/RSS/artifact/deadline/cleanup limits apply. Runtime inventories still cap each file at 2 GiB and the summed inventory at 4 GiB. Host reserve remains at least 2 GiB and disk reserve at least 5 GiB. Selecting 1.7B never raises a resource allowance or proves the model fits. Freeze performs streaming full source/runtime verification; it does not load a model. Run requires separate resource admission. The offline maximum remains 1024 windows, with the existing 2 MiB metadata bound potentially binding earlier.

## Reconstruction qualification scope

This fresh lineage is based on remotely recovered source and a small reviewed delta. Focused model-free tests use tiny synthetic sources and explicitly mocked constructor file-verification boundaries. They check profile identity, immutable selection, actual-device/variant rejection, default compatibility, raw normalization and no-replay/cancellation contracts. They are not a real-model or inherited-full-suite qualification. No earlier lost local source or acoustic receipt is reconstructed by this change. Provisioned-asset and bounded real resource/ABI checks require a later separately frozen run.
