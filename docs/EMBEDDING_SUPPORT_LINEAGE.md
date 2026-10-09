# Forward-only acoustic support lineage

A transcript correction, speaker rename, or merge is not automatically a review
of all audio used by an embedding. The current extractor can concatenate several
predicted-clean fragments from a whole analyzer window, including context beyond
the displayed utterance. Diarization can itself miss another voice.

`clean_speaker_audio` now emits `pooled_support` in analyzer metadata:

- exact half-open sample intervals in the decoded analyzer input, source frame
  count/sample rate, and total actual pooled samples;
- SHA256 of the selected concatenated mono float32 little-endian PCM, without
  WAV headers;
- at most512 interval entries, with explicit `complete=false` when truncated;
- `purity_status=predicted_from_diarization_unverified` and `human_verified=false`.

This changes metadata only. An independent1,000-case randomized comparison found
byte-identical waveforms/dtypes and unchanged prior evidence fields. It does not
prove that the selected audio contains only one real person.

The pure `project_review_support` helper retains pre/post-core context instead
of clipping it to the corrected sentence. Its origin-based chunk/time projection
is not exact sample alignment to the original compressed source file. Negative
chunk coordinates can refer to an explicitly continuous previous fragment; they
must not be resolved against an unrelated upload.

This is forward-only groundwork, not an enrollment endpoint. The existing Store
still retains only its old duration/overlap/source evidence fields, and the helper
is not wired into worker ownership or speaker registration. Historical pooled
support must remain unknown rather than being fabricated from a segment's times.
The active frozen long-run source does not include these new fields.

A future verified-anchor operation must bind the actual original source/stream,
model version and exact reviewed support, recheck its hash and coverage, store a
separate human decision with retraction lineage, and reject whole-support
certification for incomplete provenance. Merely setting `state=confirmed`, or
renaming an automatically created profile, is insufficient. No automatic template
updates have been enabled by this change.
