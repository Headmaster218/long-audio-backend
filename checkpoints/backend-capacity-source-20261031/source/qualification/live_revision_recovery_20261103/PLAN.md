# Controlled revision-race qualification

These are model-free, in-process ASGI tests of the opt-in inbox runner export recovery. They do not open a listening socket, restart production, execute models, replay inference, or claim a historical failed export succeeded.

The fixture reads the retained completed job and copies an already closed database copy into a fresh temporary directory. A new synthetic completed runner stage is constructed from the retained input/finalize ledger, with a fresh output destination and recomputed production ledger hashes. This is not a continuation of the historical runner state. Real ledger and local job validation remain enabled. Original capture, uploader journal, source receipts, PCM and partial exports remain unchanged.

The host uses the hash-checked snapshot qualification builder's actual production GET handlers and transcript implementation. The only test-controlled write increments sessions.transcript_revision in the disposable database between the first transcript page response and the cursor validation request. The latter must actually return HTTP 409 with the exact production transcript_revision_changed response. No mock response or fabricated exception is used; the test transport only maps actual ASGI response status and body to the normal product HTTPFailure contract. Every request must be GET.

Three controls:
- Default recovery disabled: the real revision conflict refuses completion and preserves partial output.
- Opt-in two attempts: the first attempt's partial output remains; a fresh second attempt reads a new revision and commits five nonempty canonical units covering 144000 frames. A subsequent runner invocation verifies the completed receipt without HTTP and without changing retained failed-attempt output.
- Persistent conflicts: both permitted attempts receive real revision conflicts, the budget is exhausted, no third attempt or success receipt exists.

The tests require AUDIO_RECOVERY_EVIDENCE_ROOT to point to the explicitly retained evidence workspace. The source manifest is generated and checked for this model-free test invocation; it is not a claim of historical source identity. The original run's complete file hashes, sizes, modification times and change times are compared before and after. A cooperative write-denial guard also protects the original run and source tree. All test database writes are confined to the fresh temporary copy, with the original database never opened by SQLite.
