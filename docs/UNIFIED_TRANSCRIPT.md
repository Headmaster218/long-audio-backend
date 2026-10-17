# Current unified transcript, version 3 (closed-component indexed reads)

Three read-only GET endpoints expose a bounded current display projection:

- `/sessions/{sid}/transcript?block=0`
- `/sessions/{sid}/transcript/evidence/{source_ref}`
- `/sessions/{sid}/transcript/binding-audit`

The existing bearer-token access policy, session deletion/tombstone checks,
private/no-store headers, and shared response lease pool apply. Reads work with
models, MOSS execution and the worker disabled. They do not start inference,
read audio files, download assets, create tables, backfill data, or replay the
change log. No materialized transcript, geometry backfill, or new table is
persisted. Startup adds one integer session revision and versioned transactional
invalidation triggers after the source schemas exist. Fresh synthetic fixtures
qualify the indexed reads, component closure and invalidation contract; no original
historical database adoption is qualified. The dev34 qualification below uses
retained numeric geometry in fresh synthetic SQLite. Earlier
[dev21 qualification](DEV21_INDEXED_TRANSCRIPT_QUALIFICATION.md) remains historical.

## Reading contract

`reading` is the useful unified reading: a deterministic, provisional display
preference combining eligible MOSS support runs with unaffected whole primary
ASR units. `baseline_reading` separately preserves the current primary units.
`selected_runs` identifies exact supports and replaced primary references.
`alternatives` retains complete linked primary units and retained MOSS timed
phrases, including simultaneous speakers and equal text, when optional closure
is complete. An explicit capacity-unresolved fallback has no optional selection
or alternatives and preserves the complete initial-page primary baseline. Nothing compares
text for selection, merges anonymous speakers, removes intrinsic model repeats,
splits a word, proportionally crops a string, or asserts improved accuracy.

The display contract always reports `complete_content: false` and `eos: null`.
Full audio support does not prove full recognition. MOSS timestamps remain
native, support-relative, model-generated and unverified. Their session display
times add the validated support origin; `word_times` is always null. The MOSS
speaker namespace is `(job_id, result_sha256, local_tag)`. A primary local label
is scoped to `(chunk_id, speaker_window_start, local_tag)` when the window is
known. Person assignments are not projected or inferred by this API.

### Fixed coordinate policy

Blocks are 60 seconds, beginning at session frame zero, with 16,000 frames per
second. Primary ASR timestamps stay exact in `canonical_asr` and its hash. Only
selection geometry uses an outward integer-frame envelope: floor the start and
ceil the end, snapping within 0.000001 frame of an integer to remove binary-sum
noise. This does not alter primary text or the original timestamps. Durable
source mapping requires exact frame coordinates and never uses this tolerance.

Primary retrieval seeks the latest indexed chunk predecessor before the block
minus five seconds, then indexed chunk starts through block end plus five
seconds. Published chunk timelines are disjoint. The five-second context bound
is the backend's maximum configured ASR context; lexical ownership may cross a
chunk boundary within this bound. Such units remain whole. Encountered geometry
beyond the bound or an invalid timeline is explicitly unresolved, not clipped.
Historical/configuration-invalid data outside these publication invariants is
unsupported by this current projection.

Initial time-page candidate discovery uses that same bounded chunk set and
separately seeks `auto_cid1` and `auto_cid2`, in every automatic state. Candidate sequences
are deduplicated before descriptor lengths, exact binding validation and
half-open support intersection. Both indexes are necessary: a seam's first
alias can precede the chunk seek while its second alias still intersects the
page. A predecessor ending before the context window is discarded, so a distant
seek across a gap does not inspect that predecessor's unrelated bindings.

Discovery then closes the page's exact graph components. Every initial-page
whole primary unit seeds a search across its full interval, even if the page has
no initial candidate. Eligible candidates connect on positive support overlap
or exact adjacency; candidate-to-primary edges require positive intersection.
Primary-to-primary overlap and shared page/envelope membership never connect
components. Only fully validated eligible candidates expand the frontier.
Ineligible result bodies and conclusively unplaced descriptors consume admission
budgets but never bridge components. Conservative primary SQL predicates are
filtered with the exact outward integer-frame envelopes described above.

All bands use one snapshot and cumulative counters. Chunks, descriptors, binding
payloads, result bodies and primary identities are deduplicated before admission;
repeated indexed seeks still spend the same SQL/VM/time budget. Complete closure
is required before selection. A loaded prefix is never a successful MOSS choice.

Only durable automatic timestamped-recording bindings place MOSS history.
Each binding must match its immutable candidate hash, known selection policy,
session, source aliases, receipt IDs, source hashes, source lengths, and actual
support parts. Each alias derives the same session transform:

`chunk.timeline_start * 16000 + part.source_start - part.logical_start`.

The indexed source-root expectation is ordered-unique across commit roots, so
two independent core commits in one upload can use one merged source alias.
This does not merge or rewrite either commit/slot identity. Actual aliases,
receipts and support geometry remain strictly validated; duplicate actual
aliases are not repaired.

The source parts must exactly cover the support. Core envelopes are checked but
never substituted for actual support. Cancelled sources remain historical
alternatives and are ineligible for the unified reading. Unbound manual or
legacy history is explicitly outside placement scope; no coordinate guess is
made. A time page can report `unsupported_unplaced` only for aliases found by
its bounded page-seeded frontier. It explicitly returns
`unplaced_history_check: not_performed_by_time_page` and `binding_audit_path`.
No page, including block zero, certifies the absence of missing/moved-source
history. The separate audit below enumerates such automatic bindings. None of
these checks is a current source/audio attestation.

### Fixed selection policy

1. Eligible support intervals are edges in an endpoint DAG. For each endpoint
   pair, retain the best exact-adjacent path, preferring fewer jobs and then the
   fixed tie order below. No subset enumeration is used.
2. A path is a legal replacement run only if its support union fully contains
   every intersecting **whole** primary unit. Internal joins may cross a primary
   unit because only the union's exterior boundaries determine legality.
3. Weighted interval scheduling chooses nonoverlapping legal runs with maximum
   total support frames, then fewer jobs, then seam before ordinary, start frame,
   archive sequence, result hash, and candidate key. Adjacent winning runs join.
4. Selection uses complete components before page rendering. Neighboring pages
   retain the same full run bounds, candidate keys and whole-primary references.
   The fixed page bounds describe the display view, not clipped support or text.

EOS-unknown alone does not make a valid provisional candidate ineligible.
Malformed retained schema, inconsistent embedded bindings, or corrupted result
hashes fail closed. Parse errors/empty timed output are ineligible for selection;
their retained valid timed lanes remain inline as alternatives. Their support
metadata says `inline_timing_projection: partial_or_unavailable` and
`omitted_lexical_content: inspect_exact_result`, with `result_ref` linking the
complete stored result. The stored result is not a promise that volatile exact
native raw output remains available.

All evidence has one home block. A primary unit's home is its start-frame block;
all phrases of a MOSS result share its support-start home block, preserving the
result's lanes together. Other intersecting blocks carry `reference_only: true`
references without repeating text. A visible global run retains references to
all of its chosen phrases and replaced primary units, including outside-page
homes. Replaced primary units are consistently suppressed from `reading` on
every intersecting page. The evidence endpoint resolves those refs.
Full text may occur in both reading and explicitly labeled alternatives within
its home block; alternatives are never a second chosen source coverage.

## Snapshot, revision and pagination

Use `next_cursor` as the next request's `cursor` parameter. An explicit initial
`block` can seek a block. A cursor binds the policy hash, session, next block and
computed revision. It is an opaque consistency token, not an authorization token.
No transaction is retained between requests.

Each time page and audit page uses one read-only SQLite snapshot. Revision
includes the maximum session primary-change cursor, `sessions.transcript_revision`,
timeline extent and policy hash. The indexed primary-change high-water read is
unchanged; no change-feed trigger definition is modified. No payload hashes,
timestamps or maximum archive sequence are scanned to derive the revision.
The counter is incremented inside the originating write transaction:

- Automatic intent insertion/deletion and relevant binding/state/job updates:
  old and new intent session owners
- Retained-history insertion/deletion and relevant state/schema/identity/result
  updates: old and new history session owners, plus retained automatic owners
  found through the existing unique job index
- Canonical primary segment insertion/deletion/updates: old and new chunk session
  owners, covering cross-session moves even though the existing public feed
  resolves the new owner on updates; mutable person assignments are excluded
- Chunk insertion/deletion and identity/session/digest/geometry/status updates:
  old and new chunk session owners, plus retained automatic owners through both
  alias indexes, including committed results after cancellation or source moves
- Recording-work insertion/deletion and source/receipt/coordinate updates:
  old and new chunk owners and retained automatic owners through both alias indexes
- Recording-batch insertion/deletion and identity/session/origin updates:
  old and new batch owners, work-chunk owners and retained automatic owners, using
  the existing batch-position and both alias indexes; deletions inspect owners
  before foreign-key cascades

Unchanged values and unrelated automatic wall clocks, proofs and accounting
writes do not increment the counter. Retained result payload/metadata changes do.
An earlier reserved archive row committing later invalidates continuation even
if the maximum archive sequence is unchanged. Reverting a source mapping also
invalidates earlier cursors. Counter overflow rolls back the complete write
transaction, rather than wrapping, becoming a floating-point value or publishing
without invalidation. Counter and exact versioned triggers install together in
the existing initialization transaction after automatic/history/recording schema
initialization and the additive canonical segment-column migration. Partial or
incompatible revision schema fails closed.

A cursor with an old policy returns HTTP 409 `invalid_transcript_cursor`; a
changed snapshot revision returns 409 `transcript_revision_changed`. A new
first-page request obtains the new view. Identical retries are deterministic.
No read transaction is retained between requests.

A reverse `chunk_timeline` seek with `LIMIT 1` bounds pagination extent by the
maximum of the session timeline and final published chunk end. Exact placement
proves a support ends no later than its last source alias's chunk end; disjoint
published chunk intervals make the final chunk end the maximum. This keeps a
late valid support reachable even if `timeline_end` lags, without traversing
bindings. The final chunk uses the same finite/nonnegative outward primary
frame envelope as primary geometry, preserving non-grid legacy upload durations;
automatic support placement still requires exact 16 kHz coordinates. Unsupported
historical overlapping chunk timelines are not silently repaired.

### Binding-placement audit

`GET /sessions/{sid}/transcript/binding-audit` examines at most 64 automatic
bindings in `auto_session_seq` order, regardless of state, using `sid` and
`seq > after` keyset predicates. It emits only `unplaced_bindings`; it does not
load retained result payloads or claim full result-schema validation. Each output
row includes its candidate/job identity, states and explicit placement failure.
The audit scope excludes unbound manual and legacy result history.

Follow `next_cursor` until `has_more` is false. The cursor binds the same session,
policy and snapshot revision as time pages but has a distinct audit view. Its
position is the last EXAMINED sequence, never the last emitted failure or number
of returned rows. Therefore an empty `unplaced_bindings` page can still require
continuation; gaps in sequence numbers do not skip rows or repeat a page.
`examined` and `last_examined_seq` make progress explicit. A changed revision
requires restarting the audit. Only traversal to the end of one unchanged
revision checks every automatic binding for placement; a single empty audit
page says nothing about unexamined history.

## Exact evidence

Primary refs are `p.{segment_id}.{canonical_asr_sha256}`. The canonical fields
are session ID, segment ID, chunk ID, original start/end, text/raw_text, backend,
timestamp kind, confidence, local speaker label and speaker window start.
Mutable person assignment and review fields are excluded. Detail returns the
complete current canonical ASR content and its hash, labeled
`exact_current_asr_content`. Changed content returns 409
`primary_evidence_changed`; missing content returns 404. There is no historical
primary replay or change-feed snapshot reconstruction.

MOSS refs are `m.{job_id}.{result_sha256}.{original_phrase_index}`; the suffix
`all` retrieves the complete stored result. Exact lookup checks session, retained
state and result hash before returning the original phrase/result JSON. It is
historical-only (`source_current: not_checked`, `source_valid_at_commit:
not_asserted`), including for cancelled or no-longer-placeable bindings. Hash
lookup itself does not certify the result's placement/schema or make it eligible
for selection; page projection performs those extra checks. Wrong hash/state is
409, missing job/index is 404. Session deletion hides both evidence types.

## Fixed resource bounds and failures

- At most 1,024 distinct automatic descriptors across the whole page-seeded
  closure and 4 MiB cumulative binding bytes, each binding at most 4 KiB. This
  remains indexed frontier work, not a session-wide scan on every time page.
- Audit pages examine at most 64 bindings plus one metadata-only lookahead row;
  all existing SQL/time/response bounds also apply to this separate endpoint.
- Automatic admission remains separately limited to 1,024 rows per session and
  4,096 globally. This change does not raise or solve those admission limits.
- At most 128 distinct discovered/source-alias chunks and 128 admitted whole
  primary units, with 256 KiB cumulative primary text-field projection
- At most 16 committed MOSS payloads across the complete page-seeded closure,
  including ineligible bodies, 256 KiB each and 2 MiB aggregate; metadata at most
  16 KiB per result. Limits do not reset per component, query or frontier band.
- At most 1,024 candidate-to-primary alternative links; all links are retained
  or the page explicitly refuses the projection
- Response at most 256 KiB, including all inline alternatives
- One staging operation at a time, and the existing shared response pool holds
  a 768-KiB response reservation until send completes or fails
- One query-only read transaction, zero busy wait, 250,000 SQLite VM steps and
  a 0.75-second cooperative read deadline

Time-page SQL uses `chunk_timeline`, `segments_chunk`, both `auto_cid1` and
`auto_cid2`, intent sequence point lookups, the archive's unique job index and
`result_changes_session`. Only the separate keyset audit uses `auto_session_seq`.
Payload lengths are checked before materialization. Binding blobs are processed
one at a time; no audio/proof blobs are selected. There is no full-session binding
payload scan per time page, full change-history traversal or unbounded overlap
closure. A long legacy single-chunk primary lookup can still scan many earlier
segment rows: the unchanged VM/deadline budget must explicitly refuse excessive
work; no invented maximum upload duration or silent truncation is introduced.
The endpoint DAG has at most 32 endpoints. Selection ranking and all aggregate
byte, linkage, response, SQL and time caps remain fixed.

The initial-page primary baseline is fully validated before optional reads.
Only an explicit optional count/byte admission failure can return HTTP 200 with
`reading == baseline_reading`, empty `selected_runs` and `alternatives`, and
`reading_status: primary_only_optional_closure_unresolved`. Its `optional_closure`
reports `complete: false`, `status: capacity_unresolved`,
`scope: page_seeded_components`, `selection_performed: false`, and
`alternatives_complete: false`. One `optional_closure_incomplete` notice repeats
the capacity reason and `evidence_complete: false`. The fixed `limit` values are
`chunks`, `bindings`, `binding_bytes`, `primary_units`, `primary_bytes`,
`result_bodies`, `result_bytes`, `results_bytes`, and `result_or_metadata_bytes`.
No optional prefix or apparently separate successful component survives an
incomplete closure. With complete closure, the completeness/selection fields
are true and the status is `complete`.

Missing required history/payload rows, malformed length metadata, read/integrity
uncertainty, SQL/VM/time exhaustion, incomplete primary baseline and final actual
serialized-response exhaustion remain explicit read failures (normally 503).
Fallback never resets a budget or bypasses final serialization/deadline checks.
Clients must inspect closure status; primary-only fallback does not establish
that optional alternatives were fully examined. A failed page is not empty text.

The 60-second block width is fixed; there is no smaller-window retry parameter.
A connected 23-job, 126-second synthetic chain exceeds the 16-result body cap on
every intersecting page, in both reading orders. Each page retains its complete
primary baseline and explicit incomplete optional status. Requesting a smaller
page would not resolve component-wide density. Exact evidence and existing
primary/history APIs remain available subject to their own bounds.

Strict malformed query/reference/cursor requests return 409. Missing/deleted
sessions return 404, access failures use the existing 401 policy, and response
pool exhaustion returns 503. The app's lifetime wrapper releases leases on
disconnect, send failure and cancellation, including late synchronous handoffs.


## dev34 qualification (model-free)

All 214 cases passed once: 35 component cases, 49 source-root cases, 43 pure
projection cases, 49 HTTP cases and 38 indexed-reader cases. Coverage includes
both page orders, disjoint components, invalid bridges, long whole-primary
tails, outward-frame endpoint rounding, shared count/byte/VM/deadline limits,
17-body and 23-job capacity fallbacks, missing/revoked dependencies, cursor
changes, exact result/proof binding and final response serialization. The
100,000-row legacy scan retains its original VM-refusal assertion; only that
fresh bulk fixture uses DELETE journaling to stay within the fixture cap. Normal
integration fixtures use WAL. This scan does not qualify WAL concurrency.

The sanitized retained-geometry fixture has 52 candidates, 43 eligible jobs,
321 whole primary units, 174 chunks and 30 pages in four scopes. Real production
binding/result validators and indexed fresh SQLite reads reproduce the accepted
numeric outcome: 7 selected jobs, 83 seconds of support, 52 replaced whole
primary units and 6 selected pages. All 30 pages were read in both orders (60
reads). Maximum observed page costs were 8 result bodies, 5,500 VM steps,
112,648 serialized response bytes and 16.24 ms. Identities, hashes, receipts and
text are synthetic; text placeholders retain encoded lexical lengths and can
increase raw UTF-8 storage. These measured synthetic costs do not certify the
original stored text/proof combinations or current source authority.

The full 11-batch sequence completed in 18.64 seconds, with peak sampled RSS
100,167,680 bytes and peak fresh fixtures 9,777,152 bytes, within one 120-second
bound, CPU 3, nice 19, 256 MiB RSS, 1 GiB address space and 16 MiB fresh fixtures.
All children were waited and fixtures cleaned. No model/audio execution,
original database read, archived rewrite or recognition-quality evaluation was
performed. The existing operational report remains a historical result and is
not changed to a successful model qualification by this checkpoint.
