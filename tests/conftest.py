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


# CPU provider tests attest the loaded interpreter/module closure. Unrelated
# NumPy testing helpers imported by earlier tests are intentionally not admitted
# provider code, so this module runs unchanged in one fresh pytest process.
# The Qwen candidate module similarly measures caller-plus-child RSS against its
# original 256 MiB fixture cap, independently of earlier pytest allocations.
import json
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time

import psutil
from _pytest.reports import TestReport

_CPU_MODULE = 'tests/test_fragment_cpu_provider.py'
_ISOLATED_MODULES = {_CPU_MODULE, 'tests/test_qwen_candidate_store.py'}
_CPU_REPORT_ENV = 'LONG_AUDIO_TEST_CPU_REPORT'
_CPU_RESULTS = pytest.StashKey[dict]()


class IsolationError(RuntimeError):
    pass


def _validated_cpu_reports(config, data, nodes, exit_code):
    """Only complete original reports can become parent test results."""
    if exit_code not in (0, 1):
        raise IsolationError(f'Isolated pytest exited unexpectedly: {exit_code}')
    try:
        reports = [config.hook.pytest_report_from_serializable(config=config, data=row) for row in data]
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise IsolationError('Malformed isolated report payload') from exc
    grouped = {node: [] for node in nodes}
    for report in reports:
        if not isinstance(report, TestReport) or report.nodeid not in grouped:
            raise IsolationError('Unexpected isolated report or node')
        # Pytest's serializer permits tuples, while JSON transports arrays.
        # Restore the public report shapes expected by terminal/JUnit consumers.
        if isinstance(report.location, list):
            report.location = tuple(report.location)
        if report.skipped and isinstance(report.longrepr, list):
            if (len(report.longrepr) != 3 or not isinstance(report.longrepr[0], str)
                    or type(report.longrepr[1]) is not int or not isinstance(report.longrepr[2], str)):
                raise IsolationError('Malformed isolated skip representation')
            report.longrepr = tuple(report.longrepr)
        grouped[report.nodeid].append(report)
    observed = []
    for node, rows in grouped.items():
        phases = [row.when for row in rows]
        if not rows or phases not in (['setup', 'call', 'teardown'], ['setup', 'teardown']):
            raise IsolationError('Missing, duplicate or unordered isolated phases: ' + node)
        if (rows[0].outcome == 'passed') != ('call' in phases):
            raise IsolationError('Isolated setup outcome and call phase disagree')
        if any(row.outcome not in {'passed', 'failed', 'skipped'} for row in rows):
            raise IsolationError('Unknown isolated outcome')
        observed.extend(rows)
    if reports != observed:
        raise IsolationError('Isolated test order differs from selected node order')
    if exit_code != int(any(report.failed for report in reports)):
        raise IsolationError('Isolated exit status and reports disagree')
    return grouped


def _live_cpu_child(child):
    try:
        return child.is_running() and child.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _cpu_group_members(pid, created):
    # A departed leader's session/group remains identifiable while members live.
    # Never signal a reused leader PID belonging to a newer unrelated process.
    try:
        if psutil.Process(pid).create_time() != created:
            return []
    except psutil.NoSuchProcess:
        pass
    members = []
    for child in psutil.process_iter(['pid', 'create_time']):
        try:
            if (child.info['create_time'] >= created and os.getsid(child.pid) == pid
                    and os.getpgid(child.pid) == pid):
                members.append(child)
        except (psutil.NoSuchProcess, ProcessLookupError, PermissionError):
            pass
    return members


def _run_cpu_report_process(argv, cwd, env, report_path, log_path, timeout=900):
    """Bound reporting and reap only this child tree on failure or timeout."""
    owned = {}
    with log_path.open('wb') as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        start = time.monotonic()
        created = psutil.Process(process.pid).create_time()
        try:
            while True:
                try:
                    root = psutil.Process(process.pid)
                    for child in [root, *root.children(recursive=True)]:
                        owned[(child.pid, child.create_time())] = child
                except psutil.NoSuchProcess:
                    pass
                if log_path.stat().st_size > 2 * 1024**2 or (
                        report_path.exists() and report_path.stat().st_size > 4 * 1024**2):
                    raise IsolationError('Isolated reporting exceeded its byte bound')
                if time.monotonic() - start > timeout:
                    raise IsolationError('Isolated test module timed out')
                if process.poll() is not None:
                    break
                time.sleep(.05)
            exit_code = process.wait()
            for child in _cpu_group_members(process.pid, created):
                owned[(child.pid, child.create_time())] = child
            if any(_live_cpu_child(child) for child in owned.values() if child.pid != process.pid):
                raise IsolationError('Isolated test module left live descendants')
        finally:
            members = _cpu_group_members(process.pid, created)
            if members:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            for child in members:
                try: owned[(child.pid, child.create_time())] = child
                except psutil.NoSuchProcess: pass
            for child in owned.values():
                try:
                    if child.is_running(): child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.wait()
            psutil.wait_procs(list(owned.values()), timeout=5)
    if not report_path.is_file():
        raise IsolationError('Isolated pytest produced no reports')
    try:
        data = [json.loads(line) for line in report_path.read_text().splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise IsolationError('Malformed isolated pytest reports') from exc
    return exit_code, data


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    destination = os.environ.get(_CPU_REPORT_ENV)
    if destination:
        report = outcome.get_result()
        serialized = item.config.hook.pytest_report_to_serializable(config=item.config, report=report)
        with open(destination, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(serialized) + '\n')


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    module = item.nodeid.split('::', 1)[0]
    if os.environ.get(_CPU_REPORT_ENV) or module not in _ISOLATED_MODULES:
        return None
    config = item.config
    if _CPU_RESULTS not in config.stash:
        config.stash[_CPU_RESULTS] = {}
    if module not in config.stash[_CPU_RESULTS]:
        nodes = [entry.nodeid for entry in item.session.items
                 if entry.nodeid.split('::', 1)[0] == module]
        try:
            with tempfile.TemporaryDirectory(prefix='cpu-pytest-isolation-') as directory:
                root = Path(directory)
                report_path, log_path = root / 'reports.jsonl', root / 'pytest.log'
                env = dict(os.environ, **{_CPU_REPORT_ENV: str(report_path)})
                argv = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                        '-o', 'tmp_path_retention_policy=failed', *nodes]
                exit_code, data = _run_cpu_report_process(argv, config.rootpath, env, report_path, log_path)
                config.stash[_CPU_RESULTS][module] = _validated_cpu_reports(config, data, nodes, exit_code)
        except (IsolationError, OSError, ValueError) as exc:
            # Missing/crashed/truncated children must fail every affected item,
            # never synthesize a pass or silently omit unreported selected nodes.
            config.stash[_CPU_RESULTS][module] = {
                node: [TestReport(nodeid=node, location=item.location, keywords={},
                                  outcome='failed', longrepr='Module test isolation failed: ' + str(exc),
                                  when='call', sections=[], duration=0)] for node in nodes}
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    for report in config.stash[_CPU_RESULTS][module][item.nodeid]:
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
