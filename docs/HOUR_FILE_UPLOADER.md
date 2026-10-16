# Finalized hour-file uploader (opt-in, Linux/POSIX-qualified)

This first slice streams **one finalized, immutable regular local file** into an
existing empty legacy session and a new explicit capture. It changes no API,
schema, primary analysis path, model, dependency, version, or default. Success
means all normalized PCM was accepted and EOS published/queued. Analysis can
still be pending. Functional fake-analyzer tests do not establish acoustic
quality, one-hour acoustic throughput, or real-time inference performance.

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
arbitrary demuxers and multi-file input are rejected. The explicit demuxer and
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

## Deliberate remaining scope: nonfinal files and overlap

--final is mandatory. Nonfinal and multi-file ingestion are rejected. A later
extension must retain the one-hour file's 72-second tail after twelve 294-second
pages, rather than dropping it or falsely ending the capture. A second contiguous
file would fill that staged page; overlap would require exact normalized-sample
comparison, explicit coordinates and bounded retained history. Neither seeking
independently into compressed files nor duration estimates prove equivalent PCM.

## Model-free acceptance

The focused logical-hour test writes exactly 57,600,000 frames in bounded blocks
and exercises the real API over an owned loopback HTTP server. Expected geometry:
13 pages, 37 source WAVs, 343 work chunks, one EOS, nonzero origin, exact PCM
conservation, one-page maximum spool, real backpressure, uncertain-response retry
and restart without duplication. Fake analysis drains the normal primary commit
path. Separate small installed-FFmpeg tests compare normalization with a single
continuous decode; these are functional checks, not acoustic qualification.
