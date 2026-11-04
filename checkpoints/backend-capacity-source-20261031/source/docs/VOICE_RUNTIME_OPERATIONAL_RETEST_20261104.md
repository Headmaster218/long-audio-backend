# Supported voice runtime: corrected operational attempt

Status: operationally interrupted, not an uninterrupted pass; quality unmeasured.
The failed dev68 study is preserved and is not replaced by this attempt.

## Product and deployment changes

The dev69 checkpoint adds opt-in bounded retries for precisely typed busy recorder
GETs and a constructor-free advisory voice-embedding diagnostic. Writes, uncertain
responses and arbitrary 503s do not gain automatic retries. ASR remains usable
when optional embedding preflight is unsupported. No acoustic allowlist, model,
threshold, crop cadence or sample-selection policy was changed.

A fresh same-host CPython 3.12.14 / SQLite 3.53.1 runtime matches all eight audited
embedding artifacts. Four CP312 wheels are explicit restorations of verified
installed bytes; this is not freshly established official-wheel provenance or a
general installation solution for other servers. A fresh physical SQLite probe,
independent audit and nine candidate checks preceded narrow materialization.
Only the two reviewed target qualification literals were materialized. The full
wheel contains 184 package files; installed verification covered 4,305,639 bytes.
All 32 dependency versions stayed fixed. No bytecode remained.

The final selected regression passed 771 cases, zero failures/errors/skips.
An earlier validation attempt had 770 passes and one test-launcher failure: its
child lacked the test-tool import path. The launcher also omitted child bytecode
suppression. That failed log and the 98 generated cache hashes were preserved;
only generated caches were removed, exact source/dependency bytes reverified,
and the complete selected regression rerun. No product change hid those errors.

## Fixed real-speech attempt

The same public AMI ES2004a first 360 seconds were used, split at 180 seconds.
No new favorable sample or threshold was selected. The new installed runtime used
five-second polling and explicitly bounded busy GET recovery. The attempt stayed
on loopback and did not exercise a phone or independent server.

Verified before interruption:
- Both originals and receiver copies preserve all 5,760,000 PCM frames.
- Four admitted sources and all 36 work chunks match original audio exactly.
- The first span's 18 chunks completed and its export was verified.
- The next session retained the same reviewed identity group.
- The original Android attempt remained readable at its original FTPS path.
- Six natural vector certificates succeeded: five in the first span, one in the
  second. Their vector hashes and original PCM crops were verified. This is
  evidence of actual embedding execution, not identification accuracy.

At about 222 seconds, the private test driver's output-size scan raced removal
of SQLite's transient WAL file and raised FileNotFoundError. Its command cleanup
then cancelled the second-span watcher and stopped the service. The service
exited with normal supervisor termination, no forced outer cleanup or surviving
observed processes. Installed package checks before and after match.

The second span retains one failed/manual chunk and 17 queued chunks. Its accepted
5.25 seconds (84,000 frames) exactly match its retained strict outcome; the source
digest and null attempt nonce support static retry eligibility. Any recovery must
use live formal admission, preserve that cursor, and issue no replacement session,
re-upload or uncertain-write replay. All 395 files were archived byte-exact before
considering recovery. A recovered completion must be labeled recovery, never an
uninterrupted pass.

A separate, not-yet-executed collector assertion was also wrong: database frames
are local to each span, so the second span requires adding the independently
verified 2,880,000-frame original offset. Database coordinates must not be rewritten.

## Quality and unattended-operation limits

No active-HTTP authority collection or complete research snapshot was produced.
False merges, missed identities, DER and WER remain unmeasured, not zero. Restart
may intentionally hide prior-boot certificates; do not bypass that authority fence
to manufacture a quality comparison. Reviewed group continuity does not relabel
transcript speakers, enroll voices, or supply ASR context. Cross-boundary context,
soft/overlapping voices, long duration, hot receiver switching, storage lifecycle
and unattended scheduling remain open. See CONTINUOUS_AUTOMATION_GAPS_ZH.md.

## Evidence hashes

- Frozen source manifest: 8cb7db78878b87aa2631aa5ab9afdfc922779516423c1775ddb308320ac46fe1
- Narrow reviewed basis: 0a5a80c887c05a1ecd1706615b4b38471a6acda29da6147e8755cc2478fe438d
- Materialization receipt: 01d7dd51b52cac0f4c5b2f60c34bdbc04efab478f718d8c8f81d567583e50d6a
- Wheel: e07fcc33794f2a19b739a0933527e4da71b09b13c66d1342c9ab5ce688e0a154
- Final regression XML: aed659f9e9a6bc929149090372baaec51ec783e0260a44fc83c8c9e0b63cc62e
- Failed operational audit: f65c165070f0ff2ef380e4159a728d32e9a5a539b61c820d694fcbda6b28b8e6

Raw audio, transcripts, reference labels, vectors, private databases and run logs
are not included in the repository. This report is not release approval.
