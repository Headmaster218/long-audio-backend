# Exact speaker-support review: real-model functional smoke test

Version `0.2.0rc2.dev1`, fixed source commit `86c1b7983206a8e83877a3384a91ba0b04a6316e`.

One six-second development-corpus clip was processed three times through the real authenticated HTTP API: once with capture disabled and twice with capture enabled. Total analyzed input was 18 seconds. The installed pipeline used SenseVoice int8, pyannote, ERes2Net and Zipformer event classification on CPU; no overlap-separation model was used.

Results:

- Disabled capture created no support or generation records.
- Enabled capture retained one complete, approximately 5.923-second support. Downloaded float-WAV bytes matched the selected original samples, private artifact and recorded PCM hash exactly.
- Normalized ASR text/timestamps, independent speaker-turn results and event/candidate outputs were identical with capture off and on.
- Genuine observation-cache eviction preserved independently retained support.
- Authentication, session scoping, restart, reset and deletion checks passed.

Observed request durations were 4.036 seconds with capture off, then 4.246 and 4.263 seconds with capture on. The sampled test-process-tree peak was 451.5 MiB. A conservative sum with the concurrent workload's recorded peak remained 2.78 GiB; available host memory stayed above 3.96 GiB and free disk above 6.34 GiB. Model compute was configured for one thread and one-core affinity; the complete HTTP/monitor/I/O process tree had additional OS threads. Resource controls were sampled kill guards, not OS-enforced hard limits.

The harness encountered an environment-proxy preflight failure before inference and an expected HTTP 409 when attempting deletion before worker release. Both were recorded; only the harness was adjusted, and the successful disabled-capture inference was resumed rather than repeated.

This small smoke test establishes covered functional behavior. It does not establish identity accuracy, single-speaker purity, human verification, long-recording quality or stable performance overhead. Capture remains explicitly opt-in and off by default. No learning or automatic verified anchors are enabled.

Reset/deletion assertions concern live database records and API access. They do not claim forensic erasure of SQLite free pages, journals or separately retained backups.
