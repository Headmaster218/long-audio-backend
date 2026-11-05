# Bounded automatic receiver admission (dev70)

This opt-in mode removes the receiver stop/restart requirement for capacity-based admission to an already prepared successor generation. It does not create or delete generations, change the endpoint, retry an upload, or advance an analysis session.

Use the existing generation preparation workflow, then add `--auto-advance-generations` to `python -m audio_backend.recorder_receiver_routing`. The output is a new private configuration; `serve --config` still needs explicit startup. The saved `auto_advance_generations` field must be a boolean and requires the bounded generation list. Defaults are unchanged. Retain all generations in the same order; new generations append to the retained prefix. Never edit live routing configuration or remove old namespaces.

## Admission and live uploads

Only a new unique MKD is considered for automatic advancement. The receiver checks raw attempt count, full three-file byte reservation and conservative publication storage capacity before choosing a generation. It scans forward from the current generation, up to the existing eight-generation bound. Only an explicit insufficient-capacity result allows a later generation. Pending publication preparation, unexpected namespace changes, collisions or ownership failures stop admission instead of being treated as capacity exhaustion.

Once selected, the receiver calls MKD creation exactly once. Any exception at that effect blocks further new admission in that receiver instance; it never retries the name in another generation. Existing known readback is unchanged. An accepted attempt reserves the maximum sizes of all three files before the directory effect. Opening each file does not charge again; successfully closed files release only unused byte allowance. Failed, unstarted or uncertain reservations are retained until shutdown.

Existing uploads retain their original owner and can finish while a new connection uploads to a successor. The same FTPS host, port, account, TLS identity and virtual root stay active. Old immutable published attempts remain readable across all generations. There is no directory move, deletion, implicit completion or adoption of write rights.

## Stop, restart and capacity

On explicit restart, the active index is at least the highest generation with any raw attempt directory, including empty and partial attempts. The ordered retained namespace list is the durable evidence; no guessed completion or new progress database is used. Old attempts remain read-only and are not resumed or repaired. A crash before any directory effect may leave no durable advancement and can safely reconsider an unused generation.

All generations full causes refusal before a new directory is created. Raw quota and publication quota remain bounded. The first implementation conservatively counts retained publication bytes plus charged raw bytes plus the next full reserve, so it can advance earlier than strictly necessary. It preserves originals rather than reclaiming space. Operators must provision another explicitly prepared generation, archive under a separately approved policy, or pause capture; this mode never removes data.

Quotas are accounting limits, not physical disk reservations. ENOSPC, filesystem faults, malformed uploads and interrupted TLS can still leave a partial attempt requiring review. Stopping during STOR retains partial bytes but does not guarantee eventual Android delivery. No automatic repair or retransmission is added.

## Verification and scope

Real loopback TLS tests cover an A-generation MKD admitted before B, B completing first, then A completing on its original owner, old readback, restart at B, and exhaustion refusal with unchanged retained bytes. Synthetic failure checks cover full reservation, post-mkdir uncertainty, pending publication ambiguity and legacy configuration behavior. No phone or external server is used.

This is bounded automatic receiver admission, not indefinite storage, an analysis-session daemon, a physical-device delivery guarantee, or long-duration recognition qualification. Existing logical boundary/generation transition remains explicit, and acoustic quality does not follow from transport continuity.
