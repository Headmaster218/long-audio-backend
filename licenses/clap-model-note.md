# CLAP research artifact licensing and provenance

This is a research-only local download of the official LAION model, not a bundled model distribution or a production dependency.

- Model: [laion/clap-htsat-unfused](https://huggingface.co/laion/clap-htsat-unfused).
- Frozen revision: `8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a`.
- [Pinned model card](https://huggingface.co/laion/clap-htsat-unfused/blob/8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a/README.md) declares `license: apache-2.0`. The declaration applies to the model repository; it is recorded separately from upstream code licensing.
- Original weight: `pytorch_model.bin`, 614,525,833 bytes. Official LFS SHA256 and locally verified SHA256: `1cd3c601bc4afe0fa87be3de4c13dd2cfadd249fac1e29acf74a9b296c3219bb`.
- [LAION upstream repository](https://github.com/LAION-AI/CLAP) lists CC0-1.0 for its code. None of that repository's Python code is downloaded or executed by this trial.
- Runtime uses already-installed Hugging Face Transformers 4.55.0 built-in `ClapModel` and `ClapProcessor`; Transformers is [Apache-2.0](https://github.com/huggingface/transformers/blob/v4.55.0/LICENSE). No `trust_remote_code` or hosted model inference API is used. The official PyTorch checkpoint is loaded with `weights_only=True` on PyTorch 2.6.0+cpu.
- The model's license does not replace licenses for evaluation audio. Existing ESC-50/noncommercial/CC-BY-SA research fixtures retain their original per-file license/attribution in `benchmarks/events/manifest.json`, and are excluded from source deliverables.
- Full artifact size, source URL, official Git/LFS hash and local SHA256 for every downloaded file are in `benchmarks/events/clap-model-manifest.json`. Local weights and model card remain under the new ignored `models/clap-htsat-unfused-research` directory.

Public checkpoint training-set overlap with these public audio fixtures is unknown. A reported weak-label benchmark result is not evidence that training overlap was ruled out.
