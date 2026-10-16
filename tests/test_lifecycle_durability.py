"""Real SQLite/filesystem failures; no models, only test-owned child processes."""
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from audio_backend import change_feed, lifecycle
from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.storage import Store


def make_store(tmp_path):
    settings = Settings(data_dir=tmp_path, disk_reserve_bytes=0, start_worker=False)
    (tmp_path / 'uploads').mkdir(exist_ok=True)
    store = Store(tmp_path / 'jobs.sqlite3', settings)
    change_feed.initialize(store)
    return store


def add_file(store, sid, key, stream_id=None):
    path = store.settings.data_dir / 'uploads' / (key + '.audio')
    path.write_bytes(b'original source bytes, not a valid inference fixture')
    cid, _ = store.add_chunk(sid, key, key, path, 1, stream_id=stream_id)
    return cid, path


def count_pending(store):
    with store.db() as db:
        return db.execute('SELECT COUNT(*) FROM session_file_cleanup').fetchone()[0]


def test_delete_failure_does_not_leave_live_chunks_without_sources(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session('private name', 'private-group')['id']
    _, first = add_file(store, sid, 'first')
    _, second = add_file(store, sid, 'second')
    attempted = []

    def fail_second(raw):
        attempted.append(raw)
        if len(attempted) == 2:
            raise PermissionError('simulated locked file')
        Path(raw).unlink()

    result = store.delete_session(sid, fail_second)
    assert result == {'deleted': sid, 'cleanup_pending': 1}
    assert not first.exists() and second.exists()
    with pytest.raises(KeyError):
        store.session(sid)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM chunks WHERE session_id=?', (sid,)).fetchone()[0] == 0
        tombstone = dict(db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone())
        assert tombstone == {'id': sid, 'name': '', 'identity_group': '', 'created': 0., 'timeline_end': 0., 'deleted': 1, 'processing_mode': 'legacy'}
        assert db.execute('SELECT COUNT(*) FROM result_changes WHERE session_id=?', (sid,)).fetchone()[0] == 0
        assert db.execute('SELECT attempts FROM session_file_cleanup').fetchone()[0] == 1
    assert store.delete_session(sid)['cleanup_pending'] == 0
    assert not second.exists() and count_pending(store) == 0
    assert store.delete_session(sid)['cleanup_pending'] == 0


def test_database_rollback_precedes_every_unlink(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session('keep me')['id']
    cid, source = add_file(store, sid, 'source')
    with store.db() as db:
        db.execute("CREATE TRIGGER injected_failure BEFORE DELETE ON chunks BEGIN SELECT RAISE(ABORT,'injected database failure'); END")
    attempted = []
    with pytest.raises(Exception, match='injected database failure'):
        store.delete_session(sid, lambda raw: attempted.append(raw))
    assert not attempted and source.exists()
    assert store.session(sid)['name'] == 'keep me'
    assert store.chunk(cid)['status'] == 'queued'
    assert count_pending(store) == 0


def test_actual_process_death_after_unlink_recovers_receipts(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session('crash')['id']
    _, first = add_file(store, sid, 'first')
    _, second = add_file(store, sid, 'second')
    code = '''
import os,sys
from pathlib import Path
from audio_backend.settings import Settings
from audio_backend.storage import Store
p=Path(sys.argv[1]);s=Store(p/'jobs.sqlite3',Settings(data_dir=p,start_worker=False,disk_reserve_bytes=0))
def crash_after_unlink(raw):
    Path(raw).unlink()
    os._exit(73)
s.delete_session(sys.argv[2],crash_after_unlink)
'''
    proc = subprocess.run([sys.executable, '-c', code, str(tmp_path), sid], timeout=10, capture_output=True)
    assert proc.returncode == 73, proc.stderr.decode()
    assert not first.exists() and second.exists()
    assert count_pending(store) == 2  # Receipt removal never ran after first unlink.
    # Actual application startup replays both intents with missing_ok semantics.
    with TestClient(create_app(store.settings)) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/sessions/' + sid).status_code == 404
        assert client.delete('/sessions/' + sid).json()['cleanup_pending'] == 0
    assert not second.exists() and count_pending(store) == 0


def test_locked_cleanup_survives_startup_and_explicit_retry(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    sid = store.create_session('locked')['id']
    _, source = add_file(store, sid, 'source')
    original = lifecycle.unlink_owned

    def locked(*args):
        raise PermissionError('simulated locked file')

    monkeypatch.setattr(lifecycle, 'unlink_owned', locked)
    assert store.delete_session(sid)['cleanup_pending'] == 1
    with TestClient(create_app(store.settings)) as client:
        assert source.exists()  # Orphan scan must not bypass failed cleanup intent.
        response = client.delete('/sessions/' + sid)
        assert response.status_code == 200 and response.json()['cleanup_pending'] == 1
        assert 'pending' in response.json()['note']
        monkeypatch.setattr(lifecycle, 'unlink_owned', original)
        assert client.delete('/sessions/' + sid).json()['cleanup_pending'] == 0
    assert not source.exists()


def test_cleanup_never_deletes_an_unowned_source(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session('bounds')['id']
    external = tmp_path / 'outside.audio'
    external.write_bytes(b'keep')
    store.add_chunk(sid, 'outside', 'outside', external, 1)
    assert store.delete_session(sid)['cleanup_pending'] == 1
    assert external.read_bytes() == b'keep'


def test_cancel_marks_descendants_terminal_and_preserves_prefix(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session('stream')['id']
    first, p1 = add_file(store, sid, 'first', 'mic')
    second, p2 = add_file(store, sid, 'second', 'mic')
    third, p3 = add_file(store, sid, 'third', 'mic')
    other, _ = add_file(store, sid, 'independent')
    claimed = store.claim()
    assert claimed['id'] == first
    assert store.commit_window(claimed, 0, 1, {'segments': [{'start': 0, 'end': .5, 'text': 'prefix'}], 'events': []})
    store.set_state(second, 'cancel')
    assert store.chunk(first)['status'] == 'completed'
    assert store.chunk(second)['status'] == store.chunk(third)['status'] == 'cancelled'
    assert 'earlier stream chunk' in store.chunk(third)['error']
    assert store.claim()['id'] == other
    assert store.results(sid)['segments'][0]['text'] == 'prefix'
    assert all(p.exists() for p in (p1, p2, p3))
    with pytest.raises(ValueError, match='finalized'):
        store.add_chunk(sid, 'new', 'new', p1, 1, stream_id='mic')
    changes = change_feed.changes(store, sid)['changes']
    assert any(c['entity_id'] == third and c['data'].get('status') == 'cancelled' for c in changes if c['data'])


def test_recover_repairs_preexisting_stranded_stream_jobs(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session('legacy')['id']
    first, _ = add_file(store, sid, 'first', 'mic')
    second, _ = add_file(store, sid, 'second', 'mic')
    with store.db() as db:
        db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (first,))
    store.recover()
    assert store.chunk(second)['status'] == 'cancelled'
    assert store.chunk(second)['stream_final']
    assert store.claim() is None


def test_descendant_cancel_is_in_the_same_transaction(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session('atomic')['id']
    first, _ = add_file(store, sid, 'first', 'mic')
    second, _ = add_file(store, sid, 'second', 'mic')
    with store.db() as db:
        db.execute(f"CREATE TRIGGER fail_cancel BEFORE UPDATE ON chunks WHEN NEW.id='{second}' BEGIN SELECT RAISE(ABORT,'injected cancellation failure'); END")
    with pytest.raises(Exception, match='injected cancellation failure'):
        store.set_state(first, 'cancel')
    assert store.chunk(first)['status'] == store.chunk(second)['status'] == 'queued'
    assert not store.chunk(second)['stream_final']


def test_cancel_is_scoped_to_one_session_and_stream(tmp_path):
    store = make_store(tmp_path)
    sid = store.create_session('one')['id']
    other_sid = store.create_session('two')['id']
    first, _ = add_file(store, sid, 'first', 'mic')
    second, _ = add_file(store, sid, 'second', 'mic')
    other_stream, _ = add_file(store, sid, 'other-stream', 'other')
    other_session, _ = add_file(store, other_sid, 'other-session', 'mic')
    store.set_state(second, 'pause')
    store.set_state(first, 'cancel')
    assert store.chunk(second)['status'] == 'cancelled'
    for cid in (other_stream, other_session):
        assert store.chunk(cid)['status'] == 'queued'
        assert not store.chunk(cid)['stream_final']


def test_cleanup_pages_do_not_retry_failed_files_in_one_pass(tmp_path):
    store = make_store(tmp_path)
    store.settings.max_pending_chunks = 200
    sid = store.create_session('many')['id']
    for i in range(130):
        add_file(store, sid, f'file-{i:03}')
    calls = []

    def fail_some(raw):
        calls.append(raw)
        if int(Path(raw).stem.split('-')[1]) < 64:
            raise PermissionError('blocked first page')
        Path(raw).unlink()

    result = store.delete_session(sid, fail_some)
    assert len(calls) == len(set(calls)) == 130
    assert result['cleanup_pending'] == 64
    assert store.delete_session(sid)['cleanup_pending'] == 0
    assert not list((tmp_path / 'uploads').iterdir())


from test_moss_result_store import archive_fixture


def test_moss_history_and_counters_erase_atomically_with_primary_session(archive_fixture):
    from test_moss_result_store import snapshot, reserve, rows, usage, TABLE, USAGE
    store, archive, sid = archive_fixture
    value = snapshot(sid); reserve(archive, value); archive.commit(value)
    before = usage(store)
    with store.db() as db:
        db.execute(f"CREATE TRIGGER fail_history_delete BEFORE DELETE ON {TABLE} BEGIN SELECT RAISE(ABORT,'synthetic history delete failure'); END")
    with pytest.raises(Exception, match='synthetic history delete failure'):store.delete_session(sid)
    assert store.session(sid)['id'] == sid
    assert archive.result(sid, value.identity.job_id) == value.result_json
    assert usage(store) == before
    with store.db() as db:db.execute('DROP TRIGGER fail_history_delete')
    store.delete_session(sid)
    assert rows(store, f'SELECT job_id FROM {TABLE}') == []
    assert rows(store, f'SELECT scope,row_count,charge_bytes FROM {USAGE}') == [('', 0, 0)]
    store.delete_session(sid)
    assert rows(store, f'SELECT scope,row_count,charge_bytes FROM {USAGE}') == [('', 0, 0)]
