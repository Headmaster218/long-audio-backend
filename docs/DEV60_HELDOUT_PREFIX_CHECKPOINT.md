# Dev60 held out prefix preregistration and preparation checkpoint

As of 19 October 2026, the fixed four-meeting prefix preparation is complete and independently accepted. Four unchanged-baseline runs are conditionally authorized after helper, static review, publication and fresh-admission gates; none has launched at this checkpoint. There are no held-out pilot inference results, fitted calibration, interpreted pilot references or confirmation outcomes.

This metadata-only checkpoint is based on accepted design and preparation evidence. The accompanying `DEV60_HELDOUT_PREFIX_PROVENANCE.json` records exact evidence hashes and all four source-file, source-PCM, prefix-file, prefix-PCM and opaque-reference byte counts and SHA256 pins. No corpus payload is included.

## Deterministic selection and limits

Exclude previously exposed `R8001_M8004`. With the single seed `dev60-heldout-identity-v1-20261019`, sort remaining IDs ascending by SHA256 of ASCII(seed + newline + meeting_id), then by meeting ID. No outcomes informed selection.

| Rank | Meeting | Frozen role |
| --- | --- | --- |
| 1 | R8007_M8011 | Calibration reserved |
| 2 | R8008_M8013 | Calibration reserved |
| 3 | R8007_M8010 | Development |
| 4 | R8009_M8019 | Development |
| 5 | R8003_M8001 | Sealed confirmation |
| 6 | R8009_M8020 | Sealed confirmation |
| 7 | R8009_M8018 | Sealed confirmation |

Calibration reserved is a data role, not a fitted calibrator. Near-field filename speaker tokens are disjoint across all eight meetings, but physical-person uniqueness across Eval sessions is not independently proven. Room/acoustic separation is not established, and pretrained-model exposure is unresolved. The split supports no stronger unseen-person or unseen-room claim.

## Accepted geometry and derivation

Each pilot source is eight-channel, 16 kHz, 16-bit PCM in the accepted extensible RIFF layout (40-byte fmt, four-byte fact, channel mask 1599, 80-byte data offset). RIFF, data and fact frame counts reconcile. Each derived file retains physical channel 1/index 0 at the original half-open interval [0,360) seconds, frames [0,5,760,000), with no resampling, downmix, padding, concatenation or window shift. The canonical mono prefix has 11,520,000 PCM bytes plus a 44-byte header, totaling 11,520,044 bytes.

| Meeting | Full source frames | Full source seconds | Full source bytes |
| --- | ---: | ---: | ---: |
| R8007_M8011 | 29,784,736 | 1861.546 | 476,555,856 |
| R8008_M8013 | 35,831,528 | 2239.4705 | 573,304,528 |
| R8007_M8010 | 29,701,151 | 1856.3219375 | 475,218,496 |
| R8009_M8019 | 31,582,816 | 1973.926 | 505,325,136 |

These full durations exceed the corpus-level descriptive 15–30-minute range. The approved contract required compatible geometry and at least 360 seconds, without an upper-duration exclusion. All four meet that contract; this discrepancy does not invalidate the preparation or justify reselection.

## Provenance and accepted preparation

Credit: AliMeeting, provided by Alibaba Group; Fan Yu et al., [M2MeT: The ICASSP 2022 Multi-Channel Multi-Party Meeting Transcription Challenge](https://arxiv.org/abs/2110.07393) (ICASSP 2022), as credited by [OpenSLR 119](https://www.openslr.org/119/). See also the [AliMeeting project](https://github.com/yufan-aslp/AliMeeting). The source is AliMeeting Eval, licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The exact channel extraction and fixed-prefix cropping above are the derivations. The repository code license does not replace the dataset license.

The [official Eval archive](https://speech-lab-share-data.oss-cn-shanghai.aliyuncs.com/AliMeeting/openlr/Eval_Ali.tar.gz) is 3,673,718,355 bytes, SHA256 `dc47343b2474b5ebcf458927e878155f6ddeb59c85e685b3645c32a1f9578d92`. This acquired-byte hash is not an official historical digest or independent authenticity proof. Its hash matched before, during and after preparation. One gzip/tar traversal checked all 73 ordered inventory entries, emitted 5,385,922,560 bytes, and completed tar, gzip CRC, single-stream and physical-EOF checks; the before/after compressed-file hash reads were additional reads, not additional decompression passes.

Eight synthetic tests passed once, covering PCM layouts and exact extraction/full hashes, malformed/truncated geometry, resource bounds, inventory/tar and gzip integrity, opaque references, confirmation exclusion and fixed selection. The one preparation attempt completed without retry. Four prefixes total 46,080,176 bytes; four opaque full-reference files total 392,897 bytes. The existing archive was reused, with no new archive or full raw-source copies. Confirmation member bytes were necessarily traversed but were not parsed or retained; no confirmation audio inference, embeddings, scores or reference interpretation occurred.

The accepted helper recorded and disk-verified payload hashes. The independent terminal audit and this checkpoint used metadata rather than reopening payloads. Acceptance covers preparation, not acoustic accuracy or identity performance.

## Resource measurements and scopes

Preparation reported 36.458882437 seconds elapsed, 36.4 sampled aggregate CPU seconds and 19,398,656 bytes peak sampled aggregate RSS. Synthetic tests reported 0.031571641 seconds test elapsed and 0.128632 seconds import-inclusive process CPU. Summed test/preparation stage elapsed was 36.490454078 seconds; summed sampled CPU was 36.52 seconds, with 22,581,248 bytes maximum sampled aggregate RSS across stages.

Stage elapsed excludes interpreter startup and final receipt writing. Import-inclusive process CPU is a different scope from sampled aggregate CPU; neither sampled CPU nor RSS is continuous kernel aggregate accounting. Wait-call durations are not total runtime. Retained limits were 256 MiB per-process address space and sampled aggregate RSS, 64 KiB streaming reads, 128 MiB fresh namespace, 768 MiB selected-member cap and 8 GiB emission cap, with 8 GiB entry free space and a preserved 6 GiB free-space floor. Budgets were 900 work seconds/600 CPU seconds across tests and preparation and a separate 60-second preparation cleanup allowance. The member cap is not a source-sized allocation.

## Finite baseline and untouched confirmation

The frozen proposal is SHA256 `d7bb36c027dad7cb1f487af52f948c6abe895ba01dc9ea3af7d264c8bdc2628f`. Its four serial 360-second runs use fresh recording-local scopes and unchanged dev60 product commit `6326733db5984d95546f52bd0de5653504af2ee0`, tree `c37ead48b684ba935e1a7b3ce9950ad3a467e513`. The .55 floor, .10 rival margin, reciprocal complete-link decisions, hard rivals, abstentions, constraints and natural crop selection remain unchanged; identity/profile authority stays disabled.

Each six-minute scope permits at most ten admissions: six bootstrap admissions plus four later minute buckets. This is at most 45 within-meeting unordered pairs, or 40 admissions/180 pairs over four meetings. These are capacity ceilings, not expected successful observations or vectors; shared-observation pairs are statistically dependent. Sparse evidence is inconclusive. Failed and abstaining scopes remain in the planned denominator of four, with no outcome-driven replacement, extension or automatic retry.

The proposal schedules roughly 8–15 minutes per run/32–60 minutes for four, with unchanged 3,420-second absolute per-run limits (13,680 seconds summed), sampled 6 GiB charged-memory ceiling and 512 MiB output ceiling per run. These are planning allowances and finite limits, not held-out timing observations or promised completion bounds. Fresh admission and reviewed helper bindings remain gates. Predictions must be sealed before their references are scored. No calibration, threshold search or alternative model is included. Confirmation ranks 5–7 remain unused until candidate rules and metrics are frozen and separately authorized.

## Evidence binding

The JSON binds the accepted design, selection and independent design review; preparation freeze, source/test reviews and receipts; preparation result and terminal summary/review; and the finite baseline proposal by byte count and SHA256. Key accepted hashes are:

- Design: `ccaca94ea77b40a7cf5afc1c385a2796bb7c574d2df31b34226e7c222215d6cf`
- Selection: `2b8ca8a0f2c12d0f24b4c4ba753dd9784bb23a88830d77ced45f13064c81728a`
- Design review: `77fd5ae997a6749d463402081999b18d89ce44af0ac8a9a7b3e7fb93d7c8dfc9`
- Preparation result: `e7580dd7df209faa45af8ec8128d7cb9f3530b6ec40962335f99e4b3ddc04b90`
- Terminal summary: `d4d11df8353e29d7d83972f9254f5fb7278fc167b64f4a8c05135f41ac695156`
- Independent terminal review: `2ea22a828736fa0d2c316f417b19de1f881dad525b59e9fb627602194deabccc`

The source-report ancestor is commit `ab3dfd09d3534f5a05dce205c9b9ad2bcd3effc1`, tree `7d177f85748d5d7f4fc199edd1d95aa9d91b198f` (606 files, 10,933,700 bytes). It is distinct from the unchanged inference product identified above.
