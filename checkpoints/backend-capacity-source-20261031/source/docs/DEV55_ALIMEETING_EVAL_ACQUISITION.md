# AliMeeting Eval acquisition qualification

This checkpoint establishes one newly acquired, exposed Mandarin/overlap evaluation input: AliMeeting Eval far-field meeting `R8001_M8004`. It records acquisition, structural validation and exact channel preparation. No ASR, diarization, acoustic accuracy, speaker-identity or alignment result was produced, and no historical corpus identity was established.

## Preserved acquisition history

1. A preliminary read-only HEAD check timed out after 20.022 seconds without an HTTP status or body.
2. The first acquisition helper used a direct HTTPS connection that did not match the environment's configured HTTPS route. Local DNS resolution failed before any GET was sent; no dataset bytes were acquired by that attempt.
3. A separately approved environment-aware attempt fetched the official archive with HTTP 200: 3,673,718,355 compressed bytes. Both complete streaming gzip/tar scans passed, each emitting 5,385,922,560 bytes and covering 73 members, including directories. Filename-only selection chose the first complete meeting and its unique reference. No replacement meeting was selected.
4. The managed acquisition execution retained an incomplete status when its frozen validator rejected the selected WAV's `fmt` chunk. This was a parser limitation, not evidence of corrupted audio. Independent inspection found standard PCM WAVE_FORMAT_EXTENSIBLE: a 40-byte `fmt` chunk, exact PCM subtype, and a 4-byte `fact` chunk matching the data frame count.
5. A separately approved, local-only continuation validated that exact observed layout and derived physical channel 1 without another transfer, archive scan, reselection, crop, resample or downmix. Its terminal result succeeded; the earlier failure records remain preserved.

## Accepted preparation result

The complete source is eight-channel, 16 kHz, 16-bit PCM with 25,181,600 frames and a duration of 1,573.85 seconds (26 minutes 13.85 seconds). The canonical mono WAV contains the same frame count and is 50,363,244 bytes. Independent verification matched every source channel-1 raw sample pair to the mono, verified source/mono file and PCM hashes, and reconciled header lengths, `fact`, frame counts and exact EOF.

The companion provenance JSON contains the official source links, dataset license, actual archive/member/file/PCM pins, geometry and predeclared selection rule. Reference bytes were hashed but remained opaque; no audio, labels or transcript content is included in this checkpoint. Fresh hashes identify these acquired bytes and do not establish historical equality or independent authenticity. OpenSLR declares the dataset CC BY-SA 4.0; the owner's Apache-2.0 code license is separate.
