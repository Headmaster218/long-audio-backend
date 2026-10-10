# Eight-input real-overlap benchmark evidence

## Result

On eight annotation-selected AliMeeting channel-0 mixed-audio inputs (120.515 seconds), all three paired views scored **127 errors / 686 reference characters = 18.5131% CER**:

- Raw canonical structured label/text diagnostic, including invalid-time rows if present
- Strict production-admitted candidate projection
- Strict plus the existing production partial-recovery policy, the frozen primary view

All eight generations ended with EOS. Strict admission accepted 50 phrases. There were no invalid-time rows, truncated outputs, or partial-recovery inputs. Thus this run demonstrates **no incremental recovery benefit**. Valid schema and successful execution do not establish transcription quality.

There are 24 reference speaker tracks across independently scoped inputs; 22 receive an oracle match, with no extra hypothesis tracks. Two reference tracks of one and four characters are unmatched under that assignment, costing five deletions. This does **not** prove their words are acoustically absent from every emitted track: content may appear on another model-local track. The other 122 errors occur in oracle-matched tracks and may combine recognition differences with within-input speaker mixing. Oracle permutation removes label naming differences, not merging, switching, or attribution errors within an input. No audio adjudication was performed, so reference/model disagreements are not classified as annotation mistakes.

A deterministic optimal edit-alignment decomposition is 72 substitutions, 44 deletions, and 11 insertions. The tie rule prefers diagonal match/substitution, then deletion, then insertion during backtrace. Other equally optimal decompositions may differ; the 127-error total does not. Per-input and all 24 per-reference-speaker error counts and denominators are in `results.json`. Three inputs contribute 92 of 127 errors (49, 25, and 18).

## Frozen selection and scoring

Inputs were selected before model outputs from internal annotation-silence gaps of at least 0.5 seconds. Endpoints are integer-frame gap midpoints; durations are 10–20 seconds inclusive. Each chosen input contains at least 20% wall time with two or more distinct annotated speakers. Choose earliest start, then earliest end, within each of eight meetings. The 33 eligible candidate pairs and the exact 56 oracle + 8 older continuous + 8 prospective continuous exclusion supports are published as time/ID metadata in `selection.json`. No positive-length intersection is allowed. There is no claim of disjointness from all historical project benchmarks, recording participants, or model training data.

Gold included every nonempty source annotation interval across all speaker tiers wholly inside each outer boundary, preserving fillers and disfluencies. No turn crossed a selected outer boundary. The population contains 59 turns and 33.19 seconds of distinct-speaker overlap (27.54%). Annotation-derived silence/overlap was not separately verified by listening. The source full-recording hashes were inherited; selected source samples, headers, annotation files, frozen inputs, and expected PCM/32768 float32 samples were independently checked exactly.

Chinese normalization is Unicode NFKC, lowercase, removal of Unicode punctuation/symbol categories, then whitespace removal. Units are remaining characters. Within each input and model-local label, phrase texts concatenate in native emission order, not generated timestamp order. The exact reference-subset dynamic program chooses a one-to-one per-input oracle assignment, with missing reference tracks deleted and extra hypothesis tracks inserted. All failed, missing, rejected or truncated inputs retain their entire reference denominator. No global identity claim, across-input label joining, text repair, duplicate-text suppression, or best-view selection is permitted. Rates may exceed 100%.

## Production scope

This was benchmark candidate projection using the pinned production strict parser, `parse_support`, `partial_support`/`partial_phrases`, partial-track validator, and public-track projection. It did **not** exercise full database/API storage, native-trigger integration, overlap discovery, continuous-stream coverage, or persistent speaker identity. No native detection counts or regions were fabricated. Zero-valued production native-count defaults represent unmeasured activity, not detected absence.

Raw structured text is diagnostic and does not become API-admissible because it scores well. Complete canonical envelopes are required; malformed envelopes are not regex-salvaged. Timing admission is separate from lexical error. Coarse phrase timestamps are not word alignment, and whole-input CER on overlap-rich audio is not overlap-only word/character error.

## Reproduction and evidence limits

`portable_scorer.py` contains unchanged production-derived normalization, edit distance, tokenization, and exact per-input oracle-scoring function bodies, plus a small fixed-population wrapper. It uses only Python's standard library and never loads a model. Run:

    python -B -m unittest -v test_evidence.py

Tests cover normalization, missing/failed inputs, extra tracks, label permutations, 1,024 exhaustive tiny matching cases, all-three-view 127/686 summary parity, per-voice counts, metadata selection/exclusion checks, and omitted transcript fields.

The published metadata can verify arithmetic, all candidate duration/overlap thresholds, nonintersection with the listed 72 supports, and earliest choice within the listed 33 candidates. Exhaustively rediscovering candidates and verifying complete gold requires original corpus annotations, which are deliberately omitted. Recomputing the observed CER also requires the omitted gold and generated text. Summary parity tests are **not** a substitute for transcript-level reproducibility. The independent audit recomputed the full private score object exactly before sanitization; evidence digests bind that audit without publishing its contents.

`provenance.json` identifies model/source/runtime hashes and licensing. `LICENSES/` contains the model and runtime license texts as notices; no model, runtime binary, audio, transcript, credential, machine path or process identifier is distributed. AliMeeting-derived selection/results metadata retain CC BY-SA 4.0 licensing, with publisher links, citations, and adaptation details in `ATTRIBUTION.md`. Corpus terms apply when obtaining or regenerating source data. License notices do not grant rights beyond their own scope. Production-derived project code remains subject to the containing project's applicable terms; no broader project license is invented here.

## Execution and interpretation

Exactly eight confirmed generations processed 120.515 seconds of submitted audio without retries. Whole execution took 262.855 seconds. Fresh model/source/runtime pre/post verification passed; independent terminal rehashes and complete score recomputation also passed. All eight worker groups and the scorer have owned-process cleanup receipts; the execution owner separately recorded terminal exit zero.

The total phase cap was 1,200 seconds, with 1,080 seconds for inference admission and reserved bounded postvalidation/scoring/cleanup. Peak owned RSS was 1,575,473,152 bytes; peak observable-namespace aggregate RSS was 1,590,284,288 bytes. The observable aggregate stop was 6 GiB, separate from the 5 GiB owned-process stop, 2 GiB host-available floor, and 10 GiB absolute user ceiling. Namespace visibility is limited and shared pages may be counted more than once; these numbers are not whole-host unique physical memory. Resource details and thresholds are in `results.json`.

The next unanswered evaluation question is native-overlap discovery and coverage on an independently frozen stream protocol. This package does not authorize or execute that experiment, change production defaults, or establish a quality-promotion gate.
