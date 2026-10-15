# Configuration and reproduction contract

`src/compare.py.in` is a source backup template, not a ready-to-run script. Only private local path/configuration substitutions were made; the separate inverse record reconstructs the executed original byte-for-byte. It remains unconfigured and unexecuted. Historical results apply to the original source hash in `SOURCE_MANIFEST.json`, not an independently tested portable implementation.

## Required fields

All exact placeholder strings and meanings are in the source manifest. They cover:

- Production Python 3.12 runtime and official asset directory, both relative to the experiment directory's parent.
- Private reference directory and the local shared stem for the channel-0 WAV and manual-reference JSON derivative.
- Existing MeetEval 0.4.3 evaluator directory and its previously frozen private source/binary closure pin file and selection prefix.
- Verified full-source WAV byte count and SHA-256.
- Verified exact 17-second PCM16LE and float32LE SHA-256 values.
- Verified private manual-reference JSON SHA-256 and expected per-reference-stream character counts as a JSON object, totaling 70 and 9.

Do not replace input identity checks with unchecked values or weaken assertions. Configure a copy in a new private experiment directory and record its own source hash. Keep the `.py.in` template unchanged as the source checkpoint. The source retains its original Linux affinity, process/resource limits, production import guard, and existing runtime assumptions; it has not been ported or tested in another environment.

## Fixed input geometry

The source helper expects the previously acquired 45.1-second channel-0 extent [422.5, 467.6), with mono PCM16 at 16 kHz and 721,600 frames. Its relative frame slice [232000, 504000) is the exact 17-second global interval [437, 454). Changing the acquisition container/header requires recomputing its full-file byte count/hash while separately proving the fixed PCM and float identities; changing the audio itself is a different experiment.

The private `.reference.json` derivative must provide complete official manual segments with absolute start/end, speaker stream, text, and previously normalized text. The frozen selection requires seven whole segments, 79 characters in two streams, and five overlap spans totaling approximately 2.0267307777 seconds. No segment may cross the selection boundary. This checkpoint does not distribute the reference text.

## Exact held-out source derivation

The first executed source and held-out executed source are separately identified by their SHA-256 values in `SOURCE_MANIFEST.json`. The public checkpoint contains one complete base template plus a finite exact-substitution recipe for the held-out variant, rather than a second near-identical source file.

To prepare the held-out template locally, start from the unchanged `src/compare.py.in` whose hash matches the manifest. Apply the held-out entry's `exact_substitutions` in listed order, requiring each `expected_occurrences` count. Abort on a mismatch and verify the final `derived_template_sha256` and byte count. These substitutions preserve the exact executed held-out source structure after private configuration substitutions are reversed; they are not a newly executed generic runner. Neither public template variant has been executed.

The held-out variant uses source-relative frames [0, 232000), absolute interval [422.5, 437), 14.5 seconds, three complete manual segments, 47 characters with per-stream counts 43 and four, and two overlap spans totaling 1.1076319366295024 seconds. The right annotated margin is only 0.03168 seconds. It changes the fixed window, derived guards/counts, output labels, and duration-derived MOSS context to 783 (audio tokens 182, prompt tokens 271, generation reserve 512, native adaptive ceiling 492). Normalization, official metric functions, native/model algorithms, scoring rules, and resource safeguards are unchanged.

After deriving the unconfigured held-out template, fill the same named path/pin fields in a separate private copy. Use the held-out input's own PCM16 and float32 identities and its own expected speaker-count mapping. The original acquired WAV and manual-reference derivative remain shared, while all source/protocol/dependent pins and check outputs must be regenerated consistently for the new configured experiment. Do not reuse first-window success records or claim that template derivation ran either model.

The second selection was the earliest complete eligible overlapping remainder disjoint from the first interval, chosen from reference intervals before recognition. It is held out only relative to the earlier evaluated window, from the same recording and speakers; it is not an independent corpus sample or a model-training holdout guarantee.

## Private acquisition provenance

The original helper reads private acquisition provenance and invocation evidence. It explicitly checks the historical qualifications that pacing was not enforced and the original metadata-writing invocation was blocked or partial after successful extraction. Preserve these facts when reconstructing that experiment. Do not manufacture a clean receipt or silently relabel the historical acquisition as successful in every respect. A new acquisition with different provenance is a new experiment, requiring an appropriately revised and reviewed source/protocol rather than falsified historical inputs.

## Regenerate source and input pins

The original private `sources.sha256` is deliberately excluded. A locally configured repeat requires a newly generated private pin inventory covering the configured script, private execution protocol, exact source/reference/provenance inputs, required production code, model/native runtime assets, and evaluator source/binary closure. Each line is SHA-256, two spaces, and a path relative to the experiment directory's parent.

The source also reads an existing evaluator-closure pin file and selects entries under a configured trailing-slash prefix. Verify that the selected set is nonempty and includes the required MeetEval 0.4.3 source/binary dependencies. Preserve all original verification obligations. Do not repurpose the public source manifest as proof that those private dependencies were checked.

`PROTOCOL.json` in this checkpoint is a public methodological restatement, not the original private runtime protocol. A configured repeat needs its own frozen private execution protocol and regenerated dependent pins. Do not reuse historical success/check files with new pins, fabricate a passed check, or claim byte-identical execution merely because configuration placeholders were filled.

## Execution sequence and safeguards

The retained source supports a model-free `check` stage and a `run` stage. Checks validate the fixed sample/reference, normalization, whole-segment ordering, unknown-label retention, MOSS context formula, and official metric examples. Run requires identically pinned successful checks before the primary, MOSS, and scoring stages. No check or model stage was executed for this checkpoint.

The original run used CPU affinity 0–1 and nice 19, a shared 780-second work limit plus up to 120 seconds cleanup, sampled combined RSS limit 6 GiB, per-process address space 8 GiB, and an inclusive 8 MiB logical-file budget. Existing MOSS runtime limits remain in force. The source's process sampling is not kernel containment; file accounting uses logical sizes rather than allocated blocks.

Keep all generated audio, text-bearing model/reference/scoring outputs, raw controller data, runtime configuration, receipts, and source/input pin inventories private. Public reporting should contain aggregate counts and appropriately limited interpretation only. This checkpoint neither installs dependencies nor authorizes an additional model run or publication.
