# Two additional English exposed diagnostics — preparation only

No model execution authorized by this document. No corpus text/audio included.

Selection fixed without reading new predictions: EN2002b earliest complete
window starting at/after0; EN2002a earliest complete window at/after120s.
For each, choose earliest feasible boundary then earliest endpoint with duration
30–45.728s. Every human annotation vocal segment AND forced-aligned lexical
word interval constrains cuts; no cut may split one. No speech-density,
speaker-count, overlap or performance selection. No replacement of sparse cases.
Exact sample coordinates required; no rounding across annotation boundaries.

Locked windows: EN2002b[0,32.494), frames[0,519904),11lexicalwords/4speakers;
EN2002a[134.894,168.784), frames[2158304,2700544),122words/3speakers.
Both official HTTP206 ranges independently match local PCM byte-for-byte.
Official AMI CC BY4 data: https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/
All cases are exposed diagnostics, NOT heldout benchmark qualification.

Per case, fresh standalone native epoch and fresh SenseVoice/turn-crop/alignment
baseline processes, then ONE independent whole native Handy process, unchanged
weights/auto-language/2CPU/f16KV/n_ctx2048. No embeddings or stableidentity claims.
No Worker/HTTP claims. Native.finish is standalone-excerpt finalization, not a
claim of correct long-stream finalization cadence. Same exact original v2 scorer,
normalization, oracle assignment and unknown bucket for both arms. No v3 jobwise
scores. Source generation/per-case IDs remain local; no cross-case label linking.

Proposed cap per phase:2CPU,300scompute+60scleanup,365souter. Baseline4GiBAS/
3GiBsampledRSS; candidate6GiBAS/4GiBsampledRSS. Six sequential phases total,
no simultaneous models. Expected actual aggregate a few minutes, not guaranteed.
Stop on operational failure, preserve failure and unrun cases; do not retry,
retune or substitute. Execution requires separate authorization and validated input pins.

Report both cases and weighted sum/errors over133referencewords, with explicit
11-word sparse case caveat; also show each separately. Do not silently drop
negative outcomes. No positive single case justifies promotion; no new output-
driven thresholds or model parameter changes. Earlier125-word result remains
separate developmental evidence. Any interpretation must distinguish within-
excerpt oracle assignment from stable identity and forced timing from human
word timing. Chinese provenance and normalization need separate approval.

Input/reference manifests, actual results and raw outputs stay outside this
source-only Git tree. Native inference reads only external audio/config/pins,
never reference text. Scoring is a separate post-inference process. One attempt
per phase, fresh output directory; exact raw frozen before interpretation.
