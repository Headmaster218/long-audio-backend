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

Derived changes here: deterministic selection of eight complete overlap-rich
inputs from the first array channel, annotation-derived timing and speaker-count
metadata, 33 eligible candidate pairs, 72 explicit support exclusions, input and
annotation digests, and aggregate/per-input/per-reference-track error counts.
Original amplitude and channel samples were preserved, with no gain, denoising,
separation, resampling, or inferred reference speakers. No corpus audio,
annotation text, or generated transcript is redistributed.

AliMeeting-derived metadata in `selection.json` and `results.json` retain
CC BY-SA 4.0 attribution and licensing. Regenerated corpus crops and annotations
remain subject to the corpus license. Source and license links should accompany
redistributed derived metadata; the changes above identify this adaptation.

MOSS-Transcribe-Diarize model: OpenMOSS-Team, Apache-2.0; model and source
revision digests are in `provenance.json`. The model license notice is retained
in `LICENSES/MODEL-APACHE-2.0.txt`.

transcribe.cpp runtime: MIT, https://github.com/handy-computer/transcribe.cpp
The runtime license notice is retained in `LICENSES/RUNTIME-MIT.txt`.
No weights, native runtime binaries, or third-party source archives are bundled.

Dataset licensing does not relicense the models, runtimes, or project code.
No new project-code license is granted by this evidence snapshot.
