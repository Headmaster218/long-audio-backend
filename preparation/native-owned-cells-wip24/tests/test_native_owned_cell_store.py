"""Real local SQLite fixtures with explicitly SIMULATED qualification records.

These test policy mechanics and readbacks. They do not qualify the production
VFS/header/allocation envelope; QUALIFIED_RUNTIMES must remain empty here.
"""
from dataclasses import replace, asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from audio_backend import native_owned_cell_store as mod
from audio_backend.native_owned_cells import Unavailable, canonical
from test_native_owned_cells import synthetic_cell


class SimulatedRuntimeQualification(mod.RuntimeQualification):
    """Fixture-only injection: binary/allocation identities are not evidence."""
    def verify_binaries(self, budget):budget.check()


def simulated_profile(root):
    db = sqlite3.connect(':memory:')
    try:
        source = db.execute('SELECT sqlite_source_id()').fetchone()[0]
        options = [row[0] for row in db.execute('PRAGMA compile_options')]
    finally:db.close()
    return SimulatedRuntimeQualification(source, sqlite3.sqlite_version,
        hashlib.sha256(canonical(sorted(options))).hexdigest(), 'linux', 'unix',
        Path(root).stat().st_dev, 65536, 1, mod.ANCILLARY_BYTES,
        hashlib.sha256(b'SIMULATED FIXTURE; NOT VFS/ALLOCATION QUALIFICATION').hexdigest(),
        interpreter_bytes=1, sqlite3_binding='observed-built-in-no-separate-sqlite-mapping-v1',
        sqlite3_extension_sha256=None, sqlite3_library_sha256=None, sqlite3_library_realpath_sha256=None,
        **{name: hashlib.sha256(('SIMULATED ' + name).encode()).hexdigest() for name in (
            'interpreter_sha256', 'interpreter_realpath_sha256', 'sqlite3_package_sha256')})


class SyntheticAuthority:
    def __init__(self):
        self.identity = 'synthetic-primary-identity'; self.owner = None; self.valid = True
        self.calls = []
    def receipt_valid(self, receipt, budget):budget.check(); self.calls.append(threading.get_ident()); return self.valid
    def cell_valid(self, doc, budget, *, publishing):budget.check(); return self.valid
    def session_valid(self, sid, budget):budget.check(); return self.valid


def wait_operation(test, result):
    test.assertIsInstance(result, mod.Operation, result)
    deadline = time.monotonic() + 3.
    while not result.done.is_set() and time.monotonic() < deadline:result.done.wait(.05)
    test.assertTrue(result.done.is_set(), 'bounded synthetic operation did not finish')
    return result.result


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.authority = SyntheticAuthority(); self.lock_token = object()
        self.owner = mod.CellStore(self.root / 'native-owned-cells', self.authority,
            enabled=True, qualification=simulated_profile(self.root), instance_lock=self.lock_token)
        self.authority.owner = self.owner; self.owner.start()
        self.assertTrue(self.owner._ready.wait(3.), self.owner.health())

    def tearDown(self):
        for pin in tuple(self.owner._pins.values()):pin.release()
        self.owner.close(.5)
        if self.owner._thread:self.owner._thread.join(3.)
        self.assertFalse(self.owner._thread and self.owner._thread.is_alive())
        self.temp.cleanup()

    def operation(self, kind, payload):return wait_operation(self, self.owner.submit(kind, payload))
    def publish(self, index=0, frames=16):
        cell = synthetic_cell(index, frames)
        result = self.operation('publish', (cell,))
        self.assertEqual(result['status'], 'durable', result)
        return cell, result

    def receipt(self, index=0):
        return mod.Receipt('session', f'chunk{index}', 'c' * 64, 16, 44, 76,
                           (1, index + 1, 76, 1, 1), 0., None)

    def test_default_registry_empty_and_memory_claim_unmeasured(self):
        self.assertEqual(mod.QUALIFIED_RUNTIMES, ())
        health = self.owner.health()
        self.assertFalse(health['working_memory_measured'])
        self.assertEqual(health['working_reservation_bytes'], 64 * 1024**2)
        self.assertNotIn(str(self.root), json.dumps(health))

    def test_pinned_runtime_and_pragma_readbacks(self):
        # Read-only second connection observes durable header limits. Per-owner
        # connection pragmas are checked by _open itself and source-gate pins.
        with sqlite3.connect(self.owner.path) as db:
            self.assertEqual(db.execute('PRAGMA page_size').fetchone()[0], 4096)
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        bad = replace(simulated_profile(self.root), sqlite_source_id='not-the-runtime')
        with sqlite3.connect(':memory:') as db, self.assertRaises(Unavailable):bad.verify(db, self.root)
        # Actual observed built-in binary/package identity is checked separately
        # from the injected storage mechanics profile. Allocation is still fake.
        observed = mod.runtime_fingerprints()
        real = mod.RuntimeQualification(**{**asdict(simulated_profile(self.root)), **observed})
        with sqlite3.connect(':memory:') as db:real.verify(db, self.root)
        self.assertIsNone(real.sqlite3_extension_sha256); self.assertIsNone(real.sqlite3_library_sha256)

    def test_runtime_mismatch_precedes_any_disk_open_or_recovery(self):
        self.owner.close()
        fixture = simulated_profile(self.root)
        cases = (
            (replace(fixture, sqlite_source_id='unqualified-build'), 'runtime_qualification_mismatch'),
            (mod.RuntimeQualification(**asdict(fixture)), 'runtime_binary_qualification_mismatch'))
        for profile, reason in cases:
            with self.subTest(reason=reason):
                rejected = mod.CellStore(self.owner.directory, self.authority, enabled=True, qualification=profile)
                original = sqlite3.connect; calls = []
                def observed(database, *args, **kwargs):
                    calls.append(database)
                    if database != ':memory:':raise AssertionError('must not open a disk sidecar on mismatch')
                    return original(database, *args, **kwargs)
                with patch.object(sqlite3, 'connect', side_effect=observed), patch.object(
                        mod, 'runtime_fingerprints', return_value={'interpreter_sha256': '0' * 64}):
                    rejected.start(); rejected._thread.join(3.)
                self.assertFalse(rejected._ready.is_set()); self.assertEqual(calls, [':memory:'])
                self.assertEqual(rejected.health()['status'], reason)

    def test_envelope_math_and_unproved_header_capacity_rejected(self):
        self.assertEqual(mod.DATABASE_BYTES + mod.JOURNAL_BYTES + mod.ANCILLARY_BYTES, mod.RESERVED_ENVELOPE_BYTES)
        self.assertLess(mod.RESERVED_ENVELOPE_BYTES, mod.TOTAL_BYTES)
        with self.assertRaises(Unavailable):replace(simulated_profile(self.root), journal_headers_max=1024)

    def test_durable_replay_and_conflicting_ownership(self):
        cell, first = self.publish()
        second = self.operation('publish', (cell,))
        self.assertEqual(first['cells'][0]['status'], 'retained')
        self.assertEqual(second['cells'][0]['status'], 'existing')
        conflict = synthetic_cell(0, 16, receipt_id='d' * 64)
        result = self.operation('publish', (conflict,))
        self.assertEqual(result['reason'], 'conflicting_committed_proof')

    def test_one_active_or_pending_slot_shared_by_receipt_and_publication(self):
        entered = threading.Event(); release = threading.Event()
        original = self.authority.receipt_valid
        def blocked(*args):entered.set(); release.wait(.2); return original(*args)
        with patch.object(self.authority, 'receipt_valid', side_effect=blocked):
            op = self.owner.submit('receipt', self.receipt())
            self.assertTrue(entered.wait(1.))
            self.assertEqual(self.owner.submit('publish', (synthetic_cell(),))['status'], 'busy')
            self.assertEqual(self.owner.submit('receipt', self.receipt(1))['status'], 'busy')
            self.assertEqual(self.owner.health()['handoffs_active_or_pending'], 1)
            release.set(); self.assertEqual(wait_operation(self, op)['status'], 'receipt_retained')
        self.assertTrue(all(t != threading.get_ident() for t in self.authority.calls))

    def test_batch_limit_and_invalid_input_cannot_evict(self):
        cell, _ = self.publish()
        with self.assertRaises(Unavailable):self.owner.submit('publish', (cell,) * 4)
        with self.assertRaises(Unavailable):self.owner.submit('publish', ('unselected',))
        self.assertEqual(self.operation('detail', cell.cell_id)['status'], 'available')

    def test_two_pins_exactly_once_release_and_expiry(self):
        cell, _ = self.publish()
        a = self.operation('read', cell.cell_id)['pin']; b = self.operation('read', cell.cell_id)['pin']
        self.assertEqual(a.read(), cell.pcm)
        self.assertEqual(self.operation('read', cell.cell_id)['reason'], 'read_pin_capacity')
        self.assertEqual(self.operation('expire', cell.cell_id)['reason'], 'cell_pinned')
        self.assertTrue(a.release()); self.assertFalse(a.release()); b.release()
        self.assertEqual(self.operation('expire', cell.cell_id)['status'], 'expired')
        detail = self.operation('detail', cell.cell_id)
        self.assertTrue(detail['regeneration_eligible'])
        self.assertEqual(self.operation('read', cell.cell_id)['reason'], 'pcm_not_retained')

    def test_source_revocation_cancels_copied_pins_and_purges(self):
        cell, _ = self.publish(); pin = self.operation('read', cell.cell_id)['pin']
        self.authority.valid = False; self.owner.revoked()
        with self.assertRaises(Unavailable):pin.read()
        pin.release()
        deadline = time.monotonic() + 3.
        while self.owner.health()['cleanup_pending'] and time.monotonic() < deadline:time.sleep(.01)
        self.assertFalse(self.owner.health()['cleanup_pending'], self.owner.health())
        self.assertEqual(self.operation('detail', cell.cell_id)['reason'], 'cell_unavailable')

    def test_keyset_pages_do_not_skip_and_are_bounded(self):
        for i in range(24):self.publish(i)
        after = 0; ids = []
        while True:
            page = self.operation('list', ('session', after))
            self.assertEqual(page['status'], 'available'); self.assertLessEqual(len(page['cells']), 20)
            self.assertLessEqual(len(canonical(page['cells'])), 65536)
            if not page['cells']:break
            self.assertGreater(page['next_cursor'], after); after = page['next_cursor']
            ids.extend(row['cell_id'] for row in page['cells'])
        self.assertEqual(len(ids), 24); self.assertEqual(len(set(ids)), 24)

    def test_real_sqlite_busy_failure_does_not_block_submitter(self):
        blocker = sqlite3.connect(self.owner.path, timeout=0., isolation_level=None)
        try:
            blocker.execute('BEGIN IMMEDIATE')
            started = time.monotonic(); result = self.owner.submit('receipt', self.receipt())
            self.assertLess(time.monotonic() - started, .1)
            self.assertIn(wait_operation(self, result)['status'], ('unknown', 'unavailable'))
        finally:blocker.execute('ROLLBACK'); blocker.close()

    def test_budget_vm_projection_and_read_failures(self):
        budget = mod.Budget()
        with self.assertRaises(Unavailable):budget.projection(256 * 1024 + 1)
        with self.assertRaises(Unavailable):budget.read(65537)
        budget.steps = 150000; self.assertEqual(budget.progress(), 1)
        budget = mod.Budget(); budget.read_bytes = 40 * 1024**2
        with self.assertRaises(Unavailable):budget.read(1)

    def test_slow_optional_operation_times_out_without_caller_fsync(self):
        entered = threading.Event(); release = threading.Event(); original = self.owner._receipt
        def slow(op, budget):entered.set(); release.wait(1.); return original(op, budget)
        with patch.object(self.owner, '_receipt', side_effect=slow):
            started = time.monotonic(); op = self.owner.submit('receipt', self.receipt())
            self.assertLess(time.monotonic() - started, .1); self.assertTrue(entered.wait(1.))
            time.sleep(.51); release.set()
            self.assertEqual(wait_operation(self, op)['reason'], 'cooperative_deadline')

    def test_shutdown_unresolved_owner_retains_instance_lock(self):
        entered = threading.Event(); release = threading.Event(); original = self.owner._receipt
        def slow(op, budget):entered.set(); release.wait(1.); return original(op, budget)
        with patch.object(self.owner, '_receipt', side_effect=slow):
            op = self.owner.submit('receipt', self.receipt()); self.assertTrue(entered.wait(1.))
            self.assertFalse(self.owner.close(.01)); self.assertIs(self.owner.instance_lock, self.lock_token)
            self.assertEqual(self.owner.health()['status'], 'shutdown_pending')
            release.set(); self.assertIn(wait_operation(self, op)['status'], ('unknown', 'unavailable'))
            self.owner._thread.join(1.)
        self.assertTrue(self.owner.close()); self.assertIsNone(self.owner.instance_lock)

    def test_unexpected_wal_shm_and_unsafe_namespace_count_fail_closed(self):
        self.owner.close(); target = self.owner.directory / 'cells.sqlite3-wal'; target.write_bytes(b'counted')
        with self.assertRaisesRegex(Unavailable, 'wal_shm'):self.owner._scan()
        self.assertGreaterEqual(self.owner._charge, len(b'counted'))
        # Retain the ordinary negative fixture; do not delete/migrate it.

    def test_zero_header_journal_is_accounted_without_manual_removal(self):
        self.owner.close(); journal = self.owner.directory / 'cells.sqlite3-journal'
        journal.write_bytes(b'\0' * 4096)
        identities = self.owner._scan()
        self.assertIn('cells.sqlite3-journal', identities)
        self.assertTrue(journal.exists())
        self.assertGreaterEqual(self.owner._charge, self.owner.path.stat().st_size + 4096)

    def test_database_high_water_remains_charged_after_cell_delete(self):
        cell, _ = self.publish(frames=16000)
        before = self.owner.path.stat().st_size
        self.assertEqual(self.operation('delete', cell.cell_id)['status'], 'deleted')
        self.assertGreaterEqual(self.owner.path.stat().st_size, before)

    def test_metadata_fallback_and_physical_capacity_status(self):
        cell = synthetic_cell(frames=16)
        original = self.owner._insert_cell
        # Force the actual metadata transaction branch, not a fake successful
        # return; physical SQLITE_FULL behavior has its separate finite gate.
        with patch.object(self.owner, '_insert_cell', side_effect=lambda op, c, budget, pcm: original(op, c, budget, False)):
            result = self.operation('publish', (cell,))
        self.assertEqual(result['cells'][0]['status'], 'not_retained_pcm')
        self.assertTrue(self.operation('detail', cell.cell_id)['regeneration_eligible'])

    def test_cell_row_ceiling_independent_of_physical_capacity(self):
        # Real 1024-row boundary with tiny manifests, no lowered policy constant.
        for start in range(0, 1024, 3):
            cells = tuple(synthetic_cell(i, 1) for i in range(start, min(1024, start + 3)))
            self.assertEqual(self.operation('publish', cells)['status'], 'durable')
        extra = synthetic_cell(1024, 1)
        self.assertEqual(self.operation('publish', (extra,))['cells'][0]['status'], 'not_retained_metadata_capacity')

    def test_source_receipt_ceiling_and_no_pending_receipt_authority(self):
        self.assertEqual(mod.MAX_RECEIPTS, 4096)
        for i in range(4096):self.assertEqual(self.operation('receipt', self.receipt(i))['status'], 'receipt_retained')
        result = self.operation('receipt', self.receipt(4096))
        self.assertEqual(result['reason'], 'source_receipt_capacity')
        self.assertIsNone(self.owner.receipt('chunk4096'))

    def test_namespace_metadata_rejection_uses_no_real_special_files(self):
        self.owner.close(); real = self.owner.directory.lstat()
        fake = SimpleNamespace(st_mode=real.st_mode, st_uid=real.st_uid,
                               st_dev=real.st_dev, st_ino=real.st_ino + 1)
        with patch.object(Path, 'lstat', return_value=fake), self.assertRaisesRegex(Unavailable, 'replaced'):
            self.owner._scan()
        # Persistently bound ancestry must reject before any SQLite connection.
        changed = self.owner._ancestor_identity[:-1] + ((real.st_dev, real.st_ino + 1),)
        with patch.object(self.owner, '_ancestor_identity', changed), patch.object(
                sqlite3, 'connect', side_effect=AssertionError('no SQLite on ancestor drift')), self.assertRaisesRegex(Unavailable, 'cell_ancestor_replaced'):
            self.owner._open()
        original = os.fstat
        def unsafe_mode(fd):
            value = original(fd)
            return SimpleNamespace(st_mode=value.st_mode | 0o002, st_uid=value.st_uid,
                                   st_dev=value.st_dev, st_ino=value.st_ino)
        with patch.object(os, 'fstat', side_effect=unsafe_mode), patch.object(
                sqlite3, 'connect', side_effect=AssertionError('no SQLite with writable ancestry')), self.assertRaisesRegex(Unavailable, 'unsafe_cell_ancestor'):
            self.owner._open()

    def test_late_deletion_after_sidecar_commit_never_returns_durable(self):
        original = self.authority.cell_valid; calls = []
        def invalidate(doc, budget, *, publishing):
            calls.append(publishing)
            if len(calls) == 3:self.authority.valid = False
            return original(doc, budget, publishing=publishing)
        with patch.object(self.authority, 'cell_valid', side_effect=invalidate):
            result = self.operation('publish', (synthetic_cell(),))
        self.assertNotEqual(result['status'], 'durable')
        self.assertEqual(result['reason'], 'source_changed_after_publication_commit')


class AdmissionTests(unittest.TestCase):
    def test_off_absent_and_enabled_unqualified_do_not_create_sql_or_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); authority = SyntheticAuthority()
            with patch.object(sqlite3, 'connect', side_effect=AssertionError('no optional SQL')):
                off = mod.CellStore(root / 'off', authority, enabled=False).start()
                enabled = mod.CellStore(root / 'unqualified', authority, enabled=True).start()
            self.assertIsNone(off._thread); self.assertIsNone(enabled._thread)
            self.assertFalse((root / 'off').exists()); self.assertFalse((root / 'unqualified').exists())
            self.assertEqual(enabled.health()['status'], 'runtime_unqualified')
