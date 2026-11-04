# Fixed frontend compatibility experiment: proposed freeze

Status: **prepared, not launched**. The operator owns final plan, source, sample and
runtime acceptance and the later inference launch. This document is a proposal,
not authorization, not a numerical result and not a production qualification.

## Question and interpretation

Measure whether two fixed frontend settings change outputs of the exact retained
512-dimensional ERes2Net encoder on previously unused, controlled multi-speaker
speech. Compare the actual pinned native path with one explicitly named
**pinned test-source-derived frontend candidate**. Do not call that candidate
the actual training frontend or a correction established by training provenance.

The separate training audit is `TRAINING_FRONTEND_CONTRACT_REPORT.md` in the
public checkpoint. Its preparation source is
`../backend-frontend-contract-20261104/TRAINING_FRONTEND_CONTRACT_REPORT.md`.
It finds a historical 512-D/80-bin/16-kHz training recipe consistent with
snip_edges=True/high_freq=0 under one pinned torchaudio default resolution, but
does not bind the installed checkpoint to that recipe, dependency, or execution.
The official Sherpa test script and its historical dependency installation are
different evidence. Its unpinned dependency history is not silently reconstructed.

## Unchanged production boundary

- No product source/default, installed product runtime, database, live service,
  reference bank, model bytes, or artifact allowlist is changed.
- Research vectors use separate research labels. They are never assigned a
  product embedding-space certificate, enrolled, merged or mixed across arms.
- The single-reference decision rule stays at similarity 0.75 and rival margin
  0.10, with exact binary64 comparisons and existing normalization/arithmetic.
  This is a fixed compatibility operating point, not calibrated equivalence
  between frontends and not evidence of general safety.
- No threshold sweep, learned mapping, model search, alternate seed, crop
  extension, result-dependent replacement, or additional corpus is allowed.

## Frozen source and runtime route

- Frozen product source: `../backend-voice-source-20261104`.
- Original runtime: `../backend-voice-runtime-20261104`, CPython 3.12.14,
  Sherpa 1.13.8 reporting revision 11afbd00, exact full official revision
  `11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf`.
- Model: `../backend-operational-loop-20261104/models/speaker.onnx`,
  39,593,761 bytes, SHA256
  `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b`.
- Separate CP312 research venv: `research-runtime/`. Its 2,014 original package
  files were copied, not hard-linked, then rehashed against the source.
- One added package: kaldi-native-fbank 1.22.3, official CP312 Linux x86_64
  wheel, 322,002 bytes, SHA256
  `f16e74372fe9e20abb4183f98a8e2288d5ee4c48d04d94b6160311170e007661`.
  The exact URL, ZIP-path checks and complete RECORD verification are in
  `KNF_WHEEL_RECEIPT.json`; installation was offline without dependencies.
- **No Python ONNX Runtime package is installed.** The small research C helper
  uses dlopen on the absolute path of the exact copied native ORT library,
  27,026,609 bytes, SHA256
  `4b3607aebd1784b26b6f9b20e4bd974c7ab8287043e4d095cb7d2cb40b5e566e`.
- Its official ONNX Runtime v1.22.0 C header requests API22, SHA256
  `d683537d0fdc29e977b5520f7f15d87a0ac212ae6d94fa9be8893a52655621ae`.
  The metadata-only probe accepted API22 and reported runtime 1.28.2. It created
  zero environments, sessions or models. API compatibility is not output parity.
- Both explicit feature paths use the same helper, same library, exact model,
  CPU provider and two intra/inter-op threads. Native Sherpa also uses two
  threads. The historical official test script's one-thread setting is not
  copied: only its declared frontend profile is the candidate intervention.
- A new KNF wheel is not claimed byte-identical to the KNF build inside Sherpa.
  Both the raw-feature bridge and embedding bridge below must pass.

Before launch, pin the completed harness, helper source/binary, source evidence,
installed runtime-package bytes, actual interpreter/venv configuration, mapped
numerical libraries, actual model, exact sample PCM and plan. This is a
same-host compatibility experiment, not a complete operating-system, standard-
library or reproducible-build proof. Verify loaded paths and unchanged hashes at terminal time.
Do not silently switch ORT libraries or fetch new weights.

## Fixed population and observation roles

Use `../backend-frontend-samples-20261104/frozen_sample_manifest.json`, metadata
selection SHA256 `20b2dd8e12fcbfa1816bf55d319887dc75e893f7a41cc8b5386b816e64ddc86d`.
Exact official audio bytes and all 36 crop frame ranges are now bound before
feature extraction or inference in bound_sample_manifest.json, SHA256
`3afb278301172b658db455a402b796a0df5c3d6f334ff93bde28fccbc649f53f`.
The preparation integrity receipt verifies original transcript selection,
FLAC PCM checks and every fixed crop; no replacement occurred.

There are 36 planned distinct utterance observations, each a center crop of
exactly 32,000 original 16-kHz mono samples, without resampling, gain adjustment,
VAD, denoising, padding, concatenation or score-based selection:

- Six known speakers, each with two reference observations from one fixed
  chapter and three queries from another fixed chapter.
- Two withheld speakers, each with three queries and no gallery reference.
- Primary decisions use **only the first reference per known speaker**. The six
  second references are diagnostic-only; they never modify a centroid or bank.
- The same byte-identical crop is used in every path/profile. A short, missing,
  malformed or failed chosen input is retained as a failure, never replaced.
- Repeatability anchors are the fixed manifest ordinals 1 and 31, one reference
  and one unknown query. Repeats never replace the first output or contribute
  extra recognition observations.

This is evaluator-selected English read speech, not automatic conversational
crop selection, far-field/overlap robustness or Chinese-language qualification.
All prior AMI, AliMeeting, AISHELL-4 and Oyez data are excluded. The historical
audit establishes no observed prior project use of the selected corpus; it
cannot prove physical-person uniqueness across corpora or training non-exposure.

## Profiles: two embedding arms plus a bridge, four feature-only cells

All explicit options are recorded, including the full KNF as_dict configuration.
Common values: float32 PCM/32768, 16 kHz, 80 bins, low_freq=20, zero dither,
25-ms frames, 10-ms hop, Povey window, pre-emphasis float32 0.97, DC removal,
power spectrum, 512-point FFT, log-fbank with float32 epsilon floor, no energy
feature, no HTK/librosa mode, VTLN warp=1 and input_finished flushing.

1. **A actual native baseline:** installed Sherpa extractor; high_freq=-400
   (7,600 Hz), snip_edges=False; native Eigen per-bin mean subtraction.
2. **A-prime bridge control:** explicit KNF with the same settings as A;
   NumPy float32 per-bin mean subtraction and exact-library C API inference.
3. **B fixed candidate:** identical to A-prime except high_freq=0 (8,000 Hz)
   and snip_edges=True. It shares A-prime's mean subtraction implementation.

Output normalization for every path is the pinned NumPy float32
asarray/linalg.norm/divide operation, followed by the existing binary64 matcher
normalization. NumPy versus native Eigen reduction order is a real bridge
difference, explicitly tested rather than concealed.

The feature-only 2x2 diagnostic is justified because two contract settings differ:
N=(False,-400), H=(False,0), S=(True,-400), C=(True,0). No embeddings are computed
for H or S. This is the whole factorial, not an open-ended sweep.

For 32,000 samples, predicted geometry is 200x80 for N/H and 198x80 for S/C.
Native frame starts are -120+160*i, while snipped starts are 160*i. Snipping
therefore changes the frame grid, not just two boundary frames. Report absolute
frame supports and reflective-edge semantics. Compute pointwise feature deltas
only for aligned N/H and S/C pairs. Do not align unequal grids by truncating,
interpolating or pretending that equal indices have equal acoustic support.
For the snipping factor report geometry and per-bin distribution summaries only.

## Gates fixed before numerical work

The following are proposed numerical engineering tolerances, not estimated from
this corpus and not proof of bitwise equality. The operator must accept or amend
them **before** any computation. No tolerance is widened after observing data.

The operator accepted these exact tolerances before numerical work. Feature
deltas/RMSE and vector differences use binary64 arithmetic. Cosine is the
binary64 dot product divided by both binary64 norms, not the raw dot product
of approximately unit-length float32 producer outputs. Matching separately
preserves the existing product's binary64 math.fsum normalization and scoring.

1. Exact geometry and PCM hashes; finite 80-bin float32 features and 512-D
   finite nonzero embeddings; expected output shape [1,512].
2. Native raw get_frames(0,200) versus explicit N raw features, on every valid
   crop: same shape; all elements pass abs(delta) <= 2e-5 + 1e-6*abs(native).
   Also report bitwise equality, max absolute error and RMSE without rounding.
3. A versus A-prime normalized producer vectors, on every valid crop:
   max absolute component difference <=1e-5, L2 difference <=1e-4 and
   1-cosine <=1e-7. Report raw-vector norms/differences separately.
4. A/A-prime must produce the same primary winner and final decision/refusal
   for all 24 planned queries with available required vectors. Report exact
   score and margin deltas; do not use epsilon in actual matching.
5. Fixed repeat anchors receive two additional calls per path after their first
   measurement. Compare first versus each repeat with the same vector bounds,
   and report exact equality separately. This is within-process repeatability,
   not cold-start or cross-machine determinism. It is not statistical replication.

A bridge or runtime-integrity failure stops the dependent candidate comparison.
Report technical incompatibility and the retained failures, rather than a causal
frontend accuracy claim. Declared repeats are measurement, not failure retries.
An unexpected inference/setup failure aborts that stage without automatic retry.

## Required metrics and denominators

Report per crop/profile frame count, raw and centered feature shape/dtype,
finite status, feature hashes and norms; the matched-configuration agreement
metrics; embedding hashes/norms/dimension and repeatability; peak resources,
elapsed/cpu and every explicit failure. Separate source-valid, vector-complete,
bridge-passed and recognition-scorable denominators.

Primary gallery: six first references. Primary query denominator: 24 planned
queries =18 known +6 withheld. Planned query/reference scores:144 =18 same
speaker +126 different speaker, including36 unknown-versus-gallery scores.
Use all six competing identities; missing required references are an unavailable
gallery, not silently dropped rivals. Unknown input/vector failures are distinct
from acoustic threshold/margin abstention.

Report for A and B separately, and paired transitions:

- Known correct suggestions /18; known wrong suggestions /18; known
  abstentions /18; unavailable known queries /18.
- Unknown false accepts /6; unknown threshold/margin refusals /6; unavailable
  unknown queries /6. Actual automatic merges remain unmeasured: none occur.
- Total emissions, correct-emission precision (undefined if no emissions),
  coverage, all-query abstention and unavailability, and each refusal reason.
- True-speaker score, best competing score, true-speaker margin, rank and
  winning score/margin for every known query; highest impostor score for every
  unknown query. Show fixed-pair same/different score ranges/medians and overlap
  descriptively, with no threshold search, ROC fitting or EER optimization.
- Secondary diagnostic only:24 queries x12 reference observations =288
  dependent pairs,36 same +252 different, including72 unknown-versus-reference.
  Second-reference scores and same-speaker stability never affect decisions.

Pairs sharing speakers, chapters or observations are dependent. Do not give
pair-level binomial confidence intervals or imply 144/288 independent subjects.
With only eight speakers, report counts and speaker-level outcomes, not broad
population accuracy, calibrated FAR, generalization or deployment readiness.

All predictions/failures for both arms are sealed before writing outcome
summaries. Speaker labels already used for evaluator-controlled selection are
not represented as a blinded-label experiment.

## Bounded serial execution proposal

Runtime preparation is separate and already complete, with no model calls.
The bounded Python harness and C wrapper are implemented. Model-free execution
and metric controls are on disk; final exact-source review and operator release
are still required before the first model constructor.

- Feature stage: at most36 crops x4 explicit profiles =144 feature extractions,
  plus native features read from the36 ordinary A streams. No feature tuning.
- Embedding stage:36 first calls each for A/A-prime/B =108 calls, plus
  2 anchors x2 extra repeats x3 paths =12, total at most120 calls/240 seconds
  of repeated 2-second waveform support. No warmup or other qualification encode.
- Three serial model-owning processes: A, then A-prime and bridge acceptance,
  then B. Keep original process handles, positive waits and separate failure
  receipts. No simultaneous model families. No candidate if bridge fails.
- Proposed inference bound:900 wall seconds inclusive of one30-second cleanup
  pool;600 aggregate CPU seconds; affinity restricted to two CPUs, nice19;
  two intra/inter-op threads configured, not a hard total-process thread cap;
  3GiB sampled owned
  RSS,32MiB fresh numerical output. Require>=2GiB host-memory reserve and5GiB
  free disk at admission. Sampling is not a continuous kernel aggregate cap.
- Audio acquisition/crop preparation has its own separately approved budget.
  Runtime preparation's256MiB cap does not authorize corpus storage or inference.
- Any integrity, ownership or resource violation stops dependent work, retains
  partial evidence and requires operator review. No renewed budget or silent retry.

## Remaining launch blockers

1. Official audio/crop binding is complete and hash-pinned.
2. The bounded harness, score arithmetic controls and terminal manifest writer
   are implemented; complete final exact-source/resource review.
3. Seal final model-free test results and the runtime/helper/source review.
4. Operator freezes the complete input/plan/source/runtime bundle and explicitly
   releases the inference stage. Until then no model constructor may be called.

For eventual Git backup, publish only reviewed code, public provenance and
sanitized aggregate reports. Do not publish model weights, raw recordings,
transcripts, per-person vectors or private runtime copies.
