# Provisional recording-local speaker links

This optional annotation path has no persistent person identity. MOSS speaker
names are job-local tags; native labels belong to their witnessed epoch/runtime
coordinate scope. The caller must verify all source aliases against the current
recording and live CellLease authority, bind the committed MOSS result hash,
and revalidate authority before publication. Hashes do not establish live access.

## Pure capture contract

`capture_candidates(support, pcm, proof, projection, job_id, result_sha256, space)`
returns a bounded serializable batch and up to four independently owned PCM byte
strings aligned with `batch['nodes']`. The PCM is never retained in the batch.
The full validated native proof is retained as a parsed bounded document. Source
manifests, support, proof, result, parsed projection, crop PCM, model-space and
node digests remain bound separately. The runner adds only the original vector
list to each node. `validate_batch` checks transport/storage integrity; the caller
must still verify the committed result and live recording membership.

Each crop is exactly 32,000 consecutive original 16 kHz PCM16LE frames. Phrase
bounds round inward; competing MOSS tags round outward. Native bounds are already
validated integer frames. For each phrase/native-label intersection, subtract all
other observed native labels and other MOSS tags, then remove 1,600 frames from
each end of each remaining component. A phrase must have exactly one native
label with an eligible component. Choose its longest component, breaking ties by
start/end, and center the exact crop with a floor-half offset. There is no padding,
concatenation, relaxed floor, or rounding of native frame coordinates. Native
cleanliness is a model prediction, not ground-truth purity.

Ordinary proofs use `turns`; same-epoch and reset-replay core seams use the single
`query.turns` response in its existing logical coordinate system. No second
support-origin or replay offset is applied. Legacy v2 seam responses remain
separate: crop support is restricted to the piece witnessed by each response.
Simultaneous native-label activity is retained as a hard cannot-link even when
it lies outside a selected clean crop. Each label pair needs only its canonical
earliest positive-overlap witness, independent of crop eligibility.

## Reconciliation

`reconcile_batches` accepts one already verified recording scope, at most 32
candidate nodes and 496 independent pairs. Input vectors must be exactly retained
float32 values, dimension 512, with squared norm within 1e-5 of one. They are never
renormalized or replaced with a centroid. Scores use Python `math.fsum` over
float64 products of original values.

Exact source-crop duplicates or identical PCM are one observation for ranking;
aliases retain their own result/phrase bindings. The internal
`independent_node_count` counts exact-crop/PCM duplicate-distinct observations,
not statistically independent acoustic evidence. Partly overlapping crops can
remain distinct under this policy. Duplicate vectors must agree.
Contradictory native cannot-links within a duplicate class cause abstention by
rejecting the optional reconciliation. This includes an ordinary and seam view
of the known AMI crop `[220656,252656)`: two result aliases are one observation.

Complete-link similarity is the minimum of every original cross-cluster pair.
A merge requires reciprocal unique best choices, score at least 0.55, and both
best-minus-runner-up margins at least 0.10. All competing clusters participate
in ranking, including below-threshold and cannot-linked competitors. A blocked
winner is vetoed after ranking, never replaced with second place. Exact ties and
missing runner-up comparisons abstain, so any two-node set cannot link.
Merges are synchronous, disjoint and canonically ordered. No connected-component
transitivity or centroid approximation is used. Current anonymous labels may
change when recording evidence changes; they are not stable person IDs.

The policy is an explicitly bounded provisional local study, not calibrated
meeting-domain identification accuracy. Synthetic tests establish geometry,
binding and algorithm invariants only. Model-free qualification covers synthetic invariants; acoustic extraction
and real-data behavior are reported separately below.


## Bounded decision projection

Reconciliation returns at most 32 compact candidate-node annotations and 32
job/result summaries. Full per-phrase outcomes and source-span manifests stay in
the immutable batch; they are not duplicated into a potentially unbounded
annotation list. Job summaries expose capacity exhaustion separately from
insufficient clean support. Missing vectors remain `probe_unavailable` or, with
an explicit batch-hash terminal-state binding, `probe_interrupted`; they never
supply a comparison. Available singleton evidence remains `source_scoped`.
Successful provisional links use `provisional_linked`. `links_stale` belongs to
the caller's revision/authority projection, not this pure calculation. Overall
state reports the applicable unavailable/interrupted/insufficient condition when
no comparison evidence is available. Decision JSON is capped at 64 KiB and
capture metadata plus owned crops plus a 64 KiB output allowance at 512 KiB.

## Text-first lifecycle and bounded recovery

The feature is disabled unless both `--recording-speaker-links` and an explicit
absolute `--recording-speaker-model` are supplied with automatic MOSS enabled.
The model must match the full audited frontend/runtime/model-space descriptor;
a matching filename alone is insufficient. Disabled startup and all transcript
GET handlers import no optional model runtime and construct no extractor.

After positive MOSS child reap and model/session close, the same job thread may
copy clean crops while its validated CellLease remains fully charged. A separate
512 KiB staging charge covers crops, prepared evidence and bounded output. The
normal result validation and text commit then run unchanged. Only an exact match
to the durably committed result permits reservation and optional inference. A
fresh optional permit has its own cancellation event; primary preemption cannot
cancel or erase committed text. The one embedding child uses existing owned
process primitives, two CPU threads, a 10-second work deadline and 5-second
cleanup phase. Uncertain cleanup retains the original handle, staging and permit.

Admission consumes an attempt before inference. The two sidecar tables retain
immutable batches and one bounded reservation per scope. Clean process exit
releases the execution permit even if SQL acknowledgement is pending. The exact
bounded payload remains charged and blocks retirement with an explicit storage
pending status. Existing idle/storage-only paths retry only those same bytes.
Lost reservation acknowledgement and pre-child failure resolve the exact
same-owner prepared attempt as unavailable without a second inference. Revoked
source authority can retain a no-vector historical terminal record, but cannot
publish current identity. Session deletion erases both tables and accounting in
the existing transaction and fences late writes.

Reconciliation checks every retained batch against current transcript binding,
result, recording membership and noncancelled source authority before scoring,
then rechecks revision/binding before publishing. Each attempt is bounded to
32 batches/nodes, 496 pair scores and 8 MiB staging. A bad scope receives a
best-effort stale/unavailable/cap state without preventing another scope's startup
recovery. Restart never repeats inference. GET reads saved small annotations
only; raw source references, archived content hashes, speaker scopes and evidence
responses remain unchanged. Current provisional annotations are separate fields,
contain no vectors/native proof, and participate in cursor invalidation.

Focused model-free tests cover clean geometry, duplicate evidence, constrained
ranking, quotas, revision CAS, source deletion/movement/cancellation, recovery,
text-first ordering, primary preemption, uncertain cleanup, lost acknowledgements,
no-model storage retry, and exact evidence projection parity. This initial 32-node policy is not long-recording qualification.
Acoustic extraction and real-data behavior have a separate measured scope.

The existing automatic idle path continues at most 16 recovery scopes per call,
throttled to once per second and skipped during a live job/probe. It introduces
no worker. A transient post-commit decision failure is therefore retried without
new inference. If annotation fields exceed the existing 256 KiB response cap,
the opt-in page omits them and sets the existing `reading_status` field to
`provisional_links_omitted_cap`. This is shorter than the original descriptive
marker, so it also fits at the exact text limit. Disabled responses are unchanged.
Missing annotations are unknown, never a positive identity; text, source scope, person
identity flags and evidence references are not truncated or replaced.


## Initial bounds and measured qualification

This feature is experimental and disabled by default. Its limit is 32 retained
phrase/crop nodes and 32 attempted batches per recording, at most four probes
per job, and 1,024 nodes/batches across the database. Retained evidence is capped
at 4 MiB per recording and 128 MiB globally. Quota exhaustion is explicit; the
implementation does not silently evict older observations. Optional staging is
512 KiB while the full source lease remains charged, and model-free reconciliation
uses at most 8 MiB staging. A recording can fragment into multiple provisional
labels. These limits do not qualify seven-day or other long-recording identity.

The executable candidate passed 82 focused tests and 244 compatibility tests,
plus six subtests. Focused tests completed in 7.74 seconds with 75.0 MB peak
sampled RSS; compatibility tests completed in 39.87 seconds with 143.2 MB peak
sampled RSS. Both stages used one pinned CPU with niceness 19 and enforced
1 GiB sampled process-tree RSS, 2 GiB address space, a 256 MiB artifact cap, a
768 MiB owner reserve and a 5.5 GiB free-disk floor. Neither stage imported a
model runtime. Source origins, all 440 source-file hashes and positive cleanup
were checked. These results establish contract behavior, not speaker accuracy.

The compatibility scope covered automatic MOSS integration, durable result
history, native execution permits, transcript HTTP/indexed reads, existing
verified-identity projection, and manual MOSS manager/API lifecycle behavior.
The focused scope covered geometry, ranking, duplicate evidence, cannot-links,
quotas, current-source authority, restart/deletion, cancellation, lost SQL
acknowledgements, source retirement, pure GET, and near-limit text preservation.

### One-case acoustic and persistence check

A 17-second Chinese CN17 recording completed three automatic MOSS jobs without
manual MOSS submission. Four original 512-dimensional embeddings were committed
in their exact audited model space. The saved decision had four source-phrase
nodes, six cosine pairs and two provisional clusters, each containing two nodes
across ordinary and seam jobs. Four distinct source phrases were linked and
eight had insufficient clean support. Six linked appearances in the current
transcript were repeated displays of those four phrases in reading/alternatives;
they were not six speakers or six unique observations.

All six cosine scores and the fixed 0.55 threshold/0.10 margin decisions were
independently recomputed. No exact crop/PCM duplicates collapsed in this case.
Three native overlap witnesses were retained, but no pair of retained node
labels formed a hard cannot-link; no hard cannot-link was merged. All four
retained nodes shared one native local label. Both linked pairs reused
substantially overlapping audio. A frozen-reference post-hoc check also found
about 0.088 seconds and 0.636 seconds of second-speaker overlap in two late crops,
despite native-exclusive selection. Native-predicted clean support is therefore
not a guarantee of pure single-speaker audio, and duplicate-distinct nodes do
not establish independent corroboration or speaker accuracy.

The check established automatic cross-job extraction, saved provisional
annotations, current-source binding and persistence. Exact archived text,
evidence responses and sidecar bytes survived a restart with models disabled;
default-off transcript text stayed unchanged. The probe pipeline ended with no
pending storage, cleanup or staging ownership. The harness exited successfully
in 84.73 seconds, with 1.844 GB peak sampled process-tree RSS, 2.649 GB including
the 768 MiB reserve, 4.99 MB peak outputs and a 0.265-second maximum sampler gap.
The four embedding calls are derived from the validated fixed code path and
committed vectors; the public API does not retain/export a native probe cleanup
receipt or compute-attempt counter. This is a functional integration result,
not speaker-identification accuracy or long-recording qualification.
