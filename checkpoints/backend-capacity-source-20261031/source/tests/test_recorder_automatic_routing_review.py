"""Independent bounded-admission review using synthetic local data and TLS."""
from dataclasses import replace
import ftplib

import pytest

from audio_backend import recorder_receiver_completion as completion
from audio_backend import recorder_receiver_publication as publication
from audio_backend import recorder_receiver_routing as routing
from test_recorder_automatic_routing import automatic
from test_recorder_ftps_receiver import configured, connect, running, upload
from test_recorder_inbox import delivery
from test_recorder_receiver_completion_review import contents, write_one
from test_recorder_receiver_routing import read, setup_files


def fresh(source, digit='f'):
    return source.name[:-32] + digit * 32


def test_publication_byte_capacity_advances_while_raw_capacity_still_fits(
        configured, tmp_path, monkeypatch):
    config, raw, ready, state = automatic(configured, tmp_path)
    routes = routing.Routes(config)
    try:
        source = configured['source']
        owner, attempt = routes.begin(source.name)
        for name in completion.FILES:
            write_one(owner, attempt, source, name)
        actual = sum((source / name).stat().st_size for name in completion.FILES)
        reserve = sum(completion.LIMITS.values())
        monkeypatch.setattr(publication, 'MAX_STORAGE_BYTES', reserve + 2 * actual - 1)
        assert owner.admission_available()
        assert not owner.publication_admission_available()
        before = contents(configured['ready'])
        selected, new = routes.begin(fresh(source))
        assert selected is routes.owners[1] and new.path.parent == raw
        assert routes.active == 1 and contents(configured['ready']) == before
        assert not (configured['raw'] / fresh(source)).exists()
    finally:
        routes.close()


def test_publication_entry_limit_reserves_unfinished_attempts(
        configured, tmp_path, monkeypatch):
    config, raw, ready, state = automatic(configured, tmp_path)
    routes = routing.Routes(config)
    try:
        source = configured['source']
        owner, attempt = routes.begin(source.name)
        monkeypatch.setattr(publication.inbox, 'MAX_ENTRIES', 1)
        assert owner.admission_available()
        assert not list(configured['ready'].iterdir())
        assert not owner.publication_admission_available()
        selected, new = routes.begin(fresh(source))
        assert selected is routes.owners[1] and new.path.parent == raw
        assert not list(attempt.path.iterdir())
    finally:
        routes.close()


def test_real_ftps_post_rename_uncertainty_keeps_original_route_without_replay(
        configured, tmp_path, monkeypatch):
    config, raw, ready, state = automatic(configured, tmp_path, max_attempts=1)
    first = configured['source']
    second = delivery(tmp_path / 'second/templates', 1, close='ROTATION')
    real = publication._rename_noreplace
    calls = []

    def uncertain(source_fd, name, inbox_fd, attempt):
        calls.append(attempt)
        real(source_fd, name, inbox_fd, attempt)
        if attempt == first.name:
            raise OSError('synthetic failure after rename')

    with monkeypatch.context() as patch:
        patch.setattr(publication, '_rename_noreplace', uncertain)
        with running(config) as (server, routes):
            a = connect(server, config)
            b = connect(server, config)
            try:
                a.mkd(first.name)
                b.mkd(second.name)
                upload(b, second)
                frozen_second = contents(ready)
                with pytest.raises(ftplib.error_temp, match='451'):
                    upload(a, first)
                assert server.receiver_fault
                assert routes.active == 1 and routes.resolve(first.name) == 0
                owner = routes.owners[0]
                attempt = owner._attempts[first.name]
                assert owner.status(attempt)['status'] == 'uncertain'
                final_writer = attempt.writers['delivery.v1.txt']
                assert owner.writer_closed(final_writer, successful=True)['status'] == 'uncertain'
                assert calls.count(first.name) == 1
                assert contents(configured['ready'] / first.name) == contents(first)
                assert contents(ready) == frozen_second
                assert not (raw / first.name).exists() and not (ready / first.name).exists()
            finally:
                a.close()
                b.close()
    # An explicit restart restores readback, never upload handles or publication.
    with running(config) as (server, routes):
        client = connect(server, config)
        try:
            assert routes.resolve(first.name) == 0 and routes.active == 1
            client.cwd('/' + first.name)
            assert read(client, 'audio.wav') == (first / 'audio.wav').read_bytes()
            assert not routes.owners[0]._attempts
        finally:
            client.close()
    assert calls.count(first.name) == 1


def test_configured_active_higher_than_used_does_not_move_back(configured, tmp_path):
    config, raw, ready, state = automatic(configured, tmp_path)
    routes = routing.Routes(config)
    try:
        routes.begin(configured['source'].name)
    finally:
        routes.close()
    config = replace(config, binding=publication.bind(state), inbox_dir=ready)
    routes = routing.Routes(config)
    try:
        assert routes.active == 1
        owner, attempt = routes.begin(fresh(configured['source']))
        assert owner is routes.owners[1] and attempt.path.parent == raw
        assert routes.resolve(configured['source'].name) == 0
    finally:
        routes.close()


def test_all_generation_refusal_has_no_directory_or_reservation_effect(configured, tmp_path):
    config, raw, ready, state = automatic(configured, tmp_path, max_attempts=1)
    routes = routing.Routes(config)
    try:
        source = configured['source']
        routes.begin(source.name)
        routes.begin(fresh(source))
        names = [set(owner._names) for owner in routes.owners]
        charges = [owner._charged_bytes for owner in routes.owners]
        with pytest.raises(publication.Refused, match='all_generations_full'):
            routes.begin(fresh(source, 'e'))
        assert names == [set(owner._names) for owner in routes.owners]
        assert charges == [owner._charged_bytes for owner in routes.owners]
        assert not any((path / fresh(source, 'e')).exists() for path in (configured['raw'], raw))
        assert not routes._admission_failed
    finally:
        routes.close()


def test_automatic_setup_reordered_retained_prefix_has_no_output(configured, tmp_path):
    second, _ = setup_files(configured, tmp_path)
    endpoint = tmp_path / 'automatic.json'
    routing.configure(endpoint_config=configured['path'],
                      generation_configs=[configured['path'], second], active_generation=0,
                      output=endpoint, auto_advance_generations=True)
    before = contents(tmp_path)
    output = tmp_path / 'reordered.json'
    with pytest.raises(publication.Refused, match='automatic_order_changed'):
        routing.configure(endpoint_config=endpoint,
                          generation_configs=[second, configured['path']], active_generation=0,
                          output=output, auto_advance_generations=True)
    assert not output.exists() and contents(tmp_path) == before
