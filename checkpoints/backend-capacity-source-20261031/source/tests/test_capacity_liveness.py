import importlib,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
run=importlib.import_module('capacity_run')


def setup(tmp_path,monkeypatch):
    clock=[1000.]
    monkeypatch.setattr(run.time,'time',lambda:clock[0])
    monkeypatch.setattr(run.time,'monotonic',lambda:clock[0])
    gate=run.Gate(tmp_path,147,3600)
    (tmp_path/'ready.json').write_text('{}')
    def health(**updates):
        value={'schema':'actual-worker-health-v1','time':clock[0],'monotonic':clock[0],'worker_alive':True,
            'maintenance_failed':False,'strict_cleanup_failed':False,'current_chunk':False}
        value.update(updates);(tmp_path/'worker_health.json').write_text(json.dumps(value))
    health();return gate,clock,health


def test_dead_worker_not_hidden_by_fresh_heartbeat(tmp_path,monkeypatch):
    gate,clock,health=setup(tmp_path,monkeypatch)
    (tmp_path/'progress.json').write_text(json.dumps({'time':clock[0]}))
    health(worker_alive=False)
    with pytest.raises(RuntimeError,match='actual_worker_thread_exited'):gate.check_liveness(clock[0])


@pytest.mark.parametrize('updates', [{'maintenance_failed':True},{'strict_cleanup_failed':True},{'monotonic':900.}])
def test_explicit_failure_and_stale_health_fail(tmp_path,monkeypatch,updates):
    gate,clock,health=setup(tmp_path,monkeypatch);health(**updates)
    with pytest.raises(RuntimeError):gate.check_liveness(clock[0])


def test_uncaught_thread_trace_fails_even_if_last_snapshot_alive(tmp_path,monkeypatch):
    gate,clock,health=setup(tmp_path,monkeypatch)
    (tmp_path/'service.stderr').write_text('Exception in thread audio-worker:\nTraceback\n')
    with pytest.raises(RuntimeError,match='actual_worker_thread_exception'):gate.check_liveness(clock[0])


def test_missing_health_is_bounded(tmp_path,monkeypatch):
    gate,clock,health=setup(tmp_path,monkeypatch);(tmp_path/'worker_health.json').unlink()
    gate.check_liveness(clock[0]);clock[0]+=6
    with pytest.raises(RuntimeError,match='worker_health_missing'):gate.check_liveness(clock[0])


def test_no_progress_alert_does_not_kill_alive_busy_worker(tmp_path,monkeypatch):
    gate,clock,health=setup(tmp_path,monkeypatch);gate.check_liveness(clock[0])
    clock[0]+=301;health(current_chunk=True);gate.check_liveness(clock[0])
    assert gate.progress_alert=='no_completed_progress_300s_inspect_stage'
    assert gate._last_advance_wall==1000
    gate.completed=1;clock[0]+=1;health();gate.check_liveness(clock[0])
    assert gate.progress_alert is None and gate._last_advance_wall==1302


def test_integrity_phase_distinct_from_processing_heartbeat(tmp_path,monkeypatch):
    gate,clock,health=setup(tmp_path,monkeypatch)
    (tmp_path/'progress.json').write_text(json.dumps({'completed':12,'last_completed_advance':990.}))
    gate.set_phase('integrity')
    value=json.loads((tmp_path/'progress.json').read_text())
    assert value['phase']=='integrity' and value['completed']==12
    assert value['last_completed_advance']==990.


def test_worker_terminal_error_class_is_not_healthy(tmp_path,monkeypatch):
    gate,clock,health=setup(tmp_path,monkeypatch);health(worker_error_class='OperationalError')
    with pytest.raises(RuntimeError,match='actual_worker_terminal_error'):gate.check_liveness(clock[0])


@pytest.mark.parametrize('fault',['malformed','write'])
def test_phase_diagnostic_failure_never_skips_original_cleanup(tmp_path,monkeypatch,fault):
    gate,clock,health=setup(tmp_path,monkeypatch);calls=[]
    if fault=='malformed':(tmp_path/'progress.json').write_text('{')
    else:monkeypatch.setattr(run,'save',lambda *a:(_ for _ in ()).throw(OSError('disk')))
    first=RuntimeError('original worker failed')
    try:
        raise first
    except RuntimeError as caught:
        assert caught is first
    finally:
        gate.set_phase('stopping')
        calls.append('original_stop')
        gate.set_phase('terminal')
        calls.append('original_fd_close')
    assert calls==['original_stop','original_fd_close']
    assert gate.phase_observation_error in ('JSONDecodeError','OSError')


def test_health_published_after_check_start_is_fresh(tmp_path,monkeypatch):
    gate,clock,health=setup(tmp_path,monkeypatch)
    started=clock[0];health(monotonic=started+.001);clock[0]+= .002
    gate.check_liveness(started)
    assert gate.worker_health['worker_alive'] is True
