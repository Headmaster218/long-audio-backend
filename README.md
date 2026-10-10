# AliMeeting staged56: public ASR evaluation snapshot

This branch is an evaluation snapshot/tool, **not an installable replacement for
long-audio-backend**. This evaluation-only snapshot branch has the pinned public
backend commit below as its single parent, but its tree contains only these 14
evaluation files. It is not a backend merge target: do not merge this snapshot
over the backend. Existing backend branches, their exact historical/current proof
gates, defaults, API, and 3.4 MB source budget remain unchanged. Use the pinned
backend as a separate, clean checkout when reproducing. No production runtime
retest or quality acceptance is implied by these evaluation-tool tests.

## Result (2026-10-10)

On 56 complete, annotation-nonoverlapping Mandarin utterances from the official
AliMeeting evaluation archive, Qwen3-ASR-0.6B Q5_K_M had 129/1578 character errors
(8.17% CER), compared with SenseVoice int8's 191/1578 (12.10%).

| Stratum | Utterances | Reference characters | SenseVoice errors/CER | Qwen errors/CER |
|---|---:|---:|---:|---:|
| Quiet | 26 | 635 | 78 / 12.28% | 37 / 5.83% |
| Ordinary | 30 | 943 | 113 / 11.98% | 92 / 9.76% |
| Overall | 56 | 1578 | 191 / 12.10% | 129 / 8.17% |

Qwen improved 6 meetings, tied 1, and regressed on 1; paired utterance outcomes
were 26 wins, 22 ties, 8 losses. One meeting accounts for 38 of the 62 net fewer
errors. There were zero failure, truncation, or repetition-suspicion flags in
112 recognizer calls. The original independently reviewed run verified all call
identities, frame bounds, result-record hashes, provenance and cleanup. Its actual
model-execution phase was 318.15 seconds; peak observable owned/project RSS was
1.406 GB. These are Linux CPU measurements with two recognizer threads, not
portable speed/memory promises, Windows/Vulkan results, or whole-day validation.

The predeclared advance gate passed: quiet relative reduction >=15% and absolute
reduction >=2 percentage points; no overall regression; improvement in >=6/8
meetings; successful untruncated calls and no Qwen repetition flags. This permits
only a larger English, overlap and end-to-end evaluation. The default is unchanged.

### What this does and does not establish

- 315.09 seconds total; 8 meetings overall, only 7 with selected quiet utterances.
  The target was 64; the fixed eligibility rules yielded 56 without relaxation.
- Prospective freeze, **not a proven unseen holdout**: historical benchmark
  membership was unavailable.
- Gold utterance/speaker boundaries are an oracle. This is direct recognizer
  quality, not segmentation, diarization, identity or end-to-end accuracy.
- Quiet means the lowest within-meeting RMS quartile; it does not prove distance.
  Annotation nonoverlap does not establish absence of audible cross-talk.
- Both arms were unhinted: SenseVoice `language='auto'`; Qwen `language=None`.
  The largest quiet gain involved SenseVoice emitting Korean for Chinese audio.
  Language identification may therefore contribute to the measured gain. This is
  not asymmetric language hinting. A forced-Chinese rerun would be a separate,
  post-hoc oracle-language diagnostic, not a replacement for this comparison.
- No broad superiority, English, overlap, Windows, GPU/Vulkan or whole-day claim.
- No raw audio, reference text, predictions, model bytes, credentials, private
  material, or controller approval/process records are published here. Public
  session IDs, native boundaries, hashes and error counts are retained. These
  metadata cannot independently prove the original execution audit. Reproduction
  must regenerate the data and predictions from official assets.

## Files and provenance

- `selection.json`: 56 ordered public selections, native boundaries, frame support,
  exact RMS inputs/ranks, reference hashes and PCM hashes; no text/audio.
- `results.json`: original error counts by utterance/meeting/stratum and gate.
- `assets.json`: official archive/model URLs, exact byte/hash pins and native files.
- `runtime.json`: original runtime versions, relevant wrapper/native hashes,
  wheel hashes, and exact recognizer configuration. Native commit's embedded
  short ID matches the pinned source; this is not reproducible-build attestation.
- `backend-source.json`: required backend commit
  `b291776174e0a8d4f74d8ca988931c7a7cba1956`, tree
  `091a197f2da2edf3f14b75d4100f6b7bc624c275`, and reused contracts.
- `prepare.py`, `recognize.py`, `score.py`: portable opt-in reproduction.
- `PROTOCOL.md`, `ATTRIBUTION.md`: selection and licensing details.

The original freeze SHA256 was
`75b2866078f4802b6030f1a4fac4c9f46667ee11517b03d6cc9df8572b1a7e77`;
execution configuration SHA256 was
`726582f10ec8a61fd915307427a54a7c60cf3b6c2266b4bee9735c754e02009b`.
These identify original local evidence, which is not bundled or required by the
portable scripts. The portable scripts are a new adaptation of the original
selection/call/scoring contracts, tested with synthetic data, **not rerun with
real models as part of this publication preparation**.

## Explicit reproduction

No command in this branch automatically downloads or installs anything. Running
synthetic tests does not access data or import recognizers. Allocate separate
storage outside both Git checkouts; acquisition needs several GB. Read licenses
first. You are responsible for download, extraction, disk/CPU/memory limits and
stopping an overlong run. The portable helper is not the original resource
supervisor; use an external timeout/resource supervisor on your own machine.

1. Clone [the backend](https://github.com/Headmaster218/long-audio-backend) into
   `$BACKEND` and checkout the exact commit above, with a clean worktree. Set
   `PYTHONDONTWRITEBYTECODE=1` for the commands below so imports leave it clean.
2. Obtain `Eval_Ali.tar.gz` from the publisher URL in `assets.json`. Verify
   SHA256 and byte size against that file **before extraction**. Extract only
   regular files under `Eval_Ali/Eval_Ali_far/{audio_dir,textgrid_dir}` into
   `$NATIVE`, rejecting absolute/traversal paths, links and duplicate members.
   `prepare.py` also verifies every native file before selection. Extraction is
   manual, outside the synthetic checks; no archive extractor is bundled.
3. Create a Python 3.12 environment for each arm. Explicitly install the versions
   listed in `runtime.json` from their official distributions. The Qwen native
   wheels must match its listed hashes. Upstream source is
   [transcribe.cpp at 63a44d9](https://github.com/handy-computer/transcribe.cpp/tree/63a44d9239d610b3908e8a66b384924cd4a77217).
   These pinned original versions are required; fail rather than silently upgrade.
   Rebuilding on another platform is a new runtime and needs separate validation.
4. Obtain the Qwen model and SenseVoice archive from `assets.json`; verify exact
   sizes/hashes and extracted model/tokens before use. Qwen weights are Apache-2.0;
   SenseVoice weights have a separate custom FunASR model license. No license is
   accepted on your behalf by the scripts.
5. From this evaluation checkout, with `$WORK` outside Git:

```sh
export PYTHONDONTWRITEBYTECODE=1
python prepare.py --native "$NATIVE" --output "$WORK/crops"
# Run with the separately pinned SenseVoice environment's Python:
python recognize.py sensevoice --backend "$BACKEND" --crops "$WORK/crops" \
  --model "$SENSEVOICE_MODEL" --tokens "$SENSEVOICE_TOKENS" \
  --output "$WORK/sensevoice.jsonl" --i-understand-model-execution
# Then run with the separately pinned Qwen environment's Python:
python recognize.py qwen --backend "$BACKEND" --crops "$WORK/crops" \
  --model "$QWEN_MODEL" --output "$WORK/qwen.jsonl" --i-understand-model-execution
python score.py --backend "$BACKEND" --references "$WORK/crops/references.json" \
  --predictions "$WORK/sensevoice.jsonl" "$WORK/qwen.jsonl" --output "$WORK/scores.json"
```

Never commit generated outputs. Each command refuses existing output paths.
Missing prediction rows are scored as failures with empty hypotheses, retaining
all reference characters. Failed calls are not discarded. A load/startup failure
stops the run; retain its error and score any available output rather than calling
it a completed experiment. No retry is automatic. Review differences from the
historical counts, including runtime and language-ID variation, before drawing
conclusions. The original aggregate resource/provenance gate is not recreated by
this simplified helper.

## Offline checks

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

These synthetic tests check parser/boundary/selection/metric/failure contracts and
metadata consistency. They do not test models, audio quality or production runtime.
