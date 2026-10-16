# Immutable hour-file uploader (opt-in, Linux/POSIX-qualified)

The original `--final` mode streams **one finalized, immutable regular local file** into an
existing empty legacy session and a new explicit capture. It changes no API,
schema, primary analysis path, model, dependency, version, or default. Success
means all normalized PCM was accepted and EOS published/queued. Analysis can
still be pending. Functional fake-analyzer tests do not establish acoustic
quality, one-hour acoustic throughput, or real-time inference performance.
The opt-in `--append` / `--finalize` modes below add bounded, paged metadata for
separately normalized originals. Fresh journals use append-v3; existing v1/v2
journals retain their old policies without migration. The existing `--final` path and journals remain
separate and compatible.

```sh
export AUDIO_API_KEY='your-existing-api-bearer-token'
python -m audio_backend.recording_uploader \
  --input /absolute/finalized-recording.wav \
  --session-id EXISTING_SESSION --capture-id unique.capture \
  --capture-origin-frame 100003 --state-dir /absolute/uploader-state --final
```

The token is read from the existing AUDIO_API_KEY environment variable and is
never written to the journal. The default API is http://127.0.0.1:8765; --api
accepts an HTTP(S) origin, with HTTPS required outside loopback. Create the session
separately: session creation is deliberately outside this restart protocol.
Capture ID and origin are explicit; coordinates describe normalized emitted PCM
frames, not compressed sample indices, physical time, or a shared device clock.
Use an exclusive state directory and capture. Keep the original input outside
the state directory; reserved control-file aliases are rejected before writes.
Run the exact command to resume.
The source must remain at the same path with the same inode, size, times and hash.
A lost journal cannot adopt a server capture. Do not edit the journal.

## Formats and normalization

Canonical PCM16 mono 16 kHz WAV uses bounded wave reads. Ordinary local WAV
(PCM integer/float), FLAC, MP3, M4A/AAC, and Ogg/Opus can use the existing
/usr/bin/ffmpeg and /usr/bin/ffprobe. No binaries, models or packages are installed
or downloaded. Compressed normalization requires Linux /proc/self/fd: the held
regular-file descriptor remains seekable. Other platforms are unqualified.
Exactly one supported audio stream is required. Playlists, network sources,
arbitrary demuxers and multi-file decoding are rejected. The explicit demuxer and
protocol allowlist avoid playlist/network expansion; MOV external references are
not enabled.

One continuous FFmpeg decode selects the recorded audio stream, explicitly limits
threads, downmixes to mono, resamples to 16 kHz with asynchronous correction off,
and emits PCM16LE. No per-page -ss, duration-based EOF, gap fill, or padded silence
is used. The executable hash, bounded version/build hash and exact argument
recipe are journalled. Resume rehashes the input, requires the same tools/recipe,
and replays from the beginning, comparing every saved derived piece hash before
new ingestion. Canonical WAV also replays sequentially for a simple exact path.
Restart cost is O(original bytes + decoded prefix), with bounded memory and disk.

## Bounds and progress

- Original input: at most 8 GiB; emitted output: at most two hours
- Every input/PCM/upload/response read: at most 64 KiB
- Sources: at most 120 seconds; page: at most 294 seconds
- One full page: 9,408,132 bytes (three canonical WAVs), with a 12 MiB allocated
  spool/metadata budget and a 5.5 GiB free-disk safety floor
- Journal and HTTP JSON: at most 512 KiB each; stderr retains the last 8 KiB
- HTTP socket timeout: 30 seconds, with a cooperatively checked request
  deadline; blocking DNS/header processing has no independent hard wall timer.
  Metadata subprocess deadline: 30 seconds; total invocation deadline: 12 hours,
  checked between operations and including backpressure waits
- Sampled process-tree RSS guard: 512 MiB; use an external OS limit for a hard cap

--disk-floor-bytes and --deadline-seconds are explicit finite overrides. Tests
use isolated zero disk floors. The CLI has a single writer lock, one pending
page, no client work queue, and no whole-hour decoded spool or in-memory PCM
join. Closed source files are fsynced before use. Atomic journal checkpoints
precede requests and precede cleanup after publication. Only exact journal-named
generated files are deleted; the input and unrelated state-directory files stay.
Disk checks are reservations, not protection against other processes filling the
disk. ENOSPC or a resource failure leaves an incomplete prefix and a journal.

A page is allowed to advance only when released/published. pending_receipts and
in-flight preparing states retain its slot. Published work may still need later
context, so the uploader does not wait for each page's analysis completion.
Real 128-fragment admission backpressure produces identical frozen seal retries;
a failed/paused/cancelled worker that blocks admission is reported, not repaired
or cancelled automatically. Transport/5xx/429 retry lasts only within the total
deadline. Authentication, integrity/409 and disk/507 errors stop for recovery.

## EOF, retries, and provenance

The last page receives EOS only after actual EOF, whole PCM frames, clean decoder
exit, unchanged input identity and final input hash verification. Exact full-page
EOF uses a persisted capture finish request; no empty page/source is submitted.
Late truncation, odd PCM, decoder failure, cancellation or resource exhaustion
must not finalize the capture. Previously published pages remain on the server.
This is a resumable-prefix contract, not all-or-nothing validation of a compressed
hour before ingestion. Preserve the source and state to recover.

The journal contains original identity/hash; decoder recipe/build; exact emitted
frame ranges and derived WAV/PCM hashes; original request keys/bodies; returned
page/source IDs; and published frontier/EOF state. It is client bookkeeping, not
new native/server evidence. The server's source/range receipts remain authority.
Uncertain PUTs reconcile exact keys, hashes and placement. Seal/finish retries use
saved immutable bodies; idempotent replay verifies frozen identity even after a
published GET. Changed input, changed decoder or replayed bytes, missing accepted
history, unexpected sources, or conflicting seals stop without adopting them.

## Experimental contiguous append and explicit finalize

Select exactly one mode. `--final` retains the one-file behavior above. For
multiple immutable originals, use one exclusive state directory and stable IDs:

```sh
python -m audio_backend.recording_uploader \
  --append --input /absolute/hour-a.wav --input-id hour-a \
  --session-id EXISTING_SESSION --capture-id unique.capture \
  --capture-origin-frame 100003 --state-dir /absolute/append-state
python -m audio_backend.recording_uploader \
  --append --input /absolute/hour-b.wav --input-id hour-b \
  --session-id EXISTING_SESSION --capture-id unique.capture \
  --capture-origin-frame 100003 --state-dir /absolute/append-state
python -m audio_backend.recording_uploader \
  --finalize --session-id EXISTING_SESSION --capture-id unique.capture \
  --capture-origin-frame 100003 --state-dir /absolute/append-state
```

An append invocation accepts one finalized regular file without capture EOS.
The next invocation fills its retained partial page with separate pieces from
the next original. A source WAV belongs to exactly one original; sources are
never silently joined, extended, or relabeled. Partial pages stay local and are
not uploaded/sealed merely because some complete 10.5-second work units fit.
Full 294-second pages publish with `eos:false`. No original is deleted.

For a 3600-second first file, twelve pages publish (3528 seconds), and its last
72 seconds / 1,152,000 frames remain in a durable 2,304,044-byte WAV. The next
page uses A's 72 seconds, then B's first 120 and next 102 seconds as three source
WAVs. Two separate one-hour files yield 7200 seconds / 115,200,000 frames:
after B, 24 pages are published and a 144-second tail remains. Only `--finalize`
publishes page 25 with EOS. At an exact full-page boundary it reuses the existing
capture-finish API without submitting an empty page or source.

`--input-start-frame N` optionally declares the original's absolute normalized
capture-frame coordinate. A new ID defaults to the current next frame; a known
ID defaults to its original saved placement. At origin 100003, the second
one-hour input begins at 57700003. Explicit coordinates must match those exact
values. Gaps and distinct-input overlap are refused before registration/decode/
HTTP mutation. This clock is normalized emitted PCM, not a physical clock,
container timestamp, or proof of codec-gapless continuity. Each original has its
own continuous decoder/resampler invocation and clean EOF.

Repeat an append with the same ID and unchanged original to retry safely, even
after capture finalization. Changed identity, bytes, recipe, or coordinates
conflict. A different ID cannot reuse the same physical original. Identical PCM
in distinct originals at their next contiguous placements is permitted and is
not treated as an alias. If an input is incomplete, resume that input ID before
adding another. A lost journal cannot adopt an existing capture; v1 and v2
journals cannot migrate or reopen one another; a saved v2 append journal is
selected automatically and keeps its original limits. A v3 client never adopts
archives or an existing capture when its exact checkpoint is missing.

Append JSON distinguishes `input_complete`, `awaiting_input`, and
`capture_finalized`; `frames` includes staged input, `published_frames` includes
only released pages, and `staged_frames` is their difference. Nonfinal input
success does not claim capture completion. Finalize takes no input/ID/coordinate
and requires nonempty clean EOF for every registered original. Its intent is
saved before EOS, fences all new IDs, and survives interruption. Finalization
success still does not mean analysis is complete.

### Bounded v3 registry and recovery

Fresh append-v3 journals default to **12 logical hours and 64 original IDs**.
The duration is normalized emitted PCM, independent of the invocation timeout.
Caller order determines placement; names, dates and timestamps are not sorted or
used for automatic alignment.

- `--max-hours H`: integer 1..48, default 12 on creation.
- `--max-inputs M`: integer 1..128, default 64 on creation.
- `--max-original-bytes B`: aggregate read-only original-file bytes, default
  8 GiB, explicit finite maximum 64 GiB. This does not authorize server storage.
- Omit these options on resume to load their frozen values. An explicit conflict
  refuses before new decode or HTTP mutation. The v1 finalized-file mode rejects
  append-only options; saved v2 journals retain 2 hours / at most 32 originals
  and their old journal/reader behavior, with no automatic migration.
- At 48 hours: 2,764,800,000 frames, at most 588 pages, and 16,458 derived
  10.5-second work chunks. The final page is 222 seconds and its final work
  chunk is 24,000 frames. Page/source geometry remains 294/120 seconds.
- At most 16 original-owned pieces fit a page. The conservative total piece
  bound is 16 × configured maximum pages (9,408 at 48 hours). Input admission
  reserves enough remaining slots to finish the pending page. Many tiny originals
  can exhaust those slots; retry or explicitly finalize the existing tail.
- Checkpoint and HTTP JSON remain at most 512 KiB. One active input record is
  at most 8 KiB; one piece at most 1 KiB; page controls at most 2 KiB. An immutable
  released-page archive is at most 32 KiB, a completed-input archive 12 KiB.
  Paths are at most 4096 UTF-8 bytes; IDs at most 128 ASCII characters.
- One page contains at most 9,408,704 WAV bytes. The allocated PCM spool limit
  remains 12 MiB, with 32 MiB allocated metadata and 48 MiB total owned client
  state caps. Controls, directories, atomic temporaries, and retiring PCM count.
  Unsupported allocation geometry fails explicitly rather than enlarging limits.
- The independent 5.5 GiB free-space floor, 64 KiB I/O, 512 MiB sampled RSS guard,
  30-second HTTP timeout, and 12-hour invocation deadline remain unchanged.

The checkpoint keeps scalar counters/frontiers, one active input, one active
page, one archive transition, and one bounded retirement list. Completed original
metadata and released pages are immutable numbered JSON archives. All original
IDs/descriptors/recipes and source request/placement/hash/server receipts remain
available; this is client bookkeeping, not new server provenance.

Startup validates the entire local history and its committed terminal hashes
before any archive drives mutation. Two compact authenticity-only bytearrays
retain at most (588 + 128) × 32 = **22,912 bytes**, with no historical JSON record
or remote-response map. Every later archive read verifies its exact bytes again
against those terminal-checked bindings. A forward hash-chain prefix alone is
never treated as authenticated. Missing, reordered, altered, unbound, aliased,
or unexpected archive files fail closed and are preserved for diagnosis.

Archive intent is saved before writing, syncing, and atomically promoting a receipt without replacing an existing file.
The exact two-name hardlink crash window is recognized only when both names,
inode, link count, canonical bytes and durable transition intent match. Its
temporary is removed only after full committed-history reconciliation; every
other archive alias is refused. Only
an exact saved intent can reconcile a crash-created archive. The checkpoint
then commits its count/head and a page-bound retirement list; owned PCM is
removed and its directory synced before retirement is cleared. No next page
starts while the preceding page is retiring. Checkpoint `.next`, archive
`archive.next`, and simultaneous retiring PCM are included in reservations.
ENOSPC, EDQUOT, fsync, or rename failures stop without claiming EOS. The last
committed checkpoint remains authoritative; a newer-looking temporary is never
adopted. The original and unrelated files are never automatically deleted.

Every invocation rehashes registered originals and checks their decoder recipes
before HTTP replay or cleanup. Completed originals are not decoded again. Only
an incomplete active original is replayed from its beginning: published spans
are stream-hashed without regeneration; pending pieces are checked in place.
Missing active-input pieces may be rebuilt after exact reconciliation. A missing
or corrupt completed-original tail remains fatal.

All saved remote pages and capture totals are reconciled before mutation.
Released seal requests are then streamed and exactly replayed, because page GET
does not expose their frozen seal key/revision. Restart is O(history) in requests
and original bytes, not constant time; metadata-only replay is not a fresh
old-audio integrity check. Changes to server IDs, hashes, placement, page sets,
extra EOS, or frozen request identity cannot create replacement captures or IDs.

Worker backpressure scans restart at cursor zero and inspect every row up to the
configured duration-derived chunk cap, including failures beyond row 1500. A
fixed **16-row batch** conservatively fits long Unicode errors within the
unchanged 512 KiB wire cap, even with escaped JSON. At 48 hours this permits at
most 1029 responses. Sparse global rowids are valid; wrong/non-progress cursors,
excess rows, and failed/paused/cancelled work fail explicitly. Exact-full batches
require a final empty sentinel when needed. No persistent cursor can hide a
previously observed row that later fails.

At the exact duration limit, only a bounded EOF probe is permitted; one extra
frame refuses without creating page 589 or implying EOS. Finalization intent
still requires clean EOF for every input, fences new IDs, and uses the original
exact seal or finish request after uncertain responses.

### Server capacity is separate

A 48-hour logical metadata fixture is not a 48-hour physical ingest or acoustic
qualification. Source plus derived canonical PCM alone is 11,059,200,000 bytes;
existing reservations require at least 11,368,005,632 bytes before headers,
allocation rounding, prior data, and caller originals. Default server retained
copy limits (1 GiB per capture / 4 GiB global) must refuse this workload.
Explicit server `--recording-capture-mib` and `--recording-total-mib` settings
accept 1..65536 MiB but never bypass allocation census, concurrent reservations,
or the free-space floor. Capacity failures are HTTP 507 and stop; genuine queue
or lock contention remains HTTP 429 and retries within the deadline.

Primary-duration support does not qualify long optional seam/MOSS retention.
Existing optional owner capacities remain 4,096 source receipts, 1,024 cells,
and 2,056 compact core commits. Capacity gaps must remain visible; no automatic
original or receipt deletion is added here.

Broader CLI overlap remains unsupported. The existing server API can compare
known-coordinate overlapping PCM and preserve exact aliases or report conflicts;
encoded hashes, timestamps, and guessed offsets are insufficient to authorize
CLI overlap. There is no alignment, silence padding, trimming, parallel queue,
new supervisor, server schema, model, or dependency in this extension.

## Model-free acceptance

The focused logical-hour test writes exactly 57,600,000 frames in bounded blocks
and exercises the real API over an owned loopback HTTP server. Expected geometry:
13 pages, 37 source WAVs, 343 work chunks, one EOS, nonzero origin, exact PCM
conservation, one-page maximum spool, real backpressure, uncertain-response retry
and restart without duplication. Fake analysis drains the normal primary commit
path. Separate small installed-FFmpeg tests compare normalization with a single
continuous decode; these are functional checks, not acoustic qualification.

The separate two-input logical-hour gate uses two original files of exactly
57,600,000 frames each, a nonzero origin, continuous deterministic nonconstant
PCM, the real HTTP/API path and fake analysis. Required final geometry is 25
pages, 74 sources, 686 work chunks (685 full plus 7.5 seconds), exact streamed
PCM conservation and source-range lineage, one EOS, no duplication on retries,
and the 72-second/144-second intermediate tails. It also checks response loss,
backpressure, completed-original decode avoidance and finalization replay.
Short fault cases cover EOF/tail/request/cleanup/finalize crashes, missing active
tail reconstruction, ownership/integrity failures, frozen registry/slot/frame
bounds and legacy compatibility. These are validation requirements, not a claim
that hour-long acoustic inference or real-time throughput has been qualified.

The long-capture validation target additionally includes actual append-v3 archive
serialization/recovery for synthetic 12/24/48-hour metadata, 588 pages and
16,458 work rows, registry maxima, page 99/100 and 587/588, frozen-cap conflicts,
late worker failures, post-scan archive tampering, and archive/checkpoint/retirement
crash points. Synthetic metadata contains no 48-hour audio and cannot replace
the existing exact two-hour real-PCM/HTTP conservation gate. Qualification is
reported only for tests actually executed against the final pinned source.


### Executed dev15 checks (2026-10-16)

The current implementation passed 242 focused cases, seven long-metadata cases,
and the exact two-input/two-hour PCM/HTTP case: 250 selected model-free cases.
This is not a full repository suite. The implementation and test bytes remained
identical throughout; only this guide and the README were then updated with
these results.

The two-hour case passed in 34.63 seconds with 97,017,856 bytes sampled peak RSS
and 706,904,064 bytes peak aggregate candidate artifacts. Its assertions cover
115,200,000 frames, 25 pages, 74 sources, 686 work chunks (120,000 frames in the
last chunk), exact streamed PCM conservation, source-range lineage, lost-response
replay, completed-original decode avoidance, and idempotent finalization. It
uses real HTTP and fake analysis, not hour-long acoustic inference.

The actual v3 journal metadata fixture represented 48 originals, 588 pages,
1,787 source pieces, and 16,458 derived work chunks. Maximum checkpoint size
was 3,161 bytes; allocated history metadata was 2,646,016 bytes and terminal
authentication bindings were 20,352 bytes. Seven metadata cases completed in
27.62 seconds with 96,829,440 bytes sampled peak RSS. These contain no 48-hour
PCM. Existing schema tests exercised all 16,458 rows and late-page cursors;
the separate namespace fixture contained 18,221 ordinary zero-byte files.

The measurements also expose existing history-dependent costs: at 48-hour
metadata scale the stream-wake query used 724,256 SQLite VM steps, and the
namespace census visited 18,224 entries with 18,223 stats. These are not constant
costs or a long-duration throughput guarantee. Optional receipt/core capacity,
physical storage admission, real model latency and 12–48-hour wall-clock
reliability remain separate limitations. Prior dev14 acoustic and installation
results are historical; no new dev15 acoustic result is implied.
