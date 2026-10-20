# Dev63 fixed AMI operations smoke: recovered PASS

## Scope and result

A fixed 480-second prefix of official AMI ES2011a Mix-Headset exercised the installed `0.2.0rc3.dev63` backend through its local public API and timestamped recording uploader. Input was mono PCM16 at 16 kHz, exactly 7,680,000 frames. Selection used metadata only, without listening or transcript labels. Available project inventory showed no prior use, but was not an exhaustive lifetime-exposure guarantee. This is an operations/cadence check, not independent speaker-accuracy confirmation or long-duration endurance acceptance.

The initial run was **PARTIAL / OPERATIONS FAIL**. A separately reviewed, same-Store continuation achieved **RECOVERED OPERATIONS PASS**. These are distinct outcomes; the failed initial run is not erased.

- All 46 work chunks across two pages and five source pieces completed, covering all 480 seconds.
- The production-created scope pinned `fresh-window-bounded-v2`; acoustic models, thresholds and `primary-clean-history-v1` policy were unchanged.
- Seven durable admissions remained: six successful vector results at source times 42, 63, 84, 105, 304.5 and 420 seconds, plus one failed pre-dispatch admission at 126 seconds. An admission is not necessarily an executed probe.
- Public projections and a guarded fresh-interpreter installed-reader traversal verified persisted output after orderly shutdown. This was not a restarted live API test.
- Authenticated analyzer, native diarizer and speaker-probe execution/configuration evidence was retained. Original process cleanup completed with no observed surviving family and no final monitor errors.
- Anonymous identity links reported abstention. This does not prevent operational completion and does not prove identity accuracy. Event and transcription accuracy were not scored.

## Initial failure and conservative recovery

The initial collector stopped after 206.91 seconds when a previously authenticated process had empty command-line metadata and an unavailable executable while a fresh kernel observation still showed it live. The existing bounded revalidation did not resolve the observation. Cleanup was positively verified. At that point 24 chunks were complete, four queued and 18 staging.

The corrective collector adds only a terminal-observation grace of at most three reads within 100 ms, inside the existing single recovery transaction. It requires a fully proven prior recognized role/native identity, preserves conservative resource charge, checks the entire observation deadline and fails closed if identity or terminal status remains uncertain. The original receipt does not establish the completeness of the prior cached role, so the original refusal cannot retrospectively be declared benign or guaranteed fixable by this change.

Twenty-nine finite synthetic collector controls passed before the root-owned continuation. **The new terminal grace was not exercised during that continuation.** The real run therefore does not independently demonstrate this race correction.

The same Store/session/capture was continued. All original 24 completed records, five terminal attempts, five certificates, 51 PCM identities, work/source ID sets and original receipt were preserved. No new session or source upload occurred. Preserved durable records show no duplicate admission or changed completed work; sampled process observations do not constitute an exhaustive replay census. The only upload-workflow continuation was five calls to the existing page-2 seal with its original idempotency key and exact geometry, following source/page reconciliation. Uncertain responses required observation before retry. Original wall deadlines were retained.

## Cadence evidence and limits

The later-fresh-window branch was **EXERCISED**: retained samples showed a post-bootstrap bucket progressing from one to three examinations after insufficient clean support. This does **not** establish that the successful probes at 304.5 or 420 seconds were caused by a same-bucket retry.

After the 120-second bootstrap, observed durable admissions obeyed the maximum of one per 60-second bucket. Production's per-bucket examination cap remained three. Sidecar polling and process census are sampled, not a record of every intermediate examination or short-lived process. Raw durable admissions and their terminal outcomes were preserved separately.

The 126-second admission failed with `dispatch_validation / ReadError / other / child_dispatch:not_attempted`. It was visible 50.778 seconds before monitor-triggered shutdown, so it was not caused by that shutdown. Sanitized terminal evidence omits the underlying reason and stack; exact cause remains unresolved. Unbound SQLite contention logs do not establish a causal diagnosis. The failed attempt remains in the recovered result and was not retried.

## Resources and timing

Two CPU threads were configured. Peak sampled charged memory was 2,275,430,400 bytes initially and 2,399,694,848 bytes during continuation, under the 6 GiB sampled charged-memory envelope. This is not a kernel-enforced aggregate-memory guarantee. Peak cumulative allocated experiment output was 108,994,560 bytes.

The final receipt reports 1,240.62 seconds measured from the initial run's origin, including manual investigation/preparation downtime. **No real-time factor or sustained-throughput claim is made.** This eight-minute fixture does not establish 12–48-hour operation, cross-recording identity accuracy, or a wall-clock-capture timestamp contract; uploader timestamp geometry uses an explicit device-frame origin.

## Reproducibility and provenance

- Fixed prefix SHA256: `89e7865390847cd9daf167cd94e90742d55b88ed436cf966751a4b9427de5f8c`.
- Initial collector freeze SHA256: `0f8fbb0e9c569c720b07f8b9538bd4fb07fd9dc66c118d6dbd6e747d2dc49f1b`.
- Corrected continuation freeze SHA256: `eb1da2c0e3bba88e9e747160087d1a9214d348c45d6dfd89e5927af315efb62e`.
- Final private execution receipt SHA256: `0ccf89fda78fcee0192042519f165922805d33e6db1a0fe0a8008f3b618f5f92`.

The accompanying collector/control checkpoint is a sanitized source archive, not a portable already-approved launch bundle. Local configuration, permissions and pins must be regenerated and reviewed before execution. Raw audio, transcripts, embeddings, databases and private absolute paths are deliberately excluded.

Fixture attribution: AMI Meeting Corpus, ES2011a Mix-Headset, [official signal metadata](https://groups.inf.ed.ac.uk/ami/corpus/signals.shtml), [official source WAV](https://groups.inf.ed.ac.uk/ami/AMICorpusMirror//amicorpus/ES2011a/audio/ES2011a.Mix-Headset.wav), [CC BY 4.0 license](https://groups.inf.ed.ac.uk/ami/corpus/license.shtml). Modification: exact first 480 seconds copied without resampling or changing samples; WAV header normalized.
