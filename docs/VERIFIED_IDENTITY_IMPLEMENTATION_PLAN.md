# Experimental verified identity memory: implementation plan

## Scope and release boundary

Build explicit correction-informed, cross-recording identity memory on top of
Phase A's exact acoustic support. This is a forward-only experimental feature,
not a claim that long-term recognition or the existing acoustic quality goals
have been achieved.

Implement B1 and B2 as reviewed commits on one isolated feature branch. Do not
merge or expose the new group mode until both slices, independent review,
aggregate tests, and a small real API integration test pass. Keep the existing
legacy default unchanged. Do not alter frozen measurements or evaluation data.

- **B1:** immutable support vectors, embedding-space proof, explicit review and
  retraction ledger, quotas, and full lifecycle integration.
- **B2:** explicit new-group verified-only mode, matching, public decision
  provenance, invalidation, and end-to-end API behavior.
- **Later required follow-up:** reversible verified merge/undo. Initially return
  an explicit unsupported-operation conflict in verified mode. Never reuse the
  legacy merge path as an acoustic verification mechanism.

No automatic anchor admission, template adaptation, implicit person creation,
automatic merge, or similarity-generalized negative memory is included.

## Current implementation facts

Phase A's `speaker_support` table retains immutable exact pooled PCM, a support
manifest, source dependencies, and `embedding_sha256`. It does **not** retain the
embedding vector. Its private observation ID is a historical link, not a durable
vector guarantee; the observation ring can evict that row.

`Store._window_identity` normalizes an analyzer vector again before storing a
legacy observation. The support fingerprint covers the original analyzer vector.
A legacy observation therefore cannot be relabeled as the proven original vector
merely because its person, model name, dimension, or approximate values agree.

Phase A's frontend description binds PCM conversion, implementation and runtime
versions, but it does not fully prove the embedding feature space. The same
weights and dimension with different FBANK settings, including `high_freq` and
`snip_edges`, can produce incompatible vectors. Decoder provenance and embedding
feature-space compatibility must be represented separately.

Relevant integration points:

- `speaker_support.py`: preparation, manifest validation, immutable persistence,
  generation capture, source-deletion trigger, metadata and audio reads.
- `inference/analyzer.py`: actual embedding extraction and producer provenance.
- `worker.py`: exact PCM/source capture and transient support preparation.
- `storage.py`: session creation, `_window_identity`, `_speaker`,
  `commit_window`, legacy corrections, and result reads.
- `app.py`: explicit request schemas and authenticated routes.
- `change_feed.py`: public allowlists and transactional result changes.
- `identity_reset.py` and `lifecycle.py`: logical erasure and source deletion.
- `speaker_calibration.py`: existing explicitly selected policy artifacts.

## B1: immutable vector evidence and compatibility

### Capture original vectors, not legacy replacements

Add a private `speaker_support_vectors` table or equivalent immutable companion
record. Each record contains:

- Immutable vector-evidence ID and support ID.
- Original analyzer vector in a bounded canonical, lossless representation.
- SHA-256 matching the support manifest's original analyzer-vector fingerprint.
- Dimension and complete embedding-space descriptor/hash.
- Explicit matcher normalization version and derived normalized-vector hash.
- Actual retained byte count and source/generation ownership.

Canonical JSON bytes matching the existing vector fingerprint are a simple
representation. If choosing a binary representation instead, prove the exact
conversion and fingerprint contract; do not silently round a different vector
to float32 and call it the original. Validate finite values, valid dimension,
nonzero norm, encoding, length and hash. Use the same versioned normalization
procedure for anchor and query vectors.

Capture the raw vector with newly produced support before Store's legacy
normalization. Persist it atomically with its support binding. Never insert it
into chunk metadata, ordinary API JSON, change-feed records, or public reports.

Charge PCM, vector bytes, and any retained normalized copy to the same bounded
artifact budget. Include per-record byte bounds and resource guards. If vector
admission fails, report that review audio may be available while verified-anchor
enrollment is unavailable. Do not silently fall back to an old observation.

Old support without original-vector and embedding-space proof remains playable
but not enrollable. Initially return `vector_proof_unavailable` or a similarly
explicit reason. A future requested re-embedding operation must create separate
versioned evidence from the exact retained PCM, with actual model/frontend
provenance; it cannot mutate the old record or invent equality to its old hash.

### Embedding-space descriptor

Create a canonical, schema-versioned embedding-space descriptor covering:

- Encoder bytes, model version/type, dimension, and relevant model metadata.
- Actual feature-extraction implementation and runtime binary/build identity.
- Resolved effective FBANK/framing settings, including high/low frequency,
  `snip_edges`, frame length/shift, windowing, amplitude scaling, and applicable
  pre-emphasis, dither, DC removal, log/energy, and mean-normalization behavior.
- Extractor/provider/configuration and output normalization semantics.

Record actual effective values or a fully resolved, hash-bound implementation
contract. Do not guess library defaults from a version string or infer them from
the model dimension. If relevant defaults cannot be established, mark the space
unknown and fail closed for verified enrollment and matching.

Keep source decoding/downmix/resampling provenance separate. It identifies the
waveform that was reviewed; it does not establish compatibility of the features
presented to the encoder.

Initially require exact embedding-space compatibility for both queries and
anchors. Do not add an implicit compatibility-equivalence list. A future runtime
or frontend change needs explicit compatibility evidence or a new bank space.
Historical records are never silently re-versioned.

### Proposed private state

The exact table names can follow repository conventions; preserve these roles:

1. `identity_group_policy`: immutable mode/policy snapshot and policy hash,
   embedding-space binding, experimental/calibration status, current profile
   revision, and monotonic availability sequence. Read the current group
   generation from its authoritative state. Bound the number of policy records.
2. `identity_review_events`: immutable event ID, group/generation, sequence,
   action, server-derived operator context, original target-person ownership,
   support/vector IDs and fingerprints, attestation version, prior prediction,
   supersedes/retracts links, idempotency key and request fingerprint.
3. `identity_active_reviews`: bounded materialized active positive references and
   exact-support negatives. Rebuildable from current-generation events; it never
   edits original vectors or changes their source ownership.

The existing person/name table can remain the public identity namespace if the
verified matcher never reads its legacy embeddings. A display name or old
`state=confirmed` is not proof of acoustic verification.

### Bounds, deduplication and ledger exhaustion

- At most eight active positive references per person, including a future merged
  component. Specify global person/reference, negative, event and byte caps.
- No automatic reference eviction, replacement, or promotion.
- Reject repeated exact PCM/space evidence as a second weighted reference.
  Different support IDs or repeated uploads do not make independent evidence.
- Retain related-source information without counting overlapping excerpts as
  independent recordings in evaluation.
- Count immutable event history, inactive records and reserved revocation slots
  in the ledger budget. Admission reserves enough capacity for its later
  retraction/source-expiry event. At capacity, refuse new learning; never block
  removal of a bad anchor, source deletion or group reset.
- A replacement checks all new capacity before deactivating the old event.
- Do not silently compact away event dependencies. Any later compaction must
  preserve a verifiable checkpoint and necessary revocation/dependency state.
- Row/byte caps bound live application state, not SQLite/WAL high-water size.

## Explicit review API and transaction contract

Suggested routes:

- `POST /sessions/{sid}/identity-reviews`
- `POST /sessions/{sid}/identity-reviews/{event_id}/retract`
- `POST /sessions/{sid}/identity-reviews/{event_id}/replace`
- Read-only bank/event summaries with strict public field allowlists.

A positive request supplies server support/vector IDs, expected group generation
and profile revision, immutable evidence fingerprints, an existing target person
or a requested new name, an idempotency key, and explicit operator attestation
covering **the entire pooled support**: one identified speaker and no overlapping
other voice. Bind the assertion to a specific attestation schema/version.

The server revalidates retained PCM integrity, manifest, original vector binding,
compatibility, source lifecycle, actual duration and quotas. Client vectors,
arbitrary paths, provenance assertions and hashes are never authoritative.

Attestation records what an operator asserted. It cannot prove that the operator
listened, identified the person correctly or detected every other voice. Keep
producer purity metadata unchanged; store the assertion separately.

Mixed, overlapping, incomplete or uncertain support cannot become a positive
anchor. A single segment rename does not certify its other pooled fragments.
Review UI/API guidance must include pre/post-core and cross-chunk context.

Initially support only exact-support negative exclusion. Its evidence key is the
complete pooled PCM hash, encoding, sample count and embedding-space ID within
the group. A negative makes that exact evidence ineligible for the specified
person; it does not learn a similarity neighborhood and never redirects a query
to the runner-up. A mixed/uncertain review rejection is not automatically a
person-negative exemplar.

Each mutation runs inside `BEGIN IMMEDIATE`:

1. Resolve live session, fixed group mode/generation, person and evidence.
2. Resolve idempotency and check expected revision/evidence bindings.
3. Check contradictory claims, deduplication and all admission reservations.
4. Append event and update active references.
5. Advance profile revision/availability sequence and invalidate decisions.
6. Publish safe result/bank changes in the same transaction.

Identical retries return the original event; a changed request under the same
key conflicts. Requests from a reset generation cannot replay. Simultaneous
positive claims for different people, or positive/negative claims for one person
and the same evidence, conflict. Replacement retracts and corrects atomically;
failure leaves the original state intact.

Do not implicitly rewrite sibling segments, turns, earlier recordings or person
names. Optional transcript corrections must specify an explicit result scope.

## B2: explicit verified-only groups and matcher

### Group mode

Extend session creation with an optional `identity_mode`:

- A new group with no explicit mode remains legacy.
- A new group explicitly requesting `verified_review_v1` uses the verified bank.
- Later sessions inherit their existing group's immutable mode.
- A conflicting mode request returns 409; existing legacy groups do not convert.

Require enabled review capture and a valid explicit experimental policy before
creating the new mode. Keep it unavailable until the complete B2 contract ships.
Reset preserves the mode/policy while rotating generation and forgetting the
identity data. It must never silently restore legacy matching.

Policy state must not grow without bound through creating/deleting groups.
Specify a global cap and deliberate reclamation/erasure rules. Do not discard a
verified-mode marker while live sessions or identity state still depend on it.

### Matcher routing and scoring

Route by group mode before the existing `_speaker` path. The verified branch
must not consult `speakers.embedding`, `speaker_examples`, provisional vectors,
or automatic observations as references.

Use only active reviewed positives with proven compatible space. Initially use
a fixed centroid scorer, distinct-person margin and abstention, with explicitly
selected and snapshotted thresholds/duration gates. Preserve the selected policy
artifact hash and its experimental limitations. A model-bound legacy policy does
not by itself prove calibration for another frontend.

No anchors, missing proof or incompatible query space yields unknown. There is
no automatic person creation, legacy fallback, threshold adaptation or template
update. A new same-person review can target an existing person directly without
requiring a merge.

Use statuses such as `experimental_identity_match`, with a separate basis of
`reviewed_reference_bank`. A successful automatic query is not human-verified.

### Query validation is independent of artifact admission

Do **not** require retaining every query in the review artifact store. Otherwise
the bounded review quota would eventually stop cross-recording recognition.

Validate transient query support/vector/space and score it against the current
reviewed bank even when new PCM/vector artifact admission is refused. Only new
review/enrollment availability stops. Persist a compact decision record with
query evidence/space fingerprints and bounded dependencies. Never retain or
promote the automatically matched query as an anchor.

`_window_identity` currently calls the legacy matcher before `support_for`
persists support. Introduce a verified-only resolver that validates transient
evidence first, reads the current bank inside `commit_window`'s transaction,
scores it, and handles optional artifact admission separately. Leave the legacy
branch's behavior unchanged. Do not use a stale in-memory bank after a mutation.

### Legacy actions in verified mode

- Rename: label-only, without changing acoustic trust.
- Segment assignment: explicit output correction only.
- Legacy enrollment/example routes: `review_required` conflict pointing to the
  new route; they cannot silently create verified anchors.
- Legacy merge/undo: explicit unsupported-operation 409 in the first release.
- Legacy enrollment/example availability flags must not advertise an unsafe
  enrollment path in verified mode.

## Decisions, public provenance and invalidation

Add immutable `identity_decisions` records and result-to-decision links. Store:

- Query evidence fingerprint and embedding-space ID.
- Group generation, policy hash, profile revision and availability sequence.
- Original predicted person, score, margin and abstention reason.
- Winning contributing review/reference IDs, bounded by eight.
- Full-gallery revision, accounting for competitor changes without embedding
  the entire gallery in every decision.
- Optional retained support ID and explicit review-availability reason.

Expose a safe decision endpoint and additive result fields for decision ID,
revision, current/stale state and opaque reference-event dependencies. No vectors
or raw file paths. Decision history grows only with bounded-per-window result
history on disk; it must not accumulate in an unbounded RAM cache. Keep existing
disk guards and document this disk-history growth separately from bank caps.

For the initial release, conservative group-wide automatic-decision invalidation
on bank mutations is acceptable:

- Keep original decisions immutable and inspectable.
- Automatic results from invalidated revisions become stale and cease exposing
  an effective current person assignment; preserve the original in decision
  history rather than pretending it never happened.
- Preserve independent explicit human output labels.
- Emit stale-result changes transactionally through the existing change feed.
  An updated GET alone is insufficient for already caught-up polling clients.
- Do not silently rescore old results or rewrite historical accuracy metrics.

This may invalidate unaffected results and must be documented. A later selective
invalidation design may narrow it, but must account for competitor/margin and
canonical-membership changes, not only the winning reference.

## Merge/undo follow-up

The initial verified mode rejects merge/undo explicitly. This remains required
follow-up work rather than a claim that the complete correction workflow exists.

A future verified merge is an explicit retractable bank event. Preserve each
anchor's original person ownership; derive canonical membership instead of
rewriting anchors into the target. Check combined capacity, compatible spaces,
positive/negative conflicts and any explicit different-person constraints.

Undo restores membership and invalidates decisions made with the combined bank.
Simply removing `merged_into` cannot repair predictions produced from a merged
centroid. Do not infer hard different-person constraints from model overlap or
separated tracks.

## Source deletion, reset and logical erasure

Deleting any contributing source must deactivate dependent positive/negative
reviews, erase private vector access, advance revision and invalidate decisions
in the same transaction. Cascading away a vector while leaving an active trust
event is incorrect. Coordinate this with the existing support source-deletion
trigger; retain a defensive backstop for all chunk-deletion paths.

Group reset removes active references, negatives, private vectors, review
bindings and identity-bearing event/decision data; rotates generation; redacts
public history consistently; and retains source audio/transcripts according to
the existing reset contract. Old requests and caches cannot rebuild the bank.

These operations provide logical deletion and loss of live application access.
SQLite freed pages/WAL, exported archives and separate backups may retain bytes.
Do not call them forensic or cryptographic erasure. Bounded `secure_delete` or
checkpoint safeguards can be evaluated separately with performance, concurrent
reader and durability/data-loss implications. They are not a prerequisite for
this feature and cannot erase external copies.

## Test and acceptance plan

### B1 model-free tests

- Exact original-vector hash across restart and observation eviction.
- Old renormalized observations cannot replace missing raw-vector proof.
- Same weights/dimension with different FBANK high frequency or `snip_edges`
  yields incompatible space IDs. Query and anchor checks both enforce this.
- Decoder identity and embedding feature-space identity remain distinct.
- Missing effective frontend/build proof fails closed.
- Full-support assertion is required; rename/one-line edits cannot certify.
- Duplicate, contradictory and cross-group claims reject.
- Idempotency, stale generation/revision, concurrent claims and rollback.
- Atomic replacement and retraction at full admission capacity.
- Combined PCM/vector quotas and private-field API/feed rejection.

### B2 model-free and transactional tests

- Legacy fixtures remain unchanged when the feature is unused.
- New verified groups never fall back or automatically create people.
- Only active reviewed positives affect scoring; incompatible queries abstain.
- Exact negative veto never chooses the runner-up.
- A long sequence exhausts support row/byte quotas: later compatible queries
  still recognize against existing anchors, artifact availability is explicitly
  false, and the reference bank never grows from those automatic matches.
- Retraction, source deletion and reset atomically invalidate results and feed
  snapshots, including competing-person and merged-membership dependencies.
- Human output corrections survive unrelated bank invalidation.
- Unsupported merge/undo fails clearly.
- Restart preserves policy, active bank and decision status.
- Capacity and failure injection cannot strand an active unrevokable reference
  or publish half a bank/result update.

### Real integration and quality boundaries

After independent review and the aggregate suite, run a small real API exercise
with explicit full-support operator assertions: capture, positive review,
cross-recording query, genuine observation eviction, correction/retraction,
restart, quota exhaustion, source deletion and reset. A scripted oracle is a
test fixture, not proof of human listening or acoustic purity.

Quality promotion requires fresh chronological target-channel evaluation with a
frozen initial review period. Preserve original difficult cases and failed
recall/safety goals. Count all initial review effort, mixed/rejected evidence,
later review requests, accepted correct assignments, wrong-person assignments
and seconds, and actual identity fragmentation. Compare newly wrong decisions
directly rather than hiding them behind a better aggregate recall score.

Explicit provenance and immutable reviewed references close particular
contamination paths. They cannot guarantee correct human labels, perfect overlap
detection, or that adding a true reference never raises an impostor's score.
The feature alone is not evidence of successful long-term identity learning.
