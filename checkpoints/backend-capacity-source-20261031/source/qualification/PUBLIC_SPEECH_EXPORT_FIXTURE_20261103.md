# Public-speech export fixture, 2026-11-03

## Scope

A reproducible host-JVM recorder fixture for a later bounded native transcript
export smoke. Fixture preparation alone is **not** native ASR execution, completed
service processing, acoustic accuracy, Android execution, microphone capture, or
hardware continuity evidence. No native service has been launched by this report.

The unchanged production Java writer, spool, metadata serializer and FTPS envelope
produce eleven one-second segments from exact public PCM. Nine deliveries are
selected, preserving producer sequences 2 through 10, epoch 7, original epoch
frames 32,000 through 176,000, and final `INTERRUPTION` reason. The first two real
producer segments precede the selection. No metadata is rewritten or rebased.

These are genuine **host replay** producer records with explicitly synthetic
observation timestamps. They are not the original AMI capture metadata. Original
public-file sample coordinates are independently bound in the local fixture
receipt. Selected-span EOS never establishes full-capture completeness or the
duration of any external gap.

## Public source and license

Attribution: AMI Consortium / University of Edinburgh, AMI Meeting Corpus,
EN2002a Mix-Headset. Official corpus and license statement:
https://groups.inf.ed.ac.uk/ami/corpus/

The official corpus page states that its signals and transcription are released
under CC BY 4.0: https://creativecommons.org/licenses/by/4.0/
The license statement was rechecked using the official page's search result.
A direct open of the official download page returned HTTP 403. No new download
was needed and no access restriction was bypassed.

Existing local full WAV: 68,566,744 bytes, SHA-256
`a15017d0d3c871866971ee178052a0ce3a7e44d6fbf89fc23dcab326511b65f5`.
The generator verifies the full WAV before extracting the eleven-second source
range [26,640,000, 26,816,000), corresponding to 1665 through 1676 seconds at
16 kHz. The nine delivered seconds correspond to original source frames
[26,672,000, 26,816,000), or 1667 through 1676 seconds. PCM bytes are unchanged:
there is no resampling, gain adjustment, synthesis or ASR-dependent selection.

Selection was fixed before this export/native run: the first nine seconds of an
already labeled 26.25-second diagnostic crop. This is a previously exposed source,
not an independent holdout. Existing public manual annotations contain fourteen
normalized reference words in the selected interval, distributed B=10, C=2, D=2.
One annotation interval crosses the outer end boundary. Counts establish a speech
fixture, not a clean word/timestamp accuracy test. No reference text is included.

The already downloaded annotation mirror archive has SHA-256
`56e29112cd89cfba76fbf8f10215eb245cc89b0d3d27355ba14b406a7ea7776c`.
Its source revision is
https://huggingface.co/datasets/FluidInference/ami-corpus-mirror/tree/722d8891643e1e4dc62cfd0d198fa05a1646c3cc
The archive was repackaged; byte identity with unavailable upstream is unestablished.

## Producer and reproducibility

Recorder source commit: `2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9`.
All 256 assembly receipt entries were compared with their committed Git bytes.
The checkout was clean. Actual production core classes were compiled with Java 8
source/target and warnings-as-errors. The new qualification-only Java harness
accepts a pinned local raw PCM file, verifies its SHA-256, and uses the unchanged
production core. Python verifies every selected WAV's exact PCM and checks that
the envelope's embedded metadata equals the original producer manifest bytes.

Reusable tools:

- `qualification/generate_public_speech_java_deliveries.py`
- `qualification/PublicSpeechProducerDeliveries.java`
- `tests/test_public_speech_producer_fixture.py`

Use the generator's `--help`. Its required input path, full WAV SHA-256, source
start frame, title, source URL and license URL prevent an implicit audio choice.
The chosen source must be canonical PCM16 mono 16 kHz and have eleven seconds
available. Output must be a fresh directory outside source trees. The current
qualification selects nine one-second outputs after two preceding segments.
The wrapper verifies source provenance; URLs are attribution metadata, not fetches.

Local fixture receipt SHA-256:
`735895855f8de14f84992216ee7b28fb7359ce74fb30a86aed6ed5e93e80dd56`.
Eight model-free tests pass for exact PCM extraction, explicit/matching hashes,
range/format rejection and symlink rejection. The actual Java generator also
completed and validated nine selected deliveries. These are focused controls,
not a full repository pass.

## Native run restrictions

A later exporter-enabled smoke must first freeze the exact final source candidate,
apply only the already accepted runtime candidate patch, obtain independent plan
review, and receive launch confirmation. Use a fresh candidate directly under the
workspace and a fresh service/database directory; never reopen an old service DB.

Retain all current limits: 300-second service Gate, two CPU affinity slots, 6 GiB
sampled RSS cap, 5.5 GiB free-disk floor, 16.25 GiB sampled artifact cap, and outer
480-second deadline with 120-second TERM grace. The nine seconds remain below the
existing incremental-span probe's 10.5-second bound. Preserve native shutdown,
child waiting and descendant cleanup behavior. Keep models, model settings and
the production runtime pin unchanged. Observe baseline export in two fresh
GET-only processes, verify exact repeated JSON/TXT output and read-only source
and intake state, and require real nonempty ASR evidence before claiming speech
transcription success. Synthetic silence never supplies that evidence.

All 2,157 existing model/runtime asset files (733,515,621 bytes) were rehashed with
zero mismatches. The accepted interpreter/SQLite fingerprints and workspace device
27 match in a read-only preflight. These checks are not a native processing run.

Only source harnesses, tests and aggregate reports are suitable for Git backup.
Raw audio, annotations/reference text, generated hypotheses, models, databases,
compiled classes, local fixture receipts and raw logs stay outside the source tree.
