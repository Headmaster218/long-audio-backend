# Fixed frontend compatibility research

Status on 2026-11-04: prospective preparation only. No feature/model experiment has run and no numerical or recognition-quality conclusion is claimed. Production remains unchanged.

The source audit distinguishes an installed-model hash from a training provenance claim. The historical 512-D training recipe inherits frontend defaults consistent with the candidate, but the exact weights are not bound to that training revision or environment. The candidate is named **pinned test-source-derived frontend candidate**, not a proven training correction.

The public reproducibility manifest fixes 36 two-second crops from 8 LibriSpeech test-clean speakers: six known speakers with two references and three queries each, plus two withheld speakers with three queries each. The primary gallery uses only the first reference for each known speaker. The other six references are diagnostic only. Corpus independence is bounded to the prior research inventory; training-set independence and cross-corpus physical-person independence are not established. Read English is not meeting, Chinese, noise or long-duration qualification.

The planned adapter prerequisite compares the actual native encoder path with an explicit same-configuration frontend through the exact same ONNX Runtime bytes. Only if fixed parity and completeness gates pass may the candidate change both edge handling and mel cutoff. Thresholds are not tuned and a changed frontend is a separate research vector space. No production bank, database, enrollment, default or acceptance threshold is changed.

Model-free preflight found issues in unknown-label interpretation and supervisor completeness/cleanup. Those are preparation defects, not model results, and must be fixed before a separately frozen run. Scripts, final prospective pins and results will be added once reviewed. Raw audio, transcripts, models and identity vectors are intentionally not included.
