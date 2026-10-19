"""Finite, synthetic primary-cache contracts; no acoustic/endurance claim.

This file is source-only until the independently frozen execution envelope is
approved. The fixture uses real Store/Worker/Capture/owner/coordinator identities,
real SQLite and bounded production collectors. It never starts a worker or model.
Cold cap fillers are explicitly unqualified metadata, never deletion evidence.
Eligible cohorts have fresh, small WAVs; no original/user audio is opened.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
import wave

import pytest

from audio_backend import moss_automatic as auto
from audio_backend import native_owned_cell_store as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_overlap as overlap
from audio_backend import recording_primary_provenance as pp
from audio_backend import recording_speaker_history as history
from audio_backend.moss_handy_jobs import MossJobs
from audio_backend.moss_result_store import MossResultStore
from audio_backend.native_execution_permit import NativeExecutionPermit
from audio_backend.native_owned_cell_capture import Capture, PrimaryAuthority, signature
from audio_backend.native_owned_cell_jobs import CellJobsSource
from audio_backend.native_owned_cells import RATE, SourceSpan, canonical, sha
from audio_backend.native_owned_support_cache import NativeOwnedSupportCache
from audio_backend.settings import Settings, primary_only_enabled
from audio_backend.storage import Store
from audio_backend.worker import Worker
from test_moss_automatic_integration import seed_intent
from test_moss_handy_jobs import ManualThread
from test_native_owned_cell_store import simulated_profile
from test_native_owned_cells import synthetic_cell
from test_native_owned_compact_gc import file_hash, probe_primary
from test_native_owned_receipt_gc import FreshReceipts, fixture_bytes, probe_sidecar
from test_native_owned_support import synthetic_provenance
from test_primary_only_configuration import no_external_execution

# Captures the independently installed gate guard, before the narrower fixture
# forbids ordinary subprocess calls. Used once, only by the explicit A1 child.
_OLD_READER_POPEN = subprocess.Popen


POLICY = 'primary-cache-retirement-v1'
Q = 168000
CAPTURE = 'synthetic-primary-cache'
HELD_STATES = ('provisional', 'ready', 'running', 'cancelled', 'terminal',
    'claim_unknown', 'append_unknown', 'cleanup_unknown')
PRESERVED = ('sessions', 'chunks', 'segments', 'speaker_turns', 'events',
    'stream_chunks', 'stream_time_index', 'recording_batches', 'recording_pages',
    'recording_sources', 'recording_work', 'recording_ranges', pp.TABLE, pp.USAGE,
    auto.TABLE, auto.USAGE, history.CERTIFICATES, history.ATTEMPTS)


def rows_hash(db, names):
    """Exact values, including WITHOUT ROWID history tables, in stable order."""
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        columns = len(db.execute('PRAGMA table_info("' + name + '")').fetchall())
        assert columns > 0
        order = ','.join(str(index) for index in range(1, columns + 1))
        for row in db.execute('SELECT * FROM "' + name + '" ORDER BY ' + order):
            for value in tuple(row):
                raw = value if type(value) is bytes else repr(value).encode()
                digest.update(len(raw).to_bytes(8, 'big')); digest.update(raw)
    return digest.hexdigest()


def primary_settings(root, **changes):
    options = dict(data_dir=root, disk_reserve_bytes=0,
        recording_disk_reserve_bytes=0, start_worker=True, window_seconds=10.5,
        context_seconds=0., asr_backend='sensevoice', diarization_backend='nemotron',
        inference_device='cpu', overlap_backend='none',
        native_diarization_model='synthetic-native-model',
        native_diarization_library='synthetic-native-library',
        native_owned_support_enabled=True, native_owned_cells_enabled=True,
        native_owned_core_seams_enabled=True, native_owned_bounded_core_support_enabled=True,
        native_owned_compact_gc_enabled=True, native_owned_receipt_gc_enabled=True,
        lexical_evidence_enabled=True, recording_speaker_links_enabled=True,
        recording_primary_observation_policy=history.POLICY,
        recording_speaker_model_path=str(root / 'ABSENT-PRIMARY-SPEAKER-MODEL'),
        moss_jobs_enabled=False, moss_automatic_enabled=False, moss_jobs_model_path=None)
    options.update(changes)
    return Settings(**options)


class PrimaryRetirement(FreshReceipts):
    """Manual real owner; synthetic SQL cohorts avoid inference and timers."""
    def __init__(self, root):
        self.root = root
        (root / 'uploads').mkdir(parents=True)
        (root / 'tmp').mkdir()
        self.settings = primary_settings(root)
        self.store = Store(root / 'primary.sqlite3', self.settings)
        self.sid = self.store.create_session('SYNTHETIC exclusive primary')['id']
        self.authority = PrimaryAuthority(self.store)
        self.owner = cells.CellStore(root / 'native-owned-cells', self.authority,
            enabled=True, core_seam_enabled=True, compact_gc_enabled=True,
            receipt_gc_enabled=True, qualification=simulated_profile(root))
        self.authority.owner = self.owner
        self.owner._open()
        self.optional = self.owner._db
        self.owner._ready.set()
        self.owner._status = 'fixture_manual_production_open'
        self.capture = Capture(self.owner, self.authority)
        self.worker = Worker(self.store, self.settings)
        self.worker.native_owned_cells = self.capture
        self.worker.native_owned_support_cache = NativeOwnedSupportCache(self.store)
        self.permit = NativeExecutionPermit(self.settings)
        self.settings._native_execution_permit = self.permit
        self.adapter = CellJobsSource(self.owner, self.store)
        self.results = MossResultStore(self.store)
        self.jobs = MossJobs(self.store, self.worker.native_owned_support_cache,
            self.permit, cell_source=self.adapter, result_store=self.results,
            _thread_factory=ManualThread)
        self.automatic = auto.Automatic(self.store, self.results,
            capture=self.capture, jobs=self.jobs)
        self.jobs.automatic = self.automatic
        self.store.moss_automatic = self.worker.moss_automatic = self.automatic
        self.capture.bind_retention_worker(self.worker)
        self.capture.core_seams.work_pending = False
        self.recordings = overlap.RecordingOverlap(self.store)
        self.worker.recordings = self.recordings
        self.receipts = {}; self.commits = {}; self.paths = {}; self.budgets = []
        self.extra_paths = []; self.batches = {}; self.next_id = 0
        self.bid = None; self.page_number = 0; self.position = 0
        assert primary_only_enabled(self.settings)
        assert overlap.primary_cache_capable(self.store)

    def create(self):
        page = self.recordings.create_page(self.sid, CAPTURE, 1, 900007)
        with self.store.db() as db:
            batch = self.recordings._batch(db, self.sid, CAPTURE)
        self.bid = batch['id']
        assert batch['primary_cache_policy'] == POLICY
        assert batch['primary_cache_store_key'] is None
        return page

    def marker(self):
        with self.store.db() as db:
            return tuple(db.execute('''SELECT primary_cache_policy,primary_cache_store_key
                FROM recording_batches WHERE session_id=?''', (self.sid,)).fetchone())

    def recording(self, count=31, *, compacts=False, gap=False, bind=True,
                  metadata_only=False, status='completed'):
        """Canonical completed mappings; direct SQL is labeled fixture setup.

        The marker is admitted by create_page. This setup uses its permitted
        one-time key transition, not an UPDATE that adopts an unmarked batch.
        Production publication/binding is covered separately by pipeline tests.
        """
        if self.bid is None:self.create()
        origin = 900007; stream = 'recording.' + self.bid; ids = []
        with self.store.db() as db, self.optional:
            if bind and not gap:
                db.execute('''UPDATE recording_batches SET primary_cache_store_key=?
                    WHERE id=? AND primary_cache_store_key IS NULL''',
                    (self.owner.retention_store_key, self.bid))
            for offset in range(0, count, 28):
                number = self.page_number + 1
                end_position = self.position + min(28, count - offset)
                old = db.execute('SELECT id FROM recording_pages WHERE batch_id=? AND page_number=?',
                    (self.bid, number)).fetchone()
                page = old[0] if old else self.bid + '-fixture-' + str(number)
                if old:
                    db.execute("UPDATE recording_pages SET state='published',released=1,start_frame=?,end_frame=? WHERE id=?",
                        (origin + self.position * Q, origin + end_position * Q, page))
                else:
                    db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,
                        start_frame,end_frame,created) VALUES(?,?,?,'published',1,?,?,0)''',
                        (page, self.bid, number, origin + self.position * Q, origin + end_position * Q))
                for position in range(self.position, end_position):
                    cid = f'a-primary-{self.next_id:05d}'; self.next_id += 1
                    path = self.root / 'uploads' / (cid + '.audio')
                    if metadata_only:
                        digest = 'c' * 64; size = 44 + 2 * Q
                        sig = (1, self.next_id, size, 1, 1)
                    else:
                        with wave.open(str(path), 'wb') as target:
                            target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
                            target.writeframes(b'\x01\x00' * Q)
                        digest = file_hash(path); size = path.stat().st_size
                        sig = signature(path.stat()); self.paths[cid] = path
                    receipt = cells.Receipt(self.sid, cid, digest, Q, 44, size, sig,
                        position * Q / RATE, stream)
                    logical_start = position * Q
                    epoch_index = logical_start // (300 * RATE)
                    part_frames = min(Q, (epoch_index + 1) * 300 * RATE - logical_start)
                    epoch = f'{epoch_index + 1:032x}'
                    native = {'synthetic': True, 'epoch_id': epoch, 'epoch_index': epoch_index}
                    cut = {'nominal': Q / RATE, 'actual': part_frames / RATE, 'reason': 'fixed_or_final'}
                    evidence = {'chunk_id': cid, 'start': 0, 'end': part_frames, 'native': native, 'core_cut': cut}
                    metadata = json.dumps({'native_diarization': native, 'core_cut': cut,
                        'fixture': 'SYNTHETIC metadata; no inference'})
                    db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
                        timeline_start,status,next_offset,created,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (cid, self.sid, cid, digest, str(path), Q / RATE, position * Q / RATE,
                         status, Q / RATE if status == 'completed' else 0., position, metadata))
                    db.execute('''INSERT INTO stream_chunks(chunk_id,session_id,stream_id,position,
                        stream_final,upload_stream_final) VALUES(?,?,?,?,0,0)''',
                        (cid, self.sid, stream, position))
                    db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
                        capture_start_frame,native_receipt_status,native_receipt,receipt_id,receipt_state,
                        page_id,file_signature) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (cid, self.bid, position, Q, size, origin + position * Q,
                         'source_receipt_capacity' if gap else 'receipt_retained', receipt.encoded,
                         receipt.receipt_id, 'gap' if gap else 'durable', page, canonical(list(sig)).decode()))
                    source = 'source-' + cid
                    db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
                        frames,bytes,source_start_frame,format,created,page_id) VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
                        (source, self.bid, source, digest, str(path) + '.original', Q, size,
                         origin + position * Q, 'wav-pcm16le', page))
                    db.execute('INSERT INTO recording_ranges VALUES(?,?,?,?,?,?,?,?)',
                        (self.bid, source, cid, 0, origin + position * Q, 0, Q, 'owner'))
                    db.execute('INSERT INTO segments(chunk_id,start,end,text,speaker_status) VALUES(?,0,?,?,?)',
                        (cid, Q / RATE, 'SYNTHETIC primary ' + cid, 'unknown'))
                    if not gap:
                        self.optional.execute('INSERT INTO sources VALUES(?,?,?,?)',
                            (cid, self.sid, digest, receipt.encoded))
                        self.owner._receipts[cid] = self.receipts[cid] = receipt
                    part = SourceSpan(self.sid, cid, receipt.receipt_id, digest, Q,
                        0, part_frames, logical_start, logical_start + part_frames)
                    commit = {'scope': {'session_id': self.sid, 'kind': 'stream', 'key': stream,
                        'origin': (0.).hex(), 'run': self.capture.core_seams.run, 'reset': epoch_index + 1,
                        'epoch': epoch, 'index': epoch_index}, 'part': asdict(part),
                        'native_sha256': sha(native), 'cut_sha256': sha(cut),
                        'evidence_sha256': sha(evidence), 'runtime_sha256': sha(synthetic_provenance())}
                    self.commits[cid] = commit
                    if compacts:self.put_commit(db, commit)
                    ids.append(cid)
                self.position = end_position; self.page_number = number
            db.execute('UPDATE recording_batches SET capture_end_frame=? WHERE id=?',
                (origin + self.position * Q, self.bid))
        fixture_bytes(self.root)
        return ids

    def put_commit(self, db, commit, *, state='none', pending=None):
        core.validate_commit(commit)
        db.execute('''INSERT INTO native_core_commits(slot,sid,cid,scope_key,logical_end,
            body,pending,state,reason) VALUES(?,?,?,?,?,?,?,?,?)''',
            (core.slot(commit), commit['part']['session_id'], commit['part']['chunk_id'],
             core.scope_key(commit['scope']), commit['part']['logical_end'],
             canonical(commit, core.COMPACT_BYTES), pending, state, 'SYNTHETIC fixture'))

    def preserved(self):
        with self.store.db() as db:primary = rows_hash(db, PRESERVED)
        return (primary, rows_hash(self.optional, ('cells', 'dependencies')),
            {cid: file_hash(path) for cid, path in self.paths.items()})

    def qualified(self, cid, key=None):
        with self.store.db() as db:
            db.execute('BEGIN')
            return overlap.primary_cache_qualified(self.store, db, self.sid, cid,
                self.owner.retention_store_key if key is None else key)

    def compact_cycle(self, turns=16):
        return [self.collect() for _ in range(turns)]

    def collect(self, *, budget=None, op=None):
        assert self.owner._slot is None
        op = op or cells.Operation('compact_gc', (), self.owner._epoch)
        budget = budget or cells.Budget.for_owner(op)
        self.owner._slot = op; self.budgets.append(budget)
        try:
            result = self.owner._collect_compacts(op, budget)
            assert 0 <= result['scanned'] <= 8 and 0 <= result['retired'] <= 8
            assert budget.sql_bytes <= cells.SQL_PROJECTION_BYTES and budget.steps <= 150000
            fixture_bytes(self.root)
            return result
        finally:
            self.owner._slot = None; op.done.set(); op.slot_released.set()

    def close(self):
        assert self.jobs._primary_pending is None
        self.jobs._closed = True
        self.worker.native_owned_support_cache.close()
        super().close()


@pytest.fixture
def primary_cache(tmp_path):
    value = PrimaryRetirement(tmp_path)
    try:yield value
    finally:value.close()


def usage(store):
    with store.db() as db:
        return [tuple(r) for r in db.execute(f'''SELECT scope,packets,charge,cache_scope_charge
            FROM {pp.USAGE} ORDER BY scope''')]


def schema_snapshot(store):
    with store.db() as db:
        return [tuple(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name")]


def old_exact_reader(db):
    """The predecessor init_schema's exact validator, using unchanged old DDL.

    Only this bounded validator is copied; no unfrozen external package is
    imported. It is run on both the accepted old shape and migrated shape.
    """
    names = tuple(pp.DDL)
    rows = db.execute('SELECT name,sql FROM sqlite_master WHERE name IN ('
        + ','.join('?' for _ in names) + ')', names).fetchall()
    if len(rows) != len(names) or any(row['sql'] != pp.DDL[row['name']] for row in rows):
        raise ValueError('Unknown primary provenance schema; migration is not supported')


def test_A1_exact_migration_and_old_usage_reader_refusal(tmp_path):
    from test_recording_primary_provenance import setup, result_for, get_packet
    legacy, chunk = setup(tmp_path)
    # Store.chunk deliberately omits the internal file path. Read only this
    # exact synthetic fixture row, bounded before returning the path bytes.
    with legacy.db() as db:
        row = db.execute('''SELECT CASE WHEN typeof(path)='text'
            AND length(CAST(path AS BLOB)) BETWEEN 1 AND 4096 THEN path ELSE NULL END
            FROM chunks WHERE id=? AND session_id=?''', (chunk['id'], chunk['session_id'])).fetchone()
    assert row is not None and row[0] is not None
    audio = Path(row[0])
    assert audio.resolve().is_relative_to(tmp_path.resolve())
    with wave.open(str(audio), 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
        target.writeframes(b'\x01\x00' * (4 * RATE))
    assert legacy.commit_window(chunk, 0, 2, result_for(chunk))
    packet = get_packet(legacy, chunk['id']); assert packet is not None
    with legacy.db() as db:
        old = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (pp.USAGE,)).fetchone()[0]
        old_exact_reader(db)
        assert 'cache_scope_charge' not in old
        assert 'primary_cache_policy' not in {r['name'] for r in db.execute('PRAGMA table_info(recording_batches)')}
        old_usage = [tuple(r) for r in db.execute(f'SELECT scope,packets,charge FROM {pp.USAGE} ORDER BY scope')]
    migrated = Store(legacy.path, primary_settings(tmp_path))
    with migrated.db() as db:
        new = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (pp.USAGE,)).fetchone()[0]
        assert 'cache_scope_charge' in new and new != old
        with pytest.raises(ValueError, match='Unknown primary provenance schema'):
            old_exact_reader(db)
        assert usage(migrated) == [row + (0,) for row in old_usage]
    assert get_packet(migrated, chunk['id']) == packet
    # A zero-packet marked scope exercises predecessor accounting refusal too.
    # This is explicit SQL fixture population; real marker admission is A3.
    marked_sid = migrated.create_session('SYNTHETIC old-reader zero-packet scope')['id']
    with migrated.db() as db:
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,created,mode,
            capture_origin_frame,capture_end_frame,primary_cache_policy)
            VALUES('old-reader-marker',?,'synthetic-old-reader',0,'sealed_pages',0,0,?)''',
            (marked_sid, POLICY))
    assert usage(migrated)[0][3] == 512
    before = schema_snapshot(migrated)
    Store(migrated.path, Settings(data_dir=tmp_path, start_worker=False))
    assert schema_snapshot(migrated) == before
    with migrated.db() as db:
        names = tuple(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"))
        logical_before = rows_hash(db, names)
    audio_before = file_hash(audio); charged = usage(migrated)
    script = Path(os.environ['PRIMARY_CACHE_OLD_READER_SCRIPT']).resolve(strict=True)
    old_source = Path(os.environ['PRIMARY_CACHE_OLD_SOURCE']).resolve(strict=True)
    evidence = Path(os.environ['PRIMARY_CACHE_OLD_READER_EVIDENCE'])
    assert not evidence.exists()
    argv = [sys.executable, '-I', '-B', str(script), str(old_source), str(migrated.path), str(evidence)]
    child = _OLD_READER_POPEN(argv, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
    try:
        stdout, stderr = child.communicate(timeout=10.)
    except BaseException:
        child.kill(); child.wait(timeout=2.)
        raise
    assert child.wait(timeout=2.) == 0, (stdout, stderr)
    assert stdout == b'' and stderr == b''
    report = json.loads(evidence.read_text())
    assert report['status'] == 'expected_old_reader_refusal'
    assert report['error_type'] == 'ValueError'
    assert report['error'] == 'Unknown primary provenance schema; migration is not supported'
    assert Path(report['interpreter_resolved']) == Path(sys.executable).resolve()
    assert report['interpreter_sha256'] == file_hash(Path(sys.executable))
    assert report['source_tree'] == '9931b72ad2cab809b9fa7338c5f6c44059360b72'
    assert Path(report['source_path']) == old_source
    assert {'audio_backend.storage', 'audio_backend.settings'} <= set(report['module_origins'])
    for name, origin in report['module_origins'].items():
        path = Path(origin['path'])
        if name == 'audio_backend' or name.startswith('audio_backend.'):
            assert path.is_relative_to(old_source)
        else:
            stdlib = Path(sys.base_prefix) / f'lib/python{sys.version_info.major}.{sys.version_info.minor}'
            runtime = old_source.parent.parent / 'dev53-runtime-restoration-20261018/runtime/lib/python3.12/site-packages'
            assert (path == script or path.is_relative_to(runtime)
                or path.is_relative_to(stdlib) and not path.is_relative_to(stdlib / 'site-packages'))
        assert file_hash(path) == origin['sha256']
    assert report['no_model_runtime_import'] is True
    assert report['no_model_child_or_network'] is True
    assert report['no_app_or_worker_import'] is True
    report.update(positive_original_wait=True, returncode=child.returncode)
    evidence.write_text(json.dumps(report, sort_keys=True) + '\n')
    assert schema_snapshot(migrated) == before
    with migrated.db() as db:assert rows_hash(db, names) == logical_before
    assert get_packet(migrated, chunk['id']) == packet and usage(migrated) == charged
    assert file_hash(audio) == audio_before


@pytest.mark.parametrize('fault', ['missing_guard', 'missing_prerequisite', 'partial_column', 'usage_shape'])
def test_A1_partial_schema_rejected_with_flags_off(tmp_path, fault):
    store = Store(tmp_path / 'primary.sqlite3', primary_settings(tmp_path))
    with store.db() as db:
        if fault == 'missing_guard':
            name = db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND sql LIKE '%primary_cache_policy%' ORDER BY name LIMIT 1").fetchone()[0]
            db.execute('DROP TRIGGER ' + name)
        elif fault == 'missing_prerequisite':
            db.execute('DROP INDEX ' + next(name for name, sql in auto.V2_SCHEMA.items() if sql.startswith('CREATE INDEX')))
        elif fault == 'partial_column':
            db.execute('ALTER TABLE recording_batches RENAME COLUMN primary_cache_store_key TO lost_cache_store_key')
        else:
            db.execute(f'ALTER TABLE {pp.USAGE} ADD COLUMN unrecognized INTEGER')
    before = schema_snapshot(store)
    with pytest.raises(ValueError):Store(store.path, Settings(data_dir=tmp_path, start_worker=False))
    assert schema_snapshot(store) == before


def test_A1_migration_failure_rolls_back_every_addition(tmp_path, monkeypatch):
    store = Store(tmp_path / 'primary.sqlite3', Settings(data_dir=tmp_path, start_worker=False))
    before = schema_snapshot(store); original = sqlite3.connect
    class Refuse(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith('CREATE TRIGGER') and 'primary_cache_policy' in sql:
                raise sqlite3.OperationalError('SYNTHETIC interrupted additive migration')
            return super().execute(sql, parameters)
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *a, **kw: original(*a, **kw, factory=Refuse))
        with pytest.raises(sqlite3.OperationalError):Store(store.path, primary_settings(tmp_path))
    assert schema_snapshot(store) == before


@pytest.mark.parametrize('field,value', [
    ('primary_cache_policy', None), ('primary_cache_store_key', 'f' * 64),
    ('id', 'replacement'), ('session_id', 'replacement'), ('mode', 'legacy'),
    ('retention_store_key', 'f' * 64), ('native_core_ordinal', 1)],
    ids=['policy', 'bound_key', 'batch_id', 'session_id', 'mode', 'moss_store_key', 'moss_ordinal'])
def test_A2_marker_identity_and_bound_key_are_immutable(primary_cache, field, value):
    f = primary_cache; f.recording(1)
    before = f.marker(); charged = usage(f.store)
    with pytest.raises(sqlite3.IntegrityError):
        with f.store.db() as db:
            db.execute(f'UPDATE recording_batches SET {field}=? WHERE id=?', (value, f.bid))
    assert f.marker() == before and usage(f.store) == charged


def test_A2_wrong_binding_and_unknown_source_never_qualify(primary_cache):
    f = primary_cache; cid, = f.recording(1)
    assert f.qualified(cid) is not None
    assert f.qualified(cid, 'f' * 64) is None
    assert f.qualified('missing') is None
    with f.store.db() as db:
        db.execute('BEGIN')
        assert overlap.primary_cache_qualified(f.store, db, 'wrong-session', cid,
            f.owner.retention_store_key) is None
    with f.store.db() as db:
        db.execute('BEGIN'); db.execute('PRAGMA defer_foreign_keys=ON')
        ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='primary_cache_batch_update'").fetchone()[0]
        db.execute('DROP TRIGGER primary_cache_batch_update')
        oversized = 'x' * 4096
        db.execute('UPDATE recording_batches SET id=? WHERE id=?', (oversized, f.bid))
        db.execute('UPDATE recording_work SET batch_id=? WHERE chunk_id=?', (oversized, cid))
        db.execute('UPDATE recording_pages SET batch_id=? WHERE batch_id=?', (oversized, f.bid))
        db.execute(ddl)
        def bounded_projection(cursor, row):
            assert 'batch_id' not in {column[0] for column in cursor.description}, 'Oversized batch payload was projected'
            return sqlite3.Row(cursor, row)
        db.row_factory = bounded_projection
        assert overlap.primary_cache_qualified(f.store, db, f.sid, cid, f.owner.retention_store_key) is None
        with cells.Budget().phase() as budget:
            assert overlap.receipt_gc_source(db, f.receipts[cid], budget) is None
        db.execute('ROLLBACK')


def test_A3_initial_gap_prefix_then_bound_durable_receipts_retire(primary_cache):
    f = primary_cache; gaps = f.recording(28, gap=True, bind=False)
    assert f.marker() == (POLICY, None)
    durable = f.recording(31)
    assert f.qualified(durable[0]) is not None
    preserved = f.preserved(); results = f.cycle(24)
    assert sum(row['retired'] for row in results) > 0, results
    assert durable[0] not in f.source_rows()
    assert not set(gaps) & set(f.source_rows())
    assert f.preserved() == preserved


@pytest.mark.parametrize('write', ['intent_insert', 'intent_update', 'dependency_insert',
    'dependency_update', 'core_pending', 'core_published', 'core_qualification'])
def test_A4_durable_guards_reject_direct_moss_ownership(primary_cache, write):
    f = primary_cache; ids = f.recording(2, compacts=True)
    sid = f.store.create_session('SYNTHETIC legacy guard control')['id']
    with f.store.db() as db:
        intent = seed_intent(db, sid, 770, state='queued')
        if write == 'dependency_update':
            db.execute(f'INSERT INTO {auto.DEPENDENCIES} VALUES(?,1,0,?,NULL)',
                (intent['seq'], 'unrelated-source'))
    before = f.preserved(); compact = f.compact_rows()
    with pytest.raises(sqlite3.IntegrityError):
        with f.store.db() as db:
            if write == 'intent_insert':seed_intent(db, f.sid, 771, cids=(ids[0],))
            elif write == 'intent_update':db.execute(f'UPDATE {auto.TABLE} SET cid1=? WHERE seq=?', (ids[0], intent['seq']))
            elif write == 'dependency_insert':db.execute(f'INSERT INTO {auto.DEPENDENCIES} VALUES(?,1,0,?,NULL)', (intent['seq'], ids[0]))
            elif write == 'dependency_update':db.execute(f'UPDATE {auto.DEPENDENCIES} SET cid=? WHERE intent_seq=?', (ids[0], intent['seq']))
            elif write == 'core_pending':db.execute("UPDATE native_core_commits SET state='pending',pending=? WHERE cid=?", (b'{}', ids[0]))
            elif write == 'core_published':db.execute("UPDATE native_core_commits SET state='published' WHERE cid=?", (ids[0],))
            else:db.execute('UPDATE native_core_commits SET recording_batch_id=?,commit_ordinal=1,retention_store_key=? WHERE cid=?',
                (f.bid, f.owner.retention_store_key, ids[0]))
    assert f.preserved() == before and f.compact_rows() == compact
    if write == 'intent_insert':
        from audio_backend.moss_handy_profile import NAME
        from audio_backend.native_owned_support_cache import ReadError
        for origin in ('rolling_support', 'durable_cell'):
            with pytest.raises(ReadError) as error:
                f.jobs._submit(origin, f.sid, ids[0] if origin == 'rolling_support' else 'd' * 64,
                    {'jobs_epoch': f.jobs.jobs_epoch, 'profile': NAME}, 'guard-' + origin, None)
            assert error.value.reason == 'primary_cache_exclusive_recording'
        assert f.jobs._records == [] and f.jobs._live is None
        assert f.worker.native_owned_support_cache._dispatch is None
    elif write == 'dependency_insert':
        cell = synthetic_cell(sid=f.sid, cid=ids[0], receipt_id=f.receipts[ids[0]].receipt_id)
        op = cells.Operation('publish', (cell,), f.owner._epoch)
        budget = cells.Budget.for_owner(op)
        with budget.phase():
            assert f.owner._cell_current(op, cell, budget) is False
            with pytest.raises(cells.Unavailable):f.owner._publish(op, budget)
        with cells.Budget.for_owner(op).phase() as final_budget:
            with pytest.raises(cells.Unavailable):f.owner._insert_cell(op, cell, final_budget, False)
        assert f.optional.execute('SELECT COUNT(*) FROM cells').fetchone()[0] == 0


def test_A5_zero_packet_marker_charge_erasure_order_and_rollback(primary_cache):
    f = primary_cache; f.create()
    assert usage(f.store) == [('', 0, 512, 512), (f.bid, 0, 512, 512)]
    with f.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        assert db.execute('SELECT deleted FROM sessions WHERE id=?', (f.sid,)).fetchone()[0] == 0
        overlap.erase(db, f.sid)
        assert [tuple(r) for r in db.execute(f'SELECT scope,packets,charge,cache_scope_charge FROM {pp.USAGE}')] == [('', 0, 0, 0)]
        db.execute('ROLLBACK')
    assert f.marker() == (POLICY, None)
    assert usage(f.store) == [('', 0, 512, 512), (f.bid, 0, 512, 512)]
    from audio_backend.lifecycle import delete_session
    delete_session(f.store, f.sid, delete_file=lambda path: None)
    assert usage(f.store) == [('', 0, 0, 0)]
    delete_session(f.store, f.sid, delete_file=lambda path: None)
    assert usage(f.store) == [('', 0, 0, 0)]


@pytest.mark.parametrize('refusal', ['recording_quota', 'global_quota', 'disk_headroom', 'capacity'])
def test_A5_optional_marker_refusal_is_permanently_unmarked(primary_cache, monkeypatch, refusal):
    f = primary_cache; original_headroom = pp.headroom; original_admission = pp.primary_cache_admission
    if refusal == 'recording_quota':f.settings.recording_primary_provenance_recording_bytes = 1
    elif refusal == 'global_quota':f.settings.recording_primary_provenance_global_bytes = 1
    else:
        target = 'headroom' if refusal == 'disk_headroom' else 'primary_cache_admission'
        monkeypatch.setattr(pp, target, lambda *a: (_ for _ in ()).throw(pp.Refused(refusal)))
    page = f.recordings.create_page(f.sid, CAPTURE, 1, 900007)
    with f.store.db() as db:
        batch = f.recordings._batch(db, f.sid, CAPTURE); f.bid = batch['id']
        assert tuple(db.execute('SELECT primary_cache_policy,primary_cache_store_key FROM recording_batches').fetchone()) == (None, None)
        db.execute("UPDATE recording_pages SET state='published',released=1,end_frame=start_frame WHERE id=?", (page['page_id'] if 'page_id' in page else page['id'],))
    monkeypatch.setattr(pp, 'headroom', original_headroom)
    monkeypatch.setattr(pp, 'primary_cache_admission', original_admission)
    f.settings.recording_primary_provenance_recording_bytes = 128 * 1024**2
    f.settings.recording_primary_provenance_global_bytes = 512 * 1024**2
    f.recordings.create_page(f.sid, CAPTURE, 2)
    assert f.marker() == (None, None) and usage(f.store) == [('', 0, 0, 0)]
    with pytest.raises(sqlite3.IntegrityError):
        with f.store.db() as db:db.execute('UPDATE recording_batches SET primary_cache_policy=? WHERE id=?', (POLICY, f.bid))


@pytest.mark.parametrize('fault', ['accounting_corrupt', 'baseline_headroom', 'insert_failure', 'unknown_insert_ack'])
def test_A5_hard_marker_failures_never_retry_as_unmarked(primary_cache, monkeypatch, fault):
    f = primary_cache; writes = []; connect = sqlite3.connect
    if fault == 'accounting_corrupt':
        with f.store.db() as db:db.execute(f"UPDATE {pp.USAGE} SET charge=512,cache_scope_charge=512 WHERE scope=''")
    elif fault == 'baseline_headroom':
        monkeypatch.setattr(pp, 'headroom', lambda *a: (_ for _ in ()).throw(OSError('SYNTHETIC baseline failure')))
    class Fault(sqlite3.Connection):
        wrote = False
        def execute(self, sql, parameters=()):
            if sql.lstrip().upper().startswith('INSERT INTO RECORDING_BATCHES'):
                writes.append(1); self.wrote = True
                if fault == 'insert_failure':raise sqlite3.OperationalError('SYNTHETIC insertion failure')
            return super().execute(sql, parameters)
        def commit(self):
            super().commit()
            if self.wrote and fault == 'unknown_insert_ack':raise sqlite3.OperationalError('SYNTHETIC lost marked INSERT ACK')
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *a, **kw: connect(*a, **kw, factory=Fault))
        with pytest.raises((ValueError, OSError, sqlite3.Error)):
            f.recordings.create_page(f.sid, CAPTURE, 1, 900007)
    with f.store.db() as db:
        rows = list(db.execute('SELECT primary_cache_policy,primary_cache_store_key FROM recording_batches'))
    if fault == 'unknown_insert_ack':
        assert [tuple(row) for row in rows] == [(POLICY, None)] and writes == [1]
        f.recordings.create_page(f.sid, CAPTURE, 1, 900007)
        assert usage(f.store)[0] == ('', 0, 512, 512)
    else:assert rows == [] and len(writes) <= 1


@pytest.mark.parametrize('mutation', ['pending', 'digest', 'unreleased', 'stream', 'missing_parent'])
def test_A6_dirty_or_orphan_mapping_is_protected(primary_cache, mutation):
    f = primary_cache; ids = f.recording(compacts=mutation == 'pending')
    with f.store.db() as db:
        if mutation == 'pending':
            # Corrupt a different compact's declared sid while its canonical
            # chunk still belongs to this marked session. Restore exact guards
            # before invoking collectors; this is not a relaxed schema test.
            ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='primary_cache_core_update'").fetchone()[0]
            db.execute('DROP TRIGGER primary_cache_core_update')
            db.execute("UPDATE native_core_commits SET sid='wrong-session',pending=?,state='pending' WHERE cid=?", (b'{}', ids[1]))
            db.execute(ddl)
        elif mutation == 'digest':db.execute('UPDATE chunks SET digest=? WHERE id=?', ('f' * 64, ids[0]))
        elif mutation == 'unreleased':db.execute("UPDATE recording_pages SET state='pending_receipts' WHERE batch_id=? AND page_number=1", (f.bid,))
        elif mutation == 'stream':db.execute("UPDATE stream_chunks SET stream_id='wrong' WHERE chunk_id=?", (ids[0],))
        else:db.execute('DELETE FROM recording_work WHERE chunk_id=?', (ids[0],))
    assert (f.qualified(ids[0]) is not None) is (mutation == 'pending')
    before = f.source_rows(); preserved = f.preserved(); f.cycle(8)
    assert ids[0] in f.source_rows() and f.source_rows()[ids[0]] == before[ids[0]]
    if mutation == 'pending':
        compact_before = f.compact_rows()
        for _ in range(8):assert f.collect()['retired'] == 0
        assert f.compact_rows() == compact_before
    assert f.preserved() == preserved


def cold_compacts(f):
    sid = f.store.create_session('SYNTHETIC unqualified compact cap metadata')['id']
    count = core.MAX_COMPACTS - len(f.compact_rows())
    with f.store.db() as db:
        for index in range(count):
            cid = f'z-cold-compact-{index:05d}'
            db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,
                timeline_start,status,next_offset,created,metadata) VALUES(?,?,?,?,?,.001,0,'completed',.001,0,'{}')''',
                (cid, sid, cid, 'c' * 64, '/synthetic/absent-cold-metadata'))
            part = SourceSpan(sid, cid, 'a' * 64, 'c' * 64, 16, 0, 16, 0, 16)
            commit = {'scope': {'session_id': sid, 'kind': 'upload', 'key': cid,
                'origin': (0.).hex(), 'run': f.capture.core_seams.run, 'reset': 1,
                'epoch': 'a' * 32, 'index': 0}, 'part': asdict(part),
                'native_sha256': 'b' * 64, 'cut_sha256': 'b' * 64,
                'evidence_sha256': 'b' * 64, 'runtime_sha256': 'b' * 64}
            f.put_commit(db, commit)
    assert len(f.compact_rows()) == 2056
    return {r['slot']: r for r in f.compact_rows() if r['sid'] == sid}


def cold_receipts(f):
    sid = f.store.create_session('SYNTHETIC unqualified receipt cap metadata')['id']
    rows = []
    for index in range(cells.MAX_RECEIPTS - len(f.source_rows())):
        cid = f'z-cold-receipt-{index:05d}'
        receipt = cells.Receipt(sid, cid, 'c' * 64, 16, 44, 76, (1, index + 1, 76, 1, 1), 0., None)
        rows.append((cid, sid, receipt.digest, receipt.encoded))
        f.owner._receipts[cid] = receipt
    f.optional.execute('BEGIN IMMEDIATE')
    f.optional.executemany('INSERT INTO sources VALUES(?,?,?,?)', rows)
    f.optional.execute('COMMIT')
    assert len(f.source_rows()) == 4096
    return {row[0]: tuple(row) for row in rows}


@pytest.mark.parametrize('kind', ['compact', 'receipt'])
def test_B1_B2_actual_caps_roll_and_replenish_without_raising_limits(primary_cache, kind):
    f = primary_cache; f.recording(59, compacts=kind == 'compact')
    cold = cold_compacts(f) if kind == 'compact' else cold_receipts(f)
    cap = core.MAX_COMPACTS if kind == 'compact' else cells.MAX_RECEIPTS
    admitted = cap; retired = 0
    capacity_gap = None
    if kind == 'receipt':
        capacity_gap, = f.recording(1, gap=True)
        with f.store.db() as db:
            raw = db.execute('SELECT native_receipt FROM recording_work WHERE chunk_id=?', (capacity_gap,)).fetchone()[0]
        receipt = cells.Receipt.decode(raw)
        op = cells.Operation('receipt', receipt, f.owner._epoch)
        with cells.Budget.for_owner(op).phase() as budget:
            with pytest.raises(cells.Unavailable, match='source_receipt_capacity'):
                f.owner._receipt(op, budget)
        assert len(f.source_rows()) == 4096 and f.owner.receipt(capacity_gap) is None
    # Four replenishments exceed the actual global cap cumulatively. Cold
    # metadata is never reclaimed: all positive deletions are real cohorts.
    for wave in range(4):
        before = len(f.compact_rows()) if kind == 'compact' else len(f.source_rows())
        results = []
        if kind == 'receipt':f.owner._receipt_gc_cursor = ''
        for _ in range((core.MAX_COMPACTS + 7) // 8 + 1 if kind == 'compact' else 8):
            results.append(f.collect() if kind == 'compact' else f.collect_receipts())
            if sum(row['retired'] for row in results) >= 4:break
        removed = sum(row['retired'] for row in results)
        assert removed >= 4, (wave, results)
        retired += removed
        # Every freed slot is replenished by a novel eligible source. A full
        # occupancy census is asserted again before the next bounded scan.
        f.recording(removed, compacts=kind == 'compact')
        admitted += removed
        after = len(f.compact_rows()) if kind == 'compact' else len(f.source_rows())
        assert after == before == cap
        assert all(row['scanned'] <= 8 and row['retired'] <= 8 for row in results)
    assert admitted > cap and retired >= 16
    current = {r['slot']: r for r in f.compact_rows()} if kind == 'compact' else f.source_rows()
    assert all(current[key] == value for key, value in cold.items())
    if capacity_gap is not None:
        assert f.owner.receipt(capacity_gap) is None and f.qualified(capacity_gap) is None
        assert any(value['position'] >= 60 for value in f.owner._receipt_gc_prefixes.values())
        with f.store.db() as db:
            assert tuple(db.execute('SELECT receipt_state,native_receipt_status FROM recording_work WHERE chunk_id=?',
                (capacity_gap,)).fetchone()) == ('gap', 'source_receipt_capacity')
    assert core.MAX_COMPACTS == 2056 and cells.MAX_RECEIPTS == 4096
    fixture_bytes(f.root)


@pytest.mark.parametrize('state', HELD_STATES)
def test_B4_every_held_primary_state_roots_both_caches_until_drop(primary_cache, state):
    f = primary_cache; ids = f.recording(compacts=True); cid = ids[0]
    handle = dict(owner=f.jobs, session_id=f.sid, recording_id=f.bid, chunk_id=cid,
        state=state, cancelled=threading.Event(), crop=b'SYNTHETIC held copy',
        charge_bytes=0, reservation=None, attempt_id=None, thread=None, operation=None)
    if state == 'cancelled':handle['cancelled'].set()
    f.jobs._primary_pending = handle
    try:
        assert f.jobs.primary_retention_roots() == frozenset((cid,))
        assert cid in f.capture.retention_roots()
        f.compact_cycle(); f.cycle()
        assert cid in {row['cid'] for row in f.compact_rows()} and cid in f.source_rows()
        f.jobs._drop_primary_copy(handle)
        assert not f.jobs.primary_retention_roots()
        f.owner._compact_gc_cursor = ''; f.compact_cycle()
        f.owner._receipt_gc_cursor = ''; f.cycle()
        assert cid not in {row['cid'] for row in f.compact_rows()} and cid not in f.source_rows()
    finally:
        if f.jobs._primary_pending is handle:f.jobs._drop_primary_copy(handle)
    if state == 'terminal':
        original = f.jobs._lock; blocked = threading.Lock(); blocked.acquire()
        try:
            f.jobs._lock = blocked
            assert f.collect()['retired'] == f.collect_receipts()['retired'] == 0
        finally:f.jobs._lock = original; blocked.release()
        f.jobs._primary_pending = dict(handle, owner=object())
        try:
            with pytest.raises(cells.Unavailable):f.jobs.primary_retention_roots()
            assert f.collect()['retired'] == f.collect_receipts()['retired'] == 0
        finally:f.jobs._primary_pending = None


@pytest.mark.parametrize('root', ['cell', 'pin', 'cancelled_pin', 'tail'])
def test_B5_primary_cell_pin_and_capture_roots_preserve_exact_source(primary_cache, root):
    f = primary_cache; ids = f.recording(compacts=True); cid = ids[0]
    cell = synthetic_cell(sid=f.sid, cid=cid, receipt_id=f.receipts[cid].receipt_id)
    if root == 'cell':f.add_cell(cell, with_pcm=False)
    elif root in ('pin', 'cancelled_pin'):
        pin = cells.Pin(f.owner, cell.cell_id, json.loads(cell.proof), cell.pcm)
        if root == 'cancelled_pin':pin.cancelled.set(); pin._release_requested = True
        f.owner._pins[id(pin)] = pin
    else:f.capture._seam_tail = SimpleNamespace(part=SimpleNamespace(chunk_id=cid))
    preserved = f.preserved(); f.compact_cycle(); f.cycle()
    assert cid in {row['cid'] for row in f.compact_rows()} and cid in f.source_rows()
    assert f.preserved() == preserved
    f.owner._pins.clear(); f.capture._seam_tail = None


def test_B5_latest_frontier_replay_and_unrelated_pending_progress(primary_cache):
    f = primary_cache; ids = f.recording(compacts=True)
    legacy_sid = f.store.create_session('SYNTHETIC unrelated dormant MOSS')['id']
    with f.store.db() as db:
        intent = seed_intent(db, legacy_sid, 901, state='queued')
        legacy = [tuple(r) for r in db.execute(f'SELECT * FROM {auto.TABLE}')]
    f.capture.core_seams.work_pending = True
    assert sum(row['retired'] for row in f.compact_cycle()) > 0
    assert ids[-1] in {row['cid'] for row in f.compact_rows()}
    # A new pending page may alias old completed audio. Its range is a receipt
    # root even though no pending work receipt or inference exists for it.
    with f.store.db() as db:
        page = f.bid + '-pending-alias'; source = 'SYNTHETIC-pending-alias'
        db.execute('''INSERT INTO recording_pages(id,batch_id,page_number,state,released,start_frame,created)
            VALUES(?,?,3,'open',0,?,0)''', (page, f.bid, 900007 + 31 * Q))
        db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
            frames,bytes,source_start_frame,format,created,page_id) VALUES(?,?,?,?,?,?,?,?,?,0,?)''',
            (source, f.bid, source, 'c' * 64, str(f.root / 'uploads' / 'pending-alias.audio'),
             Q, 44 + 2 * Q, 900007, 'wav-pcm16le', page))
        db.execute('INSERT INTO recording_ranges VALUES(?,?,?,?,?,?,?,?)',
            (f.bid, source, ids[0], 0, 900007, 0, Q, 'exact_alias'))
    assert sum(row['retired'] for row in f.cycle()) > 0
    assert ids[0] in f.source_rows()
    assert set(ids[26:]) <= set(f.source_rows())
    with f.store.db() as db:assert [tuple(r) for r in db.execute(f'SELECT * FROM {auto.TABLE}')] == legacy
    assert f.capture.core_seams.work_pending is True
    assert intent['candidate_key']


@pytest.mark.parametrize('fault', ['gap_candidate', 'pending_prefix', 'unreleased_prefix', 'mapping_prefix'])
def test_B5_gap_continuity_never_supplies_deletion_evidence(primary_cache, fault):
    f = primary_cache; ids = f.recording()
    with f.store.db() as db:
        if fault == 'gap_candidate':db.execute("UPDATE recording_work SET receipt_state='gap' WHERE chunk_id=?", (ids[0],))
        elif fault == 'pending_prefix':db.execute("UPDATE recording_work SET receipt_state='pending' WHERE chunk_id=?", (ids[0],))
        elif fault == 'unreleased_prefix':db.execute("UPDATE recording_pages SET state='pending_receipts' WHERE batch_id=? AND page_number=1", (f.bid,))
        else:db.execute('DELETE FROM stream_time_index WHERE chunk_id=?', (ids[0],))
    before = f.source_rows(); preserved = f.preserved(); results = f.cycle()
    assert ids[0] in f.source_rows()
    if fault != 'gap_candidate':assert f.source_rows() == before and not any(r['retired'] for r in results)
    else:assert any(r['retired'] for r in results)
    assert f.preserved() == preserved


@pytest.mark.parametrize('kind', ['compact', 'receipt'])
@pytest.mark.parametrize('phase', ['before_commit', 'after_commit'])
def test_C4_unknown_collector_ack_exact_reread_without_attribution(primary_cache, monkeypatch, kind, phase):
    f = primary_cache; f.recording(compacts=kind == 'compact')
    if kind == 'receipt':
        # Retain all sources while building a real completed prefix.
        protected = [f.ordinary_cell(cid, with_pcm=False) for cid in f.receipts]
        f.cycle(3)
        for cell in protected:f.optional.execute('DELETE FROM cells WHERE cell_id=?', (cell.cell_id,))
        f.owner._receipt_gc_cursor = ''
    preserved = f.preserved(); calls = []
    def fault(db, sql, args):
        if sql.strip().upper() == 'COMMIT':
            calls.append(1); raise sqlite3.OperationalError('SYNTHETIC lost cache COMMIT ACK')
    probing = probe_primary if kind == 'compact' else probe_sidecar
    collect = f.collect if kind == 'compact' else f.collect_receipts
    with probing(f, monkeypatch, **{'before' if phase == 'before_commit' else 'after': fault}):result = collect()
    assert calls == [1] and result['status'] == 'unknown' and result['retired'] == 0
    assert f.owner.health()[kind + '_gc']['retired'] == 0
    resolved = collect()
    assert resolved['status'] == 'reconciled' and resolved['retired'] == 0
    assert f.owner.health()[kind + '_gc']['retired'] == 0
    assert f.preserved() == preserved


@pytest.mark.parametrize('fault', ['generation', 'claim', 'held_root'])
def test_C5_final_recheck_rolls_back_late_root_changes(primary_cache, monkeypatch, fault):
    f = primary_cache; ids = f.recording(compacts=True)
    before = f.compact_rows(); preserved = f.preserved(); changed = []
    def after_delete(db, sql, args):
        if changed or not sql.lstrip().upper().startswith('DELETE FROM NATIVE_CORE_COMMITS'):return
        changed.append(1)
        if fault == 'generation':f.owner._epoch = object()
        elif fault == 'claim':f.worker.current_chunk = ids[0]
        else:f.jobs._primary_pending = dict(owner=f.jobs, session_id=f.sid,
            recording_id=f.bid, chunk_id=ids[0], state='provisional', cancelled=threading.Event(),
            crop=b'SYNTHETIC', charge_bytes=0, reservation=None, attempt_id=None)
    try:
        with probe_primary(f, monkeypatch, after=after_delete):result = f.collect()
        assert changed and result['retired'] == 0
        assert f.compact_rows() == before and f.preserved() == preserved
    finally:
        f.worker.current_chunk = None
        if f.jobs._primary_pending is not None:f.jobs._drop_primary_copy(f.jobs._primary_pending)


def test_C5_schema_mutation_invalidates_previously_qualified_source(primary_cache):
    f = primary_cache; ids = f.recording(compacts=True)
    assert f.qualified(ids[0]) is not None
    with f.store.db() as db:
        name = db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND sql LIKE '%primary_cache_policy%' ORDER BY name LIMIT 1").fetchone()[0]
        db.execute('DROP TRIGGER ' + name)
    before = f.compact_rows(); receipts = f.source_rows()
    with pytest.raises(ValueError):f.qualified(ids[0])
    assert not any(row['retired'] for row in f.compact_cycle(2) + f.cycle(2))
    assert f.compact_rows() == before and f.source_rows() == receipts


def test_C5_busy_primary_fence_defers_without_partial_deletion(primary_cache):
    f = primary_cache; f.recording(compacts=True)
    before = f.compact_rows(); receipts = f.source_rows(); blocker = sqlite3.connect(f.store.path, timeout=0)
    try:
        blocker.execute('BEGIN IMMEDIATE')
        assert f.collect()['retired'] == f.collect_receipts()['retired'] == 0
    finally:blocker.rollback(); blocker.close()
    assert f.compact_rows() == before and f.source_rows() == receipts


def test_C6_ordinary_collection_preserves_audio_and_retirement_markers(primary_cache):
    f = primary_cache; f.recording(compacts=True)
    with f.store.db() as db:
        marker_tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%retire%' OR name LIKE '%revok%') ORDER BY name")]
        markers = rows_hash(db, marker_tables)
    preserved = f.preserved()
    assert sum(r['retired'] for r in f.compact_cycle()) > 0
    assert sum(r['retired'] for r in f.cycle()) > 0
    assert f.preserved() == preserved
    with f.store.db() as db:assert rows_hash(db, marker_tables) == markers
