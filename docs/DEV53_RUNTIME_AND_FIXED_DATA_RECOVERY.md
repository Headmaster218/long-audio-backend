# dev53 runtime and fixed data recovery

As of 2026-10-19, pinned runtime restoration and bounded native-load qualification have independent terminal acceptance. The official evaluation reference is recovered; fixed audio remains blocked after two HTTP 500 responses.

## Source boundary and conclusion

The source baseline is commit `087c204786b1efd1a5aced16ce8f2babb7af93a2`, tree `830381443463c15ed89ae3243fd775f124495ed2`: 564 files and 10,187,861 bytes. This report is the sole proposed addition; prior files remain unchanged.

The fresh runtime restores pinned dev53 application, dependency, model, and native artifacts. Accepted checks establish installation integrity, passive application startup, and selected native library loading without model construction or inference. They do not restore the lost historical runtime or raw trial evidence, establish acoustic or speaker-identity parity, or qualify the full ASR pipeline.

## Restored artifacts

- Application: `long-audio-backend==0.2.0rc3.dev53`; wheel 830,862 bytes, SHA-256 `4af80cdf5058b66b173a7b58a8401c1015cfe516ba5cc78b200c32d37853270a`. All 133 installed application payload files match.
- Official dependency wheels: 27 verified, comprising 1,585 installed payload files; 28 distributions including the application. Installation and `pip check` succeeded.
- Independent rehash: all 1,950 regular files and 15 symlinks in the restoration inventory; all eight attestor artifacts; all 117 NeMo archive entries; all 36 pinned transcribe-cpp members; 75 license/notice files.
- Seven model and auxiliary files total 1,465,002,780 bytes. Their manifest pins were verified; no model was constructed. The restored publisher Handy Q8 model SHA-256 `64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039` differs from legacy conversion `551c0a52fb0b75cb6fd85e2247554dfff84fbcd8435305ac83d06ec0c043162f`, which remains in the legacy manifest allowlist.
- Adjacent transcribe runtime/member manifest SHA-256: `f4c92fe1ae5bd79950744e9cec1fd3b8cd4c04a6b8351eafbdaa63870d6c8a`.
- NeMo CPU archive: `nemo-speech-0.2.0-linux-x86_64-cpu.tar.gz`, 6,683,477 bytes, SHA-256 `f396057150f1b774935c7414fd32ebc04195a795a34120d78d8c9ecefa1b7507`, from the [official v0.2.0 release](https://github.com/NVIDIA/NeMo-Speech.cpp/releases/tag/v0.2.0).

Dependencies match retained `requirements-runtime-dev9.lock.txt` and `requirements-moss-td-cpp.txt`; Python 3.12.14, transcribe-cpp/native 0.2.3, Sherpa 1.13.8, NumPy 2.5.3.

A Uvicorn metadata timeout was recovered through official PyPI Simple metadata and the exact official wheel; the failure remains recorded. Matching FFmpeg/FFprobe binaries and version pins do not establish their host-package provenance or complete dynamic dependency closure.

## Accepted bounded qualification

The first four-stage gate remains `failed_or_incomplete`: passive startup, Handy, and Sherpa passed; NeMo failed because the helper selected a C++ library rather than the C-ABI library. Only that selector was corrected to `libnemo_speech_asr_c.so`. One subsequent NeMo-only stage passed. No passed stage was repeated and the original gate is not relabeled as a pass.

- Passive application startup matched the existing SQLite profile and returned HTTP 200 for health, OpenAPI, and documentation. The worker never started; normal lifespan cleanup completed.
- Handy loaded transcribe-cpp-native 0.2.3, reporting commit `63a44d9` and matching the pinned runtime members.
- Sherpa's installed-runtime attestation and pre-extractor artifact-signature checks passed; no extractor was constructed.
- Corrected NeMo loading reported version 0.2.0 and C ABI `nemo-speech-asr 0.2.0`; model creation was not called.
- All original subprocess handles were positively waited and reaped, no observed owned survivors remained, and all 1,965 inventoried identities plus source/package pins remained unchanged.

Across the original gate and corrected NeMo-only run, accounting including helper reserves was 4.997252278 CPU seconds and a maximum 94,257,152 bytes RSS; combined work wall time was 3.304947117 seconds. Final output allocation for the two qualification directories was 2,379,776 bytes, excluding the separate audit receipt. These sampled measurements and conservative helper allowances are not a complete instantaneous process-tree census or hard peak guarantee.

Restoration installation separately sampled a peak 91,000,832-byte summed RSS. Disk checkpoints recorded 2,025,467,904 allocated bytes and minimum observed free space of 29,803,028,480 bytes. Disk was not sampled continuously. These separate phase measurements do not establish inference performance.

## Fixed public data identity

The retained target is VoxConverse development member `bauzd.wav`. Historical metadata reports 500.28 seconds, 8,004,480 frames, mono PCM16 at 16,000 Hz. The old audio, archive, and reference hashes were lost. These dimensions do not establish byte identity and remain unverified against fresh audio.

The [official development audio archive](https://www.robots.ox.ac.uk/~vgg/data/voxconverse/data/voxconverse_dev_wav.zip) is the source. The first bounded suffix GET began at 2026-10-18 23:53:46 UTC and returned HTTP 500 with server date 23:53:52 UTC; zero body bytes were read. A bounded metadata pass found no verified alternate official distribution; the project page could not be retrieved, so this does not prove that no alternative exists. The sole authorized later suffix GET began at 2026-10-19 00:26:05 UTC and ended at 00:26:11 UTC with HTTP 500 and zero archive-body bytes read. Both requested `bytes=-131072`. The later attempt retained a 128 MiB transfer/staging ceiling and 256 MiB address-space cap. No further request occurred. No audio, exact archive-member listing, CRC, fresh WAV geometry, or audio/PCM hash was recovered; the audio task remains blocked.

The official evaluation reference was recovered with fresh pins:

- Repository: `joonson/voxconverse`, commit `24bf60be297701cd7e4ef18550c6d390c1b87365`.
- File: [dev/bauzd.rttm at that revision](https://github.com/joonson/voxconverse/blob/24bf60be297701cd7e4ef18550c6d390c1b87365/dev/bauzd.rttm), 5,124 bytes.
- Git blob SHA-1: `e156b1a7d4e52440f34aed7d0d475ce34f680bc5`.
- SHA-256: `b219fe6eb11f660f4fe3d3b4e91a3e886618eac06752f5fa07135cd30c3cd88c`.
- Pinned README SHA-256: `d7479cf72d990eaad9340fd62057585e30ccbde87ad1dc198c970a3c7575f0fb`; it describes annotation v0.3 and links prior v0.2. The historical annotation revision is unknown; fresh reference equality to the old trial is unproven.
- The [pinned README](https://github.com/joonson/voxconverse/blob/24bf60be297701cd7e4ef18550c6d390c1b87365/README.md) states research-purpose availability under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0), with copyright retained by original video owners. Dataset attribution: Chung, Huh, Nagrani, Afouras, and Zisserman, *Spot the conversation: speaker diarisation in the wild*, Interspeech 2020.

Reference labels are evaluation-only and must not drive crops, product thresholds, or speaker links. It makes no accuracy, false-merge, speaker-link, historical-parity, or full-suite claim.

## Evidence pins

SHA-256 digests identify retained local evidence; the evidence files themselves are outside this source-only addition.

- Restoration receipt: `1780d2a60d9fc068a9cb28a43aed2058863992c2a2cf8a12ec089956d4a7a287`.
- Final artifact inventory: `6c30ab9e0d524e5afc81ca87395f6d80d6021f03e9af5bad2d8eba9e45d41900`.
- Original failed four-stage gate: `9252e68224f70997e2854ea4ab04b1da7c37041bda7676cc96fe64df4700df67`.
- Corrected NeMo-only gate: `cb3954106605547a54ad4669a90d95b1387a6cafa5508949badb76a2c6921a77`.
- Initial fixed-data receipt: `20de57b66a03ecbab023de1684f28029deae73627907dd57df86437b499a259a`.
- Official-route metadata pass: `84f9e7019e49c14f078803630f833463ee6a6623d9f27f8bfaeee729da6bf943`.
- Independent runtime terminal audit: `8f53827b18aba829d8f763f3096fd9dcda3839d9b2ed8f6e776394fccc79ae4a`.
- Later official audio attempt: `0b1ad5d3c73625ca656ae0db2b80fad1a96e21277ed693ef2e757050c9e69809`.
