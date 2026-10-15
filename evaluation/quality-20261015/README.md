# Quality evaluation checkpoint — 15 October 2026

Source and findings checkpoint for long-audio-backend, covering the completed Chinese crop/context ablation, AMI saved-output reference evaluation, and one-call AMI full-context control. Historical aggregate results have been checked. The product default is unchanged.

## Included

- `QUALITY_REPORT.md`: aggregate findings and their limitations, with no transcript or reference text.
- `REPRODUCE.md`: input contracts, configuration requirements, and reproduction checklist.
- `SOURCE_MANIFEST.json`: original-source and template hashes, plus required configuration substitutions.
- `src/crop_ablation.py.in`: configuration template of the completed Chinese crop/context experiment.
- `src/ami_full_context.py.in`: configuration template of the completed one-call AMI full-context control.
- `src/parse_reference.py.in`: official AMI reference selection adapter.
- `src/normalization.py`: byte-identical fixed normalization helper.
- `src/score_saved_hypotheses.py.in`: adapter for official MeetEval cpWER.
- `src/score_di_companion.py.in`: adapter for official MeetEval greedy DI-cpWER.

## Source status

The `.py.in` files contain explicit unfilled configuration placeholders. They are source templates, not ready-to-run scripts, and have not been executed. Historical experiment evidence applies to the original source files identified by the executed-source hashes in the manifest. Byte-for-byte reconstruction of those originals has been checked separately using inverse substitutions. Static parsing is not a model or scoring run.

The source templates preserve the recorded algorithms and safeguards while removing local run paths and private configuration pins. They intentionally do not include the historical private inputs, model weights, audio, reference word payloads, hypotheses, result receipts, runtime environment, or protocol payloads. Generated transcription and scoring artifacts must remain private unless separately cleared for sharing.
