# Source-only diagnostic checkpoint

Frozen source commit: `65bf503626e35f73c04de4b36dfc00f9b1f39bf6`.
The 15 source files preserve that separate local Git snapshot. All executable
files are byte-identical; one sentence in REPLICATION_PROTOCOL.md was changed
from internal coordination wording to a generic authorization/input-pin rule.
This additive checkpoint does not replace the backend and excludes all production
reader fixes, corpus/model assets, reference text, inference outputs and private
input specifications. It is not a self-contained reproducible benchmark package.

Run `python verify_source.py`, then copy `source/` into an empty directory.
With Python 3.11 or newer, run `python -m unittest -v` there: eight synthetic tests.
These tests require no models, corpus or third-party packages. They test only
model-free arithmetic, synthetic PCM, edit matching and receipt validation.
They do not certify native inference, general accuracy or complete content.

## Historical protocol and external inputs

The source README is a preserved pre-run protocol. Its statement that no included
utility performs inference applies only to the original model-free utilities;
`whole_native_supervisor.py` and `whole_native_worker.py` DO perform local native
inference when explicitly invoked with external inputs. This checkpoint alone
does not authorize execution, downloads, installation or production promotion.
The preserved prospective status is not a claim about current execution status.

Whole-native execution requires Linux process/resource APIs, `psutil`, the pinned
`transcribe_cpp` runtime, model and audio described in `source/README.md`, and a
private JSON spec with `model`, `model_sha256`, `audio`, `pcm_sha256`, and `pins`
(path-to-SHA256 mapping). Supply absolute paths and include all runtime, source,
worker, model and input assets in pins. Create a fresh private output directory
through the supervisor; never commit the spec or outputs. No reference text is
passed to inference. A separate external outer deadline is required by protocol.
The source uses assertions: do not use Python optimization (`-O` or `-OO`).

Post-run scoring additionally requires an external pinned original v2
`score_overlap_transcripts.py` directory (`scorer_dir`), compatible external
backend checkout (`repo`) exposing `audio_backend.moss_handy_adapter._PHRASE`,
and private reference JSON (`gold`). Gold contains `words` objects with `text`
and `speaker` fields and `people` values matching speaker IDs. A separate
post-run spec supplies these paths and their `pins`. Those external scorer,
backend and reference artifacts are deliberately NOT included; therefore the
whole-excerpt score cannot be reconstructed from this checkpoint alone. Do not
substitute `paired_metric.py`: its boundary-sensitive metric is different.

AMI audio/annotations and their CC BY 4.0 attribution links, pinned Handy model
revision (Apache-2.0) and native source revision are documented in source README.
Native runtime source is MIT licensed. No third-party assets/code are vendored
here; consult each upstream license before independently obtaining dependencies.
This backup does not grant or change licensing for the repository's own code.
The diagnostic is exposed, not held-out; forced-aligned timing and source-derived
EOS inference do not establish content completeness or production qualification.

## Additional two-case replication tools

`select_complete_windows.py` uses external annotation intervals only, never
predictions. `REPLICATION_PROTOCOL.md` preserves the pre-run scientific plan,
selection rule, resource bounds and interpretation limits, not execution status.
`replication_supervisor.py SPEC OUTPUT` performs one local native, ASR, or
candidate phase. It requires the existing runtime, psutil, and backend inference
dependencies externally installed; baseline ASR also imports soundfile. Specs
supply `phase` (`native`, `asr`, or `candidate`), `frames`, `audio`, `pcm_sha256`,
and a complete `pins` mapping. Baseline phases require `repo`, `config`, and
`case_id`; ASR additionally requires `native_result` and `native_execution`, both
explicitly pinned. Candidate requires `model` and `model_sha256`. The same
external runtime/model pins and non-optimized Python requirement apply.

`replication_score.py SPEC RUN OUTPUT` is post-inference only. In addition to
external `gold`, `scorer_dir`, `repo`, and complete `pins`, supply `frames`,
`reference_words`, `baseline_result`, and `baseline_execution`; both baseline
outputs must be pinned. Reference format matches the preceding whole-score
contract. Outputs contain private text and must remain outside the repository.
Eight synthetic tests verify limited model-free behavior only, not a full native
pipeline, native resource safety, quality results, or production qualification.
