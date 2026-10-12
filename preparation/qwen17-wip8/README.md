# Qwen asset preparation checkpoint 8

This is an additive source-only backup. The qualified backend runtime is unchanged. It contains the clock-safe pacing correction and its model-free fixtures; it is not an installed runtime, model qualification, or transcription result.

## Outcome at this checkpoint

Three exact assets were downloaded and verified: one Handy Qwen3-ASR-1.7B Q5_K_M GGUF conversion and the two pinned transcribe-cpp 0.2.3 PyPI wheels already described in checkpoint 7. The three artifacts total 1,541,655,116 bytes. Network and independent disk hashing matched the pinned identities. This checkpoint contains none of those binary artifacts.

The successful acquisition completed in 433.50 seconds. The original process handle was waited and cleanup was confirmed. Peak sampled owned RSS was 44,138,496 bytes; the largest observed sampling gap was about 250 milliseconds. These measurements are sampled evidence, not hard operating-system memory guarantees. Independent acquisition review also streamed and matched all three assets and accepted the original wait, cleanup and retained predecessor evidence.

## Preserved failures and correction

The first attempt failed after retaining 142,374,727 bytes with a generic Refused error. Its precise branch was not retained. A requested-byte pacing defect was established from source and deterministic fixtures, but was not proven to be that attempt's failure cause.

The next attempt failed after retaining 16,336 bytes with ValueError. Source inspection established a race in the inherited pacing helper: a resource check could advance time beyond a short pacing interval before sleep received the remaining duration. The historical exception had no retained traceback, so the match remains an inference.

The helper now recomputes remaining time after the resource check and sleeps only when it is positive. The rest of the helper is unchanged from checkpoint 7. All 27 targeted fixtures passed, including deterministic reproduction of the old negative-sleep race and synthetic small-read response/socket objects through the download path. These were mocked transfers, not live socket tests. Both failed partials and their original receipts remain retained privately; the successful acquisition did not overwrite them.

## Layout and recovery limits

- acquire.py is the exact corrected helper source.
- test_pacing.py is the exact 27-case test source used for its model-free qualification.
- The private launcher, frozen execution specs, local asset lineage, receipts, paths and environment configuration are deliberately excluded.
- Tests refer to the reviewed local harness layout. This source snapshot is not a standalone runnable distribution.
- No credentials, proxy values, signed redirect URLs, audio, transcripts, downloaded models, runtime wheels or raw test logs are included.

Installation, native import compatibility and inference require their own reviewed phases. No model constructor or inference was executed during this acquisition.
