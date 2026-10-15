# Trusted Chinese quality checkpoint — 15 October 2026

A small source and aggregate-results checkpoint for two completed AISHELL-4 direct-adapter comparisons on disjoint windows from the same recording. Historical aggregate results and fixed-assignment arithmetic have been checked. The current product default is unchanged.

- `QUALITY_REPORT.md`: character-error results, resources, and limitations.
- `PROTOCOL.json`: public, data-minimized methodological restatement, including the historical acquisition caveats.
- `CONFIGURATION.md`: required configuration fields, input contracts, and source-pin regeneration.
- `SOURCE_MANIFEST.json`: executed-source and template hashes, required configuration substitutions, and an exact held-out source-derivation recipe.
- `src/compare.py.in`: exact source template with private local paths, identities, and configuration pins replaced by required placeholders.

The base source template and its derived held-out variant are unconfigured and unexecuted. Historical experiment evidence applies to the original source identified in the manifest; inverse substitutions have been checked to reconstruct both executed originals byte-for-byte. Static parsing is not a model or scoring run. The public protocol is a methodological restatement, not the original private execution protocol.

Audio, hypotheses, manual reference text, private source-pin lists, raw results, and configuration/receipt payloads are excluded. This checkpoint is separate from the previous English/Chinese crop-context checkpoint and does not modify it.
