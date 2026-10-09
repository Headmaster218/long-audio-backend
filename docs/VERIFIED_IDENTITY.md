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
  Each event supplies `detail_url`. `GET /sessions/{sid}/identity-reviews/{event_id}`
  resolves a decision's opaque `reference_event_id` to its source session, support
  metadata and full audio, including after restart. It requires the same live
  identity group/generation. Removed source evidence is explicitly unavailable;
  it is never reconstructed or replaced. No vector or filesystem path is exposed.
  The listing cursor is an append order, distinct from profile revision. Source expiry
  can append several events at one atomic bank revision.

Identical retries return the original event, which might subsequently have been
retracted; use the bank/event summaries for current active state. Changed requests
under the same key conflict. Reset-generation requests cannot replay. Simultaneous
contradictory claims serialize, and duplicate exact evidence cannot gain weight
through another support ID or repeated upload.

In verified groups, legacy enrollment/examples return `review_required` (409).
Rename changes only the display label. Segment assignment is an explicit output
correction. The legacy speaker PATCH merge/undo form returns
`verified_merge_unsupported_on_legacy_route` (409); use the revision-checked
person-relation protocol below for verified groups.

## Explicit canonical-person merge and undo

This opt-in workflow joins existing verified-group person IDs after a human
asserts that they identify the same person. It does not migrate legacy groups,
discover people automatically, verify audio, create an anchor, or enable automatic
learning. The normal identity mode remains legacy by default. The experimental
fragment HTTP path has no integration with this workflow.

Read `GET /sessions/{sid}/identity-bank` and `GET /sessions/{sid}/speakers`.
Each verified-mode speaker retains its original `id` and exposes the current
`canonical_person_id`, `canonical_revision` and `canonical_state`. The legacy
`merged_into` field is not changed or used for verified canonical resolution.
Only distinct current canonical roots can be merged; requests naming an alias
are rejected rather than silently choosing a different source or target.

`POST /sessions/{sid}/identity-relations` accepts:

```json
{
  "expected_generation":"<generation>",
  "expected_revision":2,
  "idempotency_key":"merge-review-1",
  "kind":"same_person",
  "person_id":"<source canonical person id>",
  "other_person_id":"<target canonical person id>",
  "confirmed":true
}
```

The operator assertion joins the entire source component to the target root.
The response records `action: "merge"`, its immutable `event_id`, the new bank
revision, and `acoustic_support_certified: false`. Original person rows,
observations, review event owners, support/vector bindings and decision snapshots
are not rewritten. Only already-active explicitly reviewed positives contribute
to the merged fixed centroid. The margin compares distinct canonical people;
the threshold policy is unchanged and remains experimental and uncalibrated.

Use the same request with `kind: "different_people"` to record an explicit
symmetric, non-transitive constraint between the two original IDs. Constraints
between members of one canonical component are rejected. A constraint between
any members blocks merging their components in either direction. Predicted
overlap, coactivity and separation tracks never create a constraint themselves.

`POST /sessions/{sid}/identity-relations/{event_id}/retract` accepts only current
`expected_generation`, `expected_revision` and `idempotency_key`. It returns
`action: "undo_merge"` for a merge, or `"retract_constraint"` for a different-person
claim. Undo removes that exact active edge, even in a chain. For A→B→C, undoing
A→B separates A while preserving B→C; undoing B→C keeps A→B. No later review is
moved or reinterpreted. A review submitted to original A while A is an alias
continues to belong to A; a review submitted to root C continues to belong to C.
The server cannot infer which member a human meant when choosing a canonical ID.
Clients must show the exact original owner when requesting a new review. An
incorrectly owned review needs explicit retraction/replacement, not merge undo.

Merge rejects a positive and an exact negative for the same evidence anywhere
in the proposed component. Every later positive/negative/replacement review is
checked against the component too. A component may have at most eight active
positives. Combining nine or more fails without deleting or selecting evidence;
the operator must explicitly retract selected reviews first, or keep the people
separate. Exact negatives owned by any current member veto the winning canonical
identity and never select a runner-up. Undo restores the negatives' original-owner
scope along with the mapping. Retraction/source expiry never transfers evidence
to another original person.

All relation changes share the bank transaction, generation and revision CAS.
Review and relation idempotency keys share a group/generation namespace. An
identical retry returns its original response, including after later undo, so
the response is not a statement that the relation remains active. A changed
request under the same key conflicts. Concurrent incompatible changes serialize;
the losing stale revision must be reviewed again. Source deletion expires the
source's trust but retains person relations and allows later undo, even with no
remaining references. Full identity reset erases relation events/count/head,
rotates generation and rejects old-generation retries.

`GET /sessions/{sid}/identity-relations?after=0&limit=100` provides private/no-store,
cursor-paginated audit events and their current `active` state. The maximum page
is 500; generation and current profile revision accompany every page. Relation
history is capped globally at 4,096 events, including a reserved undo/retraction
slot per active relation. Admissions fail rather than evicting history. A
hash-linked ledger and group count/head detect inconsistent or missing relation
rows before mapping or matching; this is application integrity checking, not a
cryptographic defense against an actor rewriting the whole database.

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

Verified segment/turn GETs and replay feed snapshots expose `original_speaker_id`
from the stored assignment, effective `speaker_id`, and current `canonical_revision`
and `canonical_state`. Clear stale automatic assignments before resolving a
current canonical ID. The original ID is provenance, not an effective assignment.
If mapping integrity is unavailable, effective assignments are null. Stored human
corrections remain independent and resolve through the current mapping; their
original IDs are preserved for undo. Speaker snapshots in the feed expose the
same canonical fields as the speaker list. Policy and mapping are captured in
the same read transaction for each response.

## Mandatory client invalidation protocol

A bank change conservatively invalidates **all** earlier automatic decisions in
the group, including quota-unretained queries and competitor/margin dependencies.
It does not rescore history. Original decisions remain inspectable.

Invalidation is constant-size per group. The same transaction emits a compact
`identity_bank` change-feed event with:

- `generation`, `profile_revision`, and `invalidate_before_revision`
- `invalidates_automatic_assignments: true`
- `reason` and `clears_all_identity_assignments`
- `refresh_canonical_persons: true`

**Clients must process this event.** For an ordinary bank change, clear effective
automatic assignments whose decision revision is below the supplied revision;
mark them stale and preserve independent human output labels. There are no
per-result update events for each old row. Paginated result GETs and replayed
segment/turn feed snapshots also project stale automatic assignments as no
current person. Result IDs and the existing feed provide committed-result order;
`availability_sequence` is a bank-change sequence, not a total query chronology.
On `refresh_canonical_persons`, refresh the speaker mapping and re-project cached
human assignments from their original IDs too. Use the returned current revision;
do not mix an older mapping with a newer result page. Merge/undo uses
`reason: "canonical_persons_changed"` and conservatively invalidates all old
automatic decisions without rescoring history.

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
- 4,096 immutable relation events globally, including reserved undo/retraction slots
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
does not load or rewrite all historical rows. Before matching, integrity checks
use the immutable current-group/current-generation ledger to derive expected
active membership, then compare every eligibility field with the mutable index.
Changed/missing competitors cause abstention, never a smaller gallery and a new
runner-up match. This indexed pass is bounded by that group's share of the
32,768-event cap, with indexed revocation lookups; a second indexed pass covers
only that group's active reviews (at most 12,288 globally). It does not scan
unrelated groups or unbounded transcript/decision history. Scoring streams at
most 4,096 global positive references, retaining only one person's at-most-eight
vectors at a time plus bounded reference metadata and person scores. Canonical
mapping loads only the group's indexed share of the 4,096 relation events and
at most 1,024 person IDs. These are workload bounds, not a
real-time latency guarantee.

Data are local biometric evidence. Logical deletion removes live application
access, not bytes in SQLite freed pages/WAL, exports, archives or backups. It is
not forensic or cryptographic erasure. Keep localhost; `identity_group` remains
a logical group rather than multi-tenant authorization.
