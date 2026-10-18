# Pure comparison subprocess: source and diagnostic status

## Status and scope

Checkpoint 07 is an unexecuted source backup over checkpoint 06. It changes the comparison portions of moss_handy_jobs.py and native_execution_permit.py, adds recording_speaker_history_process.py, migrates integration hooks and adds focused process tests. The exact comparator remains byte-identical: no matrix compaction, arithmetic, threshold, clustering or pruning change. Database schema, producer cadence and model policy are unchanged.

The process adapter is purpose-built for one comparison owner. It is not a pool or a generic task service. Parent storage/currentness ownership and the existing fair scope lifecycle remain in place. Detailed source comments specify bounded framing, decoded shape checks, IPC buffer charges, original-handle cleanup and cache lifetime.

## Preserved causal diagnostic

A fixed model-free workload used 512 observations, 130,816 exact pair scores, 512 annotations and one comparison round. The alone/thread arms ran in the same earlier diagnostic; the missing exec arm ran later against byte-identical retained inputs. No arm was tuned or repeated after its substantive result.

Input SHA256: ae113dd5bd1eac3f83156afc05bac1d228df4d17274b20ab5fc02da03dbe0a6d.
Vector SHA256: b1ad8f19b9a5aeb0505426924fb3e962f96131e57369afc28efa99ddc8c81819.
Both completed comparison arms produced score SHA256 8b8b38b45058625d608c60c5899d38b7fc9c8147effc964cc1b6fd85a6bdb517 and output SHA256 4070d40ff5fabcf11fc93759bccb21c6aa3339f31ecce010f3463cffbaa95ba4.

Primary alone: one committed cohort, 0.499027 seconds cohort wall and 0.331795 seconds primary-thread CPU; cumulative capture budget 0.095804 seconds.
Primary with comparison thread: one committed cohort, 1.623350 seconds cohort wall and 0.356018 seconds primary-thread CPU; capture budget 0.411867 seconds.
Primary with exec comparison: one committed cohort, 0.646703 seconds cohort wall and 0.340202 seconds primary-thread CPU; capture budget 0.170114 seconds.
All three capture budgets retained the same 0.5-second limit, 17,000 VM steps and 2,534,144 read bytes. Cohort wall includes other phases and is not the capture-budget duration. The source_row phase is not the whole capture budget.

The exec comparison completed 148 bounded quanta, 7.024005 seconds summed quantum wall and 5.629335 seconds compute CPU. Seven fully contained quanta and 6,018 pair scores overlapped the primary attempt. The comparison thread previously completed 140 quanta, 6.592456 seconds summed quantum wall and 5.386568 seconds CPU, with 19 overlapping quanta and 22,913 scores. Observed full exec-child peak RSS was 24,133,632 bytes; parent positive growth was 19,488,768 bytes; their sampled combined peak was 43,597,824 bytes. Parent growth is not exact comparison attribution. These 512-row values do not establish full-size fit.

The primary lane read back CPU 3/nice 19, and exec comparison read back CPU 4/nice 19. Affinity does not establish a CPU quota or dedicated-core deployment. Different execution times, allocator state, ambient scheduling and bounded instrumentation limit causal inference. The results are consistent with shared-interpreter contention and support a production isolation trial; they do not alone prove a GIL cause or full-size safety.

The earlier alone/thread raw primary rows differ in epoch-qualified local speaker IDs. Their separately retained provenance/native-commit bindings support an explicitly derived semantic comparison. Raw byte equality is false. The final equivalent exec historical-binding check was not executed after its lifecycle failure, so no three-arm authority-parity claim is made.

## Raw lifecycle failures and closure

The initial diagnostic failed during fresh SQLite sidecar accounting before useful work. A subsequent diagnostic retained the successful alone/thread arms but stopped at an immediate child-argv assertion. The missing exec arm then passed its bounded no-work handshake and completed primary/compute work. Its outer observer failed during process close when startup-oriented cmdline classification was applied again to the already authenticated child. The original child was subsequently positively waited. The close acknowledgement was interrupted; final fixture checks did not run.

The exact original exec Popen wait returned zero, its control channels closed, the primary lane joined, and the original outer child was positively reaped. No forced kill was recorded. Independent terminal audit retained both completed useful work and positive OS cleanup while preserving the raw failed status and unexecuted final checks. This source corrects the lifecycle distinction; the correction has not executed.

The exec-only attempt used 23.943399 seconds supervised wall and 29.612945 aggregate CPU seconds. Cumulative retained concurrency-family use through that attempt was 232.495805 seconds wall and 163.744169 CPU seconds. Final retained concurrency artifacts were 43,085,824 bytes across 282 entries; older history artifacts were separately retained at 42,102,784 bytes across 255 entries. Peak and terminal values are distinct. All failures remain preserved; no causal rerun is planned.

## Required qualification

Before deployment, execute the source-bound protocol/ownership, malformed-input, startup, close-ack loss, wait/family uncertainty, cancellation, configuration, revocation, rebase and restart cases. Recheck the useful three-observation A/A/B and two-observation abstention paths through the real store/producer and passive transcript reader. Measure full 2,884-observation cold/warm incremental work alongside matched real synthetic primary cohorts with primary phase timing, CPU masks/nice, exact compute parity and full child/parent resource accounting. Use a fixed finite primary schedule and identify observed overlap separately for scoring, clustering and output, including phases with no observed overlap; an early scoring quantum alone does not cover the complete compute path. Keep the original task deadline and primary capture budget. Whole-snapshot resource refusal must retain every admitted competitor and leave optional results pending.

The logical build ceiling remains 134,217,728 bytes. The total owner reservation is explicitly 201,326,592 bytes; child runtime and parent/IPC allowances are each 33,554,432 bytes. Actual startup baseline, maximum metadata/proof transport, simultaneous buffer peaks, cached high-water and cleanup peaks remain to be measured. Aggregate service accounting remains at most 6 GiB or a stricter configured cap. No inference, acoustic-quality, production-SQL, adverse-multi-round or long-horizon throughput qualification is implied.
