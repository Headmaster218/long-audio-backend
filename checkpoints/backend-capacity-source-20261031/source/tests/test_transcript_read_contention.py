"""Finite model-free API snapshot contracts on fresh synthetic SQLite.

Normal WAL primary-table writes and artificial DELETE/EXCLUSIVE refusal are
separate cases. This does not exercise Worker/commit_window or explain any
historical failure. Product clocks, deadlines and VM budgets remain unchanged.
"""
import sqlite3
import threading
from urllib.parse import quote

import pytest

from audio_backend import transcript_contract as c, transcript_store as ts
from test_transcript_http import SID, api, data


class _ReadConnections:
    """Observe only the exact product read-only URI; preserve SQLite execution."""

    def __init__(self, monkeypatch, path):
        self.opened = []
        self.uri = 'file:' + quote(str(path.resolve()), safe='/') + '?mode=ro'
        original = sqlite3.connect

        class Connection(sqlite3.Connection):
            def close(self):
                super().close()
                self.successful_closes = getattr(self, 'successful_closes', 0) + 1

        def connect(database, *args, **kwargs):
            if database != self.uri:
                return original(database, *args, **kwargs)
            assert kwargs.get('uri') is True and kwargs.get('timeout') == 0
            assert 'factory' not in kwargs
            connection = original(database, *args, factory=Connection, **kwargs)
            self.opened.append(connection)
            return connection

        monkeypatch.setattr(ts.sqlite3, 'connect', connect)

    def assert_closed(self, count, budget):
        assert len(self.opened) == count
        assert all(getattr(connection, 'successful_closes', 0) == 1
                   for connection in self.opened)
        assert budget._lease_count == budget._lease_bytes == 0


def _revision_state(data):
    """Compute the expected contract from committed counters, not reader output."""
    with data.db() as db:
        session = db.execute('''SELECT transcript_revision,timeline_end
            FROM sessions WHERE id=?''', (SID,)).fetchone()
        change = db.execute('SELECT MAX(cursor) FROM result_changes WHERE session_id=?',
                            (SID,)).fetchone()[0]
    return session['transcript_revision'], c.digest({
        'policy': c.POLICY_HASH, 'session': SID, 'primary_change_max': change or 0,
        'transcript_revision': session['transcript_revision'],
        'timeline_end': session['timeline_end'],
    })


def test_wal_primary_write_preserves_snapshot_and_invalidates_cursor(data, api, monkeypatch):
    assert (ts.READ_SECONDS, ts.MAX_VM_STEPS) == (.75, 250000)
    client, budget, _ = api
    reads = _ReadConnections(monkeypatch, data.path)
    url = f'/sessions/{SID}/transcript'
    old_counter, old_revision = _revision_state(data)
    baseline = client.get(url)
    assert baseline.status_code == 200, baseline.text
    reads.assert_closed(1, budget)
    old_page = baseline.json()
    assert old_page['revision'] == old_revision
    old_cursor = old_page['next_cursor']
    assert c.parse_cursor(old_cursor, SID) == {
        'policy': c.POLICY_HASH, 'session': SID, 'block': 1, 'revision': old_revision}
    old_primary = next(row for row in old_page['reading']
                       if row['canonical_asr']['id'] == 1)
    assert old_primary['text'] == old_primary['raw_text'] == 'primary one'

    ready, commit_requested, committed = (threading.Event() for _ in range(3))
    abort, writer_closed = threading.Event(), threading.Event()
    writer_errors = []
    new_text = 'committed primary one'

    def write_primary():
        connection = None
        try:
            connection = sqlite3.connect(data.path, timeout=0)
            assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('UPDATE segments SET text=?,raw_text=? WHERE id=1',
                               (new_text, new_text))
            assert connection.in_transaction
            ready.set()
            assert commit_requested.wait(5), 'Writer did not receive its finite release'
            if not abort.is_set():
                connection.commit()
                assert not connection.in_transaction
                committed.set()
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            if connection is not None:
                try:
                    if connection.in_transaction:
                        connection.rollback()
                except BaseException as exc:
                    writer_errors.append(exc)
                try:
                    connection.close()
                    writer_closed.set()
                except BaseException as exc:
                    writer_errors.append(exc)

    writer = threading.Thread(target=write_primary, name='transcript-wal-primary-writer')
    writer.start()
    try:
        assert ready.wait(2), f'Writer failed before overlap: {writer_errors!r}'
        assert not writer_errors and writer.is_alive() and not committed.is_set()
        pending = client.get(url)
        assert pending.status_code == 200, pending.text
        reads.assert_closed(2, budget)
        assert pending.content == baseline.content
        assert not committed.is_set()

        original_revision = ts.TranscriptStore._revision
        barrier_visits = []

        def revision_after_commit(reader, db, sid, session, **kwargs):
            if not barrier_visits:
                assert sid == SID and db.in_transaction
                assert session['transcript_revision'] == old_counter
                barrier_visits.append('snapshot_already_established')
                commit_requested.set()
                assert committed.wait(.25), f'Writer COMMIT not acknowledged: {writer_errors!r}'
                assert not writer_errors
                assert session['transcript_revision'] == old_counter
            return original_revision(reader, db, sid, session, **kwargs)

        monkeypatch.setattr(ts.TranscriptStore, '_revision', revision_after_commit)
        overlapping = client.get(url)
        assert overlapping.status_code == 200, overlapping.text
        reads.assert_closed(3, budget)
        assert overlapping.content == baseline.content
        assert barrier_visits == ['snapshot_already_established'] and committed.is_set()

        new_counter, new_revision = _revision_state(data)
        assert new_counter == old_counter + 1 and new_revision != old_revision
        current = client.get(url)
        assert current.status_code == 200, current.text
        reads.assert_closed(4, budget)
        page = current.json()
        assert page['revision'] == new_revision
        assert c.parse_cursor(page['next_cursor'], SID) == {
            'policy': c.POLICY_HASH, 'session': SID, 'block': 1, 'revision': new_revision}
        primary = next(row for row in page['reading'] if row['canonical_asr']['id'] == 1)
        assert primary['text'] == primary['raw_text'] == new_text
        expected_asr = dict(old_primary['canonical_asr'], text=new_text, raw_text=new_text)
        assert primary['canonical_asr'] == expected_asr
        assert primary['source_ref'] == 'p.1.' + c.digest(expected_asr)
        assert primary['source_ref'] != old_primary['source_ref']
        assert page['reading'][1:] == old_page['reading'][1:]

        stale = client.get(url, params={'cursor': old_cursor})
        assert stale.status_code == 409 and stale.json() == {'detail': 'transcript_revision_changed'}
        reads.assert_closed(5, budget)
    finally:
        abort.set()
        commit_requested.set()
        writer.join(3)
        assert not writer.is_alive(), 'Owned writer did not exit during bounded cleanup'
        assert writer_closed.is_set(), 'Owned writer connection did not close'
        assert not writer_errors, repr(writer_errors)
        assert all(getattr(connection, 'successful_closes', 0) == 1
                   for connection in reads.opened)
        assert budget._lease_count == budget._lease_bytes == 0
    assert (ts.READ_SECONDS, ts.MAX_VM_STEPS) == (.75, 250000)


def test_exclusive_storage_refusal_releases_resources_and_recovers(data, api, monkeypatch):
    assert (ts.READ_SECONDS, ts.MAX_VM_STEPS) == (.75, 250000)
    client, budget, _ = api
    original_connect = sqlite3.connect
    reads = _ReadConnections(monkeypatch, data.path)
    url = f'/sessions/{SID}/transcript'
    baseline = client.get(url)
    assert baseline.status_code == 200, baseline.text
    reads.assert_closed(1, budget)

    # This per-test mode change deliberately creates a storage refusal that a
    # normal WAL writer does not imply. No shared or production DB is touched.
    writer = sqlite3.connect(data.path, timeout=0)
    writer_closed = False
    try:
        assert writer.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
        writer.execute('BEGIN EXCLUSIVE')
        writer.execute("UPDATE segments SET text='uncommitted exclusive change' WHERE id=1")
        assert writer.in_transaction
        probe = original_connect(reads.uri, uri=True, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError) as caught:
                probe.execute('SELECT deleted FROM sessions WHERE id=?', (SID,)).fetchone()
            assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
            assert caught.value.sqlite_errorname == 'SQLITE_BUSY'
        finally:
            probe.close()
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            probe.execute('SELECT 1')

        refused = client.get(url)
        assert refused.status_code == 503
        assert refused.json() == {'detail': 'transcript_storage_unavailable'}
        reads.assert_closed(2, budget)
        assert writer.in_transaction
    finally:
        try:
            if writer.in_transaction:
                writer.rollback()
        finally:
            writer.close()
            writer_closed = True
        assert all(getattr(connection, 'successful_closes', 0) == 1
                   for connection in reads.opened)
        assert budget._lease_count == budget._lease_bytes == 0
    assert writer_closed
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        writer.execute('SELECT 1')
    recovered = client.get(url)
    assert recovered.status_code == 200, recovered.text
    reads.assert_closed(3, budget)
    assert recovered.content == baseline.content
    assert (ts.READ_SECONDS, ts.MAX_VM_STEPS) == (.75, 250000)
