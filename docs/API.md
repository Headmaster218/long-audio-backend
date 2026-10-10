# Local incremental audio API

Run **exactly one** Uvicorn process, bound to `127.0.0.1`; never use `--workers > 1` or reload in production. A cross-platform exclusive lock prevents simultaneous instances using one data directory. Import `audio_backend.app:create_app` for explicit `Settings`. No remote audio URLs, URL imports, cloud transcription, or paid model calls exist.

## Storage and upload

- `POST /sessions` JSON `{ "name": "day 1", "identity_group": "default" }` returns `id`.
- `PUT /sessions/{id}/chunks` sends **raw file bytes**, not multipart, with `Idempotency-Key: a-client-generated-key`. Only one active upload is admitted. The default queue permits 64 pending work units: one independent upload or one explicit continuous stream is one unit. Each stream permits at most 128 unfinished receipts, including recoverable failed receipts; excess gets 429. See [queue accounting](QUEUING_AND_CANDIDATE_LEDGERS.md). Audio remains on disk until explicit deletion.
- Optional `?timeline_start=86400&recorded_at=2026-10-08T10:00:00Z` puts a recording on the original timeline. Offsets are seconds within a session; timestamps do not silently determine offsets. Omitting offset appends after the last retained chunk. Overlapping ranges are rejected. Gaps are valid. `recorded_at` stores provenance; record your desired timezone explicitly.
- Identical bytes with the same key/session return the original chunk without duplicate processing. Changed bytes, stream membership, original stream_final, recorded_at, or a different explicit timeline_start with that key return 409. Omitting timeline_start on a retry uses the original allocated interval, never a new append position. Retrying requires retransmitting bytes so the hash can be verified. Keys are not filenames and cannot influence filesystem paths.
- Uploads stream directly to a random `.part` file, with incremental SHA-256, byte and disk-reserve checks. Default maximum is **8 GiB per uploaded file**, not per session. A 48-hour mono PCM16/16kHz file is approximately 5.15 GiB. Multi-day/stereo/high-sample-rate sources should be compressed or split into incremental uploads.
- Supported FFmpeg demuxers: WAV, FLAC, Ogg, MP3, MP4/MOV, Matroska/WebM, AAC. Local audio playlists are not accepted. File type and finite duration are validated with ffprobe; input protocol/demux allowlists prevent playlist-driven remote/local fetches.
- For a single 16 kHz PCM WAV or FLAC audio track, admission uses the exact rational sample count rather than the rounded six-decimal container duration. Other codec/rate/container durations remain estimates. Admission does not perform a whole-recording PCM decode or rewrite existing receipts.
- A successfully returned 202 means durable enqueue, not completed analysis. `/health` only verifies API liveness and reports worker/memory/disk state. Validate downloaded models before accepting work.

## Progress and results

- `GET /chunks/{id}`: state, duration, `next_offset` checkpoint, timeline origin, errors, last-window model metadata.
- `GET /sessions/{id}/chunks?after=0&limit=100`: ascending integer cursor pagination, up to 500 rows. Use returned `next_cursor`; it is not a timestamp.
- `GET /sessions/{id}/segments?after=0&limit=500`: timestamped text, speaker ID, local window speaker ID, confidence, match score and status. Use returned integer `next_cursor`, maximum 1000 rows. Segments are append-ordered; processing out-of-order chunks means consumers must sort by `start` to display the timeline.
- `GET /sessions/{id}/events?after=0&limit=500`: independent event cursor with label, score and global timestamps. Consecutive events can represent the same continuing sound, and overlapping event labels are possible.
- Segment/event timestamps are original-session seconds, never window-relative. Chunk provenance associates them with recording dates.

Default ASR cores are at most 30 seconds with up to 2 seconds of context per side. Lexical units retain native timing, with independent per-source overlap alignment and preferred neural-speech-gap cuts. Words are not clipped to artificial core boundaries; events and every simultaneous raw speaker turn use interval intersection. Default independent uploads share no audio context. Explicit streams follow the contract below. Recognition/timestamp errors remain possible; see [boundary assembly evidence](BOUNDARY_ASSEMBLY.md). The opt-in stateful native diarizer has additional batch/frontier buffering described below.

## Retry, pause and restart

`POST /chunks/{id}/pause`, `/resume`, `/retry`, `/cancel` change durable state. Retry is allowed after failure; resume after pause. Cancel retains input and completed results. A pause/cancel interrupts a running subprocess, discarding the uncommitted window. Current window is retried from its start when resumed. Already committed windows are not rerun.

A window's segments, events, speaker changes and next-offset checkpoint commit in one SQLite transaction. On restart, previously running jobs requeue at the last successful boundary. Under the single-instance lock, unfinished temp files and unreferenced uploaded files are removed. A persistent analyzer subprocess is reused for all windows of one uploaded chunk, then closed. Model phases may deliberately reload sequentially to bound memory.

Errors are persisted (up to 2000 characters); no infinite automatic retry loop hides repeated bad inputs or missing models. Fix the underlying problem, then retry. Defaults allow 1200 seconds per inference window, 180 seconds per FFmpeg extraction, and 60 seconds per probe. This bounds hangs but does not promise realtime throughput.

## Speaker identities and corrections

Speaker embeddings are actual model-generated vectors. Missing/invalid embeddings produce `unknown`; capacity exhaustion produces `unknown_capacity`. Every automatically created or matched unnamed identity stays `provisional`. Naming it confirms a human-reviewed label; it does not validate future automatic matches.

Sessions in the same `identity_group` (default `default`) share identities across recordings/days. Give unrelated households or datasets different group names. Groups are logical scoping, **not multi-tenant security boundaries**. This is a single-user service.

The default cosine threshold .75 with .10 margin over the second distinct identity is a tiny-sample heuristic, **not a calibrated accuracy claim**. Different voices, languages, microphones, noise, overlap and age can cause false merges or splits. Unknown speakers must not be forced onto the nearest identity. Embeddings below the separate novelty threshold (.45 by default) can create provisional identities; the .45–.75 gray zone stays unknown. Strong but insufficient-margin matches remain unknown without creating another template. No automated averaging updates embeddings, avoiding uncertain-match drift.

- `GET /sessions/{id}/speakers` lists the group's identities and aliases without exposing embedding vectors.
- `PATCH /sessions/{id}/speakers/{speaker_id}` with `{ "name": "Alice" }` supplies a human label.
- Same endpoint with `{ "merge_into": "target-id" }` corrects a split. All source voice exemplars remain usable for future matching, grouped by canonical identity; the margin compares distinct people, not their aliases.
- `{ "undo_merge": true }` removes a source's merge mapping. Existing source-tagged segments resolve separately again. Future segments created while merged may require individual reassignment.
- `PATCH /sessions/{id}/segments/{segment_id}` with `{ "speaker_id": "id" }` reassigns a mistaken segment; `null` makes it unknown. Corrections are audited in SQLite. These corrections remain mutable; automatic decisions are not permanent truth.
- The default per-group bound is 256 templates, including merged aliases. The historical settings name is `max_speakers_per_session`; the actual bound applies to the shared identity group.

## Privacy, retention and limits

`DELETE /sessions/{id}` deletes uploaded audio and extracted results after work has been stopped and released. It leaves shared speaker identities available to other sessions. `DELETE /identity-groups/{group}/speakers` explicitly forgets that group's voice templates/labels and nulls existing speaker assignments. Wait for the worker and uploads to be idle first. These are destructive administrative operations; applications should ask their users before invoking them. Deletion is not forensic secure erase and does not remove independent backups or copies already downloaded.

Identity-group reset also removes global-person hints from current and historical overlap conditioning evidence and redacts identity fields in the correction audit. Local source IDs, raw simultaneous turns, source/turn links, support intervals, transcripts and candidate phrases remain. In the change feed, historical speaker snapshots become same-ID delete tombstones; historical segment/turn assignments and candidate conditioning hints are anonymized in place. Existing cursor numbers are retained. Normal new speaker tombstones and safe current result upserts notify already caught-up clients; replaying from zero cannot recover the forgotten labels. Previously downloaded client copies and backups are outside this erasure boundary. Processing later audio can create new provisional identities.

A 2 GiB free-disk reserve, 6 GiB service-tree RSS budget and 512 MiB host-available-memory reserve are checked on admission and about every 100 ms during subprocess work. Only one inference runs at a time. There are no in-memory job queues; all result pages, analyzer outputs, subprocess output and speaker searches are bounded. Byte size and queue count also bound admission. No GPU is configured by default (VRAM usage zero).

**Polling is not a hard memory sandbox.** Brief spikes can exceed the sampled threshold. For a guaranteed 6 GiB cap, deploy within an OS-enforced cgroup/container/systemd memory limit or Windows Job Object, and account for the full descendant process tree. Keep the stated deployment limit below 10 GiB and leave host headroom. API crashes are additionally handled by an inference-child parent-liveness watcher and a FFmpeg/ffprobe supervisor that checks parent PID plus creation time and kills descendants. These are best-effort portable cleanup; OS process-group/container supervision remains necessary for deterministic cleanup even if supervisors are themselves forcibly killed. Windows needs actual testing, FFmpeg on PATH, and the built native inference binary; portable Python paths alone are not evidence of a validated Windows deployment.

`Settings` also exposes `inference_device` (`cpu` or `vulkan`), `asr_model` (a model filename), `whisper_cli`, `language`, and `cpu_threads` (1–4). GPU mode is untested here, and the RSS watchdog does not measure VRAM. Do not claim its aggregate RAM+VRAM budget is validated.

Set `AUDIO_API_KEY` to require `Authorization: Bearer ...` on all routes, including docs and health. Set `AUDIO_DATA_DIR` and `AUDIO_MODEL_DIR` as needed. Defaults bind nowhere by themselves; Uvicorn CLI binding determines exposure. Keep localhost default. Do not place an unauthenticated instance on a network. Use an authenticated TLS reverse proxy and body/rate/connection limits if you deliberately expose it. This service does not implement per-user permissions or TLS.

## Tests

`python -m pytest tests/test_api_storage.py` covers checkpoint restart, duplicate keys, overlap rejection, timeline offsets, bounded queue, cross-session identities, corrections/merges/undo, alias exemplar matching, resource rejection, context ownership, retry cursor, stable pagination, orphan cleanup, auth, size validation, and real FFmpeg with an **explicitly fake analyzer**. These tests establish service mechanics, not ML quality. Run independent real-model validation for transcription, diarization and event quality.

## Per-segment provenance and schema upgrades

Segment responses now include nullable `asr_backend`, `timestamp_kind`, and `raw_text`. The backend/timestamp qualifier is captured from that specific segment or its actual analysis window; later windows cannot relabel previous results. `raw_text` preserves the analyzer's supplied text before Chinese-script normalization when available. Null means provenance was not supplied or the row predates this schema; historical backend/timestamp values are never guessed from the latest chunk metadata.

`GET /chunks/{id}` keeps `metadata` as the honest last-window snapshot, including a trailing silence fast path. Its separate `provenance.variants` retains up to 16 distinct non-silence stable configurations, with first/last window offsets and counts. Configured models in a silence fast path do not imply those models ran. Dynamic timings/language/turn details stay in last-window metadata. `variants_truncated=true` explicitly signals more configurations than the retained bound; each stable entry is bounded to 32 KiB. Use per-segment fields for ASR backend/timestamp attribution, not the latest chunk backend. Database opening adds the optional columns without rewriting existing rows or guessing old provenance.

If a speaker embedding clears the match threshold but lacks a sufficient margin between candidate identities, it returns `unknown_ambiguous` with the best score and no assigned identity. Such ambiguity no longer creates additional near-duplicate templates. A score below the separate novelty threshold .45 may create a provisional identity; scores in [.45, .75) return `unknown_uncertain` without a new template. Real-world voice changes can still split identities until reviewed. This deliberately favors uncertainty over forced identification.

The .45 novelty threshold is a configurable tiny-sample heuristic, not calibrated proof that two voices differ. Keep `speaker_new_threshold <= speaker_match_threshold`. The API can name/merge existing provisional identities, assign unknown segments to a known ID, and explicitly enroll a new named identity from an eligible unknown segment as described below. Unknown segments are not silently inferred to be the same person.


## Explicit human enrollment from an unknown voice

`POST /sessions/{sid}/speakers` with `{ "name": "Alice", "source_segment_id": 123 }` explicitly creates a human-confirmed identity from an eligible unknown segment and assigns that selected segment. Other historical unknown segments are not silently reassigned. Future matching uses the new learned voice template under the same conservative match/margin thresholds.

Segment results expose `enrollment_available`, never the private embedding or observation ID. The service retains one private learned embedding per unknown local speaker/window, shared by its text segments, only when the analyzer supplied a valid non-overlap embedding. Overlapping speech, missing/invalid embeddings, pre-migration results, and observations beyond the capacity have no enrollment candidate. Enrollment rejects a source outside the specified session/group, a source already assigned to an identity (use rename/merge), an observation already enrolled (assign the existing identity), or a full identity registry. It accepts no arbitrary vector input and does not rerun models or force the nearest match.

Private enrollment observations are bounded to 4096 per identity group by default (`max_speaker_observations_per_group`), indexed and disk-backed. Additional unknowns remain unknown with `enrollment_available=false`. Deleting their session removes observations; the group's explicit speaker privacy reset removes all its observations and clears enrollment references. An additive migration leaves older segments unenrollable unless a valid observation was retained; it never fabricates embeddings. Human enrollment is a user assertion of identity, not a calibrated biometric verification claim.


## Explicit continuous recording streams

A session is a timeline and identity scope, not evidence of acoustic continuity. By default every upload is a separate recording, even when its timestamp immediately follows the previous file or it shares an identity group across days.

For fragments cut from **one uninterrupted waveform**, pass `stream_id=meeting-2026-10-08` on every upload. A stream ID is scoped to its session and contains 1–128 ASCII letters, digits, underscores, periods or hyphens. Upload in recording order. Server checks adjacent timeline intervals (one-millisecond numeric tolerance), rejects gaps/overlaps and rejects appending after finalization. Contiguous timestamps alone never opt a file in. Encoded fragments must decode into genuinely adjacent audio; container encoder padding or repeated audio supplied by a client cannot be reliably inferred from timestamps.

- First fragment: `PUT /sessions/{sid}/chunks?stream_id=meeting`, unique Idempotency-Key.
- Subsequent fragment: same `stream_id`, a new key. Default timeline allocation appends, or explicitly supply the correct timeline_start.
- Last fragment: add `stream_final=true` to its initial upload.
- If the last nonfinal fragment was already uploaded, call `POST /sessions/{sid}/streams/meeting/finish`. This operation is idempotent, requires no empty audio upload, and wakes a held tail. Repeating the original upload remains valid after finishing; changing its original stream_final value is not a finalize operation.

The worker reads bounded adjacent PCM from only that declared stream. It holds an **uncommitted tail**, not a publicly published provisional transcript. A chunk can enter `waiting_input` while it needs successor context or finalization. `next_offset` is its durable committed checkpoint; the remaining interval is pending. Published text/event rows are final with respect to automated boundary processing, although model output can still be wrong and human speaker corrections remain possible. An ended stream must be finalized to release its last tail; silence, elapsed time, another day's recording, or restarting the service does not imply finalization. Pause/cancel and retry do not discard committed text. Stream membership and checkpoints survive restart.

No whole-stream PCM accumulator is retained: decoding uses bounded windows plus configured adjacent context, regardless of stream hours. The durable pending queue includes waiting_input jobs. Original encoded input and result history remain disk-backed until session deletion. RAM polling is a sampled process-tree safeguard, not a hard instantaneous cap or GPU-memory measurement.

## Durable result changes and consumer synchronization

`GET /sessions/{sid}/changes?after=0&limit=500` is the authoritative replay feed for clients that need corrections and progress. The maximum page is 1000. It returns `changes`, `next_cursor`, `high_watermark`, and `has_more`. Each change includes a monotonic integer `cursor`, `entity`, string `entity_id`, `operation` (`upsert` or `delete`), a public entity `data` snapshot for upserts (null for tombstones), and a server `created` timestamp. Entities include segments, events, chunks, speakers, and stream_chunks. Private voice vectors and filesystem paths are excluded. Chunk change snapshots contain progress/contract fields; fetch GET /chunks/{id} for bounded model provenance and last-window metadata, which are not repeatedly copied into the journal.

Apply changes in cursor order and save the returned next_cursor only after applying the whole page. Segment change snapshots retain the stored speaker_id: resolve speaker merged_into links from the speaker snapshots to render the current canonical identity (and re-render on merge/undo). The regular segments endpoint performs that resolution and supplies derived enrollment_available; fetch it when reviewing enrollment options. Upsert by `(entity, entity_id)`, delete on a tombstone, and tolerate repeated pages after client interruption. Cursor gaps are normal across sessions. Poll again after catching up; a high_watermark is a point-in-time observation, not a promise that processing finished. Original-session `start`/`end` remain unchanged by speaker corrections. The feed transactionally records the same database writes as checkpoints, assignments, names, merges and resets. A rolled-back write produces no durable change. Public row snapshots are replayable without racing a subsequent lookup of a mutable row.

The segment/event `after` cursors remain useful for append-only reads; **they cannot detect updates to earlier rows**. Use the changes feed when displaying human corrections. Identity changes are broadcast to existing sessions in the identity group. A newly created session should additionally fetch its current speakers list before rendering reused group identities. Legacy databases receive one initial public-row snapshot when the change-feed schema is first installed. Journal history grows on disk, not in RAM; session deletion erases that session's historical snapshots as well as its current results. No automatic retention window silently invalidates a saved cursor. Back up the SQLite database together with source audio for recovery.


## Optional stateful native diarization

`--diarization-backend nemotron --native-diarization-model PATH --native-diarization-library PATH`
selects the separately installed, hash-pinned native backend. Default remains
`pyannote`; `/health` reports the selected backend. No automatic model download
or silent backend fallback occurs. Review the separate OpenMDW-1.1 model license
and native runtime license before deployment. See `reports/nemotron-trial/README.md`
for quality failures as well as successful measurements.

The evaluated native v0.2.0 preset emits 21.12-second batches with 0.08-second
right context, with an additional conservative two-second segmentation stability
guard. This is not 30.4 seconds of future lookahead. A live stream may initially
commit approximately 19.12 seconds after the first complete batch; its unpublished
tail can therefore be larger than the ordinary two-second ASR context.
`waiting_input` never implies discarded audio. Explicit finalization flushes the
true remaining source. Short independent recordings finalize as standalone sources.

One monitored native child handles one continuity scope at a time. Native input
is monotone, with no refeeding of ASR overlap. Each 300-second owned epoch uses up
to 21.2 seconds of preroll and at most 360 seconds of total input including bounded
lookahead. Restart recreates only that epoch from original disk-backed audio;
model/runtime fingerprints must match. A file boundary, ASR core boundary, or epoch
reset never masquerades as end-of-stream. Published prefixes remain immutable.

Native scheduling uses integer 16 kHz frames relative to the source/stream, without
rounding its absolute session timeline origin. Each native push must contain the
requested frame count and advance the native input by that count. Decoding uses
FFmpeg `-xerror`: a decoder failure is not treated as clean end-of-source. For
stateful native processing, at each whole source's declared end (not an ordinary
core/window boundary), the bounded decoder requests one extra sample and checks
the emitted count. A short clean
decode or extra emitted sample fails explicitly with a source-duration mismatch;
it does not pad silence, silently truncate the tail, or change declared stream
times. Committed checkpoints survive this failure and retry.

Legacy non-native decoding retains its requested-duration behavior and measured
resampled input ranges, including mixed-rate/channel source support. It does not
gain the native path's exact-frame endpoint check. Strict decoder-error handling
still applies to both paths.

This is a fail-closed duration check, **not generic compressed endpoint
reconciliation**. Valid AAC encoder priming/edit lists, resampling, or containers
whose duration differs from their audio track can still cause a mismatch and
require an explicitly prepared, verified 16 kHz source. The bounded seek decoder
also does not certify whole-file sequential decode integrity or reconstruct
discontinuous packet timestamps. Old continuous-stream receipts can accumulate
sub-sample rounding drift across many files and also fail the exact frame-count
check; their already allocated timeline intervals are not rewritten. Those cases
remain unresolved; a successful upload alone is not proof of a complete decoded
timeline.

Native output has eight local speaker slots per epoch. These are not eight global
people or an unlimited simultaneous-speaker claim. Ten-millisecond native frame
cadence is not guaranteed ten-millisecond timing accuracy. Simultaneous raw turns
remain separate in `/sessions/{sid}/speaker-turns`; excluding overlaps from clean
voice-profile evidence does not delete those turns. Source-separated overlapping
words are a separate experimental branch, not guaranteed by native diarization.

Each source track's lexical alignment is independent. A matched word in one track
cannot deduplicate another track's simultaneous content. Audio mixing/separation
quality and cross-window source identity still need real-data validation.

The native diarization provider is explicitly CPU-only (`gpu=-1`) in this tested
integration, including when `--device vulkan` selects Whisper acceleration.
Swapping in a Vulkan-capable native DLL/shared library does not enable native GPU
inference. A future native GPU option requires explicit selection and target
hardware validation. Linux native CPU execution was exercised here; Windows
runtime availability is not a claim that this Windows integration was tested.

## Experimental overlapping-source candidates (default off)

Ordinary mixture transcript and independent raw speaker turns are never replaced
by this branch. Read its separate, unverified evidence from
`GET /sessions/{sid}/overlap-candidates?after=0&limit=100`.

- `--overlap-backend mossformer2_experimental --overlap-python PYTHON` selects the
  optional two-source separator. `--overlap-source-dir DIRECTORY` overrides its
  pinned local implementation location. Default processing uses official 2-second
  model windows; `--overlap-window-mode direct_bounded` remains experimental.
- `--overlap-backend dicow_en_experimental --dicow-python PYTHON --dicow-model-dir DIRECTORY`
  selects English-only target-conditioned ASR. Chinese, mixed, and unsupported or
  unevidenced language remain unresolved; no automatic Chinese fallback is implied.
- `--overlap-backend moss_td_experimental --moss-td-python PYTHON --moss-td-model-dir DIRECTORY`
  selects CPU BF16 multilingual local-speaker text hypotheses. Model times and
  speaker labels are unverified. See [MOSS-TD setup and measured failures](MOSS_TD_SETUP.md).
- The default is `--overlap-backend none`. Enabled missing runtime, weights, or
  source files cause a configuration error at startup. `/health` reports
  `overlap_status`, `overlap_experimental`, and the scope/errors of file readiness.
  File readiness does not certify imports, model execution, or recognition quality.

Each analyzer call passes its planned owned-core bounds separately from available
recognition/enrollment audio. Context-only overlaps cannot consume that core's
candidate budget. The older separator/conditioned-ASR support is capped at 12 seconds total
per analysis window; unprocessed regions retain explicit budget, capacity,
missing-evidence, or failure reasons. The original bounded audio and all raw turns
remain available for clean evidence selection. No separated embedding enrolls,
updates, or confirms a global person.

MOSS-TD has a separate default 40-second support budget per core (configurable up
to 60), at most four disjoint supports, each at most 20 seconds. Supports end at
the same actual speech-gap cut used by the primary worker, not a later-discarded
nominal boundary. Every valid parsed phrase in a gated support is retained even
if its unreliable model interval misses all native overlap intervals. Its
`association_basis=support_not_model_timing` links are support associations,
not verified phrase placement. Original coarse endpoints remain unchanged;
`support_owner_anchor` is separate routing metadata, never a replacement time.

Default `--moss-td-trigger native_overlap` requires detected concurrent activity.
Optional `--moss-td-trigger speech_probe` also attempts a support containing
native speech when no native overlap row covers it. Such rows have
`region_kind=speech_support_probe`, `detected_source_count` equal to the native
peak count (at least one), and whole-support bounds. They **do not assert a real
overlap**. Existing/native rows use `region_kind=detected_overlap` and require at
least two detected sources. `support_activity` preserves peak and distinct native
counts; nullable `model_label_count` and track provenance report model counts
separately. Additional labels may be hallucinations. Probes increase computation,
not identity certainty, and never repair, enroll or approve a voice profile.

MOSS local slots 0..7 are a schema/runtime safety bound, not validated eight-person
recovery. All phrases carry minority-loss uncertainty; three-or-more labels also
have an explicit multi-speaker quality warning. Failed supports retain reason,
nullable `schema_error_count` and `raw_output_sha256`; malformed generated text
and token IDs are temporary diagnostics and are not promised as durable results.

Regions retain core-owned intervals plus their original model support. Candidate
tracks retain whole native units and unresolved identity/unverified quality.
Approximate CTC intervals may intersect overlap even when their emission anchor
slightly precedes detector onset; they are not discarded merely for that small
difference. Source-track ownership is independent, including identical text from
two sources. Window-local source slots are never silently joined across windows.

For DiCoW, `native_phrase_intervals` preserve exact native phrase text/start/end
and explicitly marked overlap-evidence routing anchors in session-absolute time.
A routing anchor is for ledger ownership, not a claimed word boundary. Shared
support phrases are stored once and linked only to overlap regions supported by
units actually owned in that core. Boundary-uncertain or outside-core evidence is
marked explicitly rather than represented as a confirmed recovered phrase.

See [overlapping-speech behavior and evidence](OVERLAPPING_SPEECH.md). Model quality
is uneven; this endpoint does not establish that every simultaneous voice was
recovered.

## Stream-history lookup scaling

Continuous-source extraction uses an internal derived time index with immutable
source bounds. Lookup seeks directly to the requested time and stops at the first
future fragment, without scanning old or future stream history. The one-time
atomic migration does not rewrite public upload receipts or create change-feed
updates. `reports/stream-time-index-scale.json` records 60-second lookups against
10,000 and 100,000 fragments: approximately 2,400–2,600 SQLite VM instructions in
both cases, with indexed search plans and no full scan. This validates lookup
scaling, not recognition throughput for very small upload fragments.

## Exact acoustic-support review (Phase A)

Capture is off by default; explicitly use `--speaker-support-review` to enable
the bounded Phase A review path. Read-only support discovery is available for a segment or independent speaker
turn under `/sessions/{sid}/segments/{id}/speaker-support` and
`/sessions/{sid}/speaker-turns/{id}/speaker-support`. Safe immutable metadata and
hash-checked full float-WAV audio are addressed by the returned session-scoped
support ID. This does not certify purity or enable identity learning. Legacy
rename/merge/assignment and enrollment semantics are unchanged. See
[the support review contract](EMBEDDING_SUPPORT_LINEAGE.md) for endpoints, strict
quotas, unavailable reasons, independent retention and erasure behavior.

## Experimental verified-review groups

New groups may explicitly select `identity_mode: verified_review_v1` when exact
review capture and an experimental policy are enabled. Legacy groups never
convert implicitly. See [the full review API and mandatory compact invalidation
protocol](VERIFIED_IDENTITY.md) before using this mode. An `identity_bank` change
invalidates prior automatic assignments by revision; reset clears human labels
too. Neither event requires millions of per-result updates.

Verified groups use `POST /sessions/{sid}/identity-relations` for explicit
`same_person` merges or `different_people` constraints, with original person IDs,
`confirmed: true`, current generation/revision and an idempotency key. Retract an
exact relation event at `POST /sessions/{sid}/identity-relations/{event_id}/retract`
to undo it. `GET /sessions/{sid}/identity-relations` is cursor-paginated audit
history. The legacy speaker PATCH merge form is unsupported in this mode.
Merges never verify pooled audio or enroll references. Contradictory evidence,
explicit different-person constraints and more than eight combined active
positives reject the operation without deleting evidence. See the full protocol
for reversible original-owner semantics and its evidence limits.

Verified speaker lists/feed snapshots include `canonical_person_id`; result
GETs/feed snapshots include stored `original_speaker_id` and effective `speaker_id`,
with current `canonical_revision` and `canonical_state`. On an `identity_bank`
event with `refresh_canonical_persons: true`, refresh mappings and re-project
cached human labels from their original IDs, as well as clearing stale automatic
assignments. Never treat an old original ID or an idempotent mutation response as
the current effective identity. Legacy feed alias-resolution behavior is unchanged.

## Opt-in finalized PCM16 integration slice

An explicitly configured embedding application may select the experimental
`processing_mode=fragment_pcm16_v1` when creating a session. This mode is disabled
without an injected provider factory or the explicit pinned CPU-manifest launcher
described in [the experiment guide](FRAGMENT_HTTP_EXPERIMENT.md). No neural
provider is selected by default. It
accepts one finalized, original 16 kHz mono PCM16 stream with zero trimming.
See [the complete experimental contract](FRAGMENT_HTTP_EXPERIMENT.md) for
admission, evidence-backed public rows, controls, atomicity and limitations.
Legacy sessions and their streaming behavior remain the default.
All identity-review and canonical-person relation mutations are explicitly
rejected for fragment sessions, including review replacement/retraction and
relation retraction. These guards precede the underlying mutation functions;
an unrelated verified-mode group in the same application remains usable.

### Private ASR input coverage receipts

For the explicit CPU provider, existing private evidence now contains an
`asr_input_coverage` ledger: actual integer-frame input ranges, separate
`execution_started` and confirmed-return/completed receipts, the current core's
successful-input union, and its half-open complement (`gaps`). No public response
field or route is added. This ledger exposes omitted or unsuccessful/unconfirmed
input; it does not fix recognition accuracy or automatically restore missing
words. Unsubmitted input is not silence, and successful call completion is not
correct transcription. Text, diarization and finished session state cannot fill
gaps. Canonical-person and fragment identity guards are unchanged. Rebuild the
explicit CPU manifest for the changed module closure before starting this version.
See [the exact coverage and recovery contract](ASR_INPUT_COVERAGE.md).
