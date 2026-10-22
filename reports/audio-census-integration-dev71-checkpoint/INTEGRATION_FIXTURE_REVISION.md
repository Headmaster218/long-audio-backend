# Real Worker census integration revision 02: bounded fixture scheduling

SOURCE ONLY, NOT EXECUTED. Original six-case gate is preserved as timeout exit
124 at 180 seconds with only four dots; it is not a six-pass result and shutdown
was not proven. Original source and artifacts remain unchanged.

High-confidence static diagnosis: _cohort requests a fair automatic handoff at
its end but, unlike the production Worker loop, its next primary_span has no
handoff pump and no stop callback. The first delayed case retained the old pending
probe then began a second cohort without closing that scheduling opportunity.
_grant_primary_locked refuses new grant while _handoff is non-null, so the
fixture could wait indefinitely. No model or SQL diagnosis is claimed from dots.

Revision 02 changes only the test module. It uses the production automatic_turn
context to close the first scheduling opportunity without submitting the old
probe. Ownership and reservation remain unchanged; the old probe is completed
only after the second real Worker window. This is explicit fixture scheduling,
not a production scheduler change, token overwrite, widened product deadline,
retry-until-pass, or disabled resource check.

The delayed cases now have a 30-second absolute case deadline propagated into
the original primary_span stop argument. That original wait releases its intent
and raises InterruptedError when canceled. Every major phase prints a marker;
expiry prints the phase and fixed permit/pending-state scalars. No database reads
or external state changes occur in that diagnostic. Pytest faulthandler_timeout
adds a diagnostic thread dump if another unbounded edge is encountered; the root
outer gate remains the final 180-second containment. Normal real lifespan and
probe cleanup assertions are unchanged. The 30-second stop does not itself
prove bounded arbitrary teardown or cleanup after an outer kill.

Base is the root's audio-census-worker-repaired-20261022 combined source (stage3
repair rev02 plus original tests). Production bytes are unchanged from that base.
The original stage3 from-first-version issue and inherited mock fixture defects
remain documented in the preceding artifact README. All six original semantic
assertions remain; at most two synthetic 10-second recordings per case.

## Root-only proposed command

From audio-census-worker-integration-rev02-20261022, using a fresh basetemp:

    timeout --signal=TERM --kill-after=5s 180s bash -c 'ulimit -v 2097152; export PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=../audio-test-tools-20261022:../audio-recovery-deps-20261022:.; exec /opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3.12 -m pytest -vv -s -o faulthandler_timeout=40 -p no:cacheprovider --basetemp=/tmp/census-worker-integration-rev02-root-20261022 tests/test_trial_sampling_census_worker_integration.py'

No Python, tests, models or SQL were executed by this preparing worker. The full
source, delta against root's combined source, test-only payload, source manifest
and checksums are frozen together. Any further edits require a new revision.
