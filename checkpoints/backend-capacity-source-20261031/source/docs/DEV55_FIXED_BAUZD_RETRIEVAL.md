# dev55 fixed bauzd retrieval status

As of 2026-10-19, the third preserved bounded request to the official VoxConverse development archive returned HTTP 500. No audio was recovered, so the fixed-audio validation remains blocked.

## Source boundary

This is a factual, source-only addition over dev55 commit `cf3f4af752f035cad45d3c697e63d4ef4f52a07f`, tree `8302044a171400d7dfbbbecce72ca4b69f4237bd`. The parent contains 579 files and 10,523,819 bytes. This report is the only proposed addition; all parent file identities remain unchanged. It changes no runtime behavior or qualification result.

## Observed request and terminal result

The exact source was the [official development audio archive](https://www.robots.ox.ac.uk/~vgg/data/voxconverse/data/voxconverse_dev_wav.zip). One GET requested `Range: bytes=-131072` at 2026-10-19 05:09:31.488576 UTC and finished at 05:09:38.893810 UTC with HTTP 500. The response Date was 05:09:38 GMT. Zero application response-body bytes were read; a zero-byte capture was retained. The error response advertised a 785-byte HTML body, which was not read. Its headers do not establish a valid audio-archive size or validator.

The plan allowed at most three conditional exact ranges: the 131,072-byte suffix, a 30-byte local header, and the same named member's bounded payload. Only the first request occurred. No member-header or payload request, automatic retry, full-archive download, alternate source, or substituted corpus followed the error.

The attempt retained a 128 MiB combined transfer/staging ceiling and a 256 MiB process address-space limit. The latter is a memory limit, not an output allowance. Other limits were one CPU at nice 19, 60 CPU seconds, 30 seconds of socket-idle timeout, and 180 seconds of outer wall time with a two-second termination grace. The completed invocation reported 7.475 seconds wall time, 0.057 user CPU seconds, 0.017 system CPU seconds, and 18,688 KiB helper peak RSS. The original invocation was positively waited to terminal exit 2. These are retrieval measurements, not inference performance or a separate hard RSS guarantee.

A separate preceding wrapper launch exited 127 because its external timing executable was absent. It stopped before the HTTP helper or timeout command launched and made no archive request. That failed evidence was preserved. A separately reviewed wrapper-only correction used Bash's timing keyword with unchanged retrieval and resource limits; no software was installed.

Retained earlier receipts report HTTP 500 for the same suffix range beginning 2026-10-18 23:53:46.665951 UTC and 2026-10-19 00:26:05.845003 UTC; the second finished at 00:26:11.585334 UTC. Both read zero archive-body bytes. Those are historical receipt observations, not requests repeated in this attempt. All seven protected prior source/reference/attempt artifacts remained byte-identical.

## Reverified public reference and source pins

The existing official reference and source README were rehashed locally before the new request; they were not refetched.

- Repository revision: `joonson/voxconverse` commit `24bf60be297701cd7e4ef18550c6d390c1b87365`.
- Reference: [dev/bauzd.rttm](https://github.com/joonson/voxconverse/blob/24bf60be297701cd7e4ef18550c6d390c1b87365/dev/bauzd.rttm), 5,124 bytes; Git blob SHA-1 `e156b1a7d4e52440f34aed7d0d475ce34f680bc5`; SHA-256 `b219fe6eb11f660f4fe3d3b4e91a3e886618eac06752f5fa07135cd30c3cd88c`.
- [Pinned README](https://github.com/joonson/voxconverse/blob/24bf60be297701cd7e4ef18550c6d390c1b87365/README.md): 2,275 bytes; SHA-256 `d7479cf72d990eaad9340fd62057585e30ccbde87ad1dc198c970a3c7575f0fb`. Its retained source receipt identifies Git blob `a40cba84ef289acf986773807363395930292b05`. The README describes annotation version 0.3 and links the previous 0.2 branch.
- The README states research-purpose availability under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0), with copyright retained by the original video owners. Attribution: Chung, Huh, Nagrani, Afouras, and Zisserman, *Spot the conversation: speaker diarisation in the wild*, Interspeech 2020.

The pinned reference is evaluation-only. Its labels must not drive crops, product thresholds, or speaker links. No audio or reference-label contents are included in this source addition.

## Identity limits and remaining blocker

Prior worker-reported metadata describes the complete target `bauzd.wav` as mono PCM16 at 16,000 Hz, 8,004,480 frames and 500.28 seconds. That implies 16,008,960 PCM bytes arithmetically; it is not a fresh audio observation. The historical audio, archive, and reference hashes were lost. The historical annotation revision is unknown, and the fresh reference pin does not establish equality with the old trial.

No archive directory, exact member path, member CRC, fresh WAV geometry, audio SHA-256, PCM SHA-256, or full-archive hash was obtained. None can be reconstructed from the reported dimensions. Complete official audio is still needed before the fixed-bauzd comparison can proceed. This blocks that specific comparison, not unrelated validation work. This attempt performed no inference, listening, scoring, or acoustic qualification and supports no historical-byte-equivalence or accuracy claim.
