"""Model-free launcher controls. No service listener, models, recording or device.

Synthetic preflight matches are only test seams, never runtime qualification.
Real process tests exercise the original lock and original OS child handles.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from audio_backend import strict_supervisor as supervisor
from audio_backend import strict_runtime_preflight as runtime

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux procfs/flock/subreaper contract')
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'tests/helpers/synthetic_supervisor_process.py'


def directory(tmp_path):
    data = tmp_path / 'data'
    data.mkdir(mode=0o700)
    lock = data / 'instance.lock'
    lock.write_bytes(b'unchanged-lock-bytes')
    lock.chmod(0o600)
    return data


def arguments(data, action='check'):
    return [action, '--data-dir', str(data), '--model-dir', str(data.parent / 'models'),
        '--native-diarization-model', str(data.parent / 'native.gguf'),
        '--native-diarization-library', str(data.parent / 'native.so'),
        '--recording-speaker-model', str(data.parent / 'speaker.onnx'), '--language', 'en',
        '--profile', 'strict-primary-payload-v2', '--strict-payload-source-storage', 'shared-source-v1']


def available(data):
    with (data / 'instance.lock').open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def conflict(data):
    with pytest.raises(BlockingIOError): available(data)


def until(predicate, process, timeout=15):
    end = time.monotonic() + timeout
    while not predicate():
        if process.poll() is not None:
            raise AssertionError('Synthetic supervisor exited before expected event')
        if time.monotonic() > end:
            raise AssertionError('Synthetic supervisor event deadline')
        time.sleep(.01)


@pytest.mark.parametrize('action', ['check', 'serve'])
def test_unqualified_runtime_refuses_without_assets_service_or_data_mutation(tmp_path, monkeypatch, capsys, action):
    data = directory(tmp_path)
    def inspect(path):
        assert path == data
        conflict(data)
        return {'status': 'unqualified', 'reason': 'synthetic_unqualified'}
    monkeypatch.setattr(runtime, 'inspect_runtime', inspect)
    monkeypatch.setattr(supervisor, '_assets', lambda _: pytest.fail('Assets touched after runtime refusal'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Child started on refusal'))
    assert supervisor.main(arguments(data, action)) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'unqualified_runtime'
    assert [p.name for p in data.iterdir()] == ['instance.lock']
    assert (data / 'instance.lock').read_bytes() == b'unchanged-lock-bytes'
    available(data)
    assert supervisor._PENDING_OWNER is None


def test_check_is_metadata_only_and_explicit_about_its_limits(tmp_path, monkeypatch, capsys):
    data = directory(tmp_path)
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: {'status': 'matched'})
    monkeypatch.setattr(supervisor, '_assets', lambda _: conflict(data))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Check launched a child'))
    assert supervisor.main(arguments(data)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'preflight_passed'
    assert report['service'] == 'not_started' and report['models'] == 'not_loaded'
    assert report['session'] == 'not_created'
    available(data)


def test_busy_lock_refuses_before_runtime_inspection(tmp_path, monkeypatch):
    data = directory(tmp_path)
    monkeypatch.setattr(runtime, 'inspect_runtime', lambda _: pytest.fail('Read before ownership'))
    with (data / 'instance.lock').open('rb') as original:
        fcntl.flock(original, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert supervisor.main(arguments(data)) == 1
        conflict(data)
    available(data)


@pytest.mark.parametrize('kind', ['missing_lock', 'public_dir', 'public_lock', 'alias_dir', 'alias_lock', 'hardlink_lock'])
def test_unsafe_existing_control_paths_refuse(tmp_path, kind):
    data = directory(tmp_path)
    lock = data / 'instance.lock'
    if kind == 'missing_lock': lock.unlink()
    elif kind == 'public_dir': data.chmod(0o755)
    elif kind == 'public_lock': lock.chmod(0o644)
    elif kind == 'alias_dir':
        alias = tmp_path / 'alias'; alias.symlink_to(data, target_is_directory=True); data = alias
    elif kind == 'alias_lock':
        real = data / 'original'; lock.rename(real); lock.symlink_to(real)
    elif kind == 'hardlink_lock': os.link(lock, data / 'other')
    assert supervisor.main(arguments(data)) == 1
    assert supervisor._PENDING_OWNER is None


@pytest.mark.parametrize('extra', [['--host', '0.0.0.0'], ['--instance-lock-fd', '3'], ['--profile', 'legacy'], ['--skip-runtime-check']])
def test_no_network_override_lock_fd_or_pin_bypass(tmp_path, extra):
    data = directory(tmp_path)
    with pytest.raises(SystemExit) as caught: supervisor.main(arguments(data) + extra)
    assert caught.value.code == 2
    available(data)


@pytest.mark.parametrize('flag,value', [('--port', '0'), ('--memory-limit-gib', 'nan'),
    ('--shutdown-grace-seconds', 'inf'), ('--recording-total-mib', '0'), ('--lexical-evidence-session-mib', '1025')])
def test_invalid_numeric_options_do_not_acquire_or_inspect(tmp_path, monkeypatch, flag, value):
    data = directory(tmp_path)
    monkeypatch.setattr(supervisor, '_acquire', lambda _: pytest.fail('Invalid config acquired lock'))
    assert supervisor.main(arguments(data) + [flag, value]) == 1


def test_command_is_exact_product_profile_without_private_harness_paths(tmp_path):
    data = directory(tmp_path)
    args = supervisor._parser().parse_args(arguments(data, 'serve'))
    command = supervisor._child_command(args, 9)
    def value(flag): return command[command.index(flag) + 1]
    assert command[:4] == [sys.executable, '-I', '-B', '-c']
    assert 'audio_backend.__main__' in command[4]
    assert value('--instance-lock-fd') == '9'
    assert value('--strict-primary-policy') == 'strict-primary-payload-v2'
    assert value('--strict-payload-source-storage') == 'shared-source-v1'
    assert value('--window-seconds') == '5.25' and value('--context-seconds') == '0'
    assert value('--speaker-support-max-records') == '65536'
    assert value('--recording-primary-sampling-policy') == 'skip-bucket-v1'
    assert value('--recording-capture-mib') == '4096' and value('--recording-total-mib') == '8192'
    assert value('--strict-primary-lifetime-records') == '32768'
    assert value('--strict-primary-receipt-records') == '8192'
    assert value('--lexical-evidence-session-mib') == '256'
    assert '--moss-jobs' not in command and '--native-owned-compact-gc' not in command
    assert not any('capacity_service' in item or 'audio-recovery' in item for item in command[1:])


@pytest.mark.parametrize('mode,signum,expected,childcode', [
    ('exit', None, 23, 23), ('release_delay', signal.SIGINT, 130, 0),
    ('release_delay', signal.SIGTERM, 143, 0), ('ignore', signal.SIGTERM, 143, -9),
    ('orphan', None, 7, 7), ('orphan_zero', None, 1, 0),
])
def test_real_original_wait_signal_and_orphan_cleanup(tmp_path, mode, signum, expected, childcode):
    data = directory(tmp_path)
    process = subprocess.Popen([sys.executable, '-B', str(HELPER), str(data), mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until(lambda: (data / 'ready').exists(), process)
        conflict(data)
        if signum is not None:
            process.send_signal(signum)
            if mode == 'release_delay':
                until(lambda: (data / 'stopping').exists(), process)
                # Child has closed its inherited duplicate, but is still alive.
                # The supervisor's original hold is the remaining authority.
                conflict(data)
                time.sleep(.1)
                conflict(data)
                (data / 'allow_exit').write_text('test has checked continuous parent ownership')
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        result = json.loads((data / 'result').read_text())
        assert result == {'result': expected, 'child_returncode': childcode,
                          'positive_wait': True, 'no_descendants': True}
        available(data)
        if (data / 'grandchild').exists():
            assert not Path('/proc/' + (data / 'grandchild').read_text()).exists()
        assert (data / 'instance.lock').read_bytes() == b'unchanged-lock-bytes'
    finally:
        (data / 'allow_exit').touch()
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired: process.kill()
        process.wait(timeout=15)


def test_child_cli_preserves_existing_default_context_and_support_cap(tmp_path, monkeypatch):
    from audio_backend import __main__ as server
    captured = []
    monkeypatch.setattr(server, '_run_app', lambda settings, *a, **k: captured.append(settings))
    server.main(['--data-dir', str(tmp_path)])
    assert captured[-1].context_seconds == 2 and captured[-1].speaker_support_max_records == 4096
    server.main(['--data-dir', str(tmp_path), '--context-seconds', '0',
                 '--speaker-support-max-records', '65536', '--window-seconds', '5.25'])
    assert captured[-1].context_seconds == 0 and captured[-1].speaker_support_max_records == 65536
    assert captured[-1].window_seconds == 5.25


def test_descendant_import_uses_same_checkout_from_foreign_cwd_and_pythonpath(tmp_path):
    data = directory(tmp_path)
    foreign = tmp_path / 'foreign'
    (foreign / 'audio_backend').mkdir(parents=True)
    (foreign / 'audio_backend/__init__.py').write_text("raise RuntimeError('wrong source origin')\n")
    env = dict(os.environ, PYTHONPATH=str(foreign))
    process = subprocess.Popen([sys.executable, '-B', str(HELPER), str(data), 'origin'],
        cwd=foreign, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        assert json.loads((data / 'result').read_text())['result'] == 0
        available(data)
    finally:
        (data / 'allow_exit').touch()
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired: process.kill()
        process.wait(timeout=15)


def test_launch_failure_with_unknown_child_inventory_retains_lock(tmp_path, monkeypatch):
    data = directory(tmp_path)
    owner = supervisor._Owner(supervisor._acquire(data))
    calls = []
    def children():
        calls.append(True)
        if len(calls) == 1: return []
        raise OSError('synthetic observation denied')
    monkeypatch.setattr(supervisor, '_children', children)
    monkeypatch.setattr(supervisor, '_subreaper', lambda: None)
    def launch(*a, **k): raise OSError('synthetic exec failure')
    monkeypatch.setattr(subprocess, 'Popen', launch)
    try:
        with pytest.raises(OSError, match='observation denied'):
            supervisor._run_child(owner, ['not-executed'], supervisor._Signals(), 1)
        assert owner.uncertain and not owner.complete
        conflict(data)
    finally:
        owner.lock.release()  # Fixture proves no actual child was launched.


def test_signal_request_before_launch_creates_no_child(tmp_path, monkeypatch):
    data = directory(tmp_path)
    def inspect(_):
        os.kill(os.getpid(), signal.SIGTERM)
        return {'status': 'matched'}
    monkeypatch.setattr(runtime, 'inspect_runtime', inspect)
    monkeypatch.setattr(supervisor, '_assets', lambda _: None)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Stop request launched child'))
    assert supervisor.main(arguments(data, 'serve')) == 143
    available(data)


def test_complete_supervisor_command_constructs_the_expected_product_settings(tmp_path, monkeypatch):
    from audio_backend import __main__ as server
    data = directory(tmp_path)
    lock = supervisor._acquire(data)
    captured = []
    monkeypatch.setattr(server, '_run_app', lambda settings, *a, **k: captured.append(settings))
    args = supervisor._parser().parse_args(arguments(data, 'serve'))
    try:
        # Same-process invocation must duplicate; production Popen has separate fd tables.
        command = supervisor._child_command(args, os.dup(lock.file.fileno()))
        server.main(command[5:])
        cfg = captured[0]
        assert cfg.strict_primary_enabled and cfg.strict_primary_policy == 'strict-primary-payload-v2'
        assert cfg.strict_payload_source_storage == 'shared-source-v1'
        assert cfg.window_seconds == 5.25 and cfg.context_seconds == 0
        assert cfg.speaker_support_enabled and cfg.speaker_support_max_records == 65536
        assert cfg.recording_primary_sampling_policy == 'skip-bucket-v1'
        assert cfg.strict_primary_lifetime_records == 32768 and cfg.strict_primary_receipt_records == 8192
        assert cfg.lexical_evidence_session_bytes == 256 * 1024**2
        assert cfg.recording_capture_bytes == 4 * 1024**3 and cfg.recording_total_bytes == 8 * 1024**3
        assert cfg.recording_disk_reserve_bytes == 11 * 1024**3 // 2
        assert cfg.native_diarization_threads == cfg.cpu_threads == 2
        assert cfg.process_memory_limit_bytes == 6 * 1024**3
        assert not cfg.moss_jobs_enabled and not cfg.native_owned_compact_gc_enabled
        conflict(data)
    finally:
        lock.release()


@pytest.mark.parametrize('mode', ['wait_failure', 'inventory_failure', 'logging_failure'])
def test_uncertain_positive_wait_keeps_cli_alive_and_original_lock_held(tmp_path, mode):
    data = directory(tmp_path)
    process = subprocess.Popen([sys.executable, '-B', str(HELPER), str(data), mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until(lambda: (data / 'uncertain').exists(), process)
        conflict(data)
        for signum in (signal.SIGINT, signal.SIGTERM):
            process.send_signal(signum)
            time.sleep(.1)
            assert process.poll() is None
            conflict(data)
        assert not (data / 'result').exists()
    finally:
        if process.poll() is None: process.kill()
        stdout, stderr = process.communicate(timeout=15)
    if mode != 'logging_failure':
        assert 'Ownership is unresolved; retaining the original lock' in stderr
    available(data)  # Only the fixture's explicit SIGKILL ends this hold.


@pytest.mark.parametrize('action', ['check', 'serve'])
def test_real_cli_refuses_unqualified_or_missing_assets_without_target_mutation(tmp_path, action):
    data = directory(tmp_path)
    result = subprocess.run([sys.executable, '-B', '-m', 'audio_backend.strict_supervisor',
        *arguments(data, action)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=30)
    # Actual current qualification is evaluated, never monkeypatched. Even an
    # independently matching runtime must refuse these intentionally absent assets.
    assert result.returncode in (1, 2), (result.stdout, result.stderr)
    assert [p.name for p in data.iterdir()] == ['instance.lock']
    assert (data / 'instance.lock').read_bytes() == b'unchanged-lock-bytes'
    available(data)
