# Finite history accounting addendum

Design-only accounting proposal that complements `PRIMARY_HISTORY_AUTHORITY_DESIGN.md` and `PRIMARY_HISTORY_SOURCE_RETIREMENT.md`. These are explicit resource envelopes for review, not implemented schema, approved increases or measured new-store performance. The original authority design remains unchanged.

Source baseline: `Headmaster218/long-audio-backend` commit `b13c6fc4f1e5f3d9f86b68a222d4cab3c78101de` (operational baseline `49cd724dda5595aee29eaf1d7da2f41739b43d08`). Comparator evidence is retained under `prototypes/history_comparator/`; it does not qualify production resource use or identity accuracy.

## 1. Ownership and per-attempt maxima

An **attempt** has one immutable outcome row. Successful vector certificates point to one shared immutable **evidence basis** for that attempt; an automatic MOSS attempt may produce at most four certificates. A standalone primary attempt produces at most one. A failed attempt retains only its bounded vector-free diagnostic, not a successful certificate. Unknown invocation keeps the existing containment state; row capacity never licenses reinvocation.

A primary basis has exactly one reference to an existing immutable primary packet (body <=65,536 bytes), one copied source Receipt (<=1,024 bytes), one source dependency, one recording-mapping dependency and at most 128 exact primary-ref dependencies. A packet with more refs is refused; there is no truncation.

A MOSS basis has at most one copied immutable typed proof object (<=524,288 bytes), at most 128 distinct source Receipts, at most 4 references to existing primary packets whose combined body size is <=65,536 bytes, at most 128 source dependencies, 128 recording-mapping dependencies and 512 primary-ref dependencies. These are union counts across the complete basis, not limits per node. Require all authority dependencies to fit; otherwise refuse the whole basis. The limits follow the existing 128 source-part ceiling, 4-packet/64 KiB primary read ceiling and 128 rows per packet, and are conservative caps on the new format. Source and mapping objects have exact typed keys; no acoustic label or vector is substituted for a dependency.

Certificates reference the basis, not independent copies of its proof, receipts or dependency graph. Objects are deduplicated only within a recording by type plus exact canonical digest; byte inequality under a matching key refuses. A receipt object's reference count is bounded by the charged basis rows: the proposed 64 MiB recording budget and 1,024-byte basis charge imply an upper bound of 65,536 references before all other charges, not an additional lifetime product limit. A committed successful basis has one owning attempt and 1–4 certificate references (exactly 1 for standalone primary). No zero-certificate basis is committed. A basis cannot outlive its attempt, and an attempt cannot be removed while the recording survives. Successful bases and dependencies are immutable and remain retained even if authority is fenced. No ordinary eviction, refcount decrement or competitor removal occurs. Explicit session erasure removes all edges and then owned objects transactionally with usage accounting. External primary packets remain owned/accounted by their existing ledger and can only disappear through that same erasure path. Missing objects or refcount/edge disagreement fences the scope.

## 2. Exact logical charges

Use integer byte charges, with UTF-8/canonical encoded lengths, checked before insertion. The following constants deliberately include a fixed identifier/index allowance; they are logical accounting, not a claim about SQLite's physical page usage:

- Recording header: 32,768 bytes, covering policy/space/scheduling/revision/status metadata; any encoded header beyond its specified component bounds refuses.
- Attempt row: 1,024 bytes; sanitized canonical diagnostic/outcome <=512 bytes. Successful and failed claimed attempts both consume this charge. Skipped live cores/occupied-slot polls update only fixed saturating counters in the recording header; they do not create rows. No free unbounded failure log.
- Certificate:len(canonical header) +2,048 +1,024 bytes. Header is 1–2,048 bytes; vector BLOB is exactly 2,048 bytes. Thus maximum 5,120 bytes.
- Shared basis row: 1,024 bytes; canonical basis header <=512 bytes.
- Copied proof object:len(proof) +512 bytes, maximum 524,800.
- Copied Receipt object:len(receipt) +256 bytes, maximum 1,280.
- Basis-to-receipt edge: 256 bytes each.
- External primary-packet reference edge: 512 bytes each, including the bounded packet identity/digest locator; the packet body is charged by the existing primary-provenance ledger, not counted twice in this new ledger.
- Typed dependency row: 512 bytes each, with canonical typed key/current-fact digest projection <=256 bytes. Separate source, recording-mapping and exact primary-ref keys support indexed destructive-change lookup.

An object is charged once when first inserted; every reference edge is charged. Certificate success, new object/edge insertion, usage updates and reservation conversion are atomic. Refcount changes equal the number of inserted/deleted edges exactly; counters use signed 63-bit nonnegative integers and overflow or disagreement rolls back. No cross-recording dedup complicates session erasure. At reservation, charge the known prospective upper bound, including all eventual vectors/certificates, against shared aggregate usage. On acknowledged final append convert reserved charge to exact retained charge; on acknowledged terminal failure retain its 1,024-byte attempt charge and release unused reservation. Unknown reservation/append outcome is resolved by exact token; do not release possibly durable charge or infer again. Existing 8 MiB global identity staging and 512 KiB crop staging remain separate admission ceilings; comparator RAM is a separately reviewed reservation.

For a primary basis with r primary refs, no dedup and maximum header/receipt sizes, the new retained charge is:

Cprimary(r) =1,024 +5,120 +1,024 +1,280 +256 +512 +512(r+2) =10,240 +512r bytes.

At r=128, this is 75,776 bytes, excluding the already charged original primary packet. A maximum four-certificate MOSS basis costs:

1,024 +4×5,120 +1,024 +524,800 +128×1,280 +128×256 +4×512 +768×512 =1,139,200 bytes.

Those are hard per-attempt logical maxima, not expected sizes. At 64 MiB per recording, pathological proof/dependency sizes can saturate with far fewer observations than the sizing example. The same recording/global budgets include primary and MOSS producers, failed attempts, reservations, proofs, edges and decision projections. Existing legacy identity scopes count toward the proposed 128 MiB old-plus-new aggregate. The independent existing primary-provenance budget remains enforced. These proposed 64 MiB/128 MiB envelopes require explicit review; no automatic increase is authorized. The 4,096 case mentioned in the earlier architecture is only a comparator/prototype envelope, not a production lifetime limit or permission to forget competitors.

## 3. Provisional decision storage envelope

A compact comparator must publish paged, revision-bound projections, not a larger monolithic 64 KiB JSON. For accounting review only, reserve:

- One snapshot header:at most 65,536 encoded bytes +1,024 charge.
- At most N annotation/membership rows:encoded row <=512 bytes +256 allowance, maximum 768 each.
- At most N−1 merge-DAG rows:encoded row <=128 bytes +256 allowance, maximum 384 each. Store child IDs, not repeated full membership lists.
- At most N round summaries:encoded row <=512 bytes +256 allowance, maximum 768 each.

Maximum snapshot charge is 65,536+1,024+1,536N+384×max(0,N−1) bytes; for N>=1 this is 65,536+1,024+1,920N−384. At N=2,884 it is 5,603,456 bytes (5.34 MiB). Keep at most a current/historical snapshot and one building snapshot, both charged; no third until obsolete projection cleanup completes. Observation/proof history remains immutable. A final revision CAS alone selects the current snapshot. Reader pages are <=32 rows and <=128 KiB including envelope, with preflight lengths; actual payload limits may force fewer than 32 rows. The prototype's reduced metadata/trace representation must demonstrate this envelope before production DDL review. A large textual trace is not silently omitted while a positive decision is published; unsupported projection capacity remains pending.

## 4. A grounded 2,884-observation storage estimate

Aggregate length, reference-count and page statistics were measured from a sealed prior audit database using read-only immutable SQLite access, with its no-WAL state and SHA256 verified before and after. These are saved aggregate measurements, not a new identity experiment or an independently repeated database benchmark. Its 47 existing primary packet bodies measured 1,601–4,336 bytes, median 2,859; exact primary refs per packet were 1–9, median 3. Existing primary-ledger row charges were 2,089–4,824 bytes, median 3,347. This is one exposed 500.28-second case, not a representative distribution or new identity/model result.

The current primary-packet table plus its indexes/usage occupied 221,184 bytes in that small database, about 4,706 bytes per packet including fixed small-table overhead. A linear extrapolation of that measured allocation is approximately 12.94 MiB for 2,884 packets; fragmentation, denser or larger rows and fixed-index amortization change it.

Define P as standalone-primary certificates, K as successful MOSS attempts and M as their certificates, with M<=4K. Total stored observations N=P+M, before any exact alias collapse; failures consume attempt rows without adding vectors. Repeated audio and duplicate typed nodes still consume their physical rows/bytes. Collapse affects comparison groups only. The benchmark's exact 2,884-row case and this 2,884-primary sizing example are not a universal 48-hour total-capacity guarantee. MOSS proof/basis/receipt/dependency charges are added per actual attempt, shared only when their exact objects match; decision charges use total N.

With P=2,884, M=0, one successful primary observation per attempt and no dedup, the new ledger's maximum-header charge using the observed 1–9 ref range is 29.57–40.84 MiB (32.39 MiB at 3 refs). Two maximum decision snapshots add 10.69 MiB; the recording header adds 0.03125 MiB. Original selected packets add 5.75–13.27 MiB in their existing logical ledger. Thus the accounted selected-evidence footprint is about 46.04–64.82 MiB across the two ledgers, even though vectors alone are only 5.63 MiB. This excludes the separate verified-retirement addendum's source-state/marker charge, at most 2,048 bytes per distinct source. If all 2,884 observations use distinct subsequently retired sources, add 5.63 MiB, giving about 51.67–70.46 MiB combined logical selected-evidence charge. MOSS, failures and unselected primary packets add separately. If existing provenance continues at every 10.5-second core, 48 hours permits about 16,458 packet opportunities rather than 2,884. The extra 13,574 packets would add about 43.33 MiB logical at the observed median charge, or about 60.92 MiB using the small saved DB's observed physical allocation per packet. This is another extrapolation, subject to existing provenance quotas and packet qualification; the selected-history estimate excludes it.

For physical main-DB planning, a **50–110 MiB incremental range** for these 2,884 selected observations is reasonable to test, not a guarantee: it combines roughly 40–52 MiB of new logical charge (already padded for keys/indexes) with 8–24 MiB for native packet pages, and allows up to about 50% additional new-table page/index slack. A cold new-store layout, worst-case metadata, long-lived readers, WAL growth and fragmentation require measurement. WAL/checkpoint work plus one operational backup may temporarily require roughly three times that incremental main-DB amount (150–330 MiB); this is a planning allowance, not a physical cap or permission to bypass the existing free-space floor. Actual full database/backup size includes all transcripts, provenance and other recordings.

Worst-case 128-ref primary packets cannot fit 2,884 observations in 64 MiB: new logical charges alone would be 208.41 MiB, before decisions or primary packets. The system must report saturation; the illustrative opportunity count is not an admission guarantee. MOSS max-sized bases consume the same remaining bytes and can reduce primary admissions sharply. Source-state/retirement rows also consume those same quotas; the 50–110 MiB physical planning range above excludes them, so their actual additional pages must be added rather than treated as free.

Finally, original-source retention dominates this estimate: 48 hours of 16 kHz mono 16-bit PCM is 5,529,600,000 bytes, approximately 5.15 GiB, before container/duplicate-work copies. For active sources, restart validation still needs original uploads available; missing originals without an authenticated retirement record prevent positive revalidation. A separately reviewed verified-retirement marker can allow already admitted certificates to survive authorized cleanup without a live-file claim. The separate verified-retirement contract defines that transition; this accounting addendum does not assume it is implemented or count uploaded audio inside the small identity-history estimate. Owned 2-second crops are discarded after their bounded operation, not accumulated as history.

## 5. Held verification

Model-free checks must cover exact charge arithmetic at all maxima, shared-object dedup/refcounts, atomic reservation-to-retained conversion and unknown acknowledgements, mixed old/new quota exhaustion, maximum dependency refusals, deletion accounting and zero orphan proof objects; then a separately approved future SQLite page/WAL/backup sizing run. The released 2,884-row comparator prototype did not authorize any new-schema/database benchmark; such a run requires its own approval regardless of size. No DDL or benchmark was executed for this addendum. Full schema, predicate/index widths and page-layout measurement remain implementation gates. The original authority-design file remains unchanged.
