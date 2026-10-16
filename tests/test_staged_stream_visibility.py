"""Model-free publication fence: real SQLite, stream reservations and small PCM."""
from contextlib import contextmanager
import hashlib
import sqlite3
import threading
import wave

import pytest

from audio_backend import boundary_store as boundary
from audio_backend.recording_overlap import RecordingOverlap
from audio_backend.settings import Settings
from audio_backend.storage import Store


@pytest.fixture
def setup(tmp_path):
    (tmp_path / 'uploads').mkdir()
    settings = Settings(data_dir=tmp_path, disk_reserve_bytes=0, start_worker=False)
    store = Store(tmp_path / 'jobs.sqlite3', settings)
    return store, store.create_session('visibility fence')['id'], tmp_path


def add(setup, key, *, staged=False, final=False, offset=None, stream='mic'):
    store, sid, root = setup
    path = root / 'uploads' / (key + '.audio')
    if not path.exists():
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
            output.writeframes(b'\1\0' * 8000)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        cid, fresh = store._add_chunk(db, sid, key, digest, path, .5, offset=offset,
            stream_id=stream, stream_final=final, _status='staging' if staged else 'queued')
    return cid, fresh


def row(store, cid):
    with store.db() as db:
        return dict(db.execute('SELECT * FROM chunks WHERE id=?', (cid,)).fetchone())


def pending_successor(setup, *, final=True):
    store, _, _ = setup
    first, _ = add(setup, 'first')
    chunk = store.claim()
    assert chunk['id'] == first
    boundary.wait_for_input(store, first, .5, False)
    second, _ = add(setup, 'second', staged=True, final=final)
    return first, second, chunk


def release(store, ids):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        boundary.publish_staged_stream_chunks(db, ids)


def test_staging_cannot_wake_or_be_claimed(setup):
    store, _, _ = setup
    first, second, _ = pending_successor(setup)
    assert store.chunk(first)['status'] == 'waiting_input'
    assert store.chunk(second)['status'] == 'staging'
    assert store.claim() is None


def test_staged_context_and_final_are_invisible(setup):
    store, _, _ = setup
    first, second, chunk = pending_successor(setup)
    info = boundary.stream_info(store, chunk)
    assert (info['end'], info['final']) == (.5, False)
    assert store.chunk(second)['stream_final']  # Public receipt retains the reserved request.
    assert [part['id'] for part in boundary.context_parts(store, chunk, 0, .5)] == [first]
    with pytest.raises(ValueError, match='Incomplete'):
        boundary.context_parts(store, chunk, 0, 1)
    assert boundary.stream_info(store, row(store, second)) is None
    with pytest.raises(ValueError, match='not published'):
        boundary.context_parts(store, row(store, second), .5, 1)


def test_wait_race_does_not_treat_staging_as_input(setup):
    store, _, _ = setup
    first, _ = add(setup, 'first')
    assert store.claim()['id'] == first
    add(setup, 'second', staged=True, final=True)
    boundary.wait_for_input(store, first, .5, False)
    assert store.chunk(first)['status'] == 'waiting_input'


def test_release_exposes_context_final_and_wake_together(setup):
    store, _, _ = setup
    first, second, chunk = pending_successor(setup)
    release(store, [second])
    assert store.chunk(first)['status'] == 'queued'
    assert store.chunk(second)['status'] == 'queued'
    assert boundary.stream_info(store, chunk)['final']
    assert [part['id'] for part in boundary.context_parts(store, chunk, 0, 1)] == [first, second]
    assert store.claim()['id'] == first


def test_release_then_wait_race_cannot_lose_wake(setup):
    store, _, _ = setup
    first, _ = add(setup, 'first')
    assert store.claim()['id'] == first
    second, _ = add(setup, 'second', staged=True, final=True)
    release(store, [second])
    boundary.wait_for_input(store, first, .5, False)
    assert store.chunk(first)['status'] == 'queued'


def test_atomic_suffix_release_rollback_and_reader_snapshot(setup):
    store, _, _ = setup
    first, second, chunk = pending_successor(setup)
    with pytest.raises(RuntimeError, match='injected rollback'):
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            boundary.publish_staged_stream_chunks(db, [second])
            assert db.execute('SELECT status FROM chunks WHERE id=?', (first,)).fetchone()[0] == 'queued'
            assert boundary.stream_info(store, chunk)['end'] == .5
            assert store.chunk(first)['status'] == 'waiting_input'
            raise RuntimeError('injected rollback')
    assert store.chunk(first)['status'] == 'waiting_input'
    assert store.chunk(second)['status'] == 'staging'
    with store.db() as snapshot:
        snapshot.execute('BEGIN')
        assert snapshot.execute('SELECT status FROM chunks WHERE id=?', (first,)).fetchone()[0] == 'waiting_input'
        release(store, [second])
        assert snapshot.execute('SELECT status FROM chunks WHERE id=?', (first,)).fetchone()[0] == 'waiting_input'
        class SnapshotStore:
            @contextmanager
            def db(self):
                yield snapshot
        assert boundary.stream_info(SnapshotStore(), chunk)['end'] == .5
    assert store.chunk(first)['status'] == 'queued'
    assert boundary.stream_info(store, chunk)['end'] == 1


def test_reservations_duplicate_final_and_append_guards(setup):
    store, sid, _ = setup
    first, second, _ = pending_successor(setup, final=False)
    assert add(setup, 'second', staged=True) == (second, False)
    with pytest.raises(ValueError, match='final flag'):
        add(setup, 'second', staged=True, final=True)
    with pytest.raises(ValueError, match='contiguous'):
        add(setup, 'gap', staged=True, offset=2)
    with pytest.raises(ValueError, match='unpublished'):
        add(setup, 'ordinary')
    with pytest.raises(ValueError, match='unpublished'):
        boundary.finish_stream(store, sid, 'mic')
    third, _ = add(setup, 'third', staged=True, final=True)
    with pytest.raises(ValueError, match='finalized'):
        add(setup, 'after-final', staged=True)
    with pytest.raises(ValueError, match='prefix'):
        release(store, [third])
    with pytest.raises(ValueError, match='reservation tail'):
        release(store, [second])
    release(store, [second, third])
    assert boundary.stream_info(store, row(store, first))['end'] == 1.5
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM stream_chunks').fetchone()[0] == 3


def test_cancelled_predecessor_cannot_release_reserved_suffix(setup):
    store, _, _ = setup
    first, second, _ = pending_successor(setup, final=False)
    store.set_state(first, 'cancel')
    with pytest.raises(ValueError, match='cancelled'):
        release(store, [second])
    assert store.chunk(second)['status'] == 'staging'


def test_ordinary_upload_and_finalize_behavior_is_unchanged(setup):
    store, sid, _ = setup
    first, _ = add(setup, 'first')
    chunk = store.claim(); boundary.wait_for_input(store, first, .5, False)
    second, _ = add(setup, 'second')
    assert store.chunk(first)['status'] == 'queued'
    assert boundary.stream_info(store, chunk)['end'] == 1
    assert len(boundary.context_parts(store, chunk, 0, 1)) == 2
    assert boundary.finish_stream(store, sid, 'mic')['last_chunk_id'] == second
    assert boundary.stream_info(store, chunk)['final']


def staged_recording(setup, monkeypatch):
    store, sid, _ = setup
    recording = RecordingOverlap(store)
    batch = recording.begin(sid, 'capture')
    path = recording.source_path(batch)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
        output.writeframes(b'\1\0' * 8000)
    recording.accept(sid, 'capture', 'source', path, 0)
    monkeypatch.setattr(recording, 'advance', lambda *args, **kwargs: False)
    status = recording.status(sid, 'capture')
    staged = recording.finish(sid, 'capture', 'finish', status['revision'], None)
    assert staged['state'] == 'pending_receipts'
    with store.db() as db:
        bid = db.execute('SELECT id FROM recording_batches WHERE session_id=?', (sid,)).fetchone()[0]
        cid = db.execute('SELECT chunk_id FROM recording_work WHERE batch_id=?', (bid,)).fetchone()[0]
    return recording, bid, cid


def test_cancelled_unreleased_recording_stays_invisible(setup, monkeypatch):
    store, sid, _ = setup
    recording, bid, cid = staged_recording(setup, monkeypatch)
    recording.cancel(sid, 'capture')
    assert store.chunk(cid)['status'] == 'cancelled'
    assert boundary.stream_info(store, row(store, cid)) is None
    assert store.chunk(cid)['stream_final']  # Reservation metadata is not worker EOS.
    with pytest.raises(ValueError, match='not published'):
        boundary.context_parts(store, row(store, cid), 0, .5)
    assert not recording._publish_pending(bid, None)
    assert store.claim() is None


def test_batch_fence_survives_processing_status_changes(setup, monkeypatch):
    store, _, _ = setup
    recording, bid, cid = staged_recording(setup, monkeypatch)
    with store.db() as db:
        db.execute("UPDATE chunks SET status='queued' WHERE id=?", (cid,))
    assert store.claim() is None
    assert boundary.stream_info(store, row(store, cid)) is None
    with store.db() as db:
        db.execute("UPDATE chunks SET status='staging' WHERE id=?", (cid,))
    assert recording._publish_pending(bid, None)
    store.set_state(cid, 'cancel')
    assert boundary.stream_info(store, row(store, cid))['final']
    assert len(boundary.context_parts(store, row(store, cid), 0, .5)) == 1


@pytest.mark.parametrize('change', ['stream_final', 'upload_stream_final', 'source_session'])
def test_changed_reservation_blocks_outbox_publication(setup, monkeypatch, change):
    store, _, _ = setup
    recording, bid, cid = staged_recording(setup, monkeypatch)
    other = store.create_session('other')['id'] if change == 'source_session' else None
    with store.db() as db:
        if other:
            db.execute('UPDATE chunks SET session_id=? WHERE id=?', (other, cid))
        else:
            db.execute(f'UPDATE stream_chunks SET {change}=0 WHERE chunk_id=?', (cid,))
    with pytest.raises(ValueError, match='ordinary work changed'):
        recording._publish_pending(bid, None)
    assert store.chunk(cid)['status'] == 'staging'
    assert store.claim() is None and boundary.stream_info(store, row(store, cid)) is None


def test_pending_receipt_and_busy_publish_leave_work_retryable(setup, monkeypatch):
    store, _, _ = setup
    recording, bid, cid = staged_recording(setup, monkeypatch)
    # The publication gate uses actual stored receipt state; no fake semantic result.
    with store.db() as db:
        db.execute("UPDATE recording_work SET receipt_state='pending' WHERE chunk_id=?", (cid,))
    # Its frozen receipt is absent, so malformed receipt state fails closed.
    with pytest.raises(ValueError, match='incomplete'):
        recording._publish_pending(bid, None)
    assert boundary.stream_info(store, row(store, cid)) is None and store.claim() is None
    with store.db() as db:
        db.execute("UPDATE recording_work SET receipt_state='gap' WHERE chunk_id=?", (cid,))
    writer = sqlite3.connect(store.path)
    try:
        writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            recording._publish_pending(bid, None)
        assert boundary.stream_info(store, row(store, cid)) is None
    finally:
        writer.rollback(); writer.close()
    assert recording._publish_pending(bid, None)
    assert boundary.stream_info(store, row(store, cid))['final']


def test_publish_rollback_restart_replay_and_output_idempotency(setup, monkeypatch):
    store, sid, _ = setup
    recording, bid, cid = staged_recording(setup, monkeypatch)
    with store.db() as db:
        db.execute("""CREATE TRIGGER fail_release BEFORE UPDATE OF state ON recording_batches
            WHEN NEW.state='queued' BEGIN SELECT RAISE(ABORT,'injected release crash'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='release crash'):
        recording._publish_pending(bid, None)
    assert store.chunk(cid)['status'] == 'staging'
    assert boundary.stream_info(store, row(store, cid)) is None
    with store.db() as db:
        db.execute('DROP TRIGGER fail_release')
    recovered = Store(store.path, store.settings); recovered.recover()
    restarted = RecordingOverlap(recovered)
    assert restarted._publish_pending(bid, None)
    assert not restarted._publish_pending(bid, None)
    claimed = recovered.claim()
    result = {'segments': [{'start': 0, 'end': .5, 'text': 'synthetic fixture'}], 'events': [], 'metadata': {}}
    assert recovered.commit_window(claimed, 0, .5, result)
    assert not recovered.commit_window(claimed, 0, .5, result)
    assert recovered.claim() is None
    with recovered.db() as db:
        assert db.execute('SELECT COUNT(*) FROM stream_chunks').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM stream_time_index').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM segments').fetchone()[0] == 1
    assert not restarted._publish_pending(bid, None)


def test_reader_snapshot_remains_hidden_across_release(setup, monkeypatch):
    store, _, _ = setup
    recording, bid, cid = staged_recording(setup, monkeypatch)
    with store.db() as snapshot:
        snapshot.execute('BEGIN')
        assert snapshot.execute('SELECT status FROM chunks WHERE id=?', (cid,)).fetchone()[0] == 'staging'
        completed = []
        thread = threading.Thread(target=lambda: completed.append(recording._publish_pending(bid, None)))
        thread.start(); thread.join(3)
        assert not thread.is_alive() and completed == [True]
        class SnapshotStore:
            @contextmanager
            def db(self):
                yield snapshot
        assert boundary.stream_info(SnapshotStore(), {'id': cid}) is None
        with pytest.raises(ValueError, match='not published'):
            boundary.context_parts(SnapshotStore(), row(store, cid), 0, .5)
        assert snapshot.execute('SELECT status FROM chunks WHERE id=?', (cid,)).fetchone()[0] == 'staging'
    assert boundary.stream_info(store, row(store, cid))['final']

