"""Portable list-scope regressions with tiny generated SQLite/WAV fixtures.

The real schema, proof parser, list body, source receipts and PrimaryAuthority
are used. Setup inserts synthetic rows synchronously; it does not start an
owner, API, model or runtime-qualification path. No pre-existing DB is opened.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from audio_backend import native_owned_cell_capture as cap
from audio_backend import native_owned_cell_store as store
from audio_backend import native_owned_seam_cells as seam
from audio_backend import native_owned_cells as cells
from test_native_owned_seam_cells import synthetic_pieces


class ListAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='list-authority-synthetic-')
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.primary = self.root / 'primary.sqlite3'
        self.sid = 'session'
        self.raw_connect = sqlite3.connect
        self.connections = []
        self.fail_rollback = False
        (self.root / 'uploads').mkdir()
        (self.root / 'cells').mkdir()
        with self.writer() as db:
            db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY, deleted INTEGER, processing_mode TEXT)')
            db.execute('CREATE TABLE chunks(id TEXT PRIMARY KEY, session_id TEXT, path TEXT, '
                       'digest TEXT, duration REAL, timeline_start REAL, next_offset REAL, '
                       'status TEXT, metadata TEXT)')
            db.execute('CREATE TABLE stream_chunks(chunk_id TEXT PRIMARY KEY, '
                       'session_id TEXT, stream_id TEXT, position INTEGER)')
            db.execute('CREATE INDEX stream_order ON stream_chunks(session_id,stream_id,position)')
            db.execute("INSERT INTO sessions VALUES(?,0,'legacy')", (self.sid,))
        self.authority = cap.PrimaryAuthority(SimpleNamespace(path=self.primary,
            settings=SimpleNamespace(data_dir=self.root)))
        self.owner = store.CellStore(self.root / 'cells', self.authority,
            enabled=True, seam_enabled=True)
        self.authority.owner = self.owner
        receipts = []
        for index, cid in enumerate(('left', 'right')):
            path = self.root / 'uploads' / (cid + '.audio')
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(cells.RATE)
                output.writeframes(b'\x01\0' * 16)
            raw = path.read_bytes()
            self.assertEqual(len(raw), 76)
            receipts.append(store.Receipt(self.sid, cid, hashlib.sha256(raw).hexdigest(),
                16, 44, 76, cap.signature(path.stat()), index * 16 / cells.RATE, 'stream'))
        self.pieces = synthetic_pieces(receipt_ids=tuple(r.receipt_id for r in receipts),
            source_hashes=tuple(r.digest for r in receipts))
        self.cell = seam.make_composite(*self.pieces)
        for index, (receipt, piece) in enumerate(zip(receipts, self.pieces)):
            self.owner._receipts[receipt.chunk_id] = receipt
            evidence = piece.commit['evidence']
            with self.writer() as db:
                db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)',
                    (receipt.chunk_id, self.sid,
                     str(self.root / 'uploads' / (receipt.chunk_id + '.audio')),
                     receipt.digest, 16 / cells.RATE, receipt.timeline_start,
                     16 / cells.RATE, 'completed', json.dumps({
                         'native_diarization': evidence['native'], 'core_cut': evidence['core_cut']})))
                db.execute('INSERT INTO stream_chunks VALUES(?,?,?,?)',
                    (receipt.chunk_id, self.sid, 'stream', index))
        self.owner._db = self.raw_connect(str(self.owner.path), timeout=0., isolation_level=None)
        self.owner._db.row_factory = sqlite3.Row
        store.CellStore._schema(self.owner._db)
        for receipt in receipts:
            self.owner._db.execute('INSERT INTO sources VALUES(?,?,?,?)',
                (receipt.chunk_id, self.sid, receipt.digest, receipt.encoded))
        self.owner._db.execute('INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state) '
            "VALUES(?,?,?,?,?,?,'retained')", (self.cell.cell_id, self.cell.ownership,
                self.sid, self.cell.chunk_id, self.cell.proof, self.cell.pcm))
        for position, part in enumerate(self.cell.parts):
            self.owner._db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',
                (self.cell.cell_id, position, part.chunk_id, part.source_sha256,
                 part.receipt_sha256, part.source_start, part.source_end,
                 part.logical_start, part.logical_end))
        # Synchronous test fixture only: receipts and rows above were seeded
        # together. This is not owner startup or runtime qualification.
        self.owner._ready.set()
        self.addCleanup(self.close_connections)
        fixture = self

        class TrackedConnection(sqlite3.Connection):
            def __init__(self, database, *args, **kwargs):
                super().__init__(database, *args, **kwargs)
                self.record = {'database': str(database), 'thread': threading.get_ident(),
                    'closed': False, 'begin': 0, 'rollback': 0, 'close_with_transaction': False}
                fixture.connections.append(self)
                if len(fixture.connections) > 256:
                    raise AssertionError('Finite fixture connection capacity exceeded')

            def execute(self, sql, parameters=()):
                statement = sql.strip().upper()
                if statement == 'BEGIN':
                    self.record['begin'] += 1
                if statement == 'ROLLBACK':
                    self.record['rollback'] += 1
                    if fixture.fail_rollback:
                        raise sqlite3.OperationalError('Injected rollback failure')
                return super().execute(sql, parameters)

            def close(self):
                if not self.record['closed']:
                    self.record['close_with_transaction'] = self.in_transaction
                    self.record['closed'] = True
                return super().close()

        def tracked_connect(database, *args, **kwargs):
            self.assertEqual(str(database), self.primary.as_uri() + '?mode=ro')
            self.assertNotIn('factory', kwargs)
            return self.raw_connect(database, *args, factory=TrackedConnection, **kwargs)

        tracker = patch.object(sqlite3, 'connect', side_effect=tracked_connect)
        tracker.start()
        self.addCleanup(tracker.stop)
        self.assertLess(sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file()), 1024**2)

    @contextmanager
    def writer(self):
        db = self.raw_connect(str(self.primary), timeout=0., isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    def close_connections(self):
        self.owner._db.close()
        self.assertTrue(all(db.record['closed'] for db in self.connections),
                        [db.record for db in self.connections if not db.record['closed']])
        self.assertIsNone(getattr(self.authority._list_reads, 'current', None))

    def commit(self, sql, params=()):
        scope = getattr(self.authority._list_reads, 'current', None)
        if scope and scope['db'] is not None:
            self.assertFalse(scope['db'].in_transaction, 'A completed authority phase retained a snapshot')
        with self.writer() as db:
            db.execute(sql, params)

    def run_list(self, *, wrapper=True, scoped=True):
        payload = (self.sid, 0, 20) if scoped else (self.sid, 0)
        op = store.Operation('jobs_list' if scoped else 'list', payload, self.owner._epoch)
        budget = store.Budget.for_owner(op)
        try:
            with budget.phase():
                self.owner._db.set_progress_handler(budget.progress, 100)
                result = (self.owner._list if wrapper else self.owner._list_rows)(op, budget)
                op.check()
                self.owner._generation(op)
            return result, budget
        finally:
            self.owner._db.set_progress_handler(None, 0)

    def mutate_after_validation(self, number, change):
        original = self.authority.cell_valid
        calls = []

        def valid(doc, budget, *, publishing):
            result = original(doc, budget, publishing=publishing)
            calls.append(result)
            if len(calls) == number:
                change()
            return result

        guard = patch.object(self.authority, 'cell_valid', side_effect=valid)
        guard.start()
        self.addCleanup(guard.stop)
        return calls

    def test_one_connection_identical_page_and_fresh_transaction_per_authority_phase(self):
        expected, baseline_budget = self.run_list(wrapper=False)
        baseline = list(self.connections)
        self.assertGreater(len(baseline), 2)
        self.assertTrue(all(db.record['begin'] == 1 and db.record['closed'] for db in baseline))
        result, budget = self.run_list()
        self.assertEqual(result, expected)
        self.assertEqual(result['status'], 'available')
        self.assertEqual([r['cell_id'] for r in result['cells']], [self.cell.cell_id])
        self.assertEqual(result['next_cursor'], 1)
        self.assertFalse(result['has_more'])
        readers = self.connections[len(baseline):]
        self.assertEqual(len(readers), 1)
        record = readers[0].record
        self.assertEqual(record['begin'], len(baseline))
        self.assertEqual(record['begin'], record['rollback'])
        self.assertTrue(record['closed'])
        self.assertFalse(record['close_with_transaction'])
        # Each fresh connection may execute fewer than the 100-opcode
        # progress-handler interval. Tiny-fixture callback counts therefore
        # do not establish a step-count performance comparison. Both paths
        # must still obey the unchanged operation cap.
        self.assertLess(baseline_budget.steps, store.SQL_STEPS)
        self.assertEqual((store.SQL_STEPS, store.OWNER_OP_SECONDS,
            store.SQL_PROJECTION_BYTES, store.SOURCE_READ_BYTES), (150000, 2., 262144, 41943040))
        self.assertLess(budget.steps, store.SQL_STEPS)
        self.assertLess(budget.sql_bytes, store.SQL_PROJECTION_BYTES)
        self.assertLess(budget.read_bytes, store.SOURCE_READ_BYTES)

    def test_unscoped_list_also_reuses_one_connection(self):
        result, _ = self.run_list(scoped=False)
        self.assertEqual([r['cell_id'] for r in result['cells']], [self.cell.cell_id])
        self.assertNotIn('has_more', result)
        self.assertEqual(len(self.connections), 1)
        self.assertGreater(self.connections[0].record['begin'], 2)

    def test_commit_mutation_before_final_pass_is_rejected(self):
        calls = self.mutate_after_validation(1, lambda: self.commit(
            "UPDATE chunks SET metadata=json_set(metadata,'$.core_cut.nominal',999) WHERE id='left'"))
        with self.assertRaisesRegex(cells.Unavailable, '^source_changed_after_list_read$'):
            self.run_list()
        self.assertEqual(calls, [True, False])
        self.assertEqual(len(self.connections), 1)

    def test_source_deletion_before_final_pass_is_rejected(self):
        self.mutate_after_validation(1, lambda: self.commit("DELETE FROM chunks WHERE id='left'"))
        with self.assertRaisesRegex(cells.Unavailable, '^source_changed_after_list_read$'):
            self.run_list()

    def test_session_deletion_at_final_session_check_is_rejected(self):
        calls = self.mutate_after_validation(2, lambda: self.commit('UPDATE sessions SET deleted=1'))
        with self.assertRaisesRegex(cells.Unavailable, '^session_deleted_after_read$'):
            self.run_list()
        self.assertEqual(calls, [True, True])

    def test_revocation_at_final_boundary_is_rejected(self):
        self.mutate_after_validation(2, lambda: self.owner.revoked())
        with self.assertRaisesRegex(cells.Unavailable, '^publication_generation_revoked$'):
            self.run_list()
        self.assertTrue(self.owner._cleanup_pending)

    def test_later_authority_phase_sees_committed_session_mutation(self):
        budget = store.Budget.for_owner()
        with self.authority.list_read_scope(budget):
            self.assertTrue(self.authority.session_valid(self.sid, budget))
            self.commit('UPDATE sessions SET deleted=1')
            self.assertFalse(self.authority.session_valid(self.sid, budget))
        self.assertEqual(len(self.connections), 1)
        self.assertEqual((self.connections[0].record['begin'], self.connections[0].record['rollback']), (2, 2))

    def test_non_list_reads_keep_fresh_connections(self):
        budget = store.Budget.for_owner()
        self.assertTrue(self.authority.session_valid(self.sid, budget))
        self.assertTrue(self.authority.session_valid(self.sid, budget))
        self.assertEqual(len(self.connections), 2)
        self.assertTrue(all(db.record['closed'] for db in self.connections))

    def test_concurrent_thread_never_uses_list_connection(self):
        outcomes = []
        worker_ids = []
        ready = threading.Event()
        proceed = threading.Event()

        def background():
            try:
                worker_ids.append(threading.get_ident())
                ready.set()
                if not proceed.wait(1.):
                    raise AssertionError('Fixture rendezvous timed out')
                other = store.Budget.for_owner()
                outcomes.extend([self.authority.session_valid(self.sid, other),
                                 self.authority.session_valid(self.sid, other)])
            except BaseException as exc:
                outcomes.append(exc)

        budget = store.Budget.for_owner()
        thread = threading.Thread(target=background, daemon=True)
        try:
            with self.authority.list_read_scope(budget):
                self.assertTrue(self.authority.session_valid(self.sid, budget))
                thread.start()
                self.assertTrue(ready.wait(1.))
                proceed.set()
                thread.join(2.)
                self.assertFalse(thread.is_alive())
                self.assertEqual(outcomes, [True, True])
                self.assertTrue(self.authority.session_valid(self.sid, budget))
        finally:
            proceed.set()
            if thread.ident is not None:
                thread.join(2.)
        self.assertEqual(len(self.connections), 3)
        self.assertEqual(sorted(db.record['begin'] for db in self.connections), [1, 1, 2])
        own = [db for db in self.connections if db.record['thread'] == threading.get_ident()]
        other = [db for db in self.connections if db.record['thread'] in worker_ids]
        self.assertEqual(len(own), 1)
        self.assertEqual(len(other), 2)
        self.assertTrue(all(db.record['closed'] for db in other))

    def test_nested_and_wrong_budget_scopes_fail_closed(self):
        budget = store.Budget.for_owner()
        with self.authority.list_read_scope(budget):
            self.assertTrue(self.authority.session_valid(self.sid, budget))
            for nested in (budget, store.Budget.for_owner()):
                with self.assertRaisesRegex(cells.Unavailable, '^nested_primary_list_read$'):
                    with self.authority.list_read_scope(nested):
                        pass
            with self.assertRaisesRegex(cells.Unavailable, '^primary_list_read_scope_mismatch$'):
                self.authority.session_valid(self.sid, store.Budget.for_owner())
            self.assertTrue(all(db.record['closed'] for db in self.connections))
            self.assertIsNone(self.authority._list_reads.current['db'])

    def test_baseexception_and_rollback_error_close_immediately(self):
        class StopRead(BaseException):
            pass

        def abort(db):
            raise StopRead()

        budget = store.Budget.for_owner()
        with self.authority.list_read_scope(budget):
            with self.assertRaises(StopRead):
                self.authority._read(budget, 'session', abort)
            self.assertIsNone(self.authority._list_reads.current['db'])
            self.fail_rollback = True
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'Injected rollback failure'):
                    self.authority.session_valid(self.sid, budget)
            finally:
                self.fail_rollback = False
            self.assertIsNone(self.authority._list_reads.current['db'])
            self.assertTrue(all(db.record['closed'] for db in self.connections))

    def test_identity_replacement_closes_retained_connection(self):
        budget = store.Budget.for_owner()
        with self.authority.list_read_scope(budget):
            self.assertTrue(self.authority.session_valid(self.sid, budget))
            replacement = self.primary.with_suffix('.replacement')
            replacement.write_bytes(self.primary.read_bytes())
            replacement.replace(self.primary)
            with self.assertRaisesRegex(cells.Unavailable, '^primary_database_replaced$'):
                self.authority.session_valid(self.sid, budget)
            self.assertIsNone(self.authority._list_reads.current['db'])
            self.assertTrue(all(db.record['closed'] for db in self.connections))

    def test_original_deadline_and_work_limits_close_retained_connection(self):
        for field, value, reason in (
            ('steps', 150001, 'operation_work_capacity'),
            ('sql_bytes', 262145, 'operation_work_capacity'),
            ('read_bytes', 41943041, 'operation_work_capacity'),
            ('deadline', time.monotonic() - 1., 'cooperative_deadline'),
        ):
            with self.subTest(field=field):
                budget = store.Budget.for_owner()
                with self.assertRaisesRegex(cells.Unavailable, '^' + reason + '$'):
                    with self.authority.list_read_scope(budget):
                        self.assertTrue(self.authority.session_valid(self.sid, budget))
                        setattr(budget, field, value)
                        self.authority.session_valid(self.sid, budget)
                self.assertIsNone(getattr(self.authority._list_reads, 'current', None))
                self.assertTrue(all(db.record['closed'] for db in self.connections))

    def test_busy_retry_closes_before_backoff_and_keeps_budget(self):
        budget = store.Budget.for_owner()
        pauses = []
        with self.writer() as lock:
            lock.execute('BEGIN EXCLUSIVE')

            def release(seconds):
                self.assertEqual(seconds, .005)
                self.assertIsNone(self.authority._list_reads.current['db'])
                self.assertTrue(all(db.record['closed'] for db in self.connections))
                pauses.append((budget.steps, budget.sql_bytes, budget.deadline))
                lock.execute('ROLLBACK')

            with self.authority.list_read_scope(budget), patch.object(cap.time, 'sleep', side_effect=release):
                self.assertTrue(self.authority.session_valid(self.sid, budget))
                self.assertTrue(self.authority.session_valid(self.sid, budget))
            self.assertEqual(len(pauses), 1)
            self.assertEqual(len(self.connections), 2)
            self.assertEqual([db.record['begin'] for db in self.connections], [1, 2])
            self.assertGreaterEqual(budget.steps, pauses[0][0])
            self.assertGreaterEqual(budget.sql_bytes, pauses[0][1])
            self.assertEqual(budget.deadline, pauses[0][2])

    def test_busy_still_stops_after_three_attempts(self):
        budget = store.Budget.for_owner()
        with self.writer() as lock:
            lock.execute('BEGIN EXCLUSIVE')
            with self.authority.list_read_scope(budget), patch.object(cap.time, 'sleep') as sleep:
                with self.assertRaises(sqlite3.OperationalError) as error:
                    self.authority.session_valid(self.sid, budget)
                self.assertEqual(error.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
                self.assertIsNone(self.authority._list_reads.current['db'])
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(len(self.connections), 3)
            self.assertTrue(all(db.record['closed'] for db in self.connections))
            lock.execute('ROLLBACK')

    def test_legacy_authority_without_optional_scope_keeps_list_body(self):
        authority = SimpleNamespace(
            session_valid=self.authority.session_valid, cell_valid=self.authority.cell_valid)
        with patch.object(self.owner, 'authority', authority):
            result, _ = self.run_list()
        self.assertEqual([r['cell_id'] for r in result['cells']], [self.cell.cell_id])
        self.assertGreater(len(self.connections), 1)
        self.assertTrue(all(db.record['begin'] == 1 for db in self.connections))
