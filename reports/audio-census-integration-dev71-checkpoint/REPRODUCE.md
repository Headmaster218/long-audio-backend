# Recoverable source and finite gate selection

## Base and delta

1. Retrieve published commit `c2094c1813392801f3e74b2fa767bff269108da7` of
   Headmaster218/long-audio-backend. Follow the dev70 checkpoint's REPRODUCE.md to
   recover its stage-2 revision-03 census candidate from the earlier dev69 base.
   The repository root itself is not that candidate.
2. In a fresh copy, check every SOURCE_DELTA.json before_sha256; null requires a
   missing file. Copy its seven source/ files to identical relative paths and
   verify every after hash. Preserve all previous source and run evidence.
3. Alternatively, from that reconstructed dev70 candidate root:

   git apply --check /path/to/dev71-from-dev70.patch
   git apply /path/to/dev71-from-dev70.patch

   The full patch contains new-file bodies. Source payload hashes are the
   authoritative identity; a new environment must validate before execution.
4. The separate terminal-release-fixture-only.patch is not part of the delta.
   Apply it only to separate fresh baseline/candidate comparison copies when
   reproducing those two fixture-scoped tests. Do not imply unadapted tests pass.

## Revised six-case integration gate

Use the reconstructed combined source and fresh output directory. The prepared
integration fixture's original command and bounds are retained in
INTEGRATION_FIXTURE_REVISION.md. Root execution used disabled automatic pytest
plugins, no bytecode/cache writes, a 2 GiB virtual-address ceiling, a 180-second
outer timeout and the existing dependency directories. Tests are:

    tests/test_trial_sampling_census_worker_integration.py

The revised delayed cases use an original-primary-span stop callback with a
30-second case deadline. Preserve output and cleanup assertions; an outer kill
must not be treated as proven teardown.

## Exact final selected production regression

Run from repaired stage-3 revision-02 source, or the combined candidate whose
product bytes are identical. Use fresh temporary output; the recorded run used:

```sh
ulimit -v 2097152
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=../audio-test-tools-20261022:../audio-recovery-deps-20261022:. \
timeout --signal=TERM --kill-after=10 180 python -m pytest -q \
  -p no:cacheprovider \
  --basetemp=../audio-census-stage3-rev02-regression-20261022/tmp \
  tests/test_primary_live_window_sampling.py \
  tests/test_primary_sampling_recovery.py \
  tests/test_primary_commit_admission.py \
  tests/test_primary_history_admission_boundaries.py \
  tests/test_primary_history_startup_recovery.py \
  tests/test_worker_claim_busy.py \
  -k 'not test_worker_handoff_has_bounded_recovery_without_model_readiness and not test_persistent_budget_failure_keeps_primary_progress_and_recovery_backoff and not test_real_app_admits_primary_after_multi_page_drain and not test_worker_survives_contention_recovers_once_or_cancels'
```

This selection produced 107 passes and ten deselections. Exclusions are explicit
limits, not successful qualifications. The inherited stub/interface exclusions
and separately unrun real-app gate must be handled before claiming broader
coverage. New runtime versions or dependencies require separate identity
recording; no historical environment restoration is implied.

## Data boundary

AMI receipt URLs and hashes identify freshly acquired inputs. No audio, archive
or annotation content is included. Labels remained unopened. Do not interpret
these receipts as permission to resample the closed dev68 trial or as evidence
that a new accuracy trial has run.
