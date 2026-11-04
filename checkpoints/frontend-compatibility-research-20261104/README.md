# Fixed frontend compatibility research

Status on 2026-11-04: the single frozen experiment completed and was independently audited. Adapter compatibility passed; neither frontend emitted any identity on 18 known and 6 withheld queries, so usable coverage remained zero. Production remains unchanged. See RESULT_REPORT.md, PUBLIC_AGGREGATE_RESULT.json and PUBLIC_RESULT_AUDIT.json. The prospective sections below record the preparation state before inference.

The source audit distinguishes an installed-model hash from a training provenance claim. The historical 512-D training recipe inherits frontend defaults consistent with the candidate, but the exact weights are not bound to that training revision or environment. The candidate is named **pinned test-source-derived frontend candidate**, not a proven training correction.

The public reproducibility manifest fixes 36 two-second crops from 8 LibriSpeech test-clean speakers: six known speakers with two references and three queries each, plus two withheld speakers with three queries each. The primary gallery uses only the first reference for each known speaker. The other six references are diagnostic only. Corpus independence is bounded to the prior research inventory; training-set independence and cross-corpus physical-person independence are not established. Read English is not meeting, Chinese, noise or long-duration qualification.

The planned adapter prerequisite compares the actual native encoder path with an explicit same-configuration frontend through the exact same ONNX Runtime bytes. Only if fixed parity and completeness gates pass may the candidate change both edge handling and mel cutoff. Thresholds are not tuned and a changed frontend is a separate research vector space. No production bank, database, enrollment, default or acceptance threshold is changed.

Model-free preflight found issues in unknown-label interpretation and supervisor completeness/cleanup. Those are preparation defects, not model results, and must be fixed before a separately frozen run. Scripts, final prospective pins and results will be added once reviewed. Raw audio, transcripts, models and identity vectors are intentionally not included.

## Prospective implementation backup

The included source and plan are frozen before inference. Model-free controls passed 15 execution tests and 10 metric tests. Earlier preparation failures are retained locally and are not model-quality outcomes. The full local input freeze SHA256 is fbe31b4d31c8bb43af745618f93bda75ccda9e6e11e7de6254471e3a23b5bc3f. See PREFLIGHT_PINS.json for source and test hashes.

This is a research checkpoint, not an independently deployable product runtime. Reproduction needs the exact runtime/model/crop bytes and directory manifest described by the plan. Scripts accept explicit preparation/runtime/model/sample paths. Metric tests allow BACKEND_SOURCE and FRONTEND_BOUND_SAMPLES environment overrides. No weights or speech are bundled.

The C helper includes provenance/ort-c-api-v1.22.0.h from the official ONNX Runtime v1.22.0 C API header, SHA256 d683537d0fdc29e977b5520f7f15d87a0ac212ae6d94fa9be8893a52655621ae. Obtain it from https://github.com/microsoft/onnxruntime/blob/v1.22.0/include/onnxruntime/core/session/onnxruntime_c_api.h and preserve its MIT notice. Compile against that header and load only the hash-pinned native ORT library. The binary helper is not published. No experiment result is implied by compilation/API availability.
