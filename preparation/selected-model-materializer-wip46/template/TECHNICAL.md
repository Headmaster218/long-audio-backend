# Selected-member archive materialization

## Archive selection and provenance

The selectors describe three fixed artifact families. Archive names, member names, expected sizes, and available SHA256 expectations are encoded in selected.py. Model weights and native binaries are external inputs; this source package does not distribute them.

AudioSet uses the sherpa-onnx-zipformer-small-audio-tagging-2024-04-15 archive family. It retains model.onnx and class_labels_indices.csv at the canonical model subtree, with exact sizes of 91,932,865 and 14,675 bytes. Both selected members have fixed SHA256 expectations.

SenseVoice uses the sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17 archive family. It retains model.int8.onnx and tokens.txt under models/sensevoice, with exact sizes of 239,233,841 and 315,894 bytes. Both selected members have fixed SHA256 expectations. The existing compressed archive and upstream model license remain in that directory.

The native family is nemo-speech-0.2.0-linux-x86_64-cpu. Its expected archive inventory contains 117 members: 75 regular files, 31 directories, and 11 symbolic links, represented by 119 raw tar headers. The regular-file payload totals 17,872,648 bytes. The selector retains the 75 regular files and writes 11 ordinary-file copies for aliases, totaling 31,003,744 bytes across those 86 outputs. Every alias must resolve to an in-tree regular member. Native member digests are discovered from the matching archive and must not be described as preexisting digest expectations.

The native layout also includes two product manifest copies, the OpenMDW vendor notice, and an installation JSON file, bringing the native output count to 90. The vendor-notice digest is preserved in source. A digest identifies bytes; it does not establish a license grant or transfer rights to model weights.

## Stream and file formats

AudioSet and SenseVoice inputs are bzip2-compressed tar streams. The native input is a gzip-compressed tar stream. Python's tarfile supplies tar and PAX decoding; a bounded reader controls decompressed input. Selected regular-file bytes are hashed as they are written, fsynced, and read back independently to check their digest. Unselected member payloads are discarded.

A complete input requires compressed-stream termination, two tar zero blocks, and a decoded suffix containing only zero bytes. Concatenated compressed streams are decoded within the same byte limits. Duplicate members, unsafe or escaping paths, selected aliases, malformed or truncated streams, nonzero suffixes, unsupported member types, and sparse-file encodings are refused.

The native installation JSON records the CPU runtime/version, asset-relative file paths, sizes and hashes, the native library location, thread settings, and the data-only materialization status. It does not certify that a native library or model has been loaded. Metadata JSON is restricted to ordinary files; installation and model outputs are ordinary files with mode 0644.

## Safety bounds and destination rules

- Archive reads and decompression output use a 64 KiB pump bound.
- Tar processing permits at most 4,096 raw headers, 32 extension levels, and 64 KiB of extension metadata.
- Decoded-stream ceilings are 300 MiB for AudioSet, 350 MiB for SenseVoice, and 256 MiB for native.
- Each selected member must have the expected regular-file type and length. Fixed digest expectations apply where supplied.
- Inputs and existing directories use no-follow descriptor traversal. File identity is checked before and after reading; filesystem descriptor values are specific to the configured environment.
- Output creation is exclusive. Existing files are never overwritten. AudioSet and native require absent destination subtrees.
- SenseVoice requires the expected archive/license pair before creation, preserves their identities, and reconciles the resulting four-name directory.
- Native aliases are flattened only after their final regular targets have been verified. Cycles, escapes, missing targets, and nonregular targets are refused.
- The completed output tree must match the finite selected layout.

The process wrapper implements a 600-second absolute limit, divided into 540 seconds of work and 60 seconds for cleanup. Its target profile uses a 1 GiB address-space limit, a 256 MiB resident-memory limit, and constrained CPU scheduling. Input/output storage is checked against finite category and aggregate budgets, including rounded file allocation and directory growth.

With a 4,096-byte allocation unit, the implementation projects 91,967,488 additional bytes for AudioSet, 239,566,848 for SenseVoice, and 31,551,488 for native, totaling 363,085,824 bytes. The native projection includes its auxiliary JSON and copied manifests/notice. Existing inputs and earlier outputs still occupy storage. These are projections for the specified layout, not a general disk-space guarantee.

## Synthetic coverage and observed results

The 12 named cases cover pinned bzip2 selection, discovered gzip hashes, duplicate members, alias substitution, digest mismatch, unsafe names, truncated gzip, nonzero suffixes, existing outputs, native alias cycles, native alias escapes, and copying a verified regular file. Payload fixtures are at most 26 bytes and their tar representation is at most 20 KiB. The native graph cases also use a small metadata inventory.

A prior execution of the original configured implementation passed all 12 cases and completed the AudioSet, SenseVoice, and native data-only materializations. AudioSet and SenseVoice each produced their two selected files. Native produced the 90 outputs described above. Model loads and model calls remained zero.

The generated-path source template in this directory is a different, unexecuted source instance. Its manifest binds the current source and this project-facing technical document. These observations establish archive/file materialization only; native runtime behavior, model inference, ASR accuracy, and end-to-end application behavior remain unqualified.
