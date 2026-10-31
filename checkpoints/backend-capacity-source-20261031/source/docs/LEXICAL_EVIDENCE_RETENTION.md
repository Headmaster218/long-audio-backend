# Prospective lexical inventory retention (candidate)

`Settings.lexical_evidence_enabled` is a strict boolean, default `False`. This
persistence-only slice retains complete existing post-adapter SenseVoice CTC
contributions in nullable `segments.lexical_evidence`. Historical rows stay NULL.
Retention alone does not enable granular reading or change default transcript
selection. The separate explicit `projection=lexical-estimated-v1` time-page view
can use current inventories; see [Lexical transcript projection](LEXICAL_TRANSCRIPT_PROJECTION.md).
Default whole-primary policy/cursors and component closure stay unchanged.
The private `l.<segment-id>.<inventory-sha256>` identity is versioned separately;
existing `p.*` identities and all `PRIMARY_FIELDS` remain unchanged.

## Exact observations, not new alignment

The existing SenseVoice adapter shifts native starts by inner decode context,
rounds to integer milliseconds, joins Latin pieces with `join_ctc_words`, then
`align_tokens` clamps starts/estimated ends and may extend zero-length intervals
or shorten estimated ends at a speaker turn. The adapter independently applies
OpenCC to each unit and to the old assembled segment. `lexical_core` then owns
whole units, adds the analyzer origin (`base`), and constructs current primary
text from the selected post-conversion unit strings. This inventory observes
that final assembly; it never reruns OpenCC or guesses an inverse conversion.

Raw and display streams are assembled and stripped independently. Contribution
spans are Python codepoint offsets in those final strings; leading/trailing
whitespace can leave an empty contribution. Units are never split. Native-phrase
spacing remains unchanged in primary assembly, but non-CTC/native-phrase windows
are not retained. The private inventory preserves start, estimated end and
ownership anchor exactly after the existing transformations. It checks the
original arithmetic order for segment placement. Seam alignment can retain a
unit before the current core, so negative core-relative overhang is preserved.
These are not frame-exact emission timestamps or certified word ends.

Unsupported Unicode/grouping remains retained when structurally valid but has
`granular_eligible: false`. The opt-in lexical view revalidates that field and applies stricter grouping
and boundary rules; it is not selection authority by itself. ASCII whole-unit groups and
single supported CJK/punctuation codepoints are the narrow eligible cases;
combining sequences, controls, emoji/ZWJ, non-ASCII multi-character groups and
internal whitespace are ineligible. No segmentation or normalization is inferred.

## Source provenance

Only the <=16 contributors already selected by `context_parts` are observed.
Their current stored chunk digest, session, duration, timeline placement and
requested seek/duration are snapshotted before the existing decode. Measured
concatenated analyzer frame ranges are added during that same extraction.
Requested source ranges and measured analyzer ranges remain distinct. No file
hashing, fresh PCM attestation, model rerun, audio reconstruction or historical
scan is added. Ordered unique contributor geometry is checked, including the
first requested position versus session origin + analyzer origin. Current rows
are compared again in the commit transaction and by the current-inventory validator.

## Atomic compatibility and bounds

All inventory rows and their byte charge use the existing primary
`BEGIN IMMEDIATE`, status and `next_offset` guard. Inventories bind the actual
assigned primary IDs/content hashes. Entire-window preparation and admission
happen before any inventory update; a rejected segment/source/cap rejects the
whole optional window. Missing, malformed, unsupported or over-cap inventory
leaves valid primary text committed normally with a fixed bounded status. SQLite
errors and transaction failures propagate, rolling back primary and inventory
together. A retry after rollback may admit once; a retry after committed but lost
acknowledgment fails the unchanged offset guard without charging again.

Caps are 512 units and 64 KiB canonical ASCII JSON per segment, 4096 units and
512 KiB per commit, 16 source contributors, and 32 MiB admitted bytes per session.
`sessions.lexical_evidence_bytes` is a monotonic admitted-byte total: inventory
revocation/deletion does not refund it. No eviction, backfill or reconstruction
is attempted. Canonical parsing is length-first and rejects duplicate keys,
unsupported schema shapes and nonfinite numbers. Optional raw text/spans do not enter legacy result diagnostics. The authenticated
lexical range evidence route returns bounded exact contributions on explicit request.

Migration is additive and rejects incomplete or incompatible shapes. Existing
exact transcript revision v1 trigger SQL is untouched. A separate inventory
UPDATE trigger invalidates transcript revisions, and a separate primary-field
edit trigger revokes stale inventory. Speaker-person assignment alone does not
change primary identity. Contributor source edits make the private validator
return stale while retaining the original admitted charge. Restoring exactly
the same declared source binding can make that content-bound inventory current
again; it is not an independent source-mutation history. Legacy `Store.results`
and `Store.session` hide the new storage-only fields.

## Focused qualification scope

Tests use only fresh synthetic SQLite, strings and numeric geometry. They cover
exact assembly, post-conversion raw/display differences, Unicode refusal for
future granular eligibility, context overhang/nonzero origin, source/timing
forgery, historical NULL, exact revision-trigger compatibility, rollback,
retry/lost acknowledgment/restart, source staleness, primary revocation,
non-refunded quota, malformed/canonical input, local caps and actual near-32 MiB
admission followed by whole-window rejection. No ASR quality claim is made.
