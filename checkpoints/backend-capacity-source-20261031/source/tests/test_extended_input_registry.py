"""Finite metadata-only admission evidence, not long-audio/native qualification.

These fixtures create genuine journal/archive files with synthetic original and
server identities. They never decode PCM, run a model, or contact a server.
"""
from contextlib import contextmanager
import json

import pytest

from audio_backend import recording_uploader as u
from audio_backend import recording_uploader_history as history
from test_recording_uploader import (
    metadata_input, metadata_journal, metadata_piece, metadata_release,
)


@contextmanager
def reopen(path, frames):
    journal = history.PagedJournal(
        path, u.Guard(u.Limits(disk_floor=0, output_frames=frames)), u)
    journal.lock.acquire()
    try:
        journal.load()
        yield journal
    finally:
        journal.lock.release()


def append_metadata(journal, number, frames, *, padding=False):
    entry = metadata_input(journal, number, frames, padding=padding)
    while entry['frames'] < frames:
        row = journal.data['active_page']
        used = sum(p['frames'] for p in row['pieces']) if row else 0
        count = min(u.SOURCE_FRAMES, u.PAGE_FRAMES - used, frames - entry['frames'])
        metadata_piece(journal, count)
        if entry['frames'] == frames:
            entry['clean_eof'] = True
            journal.save()
            journal.archive_input()
        if used + count == u.PAGE_FRAMES:
            metadata_release(journal)


@pytest.mark.parametrize('capacity', [64, 128, 192])
def test_padded_registry_preserves_frozen_cap_and_refuses_next_input(tmp_path, capacity):
    per_input = u.PAGE_FRAMES // 8
    frames = capacity * per_input + 1
    journal = metadata_journal(tmp_path, frames, capacity)
    try:
        for number in range(1, capacity + 1):
            append_metadata(journal, number, per_input, padding=True)
        assert len(journal.digest_bindings['input']) == capacity * 32
        assert (journal.path / 'uploader.json').stat().st_size < 16384
    finally:
        journal.lock.release()
    with reopen(tmp_path / 'state', frames) as resumed:
        assert resumed.data['caps']['max_inputs'] == capacity
        assert sum(1 for _ in resumed.iter_inputs()) == capacity
        assert sum(1 for _ in resumed.iter_pages()) == capacity // 8
        for entry in resumed.iter_inputs():
            assert len(entry['id']) == 128 and entry['clean_eof']
            assert len(entry['identity']['path'].encode()) > 4000
        before = (resumed.path / 'uploader.json').read_bytes()
        with pytest.raises(u.Incomplete, match='input_count bound|registry'):
            metadata_input(resumed, capacity + 1, 1)
        assert (resumed.path / 'uploader.json').read_bytes() == before
        assert not resumed.archive_path('input', capacity + 1).exists()
    # The refused in-memory mutation must not damage the committed checkpoint.
    with reopen(tmp_path / 'state', frames) as resumed:
        assert resumed.data['inputs_done'] == capacity
        assert resumed.data['caps']['max_inputs'] == capacity


@pytest.mark.parametrize('inputs,seconds,padding,pages', [
    (144, 300, False, 147),
    (192, 900, True, 588),
])
def test_extended_geometry_restart_stop_eos_and_late_authentication(
        tmp_path, inputs, seconds, padding, pages):
    frames = inputs * seconds * u.RATE
    journal = metadata_journal(tmp_path, frames, history.DEFAULT_INPUTS)
    try:
        for number in range(1, inputs + 1):
            append_metadata(journal, number, seconds * u.RATE, padding=padding)
            if number in (129, 144, 192):
                # A simulated recorder STOP leaves clean EOF, not capture finalization. Reopen
                # the actual files before admitting the next metadata input.
                journal.lock.release()
                with reopen(tmp_path / 'state', frames) as stopped:
                    assert stopped.data['inputs_done'] == number
                    assert stopped.data['completed_frames'] == number * seconds * u.RATE
                    assert not stopped.data['finalize_requested']
                    assert not stopped.data['complete']
                    assert all(not row['seal']['eos'] for row in stopped.iter_pages()
                               if row['released'])
                    assert stopped.read_archive('input', number)['clean_eof']
                journal.lock.acquire()
                journal.load()
        assert journal.data['active_page']['number'] == pages
        journal.data['finalize_requested'] = True
        journal.save()
        metadata_release(journal, eos=True)
        journal.data['complete'] = True
        journal.save()
    finally:
        journal.lock.release()
    with reopen(tmp_path / 'state', frames) as resumed:
        resumed.recover()
        assert resumed.data['complete'] and resumed.data['inputs_done'] == inputs
        assert resumed.data['pages_done'] == pages
        assert resumed.data['emitted_frames'] == resumed.data['published_frames'] == frames
        assert sum(entry['frames'] for entry in resumed.iter_inputs()) == frames
        assert sum(p['frames'] for row in resumed.iter_pages() for p in row['pieces']) == frames
        assert [row['number'] for row in resumed.iter_pages() if row['seal']['eos']] == [pages]
        assert len(resumed.digest_bindings['input']) == inputs * 32
        assert len(resumed.digest_bindings['page']) == pages * 32
        assert sum(map(len, resumed.digest_bindings.values())) <= 24960
        assert not resumed.data['spool'] and not list(resumed.path.glob('*.wav*'))
        assert 'inputs' not in resumed.data and 'pages' not in resumed.data
        assert len(u._json(resumed.data)) <= u.JSON_BYTES
        assert resumed.metadata_allocated < history.METADATA_BYTES
        for kind, count, cap in (('input', inputs, history.INPUT_ARCHIVE_BYTES),
                                 ('page', pages, history.PAGE_ARCHIVE_BYTES)):
            assert all(resumed.archive_path(kind, n).stat().st_size <= cap
                       for n in range(1, count + 1))
        print(json.dumps({'metadata_only': True, 'inputs': inputs, 'seconds_per_input': seconds,
                          'pages': pages, 'frames': frames,
                          'authentication_bytes': sum(map(len, resumed.digest_bindings.values())),
                          'metadata_allocated_bytes': resumed.metadata_allocated}))
        # Change a semantically valid byte beyond the old 128 boundary. Both
        # post-validation access and a fresh startup must reject raw-byte drift.
        for kind, ordinal in [('input', n) for n in sorted({129, 144, inputs})] + [
                ('page', n) for n in sorted({129, 144, pages})]:
            archive = resumed.archive_path(kind, ordinal)
            original = archive.read_bytes()
            changed = u._parse(original)
            if kind == 'input':
                changed['payload']['identity']['sha256'] = 'f' * 64
            else:
                changed['payload']['server_id'] = 'synthetic-corrupted-page'
            archive.write_bytes(u._json(changed))
            checkpoint = (resumed.path / 'uploader.json').read_bytes()
            try:
                with pytest.raises(u.Incomplete, match='changed after validation'):
                    resumed.read_archive(kind, ordinal)
                resumed.lock.release()
                try:
                    with pytest.raises(u.Incomplete):
                        with reopen(tmp_path / 'state', frames):
                            pytest.fail('Corrupt late history must not reopen')
                finally:
                    resumed.lock.acquire()
                assert (resumed.path / 'uploader.json').read_bytes() == checkpoint
                assert archive.read_bytes() == u._json(changed)
            finally:
                archive.write_bytes(original)
            assert resumed.read_archive(kind, ordinal) == u._parse(original)['payload']
    with reopen(tmp_path / 'state', frames) as restored:
        assert restored.data['complete']


def test_full_588_page_192_input_budget_has_finite_headroom():
    assert history.MAX_INPUTS == history.DEFAULT_INPUTS == 192
    pages = (history.MAX_FRAMES + u.PAGE_FRAMES - 1) // u.PAGE_FRAMES
    assert pages == 588
    assert (pages + history.MAX_INPUTS) * 32 == 24960
    # Conservative 4KiB allocation rounding per archive, two directories,
    # current checkpoint/lock, and the implementation's next-write reserve.
    archive_bound = pages * (history.PAGE_ARCHIVE_BYTES + 4096)
    archive_bound += history.MAX_INPUTS * (history.INPUT_ARCHIVE_BYTES + 4096)
    live_bound = 2 * 65536 + u.JSON_BYTES + 4096
    reserve = 2 * u.JSON_BYTES + history.PAGE_ARCHIVE_BYTES + 65536
    metadata_bound = archive_bound + live_bound + reserve
    assert metadata_bound < history.METADATA_BYTES
    assert metadata_bound + u.Limits().spool_bytes < history.STATE_BYTES
