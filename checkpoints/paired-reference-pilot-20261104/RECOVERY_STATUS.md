# Recovery status, 2026-11-05 UTC

The environment restart interrupted original acquisition. Three full WAVs and an ES2015d partial remained, with no terminal receipt. The original partial is retained unchanged, and the original single-attempt plan is not reported as passed.

A separately frozen recovery fetched only ES2015d from the same official URL. ETag, exact length, header hash, mono PCM16 16 kHz geometry, and original partial prefix were checked. All four full sources have SHA256 seals. Recovery exited successfully after 78.1 seconds. Conservatively counting the entire possible original transfer plus recovery gives 290,242,276 bytes, below the original 512 MiB budget. Recovery completed about eight minutes after original acquisition began, below the original 20-minute observation window.

Independent annotation audit passed with no errors. All 36 preselected two-second crops were materialized unchanged; hashes bind sources, exact PCM, WAV and float32 inputs. No audio or embeddings are published here. Native inference and paired threshold selection have not run at this checkpoint.

Limits remain: English same-day separate scenario meetings, unknown historical exposure, annotation-qualified rather than human-listened clean speech; two known and two unknown people per split. Not Chinese/cross-day/long-duration or final product acceptance. Sources: AMI Meeting Corpus, CC BY 4.0, https://groups.inf.ed.ac.uk/ami/corpus/license.shtml .
