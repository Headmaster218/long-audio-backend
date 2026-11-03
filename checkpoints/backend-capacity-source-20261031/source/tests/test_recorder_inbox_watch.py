"""Finite watch qualification with real local evidence and model-free peers."""
import json
from pathlib import Path

import pytest

from audio_backend import recorder_inbox_runner as runner
from audio_backend import recording_uploader as u
from test_recorder_inbox_runner import example, fake_export, invoke
from test_extended_recorder_intake import Peer


def watching(root, peer, **kwargs):
    options = dict(state_dir=root, exclusive_writer=True, acknowledge_span_local_closure=True,
        limits=u.Limits(disk_floor=0), transport=peer, poll_seconds=.1, max_polls=8,
        deadline_seconds=30)
    options.update(kwargs)
    return runner.watch(**options)


def test_arrivals_use_one_watch_and_exact_originals(tmp_path, monkeypatch):
    paths, config, root = example(tmp_path, 2)
    pending = tmp_path/'not-yet-delivered'; paths[1].rename(pending)
    runner.configure(state_dir=root, config=config, first_delivery=paths[0])
    pauses = []
    def arrive(guard, seconds):
        pauses.append(seconds); pending.rename(paths[1])
    monkeypatch.setattr(u.Guard, 'pause', arrive)
    result = watching(root, Peer())
    assert result['status'] == 'awaiting_finalize' and result['inputs'] == 2
    assert result['helper_actions'] == result['actions'] == 2
    assert result['polls'] == 4 and len(pauses) == 1
    assert result['automatic_intake_enabled'] and result['invocation_bounded']
    assert not result['daemon_installed'] and not result['automatic_resume_enabled']
    assert not (root/'finalize.intent.json').exists()


def test_no_arrivals_consume_polls_but_no_helpers_or_http(tmp_path, monkeypatch):
    paths, config, root = example(tmp_path, 1)
    paths[0].rename(tmp_path/'not-yet-delivered')
    runner.configure(state_dir=root, config=config)
    monkeypatch.setattr(u.Guard, 'pause', lambda *a: None)
    result = watching(root, Peer(), max_polls=3)
    assert result['status'] == 'poll_limit' and result['last_status'] == 'waiting_for_input'
    assert result['polls'] == 3 and result['actions'] == result['network_requests'] == 0


def test_pending_observations_share_action_and_network_budgets(tmp_path, monkeypatch):
    paths, config, root = example(tmp_path, 1)
    runner.configure(state_dir=root, config=config); peer = Peer()
    invoke(root, peer, finalize=True, max_actions=2)
    def pending(**kwargs):
        kwargs['transport'].request('GET','/sessions/known')
        return dict(status='pending',analysis_completion_verified=False)
    monkeypatch.setattr(runner.status, 'run', pending)
    monkeypatch.setattr(u.Guard, 'pause', lambda *a: None)
    result = watching(root, peer, max_actions=3)
    assert result['status'] == 'action_limit'
    assert result['network_requests'] == result['helper_actions'] == result['actions'] == 3
    result = watching(root, peer, max_requests=2)
    assert result['status'] == 'unresolved' and result['reason'] == 'runner_network_request_bound'
    assert result['network_requests'] == 2 and result['helper_actions'] == 3


def test_watch_export_counts_separate_helper_and_requires_explicit_flag(tmp_path, monkeypatch):
    paths, config, root = example(tmp_path, 1)
    runner.configure(state_dir=root, config=config); peer = Peer()
    invoke(root, peer, finalize=True, max_actions=2)
    monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status='completed',analysis_completion_verified=True))
    monkeypatch.setattr(runner.exporter,'run',fake_export)
    result = watching(root, peer)
    assert result['status'] == 'completed' and not Path(config['output_dir']).exists()
    result = watching(root, peer, export=True, max_actions=1)
    assert result['status'] == 'action_limit' and not (root/'export.intent.json').exists()
    result = watching(root, peer, export=True, max_actions=2)
    assert result['status'] == 'exported' and result['helper_actions'] == result['actions'] == 2
    assert watching(root, peer)['status'] == 'exported'


@pytest.mark.parametrize('state', ['blocked','completed'])
def test_nonpending_analysis_stops_without_pause(tmp_path, monkeypatch, state):
    paths, config, root = example(tmp_path, 1)
    runner.configure(state_dir=root, config=config); peer = Peer()
    invoke(root, peer, finalize=True, max_actions=2)
    monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status=state,analysis_completion_verified=state=='completed'))
    monkeypatch.setattr(u.Guard,'pause',lambda *a:pytest.fail('unexpected retry'))
    result = watching(root, peer)
    assert result['status'] == state and result['actions'] == 1


def test_global_decoded_json_budget_reserves_response_before_call(tmp_path, monkeypatch):
    paths, config, root = example(tmp_path, 1)
    runner.configure(state_dir=root, config=config); peer = Peer()
    invoke(root, peer, finalize=True, max_actions=2)
    def pending(**kwargs):
        kwargs['transport'].request('GET','/sessions/known')
        return dict(status='pending',analysis_completion_verified=False)
    monkeypatch.setattr(runner.status,'run',pending)
    monkeypatch.setattr(u.Guard,'pause',lambda *a:None)
    result = watching(root, peer, max_network_bytes=u.JSON_BYTES)
    assert result['status'] == 'unresolved' and result['reason'] == 'runner_network_byte_bound'
    assert result['network_requests'] == 1 and 0 < result['network_response_bytes'] <= u.JSON_BYTES


def test_marker_last_is_explicit_and_no_work_until_publication(tmp_path, monkeypatch):
    paths, config, root = example(tmp_path, 2)
    marker = paths[1]/'delivery.v1.txt'; raw = marker.read_bytes(); marker.unlink()
    runner.configure(state_dir=root, config=config, first_delivery=paths[0]); peer = Peer()
    result = watching(root, peer)
    assert result['status'] == 'unresolved' and result['network_requests'] == 0
    assert not Path(config['job_dir']).exists()
    def publish(guard, seconds):
        assert not Path(config['job_dir']).exists()
        assert not (root/'input-000.done.json').exists()
        marker.write_bytes(raw)
    monkeypatch.setattr(u.Guard,'pause',publish)
    result = watching(root, peer, wait_for_marker_last=True)
    assert result['status'] == 'awaiting_finalize' and result['inputs'] == 2
    assert result['last_status'] == 'waiting_for_publication'


def test_marker_absence_on_selected_original_is_always_fatal(tmp_path, monkeypatch):
    paths, config, root = example(tmp_path, 1)
    runner.configure(state_dir=root, config=config, first_delivery=paths[0])
    (paths[0]/'delivery.v1.txt').unlink()
    monkeypatch.setattr(u.Guard,'pause',lambda *a:pytest.fail('must not wait'))
    result = watching(root, Peer(), wait_for_marker_last=True)
    assert result['status'] == 'unresolved' and result['network_requests'] == 0


@pytest.mark.parametrize('change', ['extra','symlink','oversized','marker'])
def test_marker_wait_never_ignores_unsafe_unselected_attempt(tmp_path, change):
    paths, config, root = example(tmp_path, 2)
    marker = paths[1]/'delivery.v1.txt'; marker.unlink()
    if change == 'extra': (paths[1]/'unexpected').write_bytes(b'x')
    elif change == 'symlink':
        (paths[1]/'audio.wav').unlink(); (paths[1]/'audio.wav').symlink_to(paths[0]/'audio.wav')
    elif change == 'oversized':
        with (paths[1]/'segment.v1.bin').open('wb') as stream: stream.truncate(runner.MAX_METADATA+1)
    else: marker.write_bytes(b'invalid')
    runner.configure(state_dir=root, config=config, first_delivery=paths[0])
    result = watching(root, Peer(), wait_for_marker_last=True)
    assert result['status'] == 'unresolved' and result['network_requests'] == 0
    assert not Path(config['job_dir']).exists()


@pytest.mark.parametrize('field,value', [('poll_seconds',None),('poll_seconds',True),('poll_seconds',float('nan')),
    ('poll_seconds',float('inf')),('max_polls',False),('max_polls',4097),
    ('max_actions',4097),('max_network_bytes',True),('max_network_bytes',u.JSON_BYTES-1),
    ('wait_for_marker_last',1)])
def test_watch_bounds_fail_before_any_effect(tmp_path, field, value):
    paths, config, root = example(tmp_path, 1)
    runner.configure(state_dir=root, config=config)
    with pytest.raises((runner.Refused, ValueError)):
        watching(root, Peer(), **{field:value})
    assert not Path(config['job_dir']).exists()
