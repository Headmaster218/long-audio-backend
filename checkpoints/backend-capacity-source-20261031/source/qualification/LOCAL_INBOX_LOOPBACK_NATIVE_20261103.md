# Local inbox runner: public-speech loopback qualification

## Result

One independently reviewed, explicitly confirmed **native** run passed. Nine
one-second genuine host-Java recorder deliveries arrived incrementally in a local
inbox. The opt-in runner composed existing incremental intake, completed status
and private transcript export over actual `127.0.0.1` HTTP to the unchanged strict
native backend. The output contains **five nonempty primary ASR units**. A second
fresh GET-only export has byte-identical JSON, TXT and completion manifest.

The runner remains a bounded opt-in reconciler, not an installed service or an
always-on daemon. The test harness supplied arrivals and invoked fresh runner
processes. The harness, not the runner, created exactly one isolated empty
session before configuring the runner. There was no FTPS receiver, remote
provisioning, device access, credentials setup or deployment.

## Input and preserved boundaries

The public AMI Consortium / University of Edinburgh EN2002a Mix-Headset excerpt
is the same CC BY 4.0 fixture described in
[`PUBLIC_SPEECH_EXPORT_FIXTURE_20261103.md`](PUBLIC_SPEECH_EXPORT_FIXTURE_20261103.md).
Its fixed `FIXTURE.json` SHA-256 is
`735895855f8de14f84992216ee7b28fb7359ce74fb30a86aed6ed5e93e80dd56`.

All nine admitted deliveries preserve their actual Java-produced bytes: epoch 7,
sequences 2–10, original frames 32,000–176,000, eight `ROTATION` closures and final
`INTERRUPTION`. They comprise 144,000 mono 16 kHz frames, or nine seconds. Backend
time begins at zero only for this selected span. Original unknown gaps remain
unknown and EOS means selected-span closure only. No epoch, missing sequence,
producer START, whole-capture completeness or physical continuity was inferred.
Host replay with synthetic observation timestamps is not Android/microphone or
hardware timing evidence.

## Exercised behavior

- Empty inbox: no input or network action. Incomplete marker-last arrival:
  explicit refusal, zero HTTP requests and no changed runner/intake evidence.
- Four admitted inputs followed by a clean runner process reopen: same-path
  replay and a new same-content attempt for a frozen ordinal added no input.
- A valid adversarial copy at the next ordinal, differing by one PCM bit and
  corresponding checksum declarations, was refused both normally and with an
  explicit attempt selection. This disclosed test copy was never admitted or
  uploaded, and is not claimed as a genuine producer output.
- The explicit qualification operator moved only that copy to a private
  retained-conflict directory, preserving its bytes and logging both paths and
  hashes. The runner never moved, deleted, rewrote or automatically chose around
  the conflict. All selected original paths remained present.
- Two same-content attempts for a new ordinal required explicit selection of the
  original attempt. The alternate attempt remained preserved.
- The terminal delivery led to `awaiting_finalize`, not inferred EOS. Only the
  separate explicit finalize invocation published the nine original sources.
- Sixteen fresh runner observations saw pending work followed by completed
  analysis. The runner exported privately; a further fresh runner process
  verified the retained export without new network requests. Independent status
  and export processes were GET-only and left source/intake inventories unchanged.

There were **34 distinct, successfully reaped child processes** and **365 traced
child HTTP requests**. The sole child mutations occurred during explicit
finalization: one page creation, nine source PUTs and nine identical frozen seal
requests. No runner process created a session. Refusals preserved the exact
admitted count, runner/intake hashes and modification times.

## Native output and independent evidence

Real SenseVoice primary ASR and native Nemotron diarization executed. There was
no synthetic transcript insertion, model substitution or optional transcript
replacement. The independent closed-copy audit bound all five exported primary
units to canonical database rows and two retained native packets/core outcomes,
including exact source/core PCM coverage and source receipt linkage.

The private result is retained outside source control in the rev02 envelope:

- `run/export/transcript.txt`: 1,167 bytes, readable timed primary text with the
  existing uncertainty and speaker-scope notices.
- `run/export/export.json`: 10,533 bytes, original primary text, canonical
  identities, selected-span mapping, conservative timing envelopes and completion
  evidence.
- `run/export/complete.json`: 718 bytes, published last, with independently
  checked artifact lengths and SHA-256 hashes.
- `run/export-repeat/`: all three files match the first export byte for byte.

Directories are private and output files are mode 0600. No raw transcript,
reference text, audio, database, model or local run state is included in Git.
The independent audit opened only closed database copies and verified that all
original database and sidecar bytes, sizes, mtimes and ctimes stayed unchanged.
It checked the saved child wait/request evidence rather than trusting only the
harness success flag. Saved wait receipts are historical evidence, not a second
execution of the waits.

The reusable independent verifier and controls are in
`qualification/local_inbox_native_audit_20261103/`. All **37 verifier controls**
passed on this evidence: 26 model-free and 11 closed-copy controls. The candidate
harness also passed 15 focused model-free controls. These are separately scoped
runs, not a full-repository test result.

- Independent audit JSON SHA-256:
  `6921988992866f2a41b989eb0cb7f53de027979a9ff0dd1ec412c156f34d609a`.
- Audit controls log SHA-256:
  `fdc0674c6366638a6958febc0258d89afbecf3a18ce20eadf0e3d7a681e29ac9`.
- Verifier source SHA-256:
  `1e65d4db4a2ca190cfd57087845580771d3c11997c6544744e8c21445cd6dced`.
- Verifier controls SHA-256:
  `bc1f2ef9ce111a7f73ffee67fcfc0638bccb1a040843de64af1ed340d34bb0ad`.


ASR text is an observed result, not an accuracy score. Complete content, word
alignment, global speaker identity and whole-capture completeness remain
unverified. This run covers one short selected span and one backend page, not
long-duration capacity, abrupt runner-crash endurance or native-service restart.
Model-free/ASGI crash and lost-response tests have separate scope.

## Source, lifecycle and resources

Executed checkpoint: `3bd9e1c80d8aa381478d4d5d941ff0a02645a909`.
Executed source subtree: `982c21137e10a190add7d905a248d03b6a0e0776`.
The fresh candidate contains 898 pinned source files, 14,182,822 bytes. Its sole
difference from that source is the already accepted two-literal patch in
`audio_backend/native_owned_cell_store.py`. All candidate files, including
qualification sources, were rehashed unchanged afterward. Production pins,
main, models, model settings, normal service lifecycle and admission gates are
unchanged. The run used a fresh database and no native retry.

- Frozen plan SHA-256:
  `796648c06fc472efe42786efdee771a2249d8159ffff81595f416fec215fe1e7`.
- Launcher SHA-256:
  `327edd9baf3a2bec0a57cd9ad8f87e4538b10ca03952caae2ecfcc34c77cc727`.
- Independent prelaunch review SHA-256:
  `a0a7f39be18e1bf8577d3b4f842e62c24299d9f2a700e649cca794e060b68f4b`.
- All-file candidate pin manifest SHA-256:
  `b7e0c757da26baae6e8fd82ea5d258cb57ca49bf2f35ad25a5a2c6e685c5f93b`.
- Executed harness SHA-256:
  `d38b5c6426ec9de87f0c671f092c720f5877062477701a3503bcc65c87f65d65`.
- Executed runner SHA-256:
  `c615babf1127f5e37b2dc27a6eb53276a3c733ab172d18392c5276c37781431d`.
- Accepted runtime patch SHA-256:
  `3a97845bf13b00e402ce73cb9990ce72f2696b9d82254cf442fbfde18a46afe8`.

All 2,157 pinned model/runtime assets, 733,515,621 bytes, were reverified before
launch. Runtime fingerprints and filesystem device 27 matched the accepted
candidate qualification. Unchanged limits were the 300-second Gate, two CPU
affinity slots, 6 GiB sampled RSS cap, 5.5 GiB free-disk floor, 16.25 GiB sampled
artifact cap, 480-second outer timeout with 120-second TERM grace, and 90-second
normal service shutdown wait. Every child stayed under the same Gate and had an
original-`Popen` wait; exceptional child cleanup retained the existing five-second
TERM then five-second KILL bound.

Observed wall time was **21.446 seconds**; user CPU 23.858 seconds and system CPU
2.781 seconds. Peak sampled aggregate RSS was **899,428,352 bytes**. Before the
closed-copy audit, the envelope occupied 5,619,712 allocated bytes and had
59,667,492,864 free filesystem bytes. These are sampled observations, not kernel
quotas. Outer launcher, service and all 34 child waits exited zero. Every shutdown
predicate was true, the worker stopped, stores and lock were safe, permits were
clear, and no service/runner process remained.

## Preserved initial preflight failure

The first authorized launcher, for checkpoint `b7c82f291e2c34deb160166608b72eb2757b816c`,
refused in 0.291 seconds **before creating its run directory, service or database**.
Its harness treated empty managed `.git` marker directories as real repositories.
The actual runner/exporter already used a gitfile-or-HEAD check. Only the harness
guard and its regression tests changed to match that established behavior; real
repository output remains refused. The failed candidate, plan and original wait
receipt were preserved. The revised source and fresh candidate received a new
independent exact-plan review and explicit confirmation before the single native
run described above. This was not an unreviewed native retry.

## Reproduction

Use the documented private compatible workspace and a fresh sibling candidate
and output envelope. Extract the exact checkpoint source subtree, apply only
`accepted_candidate_20261103.patch`, and pin all files. Reverify runtime, asset
and fixture hashes, confirm the candidate guard accepts the actual intended
output and still refuses an actual repository, and retain independent review
plus explicit launch confirmation. Never widen limits to force success or reuse
an old database.

The frozen launcher invokes:

```text
timeout --signal=TERM --kill-after=120s 480s \
  <qualified-python> -I -B <candidate>/qualification/qualify_local_inbox_loopback.py \
  run <fresh-private-output>/run --assets <pinned-assets.json> \
  --fixture <pinned-public-speech>/FIXTURE.json
```

Unset `PYTHONPATH` and `PYTHONHOME`, use umask 077 and retain
`PYTHONDONTWRITEBYTECODE=1`. Exact private paths, argv, all source pins and raw
local evidence are retained in the reviewed rev02 launch envelope. The reusable
scenario is described in
[`LOCAL_INBOX_LOOPBACK_PLAN_20261103.md`](LOCAL_INBOX_LOOPBACK_PLAN_20261103.md).
For real operation, machine, receiver/backend destination and access still need
separate choices and authorization. This qualification does not deploy them.
