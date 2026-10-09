"""Portable model-free fragment test setup; historical failures stay explicit."""
import os
from pathlib import Path

import pytest


# These exact rejected-q1 controls remain executable with unchanged assertions.
# An unexpected pass is a failure requiring review, not automatic q1 acceptance.
_REJECTED_Q1 = {
    'tests/test_fragment_matcher_v2.py::test_frozen_v2_matrix[development-quiet-tone-jitter]',
    'tests/test_fragment_matcher_v2.py::test_frozen_v2_matrix[independent_stress-strong-tone-jitter]',
}


_PRIVATE_POSITIVE_EVENT_TESTS = {
    'test_actual_development_selection_and_matcher_transport',
    'test_full_event_only_bridge_model_free',
    'test_model_load_failure_persisted_and_same_receipt_cannot_retry',
    'test_compute_failure_keeps_durable_partial_calls',
    'test_terminal_reporting_failure_preserves_primary_and_ceiling',
    'test_reservation_persistence_failure_prevents_model_call',
    'test_compute_primary_survives_bridge_evidence_write_failure',
}
_PRIVATE_PACKAGER_TEST = (
    'tests/test_fragment_model_bridges.py::'
    'test_private_real_output_admission_and_packaging_exclusion'
)


def pytest_collection_modifyitems(items):
    root = Path(__file__).resolve().parents[1]
    for item in items:
        if item.nodeid == _PRIVATE_PACKAGER_TEST and not (root / 'scripts/package_source.py').is_file():
            item.add_marker(pytest.mark.skip(
                reason='Private source/evidence packager is excluded from the public export; public gate exclusion is tested separately',
            ))
        if (item.nodeid.startswith('tests/test_positive_event_seams.py::')
                and item.name in _PRIVATE_POSITIVE_EVENT_TESTS):
            if os.environ.get('FRAGMENT_POSITIVE_DEVELOPMENT_OPT_IN') != '1':
                item.add_marker(pytest.mark.skip(
                    reason='Private development PCM/cache-dependent case requires explicit FRAGMENT_POSITIVE_DEVELOPMENT_OPT_IN=1; assets are not distributed',
                ))
            elif not (root.parent / 'audio-backend/reports/fresh-transport-benchmark-research/development-baseline-v1/PREPROCESSED.json').is_file():
                item.add_marker(pytest.mark.skip(
                    reason='Opt-in private development PCM/cache metadata is missing from the required local source binding',
                ))
        if item.nodeid in _REJECTED_Q1:
            item.add_marker(pytest.mark.xfail(
                strict=True, raises=AssertionError,
                reason='Rejected historical q1 policy accepts frozen tone+jitter abstention controls; p1 is separate',
            ))


@pytest.fixture(scope='session', autouse=True)
def fragment_results_directory(tmp_path_factory):
    if 'FRAGMENT_RESULTS_DIR' in os.environ:
        yield
        return
    destination = tmp_path_factory.mktemp('fragment-results') / 'matrix'
    destination.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('FRAGMENT_RESULTS_DIR', str(destination))
        yield
