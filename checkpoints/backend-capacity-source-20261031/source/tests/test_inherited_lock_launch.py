"""Synthetic constructors/lifespans only: no listener, models, decode, or HTTP.

All SQLite/filesystem writes belong to pytest temporary directories. These
controls do not authorize or perform a real recording continuation.
"""
import asyncio
import gc
import json
import os
from pathlib import Path
import sys
import weakref

import pytest

from audio_backend import app as api
from audio_backend import __main__ as server
from audio_backend import recording_uploader as uploader
from audio_backend.settings import Settings
from test_inherited_instance_lock import held, assert_conflict, assert_available, assert_closed, matching_fds

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Explicit Linux/procfs contract')


class ConstructorStop(RuntimeError): pass
class CleanupStop(BaseException): pass


def settings_at(data):
    return Settings(data_dir=data, model_dir=data.parent / 'absent-models', start_worker=False)


def forbid_mutations(monkeypatch):
    calls = []
    def mkdir(*args, **kwargs):
        calls.append('mkdir'); raise AssertionError('mutation before inherited ownership')
    def store(*args, **kwargs):
        calls.append('Store'); raise AssertionError('Store before inherited ownership')
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    monkeypatch.setattr(api, 'Store', store)
    return calls


@pytest.mark.parametrize('target', ['app', 'server', 'uploader'])
def test_wrong_inherited_fd_refuses_before_any_constructor_mutation(tmp_path, monkeypatch, target):
    data = tmp_path / 'data'; data.mkdir()
    other = tmp_path / 'other.lock'
    # The expected lock exists but supplied descriptor belongs elsewhere.
    name = 'uploader.lock' if target == 'uploader' else 'instance.lock'
    expected = data / name; expected.write_bytes(b'expected')
    settings = settings_at(data)
    with held(other) as parent:
        incoming = os.dup(parent.fileno())
        calls = forbid_mutations(monkeypatch)
        if target == 'app':
            with pytest.raises(ValueError): api.create_app(settings, instance_lock_fd=incoming)
        elif target == 'server':
            with pytest.raises(ValueError): server.main(['--data-dir', str(data), '--instance-lock-fd', str(incoming)])
        else:
            with pytest.raises(ValueError):
                uploader.create_uploader(uploader_lock_fd=incoming, state_dir=data,
                    finalize=True, session_id='s', capture_id='c', capture_origin_frame=0)
        assert calls == []; assert_closed(incoming); assert_conflict(other)
        assert sorted(p.name for p in data.iterdir()) == [name]


def test_app_adopts_before_first_mkdir_and_store_failure_closes_only_child(tmp_path, monkeypatch):
    data = tmp_path / 'data'; path = data / 'instance.lock'
    with held(path) as parent:
        before = path.read_bytes(); settings = settings_at(data); seen = []
        mkdir = Path.mkdir
        def guarded_mkdir(directory, *args, **kwargs):
            assert_conflict(path)
            assert len(matching_fds(path)) == 2  # supervisor + adopted child
            seen.append(directory.name)
            return mkdir(directory, *args, **kwargs)
        def failed_store(*args, **kwargs):
            assert_conflict(path); seen.append('Store'); raise ConstructorStop('synthetic Store failure')
        monkeypatch.setattr(Path, 'mkdir', guarded_mkdir); monkeypatch.setattr(api, 'Store', failed_store)
        incoming = os.dup(parent.fileno())
        with pytest.raises(ConstructorStop): api.create_app(settings, instance_lock_fd=incoming)
        assert seen[:2] == ['uploads', 'tmp'] and seen[-1] == 'Store'
        assert_closed(incoming); assert matching_fds(path) == [parent.fileno()]
        assert path.read_bytes() == before; assert_conflict(path)


def test_default_settings_failure_occurs_after_fd_consumption(tmp_path, monkeypatch):
    data = tmp_path / 'data'; path = data / 'instance.lock'
    with held(path) as parent:
        monkeypatch.setenv('AUDIO_DATA_DIR', str(data))
        def fail(): raise ConstructorStop('synthetic Settings failure')
        monkeypatch.setattr(api, 'Settings', fail)
        incoming = os.dup(parent.fileno())
        with pytest.raises(ConstructorStop): api.create_app(instance_lock_fd=incoming)
        assert_closed(incoming); assert matching_fds(path) == [parent.fileno()]; assert_conflict(path)


def test_server_cli_rejects_alias_before_settings_can_resolve_it(tmp_path, monkeypatch):
    data = tmp_path / 'data'; path = data / 'instance.lock'
    with held(path) as parent:
        alias = tmp_path / 'alias'; alias.symlink_to(data, target_is_directory=True)
        incoming = os.dup(parent.fileno())
        calls = forbid_mutations(monkeypatch)
        with pytest.raises(ValueError, match='canonical'):
            server.main(['--data-dir', str(alias), '--instance-lock-fd', str(incoming)])
        assert calls == []; assert_closed(incoming); assert_conflict(path)


def test_server_cli_construction_only_return_closes_both_child_duplicates(tmp_path, monkeypatch):
    import uvicorn
    data = tmp_path / 'data'; path = data / 'instance.lock'
    with held(path) as parent:
        seen = []
        def no_listener(app, **kwargs):
            assert_conflict(path)
            assert len(matching_fds(path)) == 3  # parent, CLI guard, app owner
            assert kwargs == {'host': '127.0.0.1', 'port': 8765, 'workers': 1}
            assert not app.state.instance_lock_lifespan_started
            seen.append(app)
        monkeypatch.setattr(uvicorn, 'run', no_listener)
        incoming = os.dup(parent.fileno())
        server.main(['--data-dir', str(data), '--instance-lock-fd', str(incoming)])
        assert seen and seen[0].state.instance_lock.file is None
        assert incoming not in matching_fds(path); assert matching_fds(path) == [parent.fileno()]
        assert_conflict(path)


def test_server_validation_failure_closes_child_only(tmp_path):
    data = tmp_path / 'data'; path = data / 'instance.lock'
    with held(path) as parent:
        incoming = os.dup(parent.fileno())
        with pytest.raises(SystemExit) as error:
            server.main(['--data-dir', str(data), '--instance-lock-fd', str(incoming), '--port', '0'])
        assert error.value.code == 2; assert_closed(incoming)
        assert matching_fds(path) == [parent.fileno()]; assert_conflict(path)
        assert sorted(p.name for p in data.iterdir()) == ['instance.lock']


def test_app_lock_survives_parent_close_until_successful_lifespan_cleanup(tmp_path, monkeypatch):
    data = tmp_path / 'data'; path = data / 'instance.lock'
    with held(path) as parent:
        app = api.create_app(settings_at(data), instance_lock_fd=os.dup(parent.fileno()))
        parent.close(); assert_conflict(path)
        stopped = []
        def stop():
            assert_conflict(path); stopped.append(True); return True
        monkeypatch.setattr(app.state.worker, 'stop', stop)
        async def lifecycle():
            async with app.router.lifespan_context(app): assert_conflict(path)
        asyncio.run(lifecycle())
        assert stopped and app.state.instance_lock_cleanup_complete
        assert app.state.instance_lock.file is None
        assert app not in api._INHERITED_APPS_PENDING_CLEANUP
    assert_available(path)


@pytest.mark.parametrize('failure', ['stop_false', 'base_exception'])
def test_app_unresolved_cleanup_retains_lock_after_all_caller_refs_dropped(tmp_path, failure):
    data = tmp_path / 'data'; path = data / 'instance.lock'
    baseline = list(api._INHERITED_APPS_PENDING_CLEANUP)
    with held(path) as parent:
        app = api.create_app(settings_at(data), instance_lock_fd=os.dup(parent.fileno()))
        parent.close()
        def stop():
            assert_conflict(path)
            if failure == 'base_exception': raise CleanupStop('synthetic unhandled cleanup failure')
            return False
        app.state.worker.stop = stop
        ref = weakref.ref(app)
        async def lifecycle(item):
            async with item.router.lifespan_context(item): assert_conflict(path)
        try:
            with pytest.raises(CleanupStop if failure == 'base_exception' else RuntimeError):
                asyncio.run(lifecycle(app))
            del app; gc.collect()
            assert ref() is not None and ref() in api._INHERITED_APPS_PENDING_CLEANUP
            assert_conflict(path)
        finally:
            # Synthetic stop never created a worker; explicit fixture cleanup.
            for item in api._INHERITED_APPS_PENDING_CLEANUP:
                if item not in baseline: item.state.instance_lock.release()
            api._INHERITED_APPS_PENDING_CLEANUP[:] = baseline
    assert_available(path)


def uploader_arguments(tmp_path, mode):
    state = tmp_path / 'state'; state.mkdir()
    source = tmp_path / 'source.wav'; source.write_bytes(b'synthetic nondecoded original')
    kwargs = dict(state_dir=state, input=source, session_id='s', capture_id='c', capture_origin_frame=0)
    if mode == 'final': kwargs['final'] = True
    else:
        kwargs.update(append=True, input_id='original-a')
        if mode == 'v2': (state / 'uploader.json').write_text(json.dumps({'policy': uploader.APPEND_POLICY}))
    return state, kwargs


@pytest.mark.parametrize('mode', ['final', 'v2', 'v3'])
def test_all_uploader_modes_adopt_before_mkdir_and_keep_lock_until_run_cleanup(tmp_path, monkeypatch, mode):
    state, kwargs = uploader_arguments(tmp_path, mode); path = state / 'uploader.lock'
    with held(path) as parent:
        before = path.read_bytes(); mkdir = Path.mkdir; seen = []
        def guarded_mkdir(directory, *args, **options):
            assert_conflict(path); assert len(matching_fds(path)) == 2
            seen.append(directory); return mkdir(directory, *args, **options)
        monkeypatch.setattr(Path, 'mkdir', guarded_mkdir)
        incoming = os.dup(parent.fileno())
        item = uploader.create_uploader(uploader_lock_fd=incoming, **kwargs)
        assert seen and seen[0] == state
        expected_type = {'final': uploader.Uploader, 'v2': uploader.LegacyAppendUploader, 'v3': uploader.AppendUploader}[mode]
        assert type(item) is expected_type
        assert_closed(incoming); assert not os.get_inheritable(item.journal.lock.file.fileno())
        parent.close(); assert_conflict(path)
        def fail():
            assert_conflict(path); raise ConstructorStop('synthetic journal load stop')
        item.journal.load = fail
        monkeypatch.setattr(uploader, 'decoder_recipe', lambda *a, **k: {})
        # No network/decode/model can be reached before the synthetic load stop.
        with pytest.raises(ConstructorStop): item.run()
        assert item.journal.lock.file is None; assert path.read_bytes() == before
    assert_available(path)


@pytest.mark.parametrize('mode', ['final', 'v2', 'v3'])
def test_uploader_constructor_failure_consumes_fd_and_preserves_parent(tmp_path, mode):
    state, kwargs = uploader_arguments(tmp_path, mode); path = state / 'uploader.lock'
    with held(path) as parent:
        kwargs['session_id'] = 'invalid/id'; incoming = os.dup(parent.fileno())
        before = sorted(p.name for p in state.iterdir())
        with pytest.raises(ValueError): uploader.create_uploader(uploader_lock_fd=incoming, **kwargs)
        assert_closed(incoming); assert_conflict(path)
        assert matching_fds(path) == [parent.fileno()]
        assert sorted(p.name for p in state.iterdir()) == before


def test_uploader_cli_limits_failure_consumes_fd_before_construction(tmp_path):
    state = tmp_path / 'state'; path = state / 'uploader.lock'
    with held(path) as parent:
        incoming = os.dup(parent.fileno())
        result = uploader.main(['--state-dir', str(state), '--uploader-lock-fd', str(incoming),
            '--session-id', 's', '--capture-id', 'c', '--capture-origin-frame', '0', '--finalize', '--deadline-seconds', '-1'])
        assert result == 2; assert_closed(incoming); assert_conflict(path)
        assert matching_fds(path) == [parent.fileno()]
        assert sorted(p.name for p in state.iterdir()) == ['uploader.lock']


def test_uploader_cli_flag_reaches_exact_owned_journal(tmp_path, monkeypatch):
    state = tmp_path / 'state'; path = state / 'uploader.lock'
    with held(path) as parent:
        seen = []
        def no_ingestion(self):
            assert_conflict(path); seen.append(self)
            self._close_resources()
            return {'synthetic': True}
        monkeypatch.setattr(uploader.AppendUploader, 'run', no_ingestion)
        incoming = os.dup(parent.fileno())
        assert uploader.main(['--state-dir', str(state), '--uploader-lock-fd', str(incoming),
            '--session-id', 's', '--capture-id', 'c', '--capture-origin-frame', '0', '--finalize']) == 0
        assert seen and seen[0].journal.lock.file is None
        assert_closed(incoming); assert matching_fds(path) == [parent.fileno()]; assert_conflict(path)


@pytest.mark.parametrize('failure', ['reader', 'source'])
def test_uploader_unresolved_cleanup_survives_temporary_caller_gc(tmp_path, failure):
    state, kwargs = uploader_arguments(tmp_path, 'v3'); path = state / 'uploader.lock'
    baseline = list(uploader._INHERITED_UPLOADERS_PENDING_CLEANUP)
    with held(path) as parent:
        item = uploader.create_uploader(uploader_lock_fd=os.dup(parent.fileno()), **kwargs)
        parent.close(); seen = []
        class Resource:
            def __init__(self, name): self.name = name
            def close(self):
                assert_conflict(path); seen.append(self.name)
                if self.name == failure: raise CleanupStop('synthetic cleanup interruption')
        item.reader = Resource('reader'); item.source = Resource('source')
        ref = weakref.ref(item)
        try:
            with pytest.raises(CleanupStop): item._close_resources()
            assert seen == ['reader', 'source']
            del item; gc.collect()
            assert ref() is not None and ref() in uploader._INHERITED_UPLOADERS_PENDING_CLEANUP
            assert_conflict(path)
        finally:
            for entry in uploader._INHERITED_UPLOADERS_PENDING_CLEANUP:
                if entry not in baseline: entry.journal.lock.release()
            uploader._INHERITED_UPLOADERS_PENDING_CLEANUP[:] = baseline
    assert_available(path)


def test_default_journal_and_instance_lock_paths_remain_legacy(tmp_path):
    state = tmp_path / 'new-state'
    journal = uploader.Journal(state, uploader.Guard(uploader.Limits()))
    assert state.is_dir() and not journal.lock._inherited
    assert not (state / 'uploader.lock').exists()
    journal.lock.acquire()
    try:
        assert (state / 'uploader.lock').read_bytes() == b'0'
        assert_conflict(state / 'uploader.lock')
    finally: journal.lock.release()


@pytest.mark.parametrize('outcome', ['return', 'base_exception'])
def test_server_cli_retains_incomplete_lifespan_owner_even_after_guard_closes(tmp_path, monkeypatch, outcome):
    import uvicorn
    data = tmp_path / 'data'; path = data / 'instance.lock'
    baseline = list(server._INHERITED_APPS_PENDING_CLEANUP)
    refs = []
    with held(path) as parent:
        def incomplete_lifespan(app, **kwargs):
            # Synthetic uvicorn return/interruption after start, without any
            # worker or listener. Direct real lifespan paths are covered above.
            app.state.instance_lock_lifespan_started = True
            parent.close(); assert_conflict(path); refs.append(weakref.ref(app))
            if outcome == 'base_exception': raise CleanupStop('synthetic uvicorn interruption')
        monkeypatch.setattr(uvicorn, 'run', incomplete_lifespan)
        incoming = os.dup(parent.fileno())
        try:
            args = ['--data-dir', str(data), '--instance-lock-fd', str(incoming)]
            if outcome == 'base_exception':
                with pytest.raises(CleanupStop): server.main(args)
            else: server.main(args)
            gc.collect()
            assert refs[0]() is not None and refs[0]() in server._INHERITED_APPS_PENDING_CLEANUP
            assert len(matching_fds(path)) == 1  # CLI guard is closed, app survives
            assert_conflict(path)
        finally:
            for app in server._INHERITED_APPS_PENDING_CLEANUP:
                if app not in baseline: app.state.instance_lock.release()
            server._INHERITED_APPS_PENDING_CLEANUP[:] = baseline
    assert_available(path)
