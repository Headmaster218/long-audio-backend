"""Worker liveness is explicit; disabled administration is not a worker failure."""
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.worker import Worker


@pytest.mark.parametrize('enabled,alive,status',[(False,False,'ok'),(True,True,'ok'),(True,False,'degraded')])
def test_health_distinguishes_enabled_alive_and_disabled(tmp_path,monkeypatch,enabled,alive,status):
    settings=Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False)
    app=create_app(settings)
    settings.start_worker=enabled
    worker=app.state.worker
    monkeypatch.setattr(worker.thread,'is_alive',lambda:alive)
    worker._terminal_error_class='RuntimeError' if enabled and not alive else None
    client=TestClient(app)  # No lifespan entry: no threads or models started.
    try:
        response=client.get('/health');assert response.status_code==200
        value=response.json()
        assert value['status']==status
        assert value['worker_enabled'] is enabled and value['worker_alive'] is alive
        assert value['worker_error_class']==worker._terminal_error_class
    finally:client.close()


def test_health_sanitizes_terminal_metadata_and_keeps_cleanup_precedence(tmp_path,monkeypatch):
    settings=Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False)
    app=create_app(settings);settings.start_worker=True
    monkeypatch.setattr(app.state.worker.thread,'is_alive',lambda:False)
    app.state.worker._terminal_error_class='PRIVATE exception body /secret'
    app.state.fragments.cleanup_error=True
    client=TestClient(app)
    try:
        value=client.get('/health').json()
        assert value['status']=='blocked'
        assert value['worker_error_class']=='UnclassifiedError'
        assert 'PRIVATE' not in str(value) and '/secret' not in str(value)
    finally:client.close()


@pytest.mark.parametrize('failure', [RuntimeError('private message'),KeyboardInterrupt('private interrupt'),
    type('private class /secret',(Exception,),{})('private message'),
    type('A'*65,(Exception,),{})('private message'),type('私密',(Exception,),{})('private message')])
def test_terminal_capture_preserves_exception_and_cleanup(failure):
    calls=[]
    def run():calls.append('run');raise failure
    fake=SimpleNamespace(settings=SimpleNamespace(strict_primary_enabled=False,
        _native_execution_permit=SimpleNamespace(abandon_automatic_turn=lambda:calls.append('permit'))),
        resident_analyzer=SimpleNamespace(close=lambda:calls.append('close')),_run_loop=run,
        _terminal_error_class=None)
    with pytest.raises(BaseException) as caught:Worker.loop(fake)
    assert caught.value is failure and calls==['run','close','permit']
    name=type(failure).__name__
    expected=name if name.isidentifier() and name.isascii() and len(name)<=64 else 'UnclassifiedError'
    assert fake._terminal_error_class==expected
    assert 'private message' not in fake._terminal_error_class


def test_cleanup_exception_remains_terminal_and_original_is_context():
    original=RuntimeError('body private');cleanup=ValueError('cleanup private');calls=[]
    def run():raise original
    def close():raise cleanup
    fake=SimpleNamespace(settings=SimpleNamespace(strict_primary_enabled=False,
        _native_execution_permit=SimpleNamespace(abandon_automatic_turn=lambda:calls.append('permit'))),
        resident_analyzer=SimpleNamespace(close=close),_run_loop=run,_terminal_error_class=None)
    with pytest.raises(ValueError) as caught:Worker.loop(fake)
    assert caught.value is cleanup and caught.value.__context__ is original
    assert fake._terminal_error_class=='ValueError' and calls==['permit']


def test_normal_loop_exit_keeps_error_class_absent():
    calls=[]
    fake=SimpleNamespace(settings=SimpleNamespace(strict_primary_enabled=True,
        _native_execution_permit=SimpleNamespace(abandon_automatic_turn=lambda:calls.append('permit'))),
        _run_loop=lambda:calls.append('run'),_strict_retire=lambda:calls.append('retire'),
        _terminal_error_class=None)
    Worker.loop(fake)
    assert fake._terminal_error_class is None and calls==['run','retire','permit']
