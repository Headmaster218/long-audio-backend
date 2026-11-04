"""Model-free bounded leaf recovery. No inference, live data, or service restart."""
import asyncio
import json
import time

import pytest

from audio_backend import recorder_inbox_runner as runner
from audio_backend import recorder_generation_transition as transition
from audio_backend import recording_uploader as u
from audio_backend.storage import StrictMutationBusy
from test_recorder_inbox_runner import example, invoke, Peer
from test_recorder_inbox_watch import watching
from test_recorder_generation_transition import setup
from test_recorder_ftps_receiver import configured
from test_recording_session_contract import handler_app

BASE = '/sessions/known'
CAPTURE = BASE + '/recordings/extended'


def busy(reason='strict_mutation_busy', **extra):
    return u.HTTPFailure(503, json.dumps(dict(detail=u.STRICT_BUSY_DETAIL,
        admission_reason=reason, **extra)), .001)


class Scripted:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def transport(peer, **kwargs):
    options = dict(watch=True, busy_read_retries=1)
    options.update(kwargs)
    guard = options.pop('guard', u.Guard(u.Limits(disk_floor=0)))
    check = options.pop('check', lambda writes: None)
    return runner._Transport(peer, guard, dict(session_id='known', capture_id='extended'),
        options.pop('max_requests', 100), 10, check, **options)


@pytest.mark.parametrize('reason', sorted(u.STRICT_BUSY_REASONS))
def test_typed_app_code_and_parser_preserve_human_detail(tmp_path, reason):
    with handler_app(tmp_path/'server') as (_, app):
        handler = app.exception_handlers[StrictMutationBusy]
        response = asyncio.run(handler(None, StrictMutationBusy(reason)))
        assert response.status_code == 503
        assert json.loads(response.body) == dict(detail=u.STRICT_BUSY_DETAIL, admission_reason=reason)
        assert u.HTTPFailure(503, response.body.decode()).admission_reason == reason
        unknown = asyncio.run(handler(None, StrictMutationBusy('private-path-secret')))
        assert json.loads(unknown.body) == dict(detail=u.STRICT_BUSY_DETAIL)
        assert b'private-path-secret' not in unknown.body


@pytest.mark.parametrize('status,detail', [
    (500, json.dumps(dict(detail=u.STRICT_BUSY_DETAIL, admission_reason='strict_mutation_busy'))),
    (503.0, json.dumps(dict(detail=u.STRICT_BUSY_DETAIL, admission_reason='strict_mutation_busy'))),
    (503, u.STRICT_BUSY_DETAIL),
    (503, json.dumps(dict(detail=u.STRICT_BUSY_DETAIL))),
    (503, json.dumps(dict(detail='another detail', admission_reason='strict_mutation_busy'))),
    (503, json.dumps(dict(detail=u.STRICT_BUSY_DETAIL, admission_reason='unknown'))),
    (503, json.dumps(dict(detail=u.STRICT_BUSY_DETAIL, admission_reason=True))),
    (503, json.dumps(dict(detail=u.STRICT_BUSY_DETAIL, admission_reason='strict_mutation_busy', extra=1))),
    (503, '{"detail":"'+u.STRICT_BUSY_DETAIL+'","admission_reason":"strict_mutation_busy","admission_reason":"strict_mutation_busy"}'),
    (503, '['+json.dumps(dict(detail=u.STRICT_BUSY_DETAIL, admission_reason='strict_mutation_busy'))+']'),
    (503, ' ' * u.JSON_BYTES + '{}'),
])
def test_only_exact_bounded_typed_shape_is_recognized(status, detail):
    assert u.HTTPFailure(status, detail).admission_reason is None


def test_revision_marker_is_unchanged():
    assert u.HTTPFailure(409, '{"detail":"transcript_revision_changed"}').response_reason == 'transcript_revision_changed'


@pytest.mark.parametrize('path', [BASE, BASE+'/recording-contract', CAPTURE,
    CAPTURE+'/pages/1', BASE+'/chunks?limit=1', BASE+'/chunks?after=0&limit=16',
    BASE+'/chunks?after=55&limit=500'])
def test_exact_known_get_repeats_once_charges_failure_and_rechecks(tmp_path, monkeypatch, path):
    peer = Scripted(busy(), {'safe': True})
    checks = []; pauses = []
    http = transport(peer, check=lambda writes: checks.append(writes))
    monkeypatch.setattr(http.guard, 'pause', lambda seconds: pauses.append(seconds))
    assert http.request('GET', path) == {'safe': True}
    assert peer.calls == [('GET', path, {}), ('GET', path, {})]
    assert checks == [False, False] and len(pauses) == 1
    assert http.requests == 2 and http.writes == 0 and http.busy_read_retries_used == 1
    assert http.bytes == u.JSON_BYTES + len(runner.single._json({'safe': True}))


@pytest.mark.parametrize('error', [u.HTTPFailure(503), busy(extra='private-secret'),
    u.Uncertain('private-secret'), TimeoutError('private-secret')])
def test_unknown_503_uncertain_and_timeout_never_retry(monkeypatch, error):
    peer = Scripted(error)
    http = transport(peer, busy_read_retries=3)
    monkeypatch.setattr(http.guard, 'pause', lambda *a: pytest.fail('retry'))
    with pytest.raises((runner._WatchTransportStopped, TimeoutError)):
        http.request('GET', CAPTURE)
    assert len(peer.calls) == http.requests == 1 and http.bytes == u.JSON_BYTES


@pytest.mark.parametrize('method,path,kwargs', [
    ('PUT', CAPTURE+'/pages/1/sources?source_start_frame=0', {'body': {}}),
    ('POST', CAPTURE+'/finish', {'body': {}}),
    ('GET', CAPTURE, {'body': None}),
    ('GET', CAPTURE+'/unexpected', {}),
    ('GET', CAPTURE+'/pages/01', {}),
    ('GET', CAPTURE+'/pages/1?extra=1', {}),
    ('GET', BASE+'/chunks?after=0&limit=16&extra=1', {}),
    ('GET', BASE+'/transcript?cursor=0&projection=whole-primary-v1', {}),
])
def test_never_retry_write_kwargs_or_unlisted_target(monkeypatch, method, path, kwargs):
    peer = Scripted(busy())
    http = transport(peer, busy_read_retries=3)
    monkeypatch.setattr(http.guard, 'pause', lambda *a: pytest.fail('retry'))
    with pytest.raises(runner._WatchTransportStopped):
        http.request(method, path, **kwargs)
    assert peer.calls == [(method, path, kwargs)]


def test_default_off_and_export_recovery_contract_never_retries(monkeypatch):
    for options in ({'busy_read_retries': 0}, {'busy_read_retries': 3}):
        peer = Scripted(busy())
        http = transport(peer, **options)
        http.export_recovery_phase = bool(options['busy_read_retries'])
        monkeypatch.setattr(http.guard, 'pause', lambda *a: pytest.fail('retry'))
        with pytest.raises(runner._WatchTransportStopped):
            http.request('GET', CAPTURE)
        assert http.requests == 1 and http.bytes == (u.JSON_BYTES if http.export_recovery_phase else 0)
        assert http.busy_read_retries_used == 0


def test_retry_allowance_is_global_not_per_leaf(monkeypatch):
    peer = Scripted(busy(), {}, busy(), {}, busy())
    http = transport(peer, busy_read_retries=2)
    monkeypatch.setattr(http.guard, 'pause', lambda *a: None)
    http.request('GET', BASE)
    http.request('GET', CAPTURE)
    with pytest.raises(runner._WatchTransportStopped):
        http.request('GET', CAPTURE+'/pages/1')
    assert http.requests == 5 and http.busy_read_retries_used == 2
    assert http.bytes == 3*u.JSON_BYTES + 2*len(runner.single._json({}))


@pytest.mark.parametrize('bound', ['bytes', 'requests', 'cancel', 'deadline', 'local_binding'])
def test_every_retry_rechecks_original_guards(monkeypatch, bound):
    peer = Scripted(busy(), {})
    cancelled = [False]; changed = [False]
    def check(writes):
        runner.require(not changed[0], 'runner_config_changed')
    guard = u.Guard(u.Limits(disk_floor=0), cancel=lambda: cancelled[0])
    http = transport(peer, guard=guard, check=check,
        max_bytes=u.JSON_BYTES if bound == 'bytes' else runner.MAX_NETWORK_BYTES,
        max_requests=1 if bound == 'requests' else 100)
    def pause(seconds):
        if bound == 'cancel': cancelled[0] = True
        if bound == 'deadline': guard.end = time.monotonic() - 1
        if bound == 'local_binding': changed[0] = True
    monkeypatch.setattr(guard, 'pause', pause)
    with pytest.raises((runner.Refused, InterruptedError, u.Incomplete)):
        http.request('GET', CAPTURE)
    assert http.requests == len(peer.calls) == 1 and http.bytes == u.JSON_BYTES
    assert http.busy_read_retries_used == 0


def test_real_pause_cancels_within_original_deadline():
    peer = Scripted(busy())
    guard = u.Guard(u.Limits(disk_floor=0, deadline_seconds=.03))
    http = transport(peer, guard=guard)
    start = time.monotonic()
    with pytest.raises(u.Incomplete, match='deadline'):
        http.request('GET', CAPTURE)
    assert time.monotonic()-start < .5 and len(peer.calls) == 1


def test_watch_does_not_restart_helper_or_duplicate_committed_put(tmp_path, monkeypatch):
    paths, config, state = example(tmp_path, 1)
    runner.configure(state_dir=state, config=config)
    class AfterWrite(Peer):
        fired = False
        calls = None
        def request(self, method, path, **kwargs):
            self.calls.append((method, path))
            if method == 'GET' and self.puts and not self.fired:
                self.fired = True
                raise busy('strict_publication_busy')
            return super().request(method, path, **kwargs)
    peer = AfterWrite(); peer.calls = []
    monkeypatch.setattr(u.Guard, 'pause', lambda *a: None)
    result = watching(state, peer, finalize=True, max_actions=2, busy_read_retries=1)
    assert result['status'] == 'action_limit' and result['helper_actions'] == 2
    assert result['busy_read_retries_used'] == 1 and len(peer.puts) == 1
    assert result['network_requests'] == len(peer.calls)
    assert sum(method == 'PUT' for method, _ in peer.calls) == 1
    assert sum((method, path) == ('POST', CAPTURE+'/pages/1/seal') for method, path in peer.calls) == 1


@pytest.mark.parametrize('value', [-1, 4, True, 1.0, '1'])
def test_bad_retry_bounds_before_any_effect(tmp_path, value):
    _, config, state = example(tmp_path, 1)
    runner.configure(state_dir=state, config=config)
    with pytest.raises(runner.Refused, match='busy_read_retry_bound'):
        watching(state, Scripted(), busy_read_retries=value)
    assert not (state/'input-000.intent.json').exists()


@pytest.mark.parametrize('retries', [0, 1, 3])
def test_coordinator_counts_actual_busy_attempts_and_retains_safe_stop(configured, tmp_path, monkeypatch, retries):
    args, peer, _, *_ = setup(configured, tmp_path, monkeypatch)
    real = peer.request
    calls = []
    def fail(method, path, **kwargs):
        calls.append((method, path))
        if method == 'GET' and path == CAPTURE:
            raise busy('strict_archive_busy')
        return real(method, path, **kwargs)
    peer.request = fail
    monkeypatch.setattr(u.Guard, 'pause', lambda *a: None)
    result = transition.transition(**args, transport=peer, busy_read_retries=retries)
    assert result['status'] == 'transition_paused' and result['stop_reason'] == 'unresolved'
    assert result['network_requests'] == len(calls)
    assert sum(path == CAPTURE for _, path in calls) == 1+retries
    assert result['network_response_bytes'] >= (1+retries)*u.JSON_BYTES
    assert result['stop_details']['http_status'] == 503
    assert result['stop_details']['admission_reason'] == 'strict_archive_busy'
    assert result['stop_details']['possible_remote_effects'] is True
    assert not args['next_state_dir'].exists()
    assert 'private' not in json.dumps(result) and CAPTURE not in json.dumps(result)


def test_coordinator_failed_byte_reservation_blocks_second_get(configured, tmp_path, monkeypatch):
    args, peer, _, *_ = setup(configured, tmp_path, monkeypatch)
    calls = []
    def watch(**kwargs):
        assert kwargs['busy_read_retries'] == 3
        http = kwargs['transport']
        with pytest.raises(u.HTTPFailure):
            http.request('GET', CAPTURE)
        with pytest.raises(runner.Refused, match='network_byte_bound'):
            http.request('GET', CAPTURE)
        return dict(status='unresolved', reason='runner_network_byte_bound')
    def fail(method, path, **kwargs):
        calls.append((method, path)); raise busy()
    monkeypatch.setattr(runner, 'watch', watch)
    peer.request = fail
    result = transition.transition(**args, transport=peer, busy_read_retries=3, max_network_bytes=u.JSON_BYTES)
    assert result['network_requests'] == 1 and result['network_response_bytes'] == u.JSON_BYTES
    assert calls == [('GET', CAPTURE)] and not args['next_state_dir'].exists()


def test_busy_identity_leaf_still_rejects_changed_generation_before_write(monkeypatch):
    from audio_backend import recorder_runner_initialize as initialize
    handoff = initialize.reviewed_handoff('old', 'reviewed.family', 'a'*32, 'b'*64)
    changed_bank = dict(handoff, generation='c'*32, primary_associations_enabled=True,
        explicitly_retained=True, automatic_enrollment=False)
    peer = Scripted(dict(id='old', identity_group='reviewed.family', processing_mode='legacy'),
        dict(id='known', identity_group='reviewed.family', processing_mode='legacy'),
        busy(), changed_bank)
    http = transport(peer, reviewed_identity_handoff=handoff)
    monkeypatch.setattr(http.guard, 'pause', lambda *a: None)
    with pytest.raises(runner.Refused, match='bank_changed'):
        http.request('POST', CAPTURE+'/finish', body={})
    assert all(method == 'GET' and not kwargs for method, _, kwargs in peer.calls)
    assert peer.calls[-1] == peer.calls[-2]
    assert http.requests == 4 and http.writes == 0 and http.busy_read_retries_used == 1


def test_watch_cli_default_and_explicit_flag(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(runner, 'watch', lambda **kwargs: seen.append(kwargs) or dict(status='poll_limit'))
    args = ['watch', '--state-dir', '/unused', '--exclusive-writer', '--acknowledge-span-local-closure']
    assert runner.main(args) == 0 and seen[-1]['busy_read_retries'] == 0
    assert runner.main(args+['--busy-read-retries', '3']) == 0
    assert seen[-1]['busy_read_retries'] == 3
    with pytest.raises(SystemExit): runner.main(args+['--busy-read-retries', '4'])
    capsys.readouterr()


def test_coordinator_cli_default_and_explicit_flag(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(transition, 'transition', lambda **kwargs: seen.append(kwargs) or dict(status='transition_paused'))
    args = ['--'+key for key in ('acknowledge-logical-analysis-boundary', 'finalize', 'export',
        'acknowledge-create-next-session', 'exclusive-writer', 'acknowledge-span-local-closure')]
    for key in ('state-dir', 'route-config', 'successor-attempt', 'next-state-dir',
        'next-job-dir', 'next-output-dir', 'next-capture-id'):
        args += ['--'+key, 'unused']
    assert transition.main(args) == 3 and seen[-1]['busy_read_retries'] == 0
    assert transition.main(args+['--busy-read-retries', '3']) == 3
    assert seen[-1]['busy_read_retries'] == 3
    with pytest.raises(SystemExit): transition.main(args+['--busy-read-retries', '4'])
    capsys.readouterr()


@pytest.mark.parametrize('value', [-1, 4, True, 1.0, '1'])
def test_coordinator_rejects_invalid_retry_bound_before_boundary(configured, tmp_path, monkeypatch, value):
    args, peer, config, *_ = setup(configured, tmp_path, monkeypatch)
    with pytest.raises(runner.Refused, match='busy_read_retry_bound'):
        transition.transition(**args, transport=peer, busy_read_retries=value)
    assert not (runner._path(config['job_dir'])/transition.boundary.NAME).exists()
    assert peer.calls == []


def test_real_busy_pause_honors_cancellation():
    peer = Scripted(busy())
    start = time.monotonic()
    guard = u.Guard(u.Limits(disk_floor=0), cancel=lambda: time.monotonic()-start >= .02)
    http = transport(peer, guard=guard)
    with pytest.raises(InterruptedError):
        http.request('GET', CAPTURE)
    assert time.monotonic()-start < .5 and len(peer.calls) == 1


def test_identity_coordinator_retries_only_old_read_and_counts_nested_calls(configured, tmp_path, monkeypatch):
    from test_recorder_reviewed_identity_handoff import BankPeer, FLAGS
    args, pair, _, *_ = setup(configured, tmp_path, monkeypatch)
    peer = BankPeer(pair, source='known', target='next-session')
    real = peer.request
    calls = []; bytes_used = []; failed = [False]
    def request(method, path, **kwargs):
        calls.append((method, path))
        if method == 'GET' and path == CAPTURE and not failed[0]:
            failed[0] = True
            bytes_used.append(u.JSON_BYTES)
            raise busy()
        try:
            result = real(method, path, **kwargs)
        except BaseException:
            bytes_used.append(u.JSON_BYTES)
            raise
        bytes_used.append(len(runner.single._json(result)))
        return result
    peer.request = request
    monkeypatch.setattr(u.Guard, 'pause', lambda *a: None)
    result = transition.transition(**args, **FLAGS, transport=peer, busy_read_retries=1)
    assert result['status'] == 'transition_complete'
    assert result['network_requests'] == len(calls)
    assert result['network_writes'] == sum(method != 'GET' for method, _ in calls)
    assert result['network_response_bytes'] == sum(bytes_used)
    assert calls.count(('POST', '/sessions')) == 1
    failed_index = calls.index(('GET', CAPTURE))
    assert calls[failed_index+1] == calls[failed_index]
    assert len(peer.bodies) == 1
