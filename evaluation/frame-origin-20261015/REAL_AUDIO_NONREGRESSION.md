# Frame-origin candidate: finite real-audio nonregression

Date: 2026-10-15. Four serial, fresh-database cases completed using the same official model bytes and input bytes for each baseline/candidate pair. The candidate changes only the optional PCM-observation frame-origin handling in worker.py and native_owned_cell_capture.py; it was tested in isolated package copies and was not installed into the product runtime.

## Observed result

- Chinese demo, 70.470625 seconds, seven adjacent chunks: both versions produced 36 primary segments and 20 independent speaker turns, with 21 recorded SenseVoice batches. Baseline/candidate elapsed times were 44.451/45.769 seconds.
- AMI overlap fixture, 21 seconds, two adjacent chunks: both versions produced 16 primary segments and 14 independent speaker turns, with seven recorded SenseVoice batches. Elapsed times were 13.987/13.720 seconds.
- Both pairs matched exactly after the declared fresh-identity mappings. Text, raw text, timestamps, row order, scores, flags, native speaker_N labels and numeric epoch/frontier values were preserved. Fresh chunk IDs mapped to input indices, fresh speaker IDs used one consistent bijection across both row sets, and fresh epoch prefixes mapped to their validated numeric epoch indices.
- Executed module paths/hashes matched the isolated package copies; completed chunk metadata recorded actual native and SenseVoice processing. No MOSS job was requested or accepted.
- Every server and its observed owned children had positive completion/cleanup evidence. Peak sampled process-tree RSS was 697,475,072 bytes; maximum recorded RSS sampling gap was 0.272003 seconds. Combined retained case outputs stayed below 32 MiB. These are sampled bounds, not an unsampled peak guarantee.

The first sequence stopped before its second server launched because its port-availability probe returned EADDRINUSE on immediate port reuse. Its completed Chinese baseline and failure artifacts were retained. A fixed three-case continuation used distinct ports and reused the exact completed baseline; no baseline model rerun was performed. The continuation exited successfully.

## Capture and publication observations

Neither baseline nor candidate produced a capture-stage rejection on these inputs. Each Chinese case recorded three seam-commit no_same_response_native_overlap diagnostics and no retained overlap cells. Each AMI case recorded no rejection diagnostics and returned three retained cells, with three durable publications and zero dropped operations.

Both AMI raw final health snapshots retained last_capture_reason='publication_pending', an earlier asynchronous-submission event. The helper derived publication_pending=true from that string; the raw API has no such boolean. Current raw counters showed zero active/pending handoffs, zero pins and three durable cells; the helper reported no warning. The derived flag is a helper-reporting issue, not evidence of unfinished publication. Original summaries are retained unchanged. General optional-publication reliability remains outside this test's scope.

## Scope and limitations

This is finite primary-result nonregression evidence. It did not reproduce the fractional_frame_coordinate failures observed during the interrupted long-duration test, so it does not by itself prove those live failures are fixed. The earlier model-free regression establishes the candidate's behavior on explicitly constructed frame coordinates. The live failing coordinates were not retained and must not be inferred from those constructed cases.

No transcript accuracy, speaker-identity quality, MOSS behavior, long-duration operation or cleanup of the interrupted long-duration run is qualified by this result. Audio and transcript contents are not included here.

Candidate source was previously checkpointed at commit 186ac59f253bba5c1ac599ac4ba60325283a362a. Product dev9 remains the baseline; promotion of the isolated frame-origin candidate requires a separate product change.
