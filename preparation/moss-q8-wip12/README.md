# MOSS structural and prompt audit source checkpoint 12

This additive source-only checkpoint preserves the finite GGUF reader, its fixture source and the small policy correction wrapper for the experimental Handy MOSS Q8 artifact identified in ../moss-q8-wip11/PUBLIC_ASSET.json. It contains no model or runtime payload. The qualified backend runtime remains unchanged.

## Accepted result and preserved failure

The corrected audit passed 30 in-memory fixtures and the structural/prompt audit. It checked 52 fixed metadata expectations, all 685 tensor names/shapes/types, bounded nonoverlapping tensor extents, fixed prompt semantics and pinned runtime-file bytes. The acquired model's exact full-file identity was verified in that separate audit. No numerical tensor equivalence, native ABI execution, model execution, decoder EOS, transcription quality, timestamp accuracy or speaker identity was established.

The original audit failed because its expected map treated the two encoder convolution weights as F16. The pinned quantizer policy uses F32 for these Q8_0 convolution tensors. The separate wrapper corrects exactly enc.conv.0.weight and enc.conv.1.weight from F16 to F32, preserving their shapes and every other expected descriptor. The original failed attempt and its evidence remain preserved; the later success does not relabel that failure.

## Exact source allowlist

- audit/audit.py.template: bounded standard-library GGUF reader, metadata and tensor-profile validation, fixed prompt decoding, pinned-file checks, resource bounds and scoped spawn/socket audit hooks. Its original expected-map function is deliberately preserved, including the two F16 expectations.
- audit/fixtures.py.template: the original 27 synthetic container/profile fixtures, including malformed bounds, types, overlap, prompt mutation and denied audit events.
- audit/q8_policy_audit.py.template: separate source-backed two-descriptor correction and three additional checks. This wrapper is required for the corrected profile; the preserved reader's standalone expected map does not represent the accepted corrected profile.
- README.md: this summary and recovery guidance.

The 30-fixture and structural success belongs to the separately executed, accepted original sources. No fixture or audit was run while preparing this backup.

## Public source references

- Converter: https://github.com/handy-computer/transcribe.cpp/blob/63a44d9239d610b3908e8a66b384924cd4a77217/scripts/convert-moss.py
- Quantization policy: https://github.com/handy-computer/transcribe.cpp/blob/63a44d9239d610b3908e8a66b384924cd4a77217/tools/transcribe-quantize/policy.cpp
- Model configuration: https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize/blob/704aa4a9c304e8520be88901e0d1960158ef5b15/config.json

These are source references, not included vendor source copies. The public fixed prompt, model identity and source-level tokenizer constants remain part of the preserved validation code. No extracted vocabulary, actual token sequence, private audio-derived text or authentication token is included.

## Recovery and privacy limits

These are unexecuted, non-runnable recovery templates. Only private local-binding literal tokens were replaced: runtime manifest/site-packages paths, retained public-metadata path, prior local model identity, clip-specific support/prompt/context counts and local CPU binding. A private inverse map reconstructs byte-identical and AST-identical original source. No assertions, functions, imports or control flow were otherwise changed.

Recovery needs separately reviewed local bindings, source/input pinning, public metadata provenance, contracts, dependencies, resource admission, execution authority and fresh qualification. Do not weaken checks to replace missing evidence. The Python audit hook is a scoped interpreter mechanism; it is not an OS sandbox or a process census.

All original sources remain untouched. Private reversible maps, contracts, settings, environment values, credentials, local paths, run/session identities, receipts, logs, raw transcripts, audio, model bytes and installed runtime files are excluded. Generic output basenames, schema names, public model metadata and synthetic fixture strings remain code. Backup preparation performed no project imports, tests, model/audio reads, downloads, native execution or publication.
