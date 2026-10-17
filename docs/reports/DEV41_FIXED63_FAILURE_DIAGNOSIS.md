# dev41 fixed-63-second trial: primary failure and bounded diagnosis

## Outcome

The single fixed trial exited **1** after 63.862520432 seconds. Both uploads were published and all six source receipts were durable, but only the first **21 seconds** of primary processing completed. The chunk covering 21–31.5 seconds failed at local offset 0 with the retained error **`OperationalError: interrupted`**. The final three chunks remained queued at offset 0.

The 63 seconds uploaded must not be described as 63 seconds processed. The observer stopped on the primary terminal failure before collecting automatic proof/result bodies, transcript partitions, or models-disabled restart evidence. The intended same-candidate third-positive-root and cross-upload objective, partial-parent conservation, and restart are **unqualified**, rather than successful or demonstrated absent. The raw failure and original artifacts are retained; no inference retry or rerun occurred.

## Fixed input and disclosed configuration

The input was unchanged from the dev39 trial: two uploads in the same order, first 31.5 seconds of previously exposed AISHELL-4 speech, then 31.5 seconds of declared digital zeros. The speech joins the existing 14.5- and 17-second contiguous crops. Prepared bytes were reused without new audio selection, decoding, mixing, or threshold tuning. Prompt, model profile, native/ASR settings, geometry and functional criteria stayed fixed.

The published source checkpoint was dev41, commit `defcb5716501fe67c33cae8328819905a46898f4`, tree `8cef6e8b3552ad1749ee0bc6332540de7d345bd6`, with sole parent `6b16d968f4d37b4fca3da4b89ac76fc6221ee20e`.

This trial explicitly configured the supported **1.0-second cumulative bounded-primary budget**, instead of dev39's 0.75 seconds. The product default remains 0.75 seconds, and its earlier deadline/rollback evidence remains preserved. The planning subphase stayed 0.4 seconds / 120,000 steps / 128 KiB. Finalization used 0.98 seconds / 148,000 steps / 252 KiB; notice used 1.0 second / 150,000 steps / 256 KiB. The separate owner-publication budget remained 2.0 seconds. This run therefore does not qualify an unchanged-default configuration.

The same pinned local Q8_0 artifact and experimental CPU profile were retained. Local repeatability pins do not establish upstream conversion or quantization provenance.

## What the saved evidence establishes

The saved log records a `bounded_planning_capacity` refusal in **finalization**:

- Projected SQL bytes: **282,415**, exceeding the phase limit **258,048** and overall limit **262,144**.
- Cumulative elapsed time: **0.240236 seconds**. Neither the 1.0-second cumulative limit nor the 0.98-second phase limit was exceeded.
- Steps: **38,000**, below both applicable step limits.
- Source bytes read: **413,440**, below the 41,943,040-byte source-read limit.

This is direct evidence of a projected-SQL-byte refusal, not a timeout. It does not identify the exact historical SQL statement or prove how the refusal propagated into the primary error. The historical transaction's commit/rollback outcome was not measured.

The last saved observation was round 6, at 53.575394572 seconds. It showed the first two chunks complete, the third running, and the remaining three queued. Its automatic counters were candidate_count=2, queued=1, waiting=1, accepted=0 and published=0; queued and waiting may describe the same row. These are a pre-failure snapshot; they do not establish terminal automatic states or zero native/MOSS invocations.

Round 7 raised the primary-failure assertion before its response was saved. A subsequent bounded inspection of an already stopped database copy recovered the six final chunk statuses and the exact 29-byte interrupted error. It used four indexed reads including query plans, returned nine rows including plan/preflight rows, and projected 582 bytes. The inspection exited 0; all original bytes and the copied main database hash were unchanged. Only status/error/offset scalars were inspected. No proof, transcript, audio payload, live API, or model replay was used. This inspection closes the missing status evidence, not the historical transaction-causality gap.

## Resource and cleanup evidence

The original execution completed within the fixed 900-second work plus 120-second cleanup allowance. Peak sampled owned-process RSS was **1,845,940,224 bytes**; adding the 768 MiB unknown and 512 MiB secondary reserves gave **3,188,117,504 bytes**, below the 6 GiB aggregate ceiling. Peak fresh output was 11,960,320 bytes and evidence 765,952 bytes, within the 64 MiB / 8 MiB limits. No resource error was recorded.

The actual server received SIGTERM, exited with return code -15, and was positively waited. Ordered lifespan shutdown markers were present; shutdown took 0.216445306 seconds. There was no forced kill, extra descendant cleanup, or surviving owned process, and the resource monitor stopped. A history-only server was never launched.

## Qualification boundary

Full functional success still requires one naturally produced, authenticated candidate with at least three distinct native commit roots contributing positive PCM frame counts and positive PCM from both uploads, with exact proof, target, result and reading bindings. Digital-zero frames may establish provenance but cannot establish additional speech or conversational cross-file accuracy. Partial-parent replacement and exact retained-text conservation remain separate outcomes.

This failed run supplies no new qualified result-body, timing, speaker-identity, completeness, lexical-conservation, or restart outcome. No subsequent repair success is claimed. The report records the observed failure and finite diagnosis for a later source checkpoint; it does not approve or qualify a future fix.
