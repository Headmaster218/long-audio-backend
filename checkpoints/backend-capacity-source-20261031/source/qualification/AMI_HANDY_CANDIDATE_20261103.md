# Bounded Handy candidate comparison, 2026-11-03

## Decision

Do not promote Handy or replace the primary backend. On two fresh short labeled
clips, Handy slightly improves one clip and materially worsens the other. Overall
lexical errors are higher. Low-share word retention remains poor in every arm.
This is an exploratory comparison on two related-session clips, not a general
ranking, an identity test or production qualification. No product files, default,
model-selection rule, source gate or service dispatch behavior changed.

## Inputs and frozen design

Fresh AMI Mix-Headset clips: EN2002a starting 1551s and EN2002b starting 565s, both
252,000 frames (15.75s). Their intervals are disjoint from earlier evaluated clips.
Selection used only the pinned human labels before viewing hypotheses. Neither
outer cut crosses a reference-word interval. They contain 40 and 56 reference words,
with 20 and 35 overlapping-word intervals respectively. Low-share speakers are those
individually having at least 10 words and 10–30% of clip words: 11 and 15 words here.
These are two related AMI sessions, not independent-speaker validation.

The manual annotation mirror and scoring conventions are those documented in
[the labeled-context study](AMI_LABELED_QUALITY_20261103.md): human transcript text,
forced-aligned timing, fixed ASCII/NFKC normalization, start-time-ordered serialized
overlap reference and deterministic Levenshtein alignment. Low-share C/S/D is
reference-word retention, not recognition of speaker identity. Overlap ordering,
common words, number conventions and the small sample constrain interpretation.

Three predeclared arms use the same audio/reference:
1. Primary SenseVoice, three 5.25s cores, zero context, existing lexical ownership.
2. Primary SenseVoice, a single 15.75s support. This controls for changing window
   size rather than assuming every difference is caused by model choice.
3. Handy MOSS Q8, one explicit manual candidate invocation for the 15.75s support.

Final pre-inference plan SHA-256:
`a9c68542d7a00ebe299eba1a54329e89486221681a1f361371e1af0bfb8428d7`.
The run's saved plan is byte-identical. Primary language was automatic; synthetic
all-active turns isolate ASR/ownership rather than pretend to test diarization.

## Exact candidate and unchanged safety boundary

The closed `handy-moss-q8_0-cpu-experimental-v1` profile was used. Its existing
runtime verifier accepted 36 exact installed file hashes. The initially absent
model was downloaded from the profile's pinned public revision:
https://huggingface.co/handy-computer/moss-transcribe-diarize-gguf/tree/bfa3d24438711391d8713c6ab0efd6264527757c

The Q8 file has 986,899,616 bytes and SHA-256:
`64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039`.
No other model, dependency or runtime was installed or upgraded.

Both jobs used unchanged MossController and its original OwnedDriver. Each had
one construct, one session and one run attempt. There was no injection, service
registration, automatic retry or promotion. The existing 20-second support cap,
resource guards, parent ownership, raw-before-parse contract and cleanup remain.
The driver retains its 3.5 GiB process/aggregate and 5.5 GiB disk-reserve guards.

Diagnostic SourceSupport binds the original public file, frozen plan and exact
PCM hashes. Its native-runtime-manifest hash identifies an artifact, **not proof
of native source capture**. This caller-supplied diagnostic support is not a
strict-service provenance packet or recording admission claim.

## Results

Errors/reference words, in order: primary chunked / primary whole / Handy:

- EN2002a@1551: **22/40 /27/40 /21/40**. Low-share correct: 2/11 /1/11 /4/11.
- EN2002b@565: **27/56 /23/56 /36/56**. Low-share correct: 0/15 in every arm.
- Aggregate: **49/96 (51.04%) /50/96 (52.08%) /57/96 (59.38%)** serialized-reference
  WER. Low-share matches: **2/26 /1/26 /4/26**.

Handy is not a demonstrated fix for the low-share speech problem. Its small gain
on the first clip does not justify overlooking worse results on the second or
zero matches for that clip's low-share speaker. The second Handy output
contains 14 word insertions under this fixed scoring convention. This challenging overlapping subset
is not representative of all recordings; no broad model-quality claim follows.

Both candidate jobs returned bounded diagnostics without schema errors, aborted
or truncated flags. Original handles were waited/reaped with exit 0, model/session
close receipts were positive and cleanup_verified=true. Cleanup covers observed
owned identities, not proof of exhaustive process discovery.

**EOS and generated token counts remain unknown, complete_content remains false.**
A local hash-bound inspection counted zero non-ASCII letters in both retained
raw candidate outputs; English-specific normalization is still a limitation for
other inputs. Normal return and false truncated flags do not establish a complete transcript.
Scoring the returned canonical text therefore qualifies only that diagnostic
output. Candidate phrase times remain model-generated/unverified; there is no
verified word alignment, persistent speaker identity or end-to-end service result.

## Checks and evidence

- Eight primary adapter calls and two candidate calls, with no retry. Recorded
  experiment elapsed 56.62s. Local summary SHA-256:
  `d67e7c5769d911d95fd457ab270313c0974e6cf2a4d11aaef6569ba5747e84a7`.
- Local summary binds the scorer/ownership/projection source files, exact runtime
  hashes and profile/model identities; no transcript text is in that summary.
- Initial selected tests: 69 passed, 53 subtests passed, one pre-existing synthetic
  process-wiring fixture failed before Popen. Independent unchanged baseline
  reproduced ResourceLimit at start: its default /tmp filesystem is smaller than
  the untouched 5.5 GiB reserve. Original failure is retained, not hidden.
- The fixture's existing GUARD_RUN_DIRECTORY option points its synthetic scratch
  to the workspace filesystem. This changes no guard or product behavior.
  Final selected rerun: **70 passed, 53 subtests passed**. The independent
  saved-result audit verified both cleanup receipts, the frozen plan, all nine
  source hashes, all 36 runtime hashes and every reported metric.

Reproducer: `qualification/ami_handy_candidate_diagnostic_20261103.py --help`.
Run `--plan-only` first and retain that plan. Use an absolute canonical selected
interpreter path, existing pinned assets, and an output directory outside source.
The recorded environment uses two OpenMP/BLAS threads. The outer wall deadline is
850s with 65s kill grace; the unchanged driver has a 300s per-job deadline and its
own cleanup budget. No follow-up retry should be inferred from a failed run.

Only this report, the diagnostic source and its model-free tests belong in Git.
Model weights, raw references, audio, candidate scratch/raw output and local run
reports stay outside the backed-up source checkpoint. Further work should test
better handling of overlapping/minority speech on independently labeled material,
not tune repeatedly on these two clips or weaken completeness/safety gates.
