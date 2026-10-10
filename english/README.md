# AMI English far-field ASR screen, v2 (2026-10-10)

Evaluation-only evidence snapshot: 58 oracle-boundary clips, 1,079 reference
words, five meetings, 365.5 seconds per arm. No backend runtime, proof gate,
default, or version change. The inherited root-level AliMeeting snapshot is
unchanged; its README and helpers describe the Chinese evaluation, not this one.

SenseVoice int8 baseline: 249 / 1,079 word errors (23.08% WER).
Qwen3-ASR-0.6B Q5_K_M candidate: 179 / 1,079 (16.59% WER).
Quiet: 102 / 420 versus 81 / 420 (24.29% versus 19.29%).
Ordinary: 147 / 659 versus 98 / 659 (22.31% versus 14.87%).
All five meeting and leave-one-meeting-out pooled comparisons improved;
37 clips improved, 12 tied, and 9 worsened. No failed calls, truncations, or
repetition suspicions. Two English clips produced Chinese-script output under each arm. Both used
automatic language. Raw language labels are reported separately in results.json;
script/content categories must not be conflated with model language labels.

The observed two-arm phase made 116 calls in 331.24 seconds, with 1.365 GiB
peak owned RSS. These are phase resource facts, not isolated inference timing.
Independent review recomputed all 116 word/character edit counts and verified
terminal status, input/source bindings, resource limits, and cleanup. Private
execution receipts are not distributed, so these statements are reported audit
facts rather than independently verifiable public execution attestations.

## Files and offline use

- `protocol.json`: versioned selection, scoring, limits of inference, and amendment
- `selection.json`: official meeting/segment labels and sample bounds, without text
- `results.json`: pooled, stratum, meeting, leave-one-out and clip-level counts
- `pins.json`: public source/model pointers and official corpus asset checksums
- `../runtime.json`: shared verified runtime versions and per-arm settings,
  including automatic language, ITN, context/cap, CPU threads and fresh sessions
- `score.py`: standard-library-only lexical edit-count helper, with synthetic tests
- `ATTRIBUTION.md`: AMI and model attribution and licensing scope

Run `python -B -m unittest discover -s english -p 'test_*.py'` from the
snapshot root. The scorer is an importable helper: `score(reference, hypothesis)`
returns word and character edit counts. It never downloads data, loads a model,
writes transcripts, or certifies execution provenance. Consumers must obtain
licensed AMI annotations/audio independently and reconstruct references from the
named official segments under `protocol.json`. Full annotation selection and
inference reproduction are not automated by this snapshot. The root Chinese
recognizer/preparation CLI must not be used as an English-cohort runner.

## Interpretation and honest limitations

This is a prospectively frozen engineering screen, not a representative random
sample, population noninferiority proof, continuous-audio result, overlap or
diarization evaluation, or Windows/Vulkan evidence. Word timings are forced
aligned: these are annotation-assisted/oracle crops, not manually exact word
boundaries. Some nonverbal competing sound, including laughter, can remain.
ES2003c's quiet selection consists entirely of speaker D. Historical training
exposure is unknown; some meetings appeared in prior repository evaluations.

The earlier v1 five-meeting cohort failed admission and remains distinct failed
cohort evidence. It was not rescued, silently replaced, or pooled into v2. Before
model outputs, v2 screened all 171 official annotated meetings, excluded v1's
five, admitted 115 by annotation sufficiency, and took the first five in one
fixed-seed hash ordering. No alternate seed or output-based meeting swaps.

Official summary durations disagreed with actual RIFF durations. A separately
versioned amendment, reviewed before RMS selection/inference, made verified
RIFF frame bounds authoritative and retained XML timings unchanged. This does
not establish an acoustic offset or alignment correction. All unchanged
admission thresholds still applied. Passing permits further evaluation only;
it does not promote a backend default.

No audio, model assets, reference/prediction text, private identifiers, private
artifact hashes, absolute machine paths, credentials, request headers, or
process/approval logs are included.
