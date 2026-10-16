# Immutable hour-file uploader (opt-in, Linux/POSIX-qualified)

The original `--final` mode streams **one finalized, immutable regular local file** into an
existing empty legacy session and a new explicit capture. It changes no API,
schema, primary analysis path, model, dependency, version, or default. Success
means all normalized PCM was accepted and EOS published/queued. Analysis can
still be pending. Functional fake-analyzer tests do not establish acoustic
quality, one-hour acoustic throughput, or real-time inference performance.
The experimental `--append` / `--finalize` modes below add a bounded registry of
separately normalized originals. The existing `--final` path and journals remain
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
journals cannot migrate or reopen one another.

Append JSON distinguishes `input_complete`, `awaiting_input`, and
`capture_finalized`; `frames` includes staged input, `published_frames` includes
only released pages, and `staged_frames` is their difference. Nonfinal input
success does not claim capture completion. Finalize takes no input/ID/coordinate
and requires nonempty clean EOF for every registered original. Its intent is
saved before EOS, fences all new IDs, and survives interruption. Finalization
success still does not mean analysis is complete.

### Bounded registry and recovery

- `--max-inputs M` permits 1..32 originals, default 32 for a new journal. Omit it
  on resume to reuse the frozen value; changing it conflicts. Originals total
  at most 8 GiB across the registry, rather than 8 GiB per appended original.
- Aggregate output remains at most two hours, 25 pages, and 105 source records.
  This is a temporary experimental ceiling; it is not a long-duration claim.
  Raising it also requires reviewing page/journal/name/backpressure bounds.
- At most 16 separate original-owned pieces fit a page, matching the existing
  server limit. Before admitting an input, the uploader reserves enough slots
  for it to fill the current page: retained pieces + ceil(remaining frames /
  1,920,000) must be <=16. Too many tiny originals are refused before admission;
  retry or finalize the existing tail. No automatic origin merging occurs.
- Per-input journal records are <=8 KiB, pieces <=1 KiB, page control metadata
  <=2 KiB, globals <=16 KiB, and all JSON <=512 KiB. Paths are <=4096 encoded
  bytes, IDs <=128 ASCII characters. Diagnostics are bounded, not cumulative.
- One page contains at most 9,408,704 WAV bytes. With allocation rounding,
  metadata replacement and safety allowance it remains within the existing
  12 MiB spool budget. Retained tail, partial writes, and cleanup remnants all
  count. Only missing page capacity is reserved beside a tail. The existing
  64 KiB I/O, 512 MiB sampled RSS, deadline, and 5.5 GiB free-disk bounds remain.

The v2 journal records each original's identity/normalization recipe, stable ID,
global start, local emitted count and clean EOF. Each piece retains input-local
and capture ranges plus WAV/PCM hashes, keys and server IDs. This remains client
bookkeeping; existing server source/range receipts are authoritative.

Every invocation rehashes registered originals and checks their recipes before
HTTP or cleanup. Completed originals are not decoded again. Only an incomplete
active original is replayed from its beginning: saved published spans are
stream-hashed without disk regeneration; pending pieces are checked in place.
After exact source/server reconciliation, missing active-input pieces may be
rebuilt from matching replay. Missing/corrupt completed-original tail, changed
server history, extra pages or EOS without a saved request fails closed.
Only exact owned uncommitted or released files are cleaned; another original's
pending tail stays. Piece and cursor checkpoints are atomic. Seal/finish retries
replay their saved immutable requests even after a published GET. No conflict
creates replacement IDs, captures, or duplicated work.

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
