# MOSS acquisition and text-evaluation source checkpoint 11

This additive source-only backup preserves a new one-asset MOSS acquisition adapter, its model-free fixture source, and a deterministic text-only CER diagnostic. The qualified backend runtime is unchanged. No model, audio, annotation, hypothesis or transcript content is included.

## Status and limits

The pinned Handy MOSS Q8 conversion is a new experimental profile: 986,899,616 bytes, with the public identity and Apache-2.0 metadata in PUBLIC_ASSET.json. Acquisition completed and an independent review accepted the exact acquired-file integrity, retained source pins, original process wait and cleanup. The file is not included here. Structural and prompt compatibility, runtime qualification and actual MOSS inference remain pending. Acquisition success does not establish model compatibility or transcription quality.

The CER evaluator was separately qualified as a deterministic standard-library diagnostic over existing retained text. It performs no inference or audio reads. It concatenates normalized reference intervals by start time, with tier and interval indices as deterministic tie-breakers, then reports ordinary character edit distance. Unicode NFKC, case folding, whitespace removal and punctuation-category removal define normalization. Overlapping speakers impose an arbitrary serialization; edge-straddling intervals are not sample-exact text. Its all-intersecting and fully-contained reference views are single-clip diagnostics, not a corpus benchmark, speaker-attributed score, timing measure or general model ranking. No raw evaluation input or output is published.

## Minimal source allowlist

- acquisition/launch.py.template: closed synthetic/acquisition stages, resource limits, exact entry and pin checks, inherited process ownership and bounded receipts.
- acquisition/prepare.py.template: exact one-asset identity, prospective component accounting, durable one-shot claim, bounded metadata and no-retry acquisition adapter.
- acquisition/test_acquisition.py.template: preserved 36-case model-free fixture source, including pacing, privacy, scope, ownership, profile and accounting checks. This file is byte-identical to its original source; the fixture suite was not run for this backup.
- evaluation/evaluate.py.template: pinned, bounded text parsing, normalization and deterministic dynamic-programming character-error calculation.
- PUBLIC_ASSET.json: only public model URL, hash, size, license, source metadata and scoped qualification status.
- README.md: this checkpoint summary.

The corrected downloader is reused by reference from ../qwen17-wip8/acquire.py, SHA256 62eb377286b78ddb156e1f51ebf50c8f64d7851e57168743c63d8b6968154908. Its pacing fixtures are in that same earlier checkpoint. This backup does not duplicate either file.

## Recovery and privacy

These are unexecuted, non-runnable recovery templates. Only literal tokens carrying private local bindings were substituted: preparation and dependency paths, private source/interpreter pins, text input paths/sizes/hashes, clip frame bounds and retained chunk/candidate identifiers. The private reverse mapping reconstructs byte-identical and AST-identical original source. All other source bytes, assertions, imports and control flow are preserved. This static source-preservation check is not executable qualification.

Required local guard, metadata helper, manifests, contracts, provenance evidence and execution bindings are deliberately absent. Recovery requires newly reviewed bindings, full pinning, appropriate dependencies, resource admission, separate execution authority and fresh qualification. Do not remove checks to bypass missing evidence. The public asset catalog is descriptive metadata, not an executable acquisition manifest.

Original frozen sources and evidence remain untouched. Private maps and reviews, settings, environment values, credentials, contracts, specifications, manifests with private state, receipts, logs, exact run/session/lineage identifiers, raw annotations, hypotheses, audio, transcripts, models, wheels and installed runtimes are excluded. Generic schema names, output basenames, environment variable names and synthetic fixture strings remain source code. No project, model or runtime imports, tests, downloads, installations, conversions or inference were performed to prepare this checkpoint.
