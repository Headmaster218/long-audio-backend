# Fresh runtime smoke and partial sampling census

This is an additive development checkpoint based on commit
`5470f08d568da759d3087c2bb2cb907227e8ff9a`. It does not modify the root product,
activate a production feature, merge a branch or publish a release. The runtime
smoke and census controls below are separate qualifications, not an end-to-end
acoustic evaluation of the new instrumentation.

## Runtime recovery and observed smoke

Six pinned download assets, totaling **427,331,627 bytes**, were restored and
hash-verified. They include model files/archives, a model license and the native
runtime archive; they are not six distinct model families. The native runtime
is NeMo-Speech.cpp 0.2.0. The isolated NumPy 2.5.3 wheel matched its exact audited
SHA256, rather than widening the accepted runtime binding to the initially
available NumPy 2.3.5. No binary, model, waveform or transcript is published here.

The separately frozen revision-03 smoke used the existing dev69 crop-policy
candidate, not the newly instrumented census candidate. It processed one
bundled **7.152-second** English development recording through actual public
native diarization and analyzer APIs. The original child was positively waited
with exit 0. The supervisor recorded **10.3227 seconds**, sampled peak combined
RSS **517,390,336 bytes**, no sampling errors, no limit intervention, and
unchanged frozen source/manifest hashes before and after the run.

Observed completed coverage included native diarization, Zipformer tagging,
SenseVoice transcription and nonempty ERes2Net embeddings: a lower bound of
**four executed model families**, not an invocation or accuracy score.
Attestor finish completed, its descriptor validated, and the proven local
embedding-space ID was:

`6a50c1646fcc3edd2fea59828319f21ba41bc871bd34c85fb31a94be92024848`

Both obtained native/analyzer objects reported close completion and cleanup
errors were empty. This proves the implemented local runtime-provenance
contract and a short execution path. It does not prove acoustic purity,
recognition accuracy, cross-day identity, overlap protection, continuous
microphone stability, hard instantaneous RSS bounds or historical environment
restoration. Frozen before/after hashes do not exclude an unseen transient edit.

## Preserved first-run limitation

The earlier smoke completed actual model calls but returned unavailable
embedding attestation with NumPy 2.3.5. Its preparer also edited child, manifest
and README after READY while the root run was in progress. Its original wait
and aggregate result are preserved; the race prevents treating that run as a
clean frozen launch. Independently recovered exact prelaunch files are included
under `smoke-first-recovered/`; their manifest SHA256 is:

`ac2b0bfe287af2aea0b8c6867730e6307ab40cb55df2d292f02d6720f1604964`

Unused revision 02 was not executed. Revision 03 was a separate prospective,
frozen run, not a replacement receipt for the first run.

## Partial sampling-census implementation

The seven-file delta adds a bounded, trial-only observer and optional
synchronous hooks in Worker, native capture and MossJobs. No observer is
installed by default. Product transaction schema, thresholds and identity
permissions are unchanged. Observation can change execution timing; preserving
decision rules is not a counterfactual sampling-equivalence claim.

Independent review identified and the current revision corrected:

1. Guarded optional `fcntl` import: unsupported explicit sink creation refuses
   before creating files; default import remains possible.
2. Creator-PID checks before inherited mutex or descriptor use: a different
   process cannot close, unlock or append through the parent's sink.
3. Parent-directory fsync before initialization acknowledgement; failure leaves
   initialization invalid and preserves unacknowledged evidence.

Fresh synthetic root-run controls passed: **20** at stage 1, **41** at stage 2,
and **45** on current stage-2 revision 03 (4.97 seconds). Those counts are
successive overlapping suites, not 106 independent cases. The PID tests simulate
ownership changes; they are not an actual fork or cross-platform qualification.

The census is deliberately **incomplete**. Eligibility remains UNKNOWN;
terminal transport metadata is UNQUALIFIED_STAGE1; there is no API that can
produce a COMPLETE census. Missing work includes earlier lifecycle failures,
same-snapshot identity/mapping/due state, claim/skip transaction outcomes,
background maintenance and terminal linkage, stopped-snapshot reconciliation,
collector health handling, and real Worker/database equivalence coverage.
`CENSUS_REMAINING_SCOPE.md` retains the full list. Stage 3 was not implemented or
qualified at this checkpoint. Wrapper/unit passes do not authorize an acoustic
completeness claim.

## Other unresolved evidence

The dev69 broad gate remains **211 passed, 2 failed**. Its two isolated reruns
passed; a subsequent three-case storage-budget diagnostic also passed without
reproducing the failure (3.41 seconds). Neither diagnostic erases the original
broad failure or establishes its cause. The previous dev68 fixed-window trial
remains INCONCLUSIVE with no query-label scoring or predictions.

A read-only HEAD request to the official AMI audio endpoint returned HTTP 503.
The exact receipt is included. No new AMI dataset was acquired from that attempt,
and no new cross-day accuracy run is claimed.

## Scope of this publication

The source delta, complete patch, exact smoke launch/child/manifests, safe
aggregate results, positive original-wait receipts, lightweight acquisition
helpers and pinned asset identities are preserved. Model archives, runtime
binaries, WAV files, raw transcripts, labels and private databases are excluded.
The checkpoint is not an assertion that all historical files or qualifications
have been restored. Follow REPRODUCE.md for the exact source baseline and path
layout; do not run the old first-smoke helper as a new qualified test.
