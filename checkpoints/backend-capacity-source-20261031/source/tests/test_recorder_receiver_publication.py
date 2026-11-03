"""Finite local receiver/publication tests. No FTPS service or inference."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import threading
import time

import pytest

from audio_backend import recorder_receiver_publication as receiver
from audio_backend import recorder_inbox as inbox
from audio_backend import recorder_inbox_runner as runner
from audio_backend import recording_uploader as u
from test_recorder_inbox import delivery
from test_recorder_inbox_runner import example
from test_extended_recorder_intake import Peer


def layout(tmp_path, count=1):
    paths, config, runner_state = example(tmp_path, count)
    staging = tmp_path/'staging'; staging.mkdir(mode=0o700)
    sources = []
    for path in paths:
        source = staging/path.name; path.rename(source); sources.append(source)
    state = tmp_path/'receiver'
    receiver.configure(state_dir=state, staging_dir=staging, inbox_dir=tmp_path/'inbox',
                       exclusive_receiver=True)
    config['receiver_publication'] = receiver.bind(state)
    return sources, paths, state, config, runner_state


def publish(state, source, **kwargs):
    return receiver.publish_completed(state_dir=state, delivery=source,
                                      completed_delivery=True, **kwargs)


def bytes_in(path):
    return {name: (path/name).read_bytes() for name in receiver.FILES}


def test_publish_exact_original_identity_independent_copy_and_idempotent_restart(tmp_path):
    sources, paths, state, config, _ = layout(tmp_path)
    before = bytes_in(sources[0])
    result = publish(state, sources[0])
    assert result['status'] == 'published' and result['created_and_fsynced']
    assert result['published_path'] == str(paths[0]) and result['attempt'] == sources[0].name
    assert result['delivery']['sequence'] == 23 and result['delivery']['epoch'] == 7
    assert bytes_in(paths[0]) == bytes_in(sources[0]) == before
    assert {p.name for p in paths[0].iterdir()} == set(receiver.FILES)
    assert paths[0].stat().st_mode & 0o777 == 0o700
    for name in receiver.FILES:
        assert (paths[0]/name).stat().st_mode & 0o777 == 0o600
        assert (paths[0]/name).stat().st_nlink == 1
        assert (paths[0]/name).stat().st_ino != (sources[0]/name).stat().st_ino
    inodes = [p.stat().st_ino for p in paths[0].iterdir()]
    assert publish(state, sources[0])['status'] == 'already_published'
    assert [p.stat().st_ino for p in paths[0].iterdir()] == inodes
    assert not list((state/'prepare').iterdir())
    observed = receiver.inventory(config['receiver_publication'], tmp_path/'inbox')
    assert observed['deliveries'][0]['status'] == 'observed_valid'
    assert not observed['intake_authorized'] and not observed['immutable_publication_proven']


def test_explicit_completion_and_empty_owned_inbox_required(tmp_path):
    staging = tmp_path/'raw'; staging.mkdir(mode=0o700)
    ready = tmp_path/'ready'; ready.mkdir(mode=0o700)
    (ready/'unowned').touch()
    with pytest.raises(receiver.Refused, match='initial_empty'):
        receiver.configure(state_dir=tmp_path/'receiver', staging_dir=staging,
                           inbox_dir=ready, exclusive_receiver=True)
    assert not (tmp_path/'receiver').exists()
    with pytest.raises(receiver.Refused, match='completed_delivery_required'):
        receiver.publish_completed(state_dir=tmp_path/'none', delivery=staging/'none')


@pytest.mark.parametrize('change', ['partial_marker', 'growing_audio', 'extra', 'symlink', 'hardlink'])
def test_raw_unfinished_or_unsafe_delivery_never_enters_inbox(tmp_path, change):
    sources, paths, state, _, _ = layout(tmp_path)
    source = sources[0]
    if change == 'partial_marker': (source/'delivery.v1.txt').write_bytes(b'RECORDER-')
    elif change == 'growing_audio':
        with (source/'audio.wav').open('ab') as stream: stream.write(b'partial')
    elif change == 'extra': (source/'extra').touch()
    elif change == 'symlink':
        raw = source/'audio.wav'; raw.rename(tmp_path/'held'); raw.symlink_to(tmp_path/'held')
    else: os.link(source/'audio.wav', tmp_path/'hardlink')
    with pytest.raises((receiver.Refused, OSError)): publish(state, source)
    assert not paths[0].exists() and not list((state/'prepare').iterdir())


def test_source_mutation_during_copy_preserves_failed_preparation_and_explicit_retry(tmp_path, monkeypatch):
    sources, paths, state, _, _ = layout(tmp_path)
    source = sources[0]; before = bytes_in(source); original = receiver.snapshot._copy
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2] == 'audio.wav':
            with (source/'audio.wav').open('ab') as stream: stream.write(b'changed')
        return result
    monkeypatch.setattr(receiver.snapshot, '_copy', changed)
    with pytest.raises(receiver.Refused, match='delivery_changed'): publish(state, source)
    assert not paths[0].exists()
    pending = list((state/'prepare').iterdir()); assert len(pending) == 1
    pending_bytes = bytes_in(pending[0])
    (source/'audio.wav').write_bytes(before['audio.wav'])
    monkeypatch.setattr(receiver.snapshot, '_copy', original)
    assert publish(state, source)['status'] == 'published'
    assert bytes_in(pending[0]) == pending_bytes and bytes_in(paths[0]) == before


def test_source_can_change_after_publication_without_mutating_owned_original(tmp_path):
    sources, paths, state, _, _ = layout(tmp_path)
    publish(state, sources[0]); before = bytes_in(paths[0])
    (sources[0]/'audio.wav').write_bytes(b'next raw write')
    assert bytes_in(paths[0]) == before
    assert inbox.inventory(tmp_path/'inbox')['deliveries'][0]['status'] == 'observed_valid'
    with pytest.raises(receiver.Refused): publish(state, sources[0])
    assert bytes_in(paths[0]) == before


def test_metadata_content_conflict_same_attempt_never_overwrites(tmp_path):
    sources, paths, state, _, _ = layout(tmp_path)
    publish(state, sources[0]); before = bytes_in(paths[0])
    other = tmp_path/'other'; other.mkdir()
    changed = delivery(other, 23, epoch=8, start=1000, boundary='RESTART',
                       run='runner-process', seam=False, close='STOP')
    assert changed.name == sources[0].name
    for name in receiver.FILES: (sources[0]/name).write_bytes((changed/name).read_bytes())
    with pytest.raises(receiver.Refused, match='existing_attempt_conflict'): publish(state, sources[0])
    assert bytes_in(paths[0]) == before


def test_atomic_publication_never_replaces_even_racing_empty_directory(tmp_path, monkeypatch):
    sources, paths, state, _, _ = layout(tmp_path)
    original = receiver._rename_noreplace; identity = []
    def race(*args):
        paths[0].mkdir(); identity.append(paths[0].stat().st_ino)
        return original(*args)
    monkeypatch.setattr(receiver, '_rename_noreplace', race)
    with pytest.raises(receiver.Refused, match='target_exists'): publish(state, sources[0])
    assert paths[0].stat().st_ino == identity[0] and not list(paths[0].iterdir())
    assert len(list((state/'prepare').iterdir())) == 1


def test_rename_before_success_is_reconciled_and_refsynced(tmp_path, monkeypatch):
    sources, paths, state, _, _ = layout(tmp_path)
    original = receiver._rename_noreplace
    def crash(*args):
        original(*args)
        raise OSError('synthetic interruption after rename')
    monkeypatch.setattr(receiver, '_rename_noreplace', crash)
    with pytest.raises(OSError): publish(state, sources[0])
    assert bytes_in(paths[0]) == bytes_in(sources[0])
    monkeypatch.setattr(receiver, '_rename_noreplace', original)
    syncs = []; fsync = os.fsync
    def sync(fd): syncs.append(os.fstat(fd).st_ino); return fsync(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    assert publish(state, sources[0])['status'] == 'already_published'
    assert {p.stat().st_ino for p in paths[0].iterdir()} <= set(syncs)
    assert {paths[0].stat().st_ino, (state/'prepare').stat().st_ino,
            (tmp_path/'inbox').stat().st_ino} <= set(syncs)


@pytest.mark.parametrize('name', ['preparation.lock', 'publication.lock'])
def test_lock_contention_obeys_one_global_deadline(tmp_path, name):
    sources, paths, state, _, _ = layout(tmp_path)
    fd = os.open(state/name, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        with pytest.raises(receiver.Refused, match='deadline_exceeded'):
            publish(state, sources[0], deadline_seconds=.05)
        assert time.monotonic()-started < 1 and not paths[0].exists()
    finally: os.close(fd)


def test_uncoordinated_rogue_root_mutation_is_still_fatal(tmp_path, monkeypatch):
    sources, _, state, config, _ = layout(tmp_path)
    publish(state, sources[0]); inspect = inbox.inspect_delivery
    def mutation(*args, **kwargs):
        result = inspect(*args, **kwargs); (tmp_path/'inbox'/'rogue').touch(); return result
    monkeypatch.setattr(inbox, 'inspect_delivery', mutation)
    with pytest.raises(receiver.Refused, match='inbox_changed_no_partial_inventory'):
        receiver.inventory(config['receiver_publication'], tmp_path/'inbox')


def test_publish_during_watch_inventory_waits_only_for_inventory_not_runner(tmp_path, monkeypatch):
    sources, paths, state, config, runner_state = layout(tmp_path, 2)
    publish(state, sources[0])
    runner.configure(state_dir=runner_state, config=config, first_delivery=paths[0])
    real = inbox.inspect_delivery; started = threading.Event(); copied = threading.Event()
    results = []; errors = []; threads = []
    real_sync = receiver._sync_files
    def sync(fd, remaining):
        result = real_sync(fd, remaining)
        if threading.current_thread().name == 'publisher': copied.set()
        return result
    monkeypatch.setattr(receiver, '_sync_files', sync)
    def publisher():
        started.set()
        try: results.append(publish(state, sources[1]))
        except BaseException as exc: errors.append(exc)
    def inspect(*args, **kwargs):
        if not started.is_set():
            thread = threading.Thread(target=publisher, name='publisher'); threads.append(thread); thread.start()
            assert copied.wait(3)
            assert not paths[1].exists(), 'publisher changed root while inventory held shared lock'
        return real(*args, **kwargs)
    monkeypatch.setattr(inbox, 'inspect_delivery', inspect)
    result = runner.watch(state_dir=runner_state, exclusive_writer=True,
        acknowledge_span_local_closure=True, limits=u.Limits(disk_floor=0), transport=Peer(),
        poll_seconds=.1, max_polls=10, deadline_seconds=15)
    for thread in threads: thread.join(3); assert not thread.is_alive()
    assert not errors and results[0]['status'] == 'published'
    assert result['status'] == 'awaiting_finalize' and result['inputs'] == 2
    assert json.loads((runner_state/'input-001.intent.json').read_text())['path'] == str(paths[1])


def test_live_raw_partial_marker_does_not_block_other_complete_input(tmp_path):
    sources, paths, state, config, runner_state = layout(tmp_path, 2)
    publish(state, sources[0]); (sources[1]/'delivery.v1.txt').write_bytes(b'RECORDER-')
    runner.configure(state_dir=runner_state, config=config)
    result = runner.watch(state_dir=runner_state, exclusive_writer=True,
        acknowledge_span_local_closure=True, limits=u.Limits(disk_floor=0), transport=Peer(),
        poll_seconds=.1, max_polls=3, deadline_seconds=10)
    assert result['status'] == 'poll_limit' and result['inputs'] == 1
    assert result['last_status'] == 'waiting_for_input' and not paths[1].exists()


@pytest.mark.parametrize('value', [0, True, float('nan'), float('inf'), 121])
def test_deadline_bound_precedes_effect(tmp_path, value):
    sources, paths, state, _, _ = layout(tmp_path)
    with pytest.raises(receiver.Refused, match='deadline_bounds'):
        publish(state, sources[0], deadline_seconds=value)
    assert not paths[0].exists() and not list((state/'prepare').iterdir())


def test_aggregate_storage_budget_counts_ready_and_interrupted_preparations(tmp_path, monkeypatch):
    sources, paths, state, _, _ = layout(tmp_path, 2)
    publish(state, sources[0])
    first = sum((paths[0]/name).stat().st_size for name in receiver.FILES)
    second = sum((sources[1]/name).stat().st_size for name in receiver.FILES)
    pending = state/'prepare'/('pending-'+'a'*32); pending.mkdir(mode=0o700)
    (pending/'audio.wav').write_bytes(b'partial'); (pending/'audio.wav').chmod(0o600)
    monkeypatch.setattr(receiver, 'MAX_STORAGE_BYTES', first+second+6)
    with pytest.raises(receiver.Refused, match='storage_byte_bound'): publish(state, sources[1])
    assert not paths[1].exists() and list((state/'prepare').iterdir()) == [pending]
    assert (pending/'audio.wav').read_bytes() == b'partial'


def test_runner_cancel_is_observed_while_waiting_for_publication_lock(tmp_path):
    sources, paths, state, config, runner_state = layout(tmp_path)
    publish(state, sources[0]); runner.configure(state_dir=runner_state, config=config)
    cancelled = threading.Event(); fd = os.open(state/'publication.lock', os.O_RDONLY)
    timer = threading.Timer(.05, cancelled.set)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); timer.start(); started = time.monotonic()
        result = runner.watch(state_dir=runner_state, exclusive_writer=True,
            acknowledge_span_local_closure=True, limits=u.Limits(disk_floor=0), transport=Peer(),
            poll_seconds=.1, max_polls=10, deadline_seconds=30, cancel=cancelled.is_set)
        assert result['status'] == 'cancelled' and result['network_requests'] == 0
        assert time.monotonic()-started < 1
    finally: os.close(fd); timer.cancel(); timer.join()
