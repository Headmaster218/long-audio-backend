"""Bounded coordinator recovery, real publication copies and synthetic HTTP."""
import json
from pathlib import Path

import pytest

from audio_backend import recorder_generation_transition as transition
from audio_backend import recorder_logical_boundary as boundary
from audio_backend import recorder_logical_advance as advance
from audio_backend import recorder_receiver_publication as publication
from audio_backend import recording_uploader as u
from test_recorder_ftps_receiver import configured
from test_recorder_receiver_routing import two
from test_recorder_receiver_completion_review import adapter, write_one
from test_recorder_inbox import delivery
from test_recorder_inbox_runner import invoke, Peer, runner, fake_export
from test_recorder_logical_advance import NextPeer
from test_recorder_logical_boundary import files


class PeerPair:
    def __init__(self, old):
        self.old, self.next, self.calls = old, NextPeer(), []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        peer = self.next if path in ('/health', '/sessions') or path.startswith('/sessions/next-session') else self.old
        return peer.request(method, path, **kwargs)


def setup(case, tmp_path, monkeypatch):
    config, raw, ready, state = two(case, tmp_path)
    templates = tmp_path/'transition-templates'; templates.mkdir(mode=0o700)
    sources = [delivery(templates, 23+i, epoch=7, start=1000+4*i, boundary='RESTART',
        run='runner-process', seam=i>0, close='STOP' if i == 2 else 'ROTATION') for i in range(3)]
    for st, inbox, selected in ((case['state'], case['ready'], sources[:2]), (state, ready, sources[2:])):
        with adapter(st, inbox) as owner:
            for source in selected:
                attempt = owner.begin_attempt(source.name)
                for name in ('audio.wav', 'segment.v1.bin', 'delivery.v1.txt'):
                    write_one(owner, attempt, source, name)
    old_config = runner.config_from_delivery(first_delivery=case['ready']/sources[0].name,
        inbox_dir=case['ready'], job_dir=tmp_path/'job', output_dir=tmp_path/'export',
        api='http://127.0.0.1:8765', session_id='known', capture_id='extended',
        receiver_state_dir=case['state'])
    root = tmp_path/'runner'; runner.configure(state_dir=root, config=old_config,
        first_delivery=case['ready']/sources[0].name)
    old = Peer(); assert invoke(root, old, max_actions=2)['inputs'] == 2
    route = tmp_path/'route.json'
    route.write_text(json.dumps(dict(case['data'], generations=list(config.generations),
        binding=publication.bind(state), inbox_dir=str(ready)))); route.chmod(0o600)
    args = dict(state_dir=root, route_config=route, successor_attempt=sources[2].name,
        next_state_dir=tmp_path/'next-runner', next_job_dir=tmp_path/'next-job',
        next_output_dir=tmp_path/'next-export', next_capture_id='next-span',
        next_session_name='private next label', acknowledge_logical_analysis_boundary=True,
        finalize=True, export=True, acknowledge_create_next_session=True, exclusive_writer=True,
        acknowledge_span_local_closure=True, poll_seconds=.1,
        limits=u.Limits(disk_floor=0, poll_seconds=.001))
    monkeypatch.setattr(runner.status, 'run', lambda **kw: dict(status='completed', analysis_completion_verified=True))
    def stub(output_dir, **kwargs):
        manifest = fake_export(output_dir=output_dir, **kwargs)
        cut = json.loads((Path(old_config['job_dir'])/boundary.NAME).read_bytes())
        manifest.update(closure_kind='logical_analysis_boundary', logical_boundary_sha256=boundary.digest(cut),
            frames=cut['frames'], recording_complete=False, recognition_continuity_verified=False)
        (Path(output_dir)/'complete.json').write_bytes(runner.single._json(manifest))
        return manifest
    monkeypatch.setattr(runner.exporter, 'run', stub)
    return args, PeerPair(old), old_config, case['raw'], raw


class NoHTTP:
    def request(self, *args, **kwargs):
        pytest.fail('unexpected HTTP')


@pytest.mark.parametrize('deadline', [120, 600])
def test_complete_exact_link_no_receiver_change_and_zero_http_repeat(configured, tmp_path, monkeypatch, deadline):
    args, peer, config, old_raw, new_raw = setup(configured, tmp_path, monkeypatch)
    args['deadline_seconds'] = deadline
    before = files(old_raw) | files(new_raw)
    result = transition.transition(**args, transport=peer)
    assert result['status'] == 'transition_complete' and result['next_session_linked']
    assert result['closed_span_frames'] == 8 and result['next_original_sequence'] == 25
    assert result['receiver_runtime_verified'] is result['receiver_process_changed'] is False
    assert result['atomic_rollover'] is result['recording_complete'] is False
    assert [x for x in peer.calls if x == ('POST', '/sessions')] == [('POST', '/sessions')]
    assert not args['next_job_dir'].exists()
    next_config = json.loads((args['next_state_dir']/'config.json').read_bytes())
    assert next_config['inbox_dir'] == str(new_raw.parent/'ready')
    invoke(args['next_state_dir'], peer)
    again = transition.transition(**args, transport=NoHTTP())
    assert again['network_requests'] == 0 and again['next_intake_started']
    assert again['boundary_sha256'] == result['boundary_sha256']
    assert files(old_raw) | files(new_raw) == before


def test_bounded_pause_then_resume_skips_preparation(configured, tmp_path, monkeypatch):
    args, peer, config, *_ = setup(configured, tmp_path, monkeypatch)
    paused = transition.transition(**dict(args, max_actions=1), transport=peer)
    assert paused['status'] == 'transition_paused' and paused['stop_reason'] == 'action_limit'
    assert not args['next_state_dir'].exists()
    assert (args['state_dir']/'finalize.done.json').exists()
    receipt = (Path(config['job_dir'])/boundary.NAME).read_bytes()
    monkeypatch.setattr(boundary, 'prepare', lambda **kw: pytest.fail('must resume, not prepare terminal span'))
    complete = transition.transition(**args, transport=peer)
    assert complete['status'] == 'transition_complete'
    assert (Path(config['job_dir'])/boundary.NAME).read_bytes() == receipt


@pytest.mark.parametrize('ack', ['acknowledge_logical_analysis_boundary', 'finalize', 'export',
    'acknowledge_create_next_session', 'exclusive_writer', 'acknowledge_span_local_closure'])
def test_each_explicit_ack_before_any_new_state(configured, tmp_path, monkeypatch, ack):
    args, peer, *_ = setup(configured, tmp_path, monkeypatch); before = files(tmp_path)
    with pytest.raises(runner.Refused, match='explicit_ack'):
        transition.transition(**dict(args, **{ack: False}), transport=NoHTTP())
    assert files(tmp_path) == before


@pytest.mark.parametrize('key,value', [('max_actions', 0), ('max_polls', True), ('poll_seconds', float('nan')),
    ('deadline_seconds', float('inf')), ('max_requests', 0), ('max_network_writes', 0),
    ('max_network_bytes', 1), ('export_recovery_attempts', 1)])
def test_invalid_bounds_before_cut(configured, tmp_path, monkeypatch, key, value):
    args, peer, *_ = setup(configured, tmp_path, monkeypatch); before = files(tmp_path)
    with pytest.raises(runner.Refused):transition.transition(**dict(args, **{key: value}), transport=NoHTTP())
    assert files(tmp_path) == before


def test_pending_analysis_never_advances(configured, tmp_path, monkeypatch):
    args, peer, *_ = setup(configured, tmp_path, monkeypatch)
    monkeypatch.setattr(runner.status, 'run', lambda **kw: dict(status='pending', analysis_completion_verified=False))
    result = transition.transition(**dict(args, max_polls=2), transport=peer)
    assert result['status'] == 'transition_paused' and result['next_session_linked'] is False
    assert not args['next_state_dir'].exists() and not peer.next.calls


def test_uncertain_next_post_never_repeats(configured, tmp_path, monkeypatch):
    args, peer, *_ = setup(configured, tmp_path, monkeypatch); peer.next.uncertain = True
    with pytest.raises(u.Uncertain):transition.transition(**args, transport=peer)
    count = len(peer.calls)
    with pytest.raises(runner.Refused, match='uncertain_no_retry'):
        transition.transition(**args, transport=peer)
    assert len(peer.calls) == count and peer.calls.count(('POST', '/sessions')) == 1


def test_cancel_during_next_health_never_posts_session(configured, tmp_path, monkeypatch):
    args, peer, *_ = setup(configured, tmp_path, monkeypatch); stopped = [False]
    real = peer.request
    def cancel_at_health(method, path, **kwargs):
        value = real(method, path, **kwargs)
        if path == '/health':stopped[0] = True
        return value
    peer.request = cancel_at_health
    with pytest.raises(InterruptedError):
        transition.transition(**args, transport=peer, cancel=lambda: stopped[0])
    assert ('POST', '/sessions') not in peer.calls
    assert not (args['next_state_dir']/'initialization/post.intent.json').exists()
    stopped[0] = False; peer.request = real
    assert transition.transition(**args, transport=peer)['status'] == 'transition_complete'
    assert peer.calls.count(('POST', '/sessions')) == 1


def test_cancel_during_preparation_has_no_boundary_authority(configured, tmp_path, monkeypatch):
    args, peer, config, *_ = setup(configured, tmp_path, monkeypatch); stopped = [False]
    real = boundary._published_successor
    def cancel_after_successor(*a, **kw):
        value = real(*a, **kw); stopped[0] = True; return value
    monkeypatch.setattr(boundary, '_published_successor', cancel_after_successor)
    with pytest.raises(InterruptedError):
        transition.transition(**args, transport=NoHTTP(), cancel=lambda: stopped[0])
    assert not (Path(config['job_dir'])/boundary.NAME).exists()
    assert not (args['state_dir']/boundary.RUNNER_ANCHOR).exists()


def test_confirmed_creation_local_interruption_resumes_without_post(configured, tmp_path, monkeypatch):
    args, peer, *_ = setup(configured, tmp_path, monkeypatch); real = advance.initialize._saved
    def interrupt(path, value):
        if path.name == 'config.json':raise KeyboardInterrupt
        return real(path, value)
    monkeypatch.setattr(advance.initialize, '_saved', interrupt)
    with pytest.raises(KeyboardInterrupt):transition.transition(**args, transport=peer)
    monkeypatch.setattr(advance.initialize, '_saved', real)
    assert transition.transition(**args, transport=peer)['status'] == 'transition_complete'
    assert peer.calls.count(('POST', '/sessions')) == 1


@pytest.mark.parametrize('change', ['route_bytes', 'next_capture', 'next_state', 'successor'])
def test_changed_resume_intent_refused_before_http(configured, tmp_path, monkeypatch, change):
    args, peer, *_ = setup(configured, tmp_path, monkeypatch)
    transition.transition(**dict(args, max_actions=1), transport=peer)
    if change == 'route_bytes':args['route_config'].write_bytes(args['route_config'].read_bytes()+b'\n')
    if change == 'next_capture':args['next_capture_id'] = 'another'
    if change == 'next_state':args['next_state_dir'] = tmp_path/'other-runner'
    if change == 'successor':args['successor_attempt'] = args['successor_attempt'][:-32]+'f'*32
    before = files(tmp_path)
    with pytest.raises(runner.Refused):transition.transition(**args, transport=NoHTTP())
    assert files(tmp_path) == before


def test_partial_prepare_exact_resume(configured, tmp_path, monkeypatch):
    args, peer, *_ = setup(configured, tmp_path, monkeypatch); real = single_write = boundary.single.manifest_write
    def interrupt(path, value):
        real(path, value)
        if path.name == boundary.RUNNER_ANCHOR:raise KeyboardInterrupt
    monkeypatch.setattr(boundary.single, 'manifest_write', interrupt)
    with pytest.raises(KeyboardInterrupt):transition.transition(**args, transport=NoHTTP())
    monkeypatch.setattr(boundary.single, 'manifest_write', single_write)
    assert transition.transition(**args, transport=peer)['status'] == 'transition_complete'


def test_cli_fixed_private_failure_and_paused_exit(monkeypatch, capsys):
    cli = ['--'+k for k in ('acknowledge-logical-analysis-boundary', 'finalize', 'export',
        'acknowledge-create-next-session', 'exclusive-writer', 'acknowledge-span-local-closure')]
    for key in ('state-dir', 'route-config', 'successor-attempt', 'next-state-dir',
                'next-job-dir', 'next-output-dir', 'next-capture-id'):
        cli += ['--'+key, 'private-secret-value']
    def fail(**kw):raise ValueError('private-secret-value')
    monkeypatch.setattr(transition, 'transition', fail)
    assert transition.main(cli) == 2
    assert 'private-secret-value' not in capsys.readouterr().out
    monkeypatch.setattr(transition, 'transition', lambda **kw: dict(status='transition_paused'))
    assert transition.main(cli) == 3
