# Independent Chinese cross-day calibration pilot: candidate only

## Decision

Do not change production. A C-only threshold candidate of **0.46** (fixed margin 0.10) improved held-out primary known identification from 0/4 to 3/4, but the two additional days yielded only **1/8**, for **4/12 correct and 8/12 refused overall**. This is not stable, low-intervention identity continuity. No production identities were merged or enrolled. Product dev69 and its 0.75 default remain unchanged.

## Scope and provenance

The public SMIIP-TV v2.0 corpus is Mandarin mobile prompted/free speech, not a meeting corpus. Official source: https://www.openslr.org/156/ (CC BY-SA 3.0). Public archive: https://openslr.trmal.net/resources/156/SMIIP-TV_v2.0.tar.gz. Author naming evidence identifies recording days in 2020. The retained C and T sets have disjoint corpus speaker IDs and dates; source and crop hashes are distinct. This does not prove global physical-person, model-training, device, household, prompt-content, or meeting-domain independence. Speaker IDs/roles were visible in preparation, so this was not a human-label-blinded experiment.

The originally planned 32-person acquisition failed at the fixed wall limit: exit 2, 72/108 settled slots, 36 unresolved. It remains a failed original plan, not a completed 32-person test. One bounded acquisition read 4,816,814,080 bytes, plus the earlier 4 MiB probe. No retry or cap extension occurred. The full 49,951,256,989-byte archive checksum was not verified; selected WAVs and their derived crops were hashed.

A separate resource-only fallback was frozen and backed up before acquisition termination and before any scores. It retained only complete people, preserved C/T roles and original ordering, then symmetrically truncated to the smaller counts: each split 4 known and 5 unknown people. Fourteen original people were excluded: 11 incomplete, 3 for symmetric truncation. No listening, score, text, purity, or quality-based filtering was used.

Each known person has one earlier-day reference and three later-day queries; unknown people have three queries. C uses May 1/8/15/22 and T June 1/8/15/22. Each input is the fixed central two seconds of channel 0 from a 16 kHz PCM16 stereo source, with no resampling, gain, padding or VAD. This does not establish that the chosen two seconds contain clean single-speaker speech. Total: 18 people, 62 distinct clips, 70 encoder calls including eight exact repeats.

## Frozen execution and selection

Exact model SHA256: 1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b (512D ERes2Net). Native Sherpa/KNF frontend remained snip_edges=False, high_freq=-400, 80 bins, 16 kHz; producer float32 normalization and existing float64 matcher were unchanged. No experimental frontend substitution was used.

The fixed C grid was 0.00–1.00 by 0.01. Only one primary query per participant selected the threshold: require zero observed wrong-known emissions and unknown accepts, maximize correct-known emissions, break ties toward the higher threshold. Extra days were descriptive only. C selected 0.46 once. Its lock was created before T was released. T ran once, evaluated only 0.46 and baseline 0.75, and predictions were sealed before the truth-summary step. Evidence establishes the audited code path and artifact/time chain, not an externally immutable or label-blind trial.

Both stages completed 35/35 calls, exit 0 with positive waits, no kill, no surviving owned process group. Total model-stage wall time was 10.678 seconds and aggregate CPU 15.691 seconds. Acquisition took approximately 30 minutes. All eight repeated raw and normalized vectors were byte-identical. Independent audit rehashed 2,151 unique pins and recomputed the grid, saved-vector decisions and intervals without calling the scorer or encoder. Original 1,046 product source files were rehashed unchanged.

## Results

Counts are identification decisions, not performed identity merges. There were no missing/unavailable query outputs.

| Split / scope | Candidate correct known | Candidate refused known | Wrong known | Unknown accepted | Baseline correct known |
|---|---:|---:|---:|---:|---:|
| C primary | 2/4 | 2/4 | 0/4 | 0/5 | 0/4 |
| C extra days | 5/8 | 3/8 | 0/8 | 0/10 | 0/8 |
| C all three | 7/12 | 5/12 | 0/12 | 0/15 | 0/12 |
| T primary | 3/4 | 1/4 | 0/4 | 0/5 | 0/4 |
| T extra days | 1/8 | 7/8 | 0/8 | 0/10 | 0/8 |
| T all three | 4/12 | 8/12 | 0/12 | 0/15 | 0/12 |

Baseline 0.75 refused every known and unknown query in both splits. Candidate T known refusals were below threshold; one of the eight extra-day queries also ranked a wrong person first but was refused under the fixed rule. Lowering further on this T set would be post-test tuning and was not done. These observations do not isolate whether short content, contamination, reference inadequacy, channel/session differences, or model limitations caused the missed recognition.

## Error uncertainty

Formal population/safety confidence intervals are unavailable because the source sampling does not establish random iid participants. For transparency only, conditional-iid Clopper–Pearson 95% reference intervals on the single prespecified T primary per person are:

- Correct known 3/4: two-sided [19.41%, 99.37%].
- Refused known 1/4: two-sided [0.63%, 80.59%].
- Wrong known 0/4: two-sided [0%, 60.24%]; one-sided upper 52.71%.
- Unknown accepted 0/5: two-sided [0%, 52.18%]; one-sided upper 45.07%.

These are hypothetical model reference bounds, not valid population coverage or promotion criteria. Repeated queries and gallery pairs do not increase the primary participant denominator. C-selected performance is not independent qualification. Do not pool C/T for a safety claim or advertise 100% precision from three primary emissions. The sample cannot support an extremely low false-identification/merge rate.

## Preserved negative results and next boundary

The earlier fixed-version frontend comparison remains negative: both frontends ranked 18/18 known queries first but emitted none at 0.75, and the alternative raised unknown similarity too. This pilot neither overwrites that result nor validates a production threshold. Existing 0.75/0.10 was documented as an uncalibrated tiny-sample heuristic; original selection data and a model-bound training distribution were not recovered.

Next useful research would require a newly frozen cohort/protocol with better evidenced clean speech and references, meeting-like domain coverage, and sufficient independent people. It must not retune on this now-seen T set. No new model run or download is part of this completed pilot.

Unattended product work remains separate: automatic safe intake/drain/switch orchestration, recovery from uncertain writes, inference failure handling, storage and old-attempt retention, unified status, and reliable cross-boundary ASR context/speaker identity. Same-host experiments do not qualify another server, Android device, long-duration live recording, overlapping speech, or meeting accuracy.

## Artifact custody

Raw WAVs, crops, embedding vectors, model weights, transcripts and demographic tables are not included in the Git results backup. The repository includes reproducible research code, frozen selection rules/source IDs, aggregate outcomes, source/artifact hashes and this report. Exact local raw reports remain retained unchanged. The prospective input freeze was remotely backed up at 8a3da499c143cac4daf673dbaa0d08226a473b9a before C/T inference.
