# Attribution and licenses

## AliMeeting-derived metadata

AliMeeting (SLR119), provided by Alibaba Group.
Publisher: https://www.openslr.org/119/
Official archive: https://speech-lab-share-data.oss-cn-shanghai.aliyuncs.com/AliMeeting/openlr/Eval_Ali.tar.gz
Publisher-listed license: Creative Commons Attribution-ShareAlike 4.0 International.
License: https://creativecommons.org/licenses/by-sa/4.0/

Cite Fan Yu et al., “M2MeT: The ICASSP 2022 Multi-Channel Multi-Party Meeting
Transcription Challenge,” Proc. ICASSP, IEEE, 2022; and Fan Yu et al., “Summary On
The ICASSP 2022 Multi-Channel Multi-Party Meeting Transcription Grand Challenge,”
Proc. ICASSP, IEEE, 2022.

Changes: selected complete utterances from the first array channel; outward
frame rounding at original amplitude, with no gain, denoising, separation,
resampling or inferred speakers. Published selection/derived result metadata
omit audio and annotation/prediction text. AliMeeting-derived metadata in
`selection.json` and `results.json` are provided with this CC BY-SA 4.0 attribution
and license. Locally regenerated crops/reference annotations retain that license.

## Models and runtime, not bundled

Qwen3-ASR-0.6B weights: Apache-2.0. Model source:
https://huggingface.co/Qwen/Qwen3-ASR-0.6B
GGUF conversion: https://huggingface.co/handy-computer/Qwen3-ASR-0.6B-gguf
Exact conversion/model bytes are pinned in `assets.json`.

SenseVoice weights: FunASR Model Open Source License Agreement v1.1 (custom),
as recorded by the qualified backend. Read:
https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE
License bytes pinned by backend: SHA256
`7dba975a2069691db4992b0592d70828b330d2f8a30a71450f4e152a554e84f8`.
Do not infer that the SenseVoice weights are Apache-2.0 from the Qwen license.

transcribe.cpp: https://github.com/handy-computer/transcribe.cpp
sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx
Their upstream code and distribution licenses apply to separately acquired assets.

## Project evaluation code

The Python helpers are project evaluation code adapted from this project's
selection/scoring harness and reuse the separately pinned backend contracts.
The dataset's CC BY-SA statement above applies to derived metadata, not a blanket
relicensing of project code, model weights or runtime dependencies. This snapshot
does not grant new rights to third-party code or assets. Consult each upstream
license before acquisition or redistribution.

No additional project-code license is granted by this snapshot. Consult the
repository rights holder for code reuse permissions where no explicit license
is otherwise provided.
