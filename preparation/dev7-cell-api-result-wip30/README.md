# WIP30 bounded durable-cell API result

A genuine retry has completed and passed independent scoped review. This
status-only checkpoint reports the accepted result; it contains no new code
and authorizes no execution or further retry.

## Observed and accepted

- One genuine final 21-second primary core committed at frames [0, 336000).
- One selected 20-second durable cell at frames [0, 320000), with source,
  native, analyzer, proof and PCM lineage. Independent comparison matched
  the complete 640,000-byte cell PCM prefix to the committed source.
- One MOSS cell API job returned a diagnostic. An identical-key replay
  returned the same job with no redispatch. Copied-pin removal and original
  process cleanup were confirmed.
- Extant-source ON restart preserved actual cell API/proof readability with
  a fresh empty jobs epoch. OFF restart preserved closed-database logical
  source/cell/proof lineage after positive owner cleanup. Restart provider
  and model calls were zero; database byte equality is not claimed.
- The unselected one-second tail was absent and was not transcribed.
- The complete 3,781-file byte posthash matched. Original handles were
  positively waited and cleanup was confirmed.
- The retained HTTP total was 57 responses; the capture-client subset was
  43. Of the total, 45 responses carried private-no-store handling.

## Limits retained

EOS remains unknown and complete_content is false. The MOSS output is a
diagnostic, not qualified transcription content. Model-local speaker labels,
generated timing and acoustic accuracy remain unverified. Hidden native
forward counts and the live MOSS mapping were not established.

Qualification is limited to the exact Linux/runtime and sampled resource
conditions. It does not establish arbitrary long recordings, a cross-upload
seam, a nonzero core offset, native epoch transitions, in-flight cancellation,
primary preemption, deletion, or 48-hour behavior. No Windows, power-loss,
forensic memory-erasure, universal hard-latency or hostile same-UID
namespace-mutation guarantee is made. Volatile job results and invocation
deduplication are not carried across restart.

Earlier actual and fixture failures remain retained. The historical
[WIP28 entry checkpoint](../dev7-isolated-entry-wip28/README.md) and
[WIP29 corrected-source checkpoint](../dev7-corrected-source-wip29/README.md)
remain unchanged; this result does not relabel their earlier status.

This public payload is only this README. It excludes raw text/audio,
captured data, execution evidence, local paths, private evidence identifiers,
configuration, credentials and private manifests.

Sole publication parent: f82eac9432ac597744537858ffb6a089b65c31ff
