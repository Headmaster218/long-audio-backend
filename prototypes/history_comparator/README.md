# Exact full-history comparator: research WIP

This is an isolated, model-free prototype. It is not a production store,
authority mechanism, migration, model run or identity-quality qualification.
Production source remains unchanged. The scorer oracle is the published dev50
baseline, commit `49cd724dda5595aee29eaf1d7da2f41739b43d08`.

## What is preserved

- Exact original normalized float32 components, stored in immutable 2,048-byte
  rows. No renormalization, averaging, BLAS substitution or vector index.
- Float64 products summed by `math.fsum`, with triangular float64 score storage.
  Appending rows computes only new-old and new-new scores. Old-old cache bytes
  remain unchanged.
- All physical competitors participate in synchronous reciprocal complete-link
  clustering. Ranking happens before threshold/margin/cannot-link/source-overlap
  vetoes. Exact aliases retain metadata and hard witnesses, including aliases
  with no vector. No rival is dropped to improve acceptance.
- Complete-link updates use minima of constituent cluster scores and dependency
  OR. Top-two ranks preserve score and lexicographic group ordering. One-row
  score extension is O(N); whole-history revalidation is not O(N).
- Complete revision-bound observation scanning and a final revision check gate
  current publication. Missing pages, stale tokens and semantic revocation
  leave results pending. Revocation fences the entire scope.
- Observation pages have a concrete binary representation, capped at 32 rows and
  128 KiB including framing and revision/cursor fields. A row near the reduced
  prototype's 2 KiB metadata cap lowers a page below 32 rows. This metadata cap
  does not describe production admission or retained proof size.
- Current annotations and compact decision pages are capped at 32 rows and
  128 KiB including their full envelope. Merge traces use a bounded DAG rather
  than repeated full member lists. Whole trace expansion is available only for
  the small oracle tests. Minority streams stay in sequence order.

## Finite verification

All 56 focused synthetic cases passed. Exact oracle comparisons cover selected
0–32-node cluster groups and full round traces, original float32 validation and
float64 dot-product bytes. Tests include threshold neighbors, exact ties,
nontransitive and multiround cases, late competitors/aliases/witnesses,
incomplete scans, pause/resume and stale revision/space tokens.

Alias and paging tests use reduced expected assertions. They do not establish
complete `reconcile_batches` equivalence, production proof/job/outcome envelopes,
or full production annotation fields and statuses.

One fixed dense benchmark used seed 271828: Python `Random.uniform(-1, 1)` for
512 components, one binary64 normalization, then float32 conversion. It built
2,880 cold observations and appended exactly four, ending at 2,884. That count
is an illustrative 48-hour sampling-opportunity count, not a speaker count,
approved policy or lifetime limit.

Measured on one CPU at low priority:

- Cold exact score cache: 145.641 seconds for 4,145,760 genuine dot products
- Cold full comparison: 9.628 seconds
- Four-row score append: 0.414 seconds for 11,526 new dot products
- Full comparison after append: 9.759 seconds, including 6.679 seconds of
  matrix/dependency reconstruction and 2.859 seconds of clustering
- Original score-cache prefix was byte-for-byte conserved
- Peak sampled owned supervisor/process-tree RSS: 123,113,472 bytes
- Benchmark process maximum RSS: 91,516,928 bytes
- Total guarded run: 167.544 seconds, with both child processes positively reaped
- All 2,884 annotation rows, including 29 minority synthetic MOSS-tagged rows,
  were returned in 91 pages; the largest annotation page was 5,531 bytes

Both large comparisons were one-round, no-merge cases. The full comparison
stayed below the illustrative 60-second arrival interval in this geometry.
That is not a worst-case or sustained-arrival throughput guarantee. Small
multiround tests do not establish large multiround performance.

The run used a 20-minute total deadline, 256 MiB sampled owned RSS cap, 1 GiB
per-process address-space cap and 32 MiB artifact cap. Sampling does not prove
an instantaneous RSS ceiling. No 4,096-row extension or expensive retry ran.
Detailed counters, phase timing and limitations are in `QUALIFICATION.json`.

## Files and reproducibility

`compact_history.py`, `test_compact_history.py` and `benchmark_history.py` are
exact tested bytes. This README and the qualification summary are separately
sanitized publication documents. The local supervisor, machine-specific paths,
raw test logs/manifests, runtime, models and vector/cache binaries are excluded.

The tests require the baseline `audio_backend` package and pytest 8.4.2. They
can be selected from the repository root with the repository and this directory
on `PYTHONPATH`. The benchmark is standard-library-only, imports the sibling
prototype, and writes bounded JSON reports into a pre-created sibling `reports`
directory. Running it requires a separately reviewed resource supervisor; this
source backup is not authorization to launch new work or install dependencies.

## Remaining limitations

This is an in-memory stand-in, not durable admission, retention, database
indexing, concurrency, restart recovery, or real authority certificates. One
conservative revision token represents policy/space and history/dependency
changes; real transactional writers belong to the separate authority design.
Generator continuations are process-local. An abandoned partial append remains
pending rather than usable. Historical decision objects may be inspected, while
only guarded current page APIs represent current publication.

The measured memory includes a temporary complete-link matrix in addition to
the retained cache. Raw vector/cache/dependency-array bytes are not total RSS.
Each clustering round still ranks every cluster pair and updates each resulting
pair. Many rounds, large alias/witness populations and continued invalidation
can be substantially more expensive than the measured geometry.

No 4,096-row maximum, large adversarial multiround completion, continued-arrival
convergence, production SQL latency, primary-audio delay, model runtime,
acoustic accuracy, confidence calibration or cross-day identity was measured.
The hard 4,096 observation constant is a research reservation, not an approved
production lifetime cutoff. This WIP neither expands production schemas nor
changes production limits.
