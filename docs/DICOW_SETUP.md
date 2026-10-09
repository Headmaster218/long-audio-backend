# Experimental English target-speaker ASR

> Public-source edition: historical model measurements below were imported from prior development documentation, not rerun for this Git import. Full raw evaluation records and media are intentionally excluded. See [public test scope](../TEST_STATUS.md).


`dicow_en_experimental` is an opt-in candidate backend. It uses SE-DiCoW on the original mixture and keeps normal ASR untouched. Measured Mandarin results were poor; the adapter refuses non-English, mixed-language, or unknown-language evidence. A positive English gate is a routing hint, not a guarantee against language misclassification or hallucination.

## Install an isolated CPU environment

Python3.12 Linux cloud example (PowerShell users should use their environment's `Scripts/python.exe` paths; Windows execution has not been validated):

```sh
python -m venv .venv-dicow
.venv-dicow/bin/python -m pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu
.venv-dicow/bin/python -m pip install -r requirements-dicow.txt
.venv-dicow/bin/python scripts/setup_dicow.py --model-dir models/dicow-bf16
.venv-dicow/bin/python scripts/setup_dicow.py --model-dir models/dicow-bf16 --verify-only
```

Setup downloads only the pinned official Hugging Face model revision listed in `dicow-model-manifest.json`, verifies size and SHA256 of every asset, then rounds F32 tensors to small BF16 safetensors shards. It does not execute downloaded model code. Conversion uses one tensor mapping at a time and enforces a 1.5 GiB process RSS cap and 1.5 GiB host-memory reserve. Keep at least 5 GiB free disk after both the original and converted model are stored (about 6.2 GiB combined). Interrupted/invalid preparation is never ready: finish in a new empty destination or inspect the incomplete directory rather than overwriting an existing model automatically.

Existing verified downloads can be reused with `--source-dir PATH_TO_F32_HUB`. An identical prepared BF16 directory can be verified and hardlinked/copied using `--prepared-dir PATH_TO_BF16`. Hardlink mode requires no second model-sized disk copy. Every inference child rechecks the full pinned SHA256 manifest before importing the official local model modules. The health/readiness check only checks configured file existence/size and explicitly distinguishes that from full runtime verification.

The evaluated environment reads existing Torch2.6.0 CPU packages from `.venv-events` via a `.pth` file; it never modifies that environment. A fresh installation should use the standalone instructions above. `validation/dicow-research/requirements-measured.txt` records the exact research environment. No paid API, external AI quota, remote inference, blind remote-code loading, or pickle model file is used.

## Configuration

- `overlap_backend: dicow_en_experimental`
- `dicow_python`: absolute path to the isolated Python interpreter
- `dicow_model_dir`: absolute path to the verified BF16 directory
- `dicow_min_enrollment_seconds: 2.0`, range 2–30; this is a conservative occupancy heuristic, not proof of a clean or correctly identified voice
- `dicow_timeout_seconds: 300`, range 30–600
- `dicow_memory_limit_bytes: 3758096384` (3.5 GiB), allowed 2–4.5 GiB; a low cap may intentionally fail safely
- Existing `overlap_max_seconds` and `overlap_context_seconds` remain applicable; the SE child accepts at most four shared supports/12 total audio seconds per analyzer call, sequentially decoding two detected targets each. SE requires at least10 seconds of real accessible context. Nearby same-target overlap regions share a recentered10–12-second support and one inference; insufficient context/budget stays explicit
- Existing `language: en` or actual mixture-only detected language `en` is required. Any evidence of another/mixed language blocks routing, even if `language` is explicitly English

The analyzer must pass only bounded input, local raw activity, and a copied language-evidence configuration. The adapter never retranscribes the mixture using its callback. It selects at most 30 seconds of enrollment from the **already supplied ≤60-second analyzer waveform**. It neither scans later recording data nor reads a user/benchmark speaker registry. If either detected target lacks the minimum predicted target-only occupancy, the region returns `needs_more_evidence`, including the conditioning local ID, observed enrollment seconds and reason. The original mixture and region remain present. A solo/no-overlap input produces no candidate job and never loads SE-DiCoW.

## Output contract and limits

The interchangeable output is `regions`, `tracks`, and `metadata`. Every track is `quality_status: unverified`, `identity_status: unresolved`, `speaker_id: null`, and `profile_update_eligible: false`. Conditioning local IDs are provenance only. Source slots are not people; no cross-window source continuity or profile updates are allowed.

Timestamp units are **native phrases**, not word timestamps. `_units[].unit_kind` is `native_phrase`; a whole phrase is routed using the first intersecting overlap-evidence anchor, while its native start/end/text remain unchanged. A shifted anchor is tagged ownership_anchor_kind=overlap_evidence_routing and boundary_uncertain=true; it is not word alignment. There are no fabricated character/word durations. Original source support and enrollment offsets are recorded separately. Unresolved regions, no-text tracks and categorized timeout/resource/model/dependency failures survive alongside the unchanged mixture transcript.

This path is CPU-only. Upstream SE code supports PyTorch CPU/CUDA, not a validated Vulkan path. Windows, GPUs, CPU chips without efficient BF16, 12–48-hour integrated throughput, remain unverified. One actual60-second HTTP/native/SenseVoice/SE integration measured3.056GiB for the whole API process tree, below6GiB; this is not a long-duration guarantee. The measured sequential model process used ~2.5 GiB RSS and ~35–37 seconds for both voices in ~10 seconds of audio. The normal service process-tree guard still applies in addition to the child cap and host reserve.

See `reports/SE_DICOW_RESEARCH.md` for strict and uniformly normalized English scores, Mandarin regressions, conditioning caveats, original-source attribution, and the distinction between oracle and actual-mask evaluation. Model license: Apache-2.0; official card https://huggingface.co/BUT-FIT/SE-DiCoW/tree/470fce9ffff844dd53a27751cdf6c6df9efecb39.

Per-call candidate_core_start/end restrict overlap selection before the budget is spent. The full supplied waveform/turns remain available for enrollment. Shared phrases are stored once and linked to each related true overlap region using immutable track IDs; clients must deduplicate repeated references across pages by ID. Support recentering never reads outside the bounded analyzer waveform. A source phrase spanning a core edge retains its real interval and is explicitly uncertain at that boundary.

The conservative auto-language gate may skip English if any bounded context turn is misclassified as another language. This occurred in the actual integration smoke. Known-English mode passed without weakening that gate. Original audio and unresolved reasons remain accessible when skipped.
