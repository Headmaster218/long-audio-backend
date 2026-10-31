# Experimental bounded Qwen recovery

Version 0.2.0rc2.dev13 adds a pure candidate-only planner, atomic assembler and
injected execution seam in `audio_backend/qwen_recovery.py`. Default SenseVoice,
existing candidate CLI, timed text, speaker identity and production APIs are
unchanged. Native/CLI integration is **not implemented**. No quality acceptance,
release promotion, or generalization claim follows from these synthetic tests.

`prepare` requires explicit `enabled=True`, an original validated 320000-frame
16 kHz PCM receipt, canonical native status 18 OUTPUT_TRUNCATED, confirmed true
truncation and no receipt inconsistency. It preserves the original request/native
output and derives two disjoint 160000-frame children with zero context, identical
frozen model/config, exact source spans and PCM/float hashes. Short tails stay
unresolved. The arithmetic helper covers odd intervals but does not admit them.
There is no recursion, cap increase, text cleanup, deduplication or best-of choice.

`execute` accepts a shared `CallBudget`, trusted `invoke`, `check_resources`, and
mandatory `verify_material` callbacks. Budget includes original calls and reserves
both extra calls once per parent without refunds. The original absolute deadline
is never extended. A trusted external controller must persist plan/reservations
across restarts, observe fresh parent/child sessions, independently verify actual
model/runtime bytes and exact dispatched input, and enforce hard process timeout,
RSS/CPU/artifact limits. The synchronous seam cannot kill a blocking callback.
A receipt hash or JSON boolean is not evidence that a native executor told truth.

`verify_material(expected_request, exact_child_pcm, receipt)` must independently
verify these execution facts and return exactly True. Missing, false, nonboolean
or raising verification cannot replace text. Request/raw arguments are copies.
The model-free tests inject a synthetic verifier; they prove no native behavior.

Only two complete, untruncated, verified child results replace parent text, joined
literally in source order. Otherwise parent remains unresolved and all bounded
returned child raw evidence is retained, including on controller exceptions.
Original failure remains separate even after replacement. Outputs describe input
support only, never word times, speaker labels, confidence or recognition truth.
No repetition detector is present in the inherited native contract; unknown flags
are rejected rather than claiming an unverifiable repetition metric.

The source-only audit ceiling changes from 3400000 to 3420000 bytes for the new
module/tests/documentation/proof. The 250000-byte per-file cap, privacy exclusions,
exact allowlist and historical public-only source proofs remain enforced.
