# Explicit post-primary multi-voice candidates

The strict primary pipeline remains unchanged. This optional local command uses
MOSS-TD Handy Q8 to recognize multiple model-local voices directly from selected
original audio. It does **not** produce separated audio waveforms, repair or
replace the baseline, establish speaker identity, or prove that both speakers
were recognized. Recognition quality needs human-reference comparison.

## Run after an actual completed primary export

Use an isolated optional runtime with the same installed backend package and the
fixed runtime/model in [MOSS_HANDY_EXPERIMENTAL.md](MOSS_HANDY_EXPERIMENTAL.md).
Do not add these packages to an already qualified strict runtime. No download or
installation happens in this command. The fixed controller still verifies its
exact model and runtime before invocation.

The baseline export must contain `recorder-export-source-content-v1`, binding
exact validated WAV/PCM/metadata delivery hashes, input history, remote page
receipts, configured target, producer/run coordinates and closure at export time.
Old exports are preserved but refused by this command. When the original service
and source remain available, explicitly create a new GET-only primary export to
a new directory with the updated exporter. This does not replay inference. The binding includes recorded source paths/stat
identities, so copying or relocating even byte-identical inputs can also require
a fresh GET-only export.

Select at most eight ordered, non-overlapping original span-local ranges. Each
range is nonempty and at most 320,000 frames (20 seconds at mono 16 kHz PCM16).
A private mode-0600 JSON file can contain, for example:

```json
[{"start_frame": 160000, "end_frame": 352000}]
```

The selection is explicit, not an automatic overlap detector. Human-annotation
selected evaluation ranges are oracle-conditioned tests, not detection evidence.
Unselected audio is not processed. A range may cross primary 10-second chunks
or original-file seams within the finalized span; all original frame mappings
are retained. No missing frames are invented or padded.

```sh
/absolute/optional-runtime/bin/python -I -B -m audio_backend.recorder_overlap_candidates \
  --job-dir /absolute/bundle/generation-00/job \
  --export-dir /absolute/bundle/generation-00/export/attempt-01 \
  --ranges /absolute/private/ranges.json \
  --model /absolute/models/MOSS-Transcribe-Diarize-Q8_0.gguf \
  --output-dir /absolute/private/new-overlap-attempt \
  --exclusive-inputs --aggregate-budget-managed --deadline-seconds 1200
```

`--exclusive-inputs` acknowledges that the finalized job, original deliveries and
export will not be changed during this command. Existing intake/uploader locks
are held; originals, journal and export hashes are checked before and after each
call. This is historical completed-export/source validation, not a live-service
or native-owned-cache authorization. No HTTP requests, primary job mutations,
service shutdown, primary retries or strict-profile changes are made.

`--aggregate-budget-managed` is an operator acknowledgement, not a resource
measurement. Keep total primary plus optional service memory under 10 GiB and
aim for 6 GiB. The unchanged optional controller caps its own caller plus owned
processes at 3.5 GiB, with a 2 GiB host reserve and 5.5 GiB disk reserve. It cannot
account for an unrelated primary service. An idle primary can remain resident
only when the operator separately observes and limits the combined process
trees. Stopping it before this optional pass is the simplest isolation choice,
but the command does not require or claim to have stopped it.

Calls are serial, at most one per declared range. Each uses the unchanged
300-second controller compute bound and finite cleanup. The default cooperative
whole-command deadline is 1,200 seconds (explicit maximum 3,600); slow filesystem
calls and cleanup can extend wall time. The command never increases the existing
controller memory cap. Missing resources or failed/uncertain cleanup stop the
remaining plan instead of allowing another model call.

## Outputs and failure semantics

The output must be a fresh private sibling outside input storage and source-code
repositories. Existing output, including an interrupted attempt, is refused.
Keep the whole directory for investigation; never delete a claim to force retry.

- `plan.json` binds selected ranges, baseline artifacts and finalized originals.
- `call-NN.intent.json` precedes dispatch and conservatively means a call may have
  occurred. It never grants resume/replay permission.
- `call-NN.status.json` retains the controller's outcome and cleanup evidence.
- `call-NN.raw.txt`, when available after cleanup/source validation, is exact raw
  model text. Empty output remains an explicit empty result, not silence proof.
- `candidate-NN.json` retains the diagnostic projection and all raw local-label
  tracks, including empty tracks and text with rejected model timestamps.
  Exact per-label raw spans (including the unassigned prefix) also reconstruct
  every original character, including ambiguous timestamp markup. Cleaned lexical
  tracks are only a best-effort projection. Parse errors remain explicit; no reference-dependent pruning is performed.
- `complete.json` means the declared candidate passes were recorded, not correct
  recognition or complete content. It reports missing/unparsed candidate count.
- `failure.json` records an interrupted/refused remainder. An intent without a
  terminal result remains uncertain; the command does not adopt or replay it.

The controller's `SourceSupport` is used as a caller-declared original-slice
container. Its core bounds/hash refer to selected original PCM, not a native
analysis chunk. Its runtime hash identifies the fixed MOSS runtime profile only, not a primary native runtime. The
explicit `original-pcm-slice-v1` binding denies native-owned-capture authority.
Model-generated timestamps are unverified, labels are local to one support,
and EOS/generated-token completeness remain unknown. Keep the original primary
transcript alongside these candidates, including disagreements and failures.

### Exact digital-zero dispatch guard (prospective)

After verifying the completed export and selected original PCM, a selected slice
whose PCM16LE bytes are **all zero** is recorded as `skipped_exact_zero_pcm` in
`skipped-NN.json`. No controller is constructed, no model call is made, and no
voice/text candidate is invented. The receipt binds the selected range, frame
count and PCM hash. Other selected ranges retain the existing model path. Even
one nonzero sample (including amplitude 1) is not skipped. This is not a VAD,
noise threshold, or a claim about natural silence, quiet speech or ambient noise.

`candidate_count` still counts all selected range records; `model_call_count`
and `exact_zero_ranges_skipped` distinguish actual dispatches and these explicit
no-call records. An exact-zero skip is not a model success, recognition-quality
pass, or speaker-coverage result. All confidence/identity claims remain false.
Primary exports are never overwritten. Existing output directories are refused.

The earlier frozen digital-zero evaluation produced unrelated refusal text and
remains a failed qualification with its raw output and score preserved. This
prospective deterministic guard does not reinterpret or repair that measurement,
and no new model-quality panel has been run for this change.

### Automatic intervals from bound native turns (opt-in)

For a new completed-span export, add `--native-turns` to
`python -I -B -m audio_backend.recorder_incremental_span_export` with the usual
job/API/session/capture/output/exclusive-writer arguments. This makes bounded,
GET-only per-work-chunk reads and writes an additional `native-turns.json`
artifact committed by `complete.json`. Each read verifies the actual canonical
source, original source-range coverage, completed accepted cores, retained
packet or packetless witness, and exact persisted turn partition. The exporter
also compares each canonical WAV hash reconstructed from local originals and
rereads every evidence response before publishing. Native-turn evidence has its
own content digest; an ASR transcript revision does not bind these turns.
Unsupported or missing evidence refuses the export. Do not treat absence as
silence or zero overlap. Existing exports remain unchanged; obtain a fresh
GET-only export when the completed service/source is available.

Use `--auto-native-overlap` instead of `--ranges FILE` on the candidate command.
It requires this completed bound export and rechecks the local source hashes.
No gold annotations or ASR text choose intervals. The existing model-free
intersection routine considers distinct native local labels only within one
accepted core/window. Positive intersections are padded by one second, clipped
to the accepted core, and overlapping/touching time supports are merged. This
merges time supports only, never speakers or output tracks. Supports are split
at 20 seconds and at most the first eight, in chronological order, are passed
to the unchanged serial model controller. The shared invocation deadline and
resource acknowledgments remain required.

`selection.json` preserves detected regions, their chunk/core proof association,
detected union frames, selected detected frames, support frames and omitted
support-range count. These are detector coverage statistics, not human-reference
recall or recognition quality. False negatives, false positives, fragmented
labels and natural-noise hallucination remain possible. No person identity or
full-recording coverage is asserted. Fully validated evidence with no positive
intersection produces `no_detected_native_overlap`, zero calls, and an explicit
selection receipt; missing or refused evidence never produces that outcome.
Candidate receipts retain original-slice provenance, not native-owned-core
capture authority. Primary transcripts are never overwritten.

This source change has model-free tests only. Previous fixed-interval evaluation
and its digital-zero failure remain unchanged; no new automatic-selection
recognition result or new runtime qualification is implied.
