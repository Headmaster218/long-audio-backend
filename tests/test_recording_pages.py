"""Model-free sealed-page continuation tests using real WAV files and SQLite.

These fixtures test ownership, fences, lifecycle and quotas, not acoustic quality.
No worker, model, network service or downloaded artifact is required. The largest
fixture retains about 86 MiB of original and canonical PCM at the real 128-work
backpressure boundary. Test runners must keep their temporary root in this copy.
"""
from array import array
from contextlib import contextmanager
import errno
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import wave

import pytest
from fastapi.testclient import TestClient

from audio_backend import boundary_store as boundary
from audio_backend import recording_overlap as mod
from audio_backend import resources
from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.storage import Store
from test_recording_overlap import native


CAPTURE = 'capture.pages'
Q = 168000
RATE = 16000
_period = array('h', ((index * 811 % 60001) - 30000 for index in range(60001)))
if sys.byteorder != 'little':
    _period.byteswap()
_period = _period.tobytes()


def pcm(frames, start=0):
    """A bounded, reproducible PCM slice at an explicit capture-relative offset."""
    offset = (start % 60001) * 2
    return (_period * ((offset + frames * 2 + len(_period) - 1) // len(_period)))[offset:offset + frames * 2]


def wav_bytes(data):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(data)
    return output.getvalue()


@pytest.fixture
def fixture(tmp_path):
    cfg = Settings(data_dir=tmp_path, recording_disk_reserve_bytes=0,
                   disk_reserve_bytes=0, start_worker=False)
    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'tmp').mkdir()
    store = Store(tmp_path / 'jobs.sqlite3', cfg)
    sid = store.create_session('sealed-page fixture')['id']
    return store, mod.RecordingOverlap(store), sid


def create(recording, sid, number, origin=None):
    return recording.create_page(sid, CAPTURE, number, capture_origin_frame=origin)


def put(recording, sid, number, key, data, offset):
    page = recording.page_status(sid, CAPTURE, number)
    with recording.store.db() as db:
        batch = recording._batch(db, sid, CAPTURE)
    path = recording.source_path(batch, page)
    path.write_bytes(wav_bytes(data))
    try:
        return recording.accept_page(sid, CAPTURE, number, key, path,
                                     source_start_frame=offset)
    finally:
        path.unlink(missing_ok=True)


def seal(recording, sid, number, end, *, eos=False, key=None, revision=None):
    if revision is None:
        revision = recording.page_status(sid, CAPTURE, number)['revision']
    return recording.seal_page(sid, CAPTURE, number, key or f'seal.{number}',
                               revision, end, eos=eos)


def paths(store, sid):
    with store.db() as db:
        originals = [dict(row) for row in db.execute('''SELECT s.* FROM recording_sources s
            JOIN recording_batches b ON b.id=s.batch_id WHERE b.session_id=? ORDER BY s.seq''', (sid,))]
        work = [dict(row) for row in db.execute('''SELECT c.*,w.position,w.frames,w.capture_start_frame
            FROM recording_work w JOIN chunks c ON c.id=w.chunk_id
            JOIN recording_batches b ON b.id=w.batch_id WHERE b.session_id=? ORDER BY w.position''', (sid,))]
    return originals, work


def joined(work):
    result = bytearray()
    for row in work:
        with wave.open(row['path'], 'rb') as stream:
            assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) == (RATE, 1, 2)
            result.extend(stream.readframes(stream.getnframes()))
    return bytes(result)


def assert_complete_map(store, sid, page):
    """Check every source frame, including aliases referencing prior page work."""
    originals, work = paths(store, sid)
    source_by_id = {row['id']: row for row in originals}
    work_by_id = {row['id']: row for row in work}
    for source in page['sources']:
        rows = sorted((row for row in page['ranges'] if row['source_id'] == source['id']),
                      key=lambda row: row['source_start_frame'])
        cursor = 0
        with wave.open(source_by_id[source['id']]['path'], 'rb') as original:
            for row in rows:
                assert row['frames'] > 0
                assert row['source_start_frame'] == cursor
                assert row['capture_start_frame'] == source['source_start_frame'] + cursor
                assert row['relation'] in ('owner', 'exact_alias')
                target = work_by_id[row['chunk_id']]
                assert row['capture_start_frame'] == target['capture_start_frame'] + row['work_start_frame']
                assert row['work_start_frame'] + row['frames'] <= target['frames']
                with wave.open(target['path'], 'rb') as canonical:
                    canonical.setpos(row['work_start_frame'])
                    assert canonical.readframes(row['frames']) == original.readframes(row['frames'])
                cursor += row['frames']
            assert cursor == source['frames']


def first_page(recording, sid, *, origin=0, chunks=1):
    create(recording, sid, 1, origin)
    put(recording, sid, 1, 'source.first', pcm(chunks * Q), origin)
    result = seal(recording, sid, 1, origin + chunks * Q)
    assert result['state'] == 'published' and result['released']
    return result


@pytest.mark.parametrize('order', [(0, 1, 2, 3), (3, 2, 0, 1)])
def test_historical_prefix_suffix_contained_and_spanning_frontier(fixture, order):
    store, recording, sid = fixture
    origin = 900007
    first = first_page(recording, sid, origin=origin, chunks=2)
    before_originals, before_work = paths(store, sid)
    create(recording, sid, 2)
    intervals = [(0, 24000), (2 * Q - 24000, 2 * Q),
                 (8000, 16000), (2 * Q - 16000, 3 * Q)]
    expected = {}
    for index in order:
        start, end = intervals[index]
        data = pcm(end - start, start)
        receipt = put(recording, sid, 2, f'source.second.{index}', data, origin + start)
        expected[receipt['source_id']] = wav_bytes(data)
    result = seal(recording, sid, 2, origin + 3 * Q)
    assert result['state'] == 'published' and result['released']
    assert len(result['work_chunks']) == 1
    assert result['work_chunks'][0]['frames'] == Q
    originals, work = paths(store, sid)
    assert originals[:len(before_originals)] == before_originals
    assert work[:len(before_work)] == before_work
    assert [row['position'] for row in work] == [0, 1, 2]
    assert [row['timeline_start'] for row in work] == [0., 10.5, 21.]
    assert [row['capture_start_frame'] for row in work] == [origin, origin + Q, origin + 2 * Q]
    assert joined(work) == pcm(3 * Q)
    current_first = recording.page_status(sid, CAPTURE, 1)
    assert {key: value for key, value in current_first.items() if key != 'capture_end_frame'} == {
        key: value for key, value in first.items() if key != 'capture_end_frame'}
    historical = {row['id'] for row in before_work}
    assert any(row['relation'] == 'exact_alias' and row['chunk_id'] in historical
               for row in result['ranges'])
    assert_complete_map(store, sid, first)
    assert_complete_map(store, sid, result)
    for source in originals[len(before_originals):]:
        assert Path(source['path']).read_bytes() == expected[source['id']]
        assert source['digest'] == hashlib.sha256(expected[source['id']]).hexdigest()
    summary = recording.status(sid, CAPTURE)
    assert summary['mode'] == 'sealed_pages' and summary['state'] == 'open'
    assert summary['capture_origin_frame'] == origin
    assert summary['capture_end_frame'] == origin + 3 * Q
    assert summary['page_count'] == 2 and not summary['eos']


def test_one_sample_historical_conflict_retains_previous_history(fixture):
    store, recording, sid = fixture
    first = first_page(recording, sid)
    originals_before, work_before = paths(store, sid)
    create(recording, sid, 2)
    put(recording, sid, 2, 'new.tail', pcm(Q + 1000, Q - 1000), Q - 1000)
    different = bytearray(pcm(1000, 100))
    different[246] ^= 1
    receipt = put(recording, sid, 2, 'old.conflict', bytes(different), 100)
    result = seal(recording, sid, 2, 2 * Q)
    assert result['state'] == 'unresolved' and not result['released']
    assert any(row['kind'] == 'pcm_conflict' for row in result['blockers'])
    assert result['ranges'] == result['work_chunks'] == []
    originals, work = paths(store, sid)
    assert work == work_before and originals[:len(originals_before)] == originals_before
    assert recording.page_status(sid, CAPTURE, 1) == first
    assert recording.status(sid, CAPTURE)['capture_end_frame'] == Q
    original = next(row for row in originals if row['id'] == receipt['source_id'])
    assert Path(original['path']).read_bytes() == wav_bytes(bytes(different))
    assert seal(recording, sid, 2, 2 * Q) == result
    assert boundary.stream_info(store, work[0])['end'] == 10.5
    assert store.claim()['id'] == work_before[0]['id']


@pytest.mark.parametrize('offset,kind,end', [(None, 'unknown_placement', 2 * Q),
                                           (2 * Q, 'capture_gap', 3 * Q)])
def test_unresolved_next_page_does_not_hide_published_prefix(fixture, offset, kind, end):
    store, recording, sid = fixture
    first_page(recording, sid)
    _, before = paths(store, sid)
    create(recording, sid, 2)
    put(recording, sid, 2, 'unresolved', pcm(Q), offset)
    result = seal(recording, sid, 2, end)
    assert result['state'] == 'unresolved' and not result['released']
    assert kind in {item['kind'] for item in result['blockers']}
    assert not result['work_chunks'] and not result['ranges']
    assert paths(store, sid)[1] == before
    assert boundary.stream_info(store, before[0])['end'] == 10.5
    assert recording.status(sid, CAPTURE)['capture_end_frame'] == Q


def test_alias_only_page_and_same_request_retries_create_no_new_work(fixture):
    store, recording, sid = fixture
    first_page(recording, sid)
    _, before = paths(store, sid)
    create(recording, sid, 2)
    data = pcm(18000, 1234)
    receipt = put(recording, sid, 2, 'alias.only', data, 1234)
    revision = recording.page_status(sid, CAPTURE, 2)['revision']
    result = seal(recording, sid, 2, Q, revision=revision)
    assert result['state'] == 'published' and result['released']
    assert result['work_chunks'] == [] and result['ranges']
    assert {row['relation'] for row in result['ranges']} == {'exact_alias'}
    assert_complete_map(store, sid, result)
    assert paths(store, sid)[1] == before
    assert seal(recording, sid, 2, Q, revision=revision) == result
    assert put(recording, sid, 2, 'alias.only', data, 1234) == {
        'source_id': receipt['source_id'], 'duplicate': True}
    assert recording.page_status(sid, CAPTURE, 2)['revision'] == revision
    with pytest.raises(ValueError):
        seal(recording, sid, 2, Q, key='different.seal', revision=revision)
    with pytest.raises(ValueError):
        seal(recording, sid, 2, Q, eos=True, revision=revision)
    assert recording.status(sid, CAPTURE)['capture_end_frame'] == Q
    assert not recording.status(sid, CAPTURE)['eos']


def test_origin_relative_geometry_and_short_eos_keep_global_positions(fixture):
    store, recording, sid = fixture
    origin = 234567
    with pytest.raises(ValueError):
        create(recording, sid, 1)
    page = create(recording, sid, 1, origin)
    assert create(recording, sid, 1, origin) == page
    with pytest.raises(ValueError):
        create(recording, sid, 1, origin + 1)
    put(recording, sid, 1, 'first', pcm(Q), origin)
    with pytest.raises(ValueError):
        seal(recording, sid, 1, origin + Q - 1)
    assert recording.page_status(sid, CAPTURE, 1)['state'] == 'open'
    assert seal(recording, sid, 1, origin + Q)['released']
    create(recording, sid, 2)
    put(recording, sid, 2, 'last', pcm(123, Q), origin + Q)
    with pytest.raises(ValueError):
        seal(recording, sid, 2, origin + Q + 123)
    result = seal(recording, sid, 2, origin + Q + 123, eos=True)
    assert result['released']
    _, work = paths(store, sid)
    assert [row['frames'] for row in work] == [Q, 123]
    assert [row['position'] for row in work] == [0, 1]
    assert [row['timeline_start'] for row in work] == [0., 10.5]
    assert joined(work) == pcm(Q + 123)
    summary = recording.status(sid, CAPTURE)
    assert summary['eos'] and summary['state'] == 'queued'
    assert summary['capture_end_frame'] == origin + Q + 123
    with store.db() as db:
        rows = [dict(row) for row in db.execute('SELECT * FROM stream_chunks WHERE session_id=? ORDER BY position', (sid,))]
    assert [row['stream_final'] for row in rows] == [0, 1]
    assert [row['upload_stream_final'] for row in rows] == [0, 1]
    assert len({row['stream_id'] for row in rows}) == 1
    with pytest.raises(ValueError):
        create(recording, sid, 3)


def test_geometry_capacity_and_stale_revision_reject_before_freeze(fixture):
    _, recording, sid = fixture
    create(recording, sid, 1, 17)
    put(recording, sid, 1, 'source', pcm(Q), 17)
    with pytest.raises(ValueError):
        seal(recording, sid, 1, 17 + Q, revision=0)
    with pytest.raises((ValueError, OverflowError)):
        seal(recording, sid, 1, 17 + 29 * Q)
    assert recording.page_status(sid, CAPTURE, 1)['state'] == 'open'
    assert seal(recording, sid, 1, 17 + Q)['released']


def test_pending_page_uniqueness_and_capture_wide_source_key_binding(fixture, monkeypatch):
    store, recording, sid = fixture
    page = create(recording, sid, 1, 0)
    with pytest.raises(ValueError):
        create(recording, sid, 2)
    with pytest.raises(ValueError):
        create(recording, sid, 3)
    data = pcm(Q)
    receipt = put(recording, sid, 1, 'capture.wide.key', data, 0)
    with pytest.raises(ValueError):
        put(recording, sid, 1, 'capture.wide.key', data, 1)
    advance = recording.advance
    monkeypatch.setattr(recording, 'advance', lambda *args, **kwargs: False)
    pending = seal(recording, sid, 1, Q)
    assert pending['state'] == 'pending_receipts' and not pending['released']
    with pytest.raises(ValueError):
        create(recording, sid, 2)
    assert advance(None, force=True)
    create(recording, sid, 2)
    with pytest.raises(ValueError):
        put(recording, sid, 2, 'capture.wide.key', data, 0)
    assert put(recording, sid, 1, 'capture.wide.key', data, 0) == {
        'source_id': receipt['source_id'], 'duplicate': True}
    with store.db() as db:
        batch = recording._batch(db, sid, CAPTURE)
    wrong_page_path = recording.source_path(batch, page)
    wrong_page_path.write_bytes(wav_bytes(data))
    try:
        with pytest.raises(ValueError):
            recording.accept_page(sid, CAPTURE, 2, 'wrong.page.path', wrong_page_path, Q)
    finally:
        wrong_page_path.unlink(missing_ok=True)
    assert recording.page_status(sid, CAPTURE, 2)['sources'] == []


def test_prior_visibility_and_waiting_input_wake_are_atomic_with_next_release(fixture, monkeypatch):
    store, recording, sid = fixture
    first_page(recording, sid)
    prior = store.claim()
    boundary.wait_for_input(store, prior['id'], 10.5, False)
    assert store.chunk(prior['id'])['status'] == 'waiting_input'
    advance = recording.advance
    monkeypatch.setattr(recording, 'advance', lambda *args, **kwargs: False)
    create(recording, sid, 2)
    put(recording, sid, 2, 'second', pcm(Q, Q), Q)
    second = seal(recording, sid, 2, 2 * Q)
    assert second['state'] == 'pending_receipts' and not second['released']
    staged = paths(store, sid)[1][-1]
    assert boundary.stream_info(store, prior)['end'] == 10.5
    assert not boundary.stream_info(store, prior)['final']
    assert boundary.stream_info(store, staged) is None
    assert [row['id'] for row in boundary.context_parts(store, prior, 0., 10.5)] == [prior['id']]
    with pytest.raises(ValueError):
        boundary.context_parts(store, prior, 10., 11.)
    with pytest.raises(ValueError):
        boundary.context_parts(store, staged, 10.5, 11.)
    assert recording.status(sid, CAPTURE)['capture_end_frame'] == Q
    assert store.claim() is None
    observed = []
    wake = boundary._wake_published_stream

    def observe_uncommitted_wake(db, session, stream_id):
        wake(db, session, stream_id)
        # A distinct real SQLite reader must see the entire previous state,
        # even after publication has queued/woken rows in its write transaction.
        with store.db() as outside:
            statuses = {row['id']: row['status'] for row in outside.execute(
                'SELECT id,status FROM chunks WHERE session_id=?', (sid,))}
        observed.append(statuses)
        assert statuses[prior['id']] == 'waiting_input'
        assert statuses[staged['id']] == 'staging'

    monkeypatch.setattr(boundary, '_wake_published_stream', observe_uncommitted_wake)
    assert advance(None, force=True)
    assert observed
    assert recording.page_status(sid, CAPTURE, 2)['released']
    assert recording.status(sid, CAPTURE)['capture_end_frame'] == 2 * Q
    assert store.chunk(prior['id'])['status'] == 'queued'
    assert store.chunk(staged['id'])['status'] == 'queued'
    assert boundary.stream_info(store, prior)['end'] == 21.
    assert len(boundary.context_parts(store, prior, 10., 11.)) == 2
    assert store.claim()['id'] == prior['id']


def test_generic_stream_finish_cannot_bypass_capture_eos(fixture, monkeypatch):
    store, recording, sid = fixture
    first_page(recording, sid)
    _, work = paths(store, sid)
    stream_id = boundary.stream_info(store, work[0])['stream_id']
    with pytest.raises(ValueError):
        boundary.finish_stream(store, sid, stream_id)
    create(recording, sid, 2)
    summary = recording.status(sid, CAPTURE)
    with pytest.raises(ValueError):
        recording.finish(sid, CAPTURE, 'finish.capture', summary['revision'])
    put(recording, sid, 2, 'alias', pcm(100), 0)
    advance = recording.advance
    monkeypatch.setattr(recording, 'advance', lambda *args, **kwargs: False)
    pending = seal(recording, sid, 2, Q)
    if not pending['released']:
        with pytest.raises(ValueError):
            recording.finish(sid, CAPTURE, 'finish.capture', recording.status(sid, CAPTURE)['revision'])
        assert advance(None, force=True)
    revision = recording.status(sid, CAPTURE)['revision']
    result = recording.finish(sid, CAPTURE, 'finish.capture', revision)
    assert result['eos'] and result['state'] == 'queued'
    assert result['capture_end_frame'] == Q
    assert recording.finish(sid, CAPTURE, 'finish.capture', revision) == result
    assert boundary.stream_info(store, work[0])['final']
    with store.db() as db:
        row = db.execute('SELECT stream_final,upload_stream_final FROM stream_chunks WHERE chunk_id=?', (work[0]['id'],)).fetchone()
    assert tuple(row) == (1, 0)
    with pytest.raises(ValueError):
        create(recording, sid, 3)


def test_real_128_chunk_backpressure_retries_same_frozen_page(fixture):
    store, recording, sid = fixture
    assert store.settings.recording_capture_bytes == 1024**3
    assert store.settings.recording_total_bytes == 4 * 1024**3
    frontier = 0
    # At most ten chunks per original source stays under the source duration cap.
    for number, chunks in enumerate((28, 28, 28, 28, 16), 1):
        create(recording, sid, number, 0 if number == 1 else None)
        page_end = frontier + chunks * Q
        while frontier < page_end:
            count = min(10 * Q, page_end - frontier)
            put(recording, sid, number, f'page.{number}.source.{frontier}', pcm(count, frontier), frontier)
            frontier += count
        assert seal(recording, sid, number, frontier)['released']
    _, before = paths(store, sid)
    assert len(before) == 128
    assert [row['position'] for row in before] == list(range(128))
    assert all(row['status'] == 'queued' for row in before)
    create(recording, sid, 6)
    put(recording, sid, 6, 'retry.source', pcm(Q, frontier), frontier)
    revision = recording.page_status(sid, CAPTURE, 6)['revision']
    with pytest.raises(OverflowError):
        seal(recording, sid, 6, frontier + Q, revision=revision)
    page = recording.page_status(sid, CAPTURE, 6)
    assert page['state'] == 'sealed' and not page['released']
    assert page['revision'] == revision and page['work_chunks'] == []
    assert paths(store, sid)[1] == before
    assert recording.status(sid, CAPTURE)['capture_end_frame'] == frontier
    with pytest.raises(ValueError):
        seal(recording, sid, 6, frontier + Q, revision=revision, key='changed.retry')
    # Explicit model-free completion fixture frees exactly one pending slot.
    # This does not claim to test inference or transcript generation.
    with store.db() as db:
        db.execute("UPDATE chunks SET status='completed' WHERE id=?", (before[0]['id'],))
    result = seal(recording, sid, 6, frontier + Q, revision=revision)
    assert result['released'] and len(result['work_chunks']) == 1
    assert seal(recording, sid, 6, frontier + Q, revision=revision) == result
    _, after = paths(store, sid)
    assert len(after) == 129 and len({row['id'] for row in after}) == 129
    assert after[-1]['position'] == 128
    assert after[-1]['timeline_start'] == 128 * 10.5
    with store.db() as db:
        count = db.execute("SELECT COUNT(*) FROM chunks WHERE session_id=? AND status='queued'", (sid,)).fetchone()[0]
    assert count == 128
    summary = recording.status(sid, CAPTURE)
    assert summary['page_count'] == 6 and summary['capture_end_frame'] == 129 * Q
    assert not {'sources', 'ranges', 'work_chunks'}.intersection(summary)
    cursor = 0
    seen = []
    for expected in ([1, 2], [3, 4], [5, 6]):
        listing = recording.list_pages(sid, CAPTURE, after=cursor, limit=2)
        assert [row['page_number'] for row in listing['pages']] == expected
        assert all(not {'sources', 'ranges', 'work_chunks'}.intersection(row) for row in listing['pages'])
        cursor = listing['next_cursor']
        assert cursor == expected[-1]
        seen.extend(expected)
    assert seen == list(range(1, 7))
    assert recording.list_pages(sid, CAPTURE, after=cursor, limit=2)['pages'] == []


def test_cancel_waiting_predecessor_aborts_capture_and_staged_next_without_eos(fixture, monkeypatch):
    store, recording, sid = fixture
    first_page(recording, sid)
    prior = store.claim()
    boundary.wait_for_input(store, prior['id'], 10.5, False)
    advance = recording.advance
    monkeypatch.setattr(recording, 'advance', lambda *args, **kwargs: False)
    create(recording, sid, 2)
    put(recording, sid, 2, 'second', pcm(Q, Q), Q)
    assert not seal(recording, sid, 2, 2 * Q)['released']
    originals, work = paths(store, sid)
    store.set_state(prior['id'], 'cancel')
    advance(None, force=True)
    summary = recording.status(sid, CAPTURE)
    assert summary['state'] == 'aborted' and not summary['eos']
    assert summary['capture_end_frame'] == Q
    assert recording.page_status(sid, CAPTURE, 1)['released']
    assert not recording.page_status(sid, CAPTURE, 2)['released']
    assert all(store.chunk(row['id'])['status'] == 'cancelled' for row in work)
    assert all(Path(row['path']).exists() for row in originals + work)
    assert boundary.stream_info(store, work[-1]) is None
    assert store.claim() is None
    with pytest.raises(ValueError):
        create(recording, sid, 3)


def test_delete_cleans_published_and_staged_pages_with_retryable_original_failure(fixture, monkeypatch):
    store, recording, sid = fixture
    first_page(recording, sid)
    monkeypatch.setattr(recording, 'advance', lambda *args, **kwargs: False)
    create(recording, sid, 2)
    put(recording, sid, 2, 'second', pcm(Q, Q), Q)
    assert not seal(recording, sid, 2, 2 * Q)['released']
    originals, work = paths(store, sid)
    retained = originals[0]['path']

    def unlink(raw):
        if raw == retained:
            raise PermissionError('fixture retained original')
        Path(raw).unlink(missing_ok=True)

    result = store.delete_session(sid, unlink)
    assert result['cleanup_pending'] == 1 and Path(retained).exists()
    assert all(not Path(row['path']).exists() for row in originals + work if row['path'] != retained)
    with store.db() as db:
        for table in ('recording_batches', 'recording_pages', 'recording_sources',
                      'recording_work', 'recording_ranges', 'chunks', 'stream_chunks', 'stream_time_index'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    assert store.delete_session(sid)['cleanup_pending'] == 0
    assert not Path(retained).exists()
    assert list((store.settings.data_dir / 'uploads').glob('ro-*')) == []
    assert store.claim() is None


def batch_and_page(recording, sid, number=1):
    with recording.store.db() as db:
        return recording._batch(db, sid, CAPTURE), recording.page_status(sid, CAPTURE, number)


@pytest.mark.parametrize('suffix', ['.part', '.audio'])
def test_configurable_quota_counts_uncommitted_and_orphan_copies(fixture, suffix):
    store, recording, sid = fixture
    create(recording, sid, 1, 0)
    batch, page = batch_and_page(recording, sid)
    initial_total, initial_capture, initial_page = recording._page_census(batch['id'], page['id'])
    path = store.settings.data_dir / 'uploads' / f"ro-{batch['id']}-page-{page['id']}-orphan{suffix}"
    path.write_bytes(b'copy' * 2048)
    charge = max(path.stat().st_size, path.stat().st_blocks * 512)
    total, capture, own = recording._page_census(batch['id'], page['id'])
    assert (total, capture, own) == (initial_total + charge, initial_capture + charge, initial_page + charge)
    store.settings.recording_capture_bytes = capture - 1
    with pytest.raises(OverflowError, match='quota'):
        recording.reserve_page_bytes(batch, page, 0)
    store.settings.recording_capture_bytes = 1024**3
    store.settings.recording_total_bytes = total - 1
    with pytest.raises(OverflowError, match='quota'):
        recording.reserve_page_bytes(batch, page, 0)
    store.settings.recording_total_bytes = total + 4096
    token = recording.reserve_page_bytes(batch, page, 1)
    recording.release_reservation(token)
    assert path.read_bytes() == b'copy' * 2048


def test_fixed_page_quota_includes_allocated_staging_copy(fixture):
    store, recording, sid = fixture
    create(recording, sid, 1, 0)
    batch, page = batch_and_page(recording, sid)
    path = recording.source_path(batch, page)
    # Sparse size is charged even if the filesystem has not allocated blocks.
    with path.open('wb') as output:
        output.truncate(32 * 1024**2 + 1)
    with pytest.raises(OverflowError, match='quota'):
        recording.reserve_page_bytes(batch, page, 0)
    assert recording.page_status(sid, CAPTURE, 1)['sources'] == []
    assert path.exists()


@pytest.mark.parametrize('setting', ['recording_capture_bytes', 'recording_total_bytes'])
def test_concurrent_reservations_cannot_both_spend_last_slot(fixture, setting):
    store, recording, sid = fixture
    create(recording, sid, 1, 0)
    batch, page = batch_and_page(recording, sid)
    total, capture, _ = recording._page_census(batch['id'], page['id'])
    setattr(store.settings, setting, (capture if setting == 'recording_capture_bytes' else total) + 4096)
    barrier = threading.Barrier(3)
    tokens, failures = [], []

    def reserve():
        barrier.wait(timeout=3)
        try:
            tokens.append(recording.reserve_page_bytes(batch, page, 4096))
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=3)
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert len(tokens) == 1 and len(failures) == 1
    assert isinstance(failures[0], OverflowError)
    recording.release_reservation(tokens[0])
    retry = recording.reserve_page_bytes(batch, page, 4096)
    recording.release_reservation(retry)
    assert recording._reservations == {}


def test_mocked_5_5_gib_floor_uses_stricter_global_or_recording_setting(fixture, monkeypatch):
    store, recording, sid = fixture
    create(recording, sid, 1, 0)
    batch, page = batch_and_page(recording, sid)
    floor = 11 * 1024**3 // 2
    available = [floor + 4096]
    monkeypatch.setattr(resources.shutil, 'disk_usage', lambda path: SimpleNamespace(free=available[0]))
    monkeypatch.setattr(resources, 'tree_rss', lambda: 0)
    monkeypatch.setattr(resources.psutil, 'virtual_memory', lambda: SimpleNamespace(available=1024**3))
    store.settings.recording_disk_reserve_bytes = floor
    store.settings.disk_reserve_bytes = 2 * 1024**3
    token = recording.reserve_page_bytes(batch, page, 1)
    recording.release_reservation(token)
    available[0] -= 1
    with pytest.raises(RuntimeError, match='[Dd]isk reserve'):
        recording.reserve_page_bytes(batch, page, 1)
    store.settings.disk_reserve_bytes = 6 * 1024**3
    available[0] = floor + 4096
    with pytest.raises(RuntimeError, match='[Dd]isk reserve'):
        recording.reserve_page_bytes(batch, page, 1)
    available[0] = 6 * 1024**3 + 4096
    token = recording.reserve_page_bytes(batch, page, 1)
    recording.release_reservation(token)
    assert recording._reservations == {}


def test_page_resource_checks_preserve_native_permit_without_reloading_settings(fixture, monkeypatch):
    store, recording, sid = fixture
    create(recording, sid, 1, 0)
    observed = []
    permit = SimpleNamespace(observe_primary=lambda: observed.append('same permit'))
    store.settings._native_execution_permit = permit
    monkeypatch.setattr(resources.shutil, 'disk_usage', lambda path: SimpleNamespace(free=8 * 1024**3))
    monkeypatch.setattr(resources, 'tree_rss', lambda: 0)
    monkeypatch.setattr(resources.psutil, 'virtual_memory', lambda: SimpleNamespace(available=1024**3))

    def forbidden_reload(self):
        pytest.fail('A resource check must not reconstruct Settings or reload policy files')

    monkeypatch.setattr(Settings, '__post_init__', forbidden_reload)
    recording._page_resources(4096)
    assert store.settings._native_execution_permit is permit
    assert observed == ['same permit']


def test_disk_floor_includes_other_outstanding_reservation(fixture, monkeypatch):
    store, recording, sid = fixture
    create(recording, sid, 1, 0)
    batch, page = batch_and_page(recording, sid)
    floor = 11 * 1024**3 // 2
    store.settings.recording_disk_reserve_bytes = floor
    monkeypatch.setattr(resources.shutil, 'disk_usage', lambda path: SimpleNamespace(free=floor + 4096))
    monkeypatch.setattr(resources, 'tree_rss', lambda: 0)
    monkeypatch.setattr(resources.psutil, 'virtual_memory', lambda: SimpleNamespace(available=1024**3))
    first = recording.reserve_page_bytes(batch, page, 4096)
    try:
        # Either upload fits alone; the first outstanding reservation spends
        # the same free-space headroom before its bytes reach the filesystem.
        with pytest.raises(RuntimeError, match='[Dd]isk reserve'):
            recording.reserve_page_bytes(batch, page, 4096)
        assert set(recording._reservations) == {first}
    finally:
        recording.release_reservation(first)
    second = recording.reserve_page_bytes(batch, page, 4096)
    recording.release_reservation(second)


def test_delete_owned_orphan_parts_retries_failure_and_rejects_unrelated_part(fixture):
    from audio_backend.lifecycle import unlink_owned
    store, recording, sid = fixture
    first_page(recording, sid)
    create(recording, sid, 2)
    batch, page = batch_and_page(recording, sid, 2)
    source_part = recording.source_path(batch, page)
    work_part = source_part.parent / f"ro-{batch['id']}-page-{page['id']}-work-001.part"
    unrelated = source_part.parent / 'unrelated.part'
    source_part.write_bytes(b'partial original')
    work_part.write_bytes(b'partial canonical')
    unrelated.write_bytes(b'unrelated')
    with pytest.raises(ValueError):
        unlink_owned(store, str(unrelated))
    assert unrelated.read_bytes() == b'unrelated'

    def fail_one(raw):
        if raw == str(source_part):
            raise PermissionError('fixture pending original staging')
        unlink_owned(store, raw)

    deleted = store.delete_session(sid, fail_one)
    assert deleted['cleanup_pending'] == 1
    assert source_part.exists() and not work_part.exists()
    assert store.delete_session(sid)['cleanup_pending'] == 0
    assert not source_part.exists() and unrelated.exists()


def test_sixteen_sources_per_page_allow_duplicate_receipt_at_capacity(fixture):
    store, recording, sid = fixture
    create(recording, sid, 1, 0)
    receipts = [put(recording, sid, 1, f'source.{index}', pcm(1, index), index) for index in range(16)]
    with pytest.raises(OverflowError, match='count'):
        put(recording, sid, 1, 'seventeenth', pcm(1, 16), 16)
    assert put(recording, sid, 1, 'source.0', pcm(1), 0) == {
        'source_id': receipts[0]['source_id'], 'duplicate': True}
    assert recording.page_status(sid, CAPTURE, 1)['revision'] == 16
    result = seal(recording, sid, 1, 16, eos=True)
    assert result['released'] and len(result['sources']) == 16
    assert len(paths(store, sid)[0]) == 16
    assert joined(paths(store, sid)[1]) == pcm(16)
    assert_complete_map(store, sid, result)


def test_1024_page_bound_and_bounded_listing_without_large_audio(fixture):
    store, recording, sid = fixture
    create(recording, sid, 1, 0)
    with store.db() as db:
        batch = recording._batch(db, sid, CAPTURE)
        # Explicit synthetic released-page history isolates metadata/count
        # limits. It makes no claim to have published 1023 audio pages.
        db.execute("UPDATE recording_pages SET released=1,state='published',end_frame=0 WHERE batch_id=?", (batch['id'],))
        db.executemany('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,start_frame,end_frame,created)
            VALUES(?,?,?,'published',1,0,0,0)''',
            [(f'fixture.page.{number}', batch['id'], number) for number in range(2, 1024)])
    last = create(recording, sid, 1024)
    assert last['page_number'] == 1024
    with pytest.raises(ValueError):
        create(recording, sid, 1025)
    assert recording.status(sid, CAPTURE)['page_count'] == 1024
    assert len(recording.list_pages(sid, CAPTURE)['pages']) == 32
    assert len(recording.list_pages(sid, CAPTURE, limit=64)['pages']) == 64
    assert [row['page_number'] for row in recording.list_pages(sid, CAPTURE, after=1023)['pages']] == [1024]
    for after, limit in ((-1, 1), (1025, 1), (0, 0), (0, 65), (True, 1), (0, True)):
        with pytest.raises(ValueError):
            recording.list_pages(sid, CAPTURE, after=after, limit=limit)


def test_old_schema_migration_preserves_legacy_rows_and_null_page_membership(tmp_path):
    path = tmp_path / 'legacy-schema.sqlite3'
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE sessions(id TEXT PRIMARY KEY);
            CREATE TABLE chunks(id TEXT PRIMARY KEY);
            CREATE TABLE recording_batches(id TEXT PRIMARY KEY,session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id),
                capture_id TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'open',
                finish_key TEXT,finish_request TEXT,capture_origin_frame INTEGER,capture_end_frame INTEGER,
                manifest TEXT,blockers TEXT NOT NULL DEFAULT '[]',created REAL NOT NULL,UNIQUE(session_id,capture_id));
            CREATE TABLE recording_sources(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,
                batch_id TEXT NOT NULL REFERENCES recording_batches(id) ON DELETE CASCADE,idempotency_key TEXT NOT NULL,
                digest TEXT NOT NULL,path TEXT NOT NULL UNIQUE,frames INTEGER NOT NULL,bytes INTEGER NOT NULL,
                source_start_frame INTEGER,format TEXT NOT NULL,created REAL NOT NULL,UNIQUE(batch_id,idempotency_key));
            CREATE TABLE recording_work(chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                batch_id TEXT NOT NULL REFERENCES recording_batches(id) ON DELETE CASCADE,position INTEGER NOT NULL,
                frames INTEGER NOT NULL,bytes INTEGER NOT NULL,capture_start_frame INTEGER NOT NULL,
                native_receipt_status TEXT NOT NULL,UNIQUE(batch_id,position));
            INSERT INTO sessions VALUES('session.old');
            INSERT INTO chunks VALUES('chunk.old');
            INSERT INTO recording_batches(id,session_id,capture_id,revision,state,capture_origin_frame,capture_end_frame,created)
                VALUES('batch.old','session.old','capture.old',1,'queued',20,21,1);
            INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,frames,bytes,source_start_frame,format,created)
                VALUES('source.old','batch.old','source.key','digest.original','retained.audio',1,46,20,'wav-pcm16le-16000-mono',1);
            INSERT INTO recording_work VALUES('chunk.old','batch.old',0,1,46,20,'disabled');
        ''')
        before = {table: dict(db.execute(f'SELECT * FROM {table}').fetchone())
                  for table in ('recording_batches', 'recording_sources', 'recording_work')}
        mod.init_schema(db)
        mod.init_schema(db)  # Additive migration is itself idempotent.
        for table, original in before.items():
            current = dict(db.execute(f'SELECT * FROM {table}').fetchone())
            assert {key: current[key] for key in original} == original
        batch = dict(db.execute('SELECT * FROM recording_batches').fetchone())
        assert batch['mode'] == 'legacy' and batch['eos'] == 0
        assert db.execute('SELECT page_id FROM recording_sources').fetchone()[0] is None
        assert db.execute('SELECT page_id FROM recording_work').fetchone()[0] is None
        assert db.execute('SELECT COUNT(*) FROM recording_pages').fetchone()[0] == 0


def test_page_http_flow_strict_bodies_and_private_bounded_status(tmp_path):
    cfg = Settings(data_dir=tmp_path, recording_disk_reserve_bytes=0,
                   disk_reserve_bytes=0, start_worker=False)
    with TestClient(create_app(cfg)) as client:
        sid = client.post('/sessions', json={'name': 'page HTTP fixture'}).json()['id']
        url = f'/sessions/{sid}/recordings/{CAPTURE}'
        for body in ({'page_number': True, 'capture_origin_frame': 0},
                     {'page_number': '1', 'capture_origin_frame': 0},
                     {'page_number': 1, 'capture_origin_frame': 0.0},
                     {'page_number': 1, 'capture_origin_frame': 0, 'unexpected': 1},
                     {'page_number': 1025, 'capture_origin_frame': 0}):
            assert client.post(url + '/pages', json=body).status_code == 422
        created = client.post(url + '/pages', json={'page_number': 1, 'capture_origin_frame': 37})
        assert created.status_code == 202 and created.json()['state'] == 'open'
        payload = wav_bytes(pcm(Q))
        for offset in ('-1', '1.5', 'true'):
            assert client.put(url + '/pages/1/sources?source_start_frame=' + offset,
                              content=payload, headers={'Idempotency-Key': 'invalid.' + offset}).status_code == 422
        accepted = client.put(url + '/pages/1/sources?source_start_frame=37', content=payload,
                              headers={'Idempotency-Key': 'first'})
        assert accepted.status_code == 202 and not accepted.json()['duplicate']
        source_id = accepted.json()['source_id']
        duplicate = client.put(url + '/pages/1/sources?source_start_frame=37', content=payload,
                               headers={'Idempotency-Key': 'first'})
        assert duplicate.status_code == 202 and duplicate.json()['duplicate']
        assert duplicate.json()['source_id'] == source_id
        valid = {'expected_revision': 1, 'idempotency_key': 'seal.first', 'end_frame': 37 + Q}
        for change in ({'expected_revision': True}, {'expected_revision': '1'},
                       {'end_frame': float(Q + 37)}, {'eos': 'false'}, {'unexpected': 1}):
            assert client.post(url + '/pages/1/seal', json={**valid, **change}).status_code == 422
        sealed = client.post(url + '/pages/1/seal', json=valid)
        assert sealed.status_code == 202 and sealed.json()['released']
        listing = client.get(url + '/pages?after=0&limit=1')
        assert listing.status_code == 200 and listing.json()['next_cursor'] == 1
        assert [item['page_number'] for item in listing.json()['pages']] == [1]
        assert not {'sources', 'work_chunks', 'ranges'}.intersection(listing.json()['pages'][0])
        assert client.get(url + '/pages?limit=65').status_code == 422
        for endpoint in (url, url + '/pages', url + '/pages/1'):
            response = client.get(endpoint)
            assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
            assert str(tmp_path) not in response.text
        summary = client.get(url).json()
        assert summary['mode'] == 'sealed_pages' and summary['capture_end_frame'] == 37 + Q
        assert not {'sources', 'work_chunks', 'ranges'}.intersection(summary)
        assert client.put(url + '/sources?source_start_frame=37', content=payload,
                          headers={'Idempotency-Key': 'legacy.mix'}).status_code == 409
        final = client.post(url + '/finish', json={'idempotency_key': 'finish.capture',
                                                  'expected_revision': summary['revision']})
        assert final.status_code == 202 and final.json()['eos']
        assert client.post(url + '/pages', json={'page_number': 2}).status_code == 409
        assert client.delete(f'/sessions/{sid}').json()['cleanup_pending'] == 0


def crash_seal(store, sid, mode):
    """Real process death at a named publication boundary, no models or server."""
    code = '''
import os,sys
from pathlib import Path
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.recording_overlap import RecordingOverlap
root=Path(sys.argv[1])
cfg=Settings(data_dir=root,disk_reserve_bytes=0,recording_disk_reserve_bytes=0,start_worker=False)
store=Store(root/'jobs.sqlite3',cfg)
if sys.argv[3]=='prestage':
    original=RecordingOverlap._materialize
    def stop(self,*args,**kwargs):
        original(self,*args,**kwargs)
        os._exit(73)
    RecordingOverlap._materialize=stop
else:
    RecordingOverlap.advance=lambda *args,**kwargs:os._exit(74)
RecordingOverlap(store).seal_page(sys.argv[2],'capture.pages',2,'seal.2',1,336000)
'''
    result = subprocess.run([sys.executable, '-B', '-c', code, str(store.settings.data_dir), sid, mode],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == (73 if mode == 'prestage' else 74), result.stderr.decode()


def test_prestage_process_death_retries_exact_seal_and_cleans_only_own_work(fixture):
    store, recording, sid = fixture
    first = first_page(recording, sid)
    create(recording, sid, 2)
    put(recording, sid, 2, 'second', pcm(Q, Q), Q)
    batch, page = batch_and_page(recording, sid, 2)
    original_paths, before_work = paths(store, sid)
    source_orphan = recording.source_path(batch, page)
    source_orphan.write_bytes(b'preserve source staging')
    other_work = store.settings.data_dir / 'uploads' / f"ro-{batch['id']}-page-{first['id']}-work-999.audio"
    other_work.write_bytes(b'preserve other page orphan')
    crash_seal(store, sid, 'prestage')
    assert recording.page_status(sid, CAPTURE, 2)['state'] == 'preparing'
    assert paths(store, sid)[1] == before_work
    orphan_work = store.settings.data_dir / 'uploads' / f"ro-{batch['id']}-page-{page['id']}-work-001.audio"
    assert orphan_work.exists()
    store.recover()
    assert recording.page_status(sid, CAPTURE, 2)['state'] == 'sealed'
    with pytest.raises(ValueError):
        seal(recording, sid, 2, 2 * Q, key='changed.after.crash')
    assert orphan_work.exists() and source_orphan.exists() and other_work.exists()
    result = seal(recording, sid, 2, 2 * Q)
    assert result['released'] and len(result['work_chunks']) == 1
    assert paths(store, sid)[0] == original_paths
    assert paths(store, sid)[1][0] == before_work[0]
    assert joined(paths(store, sid)[1]) == pcm(2 * Q)
    assert source_orphan.read_bytes() == b'preserve source staging'
    assert other_work.read_bytes() == b'preserve other page orphan'


def test_poststage_process_death_reuses_ids_payloads_and_never_rematerializes(fixture, monkeypatch):
    store, recording, sid = fixture
    first_page(recording, sid)
    create(recording, sid, 2)
    put(recording, sid, 2, 'second', pcm(Q, Q), Q)
    crash_seal(store, sid, 'poststage')
    before = recording.page_status(sid, CAPTURE, 2)
    assert before['state'] == 'pending_receipts' and not before['released']
    with store.db() as db:
        payloads = [tuple(row) for row in db.execute('SELECT chunk_id,native_receipt,receipt_id FROM recording_work ORDER BY position')]
    originals, work = paths(store, sid)
    stable = [(row['id'], row['digest'], row['path']) for row in work]
    store.recover()
    restored = mod.RecordingOverlap(store)

    def forbidden_materialize(*args, **kwargs):
        pytest.fail('Complete staged outbox must never rematerialize audio')

    monkeypatch.setattr(restored, '_materialize', forbidden_materialize)
    assert seal(restored, sid, 2, 2 * Q) == before
    assert restored.advance(None, force=True)
    assert restored.page_status(sid, CAPTURE, 2)['released']
    store.recover()
    assert not restored.advance(None, force=True)
    assert [(row['id'], row['digest'], row['path']) for row in paths(store, sid)[1]] == stable
    assert paths(store, sid)[0] == originals
    with store.db() as db:
        assert [tuple(row) for row in db.execute('SELECT chunk_id,native_receipt,receipt_id FROM recording_work ORDER BY position')] == payloads
    assert joined(paths(store, sid)[1]) == pcm(2 * Q)


def test_recovery_rechecks_only_unreleased_page_receipts(fixture, monkeypatch):
    store, recording, sid = fixture
    first_page(recording, sid)
    first_id = paths(store, sid)[1][0]['id']
    monkeypatch.setattr(recording, 'advance', lambda *args, **kwargs: False)
    create(recording, sid, 2)
    put(recording, sid, 2, 'second', pcm(Q, Q), Q)
    pending = seal(recording, sid, 2, 2 * Q)
    second_id = pending['work_chunks'][0]['chunk_id']
    # Explicit receipt-state projection fixture tests recover()'s SQL scope;
    # genuine receipt-owner integration is exercised separately below.
    with store.db() as db:
        db.execute("UPDATE recording_work SET receipt_state='durable',native_receipt_status='receipt_retained'")
    store.recover()
    with store.db() as db:
        rows = {row['chunk_id']: dict(row) for row in db.execute('SELECT * FROM recording_work')}
    assert rows[first_id]['receipt_state'] == 'durable'
    assert rows[first_id]['native_receipt_status'] == 'receipt_retained'
    assert rows[second_id]['receipt_state'] == 'pending'
    assert rows[second_id]['native_receipt_status'] == 'restart_recheck_pending'
    assert recording.page_status(sid, CAPTURE, 1)['released']
    assert not recording.page_status(sid, CAPTURE, 2)['released']


@pytest.mark.parametrize('change', ['accepted_source_bytes', 'historical_derived_signature'])
def test_mutated_alias_authority_blocks_new_page_without_revising_prior_results(fixture, change):
    store, recording, sid = fixture
    first_page(recording, sid)
    first_id = paths(store, sid)[1][0]['id']
    with store.db() as db:
        db.execute("UPDATE chunks SET status='completed' WHERE id=?", (first_id,))
        db.execute("INSERT INTO segments(chunk_id,start,end,text,speaker_status) VALUES(?,0,1,'retained fixture transcript','unknown')", (first_id,))
        before_results = [dict(row) for row in db.execute('SELECT * FROM segments')]
    create(recording, sid, 2)
    put(recording, sid, 2, 'historical.alias', pcm(1000, 100), 100)
    originals, work = paths(store, sid)
    if change == 'accepted_source_bytes':
        with Path(originals[-1]['path']).open('r+b') as output:
            output.seek(100)
            value = output.read(1)
            output.seek(100)
            output.write(bytes([value[0] ^ 1]))
        blocker = 'original_unavailable'
    else:
        path = Path(work[0]['path'])
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        blocker = 'historical_authority_unavailable'
    result = seal(recording, sid, 2, Q)
    assert result['state'] == 'unresolved' and not result['released']
    assert blocker in {row['kind'] for row in result['blockers']}
    assert result['ranges'] == result['work_chunks'] == []
    assert paths(store, sid)[1] == work
    assert recording.status(sid, CAPTURE)['capture_end_frame'] == Q
    with store.db() as db:
        assert [dict(row) for row in db.execute('SELECT * FROM segments')] == before_results


@pytest.fixture
def native_pages(native):
    # Reuse the existing real descriptor/authority/owner fixture, whose binary
    # qualification is explicitly simulated. It never loads an acoustic model.
    native.store.settings.recording_disk_reserve_bytes = 0
    native.store.settings.disk_reserve_bytes = 0
    return native


def native_page(native, number):
    create(native.recording, native.sid, number, 19 if number == 1 else None)
    put(native.recording, native.sid, number, f'native.source.{number}', pcm(Q, (number - 1) * Q),
        19 + (number - 1) * Q)
    return native.recording.seal_page(native.sid, CAPTURE, number, f'seal.{number}', 1,
                                     19 + number * Q, cells=native.capture)


def drain_page(native, number):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        native.recording.advance(native.capture, force=True)
        page = native.recording.page_status(native.sid, CAPTURE, number)
        if page['state'] != 'pending_receipts':
            return page
        flight = native.recording._flight
        if flight is not None:
            flight['operation'].slot_released.wait(.02)
        else:
            time.sleep(.01)
    pytest.fail('Bounded fixture receipt drain did not finish this page')


def receipt_payloads(native):
    with native.store.db() as db:
        return [tuple(row) for row in db.execute('SELECT chunk_id,native_receipt,receipt_id FROM recording_work ORDER BY position')]


def test_second_page_owner_commit_before_ack_reconciles_without_replacing_payload(native_pages, monkeypatch):
    native = native_pages
    from audio_backend.native_owned_cells import Unavailable
    native_page(native, 1)
    assert drain_page(native, 1)['released']
    previous = paths(native.store, native.sid)[1][0]
    original = native.owner._transaction
    failures = []

    @contextmanager
    def commit_then_unknown(budget):
        with original(budget):
            yield
        if not failures:
            failures.append(True)
            raise Unavailable('cooperative_deadline')

    monkeypatch.setattr(native.owner, '_transaction', commit_then_unknown)
    result = native_page(native, 2)
    assert result['state'] == 'pending_receipts'
    stable = receipt_payloads(native)
    operation = native.recording._flight['operation']
    assert operation.slot_released.wait(3)
    cid = result['work_chunks'][0]['chunk_id']
    assert native.owner.receipt(cid) is None
    assert boundary.stream_info(native.store, previous)['end'] == 10.5
    assert drain_page(native, 2)['released']
    assert failures == [True] and receipt_payloads(native) == stable
    current = native.recording.page_status(native.sid, CAPTURE, 2)
    assert current['optional_receipts']['durable'] == 1
    assert current['work_chunks'][0]['receipt_attempts'] == 2
    assert native.recording.page_status(native.sid, CAPTURE, 1)['optional_receipts']['durable'] == 1
    receipt = native.owner.receipt(cid)
    assert receipt.frames == Q and receipt.timeline_start == 10.5
    assert receipt.stream_id == boundary.stream_info(native.store, previous)['stream_id']


def test_second_page_revocation_before_ack_keeps_prefix_visible_and_page_fenced(native_pages, monkeypatch):
    native = native_pages
    native_page(native, 1)
    assert drain_page(native, 1)['released']
    native_page(native, 2)
    stable = receipt_payloads(native)
    operation = native.recording._flight['operation']
    assert operation.slot_released.wait(3)
    original = native.capture.authority.receipt_valid
    revoked = []

    def revoke_after_valid(receipt, budget):
        answer = original(receipt, budget)
        if threading.current_thread() is threading.main_thread() and not revoked:
            revoked.append(True)
            native.owner.revoked()
        return answer

    monkeypatch.setattr(native.capture.authority, 'receipt_valid', revoke_after_valid)
    native.recording.advance(native.capture, force=True)
    current = native.recording.page_status(native.sid, CAPTURE, 2)
    assert revoked and current['state'] == 'pending_receipts' and not current['released']
    assert current['optional_receipts']['durable'] == 0
    work = paths(native.store, native.sid)[1]
    assert boundary.stream_info(native.store, work[0])['end'] == 10.5
    assert boundary.stream_info(native.store, work[1]) is None
    assert receipt_payloads(native) == stable
    monkeypatch.setattr(native.capture.authority, 'receipt_valid', original)
    assert drain_page(native, 2)['released']
    assert receipt_payloads(native) == stable


def test_native_recovery_keeps_released_ack_and_rechecks_only_pending_page(native_pages, monkeypatch):
    native = native_pages
    native_page(native, 1)
    assert drain_page(native, 1)['released']
    native_page(native, 2)
    operation = native.recording._flight['operation']
    assert operation.slot_released.wait(3)
    publish = native.recording._publish_pending
    monkeypatch.setattr(native.recording, '_publish_pending', lambda *args, **kwargs: False)
    native.recording.advance(native.capture, force=True)
    assert native.recording.page_status(native.sid, CAPTURE, 2)['optional_receipts']['durable'] == 1
    stable = receipt_payloads(native)
    native.store.recover()
    assert native.recording.page_status(native.sid, CAPTURE, 1)['optional_receipts']['durable'] == 1
    assert native.recording.page_status(native.sid, CAPTURE, 2)['optional_receipts']['durable'] == 0
    monkeypatch.setattr(native.recording, '_publish_pending', publish)
    native.recording = mod.RecordingOverlap(native.store)

    def forbidden_materialize(*args, **kwargs):
        pytest.fail('Native receipt recovery must replay the existing outbox')

    monkeypatch.setattr(native.recording, '_materialize', forbidden_materialize)
    assert drain_page(native, 2)['released']
    assert receipt_payloads(native) == stable
    assert native.recording.page_status(native.sid, CAPTURE, 1)['optional_receipts']['durable'] == 1
    assert native.recording.page_status(native.sid, CAPTURE, 2)['optional_receipts']['durable'] == 1
    for index, (cid, _, _) in enumerate(stable):
        assert native.owner.receipt(cid).timeline_start == index * 10.5


def test_native_pending_page_does_not_starve_unrelated_stream_claim(native_pages, monkeypatch):
    native = native_pages
    native_page(native, 1)
    assert drain_page(native, 1)['released']
    prior = paths(native.store, native.sid)[1][0]
    native.store.set_state(prior['id'], 'pause')
    monkeypatch.setattr(native.recording, 'advance', lambda *args, **kwargs: False)
    assert native_page(native, 2)['state'] == 'pending_receipts'
    other = native.store.create_session('unrelated primary work')['id']
    payload = wav_bytes(pcm(RATE))
    path = native.root / 'uploads' / 'unrelated.audio'
    path.write_bytes(payload)
    cid, created = native.store.add_chunk(other, 'other.source', hashlib.sha256(payload).hexdigest(),
                                          path, 1., 0., stream_id='unrelated.stream', stream_final=True)
    assert created and native.store.claim()['id'] == cid
    native.store.set_state(cid, 'cancel')
    assert native.recording.page_status(native.sid, CAPTURE, 2)['state'] == 'pending_receipts'
    assert native.store.claim() is None


def test_native_release_rollback_does_not_wake_waiting_prefix_early(native_pages):
    native = native_pages
    native_page(native, 1)
    assert drain_page(native, 1)['released']
    prior = native.store.claim()
    boundary.wait_for_input(native.store, prior['id'], 10.5, False)
    native_page(native, 2)
    stable = receipt_payloads(native)
    operation = native.recording._flight['operation']
    assert operation.slot_released.wait(3)
    with native.store.db() as db:
        db.execute('''CREATE TRIGGER fixture_abort_page_release BEFORE UPDATE OF released ON recording_pages
            WHEN NEW.page_number=2 AND NEW.released=1
            BEGIN SELECT RAISE(ABORT,'fixture release rollback'); END''')
    assert not native.recording.advance(native.capture, force=True)
    current = native.recording.page_status(native.sid, CAPTURE, 2)
    assert current['state'] == 'pending_receipts' and not current['released']
    assert current['optional_receipts']['durable'] == 1
    assert native.store.chunk(prior['id'])['status'] == 'waiting_input'
    assert current['work_chunks'][0]['status'] == 'staging'
    assert boundary.stream_info(native.store, prior)['end'] == 10.5
    assert native.store.claim() is None
    with native.store.db() as db:
        db.execute('DROP TRIGGER fixture_abort_page_release')
    assert native.recording.advance(native.capture, force=True)
    assert native.recording.page_status(native.sid, CAPTURE, 2)['released']
    assert native.store.chunk(prior['id'])['status'] == 'queued'
    assert boundary.stream_info(native.store, prior)['end'] == 21.
    assert receipt_payloads(native) == stable


def inject_generated_failure(patch, boundary_name, error_number, target):
    """Fail the second generated piece, after the first .audio already exists."""
    hits = []

    def fail():
        hits.append(boundary_name)
        assert target.with_name(target.name.replace('002.part', '001.audio')).exists()
        raise OSError(error_number, f'fixture generated {boundary_name} failure')

    if boundary_name == 'write':
        original = wave.Wave_write.writeframesraw

        def write(stream, data):
            if Path(stream._file.name) == target:
                fail()
            return original(stream, data)

        patch.setattr(wave.Wave_write, 'writeframesraw', write)
    elif boundary_name == 'fsync':
        original = mod.os.fsync

        def fsync(fd):
            if target.exists():
                expected, actual = target.stat(), os.fstat(fd)
                if (expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino):
                    fail()
            return original(fd)

        patch.setattr(mod.os, 'fsync', fsync)
    else:
        assert boundary_name == 'rename'
        original = mod.os.replace

        def rename(source, destination):
            if Path(source) == target:
                fail()
            return original(source, destination)

        patch.setattr(mod.os, 'replace', rename)
    return hits


@pytest.mark.parametrize('error_number', [errno.ENOSPC, errno.EDQUOT])
@pytest.mark.parametrize('boundary_name', ['write', 'fsync', 'rename'])
def test_materialization_capacity_errno_keeps_exact_seal_retryable(fixture, monkeypatch, error_number, boundary_name):
    store, recording, sid = fixture
    first = first_page(recording, sid)
    create(recording, sid, 2)
    put(recording, sid, 2, 'second', pcm(2 * Q, Q), Q)
    batch, page = batch_and_page(recording, sid, 2)
    originals, previous_work = paths(store, sid)
    original_bytes = {row['path']: Path(row['path']).read_bytes() for row in originals}
    prefix = f"ro-{batch['id']}-page-{page['id']}-work-"
    target = recording.uploads / f'{prefix}002.part'
    with monkeypatch.context() as patch:
        hits = inject_generated_failure(patch, boundary_name, error_number, target)
        with pytest.raises(OSError) as failure:
            seal(recording, sid, 2, 3 * Q, revision=1)
        assert failure.value.errno == error_number and hits == [boundary_name]
    failed = recording.page_status(sid, CAPTURE, 2)
    assert failed['state'] == 'sealed' and not failed['released']
    assert failed['revision'] == 1 and failed['work_chunks'] == failed['ranges'] == []
    assert failed['blockers'][0]['kind'] == 'retryable_backpressure'
    assert recording._reservations == {}
    assert list(recording.uploads.glob(prefix + '*')) == []
    assert paths(store, sid) == (originals, previous_work)
    assert recording.page_status(sid, CAPTURE, 1) == first
    assert {path: Path(path).read_bytes() for path in original_bytes} == original_bytes
    with store.db() as db:
        frozen = dict(db.execute('SELECT seal_key,seal_request,revision FROM recording_pages WHERE id=?', (page['id'],)).fetchone())
    assert frozen['seal_key'] == 'seal.2' and frozen['revision'] == 1
    with pytest.raises(ValueError):
        seal(recording, sid, 2, 3 * Q, key='changed.capacity.retry', revision=1)
    result = seal(recording, sid, 2, 3 * Q, revision=1)
    assert result['released'] and len(result['work_chunks']) == 2
    assert seal(recording, sid, 2, 3 * Q, revision=1) == result
    with store.db() as db:
        assert dict(db.execute('SELECT seal_key,seal_request,revision FROM recording_pages WHERE id=?', (page['id'],)).fetchone()) == frozen
    assert recording._reservations == {}
    assert paths(store, sid)[0] == originals
    assert paths(store, sid)[1][:len(previous_work)] == previous_work
    assert joined(paths(store, sid)[1]) == pcm(3 * Q)
    assert not list(recording.uploads.glob(prefix + '*.part'))


def test_noncapacity_materialization_oserror_stays_integrity_unresolved(fixture, monkeypatch):
    store, recording, sid = fixture
    first = first_page(recording, sid)
    create(recording, sid, 2)
    put(recording, sid, 2, 'second', pcm(2 * Q, Q), Q)
    batch, page = batch_and_page(recording, sid, 2)
    before = paths(store, sid)
    prefix = f"ro-{batch['id']}-page-{page['id']}-work-"
    target = recording.uploads / f'{prefix}002.part'
    with monkeypatch.context() as patch:
        hits = inject_generated_failure(patch, 'rename', errno.EIO, target)
        with pytest.raises(OSError) as failure:
            seal(recording, sid, 2, 3 * Q, revision=1)
        assert failure.value.errno == errno.EIO and hits == ['rename']
    result = recording.page_status(sid, CAPTURE, 2)
    assert result['state'] == 'unresolved' and not result['released']
    assert result['blockers'][0]['kind'] == 'page_integrity_conflict'
    assert result['work_chunks'] == result['ranges'] == []
    assert seal(recording, sid, 2, 3 * Q, revision=1) == result
    assert recording._reservations == {} and not list(recording.uploads.glob(prefix + '*'))
    assert paths(store, sid) == before
    assert recording.page_status(sid, CAPTURE, 1) == first
    assert all(Path(row['path']).exists() for row in before[0] + before[1])
