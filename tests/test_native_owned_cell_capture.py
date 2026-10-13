"""Finite model-free primary/upload/owner integration, with fake native/analyzer.

The runtime profile is explicitly simulated and injected only into the private
CellStore constructor. No production qualifier, CLI, environment or request can
install it. Original fixtures and sidecars stay under the runner's retained root.
"""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from audio_backend.app import create_app
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import Worker
from audio_backend import native_worker
from audio_backend import native_owned_cell_capture as cap
from audio_backend import native_owned_cell_store as cell_store
from audio_backend.native_owned_cells import Unavailable, SourceSpan, exact_frame
from test_native_owned_cells import synthetic_cell
from test_native_owned_cell_store import simulated_profile, wait_operation
from test_native_owned_support_api import FakeNative, FakeAnalyzer, fake_extract, wav_bytes


class CaptureFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root, disk_reserve_bytes=0, start_worker=False,
            diarization_backend='nemotron', native_diarization_model='synthetic-model',
            native_diarization_library='synthetic-library')
        self.app = create_app(self.settings, analyzer_factory=FakeAnalyzer)
        self.client = TestClient(self.app); self.client.__enter__()
        self.primary = self.app.state.store
        self.sid = self.client.post('/sessions', json={'name': 'synthetic'}).json()['id']
        # Fixture-only injection; production registry is still empty. The real
        # Worker thread is never started, but its unchanged loop is tested below.
        self.settings.native_owned_cells_enabled = True
        self.authority = cap.PrimaryAuthority(self.primary)
        self.owner = cell_store.CellStore(self.root / 'native-owned-cells', self.authority,
            enabled=True, qualification=simulated_profile(self.root), instance_lock=self.app.state.instance_lock)
        self.authority.owner = self.owner; self.capture = cap.Capture(self.owner, self.authority)
        self.app.state.worker.native_owned_cells = self.capture
        cap._REGISTRY[str(self.authority.path)] = self.capture
        self.owner.start(); self.assertTrue(self.owner._ready.wait(3.), self.owner.health())
        FakeNative.instances = []

    def tearDown(self):
        for pin in tuple(self.owner._pins.values()):pin.release()
        self.client.__exit__(None, None, None)
        if self.owner._thread:self.owner._thread.join(3.)
        cap._REGISTRY.pop(str(self.authority.path), None)
        self.temp.cleanup()

    def drain(self):
        op = self.owner._slot
        if op is not None:return wait_operation(self, op)
        return None

    def upload(self, key='one', frames=32000, **params):
        with patch('audio_backend.app.probe', return_value=frames / 16000):
            response = self.client.put(f'/sessions/{self.sid}/chunks', params=params,
                content=wav_bytes(frames), headers={'Idempotency-Key': key})
        self.assertEqual(response.status_code, 202, response.text)
        self.drain()
        return response.json()['chunk']['id']

    def process_one(self, *, commit=True):
        worker = self.app.state.worker; original = self.primary.claim; called = []
        def claim():
            if called:worker.stop_event.set(); worker.wake.set(); return None
            called.append(True); return original()
        @contextmanager
        def commit_mode():
            if commit:yield
            else:
                with patch.object(self.primary, 'commit_window', return_value=False):yield
        with patch.object(self.primary, 'claim', side_effect=claim), \
             patch.object(native_worker, 'NativeProcess', FakeNative), \
             patch.object(Worker, 'extract_pcm', fake_extract), \
             patch('audio_backend.worker.check_resources'), commit_mode():
            worker.loop()
        self.drain()
        self.assertEqual(len(FakeNative.instances), 1)
        self.assertEqual(FakeNative.instances[0].calls, ['reset', 'push', 'finish', 'turns'])

    def cell(self):
        page = wait_operation(self, self.owner.submit('list', (self.sid, 0)))
        self.assertEqual(page['status'], 'available', page)
        self.assertEqual(len(page['cells']), 1, (page, self.capture.last_reason, self.owner.health()))
        return page['cells'][0]['cell_id']

    def test_fresh_upload_receipt_binds_streamed_hash_and_rename_descriptor(self):
        cid = self.upload(); receipt = self.owner.receipt(cid)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.digest, hashlib.sha256(wav_bytes()).hexdigest())
        with self.primary.db() as db:row = dict(db.execute('SELECT * FROM chunks WHERE id=?', (cid,)).fetchone())
        self.assertEqual(receipt.signature, cap.signature(Path(row['path']).stat()))
        self.assertEqual(receipt.frames, 32000); self.assertEqual(receipt.data_offset, 44)
        self.assertLessEqual(len(receipt.encoded), 1024)

    def test_real_worker_primary_calls_unchanged_and_post_commit_publication(self):
        cid = self.upload(); self.process_one()
        self.assertEqual(self.primary.chunk(cid)['status'], 'completed')
        cell_id = self.cell()
        result = wait_operation(self, self.owner.submit('read', cell_id))
        self.assertEqual(result['status'], 'available', result)
        self.assertEqual(result['pin'].read(), b'\x01\0' * 32000); result['pin'].release()
        self.assertEqual(self.client.get(f'/sessions/{self.sid}/segments').json()['segments'][0]['text'], 'explicit fake primary')

    def test_failed_primary_commit_cannot_publish(self):
        self.upload(); self.process_one(commit=False)
        page = wait_operation(self, self.owner.submit('list', (self.sid, 0)))
        self.assertEqual(page['cells'], [])

    def test_duplicate_reuses_only_matching_durable_receipt(self):
        cid = self.upload(); receipt = self.owner.receipt(cid)
        self.assertEqual(self.upload(), cid); self.assertEqual(self.owner.receipt(cid), receipt)
        self.assertEqual(self.capture.duplicate(cid, receipt.digest)['status'], 'existing_receipt')
        self.assertEqual(self.capture.duplicate(cid, 'f' * 64)['status'], 'duplicate_without_qualifying_receipt')

    def test_primary_upload_survives_receipt_busy_and_no_historical_backfill(self):
        with patch.object(self.owner, 'submit', return_value={'status': 'busy'}):cid = self.upload()
        self.assertIsNone(self.owner.receipt(cid)); self.assertEqual(self.primary.chunk(cid)['status'], 'queued')
        self.assertEqual(self.upload(), cid); self.assertIsNone(self.owner.receipt(cid))
        self.process_one()
        self.assertEqual(wait_operation(self, self.owner.submit('list', (self.sid, 0)))['cells'], [])

    def test_bounded_riff_malformed_and_descriptor_replacement_rejected(self):
        path = self.root / 'uploads' / 'fixture.part'; path.write_bytes(b'bad' * 100)
        malformed_digest = hashlib.sha256(path.read_bytes()).hexdigest(); malformed_size = path.stat().st_size
        with path.open('rb') as source:malformed_identity = self.capture.streamed_descriptor(source.fileno(), malformed_digest, malformed_size)
        self.assertIsNone(self.capture.prepare_upload(path, malformed_digest, malformed_size, malformed_identity))
        path.write_bytes(wav_bytes())
        streamed = hashlib.sha256(wav_bytes()).hexdigest()
        with path.open('rb') as source:identity = self.capture.streamed_descriptor(source.fileno(), streamed, len(wav_bytes()))
        token = self.capture.prepare_upload(path, streamed, len(wav_bytes()), identity)
        self.assertIsNotNone(token)
        final = path.with_suffix('.audio'); path.replace(final)
        # Alter the same regular file; no special filesystem objects are created.
        with final.open('r+b') as out:out.seek(44); out.write(b'\x02\0')
        result = self.capture.finish_upload(token, self.sid, 'missing', final, True)
        self.assertEqual(result['status'], 'receipt_unavailable'); self.assertTrue(token.closed)

        # Parent identity is rechecked after opening, without creating aliases.
        handle, before = cap._open(final, cell_store.Budget())
        real_chain = cap._directory_chain
        def replaced_parent(directory, budget):
            directory_fd, identities = real_chain(directory, budget)
            return directory_fd, identities[:-1] + ((identities[-1][0], identities[-1][1] + 1),)
        try:
            with patch.object(cap, '_directory_chain', side_effect=replaced_parent), self.assertRaisesRegex(Unavailable, 'source_parent_replaced'):
                cap._continuity(handle, final, before, cell_store.Budget())
        finally:handle.close()
        # Exercise the no-follow directory metadata branch above data_dir too.
        real_stat = os.stat
        def ancestor_alias(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if path == self.root.parent.name and kwargs.get('dir_fd') is not None and kwargs.get('follow_symlinks') is False:
                return SimpleNamespace(st_mode=stat.S_IFLNK, st_dev=info.st_dev, st_ino=info.st_ino)
            return info
        with patch.object(os, 'stat', side_effect=ancestor_alias), self.assertRaisesRegex(Unavailable, 'source_parent_descriptor_mismatch'):
            cap._open(final, cell_store.Budget())

    def test_replacement_and_direct_removal_make_regeneration_ineligible(self):
        cid = self.upload(); self.process_one(); cell_id = self.cell()
        with self.primary.db() as db:path = Path(db.execute('SELECT path FROM chunks WHERE id=?', (cid,)).fetchone()[0])
        replacement = path.with_suffix('.replacement'); replacement.write_bytes(path.read_bytes()); replacement.replace(path)
        result = wait_operation(self, self.owner.submit('detail', cell_id))
        self.assertEqual(result['reason'], 'source_authority_unavailable')
        # Removal after replacement is a separate primary-source absence check.
        path.unlink(); self.assertFalse(self.authority.receipt_valid(self.owner.receipt(cid), cell_store.Budget()))

    def test_alternate_store_primary_deletion_revokes_pins_and_late_reads(self):
        self.upload(); self.process_one(); cell_id = self.cell()
        pin = wait_operation(self, self.owner.submit('read', cell_id))['pin']
        alternate = Store(self.primary.path, self.settings)
        result = alternate.delete_session(self.sid)
        self.assertTrue(result['deleted'] == self.sid)
        with self.assertRaises(Unavailable):pin.read()
        pin.release()
        result = wait_operation(self, self.owner.submit('detail', cell_id))
        self.assertIn(result.get('reason'), ('source_authority_unavailable', 'cell_unavailable'))

    def test_primary_tombstone_alone_wins_crash_before_optional_cleanup(self):
        self.upload(); self.process_one(); cell_id = self.cell()
        with self.primary.db() as db:db.execute('UPDATE sessions SET deleted=1 WHERE id=?', (self.sid,))
        result = wait_operation(self, self.owner.submit('read', cell_id))
        self.assertEqual(result['reason'], 'source_authority_unavailable')

    def test_authority_rechecked_after_original_unlocked_read(self):
        cid = self.upload()
        with self.primary.db() as db:chunk = dict(db.execute('SELECT * FROM chunks WHERE id=?', (cid,)).fetchone())
        budget = cell_store.Budget()
        with budget.phase():parts = self.authority.mapping(chunk, 0., 0, 32000, budget)
        original = cap._continuity; triggered = []
        def delete_after_read(fd, path, expected, budget):
            result = original(fd, path, expected, budget)
            if not triggered:
                triggered.append(True)
                with self.primary.db() as db:db.execute('DELETE FROM chunks WHERE id=?', (cid,))
            return result
        with patch.object(cap, '_continuity', side_effect=delete_after_read), self.assertRaises(Unavailable):
            with budget.phase():self.authority.original_pcm(parts, budget)

    def test_pause_cancel_before_publication_cannot_replace_primary(self):
        cid = self.upload(); original = self.capture.committed
        def cancel(cells):
            with self.primary.db() as db:db.execute("UPDATE chunks SET status='cancelled' WHERE id=?", (cid,))
            return original(cells)
        with patch.object(self.capture, 'committed', side_effect=cancel):self.process_one()
        self.assertEqual(self.primary.chunk(cid)['status'], 'cancelled')
        self.assertEqual(wait_operation(self, self.owner.submit('list', (self.sid, 0)))['cells'], [])

    def test_cross_upload_mapping_exact_and_original_rows_retained(self):
        first = self.upload('first', frames=16000, stream_id='stream')
        second = self.upload('second', frames=16000, stream_id='stream', stream_final=True)
        with self.primary.db() as db:chunk = dict(db.execute('SELECT * FROM chunks WHERE id=?', (first,)).fetchone())
        budget = cell_store.Budget()
        with budget.phase():
            parts = self.authority.mapping(chunk, 0., 8000, 24000, budget)
            pcm = self.authority.original_pcm(parts, budget)
        self.assertEqual([p.chunk_id for p in parts], [first, second]); self.assertEqual(len(pcm), 32000)
        with self.primary.db() as db:
            original_rows = [tuple(row) for row in db.execute('SELECT id,path,digest,duration,timeline_start FROM chunks ORDER BY id')]
            original_index = [tuple(row) for row in db.execute('SELECT chunk_id,timeline_start,timeline_end FROM stream_time_index ORDER BY position')]
        self.assertEqual(len(original_rows), 2)
        # The real immutable-index trigger remains enabled and must reject this.
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'indexed times are immutable'):
            with self.primary.db() as db:db.execute('UPDATE stream_time_index SET timeline_start=timeline_start+0.000001 WHERE chunk_id=?', (second,))
        original_db = self.authority.db; projected = []
        @contextmanager
        def mismatched_projection(budget):
            # Keep the real read-only connection, authority checks and bounded
            # query. Only the selected returned index field is synthetic.
            with original_db(budget) as db:
                def execute(sql, parameters=()):
                    cursor = db.execute(sql, parameters)
                    if 'FROM stream_time_index ti INDEXED BY stream_time_end' not in sql:return cursor
                    rows = [dict(row) for row in cursor.fetchall()]
                    for row in rows:
                        if row['id'] == second:
                            row['indexed_start'] += 0.000001; projected.append(second)
                    return SimpleNamespace(fetchall=lambda: rows)
                yield SimpleNamespace(execute=execute)
        with patch.object(self.authority, 'db', side_effect=mismatched_projection), self.assertRaisesRegex(Unavailable, 'source_index_geometry_mismatch'):
            with cell_store.Budget().phase() as b:self.authority.mapping(chunk, 0., 8000, 24000, b)
        self.assertEqual(projected, [second])
        with self.primary.db() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT id,path,digest,duration,timeline_start FROM chunks ORDER BY id')], original_rows)
            self.assertEqual([tuple(row) for row in db.execute('SELECT chunk_id,timeline_start,timeline_end FROM stream_time_index ORDER BY position')], original_index)
        with cell_store.Budget().phase() as b:
            self.assertEqual(self.authority.mapping(chunk, 0., 8000, 24000, b), parts)
            self.assertEqual(self.authority.original_pcm(parts, b), pcm)

    def test_restart_enabled_and_off_cleanup_only_preserve_authority(self):
        cid = self.upload(); self.process_one(); cell_id = self.cell()
        self.assertTrue(cap.unregister(self.capture))
        authority = cap.PrimaryAuthority(self.primary)
        owner = cell_store.CellStore(self.root / 'native-owned-cells', authority, enabled=False,
            qualification=simulated_profile(self.root), instance_lock=self.app.state.instance_lock)
        authority.owner = owner; capture = cap.Capture(owner, authority)
        self.app.state.worker.native_owned_cells = capture; cap._REGISTRY[str(authority.path)] = capture
        self.owner = owner; self.capture = capture; self.authority = authority
        owner.start(); self.assertTrue(owner._ready.wait(3.), owner.health())
        self.assertIsNotNone(owner.receipt(cid))
        self.assertEqual(owner.submit('detail', cell_id)['status'], 'optional_unavailable')
        self.assertEqual(owner.submit('receipt', owner.receipt(cid))['status'], 'optional_unavailable')

    def test_primary_identity_replacement_fails_closed(self):
        cid = self.upload(); receipt = self.owner.receipt(cid)
        original = cap._identity
        with patch.object(cap, '_identity', side_effect=lambda p: 'changed' if Path(p) == self.authority.path else original(p)):
            with self.assertRaisesRegex(Unavailable, 'primary_database_replaced'):
                self.authority.receipt_valid(receipt, cell_store.Budget())

    def test_no_new_routes_or_model_dispatch_and_conditional_health(self):
        # Historical ID retained: dev7 admits exactly these three inert routes
        # while this primary-capture fixture still has optional jobs disabled.
        routes = [(r.path, r.methods) for r in self.app.routes if 'native-owned-cells' in r.path]
        self.assertEqual(routes, [('/sessions/{sid}/native-owned-cells', {'GET'}),
            ('/sessions/{sid}/native-owned-cells/{cell_id}', {'GET'}),
            ('/sessions/{sid}/native-owned-cells/{cell_id}/moss-jobs', {'POST'})])
        self.assertFalse(self.settings.moss_jobs_enabled); self.assertIsNone(self.app.state.moss_jobs)
        path = f'/sessions/{self.sid}/native-owned-cells'
        with patch.object(native_worker, 'NativeProcess', side_effect=AssertionError('no model dispatch')), \
             patch.object(self.owner, 'submit', side_effect=AssertionError('disabled routes do not read cells')):
            for method, target in (('GET', path), ('GET', path + '/' + '0' * 64),
                                   ('POST', path + '/' + '0' * 64 + '/moss-jobs')):
                response = self.client.request(method, target)
                self.assertEqual((response.status_code, response.json()['detail']), (409, 'feature_disabled'))
        self.assertIsNone(self.app.state.moss_jobs); self.assertFalse(FakeNative.instances)
        health = self.client.get('/health').json()['native_owned_cells']
        self.assertFalse(health['working_memory_measured'])
        self.assertNotIn('native_owned_cells_enabled', self.settings.inference_config())

    def test_optional_hook_exception_does_not_replace_primary_success(self):
        cid = self.upload()
        with patch.object(self.capture, 'prepare', side_effect=RuntimeError('fixture optional failure')):self.process_one()
        self.assertEqual(self.primary.chunk(cid)['status'], 'completed')
        self.assertEqual(self.cell_count(), 0)

    def cell_count(self):return len(wait_operation(self, self.owner.submit('list', (self.sid, 0)))['cells'])

    def _seam_authority_fixture(self, complete_second=True):
        from test_native_owned_seam_cells import synthetic_composite
        first = self.upload('seam-a', frames=16000, stream_id='stream')
        second = self.upload('seam-b', frames=16000, stream_id='stream', stream_final=True)
        receipts = (self.owner.receipt(first), self.owner.receipt(second))
        cell = synthetic_composite(frames=16000, sid=self.sid, cids=(first, second),
            receipt_ids=tuple(r.receipt_id for r in receipts), source_hashes=tuple(r.digest for r in receipts))
        doc = json.loads(cell.proof)
        for index, commit in enumerate(doc['commits']):
            if index == 1 and not complete_second:break
            chunk = self.primary.claim(); self.assertEqual(chunk['id'], commit['chunk_id'])
            result = {'segments': [], 'events': [], 'metadata': {
                'native_diarization': commit['evidence']['native'], 'core_cut': commit['evidence']['core_cut']}}
            self.assertTrue(self.primary.commit_window(chunk, 0., 1., result))
            self.assertEqual(self.primary.chunk(chunk['id'])['status'], 'completed')
        return cell, doc

    def test_seam_authority_requires_both_real_completed_commits(self):
        cell, doc = self._seam_authority_fixture(complete_second=False)
        with cell_store.Budget().phase() as budget:
            self.assertFalse(self.authority.cell_valid(doc, budget, publishing=True))
        commit = doc['commits'][1]; chunk = self.primary.claim()
        self.assertEqual(chunk['id'], commit['chunk_id'])
        self.assertTrue(self.primary.commit_window(chunk, 0., 1., {'segments': [], 'events': [],
            'metadata': {'native_diarization': commit['evidence']['native'], 'core_cut': commit['evidence']['core_cut']}}))
        for publishing in (False, True):
            with cell_store.Budget().phase() as budget:
                self.assertTrue(self.authority.cell_valid(doc, budget, publishing=publishing))
        with cell_store.Budget().phase() as budget:
            self.assertEqual(self.authority.original_pcm(cell.parts, budget), cell.pcm)

    def test_seam_changed_either_commit_metadata_invalidates_every_read(self):
        cell, doc = self._seam_authority_fixture()
        for commit in doc['commits']:
            cid = commit['chunk_id']
            with self.primary.db() as db:saved = db.execute('SELECT metadata FROM chunks WHERE id=?', (cid,)).fetchone()[0]
            changed = json.loads(saved); changed['core_cut']['nominal'] += .25
            with self.primary.db() as db:db.execute('UPDATE chunks SET metadata=? WHERE id=?', (json.dumps(changed), cid))
            for publishing in (False, True):
                with cell_store.Budget().phase() as budget:
                    self.assertFalse(self.authority.cell_valid(doc, budget, publishing=publishing))
            with self.primary.db() as db:db.execute('UPDATE chunks SET metadata=? WHERE id=?', (saved, cid))

    def _assert_seam_source_replacement_or_removal(self, index):
        cell, doc = self._seam_authority_fixture(); part = cell.parts[index]
        with self.primary.db() as db:path = Path(db.execute('SELECT path FROM chunks WHERE id=?', (part.chunk_id,)).fetchone()[0])
        with path.open('r+b') as out:out.seek(44); out.write(b'\x02\0')
        with cell_store.Budget().phase() as budget:
            self.assertFalse(self.authority.cell_valid(doc, budget, publishing=False))
        path.unlink()
        with cell_store.Budget().phase() as budget:
            self.assertFalse(self.authority.cell_valid(doc, budget, publishing=True))

    def test_seam_left_source_replacement_or_removal_invalidates(self):
        self._assert_seam_source_replacement_or_removal(0)

    def test_seam_right_source_replacement_or_removal_invalidates(self):
        self._assert_seam_source_replacement_or_removal(1)

    def test_seam_revocation_between_unlocked_source_checks_fails_closed(self):
        cell, doc = self._seam_authority_fixture(); original = self.authority.seam_commit_current; seen = []
        def revoke(commit, scope, part, budget):
            result = original(commit, scope, part, budget)
            if not seen:seen.append(True); self.owner.revoked()
            return result
        with patch.object(self.authority, 'seam_commit_current', side_effect=revoke):
            with cell_store.Budget().phase() as budget:
                self.assertFalse(self.authority.cell_valid(doc, budget, publishing=False))


    def test_seam_primary_projection_budget_declines_without_widening_limits(self):
        cell, doc = self._seam_authority_fixture()
        # Twelve actual primary commitment projections share one unchanged
        # operation budget. Irrelevant analyzer output must not be copied.
        saved = {}
        for commit in doc['commits']:
            cid = commit['chunk_id']
            with self.primary.db() as db:
                saved[cid] = db.execute('SELECT metadata FROM chunks WHERE id=?', (cid,)).fetchone()[0]
                metadata = json.loads(saved[cid]); metadata['irrelevant_analyzer_output'] = 'x' * 30000
                db.execute('UPDATE chunks SET metadata=? WHERE id=?', (json.dumps(metadata), cid))
        budget = cell_store.Budget()
        with budget.phase():
            for _ in range(3):self.assertTrue(self.authority.cell_valid(doc, budget, publishing=True))
        self.assertLess(budget.sql_bytes, cell_store.SQL_PROJECTION_BYTES)
        # Invalid JSON, missing/object-type violations, duplicate relevant keys
        # and oversized UTF-8 projections decline through the same actual SQL.
        cid = doc['commits'][0]['chunk_id']; baseline = json.loads(saved[cid])
        malformed = ['{', '[]', '{}', json.dumps({'native_diarization': {}, 'core_cut': []}),
            json.dumps({'native_diarization': [], 'core_cut': {}}),
            json.dumps({'native_diarization': None, 'core_cut': {}}),
            '{"native_diarization":{},"native_diarization":{},"core_cut":{}}',
            json.dumps({'native_diarization': {'oversized': '\u00e9' * 33000}, 'core_cut': {}}, ensure_ascii=False),
            json.dumps({'native_diarization': {}, 'core_cut': {'oversized': '\u00e9' * 33000}}, ensure_ascii=False)]
        for raw in malformed:
            with self.subTest(shape=raw[:40]):
                with self.primary.db() as db:db.execute('UPDATE chunks SET metadata=? WHERE id=?', (raw, cid))
                with cell_store.Budget().phase() as bounded:
                    self.assertFalse(self.authority.cell_valid(doc, bounded, publishing=False))
        with self.primary.db() as db:db.execute('UPDATE chunks SET metadata=? WHERE id=?', (saved[cid], cid))
        budget = cell_store.Budget(); budget.sql_bytes = cell_store.SQL_PROJECTION_BYTES - 1024
        with self.assertRaisesRegex(Unavailable, 'operation_work_capacity'):
            with budget.phase():self.authority.cell_valid(doc, budget, publishing=False)
        self.assertEqual(cell_store.SQL_PROJECTION_BYTES, 256 * 1024)
        self.assertEqual(cell_store.SQL_STEPS, 150000); self.assertEqual(cell_store.OP_SECONDS, .5)



class ConfigurationAndOwnershipTests(unittest.TestCase):
    def test_default_off_and_invalid_optional_configuration_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data_dir=Path(directory), disk_reserve_bytes=0, start_worker=False)
            self.assertFalse(settings.native_owned_cells_enabled)
            settings.native_owned_cells_enabled = True
            self.assertEqual(cap.configuration_reason(settings), 'unsupported_optional_configuration')
            self.assertEqual(cap.configuration_reason(settings, object()), 'unsupported_optional_configuration')
            with self.assertRaises(ValueError):Settings(data_dir=Path(directory), native_owned_cells_enabled=1)

    def test_off_absent_register_does_not_open_optional_sql_or_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(data_dir=Path(directory), native_owned_cells_enabled=False)
            store = SimpleNamespace(path=Path(directory) / 'absent.sqlite3', settings=settings)
            with patch.object(sqlite3, 'connect', side_effect=AssertionError('no SQL')), \
                 patch.object(threading.Thread, 'start', side_effect=AssertionError('no thread')):
                self.assertIsNone(cap.register(store))

    def test_off_alias_presence_fails_closed_without_real_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); candidate = root / 'native-owned-cells'; candidate.mkdir()
            real = candidate.lstat()
            fake = SimpleNamespace(st_mode=stat.S_IFLNK | 0o700, st_uid=real.st_uid)
            with patch.object(Path, 'lstat', return_value=fake), patch.object(Path, 'exists', return_value=False):
                owner = cell_store.CellStore(candidate, SimpleNamespace(identity='synthetic'), enabled=False).start()
            self.assertEqual(owner.health()['status'], 'unsafe_cell_namespace')
            self.assertTrue(owner.health()['cleanup_pending']); self.assertIsNone(owner._thread)

    def test_registration_binds_exact_owner_before_uncertain_thread_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); settings = Settings(data_dir=root, disk_reserve_bytes=0, start_worker=False,
                native_owned_cells_enabled=True, diarization_backend='nemotron',
                native_diarization_model=str(root / 'synthetic-model'),
                native_diarization_library=str(root / 'synthetic-library'))
            self.assertFalse(Path(settings.native_diarization_model).exists())
            self.assertFalse(Path(settings.native_diarization_library).exists())
            app = create_app(settings, analyzer_factory=FakeAnalyzer)
            original = cell_store.CellStore.start
            observed = []
            def uncertain(owner):
                observed.append(app.state.worker.native_owned_cells.owner is owner)
                original(owner)
                raise RuntimeError('synthetic uncertain thread startup')
            with patch.object(cell_store.CellStore, 'start', side_effect=uncertain, autospec=True):
                with TestClient(app) as client:
                    self.assertEqual(client.get('/health').status_code, 200)
                    self.assertIsNotNone(app.state.worker.native_owned_cells)
            self.assertEqual(observed, [True])
            self.assertNotIn(str(app.state.store.path.absolute()), cap._REGISTRY)
            # Optional unregister failure cannot skip primary cleanup or release
            # the unresolved instance lock. Preserve the original diagnostic.
            retry = create_app(settings, analyzer_factory=FakeAnalyzer)
            failure = RuntimeError('synthetic optional unregister failure')
            try:
                with patch.object(cap, 'unregister', side_effect=failure), patch.object(
                        retry.state.worker, 'stop', wraps=retry.state.worker.stop) as stop:
                    with self.assertRaisesRegex(RuntimeError, 'Shutdown blocked'):
                        with TestClient(retry):pass
                    stop.assert_called_once_with()
                self.assertTrue(retry.state.shutdown_blocked)
                self.assertIs(retry.state.native_owned_cells_shutdown_error, failure)
                self.assertIsNotNone(retry.state.instance_lock.file)
                self.assertIn(str(retry.state.store.path.absolute()), cap._REGISTRY)
            finally:
                self.assertTrue(cap.unregister(retry.state.worker.native_owned_cells))
                retry.state.instance_lock.release()
