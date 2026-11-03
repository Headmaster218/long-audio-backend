"""Independent bounded-watch controls, with real local journals and processes.

Protocol peers are synthetic. These tests launch no service/native inference and
do not qualify an always-on receiver, actual FTPS or model accuracy.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

from audio_backend import recorder_capture_intake as engine
from audio_backend import recorder_inbox_runner as runner
from audio_backend import recorder_intake as single
from audio_backend import recording_uploader as u
from test_recorder_inbox import delivery
from test_recorder_inbox_runner_review import (
    LoggedPeer, alternative, example, file_bytes, invoke as one_shot,
)


def watch(state, peer, **kwargs):
    options = dict(state_dir=state, exclusive_writer=True,
        acknowledge_span_local_closure=True, transport=peer,
        limits=u.Limits(disk_floor=0, deadline_seconds=30, poll_seconds=.01),
        deadline_seconds=30, poll_seconds=.1, max_polls=10, max_actions=10)
    options.update(kwargs)
    return runner.watch(**options)


def empty(tmp_path):
    paths, config, state = example(tmp_path)
    paths[0].rename(tmp_path / 'not-yet-arrived')
    runner.configure(state_dir=state, config=config)
    return config, state


def finalized(tmp_path):
    paths, config, state = example(tmp_path, terminal='STOP')
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    one_shot(state, peer, finalize=True, max_actions=2)
    assert (state / 'finalize.done.json').exists()
    peer.calls.clear()
    return paths, config, state, peer


def environment(state, **extra):
    return dict(os.environ, RUNNER_ROOT=str(state),
        PYTHONPATH=str(Path(__file__).parent) + os.pathsep + os.environ.get('PYTHONPATH', ''),
        PYTHONDONTWRITEBYTECODE='1', **extra)


@pytest.mark.parametrize('signum', [signal.SIGINT, signal.SIGTERM])
def test_real_process_signal_interrupts_sleep_releases_same_lock_and_preserves_state(tmp_path, signum):
    config, state = empty(tmp_path)
    before = file_bytes(state)
    ready = tmp_path / 'sleep-ready'
    script = '''
import os
from pathlib import Path
from audio_backend import recorder_inbox_runner as r, recording_uploader as u
real_pause = u.Guard.pause
def pause(self, seconds):
    Path(os.environ['READY']).write_text('ready')
    return real_pause(self, seconds)
u.Guard.pause = pause
raise SystemExit(r.main(['watch', '--state-dir', os.environ['RUNNER_ROOT'],
    '--exclusive-writer', '--acknowledge-span-local-closure',
    '--poll-seconds', '10', '--max-polls', '100', '--deadline-seconds', '30']))
'''
    child = subprocess.Popen([sys.executable, '-c', script],
        env=environment(state, READY=str(ready)), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        end = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < end:
            time.sleep(.01)
        assert ready.exists(), child.poll()
        with pytest.raises(BlockingIOError): engine.held_lock(state / 'runner.lock')
        started = time.monotonic()
        child.send_signal(signum)
        stdout, stderr = child.communicate(timeout=5)
        assert time.monotonic() - started < 3
        assert child.returncode == 128 + signum, stderr
        assert not stdout
        report = json.loads(stderr)
        assert report['status'] == 'cancelled'
        assert report['network_requests'] == report['actions'] == 0
        assert report['preserve_inbox_state_job_and_output']
        assert str(state).encode() not in stderr
        assert before == file_bytes(state) and not Path(config['job_dir']).exists()
        held = engine.held_lock(state / 'runner.lock')
        os.close(held)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_deadline_is_shared_and_does_not_wait_full_poll_interval(tmp_path):
    config, state = empty(tmp_path)
    peer = LoggedPeer()
    started = time.monotonic()
    result = watch(state, peer, deadline_seconds=.2, poll_seconds=10)
    assert result['status'] == 'deadline_reached'
    assert time.monotonic() - started < 2
    assert result['polls'] == 1 and not peer.calls
    assert not Path(config['job_dir']).exists()


@pytest.mark.parametrize('change', ['config', 'lock', 'root', 'ledger'])
def test_changed_binding_or_ownership_during_sleep_stops_before_any_request(tmp_path, monkeypatch, change):
    config, state = empty(tmp_path)
    peer = LoggedPeer()
    pauses = []
    def mutate(self, seconds):
        pauses.append(seconds)
        assert len(pauses) == 1
        if change == 'config':
            changed = dict(config, session_id='different-target')
            (state / 'config.json').write_bytes(single._json(changed))
        elif change == 'lock':
            (state / 'runner.lock').rename(tmp_path / 'original-lock')
            (state / 'runner.lock').touch(mode=0o600)
        elif change == 'root':
            state.rename(tmp_path / 'original-state')
            shutil.copytree(tmp_path / 'original-state', state)
        else:
            single.manifest_write(state / 'unexpected.json', {'untrusted': True})
    monkeypatch.setattr(u.Guard, 'pause', mutate)
    result = watch(state, peer)
    assert result['status'] == 'unresolved' and result['polls'] == 1
    assert not peer.calls and not Path(config['job_dir']).exists()
    assert result['preserve_inbox_state_job_and_output']


def test_pending_observations_share_request_budget_across_polls(tmp_path, monkeypatch):
    _, _, state, peer = finalized(tmp_path)
    observations = []
    def pending(**kwargs):
        observations.append(1)
        kwargs['transport'].request('GET', '/sessions/known')
        return dict(status='pending', analysis_completion_verified=False)
    monkeypatch.setattr(runner.status, 'run', pending)
    monkeypatch.setattr(u.Guard, 'pause', lambda self, seconds: None)
    result = watch(state, peer, max_requests=2)
    assert result['status'] == 'unresolved'
    assert result['reason'] == 'runner_network_request_bound'
    assert len(peer.calls) == result['network_requests'] == 2
    assert len(observations) == result['helper_actions'] == 3
    assert not (state / 'export.intent.json').exists()


def test_decoded_response_budget_is_not_renewed_between_polls(tmp_path, monkeypatch):
    _, _, state, peer = finalized(tmp_path)
    def pending(**kwargs):
        kwargs['transport'].request('GET', '/sessions/known')
        return dict(status='pending', analysis_completion_verified=False)
    monkeypatch.setattr(runner.status, 'run', pending)
    monkeypatch.setattr(u.Guard, 'pause', lambda self, seconds: None)
    result = watch(state, peer, max_network_bytes=u.JSON_BYTES)
    assert result['status'] == 'unresolved'
    assert 'byte_bound' in result['reason']
    assert result['network_requests'] == len(peer.calls) == 1
    assert 0 < result['network_response_bytes'] < u.JSON_BYTES


@pytest.mark.parametrize('status', ['blocked', 'completed'])
def test_actual_status_is_terminal_and_does_not_implicitly_export(tmp_path, monkeypatch, status):
    _, _, state, peer = finalized(tmp_path)
    monkeypatch.setattr(runner.status, 'run', lambda **kwargs: dict(
        status=status, analysis_completion_verified=status == 'completed'))
    monkeypatch.setattr(u.Guard, 'pause', lambda *args: pytest.fail('terminal status was polled again'))
    monkeypatch.setattr(runner.exporter, 'run', lambda **kwargs: pytest.fail('export was not authorized'))
    result = watch(state, peer)
    assert result['status'] == status and result['helper_actions'] == 1
    assert not (state / 'export.intent.json').exists()


@pytest.mark.parametrize('error', [u.Uncertain('synthetic unknown outcome'),
    u.HTTPFailure(409), u.HTTPFailure(429), u.HTTPFailure(503)])
def test_failed_request_stops_without_helper_or_poll_retry(tmp_path, monkeypatch, error):
    _, config, state = example(tmp_path)
    runner.configure(state_dir=state, config=config)
    class FailedPeer:
        calls = 0
        def request(self, method, path, **kwargs):
            self.calls += 1
            raise error
    peer = FailedPeer()
    monkeypatch.setattr(u.Guard, 'pause', lambda *args: pytest.fail('failed HTTP was automatically retried'))
    result = watch(state, peer)
    assert result['status'] == 'unresolved'
    assert result['network_requests'] == peer.calls == 1
    assert result['reason'] == 'runner_watch_transport_stopped_no_retry'
    assert (state / 'input-000.intent.json').exists()
    assert not (state / 'input-000.done.json').exists()


def test_real_process_crash_preserves_exact_selection_and_requires_explicit_restart(tmp_path):
    paths, config, state = example(tmp_path, count=2)
    runner.configure(state_dir=state, config=config)
    script = '''
import os
from audio_backend import recorder_inbox_runner as r, recording_uploader as u
from test_extended_recorder_intake import Peer
real = r.intake.run
def crash(**kwargs):
    real(**kwargs)
    os._exit(73)
r.intake.run = crash
r.watch(state_dir=os.environ['RUNNER_ROOT'], exclusive_writer=True,
    acknowledge_span_local_closure=True, max_actions=10, max_polls=10,
    limits=u.Limits(disk_floor=0), transport=Peer())
'''
    child = subprocess.run([sys.executable, '-c', script], env=environment(state),
        capture_output=True, timeout=15)
    assert child.returncode == 73, child.stderr
    assert (state / 'input-000.intent.json').exists()
    assert not (state / 'input-000.done.json').exists()
    assert not (state / 'input-001.intent.json').exists()
    alternate = alternative(paths[0])
    peer = LoggedPeer()
    result = watch(state, peer, max_actions=2)
    assert result['status'] == 'action_limit' and result['inputs'] == 2
    receipt = json.loads((Path(config['job_dir']) / 'admissions/0/admission.json').read_bytes())
    assert receipt['path'] == str(paths[0]) and receipt['path'] != str(alternate)
    assert not any(method != 'GET' for method, _ in peer.calls)


@pytest.mark.parametrize('field,value', [('max_polls', True), ('max_polls', 4097),
    ('poll_seconds', False), ('poll_seconds', float('nan')), ('poll_seconds', float('inf')),
    ('deadline_seconds', float('nan')), ('max_actions', True), ('max_actions', 4097),
    ('max_network_bytes', True), ('max_network_bytes', u.JSON_BYTES - 1)])
def test_invalid_bounds_refuse_before_any_effect(tmp_path, field, value):
    config, state = empty(tmp_path)
    peer = LoggedPeer()
    before = file_bytes(state)
    with pytest.raises((single.Refused, ValueError)):
        watch(state, peer, **{field: value})
    assert before == file_bytes(state) and not peer.calls
    assert not Path(config['job_dir']).exists()


def test_marker_last_wait_blocks_every_action_until_complete_publication(tmp_path, monkeypatch):
    paths, config, state = example(tmp_path, count=2, terminal='STOP')
    marker = paths[1] / 'delivery.v1.txt'
    saved = marker.read_bytes()
    marker.unlink()
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    observations = []
    def finish_arrival(self, seconds):
        observations.append(1)
        assert not peer.calls
        assert not (state / 'input-000.intent.json').exists()
        assert not Path(config['job_dir']).exists()
        marker.write_bytes(saved)
    monkeypatch.setattr(u.Guard, 'pause', finish_arrival)
    result = watch(state, peer, wait_for_marker_last=True, max_actions=2)
    assert result['inputs'] == 2 and result['status'] == 'action_limit'
    assert observations == [1] and not peer.eos
    assert marker.read_bytes() == saved
    assert not (state / 'finalize.intent.json').exists()


@pytest.mark.parametrize('selected', [False, True])
def test_marker_last_is_opt_in_and_cannot_wait_for_selected_original(tmp_path, monkeypatch, selected):
    paths, config, state = example(tmp_path)
    runner.configure(state_dir=state, config=config, first_delivery=paths[0] if selected else None)
    (paths[0] / 'delivery.v1.txt').unlink()
    peer = LoggedPeer()
    monkeypatch.setattr(u.Guard, 'pause', lambda *args: pytest.fail('invalid original must stop'))
    result = watch(state, peer, wait_for_marker_last=selected)
    assert result['status'] == 'unresolved' and not peer.calls
    assert not Path(config['job_dir']).exists()


def test_partial_arrival_does_not_hide_complete_content_conflict(tmp_path, monkeypatch):
    paths, config, state = example(tmp_path)
    staging = tmp_path / 'other-inbox'
    staging.mkdir(mode=0o700)
    changed = delivery(staging, 23, epoch=7, start=1000, boundary='RESTART',
        run='review-process', seam=False, close='STOP')
    alternate = alternative(changed)
    alternate.rename(Path(config['inbox_dir']) / alternate.name)
    (Path(config['inbox_dir']) / ('r-' + 'f' * 64 + '-' + 'b' * 32)).mkdir()
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    monkeypatch.setattr(u.Guard, 'pause', lambda *args: pytest.fail('complete conflict must stop'))
    result = watch(state, peer, wait_for_marker_last=True)
    assert result['status'] == 'unresolved'
    assert result['reason'] == 'runner_conflicting_ordinal_content'
    assert not peer.calls and not Path(config['job_dir']).exists()


@pytest.mark.parametrize('change', ['marker_present', 'extra', 'symlink', 'hardlink',
    'oversized_audio', 'oversized_metadata', 'directory_member'])
def test_marker_absent_shape_gate_refuses_unsafe_or_invalid_arrivals(tmp_path, monkeypatch, change):
    paths, config, state = example(tmp_path)
    marker = paths[0] / 'delivery.v1.txt'
    marker.unlink()
    if change == 'marker_present': marker.write_bytes(b'partially written invalid marker')
    elif change == 'extra': (paths[0] / 'unexpected').write_bytes(b'unknown')
    elif change == 'symlink':
        audio = paths[0] / 'audio.wav'
        audio.rename(tmp_path / 'retained-audio')
        audio.symlink_to(tmp_path / 'retained-audio')
    elif change == 'hardlink': os.link(paths[0] / 'audio.wav', tmp_path / 'retained-audio')
    elif change == 'oversized_audio':
        with (paths[0] / 'audio.wav').open('r+b') as stream:
            stream.truncate(runner.inbox.MAX_WAV + 1)
    elif change == 'oversized_metadata':
        with (paths[0] / 'segment.v1.bin').open('r+b') as stream:
            stream.truncate(runner.MAX_METADATA + 1)
    else:
        (paths[0] / 'audio.wav').unlink()
        (paths[0] / 'audio.wav').mkdir()
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    monkeypatch.setattr(u.Guard, 'pause', lambda *args: pytest.fail('unsafe shape must stop'))
    result = watch(state, peer, wait_for_marker_last=True)
    assert result['status'] == 'unresolved' and not peer.calls
    assert not Path(config['job_dir']).exists()


def test_marker_wait_uses_whole_invocation_poll_bound_and_no_receipts(tmp_path, monkeypatch):
    paths, config, state = example(tmp_path)
    (paths[0] / 'delivery.v1.txt').unlink()
    runner.configure(state_dir=state, config=config)
    before = file_bytes(state)
    peer = LoggedPeer()
    pauses = []
    monkeypatch.setattr(u.Guard, 'pause', lambda self, seconds: pauses.append(seconds))
    result = watch(state, peer, wait_for_marker_last=True, max_polls=3)
    assert result['status'] == 'poll_limit' and result['polls'] == 3
    assert len(pauses) == 2 and not peer.calls
    assert result['actions'] == result['helper_actions'] == 0
    assert before == file_bytes(state) and not Path(config['job_dir']).exists()


def test_real_signal_after_synthetic_commit_preserves_uncertainty_without_cancel_or_retry(tmp_path):
    _, config, state = example(tmp_path, terminal='STOP')
    runner.configure(state_dir=state, config=config)
    calls = tmp_path / 'calls.jsonl'
    committed = tmp_path / 'committed.json'
    script = '''
import json, os, signal
from pathlib import Path
from audio_backend import recorder_inbox_runner as r, recording_uploader as u
from test_extended_recorder_intake import Peer
class SignalledPeer(Peer):
    def request(self, method, path, **kwargs):
        with Path(os.environ['CALLS']).open('a') as stream:
            stream.write(json.dumps([method, path]) + '\\n')
        result = super().request(method, path, **kwargs)
        if method == 'PUT':
            Path(os.environ['COMMITTED']).write_text(json.dumps(dict(puts=self.puts)))
            os.kill(os.getpid(), signal.SIGTERM)
        return result
peer = SignalledPeer()
u.HTTPTransport = lambda *args, **kwargs: peer
real_watch = r.watch
def watch(**kwargs):
    kwargs['limits'] = u.Limits(disk_floor=0)
    return real_watch(**kwargs)
r.watch = watch
raise SystemExit(r.main(['watch', '--state-dir', os.environ['RUNNER_ROOT'],
    '--exclusive-writer', '--acknowledge-span-local-closure', '--finalize',
    '--max-actions', '10', '--max-polls', '10', '--deadline-seconds', '30']))
'''
    child = subprocess.run([sys.executable, '-c', script],
        env=environment(state, CALLS=str(calls), COMMITTED=str(committed)),
        capture_output=True, timeout=15)
    assert child.returncode == 143, child.stderr
    result = json.loads(child.stderr)
    assert result['status'] == 'cancelled' and result['possible_remote_effects']
    assert result['preserve_inbox_state_job_and_output']
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(requests) == result['network_requests']
    assert len(json.loads(committed.read_text())['puts']) == 1
    assert requests[-1][0] == 'PUT'
    assert not any('cancel' in path or path == '/sessions' for _, path in requests)
    assert (state / 'finalize.intent.json').exists()
    assert not (state / 'finalize.done.json').exists()
    assert (Path(config['job_dir']) / 'uploader/uploader.json').exists()


@pytest.mark.parametrize('selection', [None, 'r-' + '0' * 64 + '-' + '0' * 32])
def test_marker_wait_does_not_hide_known_selection_ambiguity(tmp_path, monkeypatch, selection):
    paths, config, state = example(tmp_path)
    alternative(paths[0])
    (Path(config['inbox_dir']) / ('r-' + 'f' * 64 + '-' + 'b' * 32)).mkdir()
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    monkeypatch.setattr(u.Guard, 'pause', lambda *args: pytest.fail('selection refusal must stop'))
    result = watch(state, peer, wait_for_marker_last=True, select_attempt=selection)
    assert result['status'] == 'unresolved' and not peer.calls
    assert result['reason'] in ('runner_explicit_duplicate_selection_required',
        'runner_selected_attempt_not_found')
    assert not (state / 'input-000.intent.json').exists()


def test_marker_wait_does_not_hide_uncertain_existing_export(tmp_path, monkeypatch):
    _, config, state = example(tmp_path, terminal='STOP')
    config['output_dir'] = str(tmp_path / 'output')
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    one_shot(state, peer, finalize=True, max_actions=2)
    monkeypatch.setattr(runner.status, 'run', lambda **kwargs: dict(
        status='completed', analysis_completion_verified=True))
    def uncertain_export(**kwargs):
        output = Path(kwargs['output_dir'])
        output.mkdir(mode=0o700)
        (output / 'export.json.partial').write_bytes(b'preserve unknown publication')
        raise OSError('synthetic publication interruption')
    monkeypatch.setattr(runner.exporter, 'run', uncertain_export)
    assert watch(state, peer, export=True)['status'] == 'unresolved'
    assert (state / 'export.intent.json').exists()
    assert not (state / 'export.done.json').exists()
    (Path(config['inbox_dir']) / ('r-' + 'f' * 64 + '-' + 'b' * 32)).mkdir()
    before = file_bytes(Path(config['output_dir']))
    peer.calls.clear()
    monkeypatch.setattr(u.Guard, 'pause', lambda *args: pytest.fail('uncertain export must stop'))
    monkeypatch.setattr(runner.status, 'run', lambda **kwargs: pytest.fail('uncertain export must stop before HTTP'))
    result = watch(state, peer, export=True, wait_for_marker_last=True)
    assert result['status'] == 'unresolved'
    assert result['reason'] == 'runner_export_publication_uncertain_preserve_output'
    assert before == file_bytes(Path(config['output_dir'])) and not peer.calls


def test_marker_wait_does_not_hide_unapproved_finalize_resume(tmp_path, monkeypatch):
    _, config, state = example(tmp_path, terminal='STOP')
    runner.configure(state_dir=state, config=config)
    peer = LoggedPeer()
    one_shot(state, peer)
    intents, _ = runner._ledger(state, config)
    intent = dict(schema=runner.SCHEMA, config_sha256=runner._hash(config),
        kind='finalize', input_intents_sha256=runner._hash(intents))
    single.manifest_write(state / 'finalize.intent.json', intent)
    (Path(config['inbox_dir']) / ('r-' + 'f' * 64 + '-' + 'b' * 32)).mkdir()
    peer.calls.clear()
    monkeypatch.setattr(u.Guard, 'pause', lambda *args: pytest.fail('unapproved resume must stop'))
    result = watch(state, peer, wait_for_marker_last=True)
    assert result['status'] == 'unresolved' and not peer.calls
    assert result['reason'] == 'runner_finalize_resume_authorization_required'
    assert not (state / 'finalize.done.json').exists()
