"""Synthetic local lifecycle tests; no receiver service or native inference."""
import itertools
import os
from pathlib import Path
import threading

import pytest

from audio_backend import recorder_receiver_completion as completion
from audio_backend import recorder_receiver_publication as publication
from test_recorder_receiver_completion_review import example, adapter, contents, write_one


@pytest.mark.parametrize('order', list(itertools.permutations(publication.FILES)))
def test_any_completion_order_requires_three_exact_successful_closed_streams(tmp_path, order):
    raw, ready, state, source = example(tmp_path)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        writers = {name: owner.open_writer(attempt, name) for name in order}
        for name, writer in writers.items():
            assert writer.stream.name == str(raw/source.name/name)
            writer.stream.write((source/name).read_bytes())
        for index, name in enumerate(order):
            writers[name].stream.close()
            result = owner.writer_closed(writers[name], successful=True)
            assert result['status'] == ('published' if index == 2 else 'receiving')
            assert result['pending_writers'] == 2-index
            assert bool(contents(ready)) == (index == 2)
        assert result['publication']['status'] == 'published'
        assert contents(ready/source.name) == contents(source) == contents(raw/source.name)
        assert result['publication']['delivery']['sequence'] == 0
        assert all(writer.stream.closed for writer in writers.values())
        for name in order:
            assert (ready/source.name/name).stat().st_ino != (raw/source.name/name).stat().st_ino
        result['publication']['status'] = 'caller mutated its result'
        assert owner.status(attempt)['publication']['status'] == 'published'


def test_duplicate_close_is_idempotent_but_contradiction_before_completion_fails(tmp_path, monkeypatch):
    raw, ready, state, source = example(tmp_path)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        writers = {name: owner.open_writer(attempt, name) for name in publication.FILES}
        for name, writer in writers.items():
            writer.stream.write((source/name).read_bytes()); writer.stream.close()
        first = writers['audio.wav']
        owner.writer_closed(first, successful=True)
        assert owner.writer_closed(first, successful=True)['successful_files'] == ['audio.wav']
        with pytest.raises(publication.Refused, match='conflicting_outcome'):
            owner.writer_closed(first, successful=False)
        for name in ('segment.v1.bin', 'delivery.v1.txt'):
            assert owner.writer_closed(writers[name], successful=True)['status'] == 'failed'
        assert not list(ready.iterdir())


def test_failed_complete_bytes_and_missing_callback_never_publish(tmp_path):
    raw, ready, state, source = example(tmp_path)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        writers = {name: owner.open_writer(attempt, name) for name in publication.FILES}
        for name, writer in writers.items():
            writer.stream.write((source/name).read_bytes()); writer.stream.close()
        owner.writer_closed(writers['audio.wav'], successful=True)
        owner.writer_closed(writers['delivery.v1.txt'], successful=True)
        assert not list(ready.iterdir())
        assert owner.status(attempt)['pending_writers'] == 1
        result = owner.writer_closed(writers['segment.v1.bin'], successful=False)
        assert result['status'] == 'failed' and not list(ready.iterdir())
        assert contents(raw/source.name) == contents(source)


def test_duplicate_admission_refuses_before_truncation_or_extra_writer(tmp_path):
    raw, ready, state, source = example(tmp_path)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        writer = owner.open_writer(attempt, 'audio.wav')
        writer.stream.write(b'keep')
        with pytest.raises(publication.Refused, match='duplicate_writer'):
            owner.open_writer(attempt, 'audio.wav')
        assert (raw/source.name/'audio.wav').read_bytes() == b'keep'
        with pytest.raises(publication.Refused, match='file_name'):
            owner.open_writer(attempt, '../audio.wav')
        assert owner.status(attempt)['pending_writers'] == 1
    assert writer.stream.closed and not list(ready.iterdir())
    with pytest.raises(publication.Refused, match='adapter_closed'):
        owner.writer_closed(writer, successful=True)


def test_unknown_and_cross_instance_tokens_never_admit_or_close(tmp_path):
    raw, ready, state, source = example(tmp_path)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        writer = owner.open_writer(attempt, 'audio.wav')
        with pytest.raises(publication.Refused, match='unknown_writer'):
            owner.writer_closed(object(), successful=True)
        with pytest.raises(publication.Refused, match='unknown_attempt'):
            owner.open_writer(object(), 'audio.wav')
    with adapter(state, ready) as new:
        with pytest.raises(publication.Refused, match='unknown_attempt'):
            new.writer_closed(writer, successful=False)
        with pytest.raises(publication.Refused, match='existing_attempt'):
            new.begin_attempt(source.name)
    assert not list(ready.iterdir())


def test_stream_bound_precedes_disk_mutation_and_failure_is_terminal(tmp_path, monkeypatch):
    raw, ready, state, source = example(tmp_path)
    monkeypatch.setitem(completion.LIMITS, 'audio.wav', 4)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        writer = owner.open_writer(attempt, 'audio.wav')
        writer.stream.write(b'1234')
        with pytest.raises(publication.Refused, match='file_byte_bound'):
            writer.stream.write(b'5')
        assert (raw/source.name/'audio.wav').read_bytes() == b'1234'
        writer.stream.close()
        assert owner.writer_closed(writer, successful=True)['status'] == 'failed'
        with pytest.raises(publication.Refused, match='not_receiving'):
            owner.open_writer(attempt, 'delivery.v1.txt')
        assert not list(ready.iterdir())


def test_retained_attempt_and_byte_budget_cannot_reset_across_instances(tmp_path, monkeypatch):
    raw, ready, state, source = example(tmp_path)
    monkeypatch.setitem(completion.LIMITS, 'audio.wav', 4)
    with adapter(state, ready, max_attempts=1, max_raw_bytes=4) as owner:
        attempt = owner.begin_attempt(source.name)
        writer = owner.open_writer(attempt, 'audio.wav')
        writer.stream.write(b'1234')
    before = contents(raw)
    with adapter(state, ready, max_attempts=1, max_raw_bytes=4) as owner:
        with pytest.raises(publication.Refused, match='raw_attempt_capacity'):
            owner.begin_attempt(source.name[:-32]+'1'*32)
    with adapter(state, ready, max_raw_bytes=4) as owner:
        fresh = owner.begin_attempt(source.name[:-32]+'1'*32)
        with pytest.raises(publication.Refused, match='raw_byte_reservation'):
            owner.open_writer(fresh, 'audio.wav')
        assert not list(fresh.path.iterdir())
    with pytest.raises(publication.Refused, match='raw_byte_bound'):
        adapter(state, ready, max_raw_bytes=3)
    assert contents(raw) == before


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'extra', 'mode'])
def test_restart_inventory_rejects_unsafe_retained_raw_without_adopting(tmp_path, kind):
    raw, ready, state, source = example(tmp_path)
    path = raw/source.name; path.mkdir(mode=0o700)
    file = path/'audio.wav'
    if kind == 'symlink': file.symlink_to(source/'audio.wav')
    elif kind == 'hardlink': os.link(source/'audio.wav', file)
    elif kind == 'extra': (path/'other').touch()
    else: file.write_bytes(b'x'); file.chmod(0o666)
    with pytest.raises((publication.Refused, OSError)):
        adapter(state, ready)
    assert not list(ready.iterdir())


@pytest.mark.parametrize('change', ['marker', 'metadata', 'audio'])
def test_full_success_callbacks_still_require_strict_delivery_validation(tmp_path, change):
    raw, ready, state, source = example(tmp_path)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        writers = {}
        for name in publication.FILES:
            writer = owner.open_writer(attempt, name); writers[name] = writer
            data = (source/name).read_bytes()
            if name == {'marker':'delivery.v1.txt', 'metadata':'segment.v1.bin', 'audio':'audio.wav'}[change]:
                data += b'invalid'
            writer.stream.write(data); writer.stream.close()
        owner.writer_closed(writers['audio.wav'], successful=True)
        owner.writer_closed(writers['segment.v1.bin'], successful=True)
        with pytest.raises(publication.Refused):
            owner.writer_closed(writers['delivery.v1.txt'], successful=True)
        assert owner.status(attempt)['status'] == 'uncertain'
        assert not list(ready.iterdir())


def test_admission_serializes_with_publication_and_raw_afterward_cannot_mutate_copy(tmp_path, monkeypatch):
    raw, ready, state, source = example(tmp_path)
    began = threading.Event(); release = threading.Event(); attempted = threading.Event()
    failures = []; results = []
    original = publication.publish_completed
    def paused(**kwargs):
        began.set(); assert release.wait(3)
        return original(**kwargs)
    monkeypatch.setattr(publication, 'publish_completed', paused)
    with adapter(state, ready) as owner:
        attempt = owner.begin_attempt(source.name)
        write_one(owner, attempt, source, 'audio.wav')
        write_one(owner, attempt, source, 'segment.v1.bin')
        writer = owner.open_writer(attempt, 'delivery.v1.txt')
        writer.stream.write((source/'delivery.v1.txt').read_bytes()); writer.stream.close()
        def finish():
            try: results.append(owner.writer_closed(writer, successful=True))
            except BaseException as exc: failures.append(exc)
        def reopen():
            attempted.set()
            try: owner.open_writer(attempt, 'audio.wav')
            except BaseException as exc: failures.append(exc)
        publisher = threading.Thread(target=finish); publisher.start(); assert began.wait(3)
        contender = threading.Thread(target=reopen); contender.start(); assert attempted.wait(3)
        assert not results and contender.is_alive()
        release.set(); publisher.join(3); contender.join(3)
        assert not publisher.is_alive() and not contender.is_alive()
        assert len(failures) == 1 and str(failures[0]) == 'completion_attempt_not_receiving'
        assert results[0]['status'] == 'published'
        frozen = contents(ready)
        (raw/source.name/'audio.wav').write_bytes(b'uncooperative later write')
        assert contents(ready) == frozen


def test_event_lock_wait_is_bounded_without_opening_a_file(tmp_path):
    raw, ready, state, source = example(tmp_path)
    locked = threading.Event(); release = threading.Event()
    with adapter(state, ready, deadline_seconds=.05) as owner:
        attempt = owner.begin_attempt(source.name)
        def hold():
            with owner._mutex:
                locked.set(); release.wait(3)
        thread = threading.Thread(target=hold); thread.start(); assert locked.wait(3)
        try:
            with pytest.raises(publication.Refused, match='event_deadline'):
                owner.open_writer(attempt, 'audio.wav')
            assert not list(attempt.path.iterdir())
        finally:
            release.set(); thread.join(3)
        assert not thread.is_alive()


def test_open_sync_failure_retains_stream_for_best_effort_cleanup(tmp_path, monkeypatch):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready)
    attempt = owner.begin_attempt(source.name)
    original_close = completion._Stream.close
    try:
        with monkeypatch.context() as patch:
            patch.setattr(completion.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('sync failed')))
            patch.setattr(completion._Stream, 'close', lambda self: (_ for _ in ()).throw(OSError('close failed')))
            with pytest.raises(OSError, match='close failed'):
                owner.open_writer(attempt, 'audio.wav')
        tracked = attempt.writers['audio.wav']
        assert not tracked.stream.closed
        assert owner.status(attempt)['status'] == 'failed'
        owner.close()
        assert tracked.stream.closed and not list(ready.iterdir())
        with adapter(state, ready):
            pass
    finally:
        owner.close()
