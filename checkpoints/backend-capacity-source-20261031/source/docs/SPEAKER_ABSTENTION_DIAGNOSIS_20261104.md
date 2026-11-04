# Read-only diagnosis of the negative speaker carryover result

The original exploratory result remains **INSUFFICIENT_EVIDENCE**. All three natural queries abstained; one was a known-person miss, one was correctly left unknown, and one was reference-unscorable because of overlap. No accepted identity, manual enrollment or identity merge was produced.

A separately authorized unblinded diagnosis found:

- All five reference-half observations represented the same annotation-supported person. The frozen earliest-per-person rule retained one; one original packet-overlap veto remained, and three later observations of that same person were unused. No second gallery person's reference was lost through that veto.
- The known-person similarity was about 0.421 under the unchanged 0.75 threshold. All displayed margins were a one-person-gallery sentinel, not empirical separation from a runner-up.
- All four eligible same-person enrollment observations had descriptive pairwise cosine similarities from about 0.346 to 0.636. These six pairs share four observations and are not independent trials. This shows within-condition short-crop inconsistency, not its cause.
- One query contained almost one second of annotated overlap despite a clear native packet gate. The selected reference and missed known-person query were fully single-speaker according to segment annotations. That contamination therefore does not explain the known-person miss.
- Every crop was exactly two seconds and matched the original PCM. No digital-rail clipping was observed. Signal level statistics do not establish SNR, intelligibility or acoustic purity, and no listening or new model inference was performed.

The evidence prioritizes limited, unstable reference support and imperfect overlap screening. It cannot causally distinguish short duration/content, acoustics, frontend behavior and encoder capability. No source-separation intervention was tested. The retained negative longer-support and alternate-model research also rules out presenting either as an already-proven fix.

The proposed next validation should audit frontend compatibility, then test bounded automatic evidence accumulation on a newly fixed population, preserving unknown states, all competitors and early unsafe events. Manual intervention, false-merge risk and useful coverage must be measured separately. These three exposed queries must not be used to tune thresholds or choose a favorable reference.

This diagnosis changes no product default, source data, frozen prediction or original result. It creates no production-readiness or rare-error-rate claim.

See [primary-source methods and constraints](SPEAKER_ABSTENTION_METHODS_20261104.md) and [prospective validation design](SPEAKER_CARRYOVER_VALIDATION_DESIGN_20261104.md). The preserved operational and negative result is in [the retest report](VOICE_RUNTIME_OPERATIONAL_RETEST_20261104.md).

## Source-level frontend discrepancy

The existing native contract uses `snip_edges=False` and `high_freq=-400`
(7,600 Hz at 16 kHz). The pinned official test harness explicitly sets
`snip_edges=True` and leaves upper frequency unset. With the audited
kaldi-native-fbank 1.22.3 default, that means 8,000 Hz; the historical test
harness environment is not itself attested. Both paths use 80 bins, zero
dither, normalized waveform input and global-mean subtraction.

Sources: [official test harness](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/test-onnx.py#L41-L89),
[native extractor](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/speaker-embedding-extractor-general-impl.h),
[native feature defaults](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/sherpa-onnx/csrc/features.h),
and [versioned FBANK default](https://github.com/csukuangfj/kaldi-native-fbank/blob/v1.22.3/kaldi-native-fbank/csrc/mel-computations.h).
The existing local provenance document and dev60 support-extension report record
this unresolved difference.

This is a concrete source-level feature-contract mismatch, not a retained paired
numerical experiment or proof of a bug. Neither training-preprocessing parity
nor an effect on the current miss is established. A bounded technical parity
study should precede an encoder swap. Any changed frontend creates a distinct
versioned embedding space; it must not silently reuse existing vectors, relax
artifact admission or retune the current three-query outcome.
