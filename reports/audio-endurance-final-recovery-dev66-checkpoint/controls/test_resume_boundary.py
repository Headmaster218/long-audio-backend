# PUBLIC SOURCE INSPECTION ONLY. This is not a runnable distribution.
"""Actual-shaped finite run regressions. No audio, model, DB or API reads."""

raise RuntimeError("Inspection-only transformed excerpt: do not execute or import; private dependencies and pins are intentionally omitted.")

import copy
import hashlib
import tempfile
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from endurance import FRAMES, Input, Plan, Stop, run, resume_boundary
from resume_state import require_restart
from test_endurance_source import FakeOperations


class ResumeOperations(FakeOperations):
    def __init__(self, baseline):
        super().__init__()
        self.baseline = copy.deepcopy(baseline)
        self.live_checkpoint = copy.deepcopy(baseline['journal'])
        self.starts = 0
        self.resumes = 0
        self.arrivals = []
        self.cleanup = []

    def start(self):
        self.starts += 1
        raise AssertionError('A continuation must not create a session')

    def resume_start(self):
        self.resumes += 1
        return 'same-qualified-session'

    def checkpoint(self):
        if not self.calls:
            return copy.deepcopy(self.live_checkpoint)
        return super().checkpoint()

    def event(self, kind, value):
        if kind == 'arrival':
            self.arrivals.append(dict(value))

    def stop_and_readback(self, successful_live):
        self.cleanup.append(successful_live)
        return super().stop_and_readback(successful_live)


class ResumeBoundaryControls(unittest.TestCase):
    def setUp(self):
        release_path = Path(__file__).parent / 'resume-proposal/endurance-release.json'
        self.release = json.loads(release_path.read_bytes())
        self.resume = copy.deepcopy(self.release['resume'])
        self.baseline = json.loads(Path(self.resume['baseline_receipt']).read_bytes())
        metadata = json.loads(Path(self.release['inputs_manifest']).read_bytes())['inputs']
        self.inputs = tuple(Input(row['index'], row['path'], tuple(row['signature']), row['sha256'])
                            for row in metadata)
        self.plan = Plan(**self.release['plan'])
        self.ops = ResumeOperations(self.baseline)

    def partial(self):
        # Retain generic partial-boundary validation independently of this
        # release's exact partial-boundary qualification.
        count = self.resume['completed_inputs']
        item = self.inputs[count]
        journal = copy.deepcopy(self.baseline['journal'])
        journal.update(inputs=count + 1, clean_inputs=count,
                       frames=count * FRAMES + REDACTED_COORDINATE_A665A45920,
                       active_input=dict(id=item.input_id, normalized_start=count * FRAMES,
                           frames=REDACTED_COORDINATE_A665A45920, clean_eof=False,
                           identity=dict(path=item.path, sha256=item.sha256,
                                         signature=list(item.signature))))
        self.resume['boundary_kind'] = 'partial'
        self.ops = ResumeOperations(dict(self.baseline, journal=journal))

    def execute(self):
        # Metadata comes from the actual pinned release; original audio is never
        # opened. Real run and ingest execute with finite in-memory operations.
        with patch('endurance.verify_inputs') as verify:
            result = run(self.plan, self.inputs, self.ops, self.resume)
            verify.assert_called_once()
            return result

    def rejected(self, before_start=True):
        with self.assertRaises(Stop):
            self.execute()
        self.assertEqual(self.ops.resumes, 0 if before_start else 1)
        self.assertEqual(self.ops.starts, 0)
        self.assertEqual(self.ops.calls, [])
        self.assertEqual(self.ops.arrivals, [])
        self.assertEqual(self.ops.cleanup, [False])

    def test_actual_boundary_full_remaining_schedule(self):
        self.assertEqual(self.resume['completed_inputs'], 74)
        self.assertEqual(self.resume['boundary_kind'], 'partial')
        self.assertEqual(self.baseline['journal']['active_input']['id'], 'repeated-prefix-075')
        self.assertEqual(self.resume['resume_input_id'], 'repeated-prefix-075')
        result = self.execute()
        self.assertTrue(result['successful_live'])
        append = self.ops.calls[:-1]
        ids = [argv[argv.index('--input-id') + 1] for argv in append]
        self.assertEqual(ids, [f'repeated-prefix-{i:03d}' for i in range(75, 91)])
        self.assertEqual(len(append), 16)
        self.assertNotIn('repeated-prefix-013', ids)
        self.assertNotIn('repeated-prefix-014', ids)
        self.assertNotIn('repeated-prefix-045', ids)
        self.assertTrue(all(f'repeated-prefix-{i:03d}' not in ids for i in range(67,75)))
        self.assertEqual(sum('--finalize' in argv for argv in self.ops.calls), 1)
        self.assertIn('--finalize', self.ops.calls[-1])
        origin = self.resume['started_upper'] - self.resume['started_lower'] + self.resume['schedule_offset']
        self.assertEqual([row['due'] for row in self.ops.arrivals],
                         [origin + i * self.plan.period for i in range(75, 91)])
        self.assertEqual(self.ops.restarts, [])
        self.assertEqual(self.ops.resumes, 1)
        self.assertEqual(self.ops.starts, 0)
        self.assertEqual(self.ops.cleanup, [True])

    def test_release_count_boolean_refused(self):
        self.resume['completed_inputs'] = True
        self.rejected()

    def test_release_count_out_of_range_refused(self):
        for value in (-1, len(self.inputs), 9.0):
            with self.subTest(value=value):
                self.ops = ResumeOperations(self.baseline)
                self.resume['completed_inputs'] = value
                self.rejected()

    def test_release_count_drift_refused(self):
        self.resume['completed_inputs'] -= 1
        self.rejected()

    def test_release_id_drift_refused(self):
        self.resume['resume_input_id'] = 'repeated-prefix-011'
        self.rejected()

    def test_baseline_count_drift_refused(self):
        self.ops.baseline['journal']['clean_inputs'] -= 1
        self.rejected()

    def test_baseline_total_count_drift_refused(self):
        self.ops.baseline['journal']['inputs'] += 1
        self.rejected()

    def test_active_id_drift_refused(self):
        self.partial()
        self.ops.baseline['journal']['active_input']['id'] = 'repeated-prefix-009'
        self.rejected()

    def test_active_prefix_drift_refused(self):
        self.partial()
        self.ops.baseline['journal']['active_input']['normalized_start'] += 1
        self.rejected()

    def test_active_cursor_drift_refused(self):
        self.partial()
        self.ops.baseline['journal']['active_input']['frames'] += 1
        self.rejected()

    def test_active_cursor_bounds_refused(self):
        for value in (-1, FRAMES + 1, True):
            with self.subTest(value=value):
                self.partial()
                self.ops.baseline['journal']['active_input']['frames'] = value
                self.rejected()

    def test_emitted_frame_drift_refused(self):
        self.ops.baseline['journal']['frames'] += 1
        self.rejected()

    def test_published_frontier_overflow_refused(self):
        self.ops.baseline['journal']['published_frames'] = self.ops.baseline['journal']['frames'] + 1
        self.rejected()

    def test_completed_active_refused(self):
        self.partial()
        self.ops.baseline['journal']['active_input']['clean_eof'] = True
        self.rejected()

    def test_finalized_refused(self):
        self.ops.baseline['journal']['finalized'] = True
        self.rejected()

    def test_active_original_drift_refused(self):
        for key, value in (('path', '/wrong.wav'), ('sha256', '0' * 64),
                           ('signature', [0, 0, 0, 0, 0])):
            with self.subTest(key=key):
                self.partial()
                self.ops.baseline['journal']['active_input']['identity'][key] = value
                self.rejected()

    def test_unordered_metadata_refused(self):
        self.inputs = self.inputs[1:2] + self.inputs[:1] + self.inputs[2:]
        self.rejected()

    def test_live_checkpoint_drift_refused_before_ingestion(self):
        self.ops.live_checkpoint['checkpoint_sha256'] = '0' * 64
        self.rejected(before_start=False)

    def test_live_cursor_drift_refused_before_ingestion(self):
        self.ops.live_checkpoint['frames'] += 1
        self.rejected(before_start=False)

    def test_mid_ingest_failure_still_cleans_up(self):
        self.ops.fail_at = self.resume['completed_inputs'] + 3
        with self.assertRaisesRegex(Stop, 'synthetic budget stop'):
            self.execute()
        self.assertEqual(self.ops.resumes, 1)
        self.assertEqual(len(self.ops.calls), 3)
        self.assertEqual(self.ops.count, self.ops.fail_at)
        self.assertFalse(self.ops.finalized)
        self.assertEqual(self.ops.cleanup, [False])

    def test_ingest_published_regression_refused(self):
        original = self.ops.checkpoint
        def checkpoint():
            result = original()
            if self.ops.calls:
                result['published_frames'] = self.baseline['journal']['published_frames'] - 1
            return result
        self.ops.checkpoint = checkpoint
        with self.assertRaisesRegex(Stop, 'Published capture frontier regressed'):
            self.execute()
        self.assertEqual(len(self.ops.calls), 1)
        self.assertEqual(self.ops.cleanup, [False])

    def test_restart_marker_missing_refused(self):
        del self.resume['restart_already_done']
        with self.assertRaisesRegex(RuntimeError,'Explicit restart'):
            self.execute()
        self.assertEqual(self.ops.resumes,0)
        self.assertEqual(self.ops.cleanup,[False])

    def test_restart_marker_false_after_boundary_refused(self):
        self.resume['restart_already_done']=False
        with self.assertRaisesRegex(RuntimeError,'Passed planned restart'):
            self.execute()
        self.assertEqual(self.ops.resumes,0)
        self.assertEqual(self.ops.cleanup,[False])

    def test_restart_receipt_hash_drift_before_start(self):
        self.resume['restart_receipt_sha256']='0'*64
        with self.assertRaisesRegex(RuntimeError,'Restart receipt changed'):
            self.execute()
        self.assertEqual(self.ops.resumes,0)
        self.assertEqual(self.ops.cleanup,[False])

    def test_restart_claim_before_boundary_refused(self):
        baseline=copy.deepcopy(self.baseline)
        baseline['journal']['clean_inputs']=44
        with self.assertRaisesRegex(RuntimeError,'precedes completed boundary'):
            require_restart(self.resume,baseline,45)

    def test_restart_not_done_before_boundary_supported(self):
        baseline=copy.deepcopy(self.baseline)
        baseline['journal']['clean_inputs']=44
        self.assertFalse(require_restart(dict(self.resume,restart_already_done=False),baseline,45))

    def test_boundary_kind_is_explicit(self):
        for value in (None, 'other', True, 1):
            with self.subTest(value=value):
                self.ops = ResumeOperations(self.baseline)
                self.resume['boundary_kind'] = value
                self.rejected()

    def test_clean_requires_explicit_absent_active(self):
        del self.ops.baseline['journal']['active_input']
        self.rejected()

    def test_clean_rejects_active_input(self):
        self.ops.baseline['journal']['active_input'] = {}
        self.rejected()

    def test_clean_rejects_extra_uncommitted_input(self):
        self.ops.baseline['journal']['inputs'] += 1
        self.rejected()

    def test_clean_published_frontier_type_and_bounds(self):
        for value in (-1, True, 1.0):
            with self.subTest(value=value):
                self.ops = ResumeOperations(self.baseline)
                self.ops.baseline['journal']['published_frames'] = value
                self.rejected()

    def test_partial_boundary_remains_supported(self):
        self.partial()
        for consumed in (0, REDACTED_COORDINATE_A665A45920, FRAMES):
            with self.subTest(consumed=consumed):
                journal = self.ops.baseline['journal']
                journal['active_input']['frames'] = consumed
                journal['frames'] = self.resume['completed_inputs'] * FRAMES + consumed
                self.assertEqual(resume_boundary(self.inputs, self.resume, self.ops.baseline),
                    (self.resume['completed_inputs'], journal['published_frames']))

    def test_partial_kind_cannot_admit_clean_checkpoint(self):
        self.ops.baseline['journal']['active_input']=None
        self.rejected()

    def test_clean_kind_cannot_admit_partial_checkpoint(self):
        self.partial()
        self.resume['boundary_kind'] = 'clean'
        self.rejected()

    def test_clean_boundary_remains_supported(self):
        journal=self.ops.baseline['journal']
        count=self.resume['completed_inputs']
        journal.update(inputs=count,frames=count*FRAMES,active_input=None)
        self.resume['boundary_kind']='clean'
        self.assertEqual(resume_boundary(self.inputs,self.resume,self.ops.baseline),(count,journal['published_frames']))

    def test_restart_receipt_semantic_drift_refused(self):
        original=json.loads(Path(self.resume['restart_receipt']).read_bytes())
        changes=(('status','failed'),('accepted_input_count',44),
                 ('accepted_input_id','repeated-prefix-044'),('positive_stop',False),
                 ('positive_original_wait',False),('positive_stop_generation',1),
                 ('preserved_completed',REDACTED_COORDINATE_4B9A7F50C0),('latest_generation_cleanup','positive'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'restart.json'
            for key,value in changes:
                with self.subTest(key=key):
                    proof=copy.deepcopy(original);proof[key]=value
                    data=json.dumps(proof).encode();path.write_bytes(data)
                    resume=dict(self.resume,restart_receipt=str(path),restart_receipt_sha256=hashlib.sha256(data).hexdigest())
                    with self.assertRaises(RuntimeError):require_restart(resume,self.baseline,45)

    def test_restart_manifest_drift_refused(self):
        proof=json.loads(Path(self.resume['restart_receipt']).read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            manifest=Path(directory)/'manifest';manifest.write_text('changed')
            proof['before']['path']=str(manifest)
            proof['source_pins'][str(manifest)]=proof['before']['sha256']
            data=json.dumps(proof).encode();path=Path(directory)/'proof';path.write_bytes(data)
            resume=dict(self.resume,restart_receipt=str(path),restart_receipt_sha256=hashlib.sha256(data).hexdigest())
            with self.assertRaisesRegex(RuntimeError,'manifest changed'):require_restart(resume,self.baseline,45)

    def test_real_run_clean_boundary_after_restart(self):
        journal=copy.deepcopy(self.baseline['journal'])
        count=self.resume['completed_inputs']
        journal.update(inputs=count,frames=count*FRAMES,active_input=None)
        self.resume['boundary_kind']='clean'
        self.ops=ResumeOperations(dict(self.baseline,journal=journal))
        result=self.execute()
        self.assertTrue(result['successful_live'])
        self.assertEqual(len(self.ops.calls),len(self.inputs)-count+1)
        self.assertEqual(self.ops.restarts,[])
        self.assertEqual(sum('--finalize' in argv for argv in self.ops.calls),1)
        self.assertEqual(self.ops.cleanup,[True])
