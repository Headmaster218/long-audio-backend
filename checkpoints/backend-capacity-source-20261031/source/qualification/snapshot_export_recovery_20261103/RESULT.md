# Same-job read-only export recovery, 2026-11-03

## Result and boundary

The one real combined FTPS producer / owned-stream adapter / bounded watch / native
ASR run remains **failed at the export consistency gate**. Native processing had
completed, but the second transcript read returned 409; no completion manifest was
published. Its partial files and all original evidence remain unchanged.

A separate bounded recovery from that same completed job **passed**, followed by
an independent canonical/native provenance audit. This was a closed-database
snapshot HTTP projection using unchanged production read handlers, not a restart
of the production application. It performed no inference replay, upload, admission,
finalization or remote mutation. It does not retroactively pass the original watch.

Two fresh private output directories each completed on attempt 1, using 14 GETs each.
The output pairs and completion manifests were byte-identical. The audit checked
all five nonempty canonical ASR units, the selected 144,000 frames (9 seconds), two
retained native packets and outcomes, full owned PCM coverage and native source
receipt signatures. Transcript text was checked exactly against canonical database
rows; this establishes provenance, not recognition accuracy.

## Bounded observations

- Outer elapsed 3.535 seconds; child CPU 2.307 user + 0.630 system seconds.
- Sampled total process-family peak 133,451,776 bytes, below 512 MiB limit.
- Host elapsed 2.459 seconds, sampled peak 69,668,864 bytes; 28 GETs, 60,884 response bytes.
- One CPU/nice 19; unchanged 5.5 GiB free-disk floor and bounded output/time/request gates.
- Original Popen waits all completed with exit 0; no remaining descendants/listener.
- Active requests and response leases zero; no protected-write denials.
- Original source/job/partial outputs/databases/producer files unchanged, including
  checked bytes, lengths, mtimes and ctimes. Snapshot unchanged;303 supplemental pins verified.
- Export directories 0700, files 0600; artifacts stayed private locally.

The stable closed snapshot did not trigger the new typed revision-conflict retry.
That bounded retry and race/refusal behavior are covered by model-free tests, not
claimed as observed during these successful exports. Unknown 409 responses remain
nonretryable. Each allowed restart uses a fresh output directory and unchanged
consistency gates; old partial outputs are never reused or deleted.

## Source and checks

Executed source backup: 0cd6b5c5f94e1432469484bf53275ea7c5045c06.
Executed 955-file manifest SHA256:
1ef0bbe31171ddf472a20267487e5498055e685d8dfa4a19b7d1a6c08c119f58.
Independent auditor SHA256:
1ff681e9347375c5f73d390d01331c58d903daedd3737958e8de21a503406df3.

Before execution: 60 qualification controls passed with retained-evidence ASGI tests
enabled, plus 151 focused product recovery/export/status/watch tests. A separate
uploader regression had 178 passes and two environment-blocked legacy logical-hour
tests because their historical sitecustomize hook was unavailable; no full-suite
pass is claimed. The existing deprecation warning did not affect the tests.

No raw audio, transcript, model, database, credentials or private run evidence is
included in this source backup. No device, deployment, full-capture, speaker identity,
word-alignment, recognition-accuracy or longer-duration capacity claim is made.
The qualification host's write guard is cooperative defense in depth, not a hostile
native-code sandbox. This result closes bounded same-job export recovery only.
