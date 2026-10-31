# Dev31 mixed-input run: retained partial evidence

This report concerns product commit `871f384c7499f120d4fb330a47845db903c1ecfc`.
The original helper returned exit code 1. The 30-minute mixed-input integration
was incomplete; later inspection does not convert it to a pass.

## Fixed workload and observed progress

The intended workload was 1,800 seconds across Chinese speech, AMI speech,
environmental audio and the existing Chinese demo, using fixed repetitions and
digital silence. The unchanged work budget was 3,600 seconds plus 120 seconds
for cleanup, with a separately bounded emergency cleanup tail. No manual MOSS
job submission or automatic model retry was permitted.

The run stopped after 835.85 seconds while still in the Chinese scope:

- 472.5 seconds accepted; 441 seconds released in 14 pages / 42 work chunks.
- The last observed primary committed position total was 403.52 seconds.
- The last automatic snapshot contained 24 committed results, two interrupted
  candidates, one accepted candidate and two queued candidates.
- All 24 committed results had validated receipts reporting one model
  construction, one session and one run, plus positive owned cleanup.
- 22 results passed the structural timestamp checks. Two retained untimed text
  with an invalid-timestamp flag; structural acceptance does not establish
  transcription or speaker accuracy.
- The other three source scopes and end-of-input processing were not reached.

The helper retained 20 HTTP 503 responses: 16 `cell_owner_unavailable`, two
`jobs_lock_unavailable` and two `result_storage_unavailable`. These counts alone
do not identify the initial storage exception or prove a permanent database
fault.

## Stop condition and retained outcomes

The immediate observer stop was `optional cleanup or publication blocked`.
The final health snapshot showed a live primary worker, one queued primary
request, no executing primary request, an automatic-text permit owner and text
storage pending. Cleanup and speaker-probe pending flags were false. Primary
sampling-pending counters were zero and no primary ownership fault was
reported. The reviewed new sampling-budget recovery branch was therefore not
shown to have executed in this run.

Source inspection identified a storage-maintenance reachability cycle: the
cleaned automatic-text owner was retained pending durable publication, while
the ordinary scheduling route required that owner to be released before
reaching the retry. A separate fresh-fixture candidate tests storage-only
maintenance of the exact frozen result or failure outcome before requesting a
new automatic turn. This report does not assert that candidate has completed
an actual mixed-input run.

After positive shutdown, a bounded diagnostic used a hash-verified fresh copy
of the closed jobs database. The original was not opened by SQLite or changed.
The three relevant copied rows were `no_result`, with fixed failure phase
`wait`, category `AdmissionError`, and reason `process_sample_unavailable`.
Their outcomes reported possible invocation, no validated native receipt,
unknown construction/session/run counts, and verified observed-owned cleanup.
The post-shutdown records do not establish the original publication instant.
These attempts have no replay authorization.

A separate model-free barrier reproduction demonstrated that a competing
in-flight resource census can cause the existing running check to set the
optional cancellation event before that census later completes successfully.
It does not prove this was the cause of any particular historical attempt;
the finer historical subtype was not recorded.

## Timestamp evidence

The rejected pairs were 8.54–18.63 seconds against 18.62 seconds of support,
and 0.01–10.51 seconds against 10.50 seconds of support. Both ends exceed the
support by 0.01 seconds. Neither pair was negative, reversed or non-finite.
The parser preserved the original numbers and retained their text as untimed
alternatives. No endpoint clipping or timestamp repair was performed.

## Resources and cleanup

- Peak sampled owned-tree RSS: 1,900,748,800 bytes.
- Peak plus the explicit unknown-process and Android reserves: 3,242,926,080
  bytes, below the 6 GiB aggregate target.
- 3,318 samples; maximum recorded sample gap: 0.333 seconds.
- Retained output charge: 53,190,656 bytes across 550 entries.
- No resource-limit failure was recorded.
- The owned server received intentional SIGTERM, returned -15, and was
  positively waited and reaped. Orderly shutdown markers were retained; no
  known live owned children remained.

Sampling and observed-family cleanup are bounded observations, not proof of
instantaneous memory maxima or absence of every historically unseen process.
There is no 30-minute pass, full-input completion, real-time performance
qualification, 12–48 hour endurance acceptance, or accuracy claim from this run.
No source audio, transcript text, speaker vectors, private paths or job
identifiers are included in this report.
