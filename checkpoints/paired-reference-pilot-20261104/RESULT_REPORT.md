# Paired single-reference versus three-reference AMI mechanism study

## Result

The prespecified holdout primary set showed no difference: both arms correctly identified 2/2 known speakers and rejected 2/2 unknown speakers. In the complete 12-query holdout diagnostic set (which includes the four primaries), both arms correctly identified 4/6 known queries, refused 2/6, and made zero known-to-wrong-person assignments. Single-reference accepted 1/6 unknown queries; three-reference accepted 0/6. These are descriptive counts from a tiny, dependent cohort, not evidence of general superiority or a deployable threshold.

The arms used separately calibrated thresholds: single-reference 0.25 and three-reference 0.43, both with a fixed 0.10 winner margin. Thus the comparison combines the reference representation with its C-selected operating point; it does not isolate a reference-count effect at a common threshold. No threshold was selected or changed using T.

## Frozen design and scope

- Eight AMI global speaker IDs, disjoint between C and T. Each split has two enrolled people and two unknown people.
- Three fixed two-second references per enrolled person from distinct annotated turns in one phase-a meeting. Three fixed two-second queries per speaker from phase-d. Each split has 18 unique crops and 12 query decisions per arm.
- One prespecified primary query per speaker, with two further diagnostic queries. Diagnostics include the primaries and are not an additional independent sample.
- Same raw recording-day label, different meetings. Phase a/d is scenario order, not proof of wall-clock chronology. Three references are three turns in one meeting, not three independent enrollment sessions or the strict D3 contract.
- English, same-day, cross-meeting mechanism study only. Historical training/evaluation exposure is unknown. This is not Chinese, cross-day, long-duration or final user acceptance evidence.
- Annotation-supported crop eligibility is not a human-listening or physical speaker-purity certification. Source-role metadata contains labels, so the procedure is not label-blinded.

## Calibration and holdout counts

| Split / population | Arm | Known correct | Known refused | Known wrong | Unknown accepted | Unknown refused |
|---|---|---:|---:|---:|---:|---:|
| C primary | single | 2/2 | 0/2 | 0/2 | 0/2 | 2/2 |
| C primary | multi3 | 2/2 | 0/2 | 0/2 | 0/2 | 2/2 |
| C all diagnostic | single | 6/6 | 0/6 | 0/6 | 1/6 | 5/6 |
| C all diagnostic | multi3 | 5/6 | 1/6 | 0/6 | 0/6 | 6/6 |
| T primary | single | 2/2 | 0/2 | 0/2 | 0/2 | 2/2 |
| T primary | multi3 | 2/2 | 0/2 | 0/2 | 0/2 | 2/2 |
| T all diagnostic | single | 4/6 | 2/6 | 0/6 | 1/6 | 5/6 |
| T all diagnostic | multi3 | 4/6 | 2/6 | 0/6 | 0/6 | 6/6 |

No planned vector or query was dropped. Equal aggregate known correctness does not mean the same individual queries succeeded: one diagnostic known query changes refusal to correct and another changes correct to refusal. One diagnostic unknown acceptance changes to refusal.

Even under an unestablished hypothetical independent Bernoulli model, 0/2 errors has a one-sided 95% exact upper reference bound of about 77.64%. The machine report labels this conditional calculation explicitly. There is no formal population/safety confidence interval, paired-effect interval, independence claim, or low-risk certification.

## Execution and provenance

The original acquisition was interrupted without a completion receipt. Recovery preserved its partial file, downloaded the missing fourth source to a new location, verified its preserved prefix and fixed source geometry, and completed without source substitution. This is a recovered acquisition, not a retroactively successful original single attempt. Four complete source WAVs and all 36 fixed crops were verified. The bridge independently reconstructed every crop from its original frozen source frames and checked WAV, PCM and int16-to-float32 hashes.

The unchanged native runner and its unchanged helper retained 2,048 runtime/interpreter/model/numerical artifact pins. The speaker ONNX model SHA256 is 1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b. This is same-host CP312 evidence, not an OS or reproducible-build attestation.

C and T each ran once, sequentially, with one model session and 22 completed calls: 18 planned unique clips plus four repeats (two each for a frozen reference and query). Both supervisors exited 0 with positive original wait, absent owned process group, no cleanup error, no forced termination and no unrun IDs. All repeated-vector checks, exact input bindings, journal order, raw/normalized array consistency and native resource accounting passed. Runtime inventory was checked before and after native stages and again by the saved-result gates.

The protocol and null C/T plans were frozen before C inference. C predictions and the C report were sealed, both thresholds locked, and the retained lock hash was supplied before releasing T. T predictions and locked decisions were atomically sealed before parsing T truth for reporting. No production source or settings were changed.

Synthetic/preparation checks: 41 paired-scoring/bridge tests and 26 unchanged native-runner tests passed. These tests are distinct from the two real native stages.

## Key audit pins

- Protocol: 2acc4994830f20ac76cc5431503e1a7d02324d27b13beafaa938eb9aa8eb072e
- Crop manifest: 4e79dea3f1f81cc8448fabf6b839c5eb88e9a6fa4d590631e845ab15c23941da
- C manifest: 90711409ab4a141d7f1c2add83237b7ec6ddbdfbf77599ba77f14a04d8fb8f39
- Paired C lock: e94c95a6e61713726e681d8d68141f17f9e61220b67f678f459352a837fcf325
- T manifest: 4c72215227a2e30ba4c4e6586ff639ede5b22a1b93f59303def220c0e1b2a8f7
- T report: 720f757ba9e504cbec3a5d8346656b5c2847b50fa929da90afd37184c3fb710e

Full local evidence is in inputs-01/, C-run-01/, T-run-01/, C_NATIVE_GATE.json, T_NATIVE_GATE.json, C_STAGE_RECEIPT.json and T_STAGE_RECEIPT.json. Retain these evidence files unchanged. Git publication is coordinated separately and excludes source audio and vectors.

## Independent result audit

A separate implementation using NumPy, without importing the scorer, reproduced all C/T scores within 1e-12, C threshold-grid selection and decision counts. It also verified saved artifact hashes and positive process terminal receipts. INDEPENDENT_RESULT_AUDIT.json records PASS.
