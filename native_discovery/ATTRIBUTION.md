# Attribution and license scope

AliMeeting (SLR119), provided by Alibaba Group.
Publisher: https://www.openslr.org/119/
Official archive: https://speech-lab-share-data.oss-cn-shanghai.aliyuncs.com/AliMeeting/openlr/Eval_Ali.tar.gz
Publisher-listed corpus license: Creative Commons Attribution-ShareAlike 4.0 International.
License: https://creativecommons.org/licenses/by-sa/4.0/

Cite Fan Yu et al., “M2MeT: The ICASSP 2022 Multi-Channel Multi-Party Meeting
Transcription Challenge,” Proc. ICASSP, IEEE, 2022; and Fan Yu et al., “Summary On
The ICASSP 2022 Multi-Channel Multi-Party Meeting Transcription Grand Challenge,”
Proc. ICASSP, IEEE, 2022.

Derived changes: native overlap discovery measurements and sample-count
summaries on eight previously selected complete mixed inputs, trigger/EOF/cache
availability metadata, and fixed-denominator cached recognition error counts.
No corpus audio, raw reference/predicted timeline, annotation text or generated
transcript is redistributed. AliMeeting-derived results metadata retain
CC BY-SA 4.0 attribution and licensing. These source/license links and adaptation
description should accompany redistributed derived metadata.

Native model: NVIDIA Nemotron-3-Diarization,
https://huggingface.co/nvidia/Nemotron-3-Diarization
License: OpenMDW-1.1,
https://raw.githubusercontent.com/OpenMDW/OpenMDW/main/1.1/LICENSE.OpenMDW-1.1
Notice retained in `LICENSES/NATIVE-MODEL-OpenMDW-1.1.txt`.

Native runtime: NVIDIA NeMo-Speech.cpp 0.2.0,
https://github.com/NVIDIA/NeMo-Speech.cpp/releases/tag/v0.2.0
Apache-2.0 runtime license, NOTICE and bundled third-party notices are preserved
under `LICENSES/`. Separately obtained runtime dependencies retain their own terms.

Historical MOSS model/runtime provenance and licenses are recorded in the parent
`overlap/` evidence: OpenMOSS-Team MOSS-Transcribe-Diarize (Apache-2.0) and
transcribe.cpp (MIT). This phase made no new MOSS calls and bundles no weights,
model/runtime binaries or third-party source archives.

Dataset licensing does not relicense models, runtimes, or project code.
No new project-code license is granted by this evidence snapshot.
