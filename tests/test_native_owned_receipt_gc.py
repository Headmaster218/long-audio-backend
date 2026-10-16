"""Fresh, model-free orphan-receipt GC contracts; no acoustic qualification.

Real primary/sidecar schemas, bounded owner operations, and sealed-page row
shapes are used. Native responses, ASR, and the injected qualification profile
are explicitly synthetic. No historical/user database, model, network, decoder,
provider, migration, or original recording is opened. Metadata-capacity rows
are labeled synthetic and never imply 12/48-hour PCM or throughput evidence.
Every disposable fixture stays below 64 MiB; normal fixtures below 32 MiB.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import sqlite3
import shutil
import time
import wave

import pytest

from audio_backend import native_owned_cell_store as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import moss_result_store as history
from audio_backend.native_owned_cell_capture import Capture, PrimaryAuthority, signature
from audio_backend.native_owned_cells import RATE, SourceSpan, canonical, sha
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker
from test_native_owned_cell_store import simulated_profile
from test_native_owned_compact_gc import FreshGC, ConnectionProbe, file_hash, rows_hash, probe_primary
from test_native_owned_seam_cells import synthetic_composite, synthetic_pieces
from test_native_owned_cells import synthetic_cell
from test_native_owned_support import synthetic_provenance


Q = 168000
LIMIT = 64 * 1024**2
BATCH = 8
PREFIX = 28
PRIMARY_TABLES = ('sessions', 'chunks', 'segments', 'speaker_turns', 'events',
    'stream_chunks', 'stream_time_index', 'recording_batches', 'recording_pages',
    'recording_sources', 'recording_work', 'recording_ranges', 'native_core_commits',
    history.TABLE, history.USAGE)


def fixture_bytes(root):
    total = 0
    for path in root.rglob('*'):
        try:
            if path.is_file():total += path.stat().st_size
        except FileNotFoundError:pass
    assert total <= LIMIT, (total, LIMIT)
    return total


class FreshReceipts(FreshGC):
    """Real rows/files and production GC, no acoustic or runtime qualification."""
    def __init__(self, root):
        self.root = root; (root / 'uploads').mkdir()
        self.settings = Settings(data_dir=root, disk_reserve_bytes=0,
            diarization_backend='nemotron', start_worker=True,
            native_diarization_model='/synthetic/model.fixture',
            native_diarization_library='/synthetic/library.fixture',
            native_owned_cells_enabled=True, native_owned_core_seams_enabled=True,
            native_owned_compact_gc_enabled=True, native_owned_receipt_gc_enabled=True)
        self.store = Store(root / 'primary.sqlite3', self.settings)
        self.sid = self.store.create_session('SYNTHETIC receipt GC fixture')['id']
        self.authority = PrimaryAuthority(self.store)
        self.owner = cells.CellStore(root / 'native-owned-cells', self.authority,
            enabled=True, core_seam_enabled=True, compact_gc_enabled=True,
            receipt_gc_enabled=True, qualification=simulated_profile(root))
        self.authority.owner = self.owner
        # The real opener creates/checks bindings and uses bounded startup pages.
        # Only acoustic/runtime qualification is injected; no owner thread starts.
        self.owner._open(); self.optional = self.owner._db
        self.owner._ready.set(); self.owner._status = 'fixture_manual_production_open'
        self.capture = Capture(self.owner, self.authority)
        self.worker = Worker(self.store, self.settings)
        self.worker.native_owned_cells = self.capture
        self.capture.bind_retention_worker(self.worker)
        self.receipts = {}; self.commits = {}; self.paths = {}; self.budgets = []
        self.capture.core_seams.work_pending = False
        self.batches = {}; self.extra_paths = []; self.next_id = 0
        with self.store.db() as db:history.init_schema(db)

    def recording(self, count=31, *, sid=None, page_size=28, status='completed',
                  released=True, origin=900007, metadata_only=False, id_at=None):
        sid = sid or self.sid
        bid = 'batch-' + sid
        stream = 'recording.' + bid
        start = self.batches.get(bid, 0)
        ids = []
        self.optional.execute('BEGIN IMMEDIATE')
        with self.store.db() as db:
            if not start:
                db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,state,
                    capture_origin_frame,capture_end_frame,created,mode,eos)
                    VALUES(?,?,?,'open',?,?,0,'sealed_pages',0)''',
                    (bid, sid, 'synthetic-capture', origin, origin + count * Q))
            else:
                db.execute('UPDATE recording_batches SET capture_end_frame=? WHERE id=?',
                           (origin + (start + count) * Q, bid))
            previous = db.execute('SELECT COALESCE(MAX(page_number),0) FROM recording_pages WHERE batch_id=?', (bid,)).fetchone()[0]
            for offset in range(0, count, page_size):
                number = previous + offset // page_size + 1
                page_id = bid + '-page-' + str(number)
                end_position = start + min(count, offset + page_size)
                db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,
                    start_frame,end_frame,created) VALUES(?,?,?,?,?,?,?,0)''',
                    (page_id, bid, number, 'published' if released else 'pending_receipts', int(released),
                     origin + (start + offset) * Q, origin + end_position * Q))
                for position in range(start + offset, end_position):
                    cid = id_at(position) if id_at else f'receipt-{self.next_id:05d}'
                    self.next_id += 1
                    path = self.root / 'uploads' / (cid + '.audio')
                    if metadata_only:
                        # Deliberately absent PCM: bounded metadata/index capacity only.
                        digest = 'c' * 64; size = 44 + 2 * Q
                        sig = (1, self.next_id, size, 1, 1)
                    else:
                        with wave.open(str(path), 'wb') as target:
                            target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
                            target.writeframes(b'\x01\x00' * Q)
                        digest = file_hash(path); size = path.stat().st_size; sig = signature(path.stat())
                        self.paths[cid] = path
                    receipt = cells.Receipt(sid, cid, digest, Q, 44, size, sig, position * Q / RATE, stream)
                    db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
                        timeline_start,status,next_offset,created,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (cid, sid, cid, digest, str(path), Q / RATE, position * Q / RATE,
                         status, Q / RATE if status == 'completed' else 0., position,
                         '{"fixture":"SYNTHETIC sealed-page metadata; no acoustic inference"}'))
                    db.execute('''INSERT INTO stream_chunks(chunk_id,session_id,stream_id,position,
                        stream_final,upload_stream_final) VALUES(?,?,?,?,0,0)''', (cid, sid, stream, position))
                    db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
                        capture_start_frame,native_receipt_status,native_receipt,receipt_id,receipt_state,
                        page_id,file_signature) VALUES(?,?,?,?,?,?,'receipt_retained',?,?,'durable',?,?)''',
                        (cid, bid, position, Q, size, origin + position * Q, receipt.encoded,
                         receipt.receipt_id, page_id, canonical(list(sig)).decode()))
                    self.optional.execute('INSERT INTO sources VALUES(?,?,?,?)', (cid, sid, digest, receipt.encoded))
                    self.owner._receipts[cid] = receipt; self.receipts[cid] = receipt
                    db.execute('INSERT INTO segments(chunk_id,start,end,text,speaker_status) VALUES(?,0,?,?,?)',
                               (cid, Q / RATE, 'SYNTHETIC primary ' + cid, 'unknown'))
                    db.execute('INSERT INTO speaker_turns(chunk_id,start,end,speaker_status,overlap) VALUES(?,0,?,?,0)',
                               (cid, Q / RATE, 'unknown'))
                    db.execute('INSERT INTO events(chunk_id,start,end,label,score) VALUES(?,0,?,?,.5)',
                               (cid, Q / RATE, 'SYNTHETIC'))
                    # Real original-to-work mapping row shapes must remain byte exact.
                    source_id = 'source-' + cid
                    db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
                        frames,bytes,source_start_frame,format,created,page_id) VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
                        (source_id, bid, source_id, digest, str(path) + '.original', Q, size,
                         origin + position * Q, 'wav-pcm16le', page_id))
                    db.execute('INSERT INTO recording_ranges VALUES(?,?,?,?,?,?,?,?)',
                               (bid, source_id, cid, 0, origin + position * Q, 0, Q, 'owner'))
                    ids.append(cid)
        self.optional.execute('COMMIT')
        self.batches[bid] = start + count
        fixture_bytes(self.root)
        return ids

    def archive(self, cid):
        """Exact retained-result schema bytes, deliberately synthetic content."""
        metadata = canonical({'origin': 'durable_cell', 'fixture': 'SYNTHETIC', 'chunk_id': cid})
        result = canonical({'raw': '[0.0][S1]SYNTHETIC retained MOSS bytes', 'quality': 'unqualified'})
        charge = history.ACCOUNTING_BYTES + len(metadata) + len(result)
        with self.store.db() as db:
            db.execute(f'''INSERT INTO {history.TABLE}(job_id,session_id,original_jobs_epoch,
                idempotency_key,canonical_request_sha256,state,schema_version,metadata_json,
                result_json,result_sha256,committed_at,charge_bytes)
                VALUES(?,?,?,'fixture',?,'committed',1,?,?,?,?,?)''',
                ('1' * 32, self.sid, '2' * 32, '3' * 64, metadata, result,
                 hashlib.sha256(result).hexdigest(), 1., charge))
            db.execute(f'UPDATE {history.USAGE} SET row_count=1,charge_bytes=? WHERE scope=?', (charge, ''))
            db.execute(f'INSERT INTO {history.USAGE} VALUES(?,1,?)', (self.sid, charge))
        path = self.root / 'synthetic-moss-raw.fixture'
        path.write_bytes(result); self.extra_paths.append(path)

    def preservation(self):
        with self.store.db() as db:primary = rows_hash(db, PRIMARY_TABLES)
        return (primary, rows_hash(self.optional, ('cells', 'dependencies')),
            {cid: file_hash(path) for cid, path in self.paths.items()},
            {path.name: file_hash(path) for path in self.extra_paths})

    def source_rows(self):
        return {row['cid']: tuple(row) for row in self.optional.execute('SELECT * FROM sources ORDER BY cid')}

    def collect_receipts(self, *, op=None, budget=None):
        assert self.owner._slot is None
        op = op or cells.Operation('receipt_gc', (), self.owner._epoch)
        budget = budget or cells.Budget.for_owner(op)
        self.owner._slot = op; self.budgets.append(budget)
        try:
            result = self.owner._collect_receipts(op, budget)
            assert type(result) is dict, result
            assert 0 <= result.get('retired', 0) <= BATCH
            assert 0 <= result.get('scanned', 0) <= BATCH
            assert 0 <= result.get('prefix_rows', 0) <= PREFIX
            assert budget.sql_bytes <= cells.SQL_PROJECTION_BYTES
            assert budget.steps <= 150000
            assert budget.seconds == 2.0
            fixture_bytes(self.root)
            return result
        finally:
            self.owner._slot = None; op.done.set(); op.slot_released.set()

    def cycle(self, passes=12):
        return [self.collect_receipts() for _ in range(passes)]

    def reset_scan(self):
        self.owner._receipt_gc_cursor = ''
        self.owner._receipt_gc_prefixes = {}

    def restart_memory(self):
        """Fresh owner against this disposable DB; no durable GC attribution."""
        old = self.owner
        old._db = None; self.optional.close(); old._closed = True
        self.owner = cells.CellStore(old.directory, self.authority, enabled=True,
            core_seam_enabled=True, compact_gc_enabled=True, receipt_gc_enabled=True,
            qualification=simulated_profile(self.root))
        self.authority.owner = self.owner
        self.owner._open(); self.optional = self.owner._db
        self.owner._ready.set(); self.owner._status = 'fixture_restart_production_open'
        self.capture = Capture(self.owner, self.authority)
        self.worker.native_owned_cells = self.capture
        self.capture.bind_retention_worker(self.worker)
        self.capture.core_seams.work_pending = False

    def close(self):
        assert self.owner._thread is None and not self.worker.thread.is_alive()
        self.owner._db = None; self.optional.close(); fixture_bytes(self.root)


@pytest.fixture
def fresh_receipts(tmp_path):
    fixture = FreshReceipts(tmp_path)
    try:yield fixture
    finally:
        fixture.close()
        shutil.rmtree(tmp_path)  # Only this newly created, positively closed fixture.


def test_default_off_requires_compact_gc_without_raising_caps():
    assert Settings().native_owned_receipt_gc_enabled is False
    for value in (1, 'true', None):
        with pytest.raises(ValueError):Settings(native_owned_receipt_gc_enabled=value)
    with pytest.raises(ValueError):Settings(native_owned_receipt_gc_enabled=True)
    with pytest.raises(ValueError):Settings(native_owned_cells_enabled=True,
        native_owned_core_seams_enabled=True, native_owned_receipt_gc_enabled=True)
    settings = Settings(native_owned_cells_enabled=True, native_owned_core_seams_enabled=True,
        native_owned_compact_gc_enabled=True, native_owned_receipt_gc_enabled=True)
    assert settings.native_owned_receipt_gc_enabled is True
    assert cells.MAX_RECEIPTS == 4096 and core.MAX_COMPACTS == 2056
    assert cells.SQL_PROJECTION_BYTES == 262144 and cells.OWNER_OP_SECONDS == 2.0


def test_quiet_orphans_collect_exact_receipts_preserving_all_history(fresh_receipts):
    f = fresh_receipts; ids = f.recording(); f.archive(ids[0])
    preserved = f.preservation(); before = f.source_rows()
    results = f.cycle()
    assert any(item['retired'] for item in results), results
    # F=325.5s has current epoch-start=300s; replay begins278.8s.
    expected = set(ids[:26]); remaining = f.source_rows()
    assert set(before) - set(remaining) == expected
    assert remaining == {cid: row for cid, row in before.items() if cid not in expected}
    assert set(f.owner._receipts) == set(remaining)
    assert f.preservation() == preserved
    assert f.owner.health()['receipt_gc']['retired'] == len(expected)
    assert sum(row['retired'] for row in results) == len(expected)
    assert not any(row['retired'] for row in f.cycle(6))
    assert f.preservation() == preserved


@pytest.mark.parametrize('flag', ['receipt_gc_enabled', 'compact_gc_enabled', 'core_seam_enabled', 'enabled'])
def test_disabled_prerequisite_preserves_receipts(fresh_receipts, flag):
    f = fresh_receipts; f.recording(); before = f.source_rows(); preserved = f.preservation()
    setattr(f.owner, flag, False)
    assert not any(row['retired'] for row in f.cycle(3))
    assert f.source_rows() == before and f.preservation() == preserved


@pytest.mark.parametrize('schema', ['ordinary', 'v2', 'v3', 'reset', 'reset_unreadable'])
@pytest.mark.parametrize('with_pcm', [True, False])
def test_every_cell_dependency_is_a_root_including_metadata_only(fresh_receipts, schema, with_pcm):
    f = fresh_receipts; ids = f.recording()
    if schema == 'ordinary':
        f.ordinary_cell(ids[0], with_pcm=with_pcm); protected = {ids[0]}
    elif schema == 'v2':
        cell = synthetic_composite(sid=f.sid, cids=tuple(ids[:2]),
            receipt_ids=tuple(f.receipts[cid].receipt_id for cid in ids[:2]),
            source_hashes=tuple(f.receipts[cid].digest for cid in ids[:2]))
        f.add_cell(cell, with_pcm=with_pcm); protected = set(ids[:2])
    elif schema == 'reset':
        cell = synthetic_reset_cell(f, ids[:2])
        f.add_cell(cell, with_pcm=with_pcm); protected = set(ids[:2])
    else:
        cell, _ = f.pair(ids[0], ids[1], retain=True, with_pcm=with_pcm)
        # Remove compacts so dependency closure, rather than redundant compact
        # roots, is the reason these two receipt IDs remain.
        with f.store.db() as db:db.execute('DELETE FROM native_core_commits')
        if schema == 'reset_unreadable':
            # Unknown/corrupt reset proof metadata must fail closed too.
            f.optional.execute('UPDATE cells SET proof=? WHERE cell_id=?',
                (canonical({'schema': core.RESET_SCHEMA, 'synthetic_unreadable': True}), cell.cell_id))
        protected = set(ids[:2])
    f.archive(ids[0]); before = f.source_rows(); preserved = f.preservation()
    results = f.cycle()
    assert any(item['retired'] for item in results), results
    assert protected <= set(f.source_rows())
    assert set(before) - set(f.source_rows()) == set(ids[:26]) - protected
    assert f.preservation() == preserved


@pytest.mark.parametrize('pending', [True, False])
def test_pending_proof_and_remaining_compacts_protect_both_receipts(fresh_receipts, pending):
    f = fresh_receipts; ids = f.recording(); f.pair(ids[0], ids[1], pending=pending)
    before = f.source_rows(); preserved = f.preservation(); results = f.cycle()
    assert set(ids[:2]) <= set(f.source_rows())
    if pending:assert f.source_rows() == before and not any(row['retired'] for row in results)
    else:assert set(before) - set(f.source_rows()) == set(ids[2:26])
    assert f.preservation() == preserved


@pytest.mark.parametrize('root', ['pin', 'cancelled_undrained_pin', 'current_claim', 'claim_gap',
    'capture_active', 'provisional', 'flight', 'tail'])
def test_liveness_roots_defer_or_protect_exact_source(fresh_receipts, root):
    f = fresh_receipts; ids = f.recording(); protected = ids[0]
    if root in ('pin', 'cancelled_undrained_pin'):
        cell = synthetic_cell(sid=f.sid, cid=protected, receipt_id=f.receipts[protected].receipt_id)
        pin = cells.Pin(f.owner, cell.cell_id, json.loads(cell.proof), cell.pcm)
        if root == 'cancelled_undrained_pin':pin.cancelled.set(); pin._release_requested = True
        f.owner._pins[id(pin)] = pin
    elif root == 'current_claim':f.worker.current_chunk = ids[-1]
    elif root == 'claim_gap':
        with f.store.db() as db:db.execute("UPDATE chunks SET status='running' WHERE id=?", (ids[-1],))
        assert f.worker.current_chunk is None
    elif root == 'capture_active':f.capture.active = True
    elif root == 'provisional':f.capture._seam_provisional = object()
    elif root == 'flight':f.capture.core_seams.flight = ('synthetic', object(), 'id', 'sha')
    else:f.capture._seam_tail = synthetic_pieces(sid=f.sid, cids=tuple(ids[:2]))[0]
    before = f.source_rows(); preserved = f.preservation(); results = f.cycle(8)
    assert protected in f.source_rows()
    if root not in ('pin', 'cancelled_undrained_pin', 'tail'):
        assert f.source_rows() == before and not any(row['retired'] for row in results)
    assert f.preservation() == preserved
    f.owner._pins.clear()


@pytest.mark.parametrize('count', [28, 29, 31, 56, 58])
def test_replay_uses_epoch_start_not_latest_frontier_and_retains_straddlers(fresh_receipts, count):
    f = fresh_receipts; ids = f.recording(count); before = f.source_rows(); preserved = f.preservation()
    frontier = count * Q; boundary = frontier // (300 * RATE) * 300 * RATE
    cutoff = max(0, boundary - 339200)
    expected = {cid for index, cid in enumerate(ids) if (index + 1) * Q <= cutoff}
    f.cycle(20)
    assert set(before) - set(f.source_rows()) == expected
    for index, cid in enumerate(ids):
        if index * Q < cutoff < (index + 1) * Q:assert cid in f.source_rows()
    # For example F=588s must protect all receipts after278.8s, not just566.8s.
    assert f.preservation() == preserved


@pytest.mark.parametrize('fault', ['paused', 'failed', 'waiting_input', 'queued', 'incomplete',
    'position_gap', 'stream_index_missing', 'work_geometry', 'page_gap', 'page_unreleased'])
def test_unproved_or_incomplete_prefix_never_jumps_a_gap(fresh_receipts, fault):
    f = fresh_receipts; ids = f.recording(released=fault != 'page_unreleased', count=28 if fault == 'page_unreleased' else 31)
    with f.store.db() as db:
        if fault in ('paused', 'failed', 'waiting_input', 'queued'):
            db.execute('UPDATE chunks SET status=? WHERE id=?', (fault, ids[0]))
        elif fault == 'incomplete':db.execute('UPDATE chunks SET next_offset=0 WHERE id=?', (ids[0],))
        elif fault == 'position_gap':db.execute('UPDATE recording_work SET position=100 WHERE chunk_id=?', (ids[0],))
        elif fault == 'stream_index_missing':db.execute('DELETE FROM stream_time_index WHERE chunk_id=?', (ids[0],))
        elif fault == 'work_geometry':db.execute('UPDATE recording_work SET capture_start_frame=capture_start_frame+1 WHERE chunk_id=?', (ids[0],))
        elif fault == 'page_gap':db.execute('UPDATE recording_pages SET page_number=100 WHERE page_number=1')
    before = f.source_rows(); preserved = f.preservation(); results = f.cycle(8)
    assert not any(row['retired'] for row in results), results
    assert f.source_rows() == before and f.preservation() == preserved


@pytest.mark.parametrize('fault', ['legacy_recording', 'unreleased_work', 'pending_outbox',
    'payload_mismatch', 'receipt_id_mismatch', 'historical_alias'])
def test_unreleased_outbox_and_historical_alias_defer(fresh_receipts, fault):
    f = fresh_receipts; ids = f.recording(); cid = ids[0]
    with f.store.db() as db:
        if fault == 'legacy_recording':db.execute("UPDATE recording_batches SET mode='legacy'")
        elif fault == 'unreleased_work':db.execute('UPDATE recording_work SET page_id=NULL WHERE chunk_id=?', (cid,))
        elif fault == 'pending_outbox':db.execute("UPDATE recording_work SET receipt_state='pending' WHERE chunk_id=?", (cid,))
        elif fault == 'payload_mismatch':db.execute('UPDATE recording_work SET native_receipt=? WHERE chunk_id=?', (b'{}', cid))
        elif fault == 'receipt_id_mismatch':db.execute('UPDATE recording_work SET receipt_id=? WHERE chunk_id=?', ('f' * 64, cid))
        else:
            # A later pending historical alias explicitly reaches an old source.
            bid = 'batch-' + f.sid; page = bid + '-alias'
            db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,start_frame,end_frame,created)
                VALUES(?,?,3,'open',0,?,?,0)''', (page, bid, 900007 + 31 * Q, 900007 + 31 * Q))
            db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
                frames,bytes,source_start_frame,format,created,page_id)
                SELECT 'synthetic-alias',batch_id,'synthetic-alias',digest,path||'.alias',
                    frames,bytes,source_start_frame,format,1,? FROM recording_sources WHERE id=?''',
                (page, 'source-' + cid))
            db.execute('''INSERT INTO recording_ranges SELECT batch_id,'synthetic-alias',chunk_id,
                source_start_frame,capture_start_frame,work_start_frame,frames,'exact_alias'
                FROM recording_ranges WHERE source_id=?''', ('source-' + cid,))
    before = f.source_rows(); preserved = f.preservation(); f.cycle(8)
    assert cid in f.source_rows()
    if fault == 'legacy_recording':assert f.source_rows() == before
    assert f.preservation() == preserved


@contextmanager
def probe_sidecar(f, monkeypatch, **hooks):
    statements = []
    with monkeypatch.context() as patch:
        patch.setattr(f.owner, '_db', ConnectionProbe(f.optional, statements, **hooks))
        yield statements


def prime_prefix_without_collection(f):
    """Build genuine bounded prefix state while all source rows are pinned."""
    f.capture._seam_tail = synthetic_pieces(sid=f.sid, cids=tuple(f.receipts)[:2])[0]
    # Temporarily retain every source dependency, then remove only fixture rows.
    made = [f.ordinary_cell(cid, with_pcm=False) for cid in f.receipts]
    f.cycle(3)
    for cell in made:f.optional.execute('DELETE FROM cells WHERE cell_id=?', (cell.cell_id,))
    f.capture._seam_tail = None; f.owner._receipt_gc_cursor = ''


@pytest.mark.parametrize('phase', ['before_commit', 'after_commit'])
def test_unknown_commit_reconciles_exact_ids_without_retry_or_false_counters(fresh_receipts, monkeypatch, phase):
    f = fresh_receipts; f.recording(); prime_prefix_without_collection(f)
    before = f.source_rows(); preserved = f.preservation(); commits = []
    def fail(db, sql, args):
        if sql.strip().upper() == 'COMMIT':
            commits.append(1); raise sqlite3.OperationalError('SYNTHETIC commit acknowledgement fault')
    with probe_sidecar(f, monkeypatch, **{'before' if phase == 'before_commit' else 'after': fail}) as statements:
        result = f.collect_receipts()
    assert result['status'] == 'unknown' and result['retired'] == 0, result
    assert len(commits) == 1 and f.owner.health()['receipt_gc']['retired'] == 0
    after = f.source_rows(); removed = set(before) - set(after)
    assert len(removed) == (0 if phase == 'before_commit' else BATCH)
    assert f.owner.health()['receipt_gc']['uncertain_ids'] == BATCH
    for cid, raw in f.owner._receipt_gc_unknown:
        assert f.owner.receipt(cid) is None
        retry = cells.Operation('receipt', cells.Receipt.decode(raw), f.owner._epoch)
        budget = cells.Budget.for_owner(retry)
        with pytest.raises(cells.Unavailable, match='receipt_gc_commit_unknown'):
            with budget.phase():f.owner._receipt(retry, budget)
    assert f.source_rows() == after  # No cache fast-path ACK or blind reinsertion.
    with probe_sidecar(f, monkeypatch) as statements:resolved = f.collect_receipts()
    assert resolved['status'] == 'reconciled' and resolved['retired'] == 0, resolved
    assert not any(sql.lstrip().upper().startswith('DELETE') for sql, _ in statements)
    assert set(f.owner._receipts) == set(f.source_rows())
    assert f.owner.health()['receipt_gc']['retired'] == 0
    assert f.owner.health()['receipt_gc']['uncertain_ids'] == 0
    assert f.preservation() == preserved
    f.restart_memory()
    assert set(f.owner._receipts) == set(after)
    assert f.owner.health()['receipt_gc']['retired'] == 0
    assert f.owner.health()['receipt_gc']['uncertain_ids'] == 0
    assert not f.owner._receipt_gc_prefixes
    assert f.preservation() == preserved


@pytest.mark.parametrize('protected', [False, True])
def test_actual_global_4096_capacity_reopens_only_after_proved_gc(fresh_receipts, protected):
    f = fresh_receipts; ids = f.recording()
    if protected:
        for cid in ids:f.ordinary_cell(cid, with_pcm=False)
    other = f.store.create_session('SYNTHETIC unrelated receipt capacity')['id']
    # Actual4096 durable rows, not monkeypatched constants/count responses. These
    # cold metadata-only sources have no files and cannot prove a GC prefix.
    added = cells.MAX_RECEIPTS - len(ids)
    f.optional.execute('BEGIN IMMEDIATE')
    for index in range(added):
        cid = f'z-cold-{index:05d}'
        receipt = cells.Receipt(other, cid, 'c' * 64, 16, 44, 76,
            (1, index + 1, 76, 1, 1), 0., None)
        f.optional.execute('INSERT INTO sources VALUES(?,?,?,?)', (cid, other, receipt.digest, receipt.encoded))
        f.owner._receipts[cid] = receipt
    f.optional.execute('COMMIT')
    assert f.optional.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 4096
    # Production receipt admission with real authority/file, on another session.
    path = f.root / 'uploads' / 'new-admission.audio'
    with wave.open(str(path), 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE); target.writeframes(b'\x01\0' * 16)
    receipt = cells.Receipt(other, 'new-admission', file_hash(path), 16, 44, 76,
        signature(path.stat()), 0., None)
    with f.store.db() as db:
        db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
            timeline_start,status,next_offset,created,metadata) VALUES(?,?,?,?,?,.001,0,'completed',.001,0,'{}')''',
            (receipt.chunk_id, other, receipt.chunk_id, receipt.digest, str(path)))
    op = cells.Operation('receipt', receipt, f.owner._epoch)
    budget = cells.Budget.for_owner(op)
    with pytest.raises(cells.Unavailable, match='source_receipt_capacity'):
        with budget.phase():f.owner._receipt(op, budget)
    assert receipt.chunk_id not in f.owner._receipts
    # Reset the bounded source cursor so the first real cohort is inspected.
    f.owner._receipt_gc_cursor = ''
    results = []
    for _ in range(4):
        results.append(f.collect_receipts())
        if results[-1]['retired']:break
        f.owner._receipt_gc_cursor = ''
    if protected:
        assert not any(row['retired'] for row in results), results
        assert f.optional.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 4096
        op = cells.Operation('receipt', receipt, f.owner._epoch); budget = cells.Budget.for_owner(op)
        with pytest.raises(cells.Unavailable, match='source_receipt_capacity'):
            with budget.phase():f.owner._receipt(op, budget)
        assert receipt.chunk_id not in f.owner._receipts
        return
    assert any(row['retired'] for row in results), results
    op = cells.Operation('receipt', receipt, f.owner._epoch); budget = cells.Budget.for_owner(op)
    with budget.phase():result = f.owner._receipt(op, budget)
    assert result['status'] == 'receipt_retained'
    assert f.owner.receipt(receipt.chunk_id) == receipt
    assert f.optional.execute('SELECT COUNT(*) FROM sources WHERE sid=?', (other,)).fetchone()[0] == added + 1
    fixture_bytes(f.root)


def test_restart_rebuild_is_bounded_without_inventing_a_frontier(fresh_receipts):
    f = fresh_receipts; ids = f.recording()
    for cid in ids:f.ordinary_cell(cid, with_pcm=False)
    f.cycle(4)
    assert f.owner._receipt_gc_prefixes
    before = f.source_rows(); preserved = f.preservation()
    f.restart_memory()
    assert not f.owner._receipt_gc_prefixes
    first = f.collect_receipts()
    assert first['prefix_rows'] == PREFIX and first['retired'] == 0
    assert f.source_rows() == before
    assert f.owner.health()['receipt_gc']['retired'] == 0
    assert f.preservation() == preserved


def test_existing_indexes_bound_prefix_and_source_selection(fresh_receipts, monkeypatch):
    f = fresh_receipts; f.recording(58)
    with probe_primary(f, monkeypatch) as (primary, opens):
        with probe_sidecar(f, monkeypatch) as sidecar:result = f.collect_receipts()
    assert len(opens) == 1 and result['prefix_rows'] == 28
    selections = [(sql, args) for sql, args in primary
                  if 'WHERE w.page_id=? ORDER BY w.position LIMIT ?' in sql]
    assert len(selections) == 1 and selections[0][1][-1] <=28
    with f.store.db() as db:
        for sql, args in selections:
            plan = ' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + sql, args))
            assert 'recording_page_work' in plan and 'TEMP B-TREE' not in plan, plan
    selections = [(sql, args) for sql, args in sidecar
                  if 'SELECT cid,length(receipt) FROM sources WHERE cid>' in sql]
    assert len(selections) == 1 and selections[0][1][-1] == 8
    plan = ' '.join(row[3] for row in f.optional.execute('EXPLAIN QUERY PLAN ' + selections[0][0], selections[0][1]))
    assert 'INDEX' in plan and 'TEMP B-TREE' not in plan
    assert all(not sql.lstrip().upper().startswith(('DELETE', 'UPDATE', 'INSERT')) for sql, _ in primary)


def test_real_owner_restart_open_recording_then_append_and_future_seam(tmp_path, monkeypatch):
    from audio_backend.recording_overlap import RecordingOverlap
    from test_same_epoch_core_seams import RealHarness, wait
    from test_multicore_seam_coverage import pcm
    from test_recording_pages import CAPTURE, create, put, drain_page
    h = RealHarness(tmp_path, (42,), monkeypatch)
    try:
        h.settings.start_worker = True
        h.settings.recording_disk_reserve_bytes = 0
        h.settings.native_owned_compact_gc_enabled = True
        h.settings.native_owned_receipt_gc_enabled = True
        h.owner.compact_gc_enabled = True; h.owner.receipt_gc_enabled = True
        h.capture.bind_retention_worker(h.worker)
        monkeypatch.setattr(h.owner, '_schedule_compact_gc', lambda: False)
        monkeypatch.setattr(h.owner, '_schedule_receipt_gc', lambda: False)
        h.recording = RecordingOverlap(h.store)
        create(h.recording, h.sid, 1, 19)
        put(h.recording, h.sid, 1, 'synthetic-first', pcm(0, 4 * Q), 19)
        h.recording.seal_page(h.sid, CAPTURE, 1, 'seal-first', 1, 19 + 4 * Q, cells=h.capture)
        first = drain_page(h, 1)
        assert first['released']
        h.source_ids = [row['chunk_id'] for row in first['work_chunks']]
        while (chunk := h.store.claim()) is not None:h.worker.process(chunk)
        h.settle()
        assert h.store.claim() is None
        assert any(h.store.chunk(cid)['status'] == 'waiting_input' for cid in h.source_ids)
        with sqlite3.connect(h.owner.path) as db:before = rows_hash(db, ('sources', 'cells', 'dependencies'))
        # Real owner close/reopen executes its bounded production20-row loader.
        old = h.owner
        if h.worker.native:h.worker.native.close(); h.worker.native = None
        old.close(.5); old._thread.join(4); assert not old._thread.is_alive()
        h.owner = cells.CellStore(old.directory, h.authority, enabled=True,
            core_seam_enabled=True, compact_gc_enabled=True, receipt_gc_enabled=True,
            qualification=simulated_profile(h.root))
        h.authority.owner = h.owner
        h.capture = Capture(h.owner, h.authority); h.worker.native_owned_cells = h.capture
        h.capture.bind_retention_worker(h.worker)
        monkeypatch.setattr(h.owner, '_schedule_compact_gc', lambda: False)
        monkeypatch.setattr(h.owner, '_schedule_receipt_gc', lambda: False)
        h.owner.start(); assert h.owner._ready.wait(4), h.owner.health()
        h.settle()
        with h.owner._lock:
            assert h.owner._slot is None
            operation = cells.Operation('receipt_gc', (), h.owner._epoch, h.owner._wake)
            h.owner._slot = operation; h.owner._wake.set()
        result = wait(operation)
        assert result['retired'] == 0, result
        with sqlite3.connect(h.owner.path) as db:assert rows_hash(db, ('sources', 'cells', 'dependencies')) == before
        assert all(h.owner.receipt(cid) is not None for cid in h.source_ids)
        create(h.recording, h.sid, 2)
        put(h.recording, h.sid, 2, 'synthetic-second', pcm(4 * Q, 7 * Q), 19 + 4 * Q)
        h.recording.seal_page(h.sid, CAPTURE, 2, 'seal-second', 1, 19 + 7 * Q, eos=True, cells=h.capture)
        second = drain_page(h, 2); assert second['released']
        h.source_ids += [row['chunk_id'] for row in second['work_chunks']]
        h.run()
        matching = [(item, doc) for item, doc in h.cells() if doc['geometry']['seam_frame'] == 4 * Q]
        assert len(matching) == 1, (h.capture.health(), h.rows())
        assert core.validate_document(canonical(matching[0][1]))[0] == matching[0][1]
        assert matching[0][1]['coverage']['adds_primary_audio'] is False
        assert all(h.store.chunk(cid)['status'] == 'completed' for cid in h.source_ids)
        fixture_bytes(tmp_path)
    finally:
        h.close()
        assert not h.owner._thread.is_alive()
        fixture_bytes(tmp_path)
        shutil.rmtree(tmp_path)


def synthetic_reset_cell(fixture, ids):
    """Valid reset-schema proof; synthetic coordinates test coarse CID roots."""
    provenance = synthetic_provenance(); epoch = 'b' * 32
    a, seam, b = 290 * RATE, 300 * RATE, 310 * RATE
    replay = seam - 339200; observed_frames = b - replay
    native = {'epoch_id': epoch, 'epoch_index': 1, 'owned_start': 300., 'owned_end': 600.,
        'replay_start': replay / RATE, 'input_until': 310., 'processed_until': 310.,
        'stable_until': 310., 'finished': True, 'provenance': provenance}
    evidence = {'chunk_id': ids[1], 'start': 0, 'end': 10 * RATE, 'native': native,
        'core_cut': {'nominal': 10., 'actual': 10., 'reason': 'fixed_or_final'}}
    commits = []
    for index, cid in enumerate(ids):
        receipt = fixture.receipts[cid]
        part = SourceSpan(fixture.sid, cid, receipt.receipt_id, receipt.digest, receipt.frames,
            0, 10 * RATE, a + index * 10 * RATE, seam + index * 10 * RATE)
        commit = {'scope': {'session_id': fixture.sid, 'kind': 'stream', 'key': receipt.stream_id,
            'origin': (0.).hex(), 'run': fixture.capture.core_seams.run, 'reset': index + 1,
            'epoch': 'a' * 32 if index == 0 else epoch, 'index': index}, 'part': asdict(part),
            'native_sha256': sha(native), 'cut_sha256': sha(evidence['core_cut']),
            'evidence_sha256': sha(evidence), 'runtime_sha256': sha(provenance)}
        core.validate_commit(commit); commits.append(commit)
    state = {'epoch_id': epoch, 'finished': True, 'fingerprint': provenance['diarization_fingerprint'],
        'local_capacity': 8}
    for key in ('input', 'processed', 'stable'):
        state[key] = {'raw': observed_frames / RATE, 'hex': float(observed_frames / RATE).hex(),
                      'frame': observed_frames}
    pcm = b'\x01\x00' * (b - a)
    query = {'query_start_frame': a, 'query_end_frame': b,
        'query_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
        'support_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
        'native': {'epoch_id': epoch, 'epoch_index': 1, 'replay_start': replay,
            'state': state, 'provenance': provenance, 'observed_action_count': 5, 'true_eof': True},
        'turns': [{'label': epoch + ':speaker_' + str(index), 'original': [a, b]} for index in (1, 2)]}
    cell = core.make_cell(commits, query, evidence, pcm, schema=core.RESET_SCHEMA)
    core.validate_document(cell.proof)
    return cell


@pytest.mark.parametrize('scope', ['same_stream', 'unknown'])
def test_live_analyzer_context_is_conservative(fresh_receipts, scope):
    f = fresh_receipts; ids = f.recording(); before = f.source_rows(); preserved = f.preservation()
    f.capture.analyzer = object()
    f.capture.scope = ('stream', f.sid, f.receipts[ids[0]].stream_id) if scope == 'same_stream' else None
    f.capture.scope_start = 0.; f.capture.analyzer_start = 8 * Q
    f.cycle()
    expected = set(ids[:8]) if scope == 'same_stream' else set()
    assert set(before) - set(f.source_rows()) == expected
    assert f.preservation() == preserved


@pytest.mark.parametrize('fault', ['generation', 'cancel', 'claim', 'deadline', 'pin'])
def test_final_guard_rolls_back_late_source_delete_changes(fresh_receipts, monkeypatch, fault):
    f = fresh_receipts; ids = f.recording(); prime_prefix_without_collection(f)
    before = f.source_rows(); preserved = f.preservation(); changed = []
    op = cells.Operation('receipt_gc', (), f.owner._epoch); budget = cells.Budget.for_owner(op)
    def after_delete(db, sql, args):
        if changed or not sql.lstrip().upper().startswith('DELETE FROM SOURCES'):return
        changed.append(1)
        if fault == 'generation':f.owner._epoch = object()
        elif fault == 'cancel':op.cancelled.set()
        elif fault == 'claim':f.worker.current_chunk = ids[-1]
        elif fault == 'deadline':budget.deadline = time.monotonic() - 1.
        else:
            cell = synthetic_cell(sid=f.sid, cid=ids[0], receipt_id=f.receipts[ids[0]].receipt_id)
            pin = cells.Pin(f.owner, cell.cell_id, json.loads(cell.proof), cell.pcm)
            f.owner._pins[id(pin)] = pin
    with probe_sidecar(f, monkeypatch, after=after_delete):result = f.collect_receipts(op=op, budget=budget)
    assert changed and result['retired'] == 0, result
    assert f.source_rows() == before and f.preservation() == preserved
    assert f.owner.health()['receipt_gc']['retired'] == 0
    f.owner._pins.clear()


def test_eight_stream_prefix_cache_eviction_keeps_same_pass_plan(fresh_receipts):
    """Metadata-only cache pressure; no file or acoustic qualification claim."""
    from audio_backend.recording_overlap import receipt_gc_source, receipt_gc_prefix
    f = fresh_receipts; streams = []
    for index in range(9):
        sid = f.sid if index == 0 else f.store.create_session('SYNTHETIC metadata stream ' + str(index))['id']
        def identity(position, stream_index=index):
            if stream_index == 0:return 'a-old-eligible' if position == 0 else f'z-old-{position:05d}'
            if stream_index == 8:return f'b-new-{position:05d}'
            return f'z-{stream_index}-{position:05d}'
        streams.append(f.recording(sid=sid, metadata_only=True, id_at=identity))
    # Build actual proved-prefix values through the production bounded helper.
    # This installs no invented frontier, and avoids hundreds of cursor passes.
    for ids in streams[:8]:
        receipt = f.receipts[ids[0]]; previous = None
        for _ in range(2):
            budget = cells.Budget.for_owner()
            with budget.phase(), f.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                source = receipt_gc_source(db, receipt, budget)
                previous, count = receipt_gc_prefix(db, receipt.session_id, source, previous, budget, PREFIX)
                assert 0 < count <= PREFIX
        f.owner._receipt_gc_prefixes[(receipt.session_id, source['batch_id'])] = previous
    f.owner._receipt_gc_prefix_generation = f.owner._epoch
    assert len(f.owner._receipt_gc_prefixes) == 8
    before = f.source_rows(); preserved = f.preservation()
    result = f.collect_receipts()
    assert result['status'] == 'retired' and result['retired'] == 1, result
    assert set(before) - set(f.source_rows()) == {'a-old-eligible'}
    assert len(f.owner._receipt_gc_prefixes) == 8
    assert f.preservation() == preserved


def fill_metadata_capacity(f):
    """Actual sidecar rows; absent cold PCM is explicitly metadata-only."""
    sid = f.store.create_session('SYNTHETIC cold capacity metadata')['id']
    existing = f.optional.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
    f.optional.execute('BEGIN IMMEDIATE')
    for index in range(cells.MAX_RECEIPTS - existing):
        cid = f'z-cold-{index:05d}'
        receipt = cells.Receipt(sid, cid, 'c' * 64, 16, 44, 76, (1, index + 1, 76, 1, 1), 0., None)
        f.optional.execute('INSERT INTO sources VALUES(?,?,?,?)', (cid, sid, receipt.digest, receipt.encoded))
        f.owner._receipts[cid] = receipt
    f.optional.execute('COMMIT')
    assert f.optional.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 4096


def test_full4096_novel_pending_page_reclaims_then_real_receipt_ack_releases(fresh_receipts):
    from audio_backend.recording_overlap import RecordingOverlap
    from test_recording_pages import wav_bytes
    f = fresh_receipts; f.recording(); fill_metadata_capacity(f)
    f.settings.recording_disk_reserve_bytes = 0
    recording = RecordingOverlap(f.store); capture = 'synthetic-capture'; origin = 900007
    page = recording.create_page(f.sid, capture, 3)
    with f.store.db() as db:batch = recording._batch(db, f.sid, capture)
    path = recording.source_path(batch, page)
    path.write_bytes(wav_bytes(b'\x01\0' * Q))
    recording.accept_page(f.sid, capture, 3, 'novel-pending-source', path,
                          source_start_frame=origin + 31 * Q)
    occupied = cells.Operation('receipt_gc', (), f.owner._epoch)
    f.owner._slot = occupied  # Real one-slot backpressure keeps first delivery pending.
    try:
        page = recording.seal_page(f.sid, capture, 3, 'novel-seal', 1, origin + 32 * Q, cells=f.capture)
    finally:
        f.owner._slot = None; occupied.done.set(); occupied.slot_released.set()
    assert page['state'] == 'pending_receipts' and not page['released'], page
    assert recording._flight is None
    cid = page['work_chunks'][0]['chunk_id']
    with f.store.db() as db:
        row = db.execute('SELECT native_receipt,receipt_id,receipt_state FROM recording_work WHERE chunk_id=?', (cid,)).fetchone()
    payload, receipt_id, state = tuple(row); assert state == 'pending'
    receipt = cells.Receipt.decode(payload)
    op = cells.Operation('receipt', receipt, f.owner._epoch); budget = cells.Budget.for_owner(op)
    with pytest.raises(cells.Unavailable, match='source_receipt_capacity'):
        with budget.phase():f.owner._receipt(op, budget)
    # A refused owner operation is not delivered as a terminal recording gap.
    # Maintenance runs before the page's actual pending operation is submitted.
    before = f.preservation()
    results = []
    for _ in range(3):
        f.owner._receipt_gc_cursor = ''; results.append(f.collect_receipts())
        if results[-1]['retired']:break
    assert any(result['retired'] for result in results), results
    assert f.preservation() == before
    assert not recording.page_status(f.sid, capture, 3)['released']
    recording.advance(f.capture, force=True)
    operation = recording._flight['operation']; budget = cells.Budget.for_owner(operation)
    with budget.phase():operation.result = f.owner._receipt(operation, budget)
    assert operation.result['status'] == 'receipt_retained'
    f.owner._slot = None; operation.payload = None; operation.done.set(); operation.slot_released.set()
    assert recording.advance(f.capture, force=True)
    result = recording.page_status(f.sid, capture, 3)
    assert result['released'] and result['optional_receipts']['durable'] == 1, result
    with f.store.db() as db:
        current = tuple(db.execute('SELECT native_receipt,receipt_id,receipt_state FROM recording_work WHERE chunk_id=?', (cid,)).fetchone())
    assert current == (payload, receipt_id, 'durable')
    assert f.owner.receipt(cid) == receipt
    assert f.optional.execute('SELECT COUNT(*) FROM sources').fetchone()[0] <= 4096


def test_primary_fence_blocks_pending_alias_interval_mutation_until_commit(fresh_receipts, monkeypatch):
    f = fresh_receipts; ids = f.recording(); prime_prefix_without_collection(f)
    bid = 'batch-' + f.sid; page = bid + '-pending'; origin = 900007
    with f.store.db() as db:
        db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,start_frame,end_frame,created)
            VALUES(?,?,3,'open',0,?,?,0)''', (page, bid, origin + 31 * Q, origin + 32 * Q))
        db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
            frames,bytes,source_start_frame,format,created,page_id)
            VALUES('pending-interval',?,'pending-interval',?,'/synthetic/absent.input',?,?,?,'synthetic',0,?)''',
            (bid, 'c' * 64, Q, 44 + 2 * Q, origin + 31 * Q, page))
    attempted = []; preserved = f.preservation()
    def before_delete(db, sql, args):
        if attempted or not sql.lstrip().upper().startswith('DELETE FROM SOURCES'):return
        with f.store.db() as other:
            other.execute('PRAGMA busy_timeout=0')
            with pytest.raises(sqlite3.OperationalError) as error:
                other.execute("UPDATE recording_sources SET source_start_frame=? WHERE id='pending-interval'",
                              (origin + 8 * Q,))
            assert error.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
        attempted.append(1)
    with probe_sidecar(f, monkeypatch, before=before_delete):result = f.collect_receipts()
    assert attempted and result['retired'] == 8, result
    assert f.preservation() == preserved
    # Once the claim fence ends, the same pending input mutation can commit.
    with f.store.db() as db:
        db.execute("UPDATE recording_sources SET source_start_frame=? WHERE id='pending-interval'", (origin + 8 * Q,))
    f.owner._receipt_gc_cursor = ''
    f.cycle(8)
    assert ids[8] in f.source_rows()


def test_default_future_context_and_exact_replay_mapping_survive_real_gc(fresh_receipts):
    f = fresh_receipts; ids = f.recording(); f.settings.context_seconds = 5
    assert f.capture.analyzer is None
    f.cycle()
    assert set(f.source_rows()) == set(ids[26:])
    future = f.recording(3)
    chunk = f.store.chunk(future[-1]); budget = cells.Budget.for_owner()
    start, end = 300 * RATE - 339200, 320 * RATE
    with budget.phase():
        mapping = f.authority.mapping(chunk, 0., start, end, budget)
        pcm = f.authority.original_pcm(mapping, budget)
    assert [part.chunk_id for part in mapping] == ids[26:]
    assert mapping[0].logical_start == start and mapping[-1].logical_end == end
    assert hashlib.sha256(pcm).digest() == hashlib.sha256(b'\x01\0' * (end - start)).digest()
    # Exact twenty-second future support straddles old/new sealed-page sources.
    budget = cells.Budget.for_owner(); start, end = 3155 * RATE // 10, 3355 * RATE // 10
    with budget.phase():
        mapping = f.authority.mapping(chunk, 0., start, end, budget)
        pcm = f.authority.original_pcm(mapping, budget)
    assert [part.chunk_id for part in mapping] == [ids[-1], future[0]]
    assert len(pcm) == 40 * RATE and all(f.owner.receipt(part.chunk_id) for part in mapping)


def pending_metadata(f, *, sources=1, unknown=False, ranges=0):
    """Synthetic pending-input rows for finite metadata refusal contracts."""
    bid = 'batch-' + f.sid; page = bid + '-pending-metadata'; origin = 900007
    cid = next(iter(f.receipts))
    with f.store.db() as db:
        db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,start_frame,end_frame,created)
            VALUES(?,?,3,'open',0,?,?,0)''', (page, bid, origin + 31 * Q, origin + 32 * Q))
        for index in range(sources):
            source = f'pending-metadata-{index}'
            db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
                frames,bytes,source_start_frame,format,created,page_id) VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
                (source, bid, source, 'c' * 64, '/synthetic/' + source,
                 Q, 44 + 2 * Q, None if unknown else origin + 31 * Q, 'synthetic', page))
        for index in range(ranges):
            db.execute('INSERT INTO recording_ranges VALUES(?,?,?,?,?,?,?,?)',
                (bid, 'pending-metadata-0', cid, index, origin + index, index, 1, 'exact_alias'))
    return page


@pytest.mark.parametrize('fault', ['unknown_position', 'seventeen_sources', 'excess_ranges'])
def test_pending_metadata_excess_or_unknown_fails_closed(fresh_receipts, fault):
    f = fresh_receipts; f.recording(); prime_prefix_without_collection(f)
    pending_metadata(f, sources=17 if fault == 'seventeen_sources' else 1,
        unknown=fault == 'unknown_position', ranges=913 if fault == 'excess_ranges' else 0)
    before = f.source_rows(); preserved = f.preservation()
    results = f.cycle(3)
    assert not any(row['retired'] for row in results), results
    assert f.source_rows() == before and f.preservation() == preserved


def test_pending_alias_queries_use_existing_bounded_indexes(fresh_receipts, monkeypatch):
    f = fresh_receipts; ids = f.recording(); prime_prefix_without_collection(f)
    pending_metadata(f, ranges=1)
    with probe_primary(f, monkeypatch) as (statements, _):result = f.collect_receipts()
    assert result['retired'] == 7 and ids[0] in f.source_rows(), result
    selected = [(sql, args) for sql, args in statements if 'CROSS JOIN recording_ranges' in sql]
    assert selected
    with f.store.db() as db:
        for sql, args in selected:
            plan = ' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + sql, args))
            assert 'recording_page_sources' in plan, plan
            assert 'sqlite_autoindex_recording_ranges_1' in plan and 'SCAN r' not in plan, plan


def test_malformed_cell_anchor_without_dependency_still_protects_cid(fresh_receipts):
    f = fresh_receipts; ids = f.recording()
    cell = f.ordinary_cell(ids[0], with_pcm=False)
    f.optional.execute('DELETE FROM dependencies WHERE cell_id=?', (cell.cell_id,))
    f.optional.execute("UPDATE cells SET sid='SYNTHETIC-wrong-session' WHERE cell_id=?", (cell.cell_id,))
    preserved = f.preservation(); f.cycle()
    assert ids[0] in f.source_rows() and f.preservation() == preserved


def test_receipt_scheduler_shared_slot_refusals_and_pair_completion_cooldown(fresh_receipts):
    f = fresh_receipts; f.recording(); owner = f.owner
    owner._reconcile.clear()
    counts = owner._retention_snapshot[2]
    for occupied in (None, 0):
        counts['sources']['occupied'] = occupied
        assert not owner._schedule_receipt_gc()
    counts['sources']['occupied'] = 31
    for flag in ('receipt_gc_enabled', '_closed', '_admission_closed'):
        previous = getattr(owner, flag); setattr(owner, flag, not previous)
        assert not owner._schedule_receipt_gc()
        setattr(owner, flag, previous)
    owner._reconcile.set()
    assert not owner._schedule_receipt_gc()  # Startup/revocation cleanup wins.
    owner._reconcile.clear()
    assert owner._lock.acquire(blocking=False)
    try:assert not owner._schedule_receipt_gc()
    finally:owner._lock.release()
    busy = cells.Operation('receipt', f.receipts[next(iter(f.receipts))], owner._epoch)
    owner._slot = busy
    assert not owner._schedule_receipt_gc() and owner._slot is busy
    owner._slot = None
    counts['compacts']['occupied'] = 2
    assert owner._schedule_compact_gc()
    compact = owner._slot
    assert compact.kind == 'compact_gc' and not owner._schedule_receipt_gc()
    result = owner._collect_compacts(compact, cells.Budget.for_owner(compact))
    assert result['retired'] == 0
    owner._slot = None; compact.done.set(); compact.slot_released.set()
    assert owner._schedule_receipt_gc()
    receipt = owner._slot
    assert receipt.kind == 'receipt_gc' and receipt.payload == () and receipt.epoch is owner._epoch
    assert not owner._schedule_receipt_gc() and not owner._schedule_compact_gc()
    result = owner._collect_receipts(receipt, cells.Budget.for_owner(receipt))
    assert result['prefix_rows'] == 28 and result['retired'] == 0
    owner._slot = None; receipt.done.set(); receipt.slot_released.set()
    now = time.monotonic()
    assert owner._receipt_gc_next > now
    assert owner._compact_gc_next >= owner._receipt_gc_next
    assert not owner._schedule_receipt_gc() and not owner._schedule_compact_gc()
    # Exact unknown-commit reconciliation may run even without sampled counts,
    # but it still waits for cleanup and uses this same one owner slot.
    owner._receipt_gc_next = 0.; counts['sources']['occupied'] = None
    candidate = next(iter(f.receipts.values()))
    owner._receipt_gc_unknown = ((candidate.chunk_id, candidate.encoded),)
    owner._reconcile.set(); assert not owner._schedule_receipt_gc()
    owner._reconcile.clear(); assert owner._schedule_receipt_gc()
    operation = owner._slot
    result = owner._collect_receipts(operation, cells.Budget.for_owner(operation))
    assert result['status'] == 'reconciled' and result['retired'] == 0
    owner._slot = None; operation.done.set(); operation.slot_released.set()
    assert not owner._receipt_gc_unknown and owner.receipt(candidate.chunk_id) == candidate


def test_production_future_seam_after_positive_receipt_retirement(tmp_path, monkeypatch):
    """Real recording/worker/owner; native responses and ASR remain synthetic."""
    from audio_backend.recording_overlap import RecordingOverlap
    from test_same_epoch_core_seams import RealHarness, wait
    from test_multicore_seam_coverage import pcm
    from test_recording_pages import CAPTURE, create, put, drain_page
    h = RealHarness(tmp_path, (), monkeypatch)
    try:
        h.settings.start_worker = True; h.settings.recording_disk_reserve_bytes = 0
        h.settings.native_owned_compact_gc_enabled = True; h.settings.native_owned_receipt_gc_enabled = True
        h.owner.compact_gc_enabled = True; h.owner.receipt_gc_enabled = True
        h.capture.bind_retention_worker(h.worker)
        monkeypatch.setattr(h.owner, '_schedule_compact_gc', lambda: False)
        monkeypatch.setattr(h.owner, '_schedule_receipt_gc', lambda: False)
        h.recording = RecordingOverlap(h.store)
        completed_frames = 0
        def append(number, pieces, *, eos=False):
            nonlocal completed_frames
            create(h.recording, h.sid, number, 19 if number == 1 else None)
            # Each original is <=120s; page1 uses105s+105s+84s.
            source_count = 0
            for offset in range(0, pieces, 10):
                count = min(10, pieces - offset)
                a = completed_frames + offset * Q; b = a + count * Q
                put(h.recording, h.sid, number, f'synthetic-{number}-{offset}', pcm(a, b), 19 + a)
                source_count += 1
            completed_frames += pieces * Q
            h.recording.seal_page(h.sid, CAPTURE, number, f'seal-{number}', source_count,
                                  19 + completed_frames, eos=eos, cells=h.capture)
            page = drain_page(h, number); assert page['released'], page
            h.source_ids.extend(row['chunk_id'] for row in page['work_chunks'])
            return page
        append(1, 28); append(2, 3); append(3, 3)
        # Stop between real claims, before the queued third page is prepared.
        # Its published PCM supplies normal native lookahead for page2.
        for _ in range(31):
            chunk = h.store.claim(); assert chunk is not None
            h.worker.process(chunk)
        h.settle()
        assert all(h.store.chunk(cid)['status'] == 'completed' for cid in h.source_ids[:31])
        assert all(h.store.chunk(cid)['status'] == 'queued' for cid in h.source_ids[31:])
        def collect(kind):
            with h.owner._lock:
                assert h.owner._slot is None
                operation = cells.Operation(kind, (), h.owner._epoch, h.owner._wake)
                h.owner._slot = operation; h.owner._wake.set()
            result = wait(operation)
            assert result.get('scanned', 0) <= 8 and result.get('retired', 0) <= 8
            assert result.get('prefix_rows', 0) <= 28
            return result
        compact_results = [collect('compact_gc') for _ in range(8)]
        assert sum(row['retired'] for row in compact_results) > 0, (compact_results, h.capture.health(), h.rows())
        with h.store.db() as db:
            primary = rows_hash(db, ('chunks', 'segments', 'speaker_turns', 'events',
                'stream_chunks', 'stream_time_index', 'recording_batches', 'recording_pages',
                'recording_sources', 'recording_work', 'recording_ranges', 'native_core_commits'))
        with sqlite3.connect(h.owner.path) as db:
            before = {row[0] for row in db.execute('SELECT cid FROM sources')}
            retained_cells = rows_hash(db, ('cells', 'dependencies'))
        files = {path.name: file_hash(path) for path in (tmp_path / 'uploads').iterdir() if path.is_file()}
        results = [collect('receipt_gc') for _ in range(8)]
        retired = sum(row['retired'] for row in results)
        assert retired > 0, results
        with sqlite3.connect(h.owner.path) as db:
            after = {row[0] for row in db.execute('SELECT cid FROM sources')}
            assert rows_hash(db, ('cells', 'dependencies')) == retained_cells
        assert len(before - after) == retired
        assert set(h.source_ids[26:]) <= after  # Epoch-start-minus21.2s replay.
        with h.store.db() as db:
            assert rows_hash(db, ('chunks', 'segments', 'speaker_turns', 'events',
                'stream_chunks', 'stream_time_index', 'recording_batches', 'recording_pages',
                'recording_sources', 'recording_work', 'recording_ranges', 'native_core_commits')) == primary
        assert {path.name: file_hash(path) for path in (tmp_path / 'uploads').iterdir() if path.is_file()} == files
        # A new current native response introduces overlap only AFTER positive GC.
        seam = 31 * Q
        h.activity = [[seam - 3 * RATE // 4, seam + 3 * RATE // 4]]
        h.markers = [(seam + RATE // 4, voice, f'synthetic-future-{voice}') for voice in (1, 2)]
        append(4, 3, eos=True); h.run()
        matching = [(item, doc) for item, doc in h.cells() if doc['geometry']['seam_frame'] == seam]
        assert len(matching) == 1, (h.capture.health(), h.rows())
        item, proof = matching[0]
        assert core.validate_document(canonical(proof))[0] == proof
        assert [commit['part']['chunk_id'] for commit in proof['commits']] == h.source_ids[30:32]
        result = wait(h.owner.submit('read', item['cell_id']))
        assert result['status'] == 'available', result
        try:
            assert result['pin'].read() == pcm(proof['geometry']['start_frame'], proof['geometry']['end_frame'])
        finally:result['pin'].release()
        assert proof['coverage']['adds_primary_audio'] is False
        with sqlite3.connect(h.owner.path) as db:
            final = {row[0] for row in db.execute('SELECT cid FROM sources')}
        assert not (before - after) & final  # No hidden reintroduction of old receipts.
        assert all(h.store.chunk(cid)['status'] == 'completed' for cid in h.source_ids)
        fixture_bytes(tmp_path)
    finally:
        h.close(); assert not h.owner._thread.is_alive()
        fixture_bytes(tmp_path); shutil.rmtree(tmp_path)
