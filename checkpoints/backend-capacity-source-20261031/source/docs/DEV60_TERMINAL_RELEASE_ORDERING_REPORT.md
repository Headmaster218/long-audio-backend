# dev60 terminal-release ordering: no actionable defect

20 October 2026 UTC. One frozen, model-free gate passed **8/8 cases once**, including all 24 setup/call/teardown reports. The two new Worker/manager ordering tests and six unchanged retained controls characterize correct current behavior. They establish no actionable product defect, justify no deferral of mandatory teardown, and do not establish the historical rank-5 `stale_generation` cause. Product code and package/version remain unchanged.

## Constructed outcomes

The new [tests](../tests/test_worker_terminal_release_ordering.py) execute the real `Worker._automatic_handoff(request=False)`, `MossJobs.maintain_history_comparison`, manager close path and permit release. Process/driver, census, completion-future, source/currentness and automatic-step edges are synthetic; this is not a full Automatic or native-child execution.

1. **Terminal release during an already-active primary census:** A genuinely bound synthetic comparison has positively closed its original synthetic child while retaining its charge. After primary admission and census generation/flight capture, its synthetic task future becomes complete. Real Worker maintenance promptly closes/releases the comparison. The redundant pause preserves the comparison revision; actual grant removal rotates generation. The held census is refused as `stale_generation` before primary currentness reading or new child dispatch. Its exact reservation, owner, binding and original deadline remain held until cleanup. One census is used. This strict refusal is correct.
2. **Terminal release before new primary admission:** With the comparison already complete, the Worker's first maintenance releases it before the synthetic automatic step admits the primary. Admission and subsequent dispatch census both use the fresh post-release generation with zero comparison charge, and primary currentness succeeds. Existing Worker ordering already supplies this path.

The six byte-identical controls in [the retained suite](../tests/test_primary_probe_census_coordination_candidate.py) cover existing positive-release invalidation; generation and shutdown revocation; foreign claim/grant/owner isolation; stable bound-owner coexistence; and possible-child retention through uncertain closure until positive cleanup. The permit-level positive-release control predates this work and is not a new discovery. Historical hash-bound fixtures were not adapted or bypassed. This gate is not exhaustive revocation or retained-cache/redundant-pause qualification, and it reopens no dev58/dev59/dev60 fix.

## Bounds and cleanup

The approved single-run envelope was 60 seconds including cleanup, 30 aggregate CPU seconds, CPU 3/nice 19, 384 MiB sampled aggregate RSS, 1 GiB pytest address space and 8 MiB fresh allocation including preparation. Each new case shares one original five-second deadline across waits, barrier release and joins. At most two additional test threads were allowed; the new overlap uses one. The ordinary supervisor/pytest processes were the only permitted execution processes; no model, audio, database, network or OS comparison child was used.

The controller measured 1.342 seconds including cleanup, 1.358 accounted CPU seconds (2.358 including the conservative one-second outer allowance), 130,248,704 bytes peak sampled combined RSS and 409,600 bytes peak sampled fresh allocation. RSS includes a conservative 32 MiB outer allowance. These are sampled/accounted observations, not continuous kernel aggregate limits or a complete transient-process census. The original outer handle and pytest both completed with exit 0; all original test threads joined, observed descendants were absent, no forced kill occurred and cleanup errors were empty. Absence claims are limited to the observed execution namespace.

## Source and evidence identity

The tested installed product is unchanged dev60 commit `6326733db5984d95546f52bd0de5653504af2ee0`. Independent terminal review verified the exact eight-node argv, required module origins, all 179 input pins, the 167-file installed inventory, and unchanged product/control bytes. SHA256 pins:

- `audio_backend/native_execution_permit.py`: `2d821d8e20c526ea609e61d77e638dde1d937748f517ec4bf696d826f54d0530`
- `audio_backend/moss_handy_jobs.py`: `bd0818ff1e52539acd791cfaeefa2622668f7a92a2a1a7e981d29700058fc3ba`
- `audio_backend/worker.py`: `c2a5adad839ae565442cae212581925a5e734f5ed5d5f1742a695aa5282ad251`
- New test file: `e1d09d7ea3a6ae117933c5f55066ec822e623c2affe4f06427e780f88d377b42`
- Retained control file: `a9c56be68aa4494708a847fdbd8126150f0cb0f2124f701353fde4d6bb625ab7`
- Execution receipt: `9d2d2db4df093e23fea7c19747c23bb98bea4366241fdff0725a80bdd7e69993`
- Independent terminal audit: `0562b942b1c07bb31722e20db27362310f6725f846561addac952742c1798574`

This checkpoint retains only the exact new test and this report. Its imports use the existing retained suite's `no_external_work` fixture and `sample` helper; that file is neither added nor changed. No retest, package build, product fix, historical-attribution claim or further model evaluation follows from this negative/no-actionable result. Raw receipts and execution logs remain outside the source checkpoint.
