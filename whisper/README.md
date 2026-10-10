# Whisper.cpp turbo: bounded Chinese text diagnostic

This is a source/docs/counts-only extension to the evaluation snapshot. Backend
code, configuration and production defaults are unchanged. It is not a new
recommended default or a Windows/Vulkan qualification.

## Two distinct observations

1. A single **greedy** feasibility call used beam1/best1 on a 20-second window.
   It returned full native JSON in 162.25 seconds on CPU. No CER was assigned:
   that window cuts across reference turns.
2. A separately frozen **beam5/best5** diagnostic ran 11 fresh native processes
   over the first two complete excerpts from the existing prospective cohort:
   **189.975 seconds and 1,224 reference characters**. Temperature fallback and
   history were disabled. This is reused support, not a fresh holdout.

The support and beam5 policy were fixed before examining its text quality.
They differ from both the greedy smoke and the original adapter's temperature-
fallback defaults. No output was selected from retries or alternative decodes.

## Audited lexical counts

Lower CER is better. These are whole-excerpt errors, with both complete
references retained; they are not per-window or speaker-attribution errors.

| Arm on the same two excerpts | S | D | I | Errors / reference | CER |
|---|---:|---:|---:|---:|---:|
| SenseVoice first pass |47|429|7|483 /1224|39.46%|
| Qwen first pass |149|332|134|615 /1224|50.25%|
| Qwen with bounded recovery |76|382|5|463 /1224|37.83%|
| Whisper turbo beam5, no fallback |93|451|6|550 /1224|44.93%|

Whisper's CER was worse than SenseVoice and recovered Qwen on each of these
two excerpts. This limited diagnostic supplies no basis for promotion or a
universal model ranking. Baseline outputs were reused unchanged; they were not
rerun during the Whisper phase. Exact per-excerpt counts are in results.json.

## CPU cost and timestamp limits

All 11 calls completed. Summed native-process call wall time was 1820.73 seconds
(**9.58 times audio duration**). The complete bounded phase including
verification/scoring took 1863.72 seconds (**9.81 times audio duration**).
Peak owned sampled RSS was 848,748,544 bytes (about809.4 MiB).
These are fresh-process Linux CPU measurements, not steady-state throughput,
Windows results, GPU memory results or a Vulkan benchmark.

Native counters reported 22 encoder runs across 11 automatic-language calls.
The logs do not time language detection separately or attribute individual
encoder runs to it. Do not subtract one encoder run or model load time to claim
warm-model performance.

Full native JSON contained 57 zero-duration lexical token intervals. The final
3.355-second window contained three flagged segment/token entries reaching
3.360 seconds, 5 ms beyond its support. Those fields and all text were preserved.
Timing validity is separate from timing accuracy; no word-alignment or speaker-
identity claim is made. Successful process exit also does not prove absence of
native decoder truncation.

## Contents and checks

- selection.json: public corpus members, exact support and hashes; no audio/text
- pins.json: official source/model hashes and historical CPU build metadata
- results.json: aggregate/per-excerpt counts, policy, costs and limitations
- PROTOCOL.md: selection, failure and lexical-scoring contracts
- score.py: standalone model-free lexical scorer; no model execution
- test_snapshot.py: synthetic and metadata checks
- ATTRIBUTION.md: dataset, model and source licensing scope

Run `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s whisper -p 'test_*.py' -v`
from the snapshot root. Audio, annotations, references, predictions, model files,
native binaries and private runtime receipts are intentionally absent. Counts
alone cannot independently reproduce an ASR run; obtain permitted source data
and assets separately and follow their licenses.
