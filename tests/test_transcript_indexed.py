"""Indexed discovery, separately paged audit, and transactional invalidation.

Fresh synthetic owned databases only. No models, server, historical database
adoption, admission-cap increase, or alternative test runner.
"""
import hashlib
import json
import sqlite3

import pytest

from audio_backend import transcript_contract as c, transcript_revision as revision
from audio_backend import transcript_store as ts, moss_automatic as auto, moss_result_store as history
from audio_backend.native_owned_support_cache import ReadError
from test_transcript_http import SID, data, api


def duplicate_queued(data, count):
    """Distinct valid logical bindings, without model/result payloads.

    Direct SQL is fixture construction, not permission to bypass production
    admission. The stress case stays within the unchanged 1,024/session cap.
    """
    seed = data.job(1, 0, 1, state='queued')
    with data.db() as db:
        original = dict(db.execute(f'SELECT * FROM {auto.TABLE}').fetchone())
        original.pop('seq')
        columns = ','.join(original)
        values = ','.join('?' for _ in original)
        for index in range(1, count):
            row = dict(original)
            binding = json.loads(c.encoded(seed['binding']))
            shift = index * 16000
            for part in binding['parts']:
                part['logical_start'] += shift
                part['logical_end'] += shift
            binding['support_frames'] = [x + shift for x in binding['support_frames']]
            binding['core_frames'] = [[x + shift for x in pair] for pair in binding['core_frames']]
            raw = c.encoded(binding)
            row.update(candidate_key=hashlib.sha256(raw).hexdigest(), binding=raw,
                       job_id=f'{index + 1:032x}', charge_bytes=16384+len(raw))
            db.execute(f'INSERT INTO {auto.TABLE}({columns}) VALUES({values})', tuple(row.values()))


def counter(data, sid=SID):
    with data.db() as db:
        return db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (sid,)).fetchone()[0]


def test_far_seek_ignores_thousand_unrelated_bindings_and_uses_both_indexes(data, monkeypatch):
    duplicate_queued(data, 1000)
    data.chunk('far', 60000, 60010.5)
    wanted = data.job(2001, 60000, 60010.5, cid='far')
    statements, connect = [], sqlite3.connect
    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(ts.sqlite3, 'connect', traced)
    value = data.page(block=1000)
    assert value['selected_runs'][0]['candidate_keys'] == [wanted['key']]
    assert sum(sql.startswith('SELECT binding ') for sql in statements) == 1
    assert not any('auto_session_seq' in sql for sql in statements)
    assert any('INDEXED BY auto_cid1' in sql for sql in statements)
    assert any('INDEXED BY auto_cid2' in sql for sql in statements)
    assert auto.SESSION_ROWS == 1024 and auto.MAX_ROWS == 4096


def test_second_alias_only_seam_nonzero_origin_is_discovered(data):
    data.chunk('left', 43.5, 54)
    data.chunk('right', 54, 75)
    job = data.job(1, 50, 70, kind='core_seam', source_chunks=('left', 'right'), logical_origin=123456)
    with data.reader.snapshot(SID) as (db, _, _):
        chunks = data.reader._chunks(db, SID, 1)
        assert [row['id'] for row in chunks] == ['right']
    value = data.page(block=1)
    group = value['alternatives'][0]
    assert group['candidate_keys'] == [job['key']]
    assert group['reason'] == 'boundary_unresolved'
    assert group['start_frame'] == 50*c.RATE and group['end_frame'] == 70*c.RATE
    assert all(item['reference_only'] for item in group['candidate_reading'])


def test_alias_dedup_precedes_binding_and_result_capacity(data, monkeypatch):
    data.chunk('left', 43.5, 54)
    data.chunk('right', 54, 75)
    job = data.job(1, 50, 60, kind='core_seam', source_chunks=('left', 'right'))
    monkeypatch.setattr(c, 'MAX_BINDINGS', 1)
    binding, calls = data.reader._binding, []
    def measured(db, sid, row):
        calls.append(row['candidate_key'])
        return binding(db, sid, row)
    monkeypatch.setattr(data.reader, '_binding', measured)
    value = data.page()
    assert calls == [job['key']]
    assert value['selected_runs'][0]['candidate_keys'] == [job['key']]
    assert not data.page(block=1)['alternatives']  # Support ends exactly at 60.


def test_half_open_support_start_on_block_boundary(data):
    data.chunk('edge', 60, 70.5)
    job = data.job(1, 60, 70, cid='edge')
    assert not data.page()['selected_runs']
    assert data.page(block=1)['selected_runs'][0]['candidate_keys'] == [job['key']]


@pytest.mark.parametrize('mutation', ['missing_chunk', 'moved_chunk', 'missing_work', 'moved_batch'])
def test_unplaced_history_is_discoverable_only_by_separate_audit_when_needed(data, mutation):
    job = data.job(1, 0, 10.5)
    with data.db() as db:
        if mutation == 'missing_chunk': db.execute("DELETE FROM chunks WHERE id='chunk'")
        elif mutation == 'moved_chunk': db.execute("UPDATE chunks SET session_id='other' WHERE id='chunk'")
        elif mutation == 'missing_work': db.execute("DELETE FROM recording_work WHERE chunk_id='chunk'")
        else: db.execute("UPDATE recording_batches SET session_id='other' WHERE id='batch'")
    page = data.page()
    assert page['selected_runs'] == []
    assert page['unplaced_history_check'] == 'not_performed_by_time_page'
    if mutation in ('missing_chunk', 'moved_chunk'): assert page['notices'] == []
    audit = data.audit()
    assert audit['unplaced_bindings'][0]['candidate_key'] == job['key']
    assert audit['unplaced_bindings'][0]['reason'] == 'automatic_source_unplaced'
    assert audit['examined'] == 1 and audit['has_more'] is False


def test_empty_audit_page_continues_after_last_examined_row(data):
    duplicate_queued(data, c.AUDIT_BINDINGS + 2)
    with data.db() as db:
        last = db.execute(f'SELECT MAX(seq) FROM {auto.TABLE}').fetchone()[0]
        db.execute(f'DELETE FROM {auto.TABLE} WHERE seq=2')  # Keyset gaps are not row counts.
        db.execute(f'UPDATE {auto.TABLE} SET binding=? WHERE seq=?', (b'{}', last))
    first = data.audit()
    assert first['unplaced_bindings'] == [] and first['examined'] == c.AUDIT_BINDINGS
    assert first['last_examined_seq'] == last - 1
    assert first['has_more'] and first['next_cursor']
    assert data.audit() == first
    final = data.audit(cursor=first['next_cursor'])
    assert final['examined'] == 1 and final['last_examined_seq'] == last
    assert len(final['unplaced_bindings']) == 1 and final['next_cursor'] is None
    assert final['revision'] == first['revision']


def test_audit_is_indexed_length_first_and_never_loads_results(data, monkeypatch):
    data.job(1, 0, 10.5)
    statements, connect = [], sqlite3.connect
    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(ts.sqlite3, 'connect', traced)
    data.audit()
    assert any('INDEXED BY auto_session_seq' in sql and 'a.seq>0' in sql for sql in statements)
    length = next(i for i, sql in enumerate(statements) if 'length(a.binding)' in sql)
    payload = next(i for i, sql in enumerate(statements) if sql.startswith('SELECT binding'))
    assert length < payload
    assert not any(sql.startswith('SELECT result_json') for sql in statements)


def test_audit_cursor_scope_http_query_and_shared_response_gate(data, api):
    duplicate_queued(data, c.AUDIT_BINDINGS + 1)
    first = data.audit()
    page = data.page(block=1)
    with pytest.raises(ReadError, match='invalid_transcript_cursor'):
        data.page(cursor=first['next_cursor'])
    with pytest.raises(ReadError, match='invalid_transcript_cursor'):
        data.audit(cursor=c.cursor(SID, 1, page['revision']))
    client, budget, _ = api
    path = f'/sessions/{SID}/transcript/binding-audit'
    response = client.get(path)
    assert response.status_code == 200 and response.json() == first
    assert response.headers['cache-control'] == 'private, no-store'
    for query in ('block=0', 'after=1', 'cursor=x&cursor=y'):
        assert client.get(path + '?' + query).status_code == 409
    with data.db() as db: db.execute("UPDATE recording_work SET receipt_id=?", ('0'*64,))
    assert client.get(path + '?cursor=' + first['next_cursor']).status_code == 409
    assert budget._lease_count == budget._lease_bytes == 0


def test_earlier_reserved_history_commit_changes_revision_without_archive_max(data):
    early = data.job(1, 0, 10.5, state='accepted')
    data.job(2, 10.5, 21)
    before = data.page()
    with data.db() as db: high = db.execute(f'SELECT MAX(archive_seq) FROM {history.TABLE}').fetchone()[0]
    # The history trigger alone must suffice even before any intent update.
    with data.db() as db:
        db.execute(f'''UPDATE {history.TABLE} SET state='committed',result_json=?,result_sha256=?,
            committed_at=1.0,charge_bytes=? WHERE job_id=?''',
            (early['result'], early['sha'], len(early['result'])+len(early['metadata'])+16384, early['jid']))
        assert db.execute(f'SELECT MAX(archive_seq) FROM {history.TABLE}').fetchone()[0] == high
    with pytest.raises(ReadError, match='transcript_revision_changed'):
        data.page(cursor=before['next_cursor'])


@pytest.mark.parametrize('mutation', ['cancelled', 'digest', 'duration', 'timeline', 'receipt', 'frames',
                                     'capture_start', 'origin', 'auto_binding', 'history_metadata'])
def test_relevant_source_and_history_mutations_invalidate_cursor(data, mutation):
    job = data.job(1, 0, 10.5)
    before = data.page()
    with data.db() as db:
        if mutation == 'cancelled': db.execute("UPDATE chunks SET status='cancelled' WHERE id='chunk'")
        elif mutation == 'digest': db.execute("UPDATE chunks SET digest=? WHERE id='chunk'", ('0'*64,))
        elif mutation == 'duration': db.execute("UPDATE chunks SET duration=20 WHERE id='chunk'")
        elif mutation == 'timeline': db.execute("UPDATE chunks SET timeline_start=1 WHERE id='chunk'")
        elif mutation == 'receipt': db.execute("UPDATE recording_work SET receipt_id=?", ('0'*64,))
        elif mutation == 'frames': db.execute('UPDATE recording_work SET frames=frames-1')
        elif mutation == 'capture_start': db.execute('UPDATE recording_work SET capture_start_frame=capture_start_frame+1')
        elif mutation == 'origin': db.execute('UPDATE recording_batches SET capture_origin_frame=capture_origin_frame+1')
        elif mutation == 'auto_binding': db.execute(f'UPDATE {auto.TABLE} SET binding=?', (b'{}',))
        else: db.execute(f'''UPDATE {history.TABLE} SET metadata_json=?,charge_bytes=length(result_json)+16386''', (b'{}',))
    with pytest.raises(ReadError, match='transcript_revision_changed'):
        data.page(cursor=before['next_cursor'])


def test_moved_source_later_writes_invalidate_retained_auto_owner_and_new_owner(data):
    data.job(1, 0, 10.5)
    with data.db() as db: db.execute("UPDATE chunks SET session_id='other' WHERE id='chunk'")
    old, new = counter(data), counter(data, 'other')
    with data.db() as db: db.execute("UPDATE chunks SET status='cancelled' WHERE id='chunk'")
    assert counter(data) > old and counter(data, 'other') > new
    old, new = counter(data), counter(data, 'other')
    with data.db() as db: db.execute('UPDATE recording_work SET capture_start_frame=capture_start_frame+1')
    assert counter(data) > old and counter(data, 'other') > new
    old, new = counter(data), counter(data, 'other')
    with data.db() as db: db.execute('UPDATE recording_batches SET capture_origin_frame=capture_origin_frame+1')
    assert counter(data) > old and counter(data, 'other') > new


def test_both_alias_owner_indexes_cover_retained_sessions_after_move(data):
    data.chunk('left', 43.5, 54)
    data.chunk('right', 54, 75)
    data.job(1, 50, 70, kind='core_seam', source_chunks=('left', 'right'))
    with data.db() as db: db.execute("UPDATE chunks SET session_id='other' WHERE id IN ('left','right')")
    for cid in ('left', 'right'):
        old = counter(data)
        with data.db() as db: db.execute('UPDATE chunks SET digest=? WHERE id=?', ('0'*64, cid))
        assert counter(data) > old


def test_history_reassignment_still_invalidates_bound_automatic_owner(data):
    data.job(1, 0, 10.5)
    with data.db() as db: db.execute(f"UPDATE {history.TABLE} SET session_id='other'")
    old, new = counter(data), counter(data, 'other')
    with data.db() as db: db.execute(f"UPDATE {history.TABLE} SET result_sha256=?", ('0'*64,))
    assert counter(data) > old and counter(data, 'other') > new


def test_irrelevant_automatic_wall_clock_and_noop_writes_keep_revision(data):
    data.job(1, 0, 10.5)
    before, old = data.page(), counter(data)
    with data.db() as db:
        db.execute(f'UPDATE {auto.TABLE} SET updated=updated+1')
        db.execute(f'UPDATE {auto.TABLE} SET reason=reason')
        db.execute(f'UPDATE {history.TABLE} SET committed_at=committed_at+1')
    assert counter(data) == old and data.page() == before


def test_transaction_rollback_and_overflow_never_publish_partial_invalidation(data):
    old = counter(data)
    with data.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE chunks SET digest=? WHERE id='chunk'", ('0'*64,))
        assert db.execute('SELECT transcript_revision FROM sessions WHERE id=?', (SID,)).fetchone()[0] > old
        db.rollback()
    assert counter(data) == old
    with data.db() as db:
        db.execute('UPDATE sessions SET transcript_revision=? WHERE id=?', ((1<<63)-1, SID))
    with data.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE sessions SET timeline_end=130 WHERE id=?', (SID,))
        with pytest.raises(sqlite3.IntegrityError, match='Transcript revision exhausted'):
            db.execute("UPDATE chunks SET digest=? WHERE id='chunk'", ('0'*64,))
        assert not db.in_transaction
    with data.db() as db:
        assert db.execute('SELECT timeline_end FROM sessions WHERE id=?', (SID,)).fetchone()[0] == 120
        assert db.execute("SELECT digest FROM chunks WHERE id='chunk'").fetchone()[0] == 'd'*64


def test_revision_schema_repeat_is_exact_and_preserves_existing_feed_trigger(data):
    with data.db() as db:
        db.execute('BEGIN IMMEDIATE')
        before = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        revision.init_schema(db)
        after = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        assert before == after
        assert len(revision.SCHEMA) == 18
        db.execute('DROP TRIGGER ' + next(iter(revision.SCHEMA)))
        with pytest.raises(ValueError, match='Incomplete transcript revision schema'):
            revision.init_schema(db)
        db.rollback()


def test_single_long_legacy_chunk_scan_remains_vm_bounded(data):
    with data.db() as db:
        db.execute('DELETE FROM segments')
        db.execute("UPDATE chunks SET duration=100000 WHERE id='chunk'")
        db.executemany('''INSERT INTO segments(id,chunk_id,start,end,text,raw_text,asr_backend,
            timestamp_kind,confidence,speaker_local_id,speaker_window_start,speaker_id)
            VALUES(?,'chunk',?,?,'old','old','synthetic','native',.8,'S1',0.,NULL)''',
            ((index+1, index/10, index/10+.05) for index in range(100000)))
    with pytest.raises(ReadError, match='unresolved_capacity'):
        data.page(block=1000)


def test_final_source_support_extends_lagging_session_timeline_with_one_tail_seek(data):
    data.chunk('tail', 120, 130.5)
    job = data.job(1, 120, 130.5, cid='tail')
    first = data.page()
    second = data.page(cursor=first['next_cursor'])
    assert second['block'] == 1 and second['next_cursor'] is not None
    final = data.page(cursor=second['next_cursor'])
    assert final['block'] == 2 and final['next_cursor'] is None
    assert final['selected_runs'][0]['candidate_keys'] == [job['key']]
    assert first['revision'] == second['revision'] == final['revision']


def test_auto_old_new_session_owner_changes_and_deletion_invalidate_both(data):
    data.job(1, 0, 10.5)
    old, other = counter(data), counter(data, 'other')
    with data.db() as db: db.execute(f"UPDATE {auto.TABLE} SET sid='other'")
    assert counter(data) > old and counter(data, 'other') > other
    before = counter(data, 'other')
    with data.db() as db: db.execute(f'DELETE FROM {auto.TABLE}')
    assert counter(data, 'other') > before


@pytest.mark.parametrize('table,key', [('recording_work','chunk_id'), ('recording_batches','id'), ('chunks','id')])
def test_mapping_deletion_and_reinsertion_invalidate_retained_owner(data, table, key):
    data.job(1, 0, 10.5)
    with data.db() as db:
        row = dict(db.execute(f'SELECT * FROM {table} LIMIT 1').fetchone())
    old = counter(data)
    with data.db() as db: db.execute(f'DELETE FROM {table} WHERE {key}=?', (row[key],))
    assert counter(data) > old
    old = counter(data)
    with data.db() as db:
        db.execute(f'INSERT INTO {table}(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    assert counter(data) > old


def test_non_grid_legacy_tail_uses_primary_envelope_without_new_duration_limit(data):
    with data.db() as db:
        db.execute("UPDATE chunks SET duration=? WHERE id='chunk'", (120.000001,))
    first = data.page()
    second = data.page(cursor=first['next_cursor'])
    assert second['next_cursor'] is not None
    assert data.page(cursor=second['next_cursor'])['block'] == 2


def test_primary_move_invalidates_old_and_new_session_cursors(data):
    data.chunk('other-chunk', 0, 21, sid='other')
    old = data.page()
    other = json.loads(data.reader.page('other'))
    with data.db() as db:
        db.execute("UPDATE segments SET chunk_id='other-chunk' WHERE id=1")
    with pytest.raises(ReadError, match='transcript_revision_changed'):
        data.page(cursor=old['next_cursor'])
    with pytest.raises(ReadError, match='transcript_revision_changed'):
        data.reader.page('other', cursor=other['next_cursor'])
    assert len(data.page()['baseline_reading']) == 2
    assert len(json.loads(data.reader.page('other'))['baseline_reading']) == 1


def test_missing_old_primary_owner_is_covered_by_prior_chunk_deletion(data):
    data.chunk('other-chunk', 0, 21, sid='other')
    before = data.page()
    with data.db() as db: db.execute("DELETE FROM chunks WHERE id='chunk'")
    with pytest.raises(ReadError, match='transcript_revision_changed'):
        data.page(cursor=before['next_cursor'])
    other = json.loads(data.reader.page('other'))
    with data.db() as db: db.execute("UPDATE segments SET chunk_id='other-chunk' WHERE id=1")
    with pytest.raises(ReadError, match='transcript_revision_changed'):
        data.reader.page('other', cursor=other['next_cursor'])


def test_primary_person_assignment_does_not_increment_canonical_counter(data):
    before = counter(data)
    with data.db() as db: db.execute("UPDATE segments SET speaker_id='other-person' WHERE id=1")
    assert counter(data) == before
    with data.db() as db: db.execute("UPDATE segments SET raw_text='new canonical text' WHERE id=1")
    assert counter(data) > before
