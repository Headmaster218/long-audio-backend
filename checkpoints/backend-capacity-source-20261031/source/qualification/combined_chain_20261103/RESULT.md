# Joint FTPS, native watch and export: bounded negative result

## Outcome

The first actual integration run did **not** complete export. The real loopback
FTPS producer upload and owned-stream completion callback succeeded. The same
bounded watch admitted the input and real native processing completed, producing
five nonempty primary ASR units and two retained primary packets/core outcomes.
The exporter refused a changed transcript revision during its second validation
pass. No `complete.json` was published. Partial files are not a successful export.

This is useful negative integration evidence, not a complete-chain pass. No
product gate was weakened, no stale artifact was accepted and no inference was
retried. No deployed receiver, real Android device/microphone, accuracy, speaker
identity, long endurance or whole-capture qualification is claimed.

## Frozen preparation

Executed source backup: `2d4ca36876f8cde45e631f4751beb9403486375f` on the existing
development branch. The isolated candidate contained 942 files: 935 source files
from `6828077bb327c378ecd9c67154d034066a687500`, the exact accepted two-literal
runtime qualification patch, and seven qualification additions. Production files,
models, default settings, selectors and limits were otherwise unchanged.

Independent prelaunch checked all 303 supplemental pins, five complete inventories
(source 942, runtime 2,122, Java classes 198, fixture dependencies 185 nonbytecode
files, test tools 683), genuine public PCM/STOP metadata, 32 native assets, six
layout files, and exact interpreter/SQLite/unix VFS/device 27 qualification.
The 126 existing adapter/watch tests and 24 new guard controls passed separately.

- Frozen spec SHA256: `ccf75e4cb14da142b5fa807253ecbff626f64e34ab04c80b96427af6223c9081`.
- Independent prelaunch SHA256: `789dcaec9463a16079cbcd8cfa04b2f744404265ab10416e8142fe77a36458c8`.
- Executed harness SHA256: `d57984bfc2d037477dcf89753090c2e508c442daf5578386645a392c9a28e926`.

Import-only preflight caught missing cryptography in the isolated native Python
environment before any launch. Only the already installed cryptography 50.0.0
package and distribution metadata were copied into the private TLS fixture
area. Local RECORD hashes and exact copy identity were verified; official release
archive equality was not established. Native cffi 2.1.1, typing_extensions 4.16.0
and pycparser 3.1 were reused. The fixture install remained 16,122,663 bytes, below
its unchanged 16 MiB cap; no native environment or system trust was modified.
Fresh complete inventories are not claimed equal to unavailable historical ones.

An initial outer launcher failed before exec because `/usr/bin/time` was absent.
No service/model process or run directory was created. Independent review approved
replacing only the timing utility with monotonic/RUSAGE_CHILDREN measurements
around the same GNU timeout and original Popen wait. This was not a native retry.

## Actual wire and watch observations

The unchanged Android production core, FtpsTransfer and FtpsNetwork from
`2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9` uploaded one genuine nine-second STOP
entry. Its PCM is the fixed public AMI interval documented in PLAN.md; source
coordinates and synthetic host metadata remain distinct. One large segment was
used rather than relaxing the fixture's five-control-session limit for nine
separate uploads.

A real marker writer was held after 32 bytes while open. Two empty receiver
scans by the same watch observed no HTTP request or runner/intake mutation.
Releasing the writer led to three actual successful closed-file callbacks and
automatic publication, with zero pending/live writers. Duplicate callback was
idempotent. The harness did not manually publish the delivery.

FTPS used one control TLS session, nine data TLS channels and 868,068 application
wire bytes. The Java client verified source integrity, closed its sockets and
released its callbacks. All three fixture-owned children exited zero, every
recorded listener closed and ephemeral TLS keys/trust files were removed.
Peak sampled fixture subtree RSS was 153,616,384 bytes, below 384 MiB.

The same watch admitted one input and issued 337 requests, including 11 writes,
in 43 polls and 41 helper actions. It reached real completion and began export,
then returned `unresolved`, `server_conflict`, HTTP 409 with no retry. The final
request was a cursor-bound whole-primary transcript GET during the exporter's
second validation pass, after the initial transcript read and intervening
page/chunk status checks. The saved trace did not retain the HTTP error body.

A fresh closed-copy replay of that exact cursor returns
`transcript_revision_changed`; a no-cursor read succeeds at a newer revision.
This strongly explains the observed live 409, but is not a recovered original
response body. The exporter correctly retained partial files and withheld its
completion marker. Do not treat a generic 409 as safely retryable or disable
revision checking to make the test pass.

## Cleanup, scope and follow-up

Outer wall time was 23.206 seconds; child user/system CPU were 25.482/3.395 seconds.
Peak sampled aggregate RSS was 903,299,072 bytes. Native Gate 300 seconds, two CPU
slots, 6 GiB aggregate RSS, 5.5 GiB free-disk floor and 16.25 GiB artifact cap were
unchanged; outer timeout stayed 480 seconds plus 120-second kill grace.
Both parent-owned children exited zero. Native service shutdown exited zero;
worker/stores stopped, lock closed, permit cleared and all shutdown predicates
passed. No matching process remained. Source and runtime pins stayed unchanged.

The parent harness additionally had a completion-poll race: it could observe a
child finishing between its predicate and poll, report `child_early_exit`, and
mask the child's actual returned outcome. This was fixed only in later harness
source, leaving the executed candidate/evidence unchanged. Two deterministic
controls were added, giving 26 passing guard controls. They prove the race fix
and continued refusal when expected terminal evidence is missing, not a second
native run or a completed export.

A subsequent explicit read-only recovery can inspect/export the same completed
job under a separately reviewed plan. Any product handling of transient revision
changes must preserve failed partial evidence, finite budgets, exact typed error
handling and full repeated revision checks. No such recovery, product change or
new native execution is claimed here.

Only source, synthetic tests and this sanitized report are backed up. Public
speech bytes, partial transcripts, model files, databases, compiled classes,
private receipts and keys are excluded. Main remains untouched.
