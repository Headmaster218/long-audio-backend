# dev34: 600-second natural-speech operational check

## Outcome and limits

The unchanged **0.2.0rc3.dev34** backend completed one bounded 600-second Oyez run in **27m13.69s**, within the 1,680-second work and 1,800-second total limits. All input was accepted, published and primary-committed; admitted work drained and both servers shut down cleanly. The independent terminal audit accepted this operational result.

**There were zero MOSS runs selected into unified reading.** All 32 archived alternatives remained unresolved: **28 partial-primary cases and four native-parse cases**. The 139 inline MOSS phrase annotations reported provisional recording-speaker-link **capacity_exhausted**. These limitations remain part of the result; completion is not a recognition-quality, merged-reading or speaker-identity pass.

This run used dev34 throughout. Its report is prepared against the later dev35 publication parent a83b7422cb609b92564b2bfeac2b96f2be07bfa7; it is not a dev35 execution result.

## Input and processing

The existing official Oyez recording for case 13-517, oral argument dated 2014-10-08, supplied the first [0,600) seconds once. Decoding from the source origin produced exactly 9,600,000 mono 16 kHz PCM16 frames: 19 full 31.5-second upload pages and one 1.5-second EOS page. There were no loops or artificial silence padding. This window is disjoint from the recorded prior model crop, but the full source had previously been decoded. Oyez attribution and the research-use CC BY-NC 4.0 provenance are retained; no audio or transcript is included here.

The run completed 58 primary chunks, 79 native core commits and 20 retained source uploads. Page counts are not native-call counts. There were 83 observed SenseVoice batches, 129 FrameMN calls, and 32 naturally triggered MOSS attempts with 32 committed outcomes. No manual MOSS calls, blocked or unknown attempts, pending outcomes, or new model/dependency downloads were recorded.

## Timing and unified reading

Of the 32 MOSS results, 28 were schema-clean and four retained invalid_model_timestamps. Three rejected ends exceeded support by 20 ms and one by 30 ms. Four other results each contained one endpoint-adjusted phrase under the existing 10 ms endpoint policy. All timestamps remain model-generated and unverified; all results retain complete_content=false, unknown EOS/token count and no verified speaker identity. Schema-clean does not mean accurate or complete. Primary interval ends are estimated, not forced alignment; word-level alignment and global identity remain unqualified.

All ten contiguous 60-second transcript blocks had complete optional closure. The binding audit examined all 32 candidates with no unplaced binding. Reading nevertheless remained baseline: 36 page-local alternative entries represented the 32 unique unresolved candidates above. Speaker-link capacity exhaustion is separate from optional closure, which completed on every block.

All 32 structured history details/results and their proof PCM digests reconciled. Text/evidence projections remained stable across the models-disabled history restart. Full transcript responses were not byte-identical: that restart omitted optional speaker annotations. There was no acoustic restart experiment.

Native raw text was not independently preserved or rehashed. All 32 volatile raw-result requests were unavailable: 28 returned HTTP 409 and four HTTP 404. Their error receipts were retained. Structured results, proofs and numeric timestamp diagnostics were preserved; they do not substitute for successful raw-text retrieval.

## Throughput and resources

The 600-second pacing target was an earliest delivery target. Actual admission took 1,414.27 seconds; total wall time was 1,633.69 seconds, giving end-to-end wall RTF **2.723**. Maximum observed backlog was **85.4 seconds**, and the longest receipt wait was **188.88 seconds**. These figures include pacing, startup, optional processing, readback and cleanup; they are not pure inference speed. The earlier repeated/zero-heavy run is not a continuous-speech throughput guarantee.

CPU 0–1/nice 19 and the existing resource envelope remained in effect. Peak sampled process-tree RSS was 1,917,255,680 bytes; including the 768 MiB unknown/cleanup and shared 512 MiB secondary reserves, 3,259,432,960 bytes were charged against 6 GiB. The maximum RSS sampling gap was 0.285 seconds. No resource violation was reported, but unsampled peaks are not proven absent.

The earlier sampled output peak was 98,738,176 bytes. The directory-inclusive footprint at terminal audit was 105,451,520 bytes, below the 1 GiB output limit. These measurements have different scopes and observation times.

The deployment used the qualified installed package plus its documented adjacent external runtime manifest; the wheel alone is not a self-contained runtime qualification. This single English legal argument establishes neither Chinese accuracy, semantic completeness, word-accurate timing, natural MOSS selection into reading, global speaker identity, nor 12/48-hour endurance.
