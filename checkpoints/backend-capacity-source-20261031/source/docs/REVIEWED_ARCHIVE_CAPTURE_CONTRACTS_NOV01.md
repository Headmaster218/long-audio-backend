# Reviewed archive/capture regression contracts — Nov1

No product code changes. This review updates two stale regression bindings rather
than treating failing integrity checks as passes or substituting hashes blindly.
The prior failures reproduced on untouched public f5105d source, independently of
the Nov1 exact-support index fix. The frozen six-file 28ce7d backup is unchanged.

## Default v1 SQL contract

The old test hashed the source text of `_statements()`:
`76b990445e0f3249339d97b037b5c2bf7f00e8114833aa6d9560dc123e38aa42`.
Matching bytes survive in the Oct28/Oct29 source candidates. Current public code
parameterizes retained lifetime capacity, giving source-text hash
`02e66ce222d52ffb70d2d52b85ed402d3a6713aa84cf229c9b56ce7456f278fc`.

The reviewed fixture was independently extracted from the matching historical
function, not generated from the current function under test. Its 14 default SQL
strings equal current default output byte-for-byte. New controls compare all
strings at limits 1/2056/32768/131072 and allow only the one retained-outcome
threshold in the specifically named strict_outcome_admit trigger to differ.
All original selector/no-migration assertions remain. No schema migration, new
DDL, resource-budget increase, or loss of authorization predicates is approved.

## Capture source provenance and exact behavioral boundary

The prior whole-module pin was
`d9c2cdd81359c5ff5edc84b82a53498deba7a1649047c6f426450b774cd56539`,
matching Oct29 candidate05–12. The public 4e5712 manifest already explicitly pins
`f3215506ef85d5a546ee15e9a8379d4c1e83a38d3463414fd3019d1092c93c7f`;
f5105d inherits it. This review preserves a whole-file provenance assertion using
that independently verified public identity, and adds independently historical
AST digests for the exact `_payload_pending_ready` function and entire
`PayloadFrontier` class. Both ASTs are unchanged, including authority/order logic.
The original 16 predicate cases and detailed AST ordering assertions remain.
AST fingerprints use Python's AST representation; a future grammar-version or
source change needs review, not an automatic expected-value refresh.

The non-target whole-file differences were explicitly inspected: candidate13
adds packetless pending capture creation inside the original phase, publication
only after positive phase exit, and removal of stale/private draft/carrier fields
on failures/finally. Oct31 adds bounded connection priming and disables priming
for the automatic-intent branch. The existing prime helper intersects the original
budget, resets busy_timeout, and does not replay business statements. These are
not ignored because the target frontier AST is unchanged.

Existing generated packetless-witness and real-SQLite admission tests cover the
underlying behavior; new narrow boundary controls and separate Store-reopen tests
supplement those checks. The component frontier seam uses a synthetic authority
witness and the narrow capture tests use clearly identified mocks. Neither is
full Worker, native-model, process-restart, acoustic or endurance qualification.
No private recording, historical database, model, transcript or raw user data is
part of this review or its fixtures.

## Fresh result

The final selected archive/payload/hot-quota/store-quota/reopen/packetless/admission/
priming suite passed 141 checks; one Worker-thread handoff case was deliberately
not selected. The previous two stale failures now execute their remaining
functional assertions successfully. This is not the whole repository suite.
There are no production-code changes in this milestone. Source-pin changes were
reviewed against historical bodies and public provenance before regeneration.
