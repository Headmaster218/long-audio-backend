# Frame-origin observation candidate

This checkpoint contains a narrow correction to optional analyzer PCM observation.
It is an isolated candidate, not an installed change or deployment qualification.

## Technical scope

`worker.py` passes `(chunk_origin, extract_start, extract_end)` to the optional cell
observer only at the analyzer call site when a NativeCoordinator result exists.
Primary extraction retains its original absolute additions; the legacy witness
retains its original four arguments.

`native_owned_cell_capture.py` changes only `Capture.observe_pcm`. The optional
tuple must have three ordinary integer/float values, match the original absolute
arguments, and apply to analyzer observation. The unchanged strict `exact_frame`
parser validates the origin against the capture scope and existing integer origin
frame. Signed local bounds must lie exactly on the 16 kHz frame grid. Integer
frames are added before absolute coordinates are reconstructed; strict parsing
must recover precisely the intended frame pair. Precision loss fails closed.

Negative preceding-upload context is accepted only when resulting scope frames
are valid. Non-grid absolute scope origins remain supported where the existing
strict capture accepts them. Calls without metadata retain the original strict
absolute-coordinate behavior.

No tolerance, epsilon, timestamp snapping, proof-parser change, PCM equality
relaxation, source-mapping change, additional budget phase, budget/SQL constant
change, NativeCoordinator change, safe-cut change, or primary return/exception or
commit substitution is introduced. The two product modules are exact copies of
the tested candidate; their remaining code is unchanged from its pinned baseline.

## Focused results

The original pinned synthetic harness completed all 13 tests successfully. It
checked strict parser identity; known-grid translation; invalid coordinates and
precision loss; negative context and non-grid scope origins; 12-hour and 48-hour
synthetic coordinates; decoded WAV frame count and PCM equality; unchanged
half-sample safe-cut rejection; and primary extraction, analyzer, commit, legacy
witness, return-object, and exception-object preservation. It opened no database
connections and launched no models or native child processes.

The included public test template has NOT been executed. It preserves the test
body and frozen-source pins, with only explicit environment configuration and
introductory text changed. Those historical results do not qualify a configured
or subsequently changed template.

## Limitations

Observed warnings identified `fractional_frame_coordinate`, but exact live
coordinates were not recorded. The example `21.0 + 80768/16000` versus
`(336000+80768)/16000` is a synthetic arithmetic reproduction, not an observed
live coordinate. This candidate is not evidence that all live warnings are fixed.

Synthetic ring state was seeded and acknowledged for observation checks. No
real model/native execution, original audio, ASR-quality evaluation, long-running
test, proof-cell persistence, or end-to-end database qualification was performed.
Genuine half-sample safe-cut commits remain unavailable to strict optional capture.

## Files and configuration

- `worker.py` and `native_owned_cell_capture.py`: candidate product modules.
- `test_frame_origin_observation.py`: unexecuted standalone unittest template.
- `MANIFEST.json`: byte sizes and SHA-256 values for this README and three Python files.

The template requires an isolated Linux environment and a separately supplied
compatible `audio_backend` package. Set `TEST_CPU` to one permitted integer CPU
index and `SOURCE` to that package's resolved absolute directory. The five frozen
source pins embedded in the template must match; the package's remaining import
dependencies are also required. No dependency package is bundled here.

The original harness used Python 3.12. Its guards retain one-CPU affinity, nice 19,
1 GiB address space, 55/60 CPU-second limits, a 60-second alarm, 2 MiB per-file and
synthetic logical/allocated-space bounds, no bytecode writes, and a 256 MiB peak
RSS success ceiling. Audit hooks and constructor guards prohibit model/native
startup, subprocesses, network, SQL connections, dynamic-library loading, and
original audio/database opens. Use an external 60-second kill timeout for any
separately authorized run. Do not overlay these files on a running installation.
