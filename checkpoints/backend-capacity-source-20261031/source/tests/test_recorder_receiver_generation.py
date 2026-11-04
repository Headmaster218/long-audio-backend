"""Prepare-only, no listener or private user data."""
import fcntl
import json
import os

import pytest

from audio_backend import recorder_receiver_generation as generation
from audio_backend import recorder_receiver_publication as publication
from audio_backend import recorder_ftps_receiver as service
from test_recorder_ftps_receiver import configured
from test_recorder_receiver_completion_review import contents


def run(case, root, **kw):
    return generation.prepare(old_config=case['path'], root=root, port=2222, **kw)


def test_prepare_inherits_and_preserves(configured, tmp_path):
    c = configured
    c['data'].update(max_attempts=17, max_raw_bytes=123456789,
                     max_readback_bytes=54321, session_seconds=777)
    c['path'].write_text(json.dumps(c['data']))
    before = contents(tmp_path)
    root = tmp_path/'next'
    result = run(c, root)
    after = contents(tmp_path)
    assert all(after[k] == v for k, v in before.items())
    new = json.loads((root/'service.json').read_bytes())
    for key, value in c['data'].items():
        if key not in ('binding', 'inbox_dir', 'port'):
            assert new[key] == value
    assert new['port'] == 2222
    assert service.load_config(root/'service.json').max_attempts == 17
    assert not result['service_started'] and not result['routing_changed']
    assert 'synthetic' not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert not list((root/'raw').iterdir()) and not list((root/'inbox').iterdir())
    for p in root.rglob('*'):
        assert p.stat().st_mode & 0o077 == 0
    before = contents(tmp_path)
    assert run(c, root, resume=True) == result
    assert contents(tmp_path) == before


def test_same_port_refused_before_write(configured, tmp_path):
    with pytest.raises(publication.Refused, match='different_control'):
        generation.prepare(old_config=configured['path'], root=tmp_path/'next', port=2121)
    assert not (tmp_path/'next').exists()


def test_passive_independence(configured, tmp_path):
    c = configured
    c['data']['passive_ports'] = [30000, 30002]
    c['path'].write_text(json.dumps(c['data']))
    with pytest.raises(publication.Refused, match='overlap'):
        run(c, tmp_path/'next')
    assert not (tmp_path/'next').exists()
    run(c, tmp_path/'next', passive_ports=[30003, 30005])


@pytest.mark.parametrize('name', ['raw', 'preparation.lock', 'publication.lock'])
def test_old_busy_no_mutation(configured, tmp_path, name):
    c = configured
    path = c['raw'] if name == 'raw' else c['state']/name
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = contents(tmp_path)
        with pytest.raises(publication.Refused, match='busy'):run(c, tmp_path/'next')
        assert contents(tmp_path) == before
    finally:os.close(fd)


@pytest.mark.parametrize('boundary', ['intent.json', 'raw.json', 'inbox.json', 'service.json', 'complete.json'])
def test_completed_write_crash_resume(configured, tmp_path, monkeypatch, boundary):
    original = generation._write
    def crash(path, value):
        original(path, value)
        if path.name == boundary:raise KeyboardInterrupt
    monkeypatch.setattr(generation, '_write', crash)
    with pytest.raises(KeyboardInterrupt):run(configured, tmp_path/'next')
    monkeypatch.setattr(generation, '_write', original)
    assert run(configured, tmp_path/'next', resume=True)['status'] == 'prepared'


@pytest.mark.parametrize('boundary', ['intent.json', 'raw.json', 'inbox.json', 'service.json', 'complete.json'])
def test_partial_write_preserved_refused(configured, tmp_path, monkeypatch, boundary):
    original = generation._write
    def crash(path, value):
        if path.name == boundary:
            path.write_bytes(b'{');path.chmod(0o600)
            raise KeyboardInterrupt
        return original(path, value)
    monkeypatch.setattr(generation, '_write', crash)
    with pytest.raises(KeyboardInterrupt):run(configured, tmp_path/'next')
    monkeypatch.setattr(generation, '_write', original)
    before = contents(tmp_path)
    with pytest.raises(Exception):run(configured, tmp_path/'next', resume=True)
    assert contents(tmp_path) == before


def test_publication_complete_crash_resumable(configured, tmp_path, monkeypatch):
    original = publication.configure
    def crash(**kw):
        original(**kw);raise KeyboardInterrupt
    monkeypatch.setattr(publication, 'configure', crash)
    with pytest.raises(KeyboardInterrupt):run(configured, tmp_path/'next')
    monkeypatch.setattr(publication, 'configure', original)
    assert run(configured, tmp_path/'next', resume=True)['status'] == 'prepared'


def test_partial_publication_refused(configured, tmp_path, monkeypatch):
    original = publication.configure
    def crash(**kw):
        kw['state_dir'].mkdir(mode=0o700);raise KeyboardInterrupt
    monkeypatch.setattr(publication, 'configure', crash)
    with pytest.raises(KeyboardInterrupt):run(configured, tmp_path/'next')
    monkeypatch.setattr(publication, 'configure', original)
    before = contents(tmp_path)
    with pytest.raises(Exception):run(configured, tmp_path/'next', resume=True)
    assert contents(tmp_path) == before


def test_changed_old_config_and_arguments(configured, tmp_path):
    root = tmp_path/'next';run(configured, root)
    before = contents(tmp_path)
    with pytest.raises(publication.Refused):
        generation.prepare(old_config=configured['path'], root=root, port=2223, resume=True)
    assert contents(tmp_path) == before
    configured['path'].write_text(json.dumps(dict(configured['data'], max_attempts=12)))
    before = contents(tmp_path)
    with pytest.raises(publication.Refused):run(configured, root, resume=True)
    assert contents(tmp_path) == before


@pytest.mark.parametrize('kind', ['unknown', 'partial_raw', 'missing_raw', 'active_data', 'symlink'])
def test_unknown_or_corrupt_components_not_repaired(configured, tmp_path, kind):
    root = tmp_path/'next';run(configured, root)
    if kind == 'unknown':(root/'unknown').write_text('x')
    if kind == 'partial_raw':(root/'raw.json').unlink()
    if kind == 'missing_raw':
        (root/'raw').rmdir();(root/'raw.json').unlink()
    if kind == 'active_data':(root/'raw'/'unconfirmed').mkdir(mode=0o700)
    if kind == 'symlink':
        (root/'raw').rmdir();(root/'raw').symlink_to(configured['raw'])
    before = contents(tmp_path)
    with pytest.raises(Exception):run(configured, root, resume=True)
    assert contents(tmp_path) == before


def test_root_without_intent_refused(configured, tmp_path):
    root = tmp_path/'next';root.mkdir(mode=0o700)
    with pytest.raises(Exception):run(configured, root, resume=True)
    assert list(root.iterdir()) == []


def test_cli_redacts_interrupt_and_failure(configured, tmp_path, monkeypatch, capsys):
    args=['--old-config', str(configured['path']), '--root', str(tmp_path/'next'), '--port', '2222']
    def crash(**kw):raise KeyboardInterrupt
    monkeypatch.setattr(generation, 'prepare', crash)
    assert generation.main(args) == 130
    assert 'synthetic' not in capsys.readouterr().out
    def fail(**kw):raise ValueError('private password or path')
    monkeypatch.setattr(generation, 'prepare', fail)
    assert generation.main(args) == 2
    assert 'private password' not in capsys.readouterr().out


def test_unreceipted_mkdir_preserved(configured, tmp_path, monkeypatch):
    original = generation._write
    def crash(path, value):
        if path.name == 'raw.json':raise KeyboardInterrupt
        return original(path, value)
    monkeypatch.setattr(generation, '_write', crash)
    with pytest.raises(KeyboardInterrupt):run(configured, tmp_path/'next')
    monkeypatch.setattr(generation, '_write', original)
    before = contents(tmp_path)
    with pytest.raises(publication.Refused, match='unconfirmed_directory'):
        run(configured, tmp_path/'next', resume=True)
    assert contents(tmp_path) == before


@pytest.mark.parametrize('component', ['root', 'raw', 'preparation.lock', 'publication.lock'])
def test_new_generation_busy_refuses_without_changes(configured, tmp_path, component):
    root = tmp_path/'next';run(configured, root)
    path = root if component == 'root' else root/component if component == 'raw' else root/'publication'/component
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = contents(tmp_path)
        with pytest.raises(publication.Refused, match='busy'):run(configured, root, resume=True)
        assert contents(tmp_path) == before
    finally:os.close(fd)


def test_changed_old_during_setup_never_completes(configured, tmp_path, monkeypatch):
    original = generation._write
    def change(path, value):
        original(path, value)
        if path.name == 'service.json':
            configured['path'].write_text(json.dumps(dict(configured['data'], max_attempts=12)))
    monkeypatch.setattr(generation, '_write', change)
    with pytest.raises(publication.Refused, match='old_config_changed'):
        run(configured, tmp_path/'next')
    assert not (tmp_path/'next'/'complete.json').exists()
    assert (tmp_path/'next'/'service.json').exists()


def test_no_data_tree_nesting(configured, tmp_path):
    with pytest.raises(publication.Refused, match='overlap'):
        run(configured, configured['raw']/'next')
    assert not (configured['raw']/'next').exists()


def test_nested_generation_refused_and_original_still_verifies(configured, tmp_path):
    root = tmp_path/'next';run(configured, root)
    before = contents(tmp_path)
    with pytest.raises(publication.Refused, match='nested_generation_root'):
        generation.prepare(old_config=root/'service.json', root=root/'next', port=2223)
    assert contents(tmp_path) == before
    assert run(configured, root, resume=True)['status'] == 'prepared'
    assert contents(tmp_path) == before
    assert generation.prepare(old_config=root/'service.json', root=tmp_path/'next-two',
                              port=2223)['status'] == 'prepared'
    assert run(configured, root, resume=True)['status'] == 'prepared'
