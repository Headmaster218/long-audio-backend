"""Focused regression source for the recovered two-second owner candidate.

Run only after applying the complete patch in a disposable dev8 checkout.
These deterministic unit checks do not establish SQLite/runtime qualification.
"""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from audio_backend import native_owned_cell_store as mod
from audio_backend import native_owned_cell_jobs as jobs
from audio_backend.native_owned_cells import Unavailable
from audio_backend.native_owned_support_cache import ReadError


class BudgetTests(unittest.TestCase):
    def test_passive_and_owner_limits_leave_other_caps_unchanged(self):
        self.assertEqual(mod.OP_SECONDS, .5)
        self.assertEqual(mod.OWNER_OP_SECONDS, 2.)
        self.assertEqual(mod.LOCK_SECONDS, .005)
        self.assertEqual(mod.SQL_STEPS, 150000)
        self.assertEqual(mod.SQL_PROJECTION_BYTES, 262144)
        self.assertEqual(mod.SOURCE_READ_BYTES, 41943040)
        self.assertEqual(mod.SCHEMA, 'native-owned-cell-store-v1')
        self.assertEqual(mod.CellStore.close.__defaults__, (.5,))

    def test_passive_budget_still_expires_after_half_second(self):
        budget = mod.Budget()
        budget.elapsed = .5
        budget.check()
        budget.elapsed = .5001
        with self.assertRaisesRegex(Unavailable, 'cooperative_deadline'):
            budget.check()

    def test_owner_active_work_gets_two_seconds(self):
        budget = mod.Budget.for_owner()
        budget.elapsed = .51
        budget.check()
        budget.elapsed = 2.
        budget.check()
        budget.elapsed = 2.0001
        with self.assertRaisesRegex(Unavailable, 'cooperative_deadline'):
            budget.check()

    def test_queue_time_uses_original_admission_deadline_for_every_kind(self):
        for kind in ('receipt', 'publish', 'list', 'dispatch'):
            with self.subTest(kind=kind):
                with patch.object(mod.time, 'monotonic', return_value=100.):
                    op = mod.Operation(kind, None, object())
                with patch.object(mod.time, 'monotonic', return_value=101.8):
                    budget = mod.Budget.for_owner(op)
                    budget.check()
                self.assertEqual(op.deadline, 102.)
                self.assertEqual(budget.deadline, 102.)
                self.assertEqual(budget.elapsed, 0.)
                with patch.object(mod.time, 'monotonic', return_value=102.):
                    with self.assertRaisesRegex(Unavailable, 'cooperative_deadline'):
                        with budget.phase():
                            self.fail('An expired queued operation entered its phase')

    def test_standalone_owner_budget_does_not_count_inactive_gaps(self):
        budget = mod.Budget.for_owner()
        now = [10.]
        with patch.object(mod.time, 'monotonic', side_effect=lambda: now[0]):
            with budget.phase():
                now[0] = 10.75
            now[0] = 100.
            budget.check()
            with budget.phase():
                now[0] = 100.75
        self.assertEqual(budget.elapsed, 1.5)
        self.assertIsNone(budget.deadline)

    def test_nested_phases_count_active_time_once(self):
        budget = mod.Budget.for_owner()
        now = [10.]
        with patch.object(mod.time, 'monotonic', side_effect=lambda: now[0]):
            with budget.phase():
                now[0] = 10.25
                with budget.phase():
                    now[0] = 10.75
                now[0] = 11.
        self.assertEqual(budget.elapsed, 1.)

    def test_owner_budget_preserves_every_work_ceiling(self):
        for name, limit in (('steps', 150000), ('sql_bytes', 262144),
                            ('read_bytes', 41943040)):
            with self.subTest(name=name):
                budget = mod.Budget.for_owner()
                setattr(budget, name, limit)
                budget.check()
                setattr(budget, name, limit + 1)
                with self.assertRaisesRegex(Unavailable, 'operation_work_capacity'):
                    budget.check()


class OperationTests(unittest.TestCase):
    def operation(self, kind):
        with patch.object(mod.time, 'monotonic', return_value=100.):
            return mod.Operation(kind, None, object())

    def test_admission_records_two_seconds(self):
        for kind in ('receipt', 'jobs_list', 'dispatch'):
            self.assertEqual(self.operation(kind).deadline, 102.)

    def test_both_wait_paths_clip_to_remaining_admission_time(self):
        for kind in ('jobs_list', 'dispatch'):
            with self.subTest(kind=kind):
                op = self.operation(kind)
                op.done = Mock()
                op.done.is_set.return_value = True
                op.state = 'ready'
                with patch.object(mod.time, 'monotonic', return_value=101.75):
                    op.wait()
                op.done.wait.assert_called_once_with(.25)
                self.assertEqual(op.deadline, 102.)

    def test_default_wait_is_two_seconds_and_explicit_short_wait_is_kept(self):
        for kind in ('jobs_list', 'dispatch'):
            with self.subTest(kind=kind):
                op = self.operation(kind)
                op.done = Mock()
                op.done.is_set.return_value = True
                op.state = 'ready'
                with patch.object(mod.time, 'monotonic', return_value=100.):
                    op.wait()
                    op.done.wait.assert_called_once_with(2.)
                    op.done.wait.reset_mock()
                    op.wait(.125)
                    op.done.wait.assert_called_once_with(.125)

    def test_wait_bounds_and_expired_ordinary_wait(self):
        op = self.operation('jobs_list')
        for value in (-1, 2.01, True, None, float('nan')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    op.wait(value)
        op.done = Mock()
        op.done.is_set.return_value = False
        with patch.object(mod.time, 'monotonic', return_value=103.):
            self.assertEqual(op.wait(), {'status': 'pending'})
        op.done.wait.assert_called_once_with(0.)

    def test_expired_dispatch_cannot_expose_a_pin(self):
        op = self.operation('dispatch')
        op.state = 'ready'
        op._pin = object()
        op.result = {'status': 'available'}
        op.done.set()
        with patch.object(mod.time, 'monotonic', return_value=102.):
            result = op.wait()
            claimed = op.claim(admission_open=True)
        self.assertNotIn('pin', result)
        self.assertNotIn('pin', claimed)
        self.assertTrue(op.cancelled.is_set())
        self.assertIsNotNone(op._pin)

    def test_pin_read_uses_owner_budget(self):
        def validate(document, budget, *, publishing):
            self.assertFalse(publishing)
            self.assertEqual(budget.seconds, 2.)
            budget.elapsed = .75
            budget.check()
            return True
        owner = SimpleNamespace(authority=SimpleNamespace(cell_valid=validate))
        pin = mod.Pin(owner, 'cell', {}, b'pcm')
        self.assertEqual(pin.read(), b'pcm')


class AdapterTests(unittest.TestCase):
    def source(self, operation):
        owner = SimpleNamespace(
            authority=SimpleNamespace(identity='fixture'),
            submit=Mock(return_value=operation), _epoch=object())
        source = jobs.CellJobsSource(owner, object())
        source._current = Mock()
        return source

    def test_read_waits_with_owner_limit(self):
        op = mod.Operation('jobs_list', None, object())
        op.wait = Mock(return_value={'status': 'available'})
        self.assertEqual(self.source(op)._read('jobs_list', ('sid', 0, 20)),
                         {'status': 'available'})
        op.wait.assert_called_once_with(2.)

    def test_dispatch_waits_with_owner_limit_and_keeps_failed_handoff(self):
        op = mod.Operation('dispatch', None, object())
        op.wait = Mock()
        op.claim = Mock(return_value={'status': 'unavailable'})
        op.slot_released.set()
        source = self.source(op)
        request = {'expected_cell_sequence': 1, 'expected_cell_revision': 1,
                   'expected_proof_sha256': 'a' * 64, 'support_id': 'b' * 64}
        with self.assertRaises(ReadError):
            source.acquire_dispatch('sid', 'c' * 64, request)
        op.wait.assert_called_once_with(2.)
        self.assertIs(source._pending, op)
        self.assertTrue(op.cancelled.is_set())

    def test_pin_release_wait_remains_half_second(self):
        pin = SimpleNamespace(release=Mock(), release_confirmed=False,
                              release_done=Mock())
        source = self.source(None)
        self.assertFalse(source.release(SimpleNamespace(pin=pin)))
        pin.release_done.wait.assert_called_once_with(.5)


class SweepTests(unittest.TestCase):
    def owner(self, cell_count=0, source_count=0):
        cells = [{'seq': i, 'cell_id': 'cell' + str(i)}
                 for i in range(1, cell_count + 1)]
        sources = [{'cid': 'source' + str(i)}
                   for i in range(1, source_count + 1)]
        epoch = object()
        owner = SimpleNamespace(
            _epoch=epoch, _sweep_generation=epoch, _sweep_cell=0,
            _sweep_source='', _slot=None, _pins={}, _cleanup_pending=True,
            _reconcile=Mock(), _receipts={r['cid']: r for r in sources},
            authority=SimpleNamespace(cell_valid=Mock(return_value=True),
                                      receipt_valid=Mock(return_value=True)),
            _transaction=Mock(side_effect=lambda budget: nullcontext()))
        owner._document = Mock(side_effect=lambda cid, budget: (None, cid, None))
        owner.deleted = []
        def execute(sql, parameters):
            if sql.startswith('SELECT seq,cell_id'):
                rows = [r for r in cells if r['seq'] > parameters[0]][:20]
            elif sql.startswith('SELECT cid FROM sources'):
                rows = [r for r in sources if r['cid'] > parameters[0]][:20]
            elif sql.startswith('DELETE'):
                owner.deleted.append(parameters[0])
                rows = []
            else:
                raise AssertionError(sql)
            return SimpleNamespace(fetchall=lambda: rows)
        owner._db = SimpleNamespace(execute=Mock(side_effect=execute))
        return owner

    def test_cell_yield_finishes_current_item_and_resumes_without_skip(self):
        owner = self.owner(cell_count=3)
        slot = object()
        def valid(*args, **kwargs):
            owner._slot = slot
            return True
        owner.authority.cell_valid.side_effect = valid
        mod.CellStore._sweep(owner, mod.Budget.for_owner())
        self.assertEqual(owner._sweep_cell, 1)
        self.assertIs(owner._slot, slot)
        self.assertEqual(owner._document.call_count, 1)
        owner._slot = None
        owner.authority.cell_valid.side_effect = None
        mod.CellStore._sweep(owner, mod.Budget.for_owner())
        self.assertEqual(owner._sweep_cell, 3)
        self.assertEqual([call.args[0] for call in owner._document.call_args_list],
                         ['cell1', 'cell2', 'cell3'])

    def test_receipt_yield_finishes_current_item_and_resumes_without_skip(self):
        owner = self.owner(source_count=3)
        slot = object()
        def valid(*args):
            owner._slot = slot
            return True
        owner.authority.receipt_valid.side_effect = valid
        mod.CellStore._sweep(owner, mod.Budget.for_owner())
        self.assertEqual(owner._sweep_source, 'source1')
        self.assertIs(owner._slot, slot)
        self.assertEqual(owner.authority.receipt_valid.call_count, 1)
        owner._slot = None
        owner.authority.receipt_valid.side_effect = None
        mod.CellStore._sweep(owner, mod.Budget.for_owner())
        self.assertEqual(owner._sweep_source, 'source3')
        self.assertEqual(owner.authority.receipt_valid.call_count, 3)

    def test_no_slot_preserves_twenty_item_page_and_completion(self):
        owner = self.owner(cell_count=20)
        mod.CellStore._sweep(owner, mod.Budget.for_owner())
        self.assertEqual(owner._sweep_cell, 20)
        self.assertEqual(owner._document.call_count, 20)
        owner._reconcile.clear.assert_not_called()
        mod.CellStore._sweep(owner, mod.Budget.for_owner())
        owner._reconcile.clear.assert_called_once()
        self.assertFalse(owner._cleanup_pending)

    def test_failure_does_not_advance_cursor_or_discard_handoff(self):
        owner = self.owner(cell_count=3)
        slot = object()
        owner._slot = slot
        owner.authority.cell_valid.side_effect = Unavailable('fixture_failure')
        with self.assertRaisesRegex(Unavailable, 'fixture_failure'):
            mod.CellStore._sweep(owner, mod.Budget.for_owner())
        self.assertEqual(owner._sweep_cell, 0)
        self.assertIs(owner._slot, slot)

    def test_invalid_item_is_deleted_before_cursor_yield(self):
        for kind in ('cell', 'receipt'):
            with self.subTest(kind=kind):
                owner = self.owner(cell_count=2 if kind == 'cell' else 0,
                                   source_count=2 if kind == 'receipt' else 0)
                slot = object()
                def invalid(*args, **kwargs):
                    owner._slot = slot
                    return False
                target = (owner.authority.cell_valid if kind == 'cell'
                          else owner.authority.receipt_valid)
                target.side_effect = invalid
                mod.CellStore._sweep(owner, mod.Budget.for_owner())
                expected = 'cell1' if kind == 'cell' else 'source1'
                self.assertEqual(owner.deleted, [expected])
                self.assertIs(owner._slot, slot)
                if kind == 'cell':
                    self.assertEqual(owner._sweep_cell, 1)
                else:
                    self.assertEqual(owner._sweep_source, 'source1')
                    self.assertNotIn('source1', owner._receipts)
                owner._transaction.assert_called_once()


if __name__ == '__main__':
    unittest.main()
