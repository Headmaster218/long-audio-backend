# Model-free integrated history slice: concrete review freeze

Parent: a8e4c90ee9190a05f8606df54f38db2cd0f146ce, tree bf5eecc7c5c1576e62dc8df1f0f4842165315564. Product baseline remains immutable dev50. This concrete design defines the scoped source implementation. Exact SQL/source and executable qualification remain incomplete; no feature qualification is asserted.

## Persistent ownership

Nine new exact-schema tables, each suffixed `_v1`, installed in the existing transaction after primary fields/provenance exist. Existing evidence is neither rebuilt nor backfilled.

1. `recording_history_scopes`: scope sequence PK, global accounting sentinel 0, unique session/recording. Immutable policy/space, fixed sampling profile, retained/reserved charges, sampling high-water/counters, history and destructive-authority revisions, current/building pointers and bounded status. Global/recording usage is here rather than another table.
2. `recording_history_attempts`: token PK, scope FK, unique typed origin identity, state/reservation/exact charges, <=512-byte diagnostic, nullable <=512-byte successful immutable basis header. Basis and owning attempt share one lifetime/atomic transaction.
3. `recording_history_certificates`: scope/sequence PK, owning attempt FK, unique certificate digest, <=2048-byte canonical header, exact2048-byte original float32 vector/digest/charge. Immutable competitors.
4. `recording_history_objects`: scope/type/digest PK, receipt <=1024 or proof <=524288 bytes, exact digest, edge refcount/charge. Recording-scoped dedup.
5. `recording_history_refs`: attempt/kind/key PK, owned object or exact external immutable-primary-packet locator/digest. Separate typed edge charges; packet body remains charged to its own ledger.
6. `recording_history_dependencies`: scope/kind/key/attempt PK, bounded256-byte current-fact projection. Source, mapping and exact primary references indexed for selective destructive fencing.
7. `recording_history_sources`: scope/version PK, chunk key, receipt/path/mapping digests, active/retired/revoked, source revision/count/required-end. Immutable retirement marker <=1024 bytes/token and bounded cleanup state live in this same source lifetime.
8. `recording_history_decisions`: scope/slot PK, slot0 or1 only, snapshot/history/authority revisions, <=65536-byte header, complete flag and charge. Current/historical plus one build only.
9. `recording_history_decision_rows`: scope/slot/kind/ordinal PK, exact typed ref, <=512-byte row (merge<=128), charge. Keyset pages and exact-reference index avoid monolithic decisions.

Indexes: scopes(session,scope); partial dirty(scope); attempts(scope,state,token); unique typed attempt key; certificates(scope,attempt,sequence); refs(scope,type,digest); dependencies(kind,key,scope,attempt) and(attempt,kind,key); sources(chunk,scope,version); decision_rows(scope,slot,ref_kind,ref_key,ordinal). The primary keys provide source and page seeks.

Immutable-success fields, certificate/proof/edge/dependency bytes and marker fields reject mutation. Evidence deletion requires existing session tombstone. Bound chunks identity/path/digest/duration/timeline/session changes, progress regression behind required-end, cancellation; bound stream/recording-work mapping mutations; bound recording origin/session/cancellation; exact primary row mutations and source-mismatch notifications atomically increment destructive authority or roll back on overflow, clearing current decision. Ordinary outside-bound append and resource GC do not fence.

An additive nullable marker column on the existing exact-schema legacy batch table forces old reader startup refusal. New code recognizes exact legacy/new forms; no rebuild. Both writers enforce legacy global retained bytes plus new retained/reserved bytes <=128MiB. New per-recording limit64MiB. Accepted accounting addendum constants apply unchanged, including source512 and retirement len+512. Shared8MiB staging also aggregates old and new staging; legacy retained already includes its reservations. No global observation-count cap.

## Fixed opt-in profile and acknowledgement

Policy `primary-clean-history-v1`, off by default. First120 recording-source seconds admit at most6 opportunities separated by10 source seconds; afterward60-second source buckets. Monotone high-water prevents missed/occupied buckets replaying. Interval option default60, immutable once recording scope exists. Existing2-second clean crop/100ms erosion remain unchanged. One64000-byte pending crop independent of MOSS live slot. Only positive primary acknowledgement permits atomic scheduling claim/reservation. Unknown primary/claim/reservation/append acknowledgements grant storage-only closure, never inference or audio replay.

Private manager success capability is bound to the exact registered manager and authenticated operation/result/cleanup state. No public cleanup boolean authorizes certificates. Live source, packet, mapping, scope and durable source-active checks repeat at reserve/dispatch/append. Normal analyzer/cohort/CellLease cleanup is harmless. Positive native cleanup precedes atomic certificate/proof/accounting conversion. Terminal success/failure bytes freeze before first submission and exact bytes are reused after unknown acknowledgement.

## Comparison and fairness

One tracked on-demand manager task and one snapshot/cache. Proposed resource grant: one CPU lane and<=128MiB owned memory; initial grant only between primary/optional inference intervals, with an explicitly accounted cooperative tail after primary arrival: <=50ms compute or up-to250ms bounded read phase. Primary admission never waits. This is not strict non-overlap or qualified production four-lane coexistence. Retained memory remains charged until actual release. Primary pressure can discard recomputable cache only. Sustained-capture throughput is unqualified until bounded progress between finite cohorts is measured; this must not be portrayed as an EOS-only or starvation-permitting production policy.

Back-to-back<=50ms compute quanta,4096 dots or32768 primitive updates; cancellation checks every16 dots/64 updates. At most4 indexed SQL operations per short250ms/VM-bounded read phase, pages<=32rows/128KiB. All compute outside locks/DB transactions. Proposed180s wall deadline for one dirty scope, explicit pending/deadline result and persisted keyset scope cursor on completion/refusal, wrapping once. No cold comparison in primary commit, capture, startup or publication. While building, new observation reservations defer with counters; an already admitted append is retained and invalidates publication CAS. Only complete exact matching snapshot publishes; all admitted competitors remain.

## Source ownership and bounded gate

Store/integration modules: `recording_speaker_history.py`, `recording_speaker_links.py`, `recording_speaker_link_store.py`, `storage.py`, `native_owned_cell_store.py`, `lifecycle.py`, `transcript_contract.py`, `transcript_store.py`, `settings.py`, `__main__.py`.
Producer/lifecycle modules: `native_owned_cell_capture.py`, `recording_primary_probe.py`, `worker.py`, `moss_handy_jobs.py`, `moss_automatic.py`, `native_execution_permit.py`.
Comparator module: new `recording_speaker_history_compare.py`.
No `app.py` edit without demonstrated necessity and review. Tests limited to three new history/compare/integration files plus explicitly reviewed existing regressions. A source-only note may accompany WIP.

First executable gate uses3 naturally ordered disjoint synthetic cores, real owned copy/positive primary commit/normal cleanup/fair automatic turn, queued text, mocked fresh probe, positive cleanup, atomic certificate and complete revision-bound projection. Includes conservation/lost-ack/no-replay/capacity/restart/retirement/revocation/erasure/passive reads, outside-bound append stability, and queued primary arrivals across finite cohorts; then targeted legacy regressions. No models. Exact source hashes, test argv, fixture/output ceilings and CPU3/nice19/256MiB sampledRSS/1GiBAS controls remain to be finalized before the first test dispatch.
