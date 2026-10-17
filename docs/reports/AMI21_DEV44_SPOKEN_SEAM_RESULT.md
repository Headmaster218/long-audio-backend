# AMI21 dev44: spoken-seam functionality and saved quality

## Result
The single fixed trial exited 0. Both transcript views selected authenticated bounded-support results crossing the predeclared boundary between two contiguous spoken-source uploads. Natural geometry used two positive native commit roots; no three-root result is claimed. The lexical view also selected a partial-parent replacement with exact retained/replaced text conservation. All 21 seconds completed, and models-disabled restart preserved 50 byte-equal saved-response pairs.

On the fixed 56-word reference window, within-run cpWER errors fell from 62 for primary baseline to 49 for the whole-primary reading and 34 for the lexical reading; greedy DI-cpWER errors were 28, 16 and 9. This is one previously exposed English AMI fixture. Functional crossing, source-annotation speech context and measured quality remain distinct; neither general accuracy nor global speaker identity is established.

## Fixed source and configuration
The unchanged AMI ES2002a Array1-01 excerpt covers source [510.5,531.5) seconds: 336,000 mono PCM16 frames at 16 kHz. Its pre-model split at local 10.5 seconds / frame 168,000 / source 521.0 was reused byte-exactly. No silence, mixing, crop search, target tuning or rerun was added. The pinned RTTM overlap [519.67,522.57) crosses that boundary. Annotation timing and channel-audibility caveats remain; the separate word reference has forced-alignment caveats.

Deployed dev44 checkpoint: e3eee0d4f2ca4d81e3d91617f217d791f63d374e, tree d05cd96c73aa8459fe288f596b81154938ff9f24, parent c5cbecaf390f53de68d98d079defa16a3a0413c2. Package/source/input and existing local model/profile/runtime pins were checked. Prompt, inference settings and thresholds were unchanged. The configured cumulative bounded-primary budget remained 1.0 second: planning 0.4, finalization ceiling 0.98, notice ceiling 1.0. Product default remains 0.75 second; owner publication has a separate 2.0-second budget.

## Selected results and exact provenance
Three automatic results committed: two ordinary intents and one core-seam intent. Metadata records three model construct/session/run attempts. Each result has an authenticated proof, exact query/support PCM lineage, distinct native slots and positive frames from both uploads. Native primary invocation count is unavailable.

- Default/whole-primary selected the five-phrase result: support [159056,300144) frames, source [520.441,529.259) seconds, with 8,944 and 132,144 frames from the two roots/uploads. Proof: 41,026 bytes; v3 inventory: 15,813 bytes.
- Lexical selected the eight-phrase result: support [116816,300144), source [517.801,529.259), with 51,184 and 132,144 frames. Proof: 53,873 bytes; v3 inventory: 22,305 bytes. This support contains the full fixed RTTM overlap interval.

The common actual query was [8000,328000) frames. Query/support PCM16 and support float32 hashes were independently reconciled with the unchanged uploads. Targets are nested inside the proofs; adding their bytes again is not actual proof storage. The earliest three-phrase crossing result remained an alternative in both views. All 16 phrases and job-scoped labels were retained exactly across each reading and its alternatives.

The lexical projection kept [0,5) of one six-unit parent and replaced [5,6). Its complete raw/display partition and retained source context were conserved without duplicate full-parent or punctuation content. Other replaced whole parents and alternatives remained available. Text conservation alone does not establish word correctness.

## Saved-only quality comparison
The pinned official evaluator and unchanged normalization scored the common source window [511,531), 56 reference words, using midpoint inclusion of whole hypothesis units. In baseline / whole-primary / lexical order:

- cpWER: 62/56 = 110.71%; 49/56 = 87.50%; 34/56 = 60.71%.
- Greedy DI-cpWER: 28/56 = 50.00%; 16/56 = 28.57%; 9/56 = 16.07%.

Lexical cp errors are 2 substitutions, 15 deletions and 17 insertions; greedy DI errors are 5 substitutions, 1 deletion and 3 insertions. The 17 cp insertions are retained-primary/fragment words in three unmatched source-specific scopes, not proof of spurious audio. No gold-based speaker merges, guessed word times or alternative-result concatenation were used. DI is approximate and assignment/order-sensitive; its difference from cp is not a standalone diarization penalty.

Assignment-conditioned minority matches were 9/19 under lexical cp and 17/19 under lexical DI. Annotation-overlap-associated minority matches were 8/9 in both selected alignments, versus 0/9 at baseline. Alignment ties and forced-aligned reference timing qualify these counts.

Fixed sensitivities, in the same lane order: the full 58-word window gives cp errors 61/48/36 and DI 27/15/8; common-window fully-contained inclusion gives cp 59/49/28 and DI 30/18/11, all over 56 words. Any-positive-overlap inclusion matches the main result. Each main lane contains one boundary-crossing whole hypothesis unit. These sensitivities were reported rather than used to choose a preferred result.

## Completion, restart and limits
The first saved complete-primary observation was 33.628 seconds; total trial elapsed was 123.060 seconds. All admitted jobs drained. Restart preserved three transcript pages, 37 evidence objects, six result/history objects, durable state, projection, audit and aggregate history: 50 exact pairs. Both servers had clean shutdown and positive waits, with no survivor, forced kill or resource violation. All 78 retained helper checks passed before dispatch.

Sampled peak process-tree RSS was 1,850,044,416 bytes; including both reserves, 3,192,221,696 bytes. Peak fresh allocation was 9,097,216 bytes; evidence allocation 1,679,360 bytes. Limits remained 900 seconds work plus 120 cleanup, 6 GiB aggregate including 768 MiB unknown and 512 MiB secondary reserves, 64 MiB output and 8 MiB evidence.

Each structured result had zero schema errors and one timestamp-adjusted phrase under support-end-intersection-10ms-v1. Timing remains model-generated and unverified. Raw endpoints returned 404/404/409, so raw byte counts/digests are metadata only. EOS and generated-token counts are unavailable, complete_content is false, and speaker_identity_valid is false. No aborted/truncated flag does not prove acoustic completeness.

The dev42 AMI raw exit 1, complete primary/restart, zero replacement and 300.060-second expiry remain preserved. Its discarded query/proposal is unavailable; this success neither reconstructs the historical comparison nor isolates which repair changed it. Full model hashes were admission checks; postrun checks covered small runtime, controls, deployment, inputs and saved evidence. Upstream model-conversion provenance remains unverified. No further actual dispatch is authorized by this report.
