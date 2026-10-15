# Observed results and qualification limits

These are previously completed test observations. Checkpoint preparation did not
execute or import product/helper/test code, run models, or access SQLite.

## Focused regression coverage

The capture candidate passed 20 focused model-free tests. Coverage includes
reproducing predecessor tail loss; retaining B after an unselected A-B seam and
emitting a selected B-C composite; all-unselected streams; exact exception
matching; hook identity, owner/reset/generation and revocation guards; current
and previous authority loss; malformed selected and unselected witnesses;
nonincreasing observation count; a distinct malformed terminal tail; invalid
native observations and coordinates; and selected composite authority rejection.
The tests use synthetic producer fixtures and in-memory authority/publication
doubles. They do not establish live database or model behavior.

The reporting correction passed six focused groups: a pure no-I/O projection;
two saved snapshots with historical `publication_pending` and zero current owner
work; independence of the current count from the historical reason; unknown for
missing/invalid counts; preservation of failure warnings; and use by the actual
optional-result method without rewriting raw health observations.

## Bounded real-audio mechanics observation

A 31.5-second fixture concatenated 11.5 seconds of synthetic silence with the
first 20 seconds of an accepted AMI input. Baseline and candidate each completed
three receipts, 14 segments, 13 turns and eight ASR batches. Their primary results
were exactly equal. Both owned servers shut down with no known live children;
application shutdown completed. No MOSS job was requested or accepted.

This fixture did not exercise the quiet branch. Both A-B and B-C selected seams
in both variants. It therefore did not demonstrate real-audio quiet-tail
continuity, and it is not natural silence-boundary evidence.

Both cells-list requests returned HTTP 503 at the original 150,000 SQL-step cap.
The observed count was 150,100; elapsed times were approximately 0.179 seconds
for baseline and 0.219 seconds for candidate. Cell listing failed before proof
downloads, so retained-cell proof acceptance and ordinary-cell geometry
equivalence were not established.

## Copied-state query diagnostic

The diagnostic completed on 2026-10-15 at 16:26 UTC in 0.511 seconds. On copied
state, the default page limit of 20 again interrupted at 150,100 SQL steps.
Four existing limit-1 pages returned all four cells exactly once, with
`has_more=false` on the final page. Each operation retained the same two-second
and 150,000-step limits and the same checks. Seam pages used 83,000 and 83,100
steps; ordinary pages used 10,600 and 10,400 steps.

This is synchronous copied-state diagnosis, not live HTTP or startup
qualification. It does not establish retained-proof acceptance. Pagination
optimization remains pending; no product optimization or SQL cap change is
included in this checkpoint.

## Limits

- This is a source checkpoint, not overall subsystem qualification.
- Focused model-free quiet-branch coverage passed; the bounded real-audio run
  was a quiet-branch nonreproduction.
- Exact primary equality does not demonstrate optional cell-list success.
- The new run's clean shutdown does not resolve the older soak's UNKNOWN cleanup.
- No 12-hour soak PASS is claimed.
- The original query failure does not establish the thresholds of older failures.
