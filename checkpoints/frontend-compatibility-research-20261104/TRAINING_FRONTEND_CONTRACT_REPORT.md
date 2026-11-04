# ERes2Net base frontend contract evidence audit

Checked 2026-11-04 UTC. Scope: read-only source, paper and model-metadata research before a fixed-version compatibility experiment. No inference, feature computation, dependency installation, checkpoint download, threshold tuning or product-source modification was performed.

## Decision

**The 8000 Hz / snip-edges-true candidate has a real historical training-recipe rationale, not only a Sherpa test-script rationale. But the exact installed weights cannot presently be bound to that training revision or its dependency environment.** It must remain a named research candidate, not “the proven training frontend,” “the corrected production frontend,” or “the historical official-test environment.”

A pinned July 2023 3D-Speaker revision contains both a 512-D ERes2Net training recipe and the base-3D-Speaker ModelScope v1.0.1 inference-registry entry. Its training feature class leaves high frequency and edge handling to torchaudio defaults. A separately pinned torchaudio 0.10.1 implementation resolves those options to high_freq=0 (8000 Hz at 16 kHz) and snip_edges=True. That is **source-level option consistency under an explicit dependency resolution**, not evidence that the released checkpoint was trained in that resolved environment.

Production remains the audited native frontend: high_freq=-400 (7600 Hz), snip_edges=False. This audit neither establishes that it is wrong nor establishes numerical equivalence or accuracy for either choice.

## Evidence matrix

| Evidence | Directly established | Limit |
|---|---|---|
| Installed model bytes, freshly hashed and metadata-scanned | SHA-256 1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b; 39,593,761 bytes; framework=3d-speaker; 512-D; 16 kHz; normalized samples; global-mean features; exact iic base-3dspeaker model-family URL. ONNX producer says pytorch 2.4.1, IR version 7. | Metadata has no checkpoint digest, ModelScope revision, training commit, torchaudio version, feature cutoff or edge setting. Producer version identifies an export declaration, not a training version. |
| Existing native implementation audit | Reported Sherpa 1.13.8 commit 11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf; KNF 1.22.3; inherited native defaults yield 7600 Hz / snip=False; explicit metadata-driven scale and global mean. | Existing source/build audit is reused. Native binary hashes were not remeasured in this task. |
| Historical training chain, 3D-Speaker 71258283ee6b64d1794b4a8636e78f3b276739a0 (2023-07-12) | Actual training run.sh → train.py → WavSVDataset → FBank. Recipe: 512-D, m_channels=32, 80 bins, 16 kHz, mean normalization, 3-second training chunks. Dataset calls FBank without a dither override, so dither=0. | No run receipt or checkpoint manifest binds this source revision to the installed weights. Training chunk size is not an inference-duration requirement. |
| Historical and current inference registry | Historical damo and current iic identifiers select eres2net_base_model.ckpt at ModelScope v1.0.1, with 80-D features, 512-D embedding, m_channels=32. | Registry is a model-selection declaration. It does not hash-bind that checkpoint or name its training revision. |
| Pinned torchaudio 0.10.1 source | fbank defaults high_freq=0 and snip_edges=True, plus 25 ms / 10 ms, low_freq=20, Povey window, preemphasis=0.97. | Historical requirements allow torchaudio >=0.10.1,<1.0, not exactly 0.10.1. No training environment lockfile was obtained. Inspecting one permitted version does not prove the installed training version. |
| Current generic training recipe, 3D-Speaker 065629c313eaf1a01c65c640c46d77e61e9607b4 | Now 192-D, with 80 bins / 16 kHz / mean normalization. Git history explicitly changes the recipe from 512 to 192 on 2024-02-21. | Current generic recipe is not the exact 512-D checkpoint's training configuration. |
| Sherpa exporter at native pinned revision | Loads the checkpoint selected by imported 3D-Speaker supports, exports feature input N×T×80 with dynamic T, opset 13, and adds normalized-samples/global-mean metadata. | It exports the embedding network, not the waveform frontend. run.sh clones 3D-Speaker without a revision and installs KNF/ONNX/ORT without versions. A pinned exporter file does not pin historical export dependencies. |
| Sherpa test script at native pinned revision, combined with KNF 1.22.3 | Explicit snip_edges=True, dither=0, 80 bins, given sample rate; high_freq omitted. KNF 1.22.3 default high_freq=0 resolves to 8000 Hz. | This is a reproducible proposed dependency resolution of test source, not attestation of the dependency version used in a historical official test. Test configuration is not training configuration. |
| GitHub release asset API | Matching asset name and size, asset ID 198893098, created/updated 2024-10-14T03:41:16Z. | API digest=null. No source/checkpoint/environment attestation found. Matching name/size alone is not cryptographic byte identity. No remote weights downloaded. |
| ModelScope model card / configuration | Exact official model destination is known from bytes and registry. | Official summary returned no readable text or tool access errors; configuration.json and README API reads also failed. No card/configuration body is claimed as directly verified. Third-party reproductions were not used as authority. |
| Papers | Architecture paper describes VoxCeleb experiments; 3D-Speaker paper describes corpus and benchmarks. | Neither supplies an installed-ONNX-to-training-revision binding or specifies these two disputed frontend options for these weights. |

## Verified historical training chain

The preferred historical anchor is [3D-Speaker 71258283ee6b64d1794b4a8636e78f3b276739a0](https://github.com/modelscope/3D-Speaker/commit/71258283ee6b64d1794b4a8636e78f3b276739a0). Unlike a current generic README, the same revision supplies:

1. [Training run.sh](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/egs/3dspeaker/sv-eres2net/run.sh): invokes speakerlab/bin/train.py with conf/eres2net.yaml and 3dspeaker/train/train.csv. The stale comment mentioning VoxCeleb is not the operative path.
2. [512-D training YAML](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/egs/3dspeaker/sv-eres2net/conf/eres2net.yaml#L16-L51): 3-second chunks, 16 kHz, 80 bins, mean_nor=True, 512 output dimensions, 32 base channels.
3. [Training entrypoint](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/speakerlab/bin/train.py): builds dataset and classifier. It replaces the YAML's nominal num_classes from the label encoder, accounting for speed perturbation. The YAML's 5994 value therefore does not prove a 5994-speaker training set.
4. [Training dataset](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/speakerlab/dataset/dataset.py): waveform reader → augmentation → feature extractor, with no dither argument supplied.
5. [FBank implementation](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/speakerlab/process/processor.py#L131-L152): explicitly passes num_mel_bins, sample_frequency and dither=0 to torchaudio.compliance.kaldi.fbank, then subtracts each mel-bin's mean over frames. It does not explicitly set high_freq or snip_edges. WavReader takes the first channel, applies optional speed perturbation, randomly crops or zero-pads training inputs.
6. [Model registry](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/speakerlab/bin/infer_sv.py#L72-L118): the exact base-3D-Speaker family uses a 512-D ERes2Net, ModelScope revision v1.0.1 and eres2net_base_model.ckpt. This coexists with the recipe but does not assert it produced that checkpoint.
7. [Requirements](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/requirements.txt): torch>=1.10.1,<2.0 and torchaudio>=0.10.1,<1.0, not exact locks.
8. [torchaudio 0.10.1 fbank at resolved commit 4b64f80bef85bd951ea35048c461c8304e7fc4c4](https://github.com/pytorch/audio/blob/4b64f80bef85bd951ea35048c461c8304e7fc4c4/torchaudio/compliance/kaldi.py#L486-L511): high_freq=0 and snip_edges=True are inherited defaults. The tag-to-commit resolution was read from GitHub.

The same 512-D YAML already exists at [fec3086e0bfcb886e59416945a622646c1e706df](https://github.com/modelscope/3D-Speaker/blob/fec3086e0bfcb886e59416945a622646c1e706df/egs/3dspeaker/sv-eres2net/conf/eres2net.yaml) on 2023-06-30. The [2024-02-21 change](https://github.com/modelscope/3D-Speaker/commit/6e4026245f427d8abbea55a68e4d1a3155224ec9) explicitly changes it to 192 dimensions. The [current pinned YAML](https://github.com/modelscope/3D-Speaker/blob/065629c313eaf1a01c65c640c46d77e61e9607b4/egs/3dspeaker/sv-eres2net/conf/eres2net.yaml) retains 192, while the [current registry](https://github.com/modelscope/3D-Speaker/blob/065629c313eaf1a01c65c640c46d77e61e9607b4/speakerlab/bin/infer_sv.py) still selects the 512-D v1.0.1 released model. This explains why inspecting only current training defaults is inadequate.

A separate official [ModelScope v1.14.0 implementation](https://github.com/modelscope/modelscope/blob/b30834a0d45a46e0209246e2e25a28beda3a4bdb/modelscope/models/audio/sv/ERes2Net.py) likewise computes torchaudio Kaldi fbank with 80 bins and per-bin mean subtraction, inheriting the other options. This is additional inference-code evidence, not independent training attestation.

## Export and release boundary

The [pinned Sherpa exporter](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/export-onnx.py) imports supports from a separate 3D-Speaker checkout. It reads the ModelScope configuration for sample rate and adds a family URL, dimensions, normalized-sample flag and global-mean flag. It does **not** persist the checkpoint hash, source commit or frontend options. The [driver](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/run.sh#L5-L11) pins ModelScope 1.14.0 but not the 3D-Speaker checkout or KNF version.

The [pinned test source](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/test-onnx.py#L72-L91) explicitly requests snip_edges=True; [KNF 1.22.3 mel defaults](https://github.com/csukuangfj/kaldi-native-fbank/blob/v1.22.3/kaldi-native-fbank/csrc/mel-computations.h) supply high_freq=0 when omitted. The test script's comment claims cosine scores are in [0,1], but raw cosine has range [-1,1]; do not adopt that comment as an evaluation constraint.

The [release asset API record](https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/assets/198893098) and [release page](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models) do not close the provenance gap. The locally rechecked hash pins exactly what a proposed experiment would run, but not how its parameters were trained.

To upgrade this to an exact training contract would require a publisher/reproducible chain connecting installed ONNX hash → exported checkpoint hash and immutable model revision → training source/configuration and dependency lock/environment. Matching architectural dimensions, current registry entry, release size or paper benchmark cannot substitute for that chain.

## Paper limits

The [ERes2Net Interspeech 2023 paper, section 4.2](https://www.isca-archive.org/interspeech_2023/chen23o_interspeech.pdf) concerns VoxCeleb training, 192-D embeddings, 80-D features, 25/10 ms framing and instance mean/variance normalization. It does not identify this Chinese 512-D checkpoint. Its normalization description must not replace the directly inspected released export metadata and frontend code.

The [3D-Speaker corpus paper, version 3](https://arxiv.org/abs/2306.15354v3) describes ERes2Net baseline evaluation and EER/minDCF conventions, but does not specify a checkpoint-bound high-frequency cutoff or edge-handling option. These papers support context, not exact provenance.

## Honest experiment names and controls

Recommended primary arm names:

- **A: Audited native frontend**: fixed installed model hash, fixed Sherpa build, native KNF 1.22.3 configuration, high_freq=-400 / 7600 Hz, snip_edges=False.
- **B: Pinned test-source-derived frontend candidate**: the same model hash, explicitly pinned KNF implementation/version, high_freq=0 / 8000 Hz, snip_edges=True. State that its two options also agree with the inspected historical 512-D recipe under torchaudio 0.10.1 defaults.

Do not call B “training-correct,” “official historical reproduction,” or “parity-verified.” A stronger label becomes possible only after the missing provenance or numerical evidence exists.

For both arms, explicitly freeze normalized 16 kHz input, first-channel/downmix and resampling decisions, 80 bins, dither=0, low_freq=20, 25 ms window/10 ms hop, Povey, DC removal, preemphasis, FFT rounding, power/log feature options, mean normalization, model bytes, provider/threads and output normalization. Do not rely on undocumented defaults.

An explicit native-configuration replay through the proposed experiment adapter should be treated as an adapter-compatibility prerequisite before attributing differences to the two disputed options. If the implementation/backend itself differs, report that as another changed factor. A two-arm contrast changes both edge handling and cutoff; it cannot identify the effect of either individually. Cross-engine option agreement is not floating-point feature parity.

Use identical independent multi-speaker utterances and frozen trial/label rules, retain every scorable/unscorable outcome, and report within-arm discrimination plus paired score/embedding changes without tuning product thresholds. Pair counts are not independent speaker counts. Short-input frame counts and feature statistics should be explicit because edge handling changes boundary frames. Different frame counts must not be silently aligned, truncated or padded merely to make a numeric comparison convenient.

Changing either frontend setting creates a separately identified research embedding space. No bank migration, enrollment, matcher changes or production-default switch follows from this source audit.

## Retained evidence

- actual_model_metadata.json: fresh model hash/size and bounded protobuf metadata scan. The graph payload was skipped; no model loader or inference session was used.
- source_evidence.json: 17 fetched text source bodies with original source URLs and Git blob IDs, plus compact release/tag/change metadata.
- source_manifest.json: SHA-256 and recomputed Git blob SHA-1 for each source. **All 17 fetched source bodies match their reported Git blob IDs.**
- This report: TRAINING_FRONTEND_CONTRACT_REPORT.md.

Third-party source snapshots preserve their copyright/license notices where present. 3D-Speaker and Sherpa/KNF source notices specify Apache-2.0; torchaudio's source is governed by the upstream PyTorch Audio license. These evidence files contain no weights, audio or identity vectors. Product-source directories were not edited.

