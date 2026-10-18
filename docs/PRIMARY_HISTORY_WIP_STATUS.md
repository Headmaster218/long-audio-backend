# Primary history integration checkpoint 02

Incomplete development checkpoint against d71219ea9f4d504d5c9b9199083c9f1c701cc8ac. The parent preserves qualified dev50 and the accepted authority/accounting/retirement design and prototype evidence.

The standalone-primary producer, positive-ack reservation, fresh-operation cleanup, all-history comparator, shared new-policy MOSS route, and passive transcript adapter are now integrated. Two fresh synthetic end-to-end tests pass: three source-disjoint observations produce the unchanged conservative A/A link with a separate B competitor; two observations abstain. Normal HTTP transcript reads preserve all primary fields and return the exact corresponding saved statuses/IDs without starting work. See PRIMARY_HISTORY_GATE01.md.

This checkpoint is not a production release. Do not deploy it, open production databases with it, or enable its policy. Store initialization applies the additive schema and old-reader refusal marker even when the producer policy is off. Focused failure/restart, retirement/erasure, full schema/accounting, compatibility and concurrent progress qualification remain incomplete. The accompanying store/comparator/remaining integration tests are source-only except for the two specifically identified end-to-end nodes.

Known pending source corrections include compact source-reference accounting, precise release of unknown invalidation fences, and a final-transaction retirement path-alias census. Manual test threads establish lifecycle/scheduling semantics only, not concurrent CPU/GIL latency or sustained-capture progress. Dense upstream provenance quotas still limit long recordings. No model or real audio ran. Legacy thresholds and the 32-node legacy path remain unchanged.

Known baseline regression remains separate: CaptureFixture.test_seam_primary_projection_budget_declines_without_widening_limits exhausted 150100/150000 VM steps before this slice. Its assertion and SQL cap were not weakened.

After the two-node gate, one unexecuted store-test portability change replaced a machine-specific legacy source path with the required PRIMARY_HISTORY_LEGACY_LINK_STORE_SOURCE environment variable. The supplied file must still match the exact original byte count and SHA256; absence fails the test. No product or executed integration-test bytes changed.
