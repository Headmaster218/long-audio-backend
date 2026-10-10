# Frozen support and scoring contract

## Support

Use the first two complete excerpts in recovery/selection.json prospective_B,
in original order: R8001_M8004_MS801 (106.620s, 6 windows) and
R8003_M8001_MS801 (83.355s, 5 windows). The full outer reference turns remain
inside each excerpt. References serialize all gold turns by start, end, tier,
interval, including overlapping speakers. This ordering is a lexical evaluation
convention and is not a diarization result.

Inputs are the unchanged channel 0, 16kHz mono PCM16 crops with original amplitude.
Windows tile each full excerpt:20 seconds, followed by its final short tail.
No VAD, gain adjustment, prompt, history, language override or reference text
is supplied to the recognizer. The model and source pins are in pins.json.

## Observed decoding policies

The feasibility smoke used `-bs 1 -bo 1`; the separately frozen text diagnostic
used `-bs 5 -bo 5`. Both used the following explicit additional native flags:

`-t 2 -p 1 -l auto -ojf -sns -ng -fa -tp 0 -nf -mc 0`

Each window had its own model/input arguments and unique output prefix. Task was
transcribe, never translate. No DTW, prompt, VAD, retry, fallback, splitting or
best-result selection was used. Beam search is internal to the one admitted
invocation; it is not post-hoc selection among model calls.

The historical guard admitted one fresh process per window, at most 11 calls,
300 seconds per call and 3600 seconds overall. It reserved 100 seconds for scoring
and 20 seconds for cleanup, stopping inference at 3480 seconds. Limits were 4 GiB
owned RSS, 6 GiB observable aggregate RSS, 2 GiB available-host reserve and 5 GiB
free disk; two logical CPUs, niceness 19, two inference threads. RSS was sampled,
not a kernel memory reservation. Native files were capped at 2 MiB each, combined
output at 8 MiB per call and total extra data at 128 MiB. Original handles were
waited/reaped before continuation; no owned process remained after terminal audit.
The portable scorer below does not recreate that runtime controller.

## Text and failure handling

Concatenate transcription[].text in native array order, then concatenate windows
in frozen order for each whole excerpt. Do not infer lexical words from tokens,
trim text using token intervals, reorder competing speech, or compare an isolated
partial window to a complete-turn reference. Raw native output was retained for
audit. Native JSON is read with UTF-8 surrogateescape so byte-level token fragments
are preserved; lexical scoring uses the full segment text, not those fragments.

Normalize both texts with NFKC/lowercase, removing Unicode punctuation, symbols
and whitespace. There is no simplified/traditional Chinese conversion, filler
removal or translation. Unit-cost Levenshtein ties prefer diagonal, deletion,
then insertion. Report S/D/I and the fixed whole-reference denominator.

Every missing, aborted or failed window contributes an empty hypothesis while
all reference characters remain. A partial diagnostic may therefore have numeric
counts, but cannot be labeled completed. Interval-invalid and zero-duration
entries are counted separately and never silently remove text. Explicit
truncation statuses are distinct from unknown native completeness.

## Portable scorer input

`python whisper/score.py --input local-input.json --output new-counts.json`

The output path must not exist. The 2 MiB bounded input has schema
whole-excerpt-text-input-v1 and two arrays:

- excerpts: objects with id, reference, reference_characters and ordered window_ids
- records: objects with window_id, status and, for successful/truncated records,
  transcription (the native segment array, each entry containing text)

Statuses are success, truncated, failed, not_attempted or aborted. Missing records
are not_attempted. Failed text is ignored. IDs must be unique and belong to the
supplied coverage. Metadata-only output contains no hypotheses or references.
The helper supports bounded whole-excerpt inputs beyond this particular snapshot;
matching this diagnostic requires its exact 11 windows, two full references and
1,224 normalized reference characters. It verifies lexical contracts, not actual
model execution, source provenance, corpus extraction or decoder completeness.

See test_snapshot.py for synthetic input examples. Corpus text is not bundled.
Reference reconstruction and native execution must be performed separately under
the upstream licenses; do not commit locally generated text or audio.
