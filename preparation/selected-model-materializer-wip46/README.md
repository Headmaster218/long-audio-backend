# Selected-model archive materializer

A data-only source template for selecting AudioSet and SenseVoice model files from bzip2-compressed tar archives and a native CPU bundle from a gzip-compressed tar archive. It checks member names, types, lengths, digests, and destination layout while keeping decompression and output allocation bounded.

## Contents

- template/selected.py: archive selection, bounded streaming, output verification, and native alias flattening.
- template/owner.py: per-invocation process ownership and resource enforcement.
- template/test_selected.py: 12 named synthetic archive-selection cases.
- template/TECHNICAL.md: formats, provenance, safety bounds, and observed results.
- template/PACKET_MANIFEST.json: sizes and SHA256 values for those four files.

GENERATED_* markers stand for environment-specific paths, lengths, digests, and filesystem identifiers. This template is not configured to run as-is and has not been executed. Its Python syntax and source manifest were checked. The required resource-guard implementation and input metadata are external dependencies.

The original configured implementation previously passed its 12 synthetic cases and completed all three data-only materializations. Model loads and model calls were zero. Those results do not qualify native execution, model inference, transcription quality, or the end-to-end application. They are not execution results for this changed template.

No archives, weights, native binaries, or generated run data are included. Public selected-member and vendor-notice digests remain in the source.
