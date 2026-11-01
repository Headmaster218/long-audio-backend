# Bounded synthetic process-boundary reopen — Nov1

This milestone changes tests and protocol documentation only. It closes the
previously unselected model-free Worker handoff case and adds a real sequential
writer/reader process boundary. No production code, models or packages changed.

## Protocol and provenance

The parent starts a fresh writer with the current Python executable and explicit
local test/product paths. The writer takes the actual InstanceLock, confirms a
second lock cannot enter, and uses the existing generated two-second component
fixture to commit and archive support. Store, SQLite, publication and cold-reader
APIs are original; only the documented component frontier witness is synthetic.
Generated PCM and source/work receipts are internally checked, not real recordings.

A bounded receipt binds product/test source and fixture hashes, Python/SQLite
versions, policy, generated support identity and expected WAV/unit hashes. It does
not exhaustively attest installed third-party dependencies or a native runtime.
The writer checks original owners positively cleaned, releases its lock and exits.
The parent positively reaps it before starting the reader with a new exec; no
Python Store/SQLite/resolver object is inherited. A numeric PID may legally be
reused after reaping and is not treated as proof of process identity.

The reader reacquires the same actual lock, rechecks source/policy/runtime identity,
opens the Store, checks SQLite integrity/FKs and exact cold-audio bytes, confirms
PCM remains cold and archive bytes unchanged. It asserts nonzero read/SQL charges,
unchanged original deadline and hard refusal of an exhausted original SQL budget.
Reader cleanup and positive exit are independently required before a pass.

## Timeout and cleanup review

Only fixed trusted child programs are launched, with no descendants or shell.
The watchdog signals only its unreaped Popen-owned child, never a process group,
process-name search or arbitrary PID. On timeout it terminates, waits at most two
seconds, escalates to kill if required, and waits another two seconds. Unknown
cleanup/exit is explicit failure retaining the original exception as cause. An
uncertain original Store cleanup is not retried or relabeled successful.

Two controls use a startup-ready handshake and prove both ordinary SIGTERM/reap
and SIGTERM-ignore/kill/reap paths. Readiness itself has a two-second timeout.
The two data children each have a 20-second watchdog. Trusted finite test children
produce only a small status line or a finite error traceback; successful output
size is checked. There is no general-purpose untrusted subprocess runner here.

The timeout and ownership design received independent read-only review before
execution; its source-binding, error-cause and escalation feedback was incorporated.

## Limits

This qualifies graceful generated component data reopen across actual processes,
not crash/power-loss recovery, full HTTP service restart, real native inference,
real audio/speaker accuracy, mobile hardware behavior or long-duration endurance.
The existing Worker handoff test uses original Worker/CellStore threads but
synthetic decoder, numerical transport and analyzer; it is not a real-model test.

## Fresh results

Targeted new process-boundary and watchdog cases: 3 passed in 3.60 seconds.
Final combined archive/payload/quota/reopen/packetless/admission/priming suite,
INCLUDING the previously unselected original Worker handoff case and the three
new tests: 145 passed in 34.48 seconds, no deselections. This is the selected
142-existing-plus-3-new aggregate, not the complete repository test suite.
