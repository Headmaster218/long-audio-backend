"""Small real primary-only pipeline, synthetic PCM/native/ASR/probe edges.

Only sealed-page recording ingestion creates the retirement marker. A bounded
metadata-only completed gap tail can extend a replay prefix; it never supplies
audio, receipt, inference, or deletion evidence. No comparison/model subprocess
or long-recording audio is used. Execution requires the separate frozen gate.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import wave

import pytest

from audio_backend import native_owned_cell_store as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_overlap as overlap
from audio_backend import recording_primary_probe as probe
from audio_backend import recording_primary_provenance as pp
from audio_backend import recording_speaker_history as history
from audio_backend.native_owned_cells import RATE
from audio_backend.native_owned_support_cache import ReadError
from test_native_owned_compact_gc import file_hash
from test_primary_cache_retirement import POLICY, Q, rows_hash, usage
from test_primary_only_configuration import no_external_execution
import test_primary_only_pipeline as pipeline
from test_primary_only_pipeline import primary_app_factory
from test_native_owned_support import synthetic_state
from test_recording_speaker_history_integration import (
    _wav, _cohort, _complete_pending_probe, _primary_rows,
)


CAPTURE = 'synthetic-primary-cache'
STABLE_TABLES = (pp.TABLE, pp.USAGE, 'segments', 'speaker_turns', 'events',
    'recording_batches', 'recording_pages', 'recording_sources', 'recording_work',
    'recording_ranges', 'stream_chunks', 'stream_time_index',
    history.CERTIFICATES, history.OBJECTS, history.REFS, history.DEPENDENCIES,
    history.SOURCES, history.ATTEMPTS)


@pytest.fixture
def retirement_app_factory(primary_app_factory, monkeypatch):
    original = pipeline.primary_options
    def options(root):
        return dict(original(root), native_owned_compact_gc_enabled=True,
            native_owned_receipt_gc_enabled=True, recording_disk_reserve_bytes=0)
    monkeypatch.setattr(pipeline, 'primary_options', options)
    monkeypatch.setattr(cells.CellStore, '_schedule_compact_gc', lambda self: False)
    monkeypatch.setattr(cells.CellStore, '_schedule_receipt_gc', lambda self: False)
    original_request = pipeline.FakeNative.request
    def stable_push(native, action, stop, **kwargs):
        if action != 'push':return original_request(native, action, stop, **kwargs)
        # The existing observed_window/RollingWitness synthetic streaming
        # contract exposes only supplied PCM as stable, without claiming EOF.
        # Do not call the inherited push: it notifies a zero-stability state.
        native.calls.append(action)
        with wave.open(kwargs['wav'], 'rb') as source:
            supplied = source.getnframes()
            assert supplied > 0
        native.frames += supplied
        state = synthetic_state(native.epoch, native.frames, stable_frames=native.frames)
        assert state['finished'] is False
        assert state['input_seconds'] == native.frames / RATE
        assert 0 <= state['stable_seconds'] <= state['processed_seconds'] <= native.frames / RATE
        observer = getattr(native, 'response_observer', None)
        assert callable(observer)
        observer(state)  # Exactly one push notification.
        return state
    monkeypatch.setattr(pipeline.FakeNative, 'request', stable_push)
    @contextmanager
    def opened(**kwargs):
        with primary_app_factory(**kwargs) as h:
            if h.jobs is not None:
                # No comparison process is needed to test preserved rival rows.
                monkeypatch.setattr(h.jobs, 'maintain_history_comparison', lambda: False)
            yield h
    return opened


def batch(h):
    with h.store.db() as db:
        return dict(db.execute('SELECT * FROM recording_batches WHERE session_id=?', (h.sid,)).fetchone())


def append_page(h, *, count=1, number=1, gap=False, finish=False):
    recording = h.worker.recordings
    page = recording.create_page(h.sid, CAPTURE, number, 900007 if number == 1 else None)
    current = batch(h); start = current['capture_end_frame']
    path = recording.source_path(current, page)
    path.write_bytes(_wav(count * Q))
    try:recording.accept_page(h.sid, CAPTURE, number, 'source-' + str(number), path, start)
    finally:path.unlink(missing_ok=True)
    page = recording.page_status(h.sid, CAPTURE, number)
    page = recording.seal_page(h.sid, CAPTURE, number, 'seal-' + str(number),
        page['revision'], start + count * Q, eos=finish, cells=None if gap else h.capture)
    deadline = time.monotonic() + 4 + count * 2
    while not page['released']:
        assert time.monotonic() < deadline, page
        flight = recording._flight
        if flight is not None:
            assert flight['operation'].slot_released.wait(3.)
        recording.advance(None if gap else h.capture, force=True)
        page = recording.page_status(h.sid, CAPTURE, number)
        assert page['state'] not in ('unresolved', 'interrupted'), page
    assert len(page['work_chunks']) == count
    return tuple(row['chunk_id'] for row in page['work_chunks'])


def complete(h, cids, *, fail_at=None):
    for index, cid in enumerate(cids):
        before = h.store.chunk(cid)
        assert before['status'] == 'queued' and before['next_offset'] == 0.
        assert before['duration'] == Q / RATE
        assert _cohort(h) == cid
        after = h.store.chunk(cid)
        assert before['next_offset'] < after['next_offset'] == after['duration']
        assert after['status'] == 'completed'
        if h.jobs._primary_pending is not None:
            if index == fail_at:assert h.jobs._stop_primary()
            else:_complete_pending_probe(h)
        assert h.jobs._primary_pending is None


def gap_tail(h, count=28):
    """Finite canonical completed metadata; never creates a source receipt."""
    current = batch(h); bid = current['id']; origin = current['capture_origin_frame']
    with h.store.db() as db:
        start_position = db.execute('SELECT COALESCE(MAX(position)+1,0) FROM recording_work WHERE batch_id=?', (bid,)).fetchone()[0]
        number = db.execute('SELECT MAX(page_number)+1 FROM recording_pages WHERE batch_id=?', (bid,)).fetchone()[0]
        page = bid + '-SYNTHETIC-gap-tail'; start = current['capture_end_frame']; end = start + count * Q
        db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,start_frame,end_frame,created)
            VALUES(?,?,?,'published',1,?,?,0)''', (page, bid, number, start, end))
        for offset in range(count):
            position = start_position + offset; cid = f'z-SYNTHETIC-gap-{offset:03d}'
            path = h.store.settings.data_dir / 'uploads' / (cid + '.audio')
            db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
                timeline_start,status,next_offset,created,metadata) VALUES(?,?,?,?,?,10.5,?,'completed',10.5,?,'{}')''',
                (cid, h.sid, cid, 'c' * 64, str(path), position * Q / RATE, position))
            db.execute('''INSERT INTO stream_chunks(chunk_id,session_id,stream_id,position,
                stream_final,upload_stream_final) VALUES(?,?,?,?,0,0)''',
                (cid, h.sid, 'recording.' + bid, position))
            db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,capture_start_frame,
                native_receipt_status,receipt_state,page_id) VALUES(?,?,?,?,?,?,'source_receipt_capacity','gap',?)''',
                (cid, bid, position, Q, 44 + 2 * Q, origin + position * Q, page))
        db.execute('UPDATE recording_batches SET capture_end_frame=? WHERE id=?', (end, bid))


def collect(h, kind, turns=8):
    results = []
    for _ in range(turns):
        with h.owner._lock:
            assert h.owner._slot is None
            op = cells.Operation(kind + '_gc', (), h.owner._epoch, h.owner._wake)
            h.owner._slot = op; h.owner._wake.set()
        assert op.slot_released.wait(3.)
        result = op.result; assert isinstance(result, dict), result
        assert result.get('scanned', 0) <= 8 and result.get('retired', 0) <= 8
        assert result.get('prefix_rows', 0) <= 28
        results.append(result)
    return results


def snapshot(h):
    with h.store.db() as db:rows = rows_hash(db, STABLE_TABLES)
    files = {path.name: file_hash(path) for path in (h.store.settings.data_dir / 'uploads').iterdir() if path.is_file()}
    return rows, files


def saved_packet(h, cid):
    with h.store.db() as db:
        db.execute('BEGIN')
        return pp.read_exact(db, cid=cid, start=0, end=Q)


def test_A3_production_initial_all_gap_release_then_first_durable_binding(retirement_app_factory):
    with retirement_app_factory() as h:
        first, = append_page(h, gap=True)
        assert batch(h)['primary_cache_policy'] == POLICY
        assert batch(h)['primary_cache_store_key'] is None
        complete(h, (first,))
        second, = append_page(h, number=2)
        assert batch(h)['primary_cache_store_key'] == h.owner.retention_store_key
        complete(h, (second,))
        # Keep a newer actual compact as the compact frontier.
        third = append_page(h, number=3)
        complete(h, third)
        gap_tail(h)
        before = snapshot(h)
        collect(h, 'compact'); results = collect(h, 'receipt', 16)
        assert any(r['retired'] for r in results), results
        assert h.owner.receipt(first) is None and h.owner.receipt(second) is None
        assert snapshot(h) == before


@pytest.mark.parametrize('mode', ['primary', 'combined', 'passive'])
def test_A4_restart_mode_does_not_adopt_marked_work_and_legacy_queue_progresses(retirement_app_factory, mode):
    with retirement_app_factory() as h:
        cid, = append_page(h)
        sid = h.sid; marked = batch(h); charged = usage(h.store)
        other = h.store.create_session('SYNTHETIC unrelated legacy queue')['id']
        legacy, _ = h.store.add_chunk(other, 'legacy', 'c' * 64,
            h.store.settings.data_dir / 'uploads' / 'legacy.audio', 1.)
    with retirement_app_factory(mode=mode, sid=sid) as h:
        assert batch(h)['primary_cache_policy'] == POLICY
        assert batch(h)['primary_cache_store_key'] == marked['primary_cache_store_key']
        assert usage(h.store) == charged
        claimed = h.store.claim()
        assert claimed is not None and claimed['id'] == (cid if mode == 'primary' else legacy)
        if mode != 'primary':
            assert h.store.chunk(cid)['status'] == 'queued'
            assert h.automatic.before_window(h.store.chunk(cid)) is False
            with h.store.db() as db:db.execute("UPDATE chunks SET status='running' WHERE id=?", (cid,))
            result = {'segments': [], 'events': [], 'speaker_turns': [], 'metadata': {}}
            with pytest.raises(ValueError, match='Primary cache coordinator'):
                h.store.commit_window(h.store.chunk(cid), 0., 10.5, result)
            assert h.store.chunk(cid)['next_offset'] == 0.
        with h.store.db() as db:db.execute("UPDATE chunks SET status='paused' WHERE status='running'")


@pytest.mark.parametrize('refusal', ['marker_quota', 'provenance', 'silent', 'unknown_only'])
def test_A5_B3_optional_refusals_preserve_primary_text_and_offset(retirement_app_factory, monkeypatch, refusal):
    with retirement_app_factory() as h:
        if refusal == 'marker_quota':h.store.settings.recording_primary_provenance_recording_bytes = 1
        cids = append_page(h, count=2)
        if refusal == 'provenance':h.store.settings.recording_primary_provenance_recording_bytes = 512
        if refusal in ('silent', 'unknown_only'):
            from test_native_owned_support_api import FakeAnalyzer
            original = FakeAnalyzer.analyze
            def analyze(self, *args, **kwargs):
                value = original(self, *args, **kwargs)
                if refusal == 'silent':value['segments'] = []
                else:
                    for row in value['segments']:
                        row['speaker_local_id'] = None; row['source_track_id'] = None
                return value
            monkeypatch.setattr(FakeAnalyzer, 'analyze', analyze)
        complete(h, cids)
        assert all(h.store.chunk(cid)['status'] == 'completed' and h.store.chunk(cid)['next_offset'] == 10.5 for cid in cids)
        assert len(_primary_rows(h)) == (0 if refusal == 'silent' else 2)
        with h.store.db() as db:assert db.execute(f'SELECT COUNT(*) FROM {pp.TABLE}').fetchone()[0] == 0
        assert not h.probes
        if refusal == 'marker_quota':
            assert batch(h)['primary_cache_policy'] is None
            h.store.settings.recording_primary_provenance_recording_bytes = 128 * 1024**2
            h.worker.recordings.create_page(h.sid, CAPTURE, 2)
            assert batch(h)['primary_cache_policy'] is None
        else:
            assert usage(h.store)[0] == ('', 0, 512, 512)
            gap_tail(h)
            before = snapshot(h)
            assert any(r['retired'] for r in collect(h, 'compact'))
            assert any(r['retired'] for r in collect(h, 'receipt'))
            assert snapshot(h) == before


@pytest.mark.parametrize('kind', ['compact', 'receipt'])
def test_B6_wholly_ineligible_saturation_keeps_real_primary_progress(retirement_app_factory, kind):
    with retirement_app_factory() as h:
        if kind == 'receipt':
            rows = []
            for i in range(cells.MAX_RECEIPTS):
                cid = f'z-cold-{i:05d}'
                receipt = cells.Receipt(h.sid, cid, 'c' * 64, 16, 44, 76, (1, i + 1, 76, 1, 1), 0., None)
                rows.append((cid, h.sid, receipt.digest, receipt.encoded))
            with sqlite3.connect(h.owner.path, timeout=0) as db:db.executemany('INSERT INTO sources VALUES(?,?,?,?)', rows)
        cid, = append_page(h)
        if kind == 'compact':
            with h.store.db() as db:
                db.executemany('''INSERT INTO native_core_commits(slot,sid,cid,scope_key,logical_end,body,pending,state,reason)
                    VALUES(?,?,?,?,?, ?,NULL,'none','SYNTHETIC ineligible occupancy')''',
                    [(f'{i:064x}', h.sid, cid, 'synthetic-cold', i, b'{}') for i in range(core.MAX_COMPACTS)])
                before = rows_hash(db, ('native_core_commits',))
        else:
            with sqlite3.connect(h.owner.path) as db:before = rows_hash(db, ('sources',))
        complete(h, (cid,))
        assert h.store.chunk(cid)['status'] == 'completed' and h.store.chunk(cid)['next_offset'] == 10.5
        assert [r['text'] for r in _primary_rows(h)] == ['synthetic']
        assert not h.probes
        collect(h, kind, 2)
        if kind == 'compact':
            with h.store.db() as db:assert rows_hash(db, ('native_core_commits',)) == before
        else:
            with sqlite3.connect(h.owner.path) as db:assert rows_hash(db, ('sources',)) == before
        assert core.MAX_COMPACTS == 2056 and cells.MAX_RECEIPTS == 4096


def test_C1_packets_history_refs_and_audio_survive_both_collections_and_restart(retirement_app_factory):
    with retirement_app_factory() as h:
        cids = append_page(h, count=3); complete(h, cids); gap_tail(h)
        before = snapshot(h); saved = saved_packet(h, cids[0]); assert saved is not None
        assert any(r['retired'] for r in collect(h, 'compact'))
        assert any(r['retired'] for r in collect(h, 'receipt'))
        assert h.owner.receipt(cids[0]) is None and snapshot(h) == before
        assert saved_packet(h, cids[0]) == saved
        sid = h.sid
    with retirement_app_factory(sid=sid) as reopened:
        assert saved_packet(reopened, cids[0]) == saved
        assert snapshot(reopened) == before
        assert reopened.owner.receipt(cids[0]) is None
        assert reopened.jobs._primary_pending is None and not reopened.probes


def test_C2_unknown_rival_certificates_survive_collection_and_reads_abstain(retirement_app_factory):
    with retirement_app_factory() as h:
        # 60-second cadence makes the seventh 10.5-second core genuinely due.
        # Each fresh WAV remains below the existing 50-second fixture bound.
        first = append_page(h, count=3); complete(h, first)
        second = append_page(h, count=3, number=2); complete(h, second)
        last = append_page(h, count=2, number=3); complete(h, last, fail_at=0)
        cids = first + second + last; gap_tail(h)
        with h.store.db() as db:
            assert db.execute(f"SELECT COUNT(*) FROM {history.CERTIFICATES} WHERE evidence_kind='candidate_missing'").fetchone()[0] >= 1
        before = snapshot(h)
        assert any(r['retired'] for r in collect(h, 'compact'))
        assert any(r['retired'] for r in collect(h, 'receipt'))
        response = h.client.get(f'/sessions/{h.sid}/transcript'); assert response.status_code == 200
        for row in response.json()['baseline_reading']:
            link = row.get('recording_speaker_link')
            assert link is None or link.get('provisional_speaker_id') is None
        assert snapshot(h) == before


@pytest.mark.parametrize('mutation', ['cancellation', 'revision', 'source_replacement', 'digest', 'generation'])
def test_C3_retired_caches_do_not_turn_revoked_evidence_current(retirement_app_factory, mutation):
    with retirement_app_factory() as h:
        cids = append_page(h, count=3); complete(h, cids); gap_tail(h)
        cid = cids[0]; saved = saved_packet(h, cid); assert saved is not None
        receipt = h.owner.receipt(cid); assert receipt is not None
        fence = probe._fence(h.jobs.cell_source)
        collect(h, 'compact'); collect(h, 'receipt')
        assert h.owner.receipt(cid) is None
        with h.store.db() as db:
            db.execute('BEGIN')
            assert probe.standalone_current(db, saved, h.jobs.cell_source, fence, receipt.encoded, admitted=True)
            assert not probe.standalone_current(db, saved, h.jobs.cell_source, fence, receipt.encoded, admitted=False)
        if mutation == 'generation':h.owner.revoked()
        elif mutation == 'source_replacement':
            with h.store.db() as db:path = Path(db.execute('SELECT path FROM chunks WHERE id=?', (cid,)).fetchone()[0])
            with path.open('r+b') as target:target.seek(44); target.write(b'\x7f\x00')
        else:
            with h.store.db() as db:
                if mutation == 'cancellation':db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (cid,))
                elif mutation == 'revision':db.execute("UPDATE segments SET text='changed' WHERE chunk_id=?", (cid,))
                else:db.execute('UPDATE chunks SET digest=? WHERE id=?', ('f' * 64, cid))
        with h.store.db() as db:
            db.execute('BEGIN')
            try:current = probe.standalone_current(db, saved, h.jobs.cell_source, fence, receipt.encoded, admitted=True)
            except (cells.Unavailable, ReadError):current = False
            assert current is False
        assert saved_packet(h, cid) == saved
        assert len(h.probes) <= 3


@pytest.mark.parametrize('phase', ['before_commit', 'after_commit'])
def test_C4_binding_unknown_ack_rereads_exact_state_without_double_charge(retirement_app_factory, monkeypatch, phase):
    with retirement_app_factory() as h:
        connect = sqlite3.connect; calls = []; faulted = []; uncertain = []
        recording = h.worker.recordings
        reconcile = recording._reconcile_primary_cache_publication
        delayed = []
        def later_page_reconcile():
            if phase == 'after_commit' and not delayed:
                delayed.append(batch(h)['revision'])
                return  # First ACK reread is temporarily unavailable.
            if delayed:
                assert batch(h)['revision'] > delayed[0]
                assert batch(h)['state'] == 'pending_receipts'
            return reconcile()
        class Fault(sqlite3.Connection):
            binding = False
            def execute(self, sql, parameters=()):
                if sql.lstrip().upper().startswith('UPDATE RECORDING_BATCHES SET PRIMARY_CACHE_STORE_KEY'):
                    self.binding = True; calls.append(tuple(parameters))
                return super().execute(sql, parameters)
            def commit(self):
                fail = self.binding and not faulted
                if fail:uncertain.append(recording._primary_cache_publish_unknown)
                if fail and phase == 'before_commit':
                    faulted.append(1); raise sqlite3.OperationalError('SYNTHETIC binding ACK before COMMIT')
                super().commit()
                if fail:
                    faulted.append(1); raise sqlite3.OperationalError('SYNTHETIC lost binding COMMIT ACK')
        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, 'connect', lambda *a, **kw: connect(*a, **kw, factory=Fault))
            patch.setattr(recording, '_reconcile_primary_cache_publication', later_page_reconcile)
            cid, = append_page(h)
            if phase == 'after_commit':
                assert recording._primary_cache_publish_unknown is not None
                # Publication of page 1 committed, so the next real page may
                # advance its source revision/state before page 1's reread.
                later, = append_page(h, number=2)
        assert faulted == [1] and len(calls) == (2 if phase == 'before_commit' else 1)
        assert batch(h)['primary_cache_store_key'] == h.owner.retention_store_key
        assert usage(h.store) == [('', 0, 512, 512), (batch(h)['id'], 0, 512, 512)]
        assert h.worker.recordings._primary_cache_publish_unknown is None
        complete(h, (cid,) if phase == 'before_commit' else (cid, later))
        assert h.store.chunk(cid)['next_offset'] == 10.5
        # Replay the authentic uncertain snapshot with a mismatched immutable
        # marker/charge: neither case may clear uncertainty or mutate evidence.
        recording._primary_cache_publish_unknown = uncertain[0]
        with h.store.db() as db:
            if phase == 'before_commit':
                db.execute(f'UPDATE {pp.USAGE} SET charge=charge-512,cache_scope_charge=0 WHERE scope=?', (batch(h)['id'],))
            else:
                ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='primary_cache_batch_update'").fetchone()[0]
                db.execute('DROP TRIGGER primary_cache_batch_update')
                db.execute('UPDATE recording_batches SET primary_cache_store_key=? WHERE session_id=?', ('f' * 64, h.sid))
                db.execute(ddl)
        before = snapshot(h)
        with pytest.raises(ValueError):reconcile()
        assert recording._primary_cache_publish_unknown == uncertain[0] and snapshot(h) == before
        with h.store.db() as db:
            if phase == 'before_commit':
                db.execute(f'UPDATE {pp.USAGE} SET charge=charge+512,cache_scope_charge=512 WHERE scope=?', (batch(h)['id'],))
            else:
                db.execute('DROP TRIGGER primary_cache_batch_update')
                db.execute('UPDATE recording_batches SET primary_cache_store_key=? WHERE session_id=?', (h.owner.retention_store_key, h.sid))
                db.execute(ddl)
        reconcile(); assert recording._primary_cache_publish_unknown is None
