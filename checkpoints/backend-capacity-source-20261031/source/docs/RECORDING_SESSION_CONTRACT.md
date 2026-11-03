# Existing session and source-admission feasibility

## New narrow read-only endpoint

`GET /sessions/{sid}/recording-contract` returns one bounded read-transaction
observation of a live session's persisted processing mode, durable strict policy,
whether chunks exist, and its current capture identity/mode/origin/frontier/state.
It also returns the current source-size limits and existing source-key semantics.
The response uses `Cache-Control: private, no-store`.

The endpoint goes through the existing bearer middleware. **Authentication is
enforced only when `settings.api_key` is configured**, like the other routes.
No new credential, server identity file, persistent access or database schema is
created. `backend_instance_identity` is null and `readiness` is `not_assessed`.
A successful response is not a model/lifecycle readiness check, an empty-session
reservation, a lease, or proof of a particular remote backend instance.

A strict session's policy is read from its durable policy row, not inferred from a
runtime label. Missing strict policy refuses with 409. Missing/deleted sessions
return 404. Legacy sessions explicitly return no strict policy. This lets a future
adapter distinguish actual session state without changing creation semantics.

## Existing API facts verified

1. **Session creation is not idempotent.** Repeating `POST /sessions` with the same
   body and Idempotency-Key creates different session IDs. Do not automatically
   retry a lost creation response or try to recover by matching a nonunique name.
   Use a known existing session until a separate session-creation protocol exists.
2. Page creation replays the same session/capture/page/origin, while conflicting
   origins refuse. A capture belongs to one session; key scope is the
   **session/capture batch**, not a globally unique capture string.
3. Page source acceptance binds a key to the exact page, whole encoded-WAV SHA-256,
   source-start coordinate and format. Identical retries return the existing
   source receipt; changed bytes, placement or page conflict instead of adding
   another source. Optional current page/capture state can still evolve afterward.
4. Sources must be PCM16 mono 16 kHz and at most 120 seconds, subject to tighter
   configured duration/upload limits. The page bound is 28 work chunks, each
   normally 168,000 frames (10.5 seconds), or 294 seconds per full page.
5. A source receipt is not a sealed page, primary processing result or strict
   evidence capture. These tests do not authorize skipping native registration,
   Worker ownership or provenance checks.

## Implications for the recorder adapter

The disabled binding journal's original-source keys are **not ready-made wire
keys** for a whole hour file. An hour exceeds the actual source limit and must
be split into bounded deterministic pieces respecting page geometry. Every piece
needs its own stable parent-source/range/recipe identity, exact saved bytes and
placement. Changed content for the same piece identity must conflict; uncertain
requests must reconcile the same key/body rather than generate new keys.

The current session contract helps verify known session policy/capture state, but
there is still no authenticated backend-instance identifier or atomic reservation
in this read response. A later adapter must recheck immediately before its
mutations and handle state changes through existing conflict rules. It must also
journal exact page-seal bodies/revisions, distinguish published/pending-receipt
states, honor backpressure and preserve unknown outcomes. No adapter is activated
by this change and no new submission defaults are introduced.

## Verification boundary, 2026-11-03

Nine new tests exercise the **actual ASGI handlers and actual SQLite Store** with
real prelocked strict factory ownership. Strict manual-v1 and payload-v2 session
contracts are checked. Tests verify authentication, missing/deleted policy/session
behavior, repeated session creation, exact page/source replay, changed-byte and
placement conflicts, configured source-duration refusal, and staging-file cleanup.
Two existing recording-page tests additionally cover capture-wide source keys,
frozen-page retry and alias replay. All eleven passed.

Crucially, the new handler tests intentionally do **not** enter application
lifespan: no native registration, Worker/native threads, analyzer or model runs.
They do not pretend that production may skip startup. The inherited factory lock
is real and positively released, but this is not a supervisor child-process
handoff or service restart qualification. No page is sealed by the new HTTP tests;
no real recording is uploaded and no external network connection is made.
The existing two component tests separately use their documented generated
recording fixtures, not live strict inference.

The combined selected aggregate contains 266 tests with earlier recorder and
capacity checks. One existing Starlette/httpx deprecation warning remains. No
full-suite, live strict lifecycle, inference, acoustic, real-server authentication
or end-to-end recorder-to-backend qualification is claimed.
