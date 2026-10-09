# Boundary assembly

Independent uploads remain independent. Only genuinely adjacent parts of one uninterrupted waveform may share an explicit stream ID. Closing a stream must reflect a real end of input.

ASR uses bounded core windows with context. Lexical ownership uses native timing and neural speech-gap cuts rather than inventing word-level alignment. Events and simultaneous raw speaker turns use interval intersection. Native diarization has additional batch/frontier buffering and may legitimately wait for more real context.

Recovery replays work that was not transactionally committed; it is not a sample-perfect speech or timestamp guarantee. Synthetic boundary tests exercise ownership and state handling, while real model quality requires separate evaluation.

See [API continuous-stream contract](API.md) and [queue behavior](QUEUING_AND_CANDIDATE_LEDGERS.md).
