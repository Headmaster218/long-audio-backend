# Goals and acceptance boundaries

The project remains work in progress. Desired capabilities are bounded-resource long-recording analysis, reliable Chinese/English transcription, independent speaker activity, cautious cross-recording identity association, useful sound-event candidates and recoverable incremental uploads.

Each quality claim needs its own frozen inputs, protocol, exact source/model/configuration identity, scored terminal results and stated limitations. Synthetic API tests establish mechanics only. Short CPU runs do not establish long-duration stability, Windows support or GPU memory use.

Do not promote uncertain overlap labels or local diarizer slots into global identities. Automatic matching must not silently update reference templates. New identity-memory ideas require isolated studies and explicit review before integration.

The target resource budget is total service RAM plus dedicated VRAM of 6 GiB, with a 10 GiB deployment ceiling. Polling a CPU process tree cannot prove a hard combined budget; use operating-system controls where appropriate.

See [public test scope](../TEST_STATUS.md). Detailed raw evaluation records remain outside this public source repository.
