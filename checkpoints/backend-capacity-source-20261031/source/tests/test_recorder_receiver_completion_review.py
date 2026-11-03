"""Independent lifecycle boundary review, including fresh operating processes.

Only synthetic local files are used. No receiver service, network, credentials,
intake, or model inference is started by these checks.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from audio_backend import recorder_receiver_publication as publication
from audio_backend import recorder_receiver_completion as completion
from test_recorder_inbox import delivery


def example(tmp_path):
    raw = tmp_path / 'raw'; raw.mkdir(mode=0o700)
    ready = tmp_path / 'ready'; ready.mkdir(mode=0o700)
    state = tmp_path / 'publisher'
    templates = tmp_path / 'templates'; templates.mkdir(mode=0o700)
    source = delivery(templates, 0, close='ROTATION')
    publication.configure(state_dir=state, staging_dir=raw, inbox_dir=ready,
                          exclusive_receiver=True)
    return raw, ready, state, source


def adapter(state, ready, **kwargs):
    return completion.ReceiverCompletion(binding=publication.bind(state),
        inbox_dir=ready, exclusive_receiver=True, **kwargs)


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def write_one(owner, attempt, source, name, successful=True):
    writer = owner.open_writer(attempt, name)
    writer.stream.write((source/name).read_bytes())
    writer.stream.close()
    return writer, owner.writer_closed(writer, successful=successful)


BOOT = '''
import json, os, time
from pathlib import Path
from audio_backend import recorder_receiver_publication as p
from audio_backend import recorder_receiver_completion as c
state = Path(os.environ['REVIEW_STATE'])
ready = Path(os.environ['REVIEW_READY'])
source = Path(os.environ['REVIEW_SOURCE'])
owner = c.ReceiverCompletion(binding=p.bind(state), inbox_dir=ready,
                             exclusive_receiver=True)
'''


def child(script, state, ready, source, **extra):
    env = dict(os.environ, REVIEW_STATE=str(state), REVIEW_READY=str(ready),
               REVIEW_SOURCE=str(source), PYTHONDONTWRITEBYTECODE='1',
               **{key: str(value) for key, value in extra.items()})
    return subprocess.Popen([sys.executable, '-c', BOOT + script], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def finish(process, expected=0):
    out, err = process.communicate(timeout=10)
    assert process.returncode == expected, (out, err)
    return out


def reap(process):
    if process.poll() is None:
        process.kill(); process.communicate(timeout=5)


def wait_for(path, process):
    end = time.monotonic() + 5
    while not path.exists() and process.poll() is None and time.monotonic() < end:
        time.sleep(.01)
    assert path.exists(), (process.poll(), process.communicate(timeout=1)
                          if process.poll() is not None else None)


def test_process_lifetime_owner_prevents_a_second_adapter(tmp_path):
    raw, ready, state, source = example(tmp_path)
    gate = tmp_path/'owner-ready'
    process = child('''
Path(os.environ['REVIEW_GATE']).touch()
while True: time.sleep(.1)
''', state, ready, source, REVIEW_GATE=gate)
    try:
        wait_for(gate, process)
        with pytest.raises(publication.Refused): adapter(state, ready)
        assert not contents(raw) and not contents(ready)
    finally: reap(process)
    owner = adapter(state, ready)
    owner.close()


def test_process_death_with_live_writer_never_adopts_or_completes(tmp_path):
    raw, ready, state, source = example(tmp_path)
    process = child('''
attempt = owner.begin_attempt(source.name)
writer = owner.open_writer(attempt, 'delivery.v1.txt')
writer.stream.write((source/'delivery.v1.txt').read_bytes()[:32])
writer.stream.flush()
os._exit(79)
''', state, ready, source)
    try: finish(process, 79)
    finally: reap(process)
    frozen = contents(raw)
    assert frozen == {source.name+'/delivery.v1.txt': (source/'delivery.v1.txt').read_bytes()[:32]}
    owner = adapter(state, ready)
    try:
        with pytest.raises((publication.Refused, FileExistsError)):
            owner.begin_attempt(source.name)
        assert contents(raw) == frozen and not list(ready.iterdir())
    finally: owner.close()


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_process_death_at_publication_never_reopens_uncertain_attempt(tmp_path, phase):
    raw, ready, state, source = example(tmp_path)
    process = child('''
real = p._rename_noreplace
def die(*args):
    if os.environ['REVIEW_PHASE'] == 'after': real(*args)
    os._exit(79)
p._rename_noreplace = die
attempt = owner.begin_attempt(source.name)
for name in p.FILES:
    writer = owner.open_writer(attempt, name)
    writer.stream.write((source/name).read_bytes())
    writer.stream.close()
    owner.writer_closed(writer, successful=True)
''', state, ready, source, REVIEW_PHASE=phase)
    try: finish(process, 79)
    finally: reap(process)
    raw_before, ready_before, pending_before = contents(raw), contents(ready), contents(state/'prepare')
    assert contents(raw/source.name) == contents(source)
    assert bool(ready_before) == (phase == 'after')
    assert bool(pending_before) == (phase == 'before')
    owner = adapter(state, ready)
    try:
        with pytest.raises((publication.Refused, FileExistsError)):
            owner.begin_attempt(source.name)
        assert (contents(raw), contents(ready), contents(state/'prepare')) == (
            raw_before, ready_before, pending_before)
    finally: owner.close()


def test_closed_callback_is_not_success_until_underlying_stream_closes(tmp_path):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready)
    try:
        attempt = owner.begin_attempt(source.name)
        write_one(owner, attempt, source, 'audio.wav')
        write_one(owner, attempt, source, 'segment.v1.bin')
        writer = owner.open_writer(attempt, 'delivery.v1.txt')
        writer.stream.write((source/'delivery.v1.txt').read_bytes())
        with pytest.raises(publication.Refused): owner.writer_closed(writer, successful=True)
        assert not list(ready.iterdir())
    finally: owner.close()


def test_contradictory_callback_poison_prevents_later_final_publication(tmp_path):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready)
    try:
        attempt = owner.begin_attempt(source.name)
        writers = {name: owner.open_writer(attempt, name) for name in publication.FILES}
        for name, writer in writers.items():
            writer.stream.write((source/name).read_bytes()); writer.stream.close()
        audio = writers['audio.wav']
        owner.writer_closed(audio, successful=True)
        with pytest.raises(publication.Refused): owner.writer_closed(audio, successful=False)
        for name in ('segment.v1.bin', 'delivery.v1.txt'):
            owner.writer_closed(writers[name], successful=True)
        assert owner.status(attempt)['status'] == 'failed'
        assert not list(ready.iterdir()) and not list((state/'prepare').iterdir())
    finally: owner.close()


def test_raw_attempt_count_and_bytes_remain_charged_after_owner_restart(tmp_path):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready, max_attempts=1)
    attempt = owner.begin_attempt(source.name)
    writer = owner.open_writer(attempt, 'audio.wav')
    writer.stream.write(b'12345678')
    owner.close()
    frozen = contents(raw)
    owner = adapter(state, ready, max_attempts=1)
    try:
        alternate = source.name[:-32] + '1'*32
        with pytest.raises(publication.Refused): owner.begin_attempt(alternate)
    finally: owner.close()
    with pytest.raises(publication.Refused): adapter(state, ready, max_raw_bytes=7)
    assert contents(raw) == frozen and not list(ready.iterdir())


def test_bounded_stream_rejects_overrun_before_writing_extra_bytes(tmp_path, monkeypatch):
    raw, ready, state, source = example(tmp_path)
    monkeypatch.setitem(completion.LIMITS, 'audio.wav', 4)
    owner = adapter(state, ready, max_raw_bytes=4)
    try:
        attempt = owner.begin_attempt(source.name)
        writer = owner.open_writer(attempt, 'audio.wav')
        assert writer.stream.write(b'abc') == 3
        with pytest.raises(publication.Refused): writer.stream.write(b'de')
        writer.stream.close()
        result = owner.writer_closed(writer, successful=True)
        assert result['status'] == 'failed'
        assert (raw/source.name/'audio.wav').read_bytes() == b'abc'
        assert not list(ready.iterdir())
    finally: owner.close()


def test_old_successful_close_does_not_cover_later_raw_mutation(tmp_path):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready)
    try:
        attempt = owner.begin_attempt(source.name)
        write_one(owner, attempt, source, 'audio.wav')
        with (raw/source.name/'audio.wav').open('ab') as stream: stream.write(b'changed')
        write_one(owner, attempt, source, 'segment.v1.bin')
        with pytest.raises(publication.Refused):
            write_one(owner, attempt, source, 'delivery.v1.txt')
        assert not list(ready.iterdir())
    finally: owner.close()


def test_final_publication_failure_is_terminal_without_automatic_retry(tmp_path, monkeypatch):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready)
    calls = []
    real = publication.publish_completed
    def uncertain(**kwargs):
        calls.append(kwargs)
        real(**kwargs)
        raise OSError('synthetic loss of completed publication result')
    monkeypatch.setattr(publication, 'publish_completed', uncertain)
    try:
        attempt = owner.begin_attempt(source.name)
        write_one(owner, attempt, source, 'audio.wav')
        write_one(owner, attempt, source, 'segment.v1.bin')
        writer = owner.open_writer(attempt, 'delivery.v1.txt')
        writer.stream.write((source/'delivery.v1.txt').read_bytes()); writer.stream.close()
        with pytest.raises(OSError): owner.writer_closed(writer, successful=True)
        frozen = contents(ready)
        assert len(calls) == 1 and frozen
        repeated = owner.writer_closed(writer, successful=True)
        assert repeated['status'] == 'uncertain'
        assert repeated['reason'] == 'completion_publication_uncertain'
        with pytest.raises(publication.Refused): owner.open_writer(attempt, 'delivery.v1.txt')
        assert len(calls) == 1 and contents(ready) == frozen
    finally: owner.close()


def test_owned_stream_cannot_be_reopened_after_successful_publication(tmp_path):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready)
    try:
        attempt = owner.begin_attempt(source.name)
        for name in publication.FILES: write_one(owner, attempt, source, name)
        frozen = contents(ready)
        assert contents(ready/source.name) == contents(source)
        for name in publication.FILES:
            with pytest.raises(publication.Refused): owner.open_writer(attempt, name)
        assert contents(ready) == frozen and contents(raw/source.name) == contents(source)
    finally: owner.close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='Linux fork ownership boundary')
def test_inherited_adapter_and_stream_cannot_mutate_or_release_parent_ownership(tmp_path):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready)
    try:
        attempt = owner.begin_attempt(source.name)
        writer = owner.open_writer(attempt, 'audio.wav')
        pid = os.fork()
        if pid == 0:
            refused = 0
            try:
                writer.stream.write(b'fork-mutation')
            except (publication.Refused, OSError, RuntimeError):
                refused += 1
            try:
                owner.writer_closed(writer, successful=True)
            except (publication.Refused, OSError, RuntimeError):
                refused += 1
            try:
                owner.close()
            except (publication.Refused, OSError, RuntimeError):
                pass
            os._exit(0 if refused == 2 else 81)
        waited, status = os.waitpid(pid, 0)
        assert waited == pid and os.waitstatus_to_exitcode(status) == 0
        assert (raw/source.name/'audio.wav').stat().st_size == 0
        with pytest.raises(publication.Refused): adapter(state, ready)
        writer.stream.write((source/'audio.wav').read_bytes()); writer.stream.close()
        owner.writer_closed(writer, successful=True)
        write_one(owner, attempt, source, 'segment.v1.bin')
        write_one(owner, attempt, source, 'delivery.v1.txt')
        assert contents(ready/source.name) == contents(source)
    finally: owner.close()


@pytest.mark.parametrize('actually_closed', [False, True])
def test_shutdown_attempts_every_close_and_releases_ownership_only_after_all_closed(
        tmp_path, monkeypatch, actually_closed):
    raw, ready, state, source = example(tmp_path)
    owner = adapter(state, ready)
    attempt = owner.begin_attempt(source.name)
    writers = [owner.open_writer(attempt, name) for name in publication.FILES]
    original = writers[0].stream.close
    def broken_close():
        if actually_closed: original()
        raise OSError('synthetic close failure')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(writers[0].stream, 'close', broken_close)
            with pytest.raises(OSError, match='synthetic close failure'): owner.close()
        assert all(writer.stream.closed for writer in writers[1:])
        assert writers[0].stream.closed is actually_closed
        assert not list(ready.iterdir())
        if actually_closed:
            replacement = adapter(state, ready)
            replacement.close()
        else:
            with pytest.raises(publication.Refused): adapter(state, ready)
            assert owner.status(attempt)['status'] == 'failed'
            with pytest.raises(publication.Refused): owner.open_writer(attempt, 'audio.wav')
        owner.close()
        replacement = adapter(state, ready)
        replacement.close()
        assert all(writer.stream.closed for writer in writers)
    finally:
        owner.close()
