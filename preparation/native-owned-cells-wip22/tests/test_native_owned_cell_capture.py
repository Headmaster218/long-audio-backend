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
        with self.primary.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0], 2)
        with self.primary.db() as db:db.execute('UPDATE stream_time_index SET timeline_start=timeline_start+0.000001 WHERE chunk_id=?', (second,))
        with self.assertRaises(Unavailable):
            with cell_store.Budget().phase() as b:self.authority.mapping(chunk, 0., 8000, 24000, b)

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
        routes = [r.path for r in self.app.routes]
        self.assertFalse(any('native-owned-cells' in route for route in routes))
        health = self.client.get('/health').json()['native_owned_cells']
        self.assertFalse(health['working_memory_measured'])
        self.assertNotIn('native_owned_cells_enabled', self.settings.inference_config())

    def test_optional_hook_exception_does_not_replace_primary_success(self):
        cid = self.upload()
        with patch.object(self.capture, 'prepare', side_effect=RuntimeError('fixture optional failure')):self.process_one()
        self.assertEqual(self.primary.chunk(cid)['status'], 'completed')
        self.assertEqual(self.cell_count(), 0)

    def cell_count(self):return len(wait_operation(self, self.owner.submit('list', (self.sid, 0)))['cells'])


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
                native_owned_cells_enabled=True, diarization_backend='nemotron')
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
