# ERes2Net clean-support pilot

## Scope and execution

This is a small, preselected, two-person experiment within one AISHELL-4 meeting. It measures speaker-embedding similarity for oracle-selected audio. It does not establish cross-day identity, unseen-person rejection, end-to-end diarization, or production enrollment quality. Product defaults and galleries were not changed.

- Date: 2026-10-15 UTC. Original invocation exited 0; the helper reported 3.8224 seconds elapsed.
- 26 model-free checks passed; exactly 12 embedding calls consumed 36 seconds of audio.
- One CPU ERes2Net extractor, sherpa-onnx 1.13.8, ONNX Runtime 1.28.2; 512-dimensional float32-normalized vectors.
- Sampled aggregate peak: 197,378,048 bytes; maximum observed sampling gap: 0.135718 seconds. This is a sampled resource observation.
- One ffmpeg decode exited 0 with empty stderr; its original child was waited. The resource sampler joined.
- Retained WAV bytes: 14,672,606; other original output bytes: 179,610. No audio or embedding vectors accompany this report.

## Data and predeclared method

Source: [official AISHELL-4 test archive](https://www.openslr.org/resources/111/test.tar.gz), member test/wav/M_R003S01C01.flac; AISHELL-4 attribution and CC BY-SA 4.0 apply. An existing, incomplete FLAC prefix was hashed and decoded only through 422.5 seconds, producing 6,760,000 mono 16 kHz PCM16 frames. Full-file completeness and full PCM MD5 were not asserted.

Manual TextGrid activity selected supports before any scores. All other annotated speakers were subtracted, with 100 ms component-edge margins. Independent checking included symbol-only annotations omitted by the earlier interval JSON; selected supports are unaffected. This corrects the aggregate 006-F prefix inventory to 220.967554 seconds raw and 215.365875 seconds margin-clean. These annotation-derived spans do not independently prove acoustic purity.

Six mutually disjoint whole windows separate enrollment, calibration and evaluation. Each person contributes one window per role. Each arm receives exactly 3 seconds per call:

- Concatenated: chronological 1.5-second pieces from two naturally separated clean components, without crossfade.
- Contiguous: one 3-second interval from the second component.

The arms share 1.5 seconds within each role; the remaining 1.5 seconds differs. Thus phonetic content and time differ as well as concatenation. This is not a causal seam-only test. Production uses predicted support and a longer pooling budget; this pilot uses manual support and a fixed 3-second experimental budget. No fragment was embedded alone or used to reduce the existing 2-second floor.

Two immutable enrollment prototypes were kept per arm. No prediction, calibration sample or evaluation sample updated a prototype. The existing simultaneous-speaker identity guard was not changed or exercised by this extractor-only trial.

## Calibration and held-out outcomes

A label is accepted only if its highest cosine is at least tau and its lead over the other enrolled person is at least margin. The fixed comparison is tau=0.75, margin=0.10, from source defaults, without claiming that an active service uses these settings.

The predeclared calibration grid was tau in {0.55, 0.65, 0.75} and margin in {0.05, 0.10, 0.15}. Selection minimizes wrong-person assignments, then abstentions, breaking ties toward higher thresholds. The chosen policies were fsynced after the first eight embeddings and before any evaluation embedding. No held-out score changed selection.

| Arm | Calibration-only selection | Held-out fixed policy | Held-out selected policy |
|---|---|---|---|
| Concatenated | tau 0.75, margin 0.15 | 0 correct, 0 wrong, 2 abstentions | 0 correct, 0 wrong, 2 abstentions |
| Contiguous | tau 0.55, margin 0.15 | 0 correct, 0 wrong, 2 abstentions | 1 correct, 0 wrong, 1 abstention |

Both concatenated calibration true-person scores were below the lowest grid threshold (0.55), so every grid point abstained on both calibration queries; the predeclared tie-break selected 0.75. This is important context for the selected-policy difference, not evidence that 0.75 is optimal.

All eight queries ranked the correct enrolled person highest. Absolute thresholds caused all held-out rejections. There were no wrong-person assignments among these two enrolled people; this is not a population false-accept estimate.

| Role | Speaker | Arm | True-person cosine | Other-person cosine | Margin |
|---|---|---|---:|---:|---:|
| calibration | 001-M | contiguous | 0.603719 | 0.205150 | 0.398569 |
| calibration | 006-F | contiguous | 0.512614 | 0.043884 | 0.468730 |
| calibration | 001-M | concatenated | 0.440933 | 0.027888 | 0.413045 |
| calibration | 006-F | concatenated | 0.531156 | 0.108588 | 0.422568 |
| evaluation | 001-M | contiguous | 0.443035 | 0.081257 | 0.361778 |
| evaluation | 006-F | contiguous | 0.670197 | 0.056797 | 0.613400 |
| evaluation | 001-M | concatenated | 0.365925 | 0.113256 | 0.252669 |
| evaluation | 006-F | concatenated | 0.606601 | 0.146137 | 0.460464 |

On the two evaluation queries, contiguous supports increased true-person cosine by approximately 0.077110 and 0.063597, while reducing the corresponding impostor cosine. Calibration did not improve uniformly: the second speaker’s contiguous true-person score was slightly lower. Only two people and one query per person per split are present. The result supports a larger independent experiment, not a deployment threshold change.

## Interpretation and remaining data needs

The fixed 0.75 policy abstained on every held-out query here. Abstention is a fragmentation-pressure proxy, not measured transcript fragmentation. The calibration-selected contiguous result is descriptive and statistically fragile. There is no unknown-person trial because the other speakers in the available prefix have less than two seconds of eligible support.

A useful next dataset must supply independently recorded sessions or days for the same people, additional people absent from enrollment, separate calibration and evaluation partitions, and trusted identity/overlap labels. Same-meeting time separation cannot replace cross-day evidence. New thresholds should be evaluated for wrong-person merges, rejection and coverage before any product adoption.

## Reproducibility pins

- Frozen plan SHA-256: c9dbf7f37eb7600cece3d27611438282c563e1c63e913a2680e8c2e24cb94117
- Runner SHA-256: be37b790b865c041af7a14ea44e6c54ebcae9413260478d0d13eae6dd5d136a1
- Synthetic checks SHA-256: 45150adc7e8af032b757047b2e3d541f665c29ea1e95f2227adec39ba2ff5da1
- ERes2Net model SHA-256: 1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b
- Result SHA-256: 4f8a009144a6ef07fe3d786daca5ce6cf8ca35bcaf960ed5b171b3d73655d6f5
- Frozen calibration record SHA-256: e80d29b0b932b5c0c81919ff835b6d7977878fdda570fc899771b8786c542dc3

Methods context: [ERes2Net](https://arxiv.org/abs/2305.12838), [diart](https://github.com/juanmc2005/diart), and [sherpa-onnx speaker identification](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/speaker-identification.py). Three-second training crops and example thresholds are not validated universal inference cutoffs. This pilot does not implement diart’s frame-weighted embedding pooling.
