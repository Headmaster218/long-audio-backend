# dev56 AliMeeting monitor limited trial

Date: 2026-10-19 UTC. Source parent: `4d94a1a467e8781839b6b441faad54462d0c9f61`; tree `86ca83c15376fd2b8db8da9f807b785abe08356e`.

## Outcome and fixed scope

The sole actual dev56 attempt ended with exit status 1 after 67.56047 seconds of helper execution. Independent terminal review accepts the saved evidence as a failed, partial measurement. It is not a completed meeting evaluation or an acoustic success. No retry was performed.

The intended fixed evaluation was the full AliMeeting Eval meeting R8001_M8004, physical channel 1: 25,181,600 frames, 1,573.85 seconds, 16 kHz, 16-bit mono PCM. The selected input, order and settings were unchanged. The full target was six pages, 16 sources and 150 work rows, with at most 31 natural observations. This is exposed fixed-corpus evaluation, with no held-out, accuracy or long-recording claim. Input acquisition and provenance remain in [the acquisition report](DEV55_ALIMEETING_EVAL_ACQUISITION.md) and [provenance](DEV55_ALIMEETING_EVAL_PROVENANCE.json).

## Monitor stop and conserved progress

The helper stopped when its process monitor first observed a child already in zombie state. That child had not previously been authenticated. The source rejects this state before reading parent identity, user identity, executable or arguments; null fields mean those reads were not attempted, not that the information was proven unavailable. The child's role, parent and cause remain unknown. Three SQLite busy/refusal log notices do not establish its role or a causal product failure.

The stopped durable state contains:

- 56 of the intended 150 work rows: 28 queued and 28 staging, all at offset zero. All durable work-error fields are null.
- Six sources containing 9,408,000 frames, or 588 seconds, of upload/materialization. Page one was published; page two was pending receipts. EOS was not reached.
- Zero completed work rows, primary provenance packets, primary segments, natural observations, vectors and witnesses.

Upload/materialization is not completed-primary coverage. There is no natural observation or witness denominator from which to infer speaker-identity quality. Published links remain unavailable, with null link metrics rather than a measured zero-link result. Identity is inconclusive; DER and CER were not run.

## Cleanup and evidence integrity

The original server and uploader handles were positively waited to completion, and ordered server lifespan shutdown completed. Cleanup consumed 0.32782 seconds. No member of the observed family remained. For the refused child, helper-side non-child waiting returned a null exit code and numeric absence was subsequently observed. This establishes disappearance of the observed identity; it does not establish its original parent's reap or exit status, or absence of every unobserved descendant.

After cleanup and the pre-reference prediction seal, the actual helper opened and parsed the reference, then completed bounded reference reporting with zero natural observations to score. The passive path did not restart the service and did not establish full coverage. Stopped-original and snapshot typed-row conservation matched across all 65 tables and 452 rows; passive reading also preserved durable rows. Both retained seals matched their 94 and 111 entries. The final input pins matched. Successful reporting and conservation do not convert the failed live attempt into a successful trial.

## Resource observations

Peak sampled RSS was 357,175,296 bytes. Peak sampled charged memory, including reserves, was 1,900,679,168 bytes. These are sampled accounting values, not instantaneous maxima or kernel-enforced hard caps. The largest retained sample gap was about 2.163 seconds.

Peak sampled output allocation was 50,397,184 bytes. The final inventory contained 113 files, 50,159,493 logical bytes and 50,561,024 directory-inclusive allocated bytes. Final allocation is distinct from the sampled peak; the output monitor was not a hard filesystem quota.

## Interpretation and remaining limits

The before-input product predicate correction remains model-free qualified. Its synthetic and installed/package checks, and the helper's separate checks, do not establish actual acoustic quality or the cause of earlier refusals. The prior dev55 partial result, including 420 seconds of completed-primary coverage, seven vectors and zero stored decisions, remains preserved in [its original report](DEV55_ALIMEETING_PRIMARY_ONLY_PARTIAL_TRIAL.md).

This report adds only a factual source checkpoint. It changes no product, evaluation settings or previous result. A robust child-ownership design remains read-only planning; another actual attempt is not authorized.
