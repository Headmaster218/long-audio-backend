
## Existing uploader admission bridge (2026-11-03)

`tests/test_strict_uploader_admission.py` exercises the existing `Uploader`, not a
new upload implementation, against real strict-primary-payload-v2 ASGI handlers
and strict SQLite. A known session is created once by the fixture; the uploader
never posts `/sessions`. A synthetic PCM16 mono16k input of 120 seconds plus 17
frames becomes the normal two bounded pieces at explicit frame origin 71.
Both ordinary admission and a response lost after the first successful source
commit reconcile through GET. A fresh uploader invocation replays its durable
journal, reconstructs identical pieces and observes the same server sources
without another PUT. Input bytes remain unchanged and no recording work exists.

The test transport intentionally raises before `/seal` or `/finish`. Native
lifespan is not entered, no worker/model is started, and no inference, publication,
strict startup qualification, HTTP socket behavior or device provenance is
claimed. This is an admission compatibility test, not a production ingestion
switch. The real transport's bounded HTTP behavior remains separately tested.

## Native startup smoke blocked (2026-11-03)

`tools/strict_uploader_smoke.py` reuses the existing real capacity service and
resource/ownership guards. It is configured for a fresh known-session, eleven
seconds of generated digital silence, and the existing bounded uploader. It
never retries session creation. The work deadline is 300 seconds, followed by
up to 90 seconds graceful shutdown and approximately 21 seconds forced cleanup.
No defaults, qualification pins, primary-only boundaries or model code change.
Generated output must stay local and must not be committed.

The first real launch did NOT reach readiness: native cells reported
`runtime_binary_qualification_mismatch`. No session was created, no synthetic
WAV was generated, and no source, seal, publication or inference was exercised.
The service's original 180-second cell readiness wait expired. Clean application
shutdown was not confirmed (terminal reported worker alive and lock not closed);
the supervisor recorded a failed run and identity-checked forced cleanup with
zero surviving children. This is failure evidence, not a passed native smoke.

Read-only fingerprint comparison isolated the mismatch to sqlite3_package_sha256:
expected `096bb1a3902a75a2c3723f2d7863a8d3e9ed627bd862e6538eca2bc517d6e29b`,
observed `6ac9a15ee2c393bd994e6b3784ed3f5fb97c9b4f986cbce03dccbad6226778df`.
Interpreter bytes/hash/path and the observed built-in binding fields matched.
The package inventory includes Python bytecode caches, so this does not by itself
prove that the SQLite engine changed. No cache was deleted and no pin weakened.

The harness now performs this same read-only binary comparison before starting a
service. A fresh local check refused the same mismatch in about half a second
without a service or instance lock. Two synthetic tests verify missing-profile
and mismatched-profile refusals cannot launch a subprocess. A matching preflight
is still not full qualification: actual startup retains every original check.

Remaining blocker: restore the genuinely qualified runtime or obtain independent
finite requalification evidence under the existing policy. Seal/publication and
recorder-to-backend linkage remain unqualified. Gate's request journal covers its
own calls, not uploader traffic. A missing result is unknown, never success.
