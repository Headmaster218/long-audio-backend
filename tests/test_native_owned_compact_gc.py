"""Fresh, model-free compact-only GC contracts; no acoustic qualification.

All input is generated here or by the existing explicitly synthetic native/ASR
fixtures. No historical/user database, model, decoder, provider, network, external
process, migration, or original recording is opened. The small deterministic SQL
fixture installs real Store/Worker/Capture identities and the existing owner slot;
it does not start an owner. Its manually opened sidecar and the existing
simulated profile are fixture-only, and do not qualify SQLite/VFS/crash durability.

Per fixture, observed live files must stay <=32 MiB.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
import wave

import pytest

from audio_backend import native_owned_core_seams as core
from audio_backend import native_owned_cell_store as cells
from audio_backend.native_owned_cell_capture import Capture, PrimaryAuthority, signature
from audio_backend.native_owned_cells import RATE, SourceSpan, canonical, sha
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker
from test_native_owned_cell_store import simulated_profile
from test_native_owned_cells import synthetic_cell
from test_native_owned_seam_cells import synthetic_pieces
from test_native_owned_support import synthetic_provenance


FILE_LIMIT = 32 * 1024**2
BATCH = 8
BODY_LIMIT = 32 * 1024


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(65536):
            digest.update(block)
    return digest.hexdigest()


def live_bytes(root):
    total = 0
    for path in root.rglob('*'):
        try:
            if path.is_file():total += path.stat().st_size
        except FileNotFoundError:
            pass  # A SQLite journal may close between enumeration and stat.
    assert total <= FILE_LIMIT, (total, FILE_LIMIT)
    return total


def rows_hash(db, names):
    """Hashes exact durable cells/PCM/dependencies/text/turns/results, not counts."""
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        for row in db.execute('SELECT * FROM ' + name + ' ORDER BY rowid'):
            for value in tuple(row):
                raw = value if type(value) is bytes else repr(value).encode()
                digest.update(len(raw).to_bytes(8, 'big')); digest.update(raw)
    return digest.hexdigest()


class FreshGC:
    """Real DB schemas/bindings, never an owner thread or qualification bypass."""
    def __init__(self, root):
        self.root = root
        (root / 'uploads').mkdir()
        self.settings = Settings(data_dir=root, disk_reserve_bytes=0,
            diarization_backend='nemotron', start_worker=True,
            native_diarization_model='/synthetic/model.fixture',
            native_diarization_library='/synthetic/library.fixture',
            native_owned_cells_enabled=True, native_owned_core_seams_enabled=True,
            native_owned_compact_gc_enabled=True)
        self.store = Store(root / 'primary.sqlite3', self.settings)
        self.sid = self.store.create_session('SYNTHETIC compact GC fixture')['id']
        self.authority = PrimaryAuthority(self.store)
        directory = root / 'native-owned-cells'
        directory.mkdir(mode=0o700)
        self.optional = sqlite3.connect(directory / 'cells.sqlite3', isolation_level=None)
        self.optional.row_factory = sqlite3.Row
        self.optional.execute('PRAGMA foreign_keys=ON')
        cells.CellStore._schema(self.optional)
        self.owner = cells.CellStore(directory, self.authority,
            enabled=True, core_seam_enabled=True, compact_gc_enabled=True,
            qualification=simulated_profile(root))
        self.owner._db = self.optional
        self.owner._ready.set()
        self.owner._status = 'fixture_manual_connection'
        self.authority.owner = self.owner
        self.capture = Capture(self.owner, self.authority)
        self.worker = Worker(self.store, self.settings)
        self.worker.native_owned_cells = self.capture
        self.capture.bind_retention_worker(self.worker)
        self.receipts = {}
        self.commits = {}
        self.paths = {}
        self.budgets = []
        self.capture.core_seams.work_pending = False

    def add(self, count=1, *, compacts=True, status='completed', stream='stream',
            frames=16, scope_index=0, reset=1, epoch='a' * 32):
        first = len(self.receipts)
        answer = []
        # Batch optional fixture writes too: no per-receipt journal churn.
        self.optional.execute('BEGIN IMMEDIATE')
        with self.store.db() as db:
            for position in range(first, first + count):
                cid = f'chunk-{position:05d}'
                timeline = position * frames / RATE
                path = self.root / 'uploads' / (cid + '.audio')
                with wave.open(str(path), 'wb') as target:
                    target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
                    target.writeframes(b'\x01\x00' * frames)
                receipt = cells.Receipt(self.sid, cid, file_hash(path), frames, 44,
                    path.stat().st_size, signature(path.stat()), timeline, stream)
                evidence = {'chunk_id': cid, 'start': 0, 'end': frames,
                    'native': {'synthetic': True, 'epoch_id': epoch, 'epoch_index': scope_index},
                    'core_cut': {'nominal': frames / RATE, 'actual': frames / RATE,
                                 'reason': 'fixed_or_final'}}
                metadata = {'native_diarization': evidence['native'], 'core_cut': evidence['core_cut'],
                            'fixture': 'SYNTHETIC, no acoustic inference'}
                db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,
                    duration,timeline_start,status,next_offset,created,metadata)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (cid, self.sid, cid, receipt.digest,
                    str(path), frames / RATE, timeline, status,
                    frames / RATE if status == 'completed' else 0., position, json.dumps(metadata)))
                if stream is not None:
                    db.execute('''INSERT INTO stream_chunks(chunk_id,session_id,stream_id,
                        position,stream_final,upload_stream_final) VALUES(?,?,?,?,0,0)''',
                        (cid, self.sid, stream, position))
                self.optional.execute('INSERT INTO sources VALUES(?,?,?,?)',
                                      (cid, self.sid, receipt.digest, receipt.encoded))
                self.owner._receipts[cid] = receipt
                self.receipts[cid] = receipt; self.paths[cid] = path
                part = SourceSpan(self.sid, cid, receipt.receipt_id, receipt.digest, frames,
                                  0, frames, position * frames, (position + 1) * frames)
                commit = {'scope': {'session_id': self.sid,
                    'kind': 'stream' if stream is not None else 'upload',
                    'key': stream if stream is not None else cid, 'origin': (0.).hex(),
                    'run': self.capture.core_seams.run, 'reset': reset, 'epoch': epoch,
                    'index': scope_index}, 'part': asdict(part),
                    'native_sha256': sha(evidence['native']), 'cut_sha256': sha(evidence['core_cut']),
                    'evidence_sha256': sha(evidence), 'runtime_sha256': sha(synthetic_provenance())}
                core.validate_commit(commit)
                self.commits[cid] = commit
                if compacts:self.put_commit(db, commit)
                # Genuine primary output row shapes; content is deliberately synthetic.
                db.execute('''INSERT INTO segments(chunk_id,start,end,text,speaker_status)
                    VALUES(?,?,?,?,?)''', (cid, 0., frames / RATE, 'synthetic-' + cid, 'unknown'))
                db.execute('''INSERT INTO speaker_turns(chunk_id,start,end,speaker_status,overlap)
                    VALUES(?,?,?,?,0)''', (cid, 0., frames / RATE, 'unknown'))
                db.execute('INSERT INTO events(chunk_id,start,end,label,score) VALUES(?,?,?,?,?)',
                           (cid, 0., frames / RATE, 'SYNTHETIC', .5))
                answer.append(cid)
        self.optional.execute('COMMIT')
        live_bytes(self.root)
        return answer

    def put_commit(self, db, commit, *, state='none', pending=None):
        core.validate_commit(commit)
        db.execute('''INSERT INTO native_core_commits VALUES(?,?,?,?,?,?,?,?,?)''',
            (core.slot(commit), self.sid, commit['part']['chunk_id'],
             core.scope_key(commit['scope']), commit['part']['logical_end'],
             canonical(commit, core.COMPACT_BYTES), pending, state, 'synthetic_fixture'))

    def add_cell(self, cell, *, with_pcm=True):
        self.optional.execute('''INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state)
            VALUES(?,?,?,?,?,?,?)''', (cell.cell_id, cell.ownership, cell.session_id,
            cell.chunk_id, cell.proof, cell.pcm if with_pcm else None,
            'retained' if with_pcm else 'not_retained_pcm'))
        for index, part in enumerate(cell.parts):
            self.optional.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',
                (cell.cell_id, index, part.chunk_id, part.source_sha256, part.receipt_sha256,
                 part.source_start, part.source_end, part.logical_start, part.logical_end))
        return cell

    def ordinary_cell(self, cid, *, with_pcm=True):
        # Source-CID roots must be coarse: even unrelated commit-range metadata
        # on this source protects every source compact, not just the cell anchor.
        receipt = self.receipts[cid]
        cell = synthetic_cell(sid=self.sid, cid=cid, receipt_id=receipt.receipt_id)
        return self.add_cell(cell, with_pcm=with_pcm)

    def pair(self, left, right, *, pending=False, retain=False, with_pcm=True):
        pieces = synthetic_pieces(sid=self.sid, cids=(left, right),
            receipt_ids=tuple(self.receipts[cid].receipt_id for cid in (left, right)),
            source_hashes=tuple(self.receipts[cid].digest for cid in (left, right)))
        commits = []
        for piece in pieces:
            evidence = piece.commit['evidence']
            commits.append({'scope': {'session_id': self.sid, 'kind': 'stream', 'key': 'stream',
                'origin': (0.).hex(), 'run': self.capture.core_seams.run, 'reset': 1,
                'epoch': 'a' * 32, 'index': 0}, 'part': asdict(piece.part),
                'native_sha256': sha(evidence['native']), 'cut_sha256': sha(evidence['core_cut']),
                'evidence_sha256': sha(evidence), 'runtime_sha256': sha(piece.witness['native']['provenance'])})
        pcm = pieces[0].pcm + pieces[1].pcm
        query = {'query_start_frame': 0, 'query_end_frame': 32,
            'query_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
            'support_pcm16le_sha256': hashlib.sha256(pcm).hexdigest(),
            'native': deepcopy(pieces[1].witness['native']),
            'turns': deepcopy(pieces[1].witness['turns'])}
        cell = core.make_cell(commits, query, pieces[1].commit['evidence'], pcm)
        core.validate_document(cell.proof)
        with self.store.db() as db:
            for index, commit in enumerate(commits):
                db.execute('DELETE FROM native_core_commits WHERE cid=?',
                           (commit['part']['chunk_id'],))
                self.put_commit(db, commit, state='pending' if pending and index else 'none',
                                pending=cell.proof if pending and index else None)
                self.commits[commit['part']['chunk_id']] = commit
        if retain:self.add_cell(cell, with_pcm=with_pcm)
        return cell, commits

    def compact_rows(self):
        with self.store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM native_core_commits ORDER BY slot')]

    def snapshot(self):
        with self.store.db() as db:
            primary = rows_hash(db, ('sessions', 'chunks', 'segments', 'speaker_turns', 'events',
                'stream_chunks', 'stream_time_index', 'recording_batches', 'recording_pages', 'recording_work'))
        return (primary, rows_hash(self.optional, ('sources', 'cells', 'dependencies')),
                {cid: file_hash(path) for cid, path in self.paths.items()},
                {cid: receipt.encoded for cid, receipt in self.owner._receipts.items()})

    def collect(self, *, budget=None, op=None):
        assert self.owner._slot is None
        op = op or cells.Operation('compact_gc', (), self.owner._epoch)
        self.owner._slot = op
        budget = budget or cells.Budget.for_owner(op)
        self.budgets.append(budget)
        try:
            result = self.owner._collect_compacts(op, budget)
            assert type(result) is dict, result
            assert result.get('retired', 0) <= BATCH
            assert result.get('scanned', 0) <= BATCH
            assert budget.sql_bytes <= cells.SQL_PROJECTION_BYTES
            live_bytes(self.root)
            return result
        finally:
            self.owner._slot = None
            op.done.set(); op.slot_released.set()

    def close(self):
        assert self.owner._thread is None
        assert not self.worker.thread.is_alive()
        self.owner._db = None
        self.optional.close()
        live_bytes(self.root)


@pytest.fixture
def fresh_gc(tmp_path):
    fixture = FreshGC(tmp_path)
    try:yield fixture
    finally:fixture.close()


def assert_preserved(fixture, before, rows):
    assert fixture.snapshot() == before
    assert fixture.compact_rows() == rows


def test_default_off_flags_and_dependencies():
    assert Settings().native_owned_compact_gc_enabled is False
    for value in (1, 'yes', None):
        with pytest.raises(ValueError):Settings(native_owned_compact_gc_enabled=value)
    for flags in ({}, {'native_owned_cells_enabled': True},
                  {'native_owned_core_seams_enabled': True}):
        with pytest.raises(ValueError):Settings(native_owned_compact_gc_enabled=True, **flags)
    settings = Settings(native_owned_cells_enabled=True, native_owned_core_seams_enabled=True,
                        native_owned_compact_gc_enabled=True)
    assert settings.native_owned_compact_gc_enabled is True
    with pytest.raises((ValueError, cells.Unavailable)):
        cells.CellStore(Path('/unused-not-opened'), None, enabled=True, compact_gc_enabled=1)


@pytest.mark.parametrize('args', [[], ['--native-owned-cells'], ['--native-owned-core-seams']])
def test_cli_requires_both_prerequisites(args):
    from audio_backend.__main__ import main
    with pytest.raises(SystemExit) as exc:main(args + ['--native-owned-compact-gc'])
    assert exc.value.code == 2


def test_cli_opt_in_reaches_settings_without_starting_server(monkeypatch, tmp_path):
    import uvicorn
    from audio_backend.__main__ import main
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: calls.append(app))
    main(['--data-dir', str(tmp_path / 'cli'), '--diarization-backend', 'nemotron',
          '--native-diarization-model', '/synthetic/model.fixture',
          '--native-diarization-library', '/synthetic/library.fixture',
          '--native-owned-cells', '--native-owned-core-seams', '--native-owned-compact-gc'])
    assert len(calls) == 1
    assert calls[0].state.settings.native_owned_compact_gc_enabled is True
    assert not calls[0].state.worker.thread.is_alive()
    live_bytes(tmp_path)


def test_quiet_reclamation_preserves_every_other_durable_byte(fresh_gc):
    f = fresh_gc; ids = f.add(4)
    before = f.snapshot(); rows = f.compact_rows()
    result = f.collect()
    assert result['status'] == 'retired', result
    assert result['retired'] == 3
    assert {row['cid'] for row in f.compact_rows()} == {ids[-1]}
    assert f.snapshot() == before
    health = f.owner.health()['compact_gc']
    assert health['retired'] == 3
    assert health['scope'] == 'completed_stream_sources_only'
    assert health['max_batch'] == 8 and health['body_projection_limit'] == 32768
    assert health['source_receipt_limit'] == 4096
    assert len(rows) - len(f.compact_rows()) == health['retired']
    again = f.collect()
    assert again.get('retired', 0) == 0
    assert f.owner.health()['compact_gc']['retired'] == 3
    assert f.snapshot() == before


@pytest.mark.parametrize('flag', ['compact_gc_enabled', 'enabled', 'core_seam_enabled'])
def test_disabled_owner_never_mutates(fresh_gc, flag):
    f = fresh_gc; f.add(3); before = f.snapshot(); rows = f.compact_rows()
    setattr(f.owner, flag, False)
    result = f.collect()
    assert result.get('retired', 0) == 0, result
    assert_preserved(f, before, rows)


@pytest.mark.parametrize('fault', [
    'missing_worker', 'wrong_worker_type', 'wrong_loop_target', 'missing_current_chunk',
    'wrong_store', 'copied_settings', 'different_capture', 'different_authority_owner',
    'different_owner_authority', 'unsupported_configuration', 'configuration_failure',
])
def test_exact_live_binding_rejects_replacement_or_missing_attributes(fresh_gc, fault):
    f = fresh_gc; f.add(3); before = f.snapshot(); rows = f.compact_rows()
    if fault == 'missing_worker':f.capture._retention_worker = None
    elif fault == 'wrong_worker_type':f.capture._retention_worker = SimpleNamespace(current_chunk=None)
    elif fault == 'wrong_loop_target':f.worker.thread._target = lambda: None
    elif fault == 'missing_current_chunk':del f.worker.current_chunk
    elif fault == 'wrong_store':f.worker.store = SimpleNamespace(settings=f.settings)
    elif fault == 'copied_settings':f.worker.settings = deepcopy(f.settings)
    elif fault == 'different_capture':f.worker.native_owned_cells = Capture(f.owner, f.authority)
    elif fault == 'different_authority_owner':f.authority.owner = object()
    elif fault == 'different_owner_authority':f.owner.authority = object()
    elif fault == 'unsupported_configuration':f.settings.start_worker = False
    elif fault == 'configuration_failure':f.owner.configuration_reason = 'fixture_unsupported'
    result = f.collect()
    assert result['status'] == 'deferred', result
    assert result['retired'] == 0
    assert_preserved(f, before, rows)


@pytest.mark.parametrize('phase', ['running_before_assignment', 'paused_after_assignment',
    'interrupted_before_prepare', 'completed_before_publication'])
def test_worker_claim_and_completion_windows_refuse_gc(fresh_gc, phase):
    f = fresh_gc; ids = f.add(3)
    if phase == 'running_before_assignment':
        with f.store.db() as db:db.execute("UPDATE chunks SET status='running' WHERE id=?", (ids[-1],))
        assert f.worker.current_chunk is None
    else:
        # The real loop owns current_chunk until its outer finally, including
        # pause/InterruptedError before Capture.prepare and after completion.
        f.worker.current_chunk = ids[-1]
        if phase == 'paused_after_assignment':
            with f.store.db() as db:db.execute("UPDATE chunks SET status='paused' WHERE id=?", (ids[-1],))
        if phase == 'interrupted_before_prepare':
            f.worker.stop_event.set()
            assert not f.capture.active
        if phase == 'completed_before_publication':
            assert all(f.store.chunk(cid)['status'] == 'completed' for cid in ids)
            f.capture._seam_provisional = None
    before = f.snapshot(); rows = f.compact_rows()
    result = f.collect()
    assert result.get('retired', 0) == 0, result
    assert_preserved(f, before, rows)


def test_supported_loop_keeps_current_chunk_through_processing_finally():
    # The separate pause-gap fixture checks interruption safety.
    assert 'self._run_loop()' in inspect.getsource(Worker.loop)
    source = inspect.getsource(Worker._run_loop)
    assert source.index('current_chunk') < source.index('self.process(')
    assert 'finally:' in source and 'self.current_chunk=None' in source


@pytest.mark.parametrize('root', ['capture_active', 'provisional', 'core_flight', 'tail'])
def test_live_capture_roots_are_conservative(fresh_gc, root):
    f = fresh_gc; ids = f.add(3)
    if root == 'capture_active':f.capture.active = True
    elif root == 'provisional':f.capture._seam_provisional = object()
    elif root == 'core_flight':f.capture.core_seams.flight = ('fixture', object(), 'id', 'hash')
    else:
        f.capture._seam_tail = synthetic_pieces(sid=f.sid, cids=(ids[0], ids[1]))[0]
    before = f.snapshot(); old = f.compact_rows()
    result = f.collect()
    assert f.snapshot() == before
    retained = {row['cid'] for row in f.compact_rows()}
    assert ids[0] in retained
    if root != 'tail':
        assert result.get('retired', 0) == 0, result
        assert f.compact_rows() == old
    else:
        assert ids[-1] in retained
        assert result['retired'] == 1


@pytest.mark.parametrize('with_pcm', [True, False])
def test_all_dependency_cids_protected_even_metadata_only(fresh_gc, with_pcm):
    f = fresh_gc; ids = f.add(4)
    cell = f.ordinary_cell(ids[0], with_pcm=with_pcm)
    # Add a distinct compact range on the same CID. A per-anchor test would
    # miss this; the dependency source-CID must protect both source compacts.
    extra = deepcopy(f.commits[ids[0]])
    extra['part'].update(source_start=1, logical_start=1)
    with f.store.db() as db:f.put_commit(db, extra)
    before = f.snapshot(); result = f.collect()
    assert result['retired'] == 2, result
    retained = f.compact_rows()
    assert sum(row['cid'] == ids[0] for row in retained) == 2
    assert {row['cid'] for row in retained} == {ids[0], ids[-1]}
    assert f.snapshot() == before
    assert f.optional.execute('SELECT proof FROM cells WHERE cell_id=?', (cell.cell_id,)).fetchone()[0] == cell.proof


@pytest.mark.parametrize('with_pcm', [True, False])
def test_v3_dependencies_protect_both_sides_and_result_payload(fresh_gc, with_pcm):
    f = fresh_gc; ids = f.add(4)
    cell, _ = f.pair(ids[0], ids[1], retain=True, with_pcm=with_pcm)
    # Synthetic retained-result bytes; no MOSS/model invocation.
    result_payload = {'origin': 'durable_cell', 'cell_id': cell.cell_id,
        'cell_binding': {'proof_sha256': hashlib.sha256(cell.proof).hexdigest()},
        'raw': b'SYNTHETIC MOSS result', 'result': b'{"synthetic":true}'}
    expected_result = deepcopy(result_payload)
    before = f.snapshot(); result = f.collect()
    assert result['retired'] == 1, result
    assert {row['cid'] for row in f.compact_rows()} == {ids[0], ids[1], ids[-1]}
    assert f.snapshot() == before and result_payload == expected_result


@pytest.mark.parametrize('pending_count', [1, 4])
def test_pending_proof_roots_cannot_lose_either_side(fresh_gc, pending_count):
    f = fresh_gc; ids = f.add(6)
    cell, _ = f.pair(ids[0], ids[1], pending=True)
    if pending_count == 4:
        # Three more bounded pending rows deliberately share the exact valid
        # proof. Duplicate/mismatched row bindings must fail closed, not make
        # the two proof dependencies reclaimable. Full payload bodies need
        # not be projected to establish that pending roots exist.
        with f.store.db() as db:
            for cid in ids[2:5]:
                db.execute("UPDATE native_core_commits SET state='pending',pending=? WHERE cid=?",
                           (cell.proof, cid))
    before = f.snapshot(); old = f.compact_rows(); result = f.collect()
    assert result.get('retired', 0) == 0, result
    assert_preserved(f, before, old)
    assert sum(row['pending'] is not None for row in old) == pending_count


@pytest.mark.parametrize('receipt_status', ['pending', 'durable'])
def test_unreleased_recording_outbox_protects_source_and_frontier(fresh_gc, receipt_status):
    f = fresh_gc; ids = f.add(3)
    with f.store.db() as db:
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,state,created)
            VALUES('batch',?,'capture','open',0)''', (f.sid,))
        db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
            capture_start_frame,native_receipt_status) VALUES(?,'batch',0,16,76,0,?)''', (ids[0], receipt_status))
    before = f.snapshot(); result = f.collect()
    assert ids[0] in {row['cid'] for row in f.compact_rows()}
    assert f.snapshot() == before
    # Releasing publication in a fresh synthetic transaction removes the fence.
    with f.store.db() as db:db.execute("UPDATE recording_batches SET state='queued' WHERE id='batch'")
    f.owner._compact_gc_cursor = ''
    result = f.collect()
    assert result['retired'] >= 1, result
    assert ids[0] not in {row['cid'] for row in f.compact_rows()}


@pytest.mark.parametrize('fault', ['isolated_upload', 'paused_candidate', 'failed_candidate',
    'queued_frontier', 'same_source_frontier', 'wrong_scope', 'same_position',
    'missing_stream_index', 'incomplete_frontier', 'malformed_body', 'ambiguous_frontier'])
def test_unproved_frontier_does_not_authorize_retirement(fresh_gc, fault):
    f = fresh_gc
    ids = f.add(2, stream=None if fault == 'isolated_upload' else 'stream')
    with f.store.db() as db:
        if fault in ('paused_candidate', 'failed_candidate'):
            db.execute('UPDATE chunks SET status=? WHERE id=?', (fault.split('_')[0], ids[0]))
        elif fault == 'queued_frontier':
            db.execute("UPDATE chunks SET status='queued',next_offset=0 WHERE id=?", (ids[1],))
        elif fault == 'same_source_frontier':
            db.execute('DELETE FROM native_core_commits WHERE cid=?', (ids[1],))
            early = deepcopy(f.commits[ids[0]])
            early['part'].update(source_end=8, logical_end=8)
            db.execute('DELETE FROM native_core_commits WHERE cid=?', (ids[0],))
            f.put_commit(db, early)
            later = deepcopy(f.commits[ids[0]])
            later['part'].update(source_start=8, logical_start=8)
            f.put_commit(db, later)
        elif fault == 'wrong_scope':
            changed = deepcopy(f.commits[ids[1]]); changed['scope']['key'] = 'different-stream'
            db.execute('UPDATE native_core_commits SET body=? WHERE cid=?',
                       (canonical(changed), ids[1]))
        elif fault == 'same_position':
            db.execute('UPDATE stream_chunks SET position=3 WHERE chunk_id=?', (ids[0],))
        elif fault == 'missing_stream_index':
            db.execute('DELETE FROM stream_time_index WHERE chunk_id=?', (ids[1],))
        elif fault == 'incomplete_frontier':
            db.execute('UPDATE chunks SET next_offset=0 WHERE id=?', (ids[1],))
        elif fault == 'malformed_body':
            db.execute('UPDATE native_core_commits SET body=? WHERE cid=?', (b'{}', ids[1]))
        elif fault == 'ambiguous_frontier':
            extra = deepcopy(f.commits[ids[1]])
            extra['part'].update(source_start=1, logical_start=17)
            f.put_commit(db, extra)
    before = f.snapshot(); old = f.compact_rows(); result = f.collect()
    assert result.get('retired', 0) == 0, result
    assert_preserved(f, before, old)


def test_cursor_counts_protected_rows_without_skipping_unexamined_ids(fresh_gc):
    f = fresh_gc; ids = f.add(18)
    ordered = f.compact_rows()
    protected_cids = {row['cid'] for row in ordered[:8]}
    for cid in protected_cids:f.ordinary_cell(cid, with_pcm=False)
    before = f.snapshot()
    result = f.collect()
    assert result['scanned'] == 8 and result['retired'] == 0, result
    assert f.owner._compact_gc_cursor == ordered[7]['slot']
    assert f.compact_rows() == ordered
    expected = {row['cid'] for row in ordered[8:16]} - {ids[-1]}
    result = f.collect()
    assert result['scanned'] == 8 and result['retired'] == len(expected), result
    assert f.owner._compact_gc_cursor == ordered[15]['slot']
    left = {row['cid'] for row in f.compact_rows()}
    assert not expected & left
    assert protected_cids <= left
    assert {row['cid'] for row in ordered[16:]} <= left
    result = f.collect()
    assert result['scanned'] == 2
    assert f.snapshot() == before
    assert f.collect()['retired'] == 0  # Empty suffix only resets cursor.
    assert f.owner._compact_gc_cursor == ''
    assert {ids[-1]} | protected_cids == {row['cid'] for row in f.compact_rows()}


class ConnectionProbe:
    """Real SQLite connection with bounded deterministic fault/race barriers."""
    def __init__(self, db, statements, *, before=None, after=None, cleanup=None):
        self.db = db; self.statements = statements
        self.before = before; self.after = after; self.cleanup = cleanup

    def execute(self, sql, parameters=()):
        self.statements.append((sql, parameters))
        if self.before is not None:self.before(self.db, sql, parameters)
        result = self.db.execute(sql, parameters)
        if self.after is not None:self.after(self.db, sql, parameters)
        return result

    def set_progress_handler(self, callback, amount):
        self.db.set_progress_handler(callback, amount)
        if callback is None and self.cleanup is not None:self.cleanup()

    def __getattr__(self, name):return getattr(self.db, name)


@contextmanager
def probe_primary(f, monkeypatch, **hooks):
    original = f.store.db; statements = []; opens = []
    @contextmanager
    def wrapped():
        opens.append(1)
        with original() as db:yield ConnectionProbe(db, statements, **hooks)
    with monkeypatch.context() as patch:
        patch.setattr(f.store, 'db', wrapped)
        yield statements, opens


def test_primary_busy_uses_one_five_ms_attempt_and_never_writes(fresh_gc, monkeypatch):
    f = fresh_gc; f.add(3); before = f.snapshot(); rows = f.compact_rows()
    blocker = sqlite3.connect(f.store.path, isolation_level=None, timeout=0)
    blocker.execute('BEGIN IMMEDIATE')
    try:
        with probe_primary(f, monkeypatch) as (statements, opens):
            start = time.monotonic(); result = f.collect(); elapsed = time.monotonic() - start
        assert elapsed < .5
        assert len(opens) == 1
        sql = [statement.strip().upper() for statement, _ in statements]
        assert sql.count('BEGIN IMMEDIATE') == 1
        assert sql.index('PRAGMA BUSY_TIMEOUT=5') < sql.index('BEGIN IMMEDIATE')
        assert not any(statement.startswith('DELETE') for statement in sql)
        assert result.get('retired', 0) == 0, result
    finally:
        blocker.execute('ROLLBACK'); blocker.close()
    assert_preserved(f, before, rows)


def test_next_real_claim_is_excluded_until_gc_transaction_ends(fresh_gc, monkeypatch):
    f = fresh_gc; f.add(3); queued = f.add(1, compacts=False, status='queued')[0]
    original = f.store.db; claim_attempts = []
    def at_delete(db, sql, parameters):
        if not sql.lstrip().upper().startswith('DELETE FROM NATIVE_CORE_COMMITS'):return
        assert db.in_transaction
        # The real Store.claim uses its own connection/BEGIN IMMEDIATE. Make
        # only its connection timeout zero; never wait behind ourselves.
        @contextmanager
        def no_wait():
            with original() as candidate:
                candidate.execute('PRAGMA busy_timeout=0'); yield candidate
        with monkeypatch.context() as patch:
            patch.setattr(f.store, 'db', no_wait)
            with pytest.raises(sqlite3.OperationalError) as exc:f.store.claim()
        assert exc.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
        claim_attempts.append(1)
    with probe_primary(f, monkeypatch, before=at_delete):result = f.collect()
    assert result['status'] == 'retired' and claim_attempts
    chunk = f.store.claim()
    assert chunk['id'] == queued
    assert f.store.chunk(queued)['status'] == 'running'


def test_real_paused_claim_gap_interrupts_before_prepare_and_preserves_predecessor(fresh_gc, monkeypatch):
    f = fresh_gc; quiet = f.add(3); queued = f.add(1, compacts=False, status='queued')[0]
    original = f.store.claim; observed = []; prepares = []; commits = []
    def claimed_then_paused():
        row = original()
        if row is None:
            f.worker.stop_event.set(); return None
        assert f.worker.current_chunk is None
        f.store.set_state(row['id'], 'pause')
        observed.append(f.collect())
        assert quiet[-1] in {r['cid'] for r in f.compact_rows()}
        return row
    class NeverAnalyzer:
        def analyze(self, *args, **kwargs):raise AssertionError('paused source reached analyzer')
    # Run the actual loop and interruption path; only analyzer construction is
    # replaced, so no inference process can ever be created by this fixture.
    monkeypatch.setattr(f.store, 'claim', claimed_then_paused)
    monkeypatch.setattr(f.worker, 'process', lambda chunk: f.worker._process_windows(chunk, NeverAnalyzer()))
    monkeypatch.setattr(f.capture, 'begin_prepare', lambda *a: prepares.append(a))
    monkeypatch.setattr(f.store, 'commit_window', lambda *a: commits.append(a))
    monkeypatch.setattr(f.worker.wake, 'wait', lambda *a: None)
    f.worker.loop()
    assert len(observed) == 1
    assert not prepares and not commits
    assert f.store.chunk(queued)['status'] == 'paused'
    assert f.worker.current_chunk is None
    assert quiet[-1] in {r['cid'] for r in f.compact_rows()}


@pytest.mark.parametrize('fault', ['expired_deadline', 'steps_exhausted', 'cancelled', 'old_generation'])
def test_original_budget_cancel_and_generation_refuse_before_mutation(fresh_gc, fault):
    f = fresh_gc; f.add(3); before = f.snapshot(); rows = f.compact_rows()
    op = cells.Operation('compact_gc', (), f.owner._epoch)
    budget = cells.Budget.for_owner(op)
    assert budget.seconds == 2.0
    assert cells.SQL_STEPS == 150000 and cells.SQL_PROJECTION_BYTES == 262144
    if fault == 'expired_deadline':budget.deadline = time.monotonic() - 1.
    elif fault == 'steps_exhausted':budget.steps = 150001
    elif fault == 'cancelled':op.cancelled.set()
    else:op.epoch = object()
    result = f.collect(op=op, budget=budget)
    assert result.get('retired', 0) == 0, result
    assert_preserved(f, before, rows)


@pytest.mark.parametrize('fault', ['generation', 'cancel', 'worker_active', 'capture_active', 'deadline'])
def test_final_guard_rolls_back_deletes_on_late_change(fresh_gc, monkeypatch, fault):
    f = fresh_gc; f.add(3); before = f.snapshot(); rows = f.compact_rows(); changed = []
    op = cells.Operation('compact_gc', (), f.owner._epoch)
    budget = cells.Budget.for_owner(op)
    def after_delete(db, sql, parameters):
        if not sql.lstrip().upper().startswith('DELETE FROM NATIVE_CORE_COMMITS') or changed:return
        changed.append(1)
        if fault == 'generation':f.owner._epoch = object()
        elif fault == 'cancel':op.cancelled.set()
        elif fault == 'worker_active':f.worker.current_chunk = 'claimed-after-fixture-barrier'
        elif fault == 'capture_active':f.capture.active = True
        else:budget.deadline = time.monotonic() - 1.
    with probe_primary(f, monkeypatch, after=after_delete):result = f.collect(op=op, budget=budget)
    assert changed
    assert result.get('retired', 0) == 0, result
    assert_preserved(f, before, rows)


@pytest.mark.parametrize('target', ['candidate', 'frontier'])
def test_primary_digest_must_still_match_exact_retained_receipt(fresh_gc, target):
    f = fresh_gc; ids = f.add(2)
    with f.store.db() as db:
        db.execute('UPDATE chunks SET digest=? WHERE id=?',
                   ('f' * 64, ids[0] if target == 'candidate' else ids[-1]))
    before = f.snapshot(); rows = f.compact_rows(); result = f.collect()
    assert result['retired'] == 0, result
    assert_preserved(f, before, rows)


def test_lost_commit_ack_reconciles_absence_without_attribution_or_retry(fresh_gc, monkeypatch):
    f = fresh_gc; ids = f.add(3); before = f.snapshot(); commits = []
    def lost_ack(db, sql, parameters):
        if sql.strip().upper() == 'COMMIT':
            commits.append(1)
            raise sqlite3.OperationalError('SYNTHETIC lost COMMIT acknowledgement')
    with probe_primary(f, monkeypatch, after=lost_ack):result = f.collect()
    assert result['status'] == 'unknown' and result['retired'] == 0, result
    assert len(commits) == 1
    assert f.owner.health()['compact_gc']['retired'] == 0
    assert f.owner.health()['compact_gc']['uncertain_ids'] == 2
    assert {row['cid'] for row in f.compact_rows()} == {ids[-1]}
    assert f.snapshot() == before
    with probe_primary(f, monkeypatch) as (statements, opens):result = f.collect()
    assert result['status'] == 'reconciled' and result['reason'] == 'absent_after_unknown_commit', result
    assert result['retired'] == 0 and len(opens) == 1
    assert not any(sql.strip().upper().startswith('DELETE') for sql, _ in statements)
    assert f.owner.health()['compact_gc']['retired'] == 0
    assert f.owner.health()['compact_gc']['uncertain_ids'] == 0
    assert f.collect()['retired'] == 0
    assert f.snapshot() == before


def test_unknown_commit_with_present_rows_needs_separate_fresh_attempt(fresh_gc, monkeypatch):
    f = fresh_gc; f.add(3); before = f.snapshot(); old = f.compact_rows()
    def before_commit(db, sql, parameters):
        if sql.strip().upper() == 'COMMIT':
            raise sqlite3.OperationalError('SYNTHETIC failed before COMMIT')
    with probe_primary(f, monkeypatch, before=before_commit):result = f.collect()
    assert result['status'] == 'unknown' and result['retired'] == 0, result
    assert_preserved(f, before, old)
    with probe_primary(f, monkeypatch) as (statements, _):result = f.collect()
    assert result['status'] == 'reconciled' and result['reason'] == 'present_after_unknown_commit', result
    assert not any(sql.strip().upper().startswith('DELETE') for sql, _ in statements)
    assert_preserved(f, before, old)
    result = f.collect()
    assert result['status'] == 'retired' and result['retired'] == 2
    assert f.owner.health()['compact_gc']['retired'] == 2


@pytest.mark.parametrize('failure', ['primary_handler_cleanup', 'owner_handler_cleanup', 'postcommit_scan'])
def test_postcommit_cleanup_never_becomes_unattributed_deferred(fresh_gc, monkeypatch, failure):
    f = fresh_gc; ids = f.add(3); before = f.snapshot(); seen = []
    def cleanup():
        if not seen:
            seen.append(1)
            raise RuntimeError('SYNTHETIC postcommit handler cleanup failure')
    if failure == 'primary_handler_cleanup':
        with probe_primary(f, monkeypatch, cleanup=cleanup):result = f.collect()
    elif failure == 'owner_handler_cleanup':
        proxy = ConnectionProbe(f.optional, [], cleanup=cleanup)
        with monkeypatch.context() as patch:
            patch.setattr(f.owner, '_db', proxy); result = f.collect()
    else:
        original = f.owner._scan
        def scan(budget=None):
            answer = original(budget)
            seen.append(1)
            if len(seen) == 2:raise RuntimeError('SYNTHETIC postcommit scan failure')
            return answer
        with monkeypatch.context() as patch:
            patch.setattr(f.owner, '_scan', scan); result = f.collect()
    assert {row['cid'] for row in f.compact_rows()} == {ids[-1]}
    assert result['status'] in ('retired', 'unknown'), result
    assert f.snapshot() == before
    if result['status'] == 'unknown':
        assert result['retired'] == 0
        assert f.owner.health()['compact_gc']['retired'] == 0
        assert f.collect()['status'] == 'reconciled'
        assert f.owner.health()['compact_gc']['retired'] == 0
    else:
        assert result['retired'] == 2
        assert f.owner.health()['compact_gc']['retired'] == 2
    previous_count = f.owner.health()['compact_gc']['retired']
    assert f.collect()['retired'] == 0
    assert f.owner.health()['compact_gc']['retired'] == previous_count


def test_restart_does_not_infer_gc_retirement_from_absent_rows(fresh_gc, monkeypatch):
    f = fresh_gc; ids = f.add(3)
    def lost_ack(db, sql, parameters):
        if sql.strip().upper() == 'COMMIT':raise sqlite3.OperationalError('SYNTHETIC lost ack')
    with probe_primary(f, monkeypatch, after=lost_ack):assert f.collect()['status'] == 'unknown'
    # Reconstruct a fresh in-memory owner against the SAME disposable fixture,
    # never an old/user DB. There is no durable attribution journal to invent.
    previous = f.owner
    f.owner = cells.CellStore(previous.directory, f.authority, enabled=True,
        core_seam_enabled=True, compact_gc_enabled=True, qualification=simulated_profile(f.root))
    f.owner._db = f.optional; f.owner._ready.set(); f.owner._status = 'fixture_restart'
    f.owner._receipts = dict(previous._receipts)
    f.authority.owner = f.owner
    f.capture = Capture(f.owner, f.authority)
    f.worker.native_owned_cells = f.capture
    f.capture.bind_retention_worker(f.worker)
    before = f.snapshot(); result = f.collect()
    assert result['retired'] == 0
    assert f.owner.health()['compact_gc']['retired'] == 0
    assert f.owner.health()['compact_gc']['uncertain_ids'] == 0
    assert {row['cid'] for row in f.compact_rows()} == {ids[-1]}
    assert f.snapshot() == before


def test_actual_cap_metadata_work_and_indexed_cursor_are_bounded(fresh_gc, monkeypatch):
    f = fresh_gc
    f.add(core.MAX_COMPACTS)
    f.add(cells.MAX_RECEIPTS - core.MAX_COMPACTS, compacts=False)
    assert len(f.receipts) == 4096 and len(f.compact_rows()) == 2056
    before = f.snapshot(); bodies = []; original = core.read_commit
    def measured(db, slot_id, budget):
        result = original(db, slot_id, budget)
        if result is not None:bodies.append(len(canonical(result, core.COMPACT_BYTES)))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(core, 'read_commit', measured)
        with probe_primary(f, patch) as (statements, opens):result = f.collect()
    assert len(opens) == 1
    assert result['scanned'] == BATCH
    assert result['retired'] > 0, result
    assert len(bodies) <= 2 * BATCH
    assert sum(bodies) == result['body_bytes'] <= BODY_LIMIT
    budget = f.budgets[-1]
    assert 0 < budget.steps <= 150000
    assert sum(bodies) <= budget.sql_bytes <= 262144
    assert budget.seconds == 2.0 and budget.elapsed <= 2.0
    assert f.snapshot() == before
    selected = [(sql, args) for sql, args in statements
                if 'SELECT slot FROM native_core_commits WHERE slot>' in sql]
    frontier = [(sql, args) for sql, args in statements
                if 'SELECT slot,logical_end FROM native_core_commits' in sql]
    assert len(selected) == 1 and selected[0][1][1] == 8
    assert len(frontier) <= 8
    with f.store.db() as db:
        for sql, args in selected + frontier:
            plan = ' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + sql, args))
            assert 'INDEX' in plan and 'TEMP B-TREE' not in plan, plan
    assert live_bytes(f.root) <= FILE_LIMIT


def test_retained_reset_schema_dependencies_are_coarse_roots(fresh_gc):
    f = fresh_gc; ids = f.add(4)
    # Root closure must not dispatch on a proof schema or inspect PCM. This is
    # deliberately a malformed reset-marked metadata row: even an unreadable
    # retained proof cannot justify deleting its declared source dependencies.
    cell = f.ordinary_cell(ids[0], with_pcm=False)
    proof = canonical({'schema': core.RESET_SCHEMA, 'synthetic_corrupt_fixture': True})
    f.optional.execute('UPDATE cells SET proof=? WHERE cell_id=?', (proof, cell.cell_id))
    p = f.commits[ids[1]]['part']
    f.optional.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',
        (cell.cell_id, 1, ids[1], p['source_sha256'], p['receipt_sha256'],
         p['source_start'], p['source_end'], p['logical_start'], p['logical_end']))
    before = f.snapshot(); result = f.collect()
    assert result['retired'] == 1, result
    assert {row['cid'] for row in f.compact_rows()} == {ids[0], ids[1], ids[-1]}
    assert f.snapshot() == before


def test_quiet_predecessor_survives_then_real_worker_builds_future_seam(tmp_path, monkeypatch):
    from test_same_epoch_core_seams import RealHarness, wait
    # Real Store/Worker.loop/CoreSeams/CellStore machinery, existing fake native
    # response and ASR adapter only. No external native/model process is started.
    h = RealHarness(tmp_path, (), monkeypatch)
    try:
        h.settings.start_worker = True
        h.settings.native_owned_compact_gc_enabled = True
        h.owner.compact_gc_enabled = True
        h.capture.bind_retention_worker(h.worker)
        # Keep this deterministic barrier's one explicit owner-slot pass from
        # racing a second periodic pass; the collector itself is unmodified.
        monkeypatch.setattr(h.owner, '_schedule_compact_gc', lambda: False)
        h.add(31, False); h.add(31, False); h.add(32, True)
        original_claim = h.store.claim; collected = []; snapshots = []
        def user_output_hashes():
            with h.store.db() as db:
                primary = rows_hash(db, ('chunks', 'segments', 'speaker_turns', 'events'))
            with sqlite3.connect(h.owner.path) as db:
                sidecar = rows_hash(db, ('sources', 'cells', 'dependencies'))
            return primary, sidecar, {cid: file_hash(h.root / 'uploads' / f'source{i}.audio') for i, cid in enumerate(h.source_ids)}
        def between_claims():
            complete = [cid for cid in h.source_ids if h.store.chunk(cid)['status'] == 'completed']
            if len(complete) == 2 and not collected:
                h.settle()
                before = user_output_hashes()
                with h.owner._lock:
                    assert h.owner._slot is None
                    operation = cells.Operation('compact_gc', (), h.owner._epoch, h.owner._wake)
                    h.owner._slot = operation; h.owner._wake.set()
                result = wait(operation); collected.append(result)
                assert result['status'] == 'retired' and result['retired'] >= 1, result
                assert before == user_output_hashes()
                snapshots.append(before)
                rows = h.rows()
                assert not any(row['cid'] == h.source_ids[0] for row in rows)
                assert any(row['cid'] == h.source_ids[1] and row['logical_end'] == 62 * RATE for row in rows)
                # Future overlap is a newly returned synthetic native response;
                # it was not used to manufacture a prior selected-cell root.
                h.activity = [[int(61.25 * RATE), int(62.75 * RATE)]]
                h.markers = [(int(62.25 * RATE), 1, 'synthetic_future_1'),
                             (int(62.25 * RATE), 2, 'synthetic_future_2')]
            row = original_claim()
            if row is None:h.worker.stop_event.set()
            return row
        monkeypatch.setattr(h.store, 'claim', between_claims)
        monkeypatch.setattr(h.worker.wake, 'wait', lambda *args: None)
        # Synchronous execution of the exact supported loop keeps deterministic
        # barriers; the existing CellStore owner is the only background thread.
        h.worker.loop(); h.settle()
        assert len(collected) == 1 and len(snapshots) == 1
        matching = [(item, doc) for item, doc in h.cells()
                    if doc['geometry']['seam_frame'] == 62 * RATE]
        assert len(matching) == 1, (h.capture.health(), h.rows())
        _, proof = matching[0]
        assert [c['part']['chunk_id'] for c in proof['commits']] == h.source_ids[1:]
        assert core.validate_document(canonical(proof))[0] == proof
        assert proof['coverage']['adds_primary_audio'] is False
        assert all(h.store.chunk(cid)['status'] == 'completed' for cid in h.source_ids)
        live_bytes(tmp_path)
    finally:
        h.close()
        assert not h.owner._thread.is_alive()
        live_bytes(tmp_path)


def test_scheduler_uses_existing_slot_and_completion_cadence(fresh_gc):
    f = fresh_gc; f.add(3); owner = f.owner
    counts = owner._retention_snapshot[2]['compacts']
    for count in (None, 0, 1):
        counts['occupied'] = count
        assert not owner._schedule_compact_gc()
    counts['occupied'] = 3
    for flag in ('compact_gc_enabled', '_admission_closed'):
        original = getattr(owner, flag)
        setattr(owner, flag, not original)
        assert not owner._schedule_compact_gc()
        setattr(owner, flag, original)
    owner._reconcile.set()
    assert not owner._schedule_compact_gc()
    owner._reconcile.clear()
    assert owner._schedule_compact_gc()
    op = owner._slot
    assert isinstance(op, cells.Operation) and op.kind == 'compact_gc'
    assert op.epoch is owner._epoch and op.payload == ()
    assert not owner._schedule_compact_gc() and owner._slot is op
    result = owner._collect_compacts(op, cells.Budget.for_owner(op))
    assert result['retired'] == 2
    owner._slot = None; op.done.set(); op.slot_released.set()
    assert owner._compact_gc_next > time.monotonic()
    assert not owner._schedule_compact_gc() and owner._slot is None
