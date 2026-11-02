# Source-only diagnostic checkpoint

Frozen source commit: `8022ddf091b58fc124f6619ef6818357e94e82d0`.
The original 22 source files are unchanged from that snapshot. Two additional
files, diagnose_activity_geometry.py and test_activity_geometry.py, are exact
exports from b05a7e18eb7ea1aa3b796e25b6687dd5d0fc2aac. No other files from that
commit or its ancestors are included.
This additive checkpoint does not replace the backend and excludes all production
reader fixes, corpus/model assets, reference text, inference outputs and private
input specifications. It is not a self-contained reproducible benchmark package.

Run `python verify_source.py`, then copy `source/` into an empty directory.
With Python 3.11 or newer, run `python -m unittest -v test_model_free test_clean_cn_v1 test_activity_geometry` there: nineteen synthetic tests.
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
Nineteen synthetic tests verify limited model-free behavior only, not a full native
pipeline, native resource safety, quality results, or production qualification.

## Separate Chinese character diagnostic

`CHINESE_PROTOCOL.md` preserves the prospective Chinese scientific protocol, not
current execution status. `chinese_replication_score.py` uses the same external
scorer/runtime contracts as replication_score.py but selects language `zh` and
requires `reference_characters` rather than `reference_words`. Its private gold
JSON contains `segments`, each with `speaker` and `text_raw` fields. Do not include
that text in model inputs, the Git tree, or public reports. Chinese character
counts are not pooled with English word counts; edit rates may exceed 100%.
The synthetic normalization controls do not validate the external scorer itself.

External AISHELL-4 provenance references:
https://www.openslr.org/111/
https://huggingface.co/datasets/AISHELL/aishelltech/tree/aada72727856313b19d4a030383c426364931dbf
No corpus material is included. Preserve the upstream CC BY-SA 4.0 terms and
protocol's unverified mirror/full-archive identity caveat; the mirror's metadata
does not replace upstream license obligations. No accuracy results are included.

## Clean Chinese annotation-only selection

CN_CLEAN_PROTOCOL_V1.md and CN_CLEAN_COVERAGE_V2.md define separate prospective
selection domains. Their selector CLIs take external TextGrid, normalized mono
16 kHz PCM16 WAV, RTTM and a new output directory. Selection may write reference
text, audio and receipts: those outputs MUST stay private, outside this source
checkpoint. No corpus text or outputs are included here. The fixed anchor hash
validates a particular externally prepared recording and is not a general CLI.
Do not use Python optimization. No model inference runs during the 16 synthetic
controls. These tests cover the v1 selector; the v2 coverage extension requires
its own independent review and is not fully covered by these synthetic tests.
The original v2 external scorer and inference runtime still are not bundled.
This is a source/protocol checkpoint, not a new performance-results publication.

## Read-only activity geometry utility

`diagnose_activity_geometry.py CASE_DIRECTORY SCORER_DIRECTORY` reads an external
case layout: raw-output-freeze.json (path-to-SHA256), reference.json (segments),
native/native.json (turns), asr/structured.json (segments), asr/raw.json and
asr/cropped.json (transcription). The external scorer must supply its frozen
`tokens(text, language)` function. Reference segments require speaker/start/end/
text_raw; structured units require text/ownership_anchor. These inputs and the
printed diagnostic output remain private and are not included in this backup.
Use absolute paths in the external freeze mapping; independently pin all inputs
and scorer before analysis. The utility only verifies entries supplied in that
mapping, not completeness of the mapping. No inference is performed. Utterance
activity intersections cannot establish per-character reference timing or DER.
Three new synthetic tests cover interval primitives, not full private-case logic.

## Native-turn locality control source

Three additional files are exact exports from source commit
76cd4a6464180c6459a9ee6f0b990c2289203ee8: turn_locality_worker.py,
turn_locality_supervisor.py and TURN_LOCALITY_CONTROL.md. All earlier 24 source
files are unchanged. No other files or ancestor notes from that commit are used.
The supervisor takes an external JSON spec and a fresh output directory. Spec
fields: phase=`locality`, pins (path-to-SHA256), audio, frames, pcm_sha256,
config and repo. Supply absolute paths; config requires language=`auto` and
cpu_threads=2. It needs Linux, psutil and a compatible externally installed
backend AudioAnalyzer with its inference dependencies and existing model assets.
The supervisor verifies supplied pins, not completeness of the pin set; external
review must pin all inputs, scripts, runtime, configuration and model assets.

Window construction, private specs, independent outer timeout and sequential
cleanup review are external responsibilities described by the protocol, not
implemented by these two scripts. Do not invoke the worker directly as a guarded
entry point. The source has unreachable legacy phase branches; accepted phase is
only locality. Restoration tests do not exercise inference, native cleanup or
resource bounds of these new scripts. Existing 19 synthetic controls remain
model-free; compilation checks syntax only. Outputs remain private, never part
of this source checkpoint. This backup does not itself authorize inference.

## Context-zero separator control source

Five added files are exact exports from 8c4dc77bba7e833280f5a1f0be7e7b74de756977:
MOSS_CONTEXT0_CONTROL.md, prepare_moss_weights.py, moss_context0_supervisor.py,
separated_asr_worker.py and separated_asr_supervisor.py. Earlier 27 source files
are unchanged. No other ancestor files or private results are included.

`prepare_moss_weights.py SOURCE DESTINATION RECEIPT` requires the exact externally
acquired checkpoint hash/size embedded in its source and torch 2.10.0+cpu. It
loads tensor state without constructing a model; it is not a model-free test.
Destination must not exist; ensure receipt is fresh too because its write is not
exclusive. The pinned runtime security rationale is preserved in the protocol;
verify upstream advisories independently before any later runtime installation.
This checkpoint installs nothing and does not certify any runtime safe forever.

`moss_context0_supervisor.py SPEC OUTPUT` requires phase=`separator`, pins, repo
and request (absolute external request path). It delegates to the separately
restored backend's audio_backend/overlap_runner.py, with its own dependencies,
request schema and model assets. No backend runner or checkpoint is bundled.
`separated_asr_supervisor.py SPEC OUTPUT` accepts phase=`locality` (the preserved
implementation label), pins, repo, audio, frames, audio_sha256 and config. Config
requires language=`auto`, cpu_threads=2. Runtime needs psutil, soundfile and the
compatible backend inference dependencies. Both output directories must be new.

All input, model, source, runtime and config pin completeness must be independently
reviewed; scripts only check supplied entries. Validate waveform finiteness,
scopes and separator cleanup externally before ASR: metadata/hash checks do not
prove finite samples. Ensure the actual runner uses the reviewed CPU runtime,
not merely the environment used for weight extraction. Window planning, output
validation, outer deadlines and sequence scheduling remain external. Never use
-O/-OO; never directly invoke workers as guarded entry points. Existing 19
synthetic controls and compilation do not test these new inference paths, model
compatibility, resource bounds or cleanup. Private derived weights, requests,
receipts, audio and transcription outputs are excluded from this checkpoint.

## External separator-output validator

Only validate_moss_outputs.py is added from source commit
4ce891da66c96c821a9ebb9c2d7f948c12a47458; earlier 32 source files are unchanged.
CLI arguments: external backend directory, request JSON, regions JSON and
separator output directory. It requires numpy, soundfile and the compatible
backend's private _validate_separator_response helper. It reads result.json,
checks reported group coverage against filtered region jobs, and verifies each
reported track is nonempty mono 16 kHz FLOAT with finite samples. The emitted
receipt includes external paths/scope metadata and hashes; keep it private.
Independent preflight must still establish complete request-to-region coverage,
input/source pins, backend compatibility and observed cleanup. This checkpoint
includes no requests, regions, tracks or receipts. Existing 19 synthetic tests
and syntax checks do not exercise this backend-dependent validator.

### Synthetic separator-output controls

Additionally test_separator_output_validation.py is exported exactly from
c1bfbd808139e87270d0c66ab8270df878450482; validator bytes remain exact4ce891d.
Set QUALITY_BACKEND_SOURCE to an externally restored compatible backend root
and use an existing runtime with numpy/soundfile. Run all four test modules:
`python -m unittest test_model_free test_clean_cn_v1 test_activity_geometry test_separator_output_validation`.
The three new synthetic controls cover valid finite FLOAT, missing group and
NaN rejection, without model loading. They skip if backend path is unset;
19 tests plus 3 skips must not be reported as 22 passing tests. The reviewed
backend overlap_candidates.py SHA256 is
`a539c4adaa1b9b5616ff7fd5b6a6ea543f7c664040498aef3b78e5be82cbbb31`.
Restore backend source from public commit
f5105d48bb588a8f25c1e0b2e5ac646edc4dee9e under
checkpoints/backend-capacity-source-20261031/source/, verify its manifest and
this helper hash. No backend source is changed or copied into this checkpoint.

### Exact source-scope frame count hardening

The validator and its synthetic test are now exact exports from
17d09fe682ddae8991e0063e30c4d81ce1bed27d, superseding their earlier versions
mentioned above. Other 32 source files remain unchanged. Each source's sample
count must equal round(end*16000)-round(start*16000); a one-sample mismatch
synthetic control rejects inconsistent metadata. With the documented external
backend and runtime, the four-module command now runs 23 tests with zero skips.
These are model-free validation tests, not a separator inference run.
