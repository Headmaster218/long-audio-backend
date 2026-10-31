# Fresh payload turn-witness format

This is a fresh-only format change, not a migration or an archive-adoption tool.
The dispatch policy remains `strict-primary-payload-v2`; its persistent format
marker, exact strict schema and archive format change together. The new archive
and marker format is `strict-payload-placement-v2-turn-witness-v1`; the witness
body version is `strict-payload-core-witness-v1`. New code must
refuse the prior live-payload schema before application schema changes. Prior
code must likewise refuse the new schema. No old database, packet, archive or
historical failed run is rewritten or adopted. This does not promise that SQLite
VFS recovery performs no I/O before application admission.

Retained/existing primary packets remain unchanged. Their turn projection
replays the original validator/ownership/Store arithmetic with its original
operands and parentheses. There is no epsilon, rounding, quantization or
alternate-expression fallback.

Fresh `empty` and `unknown_only` outcomes acquire a separate immutable per-core
witness, captured while the original native dictionary and owned PCM are live.
The original commit transaction binds actual turn IDs and, for unknown rows,
original captured primary fingerprints. These witnesses are not ordinary
primary packets or identity-attribution evidence. Empty text does not imply an
empty native turn dictionary. Mutable review/person state and independent
overlap flags remain snapshot data, not newly authenticated native claims.

Witnesses and strict outcomes survive original source/session erasure as audit
records. Their presence does not establish current source availability or
authorize cold-source resurrection after deletion. Original erasure, currentness
and reader checks still govern.

The focused generated control exercises the original Authority/Capture/Store
chain with synthetic numerical native/embedding inputs and an explicit receipt
lookup adapter. It does not run a native process, model, Worker, actor, HTTP
service or thread. A synthetic frontier admits two fixed component maintenance calls;
it does not qualify automatic Worker handoff. Collector-boundary fault doubles
are labeled as such, not as real disk corruption. Tests remain source-only until
the separately authorized command executes; source review is not a runtime PASS.

The existing 2,056 outcome limit, original budgets, payload bounds and source/hot
logical row retention remain. This change does not establish 12–48 hour capacity,
accuracy, resource peaks or production readiness.

The old-format control uses an embedded, source-SHA-labeled exact old strict DDL
catalog and original admission function with only minimal trigger-support tables.
It is not a full prior application database, migration experiment or old-data
compatibility claim. It needs no historical database or external candidate tree.
The existing v1/offline branches are source-preserved, not requalified by this
focused gate. A real generated unknown-primary mutation exercises the original
lexical-revocation trigger; restoring its exact row/inventory does not rewind
revision or change-feed side effects.
