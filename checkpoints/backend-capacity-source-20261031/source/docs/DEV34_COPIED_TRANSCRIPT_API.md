# dev34: models-disabled API readback of a copied real database

Version: `0.2.0rc3.dev34`. This check exercised the installed package against
retained real result text, metadata, proof and binding combinations through
local GET endpoints. It supplements the earlier 214 model-free tests and
synthetic geometry integration; it does not rerun inference or change the
historical model-run outcome.

## Scope and copy integrity

The database copy was initially byte-identical. The original database files
were checked by binary hashes and never opened through SQLite. Only the copy
was opened. Its startup-required path relocation changed 174 chunk paths and
60 recording-source paths; no cleanup-row paths changed. The destination upload
directory was empty: no audio or native-cell files were copied or read.
Ordinary startup migration/recovery was allowed only on the copy.

Before/after comparisons preserved archived result, metadata, proof and binding
bytes, text, identities and geometry. Row comparisons excluded only the declared
path columns and the schema-added session revision counter. The installed
package and original saved inputs remained unchanged. Worker, model, automatic
execution and optional processing paths were disabled and checked throughout;
new model attempts were zero.

## Retained first failure and corrected result

The first API test exited with code 1 before any transcript request. Its history
paginator assumed four pages would suffice. The existing 32 KiB response limit
correctly returned six rows per page despite a requested limit of 20, leaving
24 of the first session's 36 rows read. This was a test-harness pagination
failure, not a transcript result or product-capacity failure. Its evidence and
clean shutdown remain preserved.

The paginator was corrected to use the exact saved row count as its finite
page bound, with strict cursor progress, duplicate/count checks and exact final
membership. Twelve offline paginator cases passed. One corrected API attempt
then exited with code 0; product and package bytes were unchanged.

## Verified GET results

- 756 GET attempts: 749 HTTP 200 responses, four expected HTTP 409 responses
  confirming disabled live execution, and three connection refusals before the
  listener became ready. All 753 received response bodies were hash-checked.
- Eleven history-list GETs recovered all 52 retained jobs. All 104 history
  detail/result response bodies matched the saved bytes exactly.
- Thirty distinct transcript pages across four sessions were read forward and
  in reverse order: 60 page responses, byte-identical between orders. All 321
  baseline primary units were accounted for, either retained or replaced whole.
- All 573 evidence GETs matched their exact references and canonical primary or
  retained phrase/result payloads. Full phrase text retained unique home pages.
- The actual stored data selected seven jobs in four runs, totaling 83 seconds
  of support and replacing 52 whole primary units across six pages. Exact
  selected identities and run geometries matched the prior numeric expectation.
  Optional closure was complete on all 30 pages.
- Of 52 candidates, 43 were eligible: seven were selected and 36 remained
  unselected. Nine were parse-unresolved and ineligible. This is the combined
  alias-placement and component-projection result, not an isolated page-boundary
  improvement or a measured accuracy gain.

## Resources and shutdown

The successful attempt took 4.451655 seconds. Peak sampled combined test/server
RSS was 104,493,056 bytes; peak new output was 20,680,704 bytes. The retained
combined footprint was 35,028,992 bytes, including the first attempt and the
final review.
The largest transcript response was 115,502 bytes, below the unchanged
262,144-byte cap; received HTTP bodies totaled 4,139,450 bytes. No logs were
discarded and no resource violation was reported.

Bounds were 300 seconds total, CPU index 3, nice 19, 1 GiB address space,
512 MiB sampled combined RSS and 128 MiB combined artifacts. Maximum sampling
gap was 0.282651 seconds. The owned server received intentional SIGTERM, completed
lifespan shutdown and was positively waited; its return code was -15. No owned
survivors remained.

## Qualification limits

This qualifies models-disabled GET readback from a path-relocated, recovered
historical copy. It does not establish current audio/source authority, live
restart or power-loss recovery, recognition accuracy, complete content, EOS or
global speaker identity. The 36 eligible unselected jobs remain outside the chosen reading, but their
retained timed phrases remain available through alternatives and exact evidence
on these complete, under-cap pages. The existing dense-component capacity limits also remain; this
successful under-cap replay does not remove them. See the
[current unified transcript contract](UNIFIED_TRANSCRIPT.md). The
[historical mixed-stream report](DEV32_MIXED_STREAM_INTEGRATION.md) is unchanged.
