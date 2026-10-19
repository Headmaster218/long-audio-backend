# dev55 AliMeeting primary-only trial: partial result

Date: 2026-10-19 (UTC). Documentation base: `f337a041fc4476d68e556901d3bfb7abaed90ba5`. Product: the unchanged installed dev55 build, commit `cf3f4af752f035cad45d3c697e63d4ef4f52a07f`.

## Outcome and fixed scope

The trial did not complete the meeting and establishes no transcription or speaker-identity quality result. The fixed input was AliMeeting meeting R8001_M8004, channel 1: 1,573.85 seconds, 25,181,600 frames at 16 kHz. MOSS was disabled; production thresholds, ownership controls and the normal uploader were unchanged.

Two separately approved actual invocations were preserved:

1. The first stopped before session creation, upload or inference. The observer failed at an HTTP response EOF boundary; that helper diagnosis was reproduced offline using a standard-library HTTP response. The original response framing headers were not retained, so their exact historical values remain unknown. A narrow EOF correction was independently reviewed and tested offline.
2. The fresh invocation performed partial inference, then stopped when the evaluation helper rejected a Python-child owner/executable relationship. The rejected child's identity was not saved. Its historical cause cannot be determined from the retained evidence. A subsequent saved-summary failure was a separate helper defect: a zero-length witness slice sharing a start with a positive slice was incorrectly required to match the advanced stream prefix.

The witness checker was corrected offline to bounds-check every slice and tile only positive ranges. Its existing synthetic method passed, including shared starts, token-order permutations, gaps, overlaps and bounds failures. One preceding test launch failed before loading tests because of inherited hard resource limits; that failure was preserved and the launcher-only correction was separately reviewed. The original failed actual results remain unchanged.

## Conserved partial progress

- 882 seconds were materialized as three pages, nine sources and 84 work rows.
- 588 seconds were page-published. The third page remained pending; EOS was not reached.
- 40 work rows completed with exact terminal offsets, covering the first 420 seconds. Another 16 rows were queued and 28 were staging. The fixed full-meeting target was 150 rows, six pages and 16 sources.
- The stopped snapshot retained 252 primary segments and 41 primary provenance packets. These counts are not accuracy measures.
- Ten history attempts produced ten observations: seven retained vectors and three missing candidates. There were 15 input witness rows, no pending attempts and no stored decisions.
- The stored scope was `unknown_rival`, with no retained history reservation or comparison-build ownership. Missing candidates remained in the accounting.

The three bounded failure descriptors all recorded `AdmissionError`, `optional_cancelled` and `child_dispatch=not_attempted`. Two recorded `prepare/ownership_changed` with sample age 0 ms; one recorded `dispatch_validation/stale_generation` with sample age 2 ms. Each recorded reservation held, zero wait, no grace use, and null phase/cause. Source tracing places these refusal paths before input submission, outside dev53’s running-only correction. Failure observation 1 precedes successes 2–7; failure 8 precedes success 9. These earlier refusals cannot be explained solely by final shutdown. Failure 10 has no later success or saved attempt timestamp, so its timing remains unknown. The health snapshots omit the missing per-attempt timing and generation details. `optional_cancelled` is guard normalization, not proof of external cancellation. No exact changed fingerprint component, generation writer or spurious historical refusal is established; the separately rejected live child’s identity and cause also remain unknown.

A single model-free saved-summary recovery succeeded after the checker correction. Exact all-table, all-column, all-row conservation across 65 tables matched the original stopped backup before and after recovery; the snapshot's byte digest was unchanged. The recovered summary still failed nine full-capture coverage checks, as expected for this partial run.

## Limits of the evidence

Reference labels were not opened. Speaker-identity scoring, DER and CER remain unrun. The summary's `identity_scoring_available=true` means only that stored geometry/alias inputs were mappable. Published-link and policy-eligibility interpretation remain unavailable, and adapter `links=null` is not a measured zero-link result. No fresh production passive-reader traversal was performed.

The inference invocation lasted about 380 seconds. Peak sampled RSS was about 778 MiB and peak sampled charged memory about 2.20 GiB, within the 6 GiB charged-memory envelope. The largest retained sampling gap was about 1.32 seconds. Five sampled probe-process sightings and two comparator sightings are incomplete census observations, not total physical dispatch counts.

The original server and uploader handles were positively waited to completion; server lifespan shutdown completed and the observed family cleared. The missing rejected-child identity limits causal and census claims. The planned output budget was 512 MiB, with a sampled 384 MiB live-stop threshold and 128 MiB headroom. This was not an instantaneous aggregate limit or a hard filesystem quota. Final actual-output allocation was about 77 MiB. The bounded offline correction and recovery evidence occupied under 2 MiB of its combined 8 MiB allowance.

No additional inference, service restart, heavier passive recovery or reference scoring was performed. This report documents partial execution and recovered evidence; it does not turn the failed live trial into a successful full-meeting evaluation.
