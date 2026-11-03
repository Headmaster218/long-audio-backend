
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
