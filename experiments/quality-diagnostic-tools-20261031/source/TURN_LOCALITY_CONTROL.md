# Prospective native-turn locality control

Use ALL frozen predicted native turns in original order, independently padded
by 0.35 seconds each side and clamped to input endpoints. Convert boundaries
with Decimal and outward floor/ceil to the 16 kHz grid. This reuses only the
padding amount, NOT production union/midpoint cropping. No gold input,
reference-boundary selection, threshold changes, or discarded windows.

Fresh process per window; one raw SenseVoice transcription using identical
config (auto language, ITN, as-is script), then close. Record detected language.
No second crop decode, no native model invocation. Preserve separate outputs;
never concatenate them into an improved score or replace the original result.
Two CPU affinity, 4 GiB address-space / 3 GiB sampled process-tree RSS caps,
300-second computation + 60-second cleanup, external 365-second deadline each.
Require positive observed cleanup before next window. Fail once, stop, no retry.

Freeze outputs before lexical inspection. Post-hoc reference inspection may
identify complete utterances covered by each window, but supplies no model
input or output ownership. Report all five windows, duplicate acoustic coverage,
partial-reference limitations and detected language. More content does not
establish correct speaker attribution; a negative result weakens locality as
an explanation but does not prove separation is the only possible remedy.
