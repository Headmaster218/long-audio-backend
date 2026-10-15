# Clean-span pilot: report and protocol regression test

This source-only checkpoint preserves the completed two-person ERes2Net pilot's
factual report and a small reusable test of its scoring protocol. Product defaults,
thresholds, runtime integration, and enrollment galleries are unchanged.

## Files

- `REPORT.md`: byte-identical to the corrected pilot report. Its 26 model-free
  checks, 12 real embedding calls, and 16 cosine scores describe the original run.
- `protocol.py`: byte-exact functions and constants extracted from that run's
  runner: finite-vector validation, two-person scoring, acceptance, grid selection,
  and immutable-gallery enrollment/calibration/evaluation ordering. Imports are
  Python standard library only. This is not a new production implementation.
- `test_protocol.py`: 18 newly authored standalone synthetic unit tests. These
  are separate from the original 26 checks and do not rerun or independently
  reproduce the audio experiment.

Run from this directory with Python 3.9 or newer:

```sh
python3 -I -B -c 'import runpy, sys; sys.path.insert(0, "."); sys.argv = ["test_protocol.py", "-v"]; runpy.run_path("test_protocol.py", run_name="__main__")'
```

The suite covers inclusive thresholds and their immediate floating-point
predecessors, wrong-person outcomes, calibration-only selection, objective and
threshold tie-breaks, finite-score bounds, freeze-before-evaluation ordering,
failed freezes, copied immutable enrollment galleries, and adverse held-out data.
Its numeric fixtures are synthetic; no real audio, recorded embedding vectors,
transcript text, or execution receipts are included.

## Limits

The extracted protocol intentionally retains the pilot's two public dataset
speaker labels, two experimental arms, fixed policy `(0.75, 0.10)`, and nine-point
calibration grid. It is not a general-purpose identity service. The freeze callback
returns a synthetic token in tests; these tests do not establish durable disk
persistence. Decoder, model, geometry checks, normalization, resource accounting,
and original artifact integrity are outside the standalone suite.

The report's hashes identify original evidence; those private artifacts and the
full execution recipe are not included. This checkpoint does not claim cross-day
identity validation, unknown-person rejection, causal seam isolation, or evidence
for changing deployment thresholds. See the report for dataset attribution and
interpretation limits.
