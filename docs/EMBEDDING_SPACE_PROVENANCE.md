# Embedding-space provenance audit

This is a bounded, model-free source/configuration audit for the experimental
verified identity bank. It does not establish recognition accuracy, human review,
or acoustic purity. Ordinary inference remains available when this proof is
unsupported; verified enrollment and matching must abstain.

## Evidence boundary

The supported binding combines the actual installed native-library hashes,
installed wheel `RECORD` checks, the native extension's reported vendor revision,
inspection of that exact official revision, and metadata read from the exact
encoder bytes. This is practical local provenance, **not** an independently
reproduced build or protection against an adversary modifying the running process.
A matching version string or model dimension alone is insufficient.

The native extension reports Sherpa 1.13.8, revision `11afbd00`, and ONNX Runtime
1.28.2. The [official revision lookup](https://api.github.com/repos/k2-fsa/sherpa-onnx/commits/11afbd00)
resolves to `11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf`, the release-v1.13.8
commit. Both native binaries match their installed distribution `RECORD` entries.
No independent official-wheel download or reproducible-build comparison was
performed. The previously inspected different revision is not used as proof.

| Artifact | SHA-256 |
| --- | --- |
| Sherpa CPython 3.12 Linux x86_64 extension | `eb9c3545b310ecd97ba0a2ff53fe39f8d8fb9efaea2dea0ca333ae9050bf2cd2` |
| Loaded `libonnxruntime.so` | `4b3607aebd1784b26b6f9b20e4bd974c7ab8287043e4d095cb7d2cb40b5e566e` |
| ERes2Net base encoder | `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b` |
| CMake-pinned kaldi-native-fbank 1.22.3 archive | `9176cc66fc7ce1edf85cf355b06e320c57db6297df74277f575183468893cf61` |

The encoder's top-level ONNX metadata was read with a bounded protobuf wire scan,
skipping graph/tensor payloads. No ONNX loader, inference session, model
constructor, or inference call was used. The actual metadata establishes:
`framework=3d-speaker`, `sample_rate=16000`, `output_dim=512`,
`normalize_samples=1`, and `feature_normalize_type=global-mean`. The complete
metadata map, including its language/source description, is hash-bound in the
code's audited record; it is not inferred from a filename.

## Exact implementation chain

All Sherpa source links below use the full reported official revision:

- [Extractor dispatch](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/speaker-embedding-extractor-impl.cc) selects the general implementation for `3d-speaker` metadata.
- [General extractor](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/speaker-embedding-extractor-general-impl.h) constructs default feature configuration, then sets sampling rate and waveform scaling from model metadata; it subtracts the per-bin mean over all frames when metadata requests `global-mean`.
- [Model metadata reader](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/speaker-embedding-extractor-model.cc), [feature defaults](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/features.h), [feature construction](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/features.cc), and [stream forwarding](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/online-stream.cc) establish the effective configuration path.
- [Pinned dependency declaration](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/cmake/kaldi-native-fbank.cmake) specifies [kaldi-native-fbank 1.22.3](https://github.com/csukuangfj/kaldi-native-fbank/archive/refs/tags/v1.22.3.tar.gz) and its archive checksum. The downloaded archive matched that checksum. Its filterbank, window, mel, and online-feature implementations were inspected to resolve the remaining inherited options.

Canonical source-file hashes, exact source URLs/revision, dependency archive hash,
complete model metadata, NumPy runtime hashes, and resolved values are retained
in `audio_backend/embedding_space.py`'s audited binding. The descriptor includes
these proof references and its binding hash. Relevant source hashes include:

| Source | SHA-256 |
| --- | --- |
| Sherpa `features.h` | `98d7cced1049d2dcaca085d8b309e7296d8b6f811db8c8f0762907d4fd31d66e` |
| Sherpa `features.cc` | `1d2f19982d5e9c410a7101027a0251ef63ca7d50413d91edab029d546c9cf2e8` |
| General speaker implementation | `585a32319d1bc29763000c3c18eb3e97413dbb5d7965a9a485caed7a0dd9ee22` |
| Speaker metadata reader | `eae4f613882731c8e027543df4d40cf58c719a0bd0ef963a9ada4dc1fa7cc120` |
| Speaker implementation dispatch | `0a98272899c8310743dea4281e1011a0dcf360d26aebe43dd146f4c594cb27bc` |
| KNF `feature-fbank.h` | `4be90549f3df404c71cab527fa9ef7fcc3ec14883c33235dfddde234be36a8ce` |
| KNF `feature-fbank.cc` | `6c9edabf3b2479f31392aec9490688f59262e6f86c85cc648cfa8987387814c1` |

Sherpa sources retain Xiaomi Corporation copyright notices and their
[Apache-2.0 license](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/LICENSE).
Kaldi-native-fbank sources retain their Xiaomi/Kaldi attribution and
[Apache-2.0 license](https://github.com/csukuangfj/kaldi-native-fbank/blob/v1.22.3/LICENSE).
No third-party source bodies, encoder bytes, audio, or identity vectors are
redistributed by this audit document.

## Resolved effective feature space

| Setting | Effective value |
| --- | --- |
| Input / encoder rate | 16,000 Hz mono float32; no extractor resampling |
| Waveform multiplier | 1, from `normalize_samples=1` |
| Mel bins / low frequency | 80 / 20 Hz |
| High frequency | **-400 Hz relative to Nyquist = 7,600 Hz** |
| Snip edges | **False**, with `input_finished()` flushing the stream |
| Frame / hop / FFT size | 25 ms / 10 ms / 512 |
| Window / dither / DC removal | Povey / 0 / enabled |
| Pre-emphasis | C++ float32 `0.97` (`0.9700000286102295`) |
| FFT rounding | Round to power of two |
| Spectrum / filterbank | Power / natural-log mel, float32-epsilon floor |
| Energy feature / HTK compatibility / librosa | Disabled / disabled / disabled |
| VTLN | Warp 1.0 |
| Feature normalization | Per-mel-bin global mean over every supplied frame |
| Inference provider / threads | Actual CPU configuration, 1–4 threads; included in the space ID |
| Producer output | `np.asarray(..., dtype=np.float32) / np.linalg.norm(...)`, converted to a Python list |

Output normalization binds NumPy 2.5.3 and the inspected installed normalization
module, native core/linalg modules, and bundled numerical-library digests. It is
separate from the bank's explicitly versioned matcher normalization. The original
producer list is copied exactly before Store normalization; its canonical JSON
fingerprint must match the support manifest.

Decoder/downmix/resampling lineage identifies the reviewed waveform and remains
separate. It cannot certify embedding-space comparability.

## Runtime use and portability

Only the audited Linux x86_64 binary/model/NumPy combination is supported here.
Windows, other wheels/builds, changed model metadata/bytes, or changed effective
settings fail closed. Future platforms need their own audited binding; there is
no implicit compatibility-equivalence list. Every accepted configuration has an
exact canonical SHA-256 identity, and thread-count differences are explicit.

The analyzer snapshots actual extractor configuration and artifact signatures
before constructing its extractor, checks its actual dimension, and verifies
unchanged artifacts after computation. A fixed-size stat-keyed cache hashes each
unchanged model/library once per analyzer lifetime. Ordinary later windows stat
these files without rereading their bytes. This detects normal concurrent file
changes, not malicious restoration of all filesystem metadata.

Support opt-in adds private original-vector copies and descriptor metadata.
Vector capture does not depend on PCM retention quotas. Raw vectors never belong
in ordinary result metadata, public API responses, or change feeds. Opt-out
preserves the legacy result contract. Silence/non-speech skips cannot reuse a
previous window's space proof.

Model-free tests cover feature/build/model/normalization mutations, unknown proof,
platform rejection, raw-vector identity, no per-window artifact rereads, file
changes during extraction, bounded capture/cache, and frozen legacy output. These
checks are structural evidence only. They do not establish recognition accuracy,
correct operator labels, acoustic purity or long-recording acceptance.
