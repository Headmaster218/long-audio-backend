# Pending-probe closure reconstruction checkpoint

This is a NEW source reconstruction after loss of unpublished working files, not recovery of the original 34-file checkpoint or its validation evidence. No release, deployment, long-audio acceptance or accuracy claim is made.

Base: Headmaster218/long-audio-backend commit b459b30d86a2331c447c049f969852df3004e9e6. Follow checkpoints/recovered-source-20261023/README.md to reconstruct the product from its authoritative source overlays and delta, rather than using the older repository root directly.

Before applying this payload, audio_backend/moss_handy_jobs.py must have SHA256 17cc40a79fb358ddf821bbe31166162118aeb5f1e682f2f85f18b4b1286a5b3f. Its replacement SHA256 is 16d8ef2d888e41e724b2a10268c7f1c3663b2ea802e0d38fb85a91ddae67d640. This is a 17-line production change. The new test file must not already exist. The sampling-recovery test replacement adds jobs=None to two existing fake coordinators without changing assertions.

The pending closure checks the same admitted owner, no execution/thread/optional grant, and either an existing expired positive deadline or the exact sticky primary_ownership_uncertain state. It reuses the original durable storage acknowledgement before releasing charges. It does not reset a permit, create a deadline, dispatch a model or retry inference. Recoverable sampling failures and unknown state remain fail-closed unless the existing deadline has expired.

Fresh verification on 2026-10-24: 93 selected tests passed, including 25 newly authored finite controls and existing storage/sampling recovery tests with the explicit fixture correction. The first gate had 89 passes and four fixture failures; those same four failures were reproduced on the unchanged baseline before correcting the two mock fields. Those original failures are not relabeled as passes. This is not the full test suite, real-device validation, real-model accuracy validation or an endurance pass.

Run the new test_reconstructed_primary_pending_closure.py alongside existing test_primary_probe_storage_reconcile.py and the supplied test_primary_sampling_recovery.py against the reconstructed product in a fresh test directory. Preserve runtime dependencies and resource limits from the base. No audio, transcripts, databases, runtime/model payloads, keys or execution logs are included.
