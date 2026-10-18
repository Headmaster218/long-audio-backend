# VoxConverse fixed 84-second trial: dev49

This single CPU-only trial used the unchanged fixed 84-second VoxConverse prefix and model, threshold, timestamp, and crop settings with dev49 commit `9867b6430ec23bdce79d715fe3cc45aceb0f877f` (tree `530e5db291d113a68e210c440a405156c1d667fc`).

**Operational completion passed. Recording-local identity quality remains unmeasured.**

## Outcomes

- All 8 primary work files completed, producing 8 native core commits and 7 stored primary packets. The remaining packet had a capacity outcome.
- One real MOSS job committed a retained diagnostic result, with one construction/session/run attempt and verified model/process cleanup. The preserved prior trial had zero MOSS jobs. The new result does not retroactively change that failed trial.
- Zero identity batches admitted primary packets; zero identity nodes, successful probe crops/vectors, or links were retained. Thus **7 stored primary packets and 0 batch-admitted packets are different denominators**. Terminal health counters are not cumulative job counts.
- The final chunk retained an unadmitted discovery: `discovery_unadmitted/bounded_planning_capacity`, with `owned_targets_preserved=false`. It is not a second committed or expired candidate. The underlying time/VM/byte subphase was not recorded. A separate SQLITE_BUSY read diagnostic cannot be assigned as its cause.

## Timestamp and clean-crop diagnosis

The result retained four finite, in-support, unadjusted phrases. A fifth timestamp pair, **[7.17,7.81) seconds**, was rejected against the **7.78-second** support. Its 30 ms overshoot exceeds the unchanged 10 ms allowance. It remains rejected; adjusted and invalid phrases cannot supply probes.

The resulting whole-result `invalid_model_timestamps` gate blocks per-phrase and prospective primary capture. However, archived, hash-bound native proof shows that removing this gate would not rescue an eligible MOSS crop here. After native competing-speaker exclusions and 100 ms erosion on each side, the four phrases have at most **9,312 / 6,736 / 2,576 / 0 frames** of clean support at 16 kHz: **0.582 / 0.421 / 0.161 / 0 seconds**, each below the unchanged 32,000-frame / 2-second requirement.

These are saved-geometry findings, not new inference, current-authority revalidation, or acoustic ground truth. Original raw model text was volatile; retained parsed phrase and rejected-pair evidence was used without reconstruction. Primary-packet bodies were not audited for clean-crop eligibility. This output therefore supplies no recovered valid independent MOSS observation supporting a phrase-quarantine change.

## Shutdown, restart, and resource limits

Both services completed genuine product lifespan shutdown, then returned the pinned Uvicorn SIGTERM exit status (-15). Process/start identity, the owner-requested signal, normal lifespan return, clean product state, positive wait, and no surviving owned descendants were verified. No forced kill or cleanup/resource fault occurred.

The models-disabled restart preserved public text, evidence, history, retained results, and relevant durable database contents. Annotated-page byte identity and fresh live identity authority were not claimed.

- Work elapsed: **98.320 s**; total including frozen scoring: **99.875 s**, within 900 s work plus 120 s cleanup.
- Peak sampled owned RSS: **1,870,094,336 B**; charged peak including fixed historical/secondary reserves: **3,212,271,616 B**, below 6 GiB.
- Peak runtime output: **12,021,760 B**; evidence: **1,511,424 B**, below 128 MiB / 8 MiB.
- CPU 0–1, nice 19; 949 resource samples. RSS was sampled, not cgroup-enforced; inherited address-space limits were unlimited.

All frozen pins and sealed artifacts passed independent terminal review. Scoring stayed unchanged and opened reference labels only after runtime output sealing. With no scored independent pair denominator, zero observed false merges is **not identity-quality success**, and missed-link rates are unmeasured. No additional model retry was performed.
