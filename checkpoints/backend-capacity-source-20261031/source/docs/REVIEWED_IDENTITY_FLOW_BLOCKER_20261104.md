# Dev68 reviewed-bank flow: operationally blocked

The fixed real-speech collection did **not** complete rollover or produce an
identity-quality comparison. Preserve this result; do not turn unavailable
measurements into zero false matches or a successful quality claim.

## Product and installation

Source checkpoint `6114cccb1c2c3dc208f6ccbeda5c4d81ad85e9ae` adds optional
existing-reviewed-bank inheritance and paired strict-supervisor configuration.
Defaults, model selection, matching policy and original audio handling are
unchanged. The intended guarantee is observed group/generation/policy checks,
not atomic or permanent inference-generation pinning.

- Frozen source: 1,043 files / 15,575,619 bytes.
- Full installed dev68 wheel: 184 product files / 4,296,393 bytes, verified before
  and after the run. Dependency checks passed; no bytecode files were added.
- Final focused regression: 693 tests passed across 22 files; no failures,
  errors or skips. Three deprecation warnings. This is not a full-repository pass.
- Fresh target physical qualification and independent audit passed for this
  cloud host. That does not qualify another server, power-loss recovery, an
  existing-store migration or broad production rollout.

## Fixed speech collection

Public AMI ES2004a Mix-Headset [0,360) seconds was fixed before inference, split
at 180 seconds. Its four participant IDs are disjoint from the previously used
EN2002a/b population. This does not establish exclusion from model training or
generalization to other rooms. Audio and annotations came from the previously
pinned public mirror revision `722d8891643e1e4dc62cfd0d198fa05a1646c3cc`.
AMI Consortium / University of Edinburgh: [corpus](https://groups.inf.ed.ac.uk/ami/download/),
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The unchanged Android writer/spool core produced two 180-second originals with
synthetic host timestamps. The unchanged production FTPS core uploaded both
through one fixed endpoint and read the first attempt back after receiver
generation change. This is cloud replay, not a physical Android microphone test.

The installed service used one native boot and the unchanged strict profile:
SenseVoice, 5.25-second windows, zero context, original native diarization and
speaker model, 60-second observation sampling, and schema-1 voice policy.
Only an empty synthetic research bank was activated. No person association,
voice enrollment, listening attestation or identity merge was submitted.

## Observed outcome

- All **5,760,000 PCM frames / 360 seconds** remain byte-exact in the producer
  and both receiver inboxes.
- The first **2,880,000 frames / 180 seconds** were admitted byte-exact as two
  source pieces and 18 contiguous work chunks. There was no storage-layer frame
  omission or duplication in the independently checked material.
- Ten chunks completed, covering [0,105) seconds. The next chunk has stored error
  `Unavailable: strict_capture_unavailable`; seven chunks remained queued.
- The status GET returned HTTP 503. The coordinator returned the documented
  `transition_paused`, `stop_reason: unresolved` result after 570 requests and
  29 writes. It had not exported the old span or linked the successor session.
- The experimental driver incorrectly rejected exit 3 as an unexpected failure
  instead of retaining a typed paused outcome. Its cleanup stopped the service
  normally. Supervisor exit was 143; no sampled child process remained.
- Runtime was 112.79 seconds; sampled native-tree peak RSS was 879,996,928 bytes.
  These are observations, not hard resource bounds.

Source inspection identifies the recording-status 503 as `StrictMutationBusy`
from strict database admission. The old response/log collapsed archive-owner
rejection and bounded mutex contention into one message. The exact branch
cannot be recovered from this run. A timeout or a particular owner is not proven.
Likewise the retained evidence does not prove whether the failed chunk's capture
error preceded cleanup or resulted from it; do not label it conclusively as an
intentional interruption.

## Quality result and stopping condition

There are three natural certificates, all unavailable candidates. No query
analysis session, live source-authority collection, research reference gallery,
predictions or quality scores were produced. **False suggestions, misses and
false merges are unmeasured, not zero.** Query reference labels were not opened
for scoring. The planned study has stopped on its operational blocker, with no
inference retry, replacement crop, parameter scan or result-driven rerun.

All three saved probe terminals say `child_wait / child_failed`, with normalized
`RuntimeError` and `child_dispatch: possible`. They are child failures, not
recorded similarity-threshold or crop-purity rejections. Stderr was discarded, so
the exact historical child exceptions cannot be reconstructed.

A subsequent constructor-free audit found a separate deterministic deployment
blocker: this Python 3.13 installation's Sherpa extension and two NumPy extension
hashes do not match the sole audited embedding binding, which was established on
CPython 3.12. The existing attestor rejects these bytes before constructing the
extractor. The same cloud computer retains a Python 3.12.14 environment whose
eight audited runtime artifacts and speaker model all match exactly. Its
constructor-free attestation passes; the 3.13 check refuses. This does not prove
the unrecorded historical exception, and it does not make the two runtimes
equivalent. No allowlist or matching threshold was changed. Fresh deployment
qualification is still required before using the supported runtime.

The isolated research scorer is deliberately separate from production human
enrollment: dataset annotations cannot substitute for listening attestations.
Even a successful future comparison would measure crop suggestions, not relabel
transcript speakers or establish cross-boundary ASR context.

## Reproducibility pins

- Source manifest: `82df7715a3ade3cb729648f43c25f56b5c0a149c52ee333b97410a6e7cc573a5`.
- Wheel: `cfa767d514833fbc70b50f0c9e1d40bb46c69360abecaf7975d54fe6b0b4dc00`.
- Materialization receipt: `d21bbf31c5e557824e55e9b0b87e4bbbafa43785e41df8bd50d0652eeb545656`.
- Frozen research plan: `6578e0ee3e84883e3b0b697aac4801fa15870b359f5d64ab4c7b188d512a4201`.
- Pre-inference coordinate addendum: `58fc00cff05d2272d9600d9e1ab99c237063c3b36d1b27baaad530b571cd268d`.
- Collection driver: `912fe02276dfa641c7e152bf4a6d3dbb94e3856be5023a78003b2a36e46aa8ba`.
- Independent failed-collection audit: `c8143f5be38241f96dbaa3fa3615d81ae585122cad776c56e02d9d6b8f3183f0`.

Raw audio, labels, transcript text, vectors, private databases and logs remain
outside the source checkpoint. Original failed state is retained. See
[remaining automation gaps](CONTINUOUS_AUTOMATION_GAPS_ZH.md) and
[reviewed identity handoff](RECORDER_REVIEWED_IDENTITY_HANDOFF.md).
