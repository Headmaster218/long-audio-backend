# ERes2NetV2 acquisition and constructor-only qualification

20 October 2026 UTC. Research qualification only; product code and configuration are unchanged.

## Result

One separately authorized acquisition of the official ERes2NetV2 ONNX asset and one separately released native constructor-only check completed successfully. Independent audits accepted the acquired byte identity, original successful process termination and a reported embedding dimension of **192**. No audio, streams, compute calls, warmup or embeddings were used or produced. This establishes bounded loading compatibility, not encoder quality or product admission.

## Artifact and provenance

The [official asset](https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx) is `3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx`, [asset ID 198893092](https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/assets/198893092). Fresh metadata matched the approved identity and exact length of **71,441,526 bytes**. The acquired file's freshly measured SHA256 is:

`bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239`

This hash identifies these acquired bytes. The asset API's digest is null; no publisher checksum was verified. A previously blocked checksum retrieval route was not bypassed. The acquisition used one logical transfer: the canonical GitHub response returned 302, followed by one validated official release-asset response returning 200. There was no retry or resume.

The [official model-specific ModelScope metadata](https://www.modelscope.cn/api/v1/models/iic/speech_eres2netv2_sv_zh-cn_16k-common?Revision=v1.0.1) declares `Apache License 2.0`. The request specified `v1.0.1`, but the response returned `master`. This is a current model-specific declaration, not revision-pinned historical license evidence for the converted artifact.

Pinned upstream [inference source](https://github.com/modelscope/3D-Speaker/blob/065629c313eaf1a01c65c640c46d77e61e9607b4/speakerlab/bin/infer_sv.py) maps this model to v1.0.1, `pretrained_eres2netv2.ckpt`, 80 features and 192 dimensions. Pinned Sherpa [export source](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/export-onnx.py) and [runner](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/run.sh) explain a plausible official conversion path. The runner clones upstream without a commit pin. Actual historical checkpoint bytes, export-run revision and environment remain unattested.

## Measured execution and accounting

- Acquisition: original process exited 0; **10.717 seconds wall, 0.288 seconds CPU**.
- Native check: Python **3.12.14**, sherpa-onnx **1.13.8**, CPU provider, two configured threads; exactly one `SpeakerEmbeddingExtractor` constructor and dimension read. Dimension was **192**; original process exited 0.
- Load stage, including its wrapper: **0.651 seconds wall, 0.693 seconds CPU**. The constructor interval alone was approximately 0.314 seconds. These are one-off loading measurements, not inference latency benchmarks.
- Combined stage timings: **11.368 seconds wall, 0.981 seconds CPU**. Both stages completed without forced cleanup.
- Native self-reported high-water RSS at the final pre-exit receipt: **218,095,616 bytes**. Teardown peak RSS was unavailable. The 2 GiB RSS check was a measured postcheck, not a hard peak cap. A **4 GiB address-space limit** and the full **4.5 GiB provisional reservation** covered the native stage through its original positive terminal result.
- Accounting used the visible process namespace plus a conservative 1 GiB background allowance and confirmation of no competing heavy work. It was not a host-wide census.

## Remaining boundaries

No numerical equivalence, frontend parity, embedding validity, recognition quality, cross-window linkage or production suitability was tested. No dependency installation, checkpoint export, frontend bridge or product modification occurred.

The existing product remains in its audited **512-D** space. Any **192-D** research adaptation must be isolated and separately reviewed. The proposed later screen involving 19 old controls and 40 calls is not approved by this qualification and was not run. No vectors, recordings, references or private experiment artifacts are included in this report.
