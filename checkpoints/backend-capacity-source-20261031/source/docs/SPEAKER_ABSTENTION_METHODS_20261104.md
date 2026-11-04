# Conservative cross-session speaker identity: research brief

Research date: 2026-11-04 UTC. Literature and existing-artifact review only. No model execution, model acquisition, audio upload, threshold sweep, product change, or enrollment occurred.

## Recommendation

Prioritize **clean multi-observation evidence and a real cross-recording open-set test**, not a lower threshold or another ERes2NetV2 promotion. Treat speaker naming as a conservative evidence decision separate from within-recording diarization. Keep unknowns, competing people, overlap and unsupported observations visible.

Three fixed experiments are proposed below. They have not been executed and are not claims of accuracy.

## What the existing evidence actually says

The installed encoder is **3D-Speaker ERes2Net base zh-cn**, 16 kHz, 512 dimensions, SHA256 `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b`. It is not ECAPA or WavLM. This is bound by the existing metadata/provenance audit, not inferred merely from “Sherpa.”

The recovered AMI prefix supplied one person in the research-reference gallery and three natural two-second queries. The separate read-only diagnostic verified that the one known-person query and its reference are annotation-single-speaker, while one other query contains annotated overlap despite no native overlap witness. The inputs are unchanged source PCM, not separation output. Thus overlap rejection deserves study, but overlap or separation failure does **not** explain the known-person miss. Quiet RMS is not an SNR measurement. This single-recording result does not test cross-session identity.

The subsequently authorized enrollment-only audit found that all five candidate enrollment crops belong to one annotated person. Four eligible enrollment vectors have six dependent pairwise cosine scores ranging .34576–.63649, median .51338. This establishes variability in the retained same-person representation, not its cause. No query was rescored and no better reference was selected. These exposed values must not set new thresholds or quality cutoffs.

Avoid repeating two already investigated ideas as if new:

- **Unconditionally lengthen the selected support:** the prior dev60 comparison extended 13 two-second inputs to native-predicted clean components of 3.429–10.119 seconds. Common-subset same-speaker scores rose, but reference-scorable coverage fell; one support had annotated overlap. Duration and speech content changed together. This did not establish better safe identity linkage.
- **Swap in ERes2NetV2 at the old operating point:** a two-meeting screen was followed by an independently reviewed three-meeting **NO_GO** confirmation. Both encoders had identical final metrics, and both temporarily linked a reference-unscorable observation. This neither promotes V2 nor proves it universally inferior. The frozen cross-model thresholds were a compatibility test, not cross-model calibration.

Local sources: `docs/EMBEDDING_SPACE_PROVENANCE.md`, `audio_backend/embedding_space.py`, `model-manifest.json`, `docs/DEV60_NATIVE_SUPPORT_EXTENSION_MEASUREMENT.md`, `docs/DEV60_ERES2NETV2_CONFIRMATION_REPORT.md`, and `docs/REVIEWED_PRIMARY_PERSONS.md` in the existing source checkout. The reviewed-person API records an exact observation claim; it does not already teach acoustic matching in future recordings.

## Method evidence and transfer limits

### 1. Reject or downweight overlap before embedding

Coria et al. explicitly reduce overlapping-frame contributions to embedding pooling and derive cannot-link constraints from local segmentation. Their online system evaluates AMI, DIHARD and VoxConverse, with configurable 0.5–5-second latency. This is substantially closer to conversational audio than a clean utterance verifier. It still establishes recording-local diarization, not durable identity across sessions. [Paper](https://arxiv.org/abs/2109.06483), [official Diart implementation](https://github.com/juanmc2005/diart).

Practical hazard: pyannote’s current source uses clean masks only when enough clean frames remain; otherwise it uses the original mask. Therefore `embedding_exclude_overlap` alone is not a strict purity guarantee. A low-false-merge identity experiment should explicitly abstain instead of using that fallback. [Source](https://github.com/pyannote/pyannote-audio/blob/main/src/pyannote/audio/pipelines/speaker_diarization.py).

The newer Community-1 pipeline reports DER 17.0% on AMI IHM, 19.9% on AMI SDM and 20.3% on AliMeeting channel 1, with no collar and overlap included. These are useful segmentation/diarization baselines, **not identity false-accept rates**. “Exclusive” diarization reconciles timestamps; it does not physically remove a second voice from PCM. The pipeline supports offline operation, defaults to CPU and supports CUDA. Its weights are CC-BY-4.0, with access conditions and contact-sharing; pyannote.audio code is MIT. No gated access was accepted. [Model card](https://huggingface.co/pyannote/speaker-diarization-community-1), [code license](https://github.com/pyannote/pyannote-audio/blob/main/LICENSE).

Tradeoff: stricter speech/overlap admission reduces evidence and delays recognition. A mistaken segmentation veto can fragment one person. Hard identity constraints should not silently treat model segmentation as truth. Diart is MIT, but its default pretrained models have their own access/license requirements.

### 2. Aggregate independent clean observations, not the best-looking score

A 2025 EUSIPCO study compares mean, median, elementwise-max and attention-based enrollment aggregation with five enrollment utterances, including two-second VoxCeleb segments and near/far-field Hi-Mia. On its VoxCeleb table, mean aggregation has EER 1.71% versus 1.61% for attention-plus-average, while mean has better minDCF at prior .001 (.39 versus .40). The gain depends on the condition and metric; complex attention is not an automatic win. Those elementwise-max results should not be confused with max-over-reference similarity. [Primary paper](https://eurasip.org/Proceedings/Eusipco/Eusipco2025/pdfs/0000026.pdf).

Zeng et al. provide a more elaborate frame- and utterance-attention approach to multiple enrollment utterances. It motivates multi-enrollment modeling, but adds training/data needs and is not evidence that arbitrary crops may be pooled safely. [Paper](https://arxiv.org/abs/2209.00485).

Start with quality gates followed by an equal-weight normalized centroid, preserving individual evidence and an explicit disagreement/rejection condition. This is an engineering baseline to test, not a literature-proven safety guarantee. Do not learn quality weights from three outcomes. Multiple observations from the same waveform or overlapping crops are not independent corroboration. Do not maximize over an ever-growing reference bank: extra chances to match can increase false acceptance.

One 512-D float32 vector is 2,048 bytes before metadata. Three references triple embedding work for those reference crops, but raw vector storage remains modest. The real costs are retaining valid provenance, obtaining correct labels, segmentation, and preventing bank contamination. A bounded one-time review of a small evidence bundle may reduce recurring intervention; automatic self-enrollment is not yet justified.

### 3. Compare one genuinely different encoder only after evidence quality is controlled

ECAPA-TDNN is a reproducible, widely deployed alternative. SpeechBrain’s VoxCeleb checkpoint is Apache-2.0, uses 16-kHz speech and has CPU/CUDA paths. Its authors explicitly do not guarantee performance on other datasets. Use it as a different-family comparator, not a promised upgrade or overlap separator. [Model and license](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb), [architecture paper](https://www.isca-archive.org/interspeech_2020/desplanques20_interspeech.pdf).

WavLM is a plausible later robustness comparator because its pretraining includes masked denoising and noisy/overlapped speech simulation. The paper has approximately 94.7M Base/Base+ and 316.6M Large parameters, before a downstream head. FP32 parameter storage alone is about 0.38/1.27 GB respectively, not total runtime memory. It needs a speaker-trained head/checkpoint, not arbitrary pooled foundation-model features. Larger models add CPU latency and memory qualification. [Paper](https://arxiv.org/html/2110.13900v5).

Microsoft’s Base-Plus-SV checkpoint uses an X-vector head and documents a dataset-dependent decision threshold; it is not the paper’s best WavLM-Large-plus-ECAPA configuration. Its model card links its license rather than declaring an interchangeable universal model license. UniLM code is MIT; verify the selected checkpoint’s linked terms and complete inference stack before acquisition. [Checkpoint card](https://huggingface.co/microsoft/wavlm-base-plus-sv), [UniLM code license](https://github.com/microsoft/unilm/blob/master/LICENSE).

The ERes2NetV2 paper reports 0.61% full-duration versus 1.48% two-second VoxCeleb1-O EER. This supports the general short-duration difficulty, but its recipe/results cannot override the project’s negative fixed confirmation or transfer directly to the installed Chinese model on mixed-headset English conversation. [Paper](https://www.isca-archive.org/interspeech_2024/chen24l_interspeech.html).

## Frontend prerequisite

The [read-only diagnostic](SPEAKER_ABSTENTION_DIAGNOSIS_20261104.md#source-level-frontend-discrepancy) identifies concrete `snip_edges` and upper-frequency contract differences. Verify their technical effect in one bounded, separately versioned compatibility study before an encoder change. This is not yet a diagnosed cause or license to mix embedding spaces.

## Three proposed experiments, in order

### A. Does stricter speech support improve evidence quality without destroying coverage?

Keep the current encoder, matcher and fixed decision opportunities. Compare the existing selector against one frozen VAD-plus-overlap admission rule. Bound observation duration, total observed audio, maximum wait and number of opportunities before any outcome is seen. Do not simply extend every crop to the full predicted component. Reject insufficient clean speech rather than falling back to overlap.

Primary outcomes: admitted annotation-single-speaker speech per recording hour; overlap contamination among admitted crops; rejected/empty opportunities; known-person decision coverage. Secondary: same/different-speaker score distributions on a clearly reported common cohort. Keep unscorable cases in the full denominator. A source-only feature-contract audit should first resolve or precisely document the previously noted native-versus-official frontend difference; it is an unresolved confound, not a diagnosed bug.

This is a support-policy study, not proof of identity improvement. No algorithmic crop may be chosen by reference labels.

### B. Does a small reviewed multi-reference gallery improve cross-session naming at fixed false-accept risk?

Use one frozen query extractor and the current encoder. Compare one reference per enrolled person with a fixed three-reference bundle of disjoint, quality-passing speech. Preserve every reference and source/session identity. Test a normalized mean aggregate with a preregistered consistency veto. If enough clean references do not exist, report insufficient enrollment rather than silently finding more favorable audio.

Enroll from one recording; query later recordings of metadata-verified same participants plus people absent from the bank. Include several enrolled competitors, not a one-person gallery where a margin sentinel cannot measure rivalry. Freeze bank size, enrollment budget and all unknown speakers before evaluation. No match may enlarge the gallery.

Measure wrong-person assignments and unknown-person false accepts separately, known-person misses, unscorable assignments, useful named coverage, time until a stable decision, and review seconds per person. This directly tests the user's low-intervention objective instead of only pairwise EER.

### C. Conditional encoder comparison, not another threshold rescue

If A/B leave substantial known-person misses with adequate pure support, compare the current encoder against **one** pinned SpeechBrain ECAPA checkpoint on identical crops and galleries. Do not run a model tournament. Keep dimensions and model-specific evidence spaces isolated; old and new vectors are never compared or silently migrated.

Use an independent calibration partition to set each system’s operating point for the same declared false-accept cost, then freeze it before held-out testing. Include the unchanged product baseline separately. A common raw cosine threshold is not equal risk across encoders. WavLM remains a later, heavier alternative if resource and checkpoint terms are qualified, not an additional arm by default.

## Independent evaluation and persistence safeguards

- Exclude the exposed AMI prefix and prior dev60 screen/confirmation material from outcome-designed confirmation. Split by actual participant/team and session before crop construction, not random pairs from the same recording.
- AMI scenario a/b/c/d recordings provide a practical cross-recording starting point, but they occur within one day; they cannot establish robustness across weeks, illness or new devices. Resolve participants using metadata rather than suffixes or local speaker labels. For natural meetings, the official docs explicitly say the same series need not have identical participants. [Meeting IDs](https://groups.inf.ed.ac.uk/ami/corpus/meetingids.shtml), [scenario description](https://groups.inf.ed.ac.uk/ami/corpus/scenariomeetings.shtml).
- Freeze a corpus manifest, calibration/test boundary, participant mapping, primary metrics, tolerable risk, minimum information floor and compute budget before running. Do not replace failed/missing examples. Use all planned decision opportunities in coverage denominators.
- Cosine scores are not posterior probabilities. Quality-aware calibration has precedent, but needs separate development data and representative duration/channel conditions. [Calibration overview](https://www.isca-archive.org/odyssey_2020/ferrer20_odyssey.html), [quality-aware calibration paper](https://arxiv.org/abs/2010.11255).
- Report uncertainty at the person/session level. Zero errors among correlated crop pairs do not establish a low operational rate. For illustration only, zero errors in 3,000 independent trials gives a one-sided 95% upper bound near 0.1%; the project's single withheld query supplies nothing close to that evidence.
- Replay chronological prefixes and session restarts, scoring every intermediate wrong/unscorable assignment, retraction and ID switch, not merely the final partition. A later correction does not erase an earlier false merge.
- Keep immutable reviewed identities separate from provisional local clusters. A reviewed UUID may persist across sessions; automatically inferred links remain reversible, evidence-bound and unable to update the trusted gallery. Reject unresolved competition, mismatched model spaces, overlap/coverage uncertainty and contradictory evidence.
- Changing an embedding model requires versioned bank migration or re-enrollment from authorized retained evidence. Storage persistence is not recognition validity, and diarization DER is not cross-session identification safety.

## Completion and limits

Primary literature, official model cards and repositories were checked on 2026-11-04. The model candidates were not downloaded, executed, licensed through a gated flow, or uploaded anywhere.

