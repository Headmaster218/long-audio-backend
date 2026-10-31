# Bounded event-presence diagnostic

Source-only harness for Linux and Python 3.11+. Requires an existing backend checkout providing `audio_backend.events`, plus a Python runtime with its inference dependencies, soundfile and psutil. It downloads and installs nothing. Audio, independently supplied labels, licenses, model weights and results must remain in an external run directory, not this source repository.

## Prepare external inputs before execution

1. Verify the source's independent clip-level positive label and license/attribution. Other classes remain unknown, not negatives. Use one five-second clip; retain original bytes and provenance externally.
2. Normalize to exactly 80000 mono samples at 16 kHz, PCM16 WAV named `normalized.wav`. Record tool version, command, source/normalized hashes and any conversion. The reference normalization convention is ffmpeg with `-ac 1 -ar 16000 -af aresample=16000:async=0 -c:a pcm_s16le`, one decoding/filter thread. Do not infer event onset/offset from clip duration.
3. Create external `freeze.json` with `model`, `labels`, `config`, and `pins`. `model` and `labels` identify the verified existing float ONNX model and label CSV. `config` is `{"event_threshold":0.25,"event_release_ratio":1.0,"event_silence_peak":0.00001}`. `pins` maps every input path to its SHA256, including normalized waveform, model, label CSV, backend detector/metrics/package source and harness. Paths may be absolute or relative to the run directory. Resolve paths for the current machine and freeze hashes before inference. Record backend commit, normalization tool identity and rain mapping (indices 286, 289, 290, 291) externally.

## Commands

```sh
python -I -B test_protocol.py --repo-root /path/to/backend
python -I -B run_once.py --help
# Only after exclusive compute capacity is available and execution is authorized:
timeout --signal=TERM --kill-after=5s 130s \
  python -I -B run_once.py --repo-root /path/to/backend \
  --run-dir /path/to/external/run --execute
```

The parent and isolated child receive explicit paths; no fixed checkout location is assumed. An existing prediction or execution receipt prevents rerun. Inputs must be independently prepared and hashes reviewed; this harness is not a dataset downloader or model installer.

## Execution and interpretation

Unchanged production path: `detect_events → category → postprocess`, two threads, original five-second duration. No fake ASR results or full Analyzer construction. Limits: two available CPUs, child 3 GiB virtual-address cap, 120-second CPU/wall guard, 2 GiB sampled combined RSS stop at 20 ms intervals. Sampling is not an instantaneous memory guarantee. No retries, tuning or cap increases. Child model scope returns, garbage collection runs, then process exit/reaping releases process resources; this is not native allocator instrumentation.

Externally score only known-positive clip presence using backend `presence_metrics(records, predictions, labels, closed_set=False)`. Positive-only precision/F1 must be null, other targets unknown. A hit is one exposed clip's presence result—not general accuracy, temporal accuracy, HTTP, Worker, ASR, diarization or full-pipeline qualification. Outputs are window support, not acoustic onset gold.

`test_protocol.py` is model-free and uses synthetic contract data only. For broader model-free coverage, run the backend's `tests/test_events_detector.py` and `tests/test_event_evaluation_coverage.py` with an existing pytest environment. Do not run inference as part of source restoration validation.
