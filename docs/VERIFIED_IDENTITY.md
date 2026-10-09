# Experimental verified-review identity groups

This is an opt-in, forward-only identity bank built from explicit operator
reviews. It is not a calibrated accuracy claim, a proof of correct human labels,
or a long-recording quality acceptance result. Legacy identity behavior remains
the default. The original legacy enrollment, merge and example APIs keep their
existing behavior in legacy groups.

## Enable a new group

Start the normal local service with both:

```sh
python -m audio_backend --speaker-support-review \
  --verified-identity-policy audio_backend/profiles/verified-review-experimental-v1.json
```

Select the normal model/backend options separately. The example policy is an
explicit **uncalibrated experimental** threshold snapshot, not a transfer of the
AMI calibration to a different frontend. Exact audited runtime/model support is
described in [Embedding space provenance](EMBEDDING_SPACE_PROVENANCE.md).
Unsupported builds, including unverified Windows bindings, abstain.

Create a new group explicitly:

```json
{"name":"Recording","identity_group":"reviewed-family","identity_mode":"verified_review_v1"}
```

Later sessions inherit that group's mode. A contradictory explicit mode returns
409. Existing legacy groups, including groups with retained legacy people, cannot
convert. A reset preserves the group's mode and policy. Keep review capture on
when restarting; without valid transient support proof, verified queries abstain.
A policy is fixed for the group, including after restart. Changing the startup
file does not rewrite an existing group's policy.

## Explicit review

Read `GET /sessions/{sid}/identity-bank` for the current generation and profile
revision. Obtain acoustic support through a segment/turn support endpoint, then
fetch its full audio. Newly captured, eligible support supplies `support_id`,
`vector_id`, `manifest_sha256`, `vector_sha256`, and `space_id`. Old Phase-A
support remains playable but lacks an original-vector proof and cannot enroll.

`POST /sessions/{sid}/identity-reviews` requires these exact server bindings:

```json
{
  "expected_generation":"<generation>",
  "expected_revision":0,
  "idempotency_key":"operator-review-1",
  "kind":"positive",
  "support_id":"<support id>",
  "vector_id":"<vector id>",
  "manifest_sha256":"<manifest hash>",
  "vector_sha256":"<original vector hash>",
  "space_id":"<embedding-space hash>",
  "new_person_name":"Person label",
  "attestation":{
    "version":"all_pooled_support_single_identified_voice_v1",
    "reviewed_all_pooled_support":true,
    "single_identified_voice":true,
    "no_other_overlapping_voice":true
  }
}
```

Use `person_id` instead of `new_person_name` for an existing person. The assertion
covers **all** pooled fragments, including pre/post-core context and contributing
chunks. Mixed, uncertain or overlapping-other-voice support is ineligible for a
positive. The server records an operator claim; it cannot prove listening,
identity correctness or acoustic purity. Producer purity metadata stays
unverified. A one-line assignment or rename never certifies pooled audio.

`kind: "negative"` requires an existing person and a reviewed full support. It
excludes only that exact pooled PCM/encoding/sample-count/embedding-space evidence
for that person. It learns no similarity neighborhood and never redirects a
vetoed query to a runner-up.

- `POST /sessions/{sid}/identity-reviews/{event_id}/retract` requires only current
  `expected_generation`, `expected_revision`, and `idempotency_key`.
- `POST /sessions/{sid}/identity-reviews/{event_id}/replace` takes a complete new
  review request. Admission and removal are atomic. A failed replacement leaves
  the old review active.
- `GET /sessions/{sid}/identity-reviews?after=0&limit=100` is cursor-paginated.
  Its cursor is an append order, distinct from profile revision. Source expiry
  can append several events at one atomic bank revision.

Identical retries return the original event, which might subsequently have been
retracted; use the bank/event summaries for current active state. Changed requests
under the same key conflict. Reset-generation requests cannot replay. Simultaneous
contradictory claims serialize, and duplicate exact evidence cannot gain weight
through another support ID or repeated upload.

In verified groups, legacy enrollment/examples return `review_required` (409).
Rename changes only the display label. Segment assignment is an explicit output
correction. Merge and undo return `verified_merge_unsupported` (409); reversible
reviewed merge/undo remains required future work.

## Matching and result interpretation

Only active explicit positives enter a fixed normalized centroid per person.
Both anchor and query must prove the exact same embedding space. Distinct-person
margin, duration and threshold checks can abstain. Missing anchors/proof, changed
runtime/model/frontend or an exact negative never triggers legacy fallback,
automatic person creation, anchor promotion or template adaptation.

An accepted automatic result has `speaker_status: "experimental_identity_match"`.
It is not human-verified. Results additionally expose `identity_decision_id`,
`identity_profile_revision`, and `identity_state`. The private/no-store decision
endpoint, `GET /sessions/{sid}/identity-decisions/{decision_id}`, exposes immutable
original prediction, score, margin, policy/revision, opaque winning reference
IDs, and review availability. It returns no vectors or audio paths.

Overlap candidates keep local conditioning and turn links only in verified mode;
`identity_decision_required` instructs clients to consult the linked source turn.
They do not copy a global identity that could outlive its decision's validity.

## Mandatory client invalidation protocol

A bank change conservatively invalidates **all** earlier automatic decisions in
the group, including quota-unretained queries and competitor/margin dependencies.
It does not rescore history. Original decisions remain inspectable.

Invalidation is constant-size per group. The same transaction emits a compact
`identity_bank` change-feed event with:

- `generation`, `profile_revision`, and `invalidate_before_revision`
- `invalidates_automatic_assignments: true`
- `reason` and `clears_all_identity_assignments`

**Clients must process this event.** For an ordinary bank change, clear effective
automatic assignments whose decision revision is below the supplied revision;
mark them stale and preserve independent human output labels. There are no
per-result update events for each old row. Paginated result GETs and replayed
segment/turn feed snapshots also project stale automatic assignments as no
current person. Result IDs and the existing feed provide committed-result order;
`availability_sequence` is a bank-change sequence, not a total query chronology.

For `reason: "identity_reset"`, the new generation and
`clears_all_identity_assignments: true` require clearing **all** cached identity
labels, including human assignments, decision links, review bindings and prior
generation caches. Speaker deletion events clear retained names. Reset scrubs
stored current results and historical feed payloads, removes identity-bearing
private review/decision data, and rotates generation, in one transaction. It
retains transcripts/source audio under the existing reset contract. Reset is an
explicit history-erasure operation and can require linear disk work; it avoids
millions of new per-result feed entries.

Deleting a contributing source expires its active trust and invalidates affected
groups in the same transaction. Quota-unretained queries still retain bounded
source dependencies. Normal source deletion does not erase independent human
output corrections in surviving sessions.

## Bounds and privacy

- 8 active positives per person; 1,024 verified people globally
- 4,096 active positives and 8,192 active exact negatives globally
- 32,768 immutable review events globally, including reserved revocation slots
- 1,000 group policies globally; empty groups are reclaimed only after their
  identity state is erased and no live sessions remain
- Existing support defaults: 4,096 records and a combined 64 MiB live PCM/vector
  artifact budget; per-PCM, manifest, vector, transient-window and disk guards
  remain bounded

Every active review reserves a later retraction/source-expiry event. Capacity
cannot strand an unrevokable reference; admissions refuse rather than silently
evicting or replacing evidence. PCM and original-vector bytes share the artifact
budget. Audio may be retained while vector admission is unavailable.

Full review quotas stop new review enrollment, **not** compatible transient query
matching. Automatically matched queries never become anchors. Compact decisions,
source links, transcript results and change-feed history grow on disk with
bounded-per-window outputs; they are not an unbounded RAM cache and remain under
the service's disk/resource guards. Read endpoints are paginated. Bank invalidation
does not load or rewrite all historical rows.

Data are local biometric evidence. Logical deletion removes live application
access, not bytes in SQLite freed pages/WAL, exports, archives or backups. It is
not forensic or cryptographic erasure. Keep localhost; `identity_group` remains
a logical group rather than multi-tenant authorization.
