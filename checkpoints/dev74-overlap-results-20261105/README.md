# dev74 overlap candidate results

Overall qualification: **FAILED**. The digital-zero control produced fabricated text and a parse failure. This measured failure remains unchanged by any later zero-input guard. Outputs are optional candidates; they do not overwrite the primary transcript.

Six eligible fixed cases (69.56 seconds) were evaluated from seven planned slots. Only three were real overlap excerpts: two English and one Mandarin. Meetings were exposed in earlier work, so this is not a fresh holdout. MOSS-TD is direct multi-speaker text recognition, not waveform separation.

- English overlap: 6 edits / 33 reference words (18.18% WER).
- Mandarin overlap: 8 edits / 65 reference characters (12.31% CER).
- Minority-voice errors: English 0/7 and 3/6; Mandarin 6/28. Extra output tracks are retained and penalized. No complete recovery of both speakers is claimed.
- Actual primary local speaker scopes cannot support a whole-clip speaker comparison. The prespecified one-stream sensitivity is a different comparison that includes missing-channel penalties, not pure content-recall improvement.
- Original run failed after a read-only503 and a native tail exit. One approved formal retry recovered the same failed/manual Chinese tail, preserving committed outputs and the complete original preimage. No candidate call was repeated. Exact cause of the initial native exit remains uncertain.

Observed peak owned RSS: 1,618,710,528 bytes (about1.51GiB); combined sampled elapsed182.11s and CPU253.70s, not exact full lifecycle totals. No surviving owned processes. The memory boundary was the9.733GiB physical guest, zero swap/GPU, with6GiB sampled stop,2GiB available reserve and8GiB per-process address-space limits; not cgroup isolation or a summed-RSS guarantee.

Independent scoring and terminal integrity audits passed; those audits do not turn this into a product quality pass. Public files contain counts, hashes and anonymous ordinals, not audio, transcripts, private gold, models or database contents.
