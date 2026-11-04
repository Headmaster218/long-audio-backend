"""Stopped status controls; no network, models, repair or transfer adoption."""
import fcntl
import json
import os
from types import SimpleNamespace

import pytest

from audio_backend import recorder_receiver_status as status
from audio_backend import recorder_receiver_publication as publication
from audio_backend import recorder_ftps_receiver as service
from test_recorder_receiver_completion_review import example, adapter, contents, write_one


def inspect(state, ready, **kwargs):
    return status.observe(binding=publication.bind(state), inbox_dir=ready, **kwargs)


def test_empty_read_only_metadata_snapshot(tmp_path):
    raw, ready, state, source = example(tmp_path)
    before = contents(tmp_path)
    result = inspect(state, ready)
    assert result['raw']['retained_attempts'] == result['publication']['published_entries'] == 0
    assert result['raw']['remaining_attempts'] == 256
    assert not result['blockers'] and result['network_requests'] == 0
    assert result['content_validated'] is False and result['deletion_safety_assessed'] is False
    assert contents(tmp_path) == before
    assert str(tmp_path) not in json.dumps(result) and source.name not in json.dumps(result)


def test_published_and_partial_counts_without_adoption(tmp_path):
    raw, ready, state, source = example(tmp_path)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        for name in publication.FILES:
            write_one(owner, attempt, source, name)
    pending = state/'prepare'/('pending-'+'a'*32)
    pending.mkdir(mode=0o700)
    file = pending/'audio.wav';file.write_bytes(b'partial');file.chmod(0o600)
    before = contents(tmp_path)
    result = inspect(state, ready, max_attempts=1)
    assert result['raw']['retained_attempts'] == result['publication']['published_entries'] == 1
    assert result['publication']['preparation_entries'] == 1
    assert result['publication']['retained_file_bytes'] == result['raw']['retained_bytes'] + 7
    assert 'raw_attempt_limit' in result['blockers']
    assert contents(tmp_path) == before


def test_live_receiver_refused_without_disturbance(tmp_path):
    raw, ready, state, source = example(tmp_path)
    with adapter(state, ready) as owner:
        with pytest.raises(publication.Refused, match='busy'):
            inspect(state, ready)
        attempt = owner.begin_attempt(source.name)
        assert owner.status(attempt)['status'] == 'receiving'


@pytest.mark.parametrize('name', ['preparation.lock', 'publication.lock'])
def test_busy_publisher_nonblocking_and_releases_raw(tmp_path, name):
    raw, ready, state, source = example(tmp_path)
    with (state/name).open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(publication.Refused, match='busy'):
            inspect(state, ready)
    with adapter(state, ready):
        pass


@pytest.mark.parametrize('kind', ['symlink', 'extra', 'hardlink', 'public', 'oversized'])
def test_invalid_raw_refuses_counts(tmp_path, kind):
    raw, ready, state, source = example(tmp_path)
    attempt = raw/source.name;attempt.mkdir(mode=0o700)
    path = attempt/'audio.wav'
    if kind == 'symlink':
        path.symlink_to(source/'audio.wav')
    else:
        path.write_bytes(b'x');path.chmod(0o600)
        if kind == 'extra': (attempt/'unexpected').write_bytes(b'x')
        if kind == 'hardlink': os.link(path, tmp_path/'other')
        if kind == 'public': path.chmod(0o644)
        if kind == 'oversized':
            with path.open('r+b') as stream:stream.truncate(publication.LIMITS['audio.wav']+1)
    with pytest.raises(publication.Refused):inspect(state, ready)


def test_detected_change_has_no_success(tmp_path, monkeypatch):
    raw, ready, state, source = example(tmp_path)
    original = status.completion._retained_raw
    calls = []
    def changing(*args):
        value = original(*args)
        calls.append(1)
        return (value[0], value[1]+1) if len(calls) == 2 else value
    monkeypatch.setattr(status.completion, '_retained_raw', changing)
    with pytest.raises(publication.Refused, match='changed'):inspect(state, ready)
    with adapter(state, ready):pass


@pytest.mark.parametrize('kwargs', [{'max_attempts':0}, {'max_raw_bytes':0}, {'deadline_seconds':0}])
def test_invalid_bounds(tmp_path, kwargs):
    raw, ready, state, source = example(tmp_path)
    with pytest.raises(publication.Refused):inspect(state, ready, **kwargs)


def test_cli_uses_configured_limits_and_redacts_errors(tmp_path, monkeypatch, capsys):
    raw, ready, state, source = example(tmp_path)
    config = SimpleNamespace(binding=publication.bind(state), inbox_dir=ready,
                             max_attempts=7, max_raw_bytes=12)
    monkeypatch.setattr(service, 'load_config', lambda _: config)
    assert status.main(['--config', '/not/read']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['raw']['attempt_limit'] == 7 and result['raw']['byte_limit'] == 12
    assert result['blockers'] == ['largest_file_reservation_unavailable']
    def failure(_):raise OSError('private filename password-value')
    monkeypatch.setattr(service, 'load_config', failure)
    assert status.main(['--config', '/private/path']) == 2
    output = capsys.readouterr().out
    assert 'password-value' not in output and '/private/path' not in output
    assert json.loads(output)['counts_available'] is False
