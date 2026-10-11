"""Bounded stdlib cache tests against synthetic unchanged primary SQL fixtures.

These tests never load a model or qualify a real native overlap. Execution stays
held until the separately reviewed source/dependency/resource guard is ready.
"""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tracemalloc
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from audio_backend import native_owned_support_cache as mod
from audio_backend.native_owned_support import (CapturedSupport, SourceMaterial,
                                                 SELECTION_POLICY_SHA256)
from audio_backend.native_lexical_alternatives import SourceSupport
from audio_backend.qwen_candidates import pcm16_to_float32le


class FakeClock:
    def __init__(self):
        self.now = 100.

    def __call__(self):
        return self.now


# Physical table/commit shapes mirror the unchanged storage/change_feed schema.
SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE sessions (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, identity_group TEXT NOT NULL,
 created REAL NOT NULL, timeline_end REAL NOT NULL DEFAULT 0,
 deleted INTEGER NOT NULL DEFAULT 0, processing_mode TEXT NOT NULL DEFAULT 'legacy');
CREATE TABLE chunks (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 session_id TEXT NOT NULL REFERENCES sessions(id), idempotency_key TEXT NOT NULL,
 digest TEXT NOT NULL, path TEXT NOT NULL, duration REAL NOT NULL,
 timeline_start REAL NOT NULL, recorded_at TEXT, status TEXT NOT NULL,
 next_offset REAL NOT NULL DEFAULT 0, error TEXT, created REAL NOT NULL,
 metadata TEXT, provenance TEXT, UNIQUE(session_id,idempotency_key));
CREATE INDEX chunk_queue ON chunks(status,created);
CREATE TABLE stream_chunks (
 chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
 session_id TEXT NOT NULL, stream_id TEXT NOT NULL,
 stream_final INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL,
 upload_stream_final INTEGER NOT NULL DEFAULT 0, UNIQUE(session_id,stream_id,position));
CREATE TABLE result_changes (
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
 entity TEXT NOT NULL, entity_id TEXT NOT NULL, operation TEXT NOT NULL,
 data TEXT, created REAL NOT NULL);
CREATE INDEX result_changes_session ON result_changes(session_id,cursor);
'''


class CacheFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'primary.sqlite'
        self.store = SimpleNamespace(path=self.path,
                                     settings=SimpleNamespace(native_owned_support_enabled=True))
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        for sql in mod.PINNED_CHUNK_TRIGGER_SQL.values():
            self.db.execute(sql)
        self.db.commit()
        self.clock = FakeClock()
        self.cache = mod.NativeOwnedSupportCache(self.store, clock=self.clock)
        self.serial = 0

    def tearDown(self):
        self.cache.close()
        for lease in tuple(self.cache._leases):
            lease.release()
        for value in tuple(mod._registry.values()):
            value.close()
            for lease in tuple(value._leases):
                lease.release()
        mod._registry.clear()
        self.db.close()
        self.temp.cleanup()

    def chunk(self, *, sid='session', frames=1600):
        self.serial += 1
        cid = f'chunk{self.serial}'
        pcm = struct.pack('<h', self.serial) * frames
        body = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', len(pcm) + 36,
                           b'WAVE', b'fmt ', 16, 1, 1, 16000, 32000, 2, 16,
                           b'data', len(pcm)) + pcm
        path = self.root / (cid + '.audio')
        path.write_bytes(body)
        self.db.execute('INSERT OR IGNORE INTO sessions(id,name,identity_group,created) VALUES(?,?,?,?)',
                        (sid, 'Synthetic fixture', 'test', 1.))
        self.db.execute('''INSERT INTO chunks(id,session_id,idempotency_key,digest,path,
                           duration,timeline_start,recorded_at,status,created)
                           VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (cid, sid, cid, hashlib.sha256(body).hexdigest(), str(path),
                         frames / 16000, 0., None, 'queued', float(self.serial)))
        self.db.commit()
        return dict(self.db.execute('SELECT * FROM chunks WHERE id=?', (cid,)).fetchone()), pcm

    def claim(self, chunk):
        watermark = self.cache.preclaim()
        self.db.execute("UPDATE chunks SET status='running',error=NULL WHERE id=?", (chunk['id'],))
        self.db.commit()
        return self.cache.begin(chunk, watermark)

    def artifact(self, chunk, pcm, selected=True):
        source = SourceMaterial.inspect(chunk)
        support = SourceSupport(source.source_generation, source.plan_sha256,
                                'a' * 64, 'b' * 64, source.frames, 0, source.frames,
                                0, source.frames, hashlib.sha256(pcm).hexdigest(),
                                hashlib.sha256(pcm16_to_float32le(pcm)).hexdigest()) if selected else None
        return CapturedSupport(source, b'{"synthetic":true}', pcm if selected else b'',
                               selected, support, SELECTION_POLICY_SHA256,
                               'available' if selected else 'no_native_overlap')

    def commit(self, chunk):
        self.db.execute('UPDATE chunks SET next_offset=?,status=?,metadata=?,provenance=? WHERE id=?',
                        (chunk['duration'], 'completed', '{}', '{}', chunk['id']))
        self.db.commit()

    def capture(self, *, sid='session', selected=True, frames=1600):
        chunk, pcm = self.chunk(sid=sid, frames=frames)
        token = self.claim(chunk)
        self.assertIsNotNone(token)
        artifact = self.artifact(chunk, pcm, selected)
        self.commit(chunk)
        self.assertTrue(self.cache.publish(token, artifact))
        return chunk, pcm, token, artifact

    def failure(self, reason, fn, *args, status=None, **kwargs):
        with self.assertRaises(mod.ReadError) as raised:
            fn(*args, **kwargs)
        self.assertEqual(reason, raised.exception.reason)
        if status is not None:
            self.assertEqual(status, raised.exception.status_code)
        self.assertEqual(0, self.cache._lease_count)

    def unrelated(self, count):
        self.db.executemany('''INSERT INTO result_changes(session_id,entity,entity_id,operation,data,created)
                               VALUES('other','segments',?,'upsert',NULL,0)''',
                            ((str(i),) for i in range(count)))
        self.db.commit()


class CaptureAndReadTests(CacheFixture):
    def test_selected_capture_public_projection_and_exact_wav(self):
        chunk, pcm, _, _ = self.capture()
        result = self.cache.read_detail('session', chunk['id'])
        public = json.loads(result.body)
        self.assertTrue(public['selected_for_overlap'])
        self.assertFalse(public['lexical_dispatch_available'])
        self.assertEqual(public['support']['end_frame'], len(pcm) // 2)
        self.assertNotIn(str(self.root), result.body.decode())
        self.assertNotIn('synthetic', public)
        result.lease.release()
        audio = self.cache.read_audio('session', chunk['id'], expected_capture_epoch=self.cache.epoch,
                                      expected_revision=public['revision'])
        self.assertEqual(Path(chunk['path']).read_bytes(), b''.join(audio.body))
        self.assertEqual('audio/wav', audio.media_type)
        audio.lease.release()
        self.assertEqual(0, self.cache._lease_count)

    def test_list_and_detail_snapshots_hold_no_pcm_or_private_proof(self):
        chunk, _, _, _ = self.capture(frames=320000)
        original = self.cache._validate_targets
        observed = []
        def inspect(db, sid, targets, budget):
            for target in targets:
                self.assertIsInstance(target, mod._ReadTarget)
                self.assertEqual(b'', target.pcm)
                self.assertFalse(hasattr(target, 'proof'))
                observed.append(target)
            return original(db, sid, targets, budget)
        with patch.object(self.cache, '_validate_targets', inspect):
            for operation, args in ((self.cache.read_list, ('session',)),
                                    (self.cache.read_detail, ('session', chunk['id']))):
                response = operation(*args)
                response.lease.release()
        self.assertEqual(2, len(observed))
        self.cache.invalidate('session')
        self.assertTrue(all(not target.pcm and not hasattr(target, 'proof') for target in observed))

    def test_audio_final_fence_has_released_shared_pcm_reference(self):
        chunk, _, _, _ = self.capture(frames=320000)
        original = self.cache._matches
        def inspect(projection):
            self.assertIsInstance(projection, mod._ReadTarget)
            self.assertEqual(b'', projection.pcm)
            return original(projection)
        with patch.object(self.cache, '_matches', inspect):
            response = self.cache.read_audio('session', chunk['id'])
        self.assertIsInstance(response.body, tuple)
        self.assertEqual(640044, sum(map(len, response.body)))
        response.lease.release()

    def test_sqlite_timestamp_projection_matches_feed_without_epsilon(self):
        chunk, pcm = self.chunk()
        timestamp = 1700000000.1234567
        self.db.execute('UPDATE chunks SET created=? WHERE id=?', (timestamp, chunk['id']))
        self.db.commit()
        chunk['created'] = timestamp
        sqlite_projection = json.loads(self.db.execute(
            "SELECT json_object('created',created) FROM chunks WHERE id=?", (chunk['id'],)).fetchone()[0])
        self.assertNotEqual(timestamp, sqlite_projection['created'])
        token = self.claim(chunk)
        artifact = self.artifact(chunk, pcm)
        self.commit(chunk)
        self.assertTrue(self.cache.publish(token, artifact))
        result = self.cache.read_detail('session', chunk['id'])
        result.lease.release()

    def test_purge_during_projection_drops_cache_entry_reference(self):
        chunk, _, _, _ = self.capture(frames=320000)
        entry = self.cache._entries[('session', chunk['id'])]
        original = self.cache._validate_targets
        def purging(*args, **kwargs):
            original(*args, **kwargs)
            self.cache.invalidate('session')
            # One closure reference plus getrefcount's own argument; no retained
            # _Entry in the read operation's projection/local variables.
            self.assertEqual(2, sys.getrefcount(entry))
        with patch.object(self.cache, '_validate_targets', purging):
            self.failure('capture_invalidated', self.cache.read_list, 'session', status=409)

    def test_two_max_audio_response_peak_and_purge_stay_within_reservation(self):
        first_chunk, _, _, _ = self.capture(sid='first', frames=320000)
        second_chunk, _, _, _ = self.capture(sid='second', frames=320000)
        tracemalloc.start()
        try:
            first = self.cache.read_audio('first', first_chunk['id'])
            second = self.cache.read_audio('second', second_chunk['id'])
            self.cache.invalidate('first')
            self.cache.invalidate('second')
            current, peak = tracemalloc.get_traced_memory()
            self.assertEqual(0, self.cache._retained)
            self.assertEqual(mod.MAX_READ_BYTES, self.cache._lease_bytes)
            self.assertEqual(1280088, sum(map(len, first.body)) + sum(map(len, second.body)))
            self.assertNotEqual(id(first.body[1]), id(second.body[1]))
            # Count both now-detached immutable PCM buffers explicitly, since
            # they were allocated before tracing, PLUS every traced read-phase
            # buffer/projection/header. Never join WAV parts in the read path.
            detached_pcm = len(first.body[1]) + len(second.body[1])
            self.assertLessEqual(detached_pcm + peak, mod.MAX_READ_BYTES)
            self.assertLessEqual(current, peak)
            first.lease.release()
            second.lease.release()
        finally:
            tracemalloc.stop()

    def test_no_overlap_marker_and_no_audio(self):
        chunk, _, _, _ = self.capture(selected=False)
        result = self.cache.read_detail('session', chunk['id'])
        public = json.loads(result.body)
        result.lease.release()
        self.assertEqual('no_native_overlap', public['reason'])
        self.assertFalse(public['selected'])
        self.assertNotIn('support', public)
        self.assertEqual(0, self.cache._retained)
        self.assertLessEqual(self.cache._marker_bytes, 512)
        self.failure('no_native_overlap', self.cache.read_audio, 'session', chunk['id'], status=409)

    def test_publish_before_primary_commit_is_not_adopted(self):
        chunk, pcm = self.chunk()
        token = self.claim(chunk)
        artifact = self.artifact(chunk, pcm)
        self.assertFalse(self.cache.publish(token, artifact))
        self.commit(chunk)
        self.assertFalse(self.cache.publish(token, artifact))
        self.assertEqual({}, self.cache._entries)

    def test_rollback_or_discard_has_no_optional_adoption(self):
        chunk, pcm = self.chunk()
        token = self.claim(chunk)
        artifact = self.artifact(chunk, pcm)
        self.db.execute("UPDATE chunks SET status='completed',next_offset=duration WHERE id=?", (chunk['id'],))
        self.db.rollback()
        self.cache.fail(token, 'lost_commit_response')
        self.assertFalse(self.cache.publish(token, artifact))
        self.assertIsNone(self.cache._pending)

    def test_pause_resume_aba_and_extra_update_invalidate(self):
        for statuses in [('paused', 'running'), ('failed', 'queued', 'running')]:
            chunk, pcm = self.chunk(sid='s' + str(self.serial))
            token = self.claim(chunk)
            artifact = self.artifact(chunk, pcm)
            for status in statuses:
                self.db.execute('UPDATE chunks SET status=? WHERE id=?', (status, chunk['id']))
            self.db.commit()
            self.commit(chunk)
            self.assertFalse(self.cache.publish(token, artifact))

    def test_read_detects_later_control_change(self):
        chunk, _, _, _ = self.capture()
        self.db.execute("UPDATE chunks SET metadata='{}' WHERE id=?", (chunk['id'],))
        self.db.commit()
        self.failure('source_control_changed', self.cache.read_detail, 'session', chunk['id'], status=409)

    def test_missing_or_replaced_trigger_is_unavailable(self):
        chunk, _, _, _ = self.capture()
        self.db.execute('DROP TRIGGER feed_chunks_update')
        self.db.commit()
        self.failure('untrusted_change_feed_trigger', self.cache.read_detail, 'session', chunk['id'], status=503)
        self.db.execute('CREATE TRIGGER feed_chunks_update AFTER UPDATE ON chunks BEGIN SELECT 1; END')
        self.db.commit()
        self.failure('untrusted_change_feed_trigger', self.cache.read_detail, 'session', chunk['id'], status=503)

    def test_erased_anchor_is_not_replaced_by_current_state(self):
        chunk, _, token, _ = self.capture()
        self.db.execute('DELETE FROM result_changes WHERE cursor>? AND entity_id=?', (token.watermark, chunk['id']))
        self.db.commit()
        self.failure('claim_anchor_missing', self.cache.read_detail, 'session', chunk['id'], status=409)

    def test_preclaim_watermark_is_global_and_survives_erasure(self):
        self.unrelated(7)
        high = self.cache.preclaim()
        self.db.execute('DELETE FROM result_changes')
        self.db.commit()
        self.assertEqual(high, self.cache.preclaim())
        chunk, _, token, _ = self.capture()
        self.assertGreater(token.watermark, high)
        self.assertEqual(chunk['id'], token.cid)

    def test_source_rewrite_and_private_path_mutation_fail(self):
        chunk, _, _, _ = self.capture()
        path = Path(chunk['path'])
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        self.failure('source_changed', self.cache.read_detail, 'session', chunk['id'], status=409)
        self.db.execute('UPDATE chunks SET path=? WHERE id=?', ('x' * 4097, chunk['id']))
        self.db.commit()
        self.failure('malformed_source_evidence', self.cache.read_detail, 'session', chunk['id'], status=503)

    def test_fifo_and_symlink_source_replacements_reject_without_read(self):
        chunk, _, _, _ = self.capture()
        path = Path(chunk['path'])
        target = self.root / 'elsewhere'
        path.rename(target)
        path.symlink_to(target)
        self.failure('source_changed', self.cache.read_detail, 'session', chunk['id'], status=409)
        path.unlink()
        os.mkfifo(path)
        self.failure('source_changed', self.cache.read_detail, 'session', chunk['id'], status=409)

    def test_missing_and_deleted_primary_entities_are_404(self):
        chunk, _, _, _ = self.capture()
        self.db.execute('DELETE FROM chunks WHERE id=?', (chunk['id'],))
        self.db.commit()
        self.failure('chunk_not_found', self.cache.read_detail, 'session', chunk['id'], status=404)
        self.db.execute('UPDATE sessions SET deleted=1 WHERE id=?', ('session',))
        self.db.commit()
        self.failure('session_not_found', self.cache.read_list, 'session', status=404)

    def test_exact_nonbinary_frame_duration_is_accepted(self):
        chunk, _, _, _ = self.capture(frames=2017)
        result = self.cache.read_detail('session', chunk['id'])
        self.assertEqual(2017, json.loads(result.body)['source_frames'])
        result.lease.release()

    def test_stream_and_fragment_modes_rejected(self):
        chunk, _ = self.chunk()
        watermark = self.cache.preclaim()
        self.db.execute("UPDATE chunks SET status='running' WHERE id=?", (chunk['id'],))
        self.db.execute('INSERT INTO stream_chunks(chunk_id,session_id,stream_id,position) VALUES(?,?,?,?)',
                        (chunk['id'], 'session', 'stream', 0))
        self.db.commit()
        self.failure('unsupported_source_mode', self.cache.begin, chunk, watermark, status=409)
        self.db.execute("UPDATE sessions SET processing_mode='fragment_pcm16_v1'")
        self.db.commit()
        self.failure('unsupported_source_mode', self.cache.read_list, 'session', status=409)


class CapacityAndLifetimeTests(CacheFixture):
    def test_four_supports_and_one_staged_capture(self):
        chunk, _ = self.chunk()
        token = self.claim(chunk)
        other, _ = self.chunk(sid='other')
        self.assertIsNone(self.cache.begin(other, self.cache.preclaim()))
        self.cache.fail(token)
        for i in range(4):
            self.capture(sid='s' + str(i))
        fifth, _ = self.chunk(sid='fifth')
        self.assertIsNone(self.claim(fifth))
        self.assertLessEqual(self.cache._retained + mod.MARKER_RESERVE_BYTES, mod.MAX_RETAINED_BYTES)

    def test_cumulative_capacity_dominates_count(self):
        self.capture()
        chunk, _ = self.chunk(sid='other')
        with patch.object(mod, 'MAX_RETAINED_BYTES', self.cache._retained + mod.MARKER_RESERVE_BYTES + mod.STAGE_BYTES - 1):
            self.assertIsNone(self.claim(chunk))
        self.assertEqual(1, len(self.cache._entries))

    def test_sixty_four_markers_bounded_without_pcm(self):
        for i in range(64):
            self.capture(sid='s' + str(i), selected=False, frames=1)
        self.assertEqual(64, len(self.cache._entries))
        self.assertEqual(0, self.cache._retained)
        self.assertLessEqual(self.cache._marker_bytes, mod.MARKER_RESERVE_BYTES)
        chunk, pcm = self.chunk(sid='last', frames=1)
        token = self.claim(chunk)
        artifact = self.artifact(chunk, pcm, False)
        self.commit(chunk)
        self.assertFalse(self.cache.publish(token, artifact))
        self.assertEqual(64, len(self.cache._entries))

    def test_expiry_does_not_redecode_or_rehydrate(self):
        chunk, _, _, _ = self.capture()
        self.clock.now += 900
        self.failure('artifact_unavailable', self.cache.read_detail, 'session', chunk['id'], status=409)
        self.assertEqual(0, self.cache._retained)
        self.assertEqual({}, self.cache._entries)

    def test_two_slow_responses_reject_third_and_release_exactly_once(self):
        chunk, _, _, _ = self.capture()
        a = self.cache.read_audio('session', chunk['id'])
        b = self.cache.read_audio('session', chunk['id'])
        self.assertEqual(mod.MAX_READ_BYTES, self.cache._lease_bytes)
        with self.assertRaises(mod.ReadError) as raised:
            self.cache.read_detail('session', chunk['id'])
        self.assertEqual('read_capacity', raised.exception.reason)
        self.assertEqual(2, self.cache._lease_count)
        a.lease.release()
        a.lease.release()
        self.assertEqual(1, self.cache._lease_count)
        b.lease.release()
        self.assertEqual(0, self.cache._lease_bytes)

    def test_byte_reservation_gate_is_independent_of_count(self):
        chunk, _, _, _ = self.capture()
        first = self.cache.read_audio('session', chunk['id'])
        with patch.object(mod, 'MAX_READ_BYTES', mod.READ_RESERVATIONS['audio'] + mod.READ_RESERVATIONS['detail'] - 1):
            with self.assertRaises(mod.ReadError) as raised:
                self.cache.read_detail('session', chunk['id'])
            self.assertEqual('read_capacity', raised.exception.reason)
        first.lease.release()

    def test_lease_release_lock_timeout_remains_charged_until_drain(self):
        chunk, _, _, _ = self.capture()
        response = self.cache.read_audio('session', chunk['id'])
        self.cache._lease_lock.acquire()
        try:
            response.lease.release()
            self.assertEqual(1, self.cache._lease_count)
            self.assertTrue(self.cache._lease_cleanup_pending.is_set())
        finally:
            self.cache._lease_lock.release()
        self.cache.drain()
        self.assertTrue(response.lease.released)
        self.assertEqual(0, self.cache._lease_bytes)
        response.lease.release()
        self.assertEqual(0, self.cache._lease_count)

    def test_delete_after_read_authorization_retains_only_leased_body(self):
        chunk, pcm, _, _ = self.capture()
        response = self.cache.read_audio('session', chunk['id'])
        self.assertFalse(self.cache.invalidate('session'))
        self.assertEqual(0, self.cache._retained)
        self.assertEqual(pcm, response.body[1])
        self.assertEqual(1, self.cache._lease_count)
        response.lease.release()
        self.assertEqual(0, self.cache._lease_count)

    def test_delete_between_source_gate_and_publication_cannot_resurrect(self):
        chunk, pcm = self.chunk()
        token = self.claim(chunk)
        artifact = self.artifact(chunk, pcm)
        self.commit(chunk)
        original = self.cache._validate_targets
        def deleting(*args, **kwargs):
            original(*args, **kwargs)
            self.cache.invalidate('session')
        with patch.object(self.cache, '_validate_targets', deleting):
            self.assertFalse(self.cache.publish(token, artifact))
        self.assertEqual({}, self.cache._entries)

    def test_close_after_validation_rejects_publish_and_read(self):
        chunk, pcm = self.chunk()
        token = self.claim(chunk)
        artifact = self.artifact(chunk, pcm)
        self.commit(chunk)
        original = self.cache._validate_targets
        def closing(*args, **kwargs):
            original(*args, **kwargs)
            self.cache.close()
        with patch.object(self.cache, '_validate_targets', closing):
            self.assertFalse(self.cache.publish(token, artifact))
        self.failure('capture_epoch_closed', self.cache.read_list, 'session', status=409)

    def test_final_read_generation_fence_rejects_late_delete(self):
        chunk, _, _, _ = self.capture()
        original = self.cache._validate_targets
        def deleting(*args, **kwargs):
            original(*args, **kwargs)
            self.cache.invalidate('session')
        with patch.object(self.cache, '_validate_targets', deleting):
            self.failure('capture_invalidated', self.cache.read_detail, 'session', chunk['id'], status=409)

    def test_cache_lock_purge_failure_is_truthful_and_closed(self):
        self.capture()
        retained = self.cache._retained
        self.cache._lock.acquire()
        try:
            self.assertTrue(self.cache.invalidate('session'))
            self.assertEqual(retained, self.cache._retained)
            self.assertTrue(self.cache._blocked.is_set())
        finally:
            self.cache._lock.release()
        self.assertFalse(self.cache.drain())
        self.assertEqual(0, self.cache._retained)
        self.failure('capture_epoch_closed', self.cache.read_list, 'session', status=409)

    def test_off_after_enabled_still_purges_and_never_enabled_is_inert(self):
        registered = mod.register(self.store)
        mod._registry.clear()
        mod._registry[self.cache.database_identity] = self.cache
        self.capture()
        self.store.settings.native_owned_support_enabled = False
        self.assertFalse(mod.notify_deleted(self.store, 'session'))
        self.assertEqual(0, self.cache._retained)
        self.failure('feature_disabled', self.cache.read_list, 'session', status=409)
        registered.close()
        mod._registry.clear()
        with patch.object(mod, '_db_key', side_effect=AssertionError('OFF must be inert')):
            self.assertIsNone(mod.notify_deleted(self.store, 'session'))

    def test_unrelated_off_database_preserves_delete_shape_during_registry_contention(self):
        owner = mod.register(self.store)
        other = self.root / 'never-enabled.sqlite'
        other.write_bytes(b'fixture')
        off = SimpleNamespace(path=other, settings=SimpleNamespace(native_owned_support_enabled=False))
        mod._registry_lock.acquire()
        try:
            self.assertIsNone(mod.notify_deleted(off, 'absent'))
            self.assertFalse(owner._blocked.is_set())
            self.assertTrue(mod.notify_deleted(self.store, 'session'))
            self.assertTrue(owner._blocked.is_set())
        finally:
            mod._registry_lock.release()

    def test_registry_aliases_restart_and_old_epoch(self):
        first = mod.register(self.store)
        alias = self.root / 'alias.sqlite'
        alias.symlink_to(self.path)
        self.assertIs(first, mod.lookup(SimpleNamespace(path=alias)))
        os.link(self.path, self.root / 'hard.sqlite')
        self.assertIs(first, mod.lookup(SimpleNamespace(path=self.root / 'hard.sqlite')))
        with self.assertRaises(mod.ReadError):
            mod.register(self.store)
        first.close()
        second = mod.register(self.store)
        self.assertNotEqual(first.epoch, second.epoch)
        self.assertEqual(1, len(mod._registry))
        self.failure('capture_epoch_changed', second.read_list, 'session', expected_capture_epoch=first.epoch)

    def test_registry_replacement_is_not_false_clean(self):
        mod.register(self.store)
        moved = self.root / 'old.sqlite'
        self.path.rename(moved)
        self.path.write_bytes(b'not the original database')
        self.assertTrue(mod.notify_deleted(self.store, 'session'))
        self.assertTrue(next(iter(mod._registry.values()))._blocked.is_set())


class BudgetAndPaginationTests(CacheFixture):
    def test_canonical_query_validation_before_optional_work(self):
        self.assertEqual({'after': 0, 'limit': 20}, mod.parse_query(b'after=0&limit=20', 'list'))
        bad = [b'after=01', b'after=-1', b'after=%31', b'after=1', b'limit=0',
               b'limit=21', b'limit=1&limit=2', b'unknown=x', b'limit=1&', b'\xff',
               b'expected_capture_epoch=' + b'a' * 1024]
        for query in bad:
            with self.subTest(query=query[:40]):
                with self.assertRaises(mod.ReadError):
                    mod.parse_query(query, 'list')
        with self.assertRaises(mod.ReadError):
            mod.parse_query(b'after=0', 'detail')

    def test_exclusive_current_state_pagination_and_revision(self):
        for _ in range(3):
            self.capture(selected=False)
        first = self.cache.read_list('session', limit=2)
        page = json.loads(first.body)
        first.lease.release()
        self.assertEqual(2, len(page['supports']))
        self.assertTrue(page['has_more'])
        second = self.cache.read_list('session', after=page['next_cursor'], limit=2,
                                      expected_capture_epoch=self.cache.epoch)
        tail = json.loads(second.body)
        second.lease.release()
        self.assertEqual(1, len(tail['supports']))
        self.assertGreater(tail['supports'][0]['revision'], page['next_cursor'])
        self.assertFalse(tail['has_more'])
        cid = tail['supports'][0]['chunk_id']
        self.failure('capture_revision_changed', self.cache.read_detail, 'session', cid,
                     expected_revision=0, status=409)

    def test_shared_global_rows_are_counted_before_entity_filtering(self):
        self.capture(selected=False)
        self.unrelated(600)
        self.capture(selected=False)
        self.unrelated(600)
        with patch.object(mod, 'MAX_VM_STEPS', 10**7):
            self.failure('feed_row_budget', self.cache.read_list, 'session', status=503)

    def test_sql_transfer_limit_is_checked_before_large_text_transfer(self):
        huge = 'x' * (2 * 1024 * 1024)
        self.db.execute('INSERT INTO sessions(id,name,identity_group,created) VALUES(?,?,?,0)',
                        ('session', huge, 'test'))
        self.db.commit()
        budget = mod.ReadBudget(self.clock)
        bounded, valid = mod._text_sql('name', 32)
        with self.cache._database(budget) as db:
            row = self.cache._fetch(db, f'SELECT {bounded},{valid} FROM sessions', (), budget)
        self.assertEqual((None, 0), row)
        self.assertLess(budget.sql_bytes, 1024)

    def test_shared_sql_byte_vm_and_source_budgets(self):
        self.capture()
        with patch.object(mod, 'MAX_SQL_BYTES', 128):
            self.failure('sql_byte_budget', self.cache.read_list, 'session', status=503)
        with patch.object(mod, 'MAX_VM_STEPS', 0):
            self.failure('sqlite_vm_budget', self.cache.read_list, 'session', status=503)
        with patch.object(mod, 'MAX_SOURCE_BYTES', 100):
            self.failure('source_byte_budget', self.cache.read_list, 'session', status=503)

    def test_shared_page_source_budget_does_not_reset_per_target(self):
        self.capture(selected=False)
        self.capture(selected=False)
        with patch.object(mod, 'MAX_SOURCE_BYTES', 5000):
            self.failure('source_byte_budget', self.cache.read_list, 'session', status=503)

    def test_deadline_expiry_after_io_and_before_final_return(self):
        self.capture()
        original = mod._source_identity
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock.now += .501
            return result
        with patch.object(mod, '_source_identity', delayed):
            self.failure('validation_deadline', self.cache.read_list, 'session', status=503)

    def test_malformed_relevant_payload_and_oversize_header_reject(self):
        chunk, _, _, _ = self.capture()
        self.db.execute("UPDATE result_changes SET data=? WHERE entity_id=? AND json_extract(data,'$.status')='completed'",
                        ('x' * 4097, chunk['id']))
        self.db.commit()
        self.failure('invalid_claim_feed', self.cache.read_detail, 'session', chunk['id'], status=503)
        self.db.execute('INSERT INTO result_changes(session_id,entity,entity_id,operation,data,created) VALUES(?,?,?,?,NULL,0)',
                        ('x' * 65, 'segments', 'id', 'upsert'))
        self.db.commit()
        # Earliest malformed payload remains the first failure; remove it to isolate headers.
        self.db.execute('DELETE FROM result_changes WHERE entity_id=?', (chunk['id'],))
        self.db.commit()
        self.failure('malformed_feed_header', self.cache.read_list, 'session', status=503)

    def test_primary_gate_is_readonly(self):
        budget = mod.ReadBudget(self.clock)
        with self.assertRaises(mod.ReadError):
            with self.cache._database(budget) as db:
                db.execute("INSERT INTO sessions(id,name,identity_group,created) VALUES('bad','','',0)")
        self.assertEqual(0, self.db.execute("SELECT COUNT(*) FROM sessions WHERE id='bad'").fetchone()[0])


if __name__ == '__main__':
    unittest.main()
