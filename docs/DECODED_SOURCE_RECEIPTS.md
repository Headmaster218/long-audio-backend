# Experimental decoded-source receipts and reader

Measurement correction: the earlier independent 34,922,496-byte figure was
pytest-process-only RSS, not a complete tree. The new combined 63-test pass
measured 86,331,392 bytes across supervisor, pytest, and live children. See
`research/MEASUREMENT_CORRECTION.md`; the original baseline archive stays unchanged.

Default OFF, isolated, read-only. Nothing imports this module into HTTP, the
worker, SourcePlan, matching, normalization, or the model runtime. No trim, Alias,
gap filling, capture-continuity assertion, original-frame-count inference, or
source rewrite is performed. Similarity candidates remain non-destructive.

## Separate identities and clocks

`inspect_decoded_source(path, enabled=True)` returns a bounded immutable receipt:
- SHA256 and size of the compressed local bytes
- Pinned FFmpeg **and ffprobe** binary/build hashes, codec, and explicit decode
  recipe; shared-library storage is an explicit trusted-environment assumption
- Native rate/channels, exact count and SHA256 of all sequentially emitted
  interleaved signed PCM16 little-endian frames
- Rational container presentation spans separately from emitted coordinates
- Bounded aggregate packet skip/discard observations, without double-subtracting
  terminal discard from already adjusted demuxed packet durations
- Structural-check scope, unresolved endpoint state, and unproven capture continuity

Raw emitted coordinates are zero-based half-open integer frame ranges. An AAC
presentation duration of 192000/48000 seconds can coexist with 192512 emitted
frames. The 44.1 kHz fixture's container presentation is 1 second while 45056
frames are emitted. The original pre-encoding frame count stays unknown. No 1024
sample priming constant or expected-input oracle is used to correct observations.

## Narrow formats

Opus: Ogg v0; one serial/one logical stream; monotonically consecutive pages;
complete BOS/OpusHead/OpusTags/audio/EOS; CRC checked on every page; mapping family
0, v1, mono/stereo, zero granule origin. Bounded packet parsing derives coded
48 kHz frames; pre-skip, final granule, last-packet discard, demux signals and
emitted count must agree. Offset/cropped streams, chains/multiplexing, lost pages,
CRC failures, oversized comments, and missing/truncated EOS are rejected. Passing
this narrow structural/presentation count contract does not prove authentic
capture continuity or ownership, and grants no automatic timeline admission.

AAC: one AAC-LC track in nonfragmented MP4, native 44.1/48 kHz mono/stereo. Box
bounds and bounded timing fields/edit lists are checked, but sample-table media
extents are not independently validated. The receipt names that limitation;
“box-bounds-complete” is weaker than Opus CRC/EOS verification. AAC endpoints are
always unresolved, including when reported duration happens to equal emitted
length. Ambiguous edits are recorded, never admitted or applied by our code.
FFmpeg's default demuxer edits/skip behavior is part of the recorded raw decode
recipe; no extra crop is applied. Decoder error diagnostics reject the receipt,
even when FFmpeg returns zero. Zero exit alone never establishes finalization.

## Bounded reader and explicit time cost

`SequentialDecodedReader(path, receipt, enabled=True).read(start, end)` verifies
compressed identity, format, pinned decoder, full sequential output count/hash,
and source stability before returning at most 48000 emitted frames. Every read
replays the **entire source from its beginning**, and hashes compressed bytes
before/after; cost is O(full source duration + compressed size), even for a range
near the beginning. There is no random seek, cache, bounded-seek latency promise,
or production long-file claim. It is not practical for repeated 48-hour windows.
A reusable rolling sequential decoder session is a separate future implementation.
No input-side `-ss` shortcut is used.

Receipts are observations, not signatures or authenticated provenance. A caller
can construct a receipt with arbitrary structural/presentation labels. The reader
independently verifies the complete emitted PCM identity and format, not those
labels; only `inspect_decoded_source` performs the declared structural checks.
No downstream component may use a caller-supplied label as admission authority.

Decode/probe pipes are drained using selectors. Pulls retain <=64 KiB chunks,
<=192 KiB requested PCM, bounded stderr (8 KiB), stream metadata (32 KiB), packet
lines (4 KiB), Ogg packets (64 KiB), MP4 timing/moov metadata (1 MiB), and receipt
JSON (16 KiB). Packet lists and decoded whole-source arrays/files are never stored.
Consumer pauses create OS-pipe backpressure. Cancellation is polled during active
operations; a paused low-level generator must be closed to immediately kill/reap
its child. The public synchronous reader has no suspended child between calls.

Defaults: 64 MiB compressed source, 28,800,000 emitted frames (10 minutes at 48 kHz; about 10m53s at
44.1 kHz), 30 seconds wall per
operation, 20 CPU seconds per subprocess, one child thread on CPU 7, sampled own
process-tree RSS <=256 MiB, >=2 GiB host available RAM, >=5.5 GiB free disk. No
output files are written. File descriptors bind hashing/probing/decoding to the
same local object; signatures and end rehashes reject ordinary mutation. This is
for controlled immutable local storage, not adversarial writers or a sandbox for
untrusted decoder exploits. Sampled RSS is not an OS-hard guarantee.

## Baseline concurrency and platform limits

The baseline synchronous helpers use Python preexec_fn for child limits and
should run only in a controlled single-threaded process. They are not safe to
advertise for concurrent-thread construction/invocation. The separate rolling
reader avoids preexec_fn throughout startup/decoding and explicitly permits only
one active rolling session per process. Both implementations rely on Linux /proc
and the pinned Linux decoder environment. Windows is not implemented or validated.

## Compact test pass

Use the existing environment, with no installation/download/model load:

    AUDIO_COMPRESSED_FIXTURES=/path/to/fragment-codec-study-v1 \
      taskset -c 7 /path/to/existing/python -m pytest -q --basetemp=research/test-temp tests/test_fragment_decoded_source.py

Structural unit cases run without fixture assets; integration cases explicitly
skip if `AUDIO_COMPRESSED_FIXTURES` is absent. The opt-in frozen fixtures verify
exact hashes and unaligned/final ranges; AAC presentation disagreement; Opus
skip/discard; truncation/CRC/EOS/sequence/chaining rejection; bounded metadata;
source mutation and altered PCM-hash/format rejection; output/wall/cancel limits; pipe
backpressure and child reaping. A larger run is not authorized by this module or
these tests. Preserve the frozen study and recovery archive; see the separate
report-only clarification in `research/COMPRESSED_STUDY_ADDENDUM.md`.

Protocol references: https://www.rfc-editor.org/rfc/rfc7845.html (Ogg Opus mapping)
and https://www.rfc-editor.org/rfc/rfc6716.html (Opus packet duration/TOC).


## Independent compact validation

Independent reviewer: 29 focused tests rerun, then 37/37 including eight added
negative cases (`research/reviewer_extra_tests.py`). That pass took 9.69 s wall
and 8.20 s test-process/child CPU. Its 34,922,496-byte RSS figure measured pytest
only, because the descendant-discovery interface was unavailable; it is not a
full-tree measurement. Preserve that original record as historical evidence.

The corrected independent pass reran all 63 baseline+rolling tests: 26.16 s wall,
23.08 s aggregate CPU, 86,331,392-byte peak sampled full-tree RSS on CPU 7. Minimum
host available RAM was 5,592,346,624 bytes; minimum free disk 6,618,861,568 bytes.
A separate restored-baseline pass measured 81,502,208 bytes using a verified
PPid-based psutil traversal. These are distinct sampled runs, not retroactively
corrected peaks. See `research/MEASUREMENT_CORRECTION.md` and the corrected resource
record. No critical review blocker remains in the narrow experimental scope;
this is not long-recording validation or an OS-hard peak-memory guarantee.
