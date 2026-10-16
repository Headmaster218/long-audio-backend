# Current unified transcript, version 1

Two read-only GET endpoints expose a bounded current display projection:

- `/sessions/{sid}/transcript?block=0`
- `/sessions/{sid}/transcript/evidence/{source_ref}`

The existing bearer-token access policy, session deletion/tombstone checks,
private/no-store headers, and shared response lease pool apply. Reads work with
models, MOSS execution and the worker disabled. They do not start inference,
read audio files, download assets, create tables, backfill data, or replay the
change log. No transcript index or materialized transcript is persisted.

## Reading contract

`reading` is the useful unified reading: a deterministic, provisional display
preference combining eligible MOSS support runs with unaffected whole primary
ASR units. `baseline_reading` separately preserves the current primary units.
`selected_runs` identifies exact supports and replaced primary references.
`alternatives` retains the complete linked primary units and all retained MOSS
timed phrases, including simultaneous speakers and equal text. Nothing compares
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

Only durable automatic timestamped-recording bindings place MOSS history.
Each binding must match its immutable candidate hash, known selection policy,
session, source aliases, receipt IDs, source hashes, source lengths, and actual
support parts. Each alias derives the same session transform:

`chunk.timeline_start * 16000 + part.source_start - part.logical_start`.

The source parts must exactly cover the support. Core envelopes are checked but
never substituted for actual support. Cancelled sources remain historical
alternatives and are ineligible for the unified reading. Unbound manual or
legacy history is explicitly outside placement scope; no coordinate guess is
made. Invalid/unmapped automatic bindings appear as `unsupported_unplaced`
notices with home block zero. These are not current source attestations.

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
4. Any support crossing a fixed block boundary remains an explicit unresolved
   alternative. No global overlap-component expansion changes the block size.

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
references without repeating text. The evidence endpoint resolves those refs.
Full text may occur in both reading and explicitly labeled alternatives within
its home block; alternatives are never a second chosen source coverage.

## Snapshot, revision and pagination

Use `next_cursor` as the next request's `cursor` parameter. An explicit initial
`block` can seek a block. A cursor binds the policy hash, session, next block and
computed revision. It is an opaque consistency token, not an authorization token.
No transaction is retained between requests.

Each page uses one read-only SQLite snapshot. Revision includes the maximum
session change cursor, timeline extent, and sorted automatic descriptors with
the actual binding hash, state/reason, job identity, archive state/schema and
result hash, plus validated mapping/cancellation status. It excludes mutable
queue age and update wall clocks. An older accepted archive row committing later
therefore changes the revision even if the maximum archive sequence is unchanged.
An incompatible cursor returns HTTP 409 `transcript_revision_changed`; a new
first-page request obtains the new view. Identical retries are deterministic.

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

- At most 1,024 automatic binding descriptors across the entire session and
  4 MiB cumulative binding bytes, each binding at most 4 KiB. Every page checks
  this session-wide limit; pagination cannot bypass it. Larger or arbitrarily
  long automatic sessions may require the existing primary and result-history
  endpoints instead.
- At most 128 nearby chunks and 128 intersecting whole primary units, with
  256 KiB cumulative primary text-field projection
- At most 16 intersecting committed MOSS payloads, 256 KiB each and 2 MiB
  aggregate; metadata at most 16 KiB per result
- At most 1,024 candidate-to-primary alternative links; all links are retained
  or the page explicitly refuses the projection
- Response at most 256 KiB, including all inline alternatives
- One staging operation at a time, and the existing shared response pool holds
  a 768-KiB response reservation until send completes or fails
- One query-only read transaction, zero busy wait, 250,000 SQLite VM steps and
  a 0.75-second cooperative read deadline

SQL uses `chunk_timeline`, `segments_chunk`, `auto_session_seq`, the archive's
unique job index and `result_changes_session`. Payload lengths are checked before
materialization. Binding blobs are processed one at a time; no audio/proof blobs
are selected. No full change-history traversal or unbounded overlap closure is
performed. A storage limit returns an explicit 503 `unresolved_capacity` (or a
bounded storage/read-capacity error), never a selected reading from a truncated
candidate set. The 60-second block width is fixed: this API has no smaller-window
retry. A dense block can fail even when adjacent blocks are readable. Clients may
inspect exact evidence, but must not treat the failed page as an empty or complete
transcript. The endpoint DAG has at most 32 endpoints; the selection policy and
all aggregate byte, linkage, response, SQL and time caps remain fixed.

Strict malformed query/reference/cursor requests return 409. Missing/deleted
sessions return 404, access failures use the existing 401 policy, and response
pool exhaustion returns 503. The app's lifetime wrapper releases leases on
disconnect, send failure and cancellation, including late synchronous handoffs.
