# Ordinary-stream endurance source checkpoint

Status: frozen source/checkpoint draft for publication review, 2026-10-15. Public
templates remain UNCONFIGURED AND NOT EXECUTED. Local-original results are
summarized in RESULTS.md. The paced real-model run passed its primary operational
checks; 49 optional-path warnings prevent an optional-reliability qualification.

## What is included

- acceptance.py.in: bounded HTTP/FFmpeg/SQLite/Worker acceptance using an injected
  fake analyzer; focused admission/retention, 12-minute logical prefix, and
  12-hour accelerated logical history modes.
- real30.py.in: fixed 30-minute paced operational shakedown using the ordinary
  real-model path, four isolated input scopes, numeric observation, and one idle
  graceful restart. Most input is digital silence.
- real12h.py.in: unexecuted source template for a 43,200-second paced primary
  operational soak, retaining the real30 profile and adding bounded optional
  diagnostic reason codes. Its actual 12-hour result is pending.
- RESULTS.md: qualified observations from the locally executed original sources.
- CONFIGURATION.md: explicit path substitutions, runtime manifest requirements,
  historical artifact fingerprints, and the configuration dependency order.
- MANIFEST.json: hashes of this public checkpoint's other six files and the
  historical source fingerprints. This is not a runtime manifest for any of the three harnesses.

No database, audio, transcript, raw receipt ledger, live-process identity, or
runtime log is included. The source retains dynamic OS process-ownership checks
and bounded result handling because those are part of the test mechanics.

## Qualification boundary

The local original acceptance source completed focused, prefix, and 720-chunk
logical tests. The logical run covered 43,200 seconds of input in 926.359 wall
seconds. It used fake analysis with real infrastructure. It provides no speech
quality, native-model throughput, or 12-hour wall-clock uptime qualification.

The local original real30 source passed both its separate model-free source
check and an audited primary 30-minute paced operational run: 42 receipts,
1,800 seconds of input pacing, and 1,812.652 seconds total wall time. Only
136.570625 seconds were distinct real recordings; the rest was digital silence.
Its idle graceful restart preserved committed history and cleanup was verified.
49 optional-path Unavailable warnings lacked historical reason/timestamp detail;
post-restart ready health does not qualify whole-run optional reliability.
This is neither a quality score nor sustained speech-load or 12-hour acceptance.

The three templates are mechanically redacted derivatives. Path bindings and the
real30 and real12h supervisor hashes are explicit placeholders; all other source bytes are
preserved. Static inverse substitution was byte-identical to each local
original, with the original fingerprints recorded in MANIFEST.json. This proves
source provenance only. It does not transfer an original run's qualification to
a differently configured template, a new checkout, runtime, machine, or workload.

## Configure before any execution

Do not import, rename-and-run, or execute a .py.in file. real30 imports its frozen
supervisor at module import time. CONFIGURATION.md describes how a separate,
reviewed runtime packet would be assembled. Neither the runtime packets nor a
portable environment are supplied by this checkpoint. Product code, installed
Python packages, interpreter, FFmpeg, models, and input assets must be available
and independently verified. No install or download is performed by these files.

Preserve all guards and fresh-directory requirements. Default Linux requirements
are CPU affinity 0-1, nice 19, at most 8 GiB per-process RLIMIT_AS, a 6 GiB sampled
aggregate tree-RSS threshold, 2 GiB per-run output, 12 GiB workspace usage, and a
5.5 GiB free-space floor. A 16 MiB margin protects disk thresholds. RSS sampling
is observational, not an instantaneous hard memory ceiling.

The public checkpoint has seven files only. Proposed repository placement is
evaluation/long-recording-20261015 on development/rc3-dev9-quality-evaluation,
based on immutable parent 9c09883265b914df21c8a5f2b2474c96b07c2d0b. This is destination
metadata, not evidence that the draft was committed, published, or validated
against that parent. This preparation performed no harness execution or Git or
network writes. The local-original terminal evidence, numeric accounting, pacing,
and owned cleanup have now been audited. The seven public files are frozen for
publication review; no publication is claimed by this draft.

## Pending 12-hour extension

The real12h source is prepared for a future 43,200-second monotonic run; no actual
12-hour result is included. The local original passed its model-free check; no models or actual pacing were
run by that check. It uses the same 136.570625 seconds of distinct real recordings once,
then 43,063.429375 seconds of silence in a separate scope. Its longer duration
must not be confused with the completed accelerated logical test. Public
templates remain unconfigured and unexecuted regardless of local-original
qualification. See CONFIGURATION.md for the new packet binding and ancestor
pins; the fixed operational resource limits remain unchanged.
