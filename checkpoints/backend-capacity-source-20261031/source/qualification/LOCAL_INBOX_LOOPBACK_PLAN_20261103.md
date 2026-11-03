# Local inbox runner loopback qualification plan

This file describes the bounded test, not a passing native result. The exact
source commit, candidate hashes, runtime/asset pins, output paths and command must
be frozen outside the repository and independently reviewed before launch.

## Scope

`qualify_local_inbox_loopback.py` composes the opt-in
`audio_backend.recorder_inbox_runner` with the unchanged real native strict
service over actual `127.0.0.1` HTTP. The harness explicitly creates exactly one
fresh empty session. The runner is bound to that known session and one selected
span; it cannot create sessions. Arrivals are controlled local filesystem copies,
not an FTPS receiver or Android network transport test.

Input is the previously qualified nine-second public AMI speech fixture, with
`FIXTURE.json` SHA-256
`735895855f8de14f84992216ee7b28fb7359ce74fb30a86aed6ed5e93e80dd56`.
The nine unchanged Java producer deliveries retain epoch 7, sequences 2–10,
frames 32,000–176,000, eight `ROTATION` closures and final `INTERRUPTION`.
Source, license and provenance limitations are in
`PUBLIC_SPEECH_EXPORT_FIXTURE_20261103.md`.

## Scenario and required evidence

1. Bind the runner to the selected origin, private state/intake/output directories,
   and the explicit empty session. Configuration performs no HTTP or intake.
2. Observe an empty inbox, then an incomplete first delivery. The latter must
   refuse without changing durable runner/intake evidence or mutating the backend.
3. Complete the first delivery and add three more. Restart the runner in a fresh
   process after four admitted inputs. Replaying the same paths and a new
   same-content attempt for an already frozen ordinal adds no input.
4. Add the fifth genuine delivery and a disclosed adversarial copy of the same
   ordinal with a single PCM bit and its checksums changed. Both ordinary run and
   explicit attempt selection must refuse the conflict, with unchanged admitted
   counts and durable state. The adversarial copy must never reach the backend.
5. The qualification harness, acting as an explicit test operator, moves only
   that adversarial copy to a private retained-conflict directory and records its
   original/retained paths and unchanged hashes. The runner itself never moves,
   deletes, chooses around, or rewrites conflicting evidence.
6. Add two same-content attempts for the next new ordinal. Refuse ambiguity, then
   admit only the explicitly selected original path. Preserve the alternate copy.
7. Add the three final genuine deliveries. Observe `awaiting_finalize`; do not
   infer EOS from a quiet inbox. Explicitly finalize the genuine `INTERRUPTION`.
8. Poll completed status through fresh runner processes, export through the
   runner, and reopen the completed runner. Independently call GET-only status
   and export into a second private directory. Require nonempty primary speech
   text, exact selected-span mapping, a valid completion manifest with matching
   artifact lengths/hashes, and byte-identical JSON/TXT/manifest files.
9. Verify all original source and admitted local delivery bytes, selected paths,
   conflict copy, source pins, original-Popen waits, service terminal predicates
   and descendant cleanup. Raw transcripts, audio, databases, models and local
   state remain outside source control.

Each phase records expected admitted counts and request traces. No runner phase
may issue `POST /sessions`; only the single harness request is permitted. Refusal
phases must be GET-only and leave runner/intake hashes and mtimes unchanged.

## Unchanged native resource and lifecycle bounds

The candidate is a fresh extraction of the reviewed source checkpoint with only
`accepted_candidate_20261103.patch` applied. Production runtime pins, model
settings, normal service lifecycle and admission gates remain unchanged.
All candidate source files, including `qualification/`, must be pinned before
launch and rehashed afterward. Existing model/runtime asset pins are reverified.

- Existing `Gate`: 300 seconds, two CPU affinity slots, 6 GiB sampled aggregate
  RSS, 5.5 GiB free-disk floor, 16.25 GiB sampled artifact cap.
- Outer launcher: `timeout --signal=TERM --kill-after=120s 480s`.
- Every runner/status/export child is polled under the same Gate and reaped using
  its original `Popen`. On an exceptional child path: five seconds TERM, then
  five seconds KILL if necessary.
- Existing normal service shutdown: 90 seconds, terminal predicates and
  descendant checks. No native retry, old database reuse or service restart.

`test_local_inbox_loopback.py` supplies model-free helper, artifact-integrity and
process-reaping controls. These tests are not network or ASR evidence.

## Excluded conclusions

This is not deployment, an always-on daemon, FTPS receipt, Android/microphone
capture, hardware continuity, abrupt-crash endurance, whole-capture completeness,
transcription accuracy, word alignment or speaker identity qualification. A clean
runner process reopen is not a native service restart. EOS applies only to the
explicit selected span. Real operation still requires separately authorized
machine/destination/access choices.
