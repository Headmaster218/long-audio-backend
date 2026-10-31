# Experimental FrameMN temporal candidates

This candidate-only extension is **off by default**. It adds bounded, unverified temporal evidence to the existing event-candidate ledger. It does not replace Zipformer, change its event thresholds or speech scores, change the non-speech ASR gate, or promote any FrameMN output to the event table. There are no new endpoints or database tables.

## Explicit local configuration

The ordinary launcher accepts these additional options, alongside the existing backend/model options:

```sh
python -m audio_backend \
  --framemn \
  --framemn-checkpoint /absolute/local/frame_mn10_strong_1.pt \
  --framemn-profile knock-001-median9-experimental-v1
```

Equivalent `Settings` fields are `framemn_enabled=True`, `framemn_checkpoint_path` set to an explicit absolute local path, and `framemn_profile='knock-001-median9-experimental-v1'`. The profile is fixed; other values are rejected. Passing a checkpoint without enablement is rejected. The CLI resolves an explicitly supplied relative path locally before creating settings. Supplying a URL cannot trigger a download.

No new environment variable implicitly enables this experimental policy. Existing `AUDIO_DATA_DIR`, `AUDIO_MODEL_DIR` and `AUDIO_API_KEY` behavior is unchanged. This option belongs to the ordinary analyzer/worker path; the manifest-owned experimental fragment-provider launcher rejects mixed legacy policy flags.

The optional runtime imports only when enabled, on a non-silent analysis. The base/inference dependency lists have not gained mandatory PyTorch packages. A separately provisioned compatible CPU runtime must already contain PyTorch, torchvision, torchaudio and NumPy. The previously recorded research environment used torch 2.14.1+cpu, torchvision 0.29.1+cpu, torchaudio 2.11.0+cpu and NumPy 1.26.4; this is a record of that environment, not a claim that other combinations are validated. Do not infer that installing the backend installs or qualifies these dependencies.

The enabled adapter conservatively sets PyTorch intra-op threads to one. This is a process-wide PyTorch setting in the analyzer process and remains reduced after the optional call. Primary inference executes before the side channel. Existing process/resource limits still apply; no new memory, speed or deployment qualification is implied.

## Fixed evidence policy and its limits

- Model: FrameMN10, width 1.0, with the pinned **447-class AudioSet strong** label ordering. This is not the separate 527-class ordering.
- Decoded target: **Knock**, class index 228, only. Apply nine-frame median smoothing, then strict `score > 0.01`.
- Profile: `knock-001-median9-experimental-v1`.
- All other retained labels are diagnostic raw per-class tile maxima. They are not decoded events, interval evidence, calibrated probabilities, or evidence of door-slam detection. `category_scores` is empty and no FrameMN speech score enters the ASR gate.
- The 0.01 operating point came from a small, previously exposed calibration experiment on a **five-label broad-door union**. Reusing it for Knock alone remains experimental. It does not establish independently validated Knock-only precision/recall, timing accuracy, production fitness, or a calibrated probability threshold. No new quality evaluation is part of this integration.

A 40 ms output grid (640 samples at 16 kHz) describes score spacing. It is **neither onset/end accuracy nor end-to-end latency**. The nine-frame filter uses four future scores, giving a nominal 160 ms median-filter lookahead on the 40 ms grid. That filter lookahead is not end-to-end latency, and the filter is not causal. `onset_precision_seconds` remains null.

## Analyzer-local context, padding and bounds

The existing analyzer accepts bounded 16 kHz mono float32 audio, at most 60.01 seconds including its existing tolerance (960,160 samples). FrameMN uses at most seven sequential, nonoverlapping 160,000-sample model tiles. Only the last tile is zero-padded if necessary. Padding-only output frames are discarded; the partial final valid grid frame is clipped to real sample support.

The valid Knock score vectors are concatenated **before** median filtering. Median-nine smoothing crosses internal 10-second tile seams; half-sample reflection occurs only at the analyzer input's endpoints. Model context is still independent per 10-second tile. This does not recreate full-recording model context.

The result is **analyzer-local, not arbitrary-chunk invariant**. Changing upload/core boundaries, analyzer context, or the analyzer window origin can change model tiles and endpoint filtering. Exact core ownership prevents duplicated committed support but does not establish equivalent predictions under different chunking. Adjacent outer analyzer windows are not one continuous filter state.

There is one temporal candidate per actual tile, including tiles whose positive `frame_runs` are empty. A tile's diagnostics are not positive-event evidence. Full class-by-time matrices are transient and are not stored or exposed. There are at most ten public diagnostic labels and 125 original positive runs per tile. Age-/gender-specific human labels covered by the privacy filter are excluded.

The combined Zipformer and FrameMN ledger remains limited to 64 candidate rows per analyzer result/commit. Each encoded candidate payload remains limited to 8 KiB, and per-row provenance remains limited to 4 KiB. Run overflow or payload overflow **refuses the optional output**; positive runs are never silently truncated to fit. The existing worker-wide 8 MiB JSON result cap remains in force. The analyzer drops optional rows and records failure if their addition exceeds that cap; it does not delete primary rows to make room. A primary result that itself exceeds the existing limit is still invalid.

## Original support versus owned support

Read the existing `/sessions/{session_id}/event-candidates` endpoint or existing changes feed. FrameMN rows have `payload_kind='framemn_temporal_v1'`, `method='framemn10_knock_temporal_experimental'`, `time_precision='model_score_grid'`, and the existing `unverified_model_candidate` status.

The original model evidence is retained separately from core ownership:

- Outer `support_start` / `support_end`: original model-tile support on the session timeline.
- Outer `start` / `end`: the tile's owned core intersection on the session timeline; these do not mean the whole interval contains a Knock.
- `temporal.tile_start_sample`: original tile origin in analyzer-input samples.
- `temporal.frame_runs`: original tile-relative, half-open integer score-frame pairs. Multiply by 640 for sample edges, clipping the final edge to `tile_sample_count`.
- `temporal.owned_tile_sample_range`: tile-relative, half-open sample bounds retained by this core.
- `temporal.owned_sample_runs`: exact intersection of the original positive support with those core sample bounds, also tile-relative and half-open. A sub-grid boundary may produce a non-640-aligned owned edge. It does not change the original model grid or assert finer model accuracy.

For an owned sample interval `[a,b)`, session times are `support_start + a/16000` and `support_start + b/16000`. Context-only support outside the core does not become a committed positive run. The original `frame_runs` remain available for auditing even when their owned intersections are empty. The ledger validates these relationships and keeps the original window metadata unchanged.

## Execution and failure reporting

Disabled results retain the previous metadata exactly; no FrameMN status or policy keys are inserted. Enabled results add namespaced metadata:

- `framemn_status`: `completed`, `failed`, or `not_run_silence`.
- `framemn_model_inference_executed`: whether execution entered the model inference block. Model construction alone does not count.
- `framemn_model_call_count`: successful model-forward returns for this analyzer window, including one whose output later fails validation.
- `framemn_expected_model_calls`: planned tile calls for this window; this is not an attempted-call counter.
- `framemn_candidate_count`: retained optional tile rows; zero on failure.
- `framemn_failure_code` / `framemn_failure_class`: bounded safe failure information, when applicable. Raw exceptions and local checkpoint paths are not returned.
- `framemn_provenance`: enabled policy and available configured pinned model/label identities, separate from the primary Zipformer identity.

All tile calls and candidate checks must succeed before any optional rows are appended. Missing dependencies/checkpoints, invalid outputs, incomplete execution evidence, duplicate/reordered tiles, or row limits leave the primary events/transcript/gate intact and return explicit optional failure metadata. Silence reports `not_run_silence`, false inference execution and zero actual/expected calls without importing the optional runtime or opening its checkpoint. Existing primary failures are not reclassified as optional successes.

Chunk metadata describes only the last committed window. Durable `chunk.provenance.framemn_execution` separately aggregates committed-window counts: `window_count`, `completed_model_calls`, `expected_model_calls`, `model_inference_windows`, `failed_windows`, `silence_windows`, and `last_window_offset`. Failed and silent windows count as windows. Zero expected calls on silence are deliberate. This compact aggregate is updated in the same transaction as the existing result commit; retries and rolled-back commits do not double count. It is scoped to committed chunk windows, not the lifetime of an analyzer instance or every abandoned process attempt.

Stable provenance variants retain `framemn_provenance` and enabled policy fields separately from dynamic counters. Each FrameMN row has its own pinned candidate provenance. Zipformer rows continue to use only the original Zipformer event provenance. A configured digest by itself is not proof that its model executed; interpret it with the execution status and counters.

## Artifact identity and licensing

The adapted source and label map are pinned to [PretrainedSED commit 1aa47e482f7e89904cba2338999345025d8b4e36](https://github.com/fschmid56/PretrainedSED/tree/1aa47e482f7e89904cba2338999345025d8b4e36). The vendored package includes the unchanged MIT `LICENSE`, Python label map and `ADAPTATION.json`, which records original source hashes and the reduced, package-relative inference closure. Upstream download paths and unrelated model/training loaders are removed. No global import-path or torch.hub monkey-patching is used.

The sole accepted checkpoint has 15,537,114 bytes and SHA-256 `4a0fe320d5369987b772394c51881fb20a602967e6842f72f3e5c8181065ece7`. This hash is **local first-seen integrity bookkeeping, not a publisher-authenticated checksum**. The original [release asset](https://github.com/fschmid56/PretrainedSED/releases/download/v0.0.1/frame_mn10_strong_1.pt) had no publisher digest in the retained release metadata. The loader hashes the regular local file, uses the same descriptor for `torch.load(..., weights_only=True, map_location='cpu')`, and requires exact state keys, shapes, dtypes and finite tensors. There is no arbitrary-pickle or relaxed-state fallback.

The canonical ordered 447-label JSON SHA-256 is `89c223c4b1d5b5fefbccc10e5a652221a8c4fd70ab46a8a1885670e889719348`.

**This package redistributes code and labels only; no checkpoint weights are bundled or fetched.** The repository MIT code license is retained, but the prior research did not establish a checkpoint-specific redistribution grant. Do not infer weight redistribution permission, or general deployment permission, from the code license. Users supply their own local checkpoint and must establish the applicable rights for their use. This integration does not resolve that uncertainty or promote the model to a production default.

## Verification scope

Focused synthetic tests cover default-off parity, CLI/configuration validation, lazy loading, silent bypass, unchanged non-speech gating, exact tile inventory, optional failure isolation, payload/result limits, namespaced provenance, and transactional execution counters. Related adapter/decoder/ledger tests use synthetic scores or fake model objects. Passing these tests is wiring/ownership evidence, not neural-quality evidence. A separate bounded source-based Linux CPU API mechanics check subsequently completed; its exact scope, cleanup and sampled-resource limits are recorded in [the dev13 functional report](DEV13_FUNCTIONAL_CHECK_ZH.md). It is not a fresh-environment or installed-wheel qualification, and does not establish model accuracy or long-duration reliability.
