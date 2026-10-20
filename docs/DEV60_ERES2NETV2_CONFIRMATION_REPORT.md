# ERes2NetV2 three-meeting confirmation: NO_GO

20 October 2026 UTC. Recording-local research only. The frozen outcome is **NO_GO**. Independent final terminal and resource review accepted the completed invocation with no audit blockers. Product code, model defaults and persistent-profile behavior remain unchanged.

## Outcome

The one fixed confirmation invocation completed **54 encodes**, 27 per arm, on all natural observations from the three preregistered meetings. With the frozen native frontend, D2 crops, comparator and unchanged 0.55 similarity/0.10 rival-margin operating point, the fresh current 512-D and ERes2NetV2 192-D arms have identical final aggregate metrics. No meeting has the required combination of strictly higher V2 pure coverage and strictly lower fragmentation.

Both arms also form one reference-unscorable link at rank-7 prefixes k=5 and k=6. This fails the absolute V2 safety gate, which required zero unscorable links at every prefix, even when the current arm does the same. The link is retracted at k=7 and remains absent through k=10. Its later absence does not undo the earlier failure.

All three information-adequacy floors pass. This is a negative result under the frozen safety and benefit rules, not an execution failure or an insufficient-information result. No production promotion, outcome-driven tuning, crop reselection or confirmation retest follows from it. Product commit `6326733db5984d95546f52bd0de5653504af2ee0` remains unchanged.

## Fixed population and final metrics

The [preregistration](DEV60_ERES2NETV2_CONFIRMATION_PREREGISTRATION.md), published at commit `81108ba96c6e5cc9069f0463931eefabc904d238`, fixes ranks 5–7 of the original seeded split. Each collection covers original physical channel 1/index 0, time [0,360) seconds, at 16 kHz PCM16. All three complete 360-second baselines were independently accepted before research-input binding. No additional meeting, longer interval, replacement crop, resampling, downmix or padding was used.

| Rank and meeting | Natural observations / stored controls / raw witnesses | Reference-scorable / all physical classes | Correct final links, each arm | Pure coverage, each arm | Pure covered / all, each arm | Fragmentation, each arm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5, R8003_M8001 | 7 / 6 / 8 | 7/7 | 1 | 2/7 | 2/7 | 2 |
| 6, R8009_M8020 | 10 / 10 / 1 | 8/10 | 1 | 2/8 | 2/10 | 5 |
| 7, R8009_M8018 | 10 / 10 / 7 | 8/10 | 0 | 0/8 | 0/10 | 6 |

The within-meeting populations total 27 distinct physical classes, all with fresh vectors in both arms. Pure coverage counts classes in nontrivial clusters whose complete membership is reference-scorable and agrees on one reference speaker; its primary denominator is the scorable population. Fragmentation sums occupied clusters minus one over represented reference speakers, including singletons. The final populations represent 4, 2 and 2 reference speakers respectively; these are recording-local counts, not established cross-meeting identities.

Final precision is 1/1, 1/1 and null respectively in both arms. A zero-link result has no precision denominator. All final false-link, unscorable-link, mixed-cluster, unscorable-membership-cluster and constraint-violation counts are zero. The planned denominator remains three meetings; the final state is counted once.

The dependency-screened, vector-complete independent-class counts are 7, 10 and 10. Scorable same-speaker/different-speaker pair opportunities are 4/17, 16/12 and 12/16. Each meeting therefore meets the frozen floor of at least three mutually source-independent classes and at least one opportunity of each kind. Dependency screening does not establish statistical independence.

## Prefix result and reference uncertainty

Each arm retains 8, 11 and 11 states, including k=0: **60 arm-prefix states** total. Every saved prefix metric matches between arms. Across them there are zero observed false links, mixed-reference clusters or constraint violations. Correct-link preservation against fresh current passes at corresponding prefixes. That relative result does not override the absolute unscorable-link failure.

The rank-7 link first appears at k=5, remains at k=6 and is retracted at k=7 in both arms. One endpoint has 5,760 of its 32,000 crop frames, or 0.36 seconds, without labeled support inside the declared reference domain. It has no overlap in that crop. This is an unscorable link caused by a reference-coverage gap, not an observed different-speaker false merge. A separate rank-7 crop is overlap and is not an endpoint of that link. Neither gap filling nor trimming was permitted.

Final reference statuses are: rank 5, seven single-speaker crops; rank 6, eight single-speaker and two missing-coverage crops; rank 7, eight single-speaker, one missing-coverage and one overlap crop. Unscorable observations stay in the full rival population. At k=5 and k=6, rank 7 has one unscorable link and one unscorable-membership cluster per arm. All other prefixes have zero such counts.

In both arms, rank 5 gains its final correct link at k=3 and retains it; rank 6 gains its final correct link at k=6 and retains it. The rank-7 retraction demonstrates why independently rebuilt prefixes do not establish persistent identity. These dependent prefix events are descriptive, not repeated independent trials.

## Matched measurements and historical boundary

The complete binder contains **N=27 observations and C=26 authenticated stored controls**. The current arm first reproduces all 26 historical normalized vectors byte-for-byte, then measures the one authentic missing crop as call 27. Its original worker is positively waited with exit 0 before the V2 worker starts its 27 calls. Each arm uses one constructor and 27 streams, with no warmup, retry or extra encode: 108 seconds of two-second waveform input across both arms.

The rank-5 historical missing vector remains the original predispatch `stale_generation` cancellation. Its authentic crop provenance is intact, allowing one fresh research measurement without repairing the historical product attempt or proving original product eligibility. Historical rank-5 publication remains unavailable/null, not zero links. Rank 6 had one historical published link, unscored at baseline collection and subsequently scored correct after the barrier; rank 7 had an available empty link set. Historical preservation is null, true and true respectively. Original product-authority eligibility remains null.

Both arms use the pinned native CPU frontend, two configured threads, float32 input and the existing float32 normalization. Runtime metadata records sherpa-onnx 1.13.8, ONNX Runtime 1.28.2 and NumPy 2.5.3 on Linux x86_64. The comparator is byte-identical to the preceding accepted research comparator, with the previously accepted dimension-only adaptation. It preserves binary64 arithmetic, the 0.55 similarity threshold, 0.10 rival margin, reciprocal synchronous complete-link rules, physical aliases, authenticated cannot-links, dependencies and every rival. The fixed thresholds are a compatibility test, not universal calibration across model spaces.

Original admission chronology and cumulative witness deltas define each prefix independently of control-first encode order. All three accepted baseline terminal seals and both complete all-meeting/all-prefix prediction seals precede reference interpretation. Each confirmation reference is then parsed and evaluated once with the unchanged sparse-v2 scorer; saved immutable classifications supply subsequent research scoring. Full-crop, half-open, zero-collar semantics remain unchanged.

## Execution evidence and preparation corrections

The retained original owner terminal reports exit 0, as do both positively waited native workers. Shell timing is **10.949 seconds wall and 15.836 user + 1.128 system = 16.964 CPU seconds**. The original dispatch-to-terminal-observation bracket gives a conservative whole-outer wall upper bound of 28.823 seconds. The explicit two-second wrapper allowance gives 18.964 charged CPU seconds; it is not a direct measurement of every outer CPU tick. These are wrapped-invocation timings, not isolated encoder benchmarks.

Pre-exit high-water RSS receipts are 157,052,928 bytes current, 271,192,064 bytes V2 and 32,079,872 bytes owner. Teardown peak RSS is unavailable. The 4.5 GiB serial reservation plus 1 GiB background allowance, 4 GiB native address-space limits, measured RSS refusal checks, 600-second outer interval, 300 aggregate CPU seconds and 8 MiB logical/16 MiB allocated output bounds remain distinct. Final whole-output accounting, including wrapper logs, is 136 files, **2,053,528 logical / 2,351,104 allocated bytes**. The independent audit accepts the resource envelope and permits reservation release; sampled/pre-exit measurements are not continuous aggregate-memory or teardown-peak proof.

Before execution, additive baseline binding receipts supplied the required meeting field and the binder changed only the corresponding receipt basename. Original receipts were preserved. Source review also corrected outcome-status precedence and required exact historical-publication/replay equality before native launch. These pre-execution corrections did not tune the comparator or require another confirmation native invocation. Earlier matched-screen guard/schema failures are preserved in the [preceding screen report](DEV60_ERES2NETV2_MATCHED_PREFIX_SCREEN.md).

## Exact pins and provenance

Public ancestry and data:

- Preceding positive bounded screen commit: `a25bd780ce0edfc59aeccf09ee81af35cb469344`.
- Confirmation input-pin commit: `82943d5ace706a58bd3998b8f86c6e49b11677ea`, tree `1d68f5fcdf267577675c6e1d5bbab65064df8abc`.
- [Published input metadata](DEV60_ERES2NETV2_CONFIRMATION_INPUT_PINS.json) SHA256: `9c2d69bde5746be6944ca4d48dcaf443cbb240ae4b521db1fd83941d77f34264`. It records exact archive-member, reference-byte, original-channel and prepared-prefix identities.
- AliMeeting archive SHA256: `dc47343b2474b5ebcf458927e878155f6ddeb59c85e685b3645c32a1f9578d92`.
- Frozen confirmation design SHA256: `fe37b14e4d21c1a64a600a459a894bef4a268eea34cf98f9f6e0847fa74aee2c`.
- Complete research manifest SHA256: `0056e1c7e2ee68e04f29ca5c59d529a0a618c902c7722f05aa044d87f5246f5c`.

Model and source SHA256:

- Current 512-D model, 39,593,761 bytes: `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b`.
- ERes2NetV2 192-D model, 71,441,526 bytes: `bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239`.
- Native arm: `0c80858181db3efd0a72109d776452adfc0aed7c74873e5141c20046cbf3eaa1`.
- Research comparator: `3ce6d7720fed26cd06a1e25ca71057eafe138b92cb4091ecdce5ab4cbb0af830`.
- Confirmation owner: `11b3aea257bf59a6b2642a9956961e59f2a8eb3e44e16cb9724395e56f8989ad`.
- Model-free tests: `a3ae345b70b5c455c5bbaa2fa95ca5995d6b3b80342c4af7a1a6cc54060b2e23`.
- Unchanged sparse-v2 scorer: `5e63008ede56b664d139980b3f5f26c1f9d7902fc7a593c6eb374dac092eb156`.
- Original terminal receipt: `2823ecb5f93b61846e3cd3ebded25c3625f46ab7d9b034886d7dd445e72b4e4b`.
- Independent final terminal audit SHA256: `97f7bed588597182c477b4b3a163129430e08828e92eb8bbf06eb7c5f6b4af63`.

The [acquisition qualification](DEV60_ERES2NETV2_ACQUISITION_LOAD_QUALIFICATION.md) limits still apply. The V2 hash is a locally measured byte identity, not a verified publisher checksum. Current model-specific Apache-2.0 metadata does not attest the historical checkpoint/export revision. Frontend equivalence to the upstream checkpoint remains unproven.

Dataset credit: AliMeeting, Alibaba Group; Fan Yu et al., [M2MeT](https://arxiv.org/abs/2110.07393), as credited by [OpenSLR 119](https://www.openslr.org/119/), licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Original-channel extraction and fixed-prefix/crop derivations are recorded in the linked input metadata. The repository license does not replace the dataset license.

## Interpretation boundary

Three six-minute recording prefixes and 27 dependent observations do not establish a general false-merge rate, broad recognition quality or unseen-room/model-training generalization. Equal final outcomes at this fixed operating point do not establish that V2 is universally inferior; the experiment did not calibrate cross-model score scales. Zero observed false links cannot convert reference gaps into positive safety evidence. The predefined confirmation blocks this candidate's advancement and does not qualify the current baseline as safe at every prefix. It supplies no persistent-identity, 12–48-hour stability, Windows/AMD/Vulkan or production-eligibility proof.

The confirmation set is now spent for outcome-designed revision. The report records the frozen negative result without changing product code or settings. It publishes aggregate metrics and public provenance only; no audio, raw labels or speaker identifiers, transcript text, vectors, weights, private filesystem paths or raw logs.
