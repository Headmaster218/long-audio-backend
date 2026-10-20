# PUBLIC INSPECTION COPY. Do not execute or import.
"""Proposed finite controls. Source only: not executed during preparation."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import unittest
from unittest.mock import patch
from endurance import FRAMES, Input, Plan, Stop, committed, ingest, run, uploader_argv


class FakeOperations:
    def __init__(self, fail_at=None):
        self.time = 0.
        self.count = 0
        self.finalized = False
        self.calls = []
        self.stopped = False
        self.restarts = []
        self.fail_at = fail_at
        self.retry = False
    def check(self):
        if self.fail_at is not None and self.count == self.fail_at:
            raise Stop('synthetic budget stop')
    def elapsed(self): return self.time
    def wait_until(self, elapsed): self.time = max(self.time, elapsed)
    def event(self, kind, value): pass
    def start(self): return 'sid'
    def upload(self, argv):
        self.calls.append(argv)
        if self.retry:
            self.retry = False
            return dict(returncode=2, outcome='retryable_uncertain', positive_wait=True, owned_children_resolved=True)
        if '--append' in argv:
            self.count = int(argv[argv.index('--input-id') + 1].split('-')[-1])
        else: self.finalized = True
        return dict(returncode=0, positive_wait=True, owned_children_resolved=True)
    def checkpoint(self):
        return dict(inputs=self.count, clean_inputs=self.count, active_input=None,
                    frames=self.count * FRAMES, published_frames=self.count * FRAMES,
                    finalized=self.finalized)
    def restart(self): self.restarts.append((self.count, self.time))
    def primary_complete(self): return self.finalized
    def drain_tick(self, deadline): self.time += 30
    def stop_and_readback(self, successful_live):
        self.stopped = True
        return dict(successful_live=successful_live)


class Controls(unittest.TestCase):
    def setUp(self):
        self.plan = Plan('/pinned/python', 'http://127.0.0.1:8765', 'sid', 'cap', '/journal')
        self.inputs = tuple(Input(i, f'/copies/{i}.wav', (1, i+1, 15360044, 1, 1)) for i in range(90))
    def test_exact_cli_coordinates_and_caps(self):
        argv = uploader_argv(self.plan, 100, self.inputs[-1])
        self.assertEqual(argv[argv.index('--input-start-frame')+1], str(REDACTED_FRAME_ORIGIN + 89*FRAMES))
        self.assertEqual(argv[argv.index('--max-inputs')+1], '90')
        self.assertEqual(argv[argv.index('--max-hours')+1], '12')
        self.assertEqual(argv[argv.index('--deadline-seconds')+1], '100')
    def test_finalize_never_contains_input(self):
        argv = uploader_argv(self.plan, 100, None)
        self.assertIn('--finalize', argv)
        self.assertNotIn('--input', argv)
    def test_same_identity_uncertain_retry(self):
        ops = FakeOperations(); ops.retry = True
        ingest(self.plan, ops, self.inputs[0])
        self.assertEqual(ops.calls[0][-6:], ops.calls[1][-6:])
        self.assertEqual(ops.count, 1)
    def test_missing_positive_wait_stops_without_retry(self):
        ops = FakeOperations()
        with patch.object(ops, 'upload', return_value=dict(returncode=2, outcome='retryable_uncertain')) as call:
            with self.assertRaises(Stop): ingest(self.plan, ops, self.inputs[0])
            self.assertEqual(call.call_count, 1)
    def test_fatal_failure_is_not_retryable(self):
        ops = FakeOperations()
        with patch.object(ops, 'upload', return_value=dict(returncode=2, outcome='source_changed', positive_wait=True, owned_children_resolved=True)) as call:
            with self.assertRaises(Stop): ingest(self.plan, ops, self.inputs[0])
            self.assertEqual(call.call_count, 1)
    def test_uncertain_retry_has_finite_bound(self):
        ops = FakeOperations()
        with patch.object(ops, 'upload', return_value=dict(returncode=2, outcome='retryable_uncertain', positive_wait=True, owned_children_resolved=True)) as call:
            with self.assertRaises(Stop): ingest(self.plan, ops, self.inputs[0])
            self.assertEqual(call.call_count, 3)
    def test_schedule_restart_and_finalization(self):
        ops = FakeOperations()
        with patch('endurance.verify_inputs'):
            report = run(self.plan, self.inputs, ops)
        self.assertTrue(report['successful_live'])
        self.assertEqual(ops.restarts, [(45, 21600)])
        self.assertEqual(ops.time, 43200)
        self.assertEqual(len(ops.calls), 91)
        self.assertIn('--finalize', ops.calls[-1])
        self.assertTrue(ops.stopped)
    def test_failure_preserves_partial_and_stops(self):
        ops = FakeOperations(fail_at=3)
        with patch('endurance.verify_inputs'):
            with self.assertRaises(Stop): run(self.plan, self.inputs, ops)
        self.assertTrue(ops.stopped)
        self.assertEqual(ops.count, 3)
        self.assertFalse(ops.finalized)
    def test_wrong_durable_boundary_fails(self):
        ops = FakeOperations(); ops.count = 1
        row = ops.checkpoint(); row['clean_inputs'] = 0
        with self.assertRaises(Stop): committed(row, 1, False)
    def test_finalized_frontier_must_be_complete(self):
        ops = FakeOperations(); ops.count = 90; ops.finalized = True
        row = ops.checkpoint(); row['published_frames'] -= 1
        with self.assertRaises(Stop): committed(row, 90, True)

# No auto-executing test runner; execution requires separate review/authorization.
