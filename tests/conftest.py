"""Portable model-free fragment test setup; historical failures stay explicit."""
import os

import pytest


# These exact rejected-q1 controls remain executable with unchanged assertions.
# An unexpected pass is a failure requiring review, not automatic q1 acceptance.
_REJECTED_Q1 = {
    'tests/test_fragment_matcher_v2.py::test_frozen_v2_matrix[development-quiet-tone-jitter]',
    'tests/test_fragment_matcher_v2.py::test_frozen_v2_matrix[independent_stress-strong-tone-jitter]',
}


def pytest_collection_modifyitems(items):
    for item in items:
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
