"""Bounded isolated imports; no subprocess, global module deletion, or model."""
import builtins
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from audio_backend import trial_sampling_census as installed


def test_module_import_without_fcntl_preserves_no_observer_path(tmp_path):
    original = builtins.__import__
    seen = []
    def restricted(name, *args, **kwargs):
        if name == 'fcntl':
            seen.append(name)
            raise ModuleNotFoundError("Synthetic platform without fcntl")
        return original(name, *args, **kwargs)
    # This private module is never inserted into sys.modules. Existing imports
    # remain untouched; the import override lasts only for executing this source.
    spec = importlib.util.spec_from_file_location('_isolated_census_portability', installed.__file__)
    module = importlib.util.module_from_spec(spec)
    with patch.object(builtins, '__import__', side_effect=restricted):
        spec.loader.exec_module(module)
    assert seen == ['fcntl']
    assert module.fcntl is None
    with module.window_observation(None, {}, 0., 1., None):
        assert module.observe_commit(lambda:True) is True
        assert module.observe_callback(lambda:7) == 7
    with pytest.raises(module.CensusError, match='unsupported_observer_platform'):
        module.TrialCensus(tmp_path / 'unsupported', run_id='b' * 64, epoch=0, manifest={})
    assert not (tmp_path / 'unsupported').exists()
    assert installed is not module
    worker_spec = importlib.util.spec_from_file_location(
        'audio_backend._isolated_worker_portability', Path(installed.__file__).with_name('worker.py'))
    worker = importlib.util.module_from_spec(worker_spec)
    def worker_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'trial_sampling_census' and level == 1:return module
        return original(name, globals, locals, fromlist, level)
    with patch.object(builtins, '__import__', side_effect=worker_import):
        worker_spec.loader.exec_module(worker)
    assert worker.window_observation is module.window_observation
    assert callable(worker.Worker)


def test_sink_fails_before_any_files_on_missing_fcntl(tmp_path):
    with patch.object(installed, 'fcntl', None):
        with pytest.raises(installed.CensusError, match='unsupported_observer_platform'):
            installed.TrialCensus(tmp_path / 'unsupported', run_id='b' * 64, epoch=0, manifest={})
    assert not (tmp_path / 'unsupported').exists()


def test_inherited_process_rejected_before_mutex_and_sink_io(tmp_path):
    import hashlib
    values = {key:'a' * 64 for key in installed._MANIFEST_HASHES}
    values.update(source_frames=16000, origin_frame=0, start_frame=0,
                  end_frame=16000, interval_frames=960000, sampling_policy='test')
    values['observer_sha256'] = hashlib.sha256(Path(installed.__file__).read_bytes()).hexdigest()
    sink = installed.TrialCensus(tmp_path / 'owned', run_id='b' * 64, epoch=0, manifest=values)
    fds = dict(sink._fds)
    class ForbiddenMutex:
        def __enter__(self):raise AssertionError('inherited mutex acquired')
        def __exit__(self, *args):raise AssertionError('inherited mutex released')
    with patch.object(installed.os, 'getpid', return_value=sink._creator_pid + 1), \
            patch.object(sink, '_mutex', ForbiddenMutex()), \
            patch.object(installed.os, 'write') as write, \
            patch.object(installed.os, 'fsync') as fsync, \
            patch.object(installed.os, 'close') as close, \
            patch.object(installed.os, 'open') as opened, \
            patch.object(installed.os, 'listdir') as listed:
        assert sink.healthy is False
        assert sink.append('WINDOW_BEGIN', 'c' * 64, {}) is False
        sink.invalidate()
        sink.close()
        sink._close_fds()
        for call in (write, fsync, close, opened, listed):call.assert_not_called()
    assert sink._fds == fds
    assert sink.failure == 'inherited_process'
    assert (tmp_path / 'owned' / 'events.jsonl').stat().st_size == 0
    sink.close()  # Back in creator PID, normal diagnostic close is permitted.


def test_parent_directory_fsync_failure_never_acknowledges_initialization(tmp_path):
    import hashlib
    import os
    values = {key:'a' * 64 for key in installed._MANIFEST_HASHES}
    values.update(source_frames=16000, origin_frame=0, start_frame=0,
                  end_frame=16000, interval_frames=960000, sampling_policy='test')
    values['observer_sha256'] = hashlib.sha256(Path(installed.__file__).read_bytes()).hexdigest()
    identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    original = os.fsync;parent_calls = []
    def fail_parent(fd):
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) == identity:
            parent_calls.append(fd)
            raise OSError('synthetic parent directory fsync failure')
        return original(fd)
    sink = installed.TrialCensus.__new__(installed.TrialCensus)
    with patch.object(installed.os, 'fsync', side_effect=fail_parent):
        with pytest.raises(OSError, match='parent directory fsync failure'):
            sink.__init__(tmp_path / 'unacknowledged', run_id='b' * 64, epoch=0, manifest=values)
    assert len(parent_calls) == 1
    assert sink.healthy is False
    assert sink.failure == 'initialization_failed'
    assert sink._fds == {}
    assert (tmp_path / 'unacknowledged' / 'events.jsonl').stat().st_size == 0
