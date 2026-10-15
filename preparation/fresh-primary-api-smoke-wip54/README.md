# Fresh primary API smoke source checkpoint

This is a sanitized source revision of a completed historical smoke helper.
The public template's actual mode has **not been executed**. Its only functional
revision changes the auxiliary overlap-candidate pagination row key from
`overlap_candidates` to `regions`, matching the product response. The response
also includes `tracks`; the helper retains the complete page but iterates region
rows. This source-only correction does not change or rerun the historical test.

## Historical primary milestone

The original helper exited successfully after two adjacent 10.5-second uploads
from the official demo: both chunks completed, producing nine primary segments.
One native diarization epoch finished at 21 seconds. Total helper time was
12.329 seconds and peak sampled process-tree RSS was 677,969,920 bytes. CellStore
was ready and accepted two inputs with zero drops. No natural-overlap cell was
available, so zero MOSS jobs were submitted.

The original auxiliary overlap reader recorded a key mismatch after retaining
an HTTP 200 response with empty `regions` and `tracks`. Primary completion is a
separate outcome. No raw receipts, audio, transcript or execution logs are in
this packet. These facts do not establish transcription accuracy, nonempty
region pagination, MOSS inference, durability, EOS behavior or completeness.

## Local configuration and validation

`smoke.py.in` is a template. Replace every placeholder listed in
`LOCAL_CONFIGURATION_FIELDS.json` with a local value, preserving Python string
literal syntax. No local value is included here. Keep the source, demo, runtime
and candidate-file hash pins unchanged; changing a pin is a separate revision.
The actual run requires the pinned runtime, installed product, psutil, FFmpeg,
recovered models and native installation settings. Configure native asset paths
locally and choose an unused output location; the helper refuses old output.

The model-free check uses only the Python standard library:

    python3 -I -B smoke.py.in --self-test

The existing self-test and the retained empty overlap pagination shape were
checked in isolated Python within 30 seconds and 256 MiB, without starting a
service, importing the product, running a model, making HTTP requests or opening
a database. That check does not transfer historical actual-mode success to this
revised template. Actual mode is selected separately with `--run` and an optional
`--port` (default 8765).

## Existing runtime bounds

The helper retains a 1,680-second work limit, 1,800-second total limit, CPU
affinity 0-1, nice 19, and a 6-GiB sampled aggregate RSS ceiling. Output limits
are 128 MiB data, 144 MiB total, 4,096 entries, a 256-KiB retained server log and
a 5.5-GiB free-space floor. HTTP reads are bounded, pagination is capped at 64
pages and 4,096 rows per route, and shutdown waits and checks tracked children.
Bounds are sampled/cooperative, not kernel quotas. Optional MOSS dispatch still
requires the original retained natural-overlap seam proof and time reserve.
There is no synthetic-overlap fallback and no automatic inference rerun.
