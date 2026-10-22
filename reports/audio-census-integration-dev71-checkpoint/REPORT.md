# Sampling-census transaction hooks and real Worker integration

Development checkpoint based on published commit
`c2094c1813392801f3e74b2fa767bff269108da7`. This adds recoverable source and
bounded test evidence under reports only. The root product, defaults and active
banks are unchanged; no merge, release or feature activation is performed.

## Source scope

The seven-file delta is relative to the dev70 stage-2 revision-03 census
candidate. It contains three product files and four test files. It adds bounded
same-snapshot HistoryStore observations and manager maintenance linkage, repairs
observer capture noninterference, and includes six real Worker/database
integration cases. The authoritative source payload and full unified patch are
both included. No full-source binary archive is required.

History method wrappers preserve the original invocation/return/exception.
Intention precedes the method; acknowledgement follows the complete original
method, including database context exit. Fixed scalar facts are copied from
existing reads or SQL assignment inputs into a bounded buffer, without extra
SELECTs or sidecar I/O in the transaction. Intended post-state is not a reread
of committed state. Errors or uncertain acknowledgement remain unknown.
Manager maintenance retains the original opportunity binding rather than
assigning delayed work to the newest window.

The first implementation evaluated some observer-only capture arguments before
entering the exception-isolated helper. Wider tests exposed two new failures.
The repair makes field production lazy and synchronous inside the capture guard,
including scope fields and counter iteration. Inactive/failed observers do not
read those mappings. Capture failures invalidate observation without replacing
the original product return or exception. No product deadline, SQL, identity
permission or sampling threshold was widened.

## Integration fixture and preserved timeout

The first six-case real Worker integration gate ended with timeout exit 124 at
180 seconds after four progress dots. Those dots are not four completed-case
proofs, and that gate did not prove cleanup. Static investigation found a
fixture scheduling gap: the fixture requested an automatic handoff but did not
pump that opportunity before attempting its next primary span.

The separate revision-02 fixture uses the real production `automatic_turn`
context to close the first scheduling opportunity while preserving the old
pending probe. It adds a 30-second delayed-case cancellation deadline through
the existing primary-span stop argument. It does not change the production
scheduler, reset reservations, relax limits, or discard the old probe.

The revised gate completed **6 passed, 1 deprecation warning in 44.32 seconds**,
with normal real lifespan/probe cleanup assertions. It exercises real Worker,
manager and SQLite paths on bounded synthetic audio, not real inference models.
Normal-case cleanup assertions do not prove arbitrary teardown after a kill.

## Root-run evidence, with failures retained

These suites overlap; their counts must not be summed as independent controls.

- Initial stage-3 controls: **56 passed**, 6.65 seconds.
- Initial wider selected regression: **105 passed, 4 failed, 10 deselected**,
  18.83 seconds. Two failures exposed the capture bug above; two came from an
  inherited terminal fixture missing startup-recovery-ready state.
- Repaired focused controls: **94 passed**, 8.85 seconds.
- Separate terminal-fixture adaptation, on fresh source copies only:
  **2 passed** on the baseline (1.40 seconds) and **2 passed** on the candidate
  (1.43 seconds). It adds only `primary_recovery_status="ready"` to the existing
  stub. The unadapted failures remain recorded. The patch is included separately
  and is NOT applied to this checkpoint's source payload.
- First six-case integration attempt: **timeout 124**, 180 seconds; cleanup not
  proven. Revised integration: **6 passed**, 44.32 seconds, as described above.
- Final selected production regression: **107 passed, 10 deselected,
  1 deprecation warning**, 21.12 seconds. This ran against repaired stage-3
  revision-02 source; the combined integration candidate has the same product
  bytes and adds the revised integration test module.

The last regression explicitly excluded four named functions. Two recovery
fixtures lack manager `jobs`; the contention fixture lacks the current
maintenance/jobs interface; a real-app multi-page-drain case remains a separate,
unrun gate. Ten parameterized cases were deselected. The terminal module was
not part of that 107-case run. REPRODUCE.md records the exact selection.
These results are not a complete suite, package qualification or real-model
qualification of the new census hooks. Earlier dev69 broad-gate failures are
not superseded by these different selected suites.

## Fresh AMI acquisition

The earlier official-endpoint HTTP 503 is preserved in dev70. A later acquisition
succeeded and recorded fresh identities for EN2002a and EN2002b audio plus the
manual annotation archive. Audio bytes total 125,745,924; the archive is
22,887,865 bytes. This checkpoint includes only URL/size/SHA256/format/license
receipts, not WAVs, the ZIP, annotation contents or labels.

The annotation archive was not inspected and labels were not opened. Historical
byte identity with the lost prior dataset is NOT proven. Acquisition is not an
acoustic evaluation, and the earlier dev68 inconclusive trial remains unchanged.

## Still incomplete

Census eligibility remains **UNKNOWN**, and there is no COMPLETE census claim.
Earlier lifecycle/preflight gaps, complete opportunity identity for no-claim
windows, immutable stopped-snapshot reconciliation, health/stop supervision,
terminal classification and broader crash/interleaving/restart coverage remain.
Actual full-model execution with these new hooks has not yet been tested. The
older successful runtime smoke used dev69 source, not this census integration.

The source preparation documents retain their original not-executed statements;
this report records later parent-run outcomes. Source recovery and selected
synthetic integration passes do not establish sampling completeness, acoustic
accuracy, stable cross-day identity or self-learning acceptance.
