# dev51 fixed500.28-second primary-history trial

Date: 2026-10-18. Executed source: 9d3fe6fdb9c3ecc9d3bcc0f2e352b296c5f2a7fc
(tree ff622f23a15ef08a7b4b42cc04a82c747eee1b78), version 0.2.0rc3.dev51.

## Result

The single approved trial preserved **exit 1, functional_success=false, and
inconclusive identity**. The runtime worker and post-seal scorer each exited 0;
those process exits do not turn the functional result into a pass. No retry ran.

All 500.28 seconds (8,004,480 frames at 16 kHz) completed in 48 primary chunks. The
saved typed export contains 48 primary packets. The raw score's
primary_packet_count=0 is a legacy MOSS-batch-derived field; it does not count
those 48 rows. This is exposed same-file evaluation, including a previously
exposed first 84 seconds, with no held-out, real-person identity or WER claim.

## Independent history and identity

There were 11 natural primary claims, below the 13-claim bound: 5 successful vector
outputs with 5 unique crop hashes, and 6 failure terminals:

- Four: dispatch_validation / other, error_type=other,
  child_dispatch=not_attempted.
- One: prepare / optional_cancelled, error_type=AdmissionError,
  child_dispatch=not_attempted.
- One: storage_unknown; stage and dispatch status were not recorded.

The prepare / optional_cancelled AdmissionError does not prove user cancellation.
Eleven claims are not eleven physical model executions. The four sanitized
other failures do not establish their underlying actual exceptions.

The durable scope settled at unknown_rival, with all attempts terminal, zero
reservation/staging bytes, no build pending and no current decision. This is
conservative terminal abstention for the saved snapshot, not an active comparison.
The public live/reopen payloads nevertheless showed pending / links_stale,
revision 22 / decision_revision=-1; their scope payloads omitted the reason.
No decision, merge or round rows were published.

Three independent policy-testable same-speaker pair opportunities remained
unlinked. There were 0 linked pairs, 0 correct links and 0 false links, with linked
pair and scored-cluster denominators both 0. Empty denominators are unmeasured;
these zeros do not demonstrate perfect precision or a zero error rate.

## Fresh-process reopen

The models-disabled fresh interpreter had a different history boot identity.
Text, evidence, history and results were conserved; all 20 stable-table hashes
matched. The passive-history predicate failed because a current decision was
already absent before restart. The expected fresh-boot historical-source branch
requires such a decision and was never reached. This does not demonstrate reopen
data loss, nor grant current speaker identity authority.

## Bounds, evidence and cleanup

The recorded total was 433.862 seconds. Service/native work used shared CPU 0–1,
nice 19, GPU disabled; the production comparator's CPU 1 is shared, not dedicated.
Peak sampled owned RSS was 911,224,832 bytes; charged RSS including fixed reserves
was 2,253,402,112 bytes within 6 GiB. Peak output was 61,632,512 bytes within 128 MiB.
The 1500-second work / 120-second cleanup limits were respected; the clipped 720-second
post-primary allowance remained inside work. Outer timeout was not exercised.
Aggregate CPU time and the observed maximum sample gap were not recorded.

Evidence used 4,829,184 stored allocated bytes within 8 MiB. Retained decoded logical
evidence totaled 6,938,076 bytes; all observed response bytes including repeats
totaled11,008,144. The audit verified 368 gzip members and 1,181 observations, exact
retained member ranges/hashes, contiguous ledgers and terminal counts. Stable
health deduplication keeps a representative body plus each observation's digest
and size; not every changing volatile body is recoverable. The seal covers 104
files / 59,893,990 logical bytes. Separate post-run owner audit allocation is 32,768
bytes; the complete preserved preparation/deployment/runtime/audit root occupies
64,897,024 allocated bytes.

Original owned handles were positively waited; no observed family remained and
no forced kill occurred. Both service exits were -15 with owner-sent SIGTERM,
pinned Uvicorn and completed product-lifespan receipts. Cache reservation after
closure was 0; no missing comparator protocol ACK is invented. These are sampled,
namespace-scoped ownership conclusions, not host-wide or continuous-peak proof.

## Diagnosis limits and preservation

Source inspection establishes a possible _ReadDeferred handling gap: automatic
before_window handles the read-only BUSY/LOCKED marker, whereas primary
_run_primary can terminalize it before ProbeOperation through other/other
sanitization. This does **not** prove the causes of the four actual failures;
per-attempt exception correlation is absent. storage_unknown remains unresolved.
No product correction or reproduction is claimed.

The fixed input, thresholds and scoring rules stayed unchanged. RTTM was used
only after positive cleanup and the runtime seal. Raw failure, score, source and
all actual artifacts remain preserved. This report performs no execution or
publication and does not qualify dev51 acoustic identity.

Independent terminal review SHA256:
a6879b8adec57e5c2d5a5ae828b7a5165f1cbe1471f15145376fca649c525e59.
