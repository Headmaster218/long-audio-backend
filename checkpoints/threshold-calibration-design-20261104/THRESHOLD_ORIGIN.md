# Origin audit: the 0.75 identity threshold and 0.10 rival margin

Audit date: 2026-11-04 UTC. Read-only source/history/receipt audit; no model inference, audio processing, score sweep, enrollment, runtime change, or threshold change. The preceding frontend negative result is not replaced or reinterpreted.

## Finding

**The recovered evidence identifies 0.75 / 0.10 as an inherited conservative heuristic and an explicitly uncalibrated experimental bank policy, not an operating point validated for a stated false-accept risk.** The exact original experiment or reasoning that selected those two numbers is not recoverable from the inspected public source history. Their earliest visible introduction is a source-snapshot import, not the original development commit.

The source itself is unusually explicit:

- `docs/API.md:40` calls the default cosine threshold .75 with .10 margin over the second distinct identity a **“tiny-sample heuristic, not a calibrated accuracy claim.”** Lines 73–80 explain the intentional uncertainty/abstention behavior rather than forced nearest-identity assignment.
- `docs/VERIFIED_IDENTITY.md:27–30` calls the supplied reviewed-bank policy an **“uncalibrated experimental threshold snapshot, not a transfer of the AMI calibration to a different frontend.”** Lines 39–44 fix the policy to the identity group; lines 149–150 preserve canonical-person rivalry and the uncalibrated status.
- The audit did not recover a development trial manifest, fitting objective, threshold grid, desired FAR/false-identification limit, confidence-bound calculation, or untouched evaluation evidence for selecting **0.75 / 0.10**.

Unless otherwise qualified, paths below are relative to `backend-resume-20261104/checkpoints/backend-capacity-source-20261031/source/`. Historical paths after a commit colon are relative to that commit's repository root.

## 1. Three policies must remain separate

| Policy | Actual values and scope | Evidence |
|---|---|---|
| Legacy default identity matching | Match .75; novelty .45; ambiguity margin .10; no selected calibration file by default | `audio_backend/settings.py:149–157`; `docs/API.md:40,73–80` |
| Explicit reviewed-bank experimental policy, used in the recent identity research | Single reference .75; centroid .75; rival margin .10; minimum query and enrollment 2 seconds; `experimental=true` | `audio_backend/profiles/verified-review-experimental-v1.json:1–9`; `docs/VERIFIED_IDENTITY.md:27–44` |
| Separate opt-in legacy AMI candidate | Single reference .55; verified centroid .56; novelty .45; ambiguity margin .10; minimum query and enrollment 2 seconds | `audio_backend/profiles/eres2net-ami20-v1.json:1–18` |

Loading the AMI profile sets the **legacy** matching settings (`settings.py:313–323`). The reviewed-bank policy is loaded independently (`settings.py:310–312`) and is stored immutably (`verified_identity.py:65–74`). The presence of a file named “calibration” does not validate the separate .75 reviewed-bank policy.

Other numbers found during search are not this policy: recording-local complete-link comparisons use .55/.10, diarization clustering has another threshold, and the bounded support inventory's .75 **seconds** is a resource budget, not speaker similarity.

## 2. Earliest recoverable introductions

The repository contains parallel published source-projection and integration histories. These dates are **publication/import dates**, not evidence of when the original numerical choice was made.

1. **Legacy defaults are already present in the initial imported baseline:** commit `db256be9f70e9b8dd37adef45c2bf330775c2da9`, published `2026-10-09T06:27:55Z` (commit stores 2026-10-08 23:27:55 −07:00). `audio_backend/settings.py:54–62` contains .75/.45/.10 and the separate calibration hook. `BASELINE_IMPORT.json:2–8` says the material is a historical recovery source snapshot, `imported_now_not_original_commit_history=true`, with **no historical test evidence imported**. Its source archive hash is `20bcfc8a9c5e401713f49a067aeea87b5a65b742cbddd48f0816279351bebc09`. The commit message maps to original public commit `9c18644834049e3fd111f27c062f37992fecdac6`; that mapping does not supply earlier numerical-choice evidence.
2. **The warning is visible immediately in public documentation:** commit `cf1541deb64027104be5b921a9ff34e253800528`, published `2026-10-09T06:28:12Z`, `docs/API.md:42`, already says .75/.10 are a tiny-sample heuristic. `docs/SPEAKER_REGISTRY.md:5–7` separately describes the .55/.56/.10 AMI candidate and its limits.
3. **The reviewed-bank policy is added in public integration:** commit `6db8655bafb90ad29183c2551cc565b2865ef8fe`, published `2026-10-09T06:57:47Z`, adds the exact nine-line `audio_backend/profiles/verified-review-experimental-v1.json`. `INTEGRATION_0_2_0RC2_DEV2.json:63–68` records the new 195-byte file as byte-identical, SHA-256 `5217b5fe9737ba2b0348d8ec1d2ba31ccbce6da264630a7506a6f5a71b09cbff`. Its provenance identifies local source commit `839187b89c833fe7f0457daee38edcfdb0d0f0a7`; the public integration message maps to original public commit `935f7482ffa0c8886f955e08d8e0b9e814c03dd9`.
4. **The corresponding source-history addition is also retained:** published commit `2b14f9448b3f7ba721daa86b68b9285177e29bfb` (`2026-10-09T20:32:23Z`) has a new-file diff adding that exact policy. Its message maps to original source commit `f0f9d67fc7adffdb0a4ef7a14370072022560521`, original source parent `84c4bbbd9efe851b7ee326807f8f06d95540a92c`. The original object is unavailable in this checkout. The same commit's `docs/VERIFIED_IDENTITY.md:18–21` already disclaims calibration transfer.
5. The projected source history starts at `e49651caef33cdb4de0d95eaecf039f96f136b35`, which already contains legacy .75/.10. Its message maps to original root source commit `bfa760194524b1ac867c747fcd63f079e6eacc8c` and explicitly says original author timestamps and commit IDs are not preserved. That original object is also unavailable here.

Therefore the auditable answer to “when was it introduced?” is **the exact first visible imports/additions above**. The available history does not justify naming an earlier original experiment, claiming an EER-derived choice, or treating these publication timestamps as the original selection date.

## 3. Dataset evidence: what was and was not calibrated

### For .75 / .10

The retained wording supports “small-sample heuristic” and deliberate conservative abstention. It does **not** identify the small sample, participant count, split, or score distribution used to pick these numbers.

`model-manifest.json:111–123` lists `official-four-speakers-zh`, a 0–30-second region of Sherpa's `0-four-speakers-zh.wav`, as a validation source. Its presence is **not evidence that this audio selected or calibrated .75/.10**; no inspected receipt links that sample to the threshold-selection procedure. It must not be called the threshold's training/calibration set.

The reviewed policy JSON has no dataset or calibration artifact attached. Its functional/synthetic tests validate branches and integrity, not acoustic error rates. Neither the recent AMI carryover study nor the 18/18 correct-ranking LibriSpeech screen is historical evidence for the original selection. Both are already exposed development evidence and must remain excluded from a newly independent calibration or test set.

### For the different AMI profile

`audio_backend/profiles/eres2net-ami20-v1.json:20–41` preserves these historical claims, not newly reproduced measurements:

- 20 calibration people: 16 enrolled and four withheld unknown people.
- First clean exemplar for single-reference automatic identity; three explicitly verified exemplars for the research centroid comparison. Runtime supports two to eight verified examples, but these other counts are explicitly not independently validated.
- Artifact digest `a971c82cc368af62286ac820567a47732083109de557155bf4149b40ec94b8fa` is recorded. The raw artifact, full split manifest, selection procedure and score distributions were not recovered in this audit.
- Original cross-day single-reference result 57/116; fresh cross-day 29/35; fresh far-field 24/35; Mandarin within-meeting 251/277.
- Original EN2001 cross-day and far-field recall gates failed. Some fresh-person source sessions supplied other calibration people despite identity separation. Unknown trials were small and correlated. Strong acted-style oracle trials included wrong assignments. These limitations remain part of the evidence.

Those records cannot establish .75 as calibrated, cannot make .55 safe for today's native crop policy, and do not establish a fully person/session/acoustic-independent holdout. The recorded four unknown **people** must not be mistaken for a large independent false-accept sample.

## 4. Encoder and frontend binding

The baseline manifest (`db256be:model-manifest.json:24–31`) already binds the 512-D Chinese 3D-Speaker ERes2Net base ONNX, SHA-256 `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b`, 39,593,761 bytes. Both current manifests/profile retain that hash.

The baseline **runtime implementation**, not proof of the historical calibration run, uses Sherpa CPU `SpeakerEmbeddingExtractor`, 16 kHz input, clean-audio pooling, at least two seconds, `input_finished()`, and float32 L2 normalization (`db256be:audio_backend/inference/analyzer.py:258–282`). Its dependency lock lists Sherpa 1.13.8 and NumPy 2.5.3. A runtime consumer and a matching model digest do not reconstruct the missing calibration frontend receipt.

The reviewed-bank provenance audit (`docs/EMBEDDING_SPACE_PROVENANCE.md:17–37,70–105`) binds the native implementation to:

- Sherpa 1.13.8 official revision `11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf`, ONNX Runtime 1.28.2, KNF 1.22.3, and the exact 512-D encoder.
- 16 kHz mono float32; waveform multiplier one; 80 mel bins; 20 Hz low cutoff; **7600 Hz high cutoff; snip_edges=False**; 25/10 ms framing; Povey window; dither zero; per-bin global-mean subtraction; float32 producer normalization.
- A supported audited Linux build/NumPy binding, not arbitrary builds or matching dimensions alone.

These provenance checks prove an identified representation path, **not a calibrated acceptance threshold**. The prior frontend-contract report (`../backend-frontend-contract-20261104/TRAINING_FRONTEND_CONTRACT_REPORT.md:7–27,31–54`) finds a 512-D historical 3D-Speaker training recipe with a 3-second training crop and torchaudio-derived 8000 Hz / snip=True options, but no chain binding those exact training/dependency bytes to the installed ONNX. It explicitly does not establish that the native frontend is wrong. The training-distribution audit accompanies this report; model pretraining, historical threshold calibration, and current validation must remain distinct.

## 5. Upstream example defaults are not product policy

At the exact pinned Sherpa revision, [`python-api-examples/speaker-identification.py`](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/python-api-examples/speaker-identification.py#L88) sets `--threshold` to **0.6** at line 88. Its opening example uses a **WeSpeaker CN-Celeb ResNet34**, averages multiple enrollment embeddings, and calls `manager.search(..., threshold=args.threshold)` at line 247. It does not provide this product's .75 threshold, .10 runner-up rule, calibration dataset or risk guarantee. The retrieved source is preserved separately in `UPSTREAM_THRESHOLD_EXAMPLE.json` for reproducibility; it was only read, never executed.

The pinned `scripts/3dspeaker/test-onnx.py` retained in the frontend source receipts computes and prints a raw cosine score. It supplies neither an identification decision threshold nor a rival margin. Its [0,1] score comment is not a valid constraint on raw cosine, whose mathematical range is [-1,1].

Thus an illustrative upstream default, training-loss angular margin, EER/minDCF benchmark operating point, diarization clustering threshold, and this product's identity acceptance policy are not interchangeable.

## 6. Exact current decision and recent research inheritance

`audio_backend/verified_identity.py:306–325` normalizes a centroid using binary64 math and computes a clamped cosine. It chooses the best distinct person, computes `best - second_best` (or 2.0 with only one person), chooses single/centroid threshold according to reference count, rejects when `best < threshold`, then rejects when `margin < policy_margin`. Equality passes each numerical gate. The complete matcher also checks eligibility, integrity and explicit negative evidence (`verified_identity.py:620–643`). These gates are not equivalent to a calibrated posterior probability.

The ES2004a carryover contract copies .75/.75/.10 (`../backend-carryover-research-20261104/es2004a_bank_carryover_research_20261104.py:15–19`). The frontend screen uses the unchanged single-reference specialization and .75/.10 with six gallery identities (`../backend-frontend-comparison-20261104/frontend_experiment.py:166–200`). It inherited the operating point; it did not fit it. Correct ranking with zero accepted identities measures discrimination plus abstention on that exposed screen, not the validity of a lower threshold.

## 7. Implications for the next design, without changing production

1. Retain .75/.10 as the immutable product/reference baseline while independently developing a candidate. Do not retrofit the exposed 18-query screen or the carryover negatives into a new “independent” partition.
2. A new numerical operating point needs its own exact model/frontend/crop/enrollment/gallery/score-policy identity. In particular, the rival margin depends on gallery composition and size, and single-reference versus centroid policies need separate prespecified treatment.
3. Freeze independently recruited/selected calibration and completely independent holdout partitions before fitting or scoring. The old AMI profile's identity-disjoint but session-overlapping limitation is a concrete warning against equating different person IDs with full independence.
4. Declare false acceptance of unknown people, misidentification of known people, and rejection/coverage separately, with confidence intervals at the independent participant/session level. Small correlated crop/pair counts do not provide a low-risk guarantee.
5. Missing original provenance remains missing. The absence of recovered .75 calibration evidence is a reason to design a proper study, **not authorization to lower the threshold or deploy a replacement**.

## Reproducibility and scope

Repository inspected: `/workspace/scratch/12ec3121e93f/backend-resume-20261104`. The checkout advanced during coordinated work from `594b0e4` to `b81646b360f55a131a2cddd3063630b81a32483f`; this audit changed no repository files. Historical findings use immutable commits above. Current-policy bytes were verified directly:

- Verified policy SHA-256: `5217b5fe9737ba2b0348d8ec1d2ba31ccbce6da264630a7506a6f5a71b09cbff`.
- AMI candidate profile SHA-256: `670388018d7af578bc54405a27287422d54d0bd98a181f8c366c13f6d80da590`.
- Current `verified_identity.py` SHA-256: `0217ec618168b4d4be3083109733b56fef7528c5692038d25df785f1925cedbb`.
- Frozen frontend experiment script SHA-256: `4c324be6150b02d513cbbd867acfd1910e3126586ab719e7ec8e5a277e4a7ed7`.

Read-only reproduction commands from the repository root:

```sh
git log --all --reverse --format='%H %aI %s' -- audio_backend/profiles/verified-review-experimental-v1.json
git log --all --format='%H %aI %s' -S 'speaker_match_threshold: float = .75' --
git log --all --format='%H %aI %s' -S 'tiny-sample heuristic' --
git show db256be:BASELINE_IMPORT.json
git show db256be:audio_backend/settings.py | nl -ba
git show cf1541d:docs/API.md | nl -ba
git show 6db8655:INTEGRATION_0_2_0RC2_DEV2.json | nl -ba
git show 2b14f94 -- audio_backend/profiles/verified-review-experimental-v1.json
git show 2b14f94:docs/VERIFIED_IDENTITY.md | nl -ba
git show e49651c --format=fuller --no-patch
git show db256be:audio_backend/inference/analyzer.py | nl -ba
```

No raw audio, vectors, individual biometric scores or credentials are included in this report. The historical baseline archive and original calibration artifact were not acquired; no unsupported historical guarantee is inferred.
