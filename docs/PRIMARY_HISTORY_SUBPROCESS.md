# Pure comparison subprocess: source and diagnostic status

## Status and scope

Checkpoint 08 preserves the subprocess integration introduced in checkpoint 07 and includes the narrowly corrected adapter and focused process tests. The manager and permit product bytes are unchanged from checkpoint 07. The implementation changes the comparison portions of moss_handy_jobs.py and native_execution_permit.py, adds recording_speaker_history_process.py, and uses process-aware integration hooks. The exact comparator remains byte-identical: no matrix compaction, arithmetic, threshold, clustering or pruning change. Database schema, producer cadence and model policy are unchanged.

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

The exact original exec Popen wait returned zero, its control channels closed, the primary lane joined, and the original outer child was positively reaped. No forced kill was recorded. Independent terminal audit retained both completed useful work and positive OS cleanup while preserving the raw failed status and unexecuted final checks. The production adapter distinguishes these lifecycle states. Its selected process and integration cases have now executed as described below; the historical diagnostic remains a raw failed gate.

The exec-only attempt used 23.943399 seconds supervised wall and 29.612945 aggregate CPU seconds. Cumulative retained concurrency-family use through that attempt was 232.495805 seconds wall and 163.744169 CPU seconds. Final retained concurrency artifacts were 43,085,824 bytes across 282 entries; older history artifacts were separately retained at 42,102,784 bytes across 255 entries. Peak and terminal values are distinct. All failures remain preserved; no causal rerun is planned.

## Current source-bound functional result

The corrected snapshot contains 535 source files totaling 9,667,296 bytes. Its manifest SHA256 is b81c3b0ad30d25b634eeb84d5d134f8e97e145cdff0c0b98be6e5403c0adda39. The adapter SHA256 is 03005a8a6b655259b39b5766a856fd196b0f219e5721ef1ed2467e989620fc53; the process-test SHA256 is f065e5a34fba9130f8ab29fe5cedcebd618d2f142d01070cc86d14ccd81d31bc. The pure comparator remains f7ff2f44212689c4b72014ef79759d155f3ae86d66e3684ef0e119ef41176959.

The first real-child startup exposed an OS identity serialization defect: an executable inode may occupy the high unsigned-64 range. Device/inode components now use exact bounded canonical decimal strings; generic numeric fields retain signed-64 bounds. Existing tests cover zero, the signed boundary, high-bit and maximum unsigned values, invalid types/ranges, actual executable identity and generic oversized-integer refusal. Other post-checkpoint-07 corrections bound the prospective encoded graph, distinguish no-child closure from a positive Popen wait, and forbid reopening a closed driver. Process parameter IDs were shortened without dropping values or assertions.

All 78 selected process tests passed. Three independently completed integration stages added 2, 13 and 12 exact JUnit passes, for 105 unique current-source cases. Selected coverage includes real child/direct-comparator parity and cached continuation, protocol bounds, startup and cleanup ownership, real primary producer/commit/normal cleanup, fresh mocked probe cleanup, positive and abstaining complete history decisions, passive transcript reads, primary conservation, lost acknowledgements, configuration, resource refusal, and cancellation/revocation fences. This is selected functional coverage, not the entire repository suite.

The last 12-case integration stage stopped at the cumulative test CPU work ceiling. It produced two progress dots and no final JUnit; all 12 remain unqualified. The stage's original pytest wait returned -15 after the guard requested termination, both observed comparator children were absent, and cleanup recorded no errors or forced kill. The immutable source, test tools and prior evidence pins matched at exit. Independent terminal review SHA256: aea9f72992a428a6e98945422c7d6473b028d6f5e6d9564520718d16fe6cfdd1.

The unfinished selection covers uncertain authority, concurrent maintainers, primary-driven comparison resource refusal, stop during claimed maintenance, positive child wait with uncertain descendant closure, revocation at feed/output/publication boundaries, and graph-capacity refusal. Completed earlier cases are not scheduled for repetition merely to finish this selection. Test-controller CPU cost and its interrupted stage are retained separately from product performance claims.

## Required qualification

Before deployment, finish the unqualified selected cases and remaining failure/restart coverage. Preserve the completed protocol/ownership and useful three-observation A/A/B and two-observation abstention results through the real store/producer and passive transcript reader; changes to their relevant source require a source-bound recheck. Measure full 2,884-observation cold/warm incremental work alongside matched real synthetic primary cohorts with primary phase timing, CPU masks/nice, exact compute parity and full child/parent resource accounting. Use a fixed finite primary schedule and identify observed overlap separately for scoring, clustering and output, including phases with no observed overlap; an early scoring quantum alone does not cover the complete compute path. Keep the original task deadline and primary capture budget. Whole-snapshot resource refusal must retain every admitted competitor and leave optional results pending.

The logical build ceiling remains 134,217,728 bytes. The total owner reservation is explicitly 201,326,592 bytes; child runtime and parent/IPC allowances are each 33,554,432 bytes. Actual startup baseline, maximum metadata/proof transport, simultaneous buffer peaks, cached high-water and cleanup peaks remain to be measured. Aggregate service accounting remains at most 6 GiB or a stricter configured cap. No inference, acoustic-quality, production-SQL, adverse-multi-round or long-horizon throughput qualification is implied.
